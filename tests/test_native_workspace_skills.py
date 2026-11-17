from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from botboy.skills.native import data as data_skill
from botboy.skills.native import files as ws_skill
from botboy.skills.native import workspace
from botboy.skills.native.base import NativeSkillContext, SkillUsageError
from botboy.skills.native.data import Dataset, execute_query, parse_query

SYMLINKS = os.name != "nt"


class WorkspaceFixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root = base / "workspace"
        self.outside = base / "outside"
        (self.root / "docs").mkdir(parents=True)
        (self.root / "data").mkdir()
        (self.root / ".private").mkdir()
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("outside secret\n", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
        (self.root / ".private" / "key.txt").write_text("key\n", encoding="utf-8")
        (self.root / "docs" / "guide.md").write_text(
            "# Guide\n\nBotBoy reads workspace files safely. Every read is confined.\n"
            "Symlinks are never followed. Hidden files stay hidden.\nTODO: nothing\n",
            encoding="utf-8",
        )
        (self.root / "docs" / "guide_v2.md").write_text(
            "# Guide\n\nBotBoy reads workspace files safely. Every read is confined and capped.\n",
            encoding="utf-8",
        )
        (self.root / "blob.bin").write_bytes(b"\x00\x01binary")
        (self.root / "data" / "sales.csv").write_text(
            "date,region,product,amount,units\n"
            "2026-01-03,EU,Widget,120.5,3\n2026-01-04,US,Widget,80,2\n2026-01-05,EU,Gadget,300,1\n"
            "2026-01-06,APAC,Widget,45.25,1\n2026-01-07,EU,Widget,99.99,2\n2026-01-08,US,Gadget,,4\n",
            encoding="utf-8",
        )
        (self.root / "data" / "semi.csv").write_text("name;score\nada;3\nbob;5\n", encoding="utf-8")
        (self.root / "data" / "orders.json").write_text(json.dumps({
            "meta": {"source": "shop"},
            "orders": [
                {"id": 1, "customer": {"name": "Ada", "country": "DE"}, "total": 42.0},
                {"id": 2, "customer": {"name": "Bob", "country": "US"}, "total": 17.5},
                {"id": 3, "customer": {"name": "Cem", "country": "DE"}, "total": 99.0},
            ],
        }), encoding="utf-8")
        (self.root / "data" / "events.jsonl").write_text(
            '{"kind": "login", "ms": 12}\n{"kind": "logout", "ms": 3}\n\n{"kind": "login", "ms": 30}\n',
            encoding="utf-8",
        )
        if SYMLINKS:
            (self.root / "link.txt").symlink_to(self.outside / "secret.txt")
            (self.root / "linkdir").symlink_to(self.outside, target_is_directory=True)
        self.ctx = NativeSkillContext(workspace_root=self.root)

    def ws(self, command: str) -> dict:
        return asyncio.run(ws_skill.run(command, self.ctx))

    def data(self, command: str) -> dict:
        return asyncio.run(data_skill.run(command, self.ctx))


class WorkspaceConfinementTests(WorkspaceFixture):
    def test_rejects_escape_vectors(self) -> None:
        for path in ("/etc/passwd", "../outside/secret.txt", "docs/../../outside/secret.txt", "~/x",
                     "C:\\Windows\\win.ini", "..\\outside", ".env", ".private/key.txt", "docs/\x00x", "a" * 600):
            with self.subTest(path=path), self.assertRaises(workspace.WorkspaceError):
                workspace.resolve(self.root, path)

    @unittest.skipUnless(SYMLINKS, "symlink creation varies on Windows")
    def test_symlinks_are_never_followed(self) -> None:
        for path in ("link.txt", "linkdir/secret.txt", "linkdir"):
            with self.subTest(path=path), self.assertRaisesRegex(workspace.WorkspaceError, "Symbolic links"):
                workspace.resolve(self.root, path)
        listed = {entry.relative for entry in workspace.walk(self.root, self.root, max_depth=5, max_entries=100)}
        self.assertNotIn("link.txt", listed)
        self.assertNotIn("linkdir", listed)
        self.assertNotIn(".env", listed)

    @unittest.skipUnless(SYMLINKS and hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW is POSIX-only")
    def test_read_refuses_link_swapped_in_after_resolution(self) -> None:
        target = workspace.resolve(self.root, "docs/guide.md", expect="file")
        target.unlink()
        target.symlink_to(self.outside / "secret.txt")
        with self.assertRaises(workspace.WorkspaceError):
            workspace.read_bytes(target)

    def test_missing_or_unset_workspace_fails_closed(self) -> None:
        with self.assertRaisesRegex(workspace.WorkspaceError, "No workspace configured"):
            workspace.resolve(None, "docs")
        result = asyncio.run(ws_skill.run("ws list", NativeSkillContext()))
        self.assertFalse(result["success"])
        self.assertIn("No workspace configured", result["output"])

    def test_read_caps_and_binary_detection(self) -> None:
        data, truncated = workspace.read_bytes(self.root / "docs" / "guide.md", max_bytes=10)
        self.assertEqual((len(data), truncated), (10, True))
        with self.assertRaisesRegex(workspace.WorkspaceError, "Binary"):
            workspace.read_text(self.root / "blob.bin")
        with self.assertRaisesRegex(workspace.WorkspaceError, "exceeds"):
            workspace.read_text(self.root / "docs" / "guide.md", 10, allow_truncation=False)


class WsSkillTests(WorkspaceFixture):
    def test_list_tree(self) -> None:
        result = self.ws("ws list --depth 2")
        self.assertTrue(result["success"], result)
        paths = {entry["path"] for entry in result["data"]["entries"]}
        self.assertTrue({"docs", "docs/guide.md", "data/sales.csv", "blob.bin"} <= paths)
        self.assertFalse(any(path.startswith(".") for path in paths))
        self.assertNotIn("data/sales.csv", {e["path"] for e in self.ws("ws list")["data"]["entries"]})

    def test_read_with_line_range(self) -> None:
        result = self.ws("ws read docs/guide.md --lines 3:4")
        self.assertTrue(result["success"])
        self.assertIn("3  BotBoy reads workspace files safely.", result["output"])
        self.assertEqual(result["data"]["line_count"], 2)
        self.assertEqual(result["data"]["total_lines"], 5)
        self.assertFalse(self.ws("ws read docs/guide.md --lines 4:2")["success"])
        self.assertFalse(self.ws("ws read blob.bin")["success"])

    def test_grep_regex_fixed_and_glob(self) -> None:
        regex = self.ws("ws grep 'read(s)? ' docs --glob '*.md'")
        self.assertTrue(regex["success"], regex)
        self.assertEqual({m["path"] for m in regex["data"]["matches"]}, {"docs/guide.md", "docs/guide_v2.md"})
        fixed = self.ws("ws grep TODO --fixed")
        self.assertEqual([(m["path"], m["line"]) for m in fixed["data"]["matches"]], [("docs/guide.md", 5)])
        ignore = self.ws("ws grep todo --fixed --ignore-case")
        self.assertEqual(len(ignore["data"]["matches"]), 1)
        self.assertEqual(self.ws("ws grep secret")["data"]["matches"], [])
        redos = self.ws("ws grep '(a+)+$' docs")
        self.assertTrue(redos["success"])

    def test_stat_analysis_and_diff(self) -> None:
        stat_result = self.ws("ws stat docs/guide.md")
        self.assertEqual(stat_result["data"]["lines"], 5)
        self.assertEqual(len(stat_result["data"]["sha256"]), 64)
        summary = self.ws("ws summarize docs/guide.md --sentences 2")
        self.assertEqual(len(summary["data"]["sentences"]), 2)
        keywords = self.ws("ws keywords docs/guide.md --top 5")
        self.assertTrue(keywords["data"]["keywords"])
        chunks = self.ws("ws chunk docs/guide.md --size 60 --overlap 20")
        self.assertGreaterEqual(chunks["data"]["total_chunks"], 2)
        diff = self.ws("ws diff docs/guide.md docs/guide_v2.md")
        self.assertIn("--- docs/guide.md", diff["output"])
        self.assertIn("+BotBoy reads workspace files safely. Every read is confined and capped.", diff["output"])

    def test_usage_errors_do_not_raise(self) -> None:
        for command in ("ws", "ws read", "ws read docs/guide.md extra", "ws list --depth 99", "ws diff a",
                        "ws chunk docs/guide.md --size 10", "ws read 'unterminated", "ws explode"):
            with self.subTest(command=command):
                self.assertFalse(self.ws(command)["success"])


class DataSkillTests(WorkspaceFixture):
    def test_schema_infers_types_and_nulls(self) -> None:
        result = self.data("data schema data/sales.csv")
        self.assertTrue(result["success"], result)
        columns = {c["column"]: c for c in result["data"]["columns"]}
        self.assertEqual(columns["amount"]["type"], "float")
        self.assertEqual(columns["amount"]["nulls"], 1)
        self.assertEqual(columns["units"]["type"], "int")
        self.assertEqual(result["data"]["rows"], 6)

    def test_filter_select_sort_with_or_groups(self) -> None:
        result = self.data("data query data/sales.csv where region == EU and amount > 100 or product == Gadget "
                           "select date,amount sort amount desc")
        self.assertEqual([row["date"] for row in result["data"]["rows"]], ["2026-01-05", "2026-01-03", "2026-01-08"])
        self.assertEqual(result["data"]["columns"], ["date", "amount"])

    def test_group_by_aggregates(self) -> None:
        result = self.data("data query data/sales.csv group by region agg count sum:amount avg:amount max:units "
                           "sort sum_amount desc")
        rows = result["data"]["rows"]
        self.assertEqual(rows[0], {"region": "EU", "count": 3, "sum_amount": 520.49, "avg_amount": 173.4966666667,
                                   "max_units": 3})
        self.assertEqual(rows[-1]["region"], "APAC")

    def test_operators(self) -> None:
        cases = {
            "where amount isnull": 1,
            "where amount notnull": 5,
            "where region in EU,APAC": 4,
            "where product startswith gad": 2,
            "where product contains IDG": 4,
            "where amount != 80": 5,
            "where amount <= 80": 2,
            "where units >= 2 and region == US": 2,
        }
        for clause, expected in cases.items():
            with self.subTest(clause=clause):
                self.assertEqual(self.data(f"data query data/sales.csv {clause}")["data"]["matched"], expected)

    def test_json_jsonl_and_dialect_detection(self) -> None:
        nested = self.data("data query data/orders.json where customer.country == DE agg count sum:total")
        self.assertEqual(nested["data"]["rows"], [{"count": 2, "sum_total": 141}])
        events = self.data("data query data/events.jsonl group by kind agg count median:ms sort kind")
        self.assertEqual(events["data"]["rows"], [{"kind": "login", "count": 2, "median_ms": 21.0},
                                                  {"kind": "logout", "count": 1, "median_ms": 3.0}])
        semi = self.data("data query data/semi.csv where score > 4 format json")
        self.assertEqual(semi["data"]["rows"], [{"name": "bob", "score": 5}])
        self.assertIn('"name": "bob"', semi["output"])
        path = self.data("data head data/orders.json --path meta")
        self.assertEqual(path["data"]["rows"], [{"source": "shop"}])

    def test_stats_and_head(self) -> None:
        stats = self.data("data stats data/sales.csv amount")
        self.assertEqual(stats["data"]["count"], 5)
        self.assertEqual(stats["data"]["max"], 300.0)
        self.assertAlmostEqual(stats["data"]["mean"], 129.148)
        head = self.data("data head data/sales.csv 2")
        self.assertEqual(len(head["data"]["rows"]), 2)

    def test_limit_and_csv_format(self) -> None:
        result = self.data("data query data/sales.csv where region in EU,US limit 2 format csv")
        self.assertEqual(result["data"]["matched"], 5)
        self.assertEqual(result["data"]["row_count"], 2)
        self.assertIn("date,region,product,amount,units\n2026-01-03,EU,Widget,120.5,3", result["output"])

    def test_query_errors(self) -> None:
        for command in ("data query data/sales.csv where nope == 1", "data query data/sales.csv where amount ~ 1",
                        "data query data/sales.csv where amount >", "data query data/sales.csv where amount > 1 and",
                        "data query data/sales.csv sort nope", "data query data/sales.csv limit 0",
                        "data query data/sales.csv agg sum", "data query data/sales.csv bogus",
                        "data query data/sales.csv group region", "data schema blob.bin", "data schema docs/guide.md",
                        "data schema ../outside/secret.txt", "data stats data/sales.csv", "data"):
            with self.subTest(command=command):
                self.assertFalse(self.data(command)["success"])

    def test_oversized_file_is_refused_not_truncated(self) -> None:
        with patch.object(data_skill, "MAX_FILE_BYTES", 50):
            result = self.data("data schema data/sales.csv")
        self.assertFalse(result["success"])
        self.assertIn("exceeds", result["output"])

    def test_clause_keyword_as_value(self) -> None:
        dataset = Dataset("x", [{"status": "limit"}, {"status": "open"}], ["status"])
        rows, _, matched = execute_query(dataset, parse_query(["where", "status", "==", "limit"]))
        self.assertEqual((matched, rows), (1, [{"status": "limit"}]))
        with self.assertRaises(SkillUsageError):
            parse_query(["where", "status", "==", "x", "limit", "5000"])


if __name__ == "__main__":
    unittest.main()


class AncestorSwapTests(unittest.TestCase):
    """Review finding: replacing a parent directory with a symlink after
    resolve() let read_text() return a file outside the workspace."""

    def setUp(self) -> None:
        from botboy.skills.native import workspace

        self.workspace = workspace
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.root = base / "ws"
        (self.root / "docs").mkdir(parents=True)
        (self.root / "docs" / "note.txt").write_text("inside", encoding="utf-8")
        self.outside = base / "outside"
        self.outside.mkdir()
        (self.outside / "note.txt").write_text("SECRET", encoding="utf-8")

    @unittest.skipIf(os.name == "nt", "symlink creation needs privileges on Windows")
    def test_parent_swapped_for_symlink_after_resolve_is_refused(self) -> None:
        path = self.workspace.resolve(self.root, "docs/note.txt", expect="file")
        os.rename(self.root / "docs", self.root / "docs-old")
        os.symlink(self.outside, self.root / "docs", target_is_directory=True)
        with self.assertRaises(self.workspace.WorkspaceError):
            self.workspace.read_text(path, root=self.root)

    def test_normal_reads_still_work_with_root(self) -> None:
        path = self.workspace.resolve(self.root, "docs/note.txt", expect="file")
        self.assertEqual(self.workspace.read_text(path, root=self.root)[0], "inside")
