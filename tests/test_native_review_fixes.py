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


class ThirdReviewRoundTests(unittest.TestCase):
    def test_markdown_stripping_is_linear_and_equivalent(self) -> None:
        import random
        import re as regex

        from botboy.skills.native import textkit

        for text in ("[" * 200_000, "```" * 100_000, "![" * 100_000 + "]"):
            started = time.perf_counter()
            textkit.strip_markup(text, ".md")
            self.assertLess(time.perf_counter() - started, 1.0)
        link_re = regex.compile(r"!?\[([^\]]*)\]\([^)]*\)")
        fence_re = regex.compile(r"```.*?```", regex.DOTALL)
        rng = random.Random(7)
        for _ in range(3000):
            text = "".join(rng.choice("ab![]()`\n ") for _ in range(rng.randint(0, 40)))
            self.assertEqual(textkit._strip_links(text), link_re.sub(r"\1", text), text)
            self.assertEqual(textkit._strip_fences(text), fence_re.sub("\n", text), text)

    def test_duplicate_csv_headers_keep_every_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "x.csv").write_text("amount,amount\n10,20\n30,40\n", encoding="utf-8")
            ctx = NativeSkillContext(workspace_root=root)
            result = asyncio.run(data_skill.run("data query x.csv agg sum:amount sum:amount_2", ctx))
        self.assertTrue(result["success"], result["output"])
        self.assertEqual(result["data"]["rows"], [{"sum_amount": 40, "sum_amount_2": 60}])

    def test_sparse_schema_is_linear(self) -> None:
        rows = [{f"field_{i}": i} for i in range(20_000)]
        dataset = data_skill.Dataset(path="x", columns=[f"field_{i}" for i in range(20_000)], rows=rows)
        started = time.perf_counter()
        schema = data_skill._schema(dataset)
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(schema["data"]["columns"][5]["nulls"], 19_999)

    def test_non_ascii_url_is_percent_encoded(self) -> None:
        policy = fetch_skill.FetchPolicy()
        target = fetch_skill.vet_url("https://example.org/wiki/München?q=café&x=%41",
                                     policy, resolver=lambda host, port: ["93.184.216.34"])
        self.assertEqual(target.path, "/wiki/M%C3%BCnchen?q=caf%C3%A9&x=%41")
        target.path.encode("ascii")

    def test_malformed_link_does_not_abort_extraction(self) -> None:
        response = fetch_skill.FetchResponse(
            url="https://example.org/", status=200, reason="OK", content_type="text/html", charset="utf-8",
            body=b"<p>Useful text</p><a href='http://['>bad</a>", truncated=False, redirects=[],
            elapsed_ms=1.0,
        )
        title, text, _links = fetch_skill.readable(response)
        self.assertIn("Useful text", text)

    def test_final_newline_difference_is_reported(self) -> None:
        result = diff_texts("hello", "hello\n")
        self.assertIn("No newline at end of file", result.unified)
        self.assertLess(result.similarity, 1.0)
        self.assertEqual(diff_texts("same\n", "same\n").unified, "")

    def test_concatenated_gzip_members_are_all_decoded(self) -> None:
        import gzip

        body = gzip.compress(b"first half\n") + gzip.compress(b"second half\n")

        class GzipResponse:
            def __init__(self):
                self.chunks = [body, b""]

            def getheader(self, name, default=None):
                return {"Content-Encoding": "gzip", "Content-Length": str(len(body))}.get(name, default)

            def read1(self, _size):
                return self.chunks.pop(0)

        data, truncated = fetch_skill._read_body(GzipResponse(), fetch_skill.FetchPolicy(), time.monotonic() + 5)
        self.assertEqual((data, truncated), (b"first half\nsecond half\n", False))

    def test_quoted_charset_is_parsed(self) -> None:
        self.assertEqual(fetch_skill.charset_from_content_type('text/plain; charset="ISO-8859-1"'), "iso-8859-1")
        self.assertEqual(fetch_skill.charset_from_content_type("text/html;charset = utf-8"), "utf-8")
        self.assertIsNone(fetch_skill.charset_from_content_type("text/plain"))


