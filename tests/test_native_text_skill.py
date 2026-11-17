from __future__ import annotations

import asyncio
import time
import unittest

from botboy.skills.native import textkit
from botboy.skills.native.base import NativeSkillContext
from botboy.skills.native.isolated_regex import RegexSandboxError, parse_flags, run_regex
from botboy.skills.native.text import run

PROSE = (
    "BotBoy is a local agent framework. It runs skills, e.g. calculators and file tools, under strict "
    "policies. Dr. Smith reviewed the design in 2025.\n\n"
    "Security comes first: every skill has a contract. Contracts declare permissions, risk and approval "
    "needs. The gateway enforces approvals for remote callers. Skill contracts make security reviewable."
)


class TextkitTests(unittest.TestCase):
    def test_sentence_splitting_respects_abbreviations_and_paragraphs(self) -> None:
        sentences = textkit.split_sentences(PROSE)
        self.assertEqual(len(sentences), 7)
        self.assertTrue(sentences[1].startswith("It runs skills, e.g. calculators"))
        self.assertEqual(sentences[2], "Dr. Smith reviewed the design in 2025.")
        german = textkit.split_sentences("Das ist z.B. ein Test. Danach folgt bzw. kommt Satz zwei.")
        self.assertEqual(len(german), 2)

    def test_sentence_spans_map_back_to_source(self) -> None:
        for start, end in textkit.sentence_spans(PROSE):
            fragment = PROSE[start:end]
            self.assertEqual(fragment, fragment.strip())
            self.assertIn(fragment[-1], ".!?")

    def test_stats_and_language_detection(self) -> None:
        stats = textkit.text_stats(PROSE)
        self.assertEqual(stats.sentences, 7)
        self.assertEqual(stats.paragraphs, 2)
        self.assertEqual(stats.language, "en")
        self.assertIn("Flesch", stats.readability_scale)
        german = textkit.text_stats("Die Katze sitzt auf der Matte und schläft, weil es warm ist.")
        self.assertEqual(german.language, "de")
        self.assertIn("Amstad", german.readability_scale)
        self.assertEqual(textkit.text_stats("").words, 0)

    def test_rake_keywords_prefer_repeated_multiword_phrases(self) -> None:
        keywords = textkit.extract_keywords(PROSE, 5)
        phrases = [keyword.phrase for keyword in keywords]
        self.assertIn("local agent framework", phrases)
        self.assertTrue(all(len(phrase.split()) <= 4 for phrase in phrases))
        self.assertFalse(any(word in textkit.STOPWORDS for phrase in phrases for word in phrase.split()))
        self.assertEqual(textkit.extract_keywords("the and of", 5), [])

    def test_summary_keeps_original_order_and_size(self) -> None:
        summary = textkit.summarize(PROSE, 2)
        self.assertEqual(len(summary.sentences), 2)
        self.assertEqual(summary.indices, sorted(summary.indices))
        self.assertEqual(summary.total_sentences, 7)
        short = textkit.summarize("One sentence only.", 3)
        self.assertEqual(short.sentences, ["One sentence only."])

    def test_chunks_cover_text_respect_bounds_and_overlap(self) -> None:
        text = (PROSE + "\n\n") * 6
        chunks = textkit.chunk_text(text, max_chars=300, overlap_chars=120)
        self.assertGreater(len(chunks), 3)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), 300)
            self.assertEqual(chunk.text, text[chunk.start:chunk.end])
        for previous, current in zip(chunks, chunks[1:]):
            self.assertLessEqual(current.start, previous.end)
            self.assertGreater(current.end, previous.end)
        covered = "".join(text[c.start:c.end] for c in chunks)
        for sentence in textkit.split_sentences(text):
            self.assertIn(sentence.split()[0], covered)
        giant = "word " * 400
        pieces = textkit.chunk_text(giant, max_chars=100, overlap_chars=0)
        self.assertTrue(all(len(piece.text) <= 100 for piece in pieces))
        with self.assertRaises(ValueError):
            textkit.chunk_text("x", max_chars=100, overlap_chars=100)

    def test_diff_line_and_word_level(self) -> None:
        inline = textkit.diff_texts("the quick brown fox", "the quick red fox jumps")
        self.assertEqual(inline.inline, "the quick [-brown-] {+red+} fox {+jumps+}")
        multi = textkit.diff_texts("a\nb\nc\n", "a\nB\nc\nd\n", a_label="old", b_label="new")
        self.assertIn("-b", multi.unified)
        self.assertIn("+B", multi.unified)
        self.assertEqual((multi.added_lines, multi.removed_lines), (2, 1))
        self.assertEqual(textkit.diff_texts("same", "same").similarity, 1.0)

    def test_markdown_stripping(self) -> None:
        cleaned = textkit.strip_markup("# Title\n\nSee [docs](http://x) and `code`.\n```python\nsecret()\n```\n- item", ".md")
        self.assertNotIn("http://x", cleaned)
        self.assertNotIn("secret()", cleaned)
        self.assertIn("Title", cleaned)
        self.assertEqual(textkit.strip_markup("# keep", ".py"), "# keep")


