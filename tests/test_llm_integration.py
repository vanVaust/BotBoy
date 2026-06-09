import pytest
from botboy.llm.integration import LLMIntegration, MockLLMBackend, LLMResponse

@pytest.mark.asyncio
async def test_mock_llm_backend():
    backend = MockLLMBackend(response_prefix="[TEST] ")
    messages = [{"role": "user", "content": "hello"}]
    
    # Test chat
    response = await backend.chat(messages)
    assert isinstance(response, LLMResponse)
    assert response.success is True
    assert response.content == "[TEST] Response to: hello"
    assert response.backend == "mock"

    # Test stream (split yields words followed by spaces, so there is a trailing space)
    stream_chunks = []
    async for chunk in backend.stream(messages):
        stream_chunks.append(chunk)
    assert "".join(stream_chunks) == "[TEST] Response to: hello "

@pytest.mark.asyncio
async def test_llm_integration_chat_and_history():
    backend = MockLLMBackend()
    integration = LLMIntegration(backend, system_prompt="Sys prompt", max_history=4)

    # Starts empty
    assert len(integration._history) == 0

    # Chat interaction 1
    response = await integration.chat("ping", use_history=True)
    assert response.content == "[MOCK] Response to: ping"
    assert len(integration._history) == 2
    assert integration._history[0] == {"role": "user", "content": "ping"}
    assert integration._history[1] == {"role": "assistant", "content": "[MOCK] Response to: ping"}

    # Chat interaction 2
    response2 = await integration.chat("pong", use_history=True)
    assert response2.content == "[MOCK] Response to: pong"
    # History size should be 4 (max_history)
    assert len(integration._history) == 4
    assert integration._history[-2] == {"role": "user", "content": "pong"}
    assert integration._history[-1] == {"role": "assistant", "content": "[MOCK] Response to: pong"}

    # Chat interaction 3 (causes trim)
    response3 = await integration.chat("hello", use_history=True)
    assert len(integration._history) == 4
    # The oldest messages (ping) should be pruned
    assert integration._history[-4] == {"role": "user", "content": "pong"}
    assert integration._history[-3] == {"role": "assistant", "content": "[MOCK] Response to: pong"}
    assert integration._history[-2] == {"role": "user", "content": "hello"}
    assert integration._history[-1] == {"role": "assistant", "content": "[MOCK] Response to: hello"}

    # Test clear_history
    integration.clear_history()
    assert len(integration._history) == 0

@pytest.mark.asyncio
async def test_llm_integration_stream_and_history():
    backend = MockLLMBackend()
    integration = LLMIntegration(backend, max_history=2)

    stream_chunks = []
    async for chunk in integration.stream("test stream"):
        stream_chunks.append(chunk)
    
    assert "".join(stream_chunks) == "[MOCK] Response to: test stream "
    assert len(integration._history) == 2
    assert integration._history[0] == {"role": "user", "content": "test stream"}
    assert integration._history[1] == {"role": "assistant", "content": "[MOCK] Response to: test stream "}

    # Stream again, should trim history to last 2 messages
    stream_chunks2 = []
    async for chunk in integration.stream("test stream 2"):
        stream_chunks2.append(chunk)

    assert len(integration._history) == 2
    assert integration._history[0] == {"role": "user", "content": "test stream 2"}
    assert integration._history[1] == {"role": "assistant", "content": "[MOCK] Response to: test stream 2 "}
