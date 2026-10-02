"""Manual approvals, the control facade, Telegram commands and the dashboard API."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.conftest import HOUR, candle
from tradebot.application.approvals import ApprovalQueue, ApprovalResolved
from tradebot.application.bot_control import BotControl
from tradebot.application.bot_facade import BotFacade
from tradebot.application.event_bus import EventBus
from tradebot.application.event_log import EventLog
from tradebot.application.indicator_feed import RollingFeed
from tradebot.application.live_trader import LiveTrader
from tradebot.application.market_risk import StaticMarketRisk
from tradebot.application.trading_engine import TradingEngine
from tradebot.bootstrap import build_risk_manager
from tradebot.config import RiskConfig
from tradebot.domain.strategies import strategy_factory
from tradebot.infrastructure.exchange.simulated import SimulatedExchange
from tradebot.infrastructure.notify.telegram import TelegramController
from tradebot.infrastructure.persistence.sqlite_repository import SqliteTradeRepository
from tradebot.interfaces.web.app import create_app

SYM = "XYZ/USDT"
NOW = datetime(2025, 1, 1, 1, tzinfo=UTC)


@pytest.fixture
def rig(unit_rules, tmp_path):
    bus = EventBus()
    control = BotControl(bus, manual=True)
    approvals = ApprovalQueue(bus, timeout_minutes=15)
    ex = SimulatedExchange({SYM: unit_rules}, 10_000, HOUR)
    ex.on_candle(SYM, candle(0, 100.0))
    strategy = strategy_factory.create("breakout")
    feed = RollingFeed(strategy)
    feed.seed(SYM, [candle(i, 100.0 + i) for i in range(30)])
    engine = TradingEngine(
        strategy, feed, ex, build_risk_manager(RiskConfig()), StaticMarketRisk(), bus,
        timedelta(hours=1), starting_cash=10.0, control=control, approvals=approvals,
    )  # fmt: skip
    engine.last_price[SYM] = 100.0
    trader = LiveTrader(engine, feed, None, [SYM], "1h", control)
    repo = SqliteTradeRepository(tmp_path / "db.sqlite")
    facade = BotFacade(
        "testnet", 10.0, engine, control, approvals, trader, repo, EventLog(bus), None,
        list(strategy_factory.available()),
    )  # fmt: skip
    return engine, approvals, facade, bus, control


async def test_manual_mode_waits_for_approval_then_buys(rig):
    engine, approvals, facade, *_ = rig
    await engine._try_open(SYM, 100.0, 1.0, NOW, "breakout")
    assert not engine.positions and len(approvals.pending()) == 1
    req = approvals.pending()[0]
    assert await engine.approve(req.id, NOW + timedelta(minutes=1)) == "bought"
    assert SYM in engine.positions and not approvals.pending()


async def test_reject_and_expiry(rig):
    engine, approvals, facade, bus, _ = rig
    outcomes = []
    bus.subscribe(ApprovalResolved, lambda e: outcomes.append(e.outcome))
    await engine._try_open(SYM, 100.0, 1.0, NOW, "x")
    assert facade.reject(approvals.pending()[0].id) == "Rejected."
    await engine._try_open(SYM, 100.0, 1.0, NOW, "x")
    req = approvals.pending()[0]
    assert (
        await engine.approve(req.id, NOW + timedelta(minutes=16)) == "request not found or expired"
    )
    assert outcomes == ["rejected", "expired"] and not engine.positions


async def test_approval_rechecks_risk_at_current_price(rig):
    engine, approvals, *_ = rig
    await engine._try_open(SYM, 100.0, 1.0, NOW, "x")
    engine.control.pause()
    msg = await engine.approve(approvals.pending()[0].id, NOW)
    assert "cannot buy now" in msg and not engine.positions


async def test_telegram_commands_and_stranger_is_ignored(rig):
    engine, approvals, facade, bus, control = rig
    sent = []

    class FakeClient:
        async def call(self, method, **params):
            sent.append((method, params))
            return {}

    tg = TelegramController(FakeClient(), "42", facade, control, bus)
    assert "PAUSED" in await tg.command("/status") or "RUNNING" in await tg.command("/status")
    assert "Paused" in await tg.command("/pause")
    assert "/stop confirm" in await tg.command("/stop")  # needs confirmation
    assert control.state.keeps_running
    await tg.handle({"message": {"chat": {"id": 999}, "text": "/stop confirm"}})
    assert control.state.keeps_running and not sent  # stranger ignored, no reply
    await tg.handle({"message": {"chat": {"id": 42}, "text": "/resume"}})
    assert control.state.allows_new_trades and sent[-1][1]["text"] == "Resumed."


async def test_telegram_approve_button(rig):
    engine, approvals, facade, bus, control = rig

    class FakeClient:
        async def call(self, method, **params):
            return {}

    tg = TelegramController(FakeClient(), "42", facade, control, bus)
    await engine._try_open(SYM, 100.0, 1.0, datetime.now(UTC), "x")  # facade uses real time
    rid = approvals.pending()[0].id
    await tg.handle({"callback_query": {"id": "q", "data": f"approve:{rid}",
                                        "message": {"chat": {"id": 42}}}})  # fmt: skip
    assert SYM in engine.positions


def test_dashboard_api(rig):
    engine, approvals, facade, bus, control = rig
    client = TestClient(create_app(facade))
    assert "tradebot" in client.get("/").text
    s = client.get("/api/state").json()
    assert s["mode"] == "testnet" and s["state"] == "RUNNING" and s["equity"] == 10.0
    assert len(client.get(f"/api/candles/{SYM}").json()) == 30
    assert "Paused" in client.post("/api/control/pause").json()["message"]
    assert client.get("/api/state").json()["state"] == "PAUSED"
    assert client.post("/api/control/nope").status_code == 404
    client.post("/api/mode", json={"manual": False})
    assert control.manual_approval is False
