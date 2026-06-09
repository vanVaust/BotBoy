from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .operator_workbench_support import (
    get_merge_review_queue as build_merge_review_queue,
    get_operator_workbench_payload as build_operator_workbench_payload,
    merge_review_signal_fields as build_merge_review_signal_fields,
    summarize_merge_queue_item as build_merge_queue_item_summary,
)
from .task_merge_helpers import (
    empty_task_merge_payload,
    normalize_merge_resolution_policy,
    normalize_merge_review_preset,
    resolve_child_merge_data,
)
from .tasks import (
    TASK_STATUS_BLOCKED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)

from botboy.constants import (
    DEFAULT_MERGE_RESOLUTION_POLICY,
    MERGE_RESOLUTION_FIRST_CHILD_WINS,
    MERGE_RESOLUTION_LAST_CHILD_WINS,
    MERGE_RESOLUTION_PREFER_NON_NULL,
    MERGE_RESOLUTION_PREFER_RICHER_VALUE,
    MERGE_RESOLUTION_PREFER_WORKER_PRIORITY,
    MERGE_REVIEW_ACTIONS,
    MERGE_REVIEW_PRESETS,
    MERGE_WORKER_PRIORITY,
    SUPPORTED_MERGE_RESOLUTION_POLICIES,
)


BEST_EFFORT_ARTIFACT_ERRORS = (
    OSError,
    RuntimeError,
    sqlite3.Error,
    TypeError,
    ValueError,
    json.JSONDecodeError,
)


