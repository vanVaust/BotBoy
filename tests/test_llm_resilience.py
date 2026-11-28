"""LLM transport hardening against a real local aiohttp server.

Every backend talks HTTP to 127.0.0.1; responses are scripted per request so
status handling, retry/backoff, Retry-After, caps, redirects, redaction,
streaming (NDJSON + SSE) and fallback are exercised over real sockets.
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import mock

from aiohttp import web

from botboy.bootstrap_service import BootstrapService
from botboy.core.config import BotBoyConfig, LLMConfig
from botboy.llm import integration as llm
from botboy.llm.integration import (
    AnthropicBackend,
    FallbackBackend,
    LLMIntegration,
    LLMTransportError,
    MeteredBackend,
    MockLLMBackend,
    OllamaBackend,
    OpenAIBackend,
    RetryPolicy,
    backend_from_config,
    build_backend,
    iter_lines,
    iter_sse,
    parse_retry_after,
)

SECRET = "sk-test-SECRET-0123456789"
MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]


class _Script:
    """Queue of handlers; each request pops one (the last one repeats)."""

    def __init__(self) -> None:
        self.handlers = []
        self.requests = []

    def push(self, *handlers):
        self.handlers.extend(handlers)

    async def __call__(self, request: web.Request) -> web.StreamResponse:
        body = await request.read()
        self.requests.append({"path": request.path, "headers": dict(request.headers),
                              "json": json.loads(body) if body else None})
        handler = self.handlers.pop(0) if len(self.handlers) > 1 else self.handlers[0]
        return await handler(request)


def json_reply(payload, status=200, headers=None):
    async def handler(_request):
        return web.json_response(payload, status=status, headers=headers)
    return handler


def raw_reply(body: bytes, status=200, content_type="text/html", headers=None):
    async def handler(_request):
        return web.Response(body=body, status=status, content_type=content_type, headers=headers)
    return handler


def stream_reply(pieces, *, content_type="text/event-stream", abort_after=None):
    async def handler(request):
        resp = web.StreamResponse(status=200, headers={"Content-Type": content_type})
        await resp.prepare(request)
        for index, piece in enumerate(pieces):
            await resp.write(piece)
            await asyncio.sleep(0)
            if abort_after is not None and index + 1 >= abort_after:
                request.transport.close()  # hard drop mid-stream
                return resp
        await resp.write_eof()
        return resp
    return handler


def slow_reply(seconds):
    async def handler(_request):
        await asyncio.sleep(seconds)
        return web.json_response({"message": {"content": "late"}})
    return handler


def ollama_ok(text="hello", prompt=3, completion=4):
    return json_reply({"message": {"role": "assistant", "content": text}, "done": True,
                       "done_reason": "stop", "prompt_eval_count": prompt, "eval_count": completion})


class _ServerCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.script = _Script()
        self.redirect_target_hits = 0
        app = web.Application()

        async def target(_request):
            self.redirect_target_hits += 1
            return web.json_response({"message": {"content": "redirected"}})

        app.router.add_route("POST", "/elsewhere/api/chat", target)
        app.router.add_route("POST", "/{tail:.*}", self.script)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.sleeps = []

    async def asyncTearDown(self) -> None:
        await self.runner.cleanup()

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def kw(self, attempts=3, **extra):
        base = dict(retry=RetryPolicy(max_attempts=attempts, base_delay=0.01, max_delay=2.0),
                    sleep=self._sleep, rand=lambda: 1.0)
        base.update(extra)
        return base


class ChatTransportTest(_ServerCase):
    async def test_429_with_retry_after_is_retried_and_honoured(self) -> None:
        self.script.push(json_reply({"error": "slow down"}, status=429, headers={"Retry-After": "1"}), ollama_ok())
        resp = await OllamaBackend(api_url=self.url, **self.kw()).chat(MSGS)
        self.assertTrue(resp.success, resp.error)
        self.assertEqual((resp.attempts, resp.status_code, resp.content), (2, 200, "hello"))
        self.assertEqual(self.sleeps, [1.0])  # max(jittered 0.01, Retry-After 1)
        self.assertEqual((resp.input_tokens, resp.output_tokens, resp.tokens_used), (3, 4, 7))

    async def test_retry_after_ms_header_takes_precedence(self) -> None:
        self.script.push(json_reply({}, status=503, headers={"retry-after-ms": "250", "Retry-After": "2"}), ollama_ok())
        resp = await OllamaBackend(api_url=self.url, **self.kw()).chat(MSGS)
        self.assertTrue(resp.success)
        self.assertEqual(self.sleeps, [0.25])

    async def test_persistent_5xx_stops_after_max_attempts(self) -> None:
        self.script.push(json_reply({"error": {"type": "api_error", "message": "boom"}}, status=500))
        resp = await OllamaBackend(api_url=self.url, **self.kw(attempts=3)).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertEqual((resp.attempts, resp.status_code, resp.retryable), (3, 500, True))
        self.assertEqual(len(self.script.requests), 3)
        self.assertEqual(len(self.sleeps), 2)
        self.assertIn("HTTP 500: api_error: boom", resp.error)

    async def test_backoff_is_exponential_and_capped(self) -> None:
        self.script.push(json_reply({}, status=502))
        backend = OllamaBackend(api_url=self.url, retry=RetryPolicy(max_attempts=6, base_delay=0.1, max_delay=0.5),
                                sleep=self._sleep, rand=lambda: 1.0)
        await backend.chat(MSGS)
        self.assertEqual(self.sleeps, [0.1, 0.2, 0.4, 0.5, 0.5])

    async def test_permanent_4xx_is_not_retried(self) -> None:
        self.script.push(json_reply({"error": {"type": "invalid_request_error", "message": "bad field"}}, status=400))
        resp = await AnthropicBackend(api_key=SECRET, api_url=self.url, **self.kw()).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertEqual((resp.attempts, resp.retryable, resp.status_code), (1, False, 400))
        self.assertEqual(self.sleeps, [])
        self.assertIn("invalid_request_error: bad field", resp.error)

    async def test_retry_after_longer_than_max_delay_fails_fast_for_fallback(self) -> None:
        self.script.push(json_reply({}, status=429, headers={"Retry-After": "120"}), ollama_ok())
        # timeout=100 makes the overall deadline (>300 s) not the limiting factor.
        resp = await OllamaBackend(api_url=self.url, timeout=100, **self.kw()).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertEqual((resp.attempts, resp.retryable), (1, True))
        self.assertEqual(self.sleeps, [])

    async def test_html_error_page_is_reported_not_parsed(self) -> None:
        self.script.push(raw_reply(b"<html><body>\x1b[31mBad Gateway</body></html>", status=502))
        resp = await OllamaBackend(api_url=self.url, **self.kw(attempts=1)).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertIn("HTTP 502", resp.error)
        self.assertNotIn("\x1b", resp.error)

    async def test_invalid_json_success_body_is_permanent_failure(self) -> None:
        self.script.push(raw_reply(b"{not json", status=200, content_type="application/json"))
        resp = await OllamaBackend(api_url=self.url, **self.kw()).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertEqual(resp.attempts, 1)
        self.assertIn("invalid JSON", resp.error)

    async def test_missing_content_is_failure_not_empty_answer(self) -> None:
        self.script.push(json_reply({"done": True}))
        resp = await OllamaBackend(api_url=self.url, **self.kw()).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertIn("no message.content", resp.error)

    async def test_declared_oversized_body_is_refused(self) -> None:
        self.script.push(ollama_ok(text="x" * 5000))
        with mock.patch.object(llm, "MAX_RESPONSE_BYTES", 1024):
            resp = await OllamaBackend(api_url=self.url, **self.kw()).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertIn("too large", resp.error)

    async def test_chunked_oversized_body_is_refused_while_reading(self) -> None:
        self.script.push(stream_reply([b'{"message":{"content":"' + b"y" * 4096] * 4 + [b'"}}'],
                                      content_type="application/json"))
        with mock.patch.object(llm, "MAX_RESPONSE_BYTES", 8192):
            resp = await OllamaBackend(api_url=self.url, **self.kw()).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertIn("too large", resp.error)

    async def test_redirects_are_not_followed_and_credentials_stay_put(self) -> None:
        async def redirect(_request):
            raise web.HTTPTemporaryRedirect(location=f"{self.url}/elsewhere/api/chat")
        self.script.push(redirect)
        resp = await OllamaBackend(api_url=self.url, **self.kw()).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertEqual((resp.status_code, resp.retryable), (307, False))
        self.assertEqual(self.redirect_target_hits, 0)

    async def test_api_key_is_redacted_from_echoed_errors(self) -> None:
        self.script.push(json_reply({"error": {"message": f"key {SECRET} is revoked"}}, status=401))
        resp = await OpenAIBackend(api_key=SECRET, api_url=self.url, **self.kw()).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertNotIn(SECRET, resp.error)
        self.assertIn("[redacted]", resp.error)
        self.assertEqual(self.script.requests[0]["headers"]["Authorization"], f"Bearer {SECRET}")

    async def test_connection_refused_is_retryable(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            dead = sock.getsockname()[1]
        resp = await OllamaBackend(api_url=f"http://127.0.0.1:{dead}", **self.kw(attempts=2)).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertEqual((resp.attempts, resp.retryable), (2, True))
        self.assertIn("connection error", resp.error)

    async def test_timeout_is_retryable_and_bounded(self) -> None:
        self.script.push(slow_reply(5))
        start = time.perf_counter()
        resp = await OllamaBackend(api_url=self.url, timeout=0.2, **self.kw(attempts=2)).chat(MSGS)
        self.assertLess(time.perf_counter() - start, 3)
        self.assertFalse(resp.success)
        self.assertEqual((resp.attempts, resp.retryable), (2, True))
        self.assertIn("timed out", resp.error)


class ProviderShapeTest(_ServerCase):
    async def test_anthropic_request_and_response_shape(self) -> None:
        self.script.push(json_reply({
            "type": "message", "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "Hal"},
                        {"type": "text", "text": "lo"}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "cache_creation_input_tokens": 5,
                      "output_tokens": 7},
        }))
        messages = [{"role": "system", "content": "A"}, {"role": "system", "content": "B"},
                    {"role": "user", "content": "q"}]
        resp = await AnthropicBackend(api_key=SECRET, api_url=self.url, max_tokens=321, **self.kw()).chat(messages)
        self.assertTrue(resp.success, resp.error)
        self.assertEqual((resp.content, resp.stop_reason), ("Hallo", "end_turn"))
        self.assertEqual((resp.input_tokens, resp.output_tokens, resp.tokens_used), (105, 7, 112))
        sent = self.script.requests[0]
        self.assertEqual(sent["path"], "/v1/messages")
        self.assertEqual(sent["headers"]["x-api-key"], SECRET)
        self.assertEqual(sent["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(sent["json"]["system"], "A\n\nB")
        self.assertEqual(sent["json"]["messages"], [{"role": "user", "content": "q"}])
        self.assertEqual(sent["json"]["max_tokens"], 321)

    async def test_openai_compatible_server_gets_max_tokens(self) -> None:
        self.script.push(json_reply({"choices": [{"message": {"content": "ok"}, "finish_reason": "length"}],
                                     "usage": {"prompt_tokens": 2, "completion_tokens": 9, "total_tokens": 11}}))
        resp = await OpenAIBackend(api_url=self.url, max_tokens=50, **self.kw()).chat(MSGS)
        self.assertTrue(resp.success, resp.error)
        self.assertEqual((resp.stop_reason, resp.tokens_used), ("length", 11))
        sent = self.script.requests[0]
        self.assertEqual(sent["json"]["max_tokens"], 50)
        self.assertNotIn("max_completion_tokens", sent["json"])
        self.assertNotIn("Authorization", sent["headers"])  # no key, no empty bearer

    def test_official_openai_uses_max_completion_tokens(self) -> None:
        payload = OpenAIBackend(api_key="k", max_tokens=77)._payload(MSGS, stream=True)
        self.assertEqual(payload["max_completion_tokens"], 77)
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(payload["stream_options"], {"include_usage": True})

    async def test_openai_refusal_is_a_failure(self) -> None:
        self.script.push(json_reply({"choices": [{"message": {"content": None, "refusal": "no"}}]}))
        resp = await OpenAIBackend(api_url=self.url, **self.kw()).chat(MSGS)
        self.assertFalse(resp.success)
        self.assertIn("model refused: no", resp.error)

    async def test_ollama_sends_num_predict(self) -> None:
        self.script.push(ollama_ok())
        await OllamaBackend(api_url=self.url, max_tokens=64, **self.kw()).chat(MSGS)
        self.assertEqual(self.script.requests[0]["json"]["options"], {"num_predict": 64})


class StreamTest(_ServerCase):
    async def _collect(self, backend):
        return [token async for token in backend.stream(MSGS)]

    async def test_openai_sse_split_across_arbitrary_chunks(self) -> None:
        events = b"".join(
            b"data: " + json.dumps({"choices": [{"delta": {"content": t}}]}).encode() + b"\r\n\r\n"
            for t in ["Hel", "lo", " World"]
        ) + b": keepalive\n\n" + b'data: {"choices":[],"usage":{"total_tokens":3}}\n\n' + b"data: [DONE]\n\n"
        pieces = [events[i:i + 3] for i in range(0, len(events), 3)]
        self.script.push(stream_reply(pieces))
        tokens = await self._collect(OpenAIBackend(api_url=self.url, **self.kw()))
        self.assertEqual("".join(tokens), "Hello World")
        self.assertTrue(self.script.requests[0]["json"]["stream"])

    async def test_anthropic_sse_events(self) -> None:
        def ev(name, data):
            return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
        body = (ev("message_start", {"type": "message_start"}) + ev("ping", {"type": "ping"})
                + ev("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Gr"}})
                + ev("content_block_delta", {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{"}})
                + ev("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "üß"}})
                + ev("message_stop", {"type": "message_stop"}))
        self.script.push(stream_reply([body]))
        tokens = await self._collect(AnthropicBackend(api_key=SECRET, api_url=self.url, **self.kw()))
        self.assertEqual("".join(tokens), "Grüß")

    async def test_ollama_ndjson_stream(self) -> None:
        lines = [json.dumps({"message": {"content": c}, "done": False}).encode() + b"\n" for c in ("a", "b")]
        lines.append(json.dumps({"done": True}).encode() + b"\n")
        self.script.push(stream_reply(lines, content_type="application/x-ndjson"))
        self.assertEqual(await self._collect(OllamaBackend(api_url=self.url, **self.kw())), ["a", "b"])

    async def test_stream_status_error_before_first_token_is_retried(self) -> None:
        self.script.push(json_reply({}, status=503),
                         stream_reply([b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n']))
        tokens = await self._collect(OpenAIBackend(api_url=self.url, **self.kw()))
        self.assertEqual(tokens, ["ok"])
        self.assertEqual(len(self.script.requests), 2)

    async def test_anthropic_overloaded_event_before_text_is_retried(self) -> None:
        err = b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"busy"}}\n\n'
        ok = (b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"x"}}\n\n'
              b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
        self.script.push(stream_reply([err]), stream_reply([ok]))
        tokens = await self._collect(AnthropicBackend(api_key=SECRET, api_url=self.url, **self.kw()))
        self.assertEqual(tokens, ["x"])

    async def test_mid_stream_failure_is_raised_not_retried(self) -> None:
        first = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        self.script.push(stream_reply([first, b'data: {"choi'], abort_after=2))
        got = []
        with self.assertRaises(LLMTransportError):
            async for token in OpenAIBackend(api_url=self.url, **self.kw()).stream(MSGS):
                got.append(token)
        self.assertEqual(got, ["partial"])
        self.assertEqual(len(self.script.requests), 1)

    async def test_malformed_event_raises(self) -> None:
        self.script.push(stream_reply([b"data: {broken\n\n"]))
        with self.assertRaises(LLMTransportError):
            await self._collect(OpenAIBackend(api_url=self.url, **self.kw(attempts=1)))

    async def test_stream_error_event_is_redacted(self) -> None:
        err = ('event: error\ndata: {"type":"error","error":{"type":"authentication_error",'
               f'"message":"bad key {SECRET}"}}}}\n\n').encode()
        self.script.push(stream_reply([err]))
        chain = FallbackBackend([AnthropicBackend(api_key=SECRET, api_url=self.url, **self.kw())])
        with self.assertRaises(LLMTransportError) as caught:
            await self._collect(chain)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertIn("[redacted]", str(caught.exception))

    async def test_truncated_stream_after_text_raises_and_is_not_recorded(self) -> None:
        self.script.push(stream_reply([b'data: {"choices":[{"delta":{"content":"half an ans"}}]}\n\n']))
        integration = LLMIntegration(OpenAIBackend(api_url=self.url, **self.kw()))
        got = []
        with self.assertRaisesRegex(LLMTransportError, "before its completion marker"):
            async for token in integration.stream("q"):
                got.append(token)
        self.assertEqual(got, ["half an ans"])
        self.assertEqual(integration._history, [])
        self.assertEqual(len(self.script.requests), 1)  # no retry once text was emitted

    async def test_empty_200_stream_is_retried_then_falls_back(self) -> None:
        self.script.push(stream_reply([]), stream_reply([]),
                         stream_reply([b'{"message":{"content":"ok"},"done":true}\n'], content_type="application/x-ndjson"))
        chain = FallbackBackend([AnthropicBackend(api_key=SECRET, api_url=self.url, **self.kw(attempts=2)),
                                 OllamaBackend(api_url=self.url, **self.kw())])
        self.assertEqual(await self._collect(chain), ["ok"])
        self.assertEqual(len(self.script.requests), 3)

    async def test_stream_budget_bounds_keepalive_attempts(self) -> None:
        async def keepalive(request):
            resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
            await resp.prepare(request)
            for _ in range(200):
                await resp.write(b": keep-alive\n\n")
                await asyncio.sleep(0.02)
            return resp
        self.script.push(keepalive)
        backend = OpenAIBackend(api_url=self.url, timeout=1, stream_timeout=0.5, **self.kw(attempts=3))
        start = time.perf_counter()
        with self.assertRaises(LLMTransportError):
            await self._collect(backend)
        self.assertLess(time.perf_counter() - start, 2.0)  # not 3 x 4 s of keep-alives

    async def test_stream_usage_reaches_the_meter(self) -> None:
        openai = (b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n'
                  b'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":3}}\n\ndata: [DONE]\n\n')
        anthropic = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
                     b'{"input_tokens":5,"cache_read_input_tokens":20,"output_tokens":1}}}\n\n'
                     b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"b"}}\n\n'
                     b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":9}}\n\n'
                     b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
        ollama = b'{"message":{"content":"c"},"done":true,"prompt_eval_count":7,"eval_count":2}\n'
        cases = [(OpenAIBackend(api_url=self.url, **self.kw()), openai, (11, 3)),
                 (AnthropicBackend(api_key=SECRET, api_url=self.url, **self.kw()), anthropic, (25, 9)),
                 (OllamaBackend(api_url=self.url, **self.kw()), ollama, (7, 2))]
        for backend, body, (tokens_in, tokens_out) in cases:
            with self.subTest(backend=backend.name):
                self.script.handlers.clear()
                self.script.push(stream_reply([body]))
                metered = MeteredBackend(backend, price_input_per_mtok=2.0, price_output_per_mtok=4.0)
                await self._collect(metered)
                usage = metered.usage()
                self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (tokens_in, tokens_out))
                self.assertAlmostEqual(usage["cost_usd"], (tokens_in * 2 + tokens_out * 4) / 1e6)
                self.assertEqual((usage["requests"], usage["failures"]), (1, 0))

    async def test_stream_retries_and_fallbacks_are_metered(self) -> None:
        self.script.push(json_reply({}, status=503), json_reply({}, status=400),
                         stream_reply([b'{"message":{"content":"z"},"done":true,"eval_count":1}\n']))
        metered = MeteredBackend(FallbackBackend([OpenAIBackend(api_url=self.url, **self.kw(attempts=2)),
                                                  OllamaBackend(api_url=self.url, **self.kw())]))
        self.assertEqual(await self._collect(metered), ["z"])
        usage = metered.usage()
        self.assertEqual((usage["retries"], usage["fallbacks"], usage["output_tokens"]), (1, 1, 1))

    async def test_failed_stream_does_not_enter_history(self) -> None:
        self.script.push(json_reply({"error": "nope"}, status=400))
        integration = LLMIntegration(OllamaBackend(api_url=self.url, **self.kw()))
        with self.assertRaises(LLMTransportError):
            async for _ in integration.stream("q"):
                pass
        self.assertEqual(integration._history, [])


class CompositionTest(_ServerCase):
    async def _collect(self, backend):
        return [token async for token in backend.stream(MSGS)]

    async def test_fallback_skips_failed_primary_and_records_trail(self) -> None:
        self.script.push(json_reply({"error": {"message": "invalid key"}}, status=401), ollama_ok("local"))
        chain = FallbackBackend([
            AnthropicBackend(api_key=SECRET, api_url=self.url, **self.kw()),
            OllamaBackend(api_url=self.url, **self.kw()),
        ])
        resp = await chain.chat(MSGS)
        self.assertTrue(resp.success, resp.error)
        self.assertEqual((resp.backend, resp.content), ("ollama", "local"))
        self.assertEqual(len(resp.fallback_trail), 1)
        self.assertIn("anthropic/", resp.fallback_trail[0])
        self.assertIn("HTTP 401", resp.fallback_trail[0])
        self.assertEqual(chain.name, "fallback(anthropic,ollama)")

    async def test_retries_are_aggregated_across_the_chain(self) -> None:
        self.script.push(json_reply({}, status=503), json_reply({}, status=503), json_reply({}, status=503),
                         ollama_ok("second"))
        metered = MeteredBackend(FallbackBackend([OpenAIBackend(api_url=self.url, **self.kw(attempts=3)),
                                                  OllamaBackend(api_url=self.url, **self.kw())]))
        resp = await metered.chat(MSGS)
        self.assertTrue(resp.success, resp.error)
        self.assertEqual((resp.attempts, resp.retries, len(resp.fallback_trail)), (1, 2, 1))
        usage = metered.usage()
        self.assertEqual((usage["retries"], usage["fallbacks"]), (2, 1))

    async def test_fallback_all_failed_reports_every_backend(self) -> None:
        self.script.push(json_reply({}, status=400))
        chain = FallbackBackend([OllamaBackend(api_url=self.url, **self.kw()),
                                 OpenAIBackend(api_url=self.url, **self.kw())])
        resp = await chain.chat(MSGS)
        self.assertFalse(resp.success)
        self.assertTrue(resp.error.startswith("all backends failed: ollama/"))
        self.assertIn("openai/", resp.error)

    async def test_stream_fallback_before_first_token(self) -> None:
        self.script.push(json_reply({}, status=400),
                         stream_reply([b'{"message":{"content":"z"},"done":true}\n'], content_type="application/x-ndjson"))
        chain = FallbackBackend([OpenAIBackend(api_url=self.url, **self.kw()), OllamaBackend(api_url=self.url, **self.kw())])
        self.assertEqual([t async for t in chain.stream(MSGS)], ["z"])

    async def test_meter_prices_and_totals(self) -> None:
        self.script.push(json_reply({}, status=503), ollama_ok(prompt=1_000_000, completion=500_000))
        metered = MeteredBackend(OllamaBackend(api_url=self.url, **self.kw()),
                                 price_input_per_mtok=3.0, price_output_per_mtok=15.0)
        resp = await metered.chat(MSGS)
        self.assertAlmostEqual(resp.cost_usd, 3.0 + 7.5)
        usage = metered.usage()
        self.assertEqual((usage["requests"], usage["retries"], usage["failures"]), (1, 1, 0))
        self.assertEqual(usage["total_tokens"], 1_500_000)
        self.assertAlmostEqual(usage["cost_usd"], 10.5)
        self.assertEqual(LLMIntegration(metered).backend_name, "ollama")

    async def test_unpriced_meter_reports_none_not_zero(self) -> None:
        metered = MeteredBackend(MockLLMBackend())
        resp = await metered.chat(MSGS)
        self.assertIsNone(resp.cost_usd)
        self.assertIsNone(metered.usage()["cost_usd"])


class ConfigTest(unittest.TestCase):
    def test_hosted_backend_ignores_ollama_defaults(self) -> None:
        backend = build_backend("anthropic", model="", api_url="", api_key="k")
        self.assertEqual(backend.model, llm.DEFAULT_MODELS["anthropic"])
        self.assertEqual(backend.api_url, "https://api.anthropic.com")
        self.assertEqual(backend_from_config(LLMConfig(backend="openai"), environ={}).inner.api_url,
                         "https://api.openai.com")
        self.assertEqual(backend_from_config(LLMConfig(backend="anthropic"), environ={}).model,
                         llm.DEFAULT_MODELS["anthropic"])
        self.assertEqual(backend_from_config(LLMConfig(backend="ollama"), environ={}).model, "llama3.2")

    def test_explicit_local_urls_are_never_replaced(self) -> None:
        chain = backend_from_config(LLMConfig(backend="anthropic", api_key="k", fallbacks=[
            {"backend": "openai", "api_url": "http://localhost:11434", "model": "llama3.2"}]), environ={}).inner
        local = chain.backends[1]
        self.assertEqual(local.api_url, "http://localhost:11434")
        self.assertEqual(local._endpoint(), "http://localhost:11434/v1/chat/completions")
        primary = backend_from_config(LLMConfig(backend="openai", api_url="http://localhost:11434"), environ={})
        self.assertEqual(primary.inner.api_url, "http://localhost:11434")

    def test_ollama_url_env_only_targets_ollama(self) -> None:
        from unittest import mock as _mock

        with _mock.patch.dict("os.environ", {"OLLAMA_URL": "http://10.0.0.9:11434"}, clear=False):
            for backend, expected in (("ollama", "http://10.0.0.9:11434"), ("openai", "")):
                cfg = BotBoyConfig()
                cfg.llm.backend = backend
                cfg._apply_env()
                self.assertEqual(cfg.llm.api_url, expected)

    def test_explicit_models_are_never_replaced(self) -> None:
        compat = build_backend("openai", model="llama3.2", api_url="http://127.0.0.1:8000")
        self.assertEqual(compat._payload(MSGS, stream=False)["model"], "llama3.2")
        chain = backend_from_config(LLMConfig(backend="ollama", fallbacks=[
            {"backend": "openai", "api_url": "http://127.0.0.1:8000", "model": "llama3.2"}]), environ={}).inner
        self.assertEqual(chain.backends[1].model, "llama3.2")
        openai = build_backend("openai", model="", api_url="http://10.0.0.5:8000")
        self.assertEqual((openai.model, openai.api_url), ("gpt-4o-mini", "http://10.0.0.5:8000"))

    def test_unknown_backend_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown LLM backend 'anthropc'"):
            build_backend("anthropc")

    def test_fallback_chain_from_config_with_env_key(self) -> None:
        cfg = LLMConfig(backend="anthropic", api_key="primary", max_retries=1, max_tokens=99,
                        fallbacks=[{"backend": "openai", "api_url": "http://127.0.0.1:9", "api_key_env": "MY_KEY"},
                                   {"backend": "ollama", "model": "qwen3"}])
        metered = backend_from_config(cfg, environ={"MY_KEY": "from-env"})
        self.assertIsInstance(metered, MeteredBackend)
        chain = metered.inner
        self.assertIsInstance(chain, FallbackBackend)
        primary, second, third = chain.backends
        self.assertEqual((primary.retry.max_attempts, primary.max_tokens), (2, 99))
        self.assertEqual(second.api_key, "from-env")
        self.assertEqual((third.model, third.api_url), ("qwen3", "http://localhost:11434"))

    def test_invalid_settings_are_rejected(self) -> None:
        for kwargs, pattern in (
            ({"max_retries": 50}, "max_retries"),
            ({"max_tokens": 0}, "max_tokens"),
            ({"retry_base_delay": 9, "retry_max_delay": 1}, "base_delay"),
            ({"fallbacks": [{"backend": "ollama", "apikey": "typo"}]}, "unknown keys"),
            ({"fallbacks": "ollama"}, "must be a list"),
            ({"backend": "openai", "api_url": "file:///etc/passwd"}, "absolute http"),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, pattern):
                backend_from_config(LLMConfig(**kwargs), environ={})

    def test_yaml_keys_are_applied(self) -> None:
        cfg = BotBoyConfig()
        cfg._apply_dict({"llm": {"max_tokens": 512, "max_retries": 0, "fallbacks": [{"backend": "mock"}],
                                 "price_input_per_mtok": 1.5, "max_context_tokens": 2000}})
        self.assertEqual((cfg.llm.max_tokens, cfg.llm.max_retries, cfg.llm.max_context_tokens), (512, 0, 2000))
        self.assertEqual(cfg.llm.fallbacks, [{"backend": "mock"}])
        integration = LLMIntegration.from_config(cfg.llm)
        self.assertEqual(integration._max_history_tokens, 2000)

    def test_invalid_config_disables_llm_visibly(self) -> None:
        bot = types.SimpleNamespace(config=types.SimpleNamespace(llm=LLMConfig(backend="nope")), llm=object())
        BootstrapService(bot)._init_llm()
        self.assertIsNone(bot.llm)


class PrimitiveTest(unittest.IsolatedAsyncioTestCase):
    def test_parse_retry_after_forms(self) -> None:
        now = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_retry_after({"Retry-After": "7"}, now), 7.0)
        self.assertEqual(parse_retry_after({"retry-after-ms": "1500"}, now), 1.5)
        date = format_datetime(now + timedelta(seconds=30), usegmt=True)
        self.assertAlmostEqual(parse_retry_after({"Retry-After": date}, now), 30.0)
        self.assertEqual(parse_retry_after({"Retry-After": format_datetime(now - timedelta(seconds=9), usegmt=True)}, now), 0.0)
        for junk in ("soon", "", "-5", "1e9"):
            self.assertIsNone(parse_retry_after({"Retry-After": junk}, now), junk)
        self.assertIsNone(parse_retry_after({"retry-after-ms": "nan"}, now))
        self.assertEqual(parse_retry_after({"Retry-After": "9" * 40}, now), 999_999_999.0)

    def test_retry_policy_bounds(self) -> None:
        policy = RetryPolicy(max_attempts=4, base_delay=1, max_delay=3)
        self.assertEqual([policy.backoff(i, lambda: 1.0) for i in range(4)], [1, 2, 3, 3])
        self.assertEqual(policy.backoff(1000, lambda: 0.5), 1.5)  # no OverflowError
        for bad in ({"max_attempts": 0}, {"max_attempts": True}, {"base_delay": -1}, {"max_delay": 301}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                RetryPolicy(**bad)

    async def _lines(self, chunks, **kw):
        async def gen():
            for c in chunks:
                yield c
        return [line async for line in iter_lines(gen(), **kw)]

    async def test_iter_lines_crlf_and_tail(self) -> None:
        self.assertEqual(await self._lines([b"a\r", b"\nb\n\nc"]), [b"a", b"b", b"", b"c"])

    async def test_iter_lines_trickle_is_linear(self) -> None:
        payload = b"x" * 200_000 + b"\n"
        start = time.perf_counter()
        lines = await self._lines([payload[i:i + 1] for i in range(len(payload))])
        self.assertEqual(lines, [b"x" * 200_000])
        self.assertLess(time.perf_counter() - start, 5)

    async def test_iter_lines_caps(self) -> None:
        with self.assertRaisesRegex(LLMTransportError, "line exceeded"):
            await self._lines([b"y" * 10], max_line=5)
        with self.assertRaisesRegex(LLMTransportError, "line exceeded"):
            await self._lines([b"ok\n" + b"y" * 10 + b"\n"], max_line=5)
        with self.assertRaisesRegex(LLMTransportError, "stream exceeded"):
            await self._lines([b"a\n"] * 10, max_total=5)

    async def test_sse_multiline_data_and_comments(self) -> None:
        async def gen():
            for line in (b": hi", b"event: e", b"data: one", b"data:two", b"", b"data: tail"):
                yield line
        self.assertEqual([e async for e in iter_sse(gen())], [("e", "one\ntwo"), ("message", "tail")])


if __name__ == "__main__":
    unittest.main()
