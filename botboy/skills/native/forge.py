"""``forge`` — lint, audit and scaffold SKILL.md / LDF skills.

The linter answers one practical question: *will BotBoy actually load, route
and run this skill, and under which contract?* It therefore reuses BotBoy's
real parsers (``parse_skill_md`` semantics, ``_build_skill_contract``, the
LDF parser) and the runtime's static code blocklist instead of a separate
schema, and it reports the effective contract an operator would get.
Nothing is imported or executed: YAML goes through ``yaml.safe_load`` and
code blocks only through ``ast.parse``.
"""
from __future__ import annotations

import ast
import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

from botboy.skills.native import workspace
from botboy.skills.native.base import (
    NativeSkillContext,
    SkillUsageError,
    failure,
    parse_options,
    result,
    split_command,
    truncate,
)
from botboy.skills.native.registry import ROUTER_RESERVED_COMMANDS

MAX_MANIFEST_BYTES = 256 * 1024
MAX_LINT_FILES = 200
SECURITY_LEVELS = ("BEGINNER", "INTERMEDIATE", "ADVANCED", "EXPERT")
RUNTIME_TYPES = {"builtin", "inprocess", "subprocess", "isolated", "auto"}
KNOWN_PERMISSION_PREFIXES = (
    "fs.read", "fs.write", "filesystem.read", "filesystem.write", "net.", "network",
    "proc.spawn", "process", "env.read", "clipboard", "notify",
)
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_TRIGGER_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,31}")
_LAYER_ID_RE = re.compile(r"[a-z0-9_-]+(\.[a-z0-9_-]+)+")
_VERSION_RE = re.compile(r"\d+\.\d+(\.\d+)?([-+][0-9A-Za-z.-]+)?")
_CODE_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


@dataclass
class Finding:
    level: str
    code: str
    message: str

    def render(self) -> str:
        return f"[{self.level.upper()}] {self.code}: {self.message}"


@dataclass
class LintReport:
    source: str
    findings: List[Finding] = field(default_factory=list)
    contract: Dict[str, Any] = field(default_factory=dict)
    executable: bool = False

    def add(self, level: str, code: str, message: str) -> None:
        self.findings.append(Finding(level, code, message))

    @property
    def errors(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "warning")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "ok": self.errors == 0,
            "errors": self.errors,
            "warnings": self.warnings,
            "executable": self.executable,
            "findings": [finding.__dict__ for finding in self.findings],
            "contract": self.contract,
        }

    def render(self) -> str:
        verdict = "PASS" if self.errors == 0 else "FAIL"
        lines = [f"{verdict} {self.source}: {self.errors} error(s), {self.warnings} warning(s)"]
        lines.extend(f"  {finding.render()}" for finding in self.findings)
        if self.contract:
            c = self.contract
            lines.append(
                "  effective contract: "
                f"name={c.get('name')} level={c.get('security_level')} runtime={c.get('runtime_tier')} "
                f"needs_approval={c.get('needs_approval')} network={c.get('allow_network')} "
                f"filesystem={c.get('allow_filesystem')} executable={self.executable}"
            )
        return "\n".join(lines)


def _trigger_owners(skill_manager: Any) -> Dict[str, str]:
    if skill_manager is not None and hasattr(skill_manager, "trigger_owners"):
        return dict(skill_manager.trigger_owners())
    from botboy.skills.manager import _BUILTIN_TRIGGERS

    return {trigger: f"builtin:{name}" for trigger, name in _BUILTIN_TRIGGERS.items()}