class FourthReviewRoundTests(unittest.TestCase):
    def test_yaml_alias_bombs_are_refused_before_expansion(self) -> None:
        levels = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
        for i in range(1, 10):
            levels.append(f"a{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 10) + "]")
        text = ("---\nname: bomb-skill\n" + "\n".join(levels) + "\ndescription: *a9\ntriggers: [bomb]\n---\n")
        started = time.perf_counter()
        report = forge.lint_skill_text(text, "s/SKILL.md")
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertIn("yaml.alias", {finding.code for finding in report.findings})

    def test_non_string_description_is_a_finding(self) -> None:
        text = "---\nname: x-skill\ndescription: [a, b]\ntriggers: [xcount]\n---\n"
        self.assertIn("description.type", {f.code for f in forge.lint_skill_text(text, "s/SKILL.md").findings})

    def test_html_without_closing_head_keeps_body(self) -> None:
        response = fetch_skill.FetchResponse(
            url="https://example.org/", status=200, reason="OK", content_type="text/html", charset="utf-8",
            body=b"<html><head><title>Demo</title><body><p>Useful text.</p><a href='/x'>link</a></body></html>",
            truncated=False,
        )
        title, text, links = fetch_skill.readable(response)
        self.assertEqual(title, "Demo")
        self.assertIn("Useful text.", text)
        self.assertEqual(len(links), 1)

    def test_clause_keywords_work_as_column_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "o.csv").write_text("order,amount,limit\n1,10,5\n2,20,6\n", encoding="utf-8")
            ctx = NativeSkillContext(workspace_root=root)

            def query(text):
                return asyncio.run(data_skill.run(f"data query o.csv {text}", ctx))

            self.assertEqual([r["amount"] for r in query("where order == 2")["data"]["rows"]], [20])
            self.assertEqual(query("select order, limit")["data"]["columns"], ["order", "limit"])
            self.assertEqual([r["order"] for r in query("sort order desc limit 1")["data"]["rows"]], [2])
            self.assertEqual(len(query("where amount > 5 order by amount limit 1")["data"]["rows"]), 1)

    def test_flattened_key_collisions_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "c.json").write_text('[{"a.b": 10, "a": {"b": 20}}]', encoding="utf-8")
            ctx = NativeSkillContext(workspace_root=root)
            result = asyncio.run(data_skill.run("data query c.json", ctx))
        self.assertFalse(result["success"])
        self.assertIn("collide", result["output"])

    def test_sparse_query_rendering_is_bounded(self) -> None:
        import json as jsonlib

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "s.json").write_text(jsonlib.dumps([{f"f{i}": i} for i in range(20_000)]), encoding="utf-8")
            ctx = NativeSkillContext(workspace_root=root)
            started = time.perf_counter()
            result = asyncio.run(data_skill.run("data query s.json limit 1000", ctx))
        self.assertLess(time.perf_counter() - started, 5.0)
        self.assertTrue(result["success"], result["output"])
        self.assertLessEqual(len(result["data"]["columns"]), 1000)

    def test_large_reordered_diff_is_bounded(self) -> None:
        a = "\n".join(str(i) for i in range(20_000))
        lines = [str(i) for i in range(20_000)]
        for i in range(0, len(lines) - 1, 2):
            lines[i], lines[i + 1] = lines[i + 1], lines[i]
        started = time.perf_counter()
        result = diff_texts(a, "\n".join(lines))
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertIn("summary", result.unified)
        self.assertEqual(diff_texts(a, a).unified, "")


