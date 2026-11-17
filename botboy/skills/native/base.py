"""Shared plumbing for BotBoy's native (packaged, trusted) executable skills."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


class SkillUsageError(ValueError):
    """Raised for malformed user input; rendered as a non-crashing failure."""


@dataclass
class NativeSkillContext:
    """Operator-controlled settings handed to every native skill invocation.

    The workspace root is the single directory filesystem-reading skills may
    touch. ``None`` means "no workspace configured" and makes those skills
    fail closed instead of guessing a directory.
    """

    workspace_root: Optional[Path] = None
    fetch_allow_private_networks: bool = False
    skill_manager: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


def result(skill_type: str, output: str, *, success: bool = True, **data: Any) -> dict:
    return {"success": success, "output": output, "type": skill_type, "data": data}


def failure(skill_type: str, output: str, **data: Any) -> dict:
    return result(skill_type, output, success=False, **data)


def split_command(command: str) -> Tuple[str, List[str]]:
    """Return (trigger, shell-like tokens) for ``command``.

    shlex gives users quoting for values with spaces without ever reaching a
    shell; an unbalanced quote is a usage error rather than an exception.
    """
    stripped = command.strip()
    if not stripped:
        return "", []
    trigger, _, rest = stripped.partition(" ")
    try:
        tokens = shlex.split(rest, posix=True) if rest.strip() else []
    except ValueError as exc:
        raise SkillUsageError(f"Could not parse arguments: {exc}") from exc
    return trigger.lower(), tokens


def raw_argument_text(command: str, words_to_skip: int) -> str:
    """Return the unparsed remainder after the first ``words_to_skip`` words.

    Free text (for example a paragraph to summarise) must not be shlex-split:
    apostrophes in prose would otherwise be treated as quotes.
    """
    remainder = command.strip()
    for _ in range(words_to_skip):
        parts = remainder.split(None, 1)
        remainder = parts[1] if len(parts) > 1 else ""
    return remainder


@dataclass
class ParsedOptions:
    positionals: List[str]
    options: Dict[str, Optional[str]]

    def get_int(self, name: str, default: int, *, minimum: int, maximum: int) -> int:
        raw = self.options.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise SkillUsageError(f"--{name} expects an integer, got {raw!r}") from exc
        if not minimum <= value <= maximum:
            raise SkillUsageError(f"--{name} must be between {minimum} and {maximum}")
        return value

    def flag(self, name: str) -> bool:
        return name in self.options

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        value = self.options.get(name)
        return default if value is None else value


def parse_options(
    tokens: Sequence[str],
    *,
    valued: Iterable[str] = (),
    flags: Iterable[str] = (),
) -> ParsedOptions:
    """Parse ``--name value`` / ``--flag`` options; reject unknown options."""
    valued_set = set(valued)
    flag_set = set(flags)
    positionals: List[str] = []
    options: Dict[str, Optional[str]] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--") and len(token) > 2:
            name, sep, inline_value = token[2:].partition("=")
            if name in flag_set:
                if sep:
                    raise SkillUsageError(f"--{name} does not take a value")
                options[name] = ""
            elif name in valued_set:
                if sep:
                    options[name] = inline_value
                else:
                    index += 1
                    if index >= len(tokens):
                        raise SkillUsageError(f"--{name} requires a value")
                    options[name] = tokens[index]
            else:
                raise SkillUsageError(f"Unknown option --{name}")
        else:
            positionals.append(token)
        index += 1
    return ParsedOptions(positionals=positionals, options=options)


def truncate(text: str, limit: int) -> Tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]", True


def format_table(rows: Sequence[Dict[str, Any]], columns: Sequence[str], *, max_width: int = 40) -> str:
    """Render rows as a fixed-width text table (no external dependency)."""
    def cell(value: Any) -> str:
        if value is None:
            text = ""
        elif isinstance(value, float):
            text = f"{value:.6g}"
        else:
            text = str(value)
        text = text.replace("\n", " ")
        return text if len(text) <= max_width else text[: max_width - 1] + "…"

    rendered = [[cell(row.get(col)) for col in columns] for row in rows]
    widths = [len(col) for col in columns]
    for line in rendered:
        for idx, value in enumerate(line):
            widths[idx] = max(widths[idx], len(value))
    header = " | ".join(col.ljust(widths[idx]) for idx, col in enumerate(columns))
    rule = "-+-".join("-" * width for width in widths)
    body = [" | ".join(value.ljust(widths[idx]) for idx, value in enumerate(line)) for line in rendered]
    return "\n".join([header, rule, *body])
