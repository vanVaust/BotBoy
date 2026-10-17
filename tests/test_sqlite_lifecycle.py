"""Resource ownership regressions that also detect Windows database locks."""
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.db_mixin import _SQLiteMixin
from botboy.gateway.server import create_app
from botboy.gateway.simple_server_support import (
    build_simple_handler_state,
    close_simple_handler_stores,
)


class SQLiteLifecycleTests(unittest.TestCase):
    def bot(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        config = BotBoyConfig()
        for section in ("memory", "history", "scheduler", "trace", "tasks"):
            getattr(config, section).db_path = str(root / (section + ".db"))
        config.tasks.artifact_root = str(root / "artifacts")
        config.security.principal_db_path = str(root / "principals.db")
        config.security.auth_api_key_store_path = str(root / "keys.db")
        config.security.token_revocation_db_path = str(root / "revocations.db")
        config.security.jwt_secret = "lifecycle-test-only-secret-123456789"
        config.security.enable_auth = True
        config.llm.enabled = False
        bot = BotBoy(config)
        self.addCleanup(bot.shutdown)
        self.assertTrue(bot.initialize())
        return bot

    def assert_closed(self, connections):
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_registry_retains_every_connection_when_worker_ids_are_reused(self):
        store = _SQLiteMixin()
        store._init_connection_pool(":memory:", "CREATE TABLE example (id INTEGER)")
        self.addCleanup(store.close)
        connections = [sqlite3.connect(":memory:"), sqlite3.connect(":memory:")]
        # Register on one thread, as happens when a thread ID is recycled.
        for connection in connections:
            store._register_conn(connection)
        store.close()
        self.assert_closed(connections)

    def test_bot_shutdown_closes_scheduler_and_all_owned_database_stores(self):
        bot = self.bot()
        stores = [bot.scheduler._store, bot.history, bot.memory,
                  bot.reflection_archive, bot.archetypes, bot.trace_store, bot.task_store]
        connections = [store._get_conn() for store in stores]
        bot.shutdown()
        self.assert_closed(connections)
        self.assertFalse(bot.scheduler._thread.is_alive())

    def test_app_lifespan_closes_its_auth_stores_and_keeps_shared_bot_alive(self):
        bot = self.bot()
        app = create_app(bot)
        with TestClient(app):
            stores = [app.state.bot_principal_store(), app.state.bot_api_key_store(),
                      app.state.bot_auth._revocation_store]
            connections = [store._get_conn() for store in stores]
        self.assert_closed(connections)
        self.assertIsInstance(bot.memory.get_stats(), dict)
        self.assertEqual(bot.task_store.list_tasks()[1], 0)

    def test_stdlib_handler_shutdown_closes_its_persistent_revocation_store(self):
        bot = self.bot()
        handler = SimpleNamespace(**build_simple_handler_state(bot, host="127.0.0.1", port=0))
        connection = handler.auth._revocation_store._get_conn()
        close_simple_handler_stores(handler)
        self.assert_closed([connection])


if __name__ == "__main__":
    unittest.main()
