"""Trust labels and the data-flow policy evaluated before every tool call."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Iterable, Mapping

if TYPE_CHECKING:
    from botboy.agent.tools import ToolSpec


class Trust(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True)
class Labeled:
    """A runtime value with provenance.

    ``sources`` names the origins that influenced the value: ``"user"`` for
    literals the planner took from the trusted task, otherwise step ids.
    """

    value: str
    trust: Trust
    sources: frozenset[str] = field(default_factory=frozenset)

    @property
    def untrusted(self) -> bool:
        return self.trust is Trust.UNTRUSTED

    @staticmethod
    def join(values: Iterable["Labeled"]) -> tuple[Trust, frozenset[str]]:
        trust = Trust.TRUSTED
        sources: set[str] = set()
        for item in values:
            sources |= item.sources
            if item.untrusted:
                trust = Trust.UNTRUSTED
        return trust, frozenset(sources)


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str
    digest: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


def call_digest(tool: str, args: Mapping[str, Labeled]) -> str:
    """Approval binding: hash of tool name and the exact argument values.

    Labels are part of the digest so an approval for a trusted value cannot be
    replayed for the same text arriving from an untrusted source.
    """
    canonical = json.dumps(
        {
            "tool": tool,
            "args": {name: [arg.value, arg.trust.value] for name, arg in sorted(args.items())},
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate(tool: "ToolSpec", args: Mapping[str, Labeled], approved: frozenset[str]) -> Decision:
    """Deny wins over approval: an approval cannot legalise a forbidden flow."""
    digest = call_digest(tool.name, args)
    for name, param in tool.params.items():
        arg = args[name]
        if param.kind == "control" and arg.untrusted:
            origin = ", ".join(sorted(arg.sources - {"user"})) or "unknown"
            return Decision(
                Verdict.DENY,
                f"untrusted data (from {origin}) may not set control parameter "
                f"'{name}' of {tool.name}",
                digest,
            )
    if tool.effect == "destructive":
        reason = f"{tool.name} irreversibly removes data"
    elif tool.effect == "write" and any(arg.untrusted for arg in args.values()):
        reason = f"{tool.name} would persist untrusted data (memory poisoning risk)"
    else:
        return Decision(Verdict.ALLOW, "", digest)
    if digest in approved:
        return Decision(Verdict.ALLOW, f"approved: {reason}", digest)
    return Decision(Verdict.NEEDS_APPROVAL, reason, digest)
