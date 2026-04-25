"""InputValidator — 4-mode input sanitisation with defence-in-depth."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


# ── Pattern lists ─────────────────────────────────────────────────────────────
# Each entry: (compiled_regex, human_readable_description)

_BLOCK_NORMAL: List[Tuple] = [
    # Code injection — execution primitives
    (re.compile(r"\beval\s*\(", re.I),                   "eval() injection"),
    (re.compile(r"\bexec\s*\(", re.I),                   "exec() injection"),
    (re.compile(r"__import__\s*\(", re.I),               "__import__() injection"),
    (re.compile(r"\bimportlib\b", re.I),                  "importlib injection"),
    (re.compile(r"\bcompile\s*\(", re.I),                 "compile() injection"),
    # Process execution
    (re.compile(r"\bsubprocess\b", re.I),                 "subprocess module"),
    (re.compile(r"\bos\.system\s*\(", re.I),              "os.system()"),
    (re.compile(r"\bos\.popen\s*\(", re.I),               "os.popen()"),
    (re.compile(r"\bos\.exec[vlpe]", re.I),               "os.exec*()"),
    (re.compile(r"\bos\.spawn", re.I),                    "os.spawn*()"),
    (re.compile(r"\bctypes\b", re.I),                     "ctypes"),
    # Filesystem writes — NOW BLOCKED IN NORMAL MODE
    (re.compile(r"open\s*\([^)]*['\"]w['\"]", re.I),     "open(write)"),
    (re.compile(r"open\s*\([^)]*['\"]a['\"]", re.I),     "open(append)"),
    (re.compile(r"open\s*\([^)]*['\"]x['\"]", re.I),     "open(exclusive)"),
    # Sensitive filesystem paths
    (re.compile(r"/etc/(passwd|shadow|sudoers|hosts)"),   "sensitive file"),
    (re.compile(r"/proc/\w"),                             "procfs access"),
    (re.compile(r"\.\./\.\./"),                           "path traversal"),
    # Destructive operations
    (re.compile(r"\bshutil\.rmtree\b", re.I),             "shutil.rmtree"),
    (re.compile(r"\brm\s+-rf\b", re.I),                   "rm -rf"),
    # SQL injection
    (re.compile(r";\s*(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE)\b", re.I),
                                                           "SQL DDL injection"),
    (re.compile(r"'\s*OR\s+'?1'?\s*=\s*'?1", re.I),      "SQL OR 1=1"),
    # XSS / HTML
    (re.compile(r"<script", re.I),                        "<script> tag"),
    (re.compile(r"javascript:", re.I),                    "javascript: URI"),
    (re.compile(r"on\w+\s*=\s*[\"']", re.I),             "event handler"),
    # Deserialization
    (re.compile(r"\bpickle\.(loads|load)\b", re.I),       "pickle deserialization"),
    # Network exfiltration
    (re.compile(r"\bcurl\s+-[^\s]*\s*https?://", re.I),   "curl exfil"),
    (re.compile(r"\bwget\s+https?://", re.I),             "wget download"),
]

_BLOCK_STRICT_EXTRA: List[Tuple] = [
    (re.compile(r"\bopen\s*\(", re.I),                    "open()"),
    (re.compile(r"\bimport\s+os\b", re.I),                "import os"),
    (re.compile(r"\bimport\s+sys\b", re.I),               "import sys"),
    (re.compile(r"\bgetattr\s*\(", re.I),                 "getattr()"),
    (re.compile(r"\bsetattr\s*\(", re.I),                 "setattr()"),
    (re.compile(r"__builtins__", re.I),                   "__builtins__"),
    (re.compile(r"globals\s*\(\)", re.I),                 "globals()"),
    (re.compile(r"locals\s*\(\)", re.I),                  "locals()"),
]

_BLOCK_LENIENT: List[Tuple] = [
    (re.compile(r"\beval\s*\(", re.I),                    "eval()"),
    (re.compile(r"\bexec\s*\(", re.I),                    "exec()"),
    (re.compile(r"__import__\s*\(",  re.I),               "__import__()"),
    (re.compile(r";\s*(DROP|DELETE)\b", re.I),            "SQL DROP/DELETE"),
    (re.compile(r"<script", re.I),                        "<script>"),
]

_MAX_COMMAND_LENGTH = 4096
_MAX_MEMORY_LENGTH  = 65536


@dataclass
class ValidationResult:
    valid:     bool
    sanitized: str
    errors:    List[str]
    warnings:  List[str]


class InputValidator:
    """
    Multi-mode input validator with layered pattern matching.

    STRICT      — maximum restrictions; blocks ALL suspicious patterns
    NORMAL      — blocks all provably dangerous patterns (default)
    LENIENT     — blocks only critical injection patterns
    PASSTHROUGH — no restrictions (internal / tests only)
    """

    STRICT      = "strict"
    NORMAL      = "normal"
    LENIENT     = "lenient"
    PASSTHROUGH = "passthrough"

    def __init__(self, mode: str = NORMAL) -> None:
        self.mode = mode

    def validate_command(self, command: str) -> ValidationResult:
        errors:   List[str] = []
        warnings: List[str] = []

        if self.mode == self.PASSTHROUGH:
            return ValidationResult(True, command, [], [])

        if len(command) > _MAX_COMMAND_LENGTH:
            return ValidationResult(
                False, "",
                [f"Command too long ({len(command)} > {_MAX_COMMAND_LENGTH})"],
                [])

        sanitized = command.strip()

        if "\x00" in sanitized:
            return ValidationResult(False, "", ["Null byte detected"], [])

        patterns_to_check: List[Tuple] = []
        if self.mode == self.STRICT:
            patterns_to_check = _BLOCK_NORMAL + _BLOCK_STRICT_EXTRA
        elif self.mode == self.NORMAL:
            patterns_to_check = _BLOCK_NORMAL
        elif self.mode == self.LENIENT:
            patterns_to_check = _BLOCK_LENIENT

        for pattern, description in patterns_to_check:
            if pattern.search(sanitized):
                errors.append(f"Blocked: {description}")
                return ValidationResult(False, "", errors, warnings)

        return ValidationResult(True, sanitized, [], [])

    def validate_memory_content(self, content: str) -> ValidationResult:
        if not content or not content.strip():
            return ValidationResult(False, "", ["Empty content"], [])
        if len(content) > _MAX_MEMORY_LENGTH:
            return ValidationResult(
                False, "",
                [f"Content too long ({len(content)} > {_MAX_MEMORY_LENGTH})"],
                [])
        return ValidationResult(True, content.strip(), [], [])

    def sanitize_for_sql(self, value: str) -> str:
        return value.replace("'", "''").replace(";", "")

    def sanitize_for_html(self, value: str) -> str:
        return (value.replace("&", "&amp;")
                     .replace("<", "&lt;")
                     .replace(">", "&gt;")
                     .replace('"', "&quot;")
                     .replace("'", "&#39;"))
