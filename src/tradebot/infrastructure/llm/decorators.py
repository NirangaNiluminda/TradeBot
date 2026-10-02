"""Extra behaviour wrapped around ANY LLMPort (Decorator pattern).

Each wrapper is itself an LLMPort, so they stack:
    CachingLLM(FallbackLLM([RetryingLLM(RateLimitedLLM(gemini)), ...]))
Provider adapters stay tiny; retries, limits and caching are written once.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from tradebot.domain.ports import LLMError, LLMPort, LLMRateLimited

log = logging.getLogger(__name__)


class RetryingLLM:
    """Retry temporary failures (network blips, a malformed reply) with a growing pause."""

    def __init__(self, inner: LLMPort, attempts: int = 2, backoff_seconds: float = 2.0) -> None:
        self.inner, self.attempts, self.backoff = inner, attempts, backoff_seconds
        self.name = inner.name

    async def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        for attempt in range(1, self.attempts + 1):
            try:
                return await self.inner.complete_json(system, prompt)
            except LLMRateLimited:
                raise  # retrying immediately won't help; let the fallback try another provider
            except LLMError as exc:
                if attempt == self.attempts:
                    raise
                log.info("%s failed (%s), retrying", self.name, exc)
                await asyncio.sleep(self.backoff * attempt)
        raise AssertionError("unreachable")


class RateLimitedLLM:
    """Keep at least `min_interval` seconds between calls (protects free-tier limits)."""

    def __init__(self, inner: LLMPort, min_interval_seconds: float) -> None:
        self.inner, self.min_interval = inner, min_interval_seconds
        self.name = inner.name
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        async with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        return await self.inner.complete_json(system, prompt)


class CachingLLM:
    """Return the previous answer for an identical question asked within `ttl` seconds."""

    def __init__(self, inner: LLMPort, ttl_seconds: float) -> None:
        self.inner, self.ttl = inner, ttl_seconds
        self.name = inner.name
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        key = hashlib.sha256(f"{system}\0{prompt}".encode()).hexdigest()
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        answer = await self.inner.complete_json(system, prompt)
        self._cache = {k: v for k, v in self._cache.items() if time.monotonic() - v[0] < self.ttl}
        self._cache[key] = (time.monotonic(), answer)
        return answer


class FallbackLLM:
    """Try providers in order; the first one that answers wins.

    The answer gets an extra "_provider" key saying which provider answered
    (it survives caching, and is shown on the dashboard).
    """

    def __init__(self, providers: list[LLMPort]) -> None:
        if not providers:
            raise ValueError("FallbackLLM needs at least one provider")
        self.providers = providers
        self.name = " -> ".join(p.name for p in providers)
        self.last_used: str | None = None

    async def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        errors = []
        for provider in self.providers:
            try:
                answer = await provider.complete_json(system, prompt)
            except LLMError as exc:
                log.warning("LLM %s failed: %s", provider.name, exc)
                errors.append(str(exc))
                continue
            self.last_used = provider.name
            return {**answer, "_provider": provider.name}
        raise LLMError("all LLM providers failed: " + " | ".join(errors))
