"""Rate Limiter — sliding window algorithm, stdlib only."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    reset_in: float    # seconds until window resets
    retry_after: float # 0 if allowed


class RateLimiter:
    """
    Sliding window rate limiter (thread-safe).
    Default: 100 requests per hour per identity.
    """

    def __init__(self, max_requests: int = 100, window_seconds: int = 3600) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._windows: Dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, identity: str) -> RateLimitResult:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            dq = self._windows[identity]

            # Evict expired timestamps
            while dq and dq[0] <= cutoff:
                dq.popleft()

            count = len(dq)
            if count >= self.max_requests:
                oldest = dq[0]
                retry_after = self.window_seconds - (now - oldest)
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    reset_in=retry_after,
                    retry_after=retry_after,
                )

            dq.append(now)
            remaining = self.max_requests - (count + 1)
            return RateLimitResult(
                allowed=True,
                remaining=remaining,
                reset_in=self.window_seconds,
                retry_after=0.0,
            )

    def reset(self, identity: str) -> None:
        with self._lock:
            self._windows.pop(identity, None)

    def stats(self) -> dict:
        with self._lock:
            return {
                "tracked_identities": len(self._windows),
                "max_requests": self.max_requests,
                "window_seconds": self.window_seconds,
            }