def lint_skill_text(content: str, source: str, *, skill_manager: Any = None) -> LintReport:
    """Lint SKILL.md ``content``; ``source`` is used for messages and contract paths."""
    # The loader reads SKILL.md with universal newlines (Path.read_text); lint
    # what the loader sees, or CRLF files (Windows checkouts) lose their code.
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    from botboy.skills.ldf import parse_layer_definition_text
    from botboy.skills.manager import _build_skill_contract
    from botboy.skills.runtime import _blocked_reason

    report = LintReport(source=source)
    match = _FRONTMATTER_RE.match(content)
    if not match:
        report.add("error", "frontmatter.missing", "File must start with a '---' line opening the YAML frontmatter and close it with '---'.")
        return report
    frontmatter = match.group(1)
    loader_parts = content.split("---")
    if len(loader_parts) < 3 or loader_parts[1].strip() != frontmatter.strip():
        report.add("error", "frontmatter.dashes",
                   "BotBoy's loader splits the file on every '---'; remove '---' from the frontmatter values.")
    try:
        meta = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        report.add("error", "yaml.invalid", f"Frontmatter is not valid YAML: {str(exc).splitlines()[0]}")
        return report
    if not isinstance(meta, dict):
        report.add("error", "yaml.not_mapping", "Frontmatter must be a YAML mapping.")
        return report

    name = meta.get("name")
    if not name or not isinstance(name, str):
        report.add("error", "name.missing", "'name' is required (the directory name is used otherwise, which is fragile).")
    elif not _NAME_RE.fullmatch(name):
        report.add("warning", "name.format", f"name {name!r} should be lower-case kebab-case (a-z, 0-9, '-').")
    description = meta.get("description")
    if not description or not str(description).strip():
        report.add("error", "description.missing", "'description' is required; routing and humans rely on it.")
    elif len(str(description).strip()) < 20:
        report.add("warning", "description.short", "description is under 20 characters; say what the skill does and when to use it.")
    version = meta.get("version")
    if version is not None and not _VERSION_RE.fullmatch(str(version)):
        report.add("warning", "version.format", f"version {version!r} is not semantic (MAJOR.MINOR[.PATCH]).")

    level = str(meta.get("security_level", "BEGINNER")).upper()
    if "security_level" not in meta:
        report.add("info", "security_level.default", "security_level missing; BotBoy assumes BEGINNER.")
    elif level not in SECURITY_LEVELS:
        report.add("error", "security_level.invalid",
                   f"security_level {meta.get('security_level')!r} is unknown; BotBoy silently treats it as INTERMEDIATE. Use one of {', '.join(SECURITY_LEVELS)}.")

    raw_triggers = meta.get("triggers", [])
    if isinstance(raw_triggers, str):
        raw_triggers = [item.strip() for item in raw_triggers.split(",")]
    if not isinstance(raw_triggers, list):
        report.add("error", "triggers.type", "triggers must be a list of single words.")
        raw_triggers = []
    owners = _trigger_owners(skill_manager)
    for trigger in raw_triggers:
        text = str(trigger).strip()
        lowered = text.lower()
        if not _TRIGGER_RE.fullmatch(lowered):
            report.add("error", "trigger.format",
                       f"trigger {text!r} can never match: routing compares only the first whitespace-separated word.")
            continue
        if lowered in ROUTER_RESERVED_COMMANDS:
            report.add("error", "trigger.reserved", f"trigger {text!r} is consumed by BotBoy's command router and never reaches skills.")
            continue
        owner = owners.get(lowered)
        if owner and owner != f"custom:{name}":
            kind = owner.split(":", 1)[0]
            level_name = "error" if kind in {"builtin", "native"} else "warning"
            report.add(level_name, "trigger.shadowed",
                       f"trigger {text!r} is already routed to {owner}; "
                       + ("builtin and native skills always win." if level_name == "error" else "routing order between custom skills is not guaranteed."))

    body = content[match.end():]
    code = "\n".join(_CODE_RE.findall(body))
    if code.strip():
        try:
            ast.parse(code)
        except SyntaxError as exc:
            report.add("error", "code.syntax", f"python block has a syntax error on line {exc.lineno}: {exc.msg}")
        blocked = _blocked_reason(code)
        if blocked:
            report.add("error", "code.blocked", f"{blocked}; the sandbox runtime refuses to execute it.")
        if not re.search(r"(^|\W)_result\s*=", code):
            report.add("warning", "code.no_result", "code never assigns _result; the skill will only report 'done'.")
    elif raw_triggers:
        report.add("warning", "code.missing",
                   "skill has triggers but no ```python block; invoking it fails with 'Skill code is empty'. "
                   "Prose-only skills should omit triggers.")

    try:
        contract = _build_skill_contract(meta, path=source, code=code, trusted=False).to_contract()
    except (TypeError, ValueError, AttributeError) as exc:
        report.add("error", "contract.build", f"BotBoy cannot derive a contract: {exc}")
        return report
    contract.pop("metadata", None)
    report.contract = contract

    runtime_type = str(((meta.get("layer") or {}).get("runtime") or {}).get("type", "")).lower() if isinstance(meta.get("layer"), dict) else ""
    tier = str(contract.get("runtime_tier", "")).lower()
    if runtime_type == "docker" or tier == "docker":
        report.add("error", "runtime.docker",
                   "runtime type 'docker' is not a runtime BotBoy knows; policy denies it. Use 'isolated' for Docker isolation.")
    elif tier and tier not in RUNTIME_TYPES:
        report.add("error", "runtime.unknown", f"runtime tier {tier!r} is unknown; policy denies execution.")
    if runtime_type == "builtin":
        entry = ((meta.get("layer") or {}).get("runtime") or {}).get("entry")
        report.add("info", "runtime.builtin",
                   "runtime.type builtin is only honoured for BotBoy's packaged native skills"
                   + (f"; entry {entry!r} is ignored" if entry else "")
                   + "; discovered skills always run in the sandbox runtime.")
    if code.strip() and (contract.get("allow_network") or contract.get("allow_filesystem")):
        causes = []
        if contract.get("allow_network"):
            causes.append("network")
        if contract.get("allow_filesystem"):
            trigger_text = " ".join(contract.get("triggers", [])).lower()
            inferred = "allow_filesystem" not in meta and any(token in trigger_text for token in ("file", "git", "diff", "code"))
            causes.append("filesystem" + (" (inferred from a trigger containing file/git/diff/code)" if inferred else ""))
        report.add("error", "runtime.capability_refused",
                   f"custom code skills declaring {' and '.join(causes)} access are refused by the sandbox runtime; "
                   "set allow_network/allow_filesystem: false explicitly or move the capability into a native skill.")
    if level in {"ADVANCED", "EXPERT"} or tier == "isolated":
        report.add("info", "runtime.isolated", "execution needs the Docker-isolated runtime; without Docker it is denied.")
    report.add("info", "approval",
               "every execution needs a server-issued approval (untrusted custom skill)"
               if contract.get("needs_approval") else "no approval required")
    remote_policy = meta.get("remote_policy")
    if remote_policy is not None and str(remote_policy).lower() not in {"allow", "approval"}:
        report.add("error", "remote_policy.invalid", "remote_policy must be 'allow' or 'approval'.")

    layer = meta.get("layer")
    if layer is not None and not isinstance(layer, dict):
        report.add("error", "layer.type", "'layer' must be a mapping.")
    elif isinstance(layer, dict):
        try:
            definition = parse_layer_definition_text(content, source)
        except (TypeError, ValueError, AttributeError) as exc:
            report.add("error", "ldf.invalid", f"LDF block cannot be parsed: {exc}")
            definition = None
        if definition is not None:
            if "id" in layer and not _LAYER_ID_RE.fullmatch(definition.layer_id):
                report.add("warning", "ldf.id", f"layer.id {definition.layer_id!r} should be dotted lower-case (e.g. botboy.skills.my-skill).")
            for permission in definition.security.permissions:
                if not str(permission).lower().startswith(KNOWN_PERMISSION_PREFIXES):
                    report.add("warning", "ldf.permission", f"permission {permission!r} is not in BotBoy's vocabulary ({', '.join(KNOWN_PERMISSION_PREFIXES)}).")
            if definition.resources.network and not contract.get("allow_network"):
                report.add("warning", "ldf.network", "layer.resources.network is true but the contract does not allow network.")
            if definition.runtime.timeout <= 0 or definition.runtime.timeout > 300:
                report.add("warning", "ldf.timeout", "layer.runtime.timeout should be between 1 and 300 seconds.")

    report.executable = bool(
        code.strip() and contract.get("triggers") and not any(
            finding.level == "error" for finding in report.findings
        )
    )
    return report


