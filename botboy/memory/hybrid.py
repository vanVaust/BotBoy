"""
HybridMemoryEngine — Episodic Store + FTS5 Keyword Search + Temporal Decay + RRF Fusion.

Architecture:
  Two parallel stores:
    1. Episodic store   — time-stamped action records with rich metadata
                          captures *what happened*, not just *what was stored*
    2. Semantic store   — the existing FTS5 BM25 full-text search from SimpleMemoryEngine
                          captures *what was explicitly remembered*

  Retrieval fusion via Reciprocal Rank Fusion (RRF):
    combined_score(doc) = Σ 1 / (k + rank_in_list_i)
    where k=60 (standard RRF constant, optimal for most retrieval tasks)

  Temporal Decay:
    score(doc) = base_score * decay(age_days)
    decay(d)   = exp(-d / half_life)
    default half_life: 30 days → recent items score ~2× older items

  Optional engine selection:
    config.yaml: memory.engine = "hybrid"
    Falls back to SimpleMemoryEngine if unset or on import error.

  Non-breaking:
    HybridMemoryEngine exposes the same public API as SimpleMemoryEngine.
    Existing code that calls store()/search()/get() works unchanged.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from botboy.db_mixin import _SQLiteMixin
from botboy.memory.simple import Memory, SimpleMemoryEngine


# ── Episodic Memory Entry ─────────────────────────────────────────────────────

@dataclass
class Episode:
    """
    A single episodic memory record: a time-stamped action with context.

    Unlike semantic memories (SimpleMemoryEngine), episodes capture
    the full context of an event: what command triggered it, what the
    result was, which archetype handled it, and any associated scores.
    """
    id:           int
    content:      str           # human-readable description of the episode
    episode_type: str           # "command" | "observation" | "reflection" | "plan"
    cmd_type:     str           # BotBoy command type (e.g. "calculate", "memory")
    archetype:    str           # which behavioral archetype handled this
    success:      bool
    confidence:   float         # gustatory score at time of recording
    latency_ms:   float
    timestamp:    str
    metadata:     Optional[Dict] = None

    def age_days(self) -> float:
        try:
            ts = datetime.fromisoformat(self.timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        except Exception:
            return 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "content": self.content,
            "episode_type": self.episode_type, "cmd_type": self.cmd_type,
            "archetype": self.archetype, "success": self.success,
            "confidence": self.confidence, "latency_ms": self.latency_ms,
            "timestamp": self.timestamp, "metadata": self.metadata,
        }


@dataclass
class HybridResult:
    """
    A fused retrieval result from the hybrid memory engine.

    Combines a match from the semantic store and/or the episodic store,
    ranked by the final RRF + temporal-decay score.
    """
    id:              int
    content:         str
    timestamp:       str
    score:           float       # final fused + decayed score
    source:          str         # "semantic" | "episodic" | "both"
    semantic_rank:   Optional[int] = None
    episodic_rank:   Optional[int] = None
    confidence:      float = 1.0
    metadata:        Optional[Dict] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "content": self.content,
            "timestamp": self.timestamp, "score": round(self.score, 4),
            "source": self.source, "confidence": self.confidence,
        }


# ── Database Schema ───────────────────────────────────────────────────────────

_SCHEMA = """
-- Episodic store: action records with rich context
CREATE TABLE IF NOT EXISTS episodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content      TEXT    NOT NULL,
    episode_type TEXT    NOT NULL DEFAULT 'command',
    cmd_type     TEXT    NOT NULL DEFAULT 'unknown',
    archetype    TEXT    NOT NULL DEFAULT 'unknown',
    success      INTEGER NOT NULL DEFAULT 1,
    confidence   REAL    NOT NULL DEFAULT 1.0,
    latency_ms   REAL    NOT NULL DEFAULT 0,
    timestamp    TEXT    NOT NULL,
    metadata     TEXT
);
CREATE INDEX IF NOT EXISTS idx_ep_timestamp  ON episodes(timestamp);
CREATE INDEX IF NOT EXISTS idx_ep_cmd_type   ON episodes(cmd_type);
CREATE INDEX IF NOT EXISTS idx_ep_archetype  ON episodes(archetype);
CREATE INDEX IF NOT EXISTS idx_ep_success    ON episodes(success);

-- FTS5 virtual table over episodes for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    content,
    content='episodes',
    content_rowid='id',
    tokenize='porter ascii'
);

CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO episodes_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""

# RRF hyperparameter — k=60 is optimal for most hybrid retrieval tasks
_RRF_K = 60

# Temporal decay half-life in days
_DEFAULT_HALF_LIFE_DAYS = 30.0


# ── HybridMemoryEngine ────────────────────────────────────────────────────────

