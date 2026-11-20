"""Gateway: every provider call goes through here (availability, spacing, one retry, usage tracking)."""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Callable, Optional

from config import AgentConfig
from providers.base import ProviderError, ProviderResult
from .usage import Availability, UsageManager


class ProviderUnavailable(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class BudgetExceeded(Exception):
    pass


def scope_label(adapter) -> str:
    """Stable, non-secret label for one API key, e.g. 'openrouter key #a1b2'. Agents sharing a key share its quota state."""
    fp = hashlib.sha256(adapter.api_key.encode()).hexdigest()[:4]
    return f"{adapter.name} key #{fp}"


class Gateway:
    def __init__(self, usage: UsageManager, adapter_factory: Callable, min_interval: float = 2.0,
                 sleep=asyncio.sleep, clock: Callable[[], float] = time.monotonic):
        self.usage = usage
        self.factory = adapter_factory   # factory(agent) -> adapter; may raise ProviderError
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    def check(self, agent: AgentConfig) -> Optional[tuple[str, str]]:
        """None if the agent can be called, else (kind, reason). Never makes a request."""
        try:
            adapter = self.factory(agent)
            adapter.check_model()
        except ProviderError as e:
            return Availability.UNAVAILABLE.value, str(e)
        av, reason = self.usage.availability(scope_label(adapter))
        if av is not Availability.AVAILABLE:
            return av.value, reason
        return None

    async def _space(self, scope: str) -> None:
        lock = self._locks.setdefault(scope, asyncio.Lock())
        async with lock:
            wait = self._last.get(scope, -1e9) + self.min_interval - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._last[scope] = self._clock()

    async def call(self, agent: AgentConfig, system: str, user: str, max_tokens: int,
                   temperature: float = 0.6) -> ProviderResult:
        problem = self.check(agent)
        if problem:
            raise ProviderUnavailable(*problem)
        adapter = self.factory(agent)
        scope = scope_label(adapter)
        for attempt in (0, 1):
            if self.usage.task_requests >= self.usage.request_cap:
                raise BudgetExceeded("request limit for this task reached")
            await self._space(scope)
            t0 = self._clock()
            try:
                res = await adapter.generate(system, user, max_tokens, temperature)
            except ProviderError as e:
                self.usage.record(scope, agent.model, False, latency=self._clock() - t0, error_kind=e.kind)
                if e.kind == "quota":
                    self.usage.mark_exhausted(scope, str(e))
                elif e.kind in ("auth", "bad_model"):
                    self.usage.mark_unavailable(scope, str(e))
                elif e.kind in ("rate_limit", "temporary") and attempt == 0:
                    await self._sleep(min(e.retry_after or 3.0, 15.0))   # the single controlled retry
                    continue
                elif e.kind == "rate_limit":
                    self.usage.mark_cooldown(scope, 30.0)
                raise
            self.usage.record(scope, agent.model, True, res.input_tokens, res.output_tokens, res.latency)
            return res
        raise ProviderError("temporary", "unreachable")  # pragma: no cover
