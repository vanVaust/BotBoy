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


class SecondReviewRoundTests(unittest.TestCase):
    def test_sentence_splitting_is_linear_on_punctuation_runs(self) -> None:
        from botboy.skills.native.textkit import sentence_spans, split_sentences

        for text in ("!" * 200_000, "x. " * 50_000, "a" + "." * 100_000 + "b"):
            started = time.perf_counter()
            sentence_spans(text)
            self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(split_sentences("Hello world! How are you? Fine, e.g. good."),
                         ["Hello world!", "How are you?", "Fine, e.g. good."])

    def test_comprehension_results_respect_expanded_size(self) -> None:
        from botboy.skills.native.expr import ExprError, SafeEvaluator, evaluate_expression

        program = ("a=[0]*10000; b=[0]*10000; c=[a for i in range(10000)]; d=[b for i in range(10000)]; "
                   "xs=[c for i in range(10000)]; ys=[d for i in range(10000)]; min(xs, ys)")
        started = time.perf_counter()
        with self.assertRaisesRegex(ExprError, "expands to more than"):
            SafeEvaluator().evaluate(program)
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(evaluate_expression("[x * 2 for x in range(5)]"), [0, 2, 4, 6, 8])

    def test_aggregate_names_never_overwrite_group_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "counts.csv").write_text("count\n10\n10\n20\n", encoding="utf-8")
            ctx = NativeSkillContext(workspace_root=root)
            result = asyncio.run(data_skill.run("data query counts.csv group by count", ctx))
        self.assertTrue(result["success"], result["output"])
        rows = sorted(result["data"]["rows"], key=lambda row: row["count"])
        self.assertEqual(rows, [{"count": 10, "count_agg": 2}, {"count": 20, "count_agg": 1}])

    def test_premature_eof_is_not_a_complete_fetch(self) -> None:
        class ShortResponse:
            def __init__(self):
                self.chunks = [b"partial", b""]

            def getheader(self, name, default=None):
                return "100" if name == "Content-Length" else default

            def read1(self, _size):
                return self.chunks.pop(0)

        with self.assertRaisesRegex(fetch_skill.FetchError, "7 of 100"):
            fetch_skill._read_body(ShortResponse(), fetch_skill.FetchPolicy(), time.monotonic() + 5)

    def test_native_skill_names_are_rejected(self) -> None:
        with self.assertRaises(SkillUsageError):
            forge.scaffold("expr", "uniquetrigger", description="Counts words in a text for testing.")
        text = ("---\nname: expr\ndescription: Counts words in the given text for testing.\n"
                "triggers: [uniquetrigger]\n---\n```python\n_result = 1\n```\n")
        report = forge.lint_skill_text(text, "s/SKILL.md")
        self.assertIn("name.native", {finding.code for finding in report.findings})
        self.assertFalse(report.executable)
