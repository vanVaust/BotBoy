from __future__ import annotations

import json

from botboy.tracing import get_trace_context

OPTIONAL_ROUTE_ERRORS = (
    RuntimeError,
    ValueError,
    OSError,
    LookupError,
    ImportError,
    json.JSONDecodeError,
)


async def handle_intelligence_route(
    bot,
    command: str,
    *,
    first: str,
    principal: str = "anonymous",
    roles: list[str] | None = None,
    approval_context: dict | None = None,
) -> dict | None:
    roles = roles or []

    if first == "agent":
        return await _handle_agent(bot, command, principal=principal, roles=roles)

    if first == "plan" and bot.planner:
        task = command[4:].strip()
        if not task:
            return {"success": False, "output": "Usage: plan <task description>", "type": "plan"}
        try:
            trace_ctx = get_trace_context()
            runner = bot._trace_async_label(
                trace_ctx,
                trace_ctx.root_span_id if trace_ctx else "",
                "planner",
                "plan",
            )
            plan = await runner(bot.planner.plan(task))
            lines = [
                f"Plan: {plan.strategy}",
                f"Executable: {plan.executable}",
                f"Confidence: {plan.confidence:.0%}",
                f"Steps: {len(plan.steps)}",
            ]
            for step in plan.steps:
                lines.append(f"  {step.step_id}: [{step.skill}] {step.command}")
            if plan.auditor_notes:
                lines.append(f"Auditor: {plan.auditor_notes}")
            return {
                "success": True,
                "output": "\n".join(lines),
                "type": "plan",
                "data": {"plan": plan.__dict__},
            }
        except OPTIONAL_ROUTE_ERRORS as exc:
            return {"success": False, "output": f"Planning error: {exc}", "type": "plan"}

    if first == "think" and bot.parallel_thinker:
        question = command[5:].strip()
        if not question:
            return {"success": False, "output": "Usage: think <question>", "type": "think"}
        try:
            trace_ctx = get_trace_context()
            runner = bot._trace_async_label(
                trace_ctx,
                trace_ctx.root_span_id if trace_ctx else "",
                "parallel_think",
                "think",
            )
            result = await runner(bot.parallel_thinker.think(question))
            return {
                "success": True,
                "output": f"Answer: {result.final_answer}\n\nTrace:\n{result.reasoning_trace}",
                "type": "think",
            }
        except OPTIONAL_ROUTE_ERRORS as exc:
            return {"success": False, "output": f"Think error: {exc}", "type": "think"}

    if bot.skills:
        skill_method = bot.skills.find_skill_for_command(command)
        if skill_method:
            contract = bot.skills.get_contract_for_skill(skill_method) or {}
            from botboy.skills.runtime import resolve_runtime_policy

            policy = resolve_runtime_policy(
                security_level=str(contract.get("security_level", "INTERMEDIATE")),
                runtime_tier=str(contract.get("runtime_tier", "auto")),
                trusted=bool(contract.get("trusted", False)),
                allow_network=bool(contract.get("allow_network", False)),
                allow_filesystem=bool(contract.get("allow_filesystem", False)),
                needs_approval=bool(contract.get("needs_approval", False)),
                approval_granted=bool((approval_context or {}).get("granted")),
                roles=roles,
            )
            if not policy.allowed:
                return {
                    "success": False,
                    "output": policy.reason,
                    "type": "skill",
                    "data": {
                        "skill": skill_method,
                        "contract": contract,
                        "policy": policy.to_dict(),
                        "principal": principal,
                    },
                }

            skill_span = None
            timer_started = False
            if bot.monitor:
                bot.monitor.start_timer(f"skill.{first}")
                timer_started = True
            trace_ctx = get_trace_context()
            if bot.trace_store and trace_ctx:
                skill_span = bot.trace_store.start_span(
                    trace_ctx,
                    component="skill",
                    event_type=skill_method,
                    parent_span_id=trace_ctx.root_span_id or "",
                    payload_ref=command[:256],
                )
            try:
                result = await bot.skills.execute_builtin(skill_method, command)
                if result is not None:
                    if skill_span and bot.trace_store:
                        bot.trace_store.finish_span(skill_span, status="success")
                    return result

                result = await bot.skills.execute_custom(
                    skill_method,
                    command,
                    principal=principal,
                    roles=roles,
                    approval_context=approval_context,
                )
                if result is not None:
                    if skill_span and bot.trace_store:
                        bot.trace_store.finish_span(
                            skill_span,
                            status="success" if result.get("success", False) else "error",
                            payload_ref=result.get("output", "")[:256],
                        )
                    return result

                if skill_span and bot.trace_store:
                    bot.trace_store.finish_span(skill_span, status="skipped")
                return None
            except OPTIONAL_ROUTE_ERRORS as exc:
                if skill_span and bot.trace_store:
                    bot.trace_store.finish_span(skill_span, status="error", payload_ref=str(exc)[:256])
                return {"success": False, "output": f"Skill error: {exc}", "type": "skill"}
            finally:
                if timer_started and bot.monitor:
                    bot.monitor.stop_timer(f"skill.{first}")

    if first in ("archetypes", "intent"):
        return await bot._handle_archetypes(command)

    if first in ("hybrid-search", "hsearch") and bot._hybrid_memory:
        query = command.split(None, 1)[1].strip() if len(command.split(None, 1)) > 1 else ""
        if not query:
            return {"success": False, "output": "Usage: hsearch <query>", "type": "memory"}
        try:
            results = bot.memory.hybrid_search(query, limit=5)
            if not results:
                return {"success": True, "output": f"No results for '{query}'", "type": "memory"}
            lines = [f"Hybrid search: {len(results)} result(s):"]
            for result in results:
                lines.append(f"  [{result.source:8}] {result.content[:80]}  (score={result.score:.4f})")
            return {"success": True, "output": "\n".join(lines), "type": "memory"}
        except OPTIONAL_ROUTE_ERRORS as exc:
            return {"success": False, "output": f"Hybrid search error: {exc}", "type": "memory"}

    if bot.llm:
        try:
            trace_ctx = get_trace_context()
            runner = bot._trace_async_label(
                trace_ctx,
                trace_ctx.root_span_id if trace_ctx else "",
                "llm",
                "chat",
            )
            llm_result = await runner(bot.llm.chat(command))
            if llm_result.success:
                output = llm_result.content
                if bot.parallel_thinker and len(command.split()) >= 8:
                    think_runner = bot._trace_async_label(
                        trace_ctx,
                        trace_ctx.root_span_id if trace_ctx else "",
                        "parallel_think",
                        "llm_followup",
                    )
                    think_result = await think_runner(bot.parallel_thinker.think(command))
                    if not think_result.skipped:
                        output = think_result.final_answer
                return {
                    "success": True,
                    "output": output,
                    "type": "llm",
                    "data": {"backend": llm_result.backend, "model": llm_result.model},
                }
            return {"success": False, "output": f"LLM error: {llm_result.error}", "type": "llm"}
        except OPTIONAL_ROUTE_ERRORS as exc:
            return {"success": False, "output": f"LLM error: {exc}", "type": "llm"}

    return None


