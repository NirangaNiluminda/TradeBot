from __future__ import annotations

from typing import Any, Protocol


class LLMError(Exception):
    """The LLM call failed (network, bad response, invalid JSON...)."""


class LLMRateLimited(LLMError):
    """The provider said 'too many requests' (free-tier limit reached)."""


class LLMPort(Protocol):
    """Any large language model that can answer with a JSON object.

    Implementations: Gemini, Groq (and any other provider) in
    `tradebot.infrastructure.llm`. The rest of the bot only knows this contract,
    so switching provider never touches business logic.
    """

    name: str

    async def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        """Send instructions + prompt, return the model's answer parsed as a JSON object."""
        ...
