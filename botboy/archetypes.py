"""
ArchetypeDatabase — Behavioral Archetype Engine v2.0

Phase 2 improvements over v1:
  - Expanded semantic vocabulary: natural-language synonyms + n-gram patterns
  - Calibrated scoring: lower thresholds + adjusted normalization factor
  - Phrase pattern matching: "what is", "how to", "show me", etc.
  - Recency bias cached in memory (not fetched from DB every call)
  - Intent confidence is now genuinely calibrated to 0-1 range
"""
from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from botboy.db_mixin import _SQLiteMixin


class BehavioralArchetype(Enum):
    INFORM      = "inform"
    ORGANIZE    = "organize"
    COMMUNICATE = "communicate"
    CREATE      = "create"
    ANALYZE     = "analyze"
    UNKNOWN     = "unknown"


@dataclass
class ArchetypeProfile:
    archetype:            BehavioralArchetype
    primary_channels:     List[str]
    skill_triggers:       List[str]       # exact first-word matches
    semantic_keywords:    List[str]       # single-word semantic vocabulary
    phrase_patterns:      List[str]       # regex phrase patterns (compiled on demand)
    context_signals:      Dict[str, float]
    confidence_threshold: float = 0.40   # Phase 2: lowered from 0.55-0.60

    _compiled_phrases: List = field(default_factory=list, repr=False)

    def compile_phrases(self) -> None:
        self._compiled_phrases = [
            re.compile(p, re.IGNORECASE) for p in self.phrase_patterns
        ]

    def phrase_match(self, text: str) -> float:
        """Returns 0.0 if no phrase matches, 0.75 if any phrase matches."""
        for p in self._compiled_phrases:
            if p.search(text):
                return 0.75
        return 0.0


