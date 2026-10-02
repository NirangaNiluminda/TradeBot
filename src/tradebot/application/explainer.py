"""Optional plain-language explanations from the LLM (switch on/off at runtime).

Turned on with the "Explain" toggle. It explains:
  - the market analysis (when you press Start or "Analyze now")
  - every new trade, right after it opens
Off = no LLM calls at all (saves the free quota).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from tradebot.application.bot_control import BotControl
from tradebot.application.event_bus import EventBus
from tradebot.application.market_analysis import MarketSnapshot
from tradebot.domain.events import Event, PositionOpened
from tradebot.domain.models import RiskAssessment
from tradebot.domain.ports import LLMError, LLMPort

log = logging.getLogger(__name__)

SYSTEM = """\
You explain what a small, careful crypto SPOT trading bot is seeing and doing, for a beginner.
Use simple words and short sentences. 2-3 sentences per coin. Mention the trend, what the bot's
strategy is waiting for (or why it just bought), and anything notable in the news risk.
Coin "status" meanings: "holding" = the bot owns this coin now; "buy signal now" = the entry
rule is met, the bot buys only if bot_state is RUNNING; "waiting" = no entry yet.
Never say the bot bought something unless its status is "holding" or the event says so.
Do not predict prices, do not promise profits, do not recommend extra trades.
Reply with ONLY a JSON object: {"explanations": {"<SYMBOL>": "<text>", ...}}"""


@dataclass(frozen=True, slots=True)
class ExplanationReady(Event):
    symbol: str
    text: str


class Explainer:
    def __init__(
        self,
        llm: LLMPort,
        bus: EventBus,
        control: BotControl,
        snapshots: Callable[[], list[MarketSnapshot]],
        risk: Callable[[], RiskAssessment],
    ) -> None:
        self.llm, self.bus, self.control = llm, bus, control
        self.snapshots, self.risk = snapshots, risk
        self.latest: dict[str, tuple[datetime, str]] = {}
        self._tasks: set[asyncio.Task] = set()
        bus.subscribe(PositionOpened, self._on_opened)

    async def explain(self, focus: str | None = None, event: str = "") -> dict[str, str]:
        """Ask the LLM to explain the current snapshots. Empty dict if off or on failure."""
        if not self.control.explain:
            return {}
        snaps = [s for s in self.snapshots() if focus is None or s.symbol == focus]
        if not snaps:
            return {}
        r = self.risk()
        prompt = json.dumps(
            {
                "event": event or "market analysis requested",
                "bot_state": self.control.state.name,
                "news_risk": {"level": r.level.value, "reason": r.reason},
                "coins": [s.to_dict() for s in snaps],
            },
            default=str,
        )
        try:
            data = await self.llm.complete_json(SYSTEM, prompt)
            texts = data.get("explanations", {})
        except LLMError as exc:
            log.warning("Explanation failed: %s", exc)
            return {}
        now, out = datetime.now(UTC), {}
        for s in snaps:
            text = str(texts.get(s.symbol, "")).strip()[:600]
            if text:
                out[s.symbol] = text
                self.latest[s.symbol] = (now, text)
                self.bus.publish(ExplanationReady(now, s.symbol, text))
        return out

    def explain_in_background(self, focus: str | None = None, event: str = "") -> None:
        if not self.control.explain:
            return
        task = asyncio.get_running_loop().create_task(self.explain(focus, event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_opened(self, e: PositionOpened) -> None:
        p = e.position
        self.explain_in_background(p.symbol, f"the bot just BOUGHT {p.symbol} at {p.entry_price}")
