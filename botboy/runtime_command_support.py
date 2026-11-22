from __future__ import annotations

from datetime import datetime, timezone

from botboy.command_effects import MEMORY_DELETE_COMMANDS, MEMORY_WRITE_COMMANDS, SCHEDULE_REMOVE_SUBCOMMANDS


# Commands from these approval-context sources are produced by an LLM, not
# typed by the operator; their writes are untrusted unless the agent engine,
# which tracks labels, explicitly declares the text trusted.
AUTOMATED_SOURCES = frozenset({"agent", "planner"})
_TRUST_FLAGS = {"--trust=trusted": "trusted", "--trust=untrusted": "untrusted"}


def parse_write_provenance(text: str, *, origin: str) -> tuple[str, str, str]:
    """Return ``(trust, source, content)`` for a memory write.

    Only the first token may be a trust flag; the agent places it in the
    fixed command prefix, so flag-looking text inside the payload is content.
    """
    head, _, rest = text.partition(" ")
    if head in _TRUST_FLAGS:
        trust, text = _TRUST_FLAGS[head], rest.strip()
    else:
        trust = "untrusted" if origin in AUTOMATED_SOURCES else "trusted"
    return trust, origin, text


def handle_memory_command(bot, command: str, *, first: str, origin: str = "operator") -> dict:
    if first in MEMORY_WRITE_COMMANDS:
        trust, source, text = parse_write_provenance(command[len(first):].strip(), origin=origin)
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
