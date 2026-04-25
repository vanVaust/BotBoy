"""
Layer Definition Format (LDF) — Extended SKILL.md parser.

Extends the existing SKILL.md YAML frontmatter with full DeusAnimaOmni
Layer Definition Format fields: channel interfaces, resource declarations,
security permissions, and runtime specifications.

Backward compatible: existing SKILL.md files without LDF extensions load
as before, with sensible defaults filling all new fields.

Key additions over basic SKILL.md:
  interface.input/output  → which sense channels carry I/O
  resources               → compute/memory/storage declarations
  security.permissions    → fine-grained permission list
  runtime.type            → builtin | inprocess | subprocess | docker
  layer.id                → globally unique layer identifier (dotted notation)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


# ── Channel Interface Specification ──────────────────────────────────────────

@dataclass
class ChannelInterfaceSpec:
    """Describes a layer's I/O contract on a single sense channel."""
    channel:     str               # "haptic" | "olfactory" | "gustatory" | "visual" | "auditory"
    iotype:      str               # "input" | "output"
    data_type:   str = "any"       # "command" | "metrics" | "score" | "render" | "stream"
    format:      str = ""          # specific format hint
    required:    bool = False
    triggers:    List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class LayerInterface:
    """Complete I/O channel interface for a layer."""
    inputs:  List[ChannelInterfaceSpec] = field(default_factory=list)
    outputs: List[ChannelInterfaceSpec] = field(default_factory=list)

    def input_channels(self) -> List[str]:
        return list({s.channel for s in self.inputs})

    def output_channels(self) -> List[str]:
        return list({s.channel for s in self.outputs})

    def channel_compatibility(self, other: "LayerInterface") -> dict:
        """Check if this layer's outputs are compatible with another's inputs."""
        my_outputs = set(self.output_channels())
        their_inputs = set(other.input_channels())
        matched = my_outputs & their_inputs
        unmatched = my_outputs - their_inputs
        return {
            "compatible": len(matched) > 0,
            "matched_channels": list(matched),
            "unmatched_channels": list(unmatched),
            "match_quality": len(matched) / max(len(my_outputs), 1),
        }


@dataclass
class ResourceSpec:
    compute:  str = "low"    # "low" | "medium" | "high"
    memory:   str = "10MB"
    storage:  str = "none"
    network:  bool = False


@dataclass
class SecuritySpec:
    sandbox:     str = "inprocess"     # "inprocess" | "subprocess" | "docker"
    permissions: List[str] = field(default_factory=list)
    protection:  str = ""              # free-text protection notes


@dataclass
class RuntimeSpec:
    runtime_type:  str = "builtin"    # "builtin" | "inprocess" | "subprocess" | "docker"
    entry:         str = ""           # dotted reference to Python callable
    timeout:       int = 10


# ── Layer Definition ──────────────────────────────────────────────────────────

@dataclass
class LayerDefinition:
    """
    Full Layer Definition Format — superset of SKILL.md.

    All fields are populated from the SKILL.md YAML frontmatter.
    Missing LDF fields receive sensible defaults so that every
    existing SKILL.md is automatically a valid LayerDefinition.
    """
    # Core identity (always present)
    layer_id:       str
    name:           str
    version:        str
    description:    str
    triggers:       List[str]
    security_level: str
    source_path:    str

    # LDF extensions (new in v0.7)
    interface:  LayerInterface = field(default_factory=LayerInterface)
    resources:  ResourceSpec   = field(default_factory=ResourceSpec)
    security:   SecuritySpec   = field(default_factory=SecuritySpec)
    runtime:    RuntimeSpec    = field(default_factory=RuntimeSpec)
    metadata:   Dict[str, Any] = field(default_factory=dict)
    code:       str            = ""

    # Compatibility check helpers
    def is_compatible_with(self, other: "LayerDefinition") -> dict:
        """Check channel-level compatibility between two layers."""
        return self.interface.channel_compatibility(other.interface)

    def to_dict(self) -> dict:
        return {
            "layer_id":      self.layer_id,
            "name":          self.name,
            "version":       self.version,
            "description":   self.description,
            "triggers":      self.triggers,
            "security_level": self.security_level,
            "runtime_type":  self.runtime.runtime_type,
            "input_channels": self.interface.input_channels(),
            "output_channels": self.interface.output_channels(),
            "resources":     {
                "compute": self.resources.compute,
                "memory":  self.resources.memory,
                "network": self.resources.network,
            },
            "permissions":   self.security.permissions,
        }


