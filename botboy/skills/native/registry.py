"""Discovery of native skills from their packaged SKILL.md manifests.

Native skills are declared exactly like any other BotBoy skill — a SKILL.md
with LDF frontmatter — but their manifests live inside the package and their
``layer.runtime.entry`` names a coroutine in ``botboy.skills.native``. Only
manifests shipped in this package directory are ever resolved to Python
callables; SKILL.md files discovered in user skill directories keep going
through the sandboxed custom-skill runtime, whatever ``entry`` they declare.
"""
from __future__ import annotations

import importlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Tuple

import yaml

MANIFEST_ROOT = Path(__file__).resolve().parent / "manifests"
ENTRY_PREFIX = "botboy.skills.native."
REMOTE_POLICIES = frozenset({"allow", "approval"})

# First words that ``command_router.route_command`` and the intelligence
# router consume before any skill lookup. A skill trigger equal to one of
# these would be unreachable, so native manifests must avoid them and the
# skill forge reports them for custom skills.
ROUTER_RESERVED_COMMANDS = frozenset({
    "help", "?", "status", "version", "skills", "skilllib", "skilllibrary", "skillroute",
    "performance", "contracts", "contract", "eval", "evals", "worker", "workers", "reflect",
    "reflection", "a2a", "remember", "store", "save", "search", "forget", "delete", "memories",
    "memstats", "schedule", "history", "task", "tasks", "handoff", "plan", "think", "agent",
})

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
NativeHandler = Callable[[str, Any], Awaitable[dict]]


class NativeManifestError(RuntimeError):
    """A packaged manifest is malformed; this is a packaging bug, not user error."""


@dataclass(frozen=True)
class NativeManifest:
    path: Path
    meta: Dict[str, Any]
    handler: NativeHandler

    @property
    def name(self) -> str:
        return str(self.meta["name"])

    @property
    def triggers(self) -> Tuple[str, ...]:
        return tuple(str(trigger).lower() for trigger in self.meta.get("triggers", []))

    @property
    def remote_policy(self) -> str:
        return str(self.meta.get("remote_policy", "approval")).lower()


def split_frontmatter(content: str) -> Tuple[str, str]:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise NativeManifestError("SKILL.md must start with a '---' delimited YAML frontmatter block")
    return match.group(1), content[match.end():]


def resolve_entry(entry: str) -> NativeHandler:
    module_name, sep, attribute = str(entry or "").partition(":")
    if not sep or not module_name.startswith(ENTRY_PREFIX) or not attribute.isidentifier():
        raise NativeManifestError(f"Native entry must be '{ENTRY_PREFIX}<module>:<coroutine>', got {entry!r}")
    module = importlib.import_module(module_name)
    handler = getattr(module, attribute, None)
    if handler is None or not inspect.iscoroutinefunction(handler):
        raise NativeManifestError(f"Native entry {entry!r} is not a coroutine function")
    return handler


def _validate(meta: Mapping[str, Any], path: Path, reserved: frozenset) -> None:
    for key in ("name", "description", "triggers", "security_level"):
        if not meta.get(key):
            raise NativeManifestError(f"{path}: missing required key {key!r}")
    runtime = (meta.get("layer") or {}).get("runtime") or {}
    if str(runtime.get("type", "")).lower() != "builtin":
        raise NativeManifestError(f"{path}: native skills must declare layer.runtime.type: builtin")
    policy = str(meta.get("remote_policy", "")).lower()
    if policy not in REMOTE_POLICIES:
        raise NativeManifestError(f"{path}: remote_policy must be one of {sorted(REMOTE_POLICIES)}")
    if "needs_approval" not in meta:
        raise NativeManifestError(f"{path}: native manifests must state needs_approval explicitly")
    for trigger in meta.get("triggers", []):
        lowered = str(trigger).lower()
        if lowered in reserved:
            raise NativeManifestError(f"{path}: trigger {trigger!r} collides with a reserved command or builtin trigger")


def load_native_manifests(reserved_triggers: frozenset = frozenset()) -> List[NativeManifest]:
    """Load, validate and resolve every packaged native skill manifest."""
    reserved = ROUTER_RESERVED_COMMANDS | frozenset(t.lower() for t in reserved_triggers)
    manifests: List[NativeManifest] = []
    seen_triggers: Dict[str, str] = {}
    for path in sorted(MANIFEST_ROOT.glob("*/SKILL.md")):
        frontmatter, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        meta = yaml.safe_load(frontmatter) or {}
        if not isinstance(meta, dict):
            raise NativeManifestError(f"{path}: frontmatter must be a mapping")
        _validate(meta, path, reserved)
        handler = resolve_entry(str(meta["layer"]["runtime"].get("entry", "")))
        manifest = NativeManifest(path=path, meta=meta, handler=handler)
        for trigger in manifest.triggers:
            if trigger in seen_triggers:
                raise NativeManifestError(f"Trigger {trigger!r} declared by both {seen_triggers[trigger]} and {manifest.name}")
            seen_triggers[trigger] = manifest.name
        manifests.append(manifest)
    return manifests