class HybridMemoryEngine(_SQLiteMixin):
    """
    Hybrid memory engine combining episodic and semantic retrieval.

    API is a strict superset of SimpleMemoryEngine — all existing call sites
    continue to work. New capabilities are accessed via record_episode()
    and hybrid_search().

    Thread-safety: follows the same _SQLiteMixin pattern as all other stores.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        semantic_db_path: Optional[str] = None,
        half_life_days: float = _DEFAULT_HALF_LIFE_DAYS,
    ) -> None:
        # Episodic store (this engine's own DB)
        self._init_connection_pool(db_path, _SCHEMA)
        self._half_life = half_life_days

        # Semantic store (separate SimpleMemoryEngine instance)
        if semantic_db_path:
            sem_path = semantic_db_path
        elif db_path == ":memory:":
            sem_path = db_path
        else:
            base_path = Path(db_path)
            if base_path.suffix:
                sem_path = str(base_path.with_name(f"{base_path.stem}_semantic{base_path.suffix}"))
            else:
                sem_path = str(base_path.with_name(f"{base_path.name}_semantic.db"))
        self._semantic = SimpleMemoryEngine(sem_path)

    # ── Temporal decay ────────────────────────────────────────────────────────

    def _decay(self, age_days: float) -> float:
        """Exponential temporal decay: 0 days → 1.0, half_life days → 0.5."""
        return math.exp(-age_days * math.log(2) / self._half_life)

    # ── SimpleMemoryEngine-compatible API ─────────────────────────────────────

    def store(self, content: str, metadata: Optional[dict] = None) -> int:
        """Store content in the semantic store (compatible with SimpleMemoryEngine)."""
        return self._semantic.store(content, metadata)

    def store_batch(self, items: List[str]) -> List[int]:
        return self._semantic.store_batch(items)

    def get(self, memory_id: int) -> Optional[Memory]:
        return self._semantic.get(memory_id)

    def update(self, memory_id: int, content: str) -> bool:
        return self._semantic.update(memory_id, content)

    def delete(self, memory_id: int) -> bool:
        return self._semantic.delete(memory_id)

    def list_all(self, limit: int = 100, offset: int = 0) -> List[Memory]:
        return self._semantic.list_all(limit, offset)

    def clear_all(self) -> int:
        count = self._semantic.clear_all()
        if self._is_memory:
            with self._shared_lock:
                self._shared_conn.execute("DELETE FROM episodes")
                self._shared_conn.commit()
        else:
            conn = self._get_conn()
            conn.execute("DELETE FROM episodes")
            conn.commit()
        return count

    def search(self, query: str, limit: int = 5) -> List[Memory]:
        """FTS5 semantic-only search (compatible API). Use hybrid_search() for full power."""
        return self._semantic.search(query, limit)

    def get_stats(self) -> dict:
        sem_stats = self._semantic.get_stats()
        ep_count = self._fetchone("SELECT COUNT(*) as n FROM episodes")
        sem_stats["episodes_total"] = ep_count["n"] if ep_count else 0
        sem_stats["engine"] = "hybrid"
        sem_stats["half_life_days"] = self._half_life
        return sem_stats

    def close(self) -> None:
        self._semantic.close()
        super().close()

    # ── Episodic API ──────────────────────────────────────────────────────────

    def record_episode(
        self,
        content: str,
        episode_type: str = "command",
        cmd_type: str = "unknown",
        archetype: str = "unknown",
        success: bool = True,
        confidence: float = 1.0,
        latency_ms: float = 0.0,
        metadata: Optional[dict] = None,
    ) -> int:
        """Record an episodic memory (action + context)."""
        ts = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else None

        if self._is_memory:
            with self._shared_lock:
                cur = self._shared_conn.execute(
                    "INSERT INTO episodes (content, episode_type, cmd_type, archetype,"
                    " success, confidence, latency_ms, timestamp, metadata)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (content, episode_type, cmd_type, archetype,
                     int(success), confidence, latency_ms, ts, meta_json))
                self._shared_conn.commit()
                return cur.lastrowid
        else:
            conn = self._get_conn()
            cur = conn.execute(
                "INSERT INTO episodes (content, episode_type, cmd_type, archetype,"
                " success, confidence, latency_ms, timestamp, metadata)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (content, episode_type, cmd_type, archetype,
                 int(success), confidence, latency_ms, ts, meta_json))
            conn.commit()
            return cur.lastrowid

    def get_episode(self, episode_id: int) -> Optional[Episode]:
        row = self._fetchone("SELECT * FROM episodes WHERE id=?", (episode_id,))
        return self._row_to_episode(row) if row else None

    def list_episodes(
        self,
        limit: int = 20,
        offset: int = 0,
        episode_type: Optional[str] = None,
        cmd_type: Optional[str] = None,
        archetype: Optional[str] = None,
        success_only: bool = False,
    ) -> Tuple[List[Episode], int]:
        conditions, params = [], []
        if episode_type:
            conditions.append("episode_type=?")
            params.append(episode_type)
        if cmd_type:
            conditions.append("cmd_type=?")
            params.append(cmd_type)
        if archetype:
            conditions.append("archetype=?")
            params.append(archetype)
        if success_only:
            conditions.append("success=1")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        total_row = self._fetchone(f"SELECT COUNT(*) n FROM episodes {where}", params)
        total = total_row["n"] if total_row else 0
        rows = self._fetchall(
            f"SELECT * FROM episodes {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset])
        return [self._row_to_episode(r) for r in rows], total

    # ── Hybrid search (the crown feature) ────────────────────────────────────

    def hybrid_search(
        self,
        query: str,
        limit: int = 10,
        semantic_weight: float = 0.5,
        episodic_weight: float = 0.5,
        decay_weight: float = 0.3,
    ) -> List[HybridResult]:
        """
        Hybrid retrieval: FTS5 semantic + episodic FTS5 + temporal decay → RRF fusion.

        Parameters:
            query            Full-text search query
            limit            Maximum results to return
            semantic_weight  Weight of the semantic store in RRF fusion (0–1)
            episodic_weight  Weight of the episodic store in RRF fusion (0–1)
            decay_weight     How strongly temporal decay affects the final score (0–1)

        Returns:
            List of HybridResult ranked by fused score, most relevant first.
        """
        fetch_limit = limit * 3  # Over-fetch for better fusion quality

        # ── Semantic retrieval ─────────────────────────────────────────────
        semantic_hits = self._semantic.search(query, limit=fetch_limit)
        semantic_rank: Dict[int, int] = {}   # memory_id → rank
        semantic_docs: Dict[int, Memory] = {}
        for rank, mem in enumerate(semantic_hits):
            semantic_rank[mem.id] = rank
            semantic_docs[mem.id] = mem

        # ── Episodic retrieval ─────────────────────────────────────────────
        ep_sql_fts = """
            SELECT e.id, e.content, e.timestamp, e.confidence, e.metadata
            FROM episodes e
            JOIN episodes_fts fts ON e.id = fts.rowid
            WHERE episodes_fts MATCH ?
            ORDER BY bm25(episodes_fts)
            LIMIT ?"""
        ep_sql_like = """
            SELECT id, content, timestamp, confidence, metadata
            FROM episodes WHERE content LIKE ? LIMIT ?"""
        try:
            ep_rows = self._fetchall(ep_sql_fts, (query, fetch_limit))
        except sqlite3.OperationalError:
            ep_rows = self._fetchall(ep_sql_like, (f"%{query}%", fetch_limit))

        episodic_rank: Dict[int, int] = {}
        episodic_docs: Dict[int, dict] = {}
        for rank, row in enumerate(ep_rows):
            # Use negative IDs to avoid collision with semantic IDs
            ep_key = -(row["id"])
            episodic_rank[ep_key] = rank
            meta = None
            if row["metadata"]:
                try:
                    meta = json.loads(row["metadata"])
                except Exception:
                    pass
            episodic_docs[ep_key] = {
                "id": row["id"],
                "content": row["content"],
                "timestamp": row["timestamp"],
                "confidence": row["confidence"],
                "metadata": meta,
            }

        # ── RRF Fusion ────────────────────────────────────────────────────
        all_ids = set(semantic_rank.keys()) | set(episodic_rank.keys())
        rrf_scores: Dict[int, float] = {}

        for doc_id in all_ids:
            score = 0.0
            if doc_id in semantic_rank:
                score += semantic_weight * (1.0 / (_RRF_K + semantic_rank[doc_id]))
            if doc_id in episodic_rank:
                score += episodic_weight * (1.0 / (_RRF_K + episodic_rank[doc_id]))
            rrf_scores[doc_id] = score

        # ── Temporal Decay ────────────────────────────────────────────────
        now = datetime.now(timezone.utc)
        decayed_scores: Dict[int, float] = {}

        for doc_id, base_score in rrf_scores.items():
            if doc_id >= 0:
                mem = semantic_docs.get(doc_id)
                if mem:
                    try:
                        ts = datetime.fromisoformat(mem.timestamp)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        age_days = (now - ts).total_seconds() / 86400
                    except Exception:
                        age_days = 0
                else:
                    age_days = 0
            else:
                ep = episodic_docs.get(doc_id, {})
                try:
                    ts = datetime.fromisoformat(ep.get("timestamp", now.isoformat()))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age_days = (now - ts).total_seconds() / 86400
                except Exception:
                    age_days = 0

            decay_factor = self._decay(age_days)
            # Decay weight controls how much decay matters: 0 = ignore, 1 = full decay
            final_score = base_score * (
                (1 - decay_weight) + decay_weight * decay_factor
            )
            decayed_scores[doc_id] = final_score

        # ── Build results — content-fingerprint deduplication ─────────────────
        # Both stores may contain identical text with identical integer IDs.
        # We build all candidates first, then deduplicate by content fingerprint.
        # "both" results (found in semantic AND episodic) rank higher than
        # single-source results with the same score.

        # Step 1: collect all candidates
        candidates = []
        for doc_id, score in decayed_scores.items():
            sem_rank_v = semantic_rank.get(doc_id)
            ep_rank_v  = episodic_rank.get(doc_id)

            if doc_id >= 0 and doc_id in semantic_docs:
                mem    = semantic_docs[doc_id]
                ep_key = -(doc_id)
                both   = ep_key in episodic_rank
                # Boost "both" sources by a small factor
                final_score = score * (1.05 if both else 1.0)
                ep_conf     = episodic_docs[ep_key].get("confidence", 1.0) if both else None
                candidates.append({
                    "id":            doc_id,
                    "content":       mem.content,
                    "timestamp":     mem.timestamp,
                    "score":         final_score,
                    "source":        "both" if both else "semantic",
                    "semantic_rank": sem_rank_v,
                    "episodic_rank": episodic_rank.get(ep_key) if both else None,
                    "confidence":    ((1.0 + (ep_conf or 1.0)) / 2) if both else 1.0,
                    "metadata":      mem.metadata,
                    "fp":            mem.content[:120],
                })
            elif doc_id < 0 and doc_id in episodic_docs:
                ep      = episodic_docs[doc_id]
                sem_key = -(doc_id)  # the positive counterpart
                if sem_key in semantic_rank:
                    # This episodic entry's semantic twin was already captured above
                    continue
                candidates.append({
                    "id":            ep["id"],
                    "content":       ep["content"],
                    "timestamp":     ep["timestamp"],
                    "score":         score,
                    "source":        "episodic",
                    "semantic_rank": None,
                    "episodic_rank": ep_rank_v,
                    "confidence":    ep.get("confidence", 1.0),
                    "metadata":      ep.get("metadata"),
                    "fp":            ep["content"][:120],
                })

        # Step 2: sort by score descending
        candidates.sort(key=lambda c: c["score"], reverse=True)

        # Step 3: deduplicate by content fingerprint, keep highest-scoring
        results: List[HybridResult] = []
        seen_fps: set = set()
        for c in candidates:
            if len(results) >= limit:
                break
            fp = c["fp"]
            if fp in seen_fps:
                continue
            seen_fps.add(fp)
            results.append(HybridResult(
                id=c["id"],
                content=c["content"],
                timestamp=c["timestamp"],
                score=c["score"],
                source=c["source"],
                semantic_rank=c["semantic_rank"],
                episodic_rank=c["episodic_rank"],
                confidence=c["confidence"],
                metadata=c["metadata"],
            ))

        return results

    # ── Episode statistics ─────────────────────────────────────────────────────

    def episode_stats(self) -> dict:
        row = self._fetchone(
            "SELECT COUNT(*) total, SUM(success) successes, AVG(confidence) avg_conf,"
            " AVG(latency_ms) avg_latency FROM episodes")
        by_type = self._fetchall(
            "SELECT episode_type, COUNT(*) cnt FROM episodes GROUP BY episode_type ORDER BY cnt DESC")
        by_arch = self._fetchall(
            "SELECT archetype, COUNT(*) cnt FROM episodes GROUP BY archetype ORDER BY cnt DESC")
        return {
            "total": row["total"] if row else 0,
            "successes": row["successes"] or 0,
            "avg_confidence": round(row["avg_conf"] or 0, 3),
            "avg_latency_ms": round(row["avg_latency"] or 0, 1),
            "by_type": {r["episode_type"]: r["cnt"] for r in by_type},
            "by_archetype": {r["archetype"]: r["cnt"] for r in by_arch},
            "half_life_days": self._half_life,
        }

    @staticmethod
    def _row_to_episode(row) -> Episode:
        meta = None
        if row["metadata"]:
            try:
                meta = json.loads(row["metadata"])
            except Exception:
                meta = {"raw": row["metadata"]}
        return Episode(
            id=row["id"], content=row["content"],
            episode_type=row["episode_type"], cmd_type=row["cmd_type"],
            archetype=row["archetype"], success=bool(row["success"]),
            confidence=row["confidence"], latency_ms=row["latency_ms"],
            timestamp=row["timestamp"], metadata=meta,
        )
