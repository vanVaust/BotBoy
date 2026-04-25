from __future__ import annotations

import json

from botboy.agent_skills import format_skill_library_summary, format_skill_route


def handle_contracts(bot, command: str) -> dict:
    if not bot.skills:
        return {"success": False, "output": "Skills not available.", "type": "contracts"}

    query = command.split(None, 1)[1].strip() if len(command.split(None, 1)) > 1 else ""
    if query.lower().startswith("audit"):
        contracts = bot.skills.list_contracts()
        approval_required = sum(1 for contract in contracts if contract.get("needs_approval"))
        network_enabled = sum(1 for contract in contracts if contract.get("allow_network"))
        filesystem_enabled = sum(1 for contract in contracts if contract.get("allow_filesystem"))
        trusted_count = sum(1 for contract in contracts if contract.get("trusted"))
        risky = [
            contract["name"]
            for contract in contracts
            if contract.get("needs_approval") or contract.get("allow_network")
        ][:10]
        lines = [
            "Contract audit:",
            f"  Total contracts: {len(contracts)}",
            f"  Approval required: {approval_required}",
            f"  Trusted contracts: {trusted_count}",
            f"  Network-enabled: {network_enabled}",
            f"  Filesystem-enabled: {filesystem_enabled}",
            f"  Review focus: {', '.join(risky) or '-'}",
        ]
        return {
            "success": True,
            "output": "\n".join(lines),
            "type": "contracts",
            "data": {
                "contracts": contracts,
                "stats": {"count": len(contracts)},
                "approval_required": approval_required,
                "trusted_count": trusted_count,
                "network_enabled": network_enabled,
                "filesystem_enabled": filesystem_enabled,
                "review_focus": risky,
            },
        }
    if not query:
        contracts = bot.skills.list_contracts()
        approval_required = sum(1 for contract in contracts if contract.get("needs_approval"))
        by_level: dict[str, int] = {}
        for contract in contracts:
            level = str(contract.get("security_level", "unknown"))
            by_level[level] = by_level.get(level, 0) + 1
        lines = [
            f"Contracts: {len(contracts)}",
            f"Approval required: {approval_required}",
            "Security levels: " + ", ".join(f"{level}={count}" for level, count in sorted(by_level.items())),
        ]
        for contract in contracts[:12]:
            marker = "approval" if contract.get("needs_approval") else "auto"
            lines.append(
                f"  [{marker}] {contract['name']} | {contract['security_level']} | {contract['runtime_tier']}"
            )
        if len(contracts) > 12:
            lines.append(f"  ... {len(contracts) - 12} more")
        return {
            "success": True,
            "output": "\n".join(lines),
            "type": "contracts",
            "data": {
                "contracts": contracts,
                "stats": {"count": len(contracts)},
                "approval_required": approval_required,
                "by_level": by_level,
            },
        }

    contract = bot.skills.get_contract_for_skill(query) or bot.skills.get_contract_for_command(query)
    if not contract:
        return {
            "success": False,
            "output": f"No contract found for '{query}'.",
            "type": "contracts",
        }

    lines = [
        f"Contract: {contract['name']}",
        f"Version: {contract['version']}",
        f"Security: {contract['security_level']}",
        f"Runtime: {contract['runtime_tier']}",
        f"Trusted: {'yes' if contract.get('trusted') else 'no'}",
        f"Approval: {'required' if contract.get('needs_approval') else 'not required'}",
        f"Network: {'yes' if contract.get('allow_network') else 'no'}",
        f"Filesystem: {'yes' if contract.get('allow_filesystem') else 'no'}",
        "Triggers: " + (", ".join(contract.get("triggers", [])) or "-"),
        "Capabilities: " + (", ".join(contract.get("capabilities", [])) or "-"),
    ]
    return {
        "success": True,
        "output": "\n".join(lines),
        "type": "contracts",
        "data": {"contract": contract},
    }


def handle_agent_skill_library(bot, command: str) -> dict:
    if not bot.agent_skill_library:
        return {"success": False, "output": "Agent skill library not available.", "type": "skilllib"}
    parts = command.split(None, 1)
    if len(parts) == 1:
        contracts = bot.skills.list_contracts() if bot.skills else []
        return {
            "success": True,
            "output": format_skill_library_summary(bot.agent_skill_library),
            "type": "skilllib",
            "data": {"stats": bot.agent_skill_library.stats(), "contracts": contracts},
        }
    query = parts[1].strip()
    entry = bot.agent_skill_library.get(query)
    if not entry:
        return {
            "success": False,
            "output": f"Unknown agent skill: {query}",
            "type": "skilllib",
        }
    return {
        "success": True,
        "output": (
            f"{entry.name}\n"
            f"  Category: {entry.category}\n"
            f"  Phase: {entry.phase}\n"
            f"  Summary: {entry.summary}\n"
            f"  Path: {entry.resolved_path()}"
        ),
        "type": "skilllib",
        "data": {"skill": entry.to_dict()},
    }