def scaffold(name: str, trigger: str, *, description: str, level: str = "BEGINNER") -> str:
    if not _NAME_RE.fullmatch(name):
        raise SkillUsageError("name must be lower-case kebab-case (a-z, 0-9, '-')")
    if not _TRIGGER_RE.fullmatch(trigger) or trigger in ROUTER_RESERVED_COMMANDS:
        raise SkillUsageError(f"trigger {trigger!r} is invalid or reserved")
    if level not in SECURITY_LEVELS[:2]:
        raise SkillUsageError("scaffold supports BEGINNER or INTERMEDIATE (custom skills run sandboxed without network/filesystem)")
    if "---" in description or "\n" in description:
        raise SkillUsageError("description must be a single line without '---'")
    safe_description = description.replace('"', "'")
    return (
        "---\n"
        f"name: {name}\n"
        "version: 0.1.0\n"
        f"description: \"{safe_description}\"\n"
        f"triggers: [{trigger}]\n"
        f"security_level: {level}\n"
        "allow_network: false\n"
        "allow_filesystem: false\n"
        "remote_policy: approval\n"
        "layer:\n"
        f"  id: botboy.skills.{name}\n"
        "  resources: {compute: low, memory: 16MB, storage: none, network: false}\n"
        "  security:\n"
        "    sandbox: subprocess\n"
        "    permissions: []\n"
        "  runtime:\n"
        "    type: subprocess\n"
        "    timeout: 10\n"
        "---\n\n"
        f"# {name}\n\n"
        f"    {trigger} <text>\n\n"
        "The sandbox passes `payload` (keys: command, trigger, args, text, skill) and returns\n"
        "whatever the code assigns to `_result`.\n\n"
        "```python\n"
        "text = payload.get(\"args\", \"\")\n"
        "words = text.split()\n"
        "_result = {\n"
        "    \"skill\": payload.get(\"skill\"),\n"
        "    \"words\": len(words),\n"
        "    \"characters\": len(text),\n"
        "    \"longest\": max(words, key=len) if words else None,\n"
        "}\n"
        "```\n"
    )


