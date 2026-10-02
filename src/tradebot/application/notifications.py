"""Turns engine events into short human messages (an event-bus listener).

The engine publishes events; this class decides what is worth telling you and
how to word it, then hands the text to any NotifierPort (console, Telegram...).
"""

from __future__ import annotations

import asyncio
import logging

from tradebot.application.bot_control import BotStateChanged
from tradebot.application.event_bus import EventBus
from tradebot.domain.events import (
    PositionClosed,
    PositionOpened,
    RiskChanged,
    SignalGenerated,
    TradeRejected,
)
from tradebot.domain.ports import NotifierPort

log = logging.getLogger(__name__)
_LEVEL_ICON = {"NORMAL": "🟢", "CAUTION": "🟡", "DANGER": "🔴"}


def describe_opened(e: PositionOpened) -> str:
    p = e.position
    return (
        f"🟢 BOUGHT {p.symbol}\n"
        f"{p.amount:g} @ {p.entry_price:,.2f} (≈ ${p.cost:.2f})\n"
        f"Stop-loss {p.stop_loss:,.2f} ({(p.stop_loss / p.entry_price - 1) * 100:+.1f}%) · "
        f"Target {p.take_profit:,.2f} ({(p.take_profit / p.entry_price - 1) * 100:+.1f}%)"
    )


def describe_closed(e: PositionClosed) -> str:
    t = e.trade
    icon = "✅" if t.pnl > 0 else "❌"
    return (
        f"{icon} SOLD {t.symbol} ({t.exit_reason.value})\n"
        f"{t.entry_price:,.2f} → {t.exit_price:,.2f}\n"
        f"Result: {t.pnl:+.3f} USDT ({t.pnl_pct:+.2f}%)"
    )


def describe_risk(e: RiskChanged) -> str:
    c = e.current
    return (
        f"{_LEVEL_ICON[c.level.value]} News risk: {e.previous.level.value} → {c.level.value}\n"
        f"{c.reason}"
    )


class EventNotifier:
    def __init__(self, bus: EventBus, notifiers: list[NotifierPort], verbose: bool = False) -> None:
        self.notifiers = notifiers
        self._tasks: set[asyncio.Task] = set()
        bus.subscribe(PositionOpened, lambda e: self._send(describe_opened(e)))
        bus.subscribe(PositionClosed, lambda e: self._send(describe_closed(e)))
        bus.subscribe(RiskChanged, lambda e: self._send(describe_risk(e)))
        bus.subscribe(BotStateChanged, lambda e: self._send(f"⚙️ Bot {e.current} ({e.reason})"))
        if verbose:
            bus.subscribe(
                SignalGenerated,
                lambda e: self._send(f"📈 {e.symbol} {e.signal.action.value}: {e.signal.reason}"),
            )
            bus.subscribe(TradeRejected, lambda e: self._send(f"⛔ {e.symbol}: {e.reason}"))

    def _send(self, text: str) -> None:
        for notifier in self.notifiers:
            task = asyncio.get_running_loop().create_task(self._safe_send(notifier, text))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _safe_send(notifier: NotifierPort, text: str) -> None:
        try:
            await notifier.send(text)
        except Exception as exc:  # a broken notifier must never stop trading
            log.warning("Notifier %s failed: %s", type(notifier).__name__, exc)
