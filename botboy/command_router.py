from __future__ import annotations

from botboy.handoff_route_support import handle_handoff_command
from botboy.intelligence_route_support import handle_intelligence_route
from botboy.remote_readiness import handle_security_command
from botboy.runtime_command_support import handle_memory_command as build_memory_command_response


async def route_command(
    bot,
    command: str,
    *,
    principal: str = "anonymous",
    roles: list[str] | None = None,
    approval_context: dict | None = None,
    request_id: str = "",
) -> dict:
    lower = command.lower()
    parts = lower.split()
    first = parts[0] if parts else ""
    roles = roles or []

    if first in ("help", "?"):
        return bot._show_help()
    if first == "status":
        return bot._show_status()
    if first == "version":
        return {"success": True, "output": f"BotBoy v{bot.VERSION}", "type": "version"}
    if first == "skills":
        names = bot.skills.list_skills() if bot.skills else []
        return {
            "success": True,
            "output": "Skills: " + ", ".join(names) if names else "No skills loaded.",
            "type": "skills",
        }
    if first in ("skilllib", "skilllibrary"):
        return bot._handle_agent_skill_library(command)
    if first == "skillroute":
        return bot._handle_agent_skill_route(command)
    if first == "performance":
        report = bot.monitor.get_report() if bot.monitor else "Monitoring not available."
        return {"success": True, "output": report, "type": "performance"}
    if first in ("contracts", "contract"):
        return bot._handle_contracts(command)
    if first in ("eval", "evals"):
        return await bot._handle_evals(command)
    if first == "security":
        return handle_security_command(bot, command)
    if first in ("worker", "workers"):
        return bot._handle_workers(command)
    if first in ("reflect", "reflection"):
        return bot._handle_reflection_archive(command, principal=principal)
    if first == "a2a":
        return bot._handle_a2a(command, principal=principal)
    if first in ("remember", "store", "save", "search", "forget", "delete", "memories", "memstats"):
        return build_memory_command_response(bot, command, first=first)
    if first == "schedule":
        return await bot._handle_schedule(command)
    if first == "history":
        return await bot._handle_history(command)
    if first in ("task", "tasks"):
        return await bot._handle_tasks(command, principal=principal, roles=roles, request_id=request_id)
    if first == "handoff":
        return await handle_handoff_command(
            bot,
            command,
            principal=principal,
            roles=roles,
            approval_context=approval_context,
        )

    intelligence_result = await handle_intelligence_route(
        bot,
        command,
        first=first,
        principal=principal,
        roles=roles,
        approval_context=approval_context,
    )
    if intelligence_result is not None:
        return intelligence_result

    return {
        "success": False,
        "output": f"Unknown command: '{first}'. Type 'help' for available commands.",
        "type": "unknown",
    }
