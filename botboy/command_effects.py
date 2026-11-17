"""Single source of truth for builtin commands that destroy persisted state.

The router, the handlers and the execution-service approval gate all import
these names.  A command alias added to a handler without being added here
would otherwise reach a destructive code path without the approval gate
(``delete`` was such an alias for ``forget``).
"""

from __future__ import annotations

from typing import Optional

MEMORY_DELETE_COMMANDS = frozenset({"forget", "delete"})
HISTORY_DESTRUCTIVE_SUBCOMMANDS = frozenset({"clear"})
SCHEDULE_REMOVE_SUBCOMMANDS = frozenset({"cancel", "remove", "delete"})


def _tokens(command: str) -> list[str]:
    # Mirrors the router: ``command.lower().split()`` decides dispatch.
    return str(command or "").lower().split()


def destructive_effect(command: str) -> Optional[str]:
    """Return a stable effect label if ``command`` irreversibly removes data."""
    parts = _tokens(command)
    if not parts:
        return None
    first = parts[0]
    sub = parts[1] if len(parts) > 1 else ""
    if first in MEMORY_DELETE_COMMANDS:
        return "memory.delete"
    if first == "history" and sub in HISTORY_DESTRUCTIVE_SUBCOMMANDS:
        return "history.clear"
    if first == "schedule" and sub in SCHEDULE_REMOVE_SUBCOMMANDS:
        return "schedule.remove"
    return None


def is_destructive(command: str) -> bool:
    return destructive_effect(command) is not None
