from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
import warnings
from pathlib import Path

from botboy.cache import _BYPASS, _ttl_for_command


class CacheTtlPolicyTests(unittest.TestCase):
    def test_writes_deletes_and_unknown_prefixes_are_never_cached(self):
        for command in ("remember x", "store x", "save x", "forget x", "delete x", "history clear",
                        "schedule add a 5m", "agent do it", "plan something", "think hard",
                        "unknown-custom-skill arg", "time", ""):
            with self.subTest(command=command):
                self.assertEqual(_ttl_for_command(command), _BYPASS)

    def test_known_pure_reads_stay_cacheable(self):
        for command in ("calculate 2+2", "hash abc", "status", "search zebra", "skills", "help"):
            with self.subTest(command=command):
                self.assertNotEqual(_ttl_for_command(command), _BYPASS)


class CachedAliasRegressionTest(unittest.TestCase):
    """`save x` twice returned the cached confirmation and stored nothing."""

    def test_repeated_alias_writes_and_deletes_take_effect(self):
        from botboy.__main__ import BotBoy
        from botboy.core.config import BotBoyConfig

        root = Path(tempfile.mkdtemp(prefix="cache-alias-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        config = BotBoyConfig()
        for name in ("memory", "history", "scheduler", "trace", "tasks"):
            getattr(config, name).db_path = str(root / f"{name}.db")
        config.tasks.artifact_root = str(root / "artifacts")
        config.security.principal_db_path = str(root / "principals.db")
        config.security.auth_api_key_store_path = str(root / "keys.db")
        config.security.jwt_secret_file = str(root / "jwt.secret")
        config.llm.enabled = False
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bot = BotBoy(config)
            self.assertTrue(bot.initialize())
        self.addCleanup(bot.shutdown)

        def run(command):
            return asyncio.run(bot.process_command(command, principal="local-cli"))["output"]

        self.assertIn("No results", run("search alpha"))  # primes the 30 s read cache
        self.assertIn("id=1", run("save alpha note"))
        self.assertIn("Found 1", run("search alpha"))
        self.assertIn("id=2", run("save alpha note"))
        self.assertIn("Deleted 2", run("delete alpha"))
        self.assertIn("id=", run("save alpha note"))
        self.assertIn("Deleted 1", run("delete alpha"))
        self.assertIn("No results", run("search alpha"))

    def test_writes_invalidate_cached_reads(self):
        from botboy.command_effects import mutates_state

        for command in ("remember x", "save x", "store x", "forget x", "delete x", "history clear",
                        "schedule add a 5m", "schedule cancel a", "agent t"):
            self.assertTrue(mutates_state(command), command)
        for command in ("search x", "memories", "status", "schedule list", "history", ""):
            self.assertFalse(mutates_state(command), command)


if __name__ == "__main__":
    unittest.main()
