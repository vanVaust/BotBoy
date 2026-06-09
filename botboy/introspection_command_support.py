from __future__ import annotations


def _worker_node_usage() -> str:
    return (
        "Usage: worker node "
        "[list|register <node_id> <worker_id> [endpoint]|heartbeat <node_id> [status]|drain <node_id> [reason]]"
    )


def _worker_node_list(bot) -> dict:
    store = getattr(bot, "task_store", None)
    if not store:
        return {"success": False, "output": "Task store not available.", "type": "worker"}
    nodes = store.list_worker_nodes()
    summary = store.worker_node_summary()
    queues = store.list_execution_queues()
    lines = [
        f"Worker nodes ({summary['node_count']} total, healthy={summary['healthy_count']}, draining={summary['draining_count']}):"
    ]
    if not nodes:
        lines.append("  - none")
    for node in nodes:
        lines.append(
            f"  [{node['node_id']}] worker={node['worker_id']} status={node['effective_status']} "
            f"queue={node['queue_name']} heartbeat={node['last_heartbeat_at'] or '-'}"
        )
    return {
        "success": True,
        "output": "\n".join(lines),
        "type": "worker",
        "data": {"nodes": nodes, "queues": queues, "summary": summary},
    }


def _worker_node_register(bot, command: str) -> dict:
    store = getattr(bot, "task_store", None)
    if not store:
        return {"success": False, "output": "Task store not available.", "type": "worker"}
    parts = command.split(None, 5)
    if len(parts) < 5:
        return {"success": False, "output": _worker_node_usage(), "type": "worker"}
    endpoint = parts[5].strip() if len(parts) > 5 else ""
    try:
        node = store.register_worker_node(
            node_id=parts[3].strip(),
            worker_id=parts[4].strip(),
            endpoint=endpoint,
            last_seen_ip="local-cli",
        )
    except ValueError as exc:
        return {"success": False, "output": str(exc), "type": "worker"}
    return {
        "success": True,
        "output": (
            f"Worker node registered: {node['node_id']}\n"
            f"  Worker: {node['worker_id']}\n"
            f"  Queue: {node['queue_name']}\n"
            f"  Status: {node['effective_status']}"
        ),
        "type": "worker",
        "data": {"node": node, "summary": store.worker_node_summary()},
    }


def _worker_node_heartbeat(bot, command: str) -> dict:
    store = getattr(bot, "task_store", None)
    if not store:
        return {"success": False, "output": "Task store not available.", "type": "worker"}
    parts = command.split(None, 4)
    if len(parts) < 4:
        return {"success": False, "output": _worker_node_usage(), "type": "worker"}
    node = store.heartbeat_worker_node(
        parts[3].strip(),
        node_status=parts[4].strip() if len(parts) > 4 else "ready",
        last_seen_ip="local-cli",
    )
    if not node:
        return {"success": False, "output": f"Unknown worker node '{parts[3].strip()}'.", "type": "worker"}
    return {
        "success": True,
        "output": (
            f"Worker node heartbeat: {node['node_id']}\n"
            f"  Status: {node['effective_status']}\n"
            f"  Heartbeat: {node['last_heartbeat_at']}"
        ),
        "type": "worker",
        "data": {"node": node, "summary": store.worker_node_summary()},
    }


def _worker_node_drain(bot, command: str) -> dict:
    store = getattr(bot, "task_store", None)
    if not store:
        return {"success": False, "output": "Task store not available.", "type": "worker"}
    parts = command.split(None, 4)
    if len(parts) < 4:
        return {"success": False, "output": _worker_node_usage(), "type": "worker"}
    node = store.drain_worker_node(parts[3].strip(), reason=parts[4].strip() if len(parts) > 4 else "")
    if not node:
        return {"success": False, "output": f"Unknown worker node '{parts[3].strip()}'.", "type": "worker"}
    return {
        "success": True,
        "output": (
            f"Worker node draining: {node['node_id']}\n"
            f"  Worker: {node['worker_id']}\n"
            f"  Queue: {node['queue_name']}\n"
            f"  Status: {node['effective_status']}"
        ),
        "type": "worker",
        "data": {"node": node, "summary": store.worker_node_summary()},
    }


def handle_workers(bot, command: str) -> dict:
    parts = command.split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else "list"
    if sub == "node":
        action = parts[2].split(None, 1)[0].lower() if len(parts) > 2 and parts[2].strip() else "list"
        if action == "list":
            return _worker_node_list(bot)
        if action == "register":
            return _worker_node_register(bot, command)
        if action == "heartbeat":
            return _worker_node_heartbeat(bot, command)
        if action == "drain":
            return _worker_node_drain(bot, command)
        return {"success": False, "output": _worker_node_usage(), "type": "worker"}
    payload = bot.get_worker_payload()
    workers = payload["workers"]
    if sub == "list":
        lines = [f"Workers ({len(workers)}):"]
        for worker in workers:
            lines.append(
                f"  [{worker['worker_id']}] {worker['role']:14} active={worker['active_tasks']} "
                f"delegated={worker['delegated_tasks']} caps={len(worker['capabilities'])}"
            )
        return {"success": True, "output": "\n".join(lines), "type": "worker", "data": payload}
    if len(parts) < 3:
        return {"success": False, "output": "Usage: worker [list|show] <worker_id>", "type": "worker"}
    worker = bot.workers.get(parts[2].strip().lower())
    if not worker:
        return {"success": False, "output": f"Unknown worker '{parts[2].strip()}'.", "type": "worker"}
    item = worker.to_dict()
    summary_map = {entry["worker_id"]: entry for entry in workers}
    item.update(summary_map.get(worker.worker_id, {}))
    lines = [
        f"Worker: {item['worker_id']}",
        f"  Display: {item['display_name']}",
        f"  Role: {item['role']}",
        f"  Approval profile: {item['approval_profile']}",
        f"  Max concurrency: {item['max_concurrency']}",
        f"  Active tasks: {item.get('active_tasks', 0)}",
        f"  Delegated tasks: {item.get('delegated_tasks', 0)}",
        f"  Capabilities: {', '.join(item['capabilities'])}",
    ]
    return {"success": True, "output": "\n".join(lines), "type": "worker", "data": {"worker": item}}


