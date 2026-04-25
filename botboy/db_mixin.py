"""Thread-safe SQLite mixin for BotBoy stores."""
from __future__ import annotations
import atexit
import sqlite3
import threading
import weakref
from pathlib import Path
from typing import Dict, Optional


_LIVE_SQLITE_MIXINS = weakref.WeakSet()


def _close_live_sqlite_mixins() -> None:
    for mixin in list(_LIVE_SQLITE_MIXINS):
        try:
            mixin.close()
        except Exception:
            pass


atexit.register(_close_live_sqlite_mixins)

class _SQLiteMixin:
    """
    Thread-safe SQLite connection management.
    :memory: → single shared connection + threading.Lock (same DB across threads).
    file    → threading.local() pool (one connection per thread, WAL mode).
    """
    def _init_connection_pool(self, db_path: str, schema: str) -> None:
        self.db_path = db_path
        self._is_memory = db_path == ":memory:"
        self._closed = False
        self._connections: Dict[int, sqlite3.Connection] = {}
        self._connections_lock = threading.Lock()
        _LIVE_SQLITE_MIXINS.add(self)
        if self._is_memory:
            self._shared_conn = sqlite3.connect(
                ":memory:", check_same_thread=False, timeout=30.0
            )
            self._shared_conn.row_factory = sqlite3.Row
            self._shared_conn.execute("PRAGMA busy_timeout=30000")
            self._shared_lock = threading.Lock()
            self._shared_conn.executescript(schema)
            self._shared_conn.commit()
            self._register_conn(self._shared_conn)
        else:
            self._local = threading.local()
            self._shared_conn = None
            self._shared_lock = None
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            init = sqlite3.connect(db_path, timeout=30.0)
            init.execute("PRAGMA busy_timeout=30000")
            init.execute("PRAGMA journal_mode=WAL")
            init.executescript(schema)
            init.commit()
            init.close()

    def _register_conn(self, conn: sqlite3.Connection) -> None:
        with self._connections_lock:
            self._connections[threading.get_ident()] = conn

    def _get_conn(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("SQLite store is closed")
        if self._is_memory:
            return self._shared_conn
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
            self._register_conn(conn)
        return self._local.conn

    def _execute(self, sql: str, params=()) -> "sqlite3.Cursor":
        if self._closed:
            raise RuntimeError("SQLite store is closed")
        if self._is_memory:
            with self._shared_lock:
                return self._shared_conn.execute(sql, params)
        return self._get_conn().execute(sql, params)

    def _commit(self) -> None:
        if self._closed:
            raise RuntimeError("SQLite store is closed")
        if self._is_memory:
            with self._shared_lock:
                self._shared_conn.commit()
        else:
            self._get_conn().commit()

    def _executescript(self, sql: str) -> None:
        if self._closed:
            raise RuntimeError("SQLite store is closed")
        if self._is_memory:
            with self._shared_lock:
                self._shared_conn.executescript(sql)
        else:
            self._get_conn().executescript(sql)

    def _fetchall(self, sql: str, params=()) -> list:
        if self._closed:
            raise RuntimeError("SQLite store is closed")
        if self._is_memory:
            with self._shared_lock:
                return self._shared_conn.execute(sql, params).fetchall()
        return self._get_conn().execute(sql, params).fetchall()

    def _fetchone(self, sql: str, params=()):
        if self._closed:
            raise RuntimeError("SQLite store is closed")
        if self._is_memory:
            with self._shared_lock:
                return self._shared_conn.execute(sql, params).fetchone()
        return self._get_conn().execute(sql, params).fetchone()

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return

        with getattr(self, "_connections_lock", threading.Lock()):
            connections = list(getattr(self, "_connections", {}).items())
            self._connections = {}

        for _, conn in connections:
            try:
                conn.close()
            except Exception:
                pass

        if getattr(self, "_is_memory", False) and getattr(self, "_shared_conn", None):
            try:
                self._shared_conn.close()
            except Exception:
                pass
            self._shared_conn = None

        if hasattr(self, "_local"):
            self._local.__dict__.clear()

        self._closed = True
        _LIVE_SQLITE_MIXINS.discard(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
