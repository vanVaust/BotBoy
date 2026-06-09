"""LLM Integration — Ollama / OpenAI / Anthropic / Mock backends."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


@dataclass
class LLMResponse:
    content: str
    model: str
    backend: str
    tokens_used: int = 0
    duration_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


class BaseLLMBackend:
    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""  # make it a generator


# ── Mock Backend ──────────────────────────────────────────────────────────────

class MockLLMBackend(BaseLLMBackend):
    """Deterministic mock — no network, no API key required. Used for tests."""

    def __init__(self, response_prefix: str = "[MOCK] ") -> None:
        self._prefix = response_prefix

    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        last = messages[-1]["content"] if messages else ""
        content = f"{self._prefix}Response to: {last[:80]}"
        return LLMResponse(content=content, model="mock", backend="mock",
                           tokens_used=len(last.split()), duration_ms=1.0)

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        resp = await self.chat(messages, **kwargs)
        for word in resp.content.split():
            yield word + " "
            await asyncio.sleep(0)


# ── Ollama Backend ────────────────────────────────────────────────────────────

class OllamaBackend(BaseLLMBackend):
    def __init__(self, model: str = "llama3.2", api_url: str = "http://localhost:11434",
                 timeout: int = 30) -> None:
        self.model = model
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        if not AIOHTTP_AVAILABLE:
            return LLMResponse(content="", model=self.model, backend="ollama", success=False,
                               error="aiohttp not installed")
        start = time.perf_counter()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/api/chat",
                    json={"model": self.model, "messages": messages, "stream": False},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    data = await resp.json()
                    content = data.get("message", {}).get("content", "")
                    return LLMResponse(
                        content=content, model=self.model, backend="ollama",
                        tokens_used=data.get("eval_count", 0),
                        duration_ms=(time.perf_counter() - start) * 1000,
                    )
        except Exception as e:
            return LLMResponse(content="", model=self.model, backend="ollama",
                               success=False, error=str(e))

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        if not AIOHTTP_AVAILABLE:
            yield "aiohttp not installed"
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/api/chat",
                    json={"model": self.model, "messages": messages, "stream": True},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    async for line in resp.content:
                        line = line.decode().strip()
                        if line:
                            try:
                                data = json.loads(line)
                                token = data.get("message", {}).get("content", "")
                                if token:
                                    yield token
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            yield f"[Error: {e}]"


# ── OpenAI Backend ────────────────────────────────────────────────────────────

class OpenAIBackend(BaseLLMBackend):
    def __init__(self, model: str = "gpt-4o-mini", api_key: str = "",
                 api_url: str = "https://api.openai.com", timeout: int = 30) -> None:
        self.model = model
        self.api_key = api_key
        self.api_url = api_url
        self.timeout = timeout

    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        if not AIOHTTP_AVAILABLE:
            return LLMResponse(content="", model=self.model, backend="openai", success=False,
                               error="aiohttp not installed")
        start = time.perf_counter()
        try:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(
                    f"{self.api_url}/v1/chat/completions",
                    json={"model": self.model, "messages": messages},
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    return LLMResponse(
                        content=content, model=self.model, backend="openai",
                        tokens_used=usage.get("total_tokens", 0),
                        duration_ms=(time.perf_counter() - start) * 1000,
                    )
        except Exception as e:
            return LLMResponse(content="", model=self.model, backend="openai",
                               success=False, error=str(e))

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        yield "[OpenAI streaming not yet implemented]"


# ── Anthropic Backend ─────────────────────────────────────────────────────────

class AnthropicBackend(BaseLLMBackend):
    def __init__(self, model: str = "claude-haiku-4-5-20251001", api_key: str = "",
                 timeout: int = 30) -> None:
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    async def chat(self, messages: List[dict], **kwargs) -> LLMResponse:
        if not AIOHTTP_AVAILABLE:
            return LLMResponse(content="", model=self.model, backend="anthropic", success=False,
                               error="aiohttp not installed")
        start = time.perf_counter()
        try:
            # Separate system message if present
            system = ""
            user_messages = []
            for m in messages:
                if m["role"] == "system":
                    system = m["content"]
                else:
                    user_messages.append(m)

            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            body: dict = {"model": self.model, "max_tokens": 1024, "messages": user_messages}
            if system:
                body["system"] = system

            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(
                    "https://api.anthropic.com/v1/messages",
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    data = await resp.json()
                    content = data["content"][0]["text"]
                    usage = data.get("usage", {})
                    return LLMResponse(
                        content=content, model=self.model, backend="anthropic",
                        tokens_used=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                        duration_ms=(time.perf_counter() - start) * 1000,
                    )
        except Exception as e:
            return LLMResponse(content="", model=self.model, backend="anthropic",
                               success=False, error=str(e))

    async def stream(self, messages: List[dict], **kwargs) -> AsyncIterator[str]:
        yield "[Anthropic streaming not yet implemented]"


# ── Factory ───────────────────────────────────────────────────────────────────

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
        # First enforce the message count limit
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
            
        # Then enforce token limit
        while self._history:
            total_tokens = sum(self._estimate_tokens(m.get("content", "")) for m in self._history)
            if total_tokens <= self._max_history_tokens:
                break
            # Remove oldest user/assistant pair or message to free up tokens
            self._history.pop(0)

    @classmethod
    def from_config(cls, config) -> "LLMIntegration":
        backend_name = getattr(config, "backend", "mock")
        model = getattr(config, "model", "llama3.2")
        api_url = getattr(config, "api_url", "http://localhost:11434")
        api_key = getattr(config, "api_key", "")
        timeout = getattr(config, "timeout", 30)

        if backend_name == "ollama":
            backend = OllamaBackend(model=model, api_url=api_url, timeout=timeout)
        elif backend_name == "openai":
            backend = OpenAIBackend(model=model, api_key=api_key, timeout=timeout)
        elif backend_name == "anthropic":
            backend = AnthropicBackend(model=model, api_key=api_key, timeout=timeout)
        else:
            backend = MockLLMBackend()

        return cls(backend=backend)

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
        async for chunk in self._backend.stream(messages):
            chunks.append(chunk)
            yield chunk
        if chunks:
            self._history.append({"role": "user", "content": user_message})
            self._history.append({"role": "assistant", "content": "".join(chunks)})
            self._prune_history()

    def clear_history(self) -> None:
        self._history.clear()

    @property
    def backend_name(self) -> str:
        name = type(self._backend).__name__.replace("Backend", "").replace("LLM", "").lower()
        return name or "unknown"
