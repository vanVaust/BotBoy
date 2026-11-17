"""Regression tests for the automated review findings on PR #6 (P2 items)."""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from botboy.skills.native import data as data_skill
from botboy.skills.native import fetch as fetch_skill
from botboy.skills.native import forge
from botboy.skills.native.base import NativeSkillContext, SkillUsageError
from botboy.skills.native.textkit import diff_texts


class DataSkillPrecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.ctx = NativeSkillContext(workspace_root=self.root)

    def call(self, command: str) -> dict:
        return asyncio.run(data_skill.run(command, self.ctx))

    def test_large_integer_ids_stay_distinct(self) -> None:
        (self.root / "ids.csv").write_text("id\n9007199254740992\n9007199254740993\n", encoding="utf-8")
        result = self.call("data query ids.csv where id == 9007199254740993")
        self.assertTrue(result["success"], result["output"])
        self.assertEqual([row["id"] for row in result["data"]["rows"]], [9007199254740993])
        stats = self.call("data stats ids.csv id")
        self.assertEqual(stats["data"]["sum"], 18014398509481985)
        self.assertEqual(stats["data"]["max"], 9007199254740993)

    def test_partial_statistics_are_disclosed(self) -> None:
        (self.root / "big.csv").write_text("amount\n" + "1\n" * 30 + "1000000\n", encoding="utf-8")
        with patch.object(data_skill, "MAX_ROWS", 30):
            stats = self.call("data stats big.csv amount")
        self.assertTrue(stats["data"]["partial"])
        self.assertIn("partial statistics", stats["output"])
        complete = self.call("data stats big.csv amount")
        self.assertFalse(complete["data"]["partial"])
        self.assertEqual(complete["data"]["max"], 1000000)

    def test_schema_distinct_counts_every_loaded_row(self) -> None:
        dataset = data_skill.Dataset(path="x", columns=["id"], rows=[{"id": i} for i in range(50_001)])
        schema = data_skill._schema(dataset)
        self.assertEqual(schema["data"]["columns"][0]["distinct"], 50_001)


class DiffCountTests(unittest.TestCase):
    def test_content_starting_with_plus_plus_or_minus_minus_is_counted(self) -> None:
        result = diff_texts("old\n--counter;\n", "old\n++counter;\n")
        self.assertEqual((result.added_lines, result.removed_lines), (1, 1))


class ForgeReviewTests(unittest.TestCase):
    def test_non_mapping_runtime_is_a_finding_not_a_crash(self) -> None:
        text = ("---\nname: x-skill\ndescription: Counts words in the given text for testing.\n"
                "triggers: [xcount]\nlayer: {runtime: [subprocess]}\n---\n```python\n_result = 1\n```\n")
        report = forge.lint_skill_text(text, "s/SKILL.md")
        self.assertIn("layer.runtime.type", {finding.code for finding in report.findings})
        self.assertFalse(report.executable)

    def test_agent_trigger_is_reserved(self) -> None:
        with self.assertRaises(SkillUsageError):
            forge.scaffold("word-counter", "agent", description="Counts words in a text for testing.")


class FetchDeadlineTests(unittest.TestCase):
    def test_drip_fed_body_hits_the_overall_deadline(self) -> None:
        class DripResponse:
            def getheader(self, name, default=None):
                return default

            def read1(self, _size):
                time.sleep(0.02)
                return b"x"

            def read(self, _size):
                raise AssertionError("read(n) would wait to fill its buffer")

        policy = fetch_skill.FetchPolicy(max_bytes=10_000_000)
        started = time.monotonic()
        with self.assertRaisesRegex(fetch_skill.FetchError, "deadline"):
            fetch_skill._read_body(DripResponse(), policy, started + 0.2)
        self.assertLess(time.monotonic() - started, 0.6)


if __name__ == "__main__":
    unittest.main()
