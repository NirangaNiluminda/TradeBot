"""Builds the configured LLM stack from config (Factory + Registry patterns).

To add a provider: write an adapter implementing LLMPort, then register a
builder here with `@register("name")`. Selecting it is a config change only:

    llm:
      providers: [gemini, groq]   # order = fallback order
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from tradebot.domain.ports import LLMPort
from tradebot.infrastructure.llm.decorators import (
    CachingLLM,
    FallbackLLM,
    RateLimitedLLM,
    RetryingLLM,
)
from tradebot.infrastructure.llm.gemini import GeminiLLM
from tradebot.infrastructure.llm.openai_compatible import OpenAICompatibleLLM

log = logging.getLogger(__name__)

# builder(model, api_key, http) -> LLMPort
Builder = Callable[[str, str, httpx.AsyncClient], LLMPort]
_REGISTRY: dict[str, Builder] = {}


def register(name: str) -> Callable[[Builder], Builder]:
    def decorator(fn: Builder) -> Builder:
        _REGISTRY[name] = fn
        return fn

    return decorator


@register("gemini")
def _gemini(model: str, key: str, http: httpx.AsyncClient) -> LLMPort:
    return GeminiLLM(key, model, http)


@register("groq")
def _groq(model: str, key: str, http: httpx.AsyncClient) -> LLMPort:
    return OpenAICompatibleLLM("groq", "https://api.groq.com/openai/v1", key, model, http)


@register("openai")
def _openai(model: str, key: str, http: httpx.AsyncClient) -> LLMPort:
    return OpenAICompatibleLLM("openai", "https://api.openai.com/v1", key, model, http)


@register("openrouter")
def _openrouter(model: str, key: str, http: httpx.AsyncClient) -> LLMPort:
    return OpenAICompatibleLLM("openrouter", "https://openrouter.ai/api/v1", key, model, http)


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def build_llm(
    providers: list[tuple[str, str, str | None]],
    http: httpx.AsyncClient,
    min_interval_seconds: float,
    cache_seconds: float,
    retries: int = 2,
) -> LLMPort:
    """`providers` = [(name, model, api_key), ...] in fallback order. Missing keys are skipped."""
    stack: list[LLMPort] = []
    for name, model, key in providers:
        if name not in _REGISTRY:
            raise ValueError(f"Unknown LLM provider '{name}'. Available: {available_providers()}")
        if not key:
            log.warning("LLM provider '%s' skipped: no API key in .env", name)
            continue
        llm = _REGISTRY[name](model, key, http)
        stack.append(RetryingLLM(RateLimitedLLM(llm, min_interval_seconds), attempts=retries))
    if not stack:
        raise ValueError("No LLM provider has an API key. Add one to .env")
    return CachingLLM(FallbackLLM(stack), cache_seconds)
