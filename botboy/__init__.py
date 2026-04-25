"""BotBoy v0.6.0-dev - Production AI Agent Framework."""

from botboy.agent_skills import AgentSkillLibrary, default_registry_path
from botboy.runtime import available_modules, get_bot

__version__ = "0.6.0-dev"
__all__ = ["BotBoy", "get_bot", "available_modules", "AgentSkillLibrary", "default_registry_path"]


def __getattr__(name: str):
    if name == "BotBoy":
        from botboy.__main__ import BotBoy

        return BotBoy
    if name == "get_bot":
        return get_bot
    if name == "available_modules":
        return available_modules
    raise AttributeError(f"module 'botboy' has no attribute {name!r}")
