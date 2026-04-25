from __future__ import annotations

from typing import Optional

from botboy.tasks import (
    LEASE_TTL_SECONDS,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
)

TASK_USAGE_FULL = (
    "Usage: task [list|show|merge|events|artifacts|children|graph|blockers|leases|operator|resume|cancel|recover|reassign] <task_id>"
    " | task merge queue [limit]"
    " | task merge set-policy <task_id> <policy>"
    " | task merge resolve <task_id> <key> <source>"
    " | task merge resolve-many <task_id> <key=source> [<key=source> ...]"
    " | task merge resolve-all-by-source <task_id> <source> [<key> ...]"
    " | task merge clear-resolution <task_id> <key>"
    " | task merge clear-many <task_id> <key> [<key> ...]"
    " | task merge apply-preset <task_id> <preset>"
    " | task merge reapply <task_id>"
)

TASK_USAGE_SHORT = (
    "Usage: task [list|show|merge|events|artifacts|children|graph|blockers|leases|operator|resume|cancel|recover|reassign] <task_id>"
    " | task merge queue [limit]"
    " | task merge resolve-many <task_id> <key=source> [<key=source> ...]"
    " | task merge clear-many <task_id> <key> [<key> ...]"
    " | task merge apply-preset <task_id> <preset>"
)


def _task_response(output: str, **data) -> dict:
    response = {"success": True, "output": output, "type": "task"}
    if data:
        response["data"] = data
    return response


def _task_error(output: str) -> dict:
    return {"success": False, "output": output, "type": "task"}


