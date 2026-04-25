"""Skills Manager — 9 builtin skills + SKILL.md auto-discovery parser."""
from __future__ import annotations

import ast
import base64
import glob
import hashlib
import json
import math
import operator
import os
import platform
import re
import socket
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ── Skill Metadata ─────────────────────────────────────────────────────────────

@dataclass
class Skill:
    name: str
    version: str
    description: str
    triggers: List[str]
    security_level: str
    source_path: str
    code: str = ""
    runtime_tier: str = "auto"
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)
    needs_approval: bool = False
    allow_network: bool = False
    allow_filesystem: bool = False
    trusted: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_contract(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "triggers": list(self.triggers),
            "security_level": self.security_level,
            "runtime_tier": self.runtime_tier,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "capabilities": list(self.capabilities),
            "needs_approval": self.needs_approval,
            "allow_network": self.allow_network,
            "allow_filesystem": self.allow_filesystem,
            "trusted": self.trusted,
            "source_path": self.source_path,
            "metadata": dict(self.metadata),
        }


# ── SKILL.md Parser ───────────────────────────────────────────────────────────

_DANGEROUS_CODE_PATTERNS = re.compile(
    r'\b(eval|exec|__import__|subprocess|os\.system|os\.popen|open\s*\(.*["\']w["\'])\b',
    re.IGNORECASE,
)

_SECURITY_DEFAULT_RUNTIME = {
    "BEGINNER": "inprocess",
    "INTERMEDIATE": "subprocess",
    "ADVANCED": "isolated",
    "EXPERT": "isolated",
}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "allow", "allowed"}
    return bool(value)


def _coerce_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("[") and stripped.endswith("]"):
            inner = stripped[1:-1]
            return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return [str(value).strip()]


def _coerce_schema(value: Any, fallback_title: str) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {"type": "object", "title": fallback_title, "properties": {}}


def _normalize_security_level(value: Any) -> str:
    level = str(value or "BEGINNER").upper()
    return level if level in _SECURITY_DEFAULT_RUNTIME else "INTERMEDIATE"


