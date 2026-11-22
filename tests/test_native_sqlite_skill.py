"""``sqlite`` native skill: read-only SQL, confinement, and adversarial databases."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from botboy.skills.native import sqlitedb
from botboy.skills.native.base import NativeSkillContext, SkillUsageError, take_word


def run(command: str, ctx: NativeSkillContext) -> dict:
    return asyncio.run(sqlitedb.run(command, ctx))


def make_db(path: Path, script: str, params=()) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        for sql, values in params:
            connection.execute(sql, values)
        connection.commit()
    finally:
        connection.close()


class SqliteSkillTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.ws = self.root / "ws"
        self.ws.mkdir()
        self.ctx = NativeSkillContext(workspace_root=self.ws)
        make_db(self.ws / "shop.db", """
            CREATE TABLE orders(id INTEGER PRIMARY KEY, region TEXT NOT NULL, amount REAL, big INTEGER,
                                note TEXT, data BLOB, customer INTEGER REFERENCES customers(id));
            CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT UNIQUE);
            CREATE INDEX ix_region ON orders(region);
            CREATE VIEW totals AS SELECT region, sum(amount) AS total FROM orders GROUP BY region;
            INSERT INTO customers VALUES (1, 'ada'), (2, 'grace');
        """, [
            ("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", (1, "EU", 10.5, 9007199254740993, "hello", b"\x00\xff", 1)),
            ("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", (2, "US", 0.1, -(2 ** 63), "line1\nline2\u202e\x1b[31m", None, 2)),
            ("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", (3, "EU", 1 / 3, 2 ** 63 - 1, None, None, 1)),
        ])


class QueryTests(SqliteSkillTestCase):
    def test_tables_schema_and_query(self) -> None:
        tables = run("sqlite tables shop.db", self.ctx)
        self.assertTrue(tables["success"], tables["output"])
        counts = {t["name"]: t["rows"] for t in tables["data"]["tables"]}
        self.assertEqual(counts, {"customers": 2, "orders": 3, "totals": None})

        schema = run("sqlite schema shop.db orders", self.ctx)
        self.assertTrue(schema["success"], schema["output"])
        self.assertEqual([c["name"] for c in schema["data"]["columns"]][:3], ["id", "region", "amount"])
        self.assertIn("index ix_region (region)", schema["output"])
        self.assertIn("foreign key customer -> customers(id)", schema["output"])
        whole = run("sqlite schema shop.db", self.ctx)
        self.assertIn("CREATE VIEW totals", whole["output"])

        query = run("sqlite query shop.db SELECT region, total FROM totals ORDER BY region", self.ctx)
        self.assertTrue(query["success"], query["output"])
        self.assertEqual(query["data"]["rows"], [["EU", 10.5 + 1 / 3], ["US", 0.1]])

    def test_numbers_are_exact_and_cells_are_escaped(self) -> None:
        out = run("sqlite query shop.db SELECT id, amount, big, note, data FROM orders ORDER BY id", self.ctx)
        self.assertTrue(out["success"], out["output"])
        rows = out["data"]["rows"]
        self.assertEqual([row[2] for row in rows], [9007199254740993, -(2 ** 63), 2 ** 63 - 1])
        self.assertEqual(rows[2][1], 1 / 3)
        self.assertIn(repr(1 / 3), out["output"])  # no 6-significant-digit rounding
        self.assertEqual(rows[0][4], {"blob": 2, "hex": "00ff"})
        self.assertIn("line1\\x0aline2\\u202e\\x1b[31m", out["output"])
        self.assertNotIn("\x1b", out["output"])

    def test_formats_options_and_row_cap(self) -> None:
        js = run("sqlite query --format json shop.db SELECT id FROM orders ORDER BY id", self.ctx)
        self.assertIn('"id": 1', js["output"])
        csv_out = run("sqlite query --format=csv --max-rows 2 shop.db SELECT id, region FROM orders ORDER BY id",
                      self.ctx)
        self.assertIn("id,region\n1,EU\n2,US", csv_out["output"])
        self.assertTrue(csv_out["data"]["more"])
        self.assertIn("more rows exist", csv_out["output"])
        for bad in ("--max-rows 0", "--max-rows 1001", "--format xml", "--nope 1"):
            with self.subTest(option=bad):
                self.assertFalse(run(f"sqlite query {bad} shop.db SELECT 1", self.ctx)["success"])

    def test_quoted_paths_with_spaces(self) -> None:
        make_db(self.ws / "my data.db", "CREATE TABLE t(x); INSERT INTO t VALUES (42);")
        out = run("sqlite query 'my data.db' SELECT x FROM t", self.ctx)
        self.assertTrue(out["success"], out["output"])
        self.assertEqual(out["data"]["rows"], [[42]])
        self.assertTrue(run('sqlite tables "my data.db"', self.ctx)["success"])

    def test_cells_and_output_are_capped(self) -> None:
        make_db(self.ws / "wide.db", "CREATE TABLE t(x);", [("INSERT INTO t VALUES (?)", ("y" * 50_000,))])
        out = run("sqlite query wide.db SELECT x FROM t", self.ctx)
        self.assertTrue(out["data"]["cells_truncated"])
        self.assertEqual(len(out["data"]["rows"][0][0]), sqlitedb.MAX_CELL_CHARS)

    def test_usage_errors(self) -> None:
        self.assertIn("Usage", run("sqlite", self.ctx)["output"])
        self.assertTrue(run("sqlite help", self.ctx)["success"])
        self.assertFalse(run("sqlite frobnicate shop.db", self.ctx)["success"])
        self.assertFalse(run("sqlite query shop.db", self.ctx)["success"])
        self.assertFalse(run("sqlite query 'unterminated SELECT 1", self.ctx)["success"])
        self.assertIn("no table or view", run("sqlite schema shop.db missing", self.ctx)["output"])


class ReadOnlyTests(SqliteSkillTestCase):
    def assert_refused(self, sql: str, needle: str = "") -> dict:
        out = run(f"sqlite query shop.db {sql}", self.ctx)
        self.assertFalse(out["success"], (sql, out["output"]))
        if needle:
            self.assertIn(needle, out["output"].lower())
        return out

    def test_gate_admits_only_select_with_values(self) -> None:
        for sql in ("DELETE FROM orders", "UPDATE orders SET amount = 0", "DROP TABLE orders",
                    "ATTACH DATABASE 'x.db' AS x", "PRAGMA writable_schema=1", "VACUUM INTO 'copy.db'",
                    "/* hidden */ INSERT INTO orders(region) VALUES ('x')", "-- comment\nDELETE FROM orders",
                    "BEGIN", "REINDEX", "ANALYZE", "CREATE TABLE z(a)", "/* unterminated SELECT 1"):
            with self.subTest(sql=sql):
                self.assert_refused(sql, "only read-only")
        self.assertTrue(run("sqlite query shop.db VALUES (1), (2)", self.ctx)["success"])
        self.assertTrue(run("sqlite query shop.db /* c */ WITH t AS (SELECT 1 AS x) SELECT x FROM t;",
                            self.ctx)["success"])

    def test_authorizer_denies_what_the_gate_lets_through(self) -> None:
        self.assert_refused("WITH x AS (SELECT 1) DELETE FROM orders", "not authorized")
        self.assert_refused("WITH x AS (SELECT 1) INSERT INTO orders(region) SELECT 'x' FROM x", "not authorized")
        self.assert_refused("SELECT load_extension('evil')", "load_extension")
        self.assert_refused("SELECT * FROM pragma_table_info('orders')", "pragma")
        self.assert_refused("SELECT zeroblob(10)", "zeroblob")
        self.assert_refused("SELECT 1; DROP TABLE orders")
        self.assertEqual(run("sqlite tables shop.db", self.ctx)["data"]["tables"][1]["rows"], 3)

    def test_worker_denies_attach_and_vacuum_into_without_the_gate(self) -> None:
        # Bypass the lexical gate and talk to the worker directly: the
        # authorizer and query_only must still stop file-writing statements.
        target = self.root / "exfil.db"
        rel, tmpdir, snapshot, _ = sqlitedb._snapshot(self.ctx, "shop.db")
        try:
            for sql in (f"VACUUM INTO '{target.as_posix()}'", f"ATTACH DATABASE '{target.as_posix()}' AS x",
                        "PRAGMA query_only=0", "DELETE FROM orders"):
                with self.subTest(sql=sql):
                    request = sqlitedb._base_request(snapshot, "query")
                    request["sql"] = sql
                    with self.assertRaises(sqlitedb.SqliteSkillError):
                        asyncio.run(sqlitedb.run_worker(request))
                    self.assertFalse(target.exists())
        finally:
            sqlitedb._remove_tree(tmpdir)

    def test_workspace_file_is_never_modified(self) -> None:
        path = self.ws / "shop.db"
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        run("sqlite query shop.db SELECT * FROM orders", self.ctx)
        run("sqlite query shop.db WITH x AS (SELECT 1) DELETE FROM orders", self.ctx)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertEqual(sorted(p.name for p in self.ws.iterdir()), ["shop.db"])


class ResourceTests(SqliteSkillTestCase):
    def test_runaway_recursion_hits_the_work_budget(self) -> None:
        started = time.perf_counter()
        out = run("sqlite query shop.db WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) "
                  "SELECT count(*) FROM r", self.ctx)
        self.assertFalse(out["success"])
        self.assertIn("budget exhausted", out["output"])
        self.assertLess(time.perf_counter() - started, sqlitedb.DEADLINE_S + sqlitedb.KILL_GRACE_S + 5)

    def test_string_doubling_is_stopped_by_memory_limits(self) -> None:
        inner = "SELECT 'abcdefgh' AS x"
        for _ in range(40):
            inner = f"SELECT x || x AS x FROM ({inner})"
        out = run(f"sqlite query shop.db SELECT length(x) FROM ({inner})", self.ctx)
        self.assertFalse(out["success"], out["output"])
        self.assertTrue(any(word in out["output"].lower() for word in ("memory", "too big", "budget")),
                        out["output"])

    def test_hostile_schema_expressions_are_bounded(self) -> None:
        # A generated column is evaluated on read and bypasses the function
        # allowlist (schema is parsed without the authorizer); the heap and
        # length limits still apply.
        expression = "'aaaa'"
        for _ in range(9):
            expression = f"replace({expression}, 'a', 'aaaaaaaaaa')"  # 4 * 10**9 characters
        # Added after the row exists, so creating the file never evaluates them.
        make_db(self.ws / "gen.db",
                "CREATE TABLE g(a INTEGER); INSERT INTO g(a) VALUES (1);"
                f"ALTER TABLE g ADD COLUMN b TEXT GENERATED ALWAYS AS ({expression}) VIRTUAL;"
                "ALTER TABLE g ADD COLUMN c TEXT GENERATED ALWAYS AS (printf('%.*c', 600000000, 'x')) VIRTUAL;")
        started = time.perf_counter()
        out = run("sqlite query gen.db SELECT length(b) FROM g", self.ctx)
        self.assertFalse(out["success"], out["output"])
        self.assertTrue(any(word in out["output"].lower() for word in ("memory", "too big")), out["output"])
        printf = run("sqlite query gen.db SELECT length(c) FROM g", self.ctx)
        # Either refused or clamped by SQLite; never a 600 MB value.
        self.assertTrue(not printf["success"] or printf["data"]["rows"] in ([[None]], [[0]]), printf["output"])
        self.assertLess(time.perf_counter() - started, 20)

    def test_wall_clock_kill_bounds_a_worker_that_ignores_its_budget(self) -> None:
        rel, tmpdir, snapshot, _ = sqlitedb._snapshot(self.ctx, "shop.db")
        try:
            request = sqlitedb._base_request(snapshot, "query")
            request.update(op_budget=10 ** 12, deadline_s=600,
                           sql="WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r")
            started = time.perf_counter()
            with mock.patch.object(sqlitedb, "KILL_GRACE_S", 0.2):
                with self.assertRaises(sqlitedb.SqliteSkillError) as caught:
                    asyncio.run(sqlitedb.run_worker(request, deadline_s=0.5))
            self.assertIn("killed", str(caught.exception))
            self.assertLess(time.perf_counter() - started, 10)
        finally:
            sqlitedb._remove_tree(tmpdir)

    def test_oversized_and_non_sqlite_files_are_refused(self) -> None:
        (self.ws / "fake.db").write_bytes(b"not a database at all" * 10)
        self.assertIn("not an SQLite 3 database", run("sqlite tables fake.db", self.ctx)["output"])
        with mock.patch.object(sqlitedb, "MAX_DB_BYTES", 1024):
            self.assertIn("limit", run("sqlite tables shop.db", self.ctx)["output"])

    def test_malformed_database_fails_cleanly(self) -> None:
        data = bytearray((self.ws / "shop.db").read_bytes())
        for offset in range(100, len(data), 97):
            data[offset] = (data[offset] * 7 + 13) & 0xFF
        (self.ws / "broken.db").write_bytes(bytes(data))
        for command in ("sqlite tables broken.db", "sqlite query broken.db SELECT * FROM orders",
                        "sqlite schema broken.db orders"):
            with self.subTest(command=command):
                out = run(command, self.ctx)
                self.assertIn("type", out)  # a result, not a crash
                if not out["success"]:
                    self.assertTrue(out["output"].startswith("sqlite"))

    def test_temporary_snapshots_are_removed(self) -> None:
        before = set(Path(tempfile.gettempdir()).glob("botboy-sqlite-*"))
        run("sqlite query shop.db SELECT 1", self.ctx)
        run("sqlite query shop.db SELECT nope FROM nowhere", self.ctx)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("botboy-sqlite-*")), before)


class ConfinementTests(SqliteSkillTestCase):
    def test_paths_outside_and_hidden_are_refused(self) -> None:
        make_db(self.root / "outside.db", "CREATE TABLE secret(x);")
        (self.ws / ".hidden").mkdir()
        make_db(self.ws / ".hidden" / "h.db", "CREATE TABLE t(x);")
        for target in ("../outside.db", str(self.root / "outside.db"), ".hidden/h.db", "~/x.db", "C:/x.db"):
            with self.subTest(target=target):
                out = run(f"sqlite tables '{target}'", self.ctx)
                self.assertFalse(out["success"])
                self.assertNotIn("secret", out["output"])

    @unittest.skipIf(sys.platform == "win32", "symlinks need privileges on Windows")
    def test_symlinked_database_is_refused(self) -> None:
        make_db(self.root / "outside.db", "CREATE TABLE secret(x);")
        os.symlink(self.root / "outside.db", self.ws / "link.db")
        out = run("sqlite tables link.db", self.ctx)
        self.assertFalse(out["success"])
        self.assertIn("Symbolic links", out["output"])

    @unittest.skipIf(sys.platform == "win32", "dir_fd pinning is POSIX-only")
    def test_snapshot_reads_through_the_pinned_descriptor(self) -> None:
        # Swap the checked path for a symlink between resolve() and open():
        # the descriptor walk refuses to follow it.
        make_db(self.root / "outside.db", "CREATE TABLE secret(x);")
        from botboy.skills.native import workspace

        original = workspace.resolve

        def swapping_resolve(root, user_path, **kwargs):
            resolved = original(root, user_path, **kwargs)
            resolved.unlink()
            os.symlink(self.root / "outside.db", resolved)
            return resolved

        with mock.patch.object(workspace, "resolve", swapping_resolve):
            out = run("sqlite tables shop.db", self.ctx)
        self.assertFalse(out["success"])
        self.assertNotIn("secret", out["output"])

    def test_read_only_file_is_readable(self) -> None:
        path = self.ws / "ro.db"
        make_db(path, "CREATE TABLE t(x); INSERT INTO t VALUES (1);")
        os.chmod(path, stat.S_IREAD)
        self.addCleanup(os.chmod, path, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual(run("sqlite query ro.db SELECT x FROM t", self.ctx)["data"]["rows"], [[1]])

    def test_wal_database_with_pending_changes_is_reported(self) -> None:
        path = self.ws / "wal.db"
        writer = sqlite3.connect(path)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE t(x)")
        writer.commit()
        writer.execute("INSERT INTO t VALUES (1)")
        writer.commit()
        out = run("sqlite query wal.db SELECT count(*) FROM sqlite_master", self.ctx)
        self.assertTrue(out["success"], out["output"])
        self.assertIn("-wal", out["output"])

    def test_no_workspace_fails_closed(self) -> None:
        out = run("sqlite tables shop.db", NativeSkillContext(workspace_root=None))
        self.assertFalse(out["success"])
        self.assertIn("No workspace configured", out["output"])


class HelperTests(unittest.TestCase):
    def test_leading_keyword(self) -> None:
        cases = {"SELECT 1": "select", "  /* x */ -- y\n with t as (select 1) select 1": "with",
                 "/* never closed": "", "-- only comment": "", "(SELECT 1)": "", "values(1)": "values"}
        for sql, expected in cases.items():
            with self.subTest(sql=sql):
                self.assertEqual(sqlitedb.leading_keyword(sql), expected)

    def test_leading_keyword_is_linear(self) -> None:
        started = time.perf_counter()
        sqlitedb.leading_keyword("/*" * 200_000 + "*/ SELECT 1")
        sqlitedb.leading_keyword("-- \n" * 200_000 + "SELECT 1")
        self.assertLess(time.perf_counter() - started, 2)

    def test_take_word_matches_shlex_quote(self) -> None:
        import shlex

        for value in ("plain", "with space", "it's", 'say "hi"', "back\\slash", "", "--x", "ü ñ"):
            with self.subTest(value=value):
                word, rest = take_word(shlex.quote(value) + "  SELECT 'x'")
                self.assertEqual(word, value)
                self.assertEqual(rest, "SELECT 'x'")
        self.assertEqual(take_word('"a\\"b" tail'), ('a"b', "tail"))
        self.assertEqual(take_word("   "), (None, ""))
        with self.assertRaises(SkillUsageError):
            take_word("'open")

    def test_parse_query_command(self) -> None:
        self.assertEqual(sqlitedb.parse_query_command("--max-rows 5 a.db SELECT 1"),
                         ({"max-rows": "5"}, "a.db", "SELECT 1"))
        self.assertEqual(sqlitedb.parse_query_command("a.db SELECT '--max-rows'"),
                         ({}, "a.db", "SELECT '--max-rows'"))


if __name__ == "__main__":
    unittest.main()