# Agent steps run through process_command with this context: never the implicit
# local-operator grant, so approval-gated commands stay gated (defence in depth
# beneath the agent's own data-flow policy).
AGENT_APPROVAL_CONTEXT = {"granted": False, "explicit": False, "reason": "agent_step", "source": "agent"}

_AGENT_USAGE = "Usage: agent [--approve <sha256>]... <task>"


def _parse_agent_command(command: str) -> tuple[list[str], str]:
    tokens = command.split()[1:]
    approved: list[str] = []
    while len(tokens) >= 2 and tokens[0] == "--approve":
        digest = tokens[1].lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("--approve expects a 64-character SHA-256 hex digest")
        approved.append(digest)
        tokens = tokens[2:]
    return approved, " ".join(tokens)


def _format_agent_run(run) -> str:
    lines = [f"Agent: {run.status}"]
    for step in run.steps:
        args = ", ".join(
            f"{name}={value.value[:60]!r}{'' if value.trust.value == 'trusted' else ' [untrusted]'}"
            for name, value in step.args.items()
        )
        lines.append(f"  {step.step_id} {step.tool}({args}) -> {step.status}"
                     + (f": {step.reason}" if step.reason and step.status != "ok" else ""))
    if run.status == "approval_required" and run.pending is not None:
        lines.append(f"  Approve exactly this call with: agent --approve {run.pending.digest} <same task>")
    if run.status == "completed":
        marker = ""
        if run.answer_trust.value == "untrusted":
            origins = ", ".join(run.untrusted_origins)
            marker = f"\n[contains untrusted content from: {origins}]"
        lines.append(f"Answer: {run.answer}{marker}")
    elif run.error:
        lines.append(f"Reason: {run.error}")
    return "\n".join(lines)


async def _handle_agent(bot, command: str, *, principal: str, roles: list[str]) -> dict:
    from botboy.agent import SecureAgent

    try:
        approved, task = _parse_agent_command(command)
    except ValueError as exc:
        return {"success": False, "output": f"{exc}. {_AGENT_USAGE}", "type": "agent"}
    if not task:
        return {"success": False, "output": _AGENT_USAGE, "type": "agent"}
    backend = getattr(getattr(bot, "llm", None), "_backend", None)
    if backend is None:
        return {"success": False, "output": "Agent requires an enabled LLM backend.", "type": "agent"}

    async def execute(step_command: str) -> dict:
        return await bot.process_command(
            step_command,
            principal=principal,
            roles=list(roles),
            approval_context=dict(AGENT_APPROVAL_CONTEXT),
        )

    run = await SecureAgent(backend, execute).run(task, approved=approved)
    return {
        "success": run.status == "completed",
        "output": _format_agent_run(run),
        "type": "agent",
        "data": run.to_dict(),
    }
