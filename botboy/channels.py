"""
FiveChannelRouter — Formal inter-layer communication bus.

Maps the DeusAnimaOmni Five-Senses Interface Paradigm onto BotBoy's
existing architecture as a thin, non-breaking wrapper layer.

Channels:
  HAPTIC     Discrete events: commands, clicks, HTTP POSTs, state changes
  OLFACTORY  Diffuse system state: metrics, resource telemetry, load signals
  GUSTATORY  Qualitative scores: confidence, LLM quality, semantic metadata
  VISUAL     Structured output: rendered text, display data, UI payloads
  AUDITORY   Time-sequential streams: voice, WebSocket chunks, token streams

Design principle: BotBoy already implements all five channels informally.
This module formalises them as a typed message bus with pub/sub routing,
without touching the existing process_command() pipeline.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Awaitable


# ── Channel Taxonomy ──────────────────────────────────────────────────────────

class SenseChannel(Enum):
    HAPTIC    = "haptic"     # Commands, events, discrete interactions
    OLFACTORY = "olfactory"  # Metrics, resource state, ambient telemetry
    GUSTATORY = "gustatory"  # Confidence scores, quality assessments
    VISUAL    = "visual"     # Rendered output, display payloads
    AUDITORY  = "auditory"   # Streaming audio, token streams, voice


# Channel semantic metadata — what each channel carries and its latency profile
CHANNEL_SEMANTICS = {
    SenseChannel.HAPTIC:    {"latency": "low",    "pattern": "request-response",  "format": "event"},
    SenseChannel.OLFACTORY: {"latency": "medium", "pattern": "broadcast",         "format": "metrics"},
    SenseChannel.GUSTATORY: {"latency": "medium", "pattern": "feedback",          "format": "score"},
    SenseChannel.VISUAL:    {"latency": "low",    "pattern": "push",              "format": "render"},
    SenseChannel.AUDITORY:  {"latency": "low",    "pattern": "stream",            "format": "chunks"},
}


# ── Channel Message ────────────────────────────────────────────────────────────

@dataclass
class ChannelMessage:
    """
    A typed message routed through the FiveChannelRouter.

    Attributes:
        channel      Which sense channel carries this message
        source_layer Originating module/skill identifier (e.g. "memory.simple")
        target_layer Target module/skill, or "*" for broadcast
        data         Message payload (any serialisable type)
        confidence   Gustatory metadata: certainty of the payload (0.0–1.0)
        intensity    Olfactory metadata: signal strength / resource pressure
        timestamp_ms Wall-clock timestamp in milliseconds
        metadata     Arbitrary key-value annotations
        msg_id       Unique message identifier for tracing
    """
    channel:      SenseChannel
    source_layer: str
    target_layer: str
    data:         Any
    confidence:   float = 1.0
    intensity:    float = 1.0
    timestamp_ms: float = field(default_factory=lambda: time.monotonic() * 1000)
    metadata:     Dict[str, Any] = field(default_factory=dict)
    msg_id:       str = field(default_factory=lambda: f"{time.monotonic_ns():x}")

    def to_dict(self) -> dict:
        return {
            "msg_id":       self.msg_id,
            "channel":      self.channel.value,
            "source_layer": self.source_layer,
            "target_layer": self.target_layer,
            "confidence":   self.confidence,
            "intensity":    self.intensity,
            "timestamp_ms": self.timestamp_ms,
            "metadata":     self.metadata,
            # data is intentionally excluded — may not be serialisable
        }


Handler = Callable[[ChannelMessage], Any]  # sync or async


# ── FiveChannelRouter ─────────────────────────────────────────────────────────

class FiveChannelRouter:
    """
    Asynchronous pub/sub message router for the five sense channels.

    Usage:
        router = FiveChannelRouter()
        router.subscribe(SenseChannel.HAPTIC, my_handler)
        await router.publish(ChannelMessage(channel=SenseChannel.HAPTIC, ...))

    Integration with BotBoy:
        HAPTIC    → wraps process_command() events
        OLFACTORY → wraps MetricsCollector / PerformanceMonitor broadcasts
        GUSTATORY → wraps ReflectionEngine.confidence scores
        VISUAL    → wraps rendered output to web UI / CLI
        AUDITORY  → wraps VoiceListener streams and WebSocket chunks
    """

    def __init__(self, max_queue_size: int = 1024) -> None:
        self._handlers: Dict[SenseChannel, List[Handler]] = defaultdict(list)
        self._global_handlers: List[Handler] = []
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._lock = threading.Lock()
        self._message_count: Dict[SenseChannel, int] = defaultdict(int)
        self._error_count: int = 0
        self._running = False

    def _increment_message_count(self, channel: SenseChannel) -> None:
        with self._lock:
            self._message_count[channel] += 1

    def _increment_error_count(self) -> None:
        with self._lock:
            self._error_count += 1

    # ── Subscription ──────────────────────────────────────────────────────────

    def subscribe(self, channel: SenseChannel, handler: Handler) -> None:
        """Register a handler for a specific channel."""
        with self._lock:
            self._handlers[channel].append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        """Register a handler that receives messages from ALL channels."""
        with self._lock:
            self._global_handlers.append(handler)

    def unsubscribe(self, channel: SenseChannel, handler: Handler) -> bool:
        with self._lock:
            lst = self._handlers[channel]
            if handler in lst:
                lst.remove(handler)
                return True
        return False

    # ── Publishing ────────────────────────────────────────────────────────────

    async def publish(self, msg: ChannelMessage) -> None:
        """Publish a message — routes immediately to all subscribed handlers."""
        self._increment_message_count(msg.channel)
        await self._dispatch(msg)

    def publish_sync(self, msg: ChannelMessage) -> None:
        """Synchronous publish for non-async contexts (fire-and-forget)."""
        self._increment_message_count(msg.channel)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._dispatch(msg))
            else:
                loop.run_until_complete(self._dispatch(msg))
        except RuntimeError:
            # No event loop — dispatch synchronously through sync handlers only
            with self._lock:
                handlers = list(self._handlers[msg.channel]) + list(self._global_handlers)
            for handler in handlers:
                try:
                    if not asyncio.iscoroutinefunction(handler):
                        handler(msg)
                except Exception:
                    self._increment_error_count()

    async def _dispatch(self, msg: ChannelMessage) -> None:
        with self._lock:
            handlers = list(self._handlers[msg.channel]) + list(self._global_handlers)
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(msg)
                else:
                    handler(msg)
            except Exception:
                self._increment_error_count()

    # ── Convenience factory methods ───────────────────────────────────────────

    def haptic(self, source: str, data: Any, target: str = "*", **meta) -> ChannelMessage:
        """Create a HAPTIC channel message (command/event)."""
        return ChannelMessage(channel=SenseChannel.HAPTIC,
                              source_layer=source, target_layer=target, data=data, **meta)

    def olfactory(self, source: str, data: Any, intensity: float = 1.0, **meta) -> ChannelMessage:
        """Create an OLFACTORY channel message (metrics/resource state)."""
        return ChannelMessage(channel=SenseChannel.OLFACTORY,
                              source_layer=source, target_layer="*",
                              data=data, intensity=intensity, **meta)

    def gustatory(self, source: str, data: Any, confidence: float = 1.0, **meta) -> ChannelMessage:
        """Create a GUSTATORY channel message (quality score / confidence)."""
        return ChannelMessage(channel=SenseChannel.GUSTATORY,
                              source_layer=source, target_layer="*",
                              data=data, confidence=confidence, **meta)

    def visual(self, source: str, data: Any, target: str = "*", **meta) -> ChannelMessage:
        """Create a VISUAL channel message (rendered output)."""
        return ChannelMessage(channel=SenseChannel.VISUAL,
                              source_layer=source, target_layer=target, data=data, **meta)

    def auditory(self, source: str, data: Any, **meta) -> ChannelMessage:
        """Create an AUDITORY channel message (stream chunk)."""
        return ChannelMessage(channel=SenseChannel.AUDITORY,
                              source_layer=source, target_layer="*", data=data, **meta)

    # ── Observability ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            handler_counts = {ch.value: len(h) for ch, h in self._handlers.items()}
            message_counts = {ch.value: c for ch, c in self._message_count.items()}
            total_messages = sum(self._message_count.values())
            error_count = self._error_count
            global_handlers = len(self._global_handlers)
        return {
            "message_counts": message_counts,
            "total_messages": total_messages,
            "handler_counts": handler_counts,
            "global_handlers": global_handlers,
            "error_count": error_count,
        }

    def reset_stats(self) -> None:
        with self._lock:
            self._message_count.clear()
            self._error_count = 0


# ── Channel-Aware BotBoy Adapter ──────────────────────────────────────────────

class ChannelAdapter:
    """
    Wraps BotBoy's process_command() to emit typed channel messages
    before and after command processing.

    This is the bridge between BotBoy's imperative command pipeline
    and the declarative Five-Channel architecture.

    Integration:
        adapter = ChannelAdapter(bot, router)
        result = await adapter.process(command)
        # → publishes HAPTIC(pre), VISUAL(result), GUSTATORY(confidence),
        #              OLFACTORY(latency metrics)
    """

    def __init__(self, bot, router: FiveChannelRouter) -> None:
        self._bot = bot
        self._router = router

    async def process(self, command: str, principal: str = "user") -> dict:
        """Process a command through BotBoy and emit channel messages."""
        start = time.perf_counter()

        # HAPTIC: incoming command event
        await self._router.publish(self._router.haptic(
            source="gateway",
            data={"command": command, "principal": principal},
            target="orchestrator",
            metadata={"type": "command_request"},
        ))

        # Execute through BotBoy core
        result = await self._bot.process_command(command)

        latency_ms = (time.perf_counter() - start) * 1000

        # VISUAL: rendered output
        if result.get("output"):
            await self._router.publish(self._router.visual(
                source="orchestrator",
                data={"output": result["output"], "type": result.get("type", "unknown")},
                target=principal,
                metadata={"command": command},
            ))

        # GUSTATORY: response quality / success confidence
        confidence = 1.0 if result.get("success") else 0.2
        await self._router.publish(self._router.gustatory(
            source="orchestrator",
            data={"success": result.get("success"), "type": result.get("type")},
            confidence=confidence,
            metadata={"command": command},
        ))

        # OLFACTORY: performance telemetry
        await self._router.publish(self._router.olfactory(
            source="orchestrator",
            data={"latency_ms": latency_ms, "cmd_type": result.get("type", "unknown")},
            intensity=min(1.0, latency_ms / 5000),  # normalised to [0,1] at 5s
            metadata={"principal": principal},
        ))

        return result

    async def emit_audio_chunk(self, chunk: str, source: str = "voice") -> None:
        """Emit an AUDITORY channel message for a voice/stream chunk."""
        await self._router.publish(self._router.auditory(
            source=source, data={"chunk": chunk}
        ))


# ── Global singleton ──────────────────────────────────────────────────────────

_global_router: Optional[FiveChannelRouter] = None
_router_lock = threading.Lock()


def get_router() -> FiveChannelRouter:
    """Return (or create) the global FiveChannelRouter singleton."""
    global _global_router
    if _global_router is None:
        with _router_lock:
            if _global_router is None:
                _global_router = FiveChannelRouter()
    return _global_router
