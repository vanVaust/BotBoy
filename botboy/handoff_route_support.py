from __future__ import annotations

from typing import Optional

from botboy.tasks import TaskContext
from botboy.tracing import get_trace_context


def resolve_active_parent_task_context(bot) -> Optional[TaskContext]:
    trace_ctx = get_trace_context()
    if not (trace_ctx and trace_ctx.task_id and bot.task_store):
        return None
    record = bot.task_store.get_task(trace_ctx.task_id)
    if not record:
        return None
    return bot._task_context_from_record(record)


async def handle_handoff_command(
    bot,
    command: str,
    *,
    principal: str = "anonymous",
    roles: Optional[list[str]] = None,
    approval_context: Optional[dict] = None,
) -> dict:
    roles = roles or []
    lower = command.lower()

    if lower.startswith("handoff suggest "):
        return bot._handle_handoff_suggest(command)

    if lower.startswith("handoff batch "):
        spec_text = command[len("handoff batch "):].strip()
        try:
            resolution_policy, specs = bot._parse_handoff_batch_request(spec_text)
        except ValueError as exc:
            return {"success": False, "output": str(exc), "type": "handoff_batch"}
        task_ctx = resolve_active_parent_task_context(bot)
        if not task_ctx:
            return {
                "success": False,
                "output": "Handoff batch requires an active task context.",
                "type": "handoff_batch",
            }
        return await bot._run_worker_handoff_batch(
            parent_task=task_ctx,
            specs=specs,
            resolution_policy=resolution_policy,
            principal=principal,
            roles=roles,
            approval_context=approval_context,
        )

    parts = command.split(None, 2)
    if len(parts) < 3:
        return {
            "success": False,
            "output": "Usage: handoff <worker_id> <command>",
            "type": "handoff",
        }

    task_ctx = resolve_active_parent_task_context(bot)
    if not task_ctx:
        return {
            "success": False,
            "output": "Handoff requires an active task context.",
            "type": "handoff",
        }

    return await bot._run_worker_handoff(
        parent_task=task_ctx,
        worker_id=parts[1].lower(),
        delegated_command=parts[2].strip(),
        principal=principal,
        roles=roles,
        approval_context=approval_context,
    )