class FifthReviewRoundTests(unittest.TestCase):
    def test_sum_rejects_sequences(self) -> None:
        from botboy.skills.native.expr import ExprError, evaluate_expression

        started = time.perf_counter()
        with self.assertRaisesRegex(ExprError, "numbers only"):
            evaluate_expression("sum([[0] for i in range(10000)], [])")
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(evaluate_expression("sum([1, 2, 3.5])"), 6.5)

    def test_projection_happens_after_limit(self) -> None:
        rows = [{f"k{i}": i} for i in range(20_000)]
        dataset = data_skill.Dataset(path="x", columns=[f"k{i}" for i in range(20_000)], rows=rows)
        query = data_skill.parse_query(["select", ",".join(f"k{i}" for i in range(800)), "limit", "1"])
        started = time.perf_counter()
        result_rows, columns, matched = data_skill.execute_query(dataset, query)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual((len(result_rows), matched, len(columns)), (1, 20_000, 800))

    def test_deep_json_is_not_pretty_printed(self) -> None:
        body = ("[" * 800 + ",".join(["0"] * 200_001) + "]" * 800).encode()
        response = fetch_skill.FetchResponse(url="https://example.org/a.json", status=200, reason="OK",
                                             content_type="application/json", charset="utf-8", body=body,
                                             truncated=False)
        _, text, _ = fetch_skill.readable(response)
        self.assertLessEqual(len(text), len(body))
        shallow = fetch_skill.FetchResponse(url="https://example.org/b.json", status=200, reason="OK",
                                            content_type="application/json", charset="utf-8",
                                            body=b'{"a": [1, 2]}', truncated=False)
        self.assertIn('\n  "a"', fetch_skill.readable(shallow)[1])


class SixthReviewRoundTests(unittest.TestCase):
    def test_prod_rejects_sequences(self) -> None:
        from botboy.skills.native.expr import ExprError, evaluate_expression

        started = time.perf_counter()
        with self.assertRaisesRegex(ExprError, "numbers only"):
            evaluate_expression("prod([1]*10000, start=[0]*10000)")
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(evaluate_expression("prod([2, 3, 4])"), 24)

    def test_long_integer_cells_stay_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "ids.csv").write_text("id\n1234567890123456789\n1234567890123456790\n", encoding="utf-8")
            ctx = NativeSkillContext(workspace_root=root)
            hit = asyncio.run(data_skill.run("data query ids.csv where id == 1234567890123456789", ctx))
            stats = asyncio.run(data_skill.run("data stats ids.csv id", ctx))
        self.assertEqual([row["id"] for row in hit["data"]["rows"]], [1234567890123456789])
        self.assertEqual(stats["data"]["distinct"], 2)