@dataclass
class IntentMatch:
    archetype:   BehavioralArchetype
    confidence:  float
    matched_via: str
    skill_hint:  Optional[str] = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS archetype_stats (
    archetype    TEXT PRIMARY KEY,
    use_count    INTEGER NOT NULL DEFAULT 0,
    last_used    REAL    NOT NULL DEFAULT 0,
    success_rate REAL    NOT NULL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS intent_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command     TEXT NOT NULL,
    archetype   TEXT NOT NULL,
    confidence  REAL NOT NULL,
    matched_via TEXT NOT NULL,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_il_ts   ON intent_log(ts);
CREATE INDEX IF NOT EXISTS idx_il_arch ON intent_log(archetype);
"""


def _build_default_profiles() -> Dict[BehavioralArchetype, ArchetypeProfile]:
    """
    Build archetype profiles with expanded vocabulary.
    Each profile has:
      skill_triggers  — exact first-word matches (highest confidence)
      semantic_keywords — individual words that indicate this archetype
      phrase_patterns   — regex patterns for common natural-language phrases
    """
    profiles = {
        BehavioralArchetype.INFORM: ArchetypeProfile(
            archetype=BehavioralArchetype.INFORM,
            primary_channels=["visual", "haptic"],
            skill_triggers=["help", "?", "status", "version", "skills",
                            "performance", "system", "memstats"],
            semantic_keywords=[
                "what", "show", "display", "info", "about", "list", "tell",
                "describe", "explain", "detail", "overview", "summary",
                "report", "check", "see", "view", "current", "now",
                "state", "health", "running", "active", "configuration",
            ],
            phrase_patterns=[
                r"what is\b", r"what are\b", r"how many\b", r"show me\b",
                r"tell me\b", r"give me\b", r"display\b", r"list\b",
                r"current status", r"system info", r"what's\b", r"whats\b",
            ],
            context_signals={"system.load": 0.1},
            confidence_threshold=0.35,
        ),

        BehavioralArchetype.ORGANIZE: ArchetypeProfile(
            archetype=BehavioralArchetype.ORGANIZE,
            primary_channels=["haptic", "gustatory"],
            skill_triggers=["remember", "store", "save", "search", "forget",
                            "delete", "memories", "schedule", "history",
                            "remind", "todo", "memstats"],
            semantic_keywords=[
                "remember", "store", "save", "find", "search", "recall",
                "memory", "organize", "schedule", "remind", "track", "note",
                "keep", "record", "log", "bookmark", "tag", "label",
                "archive", "retrieve", "lookup", "query", "locate",
                "manage", "sort", "filter", "catalog", "index",
            ],
            phrase_patterns=[
                r"remember\b", r"don't forget\b", r"keep track",
                r"make a note", r"note that\b", r"save this\b",
                r"find my\b", r"search for\b", r"look up\b",
                r"where is\b", r"find where\b",
            ],
            context_signals={"memory.usage": 0.5, "storage.available": 0.8},
            confidence_threshold=0.38,
        ),

        BehavioralArchetype.COMMUNICATE: ArchetypeProfile(
            archetype=BehavioralArchetype.COMMUNICATE,
            primary_channels=["auditory", "haptic"],
            skill_triggers=["plan", "think", "chat", "ask", "voice"],
            semantic_keywords=[
                "explain", "how", "why", "reason", "discuss", "talk",
                "communicate", "analyse", "analyze", "understand", "describe",
                "clarify", "elaborate", "illustrate", "argue", "debate",
                "consider", "think", "reflect", "ponder", "explore",
                "discuss", "converse", "dialogue", "respond", "answer",
                "question", "wonder", "curious", "because",
            ],
            phrase_patterns=[
                r"explain\b", r"how does\b", r"why does\b", r"why is\b",
                r"can you\b", r"could you\b", r"help me understand",
                r"tell me about\b", r"what causes\b", r"how to\b",
                r"I want to know\b", r"I wonder\b", r"help me\b",
                r"what do you think\b", r"in your opinion\b",
            ],
            context_signals={"llm.available": 1.0, "llm.latency_ms": 0.3},
            confidence_threshold=0.35,
        ),

        BehavioralArchetype.CREATE: ArchetypeProfile(
            archetype=BehavioralArchetype.CREATE,
            primary_channels=["haptic", "gustatory"],
            skill_triggers=["calculate", "compute", "math", "calc", "hash",
                            "base64", "convert", "password", "passgen",
                            "run", "execute-code", "generate", "sqrt",
                            "sin", "cos", "log", "encode", "decode"],
            semantic_keywords=[
                "calculate", "compute", "create", "generate", "make", "build",
                "produce", "transform", "encode", "decode", "convert",
                "hash", "encrypt", "compress", "format", "render", "compile",
                "process", "derive", "evaluate", "solve", "result", "output",
                "value", "number", "equation", "formula", "expression",
                "plus", "minus", "times", "divide", "equal", "sum",
                "multiply", "subtraction", "addition", "percentage",
            ],
            phrase_patterns=[
                r"calculate\b", r"what is \d+", r"how much is\b",
                r"convert\b", r"\d+\s*[\+\-\*\/]", r"in \w+ to \w+",
                r"generate\b", r"create\b", r"make a\b", r"encode\b",
                r"decrypt\b", r"encrypt\b", r"hash of\b",
            ],
            context_signals={"cpu.available": 0.7},
            confidence_threshold=0.38,
        ),

        BehavioralArchetype.ANALYZE: ArchetypeProfile(
            archetype=BehavioralArchetype.ANALYZE,
            primary_channels=["visual", "haptic", "gustatory", "olfactory"],
            skill_triggers=["web", "weather", "analyze", "stats", "diff",
                            "compare", "git-summary", "text-stats",
                            "data-analyze", "regex", "file"],
            semantic_keywords=[
                "analyze", "compare", "research", "investigate", "examine",
                "evaluate", "measure", "monitor", "check", "review", "inspect",
                "look up", "find out", "discover", "data", "statistics",
                "metrics", "performance", "report", "audit", "profile",
                "benchmark", "diagnose", "test", "verify", "validate",
                "difference", "similarity", "pattern", "trend", "regression",
            ],
            phrase_patterns=[
                r"analyze\b", r"compare\b", r"difference between\b",
                r"research\b", r"look up\b", r"find out\b",
                r"how does .* compare", r"what are the stats",
                r"performance of\b", r"audit\b", r"review\b",
            ],
            context_signals={"network.available": 0.8, "cpu.available": 0.5},
            confidence_threshold=0.35,
        ),
    }

    # Compile phrase patterns for all profiles
    for p in profiles.values():
        p.compile_phrases()

    return profiles


class ArchetypeDatabase(_SQLiteMixin):
    """
    SQLite-backed behavioral archetype database with calibrated intent matching.

    Phase 2 matching pipeline:
        1. Trigger match   (O(1) — exact first-word)          → confidence 0.95
        2. Phrase match    (regex on full command)              → confidence 0.75
        3. Semantic match  (keyword overlap + normalisation)    → up to 0.70
        4. Context weight  (olfactory signal adjustment)        → ±0.10
        5. Recency bias    (in-memory cache, not DB per-call)   → ±0.06
    """

    _RECENCY_HALF_LIFE_HOURS = 6.0
    _RECENCY_CACHE_TTL = 30.0  # seconds before refreshing recency from DB

    def __init__(self, db_path: str = ":memory:") -> None:
        self._init_connection_pool(db_path, _SCHEMA)
        self._profiles = _build_default_profiles()
        self._trigger_index = self._build_trigger_index()
        self._context_signals: Dict[str, float] = {}
        self._context_lock = threading.Lock()
        # Recency cache — avoids DB query on every match_intent call
        self._recency_cache: Dict[str, Tuple[float, float]] = {}  # arch → (score, expiry)
        self._recency_lock = threading.Lock()
        self._seed_stats()

    def _build_trigger_index(self) -> Dict[str, BehavioralArchetype]:
        idx: Dict[str, BehavioralArchetype] = {}
        for arch, profile in self._profiles.items():
            for t in profile.skill_triggers:
                idx[t.lower()] = arch
        return idx

    def _seed_stats(self) -> None:
        for arch in BehavioralArchetype:
            if arch == BehavioralArchetype.UNKNOWN:
                continue
            if not self._fetchone("SELECT 1 FROM archetype_stats WHERE archetype=?", (arch.value,)):
                if self._is_memory:
                    with self._shared_lock:
                        self._shared_conn.execute(
                            "INSERT INTO archetype_stats (archetype) VALUES (?)", (arch.value,))
                        self._shared_conn.commit()
                else:
                    conn = self._get_conn()
                    conn.execute("INSERT INTO archetype_stats (archetype) VALUES (?)", (arch.value,))
                    conn.commit()

    def close(self) -> None:
        super().close()

    def update_context(self, signal: str, value: float) -> None:
        with self._context_lock:
            self._context_signals[signal] = float(value)

    def update_context_batch(self, signals: Dict[str, float]) -> None:
        with self._context_lock:
            self._context_signals.update(signals)

    def _get_recency_scores(self) -> Dict[str, float]:
        """Return recency bonus per archetype, cached to avoid per-call DB access."""
        now = time.time()
        with self._recency_lock:
            # Check if cache is still fresh
            cache_valid = all(
                arch.value in self._recency_cache
                and self._recency_cache[arch.value][1] > now
                for arch in BehavioralArchetype
                if arch != BehavioralArchetype.UNKNOWN
            )
            if cache_valid:
                return {k: v[0] for k, v in self._recency_cache.items()}

        # Refresh from DB
        expiry = now + self._RECENCY_CACHE_TTL
        recency: Dict[str, float] = {}
        for arch in BehavioralArchetype:
            if arch == BehavioralArchetype.UNKNOWN:
                recency[arch.value] = 0.0
                continue
            row = self._fetchone(
                "SELECT use_count, last_used, success_rate FROM archetype_stats WHERE archetype=?",
                (arch.value,))
            if row and row["last_used"] > 0:
                hours_ago = (now - row["last_used"]) / 3600
                decay = math.exp(-hours_ago / self._RECENCY_HALF_LIFE_HOURS)
                bonus = decay * 0.06 * float(row["success_rate"])
            else:
                bonus = 0.0
            recency[arch.value] = bonus

        with self._recency_lock:
            for k, v in recency.items():
                self._recency_cache[k] = (v, expiry)
        return recency

    def match_intent(self, command: str) -> IntentMatch:
        if not command or not command.strip():
            return IntentMatch(BehavioralArchetype.UNKNOWN, 0.0, "empty")

        words = command.strip().lower().split()
        prefix = words[0] if words else ""
        full_text = command.strip().lower()

        # ── Stage 1: Exact trigger (highest confidence) ────────────────────
        if prefix in self._trigger_index:
            arch = self._trigger_index[prefix]
            return IntentMatch(arch, 0.95, "trigger", skill_hint=prefix)

        # ── Stage 2: Phrase pattern matching ──────────────────────────────
        best_phrase_arch: Optional[BehavioralArchetype] = None
        for arch, profile in self._profiles.items():
            phrase_score = profile.phrase_match(command)
            if phrase_score > 0:
                best_phrase_arch = arch
                break  # First match wins for phrases — they're specific enough

        # ── Stage 3: Keyword semantic scoring ─────────────────────────────
        word_set = set(words)
        scores: Dict[BehavioralArchetype, float] = {}

        for arch, profile in self._profiles.items():
            kw_set = set(k.lower() for k in profile.semantic_keywords)
            overlap = len(word_set & kw_set)
            if overlap == 0:
                scores[arch] = 0.0
                continue
            # Calibrated formula: linear overlap / sqrt(keywords) * scale
            # sqrt normalization is gentler than log for small vocabularies
            normalised = overlap / math.sqrt(len(kw_set))
            scores[arch] = min(0.70, normalised * 1.2)

        # ── Stage 4: Context signal weighting ─────────────────────────────
        with self._context_lock:
            ctx = dict(self._context_signals)

        for arch, profile in self._profiles.items():
            if not profile.context_signals:
                continue
            ctx_boost = sum(
                weight * ctx.get(signal, 0.5)
                for signal, weight in profile.context_signals.items()
            ) / len(profile.context_signals)
            scores[arch] = scores.get(arch, 0.0) + ctx_boost * 0.10

        # ── Stage 5: Recency bias (cached) ────────────────────────────────
        recency = self._get_recency_scores()
        for arch in list(scores.keys()):
            scores[arch] += recency.get(arch.value, 0.0)

        # ── Merge phrase match into scores ─────────────────────────────────
        if best_phrase_arch is not None:
            scores[best_phrase_arch] = max(
                scores.get(best_phrase_arch, 0.0), 0.75)

        # ── Pick winner ────────────────────────────────────────────────────
        if not scores:
            return IntentMatch(BehavioralArchetype.UNKNOWN, 0.0, "no_match")

        best_arch = max(scores, key=lambda a: scores[a])
        best_score = scores[best_arch]
        profile = self._profiles[best_arch]

        if best_score < profile.confidence_threshold:
            return IntentMatch(BehavioralArchetype.UNKNOWN, best_score, "below_threshold")

        method = "phrase" if best_phrase_arch == best_arch else "semantic"
        return IntentMatch(best_arch, min(0.95, best_score), method)

    def record_outcome(self, command: str, archetype: BehavioralArchetype,
                       confidence: float, matched_via: str, success: bool) -> None:
        now = time.time()
        if self._is_memory:
            with self._shared_lock:
                self._shared_conn.execute(
                    "UPDATE archetype_stats SET use_count=use_count+1, last_used=?,"
                    " success_rate=(success_rate*use_count+?)/(use_count+1) WHERE archetype=?",
                    (now, 1.0 if success else 0.0, archetype.value))
                self._shared_conn.execute(
                    "INSERT INTO intent_log (command,archetype,confidence,matched_via,ts)"
                    " VALUES (?,?,?,?,?)",
                    (command[:256], archetype.value, confidence, matched_via, now))
                self._shared_conn.commit()
        else:
            conn = self._get_conn()
            conn.execute(
                "UPDATE archetype_stats SET use_count=use_count+1, last_used=?,"
                " success_rate=(success_rate*use_count+?)/(use_count+1) WHERE archetype=?",
                (now, 1.0 if success else 0.0, archetype.value))
            conn.execute(
                "INSERT INTO intent_log (command,archetype,confidence,matched_via,ts)"
                " VALUES (?,?,?,?,?)",
                (command[:256], archetype.value, confidence, matched_via, now))
            conn.commit()
        # Invalidate recency cache
        with self._recency_lock:
            self._recency_cache.clear()

    def stats(self) -> dict:
        rows = self._fetchall("SELECT * FROM archetype_stats ORDER BY use_count DESC")
        total = sum(r["use_count"] for r in rows)
        return {
            "total_commands_routed": total,
            "archetypes": [
                {"archetype": r["archetype"], "use_count": r["use_count"],
                 "success_rate": round(r["success_rate"], 3), "last_used": r["last_used"]}
                for r in rows
            ],
        }

    def top_commands(self, archetype: Optional[BehavioralArchetype] = None,
                     limit: int = 10) -> List[dict]:
        if archetype:
            rows = self._fetchall(
                "SELECT command, COUNT(*) cnt FROM intent_log WHERE archetype=?"
                " GROUP BY command ORDER BY cnt DESC LIMIT ?",
                (archetype.value, limit))
        else:
            rows = self._fetchall(
                "SELECT command, COUNT(*) cnt FROM intent_log"
                " GROUP BY command ORDER BY cnt DESC LIMIT ?", (limit,))
        return [{"command": r["command"], "count": r["cnt"]} for r in rows]

    def profile_for(self, archetype: BehavioralArchetype) -> Optional[ArchetypeProfile]:
        return self._profiles.get(archetype)

    def add_trigger(self, trigger: str, archetype: BehavioralArchetype) -> None:
        self._trigger_index[trigger.lower()] = archetype
        if archetype in self._profiles:
            self._profiles[archetype].skill_triggers.append(trigger.lower())

    def all_archetypes(self) -> List[BehavioralArchetype]:
        return [a for a in BehavioralArchetype if a != BehavioralArchetype.UNKNOWN]
