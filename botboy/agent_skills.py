"""Agent skill registry and routing helpers for the BotBoy skill library."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from botboy.resources import resolve_registry_skill_path, skill_registry_path

def default_registry_path() -> Path:
    return skill_registry_path()


@dataclass
class AgentSkillEntry:
    name: str
    category: str
    phase: str
    summary: str
    path: str
    portfolio_bucket: str
    portfolio_tier: str
    implementation_rank: int
    tags: List[str]
    routing_keywords: List[str]
    has_references: bool
    has_scripts: bool
    inputs_schema: Dict[str, Any] = field(default_factory=dict)
    outputs_schema: Dict[str, Any] = field(default_factory=dict)

    class ContractValidationError(Exception):
        pass

    def validate_contract(self, inputs: Dict[str, Any]) -> bool:
        """Validates inputs against the typed contract schema."""
        if not self.inputs_schema:
            return True
            
        required = self.inputs_schema.get("required", [])
        for req in required:
            if req not in inputs:
                raise self.ContractValidationError(f"Missing required input: {req}")
                
        properties = self.inputs_schema.get("properties", {})
        for k, v in inputs.items():
            if k in properties:
                expected_type = properties[k].get("type")
                if expected_type == "string" and not isinstance(v, str):
                    raise self.ContractValidationError(f"Input '{k}' must be string")
                elif expected_type == "integer" and not isinstance(v, int):
                    raise self.ContractValidationError(f"Input '{k}' must be integer")
                elif expected_type == "boolean" and not isinstance(v, bool):
                    raise self.ContractValidationError(f"Input '{k}' must be boolean")
        return True

    @property
    def resolved_path(self) -> str:
        return str(resolve_registry_skill_path(self.path))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AgentSkillEntry":
        return cls(
            name=str(payload.get("name", "")),
            category=str(payload.get("category", "uncategorized")),
            phase=str(payload.get("phase", "unknown")),
            summary=str(payload.get("summary", "")),
            path=str(payload.get("path", "")),
            portfolio_bucket=str(payload.get("portfolio_bucket", "")),
            portfolio_tier=str(payload.get("portfolio_tier", "")),
            implementation_rank=int(payload.get("implementation_rank", 0) or 0),
            tags=[str(item) for item in payload.get("tags", [])],
            routing_keywords=[str(item) for item in payload.get("routing_keywords", [])],
            has_references=bool(payload.get("has_references", False)),
            has_scripts=bool(payload.get("has_scripts", False)),
            inputs_schema=payload.get("inputs_schema", {}),
            outputs_schema=payload.get("outputs_schema", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "phase": self.phase,
            "summary": self.summary,
            "path": self.path,
            "portfolio_bucket": self.portfolio_bucket,
            "portfolio_tier": self.portfolio_tier,
            "implementation_rank": self.implementation_rank,
            "tags": list(self.tags),
            "routing_keywords": list(self.routing_keywords),
            "has_references": self.has_references,
            "has_scripts": self.has_scripts,
            "inputs_schema": self.inputs_schema,
            "outputs_schema": self.outputs_schema,
        }


class AgentSkillLibrary:
    def __init__(self, entries: List[AgentSkillEntry], *, registry_path: Optional[Path] = None) -> None:
        self.entries = entries
        self.registry_path = registry_path

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "AgentSkillLibrary":
        registry_path = Path(path) if path else default_registry_path()
        payload = json.loads(registry_path.read_text(encoding="utf-8-sig"))
        entries = [AgentSkillEntry.from_dict(item) for item in payload.get("skills", [])]
        return cls(entries, registry_path=registry_path)

    def stats(self) -> Dict[str, Any]:
        by_category: Dict[str, int] = {}
        by_phase: Dict[str, int] = {}
        by_bucket: Dict[str, int] = {}
        by_tier: Dict[str, int] = {}
        for entry in self.entries:
            by_category[entry.category] = by_category.get(entry.category, 0) + 1
            by_phase[entry.phase] = by_phase.get(entry.phase, 0) + 1
            if entry.portfolio_bucket:
                by_bucket[entry.portfolio_bucket] = by_bucket.get(entry.portfolio_bucket, 0) + 1
            if entry.portfolio_tier:
                by_tier[entry.portfolio_tier] = by_tier.get(entry.portfolio_tier, 0) + 1
        return {
            "count": len(self.entries),
            "by_category": dict(sorted(by_category.items())),
            "by_bucket": dict(sorted(by_bucket.items())),
            "by_phase": dict(sorted(by_phase.items())),
            "by_tier": dict(sorted(by_tier.items())),
        }

    def get(self, name: str) -> Optional[AgentSkillEntry]:
        lowered = name.strip().lower()
        for entry in self.entries:
            if entry.name.lower() == lowered:
                return entry
        return None

    def load_skill_dynamic(self, payload: Dict[str, Any]) -> AgentSkillEntry:
        """Dynamically loads and hot-swaps an MCP/Skill entry at runtime."""
        entry = AgentSkillEntry.from_dict(payload)
        
        # Remove old entry if hot-swapping an existing skill
        self.unload_skill(entry.name)
        
        self.entries.append(entry)
        return entry

    def unload_skill(self, name: str) -> bool:
        """Dynamically unloads a skill at runtime."""
        lowered = name.strip().lower()
        for i, entry in enumerate(self.entries):
            if entry.name.lower() == lowered:
                self.entries.pop(i)
                return True
        return False

    def suggest(self, query: str, *, phase: str = "", limit: int = 5) -> List[AgentSkillEntry]:
        tokens = [token for token in re.split(r"[^a-z0-9]+", query.lower()) if token]
        scored: List[tuple[int, AgentSkillEntry]] = []
        for entry in self.entries:
            if phase and entry.phase != phase:
                continue
            score = 0
            haystacks = [
                entry.name.lower().replace("-", " "),
                entry.summary.lower(),
                " ".join(entry.tags).lower(),
                " ".join(entry.routing_keywords).lower(),
                entry.category.lower().replace("-", " "),
            ]
            for token in tokens:
                for hay in haystacks:
                    if token in hay:
                        score += 2
                if token in entry.name.lower():
                    score += 3
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [entry for _, entry in scored[:limit]]


def format_skill_library_summary(library: AgentSkillLibrary) -> str:
    stats = library.stats()
    lines = [
        f"Agent skill library: {stats['count']} skills",
        "By phase: " + ", ".join(f"{k}={v}" for k, v in stats["by_phase"].items()),
        "By category: " + ", ".join(f"{k}={v}" for k, v in stats["by_category"].items()),
    ]
    if stats["by_bucket"]:
        lines.append("By bucket: " + ", ".join(f"{k}={v}" for k, v in stats["by_bucket"].items()))
    if stats["by_tier"]:
        lines.append("By tier: " + ", ".join(f"{k}={v}" for k, v in stats["by_tier"].items()))
    return "\n".join(lines)


def format_skill_route(query: str, matches: List[AgentSkillEntry]) -> str:
    if not matches:
        return f"No agent skill matches for '{query}'."
    lines = [f"Agent skill matches for '{query}':"]
    for entry in matches:
        suffix = f"{entry.phase}"
        if entry.portfolio_tier:
            suffix += f", {entry.portfolio_tier}"
        if entry.portfolio_bucket:
            suffix += f", {entry.portfolio_bucket}"
        lines.append(f"  {entry.name} [{suffix}] - {entry.summary}")
    return "\n".join(lines)
