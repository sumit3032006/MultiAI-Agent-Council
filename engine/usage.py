"""Central API usage manager: counters, provider availability and per-task budgets."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class Availability(str, Enum):
    AVAILABLE = "available"
    QUOTA_EXHAUSTED = "quota_exhausted"
    UNAVAILABLE = "unavailable"
    COOLING_DOWN = "cooling_down"


@dataclass
class Stats:
    requests: int = 0
    successes: int = 0
    failures: int = 0
    rate_limit_errors: int = 0
    input_tokens: int = 0     # only tokens the provider actually reported
    output_tokens: int = 0
    unreported: int = 0       # successful requests where the provider gave no token numbers
    latency_total: float = 0.0


class UsageManager:
    def __init__(self, on_record: Optional[Callable[[dict], None]] = None, clock: Callable[[], float] = time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._on_record = on_record
        self.stats: dict[tuple[str, str], Stats] = {}
        self._state: dict[str, tuple[Availability, str, float]] = {}
        self.begin_task(None, 10 ** 9)

    # ---- availability -------------------------------------------------
    def availability(self, provider: str) -> tuple[Availability, str]:
        with self._lock:
            av, reason, until = self._state.get(provider, (Availability.AVAILABLE, "", 0.0))
            if av is Availability.COOLING_DOWN and self._clock() >= until:
                self._state.pop(provider, None)
                return Availability.AVAILABLE, ""
            return av, reason

    def _set(self, provider, av, reason, until=0.0):
        with self._lock:
            self._state[provider] = (av, reason, until)

    def mark_exhausted(self, provider: str, reason: str = "free quota exhausted") -> None:
        self._set(provider, Availability.QUOTA_EXHAUSTED, reason)

    def mark_unavailable(self, provider: str, reason: str) -> None:
        self._set(provider, Availability.UNAVAILABLE, reason)

    def mark_cooldown(self, provider: str, seconds: float, reason: str = "rate limited") -> None:
        self._set(provider, Availability.COOLING_DOWN, reason, self._clock() + seconds)

    def reset(self, provider: Optional[str] = None) -> None:
        with self._lock:
            if provider:
                self._state.pop(provider, None)
            else:
                self._state.clear()

    # ---- counters -----------------------------------------------------
    def begin_task(self, task_id: Optional[str], request_cap: int) -> None:
        with self._lock:
            self.task_id = task_id
            self.request_cap = request_cap
            self.task_requests = 0
            self.task_failures = 0
            self.task_input_tokens = 0
            self.task_output_tokens = 0

    def record(self, provider: str, model: str, ok: bool, input_tokens: Optional[int] = None,
               output_tokens: Optional[int] = None, latency: float = 0.0, error_kind: Optional[str] = None) -> None:
        with self._lock:
            s = self.stats.setdefault((provider, model), Stats())
            s.requests += 1
            self.task_requests += 1
            s.latency_total += latency
            if ok:
                s.successes += 1
                if input_tokens is None and output_tokens is None:
                    s.unreported += 1
                s.input_tokens += input_tokens or 0
                s.output_tokens += output_tokens or 0
                self.task_input_tokens += input_tokens or 0
                self.task_output_tokens += output_tokens or 0
            else:
                s.failures += 1
                self.task_failures += 1
                if error_kind == "rate_limit":
                    s.rate_limit_errors += 1
            task_id = self.task_id
        if self._on_record:
            self._on_record(dict(task_id=task_id, provider=provider, model=model, ok=ok,
                                 input_tokens=input_tokens, output_tokens=output_tokens,
                                 latency=latency, error_kind=error_kind))

    def summary_rows(self) -> list[dict]:
        """One row per provider/model. Cost is never guessed: free tiers are reported as such."""
        rows = []
        with self._lock:
            items = list(self.stats.items())
        for (provider, model), s in sorted(items):
            av, reason = self.availability(provider)
            rows.append(dict(provider=provider, model=model, requests=s.requests, failures=s.failures,
                             rate_limit_errors=s.rate_limit_errors, input_tokens=s.input_tokens,
                             output_tokens=s.output_tokens, unreported=s.unreported,
                             avg_latency=(s.latency_total / s.requests) if s.requests else 0.0,
                             availability=av.value, reason=reason, cost="n/a (free tier)"))
        return rows
