"""PerformanceMonitor - timing and p50/p95/p99 metrics tracking."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional


@dataclass
class MetricStats:
    name: str
    count: int
    min_ms: float
    max_ms: float
    avg_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


@dataclass(frozen=True)
class ObservationEvent:
    name: str
    duration_ms: float
    principal: str
    request_id: str
    recorded_at: float = field(default_factory=time.time)


class PerformanceMonitor:
    """Thread-safe performance monitor with sliding window retention."""

    def __init__(self, retention: int = 1000) -> None:
        self.retention = retention
        self._metrics: Dict[str, Deque[ObservationEvent]] = defaultdict(
            lambda: deque(maxlen=retention)
        )
        self._recent_observations: Deque[ObservationEvent] = deque(maxlen=retention)
        self._principal_counts: Dict[str, int] = defaultdict(int)
        self._timers: Dict[str, tuple[float, str, str]] = {}
        self._lock = threading.Lock()

    def start_timer(self, name: str, principal: str = "anonymous", request_id: str = "") -> None:
        key = f"{name}_{threading.get_ident()}"
        self._timers[key] = (time.perf_counter(), principal or "anonymous", request_id or "")

    def stop_timer(
        self,
        name: str,
        principal: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> float:
        key = f"{name}_{threading.get_ident()}"
        start = self._timers.pop(key, None)
        if start is None:
            return 0.0

        started_at, stored_principal, stored_request_id = start
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        self._record_observation(
            name,
            elapsed_ms,
            principal or stored_principal,
            request_id if request_id is not None else stored_request_id,
        )
        return elapsed_ms

    def record(
        self,
        name: str,
        duration_ms: float,
        principal: str = "anonymous",
        request_id: str = "",
    ) -> None:
        self._record_observation(name, duration_ms, principal, request_id)

    def _record_observation(
        self,
        name: str,
        duration_ms: float,
        principal: str,
        request_id: str,
    ) -> None:
        event = ObservationEvent(
            name=name,
            duration_ms=duration_ms,
            principal=principal or "anonymous",
            request_id=request_id or "",
        )
        with self._lock:
            metric_samples = self._metrics[name]
            if metric_samples.maxlen is not None and len(metric_samples) >= metric_samples.maxlen:
                evicted = metric_samples.popleft()
                self._decrement_principal(evicted.principal)
            metric_samples.append(event)
            self._principal_counts[event.principal] += 1
            self._recent_observations.append(event)

    def _decrement_principal(self, principal: str) -> None:
        current = self._principal_counts.get(principal)
        if current is None:
            return
        if current <= 1:
            self._principal_counts.pop(principal, None)
        else:
            self._principal_counts[principal] = current - 1

    def get_stats(self, metric_name: str) -> Optional[MetricStats]:
        with self._lock:
            samples = [sample.duration_ms for sample in self._metrics.get(metric_name, [])]
        if not samples:
            return None
        sorted_samples = sorted(samples)
        n = len(sorted_samples)

        def pct(p: float) -> float:
            idx = int(p / 100 * n)
            return sorted_samples[min(idx, n - 1)]

        return MetricStats(
            name=metric_name,
            count=n,
            min_ms=sorted_samples[0],
            max_ms=sorted_samples[-1],
            avg_ms=sum(sorted_samples) / n,
            p50_ms=pct(50),
            p95_ms=pct(95),
            p99_ms=pct(99),
        )

    def get_all_stats(self) -> Dict[str, MetricStats]:
        with self._lock:
            names = list(self._metrics.keys())
        return {name: s for name in names if (s := self.get_stats(name))}

    def get_observability_summary(self) -> dict:
        with self._lock:
            recent_observations = list(self._recent_observations)
            principal_counts = dict(self._principal_counts)
            total_samples = sum(len(samples) for samples in self._metrics.values())
            metric_count = len(self._metrics)

        top_principal = None
        if principal_counts:
            principal, count = max(principal_counts.items(), key=lambda item: (item[1], item[0]))
            top_principal = {"principal": principal, "count": count}

        last_observation = None
        if recent_observations:
            last = recent_observations[-1]
            last_observation = {
                "name": last.name,
                "duration_ms": last.duration_ms,
                "principal": last.principal,
                "request_id": last.request_id,
                "recorded_at": last.recorded_at,
            }

        return {
            "total_samples": total_samples,
            "unique_metrics": metric_count,
            "principal_counts": principal_counts,
            "unique_principals": len(principal_counts),
            "top_principal": top_principal,
            "last_observation": last_observation,
        }

    def get_report(self) -> str:
        all_stats = self.get_all_stats()
        if not all_stats:
            return "No metrics recorded yet."

        lines = ["Performance Report:"]
        for name, s in sorted(all_stats.items()):
            lines.append(
                f"  {name}: count={s.count} | avg={s.avg_ms:.1f}ms"
                f" | p50={s.p50_ms:.1f}ms | p95={s.p95_ms:.1f}ms | p99={s.p99_ms:.1f}ms"
            )

        summary = self.get_observability_summary()
        if summary["total_samples"]:
            lines.append("Observability Summary:")
            lines.append(
                f"  samples={summary['total_samples']} | metrics={summary['unique_metrics']}"
                f" | principals={summary['unique_principals']}"
            )
            top_principal = summary["top_principal"]
            if top_principal:
                lines.append(
                    f"  top_principal={top_principal['principal']}"
                    f" ({top_principal['count']} samples)"
                )
            last_observation = summary["last_observation"]
            if last_observation:
                suffix = (
                    f" | request_id={last_observation['request_id']}"
                    if last_observation["request_id"]
                    else ""
                )
                lines.append(
                    f"  last_sample={last_observation['name']}"
                    f" | principal={last_observation['principal']}"
                    f"{suffix}"
                    f" | duration={last_observation['duration_ms']:.1f}ms"
                )
        return "\n".join(lines)

    def reset(self, metric_name: Optional[str] = None) -> None:
        with self._lock:
            if metric_name:
                samples = self._metrics.pop(metric_name, None)
                if samples:
                    for event in samples:
                        self._decrement_principal(event.principal)
                if self._recent_observations:
                    self._recent_observations = deque(
                        (event for event in self._recent_observations if event.name != metric_name),
                        maxlen=self.retention,
                    )
            else:
                self._metrics.clear()
                self._principal_counts.clear()
                self._recent_observations.clear()


@contextmanager
def timed(
    monitor: PerformanceMonitor,
    name: str,
    principal: str = "anonymous",
    request_id: str = "",
):
    monitor.start_timer(name, principal=principal, request_id=request_id)
    try:
        yield
    finally:
        monitor.stop_timer(name)
