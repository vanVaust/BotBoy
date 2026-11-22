"""``sqlite`` — read-only SQL over SQLite databases in the workspace.

    sqlite tables shop.db
    sqlite schema shop.db orders
    sqlite query shop.db SELECT region, sum(amount) FROM orders GROUP BY region ORDER BY 2 DESC

Threat model (the database file is attacker-controlled; the SQL comes from
the operator, a remote principal holding an approval, or a trusted agent
literal):

* Confinement and TOCTOU: the file is resolved through the workspace layer and
  copied, through a descriptor opened beneath the pinned workspace root, into
  a private temporary snapshot. SQLite only ever opens that snapshot
  (``mode=ro&immutable=1``), so swapping the workspace path for a link after
  the check reaches nothing, and the user's file is never locked or written.
* Writes and side effects: a lexical gate admits only statements starting with
  SELECT, WITH or VALUES; the worker's authorizer denies everything except
  column reads, SELECT, recursive CTEs and an allowlist of pure functions
  (no ATTACH, PRAGMA, VACUUM INTO, load_extension, DDL or DML); the
  connection is ``query_only``.
* Resource exhaustion and engine bugs: the query runs in a separate
  ``python -I -S`` process (see ``sqlite_worker.py``) with a VM-step budget,
  a hard SQLite heap limit, per-value length limits where Python supports
  them, CPU/address-space rlimits where the OS supports them, and a parent
  wall-clock kill, so neither a single long C-level operation nor a crash in
  SQLite on a malformed file can stall or take down BotBoy.
* Output: rows, cell length and total output are capped; floats keep their
  exact ``repr``; integers are never rounded; control characters in names
  and cells are escaped in the text rendering.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from botboy.skills.native import workspace
from botboy.skills.native.base import (
    NativeSkillContext,
    SkillUsageError,
    failure,
    format_table,
    result,
    split_command,
    take_word,
    truncate,
)
from botboy.skills.native.isolated_regex import _child_env

MAX_DB_BYTES = 256 * 1024 * 1024
HEADER = b"SQLite format 3\x00"
DEFAULT_ROWS = 50
MAX_ROWS = 1000
MAX_CELL_CHARS = 2000
MAX_OUTPUT_CHARS = 200_000
MAX_VALUE_BYTES = 32 * 1024 * 1024
HEAP_BYTES = 256 * 1024 * 1024
ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
OP_BUDGET_CALLS = 50_000  # x 1000 VM steps
COUNT_BUDGET_CALLS = 5_000
DEADLINE_S = 5.0
KILL_GRACE_S = 2.0
MAX_TABLES = 200
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_SQL_CHARS = 20_000
_TEXT_LIMIT = 12_000
_STATEMENT_HEADS = {"select", "with", "values"}
_WORKER_SOURCE_PATH = Path(__file__).with_name("sqlite_worker.py")

_USAGE = (
    "Usage (database paths are relative to the workspace root):\n"
    "  sqlite tables <db>\n"
    "  sqlite schema <db> [table]\n"
    "  sqlite query [--max-rows N] [--format table|json|csv] <db> <SELECT ...>\n"
    "Only one read-only SELECT/WITH/VALUES statement per query."
)


class SqliteSkillError(ValueError):
    """Refused database or failed query (rendered as a non-crashing failure)."""


# ── SQL gate ────────────────────────────────────────────────────────────────

def leading_keyword(sql: str) -> str:
    """First keyword of ``sql`` after whitespace and comments (lower case).

    Linear scan; an unterminated block comment yields "".
    """
    index, length = 0, len(sql)
    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
        elif sql.startswith("--", index):
            newline = sql.find("\n", index)
            if newline == -1:
                return ""
            index = newline + 1
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end == -1:
                return ""
            index = end + 2
        else:
            break
    start = index
    while index < length and (sql[index].isalnum() or sql[index] == "_"):
        index += 1
    return sql[start:index].lower()


def check_sql(sql: str) -> str:
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise SkillUsageError("sqlite query needs a SELECT statement")
    if len(text) > MAX_SQL_CHARS:
        raise SkillUsageError(f"SQL longer than {MAX_SQL_CHARS} characters")
    if "\x00" in text:
        raise SkillUsageError("SQL contains a NUL character")
    head = leading_keyword(text)
    if head not in _STATEMENT_HEADS:
        raise SkillUsageError(
            f"only read-only SELECT/WITH/VALUES statements are allowed (got {head.upper() or 'nothing'!s})"
        )
    return text


# ── snapshot ────────────────────────────────────────────────────────────────

def _snapshot(ctx: NativeSkillContext, user_path: str) -> Tuple[str, Path, Path, List[str]]:
    """Copy the database through a pinned descriptor into a private temp dir.

    Returns ``(relative path, temp dir, snapshot path, notes)``.
    """
    path = workspace.resolve(ctx.workspace_root, user_path, expect="file")
    rel = workspace.relative(ctx.workspace_root, path)
    notes: List[str] = []
    fd = workspace.open_regular(path, root=ctx.workspace_root)
    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="botboy-sqlite-"))
    except OSError:
        os.close(fd)
        raise
    snapshot = tmpdir / "snapshot.db"
    try:
        size = os.fstat(fd).st_size
        if size > MAX_DB_BYTES:
            raise SqliteSkillError(f"{rel} is {size} bytes; the limit is {MAX_DB_BYTES} bytes")
        copied = 0
        header = b""
        out = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > MAX_DB_BYTES:
                    raise SqliteSkillError(f"{rel} grew beyond {MAX_DB_BYTES} bytes while being read")
                if len(header) < 100:
                    header += chunk[: 100 - len(header)]
                os.write(out, chunk)
        finally:
            os.close(out)
        if not header.startswith(HEADER):
            raise SqliteSkillError(f"{rel} is not an SQLite 3 database")
        if len(header) >= 20 and header[18:20] == b"\x02\x02":
            # WAL-mode file: mark the private copy as rollback-journal so the
            # read-only open never looks for -wal/-shm files. The workspace
            # file itself is untouched.
            with open(snapshot, "r+b") as handle:
                handle.seek(18)
                handle.write(b"\x01\x01")
            wal = path.with_name(path.name + "-wal")
            try:
                if os.lstat(wal).st_size > 0:
                    notes.append("database has a non-empty -wal file; uncheckpointed changes are not visible")
            except OSError:
                pass
        return rel, tmpdir, snapshot, notes
    except BaseException:
        _remove_tree(tmpdir)
        raise
    finally:
        os.close(fd)


def _remove_tree(path: Path) -> None:
    # Windows may hold the file a moment after the child exits.
    for _ in range(20):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.05)


# ── worker ──────────────────────────────────────────────────────────────────

def _worker_source() -> str:
    return _WORKER_SOURCE_PATH.read_text(encoding="utf-8")


async def _read_bounded(stream: Any, limit: int) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise SqliteSkillError("SQLite worker produced oversized output")
        chunks.append(chunk)


async def run_worker(request: Dict[str, Any], *, deadline_s: float = DEADLINE_S) -> Dict[str, Any]:
    payload = json.dumps(request).encode("utf-8")
    cpu_seconds = str(max(1, int(deadline_s) + 1))
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", "-c", _worker_source(), cpu_seconds, str(ADDRESS_SPACE_BYTES),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_child_env(),
    )

    async def exchange() -> Tuple[bytes, bytes]:
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()
        out, err = await asyncio.gather(_read_bounded(process.stdout, MAX_RESPONSE_BYTES),
                                        _read_bounded(process.stderr, 64 * 1024))
        await process.wait()
        return out, err

    try:
        stdout, stderr = await asyncio.wait_for(exchange(), timeout=deadline_s + KILL_GRACE_S)
    except (asyncio.TimeoutError, SqliteSkillError, ConnectionError) as exc:
        if process.returncode is None:
            process.kill()
        await process.wait()
        if isinstance(exc, asyncio.TimeoutError):
            raise SqliteSkillError(f"query exceeded {deadline_s:g}s and was killed") from None
        raise SqliteSkillError(str(exc) or "SQLite worker failed") from None
    if process.returncode != 0:
        detail = stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        raise SqliteSkillError(f"SQLite worker failed (exit {process.returncode}): {detail[0][:200]}")
    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SqliteSkillError("SQLite worker returned malformed output") from exc
    if not isinstance(response, dict):
        raise SqliteSkillError("SQLite worker returned malformed output")
    if not response.get("ok"):
        raise SqliteSkillError(str(response.get("error", "query failed")))
    return response


def _base_request(snapshot: Path, mode: str) -> Dict[str, Any]:
    return {
        "uri": snapshot.resolve().as_uri() + "?mode=ro&immutable=1",
        "mode": mode,
        "op_budget": OP_BUDGET_CALLS,
        "count_budget": COUNT_BUDGET_CALLS,
        "deadline_s": DEADLINE_S,
        "heap_bytes": HEAP_BYTES,
        "max_value_bytes": MAX_VALUE_BYTES,
        "max_rows": DEFAULT_ROWS,
        "max_cell_chars": MAX_CELL_CHARS,
        "max_output_chars": MAX_OUTPUT_CHARS,
        "max_tables": MAX_TABLES,
    }


async def _with_snapshot(ctx: NativeSkillContext, user_path: str, mode: str,
                         **fields: Any) -> Tuple[str, Dict[str, Any], List[str]]:
    rel, tmpdir, snapshot, notes = await asyncio.to_thread(_snapshot, ctx, user_path)
    try:
        request = _base_request(snapshot, mode)
        request.update(fields)
        response = await run_worker(request)
    finally:
        await asyncio.to_thread(_remove_tree, tmpdir)
    return rel, response, notes + list(response.get("notes") or [])


# ── rendering ───────────────────────────────────────────────────────────────

def display(value: Any) -> str:
    """Text for one cell: exact numbers, escaped control characters."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, dict) and "blob" in value:
        return f"<blob {value['blob']} bytes {value['hex']}{'…' if value['blob'] > 16 else ''}>"
    return escape_controls(str(value))


