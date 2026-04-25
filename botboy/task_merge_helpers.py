from __future__ import annotations

from typing import Any, Callable, Iterable, List, Optional


def merge_value_is_non_null(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def merge_value_richness_score(value: Any) -> tuple[int, int]:
    if value is None:
        return (0, 0)
    if isinstance(value, dict):
        populated = sum(1 for item in value.values() if merge_value_is_non_null(item))
        return (5, len(value) * 10 + populated)
    if isinstance(value, (list, tuple, set)):
        seq = list(value)
        populated = sum(1 for item in seq if merge_value_is_non_null(item))
        return (4, len(seq) * 10 + populated)
    if isinstance(value, str):
        stripped = value.strip()
        return (3 if stripped else 1, len(stripped))
    if isinstance(value, (int, float)):
        return (2, 1)
    if isinstance(value, bool):
        return (1, int(bool(value)))
    return (1, len(str(value)))


def merge_override_source_matches(source: str, *, task_id: str, worker_id: str) -> bool:
    normalized = str(source or "").strip()
    if not normalized:
        return False
    task_id = str(task_id or "").strip()
    worker_id = str(worker_id or "").strip()
    if normalized in {task_id, worker_id}:
        return True
    if normalized.startswith("task:") and normalized.split(":", 1)[1].strip() == task_id:
        return True
    if normalized.startswith("worker:") and normalized.split(":", 1)[1].strip() == worker_id:
        return True
    return False


def merge_worker_priority(worker_id: str, worker_priority_map: dict[str, int]) -> int:
    return int(worker_priority_map.get(str(worker_id or "").strip().lower(), 0) or 0)


def normalize_merge_resolution_policy(
    policy: str,
    *,
    strict: bool = False,
    default_policy: str,
    supported_policies: dict[str, str],
) -> str:
    normalized = str(policy or "").strip().lower().replace("-", "_")
    if not normalized:
        return default_policy
    if normalized in supported_policies:
        return supported_policies[normalized]
    if strict:
        supported = ", ".join(sorted(supported_policies))
        raise ValueError(f"Unsupported resolution policy '{policy}'. Supported: {supported}")
    return default_policy


def parse_handoff_batch_specs(spec_text: str, *, usage_text: str) -> List[tuple[str, str]]:
    specs: list[tuple[str, str]] = []
    for raw_segment in spec_text.split("||"):
        segment = raw_segment.strip()
        if not segment:
            continue
        worker_id, sep, delegated_command = segment.partition(":")
        worker_id = worker_id.strip().lower()
        delegated_command = delegated_command.strip()
        if not sep or not worker_id or not delegated_command:
            raise ValueError(usage_text)
        specs.append((worker_id, delegated_command))
    if not specs:
        raise ValueError(usage_text)
    return specs


def parse_handoff_batch_request(
    spec_text: str,
    *,
    normalize_policy: Callable[[str], str],
    default_policy: str,
    usage_text: str,
) -> tuple[str, List[tuple[str, str]]]:
    text = spec_text.strip()
    resolution_policy = default_policy
    if text.startswith("--resolution-policy="):
        option, _, remainder = text.partition(" ")
        resolution_policy = normalize_policy(option.split("=", 1)[1])
        text = remainder.strip()
    elif text.startswith("--resolution-policy "):
        _, _, remainder = text.partition(" ")
        option_value, _, text = remainder.partition(" ")
        resolution_policy = normalize_policy(option_value)
        text = text.strip()
    specs = parse_handoff_batch_specs(text, usage_text=usage_text)
    return resolution_policy, specs


def normalize_merge_review_preset(
    preset: str,
    *,
    strict: bool = False,
    review_presets: dict[str, dict[str, str]],
) -> str:
    normalized = str(preset or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return ""
    aliases = {
        "safe": "safest",
        "first": "safest",
        "fast": "fastest",
        "last": "fastest",
        "rich": "richest",
        "priority": "priority_weighted",
        "worker_priority": "priority_weighted",
        "reviewer": "prefer_reviewer",
        "planner": "prefer_planner",
        "executor": "prefer_executor",
    }
    normalized = aliases.get(normalized, normalized)
    if strict and normalized not in review_presets:
        raise ValueError(
            "Unsupported merge review preset "
            f"'{preset}'. Presets: {', '.join(sorted(review_presets))}"
        )
    return normalized if normalized in review_presets else ""


def empty_task_merge_payload(
    *,
    supported_policies: dict[str, str],
    review_actions: Iterable[str],
) -> dict:
    return {
        "available": False,
        "merge_policy": "",
        "configured_resolution_policy": "",
        "configured_resolution_overrides": {},
        "resolution_policy": "",
        "effective_resolution_policy": "",
        "available_resolution_policies": sorted(set(supported_policies.values())),
        "available_review_actions": list(review_actions),
        "available_review_presets": [],
        "merged_from_child_task_id": "",
        "merged_from_owner": "",
        "merged_from_worker": "",
        "child_status": "",
        "child_summary": "",
        "child_result": {},
        "child_merges": [],
        "completed_child_count": 0,
        "active_child_count": 0,
        "pending_child_ids": [],
        "workers_involved": [],
        "completed_child_ids": [],
        "linked_artifact_count": 0,
        "linked_artifacts": [],
        "review_status": "",
        "review_pending_keys": [],
        "applied_override_keys": [],
        "override_count": 0,
        "actionable": False,
        "override_active": False,
        "policy_delta": "aligned",
        "review_state": "",
        "next_action": "none",
        "known_review_keys": [],
        "valid_sources": [],
        "merge_resolution": {
            "policy": "",
            "resolved_data": {},
            "resolved_keys": [],
            "source_map": {},
            "conflicts": [],
            "conflict_count": 0,
            "applied_overrides": {},
            "applied_override_keys": [],
            "override_count": 0,
            "pending_conflict_keys": [],
            "review_status": "",
        },
    }


def resolve_child_merge_data(
    child_merges: List[dict],
    *,
    resolution_policy: str,
    resolution_overrides: Optional[dict[str, str]],
    first_child_wins_policy: str,
    prefer_non_null_policy: str,
    prefer_richer_value_policy: str,
    prefer_worker_priority_policy: str,
    worker_priority_map: dict[str, int],
    normalize_policy: Callable[[str], str],
) -> dict:
    resolution_policy = normalize_policy(resolution_policy)
    resolution_overrides = {
        str(key or "").strip(): str(value or "").strip()
        for key, value in (resolution_overrides or {}).items()
        if str(key or "").strip() and str(value or "").strip()
    }
    resolved_data: dict[str, Any] = {}
    source_map: dict[str, dict[str, str]] = {}
    conflicts: list[dict[str, Any]] = []
    applied_overrides: dict[str, dict[str, str]] = {}
    for merge in child_merges:
        child_id = str(merge.get("merged_from_child_task_id", "")).strip()
        worker_id = str(merge.get("merged_from_worker", "")).strip()
        child_result = merge.get("child_result", {})
        if not isinstance(child_result, dict):
            continue
        child_data = child_result.get("data", {})
        if not isinstance(child_data, dict):
            continue
        for key, value in child_data.items():
            if key in resolved_data and resolved_data[key] != value:
                previous = source_map.get(key, {})
                conflict = {
                    "key": key,
                    "previous_child_task_id": previous.get("task_id", ""),
                    "previous_worker": previous.get("worker", ""),
                    "previous_value": resolved_data[key],
                    "incoming_child_task_id": child_id,
                    "incoming_worker": worker_id,
                    "incoming_value": value,
                }
                override_source = resolution_overrides.get(key, "")
                if override_source:
                    conflict["override_source"] = override_source
                    if merge_override_source_matches(
                        override_source,
                        task_id=previous.get("task_id", ""),
                        worker_id=previous.get("worker", ""),
                    ):
                        conflict["resolution"] = "override_previous_retained"
                        conflict["resolution_origin"] = "override"
                        applied_overrides[key] = {
                            "source": override_source,
                            "task_id": previous.get("task_id", ""),
                            "worker": previous.get("worker", ""),
                        }
                        conflicts.append(conflict)
                        continue
                    if merge_override_source_matches(
                        override_source,
                        task_id=child_id,
                        worker_id=worker_id,
                    ):
                        conflict["resolution"] = "override_incoming_selected"
                        conflict["resolution_origin"] = "override"
                        applied_overrides[key] = {
                            "source": override_source,
                            "task_id": child_id,
                            "worker": worker_id,
                        }
                        conflicts.append(conflict)
                        resolved_data[key] = value
                        source_map[key] = {"task_id": child_id, "worker": worker_id}
                        continue
                if resolution_policy == first_child_wins_policy:
                    conflict["resolution"] = "previous_retained"
                    conflict["resolution_origin"] = "policy"
                    conflicts.append(conflict)
                    continue
                if resolution_policy == prefer_non_null_policy:
                    existing_non_null = merge_value_is_non_null(resolved_data[key])
                    incoming_non_null = merge_value_is_non_null(value)
                    if existing_non_null and not incoming_non_null:
                        conflict["resolution"] = "previous_non_null_retained"
                        conflict["resolution_origin"] = "policy"
                        conflicts.append(conflict)
                        continue
                    if not existing_non_null and incoming_non_null:
                        conflict["resolution"] = "incoming_non_null_replaced_previous"
                    else:
                        conflict["resolution"] = "incoming_replaced_previous"
                    conflict["resolution_origin"] = "policy"
                    conflicts.append(conflict)
                    resolved_data[key] = value
                    source_map[key] = {"task_id": child_id, "worker": worker_id}
                    continue
                if resolution_policy == prefer_richer_value_policy:
                    existing_richness = merge_value_richness_score(resolved_data[key])
                    incoming_richness = merge_value_richness_score(value)
                    if existing_richness > incoming_richness:
                        conflict["resolution"] = "previous_richer_retained"
                        conflict["resolution_origin"] = "policy"
                        conflicts.append(conflict)
                        continue
                    conflict["resolution"] = (
                        "incoming_richer_replaced_previous"
                        if incoming_richness > existing_richness
                        else "equal_richness_incoming_replaced_previous"
                    )
                    conflict["resolution_origin"] = "policy"
                    conflicts.append(conflict)
                    resolved_data[key] = value
                    source_map[key] = {"task_id": child_id, "worker": worker_id}
                    continue
                if resolution_policy == prefer_worker_priority_policy:
                    previous_priority = merge_worker_priority(
                        previous.get("worker", ""),
                        worker_priority_map,
                    )
                    incoming_priority = merge_worker_priority(worker_id, worker_priority_map)
                    if previous_priority > incoming_priority:
                        conflict["resolution"] = "previous_worker_priority_retained"
                        conflict["resolution_origin"] = "policy"
                        conflicts.append(conflict)
                        continue
                    conflict["resolution"] = (
                        "incoming_worker_priority_replaced_previous"
                        if incoming_priority > previous_priority
                        else "equal_worker_priority_incoming_replaced_previous"
                    )
                    conflict["resolution_origin"] = "policy"
                    conflicts.append(conflict)
                    resolved_data[key] = value
                    source_map[key] = {"task_id": child_id, "worker": worker_id}
                    continue
                conflict["resolution"] = "incoming_replaced_previous"
                conflict["resolution_origin"] = "policy"
                conflicts.append(conflict)
            resolved_data[key] = value
            source_map[key] = {"task_id": child_id, "worker": worker_id}
    conflict_keys = sorted(
        {str(item.get("key", "")).strip() for item in conflicts if str(item.get("key", "")).strip()}
    )
    applied_override_keys = sorted(applied_overrides.keys())
    pending_conflict_keys = [key for key in conflict_keys if key not in applied_overrides]
    return {
        "policy": resolution_policy,
        "resolved_data": resolved_data,
        "resolved_keys": sorted(resolved_data.keys()),
        "source_map": source_map,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "applied_overrides": applied_overrides,
        "applied_override_keys": applied_override_keys,
        "override_count": len(applied_overrides),
        "pending_conflict_keys": pending_conflict_keys,
        "review_status": (
            "clean"
            if not conflict_keys
            else ("overridden" if not pending_conflict_keys else "needs_attention")
        ),
    }