def handle_task_command(
    bot,
    command: str,
    *,
    principal: str = "anonymous",
    roles: Optional[list[str]] = None,
    request_id: str = "",
) -> Optional[dict]:
    parts = command.split()
    sub = parts[1].lower() if len(parts) > 1 else "list"
    roles = roles or []

    if not bot.task_store:
        return _task_error("Task store not enabled.")

    if sub == "resume":
        return None

    if sub == "list":
        records, total = bot.task_store.list_tasks(limit=10)
        if not records:
            return {
                "success": True,
                "output": "No tasks yet.",
                "type": "task",
                "data": {"tasks": [], "total": 0},
            }
        lines = [f"Last {len(records)} of {total} tasks:"]
        for record in records:
            lines.append(
                f"  [{record.task_id[:8]}] {record.status:16} {record.kind:12} "
                f"{record.title[:60]} | principal={record.principal}"
            )
        return _task_response(
            "\n".join(lines),
            tasks=[record.to_dict() for record in records],
            total=total,
        )

    if sub == "blockers":
        records = bot.task_store.list_blockers(limit=20)
        lines = [f"Blockers ({len(records)}):"]
        for record in records:
            lines.append(
                f"  [{record.task_id[:8]}] {record.status:16} blocked_by={record.blocked_by_task_id or '-'} "
                f"kind={record.blocked_kind or '-'} owner={record.owner}"
            )
        return _task_response(
            "\n".join(lines),
            tasks=[record.to_dict() for record in records],
        )

    if sub == "leases":
        leases = bot.task_store.list_worker_leases(limit=50)
        stale_count = sum(1 for item in leases if item["is_stale"])
        recoverable_count = sum(1 for item in leases if item["is_recoverable"])
        lines = [
            f"Worker leases ({len(leases)} total, {stale_count} stale, {recoverable_count} recoverable):"
        ]
        for item in leases:
            state = "STALE" if item["is_stale"] else item["status"].upper()
            lines.append(
                f"  [{item['task_id'][:8]}] worker={item['worker_id'] or '-'} state={state:8} "
                f"age={item['lease_age_s']}s parent={item['parent_task_id'][:8] if item['parent_task_id'] else '-'}"
            )
        return _task_response(
            "\n".join(lines),
            leases=leases,
            stale_lease_count=stale_count,
            recoverable_lease_count=recoverable_count,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
        )

    if sub == "operator":
        limit = 10
        if len(parts) > 2:
            try:
                limit = max(1, min(int(parts[2]), 50))
            except ValueError:
                return _task_error("Usage: task operator [limit]")
        workbench = bot.get_operator_workbench_payload(limit=limit)
        summary = workbench.get("summary", {}) if isinstance(workbench, dict) else {}
        merge_queue = (
            (workbench.get("merge_queue", {}) or {}).get("items", [])
            if isinstance(workbench, dict)
            else []
        )
        blockers = workbench.get("blockers", []) if isinstance(workbench, dict) else []
        recoverable = (
            workbench.get("recoverable_leases", []) if isinstance(workbench, dict) else []
        )
        stale = workbench.get("stale_leases", []) if isinstance(workbench, dict) else []
        lines = [
            "Operator workbench:",
            f"  Merge actionable: {int(summary.get('merge_actionable_count', 0) or 0)}",
            f"  Merge queue total: {int(summary.get('merge_total', 0) or 0)}",
            f"  Blocked tasks: {int(summary.get('blocked_count', 0) or 0)}",
            f"  Recoverable leases: {int(summary.get('recoverable_lease_count', 0) or 0)}",
            f"  Stale leases: {int(summary.get('stale_lease_count', 0) or 0)}",
        ]
        for item in merge_queue[:limit]:
            lines.append(
                f"  Queue [{item['task_id'][:8]}] state={item['review_status']} next={item['next_action']} "
                f"conflicts={item['conflict_count']} overrides={item['override_count']} "
                f"pending_keys={len(item['review_pending_keys'])}"
            )
        for item in blockers[: min(limit, 5)]:
            lines.append(
                f"  Blocked [{item['task_id'][:8]}] owner={item['owner']} "
                f"kind={item['blocked_kind'] or '-'} by={item['blocked_by_task_id'] or '-'}"
            )
        for item in recoverable[: min(limit, 5)]:
            lines.append(
                f"  Recover [{item['task_id'][:8]}] worker={item['worker_id'] or '-'} "
                f"age={item['lease_age_s']}s parent={item['parent_task_id'][:8] if item['parent_task_id'] else '-'}"
            )
        for item in stale[: min(limit, 5)]:
            lines.append(
                f"  Stale [{item['task_id'][:8]}] worker={item['worker_id'] or '-'} "
                f"age={item['lease_age_s']}s"
            )
        return _task_response("\n".join(lines), operator_workbench=workbench)

    if sub == "merge" and len(parts) > 2 and parts[2].lower() == "queue":
        limit = 10
        if len(parts) > 3:
            try:
                limit = max(1, min(int(parts[3]), 50))
            except ValueError:
                return _task_error("Usage: task merge queue [limit]")
        queue = bot.get_merge_review_queue(limit=limit)
        items = queue.get("items", []) if isinstance(queue, dict) else []
        lines = [
            f"Merge review queue ({queue.get('actionable_count', 0)} actionable / {queue.get('total', 0)} total):"
        ]
        for item in items:
            lines.append(
                f"  [{item['task_id'][:8]}] {item['review_status']:16} next={item['next_action']:18} "
                f"conflicts={item['conflict_count']} overrides={item['override_count']} "
                f"pending={len(item['review_pending_keys'])}"
            )
        return _task_response("\n".join(lines), merge_queue=queue)

    if sub not in {"list", "blockers", "leases"} and len(parts) < 3:
        return _task_error(TASK_USAGE_FULL)

    if sub == "merge" and len(parts) > 2 and parts[2].lower() in {"set-policy", "policy"}:
        if len(parts) < 5:
            return _task_error("Usage: task merge set-policy <task_id> <policy>")
        policy_task_id = parts[3].strip()
        policy = parts[4].strip()
        if not policy_task_id or not policy:
            return _task_error("Usage: task merge set-policy <task_id> <policy>")
        policy_record = bot.task_store.get_task(policy_task_id)
        if not policy_record:
            return _task_error(f"Task '{policy_task_id}' not found.")
        updated = bot.set_task_merge_resolution_policy(
            policy_task_id,
            policy=policy,
            principal=principal,
            request_id=request_id or policy_record.request_id or policy_task_id,
        )
        refreshed = bot.task_store.get_task(policy_task_id) if bot.task_store else updated
        task_record = refreshed or updated or policy_record
        merge = bot.get_task_merge_payload(policy_task_id, record=task_record)
        normalized = bot._merge_resolution_policy_for_record(task_record)
        return _task_response(
            f"Merge resolution policy for {policy_task_id} set to {normalized}.",
            task_record=task_record.to_dict(),
            merge=merge,
        )

    if sub == "merge" and len(parts) > 2 and parts[2].lower() in {
        "resolve",
        "override",
        "resolve-key",
    }:
        if len(parts) < 6:
            return _task_error("Usage: task merge resolve <task_id> <key> <source>")
        review_task_id = parts[3].strip()
        review_key = parts[4].strip()
        review_source = parts[5].strip()
        try:
            updated = bot.apply_task_merge_review_action(
                review_task_id,
                action="resolve",
                key=review_key,
                source=review_source,
                principal=principal,
                request_id=request_id or review_task_id,
            )
        except ValueError as exc:
            return _task_error(str(exc))
        if not updated:
            return _task_error(f"Task '{review_task_id}' not found.")
        merge = bot.get_task_merge_payload(review_task_id, record=updated)
        return _task_response(
            f"Merge review override for {review_key} set to {review_source}.",
            task_record=updated.to_dict(),
            merge=merge,
        )

    if sub == "merge" and len(parts) > 2 and parts[2].lower() in {
        "resolve-many",
        "bulk-resolve",
        "bulk-override",
    }:
        if len(parts) < 5:
            return _task_error(
                "Usage: task merge resolve-many <task_id> <key=source> [<key=source> ...]"
            )
        review_task_id = parts[3].strip()
        bulk_items: list[dict[str, str]] = []
        for token in parts[4:]:
            key_text, separator, source_text = token.partition("=")
            if not separator or not key_text.strip() or not source_text.strip():
                return _task_error(
                    "Usage: task merge resolve-many <task_id> <key=source> [<key=source> ...]"
                )
            bulk_items.append({"key": key_text.strip(), "source": source_text.strip()})
        try:
            updated = bot.apply_task_merge_review_action(
                review_task_id,
                action="resolve_many",
                items=bulk_items,
                principal=principal,
                request_id=request_id or review_task_id,
            )
        except ValueError as exc:
            return _task_error(str(exc))
        if not updated:
            return _task_error(f"Task '{review_task_id}' not found.")
        merge = bot.get_task_merge_payload(review_task_id, record=updated)
        return _task_response(
            f"Merge review overrides updated for {len(bulk_items)} key(s).",
            task_record=updated.to_dict(),
            merge=merge,
        )

    if sub == "merge" and len(parts) > 2 and parts[2].lower() in {
        "resolve-all-by-source",
        "resolve-all",
    }:
        if len(parts) < 5:
            return _task_error(
                "Usage: task merge resolve-all-by-source <task_id> <source> [<key> ...]"
            )
        review_task_id = parts[3].strip()
        review_source = parts[4].strip()
        review_keys = [item.strip() for item in parts[5:] if item.strip()]
        try:
            updated = bot.apply_task_merge_review_action(
                review_task_id,
                action="resolve_all_by_source",
                source=review_source,
                keys=review_keys,
                principal=principal,
                request_id=request_id or review_task_id,
            )
        except ValueError as exc:
            return _task_error(str(exc))
        if not updated:
            return _task_error(f"Task '{review_task_id}' not found.")
        merge = bot.get_task_merge_payload(review_task_id, record=updated)
        resolved_keys = (
            review_keys or merge.get("review_pending_keys", []) or merge.get("known_review_keys", [])
        )
        return _task_response(
            f"Merge review source {review_source} applied to {len(resolved_keys)} key(s).",
            task_record=updated.to_dict(),
            merge=merge,
        )

    if sub == "merge" and len(parts) > 2 and parts[2].lower() in {
        "clear",
        "clear-resolution",
        "clear-override",
    }:
        if len(parts) < 5:
            return _task_error("Usage: task merge clear-resolution <task_id> <key>")
        review_task_id = parts[3].strip()
        review_key = parts[4].strip()
        try:
            updated = bot.apply_task_merge_review_action(
                review_task_id,
                action="clear_resolution",
                key=review_key,
                principal=principal,
                request_id=request_id or review_task_id,
            )
        except ValueError as exc:
            return _task_error(str(exc))
        if not updated:
            return _task_error(f"Task '{review_task_id}' not found.")
        merge = bot.get_task_merge_payload(review_task_id, record=updated)
        return _task_response(
            f"Merge review override for {review_key} cleared.",
            task_record=updated.to_dict(),
            merge=merge,
        )

    if sub == "merge" and len(parts) > 2 and parts[2].lower() in {"clear-many", "bulk-clear"}:
        if len(parts) < 5:
            return _task_error("Usage: task merge clear-many <task_id> <key> [<key> ...]")
        review_task_id = parts[3].strip()
        review_keys = [item.strip() for item in parts[4:] if item.strip()]
        try:
            updated = bot.apply_task_merge_review_action(
                review_task_id,
                action="clear_many",
                keys=review_keys,
                principal=principal,
                request_id=request_id or review_task_id,
            )
        except ValueError as exc:
            return _task_error(str(exc))
        if not updated:
            return _task_error(f"Task '{review_task_id}' not found.")
        merge = bot.get_task_merge_payload(review_task_id, record=updated)
        return _task_response(
            f"Merge review overrides cleared for {len(review_keys)} key(s).",
            task_record=updated.to_dict(),
            merge=merge,
        )

    if sub == "merge" and len(parts) > 2 and parts[2].lower() in {"apply-preset", "preset"}:
        if len(parts) < 5:
            return _task_error("Usage: task merge apply-preset <task_id> <preset>")
        review_task_id = parts[3].strip()
        review_preset = parts[4].strip()
        try:
            updated = bot.apply_task_merge_review_action(
                review_task_id,
                action="apply_preset",
                preset=review_preset,
                principal=principal,
                request_id=request_id or review_task_id,
            )
        except ValueError as exc:
            return _task_error(str(exc))
        if not updated:
            return _task_error(f"Task '{review_task_id}' not found.")
        merge = bot.get_task_merge_payload(review_task_id, record=updated)
        return _task_response(
            f"Merge review preset {review_preset} applied.",
            task_record=updated.to_dict(),
            merge=merge,
        )

    if sub == "merge" and len(parts) > 2 and parts[2].lower() in {
        "reapply",
        "refresh",
        "apply-policy",
    }:
        if len(parts) < 4:
            return _task_error("Usage: task merge reapply <task_id>")
        review_task_id = parts[3].strip()
        try:
            updated = bot.apply_task_merge_review_action(
                review_task_id,
                action="reapply",
                principal=principal,
                request_id=request_id or review_task_id,
            )
        except ValueError as exc:
            return _task_error(str(exc))
        if not updated:
            return _task_error(f"Task '{review_task_id}' not found.")
        merge = bot.get_task_merge_payload(review_task_id, record=updated)
        return _task_response(
            f"Merge review for {review_task_id} reapplied.",
            task_record=updated.to_dict(),
            merge=merge,
        )

    extra_parts = command.split(None, 3) if sub == "reassign" else None
    if sub == "reassign":
        if len(extra_parts or []) < 4:
            return _task_error("Usage: task reassign <task_id> <worker_id>")
        task_id = extra_parts[2].strip()
    else:
        task_id = parts[2].strip()
    record = bot.task_store.get_task(task_id)
    if not record:
        return _task_error(f"Task '{task_id}' not found.")

    if sub == "show":
        task = record.to_dict()
        lines = [
            f"Task: {task['task_id']}",
            f"  Root: {task['root_task_id']}",
            f"  Parent: {task['parent_task_id'] or '-'}",
            f"  Status: {task['status']}",
            f"  Kind: {task['kind']}",
            f"  Owner: {task['owner']}",
            f"  Principal: {task['principal']}",
            f"  Request: {task['request_id'] or '-'}",
            f"  Run: {task['run_id'] or '-'}",
            f"  Scheduler: {task['scheduler_task_id'] or '-'}",
            f"  Delegation: {task['delegation_status'] or '-'}",
            f"  Worker: {task['delegated_to_worker'] or '-'}",
            f"  Blocked by: {task['blocked_by_task_id'] or '-'}",
            f"  Blocked kind: {task['blocked_kind'] or '-'}",
            f"  Blocked reason: {task['blocked_reason'] or '-'}",
            f"  Lease until: {task['lease_expires_at'] or '-'}",
            f"  Heartbeat: {task['heartbeat_at'] or '-'}",
            f"  Title: {task['title']}",
            f"  Summary: {task['summary'] or '-'}",
            f"  Command: {task['command'] or '-'}",
            f"  Created: {task['created_at']}",
            f"  Started: {task['started_at'] or '-'}",
            f"  Ended: {task['ended_at'] or '-'}",
        ]
        return _task_response("\n".join(lines), task_record=task)

    if sub == "merge":
        merge = bot.get_task_merge_payload(task_id, record=record)
        if not merge["available"]:
            return {
                "success": True,
                "output": (
                    f"Task {task_id} has no merge result."
                    f" Configured policy: {merge['configured_resolution_policy'] or '-'}."
                ),
                "type": "task",
                "data": {"task_record": record.to_dict(), "merge": merge},
            }
        resolution = merge.get("merge_resolution", {})
        conflicts = resolution.get("conflicts", []) if isinstance(resolution, dict) else []
        lines = [
            f"Merge review for {task_id}:",
            f"  Merge policy: {merge['merge_policy']}",
            f"  Configured policy: {merge['configured_resolution_policy'] or '-'}",
            f"  Configured overrides: {', '.join(f'{key}={value}' for key, value in merge['configured_resolution_overrides'].items()) or '-'}",
            f"  Resolution policy: {merge['resolution_policy'] or '-'}",
            f"  Effective policy: {merge.get('effective_resolution_policy') or '-'}",
            f"  Available policies: {', '.join(merge['available_resolution_policies']) or '-'}",
            f"  Review status: {merge['review_status'] or '-'}",
            f"  Review state: {merge.get('review_state') or '-'}",
            f"  Next action: {merge.get('next_action') or '-'}",
            f"  Actionable: {'yes' if merge.get('actionable') else 'no'}",
            f"  Policy delta: {merge.get('policy_delta') or '-'}",
            f"  Available review actions: {', '.join(merge['available_review_actions']) or '-'}",
            f"  Available presets: {', '.join(item.get('id', '') for item in merge.get('available_review_presets', [])) or '-'}",
            f"  Completed children: {merge['completed_child_count']}",
            f"  Active children: {merge['active_child_count']}",
            f"  Linked artifacts: {merge['linked_artifact_count']}",
            f"  Conflict count: {resolution.get('conflict_count', 0)}",
            f"  Override count: {merge['override_count']}",
            f"  Workers: {', '.join(merge['workers_involved']) or '-'}",
            f"  Valid sources: {', '.join(merge.get('valid_sources', [])) or '-'}",
            f"  Resolved keys: {', '.join(resolution.get('resolved_keys', [])) or '-'}",
            f"  Known review keys: {', '.join(merge.get('known_review_keys', [])) or '-'}",
            f"  Pending review keys: {', '.join(merge['review_pending_keys']) or '-'}",
            f"  Applied override keys: {', '.join(merge['applied_override_keys']) or '-'}",
            f"  Pending children: {', '.join(merge['pending_child_ids']) or '-'}",
        ]
        for conflict in conflicts[:5]:
            lines.append(
                "  Conflict: "
                f"{conflict.get('key', '-')} "
                f"{conflict.get('previous_worker') or conflict.get('previous_child_task_id') or '-'}"
                " -> "
                f"{conflict.get('incoming_worker') or conflict.get('incoming_child_task_id') or '-'}"
                f" [{conflict.get('resolution', '-')}]"
            )
        return _task_response("\n".join(lines), task_record=record.to_dict(), merge=merge)

    if sub == "children":
        children = bot.task_store.list_children(task_id, limit=50)
        if not children:
            return _task_response(f"No child tasks for {task_id}.")
        lines = [f"Children for {task_id}:"]
        for child in children:
            lines.append(
                f"  [{child.task_id[:8]}] {child.status:16} worker={child.delegated_to_worker or '-'} "
                f"owner={child.owner} cmd={child.command[:56]}"
            )
        return _task_response(
            "\n".join(lines),
            task_record=record.to_dict(),
            children=[child.to_dict() for child in children],
        )

    if sub == "graph":
        graph = bot.task_store.task_graph(task_id)
        tasks = graph.get("tasks", [])
        lines = [f"Task graph for {task_id} ({len(tasks)} nodes):"]
        for item in tasks:
            lines.append(
                f"  [{item['task_id'][:8]}] parent={item['parent_task_id'] or '-'} "
                f"status={item['status']} worker={item.get('delegated_to_worker') or '-'}"
            )
        return _task_response("\n".join(lines), **graph)

    if sub == "events":
        events = bot.task_store.get_events(task_id)
        if not events:
            return _task_response(f"No events for task {task_id}.")
        lines = [f"Events for {task_id}:"]
        for event in events:
            lines.append(
                f"  [{event.event_type}] {event.status or '-'} | {event.message or '-'} | request={event.request_id or '-'}"
            )
        return _task_response(
            "\n".join(lines),
            task_record=record.to_dict(),
            events=[event.to_dict() for event in events],
        )

    if sub == "artifacts":
        artifacts = bot.task_store.get_artifacts(task_id)
        if not artifacts:
            return _task_response(f"No artifacts for task {task_id}.")
        lines = [f"Artifacts for {task_id}:"]
        for artifact in artifacts:
            lines.append(
                f"  [{artifact.category}] {artifact.label} | {artifact.media_type} | {artifact.file_path}"
            )
        return _task_response(
            "\n".join(lines),
            task_record=record.to_dict(),
            artifacts=[artifact.to_dict() for artifact in artifacts],
        )

    if sub == "cancel":
        cancelled = bot.task_store.cancel_task(
            task_id,
            principal=principal or record.principal,
            request_id=record.request_id,
            reason="Cancelled via task command",
        )
        return _task_response(
            f"Cancelled task {task_id}.",
            task_record=cancelled.to_dict() if cancelled else None,
        )

    if sub == "recover":
        if "admin" not in roles:
            return _task_error("task recover requires admin role.")
        recovered = bot.task_store.recover_stale_worker_task(
            task_id,
            principal=principal or record.principal,
            request_id=record.request_id or task_id,
            run_id=record.run_id,
        )
        if not recovered:
            return _task_error(f"Task {task_id} has no recoverable stale worker lease.")
        return _task_response(
            f"Recovered stale worker lease for task {task_id}.",
            **recovered,
        )

    if sub == "reassign":
        if "admin" not in roles:
            return _task_error("task reassign requires admin role.")
        worker_id = extra_parts[3].strip().lower()
        worker = bot.workers.get(worker_id)
        if not worker:
            return _task_error(f"Unknown worker '{worker_id}'.")
        if not record.parent_task_id or not record.delegated_to_worker:
            return _task_error("Only delegated child tasks can be reassigned.")
        if record.status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}:
            return _task_error("Only delegated queued or running child tasks can be reassigned.")
        updated = bot.task_store.reassign_task(
            task_id,
            worker_id=worker_id,
            principal=principal or record.principal,
            request_id=record.request_id,
            run_id=record.run_id,
        )
        return _task_response(
            f"Reassigned task {task_id} to worker:{worker_id}.",
            task_record=updated.to_dict() if updated else None,
            worker=worker.to_dict(),
        )

    if sub == "resume":
        if record.status not in {
            TASK_STATUS_WAITING_APPROVAL,
            TASK_STATUS_BLOCKED,
            TASK_STATUS_FAILED,
        }:
            return _task_error(f"Task {task_id} is not resumable from status '{record.status}'.")
        return None

    return _task_error(TASK_USAGE_SHORT)