def escape_controls(text: str) -> str:
    out: List[str] = []
    for char in text:
        code = ord(char)
        if char in "\t":
            out.append(" ")
        elif code < 32 or code == 127 or 0x80 <= code < 0xA0 or char in "‎‏‪‫‬‭‮⁦⁧⁨⁩":
            out.append(f"\\u{code:04x}" if code > 0xFF else f"\\x{code:02x}")
        else:
            out.append(char)
    return "".join(out)


def _render_rows(columns: List[str], rows: List[List[Any]], fmt: str) -> str:
    if fmt == "json":
        return json.dumps([dict(zip(columns, row)) for row in rows], ensure_ascii=False, indent=1)
    if fmt == "csv":
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if cell is None else display(cell) for cell in row])
        return buffer.getvalue().rstrip("\n")
    names = _unique_names(columns)
    table_rows = [{name: display(cell) for name, cell in zip(names, row)} for row in rows]
    return format_table(table_rows, names, max_width=80)


def _unique_names(columns: List[str]) -> List[str]:
    seen: Dict[str, int] = {}
    names = []
    for column in columns:
        name = escape_controls(column) or "?"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        names.append(name)
    return names


# ── actions ─────────────────────────────────────────────────────────────────

async def _tables(ctx: NativeSkillContext, args: List[str]) -> dict:
    if len(args) != 1:
        raise SkillUsageError("sqlite tables <db>")
    rel, response, notes = await _with_snapshot(ctx, args[0], "tables")
    tables = response.get("tables") or []
    rows = [{"name": escape_controls(str(t["name"])), "type": t["type"] + (" (virtual)" if t.get("virtual") else ""),
             "rows": "?" if t.get("rows") is None and t["type"] == "table" else ("" if t.get("rows") is None else t["rows"])}
            for t in tables]
    lines = [f"{rel}: {len(tables)} table(s)/view(s)" + (f" (first {MAX_TABLES})" if response.get("more") else "")]
    if rows:
        lines.append(format_table(rows, ["name", "type", "rows"], max_width=60))
    uncounted = [t["name"] for t in tables if t.get("error")]
    if uncounted:
        lines.append(f"row counts unavailable for {len(uncounted)} table(s): "
                     + "; ".join(f"{escape_controls(str(t['name']))}: {t['error']}" for t in tables if t.get("error"))[:600])
    lines.extend(f"note: {note}" for note in notes)
    return result("sqlite", "\n".join(lines), action="tables", path=rel, tables=tables,
                  more=bool(response.get("more")), notes=notes)


