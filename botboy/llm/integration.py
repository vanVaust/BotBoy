"""LLM integration: Ollama / OpenAI(-compatible) / Anthropic / Mock backends.

Transport contract shared by every HTTP backend:

* the response status is checked before the body is interpreted; a proxy's
  HTML error page is never parsed as a model answer;
* bodies are read under a hard byte cap (after transparent decompression),
  never with an unbounded ``resp.json()``;
* redirects are not followed, so credentials in custom headers
  (``x-api-key``) cannot be forwarded to a host the operator did not name;
* transient failures (408, 425, 429, 5xx, 529, connection errors, timeouts)
  are retried with capped, fully jittered exponential backoff that honours
  ``Retry-After``/``retry-after-ms``; permanent failures are returned at once;
* an overall deadline bounds a call including all retries and sleeps;
* the API key is redacted from every error string.

Streams retry only before the first token: once text has been yielded a
retry would duplicate it, so a mid-stream failure is raised to the caller.
"""
from __future__ import annotations

import asyncio
import email.utils
import json
import random
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover - aiohttp is a declared dependency
    aiohttp = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False


DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OPENAI_URL = "https://api.openai.com"
DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODELS = {
    "ollama": "llama3.2",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
}

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_STREAM_LINE_BYTES = 1024 * 1024
_ERROR_DETAIL_CHARS = 300
_CHUNK = 64 * 1024

# 529 is Anthropic's "overloaded"; 425/408 are explicit "try again" signals.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 529})


class LLMTransportError(Exception):
    """A classified backend failure. ``retryable`` drives retry and fallback."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        retryable: bool = False,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
        self.attempts = 1


@dataclass
class LLMResponse:
    content: str
    model: str
    backend: str
    tokens_used: int = 0
    duration_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    attempts: int = 1        # attempts on the backend that produced this response
    retries: int = 0         # retries across every backend tried (fallback chain included)
    status_code: Optional[int] = None
    retryable: bool = False
    cost_usd: Optional[float] = None
    fallback_trail: List[str] = field(default_factory=list)

    def usage(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.tokens_used,
            "attempts": self.attempts,
            "retries": self.retries,
            "cost_usd": self.cost_usd,
            "stop_reason": self.stop_reason,
            "fallback_trail": list(self.fallback_trail),
        }


@dataclass
class StreamUsage:
    """Usage of one streamed call, filled in by the backends as it runs.

    Passed down as ``stream(..., usage=StreamUsage())``; the meter records
    it when the stream ends, failed or not (aborted attempts can be billed).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    fallbacks: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


