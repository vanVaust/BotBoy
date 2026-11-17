"""Tool catalogue for the secure agent.

Each tool declares, per parameter, whether it is a ``control`` parameter
(selects *what* happens or *where* data goes) or a ``data`` parameter (payload
that is only processed).  Untrusted values may fill data parameters only.

Tools compile to a BotBoy command whose first token is fixed by the template,
so argument text can never change which handler the router selects.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Mapping, Optional

ParamKind = Literal["control", "data"]
Effect = Literal["read", "write", "destructive", "egress"]
OutputTrust = Literal["trusted", "untrusted", "inherit"]

_MAX_ARG_CHARS = 2_000
_WHITESPACE_RUN = re.compile(r"\s+")


class ToolArgumentError(ValueError):
    pass


@dataclass(frozen=True)
class ParamSpec:
    kind: ParamKind
    description: str
    max_chars: int = _MAX_ARG_CHARS


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    params: Mapping[str, ParamSpec]
    effect: Effect
    output_trust: OutputTrust
    command_template: str = ""
    # Quarantined tools are run by the engine itself (dual-LLM pattern), not
    # by the command router.
    quarantined_llm: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def catalogue_entry(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "effect": self.effect,
            "params": {
                name: {"kind": spec.kind, "description": spec.description}
                for name, spec in self.params.items()
            },
        }

    def build_command(self, values: Mapping[str, str]) -> str:
        if self.quarantined_llm or not self.command_template:
            raise ToolArgumentError(f"{self.name} is not a command tool")
        cleaned = {name: sanitize_argument(values[name], spec.max_chars, name)
                   for name, spec in self.params.items()}
        return self.command_template.format(**cleaned)


def sanitize_argument(value: str, max_chars: int, name: str) -> str:
    """Flatten an argument into a single safe line.

    Line breaks and other separators are collapsed so a value cannot smuggle a
    second command into handlers that split on newlines; control and format
    characters (bidi overrides, zero-width joiners) are dropped because they
    can hide content from the human reviewing an approval.
    """
    text = "".join(
        ch for ch in unicodedata.normalize("NFKC", str(value))
        if unicodedata.category(ch) not in {"Cc", "Cf"} or ch in "\t\n\r"
    )
    text = _WHITESPACE_RUN.sub(" ", text).strip()
    if not text:
        raise ToolArgumentError(f"argument '{name}' is empty after sanitising")
    if len(text) > max_chars:
        raise ToolArgumentError(f"argument '{name}' exceeds {max_chars} characters")
    return text


def default_tools() -> dict[str, ToolSpec]:
    tools = (
        ToolSpec(
            name="memory.search",
            description="Full-text search over stored memories; returns matching entries.",
            params={"query": ParamSpec("data", "search words", 256)},
            effect="read",
            output_trust="untrusted",  # stored text may stem from earlier untrusted input
            command_template="search {query}",
        ),
        ToolSpec(
            name="memory.store",
            description="Persist a text as a new memory entry.",
            params={"text": ParamSpec("data", "text to remember")},
            effect="write",
            output_trust="trusted",
            command_template="remember {text}",
        ),
        ToolSpec(
            name="memory.delete",
            description="Delete up to three memories matching a query.",
            params={"query": ParamSpec("control", "selects what is deleted", 256)},
            effect="destructive",
            output_trust="trusted",
            command_template="forget {query}",
        ),
        ToolSpec(
            name="web.search",
            description="Instant-answer web search (query leaves this machine).",
            params={"query": ParamSpec("control", "query sent to the search provider", 256)},
            effect="egress",
            output_trust="untrusted",
            command_template="web search {query}",
        ),
        ToolSpec(
            name="file.search",
            description="Find files by glob pattern below the home directory (names only).",
            params={"pattern": ParamSpec("control", "glob pattern, relative to home", 256)},
            effect="read",
            output_trust="untrusted",  # file names are attacker-controllable
            command_template="file search {pattern}",
        ),
        ToolSpec(
            name="calc",
            description="Evaluate an arithmetic expression safely.",
            params={"expression": ParamSpec("data", "arithmetic expression", 256)},
            effect="read",
            output_trust="inherit",
            command_template="calc {expression}",
        ),
        ToolSpec(
            name="hash.sha256",
            description="SHA-256 hex digest of a text.",
            params={"text": ParamSpec("data", "text to hash")},
            effect="read",
            output_trust="inherit",
            command_template="hash sha256 {text}",
        ),
        ToolSpec(
            name="time.now",
            description="Current UTC and local time.",
            params={},
            effect="read",
            output_trust="trusted",
            command_template="time",
        ),
        ToolSpec(
            name="llm.extract",
            description=(
                "Quarantined LLM: apply an instruction to untrusted data and return text. "
                "Has no tools; its output is always untrusted."
            ),
            params={
                "instruction": ParamSpec("control", "what to extract or summarise", 500),
                "data": ParamSpec("data", "text to process", 8_000),
            },
            effect="read",
            output_trust="untrusted",
            quarantined_llm=True,
        ),
    )
    return {tool.name: tool for tool in tools}


def lookup(tools: Mapping[str, ToolSpec], name: str) -> Optional[ToolSpec]:
    return tools.get(name)