# ── Minimal YAML parser (fallback when pyyaml is absent) ─────────────────────

def _minimal_yaml_parse(text: str) -> dict:
    """
    Parse simple YAML key: value and key:\n  - item lists.
    Sufficient for SKILL.md frontmatter; not a full YAML parser.
    """
    result: dict = {}
    lines = text.strip().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        if ":" in line and not line.startswith(" "):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "" or val == "|" or val == ">":
                # Possibly a block scalar or list — peek ahead
                items = []
                i += 1
                while i < len(lines) and lines[i].startswith("  "):
                    item = lines[i].strip()
                    if item.startswith("- "):
                        items.append(item[2:].strip().strip("'\""))
                    elif item.startswith("-"):
                        items.append(item[1:].strip().strip("'\""))
                    i += 1
                result[key] = items if items else ""
                continue
            # Strip inline list notation [a, b, c]
            if val.startswith("[") and val.endswith("]"):
                result[key] = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
            elif val.lower() == "true":
                result[key] = True
            elif val.lower() == "false":
                result[key] = False
            else:
                try:
                    result[key] = int(val)
                except ValueError:
                    try:
                        result[key] = float(val)
                    except ValueError:
                        result[key] = val.strip("'\"")
        i += 1
    return result


def _nested_yaml_parse(text: str) -> dict:
    """Parse nested YAML mappings/lists for SKILL.md frontmatter without PyYAML."""
    lines = text.strip().splitlines()

    def _indent(raw: str) -> int:
        return len(raw) - len(raw.lstrip(" "))

    def _parse_scalar(value: str):
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [_parse_scalar(part.strip()) for part in inner.split(",") if part.strip()]
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value.strip("'\"")

    def _next_meaningful(start: int) -> int:
        idx = start
        while idx < len(lines):
            stripped = lines[idx].strip()
            if stripped and not stripped.startswith("#"):
                return idx
            idx += 1
        return idx

    def _parse_list(start: int, indent: int):
        items = []
        idx = start
        while idx < len(lines):
            raw = lines[idx]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                idx += 1
                continue
            current_indent = _indent(raw)
            if current_indent < indent or not stripped.startswith("- "):
                break
            item_text = stripped[2:].strip()
            if not item_text:
                child_idx = _next_meaningful(idx + 1)
                if child_idx < len(lines) and _indent(lines[child_idx]) > current_indent:
                    child, idx = _parse_block(child_idx, _indent(lines[child_idx]))
                    items.append(child)
                    continue
                items.append("")
                idx += 1
                continue
            items.append(_parse_scalar(item_text))
            idx += 1
        return items, idx

    def _parse_block(start: int, indent: int):
        data: Dict[str, Any] = {}
        idx = start
        while idx < len(lines):
            raw = lines[idx]
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                idx += 1
                continue
            current_indent = _indent(raw)
            if current_indent < indent:
                break
            if current_indent > indent:
                break
            if ":" not in stripped:
                idx += 1
                continue
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            if value in {"|", ">"}:
                block_lines = []
                idx += 1
                while idx < len(lines):
                    candidate = lines[idx]
                    candidate_indent = _indent(candidate)
                    if candidate.strip() and candidate_indent <= current_indent:
                        break
                    if candidate.strip():
                        block_lines.append(candidate.strip())
                    idx += 1
                data[key] = " ".join(block_lines).strip()
                continue

            if value == "":
                child_idx = _next_meaningful(idx + 1)
                if child_idx >= len(lines) or _indent(lines[child_idx]) <= current_indent:
                    data[key] = {}
                    idx += 1
                    continue
                child_indent = _indent(lines[child_idx])
                if lines[child_idx].strip().startswith("- "):
                    child, idx = _parse_list(child_idx, child_indent)
                else:
                    child, idx = _parse_block(child_idx, child_indent)
                data[key] = child
                continue

            data[key] = _parse_scalar(value)
            idx += 1
        return data, idx

    parsed, _ = _parse_block(0, 0)
    return parsed


