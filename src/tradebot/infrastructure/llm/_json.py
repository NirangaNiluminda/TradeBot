from __future__ import annotations

import json
import re
from typing import Any

from tradebot.domain.ports import LLMError

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_object(text: str, provider: str) -> dict[str, Any]:
    """Parse a model's reply as a JSON object (tolerates ```json fences)."""
    try:
        data = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError as exc:
        raise LLMError(f"{provider}: reply was not valid JSON: {text[:200]!r}") from exc
    if not isinstance(data, dict):
        raise LLMError(f"{provider}: expected a JSON object, got {type(data).__name__}")
    return data
