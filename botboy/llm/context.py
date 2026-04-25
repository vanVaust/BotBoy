"""ContextManager — token-budget aware conversation context management."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Message:
    role: str   # system | user | assistant
    content: str

    @property
    def token_estimate(self) -> int:
        """Rough token estimate: ~4 chars per token."""
        return max(1, len(self.content) // 4)

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


class ContextManager:
    """
    Manages conversation context within a configurable token budget.
    Oldest messages (excluding system) are evicted first when budget is exceeded.
    """

    def __init__(self, max_tokens: int = 4096, system_prompt: str = "") -> None:
        self.max_tokens = max_tokens
        self._messages: List[Message] = []
        if system_prompt:
            self._messages.append(Message(role="system", content=system_prompt))

    def add_user(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))
        self._truncate()

    def add_assistant(self, content: str) -> None:
        self._messages.append(Message(role="assistant", content=content))
        self._truncate()

    def _truncate(self) -> None:
        """Evict oldest non-system messages to stay within token budget."""
        while self._total_tokens() > self.max_tokens and len(self._messages) > 1:
            # Find first non-system message to evict
            for i, msg in enumerate(self._messages):
                if msg.role != "system":
                    self._messages.pop(i)
                    break
            else:
                break  # Only system message left, can't truncate further

    def _total_tokens(self) -> int:
        return sum(m.token_estimate for m in self._messages)

    def build_context(self, last_n: Optional[int] = None) -> List[dict]:
        """Return messages as list of dicts for LLM API consumption."""
        messages = self._messages
        if last_n is not None:
            # Always include system message
            system = [m for m in messages if m.role == "system"]
            non_system = [m for m in messages if m.role != "system"]
            messages = system + non_system[-last_n:]
        return [m.to_dict() for m in messages]

    def clear(self, keep_system: bool = True) -> None:
        if keep_system:
            self._messages = [m for m in self._messages if m.role == "system"]
        else:
            self._messages.clear()

    def stats(self) -> dict:
        return {
            "message_count": len(self._messages),
            "estimated_tokens": self._total_tokens(),
            "max_tokens": self.max_tokens,
            "utilization": self._total_tokens() / self.max_tokens,
        }