def _parse_yaml(text: str) -> dict:
    if _YAML_AVAILABLE:
        return yaml.safe_load(text) or {}
    return _nested_yaml_parse(text)


# ── LDF Builder ───────────────────────────────────────────────────────────────

def _build_interface(layer_meta: dict) -> LayerInterface:
    """Parse the 'interface' section of a layer YAML block."""
    iface_data = layer_meta.get("interface", {})
    inputs, outputs = [], []

    for channel, specs_list in iface_data.get("input", {}).items():
        if isinstance(specs_list, list):
            for spec in specs_list:
                if isinstance(spec, dict):
                    inputs.append(ChannelInterfaceSpec(
                        channel=channel, iotype="input",
                        data_type=spec.get("type", "any"),
                        triggers=spec.get("triggers", []),
                        description=spec.get("description", ""),
                    ))
        else:
            inputs.append(ChannelInterfaceSpec(channel=channel, iotype="input"))

    for channel, specs_list in iface_data.get("output", {}).items():
        if isinstance(specs_list, list):
            for spec in specs_list:
                if isinstance(spec, dict):
                    outputs.append(ChannelInterfaceSpec(
                        channel=channel, iotype="output",
                        data_type=spec.get("type", "any"),
                        format=spec.get("format", ""),
                        description=spec.get("description", ""),
                    ))
        else:
            outputs.append(ChannelInterfaceSpec(channel=channel, iotype="output"))

    return LayerInterface(inputs=inputs, outputs=outputs)


