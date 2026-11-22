"""Child-process half of the ``sqlite`` skill (stdlib only, no botboy imports).

The parent (``sqlitedb.py``) runs this file's source as
``python -I -S -c <source> <cpu-seconds> <address-space-bytes>`` and sends one
JSON request on stdin; one JSON response comes back on stdout. The database
is a private snapshot the parent copied from the workspace through a pinned
descriptor, opened here with ``mode=ro&immutable=1``.

Defences, in the order they are installed (each one alone blocks a class):

1. ``RLIMIT_CPU``/``RLIMIT_AS`` where the OS has them; the parent also kills
   this process at its wall-clock deadline, which bounds single long-running
   C operations (``LIKE``/``instr`` over huge cells) that no in-process
   budget can interrupt.
2. ``PRAGMA hard_heap_limit``: every SQLite allocation in this process is
   capped, so string doubling (``x||x`` nesting, ``replace``, ``printf``
   widths, ``zeroblob``) fails with "out of memory" instead of exhausting RAM.
   It is process-global, which is harmless in a single-purpose child.
3. ``Connection.setlimit`` (Python 3.11+): per-value length, SQL length,
   expression depth, compound selects, function arguments, no ATTACH.
4. ``trusted_schema=OFF``, ``query_only=ON``, ``temp_store=MEMORY`` (sorts
   never spill to disk), ``cell_size_check=ON`` (malformed pages are errors),
   and defensive ``setconfig`` flags where available (3.12+).
5. An authorizer that allows only SELECT, column reads, recursive CTEs and an
   allowlist of pure functions; everything else (writes, DDL, ATTACH,
   PRAGMA, transactions, VACUUM INTO, load_extension) is denied.
6. A progress handler: a VM-operation budget and an in-process deadline.
7. Row, cell and output-size caps while fetching.
"""
import json
import math
import sqlite3
import sys
import time

SAFE_FUNCTIONS = frozenset({
    # core scalar
    "abs", "char", "coalesce", "concat", "concat_ws", "format", "glob", "hex", "if", "ifnull", "iif",
    "instr", "length", "like", "likelihood", "likely", "lower", "ltrim", "max", "min", "nullif",
    "octet_length", "printf", "quote", "random", "replace", "round", "rtrim", "sign", "soundex",
    "sqlite_version", "substr", "substring", "trim", "typeof", "unhex", "unicode", "unistr",
    "unistr_quote", "unlikely", "upper",
    # aggregates
    "avg", "count", "group_concat", "string_agg", "sum", "total", "median", "percentile",
    "percentile_cont", "percentile_disc",
    # date and time
    "date", "time", "datetime", "julianday", "strftime", "unixepoch", "timediff",
    # math
    "acos", "acosh", "asin", "asinh", "atan", "atan2", "atanh", "ceil", "ceiling", "cos", "cosh",
    "degrees", "exp", "floor", "ln", "log", "log10", "log2", "mod", "pi", "pow", "power", "radians",
    "sin", "sinh", "sqrt", "tan", "tanh", "trunc",
    # window
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead",
    "first_value", "last_value", "nth_value",
    # JSON (pure; results stay inside the heap limit)
    "json", "jsonb", "json_array", "json_array_length", "json_each", "json_error_position",
    "json_extract", "json_group_array", "json_group_object", "json_insert", "json_object",
    "json_patch", "json_pretty", "json_quote", "json_remove", "json_replace", "json_set", "json_tree",
    "json_type", "json_valid", "->", "->>",
    # full-text search auxiliaries (read-only)
    "match", "snippet", "highlight", "bm25", "offsets", "matchinfo",
})
# Only the schema command uses these, with names taken from sqlite_master.
INTERNAL_PRAGMAS = frozenset({"table_xinfo", "index_list", "index_info", "foreign_key_list"})
PROGRESS_STEP = 1000


def _apply_rlimits(argv):
    try:
        import resource
        cpu = int(argv[1])
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        memory = int(argv[2])
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except Exception:  # noqa: BLE001 - Windows has no resource module
        pass


class Guard:
    """Authorizer and progress handler sharing one mutable state."""

    def __init__(self, op_budget, deadline):
        self.internal = False
        self.op_budget = op_budget
        self.calls = 0
        self.deadline = deadline
        self.stop_reason = ""
        self.denied = []

    def authorize(self, action, arg1, arg2, _db, _source):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, getattr(sqlite3, "SQLITE_RECURSIVE", 33)):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and str(arg2 or "").lower() in SAFE_FUNCTIONS:
            return sqlite3.SQLITE_OK
        if self.internal and action == sqlite3.SQLITE_PRAGMA and str(arg1 or "").lower() in INTERNAL_PRAGMAS:
            return sqlite3.SQLITE_OK
        if len(self.denied) < 5:
            self.denied.append(_describe(action, arg1, arg2))
        return sqlite3.SQLITE_DENY

    def progress(self):
        self.calls += 1
        if self.calls > self.op_budget:
            self.stop_reason = "work budget exhausted"
            return 1
        if time.perf_counter() > self.deadline:
            self.stop_reason = "time budget exhausted"
            return 1
        return 0

    def reset_budget(self, op_budget):
        self.calls = 0
        self.op_budget = op_budget
        self.stop_reason = ""