@dataclass(frozen=True)
class RetryPolicy:
    """``max_attempts`` counts the first try; ``max_attempts=1`` disables retries."""

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or not 1 <= self.max_attempts <= 11:
            raise ValueError("max_attempts must be an integer in [1, 11]")
        for name in ("base_delay", "max_delay"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 300:
                raise ValueError(f"{name} must be a number in [0, 300]")
        if self.base_delay > self.max_delay:
            raise ValueError("base_delay must not exceed max_delay")

    def backoff(self, retry_index: int, rand: Callable[[], float]) -> float:
        # Full jitter (AWS architecture blog): uniform in [0, min(cap, base*2^n)].
        ceiling = min(self.max_delay, self.base_delay * (2 ** min(retry_index, 30)))
        return ceiling * min(max(rand(), 0.0), 1.0)

    def budget(self, per_attempt_timeout: float) -> float:
        return per_attempt_timeout * self.max_attempts + self.max_delay * (self.max_attempts - 1)


class BaseLLMBackend:
    name = "base"
    model = ""

    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""  # pragma: no cover - makes this an async generator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _nonneg_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _clean_text(text: str, limit: int = _ERROR_DETAIL_CHARS) -> str:
    # Error text comes from the network: strip control/format characters so it
    # cannot forge log lines or terminal escapes.
    out = []
    for ch in text[: limit * 2]:
        cat = unicodedata.category(ch)
        out.append(" " if cat in ("Cc", "Cf", "Zl", "Zp") else ch)
    cleaned = " ".join("".join(out).split())
    return cleaned[:limit]


def _error_detail(body: bytes) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return _clean_text(body[: _ERROR_DETAIL_CHARS * 4].decode("utf-8", "replace"))
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            parts = [str(err.get(k)) for k in ("type", "code", "message") if isinstance(err.get(k), (str, int)) and err.get(k) != ""]
            if parts:
                return _clean_text(": ".join(parts))
        if isinstance(err, str):
            return _clean_text(err)
        if isinstance(data.get("message"), str):
            return _clean_text(data["message"])
    return _clean_text(json.dumps(data, ensure_ascii=False)[: _ERROR_DETAIL_CHARS * 2])


def parse_retry_after(headers: Optional[Mapping[str, str]], now: Optional[datetime] = None) -> Optional[float]:
    """Seconds to wait from ``retry-after-ms`` or ``Retry-After`` (delta or HTTP-date)."""

    ms = headers.get("retry-after-ms") if headers is not None else None
    if ms is not None:
        try:
            value = float(ms) / 1000.0
            if value >= 0 and value == value and value != float("inf"):
                return value
        except ValueError:
            pass
    raw = headers.get("Retry-After") if headers is not None else None
    if raw is None:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return float(int(raw[:9]))  # 9 digits ≈ 31 years; caps absurd values cheaply
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = (when - (now or datetime.now(timezone.utc))).total_seconds()
    return max(0.0, delta)


async def _read_capped(resp, cap: int) -> bytes:
    declared = resp.content_length
    if declared is not None and declared > cap:
        raise LLMTransportError(f"response too large ({declared} bytes > {cap})", status=resp.status)
    chunks: List[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(_CHUNK):
        total += len(chunk)
        if total > cap:
            raise LLMTransportError(f"response too large (> {cap} bytes)", status=resp.status)
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_prefix(resp, cap: int) -> bytes:
    """Error bodies are diagnostics only: keep a prefix instead of failing."""
    chunks: List[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(_CHUNK):
        chunks.append(chunk[: cap - total])
        total += len(chunks[-1])
        if total >= cap:
            break
    return b"".join(chunks)


async def iter_lines(
    chunks: AsyncIterator[bytes],
    *,
    max_line: int = MAX_STREAM_LINE_BYTES,
    max_total: int = MAX_STREAM_BYTES,
) -> AsyncIterator[bytes]:
    """Split a byte stream on LF (dropping a trailing CR) in linear time.

    Chunks without a newline are parked in a list instead of being
    concatenated, so a byte-by-byte trickle stays O(n) rather than O(n²).
    """

    pending: List[bytes] = []
    pending_len = 0
    total = 0
    async for chunk in chunks:
        total += len(chunk)
        if total > max_total:
            raise LLMTransportError(f"stream exceeded {max_total} bytes")
        if b"\n" not in chunk:
            pending.append(chunk)
            pending_len += len(chunk)
            if pending_len > max_line:
                raise LLMTransportError(f"stream line exceeded {max_line} bytes")
            continue
        data = b"".join(pending) + chunk if pending else chunk
        parts = data.split(b"\n")
        tail = parts.pop()
        for part in parts:
            if len(part) > max_line:
                raise LLMTransportError(f"stream line exceeded {max_line} bytes")
            yield part[:-1] if part.endswith(b"\r") else part
        pending = [tail] if tail else []
        pending_len = len(tail)
        if pending_len > max_line:
            raise LLMTransportError(f"stream line exceeded {max_line} bytes")
    if pending:
        tail = b"".join(pending)
        yield tail[:-1] if tail.endswith(b"\r") else tail


async def iter_sse(lines: AsyncIterator[bytes]) -> AsyncIterator[Tuple[str, str]]:
    """Server-sent events (WHATWG HTML §9.2): yields ``(event, data)``."""

    event = ""
    data: List[str] = []
    async for raw in lines:
        line = raw.decode("utf-8", "replace")
        if not line:
            if data:
                yield event or "message", "\n".join(data)
            event, data = "", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        yield event or "message", "\n".join(data)


def _premature_end(provider: str, marker: str) -> LLMTransportError:
    # A clean EOF before the completion marker is a truncated answer, not a
    # short one. Retryable: the stream loop retries/falls back only while no
    # text has been emitted, and raises otherwise.
    return LLMTransportError(f"{provider} stream ended before its completion marker ({marker})", retryable=True)


def _json_event(payload: str) -> dict:
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise LLMTransportError(f"malformed stream event: {exc.msg}") from None
    if not isinstance(data, dict):
        raise LLMTransportError("malformed stream event: not an object")
    return data


# ── Mock Backend ──────────────────────────────────────────────────────────────

class MockLLMBackend(BaseLLMBackend):
    """Deterministic mock — no network, no API key required. Used for tests."""

    name = "mock"
    model = "mock"

    def __init__(self, response_prefix: str = "[MOCK] ") -> None:
        self._prefix = response_prefix

    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        last = messages[-1]["content"] if messages else ""
        content = f"{self._prefix}Response to: {last[:80]}"
        words_in = len(last.split())
        return LLMResponse(content=content, model="mock", backend="mock",
                           tokens_used=words_in, input_tokens=words_in, duration_ms=1.0)

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        resp = await self.chat(messages, **kwargs)
        for word in resp.content.split():
            yield word + " "
            await asyncio.sleep(0)


# ── HTTP base ────────────────────────────────────────────────────────────────

Sleep = Callable[[float], Awaitable[None]]


class _HTTPBackend(BaseLLMBackend):
    name = "http"

    def __init__(
        self,
        *,
        model: str,
        api_url: str,
        api_key: str = "",
        timeout: float = 30,
        max_tokens: int = 2048,
        retry: Optional[RetryPolicy] = None,
        stream_timeout: Optional[float] = None,
        sleep: Sleep = asyncio.sleep,
        rand: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
            raise ValueError("timeout must be a number in (0, 3600]")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 1_000_000:
            raise ValueError("max_tokens must be an integer in [1, 1000000]")
        parts = urlsplit(api_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"api_url must be an absolute http(s) URL, got {api_url!r}")
        self.model = model
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key or ""
        self.timeout = float(timeout)
        self.max_tokens = max_tokens
        self.retry = retry or RetryPolicy()
        self.stream_timeout = float(stream_timeout) if stream_timeout else max(self.timeout * 10, 300.0)
        self._sleep = sleep
        self._rand = rand
        self._clock = clock

    # Subclasses define these three.
    def _endpoint(self) -> str:
        raise NotImplementedError

    def _headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json"}

    def _payload(self, messages: List[dict], *, stream: bool) -> dict:
        raise NotImplementedError

    def _parse(self, data: dict) -> LLMResponse:
        raise NotImplementedError

    def _tokens(self, lines: AsyncIterator[bytes], usage: "StreamUsage") -> AsyncIterator[str]:
        raise NotImplementedError

    def _redact(self, text: str) -> str:
        if self.api_key and len(self.api_key) >= 4:
            text = text.replace(self.api_key, "[redacted]")
        return text

    def _failure(self, message: str, start: float, **extra) -> LLMResponse:
        response = LLMResponse(
            content="", model=self.model, backend=self.name, success=False,
            error=self._redact(message), duration_ms=(time.perf_counter() - start) * 1000, **extra,
        )
        response.retries = max(0, response.attempts - 1)
        return response

    def _redacted(self, error: LLMTransportError) -> LLMTransportError:
        """Same classification, message with the API key removed."""
        message = self._redact(str(error))
        if message == str(error):
            return error
        clean = LLMTransportError(message, status=error.status, retryable=error.retryable,
                                  retry_after=error.retry_after)
        clean.attempts = error.attempts
        return clean

    def _classify(self, exc: BaseException, timeout: float) -> LLMTransportError:
        if isinstance(exc, LLMTransportError):
            return exc
        if isinstance(exc, asyncio.TimeoutError):
            return LLMTransportError(f"timed out after {timeout:.1f}s", retryable=True)
        if isinstance(exc, (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError)):
            return LLMTransportError(
                f"connection error: {type(exc).__name__}: {self._redact(_clean_text(str(exc)))}", retryable=True
            )
        if isinstance(exc, aiohttp.ClientError):
            return LLMTransportError(f"client error: {type(exc).__name__}: {self._redact(_clean_text(str(exc)))}")
        raise exc

    async def _status_error(self, resp) -> LLMTransportError:
        body = await _read_prefix(resp, MAX_ERROR_BODY_BYTES)
        detail = self._redact(_error_detail(body)) if body else (resp.reason or "")
        return LLMTransportError(
            f"HTTP {resp.status}: {detail}".rstrip(": "),
            status=resp.status,
            retryable=resp.status in RETRYABLE_STATUS,
            retry_after=parse_retry_after(resp.headers),
        )

    async def _attempt(self, payload: dict, timeout: float) -> Tuple[dict, int]:
        try:
            async with aiohttp.ClientSession(
                headers=self._headers(), timeout=aiohttp.ClientTimeout(total=timeout)
            ) as session:
                async with session.post(self._endpoint(), json=payload, allow_redirects=False) as resp:
                    if not 200 <= resp.status < 300:
                        raise await self._status_error(resp)
                    body = await _read_capped(resp, MAX_RESPONSE_BYTES)
                    try:
                        data = json.loads(body)
                    except ValueError:
                        raise LLMTransportError("invalid JSON in response body", status=resp.status) from None
                    if not isinstance(data, dict):
                        raise LLMTransportError("response body is not a JSON object", status=resp.status)
                    return data, resp.status
        except Exception as exc:  # noqa: BLE001 - classified or re-raised below
            raise self._classify(exc, timeout) from None

    async def _next_delay(self, exc: LLMTransportError, attempts: int, deadline: float) -> Optional[float]:
        """Backoff before the next attempt, or ``None`` to give up now."""
        if not exc.retryable or attempts >= self.retry.max_attempts:
            return None
        delay = self.retry.backoff(attempts - 1, self._rand)
        if exc.retry_after is not None:
            # A server asking for a longer pause than we accept will not have
            # recovered sooner; fail now so a fallback backend can answer.
            if exc.retry_after > self.retry.max_delay:
                return None
            delay = max(delay, exc.retry_after)
        if self._clock() + delay >= deadline:
            return None
        return delay

    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        start = time.perf_counter()
        if not AIOHTTP_AVAILABLE:
            return self._failure("aiohttp not installed", start)
        payload = self._payload(messages, stream=False)
        deadline = self._clock() + self.retry.budget(self.timeout)
        attempts = 0
        while True:
            attempts += 1
            remaining = deadline - self._clock()
            if remaining <= 0:
                return self._failure("deadline exceeded", start, attempts=attempts - 1, retryable=True)
            try:
                data, status = await self._attempt(payload, min(self.timeout, remaining))
            except LLMTransportError as exc:
                delay = await self._next_delay(exc, attempts, deadline)
                if delay is None:
                    return self._failure(
                        str(exc), start, attempts=attempts, status_code=exc.status, retryable=exc.retryable,
                    )
                await self._sleep(delay)
                continue
            try:
                response = self._parse(data)
            except LLMTransportError as exc:
                return self._failure(str(exc), start, attempts=attempts, status_code=status)
            response.attempts = attempts
            response.retries = attempts - 1
            response.status_code = status
            response.duration_ms = (time.perf_counter() - start) * 1000
            return response

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        if not AIOHTTP_AVAILABLE:
            raise LLMTransportError("aiohttp not installed")
        payload = self._payload(messages, stream=True)
        usage = kwargs.get("usage")
        if not isinstance(usage, StreamUsage):
            usage = StreamUsage()
        # One budget for the whole stream, retries included: keep-alive
        # comments reset sock_read, so only a shrinking total bounds an
        # attempt, and a retry can never extend the advertised budget.
        deadline = self._clock() + self.stream_timeout
        attempts = 0
        while True:
            attempts += 1
            emitted = False
            try:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise LLMTransportError(f"stream deadline of {self.stream_timeout:.0f}s exceeded", retryable=True)
                async with aiohttp.ClientSession(
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(
                        total=remaining, sock_connect=min(self.timeout, remaining),
                        sock_read=min(self.timeout, remaining),
                    ),
                ) as session:
                    async with session.post(self._endpoint(), json=payload, allow_redirects=False) as resp:
                        if not 200 <= resp.status < 300:
                            raise await self._status_error(resp)
                        async for token in self._tokens(iter_lines(resp.content.iter_any()), usage):
                            emitted = True
                            yield token
                        return
            except Exception as exc:  # noqa: BLE001 - classified or re-raised below
                # Provider error events can echo request headers; never let
                # the key leave in an exception (fallback aggregates these).
                error = self._redacted(self._classify(exc, self.timeout))
                error.attempts = attempts
                delay = None if emitted else await self._next_delay(error, attempts, deadline)
                if delay is None:
                    raise error from None
                usage.retries += 1
                await self._sleep(delay)


# ── Ollama Backend ────────────────────────────────────────────────────────────

class OllamaBackend(_HTTPBackend):
    name = "ollama"

    def __init__(self, model: str = DEFAULT_MODELS["ollama"], api_url: str = DEFAULT_OLLAMA_URL,
                 timeout: float = 30, **kwargs) -> None:
        super().__init__(model=model, api_url=api_url, timeout=timeout, **kwargs)

    def _endpoint(self) -> str:
        return f"{self.api_url}/api/chat"

    def _payload(self, messages: List[dict], *, stream: bool) -> dict:
        return {"model": self.model, "messages": messages, "stream": stream,
                "options": {"num_predict": self.max_tokens}}

    def _parse(self, data: dict) -> LLMResponse:
        if isinstance(data.get("error"), str):
            raise LLMTransportError(_clean_text(data["error"]))
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMTransportError("Ollama response has no message.content")
        prompt, completion = _nonneg_int(data.get("prompt_eval_count")), _nonneg_int(data.get("eval_count"))
        return LLMResponse(content=content, model=self.model, backend=self.name,
                           tokens_used=prompt + completion, input_tokens=prompt, output_tokens=completion,
                           stop_reason=str(data.get("done_reason") or ""))

    async def _tokens(self, lines: AsyncIterator[bytes], usage: StreamUsage) -> AsyncIterator[str]:
        async for line in lines:
            if not line.strip():
                continue
            data = _json_event(line.decode("utf-8", "replace"))
            if isinstance(data.get("error"), str):
                raise LLMTransportError(_clean_text(data["error"]))
            message = data.get("message")
            token = message.get("content") if isinstance(message, dict) else None
            if isinstance(token, str) and token:
                yield token
            if data.get("done") is True:
                usage.add(_nonneg_int(data.get("prompt_eval_count")), _nonneg_int(data.get("eval_count")))
                return
        raise _premature_end("Ollama", '"done": true')


# ── OpenAI Backend ────────────────────────────────────────────────────────────

class OpenAIBackend(_HTTPBackend):
    """OpenAI Chat Completions; also any compatible server (vLLM, llama.cpp, LM Studio)."""

    name = "openai"

    def __init__(self, model: str = DEFAULT_MODELS["openai"], api_key: str = "",
                 api_url: str = DEFAULT_OPENAI_URL, timeout: float = 30, **kwargs) -> None:
        super().__init__(model=model, api_url=api_url, api_key=api_key, timeout=timeout, **kwargs)
        self._official = (urlsplit(self.api_url).hostname or "").lower() == "api.openai.com"

    def _endpoint(self) -> str:
        return f"{self.api_url}/v1/chat/completions"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, messages: List[dict], *, stream: bool) -> dict:
        body: dict = {"model": self.model, "messages": messages}
        # The official API deprecated max_tokens (rejected by reasoning models);
        # compatible servers widely implement only max_tokens.
        body["max_completion_tokens" if self._official else "max_tokens"] = self.max_tokens
        if stream:
            body["stream"] = True
            if self._official:
                body["stream_options"] = {"include_usage": True}
        return body

    def _parse(self, data: dict) -> LLMResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMTransportError("OpenAI response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LLMTransportError("OpenAI response choice has no message")
        content = message.get("content")
        if content is None and isinstance(message.get("refusal"), str):
            raise LLMTransportError(f"model refused: {_clean_text(message['refusal'])}")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise LLMTransportError("OpenAI message.content is not a string")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        prompt, completion = _nonneg_int(usage.get("prompt_tokens")), _nonneg_int(usage.get("completion_tokens"))
        total = _nonneg_int(usage.get("total_tokens")) or prompt + completion
        return LLMResponse(content=content, model=self.model, backend=self.name, tokens_used=total,
                           input_tokens=prompt, output_tokens=completion,
                           stop_reason=str(choices[0].get("finish_reason") or ""))

    async def _tokens(self, lines: AsyncIterator[bytes], usage: StreamUsage) -> AsyncIterator[str]:
        seen_usage: list = [(0, 0)]
        try:
            async for token in self._openai_events(lines, seen_usage):
                yield token
        finally:
            usage.add(*seen_usage[-1])

    async def _openai_events(self, lines: AsyncIterator[bytes], seen_usage: list) -> AsyncIterator[str]:
        async for _event, payload in iter_sse(lines):
            if payload.strip() == "[DONE]":
                return
            data = _json_event(payload)
            if "error" in data:
                raise LLMTransportError(_error_detail(json.dumps(data).encode()))
            if isinstance(data.get("usage"), dict):
                # Cumulative per stream (include_usage chunk, or per-chunk stats
                # on some servers): keep the latest, never sum chunks.
                seen_usage.append((_nonneg_int(data["usage"].get("prompt_tokens")),
                                   _nonneg_int(data["usage"].get("completion_tokens"))))
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue  # the final include_usage chunk carries no choices
            delta = choices[0].get("delta")
            token = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(token, str) and token:
                yield token
        raise _premature_end("OpenAI", "data: [DONE]")


# ── Anthropic Backend ─────────────────────────────────────────────────────────

class AnthropicBackend(_HTTPBackend):
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODELS["anthropic"], api_key: str = "",
                 timeout: float = 30, api_url: str = DEFAULT_ANTHROPIC_URL, **kwargs) -> None:
        kwargs.setdefault("max_tokens", 1024)
        super().__init__(model=model, api_url=api_url, api_key=api_key, timeout=timeout, **kwargs)

    def _endpoint(self) -> str:
        return f"{self.api_url}/v1/messages"

    def _headers(self) -> Dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json"}

    def _payload(self, messages: List[dict], *, stream: bool) -> dict:
        # The Messages API takes the system prompt out of band; several system
        # messages are concatenated rather than letting the last one win.
        system = [m["content"] for m in messages if m.get("role") == "system" and m.get("content")]
        turns = [m for m in messages if m.get("role") != "system"]
        body: dict = {"model": self.model, "max_tokens": self.max_tokens, "messages": turns}
        if system:
            body["system"] = "\n\n".join(system)
        if stream:
            body["stream"] = True
        return body

    def _parse(self, data: dict) -> LLMResponse:
        if data.get("type") == "error":
            raise LLMTransportError(_error_detail(json.dumps(data).encode()))
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise LLMTransportError("Anthropic response has no content blocks")
        text = "".join(
            b["text"] for b in blocks
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        prompt = (_nonneg_int(usage.get("input_tokens")) + _nonneg_int(usage.get("cache_creation_input_tokens"))
                  + _nonneg_int(usage.get("cache_read_input_tokens")))
        completion = _nonneg_int(usage.get("output_tokens"))
        return LLMResponse(content=text, model=self.model, backend=self.name, tokens_used=prompt + completion,
                           input_tokens=prompt, output_tokens=completion,
                           stop_reason=str(data.get("stop_reason") or ""))

    async def _tokens(self, lines: AsyncIterator[bytes], usage: StreamUsage) -> AsyncIterator[str]:
        counts = {"input": 0, "output": 0}
        try:
            async for token in self._anthropic_events(lines, counts):
                yield token
        finally:
            usage.add(counts["input"], counts["output"])

    async def _anthropic_events(self, lines: AsyncIterator[bytes], counts: dict) -> AsyncIterator[str]:
        async for event, payload in iter_sse(lines):
            if event == "ping":
                continue
            data = _json_event(payload)
            kind = data.get("type") or event
            if kind == "message_start":
                message = data.get("message") if isinstance(data.get("message"), dict) else {}
                start = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                counts["input"] = (_nonneg_int(start.get("input_tokens"))
                                   + _nonneg_int(start.get("cache_creation_input_tokens"))
                                   + _nonneg_int(start.get("cache_read_input_tokens")))
                counts["output"] = _nonneg_int(start.get("output_tokens"))
            elif kind == "message_delta" and isinstance(data.get("usage"), dict):
                counts["output"] = _nonneg_int(data["usage"].get("output_tokens")) or counts["output"]
            if kind == "error":
                err = data.get("error") if isinstance(data.get("error"), dict) else {}
                raise LLMTransportError(
                    _error_detail(json.dumps(data).encode()),
                    retryable=err.get("type") in ("overloaded_error", "api_error"),
                )
            if kind == "content_block_delta":
                delta = data.get("delta")
                if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                    yield delta["text"]
            elif kind == "message_stop":
                return
        raise _premature_end("Anthropic", "message_stop")


# ── Composition: fallback chain and metering ─────────────────────────────────

class FallbackBackend(BaseLLMBackend):
    """Try backends in order; any failure moves on to the next one.

    Falling through on permanent errors is deliberate: a revoked key or a
    context-length rejection on the primary is exactly when a local model
    should still answer. ``fallback_trail`` records every skipped failure.
    """

    def __init__(self, backends: Sequence[BaseLLMBackend]) -> None:
        if not backends:
            raise ValueError("FallbackBackend needs at least one backend")
        self.backends = list(backends)
        self.name = "fallback(" + ",".join(b.name for b in self.backends) + ")"
        self.model = self.backends[0].model

    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        trail: List[str] = []
        retries = 0
        response: Optional[LLMResponse] = None
        for backend in self.backends:
            response = await backend.chat(messages, **kwargs)
            retries += response.retries
            if response.success:
                response.fallback_trail = trail + response.fallback_trail
                response.retries = retries
                return response
            trail.append(f"{response.backend}/{response.model}: {response.error}")
        assert response is not None
        response.fallback_trail = trail[:-1]
        response.retries = retries
        response.error = "all backends failed: " + " | ".join(trail)
        return response

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        errors: List[str] = []
        usage = kwargs.get("usage")
        for index, backend in enumerate(self.backends):
            emitted = False
            if index and isinstance(usage, StreamUsage):
                usage.fallbacks += 1
            try:
                async for token in backend.stream(messages, **kwargs):
                    emitted = True
                    yield token
                return
            except LLMTransportError as exc:
                if emitted:
                    raise
                errors.append(f"{backend.name}/{backend.model}: {exc}")
        raise LLMTransportError("all backends failed: " + " | ".join(errors))


@dataclass
class UsageTotals:
    requests: int = 0
    failures: int = 0
    retries: int = 0
    fallbacks: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    priced: bool = False

    def to_dict(self) -> dict:
        return {
            "requests": self.requests, "failures": self.failures, "retries": self.retries,
            "fallbacks": self.fallbacks,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "cost_usd": round(self.cost_usd, 6) if self.priced else None,
        }


class MeteredBackend(BaseLLMBackend):
    """Usage ledger shared by every engine that reuses ``LLMIntegration._backend``.

    Prices are operator-supplied (USD per million tokens); no price list is
    built in, because a stale table would report confident wrong numbers.
    """

    def __init__(self, inner: BaseLLMBackend, *, price_input_per_mtok: float = 0.0,
                 price_output_per_mtok: float = 0.0) -> None:
        for name, value in (("price_input_per_mtok", price_input_per_mtok), ("price_output_per_mtok", price_output_per_mtok)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < 1e6:
                raise ValueError(f"{name} must be a number in [0, 1e6)")
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self._prices = (float(price_input_per_mtok), float(price_output_per_mtok))
        self._priced = any(self._prices)
        self._totals = UsageTotals(priced=self._priced)
        self._lock = threading.Lock()

    def cost(self, input_tokens: int, output_tokens: int) -> Optional[float]:
        if not self._priced:
            return None
        return (input_tokens * self._prices[0] + output_tokens * self._prices[1]) / 1_000_000

    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        response = await self.inner.chat(messages, **kwargs)
        response.cost_usd = self.cost(response.input_tokens, response.output_tokens)
        with self._lock:
            totals = self._totals
            totals.requests += 1
            totals.failures += 0 if response.success else 1
            totals.retries += response.retries
            totals.fallbacks += len(response.fallback_trail)
            totals.input_tokens += response.input_tokens
            totals.output_tokens += response.output_tokens
            totals.cost_usd += response.cost_usd or 0.0
        return response

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        usage = kwargs.get("usage")
        if not isinstance(usage, StreamUsage):
            usage = StreamUsage()
        kwargs["usage"] = usage
        failed = True
        try:
            async for token in self.inner.stream(messages, **kwargs):
                yield token
            failed = False
        finally:
            # Recorded on success, failure and abandonment alike.
            cost = self.cost(usage.input_tokens, usage.output_tokens)
            with self._lock:
                totals = self._totals
                totals.requests += 1
                totals.failures += 1 if failed else 0
                totals.retries += usage.retries
                totals.fallbacks += usage.fallbacks
                totals.input_tokens += usage.input_tokens
                totals.output_tokens += usage.output_tokens
                totals.cost_usd += cost or 0.0

    def usage(self) -> dict:
        with self._lock:
            return self._totals.to_dict()


# ── Factory ───────────────────────────────────────────────────────────────────

def _coerce_int(value: object, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"llm.{name} must be an integer")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"llm.{name} must be an integer, got {value!r}") from None
    if not lo <= number <= hi:
        raise ValueError(f"llm.{name} must be in [{lo}, {hi}], got {number}")
    return number


def _coerce_float(value: object, name: str, lo: float, hi: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"llm.{name} must be a number")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"llm.{name} must be a number, got {value!r}") from None
    if not lo <= number <= hi:
        raise ValueError(f"llm.{name} must be in [{lo}, {hi}], got {number}")
    return number


def build_backend(
    backend: str,
    *,
    model: str = "",
    api_url: str = "",
    api_key: str = "",
    timeout: float = 30,
    max_tokens: int = 2048,
    retry: Optional[RetryPolicy] = None,
    **kwargs,
) -> BaseLLMBackend:
    name = (backend or "").strip().lower()
    if name == "mock":
        return MockLLMBackend()
    if name not in DEFAULT_MODELS:
        raise ValueError(f"unknown LLM backend {backend!r}; expected mock, ollama, openai or anthropic")
    # Only an omitted model is filled in; an explicit one (even "llama3.2" on
    # an OpenAI-compatible server) is sent as configured.
    if not model:
        model = DEFAULT_MODELS[name]
    # Like the model, an omitted URL gets the backend default; an explicit one
    # (e.g. Ollama's OpenAI-compatible endpoint on localhost) is kept, so a
    # local configuration can never be silently redirected to a hosted API.
    common = dict(model=model, timeout=timeout, max_tokens=max_tokens, retry=retry, **kwargs)
    if name == "ollama":
        return OllamaBackend(api_url=api_url or DEFAULT_OLLAMA_URL, **common)
    if name == "openai":
        return OpenAIBackend(api_key=api_key, api_url=api_url or DEFAULT_OPENAI_URL, **common)
    return AnthropicBackend(api_key=api_key, api_url=api_url or DEFAULT_ANTHROPIC_URL, **common)


def backend_from_config(config, *, environ: Optional[Dict[str, str]] = None) -> BaseLLMBackend:
    """Build the metered (and optionally fallback-chained) backend for ``LLMConfig``."""

    import os

    env = os.environ if environ is None else environ
    timeout = _coerce_float(getattr(config, "timeout", 30), "timeout", 0.001, 3600)
    max_tokens = _coerce_int(getattr(config, "max_tokens", 2048), "max_tokens", 1, 1_000_000)
    retry = RetryPolicy(
        max_attempts=1 + _coerce_int(getattr(config, "max_retries", 2), "max_retries", 0, 10),
        base_delay=_coerce_float(getattr(config, "retry_base_delay", 0.5), "retry_base_delay", 0, 300),
        max_delay=_coerce_float(getattr(config, "retry_max_delay", 8.0), "retry_max_delay", 0, 300),
    )
    primary = build_backend(
        getattr(config, "backend", "mock"), model=getattr(config, "model", ""),
        api_url=getattr(config, "api_url", ""), api_key=getattr(config, "api_key", ""),
        timeout=timeout, max_tokens=max_tokens, retry=retry,
    )
    chain: List[BaseLLMBackend] = [primary]
    fallbacks = getattr(config, "fallbacks", None) or []
    if not isinstance(fallbacks, (list, tuple)):
        raise ValueError("llm.fallbacks must be a list of mappings")
    for index, spec in enumerate(fallbacks):
        if not isinstance(spec, dict) or "backend" not in spec:
            raise ValueError(f"llm.fallbacks[{index}] must be a mapping with a 'backend' key")
        unknown = set(spec) - {"backend", "model", "api_url", "api_key", "api_key_env", "timeout", "max_tokens"}
        if unknown:
            raise ValueError(f"llm.fallbacks[{index}] has unknown keys: {sorted(unknown)}")
        key = spec.get("api_key") or ""
        if not key and spec.get("api_key_env"):
            key = env.get(str(spec["api_key_env"]), "")
        chain.append(build_backend(
            str(spec["backend"]), model=str(spec.get("model") or ""), api_url=str(spec.get("api_url") or ""),
            api_key=str(key),
            timeout=_coerce_float(spec.get("timeout", timeout), f"fallbacks[{index}].timeout", 0.001, 3600),
            max_tokens=_coerce_int(spec.get("max_tokens", max_tokens), f"fallbacks[{index}].max_tokens", 1, 1_000_000),
            retry=retry,
        ))
    inner: BaseLLMBackend = chain[0] if len(chain) == 1 else FallbackBackend(chain)
    return MeteredBackend(
        inner,
        price_input_per_mtok=_coerce_float(getattr(config, "price_input_per_mtok", 0.0), "price_input_per_mtok", 0, 1e6 - 1),
        price_output_per_mtok=_coerce_float(getattr(config, "price_output_per_mtok", 0.0), "price_output_per_mtok", 0, 1e6 - 1),
    )


class LLMIntegration:
    """High-level LLM interface with conversation history."""

    def __init__(self, backend: BaseLLMBackend, system_prompt: str = "", max_history: int = 100, max_history_tokens: int = 4096) -> None:
        self._backend = backend
        self._system_prompt = system_prompt or (
            "You are BotBoy, a helpful AI agent. Be concise and accurate."
        )
        self._max_history = max_history
        self._max_history_tokens = max_history_tokens
        self._history: List[dict] = []

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def _prune_history(self) -> None:
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        # Running total: O(n) instead of re-summing the window per pop.
        total = sum(self._estimate_tokens(m.get("content", "")) for m in self._history)
        drop = 0
        while drop < len(self._history) and total > self._max_history_tokens:
            total -= self._estimate_tokens(self._history[drop].get("content", ""))
            drop += 1
        if drop:
            del self._history[:drop]

    @classmethod
    def from_config(cls, config) -> "LLMIntegration":
        max_context = getattr(config, "max_context_tokens", 4096)
        return cls(backend=backend_from_config(config),
                   max_history_tokens=max_context if isinstance(max_context, int) and max_context > 0 else 4096)

    async def chat(self, user_message: str, use_history: bool = True) -> LLMResponse:
        messages = [{"role": "system", "content": self._system_prompt}]
        if use_history:
            messages.extend(self._history[-10:])  # last 5 exchanges
        messages.append({"role": "user", "content": user_message})

        response = await self._backend.chat(messages)

        if response.success and use_history:
            self._history.append({"role": "user", "content": user_message})
            self._history.append({"role": "assistant", "content": response.content})
            self._prune_history()

        return response

    async def stream(self, user_message: str) -> AsyncIterator[str]:
        messages = [{"role": "system", "content": self._system_prompt}]
        messages.extend(self._history[-10:])
        messages.append({"role": "user", "content": user_message})
        chunks: list[str] = []
        # A failed stream raises LLMTransportError and records nothing, so a
        # partial answer never becomes conversation history.
        async for chunk in self._backend.stream(messages):
            chunks.append(chunk)
            yield chunk
        if chunks:
            self._history.append({"role": "user", "content": user_message})
            self._history.append({"role": "assistant", "content": "".join(chunks)})
            self._prune_history()

    def clear_history(self) -> None:
        self._history.clear()

    def usage(self) -> Optional[dict]:
        meter = getattr(self._backend, "usage", None)
        return meter() if callable(meter) else None

    @property
    def backend_name(self) -> str:
        name = getattr(self._backend, "name", "")
        if name and name != "base":
            return name
        name = type(self._backend).__name__.replace("Backend", "").replace("LLM", "").lower()
        return name or "unknown"
