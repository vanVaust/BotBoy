"""BotBoy eval / replay runner.

This module intentionally stays side-effect free on import. It can load an
eval manifest plus replay seeds, run them against a local BotBoy instance, and
return a structured report.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time
from collections import Counter
from datetime import datetime, timezone
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.simple_server import SimpleHTTPServer
from botboy.resources import (
    bundled_eval_manifest_path,
    bundled_eval_seed_path,
    bundled_skills_dir,
)
from botboy.scheduler import ScheduledTask


@dataclass(frozen=True)
class EvalCase:
    id: str
    kind: str
    priority: str = "medium"
    command: str = ""
    request: str = ""
    expected_contains: List[str] = field(default_factory=list)
    expected: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReplaySeed:
    id: str
    case_id: str
    kind: str
    principal: str
    request_id: str
    input: str
    request_headers: Dict[str, Any] = field(default_factory=dict)
    request_body: Dict[str, Any] = field(default_factory=dict)
    expected_contains: List[str] = field(default_factory=list)
    expected_status: Optional[int] = None
    expected_fields: List[str] = field(default_factory=list)
    expected_status_on_repeat: Optional[int] = None
    expected_retry_after_positive: bool = False
    notes: str = ""


@dataclass(frozen=True)
class WaveIntegrityIssue:
    code: str
    message: str
    severity: str = "error"
    case_id: str = ""
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "case_id": self.case_id,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass
class EvalCheck:
    name: str
    passed: bool
    expected: Any = None
    actual: Any = None


@dataclass
class EvalCaseResult:
    case_id: str
    kind: str
    passed: bool
    checks: List[EvalCheck] = field(default_factory=list)
    observed: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "kind": self.kind,
            "passed": self.passed,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "expected": check.expected,
                    "actual": check.actual,
                }
                for check in self.checks
            ],
            "observed": self.observed,
            "error": self.error,
        }


@dataclass
class EvalRunResult:
    manifest_name: str
    wave: str
    total: int
    passed: int
    failed: int
    skipped: int
    cases: List[EvalCaseResult] = field(default_factory=list)
    duration_ms: float = 0.0
    manifest_path: str = ""
    seed_path: str = ""
    manifest_case_count: int = 0
    replay_seed_count: int = 0
    matched_seed_count: int = 0
    integrity_issues: List[WaveIntegrityIssue] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_name": self.manifest_name,
            "wave": self.wave,
            "manifest_path": self.manifest_path,
            "seed_path": self.seed_path,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_ms": self.duration_ms,
            "summary": self.summary(),
            "integrity_issues": [issue.to_dict() for issue in self.integrity_issues],
            "cases": [case.to_dict() for case in self.cases],
        }

    def summary(self) -> Dict[str, Any]:
        case_kinds = Counter(case.kind for case in self.cases)
        failed_cases = [case.case_id for case in self.cases if not case.passed]
        manifest_case_count = self.manifest_case_count or len(self.cases)
        replay_seed_count = self.replay_seed_count
        matched_seed_count = self.matched_seed_count
        return {
            "integrity_ok": not self.integrity_issues,
            "integrity_issue_count": len(self.integrity_issues),
            "integrity_issues": [issue.to_dict() for issue in self.integrity_issues],
            "case_kinds": dict(sorted(case_kinds.items())),
            "failed_cases": failed_cases,
            "pass_rate": round(self.passed / self.total, 3) if self.total else 0.0,
            "manifest_case_count": manifest_case_count,
            "replay_seed_count": replay_seed_count,
            "matched_seed_count": matched_seed_count,
            "manifest_coverage": round(matched_seed_count / manifest_case_count, 3) if manifest_case_count else 0.0,
            "replay_coverage": round(matched_seed_count / replay_seed_count, 3) if replay_seed_count else 0.0,
        }

    def render(self) -> str:
        summary = self.summary()
        integrity_label = "OK" if summary["integrity_ok"] else f"WARN ({summary['integrity_issue_count']} issues)"
        lines = [
            f"Wave: {self.wave}",
            f"Manifest: {self.manifest_name}",
            f"Paths: manifest={self.manifest_path or '-'} seed={self.seed_path or '-'}",
            f"Total: {self.total}  Passed: {self.passed}  Failed: {self.failed}  Skipped: {self.skipped}",
            f"Integrity: {integrity_label}",
            f"Replay coverage: {summary['matched_seed_count']}/{summary['manifest_case_count']} manifest cases, {summary['matched_seed_count']}/{summary['replay_seed_count']} seeds",
            f"Pass rate: {summary['pass_rate']:.3f}",
        ]
        if summary["case_kinds"]:
            kinds = ", ".join(f"{kind}={count}" for kind, count in summary["case_kinds"].items())
            lines.append(f"Case kinds: {kinds}")
        if self.integrity_issues:
            for issue in self.integrity_issues[:5]:
                details = ""
                if issue.expected is not None:
                    details += f" expected={issue.expected!r}"
                if issue.actual is not None:
                    details += f" actual={issue.actual!r}"
                lines.append(f"- integrity[{issue.severity}] {issue.code}: {issue.message}{details}")
        for case in self.cases:
            status = "PASS" if case.passed else "FAIL"
            lines.append(f"- {case.case_id} [{case.kind}] {status}")
            for check in case.checks:
                mark = "ok" if check.passed else "x"
                if check.passed:
                    lines.append(f"  - {check.name}: {mark}")
                else:
                    lines.append(f"  - {check.name}: {mark} expected={check.expected!r} actual={check.actual!r}")
        return "\n".join(lines)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_manifest_path() -> Path:
    bundled = bundled_eval_manifest_path("release_smoke")
    if bundled.exists():
        return bundled
    return _repo_root() / "tests" / "evals" / "wave_1_manifest.json"


def default_seed_path() -> Path:
    bundled = bundled_eval_seed_path("release_smoke")
    if bundled.exists():
        return bundled
    return _repo_root() / "tests" / "evals" / "replays" / "wave_1_seed.jsonl"


def _normalise_wave(wave: str) -> str:
    token = str(wave).strip().lower().replace("-", "_").replace(" ", "_")
    collapsed = token.replace("_", "")
    match = re.fullmatch(r"(?:wave|welle)?(\d+)", collapsed)
    if match:
        return f"wave_{int(match.group(1))}"
    return token or "wave_1"


def manifest_path_for_wave(wave: str) -> Path:
    wave_id = _normalise_wave(wave)
    bundled = bundled_eval_manifest_path(wave_id)
    if bundled.exists():
        return bundled
    return _repo_root() / "tests" / "evals" / f"{wave_id}_manifest.json"


def seed_path_for_wave(wave: str) -> Path:
    wave_id = _normalise_wave(wave)
    bundled = bundled_eval_seed_path(wave_id)
    if bundled.exists():
        return bundled
    return _repo_root() / "tests" / "evals" / "replays" / f"{wave_id}_seed.jsonl"


def _default_eval_runtime_root() -> Path:
    override = str(os.getenv("BOTBOY_EVAL_RUNTIME_ROOT", "") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    session = str(os.getenv("BOTBOY_EVAL_SESSION", "") or "").strip()
    if not session:
        session = f"session-{os.getpid()}"
    return (_repo_root() / ".codex_eval_runtime" / session).resolve()


def _build_eval_runtime_dir(case_id: str) -> Path:
    runtime_root = _default_eval_runtime_root()
    runtime_root.mkdir(parents=True, exist_ok=True)
    return runtime_root


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_replay_seeds(path: str | Path) -> List[ReplaySeed]:
    seeds: List[ReplaySeed] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        seeds.append(
            ReplaySeed(
                id=str(payload["id"]),
                case_id=str(payload["case_id"]),
                kind=str(payload["kind"]),
                principal=str(payload.get("principal", "")),
                request_id=str(payload.get("request_id", "")),
                input=str(payload.get("input", "")),
                request_headers=dict(payload.get("request_headers") or {}),
                request_body=dict(payload.get("request_body") or {}),
                expected_contains=list(payload.get("expected_contains", [])),
                expected_status=payload.get("expected_status"),
                expected_fields=list(payload.get("expected_fields", [])),
                expected_status_on_repeat=payload.get("expected_status_on_repeat"),
                expected_retry_after_positive=bool(payload.get("expected_retry_after_positive", False)),
                notes=str(payload.get("notes", "")),
            )
        )
    return seeds


def save_report_json(path: str | Path, result: EvalRunResult) -> Path:
    """Persist a structured eval report as JSON."""
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_path


def _http_request(
    url: str,
    method: str,
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> tuple[int, dict]:
    data = None
    if payload is not None and method.upper() not in {"GET", "DELETE"}:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method.upper())
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8") if exc.fp else ""
            try:
                parsed = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed = {"raw": body}
            parsed.setdefault("status", exc.code)
            return exc.code, parsed
        except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt == 2:
                raise
            time.sleep(0.15 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("HTTP request failed without an exception")


def _json_request(url: str, payload: dict, headers: Optional[dict] = None) -> tuple[int, dict]:
    return _http_request(url, "POST", payload, headers)


def _get_json(url: str, headers: Optional[dict] = None) -> tuple[int, dict]:
    return _http_request(url, "GET", None, headers)


def _parse_http_request(request: str) -> tuple[str, str]:
    text = str(request or "").strip()
    if not text:
        return "GET", "/api/command"
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return parts[0].upper(), parts[1].strip()
    if text.startswith("/"):
        return "GET", text
    return "POST", "/api/command"


def _build_eval_config(case_id: str) -> BotBoyConfig:
    config = BotBoyConfig()
    config.skills.directory = str(bundled_skills_dir())
    config.security.enable_auth = False
    config.security.rate_limit_enabled = True
    config.security.rate_limit_requests = 120
    config.skills.auto_discover = True
    config.skills.validate_security = True
    config.memory.db_path = ":memory:"
    config.history.db_path = ":memory:"
    config.scheduler.db_path = ":memory:"
    config.trace.db_path = ":memory:"
    task_root = _build_eval_runtime_dir(case_id)
    safe_case_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(case_id or "eval-case")).strip("-") or "eval-case"
    task_token = f"{safe_case_id}-{time.time_ns()}-{uuid4().hex[:8]}"
    task_dir = (task_root / task_token).resolve()
    config.tasks.enabled = True
    config.tasks.db_path = str(task_dir / "tasks.db")
    config.tasks.artifact_root = str(task_dir / "artifacts" / "tasks")
    if case_id == "rate_limit_smoke":
        config.security.rate_limit_requests = 1
    return config


_MISSING = object()
_TEMPLATE_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _lookup_path(payload: Any, path: str) -> Any:
    if not path:
        return _MISSING
    current = payload
    for segment in str(path).split("."):
        if isinstance(current, dict):
            if segment not in current:
                return _MISSING
            current = current[segment]
            continue
        if isinstance(current, list):
            if not segment.isdigit():
                return _MISSING
            index = int(segment)
            if index < 0 or index >= len(current):
                return _MISSING
            current = current[index]
            continue
        return _MISSING
    return current


def _resolve_template_string(template: str, context: Any) -> Any:
    stripped = template.strip()
    full_match = _TEMPLATE_PATTERN.fullmatch(stripped)
    if full_match:
        value = _lookup_path(context, full_match.group(1).strip())
        return None if value is _MISSING else value

    def repl(match: re.Match[str]) -> str:
        value = _lookup_path(context, match.group(1).strip())
        if value is _MISSING or value is None:
            return ""
        return str(value)

    return _TEMPLATE_PATTERN.sub(repl, template)


def _resolve_template_value(value: Any, context: Any) -> Any:
    if isinstance(value, str):
        return _resolve_template_string(value, context)
    if isinstance(value, list):
        return [_resolve_template_value(item, context) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve_template_value(item, context) for item in value)
    if isinstance(value, dict):
        return {key: _resolve_template_value(item, context) for key, item in value.items()}
    return value


def _add_contains_checks(checks: List[EvalCheck], text: str, tokens: Iterable[Any], label: str) -> None:
    for token in tokens:
        token_text = str(token)
        checks.append(
            EvalCheck(
                name=f"{label}.contains:{token_text}",
                passed=token_text.lower() in text.lower(),
                expected=token_text,
                actual=text,
            )
        )


def _add_path_checks(
    checks: List[EvalCheck],
    payload: Any,
    paths: Iterable[Any],
    label: str,
) -> None:
    for path in paths:
        path_text = str(path)
        value = _lookup_path(payload, path_text)
        checks.append(
            EvalCheck(
                name=f"{label}.path:{path_text}",
                passed=value is not _MISSING,
                expected=True,
                actual=None if value is _MISSING else value,
            )
        )


def _add_value_checks(
    checks: List[EvalCheck],
    payload: Any,
    mapping: dict[str, Any],
    label: str,
) -> None:
    for path, expected in mapping.items():
        path_text = str(path)
        actual = _lookup_path(payload, path_text)
        checks.append(
            EvalCheck(
                name=f"{label}.value:{path_text}",
                passed=actual is not _MISSING and actual == expected,
                expected=expected,
                actual=None if actual is _MISSING else actual,
            )
        )


def _add_min_value_checks(
    checks: List[EvalCheck],
    payload: Any,
    mapping: dict[str, Any],
    label: str,
) -> None:
    for path, minimum in mapping.items():
        path_text = str(path)
        actual = _lookup_path(payload, path_text)
        numeric = actual if isinstance(actual, (int, float)) else None
        checks.append(
            EvalCheck(
                name=f"{label}.min:{path_text}",
                passed=numeric is not None and numeric >= minimum,
                expected=minimum,
                actual=numeric,
            )
        )


def _command_kwargs(case: EvalCase, seed: ReplaySeed) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "principal": seed.principal,
        "request_id": seed.request_id,
    }
    approval_context = case.expected.get("approval_context")
    if isinstance(approval_context, dict):
        kwargs["approval_context"] = dict(approval_context)
    return kwargs


class EvalReplayRunner:
    def __init__(self, manifest_path: str | Path, seed_path: str | Path) -> None:
        self.manifest_path = Path(manifest_path)
        self.seed_path = Path(seed_path)
        manifest = load_manifest(self.manifest_path)
        self.manifest_name = str(manifest.get("name", self.manifest_path.stem))
        self.wave = str(manifest.get("wave", "unknown"))
        self._cases = {
            case["id"]: EvalCase(
                id=str(case["id"]),
                kind=str(case["kind"]),
                priority=str(case.get("priority", "medium")),
                command=str(case.get("command", "")),
                request=str(case.get("request", "")),
                expected_contains=list(case.get("expected_contains", [])),
                expected=dict(case.get("expected", {})),
            )
            for case in manifest.get("cases", [])
        }
        self._seeds = load_replay_seeds(self.seed_path)
        self._integrity_issues = self.validate_wave_integrity()

    @classmethod
    def from_defaults(cls) -> "EvalReplayRunner":
        return cls(default_manifest_path(), default_seed_path())

    @classmethod
    def for_wave(cls, wave: str) -> "EvalReplayRunner":
        return cls(manifest_path_for_wave(wave), seed_path_for_wave(wave))

    def _make_bot(self, case_id: str) -> BotBoy:
        config = _build_eval_config(case_id)
        runtime_root = _build_eval_runtime_dir(case_id)
        config.tasks.db_path = ":memory:"
        config.tasks.artifact_root = str((runtime_root / "artifacts" / "tasks").resolve())
        bot = BotBoy(config)
        if not bot.initialize():
            raise RuntimeError(f"BotBoy init failed for eval case {case_id}")
        return bot

    def _wave_number(self) -> int:
        wave_id = _normalise_wave(self.wave or self.manifest_name)
        match = re.search(r"(\d+)$", wave_id)
        return int(match.group(1)) if match else 0

    def _shared_runtime_enabled(self) -> bool:
        return self._wave_number() >= 16

    def validate_wave_integrity(self) -> List[WaveIntegrityIssue]:
        issues: List[WaveIntegrityIssue] = []
        if not self._cases:
            issues.append(
                WaveIntegrityIssue(
                    code="manifest.empty",
                    message="Manifest contains no eval cases",
                )
            )
            return issues

        manifest_case_ids = list(self._cases.keys())
        seed_case_ids = [seed.case_id for seed in self._seeds]

        for case_id, count in Counter(manifest_case_ids).items():
            if count > 1:
                issues.append(
                    WaveIntegrityIssue(
                        code="manifest.duplicate_case",
                        message="Manifest contains duplicate case ids",
                        case_id=case_id,
                    )
                )

        for case_id, count in Counter(seed_case_ids).items():
            if count > 1:
                issues.append(
                    WaveIntegrityIssue(
                        code="seed.duplicate_case",
                        message="Replay seeds contain duplicate case ids",
                        case_id=case_id,
                    )
                )

        seed_case_map = {seed.case_id: seed for seed in self._seeds}
        for case_id, case in self._cases.items():
            seed = seed_case_map.get(case_id)
            if seed is None:
                issues.append(
                    WaveIntegrityIssue(
                        code="seed.missing",
                        message="Manifest case has no replay seed",
                        case_id=case_id,
                        expected=case.kind,
                    )
                )
                continue
            if str(seed.kind).lower() != str(case.kind).lower():
                issues.append(
                    WaveIntegrityIssue(
                        code="seed.kind_mismatch",
                        message="Replay seed kind does not match manifest case kind",
                        case_id=case_id,
                        expected=case.kind,
                        actual=seed.kind,
                    )
                )

        for seed in self._seeds:
            if seed.case_id not in self._cases:
                issues.append(
                    WaveIntegrityIssue(
                        code="manifest.extra_seed",
                        message="Replay seed has no matching manifest case",
                        case_id=seed.case_id,
                        expected="manifest case",
                        actual=seed.kind,
                    )
                )

        return issues

    def _evaluate_structured_result(
        self,
        case: EvalCase,
        seed: ReplaySeed,
        result: dict,
        command: str,
        observed: dict,
        error_prefix: str,
    ) -> EvalCaseResult:
        output = str(result.get("output", ""))
        checks: List[EvalCheck] = []
        _add_contains_checks(checks, output, seed.expected_contains, "seed")
        _add_contains_checks(checks, output, case.expected_contains, "manifest")
        _add_contains_checks(checks, output, case.expected.get("output_contains", []), "expected")
        _add_contains_checks(checks, output, case.expected.get("contains", []), "expected")
        _add_path_checks(checks, result, case.expected.get("result_fields", []), "result")
        _add_value_checks(checks, result, case.expected.get("field_values", {}), "result")

        data = result.get("data", {})
        _add_path_checks(checks, data, case.expected.get("data_fields", []), "data")
        _add_value_checks(checks, data, case.expected.get("data_field_values", {}), "data")
        _add_min_value_checks(checks, data, case.expected.get("data_min_values", {}), "data")
        _add_min_value_checks(checks, result, case.expected.get("result_min_values", {}), "result")

        expected_success = bool(case.expected.get("result_success", True))
        actual_success = bool(result.get("success", False))
        checks.insert(
            0,
            EvalCheck(
                name="result.success",
                passed=actual_success == expected_success,
                expected=expected_success,
                actual=actual_success,
            ),
        )
        passed = all(check.passed for check in checks)
        error = "" if passed else f"{error_prefix} case '{seed.case_id}' did not satisfy expected checks"
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed={**observed, "command": command, "output": output, "result": result},
            error=error,
        )

    def _run_command_case(self, bot: BotBoy, case: EvalCase, seed: ReplaySeed) -> EvalCaseResult:
        command = seed.input or case.command
        result = asyncio.run(bot.process_command(command, **_command_kwargs(case, seed)))
        return self._evaluate_structured_result(
            case,
            seed,
            result,
            command,
            {"source": "process_command"},
            "Command",
        )

    def _run_http_command_case(self, base_url: str, case: EvalCase, seed: ReplaySeed) -> EvalCaseResult:
        payload = dict(seed.request_body or {})
        payload.setdefault("command", seed.input or case.command)
        headers = dict(seed.request_headers or {})
        if seed.request_id:
            headers.setdefault("X-Request-ID", seed.request_id)
        if seed.principal:
            headers.setdefault("X-Forwarded-For", seed.principal)
        status, body = _json_request(f"{base_url}/api/command", payload, headers=headers)
        result = body if isinstance(body, dict) else {"raw": body}
        expected_status = seed.expected_status if seed.expected_status is not None else 200
        if "http_status" in case.expected:
            expected_status = int(case.expected["http_status"])
        status_checks = [
            EvalCheck(
                name="http.status",
                passed=status == expected_status,
                expected=expected_status,
                actual=status,
            )
        ]
        structured = self._evaluate_structured_result(
            case,
            seed,
            result,
            payload["command"],
            {
                "status": status,
                "http_body": body,
                "request_headers": headers,
                "request_body": payload,
            },
            "HTTP command",
        )
        structured.checks = status_checks + structured.checks
        structured.passed = structured.passed and all(check.passed for check in status_checks)
        if not structured.passed and not structured.error:
            structured.error = f"HTTP command case '{seed.case_id}' did not satisfy expected checks"
        return structured

    def _run_http_path_case(self, base_url: str, case: EvalCase, seed: ReplaySeed) -> EvalCaseResult:
        request_text = str(case.request or "").strip()
        method, path = _parse_http_request(request_text)
        if not request_text and seed.input:
            method, path = _parse_http_request(str(seed.input))
        if not path:
            path = "/api/command"
        payload = dict(seed.request_body or {})
        headers = dict(seed.request_headers or {})
        if seed.request_id:
            headers.setdefault("X-Request-ID", seed.request_id)
        if seed.principal:
            headers.setdefault("X-Forwarded-For", seed.principal)
        body_payload = payload if method not in {"GET", "DELETE"} else None
        status, body = _http_request(f"{base_url}{path}", method, body_payload, headers=headers)
        result = dict(body) if isinstance(body, dict) else {"raw": body}
        result.setdefault("status", status)
        result.setdefault("success", 200 <= status < 300)
        if "output" not in result:
            if isinstance(body, (dict, list)):
                result["output"] = json.dumps(body, sort_keys=True)
            else:
                result["output"] = str(body)
        expected_status = seed.expected_status if seed.expected_status is not None else int(case.expected.get("http_status", 200))
        if "http_status" in case.expected:
            expected_status = int(case.expected["http_status"])
        status_checks = [
            EvalCheck(
                name="http.status",
                passed=status == expected_status,
                expected=expected_status,
                actual=status,
            )
        ]
        structured = self._evaluate_structured_result(
            case,
            seed,
            result,
            request_text or f"{method} {path}",
            {
                "status": status,
                "http_body": body,
                "request_headers": headers,
                "request_body": payload,
                "request_method": method,
                "request_path": path,
            },
            "HTTP path",
        )
        structured.checks = status_checks + structured.checks
        structured.passed = structured.passed and all(check.passed for check in status_checks)
        if not structured.passed and not structured.error:
            structured.error = f"HTTP path case '{seed.case_id}' did not satisfy expected checks"
        return structured

    def _run_mcp_case(self, case: EvalCase, seed: ReplaySeed) -> EvalCaseResult:
        from botboy_mcp_server import _handle_tool, _tools_with_contracts

        request = str(case.request or "").strip().lower()
        checks: List[EvalCheck] = []

        if "list" in request:
            tools = _tools_with_contracts()
            payload = {
                "success": True,
                "count": len(tools),
                "tools": tools,
                "tools_by_name": {tool["name"]: tool for tool in tools},
            }
            text = json.dumps(payload, sort_keys=True)
        elif "call" in request:
            tool_name = str(seed.request_body.get("name") or case.expected.get("tool_name") or "")
            tool_args = dict(seed.request_body.get("arguments") or case.expected.get("tool_args") or {})
            raw = _handle_tool(tool_name, tool_args)
            content = raw.get("content", []) if isinstance(raw, dict) else []
            text = ""
            if content and isinstance(content, list):
                text = str(content[0].get("text", ""))
            try:
                parsed = json.loads(text) if text else {}
            except json.JSONDecodeError:
                parsed = {"raw_text": text}
            payload = {
                "success": not bool(raw.get("isError", False)) if isinstance(raw, dict) else False,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "response": raw,
                "parsed": parsed,
            }
        else:
            return EvalCaseResult(
                case_id=seed.case_id,
                kind=seed.kind,
                passed=False,
                error=f"Unsupported MCP request for case '{seed.case_id}': {case.request}",
            )

        _add_contains_checks(checks, text, seed.expected_contains, "seed")
        _add_contains_checks(checks, text, case.expected_contains, "manifest")
        _add_contains_checks(checks, text, case.expected.get("output_contains", []), "expected")
        _add_path_checks(checks, payload, case.expected.get("required_paths", []), "mcp")
        _add_value_checks(checks, payload, case.expected.get("field_values", {}), "mcp")
        _add_min_value_checks(checks, payload, case.expected.get("min_values", {}), "mcp")
        expected_success = bool(case.expected.get("result_success", True))
        actual_success = bool(payload.get("success", False))
        checks.insert(
            0,
            EvalCheck(
                name="mcp.success",
                passed=actual_success == expected_success,
                expected=expected_success,
                actual=actual_success,
            ),
        )
        passed = all(check.passed for check in checks)
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed={"request": case.request, "payload": payload, "text": text},
            error="" if passed else f"MCP case '{seed.case_id}' did not satisfy expected checks",
        )

    def _run_status_case(self, bot: BotBoy, seed: ReplaySeed) -> EvalCaseResult:
        case = self._cases.get(seed.case_id, EvalCase(id=seed.case_id, kind=seed.kind))
        return self._run_command_case(bot, case, seed)

    def _run_contract_case(self, bot: BotBoy, seed: ReplaySeed) -> EvalCaseResult:
        case = self._cases.get(seed.case_id, EvalCase(id=seed.case_id, kind=seed.kind))
        return self._run_command_case(bot, case, seed)

    def _run_dashboard_case(self, bot: BotBoy, case: EvalCase, seed: ReplaySeed) -> EvalCaseResult:
        command = seed.input or case.command or "status"
        status_result = asyncio.run(bot.process_command(command, **_command_kwargs(case, seed)))
        dashboard = bot.get_dashboard_payload(mode="eval")
        resolved_expected = _resolve_template_value(
            case.expected,
            {
                "status": status_result,
                "dashboard": dashboard,
                **(dashboard if isinstance(dashboard, dict) else {}),
            },
        )
        checks: List[EvalCheck] = []
        _add_contains_checks(checks, str(status_result.get("output", "")), seed.expected_contains, "seed")
        _add_contains_checks(checks, str(status_result.get("output", "")), case.expected_contains, "manifest")
        _add_path_checks(checks, dashboard, resolved_expected.get("required_paths", []), "dashboard")
        _add_value_checks(checks, dashboard, resolved_expected.get("field_values", {}), "dashboard")
        _add_min_value_checks(checks, dashboard, resolved_expected.get("min_values", {}), "dashboard")

        top_level_keys = resolved_expected.get("top_level_keys", [])
        for key in top_level_keys:
            checks.append(
                EvalCheck(
                    name=f"dashboard.key:{key}",
                    passed=key in dashboard,
                    expected=True,
                    actual=key in dashboard,
                )
            )

        history = dashboard.get("history", {})
        traces = dashboard.get("traces", {})
        if "min_history_total" in resolved_expected:
            checks.append(
                EvalCheck(
                    name="dashboard.min_history_total",
                    passed=isinstance(history, dict) and history.get("total", 0) >= resolved_expected["min_history_total"],
                    expected=resolved_expected["min_history_total"],
                    actual=history.get("total") if isinstance(history, dict) else None,
                )
            )
        if "min_trace_runs" in resolved_expected:
            checks.append(
                EvalCheck(
                    name="dashboard.min_trace_runs",
                    passed=isinstance(traces, dict) and traces.get("total_runs", 0) >= resolved_expected["min_trace_runs"],
                    expected=resolved_expected["min_trace_runs"],
                    actual=traces.get("total_runs") if isinstance(traces, dict) else None,
                )
            )
        if "min_trace_spans" in resolved_expected:
            checks.append(
                EvalCheck(
                    name="dashboard.min_trace_spans",
                    passed=isinstance(traces, dict) and traces.get("total_spans", 0) >= resolved_expected["min_trace_spans"],
                    expected=resolved_expected["min_trace_spans"],
                    actual=traces.get("total_spans") if isinstance(traces, dict) else None,
                )
            )

        expected_success = bool(resolved_expected.get("result_success", True))
        passed = bool(status_result.get("success", False)) == expected_success and all(check.passed for check in checks)
        error = "" if passed else f"Dashboard case '{seed.case_id}' did not satisfy expected checks"
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed={
                "command": command,
                "status_result": status_result,
                "dashboard": dashboard,
            },
            error=error,
        )

    def _run_trace_case(self, bot: BotBoy, case: EvalCase, seed: ReplaySeed) -> EvalCaseResult:
        command = seed.input or case.command or "status"
        status_result = asyncio.run(bot.process_command(command, **_command_kwargs(case, seed)))
        trace_info = status_result.get("data", {}).get("trace", {})
        run_id = str(trace_info.get("run_id", ""))
        trace_detail = bot.trace_store.get_run(run_id) if bot.trace_store and run_id else None
        checks: List[EvalCheck] = []
        _add_contains_checks(checks, str(status_result.get("output", "")), seed.expected_contains, "seed")
        _add_contains_checks(checks, str(status_result.get("output", "")), case.expected_contains, "manifest")

        checks.append(
            EvalCheck(
                name="trace.run_id_present",
                passed=bool(run_id),
                expected=True,
                actual=bool(run_id),
            )
        )
        checks.append(
            EvalCheck(
                name="trace.detail_present",
                passed=trace_detail is not None,
                expected=True,
                actual=trace_detail is not None,
            )
        )

        if trace_detail:
            _add_path_checks(checks, trace_detail, case.expected.get("required_paths", []), "trace")
            _add_value_checks(checks, trace_detail, case.expected.get("field_values", {}), "trace")
            _add_min_value_checks(checks, trace_detail, case.expected.get("min_values", {}), "trace")
            if "min_spans" in case.expected:
                spans = trace_detail.get("spans", [])
                checks.append(
                    EvalCheck(
                        name="trace.min_spans",
                        passed=len(spans) >= case.expected["min_spans"],
                        expected=case.expected["min_spans"],
                        actual=len(spans),
                    )
                )
        expected_success = bool(case.expected.get("result_success", True))
        passed = bool(status_result.get("success", False)) == expected_success and all(check.passed for check in checks)
        error = "" if passed else f"Trace case '{seed.case_id}' did not satisfy expected checks"
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed={
                "command": command,
                "status_result": status_result,
                "trace": trace_detail,
            },
            error=error,
        )

    def _run_history_case(self, bot: BotBoy, case: EvalCase, seed: ReplaySeed) -> EvalCaseResult:
        command = seed.input or case.command or "status"
        seed_result = asyncio.run(bot.process_command(command, **_command_kwargs(case, seed)))
        stats_result = asyncio.run(bot.process_command("history stats", **_command_kwargs(case, seed)))
        stats_payload = bot.history.stats() if bot.history else {}
        list_by_request = bot.history.list(limit=10, request_id=seed.request_id) if bot.history else ([], 0)
        list_by_principal = bot.history.list(limit=10, principal=seed.principal) if bot.history else ([], 0)
        checks: List[EvalCheck] = []
        _add_contains_checks(checks, str(stats_result.get("output", "")), seed.expected_contains, "seed")
        _add_contains_checks(checks, str(stats_result.get("output", "")), case.expected_contains, "manifest")
        _add_contains_checks(checks, str(stats_result.get("output", "")), case.expected.get("output_contains", []), "expected")
        _add_path_checks(checks, stats_payload, case.expected.get("required_paths", []), "history")
        _add_value_checks(checks, stats_payload, case.expected.get("field_values", {}), "history")
        _add_min_value_checks(checks, stats_payload, case.expected.get("min_values", {}), "history")

        if "min_total" in case.expected:
            checks.append(
                EvalCheck(
                    name="history.min_total",
                    passed=stats_payload.get("total", 0) >= case.expected["min_total"],
                    expected=case.expected["min_total"],
                    actual=stats_payload.get("total"),
                )
            )
        if "min_success_count" in case.expected:
            checks.append(
                EvalCheck(
                    name="history.min_success_count",
                    passed=stats_payload.get("success_count", 0) >= case.expected["min_success_count"],
                    expected=case.expected["min_success_count"],
                    actual=stats_payload.get("success_count"),
                )
            )
        if "principal_min_hits" in case.expected:
            checks.append(
                EvalCheck(
                    name="history.principal_min_hits",
                    passed=stats_payload.get("by_principal", {}).get(seed.principal, 0) >= case.expected["principal_min_hits"],
                    expected=case.expected["principal_min_hits"],
                    actual=stats_payload.get("by_principal", {}).get(seed.principal, 0),
                )
            )
        if case.expected.get("verify_request_lookup", True):
            records, total = list_by_request
            checks.append(
                EvalCheck(
                    name="history.lookup_request",
                    passed=total >= 1 and any(record.request_id == seed.request_id for record in records),
                    expected=True,
                    actual=total,
                )
            )
        if case.expected.get("verify_principal_lookup", False):
            records, total = list_by_principal
            checks.append(
                EvalCheck(
                    name="history.lookup_principal",
                    passed=total >= 1 and any(record.principal == seed.principal for record in records),
                    expected=True,
                    actual=total,
                )
            )

        expected_seed_success = bool(case.expected.get("result_success", True))
        expected_stats_success = bool(case.expected.get("stats_success", True))
        passed = (
            bool(seed_result.get("success", False)) == expected_seed_success
            and bool(stats_result.get("success", False)) == expected_stats_success
            and all(check.passed for check in checks)
        )
        error = "" if passed else f"History case '{seed.case_id}' did not satisfy expected checks"
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed={
                "command": command,
                "seed_result": seed_result,
                "stats_result": stats_result,
                "stats": stats_payload,
                "request_records": [record.to_dict() for record in list_by_request[0]],
                "principal_records": [record.to_dict() for record in list_by_principal[0]],
            },
            error=error,
        )

    def _run_workflow_case(self, bot: BotBoy, case: EvalCase, seed: ReplaySeed) -> EvalCaseResult:
        step_specs = case.expected.get("steps") or [seed.input or case.command or "status"]
        executed_steps: List[Dict[str, Any]] = []

        for index, step_spec in enumerate(step_specs):
            template_context = {"steps": executed_steps}
            resolved_step = _resolve_template_value(step_spec, template_context)

            if isinstance(resolved_step, str):
                step_command = resolved_step.strip()
                step_request_id = seed.request_id
                step_approval_context: Dict[str, Any] = {}
            elif isinstance(resolved_step, dict):
                step_command = str(resolved_step.get("command", "")).strip()
                step_request_id = str(resolved_step.get("request_id", seed.request_id))
                step_approval_context = dict(resolved_step.get("approval_context") or {})
            else:
                step_command = str(resolved_step).strip()
                step_request_id = seed.request_id
                step_approval_context = {}

            if not step_command:
                return EvalCaseResult(
                    case_id=seed.case_id,
                    kind=seed.kind,
                    passed=False,
                    error=f"Workflow case '{seed.case_id}' contains an empty step at index {index}",
                )

            command_kwargs = _command_kwargs(case, seed)
            command_kwargs["request_id"] = step_request_id
            if step_approval_context:
                merged_approval_context = dict(command_kwargs.get("approval_context") or {})
                merged_approval_context.update(step_approval_context)
                command_kwargs["approval_context"] = merged_approval_context

            step_result = asyncio.run(bot.process_command(step_command, **command_kwargs))
            executed_steps.append(
                {
                    "command": step_command,
                    "request_id": step_request_id,
                    "result": step_result,
                }
            )

        primary_result = executed_steps[0]["result"]
        final_result = executed_steps[-1]["result"]
        resolved_expected = _resolve_template_value(
            case.expected,
            {
                "steps": executed_steps,
                "primary": primary_result,
                "final": final_result,
            },
        )
        primary_trace = primary_result.get("data", {}).get("trace", {})
        run_id = str(primary_trace.get("run_id", ""))
        trace_detail = bot.trace_store.get_run(run_id) if bot.trace_store and run_id else None
        trace_runs, trace_total = bot.trace_store.list_runs(request_id=seed.request_id) if bot.trace_store else ([], 0)
        history_stats = bot.history.stats() if bot.history else {}
        request_records, request_total = bot.history.list(limit=20, request_id=seed.request_id) if bot.history else ([], 0)
        principal_records, principal_total = bot.history.list(limit=20, principal=seed.principal) if bot.history else ([], 0)
        dashboard_payload = bot.get_dashboard_payload() if hasattr(bot, "get_dashboard_payload") else {}

        observed = {
            "primary": primary_result,
            "final": final_result,
            "steps": [step["result"] for step in executed_steps],
            "step_commands": [step["command"] for step in executed_steps],
            "history": history_stats,
            "dashboard": dashboard_payload,
            "traces": dashboard_payload.get("traces", {}) if isinstance(dashboard_payload, dict) else {},
            "trace": trace_detail or {},
            "trace_total": trace_total,
            "trace_runs": [run.to_dict() for run in trace_runs],
            "request_records": [record.to_dict() for record in request_records],
            "request_total": request_total,
            "principal_records": [record.to_dict() for record in principal_records],
            "principal_total": principal_total,
        }

        checks: List[EvalCheck] = []
        _add_contains_checks(checks, str(primary_result.get("output", "")), seed.expected_contains, "seed")
        _add_contains_checks(checks, str(primary_result.get("output", "")), case.expected_contains, "manifest")
        _add_path_checks(checks, observed, resolved_expected.get("required_paths", []), "workflow")
        _add_value_checks(checks, observed, resolved_expected.get("field_values", {}), "workflow")
        _add_min_value_checks(checks, observed, resolved_expected.get("min_values", {}), "workflow")

        checks.append(
            EvalCheck(
                name="workflow.step_count",
                passed=len(executed_steps) >= int(resolved_expected.get("min_step_count", 1)),
                expected=int(resolved_expected.get("min_step_count", 1)),
                actual=len(executed_steps),
            )
        )
        checks.append(
            EvalCheck(
                name="workflow.trace_total",
                passed=trace_total >= int(resolved_expected.get("min_trace_runs", 1)),
                expected=int(resolved_expected.get("min_trace_runs", 1)),
                actual=trace_total,
            )
        )

        if resolved_expected.get("verify_request_lookup", True):
            checks.append(
                EvalCheck(
                    name="workflow.request_lookup",
                    passed=request_total >= int(resolved_expected.get("min_request_hits", 1)),
                    expected=int(resolved_expected.get("min_request_hits", 1)),
                    actual=request_total,
                )
            )
        if resolved_expected.get("verify_principal_lookup", True):
            checks.append(
                EvalCheck(
                    name="workflow.principal_lookup",
                    passed=principal_total >= int(resolved_expected.get("min_principal_hits", 1)),
                    expected=int(resolved_expected.get("min_principal_hits", 1)),
                    actual=principal_total,
                )
            )
        if resolved_expected.get("verify_all_steps_success", True):
            checks.append(
                EvalCheck(
                    name="workflow.all_steps_success",
                    passed=all(bool(step["result"].get("success", False)) for step in executed_steps),
                    expected=True,
                    actual=[bool(step["result"].get("success", False)) for step in executed_steps],
                )
            )

        expected_primary_success = bool(resolved_expected.get("result_success", True))
        passed = bool(primary_result.get("success", False)) == expected_primary_success and all(check.passed for check in checks)
        error = "" if passed else f"Workflow case '{seed.case_id}' did not satisfy expected checks"
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed=observed,
            error=error,
        )

    def _run_scheduler_case(self, bot: BotBoy, case: EvalCase, seed: ReplaySeed) -> EvalCaseResult:
        seed_expected = _resolve_template_value(case.expected, {"seed": seed.__dict__})
        payload = dict(seed_expected.get("payload") or {})
        payload.setdefault("command", seed.input or case.command or "status")
        payload.setdefault("principal", seed.principal or "scheduler")
        payload.setdefault("request_id", seed.request_id or f"{seed.case_id}-scheduler")

        scheduled_task = ScheduledTask(
            task_id=f"sched-{seed.case_id}-{int(time.time() * 1000)}",
            name=str(seed_expected.get("name") or case.id or seed.case_id),
            schedule=str(seed_expected.get("schedule") or "in 1m"),
            task_type=str(seed_expected.get("task_type") or "generic"),
            payload=payload,
            next_run_ts=float(seed_expected.get("next_run_ts") or time.time()),
            enabled=bool(seed_expected.get("enabled", True)),
            max_runs=seed_expected.get("max_runs"),
            run_count=int(seed_expected.get("run_count", 0) or 0),
            created_at=str(seed_expected.get("created_at") or datetime.now(timezone.utc).isoformat()),
        )

        asyncio.run(bot._run_scheduled_task(scheduled_task))

        task_summary = bot.task_store.summary() if bot.task_store else {}
        latest_task_id = task_summary.get("latest_task_id", "")
        task_detail = bot.task_store.get_task(latest_task_id) if bot.task_store and latest_task_id else None
        task_events = bot.task_store.get_events(latest_task_id) if bot.task_store and latest_task_id else []
        dashboard = bot.get_dashboard_payload(mode="eval") if hasattr(bot, "get_dashboard_payload") else {}

        observed = {
            "scheduler_task": scheduled_task.to_dict(),
            "task_summary": task_summary,
            "task_detail": task_detail.to_dict() if task_detail else {},
            "task_events": [event.to_dict() for event in task_events],
            "dashboard": dashboard,
        }
        resolved_expected = _resolve_template_value(
            case.expected,
            {
                "seed": seed.__dict__,
                **observed,
            },
        )
        text = json.dumps(observed, sort_keys=True)

        checks: List[EvalCheck] = []
        _add_contains_checks(checks, text, seed.expected_contains, "seed")
        _add_contains_checks(checks, text, case.expected_contains, "manifest")
        _add_path_checks(checks, observed, resolved_expected.get("required_paths", []), "scheduler")
        _add_value_checks(checks, observed, resolved_expected.get("field_values", {}), "scheduler")
        _add_min_value_checks(checks, observed, resolved_expected.get("min_values", {}), "scheduler")

        expected_success = bool(resolved_expected.get("result_success", True))
        actual_success = bool(task_detail and task_detail.status == "completed")
        checks.insert(
            0,
            EvalCheck(
                name="scheduler.success",
                passed=actual_success == expected_success,
                expected=expected_success,
                actual=actual_success,
            ),
        )

        passed = all(check.passed for check in checks)
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed=observed,
            error="" if passed else f"Scheduler case '{seed.case_id}' did not satisfy expected checks",
        )

    def _run_auth_case(self, base_url: str, seed: ReplaySeed) -> EvalCaseResult:
        payload = {
            "username": seed.principal or "eval.wave1",
            "password": "eval-pass",
            "display_name": seed.principal or "Eval Wave 1",
        }
        status, body = _json_request(f"{base_url}/api/auth/login", payload)
        checks = [
            EvalCheck(name="status", passed=status == (seed.expected_status or 200), expected=seed.expected_status or 200, actual=status),
        ]
        for field in seed.expected_fields:
            checks.append(EvalCheck(name=f"field:{field}", passed=field in body, expected=True, actual=field in body))
        passed = all(check.passed for check in checks)
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed={"status": status, "body": body},
            error="" if passed else "Auth login did not satisfy expected fields/status",
        )

    def _run_rate_limit_case(self, base_url: str, seed: ReplaySeed) -> EvalCaseResult:
        headers = {
            "X-Forwarded-For": seed.principal or "198.51.100.10",
            "X-Request-ID": seed.request_id,
        }
        first_status, first_body = _get_json(f"{base_url}/api/status", headers=headers)
        second_status, second_body = _get_json(f"{base_url}/api/status", headers=headers)
        checks = [
            EvalCheck(name="first_status", passed=first_status == 200, expected=200, actual=first_status),
            EvalCheck(name="repeat_status", passed=second_status == (seed.expected_status_on_repeat or 429), expected=seed.expected_status_on_repeat or 429, actual=second_status),
            EvalCheck(
                name="retry_after_positive",
                passed=bool(second_body.get("retry_after", 0)) and int(second_body.get("retry_after", 0)) > 0 if isinstance(second_body.get("retry_after", 0), (int, float)) else bool(second_body.get("retry_after")),
                expected=True,
                actual=second_body.get("retry_after"),
            ),
        ]
        passed = all(check.passed for check in checks)
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed={
                "first_status": first_status,
                "first_body": first_body,
                "repeat_status": second_status,
                "repeat_body": second_body,
            },
            error="" if passed else "Rate-limit replay did not behave as expected",
        )

    def _run_dashboard_trace_case(self, base_url: str, seed: ReplaySeed) -> EvalCaseResult:
        status, status_body = _get_json(
            f"{base_url}/api/status",
            headers={"X-Request-ID": seed.request_id},
        )
        run_id = status_body.get("data", {}).get("trace", {}).get("run_id", "")
        dashboard_status, dashboard_body = _get_json(f"{base_url}/api/dashboard")
        detail_status, detail_body = _get_json(f"{base_url}/api/traces/{run_id}") if run_id else (404, {})

        checks = [
            EvalCheck(name="status", passed=status == 200, expected=200, actual=status),
            EvalCheck(name="dashboard_status", passed=dashboard_status == 200, expected=200, actual=dashboard_status),
            EvalCheck(name="run_id_present", passed=bool(run_id), expected=True, actual=bool(run_id)),
            EvalCheck(
                name="dashboard_has_status_snapshot",
                passed="status_snapshot" in dashboard_body,
                expected=True,
                actual="status_snapshot" in dashboard_body,
            ),
            EvalCheck(
                name="dashboard_has_readiness",
                passed="system_readiness" in dashboard_body,
                expected=True,
                actual="system_readiness" in dashboard_body,
            ),
            EvalCheck(name="trace_detail_status", passed=detail_status == 200, expected=200, actual=detail_status),
            EvalCheck(
                name="trace_request_id",
                passed=detail_body.get("run", {}).get("request_id") == seed.request_id,
                expected=seed.request_id,
                actual=detail_body.get("run", {}).get("request_id"),
            ),
            EvalCheck(
                name="trace_span_count",
                passed=len(detail_body.get("spans", [])) >= 2,
                expected=">=2",
                actual=len(detail_body.get("spans", [])),
            ),
        ]
        passed = all(check.passed for check in checks)
        return EvalCaseResult(
            case_id=seed.case_id,
            kind=seed.kind,
            passed=passed,
            checks=checks,
            observed={
                "status": status_body,
                "dashboard": dashboard_body,
                "trace_detail": detail_body,
            },
            error="" if passed else "Dashboard/trace replay did not satisfy expected checks",
        )

    def run(self) -> EvalRunResult:
        started = time.perf_counter()
        results: List[EvalCaseResult] = []
        shared_bot: Optional[BotBoy] = None
        shared_wave = _normalise_wave(self.wave)
        reuse_wave_state = shared_wave in {f"wave_{wave}" for wave in range(16, 23)}

        def _shared_eval_bot() -> BotBoy:
            nonlocal shared_bot
            if shared_bot is None:
                shared_bot = self._make_bot(self.manifest_name)
            return shared_bot

        try:
            for seed in self._seeds:
                case = self._cases.get(seed.case_id)
                if case is None:
                    results.append(
                        EvalCaseResult(
                            case_id=seed.case_id,
                            kind=seed.kind,
                            passed=False,
                            error="Replay seed has no matching manifest case",
                        )
                    )
                    continue

                kind = case.kind.lower()
                if kind == "command":
                    if reuse_wave_state:
                        results.append(self._run_command_case(_shared_eval_bot(), case, seed))
                    else:
                        bot = self._make_bot(seed.case_id)
                        try:
                            results.append(self._run_command_case(bot, case, seed))
                        finally:
                            bot.shutdown()
                    continue

                if kind == "dashboard":
                    if reuse_wave_state:
                        results.append(self._run_dashboard_case(_shared_eval_bot(), case, seed))
                    else:
                        bot = self._make_bot(seed.case_id)
                        try:
                            results.append(self._run_dashboard_case(bot, case, seed))
                        finally:
                            bot.shutdown()
                    continue

                if kind == "trace":
                    if reuse_wave_state:
                        results.append(self._run_trace_case(_shared_eval_bot(), case, seed))
                    else:
                        bot = self._make_bot(seed.case_id)
                        try:
                            results.append(self._run_trace_case(bot, case, seed))
                        finally:
                            bot.shutdown()
                    continue

                if kind == "history":
                    if reuse_wave_state:
                        results.append(self._run_history_case(_shared_eval_bot(), case, seed))
                    else:
                        bot = self._make_bot(seed.case_id)
                        try:
                            results.append(self._run_history_case(bot, case, seed))
                        finally:
                            bot.shutdown()
                    continue

                if kind == "workflow":
                    if reuse_wave_state:
                        results.append(self._run_workflow_case(_shared_eval_bot(), case, seed))
                    else:
                        bot = self._make_bot(seed.case_id)
                        try:
                            results.append(self._run_workflow_case(bot, case, seed))
                        finally:
                            bot.shutdown()
                    continue

                if kind == "scheduler":
                    if reuse_wave_state:
                        results.append(self._run_scheduler_case(_shared_eval_bot(), case, seed))
                    else:
                        bot = self._make_bot(seed.case_id)
                        try:
                            results.append(self._run_scheduler_case(bot, case, seed))
                        finally:
                            bot.shutdown()
                    continue

                if kind == "http" and seed.case_id in {"auth_login_smoke", "rate_limit_smoke", "dashboard_trace_smoke"}:
                    bot = self._make_bot(seed.case_id)
                    server = SimpleHTTPServer(bot, host="127.0.0.1", port=0)
                    server.start(blocking=False)
                    try:
                        base_url = f"http://127.0.0.1:{server.port}"
                        if seed.case_id == "auth_login_smoke":
                            results.append(self._run_auth_case(base_url, seed))
                        elif seed.case_id == "dashboard_trace_smoke":
                            results.append(self._run_dashboard_trace_case(base_url, seed))
                        else:
                            results.append(self._run_rate_limit_case(base_url, seed))
                    finally:
                        server.stop()
                        bot.shutdown()
                    continue

                if kind == "http":
                    bot = self._make_bot(seed.case_id)
                    server = SimpleHTTPServer(bot, host="127.0.0.1", port=0)
                    server.start(blocking=False)
                    try:
                        base_url = f"http://127.0.0.1:{server.port}"
                        request_text = str(case.request or "").strip()
                        if "/api/command" in request_text:
                            results.append(self._run_http_command_case(base_url, case, seed))
                        else:
                            results.append(self._run_http_path_case(base_url, case, seed))
                    finally:
                        server.stop()
                        bot.shutdown()
                    continue

                if kind == "mcp":
                    results.append(self._run_mcp_case(case, seed))
                    continue

                results.append(
                    EvalCaseResult(
                        case_id=seed.case_id,
                        kind=seed.kind,
                        passed=False,
                        error=f"Unsupported replay case: {seed.case_id} ({case.kind})",
                    )
                )
        finally:
            if shared_bot:
                shared_bot.shutdown()
            duration_ms = (time.perf_counter() - started) * 1000

        passed = sum(1 for result in results if result.passed)
        failed = sum(1 for result in results if not result.passed)
        return EvalRunResult(
            manifest_name=self.manifest_name,
            wave=self.wave,
            manifest_path=str(self.manifest_path),
            seed_path=str(self.seed_path),
            total=len(results),
            passed=passed,
            failed=failed,
            skipped=0,
            cases=results,
            duration_ms=duration_ms,
            manifest_case_count=len(self._cases),
            replay_seed_count=len(self._seeds),
            matched_seed_count=sum(1 for seed in self._seeds if seed.case_id in self._cases),
            integrity_issues=list(self._integrity_issues),
        )


def main(argv: Optional[Iterable[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run BotBoy eval/replay baseline")
    parser.add_argument("--manifest", default=str(default_manifest_path()), help="Path to the eval manifest JSON")
    parser.add_argument(
        "--seed",
        "--replay",
        dest="seed",
        default=str(default_seed_path()),
        help="Path to the replay seed JSONL",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json", "summary"),
        default=None,
        help="Output format for stdout",
    )
    parser.add_argument("--json", action="store_true", help="Shortcut for --format json")
    parser.add_argument("--text", action="store_true", help="Shortcut for --format text")
    parser.add_argument("--summary", action="store_true", help="Shortcut for --format summary")
    parser.add_argument(
        "--report-json",
        default="",
        help="Optional path for a JSON report artifact",
    )
    parser.add_argument(
        "--wave",
        default="",
        help="Optional eval wave alias, e.g. 1, 2, wave_1, wave_2",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.wave:
        runner = EvalReplayRunner.for_wave(args.wave)
    else:
        runner = EvalReplayRunner(args.manifest, args.seed)
    result = runner.run()

    output_format = args.format
    if output_format is None:
        if args.json:
            output_format = "json"
        elif args.summary:
            output_format = "summary"
        elif args.text:
            output_format = "text"
        else:
            output_format = "text"

    if args.report_json:
        save_report_json(args.report_json, result)

    if output_format == "json":
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    elif output_format == "summary":
        summary = result.summary()
        integrity_label = "OK" if summary["integrity_ok"] else f"WARN ({summary['integrity_issue_count']} issues)"
        print(
            "\n".join(
                [
                    f"Wave: {result.wave}",
                    f"Manifest: {result.manifest_name}",
                    f"Integrity: {integrity_label}",
                    f"Replay coverage: {summary['matched_seed_count']}/{summary['manifest_case_count']} manifest cases, {summary['matched_seed_count']}/{summary['replay_seed_count']} seeds",
                    f"Pass rate: {summary['pass_rate']:.3f}",
                ]
            )
        )
    else:
        print(result.render())
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