async def _schema(ctx: NativeSkillContext, args: List[str]) -> dict:
    if not 1 <= len(args) <= 2:
        raise SkillUsageError("sqlite schema <db> [table]")
    table = args[1] if len(args) == 2 else ""
    rel, response, notes = await _with_snapshot(ctx, args[0], "schema", table=table)
    if not table:
        objects = response.get("objects") or []
        body = "\n\n".join(f"-- {o['type']} {escape_controls(str(o['name']))}\n{escape_controls(str(o['sql']))};"
                           for o in objects)
        text, _ = truncate(body or "(empty schema)", _TEXT_LIMIT)
        return result("sqlite", f"{rel}: {len(objects)} schema object(s)\n{text}", action="schema", path=rel,
                      objects=objects, notes=notes)
    columns = response.get("columns") or []
    hidden = {0: "", 1: "hidden", 2: "generated (virtual)", 3: "generated (stored)"}
    rows = [{"column": escape_controls(str(c["name"])), "type": escape_controls(str(c["type"] or "")),
             "pk": c["pk"] or "", "notnull": "yes" if c["notnull"] else "",
             "default": "" if c["default"] is None else escape_controls(str(c["default"])),
             "extra": hidden.get(c["hidden"], str(c["hidden"]))} for c in columns]
    lines = [f"{rel} :: {escape_controls(table)} ({response.get('type')}, {len(columns)} column(s))",
             format_table(rows, ["column", "type", "pk", "notnull", "default", "extra"], max_width=60)]
    for index in response.get("indexes") or []:
        lines.append(f"index {escape_controls(str(index['name']))}{' unique' if index['unique'] else ''}"
                     f"{' partial' if index['partial'] else ''} ({', '.join(escape_controls(str(c)) for c in index['columns'])})")
    for key in response.get("foreign_keys") or []:
        lines.append(f"foreign key {escape_controls(str(key['from']))} -> "
                     f"{escape_controls(str(key['table']))}({escape_controls(str(key['to']))})")
    lines.extend(f"note: {note}" for note in notes)
    text, _ = truncate("\n".join(lines), _TEXT_LIMIT)
    return result("sqlite", text, action="schema", path=rel, table=table, columns=columns,
                  indexes=response.get("indexes") or [], foreign_keys=response.get("foreign_keys") or [],
                  sql=response.get("sql"), notes=notes)


