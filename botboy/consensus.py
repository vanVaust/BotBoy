"""Multi-Agent Consensus (Hive-Mind) — Voting and Collaborative Merge Reviews (V6)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from botboy.db_mixin import _SQLiteMixin

@dataclass
class Vote:
    voter_id: str
    role: str
    decision: str  # "approve", "reject", "refine"
    rationale: str
    confidence: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class ConsensusRecord:
    target_id: str  # e.g. task_id or plan_id
    topic: str
    status: str     # "pending", "reached", "failed"
    votes: List[Vote] = field(default_factory=list)
    quorum_required: int = 3
    final_decision: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: Optional[str] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consensus_records (
    target_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    status TEXT NOT NULL,
    quorum INTEGER NOT NULL,
    final_decision TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    votes_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (target_id, topic)
);
"""

class ConsensusEngine(_SQLiteMixin):
    """Administers multi-agent voting and merge-review thresholds."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _SCHEMA)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_poll(self, target_id: str, topic: str, quorum_required: int = 3) -> ConsensusRecord:
        now = self._now()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO consensus_records (target_id, topic, status, quorum, created_at, votes_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id, topic) DO NOTHING
            """,
            (target_id, topic, "pending", quorum_required, now, "[]")
        )
        conn.commit()
        return self.get_record(target_id, topic) # type: ignore

    def cast_vote(self, target_id: str, topic: str, voter_id: str, role: str, decision: str, rationale: str, confidence: float) -> ConsensusRecord:
        record = self.get_record(target_id, topic)
        if not record:
            raise ValueError(f"Consensus poll {topic} for target {target_id} not found.")

        if record.status != "pending":
            return record

        vote = Vote(voter_id, role, decision, rationale, confidence)
        record.votes.append(vote)

        # Check if quorum is reached
        if len(record.votes) >= record.quorum_required:
            approves = sum(1 for v in record.votes if v.decision == "approve")
            rejects = sum(1 for v in record.votes if v.decision == "reject")
            
            record.status = "reached" if approves >= rejects else "failed"
            record.final_decision = "approved" if approves >= rejects else "rejected"
            record.resolved_at = self._now()

        conn = self._get_conn()
        # Ensure json encoding uses dicts
        votes_dump = [v.__dict__ for v in record.votes]
        conn.execute(
            """
            UPDATE consensus_records 
            SET status = ?, final_decision = ?, resolved_at = ?, votes_json = ? 
            WHERE target_id = ? AND topic = ?
            """,
            (record.status, record.final_decision, record.resolved_at, json.dumps(votes_dump), target_id, topic)
        )
        conn.commit()
        return record

    def get_record(self, target_id: str, topic: str) -> Optional[ConsensusRecord]:
        row = self._get_conn().execute(
            "SELECT * FROM consensus_records WHERE target_id = ? AND topic = ?",
            (target_id, topic)
        ).fetchone()
        
        if not row:
            return None
            
        votes_raw = json.loads(row["votes_json"])
        votes = [Vote(**v) for v in votes_raw]
        
        return ConsensusRecord(
            target_id=row["target_id"],
            topic=row["topic"],
            status=row["status"],
            votes=votes,
            quorum_required=row["quorum"],
            final_decision=row["final_decision"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"]
        )
