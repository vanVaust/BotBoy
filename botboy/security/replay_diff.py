"""Replay diff reports for deterministic BotBoy trace replays."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except TypeError:
        return json.dumps(str(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_id(*parts: Any, prefix: str = "rdiff") -> str:
    raw = "\n".join(_stable_json(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _payload_from_replay(value: Any) -> dict[str, Any]:
    data = _as_dict(value)
    if not data and isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        data = {"results": _as_list(value)}
    results = _as_list(data.get("results"))
    if not results:
        results = _as_list(data.get("spans"))
    return {
        "run_id": str(data.get("run_id") or data.get("id") or ""),
        "success": data.get("success"),
        "span_count": int(data.get("replayed_spans", data.get("span_count", len(results))) or 0),
        "results": results,
    }


def _span_id(item: Mapping[str, Any], index: int) -> str:
    raw = item.get("span_id") or item.get("id") or item.get("event_id")
    return str(raw or f"index:{index}")


def _component(item: Mapping[str, Any]) -> str:
    return str(item.get("simulated_component") or item.get("component") or "")


def _payload_value(item: Mapping[str, Any]) -> Any:
    if "payload" in item:
        return item.get("payload")
    if "payload_ref" in item:
        return item.get("payload_ref")
    return item.get("data")


@dataclass(frozen=True)
class ReplayDiffEntry:
    code: str
    category: str
    message: str
    span_id: str = ""
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "span_id": self.span_id,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class ReplayDiffReport:
    report_id: str
    expected_run_id: str
    actual_run_id: str
    entries: list[ReplayDiffEntry] = field(default_factory=list)

    @property
    def matches(self) -> bool:
        return not self.entries

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "expected_run_id": self.expected_run_id,
            "actual_run_id": self.actual_run_id,
            "matches": self.matches,
            "entry_count": len(self.entries),
            "entries": [entry.to_dict() for entry in self.entries],
        }


def compare_replay_payloads(expected: Any, actual: Any) -> ReplayDiffReport:
    """Compare deterministic replay payloads without depending on removed Workflow-IR types."""
    expected_payload = _payload_from_replay(expected)
    actual_payload = _payload_from_replay(actual)
    expected_results = expected_payload["results"]
    actual_results = actual_payload["results"]
    entries: list[ReplayDiffEntry] = []

    def add(
        code: str,
        category: str,
        message: str,
        *,
        span_id: str = "",
        expected_value: Any = None,
        actual_value: Any = None,
    ) -> None:
        entries.append(
            ReplayDiffEntry(
                code=code,
                category=category,
                message=message,
                span_id=span_id,
                expected=expected_value,
                actual=actual_value,
            )
        )

    if expected_payload["success"] != actual_payload["success"]:
        add(
            "replay_success_changed",
            "outcome",
            "Replay success flag changed.",
            expected_value=expected_payload["success"],
            actual_value=actual_payload["success"],
        )

    if expected_payload["span_count"] != actual_payload["span_count"]:
        add(
            "replay_span_count_changed",
            "replay",
            "Replay span count changed.",
            expected_value=expected_payload["span_count"],
            actual_value=actual_payload["span_count"],
        )

    expected_keys = [_span_id(item, index) for index, item in enumerate(expected_results)]
    actual_keys = [_span_id(item, index) for index, item in enumerate(actual_results)]
    if expected_keys != actual_keys:
        add(
            "replay_span_sequence_changed",
            "replay",
            "Replay span sequence changed.",
            expected_value=expected_keys,
            actual_value=actual_keys,
        )

    expected_by_id = {span_id: item for span_id, item in zip(expected_keys, expected_results)}
    actual_by_id = {span_id: item for span_id, item in zip(actual_keys, actual_results)}
    for span_id in sorted(set(expected_by_id) | set(actual_by_id)):
        expected_span = expected_by_id.get(span_id)
        actual_span = actual_by_id.get(span_id)
        if expected_span is None:
            add("replay_span_added", "replay", "Replay span was added.", span_id=span_id, actual_value=actual_span)
            continue
        if actual_span is None:
            add("replay_span_removed", "replay", "Replay span was removed.", span_id=span_id, expected_value=expected_span)
            continue
        comparisons = (
            ("replay_component_changed", "route", "Replay component changed.", _component(expected_span), _component(actual_span)),
            (
                "replay_event_type_changed",
                "replay",
                "Replay event type changed.",
                str(expected_span.get("event_type") or ""),
                str(actual_span.get("event_type") or ""),
            ),
            (
                "replay_status_changed",
                "outcome",
                "Replay span status changed.",
                str(expected_span.get("status") or ""),
                str(actual_span.get("status") or ""),
            ),
            (
                "replay_payload_changed",
                "payload",
                "Replay span payload changed.",
                _payload_value(expected_span),
                _payload_value(actual_span),
            ),
        )
        for code, category, message, expected_value, actual_value in comparisons:
            if expected_value != actual_value:
                add(
                    code,
                    category,
                    message,
                    span_id=span_id,
                    expected_value=expected_value,
                    actual_value=actual_value,
                )

    report_id = _stable_id(
        expected_payload["run_id"],
        actual_payload["run_id"],
        [entry.to_dict() for entry in entries],
    )
    return ReplayDiffReport(
        report_id=report_id,
        expected_run_id=expected_payload["run_id"],
        actual_run_id=actual_payload["run_id"],
        entries=entries,
    )


def persist_replay_diff_report(store: Any, report: ReplayDiffReport, *, source: str = "runtime") -> bool:
    if not store:
        return False
    conn = store._get_conn()
    payload = report.to_dict()
    reason_codes = [entry.code for entry in report.entries]
    categories = sorted({entry.category for entry in report.entries})
    now = _now()
    conn.execute(
        """
        INSERT INTO replay_diffs (
            report_id,
            expected_run_id,
            actual_run_id,
            source,
            matches,
            entry_count,
            reason_codes_json,
            categories_json,
            report_json,
            created_at,
            updated_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(report_id) DO UPDATE SET
            source = excluded.source,
            matches = excluded.matches,
            entry_count = excluded.entry_count,
            reason_codes_json = excluded.reason_codes_json,
            categories_json = excluded.categories_json,
            report_json = excluded.report_json,
            updated_at = excluded.updated_at
        """,
        (
            report.report_id,
            report.expected_run_id,
            report.actual_run_id,
            str(source or "runtime"),
            1 if report.matches else 0,
            len(report.entries),
            json.dumps(reason_codes, sort_keys=True),
            json.dumps(categories, sort_keys=True),
            json.dumps(payload, sort_keys=True),
            now,
            now,
        ),
    )
    conn.commit()
    return True


def _json_list(payload: str) -> list[Any]:
    try:
        value = json.loads(payload or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _row_summary(row: Any) -> dict[str, Any]:
    return {
        "report_id": row["report_id"],
        "expected_run_id": row["expected_run_id"],
        "actual_run_id": row["actual_run_id"],
        "source": row["source"],
        "matches": bool(row["matches"]),
        "entry_count": int(row["entry_count"] or 0),
        "reason_codes": _json_list(row["reason_codes_json"]),
        "categories": _json_list(row["categories_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def load_replay_diff_report(store: Any, report_id: str) -> dict[str, Any] | None:
    if not store or not report_id:
        return None
    try:
        row = store._fetchone("SELECT report_json FROM replay_diffs WHERE report_id = ?", (report_id,))
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    try:
        value = json.loads(row["report_json"] or "{}")
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def list_replay_diff_summaries(store: Any, *, run_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    if not store:
        return []
    normalized_limit = max(1, min(int(limit or 50), 500))
    params: list[Any] = []
    where = ""
    if run_id:
        where = "WHERE expected_run_id = ? OR actual_run_id = ?"
        params.extend([run_id, run_id])
    try:
        rows = store._fetchall(
            f"""
            SELECT *
            FROM replay_diffs
            {where}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            tuple(params + [normalized_limit]),
        )
    except sqlite3.OperationalError:
        return []
    return [_row_summary(row) for row in rows]


def summarize_replay_diffs(store: Any) -> dict[str, Any]:
    if not store:
        return {"available": False}
    try:
        rows = store._fetchall(
            """
            SELECT *
            FROM replay_diffs
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 500
            """
        )
    except sqlite3.OperationalError:
        return {"available": False, "reason": "replay_diff_table_missing"}
    summaries = [_row_summary(row) for row in rows]
    reason_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    drift_count = 0
    for item in summaries:
        if not item["matches"]:
            drift_count += 1
        for code in item["reason_codes"]:
            reason_counts[str(code)] = reason_counts.get(str(code), 0) + 1
        for category in item["categories"]:
            category_counts[str(category)] = category_counts.get(str(category), 0) + 1
    latest = summaries[0] if summaries else {}
    return {
        "available": True,
        "total": len(summaries),
        "drift_count": drift_count,
        "match_count": len(summaries) - drift_count,
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "latest_report_id": latest.get("report_id", ""),
        "latest_updated_at": latest.get("updated_at", ""),
        "recent": summaries[:5],
    }
