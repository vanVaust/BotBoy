from __future__ import annotations

import asyncio
import json
from typing import List, Optional

from botboy.tasks import (
    TASK_STATUS_BLOCKED,
    TASK_STATUS_FAILED,
    TASK_STATUS_WAITING_APPROVAL,
)
from botboy.tracing import get_trace_context


async def handle_evals(bot, command: str) -> dict:
    parts = command.split()
    output_format = "text"
    wave = ""
    for token in parts[1:]:
        lowered = token.lower()
        if lowered in {"json", "text"}:
            output_format = lowered
        elif not wave:
            wave = token
    try:
        from botboy.evals import EvalReplayRunner
    except ImportError as exc:
        return {
            "success": False,
            "output": f"Eval runner unavailable: {exc}",
            "type": "evals",
        }
    try:
        runner = EvalReplayRunner.for_wave(wave) if wave else EvalReplayRunner.from_defaults()
        result = await asyncio.to_thread(runner.run)
        payload = result.to_dict()
        trace_ctx = get_trace_context()
        artifact = None
        if bot.task_store and trace_ctx and trace_ctx.task_id:
            artifact = bot.task_store.add_artifact(
                trace_ctx.task_id,
                category="eval_report",
                label=f"{payload.get('manifest_name', 'eval_report')}.json",
                content=json.dumps(payload, indent=2, sort_keys=True) + "\n",
                media_type="application/json",
                suffix=".json",
            )
        output = json.dumps(payload, indent=2, sort_keys=True) if output_format == "json" else result.render()
        data = dict(payload)
        if artifact:
            data["artifact"] = artifact.to_dict()
        return {
            "success": result.failed == 0,
            "output": output,
            "type": "evals",
            "data": data,
        }
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "success": False,
            "output": f"Eval runner error: {exc}",
            "type": "evals",
        }


async def handle_task_resume_command(
    bot,
    command: str,
    *,
    principal: str = "anonymous",
    roles: Optional[List[str]] = None,
    request_id: str = "",
) -> dict:
    parts = command.split()
    if len(parts) < 3:
        return {
            "success": False,
            "output": "Usage: task resume <task_id>",
            "type": "task",
        }

    task_id = parts[2].strip()
    record = bot.task_store.get_task(task_id) if bot.task_store else None
    if not record:
        return {"success": False, "output": f"Task '{task_id}' not found.", "type": "task"}

    if record.status not in {
        TASK_STATUS_WAITING_APPROVAL,
        TASK_STATUS_BLOCKED,
        TASK_STATUS_FAILED,
    }:
        return {
            "success": False,
            "output": f"Task {task_id} is not resumable from status '{record.status}'.",
            "type": "task",
        }
    resumed_request_id = f"{record.request_id or task_id}-resume"
    bot.task_store.add_event(
        task_id,
        event_type="resume_requested",
        status="running",
        message="Resume requested via task command",
        principal=principal or record.principal,
        request_id=resumed_request_id,
        run_id=record.run_id,
    )
    resumed = await bot.process_command(
        record.command,
        principal=principal or record.principal,
        request_id=resumed_request_id,
        roles=roles or [],
        approval_context={
            "granted": True,
            "explicit": True,
            "reason": "task_resume",
            "source": "task_command",
        },
        task_context=bot._task_context_from_record(record),
    )
    resumed_data = dict(resumed.get("data", {}) or {})
    resumed_task = resumed_data.get("task")
    if isinstance(resumed_task, dict):
        resumed_data["resumed_task"] = dict(resumed_task)
    resumed_data["resumed_from"] = {
        "task_id": record.task_id,
        "request_id": resumed_request_id,
    }
    refreshed_record = bot.task_store.get_task(record.task_id)
    if refreshed_record and refreshed_record.parent_task_id:
        bot._sync_parent_after_child(
            refreshed_record,
            principal=principal or record.principal,
            request_id=resumed_request_id,
        )
        parent_record = bot.task_store.get_task(refreshed_record.parent_task_id)
        if parent_record:
            resumed_data["parent_task"] = parent_record.to_dict()
    resumed["data"] = resumed_data
    return resumed
