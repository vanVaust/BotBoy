from __future__ import annotations

from datetime import datetime, timezone

from botboy.command_effects import MEMORY_DELETE_COMMANDS, MEMORY_WRITE_COMMANDS, SCHEDULE_REMOVE_SUBCOMMANDS


# Only these approval-context sources are a human operator at this machine:
# "local" is the CLI default context, "operator" a direct call without one.
# Everything else (gateways, scheduler, planner, agent, unknown) is untrusted
# by default; allowlisting keeps a new entry point from inheriting trust.
TRUSTED_ORIGINS = frozenset({"local", "operator"})
_TRUST_FLAGS = {"--trust=trusted": "trusted", "--trust=untrusted": "untrusted"}


def parse_write_provenance(text: str, *, origin: str, root_origin: str = "") -> tuple[str, str, str]:
    """Return ``(trust, source, content)`` for a memory write.

    Only the first token may be a trust flag. ``--trust=untrusted`` is always
    honoured; ``--trust=trusted`` only from the operator, or from the secure
    agent (which computes it from labels) when the agent run itself was
    started by the operator (``root_origin``). Model-written commands — the
    planner, an agent started over a gateway — can never assert trust.
    """
    head, _, rest = text.partition(" ")
    may_assert = origin in TRUSTED_ORIGINS or (origin == "agent" and root_origin in TRUSTED_ORIGINS)
    if head in _TRUST_FLAGS:
        text = rest.strip()
        trust = "trusted" if _TRUST_FLAGS[head] == "trusted" and may_assert else "untrusted"
    else:
        trust = "trusted" if origin in TRUSTED_ORIGINS else "untrusted"
    return trust, origin, text


def handle_memory_command(bot, command: str, *, first: str, origin: str = "operator",
                          root_origin: str = "") -> dict:
    if first in MEMORY_WRITE_COMMANDS:
        trust, source, text = parse_write_provenance(command[len(first):].strip(), origin=origin,
                                                     root_origin=root_origin)
        if not text:
            return {"success": False, "output": f"Usage: {first} <text>", "type": "memory"}
        if bot.memory:
            memory_id = bot.memory.store(text, trust=trust, source=source)
            if bot.metrics:
                bot.metrics.record_memory_op()
            return {
                "success": True,
                "output": f"Remembered (id={memory_id}): {text[:60]}",
                "type": "memory",
                "data": {"id": memory_id, "trust": trust, "source": source},
            }
        return {"success": False, "output": "Memory not available.", "type": "memory"}

    if first == "search":
        query = command[len(first):].strip()
        if not query:
            return {"success": False, "output": "Usage: search <query>", "type": "memory"}
        if bot.memory:
            results = bot.memory.search(query, limit=5)
            if results:
                lines = [f"Found {len(results)} result(s):"]
                for result in results:
                    lines.append(f"  [{result.id}] {result.content[:100]}")
                return {
                    "success": True,
                    "output": "\n".join(lines),
                    "type": "memory",
                    "data": {"results": [result.to_dict() for result in results]},
                }
            return {"success": True, "output": f"No results for '{query}'.", "type": "memory",
                    "data": {"results": []}}
        return {"success": False, "output": "Memory not available.", "type": "memory"}

    if first in MEMORY_DELETE_COMMANDS:
        query = command[len(first):].strip()
        if not query:
            return {"success": False, "output": f"Usage: {first} <query>", "type": "memory"}
        if bot.memory:
            results = bot.memory.search(query, limit=3)
            if results:
                for result in results:
                    bot.memory.delete(result.id)
                return {
                    "success": True,
                    "output": f"Deleted {len(results)} matching memory(s).",
                    "type": "memory",
                }
            return {"success": True, "output": "No matching memories found.", "type": "memory"}
        return {"success": False, "output": "Memory not available.", "type": "memory"}

    if first == "memories":
        if bot.memory:
            all_memories = bot.memory.list_all(limit=20)
            if all_memories:
                lines = [f"Stored memories ({len(all_memories)} shown):"]
                for memory in all_memories:
                    lines.append(f"  [{memory.id}] {memory.content[:80]}")
                return {"success": True, "output": "\n".join(lines), "type": "memory"}
            return {"success": True, "output": "No memories stored.", "type": "memory"}
        return {"success": False, "output": "Memory not available.", "type": "memory"}

    if first == "memstats":
        if bot.memory:
            stats = bot.memory.get_stats()
            output = (
                f"Memory DB stats:\n"
                f"  Total: {stats['total']}\n"
                f"  Oldest: {stats.get('oldest', 'N/A')}\n"
                f"  Newest: {stats.get('newest', 'N/A')}\n"
                f"  DB size: {stats.get('db_size_bytes', 0) // 1024} KB"
            )
            return {"success": True, "output": output, "type": "memory", "data": stats}
        return {"success": False, "output": "Memory not available.", "type": "memory"}

    return {"success": False, "output": f"Unknown memory command: {first}", "type": "memory"}


def handle_schedule(bot, command: str) -> dict:
    parts = command.split(None, 3)
    sub = parts[1].lower() if len(parts) > 1 else "list"

    if not bot.scheduler:
        return {"success": False, "output": "Scheduler not enabled.", "type": "schedule"}

    if sub == "list":
        tasks = bot.scheduler.list_tasks()
        if not tasks:
            return {"success": True, "output": "No scheduled tasks.", "type": "schedule"}
        lines = [f"Scheduled tasks ({len(tasks)}):"]
        for task in tasks:
            next_run = datetime.fromtimestamp(task.next_run_ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            lines.append(f"  [{task.task_id[:8]}] {task.name} | {task.schedule} | next: {next_run}")
        return {"success": True, "output": "\n".join(lines), "type": "schedule"}

    if sub == "add" and len(parts) >= 4:
        try:
            name = parts[2].strip("\"'")
            schedule = parts[3].strip("\"'")
            task_id = bot.scheduler.add(name=name, schedule=schedule)
            return {
                "success": True,
                "output": f"Scheduled '{name}' (id={task_id[:8]})",
                "type": "schedule",
            }
        except Exception as exc:
            return {"success": False, "output": f"Schedule add error: {exc}", "type": "schedule"}

    if sub in SCHEDULE_REMOVE_SUBCOMMANDS and len(parts) > 2:
        task_id = parts[2]
        bot.scheduler.cancel(task_id)
        return {"success": True, "output": f"Cancelled task {task_id[:8]}", "type": "schedule"}

    stats = bot.scheduler.stats()
    return {
        "success": True,
        "output": f"Scheduler: {stats['active']} active, {stats['total_runs']} total runs",
        "type": "schedule",
    }
