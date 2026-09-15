"""ResponseCache — LRU cache with TTL tiers per command type."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional

# TTL constants in seconds
_PERMANENT = 0        # Never expires (deterministic: calculate, hash, base64, convert)
_LONG = 600           # 10 minutes (skills, help, version, system info)
_MEDIUM = 120         # 2 minutes (weather, web search)
_SHORT = 30            # 30 seconds (status, search, performance)
_BYPASS = -1          # Never cached (remember, forget, time, date)

_CMD_TTL: Dict[str, int] = {
    "calculate": _PERMANENT,
    "hash": _PERMANENT,
    "base64": _PERMANENT,
    "convert": _PERMANENT,
    "skills": _LONG,
    "help": _LONG,
    "version": _LONG,
    "system": _LONG,
    "weather": _MEDIUM,
    "web": _MEDIUM,
    "status": _SHORT,
    "search": _SHORT,
    "performance": _SHORT,
    "remember": _BYPASS,
    "forget": _BYPASS,
    "time": _BYPASS,
    "date": _BYPASS,
    "memories": _SHORT,
    "memstats": _SHORT,
    "schedule": _BYPASS,
    "history": _BYPASS,
}


@dataclass
class CacheEntry:
    value: Any
    stored_at: float
    ttl: int


@dataclass
class CacheStats:
    hits: int
    misses: int
    bypasses: int
    size: int
    capacity: int
    hit_rate: float


def _ttl_for_command(command: str) -> int:
    prefix = command.strip().lower().split()[0] if command.strip() else ""
    return _CMD_TTL.get(prefix, _SHORT)


def _scope_key(command: str, security_scope: str = "") -> str:
    """Return a cache key that cannot cross security scopes.

    The caller should supply a stable principal/tenant/security-context digest.
    Empty scope is retained for backwards compatibility with direct cache users.
    """
    normalized = command.strip().lower()
    return f"{security_scope}\x00{normalized}" if security_scope else normalized


class ResponseCache:
    """Thread-safe LRU cache with per-command-type TTL tiers."""

    def __init__(self, max_size: int = 500) -> None:
        self.max_size = max_size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._bypasses = 0

    def _is_expired(self, entry: CacheEntry) -> bool:
        if entry.ttl == _PERMANENT:
            return False
        return (time.monotonic() - entry.stored_at) > entry.ttl

    def get(self, command: str, *, security_scope: str = "") -> Optional[Any]:
        ttl = _ttl_for_command(command)
        if ttl == _BYPASS:
            with self._lock:
                self._bypasses += 1
            return None

        key = _scope_key(command, security_scope)
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            entry = self._cache[key]
            if self._is_expired(entry):
                del self._cache[key]
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return entry.value

    def set(self, command: str, value: Any, *, security_scope: str = "") -> bool:
        ttl = _ttl_for_command(command)
        if ttl == _BYPASS:
            return False

        key = _scope_key(command, security_scope)
        entry = CacheEntry(value=value, stored_at=time.monotonic(), ttl=ttl)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = entry
            else:
                self._cache[key] = entry
                if len(self._cache) > self.max_size:
                    self._cache.popitem(last=False)
        return True

    def invalidate(self, command: str, *, security_scope: str = "") -> bool:
        key = _scope_key(command, security_scope)
        with self._lock:
            return self._cache.pop(key, None) is not None

    def purge_expired(self) -> int:
        removed = 0
        with self._lock:
            expired_keys = [k for k, e in self._cache.items() if self._is_expired(e)]
            for k in expired_keys:
                del self._cache[k]
                removed += 1
        return removed

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def stats(self) -> CacheStats:
        with self._lock:
            total = self._hits + self._misses
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                bypasses=self._bypasses,
                size=len(self._cache),
                capacity=self.max_size,
                hit_rate=self._hits / total if total > 0 else 0.0,
            )
