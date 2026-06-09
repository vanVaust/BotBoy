"""Memory Graph — Entity/Edge Storage Integration for BotBoy V3."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from botboy.db_mixin import _SQLiteMixin

@dataclass
class Entity:
    entity_id: str
    entity_type: str
    properties: Dict[str, Any]
    created_at: str
    updated_at: str

@dataclass
class Edge:
    source_id: str
    target_id: str
    relation: str
    weight: float
    properties: Dict[str, Any]
    created_at: str

_GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    properties_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    properties_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id, relation),
    FOREIGN KEY(source_id) REFERENCES graph_entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(target_id) REFERENCES graph_entities(entity_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_relation ON graph_edges(relation);
"""

class MemoryGraph(_SQLiteMixin):
    """
    SQLite-backed Property Graph for Memory Storage.
    Supports Entity and Edge (Relation) management for semantic memory traversal.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _GRAPH_SCHEMA)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_entity(self, entity_id: str, type_name: str, properties: Dict[str, Any]) -> Entity:
        now = self._now()
        conn = self._get_conn()
        
        props_str = json.dumps(properties)
        conn.execute(
            """
            INSERT INTO graph_entities (entity_id, entity_type, properties_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                entity_type=excluded.entity_type,
                properties_json=excluded.properties_json,
                updated_at=excluded.updated_at
            """,
            (entity_id, type_name, props_str, now, now)
        )
        conn.commit()
        return self.get_entity(entity_id) # type: ignore

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        row = self._get_conn().execute(
            "SELECT * FROM graph_entities WHERE entity_id = ?",
            (entity_id,)
        ).fetchone()
        if not row:
            return None
        return Entity(
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            properties=json.loads(row["properties_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"]
        )

    def add_edge(self, source_id: str, target_id: str, relation: str, properties: Dict[str, Any] = None, weight: float = 1.0) -> Edge:
        props = properties or {}
        now = self._now()
        conn = self._get_conn()
        
        # Verify entities exist
        src = self.get_entity(source_id)
        tgt = self.get_entity(target_id)
        if not src or not tgt:
            raise ValueError(f"Both source '{source_id}' and target '{target_id}' must exist.")

        conn.execute(
            """
            INSERT INTO graph_edges (source_id, target_id, relation, weight, properties_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, target_id, relation) DO UPDATE SET
                weight=excluded.weight,
                properties_json=excluded.properties_json
            """,
            (source_id, target_id, relation, weight, json.dumps(props), now)
        )
        conn.commit()
        
        return Edge(
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            weight=weight,
            properties=props,
            created_at=now
        )

    def get_neighbors(self, entity_id: str, relation: str = None) -> List[Entity]:
        conn = self._get_conn()
        
        q = """
            SELECT ge.* FROM graph_entities ge
            JOIN graph_edges e ON ge.entity_id = e.target_id
            WHERE e.source_id = ?
        """
        params = [entity_id]
        
        if relation:
            q += " AND e.relation = ?"
            params.append(relation)
            
        rows = conn.execute(q, tuple(params)).fetchall()
        return [
            Entity(
                entity_id=row["entity_id"],
                entity_type=row["entity_type"],
                properties=json.loads(row["properties_json"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"]
            )
            for row in rows
        ]

    def close(self):
        super().close()
