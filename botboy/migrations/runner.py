from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from sqlite3 import Connection
from typing import Iterable


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema_table(conn: Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            namespace   TEXT NOT NULL,
            version     INTEGER NOT NULL,
            name        TEXT NOT NULL,
            applied_at  TEXT NOT NULL,
            PRIMARY KEY(namespace, version)
        )
        """
    )
    conn.commit()


def apply_migrations(conn: Connection, namespace: str, migrations: Iterable[Migration]) -> None:
    _ensure_schema_table(conn)
    applied = {
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM schema_migrations WHERE namespace = ? ORDER BY version ASC",
            (namespace,),
        ).fetchall()
    }
    changed = False
    for migration in sorted(migrations, key=lambda item: item.version):
        if migration.version in applied:
            continue
        import sqlite3
        for statement in migration.statements:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                    pass # Schema was already initialized with the latest DDL directly
                else:
                    print(f"[BotBoy] Initialisation error: {e} | Statement: {statement}")
                    raise
        conn.execute(
            "INSERT INTO schema_migrations (namespace, version, name, applied_at) VALUES (?,?,?,?)",
            (namespace, migration.version, migration.name, _now()),
        )
        changed = True
    if changed:
        conn.commit()