def parse_query_command(rest: str) -> Tuple[Dict[str, str], str, str]:
    """``[--max-rows N] [--format F] <db> <sql>`` → (options, db, sql)."""
    options: Dict[str, str] = {}
    while True:
        word, tail = take_word(rest)
        if word is None:
            raise SkillUsageError("sqlite query needs a database and a SELECT statement")
        if word.startswith("--") and len(word) > 2:
            name, sep, inline = word[2:].partition("=")
            if name not in {"max-rows", "format"}:
                raise SkillUsageError(f"Unknown option --{name}")
            if sep:
                options[name] = inline
            else:
                value, tail = take_word(tail)
                if value is None:
                    raise SkillUsageError(f"--{name} requires a value")
                options[name] = value
            rest = tail
            continue
        return options, word, tail


async def _query(ctx: NativeSkillContext, rest: str) -> dict:
    options, db, sql = parse_query_command(rest)
    sql = check_sql(sql)
    fmt = options.get("format", "table").lower()
    if fmt not in {"table", "json", "csv"}:
        raise SkillUsageError("--format must be table, json or csv")
    raw_rows = options.get("max-rows", str(DEFAULT_ROWS))
    if not raw_rows.isdigit() or not 1 <= int(raw_rows) <= MAX_ROWS:
        raise SkillUsageError(f"--max-rows must be between 1 and {MAX_ROWS}")
    max_rows = int(raw_rows)
    rel, response, notes = await _with_snapshot(ctx, db, "query", sql=sql, max_rows=max_rows)
    columns = [str(name) for name in response.get("columns") or []]
    rows = response.get("rows") or []
    status = []
    if response.get("more"):
        status.append(f"more rows exist; showing the first {max_rows} (use --max-rows or LIMIT/OFFSET)")
    if response.get("stopped"):
        status.append(f"INCOMPLETE: {response['stopped']}")
    if response.get("cells_truncated"):
        status.append(f"cells longer than {MAX_CELL_CHARS} characters were cut")
    body = _render_rows(columns, rows, fmt) if columns else "(statement returned no columns)"
    body, display_truncated = truncate(body, _TEXT_LIMIT)
    header = f"{rel}: {len(rows)} row(s)"
    lines = [header, body, *status, *(f"note: {note}" for note in notes)]
    return result(
        "sqlite", "\n".join(lines), action="query", path=rel, sql=sql, columns=columns, rows=rows,
        row_count=len(rows), more=bool(response.get("more")), incomplete=bool(response.get("stopped")),
        stopped=response.get("stopped") or "", cells_truncated=bool(response.get("cells_truncated")),
        display_truncated=display_truncated, notes=notes, vm_steps=response.get("vm_steps"),
        sqlite_version=response.get("sqlite_version"),
    )


async def run(command: str, ctx: NativeSkillContext) -> dict:
    stripped = command.strip()
    _, _, rest = stripped.partition(" ")
    action = ""
    try:
        action_word, after_action = take_word(rest)
        action = (action_word or "").lower()
        if action in {"", "help", "--help"}:
            return result("sqlite", _USAGE, usage=True) if action else failure("sqlite", _USAGE)
        if action == "query":
            return await _query(ctx, after_action)
        _, tokens = split_command(stripped)
        if action == "tables":
            return await _tables(ctx, tokens[1:])
        if action == "schema":
            return await _schema(ctx, tokens[1:])
        return failure("sqlite", f"Unknown sqlite action {action!r}.\n{_USAGE}")
    except (SkillUsageError, SqliteSkillError, workspace.WorkspaceError) as exc:
        return failure("sqlite", f"sqlite: {exc}")