def _lint_path(ctx: NativeSkillContext, user_path: str) -> List[LintReport]:
    target = workspace.resolve(ctx.workspace_root, user_path)
    files: List[Path] = []
    if target.is_file():
        files.append(target)
    else:
        for entry in workspace.walk(ctx.workspace_root, target, max_depth=8, max_entries=20000):
            if not entry.is_dir and entry.path.name == "SKILL.md":
                files.append(entry.path)
                if len(files) >= MAX_LINT_FILES:
                    break
    if not files:
        raise SkillUsageError(f"No SKILL.md found under {user_path or '.'}")
    reports = []
    for path in files:
        content, _ = workspace.read_text(path, MAX_MANIFEST_BYTES, allow_truncation=False)
        reports.append(lint_skill_text(content, workspace.relative(ctx.workspace_root, path), skill_manager=ctx.skill_manager))
    owners: Dict[str, List[str]] = {}
    for report in reports:
        if report.contract.get("name"):
            owners.setdefault(report.contract["name"], []).append(report.source)
    for report in reports:
        others = [source for source in owners.get(report.contract.get("name", ""), []) if source != report.source]
        if others:
            report.add("error", "name.duplicate",
                       f"name {report.contract['name']!r} is also declared by {', '.join(others)}; "
                       "SkillManager keys skills by name, so only one of them would load.")
            report.executable = False
    return reports