class SeventhReviewRoundTests(unittest.TestCase):
    def _run(self, command: str, files: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name, content in files.items():
                (root / name).write_text(content, encoding="utf-8")
            return asyncio.run(data_skill.run(command, NativeSkillContext(workspace_root=root)))

    def test_single_line_word_diff_is_budgeted(self) -> None:
        words = [str(i) for i in range(20_000)]
        swapped = list(words)
        for i in range(0, len(swapped) - 1, 2):
            swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
        started = time.perf_counter()
        result = diff_texts(" ".join(words), " ".join(swapped))
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertIn("word-level diff skipped", result.inline)
        self.assertLess(result.similarity, 1.0)
        small = diff_texts("a b c", "a x c")
        self.assertEqual(small.inline, "a [-b-] {+x+} c")

    def test_head_prunes_sparse_columns(self) -> None:
        records = "\n".join(f'{{"f{i}": {i}}}' for i in range(200_000))
        started = time.perf_counter()
        result = self._run("data head s.jsonl 100", {"s.jsonl": records})
        self.assertLess(time.perf_counter() - started, 10.0)
        self.assertTrue(result["success"], result["output"])
        self.assertEqual(result["data"]["columns"], [f"f{i}" for i in range(100)])

    def test_extra_csv_fields_never_overwrite_named_columns(self) -> None:
        result = self._run("data query t.csv", {"t.csv": "column_2\n10,20\n30,40\n"})
        rows = result["data"]["rows"]
        self.assertEqual([row["column_2"] for row in rows], [10, 30])
        self.assertEqual([row["column_2_2"] for row in rows], [20, 40])
        agg = self._run("data query t.csv agg sum:column_2 format json", {"t.csv": "column_2\n10,20\n30,40\n"})
        self.assertEqual(agg["data"]["rows"], [{"sum_column_2": 40}])  # was 60 with the overwrite

    def test_repeated_header_names_are_linear(self) -> None:
        from botboy.skills.native.data import _distinct_headers

        started = time.perf_counter()
        names = _distinct_headers(["a"] * 200_000)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual(names[:3], ["a", "a_2", "a_3"])
        self.assertEqual(len(set(names)), 200_000)
        self.assertEqual(_distinct_headers(["a", "a_2", "a"]), ["a", "a_2", "a_3"])


class EighthReviewRoundTests(unittest.TestCase):
    def test_wide_csv_loads_linearly(self) -> None:
        header = ",".join(f"c{i}" for i in range(50_000))
        row = ",".join("1" for _ in range(50_000))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "wide.csv").write_text(header + "\n" + row + "\n", encoding="utf-8")
            started = time.perf_counter()
            dataset = data_skill.load_dataset(NativeSkillContext(workspace_root=root), "wide.csv", None)
        self.assertLess(time.perf_counter() - started, 3.0)
        self.assertEqual(len(dataset.columns), 50_000)

    def test_high_overlap_chunking_materialises_only_the_kept_prefix(self) -> None:
        from botboy.skills.native import textkit

        text = "Hello. " * 40_000
        started = time.perf_counter()
        spans = textkit.chunk_spans(text, 20_000, 19_990)
        kept = textkit.chunk_text(text, 20_000, 19_990, limit=200)
        self.assertLess(time.perf_counter() - started, 3.0)
        self.assertGreater(len(spans), 10_000)
        self.assertEqual(len(kept), 200)
        self.assertEqual(sum(len(c.text) for c in kept), sum(end - start for start, end in spans[:200]))

    def test_watchdog_abort_is_a_deadline_even_with_a_coarse_clock(self) -> None:
        # Windows CI: time.monotonic() ticks every ~16 ms, so right after the
        # watchdog aborted the read the clock could still read below the
        # deadline and the abort surfaced as "ConnectionAbortedError".
        # Simulate the worst case: a clock that has not advanced at all.
        import socket
        import threading
        import types
        from unittest import mock

        from botboy.skills.native import fetch
        from botboy.skills.native.fetch import FetchError, FetchPolicy, fetch_url

        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        stop = threading.Event()

        def drip() -> None:
            conn, _ = server.accept()
            try:
                conn.recv(4096)
                conn.sendall(b"HTTP/1.1 200 OK\r\n")
                while not stop.is_set():
                    conn.sendall(b"X-Drip: 1\r\n")
                    time.sleep(0.02)
            except OSError:
                pass
            finally:
                conn.close()

        threading.Thread(target=drip, daemon=True).start()
        self.addCleanup(server.close)
        self.addCleanup(stop.set)
        frozen = time.monotonic()
        clock = types.SimpleNamespace(monotonic=lambda: frozen)
        policy = FetchPolicy(allow_private_networks=True, allowed_ports=frozenset({port}), deadline_seconds=0.3)
        started = time.perf_counter()
        with mock.patch.object(fetch, "time", clock):
            with self.assertRaisesRegex(FetchError, "Deadline"):
                fetch_url(f"http://127.0.0.1:{port}/", policy)
        self.assertLess(time.perf_counter() - started, 2.5)

    def test_header_drip_cannot_outlive_the_deadline(self) -> None:
        import socket
        import threading

        from botboy.skills.native.fetch import FetchError, FetchPolicy, fetch_url

        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        stop = threading.Event()

        def drip() -> None:
            conn, _ = server.accept()
            try:
                conn.recv(4096)
                conn.sendall(b"HTTP/1.1 200 OK\r\n")
                while not stop.is_set():
                    conn.sendall(b"X-Drip: 1\r\n")
                    time.sleep(0.02)
            except OSError:
                pass
            finally:
                conn.close()

        thread = threading.Thread(target=drip, daemon=True)
        thread.start()
        self.addCleanup(server.close)
        self.addCleanup(stop.set)
        policy = FetchPolicy(allow_private_networks=True, allowed_ports=frozenset({port}), deadline_seconds=0.3)
        started = time.perf_counter()
        with self.assertRaisesRegex(FetchError, "Deadline"):
            fetch_url(f"http://127.0.0.1:{port}/", policy)
        self.assertLess(time.perf_counter() - started, 1.5)