def handle_reflection_archive(bot, command: str, *, principal: str = "anonymous") -> dict:
    archive = getattr(bot, "reflection_archive", None)
    if not archive:
        return {"success": False, "output": "Reflection archive not available.", "type": "reflection"}
    parts = command.split(None, 3)
    sub = parts[1].lower() if len(parts) > 1 else "stats"

    if sub == "stats":
        stats = archive.stats()
        lines = [
            "Reflection archive:",
            f"  Total entries: {stats.get('total_entries', 0)}",
            f"  By outcome: {', '.join(f'{key}={value}' for key, value in stats.get('by_outcome', {}).items()) or '-'}",
            f"  Top tags: {', '.join(f'{key}={value}' for key, value in stats.get('top_tags', {}).items()) or '-'}",
            f"  Latest entry: {(stats.get('latest_entry') or {}).get('entry_id', '-')}",
        ]
        return {"success": True, "output": "\n".join(lines), "type": "reflection", "data": stats}

    if sub == "list":
        task_id = parts[2].strip() if len(parts) > 2 else ""
        entries = archive.list_entries(task_id=task_id, limit=10)
        lines = [f"Reflection entries ({len(entries)} shown):"]
        for entry in entries:
            lines.append(
                f"  [{entry.entry_id[:8]}] task={entry.task_id or '-'} outcome={entry.outcome} "
                f"author={entry.author or '-'} summary={entry.summary or '-'}"
            )
        return {
            "success": True,
            "output": "\n".join(lines),
            "type": "reflection",
            "data": {"entries": [entry.to_dict() for entry in entries], "task_id": task_id},
        }

    if sub == "latest" and len(parts) > 2:
        task_id = parts[2].strip()
        entry = archive.latest_for_task(task_id)
        if not entry:
            return {"success": False, "output": f"No reflection entry for task {task_id}.", "type": "reflection"}
        return {
            "success": True,
            "output": (
                f"Latest reflection for {task_id}: {entry.entry_id}\n"
                f"  Outcome: {entry.outcome}\n"
                f"  Author: {entry.author or '-'}\n"
                f"  Reflection: {entry.reflection or '-'}"
            ),
            "type": "reflection",
            "data": {"entry": entry.to_dict()},
        }

    if sub in {"record", "archive"} and len(parts) > 3:
        task_id = parts[2].strip()
        reflection_text = parts[3].strip()
        if not bot.task_store:
            return {"success": False, "output": "Task store not available.", "type": "reflection"}
        record = bot.task_store.get_task(task_id)
        if not record:
            return {"success": False, "output": f"Task '{task_id}' not found.", "type": "reflection"}
        trace = bot.trace_store.get_run(record.run_id) if bot.trace_store and record.run_id else None
        entry = archive.archive_context(
            task=record,
            trace=trace,
            outcome=record.status,
            summary=record.summary or record.title,
            reflection=reflection_text,
            tags=("task_reflection", record.status, record.owner),
            metadata={"command": record.command, "principal": record.principal},
            author=principal,
            source="cli_reflection",
        )
        return {
            "success": True,
            "output": f"Reflection archived for task {task_id} (entry={entry.entry_id}).",
            "type": "reflection",
            "data": {"entry": entry.to_dict()},
        }

    return {
        "success": False,
        "output": "Usage: reflect [stats|list [task_id]|latest <task_id>|record <task_id> <reflection>]",
        "type": "reflection",
    }


def handle_archetypes(bot, command: str) -> dict:
    parts = command.split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else "stats"

    if not bot.archetypes:
        return {"success": False, "output": "Archetype engine not initialised.", "type": "archetypes"}

    if sub == "stats":
        stats = bot.archetypes.stats()
        lines = [f"Archetype routing stats ({stats['total_commands_routed']} total):"]
        for item in stats["archetypes"]:
            lines.append(
                f"  {item['archetype']:12} uses={item['use_count']:4}  success={item['success_rate']:.0%}"
            )
        return {"success": True, "output": "\n".join(lines), "type": "archetypes"}

    if sub == "match" and len(parts) > 2:
        query = parts[2]
        match = bot.archetypes.match_intent(query)
        profile = bot.archetypes.profile_for(match.archetype)
        channels = profile.primary_channels if profile else []
        lines = [
            f"Intent match for: '{query}'",
            f"  Archetype:   {match.archetype.value}",
            f"  Confidence:  {match.confidence:.0%}",
            f"  Matched via: {match.matched_via}",
            f"  Channels:    {', '.join(channels)}",
        ]
        return {"success": True, "output": "\n".join(lines), "type": "archetypes"}

    if sub == "channels":
        from botboy.channels import CHANNEL_SEMANTICS, SenseChannel

        lines = ["Active channels:"]
        if bot.router:
            stats = bot.router.stats()
            for channel in SenseChannel:
                count = stats["message_counts"].get(channel.value, 0)
                semantics = CHANNEL_SEMANTICS[channel]
                lines.append(
                    f"  {channel.value:10}  msgs={count:4}  pattern={semantics['pattern']}"
                )
        return {"success": True, "output": "\n".join(lines), "type": "archetypes"}

    return {
        "success": False,
        "output": "Usage: archetypes [stats|match <query>|channels]",
        "type": "archetypes",
    }