# Authorizer action codes 1..33 (sqlite3.h); the module's SQLITE_* constants
# mix them with result codes, so they are listed explicitly.
_ACTION_NAMES = (
    "", "create_index", "create_table", "create_temp_index", "create_temp_table", "create_temp_trigger",
    "create_temp_view", "create_trigger", "create_view", "delete", "drop_index", "drop_table",
    "drop_temp_index", "drop_temp_table", "drop_temp_trigger", "drop_temp_view", "drop_trigger",
    "drop_view", "insert", "pragma", "read", "select", "transaction", "update", "attach", "detach",
    "alter_table", "reindex", "analyze", "create_vtable", "drop_vtable", "function", "savepoint",
    "recursive",
)


def _describe(action, arg1, arg2):
    name = _ACTION_NAMES[action] if 0 < action < len(_ACTION_NAMES) else f"action {action}"
    detail = arg2 if action == sqlite3.SQLITE_FUNCTION else (arg1 or "")
    return f"{name} {detail}".strip()


def _harden(conn, request):
    notes = []
    heap = int(request["heap_bytes"])
    try:
        conn.execute(f"PRAGMA hard_heap_limit={heap}")
        conn.execute(f"PRAGMA soft_heap_limit={heap // 2}")
        if conn.execute("PRAGMA hard_heap_limit").fetchone()[0] != heap:
            notes.append("hard_heap_limit unavailable")
    except sqlite3.Error:
        notes.append("hard_heap_limit unavailable")
    for pragma in ("trusted_schema=OFF", "query_only=ON", "temp_store=MEMORY", "cell_size_check=ON"):
        try:
            conn.execute(f"PRAGMA {pragma}")
        except sqlite3.Error:
            pass
    if hasattr(conn, "setlimit"):
        limits = {
            "SQLITE_LIMIT_LENGTH": int(request["max_value_bytes"]),
            "SQLITE_LIMIT_SQL_LENGTH": 100_000,
            "SQLITE_LIMIT_EXPR_DEPTH": 200,
            "SQLITE_LIMIT_COMPOUND_SELECT": 50,
            "SQLITE_LIMIT_FUNCTION_ARG": 64,
            "SQLITE_LIMIT_ATTACHED": 0,
            "SQLITE_LIMIT_LIKE_PATTERN_LENGTH": 2_000,
            "SQLITE_LIMIT_TRIGGER_DEPTH": 1,
            "SQLITE_LIMIT_WORKER_THREADS": 0,
        }
        for name, value in limits.items():
            category = getattr(sqlite3, name, None)
            if category is not None:
                conn.setlimit(category, value)
    else:
        notes.append("per-value length limit unavailable on this Python (heap limit still applies)")
    if hasattr(conn, "setconfig"):
        for name, value in (("SQLITE_DBCONFIG_DEFENSIVE", True), ("SQLITE_DBCONFIG_TRUSTED_SCHEMA", False),
                            ("SQLITE_DBCONFIG_ENABLE_TRIGGER", False), ("SQLITE_DBCONFIG_ENABLE_VIEW", True),
                            ("SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION", False)):
            option = getattr(sqlite3, name, None)
            if option is not None:
                try:
                    conn.setconfig(option, value)
                except sqlite3.Error:
                    pass
    return notes


def _cell(value, max_chars):
    """JSON-safe cell; returns (value, truncated)."""
    if value is None or isinstance(value, int):
        return value, False
    if isinstance(value, float):
        if math.isfinite(value):
            return value, False
        return ("inf" if value > 0 else "-inf"), False
    if isinstance(value, bytes):
        return {"blob": len(value), "hex": value[:16].hex()}, False
    text = str(value)
    if len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _fetch(cursor, guard, request):
    max_rows = int(request["max_rows"])
    max_cell = int(request["max_cell_chars"])
    budget = int(request["max_output_chars"])
    columns = [item[0] for item in (cursor.description or [])]
    rows, used, more, cells_truncated, stopped = [], 0, False, False, ""
    try:
        while True:
            batch = cursor.fetchmany(64)
            if not batch:
                break
            for raw in batch:
                if len(rows) >= max_rows:
                    more = True
                    break
                row = []
                for value in raw:
                    cell, cut = _cell(value, max_cell)
                    cells_truncated = cells_truncated or cut
                    row.append(cell)
                    used += len(str(cell)) + 4
                rows.append(row)
                if used > budget:
                    stopped = "output budget reached"
                    break
            if more or stopped:
                break
    except sqlite3.OperationalError as exc:
        stopped = guard.stop_reason or f"query failed after {len(rows)} row(s): {exc}"
    return {"columns": columns, "rows": rows, "more": more, "cells_truncated": cells_truncated,
            "stopped": stopped}