def _audit(ctx: NativeSkillContext) -> List[LintReport]:
    manager = ctx.skill_manager
    if manager is None or not hasattr(manager, "custom_skill_paths"):
        raise SkillUsageError("No skill manager available to audit")
    reports = []
    for name, source in sorted(manager.custom_skill_paths().items()):
        path = Path(source)
        try:
            data, truncated = workspace.read_bytes(path, MAX_MANIFEST_BYTES)
        except workspace.WorkspaceError as exc:
            report = LintReport(source=source)
            report.add("error", "file.unreadable", str(exc))
            reports.append(report)
            continue
        if truncated:
            report = LintReport(source=source)
            report.add("error", "file.size", f"SKILL.md larger than {MAX_MANIFEST_BYTES} bytes")
            reports.append(report)
            continue
        reports.append(lint_skill_text(data.decode("utf-8", errors="replace"), source, skill_manager=manager))
    return reports


def _summarise(reports: List[LintReport], title: str) -> dict:
    errors = sum(report.errors for report in reports)
    warnings = sum(report.warnings for report in reports)
    executable = sum(1 for report in reports if report.executable)
    header = (f"{title}: {len(reports)} skill file(s), {errors} error(s), {warnings} warning(s), "
              f"{executable} executable")
    body = "\n".join(report.render() for report in reports)
    output, _ = truncate(f"{header}\n{body}", 20000)
    return result("forge", output, success=errors == 0, action=title.split()[0].lower(),
                  errors=errors, warnings=warnings, executable=executable,
                  reports=[report.to_dict() for report in reports[:MAX_LINT_FILES]])


_USAGE = (
    "Usage:\n"
    "  forge lint <SKILL.md | directory>   (relative to the workspace root)\n"
    "  forge audit                         (lint every discovered custom skill)\n"
    "  forge scaffold <name> --trigger <word> [--description TEXT] [--level BEGINNER|INTERMEDIATE]"
)


def _execute(ctx: NativeSkillContext, tokens: List[str]) -> dict:
    action, args = tokens[0].lower(), tokens[1:]
    if action == "lint":
        if len(args) != 1:
            raise SkillUsageError("forge lint <SKILL.md | directory>")
        return _summarise(_lint_path(ctx, args[0]), "Lint")
    if action == "audit":
        if args:
            raise SkillUsageError("forge audit takes no arguments")
        return _summarise(_audit(ctx), "Audit")
    if action == "scaffold":
        opts = parse_options(args, valued=("trigger", "description", "level"))
        if len(opts.positionals) != 1 or not opts.get("trigger"):
            raise SkillUsageError("forge scaffold <name> --trigger <word> [--description TEXT] [--level LEVEL]")
        name = opts.positionals[0]
        trigger = (opts.get("trigger") or "").lower()
        description = opts.get("description") or f"Generated skill {name}: replace this with what it does and when to use it."
        content = scaffold(name, trigger, description=description, level=(opts.get("level") or "BEGINNER").upper())
        report = lint_skill_text(content, f"{name}/SKILL.md", skill_manager=ctx.skill_manager)
        output = f"{content}\n# self-check\n{report.render()}"
        return result("forge", output, success=report.errors == 0, action="scaffold", name=name,
                      skill_md=content, lint=report.to_dict())
    raise SkillUsageError(f"Unknown forge action {action!r}")


async def run(command: str, ctx: NativeSkillContext) -> dict:
    try:
        _, tokens = split_command(command)
        if not tokens or tokens[0].lower() in {"help", "--help"}:
            return result("forge", _USAGE, usage=True) if tokens else failure("forge", _USAGE)
        return await asyncio.to_thread(_execute, ctx, tokens)
    except (SkillUsageError, workspace.WorkspaceError) as exc:
        return failure("forge", f"forge: {exc}\n{_USAGE}" if "Unknown forge action" in str(exc) else f"forge: {exc}")
