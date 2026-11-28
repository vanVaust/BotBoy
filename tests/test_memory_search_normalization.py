"""German-aware memory search: CISTEM reference vectors, index migration, query safety."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from botboy.memory import simple as simple_mod
from botboy.memory import textnorm
from botboy.memory.simple import SimpleMemoryEngine
from botboy.memory.textnorm import build_match_query, cistem, normalize_for_index, tokens

_LEGACY = """
CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
                       timestamp TEXT NOT NULL, metadata TEXT);
CREATE VIRTUAL TABLE memories_fts USING fts5(content, content='memories', content_rowid='id', tokenize='porter ascii');
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""


def _tmp(case) -> Path:
    root = Path(tempfile.mkdtemp(prefix="memsearch-"))
    case.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
    return root


def _integrity(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT INTO memories_fts2(memories_fts2) VALUES ('integrity-check')")
    finally:
        conn.close()


class CistemTest(unittest.TestCase):
    def test_reference_vectors(self) -> None:
        # Case-insensitive CISTEM outputs from the NLTK reference implementation
        # docs (nltk.stem.cistem, Cistem(case_insensitive=True)).
        for word, stem in (("speicherbehältern", "speicherbehal"), ("grenzpostens", "grenzpo"),
                           ("ausgefeiltere", "ausgefeil")):
            self.assertEqual(cistem(word), stem)

    def test_inflections_conflate(self) -> None:
        groups = [("Häuser", "Haus", "Hause"), ("Straße", "Strasse", "Straßen"), ("Bücher", "Buch"),
                  ("laufen", "läuft", "gelaufen"), ("Schule", "Schulen"), ("Kinder", "Kind"),
                  ("Feiertage", "Feiertag"), ("Äpfel", "äpfel", "APFEL")]
        for group in groups:
            with self.subTest(group=group):
                self.assertEqual(len({tuple(tokens(w)) for w in group}), 1)

    def test_distinct_words_stay_distinct(self) -> None:
        for a, b in (("Haus", "Hund"), ("Schule", "Schuh"), ("Wasser", "Messer")):
            self.assertNotEqual(tokens(a), tokens(b))

    def test_long_and_odd_tokens_are_bounded(self) -> None:
        text = ("x" * 100_000) + " " + "ﬁnden" + " " + "ＡＢＣ"
        out = normalize_for_index(text)
        self.assertLessEqual(len(out.split()[0]), 64)
        self.assertIn("find", out)  # NFKC: ligature ﬁ → fi
        self.assertIn("abc", out)


class MatchQueryTest(unittest.TestCase):
    def test_syntax_is_neutralised(self) -> None:
        self.assertEqual(build_match_query('"zebra" NEAR(a b) col:x -y ^z'),
                         '"zebra" AND ("near" AND "a") AND "b" AND ("col" AND "x") AND "y" AND "z"')
        self.assertIsNone(build_match_query('*** "" ( ) :'))
        self.assertEqual(build_match_query("Häuser OR Wohnung Berl*"),
                         '(("hau" OR "häuser") OR "wohnung") AND "berl"*')
        self.assertEqual(build_match_query("OR zebra OR"), '"zebra"')
        self.assertEqual(build_match_query("Häus*"), '"haus"*')
        self.assertEqual(build_match_query("test"), '("tes" OR "test")')

    def test_every_generated_query_is_valid_fts5(self) -> None:
        engine = SimpleMemoryEngine()
        engine.store("probe")
        for query in ('a b OR c OR d', 'x* y', '"q" NEAR(a b)', 'Häuser OR Wohnung Berl*', 'col:x -y', 'test save'):
            with self.subTest(query=query):
                engine._shared_conn.execute("SELECT count(*) FROM memories_fts2 WHERE memories_fts2 MATCH ?",
                                            (build_match_query(query),)).fetchone()

    def test_query_term_cap_fails_closed(self) -> None:
        from botboy.memory.textnorm import QueryTooLong

        self.assertEqual(build_match_query(" ".join(f"w{i}" for i in range(64))).count(" AND ") + 1, 64)
        with self.assertRaises(QueryTooLong):
            build_match_query(" ".join(f"w{i}" for i in range(65)))

    def test_oversized_selector_matches_nothing(self) -> None:
        engine = SimpleMemoryEngine()
        engine.store(" ".join(f"term{i}" for i in range(64)) + " keep")
        query = " ".join(f"term{i}" for i in range(64)) + " missing"
        self.assertEqual(engine.search(query), [])  # was: matched on a truncated prefix


class SearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SimpleMemoryEngine()
        for text in ("Die Häuser in der Straße werden saniert", "Rechnung für die Wasserleitungen",
                     "Kinder spielen im Garten", "running the zebra tests", "Preis: 100% netto_wert"):
            self.engine.store(text)

    def hits(self, query):
        return [m.content for m in self.engine.search(query, limit=10)]

    def test_german_morphology_and_case(self) -> None:
        self.assertEqual(self.hits("haus strasse"), ["Die Häuser in der Straße werden saniert"])
        self.assertEqual(self.hits("Wasserleitung"), ["Rechnung für die Wasserleitungen"])
        self.assertEqual(self.hits("KIND"), ["Kinder spielen im Garten"])
        self.assertEqual(self.hits("sanieren"), ["Die Häuser in der Straße werden saniert"])

    def test_english_still_stems(self) -> None:
        self.assertEqual(self.hits("run zebras"), ["running the zebra tests"])
        # CISTEM alone maps "test"→"tes" but leaves "testing"; the raw form
        # indexed alongside keeps Porter's English conflation.
        self.engine.store("testing saved settings")
        self.assertEqual(self.hits("test save setting"), ["testing saved settings"])

    def test_or_and_prefix(self) -> None:
        self.assertEqual(len(self.hits("Garten OR Rechnung")), 2)
        self.assertEqual(self.hits("Wasserleit*"), ["Rechnung für die Wasserleitungen"])

    def test_fts_syntax_does_not_error_or_widen(self) -> None:
        self.assertEqual(self.hits('zebra NOT running'), [])  # NOT is a plain term, AND-ed
        self.assertEqual(self.hits('" OR *'), [])

    def test_like_fallback_escapes_wildcards(self) -> None:
        self.assertEqual(self.hits("%"), ["Preis: 100% netto_wert"])
        self.assertEqual(self.hits("__"), [])
        self.assertEqual(self.hits("@@@"), [])

    def test_update_and_delete_keep_index_consistent(self) -> None:
        [mid] = [m.id for m in self.engine.search("Garten")]
        self.engine.update(mid, "Hunde bellen")
        self.assertEqual(self.hits("Garten"), [])
        self.assertEqual(self.hits("Hund"), ["Hunde bellen"])
        self.engine.delete(mid)
        self.assertEqual(self.hits("Hund"), [])
        self.engine._shared_conn.execute("INSERT INTO memories_fts2(memories_fts2) VALUES ('integrity-check')")


class MigrationTest(unittest.TestCase):
    def _legacy_db(self, rows=("Die Häuser", "zebra notes")) -> Path:
        path = _tmp(self) / "memory.db"
        conn = sqlite3.connect(path)
        conn.executescript(_LEGACY)
        conn.executemany("INSERT INTO memories (content, timestamp) VALUES (?, 't')", [(r,) for r in rows])
        conn.commit()
        conn.close()
        return path

    def open(self, path):
        engine = SimpleMemoryEngine(str(path))
        self.addCleanup(engine.close)
        return engine

    def objects(self, path):
        conn = sqlite3.connect(path)
        try:
            return {name for (name,) in conn.execute("SELECT name FROM sqlite_master")}
        finally:
            conn.close()

    def test_legacy_index_is_replaced_and_backfilled(self) -> None:
        path = self._legacy_db()
        engine = self.open(path)
        self.assertEqual([m.content for m in engine.search("haus")], ["Die Häuser"])
        objects = self.objects(path)
        self.assertNotIn("memories_fts", objects)
        self.assertNotIn("memories_ai", objects)
        self.assertTrue({"memories_fts2", "memories_fts2_ai", "memories_fts2_au", "memories_fts2_ad"} <= objects)
        _integrity(path)

    def test_rows_written_by_an_older_process_are_picked_up(self) -> None:
        path = self._legacy_db()
        self.open(path)
        conn = sqlite3.connect(path)  # an old BotBoy: own triggers, no search_text
        conn.executescript(_LEGACY.replace("CREATE TABLE memories", "CREATE TABLE IF NOT EXISTS memories")
                           .replace("CREATE VIRTUAL TABLE memories_fts", "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts")
                           .replace("CREATE TRIGGER", "CREATE TRIGGER IF NOT EXISTS"))
        conn.execute("INSERT INTO memories (content, timestamp) VALUES ('Schulen offline', 't')")
        conn.commit()
        conn.close()
        engine = self.open(path)
        self.assertEqual([m.content for m in engine.search("schule")], ["Schulen offline"])
        _integrity(path)

    def test_normalisation_version_bump_rebuilds(self) -> None:
        path = self._legacy_db()
        self.open(path)
        with mock.patch.object(simple_mod, "NORMALIZATION_VERSION", "99"), \
                mock.patch.object(simple_mod, "normalize_for_index", lambda text: "rebuilt"):
            self.open(path)
        conn = sqlite3.connect(path)
        try:
            self.assertEqual({t for (t,) in conn.execute("SELECT search_text FROM memories")}, {"rebuilt"})
            self.assertEqual(conn.execute("SELECT value FROM memory_meta").fetchone()[0], "99")
            self.assertEqual(conn.execute("SELECT count(*) FROM memories_fts2 WHERE memories_fts2 MATCH 'rebuilt'")
                             .fetchone()[0], 2)
        finally:
            conn.close()
        _integrity(path)

    def test_concurrent_openers_serialise(self) -> None:
        path = self._legacy_db(rows=[f"Eintrag {i}" for i in range(2000)])
        errors, engines = [], []

        def worker():
            try:
                engines.append(SimpleMemoryEngine(str(path)))
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for engine in engines:
            self.addCleanup(engine.close)
        self.assertEqual(errors, [])
        self.assertEqual(len(engines[0].search("eintrag", limit=5000)), 2000)
        _integrity(path)

    def test_backfill_batches_cover_every_row(self) -> None:
        path = self._legacy_db(rows=[f"Zeile {i}" for i in range(1203)])
        with mock.patch.object(simple_mod, "_BACKFILL_BATCH", 100):
            engine = self.open(path)
        self.assertEqual(len(engine.search("zeile", limit=5000)), 1203)


if __name__ == "__main__":
    unittest.main()


class LockRetryTest(unittest.TestCase):
    def test_lock_conflicts_are_retried_other_errors_are_not(self) -> None:
        from botboy.db_mixin import retry_when_locked

        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        self.assertEqual(retry_when_locked(flaky), "ok")
        self.assertEqual(len(calls), 3)
        with self.assertRaisesRegex(sqlite3.OperationalError, "no such table"):
            retry_when_locked(lambda: (_ for _ in ()).throw(sqlite3.OperationalError("no such table: x")))
        with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
            retry_when_locked(lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
                              budget=0.05)

    def test_deadlocked_journal_switch_recovers(self) -> None:
        path = _tmp(self) / "busy.db"
        sqlite3.connect(path).executescript(_LEGACY)
        blocker = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        blocker.execute("BEGIN EXCLUSIVE")
        threading.Timer(0.3, lambda: blocker.execute("COMMIT")).start()
        engine = SimpleMemoryEngine(str(path))  # waits/retries instead of failing
        self.addCleanup(engine.close)
        blocker.close()
        engine.store("Häuser")
        self.assertEqual(len(engine.search("haus")), 1)