def _default_interface_for_security_level(security_level: str,
                                           triggers: List[str]) -> LayerInterface:
    """
    Infer a reasonable default channel interface from a skill's security
    level and trigger names. Used when no explicit 'layer.interface' is declared.
    """
    # All skills have HAPTIC input (they respond to commands)
    inputs = [ChannelInterfaceSpec(channel="haptic", iotype="input",
                                   data_type="command", triggers=triggers)]
    outputs = [ChannelInterfaceSpec(channel="haptic", iotype="output",
                                    data_type="command-result")]

    # Add channel enrichments by security level / trigger content
    trigger_text = " ".join(triggers).lower()
    if any(t in trigger_text for t in ["analyze", "stats", "diff", "web", "git"]):
        outputs.append(ChannelInterfaceSpec(
            channel="visual", iotype="output", data_type="render"))
        outputs.append(ChannelInterfaceSpec(
            channel="olfactory", iotype="output", data_type="metrics"))

    if any(t in trigger_text for t in ["calculate", "hash", "convert", "base64"]):
        outputs.append(ChannelInterfaceSpec(
            channel="gustatory", iotype="output", data_type="score",
            description="result confidence"))

    if security_level in ("ADVANCED", "EXPERT"):
        outputs.append(ChannelInterfaceSpec(
            channel="olfactory", iotype="output", data_type="metrics",
            description="resource telemetry"))

    return LayerInterface(inputs=inputs, outputs=outputs)


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_layer_definition(path: str) -> Optional[LayerDefinition]:
    """
    Parse a SKILL.md file into a full LayerDefinition.

    Backward compatible: SKILL.md files without LDF extensions load
    successfully with default values for all new fields.
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None

    # Split on YAML frontmatter delimiters
    parts = content.split("---")
    if len(parts) < 3:
        return None

    try:
        meta = _parse_yaml(parts[1])
    except Exception:
        return None

    # Extract Python code blocks from the body
    body = "---".join(parts[2:])
    code_blocks = re.findall(r"```python\n(.*?)```", body, re.DOTALL)
    code = "\n".join(code_blocks)

    # Core fields
    triggers = meta.get("triggers", [])
    if isinstance(triggers, str):
        triggers = [t.strip() for t in triggers.split(",")]
    triggers = [str(t) for t in triggers]

    name = meta.get("name", Path(path).parent.name)
    security_level = str(meta.get("security_level", "BEGINNER")).upper()

    # LDF layer block
    layer_meta = meta.get("layer", {})
    if not isinstance(layer_meta, dict):
        layer_meta = {}
    layer_id = layer_meta.get("id", f"botboy.skills.{name.replace(' ', '-').lower()}")
    layer_id = str(layer_id)

    # Interface: explicit LDF or inferred default
    if "interface" in layer_meta:
        interface = _build_interface(layer_meta)
    else:
        interface = _default_interface_for_security_level(security_level, triggers)

    # Resources
    res_data = layer_meta.get("resources", {})
    resources = ResourceSpec(
        compute=str(res_data.get("compute", "low")),
        memory=str(res_data.get("memory", "10MB")),
        storage=str(res_data.get("storage", "none")),
        network=bool(res_data.get("network", False)),
    )

    # Security
    sec_data = layer_meta.get("security", {})
    sandbox_map = {
        "BEGINNER": "inprocess",
        "INTERMEDIATE": "subprocess",
        "ADVANCED": "docker",
        "EXPERT": "docker",
    }
    security = SecuritySpec(
        sandbox=str(sec_data.get("sandbox", sandbox_map.get(security_level, "inprocess"))),
        permissions=list(sec_data.get("permissions", [])),
        protection=str(sec_data.get("protection", "")),
    )

    # Runtime
    rt_data = layer_meta.get("runtime", {})
    runtime = RuntimeSpec(
        runtime_type=str(rt_data.get("type", "builtin")),
        entry=str(rt_data.get("entry", "")),
        timeout=int(rt_data.get("timeout", 10)),
    )

    return LayerDefinition(
        layer_id=layer_id,
        name=str(name),
        version=str(meta.get("version", "1.0.0")),
        description=str(meta.get("description", "")),
        triggers=triggers,
        security_level=security_level,
        source_path=path,
        interface=interface,
        resources=resources,
        security=security,
        runtime=runtime,
        metadata=meta,
        code=code,
    )


# ── Compatibility checker ─────────────────────────────────────────────────────

def check_layer_compatibility(layer_a: LayerDefinition,
                               layer_b: LayerDefinition) -> dict:
    """
    Check channel-level compatibility between two layers.
    Returns a compatibility report following the five-senses paradigm.
    """
    return layer_a.is_compatible_with(layer_b)


# ── LDF Registry ─────────────────────────────────────────────────────────────

class LDFRegistry:
    """In-memory registry of all loaded LayerDefinitions."""

    def __init__(self) -> None:
        self._layers: Dict[str, LayerDefinition] = {}

    def register(self, layer: LayerDefinition) -> None:
        self._layers[layer.layer_id] = layer

    def get(self, layer_id: str) -> Optional[LayerDefinition]:
        return self._layers.get(layer_id)

    def find_by_trigger(self, trigger: str) -> List[LayerDefinition]:
        t = trigger.lower()
        return [l for l in self._layers.values()
                if t in [x.lower() for x in l.triggers]]

    def find_compatible(self, layer: LayerDefinition) -> List[LayerDefinition]:
        """Find all registered layers compatible with the given layer's outputs."""
        return [
            other for other in self._layers.values()
            if other.layer_id != layer.layer_id
            and layer.is_compatible_with(other)["compatible"]
        ]

    def all_layers(self) -> List[LayerDefinition]:
        return list(self._layers.values())

    def stats(self) -> dict:
        all_channels = set()
        for l in self._layers.values():
            all_channels.update(l.interface.input_channels())
            all_channels.update(l.interface.output_channels())
        return {
            "total_layers": len(self._layers),
            "channels_in_use": list(all_channels),
            "security_levels": list({l.security_level for l in self._layers.values()}),
        }
