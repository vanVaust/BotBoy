from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from botboy.operator_workbench_support import merge_review_signal_fields
from botboy.task_merge_helpers import (
    merge_override_source_matches,
    merge_value_is_non_null,
    merge_value_richness_score,
    merge_worker_priority,
    normalize_merge_resolution_policy,
    normalize_merge_review_preset,
    parse_handoff_batch_request,
    parse_handoff_batch_specs,
    resolve_child_merge_data,
)
from botboy.tasks import (
    DELEGATION_STATUS_AWAITING_MERGE,
    DELEGATION_STATUS_BLOCKED_ON_CHILD,
    DELEGATION_STATUS_LEASED,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
    TaskContext,
)

from botboy.constants import (
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



class WorkerHandoffService:
    def __init__(
        self,
        bot: Any | None = None,
        *,
        task_store: Any | None = None,
        workers: Any | None = None,
        process_command: Callable[..., Awaitable[dict]] | None = None,
    ) -> None:
        self.bot = bot
        self._task_store_arg = task_store
        self._workers_arg = workers
        self._process_command_arg = process_command

    @property
    def task_store(self):
        return self._task_store_arg if self._task_store_arg is not None else getattr(self.bot, "task_store", None)

    @property
    def workers(self):
        return self._workers_arg if self._workers_arg is not None else getattr(self.bot, "workers", {})

    @property
    def process_command(self):
        return self._process_command_arg if self._process_command_arg is not None else getattr(self.bot, "process_command", None)

    @staticmethod
    def _child_request_id(root_request_id: str, worker_id: str, attempt: int) -> str:
        base = root_request_id.strip() or "req-handoff"
        return f"{base}-w{worker_id}-{attempt}"

    @staticmethod
    def _normalize_merge_resolution_policy(policy: str, *, strict: bool = False) -> str:
        return normalize_merge_resolution_policy(
            policy,
            strict=strict,
            default_policy=MERGE_RESOLUTION_LAST_CHILD_WINS,
            supported_policies=SUPPORTED_MERGE_RESOLUTION_POLICIES,
        )

    @staticmethod
    def _infer_task_status(result: dict) -> str:
        if result.get("success", False):
            return TASK_STATUS_COMPLETED
        policy = result.get("data", {}).get("policy", {}) if isinstance(result.get("data"), dict) else {}
        if policy.get("denied_by_policy"):
            if policy.get("needs_approval"):
                return TASK_STATUS_WAITING_APPROVAL
            return TASK_STATUS_BLOCKED
        result_status = str(result.get("data", {}).get("task_status", "")).strip().lower()
        if result_status in {
            TASK_STATUS_WAITING_APPROVAL,
            TASK_STATUS_BLOCKED,
            TASK_STATUS_FAILED,
            TASK_STATUS_CANCELLED,
            TASK_STATUS_COMPLETED,
        }:
            return result_status
        return TASK_STATUS_FAILED

    @staticmethod
    def _merge_value_is_non_null(value: Any) -> bool:
        return merge_value_is_non_null(value)

    @classmethod
    def _merge_value_richness_score(cls, value: Any) -> tuple[int, int]:
        return merge_value_richness_score(value)

    @staticmethod
    def _merge_worker_priority(worker_id: str) -> int:
        return merge_worker_priority(worker_id, MERGE_WORKER_PRIORITY)

    @staticmethod
    def _merge_override_source_matches(source: str, *, task_id: str, worker_id: str) -> bool:
        return merge_override_source_matches(source, task_id=task_id, worker_id=worker_id)

    @classmethod
    def _parse_handoff_batch_request(cls, spec_text: str) -> tuple[str, list[tuple[str, str]]]:
        usage_text = (
            "Usage: handoff batch "
            "[--resolution-policy=<last_child_wins|first_child_wins|prefer_non_null|prefer_richer_value|prefer_worker_priority>] "
            "<worker>:<command> || <worker>:<command>"
        )
        return parse_handoff_batch_request(
            spec_text,
            normalize_policy=lambda value: cls._normalize_merge_resolution_policy(value, strict=True),
            default_policy=MERGE_RESOLUTION_LAST_CHILD_WINS,
            usage_text=usage_text,
        )

    @staticmethod
    def _parse_handoff_batch_specs(spec_text: str) -> list[tuple[str, str]]:
        usage_text = (
            "Usage: handoff batch "
            "[--resolution-policy=<last_child_wins|first_child_wins|prefer_non_null|prefer_richer_value|prefer_worker_priority>] "
            "<worker>:<command> || <worker>:<command>"
        )
        return parse_handoff_batch_specs(spec_text, usage_text=usage_text)

    @staticmethod
    def _normalize_merge_review_preset(preset: str, *, strict: bool = False) -> str:
        return normalize_merge_review_preset(
            preset,
            strict=strict,
            review_presets=MERGE_REVIEW_PRESETS,
        )

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

    def _merge_valid_sources(self, merge: dict) -> list[str]:
        valid_sources = set()
        valid_sources.update(str(item).strip() for item in merge.get("completed_child_ids", []) if str(item).strip())
        valid_sources.update(str(item).strip() for item in merge.get("workers_involved", []) if str(item).strip())
        valid_sources.update(
            f"worker:{str(item).strip()}"
            for item in merge.get("workers_involved", [])
            if str(item).strip()
        )
        return sorted(valid_sources)

    def _merge_available_review_presets(self, merge: dict) -> list[dict]:
        available_workers = {
            str(item).strip().lower()
            for item in merge.get("workers_involved", [])
            if str(item).strip()
        }
        presets: list[dict] = []
        for _preset_id, definition in MERGE_REVIEW_PRESETS.items():
            if definition.get("mode") == "source" and str(definition.get("source", "")).strip().lower() not in available_workers:
                continue
            presets.append(dict(definition))
        return presets

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

    def _merge_review_signal_fields(self, merge: dict) -> dict:
        return merge_review_signal_fields(self, merge)

    def _merge_resolution_policy_for_record(self, record) -> str:
        payload = record.payload if record else {}
        if not isinstance(payload, dict):
            return MERGE_RESOLUTION_LAST_CHILD_WINS
        return self._normalize_merge_resolution_policy(payload.get("merge_resolution_policy", ""))

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

    def _resolve_child_merge_data(
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
            worker_priority_map=MERGE_WORKER_PRIORITY,
            normalize_policy=lambda value: self._normalize_merge_resolution_policy(value),
        )

    def _build_parent_merge_result(self, parent_record, *, child_merges: list[dict]) -> dict:
        if not self.task_store:
            return {}
        siblings = self.task_store.list_children(parent_record.task_id, limit=200)
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
        merge_resolution = self._resolve_child_merge_data(
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
            "available_review_actions": list(MERGE_REVIEW_ACTIONS),
            "merged_from_child_task_id": str(latest_merge.get("merged_from_child_task_id", "") or ""),
            "merged_from_owner": str(latest_merge.get("merged_from_owner", "") or ""),
            "merged_from_worker": str(latest_merge.get("merged_from_worker", "") or ""),
            "child_status": str(latest_merge.get("child_status", "") or ""),
            "child_summary": str(latest_merge.get("child_summary", "") or ""),
            "child_result": latest_merge.get("child_result", {})
            if isinstance(latest_merge.get("child_result", {}), dict)
            else {},
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

    def _build_child_approval_context(self, approval_context: Optional[dict]) -> dict:
        child_approval_context = dict(approval_context or {})
        inherited_scope = bool((approval_context or {}).get("approval_scope")) or (
            bool((approval_context or {}).get("granted"))
            and bool((approval_context or {}).get("explicit"))
        )
        child_approval_context["granted"] = inherited_scope
        if inherited_scope and "approval_scope" not in child_approval_context:
            child_approval_context["approval_scope"] = "parent_inherited"
        child_approval_context.setdefault("source", "worker_handoff")
        child_approval_context.setdefault("reason", "child_execution")
        return child_approval_context

    def _lookup_worker(self, worker_id: str):
        lookup = getattr(self.workers, "get", None)
        if callable(lookup):
            return lookup(worker_id)
        if isinstance(self.workers, dict):
            return self.workers.get(worker_id)
        return None

    def _create_worker_child_task(
        self,
        *,
        parent_task: TaskContext,
        worker,
        delegated_command: str,
        principal: str,
        child_request_id: str,
        attempt_count: int,
    ) -> TaskContext:
        if not self.task_store:
            raise RuntimeError("Task store not enabled.")
        child_ctx = self.task_store.create_task(
            title=f"{worker.display_name}: {delegated_command[:96]}",
            kind="worker_handoff",
            owner=f"worker:{worker.worker_id}",
            principal=principal,
            request_id=child_request_id,
            command=delegated_command,
            payload={
                "worker_id": worker.worker_id,
                "worker_role": worker.role,
                "delegated_command": delegated_command,
                "parent_task_id": parent_task.task_id,
            },
            parent_task_id=parent_task.task_id,
            root_task_id=parent_task.root_task_id,
            status=TASK_STATUS_QUEUED,
            summary=f"Delegated to worker:{worker.worker_id}",
            delegation_status=DELEGATION_STATUS_LEASED,
            delegated_to_worker=worker.worker_id,
            attempt_count=attempt_count,
        )
        lease_expires_at = self.task_store._now()
        heartbeat_at = lease_expires_at
        self.task_store.update_task(
            child_ctx.task_id,
            delegation_status=DELEGATION_STATUS_LEASED,
            delegated_to_worker=worker.worker_id,
            lease_expires_at=lease_expires_at,
            heartbeat_at=heartbeat_at,
        )
        self.task_store.add_event(
            parent_task.task_id,
            event_type="handoff_spawned",
            status=TASK_STATUS_RUNNING,
            message=f"Delegated to worker:{worker.worker_id}",
            principal=principal,
            request_id=parent_task.request_id,
            payload_ref=child_ctx.task_id,
        )
        self.task_store.add_event(
            child_ctx.task_id,
            event_type="worker_started",
            status=TASK_STATUS_RUNNING,
            message=f"Worker {worker.display_name} started",
            principal=principal,
            request_id=child_request_id,
            payload_ref=worker.worker_id,
        )
        return child_ctx

    async def _execute_worker_child_task(
        self,
        *,
        parent_task: TaskContext,
        child_ctx: TaskContext,
        worker,
        delegated_command: str,
        principal: str,
        roles: Optional[list[str]] = None,
        approval_context: Optional[dict] = None,
    ) -> tuple[dict, Any, str]:
        if not callable(self.process_command):
            raise RuntimeError("process_command is not configured.")
        child_result = await self.process_command(
            delegated_command,
            principal=principal,
            request_id=child_ctx.request_id,
            roles=roles or [],
            approval_context=self._build_child_approval_context(approval_context),
            task_context=child_ctx,
        )
        child_record = self.task_store.get_task(child_ctx.task_id) if self.task_store else None
        child_status = child_record.status if child_record else self._infer_task_status(child_result)
        if self.task_store:
            self.task_store.add_event(
                child_ctx.task_id,
                event_type="worker_heartbeat",
                status=child_status,
                message=f"Worker {worker.display_name} heartbeat",
                principal=principal,
                request_id=child_ctx.request_id,
                run_id=child_record.run_id if child_record else "",
                payload_ref=worker.worker_id,
            )
        self._sync_parent_after_child(
            child_record,
            principal=principal,
            request_id=parent_task.request_id,
        )
        return child_result, child_record, child_status

    async def _run_worker_handoff_batch(
        self,
        *,
        parent_task: TaskContext,
        specs: list[tuple[str, str]],
        resolution_policy: str,
        principal: str,
        roles: Optional[list[str]] = None,
        approval_context: Optional[dict] = None,
    ) -> dict:
        if not self.task_store:
            return {"success": False, "output": "Task store not enabled.", "type": "handoff_batch"}
        parent_record = self.task_store.get_task(parent_task.task_id)
        parent_payload = parent_record.payload if parent_record and isinstance(parent_record.payload, dict) else {}
        parent_payload = dict(parent_payload)
        parent_payload["merge_resolution_policy"] = self._normalize_merge_resolution_policy(resolution_policy)
        base_attempt = int(getattr(parent_record, "attempt_count", 0) or 0)
        prepared: list[tuple[Any, str, TaskContext]] = []
        for offset, (worker_id, delegated_command) in enumerate(specs, start=1):
            worker = self._lookup_worker(worker_id)
            if not worker:
                return {"success": False, "output": f"Unknown worker '{worker_id}'.", "type": "handoff_batch"}
            child_request_id = self._child_request_id(parent_task.request_id, worker_id, base_attempt + offset)
            child_ctx = self._create_worker_child_task(
                parent_task=parent_task,
                worker=worker,
                delegated_command=delegated_command,
                principal=principal,
                child_request_id=child_request_id,
                attempt_count=base_attempt + offset,
            )
            prepared.append((worker, delegated_command, child_ctx))
        self.task_store.update_task(
            parent_task.task_id,
            status=TASK_STATUS_RUNNING,
            delegation_status=DELEGATION_STATUS_BLOCKED_ON_CHILD,
            delegated_to_worker="batch",
            blocked_by_task_id=prepared[0][2].task_id if prepared else "",
            blocked_kind=TASK_STATUS_RUNNING,
            blocked_reason=f"Waiting on {len(prepared)} worker children",
            attempt_count=base_attempt + len(prepared),
            payload=parent_payload,
        )
        self.task_store.add_event(
            parent_task.task_id,
            event_type="handoff_batch_spawned",
            status=TASK_STATUS_RUNNING,
            message=f"Delegated batch to {len(prepared)} workers",
            principal=principal,
            request_id=parent_task.request_id,
            payload_ref=",".join(item[2].task_id for item in prepared),
        )
        child_results = []
        child_records = []
        for worker, delegated_command, child_ctx in prepared:
            child_result, child_record, child_status = await self._execute_worker_child_task(
                parent_task=parent_task,
                child_ctx=child_ctx,
                worker=worker,
                delegated_command=delegated_command,
                principal=principal,
                roles=roles or [],
                approval_context=approval_context,
            )
            child_results.append(
                {
                    "worker": worker.to_dict(),
                    "child_task": child_record.to_dict() if child_record else {"task_id": child_ctx.task_id},
                    "child_result": child_result,
                    "task_status": child_status,
                }
            )
            child_records.append(child_record.to_dict() if child_record else {"task_id": child_ctx.task_id})
        merged_parent = self.task_store.get_task(parent_task.task_id)
        overall_success = bool(merged_parent and merged_parent.status == TASK_STATUS_COMPLETED)
        return {
            "success": overall_success,
            "output": (
                f"Batch handoff completed across {len(prepared)} workers."
                if overall_success
                else f"Batch handoff finished with parent status '{merged_parent.status if merged_parent else 'unknown'}'."
            ),
            "type": "handoff_batch",
            "data": {
                "parent_task": merged_parent.to_dict() if merged_parent else {"task_id": parent_task.task_id},
                "merge": merged_parent.result if merged_parent else {},
                "children": child_records,
                "child_results": child_results,
                "task_status": merged_parent.status if merged_parent else TASK_STATUS_FAILED,
                "worker_count": len(prepared),
            },
        }

    async def _run_worker_handoff(
        self,
        *,
        parent_task: TaskContext,
        worker_id: str,
        delegated_command: str,
        principal: str,
        roles: Optional[list[str]] = None,
        approval_context: Optional[dict] = None,
    ) -> dict:
        if not self.task_store:
            return {"success": False, "output": "Task store not enabled.", "type": "handoff"}
        worker = self._lookup_worker(worker_id)
        if not worker:
            return {"success": False, "output": f"Unknown worker '{worker_id}'.", "type": "handoff"}
        parent_record = self.task_store.get_task(parent_task.task_id)
        attempt_count = int(getattr(parent_record, "attempt_count", 0) or 0) + 1 if parent_record else 1
        child_request_id = self._child_request_id(parent_task.request_id, worker_id, attempt_count)
        child_ctx = self._create_worker_child_task(
            parent_task=parent_task,
            worker=worker,
            delegated_command=delegated_command,
            principal=principal,
            child_request_id=child_request_id,
            attempt_count=attempt_count,
        )
        self.task_store.update_task(
            parent_task.task_id,
            status=TASK_STATUS_RUNNING,
            delegation_status=DELEGATION_STATUS_BLOCKED_ON_CHILD,
            delegated_to_worker=worker_id,
            blocked_by_task_id=child_ctx.task_id,
            blocked_kind=TASK_STATUS_RUNNING,
            blocked_reason=f"Waiting on worker:{worker_id}",
            attempt_count=attempt_count,
        )
        child_result, child_record, child_status = await self._execute_worker_child_task(
            parent_task=parent_task,
            child_ctx=child_ctx,
            worker=worker,
            delegated_command=delegated_command,
            principal=principal,
            roles=roles or [],
            approval_context=approval_context,
        )
        if child_status == TASK_STATUS_COMPLETED:
            merged_parent = self.task_store.get_task(parent_task.task_id)
            return {
                "success": True,
                "output": f"Handoff to worker:{worker_id} completed.",
                "type": "handoff",
                "data": {
                    "worker": worker.to_dict(),
                    "parent_task": merged_parent.to_dict() if merged_parent else {"task_id": parent_task.task_id},
                    "merge": merged_parent.result if merged_parent else {},
                    "child_task": child_record.to_dict() if child_record else {"task_id": child_ctx.task_id},
                    "child_result": child_result,
                    "task_status": TASK_STATUS_COMPLETED,
                },
            }
        blocked_kind = child_status if child_status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED} else TASK_STATUS_FAILED
        return {
            "success": False,
            "output": f"Handoff to worker:{worker_id} blocked by child status '{child_status}'.",
            "type": "handoff",
            "data": {
                "worker": worker.to_dict(),
                "child_task": child_record.to_dict() if child_record else {"task_id": child_ctx.task_id},
                "child_result": child_result,
                "task_status": TASK_STATUS_BLOCKED,
            },
        }

    def _sync_parent_after_child(
        self,
        child_record,
        *,
        principal: str,
        request_id: str,
    ) -> None:
        if not self.task_store or not child_record or not child_record.parent_task_id:
            return
        parent_record = self.task_store.get_task(child_record.parent_task_id)
        if not parent_record:
            return
        if child_record.status == TASK_STATUS_COMPLETED:
            siblings = self.task_store.list_children(parent_record.task_id, limit=200)
            active_siblings = [
                sibling
                for sibling in siblings
                if sibling.task_id != child_record.task_id
                and sibling.status in {
                    TASK_STATUS_QUEUED,
                    TASK_STATUS_RUNNING,
                    TASK_STATUS_WAITING_APPROVAL,
                    TASK_STATUS_BLOCKED,
                }
            ]
            linked_artifacts = self.task_store.link_artifacts_from_task(
                parent_record.task_id,
                child_record.task_id,
                category_prefix="merged",
                label_prefix="Merged child artifact",
                limit=100,
            )
            prior_merge_result = parent_record.result if isinstance(parent_record.result, dict) else {}
            prior_merges = list(prior_merge_result.get("child_merges", [])) if isinstance(prior_merge_result.get("child_merges", []), list) else []
            current_merge = {
                "merge_policy": "single_child_last_writer",
                "merged_from_child_task_id": child_record.task_id,
                "merged_from_owner": child_record.owner,
                "merged_from_worker": child_record.delegated_to_worker or "",
                "child_status": child_record.status,
                "child_summary": child_record.summary,
                "child_result": child_record.result,
                "linked_artifact_count": len(linked_artifacts),
                "linked_artifacts": [
                    {
                        "artifact_id": artifact.artifact_id,
                        "task_id": artifact.task_id,
                        "category": artifact.category,
                        "label": artifact.label,
                        "file_path": artifact.file_path,
                        "media_type": artifact.media_type,
                        "sha256": artifact.sha256,
                    }
                    for artifact in linked_artifacts
                ],
            }
            filtered_prior_merges = [
                item
                for item in prior_merges
                if str(item.get("merged_from_child_task_id", "")) != child_record.task_id
            ]
            child_merges = filtered_prior_merges + [current_merge]
            merge_result = self._build_parent_merge_result(parent_record, child_merges=child_merges)
            try:
                self.task_store.write_artifact(
                    parent_record.task_id,
                    category="merge_report",
                    label=f"Merge report for {child_record.task_id[:8]}",
                    filename=f"merge_{child_record.task_id[:8]}.json",
                    content=json.dumps(merge_result, indent=2, sort_keys=True) + "\n",
                    media_type="application/json",
                )
            except OSError:
                pass
            self.task_store.add_event(
                parent_record.task_id,
                event_type="child_completed",
                status=child_record.status,
                message=f"Child task {child_record.task_id} completed",
                principal=principal,
                request_id=request_id,
                run_id=child_record.run_id or "",
                payload_ref=child_record.task_id,
            )
            if active_siblings:
                self.task_store.update_task(
                    parent_record.task_id,
                    status=TASK_STATUS_BLOCKED,
                    delegation_status=DELEGATION_STATUS_BLOCKED_ON_CHILD,
                    summary=f"Child {child_record.task_id[:8]} merged; waiting on {len(active_siblings)} sibling tasks",
                    blocked_by_task_id=active_siblings[0].task_id,
                    blocked_kind="child_active",
                    blocked_reason=f"{len(active_siblings)} sibling child tasks still active",
                    result=merge_result,
                )
                self.task_store.add_event(
                    parent_record.task_id,
                    event_type="merge_deferred",
                    status=TASK_STATUS_BLOCKED,
                    message=f"Deferred parent completion; {len(active_siblings)} sibling child tasks still active",
                    principal=principal,
                    request_id=request_id,
                    run_id=child_record.run_id or "",
                    payload_ref=active_siblings[0].task_id,
                )
                return
            self.task_store.update_task(
                parent_record.task_id,
                status=TASK_STATUS_COMPLETED,
                delegation_status=DELEGATION_STATUS_AWAITING_MERGE,
                summary=f"Merged child {child_record.task_id[:8]} from {child_record.owner}",
                blocked_by_task_id="",
                blocked_kind="",
                blocked_reason="",
                result=merge_result,
                ended=True,
            )
            self.task_store.add_event(
                parent_record.task_id,
                event_type="merge_completed",
                status=TASK_STATUS_COMPLETED,
                message=(
                    f"Merged child result from {child_record.owner}"
                    f" with {len(linked_artifacts)} linked artifacts"
                ),
                principal=principal,
                request_id=request_id,
                run_id=child_record.run_id or "",
                payload_ref=child_record.task_id,
            )
            return
        blocked_kind = (
            child_record.status
            if child_record.status in {TASK_STATUS_WAITING_APPROVAL, TASK_STATUS_BLOCKED}
            else TASK_STATUS_FAILED
        )
        self.task_store.update_task(
            parent_record.task_id,
            status=TASK_STATUS_BLOCKED,
            delegation_status=DELEGATION_STATUS_BLOCKED_ON_CHILD,
            blocked_by_task_id=child_record.task_id,
            blocked_kind=blocked_kind,
            blocked_reason=f"Child task {child_record.task_id} ended with {child_record.status}",
        )
        self.task_store.add_event(
            parent_record.task_id,
            event_type="child_failed" if child_record.status == TASK_STATUS_FAILED else "child_blocked",
            status=TASK_STATUS_BLOCKED,
            message=f"Child task {child_record.task_id} ended with {child_record.status}",
            principal=principal,
            request_id=request_id,
            run_id=child_record.run_id or "",
            payload_ref=child_record.task_id,
        )
