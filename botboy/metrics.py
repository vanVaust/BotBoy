"""MetricsCollector — Prometheus text format metrics, stdlib only."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple


class MetricsCollector:
    """
    Thread-safe Prometheus-compatible metrics collector.
    Singleton pattern — use get() to access the global instance.
    """

    _instance: Optional["MetricsCollector"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._command_counts: Dict[str, int] = defaultdict(int)
        self._error_counts: Dict[str, int] = defaultdict(int)
        self._principal_counts: Dict[str, int] = defaultdict(int)
        self._durations: Dict[str, list] = defaultdict(list)
        self._recent_commands = deque(maxlen=25)
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_bypasses = 0
        self._memory_ops = 0
        self._start_time = time.time()
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "MetricsCollector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def record_command(
        self,
        cmd_type: str,
        success: bool,
        duration_s: float = 0.0,
        principal: str = "anonymous",
        request_id: str = "",
    ) -> None:
        with self._lock:
            self._command_counts[cmd_type] += 1
            if not success:
                self._error_counts[cmd_type] += 1
            if principal:
                self._principal_counts[principal] += 1
            if duration_s > 0:
                self._durations[cmd_type].append(duration_s)
            self._recent_commands.append(
                {
                    "timestamp": time.time(),
                    "cmd_type": cmd_type,
                    "success": success,
                    "duration_s": duration_s,
                    "principal": principal or "anonymous",
                    "request_id": request_id or "",
                }
            )

    def record_cache(self, hit: bool = False, miss: bool = False, bypass: bool = False) -> None:
        with self._lock:
            if hit:
                self._cache_hits += 1
            if miss:
                self._cache_misses += 1
            if bypass:
                self._cache_bypasses += 1

    def record_memory_op(self) -> None:
        with self._lock:
            self._memory_ops += 1

    def render(self) -> Tuple[str, str]:
        """Returns (body, content_type) for Prometheus scraping."""
        lines = []
        uptime = time.time() - self._start_time

        with self._lock:
            # Uptime
            lines.append("# HELP botboy_uptime_seconds Uptime in seconds")
            lines.append("# TYPE botboy_uptime_seconds gauge")
            lines.append(f"botboy_uptime_seconds {uptime:.2f}")

            # Commands
            lines.append("# HELP botboy_commands_total Total commands processed")
            lines.append("# TYPE botboy_commands_total counter")
            for cmd_type, count in self._command_counts.items():
                lines.append(f'botboy_commands_total{{type="{cmd_type}"}} {count}')

            # Errors
            lines.append("# HELP botboy_errors_total Total command errors")
            lines.append("# TYPE botboy_errors_total counter")
            for cmd_type, count in self._error_counts.items():
                lines.append(f'botboy_errors_total{{type="{cmd_type}"}} {count}')

            # Cache
            lines.append("# HELP botboy_cache_hits_total Cache hits")
            lines.append("# TYPE botboy_cache_hits_total counter")
            lines.append(f"botboy_cache_hits_total {self._cache_hits}")

            lines.append("# HELP botboy_cache_misses_total Cache misses")
            lines.append("# TYPE botboy_cache_misses_total counter")
            lines.append(f"botboy_cache_misses_total {self._cache_misses}")

            # Memory ops
            lines.append("# HELP botboy_memory_ops_total Memory store/search operations")
            lines.append("# TYPE botboy_memory_ops_total counter")
            lines.append(f"botboy_memory_ops_total {self._memory_ops}")

            # Duration histograms (avg per type)
            lines.append("# HELP botboy_command_duration_seconds Command duration average")
            lines.append("# TYPE botboy_command_duration_seconds gauge")
            for cmd_type, durations in self._durations.items():
                if durations:
                    avg = sum(durations) / len(durations)
                    lines.append(f'botboy_command_duration_seconds{{type="{cmd_type}"}} {avg:.6f}')

            # Principal summary
            lines.append("# HELP botboy_commands_by_principal_total Commands grouped by principal")
            lines.append("# TYPE botboy_commands_by_principal_total counter")
            for principal, count in self._principal_counts.items():
                lines.append(f'botboy_commands_by_principal_total{{principal="{principal}"}} {count}')

        body = "\n".join(lines) + "\n"
        return body, "text/plain; version=0.0.4; charset=utf-8"

    def to_json(self) -> dict:
        with self._lock:
            uptime = time.time() - self._start_time
            total = sum(self._command_counts.values())
            errors = sum(self._error_counts.values())
            hr = self._cache_hits + self._cache_misses
            hit_rate = self._cache_hits / hr if hr > 0 else 0.0
            recent_commands = list(self._recent_commands)
            by_type = dict(self._command_counts)
            by_principal = dict(self._principal_counts)
            cache_hits = self._cache_hits
            cache_misses = self._cache_misses
            memory_ops = self._memory_ops
        return {
            "uptime_seconds": uptime,
            "total_commands": total,
            "total_errors": errors,
            "error_rate": errors / total if total > 0 else 0.0,
            "cache_hit_rate": hit_rate,
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "memory_ops": memory_ops,
            "by_type": by_type,
            "by_principal": by_principal,
            "recent_commands": recent_commands,
            "last_command": recent_commands[-1] if recent_commands else None,
        }