def _quote_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def _tables(conn, guard, request):
    guard.internal = False
    listing = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
    ).fetchmany(int(request["max_tables"]) + 1)
    more = len(listing) > int(request["max_tables"])
    tables = []
    for kind, name, sql in listing[: int(request["max_tables"])]:
        entry = {"name": name, "type": kind, "rows": None,
                 "virtual": bool(sql and str(sql).lstrip().upper().startswith("CREATE VIRTUAL"))}
        if kind == "table" and time.perf_counter() < guard.deadline:
            guard.reset_budget(int(request["count_budget"]))
            try:
                entry["rows"] = conn.execute(f"SELECT count(*) FROM {_quote_identifier(name)}").fetchone()[0]
            except sqlite3.Error as exc:
                entry["error"] = guard.stop_reason or str(exc)[:200]
        tables.append(entry)
    return {"tables": tables, "more": more}


def _schema(conn, guard, request):
    table = request.get("table") or ""
    guard.internal = True
    try:
        if not table:
            rows = conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
            ).fetchmany(int(request["max_tables"]) * 4)
            return {"objects": [{"type": t, "name": n, "table": tn, "sql": s} for t, n, tn, s in rows]}
        found = conn.execute("SELECT type, sql FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
                             (table,)).fetchone()
        if found is None:
            return {"error": f"no table or view named {table!r}"}
        columns = [
            {"cid": cid, "name": name, "type": decl, "notnull": bool(notnull), "default": default,
             "pk": pk, "hidden": hidden}
            for cid, name, decl, notnull, default, pk, hidden in conn.execute(
                'SELECT cid, name, type, "notnull", dflt_value, pk, hidden FROM pragma_table_xinfo(?)', (table,))
        ]
        indexes = []
        for name, unique, origin, partial in conn.execute(
                'SELECT name, "unique", origin, partial FROM pragma_index_list(?)', (table,)).fetchmany(200):
            cols = [row[0] for row in conn.execute("SELECT name FROM pragma_index_info(?)", (name,)).fetchmany(64)]
            indexes.append({"name": name, "unique": bool(unique), "origin": origin, "partial": bool(partial),
                            "columns": cols})
        foreign = [{"table": ref, "from": src, "to": dst} for ref, src, dst in conn.execute(
            'SELECT "table", "from", "to" FROM pragma_foreign_key_list(?)', (table,)).fetchmany(200)]
        return {"table": table, "type": found[0], "sql": found[1], "columns": columns, "indexes": indexes,
                "foreign_keys": foreign}
    finally:
        guard.internal = False


def run_request(request):
    started = time.perf_counter()
    guard = Guard(int(request["op_budget"]), started + float(request["deadline_s"]))
    try:
        conn = sqlite3.connect(request["uri"], uri=True, isolation_level=None, check_same_thread=False)
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"cannot open database: {exc}"}
    try:
        notes = _harden(conn, request)
        # Python 3.10 cannot remove an authorizer again, so every statement
        # this process prepares after this line is authorised.
        conn.set_authorizer(guard.authorize)
        conn.set_progress_handler(guard.progress, PROGRESS_STEP)
        mode = request["mode"]
        if mode == "tables":
            payload = _tables(conn, guard, request)
        elif mode == "schema":
            payload = _schema(conn, guard, request)
        elif mode == "query":
            cursor = conn.execute(request["sql"])
            payload = _fetch(cursor, guard, request)
        else:
            return {"ok": False, "error": f"unknown mode {mode!r}"}
    except sqlite3.DatabaseError as exc:
        message = str(exc)
        if guard.stop_reason:
            message = f"{guard.stop_reason} ({message})"
        elif guard.denied and "not authorized" in message:
            message = f"not authorized: {', '.join(guard.denied)}"
        return {"ok": False, "error": message[:500]}
    except (sqlite3.Warning, sqlite3.ProgrammingError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:500]}
    except MemoryError:
        return {"ok": False, "error": "memory limit exceeded"}
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    if "error" in payload:
        return {"ok": False, "error": payload["error"]}
    payload.update({"ok": True, "notes": notes, "vm_steps": guard.calls * PROGRESS_STEP,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "sqlite_version": sqlite3.sqlite_version})
    return payload


def main(argv):
    _apply_rlimits(argv)
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        response = run_request(request)
    except MemoryError:
        response = {"ok": False, "error": "memory limit exceeded"}
    sys.stdout.write(json.dumps(response, ensure_ascii=True))
    sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv)
