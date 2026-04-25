"""Shared runtime helpers for the BotBoy package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from botboy.core.config import BotBoyConfig

if TYPE_CHECKING:
    from botboy.__main__ import BotBoy


_global_bot: Optional["BotBoy"] = None


def get_bot(config: Optional[BotBoyConfig] = None) -> "BotBoy":
    """Return or create the global BotBoy singleton."""
    global _global_bot
    if _global_bot is None or not _global_bot._initialized:
        from botboy.__main__ import BotBoy

        _global_bot = BotBoy(config)
        _global_bot.initialize()
    return _global_bot


def available_modules() -> dict:
    """Report which optional modules are importable."""
    modules = {}
    for name in [
        "fastapi",
        "uvicorn",
        "aiohttp",
        "websockets",
        "yaml",
        "vosk",
        "pyaudio",
        "docker",
        "sentence_transformers",
        "openai",
        "anthropic",
    ]:
        try:
            __import__(name)
            modules[name] = True
        except ImportError:
            modules[name] = False
    return modules