class RegexSandboxTests(unittest.TestCase):
    def test_matches_groups_and_flags(self) -> None:
        outcome = asyncio.run(run_regex(r"(?P<user>\w+)@(\w+)\.com", ["ALICE@example.com bob@test.com"],
                                        flags=parse_flags("i")))
        self.assertEqual([m["match"] for m in outcome.matches], ["ALICE@example.com", "bob@test.com"])
        self.assertEqual(outcome.matches[0]["named"], {"user": "ALICE"})
        self.assertEqual(outcome.group_count, 2)

    def test_catastrophic_backtracking_is_killed(self) -> None:
        started = time.perf_counter()
        with self.assertRaisesRegex(RegexSandboxError, "killed"):
            asyncio.run(run_regex(r"(a+)+$", ["a" * 40 + "!"], timeout_seconds=1.0))
        self.assertLess(time.perf_counter() - started, 4.0)

    def test_invalid_inputs(self) -> None:
        for kwargs in ({"pattern": "[", "documents": ["x"]}, {"pattern": "", "documents": ["x"]},
                       {"pattern": "a" * 1001, "documents": ["x"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RegexSandboxError):
                asyncio.run(run_regex(**kwargs))
        with self.assertRaises(RegexSandboxError):
            parse_flags("z")

    def test_match_limit_and_line_mode(self) -> None:
        outcome = asyncio.run(run_regex("x", ["x\n" * 50], mode="lines", max_matches=10))
        self.assertEqual(len(outcome.matches), 10)
        self.assertTrue(outcome.truncated)
        self.assertEqual(outcome.matches[3]["line"], 4)


class TextSkillTests(unittest.TestCase):
    def call(self, command: str) -> dict:
        return asyncio.run(run(command, NativeSkillContext()))

    def test_actions(self) -> None:
        stats = self.call(f"text stats {PROSE}")
        self.assertTrue(stats["success"])
        self.assertEqual(stats["data"]["stats"]["sentences"], 7)
        keywords = self.call(f"text keywords --top 3 {PROSE}")
        self.assertEqual(len(keywords["data"]["keywords"]), 3)
        summary = self.call(f"text summarize --sentences 2 {PROSE}")
        self.assertEqual(len(summary["data"]["sentences"]), 2)
        diff = self.call("text diff it's a cat ||| it's a dog")
        self.assertIn("[-cat-] {+dog+}", diff["output"])
        regex = self.call(r"text regex --flags i \d{4} ||| Released in 2025 and 2026")
        self.assertEqual([m["match"] for m in regex["data"]["matches"]], ["2025", "2026"])

    def test_prose_with_apostrophes_is_not_shell_parsed(self) -> None:
        result = self.call("text stats Don't split on apostrophes, it's prose.")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["stats"]["words"], 6)

    def test_usage_errors(self) -> None:
        for command in ("text", "text keywords", "text diff only one side", "text keywords --top 99 x",
                        "text keywords --bogus 1 x", "text regex (a+)+$ ||| " + "a" * 40 + "!", "text frobnicate x"):
            with self.subTest(command=command):
                self.assertFalse(self.call(command)["success"])


if __name__ == "__main__":
    unittest.main()