def _nested_get(mapping: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _parse_scalar(value: str) -> Any:
    stripped = value.strip()
    if not stripped:
        return ""
    lowered = stripped.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
    return stripped.strip("'\"")


def _next_meaningful_line(lines: List[str], start_index: int) -> tuple[int, str] | tuple[None, None]:
    index = start_index
    while index < len(lines):
        raw = lines[index]
        if raw.strip():
            return index, raw
        index += 1
    return None, None


def _parse_frontmatter_fallback(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    lines = text.splitlines()
    stack: List[tuple[int, Any]] = [(-1, root)]
    index = 0

    while index < len(lines):
        raw = lines[index].rstrip()
        index += 1
        if not raw.strip():
            continue

        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        container = stack[-1][1]

        if line.startswith("- "):
            if isinstance(container, list):
                container.append(_parse_scalar(line[2:]))
            continue

        key, sep, value = line.partition(":")
        if not sep or not isinstance(container, dict):
            continue

        key = key.strip()
        value = value.strip()
        if value in {">", "|"}:
            block_lines: List[str] = []
            while index < len(lines):
                candidate = lines[index].rstrip()
                if not candidate.strip():
                    block_lines.append("")
                    index += 1
                    continue
                candidate_indent = len(candidate) - len(candidate.lstrip(" "))
                if candidate_indent <= indent:
                    break
                block_lines.append(candidate.strip())
                index += 1
            if value == ">":
                container[key] = " ".join(part for part in block_lines if part).strip()
            else:
                container[key] = "\n".join(block_lines).strip()
            continue

        if value == "":
            next_index, next_line = _next_meaningful_line(lines, index)
            if next_line is None:
                container[key] = {}
                continue
            next_indent = len(next_line) - len(next_line.lstrip(" "))
            if next_indent <= indent:
                container[key] = {}
                continue
            new_container: Any = [] if next_line.strip().startswith("- ") else {}
            container[key] = new_container
            stack.append((indent, new_container))
            continue

        container[key] = _parse_scalar(value)

    return root


def _infer_runtime_tier(meta: Dict[str, Any], security_level: str) -> str:
    def normalize_tier(value: Any) -> str:
        tier = str(value or "").strip().lower()
        if tier == "builtin":
            return "inprocess"
        return tier

    runtime_tier = meta.get("runtime_tier")
    if runtime_tier:
        return normalize_tier(runtime_tier)
    layer_tier = _nested_get(meta, "layer", "runtime", "type")
    if layer_tier:
        return normalize_tier(layer_tier)
    sandbox_tier = _nested_get(meta, "layer", "security", "sandbox")
    if sandbox_tier:
        return normalize_tier(sandbox_tier)
    return _SECURITY_DEFAULT_RUNTIME.get(security_level, "subprocess")


def _infer_allow_network(meta: Dict[str, Any]) -> bool:
    direct = meta.get("allow_network")
    if direct is not None:
        return _coerce_bool(direct)
    layer_network = _nested_get(meta, "layer", "resources", "network")
    if layer_network is not None:
        return _coerce_bool(layer_network)
    capabilities = _coerce_list(meta.get("capabilities"))
    return any(cap.lower() in {"network", "web", "http"} for cap in capabilities)


def _infer_allow_filesystem(meta: Dict[str, Any], triggers: List[str]) -> bool:
    direct = meta.get("allow_filesystem")
    if direct is not None:
        return _coerce_bool(direct)
    permissions = _coerce_list(_nested_get(meta, "layer", "security", "permissions", default=[]))
    if any("file" in permission.lower() or "fs" in permission.lower() for permission in permissions):
        return True
    trigger_text = " ".join(triggers).lower()
    return any(token in trigger_text for token in ("file", "git", "diff", "code"))


def _infer_capabilities(meta: Dict[str, Any], triggers: List[str], allow_network: bool,
                        allow_filesystem: bool) -> List[str]:
    capabilities = _coerce_list(meta.get("capabilities"))
    lowered = {item.lower() for item in capabilities}
    if allow_network and "network" not in lowered:
        capabilities.append("network")
        lowered.add("network")
    if allow_filesystem and "filesystem" not in lowered:
        capabilities.append("filesystem")
        lowered.add("filesystem")
    if triggers and "command-routing" not in lowered:
        capabilities.append("command-routing")
    return capabilities


def _infer_needs_approval(meta: Dict[str, Any], *, security_level: str, trusted: bool,
                          allow_network: bool, allow_filesystem: bool) -> bool:
    explicit = meta.get("needs_approval")
    if explicit is not None:
        return _coerce_bool(explicit)
    if not trusted:
        return True
    if security_level in ("INTERMEDIATE", "ADVANCED", "EXPERT"):
        return True
    return allow_network or allow_filesystem


def _build_skill_contract(meta: Dict[str, Any], *, path: str, code: str,
                          trusted: bool = False) -> Skill:
    triggers = _coerce_list(meta.get("triggers", []))
    security_level = _normalize_security_level(meta.get("security_level", "BEGINNER"))
    allow_network = _infer_allow_network(meta)
    allow_filesystem = _infer_allow_filesystem(meta, triggers)
    return Skill(
        name=str(meta.get("name", Path(path).parent.name)),
        version=str(meta.get("version", "1.0.0")),
        description=str(meta.get("description", "")),
        triggers=triggers,
        security_level=security_level,
        source_path=path,
        code=code,
        runtime_tier=_infer_runtime_tier(meta, security_level),
        input_schema=_coerce_schema(meta.get("input_schema"), f"{Path(path).stem}Input"),
        output_schema=_coerce_schema(meta.get("output_schema"), f"{Path(path).stem}Output"),
        capabilities=_infer_capabilities(meta, triggers, allow_network, allow_filesystem),
        needs_approval=_infer_needs_approval(
            meta,
            security_level=security_level,
            trusted=trusted,
            allow_network=allow_network,
            allow_filesystem=allow_filesystem,
        ),
        allow_network=allow_network,
        allow_filesystem=allow_filesystem,
        trusted=trusted,
        metadata=meta,
    )


def parse_skill_md(path: str) -> Optional[Skill]:
    """Parse a SKILL.md file into a Skill dataclass."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None

    # Split YAML frontmatter
    parts = content.split("---")
    if len(parts) < 3:
        return None

    try:
        try:
            import yaml
            meta = yaml.safe_load(parts[1]) or {}
        except ImportError:
            meta = _parse_frontmatter_fallback(parts[1])
    except Exception:
        return None

    body = "---".join(parts[2:])

    # Extract Python code blocks
    code_blocks = re.findall(r"```python\n(.*?)```", body, re.DOTALL)
    code = "\n".join(code_blocks)

    # Validate security — block truly dangerous patterns in custom skills
    return _build_skill_contract(meta, path=path, code=code, trusted=False)


# ── AST-Safe Calculator ────────────────────────────────────────────────────────

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
}

_SAFE_FUNCS = {
    name: getattr(math, name)
    for name in dir(math)
    if not name.startswith("_") and callable(getattr(math, name))
}
_SAFE_FUNCS.update({
    "abs": abs, "round": round, "min": min, "max": max,
    "sum": sum, "int": int, "float": float,
})
_SAFE_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}


def _safe_eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value)}")
    if isinstance(node, ast.BinOp):
        op = type(node.op)
        if op not in _SAFE_OPS:
            raise ValueError(f"Unsupported operator: {op}")
        return _SAFE_OPS[op](_safe_eval_node(node.left), _safe_eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op = type(node.op)
        if op not in _SAFE_OPS:
            raise ValueError(f"Unsupported unary operator: {op}")
        return _SAFE_OPS[op](_safe_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls allowed")
        func_name = node.func.id
        if func_name not in _SAFE_FUNCS:
            raise ValueError(f"Function not allowed: {func_name}")
        args = [_safe_eval_node(a) for a in node.args]
        return _SAFE_FUNCS[func_name](*args)
    if isinstance(node, ast.Name):
        if node.id in _SAFE_CONSTS:
            return _SAFE_CONSTS[node.id]
        raise ValueError(f"Undefined name: {node.id}")
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def safe_calculate(expression: str) -> Any:
    """Evaluate a mathematical expression safely using AST parsing."""
    tree = ast.parse(expression.strip(), mode="eval")
    return _safe_eval_node(tree)


# ── Unit Conversion ────────────────────────────────────────────────────────────

_CONVERSIONS: Dict[tuple, float] = {
    # Length (base: meter)
    ("km", "m"): 1000, ("m", "km"): 0.001,
    ("m", "cm"): 100, ("cm", "m"): 0.01,
    ("m", "mm"): 1000, ("mm", "m"): 0.001,
    ("m", "ft"): 3.28084, ("ft", "m"): 0.3048,
    ("m", "in"): 39.3701, ("in", "m"): 0.0254,
    ("km", "mi"): 0.621371, ("mi", "km"): 1.60934,
    # Weight (base: kg)
    ("kg", "g"): 1000, ("g", "kg"): 0.001,
    ("kg", "lb"): 2.20462, ("lb", "kg"): 0.453592,
    ("kg", "oz"): 35.274, ("oz", "kg"): 0.0283495,
    # Temperature — handled separately
}


def convert_units(value: float, from_unit: str, to_unit: str) -> float:
    from_u = from_unit.lower()
    to_u = to_unit.lower()

    # Temperature special cases
    if from_u == "c" and to_u == "f":
        return value * 9 / 5 + 32
    if from_u == "f" and to_u == "c":
        return (value - 32) * 5 / 9
    if from_u == "c" and to_u == "k":
        return value + 273.15
    if from_u == "k" and to_u == "c":
        return value - 273.15

    factor = _CONVERSIONS.get((from_u, to_u))
    if factor:
        return value * factor

    raise ValueError(f"Unknown conversion: {from_unit} → {to_unit}")


# ── Builtin Skills ─────────────────────────────────────────────────────────────

class BuiltinSkills:
    """9 built-in skills — all AST-safe, no eval/exec."""

    @staticmethod
    async def calculator(command: str) -> dict:
        """calculate / compute / math — AST-safe expression evaluator."""
        # Extract expression from command
        prefix_re = re.compile(r"^(calculate|compute|math|calc)\s+", re.IGNORECASE)
        expr = prefix_re.sub("", command).strip()
        if not expr:
            return {"success": False, "output": "Usage: calculate <expression>", "type": "calculate"}
        try:
            result = safe_calculate(expr)
            if isinstance(result, float):
                formatted = f"{result:.10g}"
            else:
                formatted = str(result)
            return {"success": True, "output": f"{expr} = {formatted}", "type": "calculate",
                    "data": {"expression": expr, "result": result}}
        except Exception as e:
            return {"success": False, "output": f"Calculation error: {e}", "type": "calculate"}

    @staticmethod
    async def hash_text(command: str) -> dict:
        """hash — md5/sha1/sha256/sha512 hashing."""
        parts = command.strip().split(None, 2)
        # hash [algo] <text>
        if len(parts) < 2:
            return {"success": False, "output": "Usage: hash [algo] <text>", "type": "hash"}

        algos = {"md5", "sha1", "sha256", "sha512", "sha224", "sha384"}
        if len(parts) >= 3 and parts[1].lower() in algos:
            algo, text = parts[1].lower(), parts[2]
        else:
            algo, text = "sha256", " ".join(parts[1:])

        try:
            h = hashlib.new(algo, text.encode()).hexdigest()
            return {"success": True, "output": f"[{algo}] {h}", "type": "hash",
                    "data": {"algo": algo, "input": text, "hash": h}}
        except Exception as e:
            return {"success": False, "output": f"Hash error: {e}", "type": "hash"}

    @staticmethod
    async def base64_op(command: str) -> dict:
        """base64 encode|decode <data>"""
        parts = command.strip().split(None, 2)
        if len(parts) < 3:
            return {"success": False, "output": "Usage: base64 encode|decode <data>", "type": "base64"}
        mode = parts[1].lower()
        data = parts[2]
        try:
            if mode in ("encode", "enc", "e"):
                result = base64.b64encode(data.encode()).decode()
            elif mode in ("decode", "dec", "d"):
                result = base64.b64decode(data.encode()).decode("utf-8", errors="replace")
            else:
                return {"success": False, "output": f"Unknown mode '{mode}'. Use encode or decode.", "type": "base64"}
            return {"success": True, "output": result, "type": "base64",
                    "data": {"mode": mode, "result": result}}
        except Exception as e:
            return {"success": False, "output": f"Base64 error: {e}", "type": "base64"}

    @staticmethod
    async def convert(command: str) -> dict:
        """convert <value> <from_unit> <to_unit>"""
        parts = command.strip().split()
        if len(parts) < 4:
            return {"success": False, "output": "Usage: convert <value> <from> <to>", "type": "convert"}
        try:
            value = float(parts[1])
            from_unit, to_unit = parts[2], parts[3]
            result = convert_units(value, from_unit, to_unit)
            return {"success": True,
                    "output": f"{value} {from_unit} = {result:.6g} {to_unit}",
                    "type": "convert",
                    "data": {"value": value, "from": from_unit, "to": to_unit, "result": result}}
        except Exception as e:
            return {"success": False, "output": f"Conversion error: {e}", "type": "convert"}

    @staticmethod
    async def weather(command: str) -> dict:
        """weather <city> — uses wttr.in, no API key required."""
        parts = command.strip().split(None, 1)
        city = parts[1].strip() if len(parts) > 1 else "Berlin"
        city_encoded = urllib.parse.quote(city)
        url = f"https://wttr.in/{city_encoded}?format=3"
        try:
            import urllib.parse as _urlparse
            city_encoded = _urlparse.quote(city)
            url = f"https://wttr.in/{city_encoded}?format=3"
            req = urllib.request.Request(url, headers={"User-Agent": "BotBoy/0.6"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = resp.read().decode("utf-8").strip()
            return {"success": True, "output": result, "type": "weather",
                    "data": {"city": city, "raw": result}}
        except Exception as e:
            return {"success": False, "output": f"Weather unavailable: {e}", "type": "weather"}

    @staticmethod
    async def web_search(command: str) -> dict:
        """web search <query> — DuckDuckGo Instant Answer API."""
        import urllib.parse
        prefix_re = re.compile(r"^web\s+(search\s+)?", re.IGNORECASE)
        query = prefix_re.sub("", command).strip()
        if not query:
            return {"success": False, "output": "Usage: web search <query>", "type": "web"}
        try:
            encoded = urllib.parse.quote_plus(query)
            url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_redirect=1&no_html=1"
            req = urllib.request.Request(url, headers={"User-Agent": "BotBoy/0.6"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            abstract = data.get("AbstractText", "")
            if not abstract:
                related = data.get("RelatedTopics", [])
                if related and isinstance(related[0], dict):
                    abstract = related[0].get("Text", "No results found.")
            answer = data.get("Answer") or abstract or "No instant answer available."
            return {"success": True, "output": answer, "type": "web",
                    "data": {"query": query, "source": data.get("AbstractSource", "")}}
        except Exception as e:
            return {"success": False, "output": f"Web search unavailable: {e}", "type": "web"}

    @staticmethod
    async def file_search(command: str) -> dict:
        """file search <pattern> — glob-based, restricted to home dir."""
        prefix_re = re.compile(r"^file\s+(search\s+)?", re.IGNORECASE)
        pattern = prefix_re.sub("", command).strip()
        if not pattern:
            return {"success": False, "output": "Usage: file search <pattern>", "type": "file"}
        try:
            home = str(Path.home())
            safe_pattern = pattern.replace("..", "").replace("//", "/")
            search_path = os.path.join(home, "**", safe_pattern)
            matches = glob.glob(search_path, recursive=True)[:20]
            if matches:
                output = f"Found {len(matches)} file(s):\n" + "\n".join(matches)
            else:
                output = f"No files matching '{pattern}' found."
            return {"success": True, "output": output, "type": "file",
                    "data": {"pattern": pattern, "matches": matches}}
        except Exception as e:
            return {"success": False, "output": f"File search error: {e}", "type": "file"}

    @staticmethod
    async def system_info(command: str) -> dict:
        """system info — platform and runtime information."""
        try:
            info = {
                "os": platform.system(),
                "os_version": platform.version(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
            }
            output = (
                f"OS: {info['os']} {info['os_version']}\n"
                f"Machine: {info['machine']}\n"
                f"Python: {info['python']}\n"
                f"Hostname: {info['hostname']}\n"
                f"PID: {info['pid']}"
            )
            return {"success": True, "output": output, "type": "system", "data": info}
        except Exception as e:
            return {"success": False, "output": f"System info error: {e}", "type": "system"}

    @staticmethod
    async def time_date(command: str) -> dict:
        """time / date — current UTC and local time."""
        now_utc = datetime.now(timezone.utc)
        now_local = datetime.now()
        output = (
            f"UTC:   {now_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"Local: {now_local.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return {"success": True, "output": output, "type": "time",
                "data": {"utc": now_utc.isoformat(), "local": now_local.isoformat()}}


# ── Skill Manager ─────────────────────────────────────────────────────────────

_BUILTIN_TRIGGERS: Dict[str, str] = {
    "calculate": "calculator", "compute": "calculator", "math": "calculator", "calc": "calculator",
    "sqrt": "calculator", "sin": "calculator", "cos": "calculator", "log": "calculator",
    "hash": "hash_text", "md5": "hash_text", "sha256": "hash_text", "sha512": "hash_text",
    "base64": "base64_op",
    "convert": "convert",
    "weather": "weather",
    "web": "web_search",
    "file": "file_search",
    "system": "system_info",
    "time": "time_date", "date": "time_date",
}

_BUILTIN_CONTRACT_META: Dict[str, Dict[str, Any]] = {
    "calculator": {
        "name": "calculator",
        "version": "builtin",
        "description": "AST-safe calculator for mathematical expressions.",
        "triggers": ["calculate", "compute", "math", "calc", "sqrt", "sin", "cos", "log"],
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["math"],
        "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        "output_schema": {"type": "object", "properties": {"result": {}}},
    },
    "hash_text": {
        "name": "hash_text",
        "version": "builtin",
        "description": "Hash arbitrary text with common digest algorithms.",
        "triggers": ["hash", "md5", "sha256", "sha512"],
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["crypto"],
    },
    "base64_op": {
        "name": "base64_op",
        "version": "builtin",
        "description": "Encode and decode Base64 content.",
        "triggers": ["base64"],
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["encoding"],
    },
    "convert": {
        "name": "convert",
        "version": "builtin",
        "description": "Convert common units.",
        "triggers": ["convert"],
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["conversion"],
    },
    "weather": {
        "name": "weather",
        "version": "builtin",
        "description": "Fetch current weather via wttr.in.",
        "triggers": ["weather"],
        "security_level": "INTERMEDIATE",
        "runtime_tier": "subprocess",
        "capabilities": ["network", "weather"],
        "allow_network": True,
        "needs_approval": True,
    },
    "web_search": {
        "name": "web_search",
        "version": "builtin",
        "description": "Perform web search via DuckDuckGo Instant Answer API.",
        "triggers": ["web"],
        "security_level": "INTERMEDIATE",
        "runtime_tier": "subprocess",
        "capabilities": ["network", "search"],
        "allow_network": True,
        "needs_approval": True,
    },
    "file_search": {
        "name": "file_search",
        "version": "builtin",
        "description": "Search files within the user's home directory.",
        "triggers": ["file"],
        "security_level": "INTERMEDIATE",
        "runtime_tier": "subprocess",
        "capabilities": ["filesystem", "search"],
        "allow_filesystem": True,
        "needs_approval": True,
    },
    "system_info": {
        "name": "system_info",
        "version": "builtin",
        "description": "Inspect local system metadata.",
        "triggers": ["system"],
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["system-info"],
    },
    "time_date": {
        "name": "time_date",
        "version": "builtin",
        "description": "Return current UTC and local time.",
        "triggers": ["time", "date"],
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["time"],
    },
}

_BUILTIN_CONTRACTS: Dict[str, Skill] = {
    method_name: _build_skill_contract(meta, path=f"builtin:{method_name}", code="", trusted=True)
    for method_name, meta in _BUILTIN_CONTRACT_META.items()
}


class SkillManager:
    """Loads and routes to builtin + custom SKILL.md skills."""

    def __init__(self, skills_dir: Optional[str] = None, auto_discover: bool = True,
                 validate_security: bool = True) -> None:
        self.skills_dir = skills_dir
        self.auto_discover = auto_discover
        self.validate_security = validate_security
        self._skills: Dict[str, Skill] = {}
        self._builtins = BuiltinSkills()

    def load_all_skills(self) -> int:
        count = 0
        if self.auto_discover and self.skills_dir:
            expanded = str(Path(self.skills_dir).expanduser())
            pattern = os.path.join(expanded, "**", "SKILL.md")
            for path in glob.glob(pattern, recursive=True):
                skill = parse_skill_md(path)
                if skill:
                    if self.validate_security and _DANGEROUS_CODE_PATTERNS.search(skill.code):
                        # Warn but still load — the validator will block execution
                        pass
                    self._skills[skill.name] = skill
                    count += 1
        return count

    def list_skills(self) -> List[str]:
        builtins = list(set(_BUILTIN_TRIGGERS.values()))
        custom = list(self._skills.keys())
        return sorted(set(builtins + custom))

    def list_contracts(self) -> List[Dict[str, Any]]:
        contracts = [skill.to_contract() for skill in _BUILTIN_CONTRACTS.values()]
        contracts.extend(skill.to_contract() for skill in self._skills.values())
        return sorted(contracts, key=lambda item: item["name"])

    def find_skill_for_command(self, command: str) -> Optional[str]:
        """Return builtin method name or custom skill name, or None."""
        prefix = command.strip().lower().split()[0] if command.strip() else ""
        if prefix in _BUILTIN_TRIGGERS:
            return _BUILTIN_TRIGGERS[prefix]
        # Check custom skill triggers
        for skill in self._skills.values():
            if prefix in [t.lower() for t in skill.triggers]:
                return skill.name
        return None

    def get_contract_for_skill(self, skill_name: str) -> Optional[Dict[str, Any]]:
        if skill_name in _BUILTIN_CONTRACTS:
            return _BUILTIN_CONTRACTS[skill_name].to_contract()
        skill = self._skills.get(skill_name)
        return skill.to_contract() if skill else None

    def get_contract_for_command(self, command: str) -> Optional[Dict[str, Any]]:
        skill_name = self.find_skill_for_command(command)
        if not skill_name:
            return None
        return self.get_contract_for_skill(skill_name)

    def _build_custom_payload(self, skill: Skill, command: str) -> Dict[str, Any]:
        parts = command.strip().split(None, 1)
        args = parts[1].strip() if len(parts) > 1 else ""
        return {
            "command": command,
            "trigger": parts[0].strip().lower() if parts else "",
            "args": args,
            "text": args,
            "skill": skill.name,
        }

    @staticmethod
    def _format_custom_output(value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return "Skill completed."
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2, sort_keys=True)
        return str(value)

    async def execute_builtin(self, method_name: str, command: str) -> Optional[dict]:
        """Execute a builtin skill by method name."""
        method_map = {
            "calculator": self._builtins.calculator,
            "hash_text": self._builtins.hash_text,
            "base64_op": self._builtins.base64_op,
            "convert": self._builtins.convert,
            "weather": self._builtins.weather,
            "web_search": self._builtins.web_search,
            "file_search": self._builtins.file_search,
            "system_info": self._builtins.system_info,
            "time_date": self._builtins.time_date,
        }
        handler = method_map.get(method_name)
        if handler:
            return await handler(command)
        return None

    async def execute_custom(
        self,
        skill_name: str,
        command: str,
        *,
        principal: str = "anonymous",
        roles: Optional[List[str]] = None,
        approval_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[dict]:
        skill = self.get_custom_skill(skill_name)
        if not skill:
            return None

        from botboy.skills.runtime import SandboxConfig, get_runtime_by_name, resolve_runtime_policy

        approval_context = dict(approval_context or {})
        policy = resolve_runtime_policy(
            security_level=skill.security_level,
            runtime_tier=skill.runtime_tier,
            trusted=skill.trusted,
            allow_network=skill.allow_network,
            allow_filesystem=skill.allow_filesystem,
            needs_approval=skill.needs_approval,
            approval_granted=_coerce_bool(approval_context.get("granted"), default=False),
            roles=roles or [],
        )
        if not policy.allowed:
            return {
                "success": False,
                "output": policy.reason,
                "type": "skill",
                "data": {
                    "skill": skill.name,
                    "principal": principal,
                    "policy": policy.to_dict(),
                    "contract": skill.to_contract(),
                },
            }

        runtime = get_runtime_by_name(policy.runtime_name)
        payload = self._build_custom_payload(skill, command)
        result = runtime.execute(
            skill_name=skill.name,
            code=skill.code,
            payload=payload,
            config=SandboxConfig(
                allow_network=skill.allow_network,
                allow_filesystem=skill.allow_filesystem,
            ),
        )
        return {
            "success": result.success,
            "output": self._format_custom_output(result.output if result.success else result.error),
            "type": "skill",
            "data": {
                "skill": skill.name,
                "runtime_used": result.runtime_used,
                "duration_ms": result.duration_ms,
                "principal": principal,
                "contract": skill.to_contract(),
                "policy": policy.to_dict(),
                "result": result.output if result.success else None,
            },
        }

    def get_custom_skill(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)