class NinthReviewRoundTests(unittest.TestCase):
    def _data(self, command: str, files: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name, content in files.items():
                (root / name).write_text(content, encoding="utf-8")
            return asyncio.run(data_skill.run(command, NativeSkillContext(workspace_root=root)))

    def test_small_float_aggregates_are_not_rounded_away(self) -> None:
        result = self._data("data query m.csv agg sum:v avg:v format json", {"m.csv": "v\n1e-12\n2e-12\n"})
        row = result["data"]["rows"][0]
        self.assertAlmostEqual(row["sum_v"], 3e-12, delta=1e-24)
        self.assertAlmostEqual(row["avg_v"], 1.5e-12, delta=1e-24)
        stats = self._data("data stats m.csv v", {"m.csv": "v\n1e-12\n2e-12\n"})
        self.assertGreater(stats["data"]["stats"]["mean"] if "stats" in stats["data"] else stats["data"]["mean"], 0)

    def test_long_header_names_do_not_inflate_rows(self) -> None:
        from botboy.skills.native.base import format_table

        long_key = "k" * 2_000_000
        table = format_table([{long_key: 1}] + [{"short": 2}] * 99, [long_key, "short"])
        self.assertLess(len(table), 20_000)
        self.assertIn("k" * 39 + "…", table)

    def test_unclosed_link_targets_are_linear(self) -> None:
        from botboy.skills.native.textkit import strip_markup

        started = time.perf_counter()
        out = strip_markup("[](" * 650_000, ".md")
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertEqual(len(out), 3 * 650_000)
        self.assertEqual(strip_markup("[a](x) [b](y", ".md"), "a [b](y")

    def test_chunked_framing_drip_cannot_outlive_the_deadline(self) -> None:
        import socket
        import threading

        from botboy.skills.native.fetch import FetchError, FetchPolicy, fetch_url

        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        stop = threading.Event()

        def drip() -> None:
            conn, _ = server.accept()
            try:
                conn.recv(4096)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n"
                             b"Connection: close\r\n\r\n5")
                while not stop.is_set():
                    conn.sendall(b";x")  # endless chunk extension
                    time.sleep(0.02)
            except OSError:
                pass
            finally:
                conn.close()

        threading.Thread(target=drip, daemon=True).start()
        self.addCleanup(server.close)
        self.addCleanup(stop.set)
        policy = FetchPolicy(allow_private_networks=True, allowed_ports=frozenset({port}), deadline_seconds=0.3)
        started = time.perf_counter()
        with self.assertRaises(FetchError):
            fetch_url(f"http://127.0.0.1:{port}/", policy)
        self.assertLess(time.perf_counter() - started, 1.5)


class DeadlineClassificationTests(unittest.TestCase):
    def test_watchdog_abort_is_reported_as_deadline_with_a_coarse_clock(self) -> None:
        # Windows' monotonic clock ticks every ~15.6 ms, so right after the
        # watchdog fires it can still read "before the deadline".
        import itertools
        from unittest import mock

        from botboy.skills.native import fetch as fetch_mod

        ticks = itertools.chain([100.0], itertools.repeat(100.0))
        with mock.patch.object(fetch_mod.time, "monotonic", lambda: next(ticks)):
            test = EighthReviewRoundTests("test_header_drip_cannot_outlive_the_deadline")
            outcome = unittest.TestResult()
            test.run(outcome)
        self.assertEqual(outcome.failures + outcome.errors, [])


