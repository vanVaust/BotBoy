from __future__ import annotations


def _render_records(records, total: int, label: str) -> dict:
    if not records:
        return {"success": True, "output": f"No history for {label}.", "type": "history"}
    lines = [f"Last {len(records)} of {total} commands for {label}:"]
    for record in records:
        status = "\u2713" if record.success else "\u2717"
        suffix = f" | principal={record.principal}"
        if record.request_id:
            suffix += f" | request_id={record.request_id}"
        lines.append(
            f"  {status} [{record.cmd_type}] {record.command[:60]}  ({record.latency_ms:.0f}ms){suffix}"
        )
    return {"success": True, "output": "\n".join(lines), "type": "history"}


def handle_history(bot, command: str) -> dict:
    parts = command.split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else "list"

    if not bot.history:
        return {"success": False, "output": "History not enabled.", "type": "history"}

    if sub == "stats":
        stats = bot.history.stats()
        lines = [
            "History stats:",
            f"  Total:   {stats['total']}",
            f"  Success: {stats['success_count']} ({stats['success_rate']:.0%})",
            f"  Avg latency: {stats['avg_latency_ms']:.1f}ms",
            "  By type: " + ", ".join(f"{key}={value}" for key, value in stats["by_type"].items()),
        ]
        if stats["by_principal"]:
            lines.append(
                "  By principal: "
                + ", ".join(f"{key}={value}" for key, value in stats["by_principal"].items())
            )
        return {"success": True, "output": "\n".join(lines), "type": "history"}

    if sub == "clear":
        purged = bot.history.purge(older_than_days=0)
        return {
            "success": True,
            "output": f"Cleared {purged} history records.",
            "type": "history",
        }

    if sub == "principal" and len(parts) > 2:
        principal = parts[2].strip()
        records, total = bot.history.list(limit=10, principal=principal)
        return _render_records(records, total, f"principal '{principal}'")

    if sub in ("request", "trace") and len(parts) > 2:
        trace_id = parts[2].strip()
        records, total = bot.history.list(limit=10, request_id=trace_id)
        return _render_records(records, total, f"request '{trace_id}'")

    records, total = bot.history.list(limit=10)
    if not records:
        return {"success": True, "output": "No history yet.", "type": "history"}
    lines = [f"Last {len(records)} of {total} commands:"]
    for record in records:
        status = "\u2713" if record.success else "\u2717"
        lines.append(
            f"  {status} [{record.cmd_type}] {record.command[:60]}  ({record.latency_ms:.0f}ms)"
        )
    return {"success": True, "output": "\n".join(lines), "type": "history"}
