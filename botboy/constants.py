"""Shared merge-resolution constants for BotBoy v2.

This module is the single source of truth for all merge-related constants
used across the orchestrator, the worker-handoff service, and the
task-merge service.  Import from here instead of duplicating definitions.
"""
from __future__ import annotations

__all__ = [
    "MERGE_RESOLUTION_LAST_CHILD_WINS",
    "MERGE_RESOLUTION_FIRST_CHILD_WINS",
    "MERGE_RESOLUTION_PREFER_NON_NULL",
    "MERGE_RESOLUTION_PREFER_RICHER_VALUE",
    "MERGE_RESOLUTION_PREFER_WORKER_PRIORITY",
    "DEFAULT_MERGE_RESOLUTION_POLICY",
    "SUPPORTED_MERGE_RESOLUTION_POLICIES",
    "MERGE_REVIEW_ACTIONS",
    "MERGE_WORKER_PRIORITY",
    "MERGE_REVIEW_PRESETS",
]

# ---------------------------------------------------------------------------
# Merge-resolution policy identifiers
# ---------------------------------------------------------------------------
MERGE_RESOLUTION_LAST_CHILD_WINS = "last_child_wins"
MERGE_RESOLUTION_FIRST_CHILD_WINS = "first_child_wins"
MERGE_RESOLUTION_PREFER_NON_NULL = "prefer_non_null"
MERGE_RESOLUTION_PREFER_RICHER_VALUE = "prefer_richer_value"
MERGE_RESOLUTION_PREFER_WORKER_PRIORITY = "prefer_worker_priority"

DEFAULT_MERGE_RESOLUTION_POLICY = MERGE_RESOLUTION_LAST_CHILD_WINS

# ---------------------------------------------------------------------------
# Alias map — canonical *and* shorthand names → canonical policy value
# ---------------------------------------------------------------------------
SUPPORTED_MERGE_RESOLUTION_POLICIES = {
    MERGE_RESOLUTION_LAST_CHILD_WINS: MERGE_RESOLUTION_LAST_CHILD_WINS,
    "last_wins": MERGE_RESOLUTION_LAST_CHILD_WINS,
    MERGE_RESOLUTION_FIRST_CHILD_WINS: MERGE_RESOLUTION_FIRST_CHILD_WINS,
    "first_wins": MERGE_RESOLUTION_FIRST_CHILD_WINS,
    MERGE_RESOLUTION_PREFER_NON_NULL: MERGE_RESOLUTION_PREFER_NON_NULL,
    "non_null": MERGE_RESOLUTION_PREFER_NON_NULL,
    MERGE_RESOLUTION_PREFER_RICHER_VALUE: MERGE_RESOLUTION_PREFER_RICHER_VALUE,
    "richer_value": MERGE_RESOLUTION_PREFER_RICHER_VALUE,
    "prefer_richer": MERGE_RESOLUTION_PREFER_RICHER_VALUE,
    MERGE_RESOLUTION_PREFER_WORKER_PRIORITY: MERGE_RESOLUTION_PREFER_WORKER_PRIORITY,
    "worker_priority": MERGE_RESOLUTION_PREFER_WORKER_PRIORITY,
    "prefer_worker": MERGE_RESOLUTION_PREFER_WORKER_PRIORITY,
}

# ---------------------------------------------------------------------------
# Review actions supported by the merge-review workflow
# ---------------------------------------------------------------------------
MERGE_REVIEW_ACTIONS = (
    "set_policy",
    "resolve_key",
    "resolve_many",
    "resolve_all_by_source",
    "clear_resolution",
    "clear_many",
    "apply_preset",
    "reapply",
)

# ---------------------------------------------------------------------------
# Worker-role → priority score (higher = stronger voice in conflicts)
# ---------------------------------------------------------------------------
MERGE_WORKER_PRIORITY = {
    "reviewer": 50,
    "executor": 40,
    "planner": 30,
    "researcher": 20,
    "designer": 10,
}

# ---------------------------------------------------------------------------
# Preset configurations for quick merge-review workflows
# ---------------------------------------------------------------------------
MERGE_REVIEW_PRESETS = {
    "fastest": {
        "preset": "fastest",
        "mode": "policy",
        "policy": MERGE_RESOLUTION_LAST_CHILD_WINS,
        "label": "Fastest",
        "description": "Favor the latest completed child result.",
    },
    "safest": {
        "preset": "safest",
        "mode": "policy",
        "policy": MERGE_RESOLUTION_FIRST_CHILD_WINS,
        "label": "Safest",
        "description": "Keep the first completed child result where conflicts exist.",
    },
    "richest": {
        "preset": "richest",
        "mode": "policy",
        "policy": MERGE_RESOLUTION_PREFER_RICHER_VALUE,
        "label": "Richest",
        "description": "Prefer structurally richer payload values during merge review.",
    },
    "priority_weighted": {
        "preset": "priority_weighted",
        "mode": "policy",
        "policy": MERGE_RESOLUTION_PREFER_WORKER_PRIORITY,
        "label": "Priority Weighted",
        "description": "Prefer higher-priority worker outputs during conflict resolution.",
    },
    "prefer_non_null": {
        "preset": "prefer_non_null",
        "mode": "policy",
        "policy": MERGE_RESOLUTION_PREFER_NON_NULL,
        "label": "Prefer Non Null",
        "description": "Prefer non-null values when children disagree.",
    },
    "prefer_reviewer": {
        "preset": "prefer_reviewer",
        "mode": "source",
        "source": "reviewer",
        "label": "Prefer Reviewer",
        "description": "Resolve all pending conflict keys to reviewer when available.",
    },
    "prefer_planner": {
        "preset": "prefer_planner",
        "mode": "source",
        "source": "planner",
        "label": "Prefer Planner",
        "description": "Resolve all pending conflict keys to planner when available.",
    },
    "prefer_executor": {
        "preset": "prefer_executor",
        "mode": "source",
        "source": "executor",
        "label": "Prefer Executor",
        "description": "Resolve all pending conflict keys to executor when available.",
    },
}
