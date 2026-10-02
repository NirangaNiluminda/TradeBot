"""Google Gemini adapter (LLMPort) — uses the REST API directly via httpx."""

from __future__ import annotations

from typing import Any

import httpx

from tradebot.domain.ports import LLMError, LLMRateLimited
from tradebot.infrastructure.llm._json import parse_json_object

_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiLLM:
    def __init__(
        self, api_key: str, model: str, http: httpx.AsyncClient, temperature: float = 0.0
    ) -> None:
        self.name = f"gemini/{model}"
        self._key, self._model, self._http, self._temperature = api_key, model, http, temperature

    async def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self._temperature,
                "responseMimeType": "application/json",
            },
        }
        try:
            r = await self._http.post(
                f"{_BASE}/models/{self._model}:generateContent",
                headers={"x-goog-api-key": self._key},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: {type(exc).__name__}") from exc
        if r.status_code == 429:
            raise LLMRateLimited(f"{self.name}: rate limited")
        if r.status_code != 200:
            raise LLMError(f"{self.name}: HTTP {r.status_code}: {r.text[:200]}")
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"{self.name}: unexpected response shape") from exc
        return parse_json_object(text, self.name)
