"""Bounded in-process A2A pilot registry for BotBoy Welle 21."""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class A2APilotMessage:
    message_id: str
    adapter_id: str
    sender: str
    principal: str
    task_id: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "adapter_id": self.adapter_id,
            "sender": self.sender,
            "principal": self.principal,
            "task_id": self.task_id,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class A2APilotResponse:
    adapter_id: str
    message_id: str
    ok: bool
    status: str
    payload: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "adapter_id": self.adapter_id,
            "message_id": self.message_id,
            "ok": self.ok,
            "status": self.status,
            "payload": dict(self.payload),
            "error": self.error,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class A2APilotAdapter:
    adapter_id: str
    name: str
    handler: Callable[[A2APilotMessage], Any]
    capabilities: List[str] = field(default_factory=list)
    principal_allowlist: List[str] = field(default_factory=list)
    max_payload_bytes: int = 8192
    metadata: Dict[str, Any] = field(default_factory=dict)

    def accepts_principal(self, principal: str) -> bool:
        if not self.principal_allowlist:
            return True
        return principal in self.principal_allowlist

    def to_dict(self) -> dict:
        return {
            "adapter_id": self.adapter_id,
            "name": self.name,
            "capabilities": list(self.capabilities),
            "principal_allowlist": list(self.principal_allowlist),
            "max_payload_bytes": self.max_payload_bytes,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AdapterFit:
    adapter_id: str
    score: float
    reasons: List[str] = field(default_factory=list)
    matched_capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "adapter_id": self.adapter_id,
            "score": self.score,
            "reasons": list(self.reasons),
            "matched_capabilities": list(self.matched_capabilities),
        }


class BoundedA2APilotRegistry:
    """In-process A2A adapter registry with bounded payloads and no network."""

    def __init__(self, *, max_payload_bytes: int = 8192) -> None:
        self._adapters: Dict[str, A2APilotAdapter] = {}
        self._max_payload_bytes = max_payload_bytes

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(8)}"

    @staticmethod
    def _normalize_capabilities(capabilities: Sequence[str]) -> List[str]:
        normalized: List[str] = []
        for item in capabilities:
            text = str(item).strip().lower()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _payload_size(payload: Mapping[str, Any]) -> int:
        return len(json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8"))

    def register(self, adapter: A2APilotAdapter) -> A2APilotAdapter:
        adapter_id = (adapter.adapter_id or "").strip()
        if not adapter_id:
            raise ValueError("adapter_id is required")
        if adapter_id in self._adapters:
            raise ValueError(f"Adapter already registered: {adapter_id}")
        resolved = A2APilotAdapter(
            adapter_id=adapter_id,
            name=adapter.name or adapter_id,
            handler=adapter.handler,
            capabilities=self._normalize_capabilities(adapter.capabilities),
            principal_allowlist=[item for item in adapter.principal_allowlist if item],
            max_payload_bytes=int(adapter.max_payload_bytes or self._max_payload_bytes),
            metadata=dict(adapter.metadata or {}),
        )
        self._adapters[adapter_id] = resolved
        return resolved

    def get(self, adapter_id: str) -> Optional[A2APilotAdapter]:
        return self._adapters.get((adapter_id or "").strip())

    def list_adapters(self) -> List[A2APilotAdapter]:
        return [self._adapters[key] for key in sorted(self._adapters)]

    def rank_adapters(
        self,
        task_text: str,
        *,
        required_capabilities: Sequence[str] = (),
        preferred_adapter: str = "",
    ) -> List[AdapterFit]:
        tokens = set(re.findall(r"[a-z0-9_]+", (task_text or "").lower()))
        required = [str(item).strip().lower() for item in required_capabilities if str(item).strip()]
        ranked: List[AdapterFit] = []
        for adapter_id, adapter in self._adapters.items():
            score = 0.0
            reasons: List[str] = []
            matched = sorted(set(adapter.capabilities).intersection(required))
            if preferred_adapter and adapter_id == preferred_adapter:
                score += 10.0
                reasons.append("preferred adapter match")
            if matched:
                score += 4.0 * len(matched)
                reasons.append(f"covers required capabilities: {', '.join(matched)}")
            role_terms = set(adapter.capabilities) | {adapter_id.lower(), adapter.name.lower()}
            overlap = sorted(tokens.intersection(role_terms))
            if overlap:
                score += 2.0 * len(overlap)
                reasons.append(f"text overlap: {', '.join(overlap[:5])}")
            ranked.append(
                AdapterFit(
                    adapter_id=adapter_id,
                    score=round(score, 2),
                    reasons=reasons,
                    matched_capabilities=matched,
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.adapter_id))
        return ranked

    def suggest_adapter(
        self,
        task_text: str,
        *,
        required_capabilities: Sequence[str] = (),
        preferred_adapter: str = "",
    ) -> Optional[A2APilotAdapter]:
        ranked = self.rank_adapters(
            task_text,
            required_capabilities=required_capabilities,
            preferred_adapter=preferred_adapter,
        )
        return self._adapters.get(ranked[0].adapter_id) if ranked else None

    def dispatch(
        self,
        adapter_id: str,
        payload: Mapping[str, Any],
        *,
        sender: str = "",
        principal: str = "anonymous",
        task_id: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> A2APilotResponse:
        adapter = self.get(adapter_id)
        if adapter is None:
            raise KeyError(f"Unknown adapter: {adapter_id}")
        if not adapter.accepts_principal(principal):
            raise PermissionError(f"Principal not allowed for adapter {adapter_id}")
        resolved_payload = dict(payload or {})
        payload_size = self._payload_size(resolved_payload)
        limit = min(adapter.max_payload_bytes, self._max_payload_bytes)
        if payload_size > limit:
            raise ValueError(f"Payload too large for adapter {adapter_id}: {payload_size} bytes > {limit} bytes")
        message = A2APilotMessage(
            message_id=self._new_id("msg"),
            adapter_id=adapter.adapter_id,
            sender=sender or "botboy",
            principal=principal or "anonymous",
            task_id=task_id or "",
            payload=resolved_payload,
            metadata=dict(metadata or {}),
            created_at=self._now(),
        )
        result = adapter.handler(message)
        return self._coerce_response(adapter, message, result)

    def stats(self) -> dict:
        by_capability: Dict[str, int] = {}
        for adapter in self._adapters.values():
            for capability in adapter.capabilities:
                by_capability[capability] = by_capability.get(capability, 0) + 1
        return {
            "available": True,
            "total_adapters": len(self._adapters),
            "capabilities": dict(sorted(by_capability.items())),
            "adapters": [adapter.to_dict() for adapter in self.list_adapters()],
        }

    @staticmethod
    def _coerce_response(adapter: A2APilotAdapter, message: A2APilotMessage, result: Any) -> A2APilotResponse:
        if isinstance(result, A2APilotResponse):
            return result
        if isinstance(result, Mapping):
            payload = dict(result.get("payload", result))
            metadata = dict(result.get("metadata", {}))
            status = str(result.get("status", "ok"))
            error = str(result.get("error", ""))
            ok = bool(result.get("ok", not error))
            return A2APilotResponse(
                adapter_id=adapter.adapter_id,
                message_id=message.message_id,
                ok=ok,
                status=status,
                payload=payload,
                error=error,
                metadata=metadata,
            )
        return A2APilotResponse(
            adapter_id=adapter.adapter_id,
            message_id=message.message_id,
            ok=True,
            status="ok",
            payload={"result": result},
            metadata={},
        )