class TaskMergeService:
    def __init__(
        self,
        bot,
        *,
        supported_policies: Optional[dict[str, str]] = None,
        review_actions: Iterable[str] = MERGE_REVIEW_ACTIONS,
        review_presets: Optional[dict[str, dict[str, str]]] = None,
        worker_priority_map: Optional[dict[str, int]] = None,
    ) -> None:
        self.bot = bot
        self.supported_policies = dict(supported_policies or SUPPORTED_MERGE_RESOLUTION_POLICIES)
        self.review_actions = tuple(review_actions)
        self.review_presets = dict(review_presets or MERGE_REVIEW_PRESETS)
        self.worker_priority_map = dict(worker_priority_map or MERGE_WORKER_PRIORITY)

    @property
    def task_store(self):
        return getattr(self.bot, "task_store", None)

    def _normalize_merge_resolution_policy(self, policy: str, *, strict: bool = False) -> str:
        return normalize_merge_resolution_policy(
            policy,
            strict=strict,
            default_policy=DEFAULT_MERGE_RESOLUTION_POLICY,
            supported_policies=self.supported_policies,
        )

    def normalize_merge_resolution_policy(self, policy: str, *, strict: bool = False) -> str:
        return self._normalize_merge_resolution_policy(policy, strict=strict)

    def _normalize_merge_review_preset(self, preset: str, *, strict: bool = False) -> str:
        return normalize_merge_review_preset(
            preset,
            strict=strict,
            review_presets=self.review_presets,
        )

    def normalize_merge_review_preset(self, preset: str, *, strict: bool = False) -> str:
        return self._normalize_merge_review_preset(preset, strict=strict)

    def _merge_resolution_policy_for_record(self, record) -> str:
        payload = record.payload if record else {}
        if not isinstance(payload, dict):
            return DEFAULT_MERGE_RESOLUTION_POLICY
        return self._normalize_merge_resolution_policy(payload.get("merge_resolution_policy", ""))

    def merge_resolution_policy_for_record(self, record) -> str:
        return self._merge_resolution_policy_for_record(record)

    def _merge_resolution_overrides_for_record(self, record) -> dict[str, str]:
        payload = record.payload if record else {}
        if not isinstance(payload, dict):
            return {}
        overrides = payload.get("merge_resolution_overrides", {})
        if not isinstance(overrides, dict):
            return {}
        normalized: dict[str, str] = {}
        for key, source in overrides.items():
            key_text = str(key or "").strip()
            source_text = str(source or "").strip()
            if key_text and source_text:
                normalized[key_text] = source_text
        return normalized

    def merge_resolution_overrides_for_record(self, record) -> dict[str, str]:
        return self._merge_resolution_overrides_for_record(record)

    def _merge_known_review_keys(self, merge: dict) -> list[str]:
        known_keys = set()
        merge_resolution = merge.get("merge_resolution", {})
        if isinstance(merge_resolution, dict):
            known_keys.update(
                str(item).strip()
                for item in merge_resolution.get("resolved_keys", [])
                if str(item).strip()
            )
            known_keys.update(
                str(item.get("key", "")).strip()
                for item in merge_resolution.get("conflicts", [])
                if isinstance(item, dict) and str(item.get("key", "")).strip()
            )
        return sorted(item for item in known_keys if item)

    def merge_known_review_keys(self, merge: dict) -> list[str]:
        return self._merge_known_review_keys(merge)

    def _merge_valid_sources(self, merge: dict) -> list[str]:
        valid_sources = set()
        valid_sources.update(
            str(item).strip()
            for item in merge.get("completed_child_ids", [])
            if str(item).strip()
        )
        valid_sources.update(
            str(item).strip()
            for item in merge.get("workers_involved", [])
            if str(item).strip()
        )
        valid_sources.update(
            f"worker:{str(item).strip()}"
            for item in merge.get("workers_involved", [])
            if str(item).strip()
        )
        return sorted(valid_sources)

    def merge_valid_sources(self, merge: dict) -> list[str]:
        return self._merge_valid_sources(merge)

    def _merge_available_review_presets(self, merge: dict) -> list[dict]:
        available_workers = {
            str(item).strip().lower()
            for item in merge.get("workers_involved", [])
            if str(item).strip()
        }
        presets: list[dict] = []
        for preset_id, definition in self.review_presets.items():
            if definition.get("mode") == "source" and str(definition.get("source", "")).strip().lower() not in available_workers:
                continue
            presets.append(dict(definition))
        return presets

    def merge_available_review_presets(self, merge: dict) -> list[dict]:
        return self._merge_available_review_presets(merge)

    @staticmethod
    def _merge_review_next_action(
        *,
        pending_child_ids: list[str],
        review_pending_keys: list[str],
        override_count: int,
    ) -> str:
        if pending_child_ids:
            return "wait_children"
        if review_pending_keys:
            return "resolve_many"
        if override_count:
            return "review_overrides"
        return "none"

    def merge_review_next_action(
        self,
        *,
        pending_child_ids: list[str],
        review_pending_keys: list[str],
        override_count: int,
    ) -> str:
        return self._merge_review_next_action(
            pending_child_ids=pending_child_ids,
            review_pending_keys=review_pending_keys,
            override_count=override_count,
        )

    def _merge_review_signal_fields(self, merge: dict) -> dict:
        return build_merge_review_signal_fields(self, merge)

    def merge_review_signal_fields(self, merge: dict) -> dict:
        return self._merge_review_signal_fields(merge)

    def _summarize_merge_queue_item(self, record, merge: dict) -> dict:
        return build_merge_queue_item_summary(self, record, merge)

    def summarize_merge_queue_item(self, record, merge: dict) -> dict:
        return self._summarize_merge_queue_item(record, merge)

    def get_merge_review_queue(
        self,
        *,
        limit: int = 20,
        include_non_actionable: bool = False,
    ) -> dict:
        return build_merge_review_queue(
            self,
            limit=limit,
            include_non_actionable=include_non_actionable,
        )

    def get_operator_workbench_payload(self, *, limit: int = 10) -> dict:
        return build_operator_workbench_payload(self, limit=limit)

    def _merge_child_merges_for_record(self, record) -> list[dict]:
        merge_result = self._load_task_merge_result(record)
        child_merges = merge_result.get("child_merges", []) if isinstance(merge_result, dict) else []
        return [item for item in child_merges if isinstance(item, dict)]

    def merge_child_merges_for_record(self, record) -> list[dict]:
        return self._merge_child_merges_for_record(record)

    def build_parent_merge_result(
        self,
        parent_record,
        *,
        child_merges: list[dict],
    ) -> dict:
        if not self.task_store:
            return {}
        list_children = getattr(self.task_store, "list_children", None)
        siblings = list_children(parent_record.task_id, limit=200) if callable(list_children) else []
        active_siblings = [
            sibling
            for sibling in siblings
            if sibling.status in {
                TASK_STATUS_QUEUED,
                TASK_STATUS_RUNNING,
                TASK_STATUS_WAITING_APPROVAL,
                TASK_STATUS_BLOCKED,
            }
        ]
        completed_children = [sibling for sibling in siblings if sibling.status == TASK_STATUS_COMPLETED]
        merge_resolution_policy = self._merge_resolution_policy_for_record(parent_record)
        merge_resolution_overrides = self._merge_resolution_overrides_for_record(parent_record)
        merge_resolution = self.resolve_child_merge_data(
            child_merges,
            resolution_policy=merge_resolution_policy,
            resolution_overrides=merge_resolution_overrides,
        )
        latest_merge = child_merges[-1] if child_merges else {}
        latest_linked_artifacts = latest_merge.get("linked_artifacts", []) if isinstance(latest_merge, dict) else []
        merge_result = {
            "success": True,
            "merge_policy": "multi_child_sequential_accumulator",
            "configured_resolution_policy": merge_resolution_policy,
            "resolution_policy": merge_resolution["policy"],
            "configured_resolution_overrides": dict(sorted(merge_resolution_overrides.items())),
            "review_status": str(merge_resolution.get("review_status", "clean") or "clean"),
            "review_pending_keys": [
                str(item)
                for item in merge_resolution.get("pending_conflict_keys", [])
                if str(item).strip()
            ],
            "applied_override_keys": [
                str(item)
                for item in merge_resolution.get("applied_override_keys", [])
                if str(item).strip()
            ],
            "available_review_actions": list(self.review_actions),
            "merged_from_child_task_id": str(latest_merge.get("merged_from_child_task_id", "") or ""),
            "merged_from_owner": str(latest_merge.get("merged_from_owner", "") or ""),
            "merged_from_worker": str(latest_merge.get("merged_from_worker", "") or ""),
            "child_status": str(latest_merge.get("child_status", "") or ""),
            "child_summary": str(latest_merge.get("child_summary", "") or ""),
            "child_result": (
                latest_merge.get("child_result", {})
                if isinstance(latest_merge.get("child_result", {}), dict)
                else {}
            ),
            "child_merges": child_merges,
            "completed_child_count": len(completed_children),
            "active_child_count": len(active_siblings),
            "pending_child_ids": [item.task_id for item in active_siblings],
            "workers_involved": sorted(
                {
                    str(item.get("merged_from_worker", "")).strip()
                    for item in child_merges
                    if str(item.get("merged_from_worker", "")).strip()
                }
            ),
            "completed_child_ids": [
                str(item.get("merged_from_child_task_id", "")).strip()
                for item in child_merges
                if str(item.get("merged_from_child_task_id", "")).strip()
            ],
            "merge_resolution": merge_resolution,
            "linked_artifact_count": len(latest_linked_artifacts),
            "linked_artifacts": [item for item in latest_linked_artifacts if isinstance(item, dict)],
        }
        merge_result.update(self._merge_review_signal_fields(merge_result))
        return merge_result

    def _refresh_task_merge_review(
        self,
        task_id: str,
        *,
        principal: str,
        request_id: str,
        event_type: str,
        message: str,
    ):
        if not self.task_store:
            return None
        get_task = getattr(self.task_store, "get_task", None)
        record = get_task(task_id) if callable(get_task) else None
        if not record:
            return None
        child_merges = self._merge_child_merges_for_record(record)
        if not child_merges:
            return record
        merge_result = self.build_parent_merge_result(record, child_merges=child_merges)
        if not merge_result:
            return record
        update_task = getattr(self.task_store, "update_task", None)
        updated = update_task(task_id, result=merge_result) if callable(update_task) else record
        write_artifact = getattr(self.task_store, "write_artifact", None)
        if callable(write_artifact):
            try:
                write_artifact(
                    task_id,
                    category="merge_report",
                    label=f"Merge review refresh for {task_id[:8]}",
                    filename=f"merge_review_{int(time.time() * 1000)}.json",
                    content=json.dumps(merge_result, indent=2, sort_keys=True) + "\n",
                    media_type="application/json",
                )
            except OSError:
                pass
        add_event = getattr(self.task_store, "add_event", None)
        if callable(add_event):
            add_event(
                task_id,
                event_type=event_type,
                status=updated.status if updated else record.status,
                message=message,
                principal=principal,
                request_id=request_id,
                run_id=(updated.run_id if updated else record.run_id) or "",
            )
        return updated or record

    def refresh_task_merge_review(
        self,
        task_id: str,
        *,
        principal: str,
        request_id: str,
        event_type: str,
        message: str,
    ):
        return self._refresh_task_merge_review(
            task_id,
            principal=principal,
            request_id=request_id,
            event_type=event_type,
            message=message,
        )

    def _set_task_merge_resolution_policy(
        self,
        task_id: str,
        *,
        policy: str,
        principal: str,
        request_id: str,
    ):
        if not self.task_store:
            return None
        get_task = getattr(self.task_store, "get_task", None)
        record = get_task(task_id) if callable(get_task) else None
        if not record:
            return None
        normalized = self._normalize_merge_resolution_policy(policy, strict=True)
        payload = record.payload if isinstance(record.payload, dict) else {}
        updated_payload = dict(payload)
        if updated_payload.get("merge_resolution_policy", "") == normalized:
            return record
        updated_payload["merge_resolution_policy"] = normalized
        update_task = getattr(self.task_store, "update_task", None)
        updated = update_task(task_id, payload=updated_payload) if callable(update_task) else record
        add_event = getattr(self.task_store, "add_event", None)
        if callable(add_event):
            add_event(
                task_id,
                event_type="merge_policy_updated",
                status=updated.status if updated else record.status,
                message=f"Merge resolution policy set to {normalized}",
                principal=principal,
                request_id=request_id,
            )
        return updated or record

    def set_task_merge_resolution_policy(
        self,
        task_id: str,
        *,
        policy: str,
        principal: str,
        request_id: str,
    ):
        return self._set_task_merge_resolution_policy(
            task_id,
            policy=policy,
            principal=principal,
            request_id=request_id,
        )

    def set_task_merge_resolution_override(
        self,
        task_id: str,
        *,
        key: str,
        source: str,
        principal: str,
        request_id: str,
    ):
        if not self.task_store:
            return None
        get_task = getattr(self.task_store, "get_task", None)
        record = get_task(task_id) if callable(get_task) else None
        if not record:
            return None
        merge = self.get_task_merge_payload(task_id, record=record)
        if not merge.get("available"):
            raise ValueError(f"Task '{task_id}' has no merge result to review.")
        key_text = str(key or "").strip()
        source_text = str(source or "").strip()
        if not key_text or not source_text:
            raise ValueError("Merge resolution override requires both key and source.")
        known_keys = set(merge.get("merge_resolution", {}).get("resolved_keys", []))
        known_keys.update(
            str(item.get("key", "")).strip()
            for item in merge.get("merge_resolution", {}).get("conflicts", [])
            if isinstance(item, dict)
        )
        known_keys.discard("")
        if key_text not in known_keys:
            raise ValueError(
                f"Unknown merge key '{key_text}'. Known keys: {', '.join(sorted(known_keys)) or '-'}"
            )
        valid_sources = set(merge.get("completed_child_ids", []))
        valid_sources.update(merge.get("workers_involved", []))
        valid_sources.update(f"worker:{item}" for item in merge.get("workers_involved", []))
        if source_text not in valid_sources:
            raise ValueError(
                "Unknown merge source "
                f"'{source_text}'. Sources: {', '.join(sorted(valid_sources)) or '-'}"
            )
        payload = record.payload if isinstance(record.payload, dict) else {}
        overrides = (
            dict(payload.get("merge_resolution_overrides", {}))
            if isinstance(payload.get("merge_resolution_overrides", {}), dict)
            else {}
        )
        overrides[key_text] = source_text
        updated_payload = dict(payload)
        updated_payload["merge_resolution_overrides"] = overrides
        update_task = getattr(self.task_store, "update_task", None)
        if callable(update_task):
            update_task(task_id, payload=updated_payload)
        return self._refresh_task_merge_review(
            task_id,
            principal=principal,
            request_id=request_id,
            event_type="merge_resolution_override_updated",
            message=f"Merge review override for {key_text} set to {source_text}",
        )

    def set_task_merge_resolution_overrides_bulk(
        self,
        task_id: str,
        *,
        items: list[dict[str, str]],
        principal: str,
        request_id: str,
    ):
        if not self.task_store:
            return None
        get_task = getattr(self.task_store, "get_task", None)
        record = get_task(task_id) if callable(get_task) else None
        if not record:
            return None
        merge = self.get_task_merge_payload(task_id, record=record)
        if not merge.get("available"):
            raise ValueError(f"Task '{task_id}' has no merge result to review.")
        normalized_items: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key_text = str(item.get("key", "")).strip()
            source_text = str(item.get("source", "")).strip()
            if key_text and source_text:
                normalized_items.append((key_text, source_text))
        if not normalized_items:
            raise ValueError("Bulk merge resolution requires at least one key/source pair.")
        known_keys = set(merge.get("known_review_keys", []))
        valid_sources = set(merge.get("valid_sources", []))
        invalid_keys = sorted({key for key, _ in normalized_items if key not in known_keys})
        if invalid_keys:
            raise ValueError(
                f"Unknown merge keys: {', '.join(invalid_keys)}. "
                f"Known keys: {', '.join(sorted(known_keys)) or '-'}"
            )
        invalid_sources = sorted({source for _, source in normalized_items if source not in valid_sources})
        if invalid_sources:
            raise ValueError(
                f"Unknown merge sources: {', '.join(invalid_sources)}. "
                f"Sources: {', '.join(sorted(valid_sources)) or '-'}"
            )
        payload = record.payload if isinstance(record.payload, dict) else {}
        overrides = (
            dict(payload.get("merge_resolution_overrides", {}))
            if isinstance(payload.get("merge_resolution_overrides", {}), dict)
            else {}
        )
        for key_text, source_text in normalized_items:
            overrides[key_text] = source_text
        updated_payload = dict(payload)
        updated_payload["merge_resolution_overrides"] = overrides
        update_task = getattr(self.task_store, "update_task", None)
        if callable(update_task):
            update_task(task_id, payload=updated_payload)
        return self._refresh_task_merge_review(
            task_id,
            principal=principal,
            request_id=request_id,
            event_type="merge_resolution_overrides_bulk_updated",
            message=f"Merge review overrides updated for {len(normalized_items)} key(s)",
        )

    def clear_task_merge_resolution_override(
        self,
        task_id: str,
        *,
        key: str,
        principal: str,
        request_id: str,
    ):
        if not self.task_store:
            return None
        get_task = getattr(self.task_store, "get_task", None)
        record = get_task(task_id) if callable(get_task) else None
        if not record:
            return None
        key_text = str(key or "").strip()
        if not key_text:
            raise ValueError("Merge resolution clear requires a key.")
        payload = record.payload if isinstance(record.payload, dict) else {}
        overrides = (
            dict(payload.get("merge_resolution_overrides", {}))
            if isinstance(payload.get("merge_resolution_overrides", {}), dict)
            else {}
        )
        if key_text not in overrides:
            raise ValueError(f"No merge resolution override configured for '{key_text}'.")
        overrides.pop(key_text, None)
        updated_payload = dict(payload)
        if overrides:
            updated_payload["merge_resolution_overrides"] = overrides
        else:
            updated_payload.pop("merge_resolution_overrides", None)
        update_task = getattr(self.task_store, "update_task", None)
        if callable(update_task):
            update_task(task_id, payload=updated_payload)
        return self._refresh_task_merge_review(
            task_id,
            principal=principal,
            request_id=request_id,
            event_type="merge_resolution_override_cleared",
            message=f"Merge review override for {key_text} cleared",
        )

    def clear_task_merge_resolution_overrides_bulk(
        self,
        task_id: str,
        *,
        keys: list[str],
        principal: str,
        request_id: str,
    ):
        if not self.task_store:
            return None
        get_task = getattr(self.task_store, "get_task", None)
        record = get_task(task_id) if callable(get_task) else None
        if not record:
            return None
        normalized_keys = [str(item or "").strip() for item in keys if str(item or "").strip()]
        if not normalized_keys:
            raise ValueError("Bulk merge override clear requires at least one key.")
        payload = record.payload if isinstance(record.payload, dict) else {}
        overrides = (
            dict(payload.get("merge_resolution_overrides", {}))
            if isinstance(payload.get("merge_resolution_overrides", {}), dict)
            else {}
        )
        missing = sorted({key for key in normalized_keys if key not in overrides})
        if missing:
            raise ValueError(
                f"No merge resolution override configured for: {', '.join(missing)}."
            )
        for key_text in normalized_keys:
            overrides.pop(key_text, None)
        updated_payload = dict(payload)
        if overrides:
            updated_payload["merge_resolution_overrides"] = overrides
        else:
            updated_payload.pop("merge_resolution_overrides", None)
        update_task = getattr(self.task_store, "update_task", None)
        if callable(update_task):
            update_task(task_id, payload=updated_payload)
        return self._refresh_task_merge_review(
            task_id,
            principal=principal,
            request_id=request_id,
            event_type="merge_resolution_overrides_bulk_cleared",
            message=f"Merge review overrides cleared for {len(normalized_keys)} key(s)",
        )

    def resolve_all_task_merge_keys_by_source(
        self,
        task_id: str,
        *,
        source: str,
        keys: Optional[list[str]] = None,
        principal: str,
        request_id: str,
    ):
        if not self.task_store:
            return None
        get_task = getattr(self.task_store, "get_task", None)
        record = get_task(task_id) if callable(get_task) else None
        if not record:
            return None
        merge = self.get_task_merge_payload(task_id, record=record)
        if not merge.get("available"):
            raise ValueError(f"Task '{task_id}' has no merge result to review.")
        normalized_keys = [str(item or "").strip() for item in (keys or []) if str(item or "").strip()]
        if not normalized_keys:
            normalized_keys = [
                str(item).strip()
                for item in (merge.get("review_pending_keys", []) or merge.get("known_review_keys", []))
                if str(item).strip()
            ]
        if not normalized_keys:
            raise ValueError("No merge keys available for bulk source resolution.")
        return self.set_task_merge_resolution_overrides_bulk(
            task_id,
            items=[{"key": key_text, "source": str(source or "").strip()} for key_text in normalized_keys],
            principal=principal,
            request_id=request_id,
        )

    def apply_task_merge_review_preset(
        self,
        task_id: str,
        *,
        preset: str,
        principal: str,
        request_id: str,
    ):
        if not self.task_store:
            return None
        get_task = getattr(self.task_store, "get_task", None)
        record = get_task(task_id) if callable(get_task) else None
        if not record:
            return None
        normalized = self._normalize_merge_review_preset(preset, strict=True)
        definition = self.review_presets[normalized]
        mode = str(definition.get("mode", "")).strip().lower()
        if mode == "policy":
            self._set_task_merge_resolution_policy(
                task_id,
                policy=str(definition.get("policy", "")).strip(),
                principal=principal,
                request_id=request_id,
            )
            return self._refresh_task_merge_review(
                task_id,
                principal=principal,
                request_id=request_id,
                event_type="merge_review_preset_applied",
                message=f"Merge review preset '{normalized}' applied",
            )
        if mode == "source":
            source_worker = str(definition.get("source", "")).strip().lower()
            if not source_worker:
                raise ValueError(f"Merge review preset '{normalized}' has no source binding.")
            return self.resolve_all_task_merge_keys_by_source(
                task_id,
                source=f"worker:{source_worker}",
                principal=principal,
                request_id=request_id,
            )
        raise ValueError(f"Unsupported merge review preset '{preset}'.")

    def reapply_task_merge_resolution(
        self,
        task_id: str,
        *,
        principal: str,
        request_id: str,
    ):
        if not self.task_store:
            return None
        get_task = getattr(self.task_store, "get_task", None)
        record = get_task(task_id) if callable(get_task) else None
        if not record:
            return None
        if not self._merge_child_merges_for_record(record):
            raise ValueError(f"Task '{task_id}' has no merge result to reapply.")
        return self._refresh_task_merge_review(
            task_id,
            principal=principal,
            request_id=request_id,
            event_type="merge_resolution_reapplied",
            message="Merge review reapplied using configured policy and overrides",
        )

    def apply_task_merge_review_action(
        self,
        task_id: str,
        *,
        action: str,
        key: str = "",
        source: str = "",
        keys: Optional[list[str]] = None,
        items: Optional[list[dict[str, str]]] = None,
        preset: str = "",
        principal: str,
        request_id: str,
    ):
        normalized = str(action or "").strip().lower().replace("-", "_")
        if normalized in {"resolve", "override", "resolve_key"}:
            return self.set_task_merge_resolution_override(
                task_id,
                key=key,
                source=source,
                principal=principal,
                request_id=request_id,
            )
        if normalized in {"resolve_many", "bulk_resolve", "bulk_override"}:
            return self.set_task_merge_resolution_overrides_bulk(
                task_id,
                items=items or [],
                principal=principal,
                request_id=request_id,
            )
        if normalized in {"resolve_all_by_source", "resolve_all"}:
            return self.resolve_all_task_merge_keys_by_source(
                task_id,
                source=source,
                keys=keys,
                principal=principal,
                request_id=request_id,
            )
        if normalized in {"clear", "clear_resolution", "clear_override"}:
            return self.clear_task_merge_resolution_override(
                task_id,
                key=key,
                principal=principal,
                request_id=request_id,
            )
        if normalized in {"clear_many", "clear_bulk", "bulk_clear"}:
            return self.clear_task_merge_resolution_overrides_bulk(
                task_id,
                keys=keys or [],
                principal=principal,
                request_id=request_id,
            )
        if normalized in {"reapply", "refresh", "apply_policy"}:
            return self.reapply_task_merge_resolution(
                task_id,
                principal=principal,
                request_id=request_id,
            )
        if normalized in {"apply_preset", "preset"}:
            return self.apply_task_merge_review_preset(
                task_id,
                preset=preset,
                principal=principal,
                request_id=request_id,
            )
        raise ValueError(f"Unsupported merge review action '{action}'.")

    def resolve_child_merge_data(
        self,
        child_merges: list[dict],
        *,
        resolution_policy: str = MERGE_RESOLUTION_LAST_CHILD_WINS,
        resolution_overrides: Optional[dict[str, str]] = None,
    ) -> dict:
        return resolve_child_merge_data(
            child_merges,
            resolution_policy=resolution_policy,
            resolution_overrides=resolution_overrides,
            first_child_wins_policy=MERGE_RESOLUTION_FIRST_CHILD_WINS,
            prefer_non_null_policy=MERGE_RESOLUTION_PREFER_NON_NULL,
            prefer_richer_value_policy=MERGE_RESOLUTION_PREFER_RICHER_VALUE,
            prefer_worker_priority_policy=MERGE_RESOLUTION_PREFER_WORKER_PRIORITY,
            worker_priority_map=self.worker_priority_map,
            normalize_policy=lambda value: self._normalize_merge_resolution_policy(value),
        )

    def empty_task_merge_payload(self) -> dict:
        return empty_task_merge_payload(
            supported_policies=self.supported_policies,
            review_actions=self.review_actions,
        )

    def _empty_task_merge_payload(self) -> dict:
        return self.empty_task_merge_payload()

    def load_task_merge_result(self, record) -> dict:
        return self._load_task_merge_result(record)

    def _load_task_merge_result(self, record) -> dict:
        merge_result = record.result if isinstance(record.result, dict) else {}
        if isinstance(merge_result, dict) and merge_result.get("merge_policy"):
            return merge_result
        if isinstance(merge_result, dict):
            nested_data = merge_result.get("data", {})
            nested_merge = nested_data.get("merge", {}) if isinstance(nested_data, dict) else {}
            if isinstance(nested_merge, dict) and nested_merge.get("merge_policy"):
                return nested_merge
        if not self.task_store:
            return merge_result if isinstance(merge_result, dict) else {}
        get_artifacts = getattr(self.task_store, "get_artifacts", None)
        if not callable(get_artifacts):
            return merge_result if isinstance(merge_result, dict) else {}
        best_merge = {}
        try:
            for artifact in get_artifacts(record.task_id, limit=20):
                if artifact.category != "merge_report":
                    continue
                artifact_path = Path(artifact.file_path)
                if not artifact_path.exists():
                    continue
                parsed = json.loads(artifact_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict) and parsed.get("merge_policy"):
                    if not best_merge:
                        best_merge = parsed
                        continue
                    parsed_completed = int(parsed.get("completed_child_count", 0) or 0)
                    best_completed = int(best_merge.get("completed_child_count", 0) or 0)
                    parsed_active = int(parsed.get("active_child_count", 0) or 0)
                    best_active = int(best_merge.get("active_child_count", 0) or 0)
                    parsed_conflicts = int(
                        (parsed.get("merge_resolution", {}) or {}).get("conflict_count", 0) or 0
                    )
                    best_conflicts = int(
                        (best_merge.get("merge_resolution", {}) or {}).get("conflict_count", 0) or 0
                    )
                    parsed_score = (
                        parsed_completed,
                        -parsed_active,
                        parsed_conflicts,
                        int(parsed.get("linked_artifact_count", 0) or 0),
                    )
                    best_score = (
                        best_completed,
                        -best_active,
                        best_conflicts,
                        int(best_merge.get("linked_artifact_count", 0) or 0),
                    )
                    if parsed_score > best_score:
                        best_merge = parsed
        except BEST_EFFORT_ARTIFACT_ERRORS:
            pass
        if best_merge:
            return best_merge
        return merge_result if isinstance(merge_result, dict) else {}

    def get_task_merge_payload(self, task_id: str, *, record=None) -> dict:
        empty = self.empty_task_merge_payload()
        if not self.task_store:
            return empty
        get_task = getattr(self.task_store, "get_task", None)
        task_record = record or (get_task(task_id) if callable(get_task) else None)
        if not task_record:
            return empty
        configured_resolution_policy = self._merge_resolution_policy_for_record(task_record)
        merge_result = self._load_task_merge_result(task_record)
        payload = self.empty_task_merge_payload()
        payload["configured_resolution_policy"] = configured_resolution_policy
        payload["configured_resolution_overrides"] = self._merge_resolution_overrides_for_record(task_record)
        if not isinstance(merge_result, dict) or not merge_result.get("merge_policy"):
            return payload

        merge_resolution = merge_result.get("merge_resolution", {})
        if not isinstance(merge_resolution, dict):
            merge_resolution = {}
        resolved_data = merge_resolution.get("resolved_data", {})
        source_map = merge_resolution.get("source_map", {})
        conflicts = merge_resolution.get("conflicts", [])
        child_merges = merge_result.get("child_merges", [])
        linked_artifacts = merge_result.get("linked_artifacts", [])

        payload.update(
            {
                "available": True,
                "merge_policy": str(merge_result.get("merge_policy", "") or ""),
                "configured_resolution_policy": configured_resolution_policy,
                "configured_resolution_overrides": (
                    merge_result.get("configured_resolution_overrides", payload["configured_resolution_overrides"])
                    if isinstance(merge_result.get("configured_resolution_overrides", payload["configured_resolution_overrides"]), dict)
                    else payload["configured_resolution_overrides"]
                ),
                "resolution_policy": str(
                    merge_result.get("resolution_policy")
                    or merge_resolution.get("policy")
                    or ""
                ),
                "available_resolution_policies": sorted(set(self.supported_policies.values())),
                "available_review_actions": list(self.review_actions),
                "merged_from_child_task_id": str(merge_result.get("merged_from_child_task_id", "") or ""),
                "merged_from_owner": str(merge_result.get("merged_from_owner", "") or ""),
                "merged_from_worker": str(merge_result.get("merged_from_worker", "") or ""),
                "child_status": str(merge_result.get("child_status", "") or ""),
                "child_summary": str(merge_result.get("child_summary", "") or ""),
                "child_result": (
                    merge_result.get("child_result", {})
                    if isinstance(merge_result.get("child_result", {}), dict)
                    else {}
                ),
                "child_merges": [item for item in child_merges if isinstance(item, dict)],
                "completed_child_count": int(merge_result.get("completed_child_count", 0) or 0),
                "active_child_count": int(merge_result.get("active_child_count", 0) or 0),
                "pending_child_ids": [
                    str(item) for item in merge_result.get("pending_child_ids", []) if str(item).strip()
                ],
                "workers_involved": [
                    str(item) for item in merge_result.get("workers_involved", []) if str(item).strip()
                ],
                "completed_child_ids": [
                    str(item) for item in merge_result.get("completed_child_ids", []) if str(item).strip()
                ],
                "linked_artifact_count": int(merge_result.get("linked_artifact_count", len(linked_artifacts)) or 0),
                "linked_artifacts": [item for item in linked_artifacts if isinstance(item, dict)],
                "review_status": str(
                    merge_result.get("review_status")
                    or merge_resolution.get("review_status")
                    or ("clean" if not conflicts else "needs_attention")
                ),
                "review_pending_keys": [
                    str(item)
                    for item in merge_result.get(
                        "review_pending_keys",
                        merge_resolution.get("pending_conflict_keys", []),
                    )
                    if str(item).strip()
                ],
                "applied_override_keys": [
                    str(item)
                    for item in merge_result.get(
                        "applied_override_keys",
                        merge_resolution.get("applied_override_keys", []),
                    )
                    if str(item).strip()
                ],
                "override_count": int(
                    merge_result.get(
                        "override_count",
                        merge_resolution.get("override_count", 0),
                    )
                    or 0
                ),
                "merge_resolution": {
                    "policy": str(merge_resolution.get("policy", "") or ""),
                    "resolved_data": resolved_data if isinstance(resolved_data, dict) else {},
                    "resolved_keys": [
                        str(item) for item in merge_resolution.get("resolved_keys", []) if str(item).strip()
                    ],
                    "source_map": source_map if isinstance(source_map, dict) else {},
                    "conflicts": [item for item in conflicts if isinstance(item, dict)],
                    "conflict_count": int(
                        merge_resolution.get(
                            "conflict_count",
                            len(conflicts) if isinstance(conflicts, list) else 0,
                        )
                        or 0
                    ),
                    "applied_overrides": (
                        merge_resolution.get("applied_overrides", {})
                        if isinstance(merge_resolution.get("applied_overrides", {}), dict)
                        else {}
                    ),
                    "applied_override_keys": [
                        str(item) for item in merge_resolution.get("applied_override_keys", []) if str(item).strip()
                    ],
                    "override_count": int(merge_resolution.get("override_count", 0) or 0),
                    "pending_conflict_keys": [
                        str(item) for item in merge_resolution.get("pending_conflict_keys", []) if str(item).strip()
                    ],
                    "review_status": str(merge_resolution.get("review_status", "") or ""),
                },
            }
        )
        payload.update(self._merge_review_signal_fields(payload))
        return payload


def create_task_merge_service(bot, **kwargs) -> TaskMergeService:
    return TaskMergeService(bot, **kwargs)
