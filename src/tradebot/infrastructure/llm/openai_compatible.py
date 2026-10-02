"""Adapter for any OpenAI-compatible chat API (LLMPort).

Groq uses this format, and so do OpenAI, OpenRouter, Together, Mistral and
local servers like Ollama/LM Studio — so one class covers many providers.
"""

from __future__ import annotations

from typing import Any

import httpx

from tradebot.domain.ports import LLMError, LLMRateLimited
from tradebot.infrastructure.llm._json import parse_json_object


class OpenAICompatibleLLM:
    def __init__(
        self,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        http: httpx.AsyncClient,
        temperature: float = 0.0,
    ) -> None:
        self.name = f"{provider}/{model}"
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key, self._model, self._http, self._temperature = api_key, model, http, temperature

    async def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        body = {
            "model": self._model,
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            r = await self._http.post(
                self._url, headers={"Authorization": f"Bearer {self._key}"}, json=body
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: {type(exc).__name__}") from exc
        if r.status_code == 429:
            raise LLMRateLimited(f"{self.name}: rate limited")
        if r.status_code != 200:
            raise LLMError(f"{self.name}: HTTP {r.status_code}: {' '.join(r.text.split())[:160]}")
        try:
            text = r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"{self.name}: unexpected response shape") from exc
        return parse_json_object(text, self.name)