class TenthReviewRoundTests(unittest.TestCase):
    def test_comprehension_budget_is_enforced_while_building(self) -> None:
        from botboy.skills.native.expr import ExprError, SafeEvaluator

        started = time.perf_counter()
        with self.assertRaisesRegex(ExprError, "expands to more than"):
            SafeEvaluator().evaluate("[range(2**4000, 2**4000 + 10000) for i in range(10000)]")
        self.assertLess(time.perf_counter() - started, 1.0)
        value, _ = SafeEvaluator().evaluate("[[j for j in range(3)] for i in range(4)]")
        self.assertEqual(value, [[0, 1, 2]] * 4)

    def test_repeated_csv_columns_and_huge_cells_are_bounded(self) -> None:
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "big.json").write_text(_json.dumps([{"f": "x" * 1_000_000}]), encoding="utf-8")
            command = "data query big.json select " + ",".join(["f"] * 800) + " limit 1 format csv"
            result = asyncio.run(data_skill.run(command, NativeSkillContext(workspace_root=root)))
        self.assertTrue(result["success"], result["output"])
        self.assertEqual(result["data"]["columns"], ["f"])
        self.assertLess(len(result["output"]), 70_000)


class EleventhReviewRoundTests(unittest.TestCase):
    def test_repeated_group_columns_are_bounded(self) -> None:
        rows = "id\n" + "\n".join(str(i) for i in range(20_000)) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "ids.csv").write_text(rows, encoding="utf-8")
            command = "data query ids.csv group by " + ",".join(["id"] * 800) + " agg count limit 1"
            result = asyncio.run(data_skill.run(command, NativeSkillContext(workspace_root=root)))
        self.assertTrue(result["success"], result["output"])
        self.assertEqual(len(result["data"]["rows"]), 1)
        self.assertEqual(set(result["data"]["rows"][0]), {"id", "count"})

    def test_group_key_budget_is_enforced(self) -> None:
        from unittest import mock

        from botboy.skills.native import data as data_mod

        rows = "k\n" + "\n".join("v" * 50 + str(i) for i in range(1000)) + "\n"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(data_mod, "MAX_GROUP_KEY_CHARS", 10_000):
            root = Path(tmp).resolve()
            (root / "k.csv").write_text(rows, encoding="utf-8")
            result = asyncio.run(data_skill.run("data query k.csv group by k agg count",
                                                NativeSkillContext(workspace_root=root)))
        self.assertFalse(result["success"])
        self.assertIn("group by keys exceed", result["output"])

    def test_scaffold_descriptions_round_trip_through_yaml(self) -> None:
        import yaml

        from botboy.skills.native.forge import scaffold

        for description in (r"Count matches for the regex \d+ in text", r"tab \t and \n stay literal",
                            'quote " and colon: ok', "Umlaute äöü ß"):
            with self.subTest(description=description):
                manifest = scaffold("regex-helper", "rxhelp", description=description)
                meta = yaml.safe_load(manifest.split("---")[1])
                self.assertEqual(meta["description"], description)

    def test_non_finite_results_are_not_embedded_as_json(self) -> None:
        import json as _json

        from botboy.skills.native.expr import _json_compatible

        for value in (float("inf"), float("nan"), [1, float("-inf")]):
            self.assertFalse(_json_compatible(value))
        self.assertTrue(_json_compatible([1, 2.5, None]))
        from botboy.skills.native import expr as expr_skill

        result = asyncio.run(expr_skill.run("expr [1, inf]", NativeSkillContext(workspace_root=Path("."))))
        _json.dumps(result.get("data"), allow_nan=False)  # must not raise