def handle_agent_skill_route(bot, command: str) -> dict:
    if not bot.agent_skill_library:
        return {"success": False, "output": "Agent skill library not available.", "type": "skillroute"}
    query = command[len("skillroute"):].strip()
    if not query:
        return {"success": False, "output": "Usage: skillroute <query>", "type": "skillroute"}
    matches = bot.agent_skill_library.suggest(query, limit=5)
    return {
        "success": True,
        "output": format_skill_route(query, matches),
        "type": "skillroute",
        "data": {"matches": [entry.to_dict() for entry in matches]},
    }


def handle_handoff_suggest(bot, command: str) -> dict:
    advisor = getattr(bot, "delegation_advisor", None)
    if not advisor:
        return {"success": False, "output": "Delegation advisor not available.", "type": "handoff"}
    query = command[len("handoff suggest "):].strip()
    if not query:
        return {"success": False, "output": "Usage: handoff suggest <task description>", "type": "handoff"}
    advice = advisor.recommend_for_task(query)
    lines = [
        f"Delegation advice for: {query}",
        f"  Chosen worker: {advice.chosen_worker or '-'}",
        f"  Rationale: {advice.rationale or '-'}",
    ]
    for fit in advice.ranked_workers[:3]:
        lines.append(
            f"  Candidate [{fit.worker_id}] score={fit.score:.2f} "
            f"matched={', '.join(fit.matched_capabilities) or '-'}"
        )
    for step in advice.plan[:3]:
        lines.append(f"  Plan: {step.worker_id} -> {step.action}")
    return {
        "success": True,
        "output": "\n".join(lines),
        "type": "handoff",
        "data": {"advice": advice.to_dict()},
    }


def handle_a2a(bot, command: str, *, principal: str = "anonymous") -> dict:
    registry = getattr(bot, "a2a_pilot", None)
    if not registry:
        return {"success": False, "output": "A2A pilot registry not available.", "type": "a2a"}
    parts = command.split(None, 3)
    sub = parts[1].lower() if len(parts) > 1 else "list"

    if sub in {"list", "stats"}:
        stats = registry.stats()
        lines = [f"A2A adapters ({stats.get('total_adapters', 0)}):"]
        for adapter in stats.get("adapters", []):
            lines.append(
                f"  [{adapter['adapter_id']}] caps={', '.join(adapter.get('capabilities', [])) or '-'} "
                f"limit={adapter.get('max_payload_bytes', 0)}"
            )
        return {"success": True, "output": "\n".join(lines), "type": "a2a", "data": stats}

    if sub == "suggest" and len(parts) > 2:
        query = command.split(None, 2)[2].strip()
        ranked = registry.rank_adapters(query)
        suggested = registry.suggest_adapter(query)
        lines = [
            f"A2A suggestion for: {query}",
            f"  Suggested adapter: {suggested.adapter_id if suggested else '-'}",
        ]
        for fit in ranked[:3]:
            lines.append(
                f"  Candidate [{fit.adapter_id}] score={fit.score:.2f} "
                f"matched={', '.join(fit.matched_capabilities) or '-'}"
            )
        return {
            "success": True,
            "output": "\n".join(lines),
            "type": "a2a",
            "data": {
                "suggested_adapter": suggested.to_dict() if suggested else None,
                "ranked": [item.to_dict() for item in ranked],
            },
        }

    if sub in {"dispatch", "send"} and len(parts) > 3:
        adapter_id = parts[2].strip()
        try:
            payload = json.loads(parts[3].strip())
        except json.JSONDecodeError as exc:
            return {"success": False, "output": f"Invalid A2A JSON payload: {exc}", "type": "a2a"}
        if not isinstance(payload, dict):
            return {"success": False, "output": "A2A payload must be a JSON object.", "type": "a2a"}
        try:
            response = registry.dispatch(
                adapter_id,
                payload,
                sender="botboy",
                principal=principal,
                task_id=payload.get("task_id", "") if isinstance(payload, dict) else "",
            )
        except (KeyError, PermissionError, ValueError) as exc:
            return {"success": False, "output": str(exc), "type": "a2a"}
        return {
            "success": response.ok,
            "output": f"A2A dispatch via {adapter_id}: {response.status}",
            "type": "a2a",
            "data": {"response": response.to_dict()},
        }

    return {
        "success": False,
        "output": "Usage: a2a [list|stats|suggest <query>|dispatch <adapter_id> <json-payload>]",
        "type": "a2a",
    }
