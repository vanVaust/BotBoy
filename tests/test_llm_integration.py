from __future__ import annotations

import unittest

from botboy.llm.integration import LLMIntegration, LLMResponse, MockLLMBackend


class LLMIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_mock_llm_backend(self) -> None:
        backend = MockLLMBackend(response_prefix="[TEST] ")
        messages = [{"role": "user", "content": "hello"}]

        response = await backend.chat(messages)
        self.assertIsInstance(response, LLMResponse)
        self.assertTrue(response.success)
        self.assertEqual(response.content, "[TEST] Response to: hello")
        self.assertEqual(response.backend, "mock")

        stream_chunks = []
        async for chunk in backend.stream(messages):
            stream_chunks.append(chunk)
        self.assertEqual("".join(stream_chunks), "[TEST] Response to: hello ")

    async def test_llm_integration_chat_and_history(self) -> None:
        backend = MockLLMBackend()
        integration = LLMIntegration(backend, system_prompt="Sys prompt", max_history=4)

        self.assertEqual(len(integration._history), 0)

        response = await integration.chat("ping", use_history=True)
        self.assertEqual(response.content, "[MOCK] Response to: ping")
        self.assertEqual(len(integration._history), 2)
        self.assertEqual(integration._history[0], {"role": "user", "content": "ping"})
        self.assertEqual(integration._history[1], {"role": "assistant", "content": "[MOCK] Response to: ping"})

        response2 = await integration.chat("pong", use_history=True)
        self.assertEqual(response2.content, "[MOCK] Response to: pong")
        self.assertEqual(len(integration._history), 4)
        self.assertEqual(integration._history[-2], {"role": "user", "content": "pong"})
        self.assertEqual(integration._history[-1], {"role": "assistant", "content": "[MOCK] Response to: pong"})

        await integration.chat("hello", use_history=True)
        self.assertEqual(len(integration._history), 4)
        self.assertEqual(integration._history[-4], {"role": "user", "content": "pong"})
        self.assertEqual(integration._history[-3], {"role": "assistant", "content": "[MOCK] Response to: pong"})
        self.assertEqual(integration._history[-2], {"role": "user", "content": "hello"})
        self.assertEqual(integration._history[-1], {"role": "assistant", "content": "[MOCK] Response to: hello"})

        integration.clear_history()
        self.assertEqual(len(integration._history), 0)

    async def test_llm_integration_stream_and_history(self) -> None:
        backend = MockLLMBackend()
        integration = LLMIntegration(backend, max_history=2)

        stream_chunks = []
        async for chunk in integration.stream("test stream"):
            stream_chunks.append(chunk)

        self.assertEqual("".join(stream_chunks), "[MOCK] Response to: test stream ")
        self.assertEqual(len(integration._history), 2)
        self.assertEqual(integration._history[0], {"role": "user", "content": "test stream"})
        self.assertEqual(
            integration._history[1],
            {"role": "assistant", "content": "[MOCK] Response to: test stream "},
        )

        stream_chunks2 = []
        async for chunk in integration.stream("test stream 2"):
            stream_chunks2.append(chunk)

        self.assertEqual("".join(stream_chunks2), "[MOCK] Response to: test stream 2 ")
        self.assertEqual(len(integration._history), 2)
        self.assertEqual(integration._history[0], {"role": "user", "content": "test stream 2"})
        self.assertEqual(
            integration._history[1],
            {"role": "assistant", "content": "[MOCK] Response to: test stream 2 "},
        )


if __name__ == "__main__":
    unittest.main()
