"""One simple control panel for the running bot (Facade pattern).

Telegram and the web dashboard both talk ONLY to this class. They don't need
to know about the engine, feed, approval queue, news service or database.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from tradebot.application.approvals import ApprovalQueue
from tradebot.application.bot_control import BotControl, InvalidTransition
from tradebot.application.event_log import EventLog
from tradebot.application.explainer import Explainer
from tradebot.application.live_trader import LiveTrader
from tradebot.application.market_analysis import MarketSnapshot, analyze
from tradebot.application.metrics import trade_stats
from tradebot.application.news_risk_service import NewsRiskService
from tradebot.application.trading_engine import TradingEngine
from tradebot.domain.ports import TradeRepository


class BotFacade:
    def __init__(
        self,
        mode: str,
        budget: float,
        engine: TradingEngine,
        control: BotControl,
        approvals: ApprovalQueue,
        trader: LiveTrader,
        repo: TradeRepository,
        events: EventLog,
        news: NewsRiskService | None,
        strategies: list[str],
        configured_params: dict[str, dict] | None = None,
        explainer: Explainer | None = None,
    ) -> None:
        self.mode, self.budget = mode, budget
        self.engine, self.control, self.approvals = engine, control, approvals
        self.trader, self.repo, self.events, self.news = trader, repo, events, news
        self.strategies = strategies
        # settings from config.yaml are reused when you switch back to that strategy
        self.configured_params = configured_params or {}
        self.explainer = explainer

    # ------------------------------------------------------------------ analysis
    def snapshots(self) -> list[MarketSnapshot]:
        e = self.engine
        out = []
        for symbol in self.trader.symbols:
            snap = analyze(
                symbol,
                self.trader.recent_candles(symbol),
                e.timeframe_delta,
                e.strategy,
                in_position=symbol in e.positions,
                live_price=e.last_price.get(symbol),
            )
            if snap:
                out.append(snap)
        return out

    async def analyze(self) -> str:
        """Market picture for every coin (+ LLM explanation if Explain is on)."""
        snaps = self.snapshots()
        texts = await self.explainer.explain() if self.explainer else {}
        lines = []
        for s in snaps:
            lines.append(
                f"📊 {s.symbol} {s.price:,.2f} · 24h {s.change_24h_pct:+.1f}% · {s.trend} "
                f"(strength {s.trend_strength:.0f}) · RSI {s.rsi:.0f} · {s.status}\n"
                f"   {s.strategy_view}"
            )
            if s.symbol in texts:
                lines.append(f"   💬 {texts[s.symbol]}")
        return "\n".join(lines) or "Not enough data yet."

    # ------------------------------------------------------------------ read
    def state(self) -> dict[str, Any]:
        e = self.engine
        now = datetime.now(UTC)
        self.approvals.expire(now)
        positions = []
        for p in e.positions.values():
            price = e.last_price.get(p.symbol, p.entry_price)
            positions.append(
                {
                    "symbol": p.symbol,
                    "amount": p.amount,
                    "entry_price": p.entry_price,
                    "price": price,
                    "pnl": (price - p.entry_price) * p.amount - p.entry_fee,
                    "pnl_pct": (price / p.entry_price - 1) * 100,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "since": p.entry_time.isoformat(),
                }
            )
        trades = self.repo.load_trades()
        stats = asdict(trade_stats(trades))
        if stats["profit_factor"] == float("inf"):
            stats["profit_factor"] = None
        risk = e.market_risk.current()
        equity = e.equity()
        return {
            "mode": self.mode,
            "state": self.control.state.name,
            "manual_approval": self.control.manual_approval,
            "loop": self.control.loop,
            "explain": self.control.explain,
            "analysis": [s.to_dict() for s in self.snapshots()],
            "explanations": {
                sym: {"time": t.isoformat(), "text": text}
                for sym, (t, text) in (self.explainer.latest if self.explainer else {}).items()
            },
            "strategy": {"name": e.strategy.name, "params": e.strategy.params()},
            "strategies": self.strategies,
            "timeframe": self.trader.timeframe,
            "symbols": self.trader.symbols,
            "budget": self.budget,
            "cash": e.cash,
            "equity": equity,
            "return_pct": (equity / self.budget - 1) * 100,
            "prices": dict(e.last_price),
            "positions": positions,
            "risk": {
                "level": risk.level.value,
                "reason": risk.reason,
                "sentiment": risk.sentiment,
                "source": risk.source,
                "time": risk.time.isoformat() if risk.time else None,
                "headlines": [
                    {"title": n.title, "source": n.source, "published": n.published.isoformat()}
                    for n in (self.news.last_headlines if self.news else [])[:15]
                ],
            },
            "approvals": [
                {
                    "id": r.id,
                    "symbol": r.proposal.symbol,
                    "quote_amount": r.proposal.quote_amount,
                    "price": r.proposal.price,
                    "reason": r.reason,
                    "expires": r.expires.isoformat(),
                }
                for r in self.approvals.pending()
            ],
            "stats": stats,
            "trades": [
                {
                    "symbol": t.symbol,
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "reason": t.exit_reason.value,
                }
                for t in trades[-30:]
            ][::-1],
            "events": self.events.recent(60),
            "started_at": self.trader.started_at.isoformat() if self.trader.started_at else None,
        }

    def candles(self, symbol: str, limit: int = 300) -> list[dict[str, float]]:
        return [
            {"time": int(c.time.timestamp()), "open": c.open, "high": c.high, "low": c.low,
             "close": c.close}
            for c in self.trader.recent_candles(symbol)[-limit:]
        ]  # fmt: skip

    def status_text(self) -> str:
        s = self.state()
        lines = [
            f"🤖 {s['mode'].upper()} · {s['state']} · "
            f"{'MANUAL approval' if s['manual_approval'] else 'AUTO'} · "
            f"loop {'ON' if s['loop'] else 'OFF'} · explain {'ON' if s['explain'] else 'OFF'}",
            f"Strategy {s['strategy']['name']} · {s['timeframe']} · {', '.join(s['symbols'])}",
            f"Equity ${s['equity']:.2f} ({s['return_pct']:+.2f}%) · cash ${s['cash']:.2f}",
            f"News risk: {s['risk']['level']} — {s['risk']['reason']}",
        ]
        for p in s["positions"]:
            lines.append(
                f"📦 {p['symbol']} {p['pnl_pct']:+.2f}% (stop {p['stop_loss']:,.2f}, "
                f"target {p['take_profit']:,.2f})"
            )
        if not s["positions"]:
            lines.append("No open positions.")
        for r in s["approvals"]:
            lines.append(f"⏳ Waiting for approval: #{r['id']} buy {r['symbol']}")
        return "\n".join(lines)

    def trades_text(self) -> str:
        st = self.state()["stats"]
        if not st["count"]:
            return "No finished trades yet."
        pf = st["profit_factor"]
        return (
            f"📊 {st['count']} trades · win rate {st['win_rate_pct']:.0f}% · "
            f"profit factor {'∞' if pf is None else f'{pf:.2f}'}\n"
            f"Avg win {st['avg_win_pct']:+.2f}% · avg loss {st['avg_loss_pct']:+.2f}%\n"
            f"Total {st['total_pnl']:+.3f} USDT (fees {st['fees']:.3f})"
        )

    # ------------------------------------------------------------------ control
    async def start(self) -> str:
        """Start trading: analyse the market now and act on it, then keep going."""
        if not self.control.state.keeps_running:
            return "The bot was stopped with the kill switch. Restart the program."
        if not self.control.state.allows_new_trades:
            self.control.resume("started by user")
        results = await self.trader.evaluate_now()
        if self.explainer:
            self.explainer.explain_in_background(event="the user pressed Start")
        return "▶ Trading started.\n" + "\n".join(results)

    def set_loop(self, on: bool) -> str:
        self.control.set_loop(on)
        return (
            "Loop ON: after each trade the bot looks for the next one."
            if on
            else ("Loop OFF: the bot pauses after the next trade finishes.")
        )

    def set_explain(self, on: bool) -> str:
        self.control.set_explain(on)
        return "Explanations ON (uses the LLM)." if on else "Explanations OFF."

    def pause(self) -> str:
        return self._transition(self.control.pause, "Paused: no new buys. Exits stay active.")

    def resume(self) -> str:
        return self._transition(self.control.resume, "Resumed.")

    def stop(self) -> str:
        return self._transition(
            self.control.stop, "Kill switch: selling all positions and stopping."
        )

    def set_manual(self, manual: bool) -> str:
        self.control.set_manual_approval(manual)
        return "Every buy now needs your approval." if manual else "Bot buys automatically now."

    async def approve(self, request_id: str) -> str:
        return await self.engine.approve(request_id, datetime.now(UTC))

    def reject(self, request_id: str) -> str:
        ok = self.approvals.reject(request_id, datetime.now(UTC))
        return "Rejected." if ok else "Request not found or expired."

    async def reconfigure(
        self, strategy: str | None, params: dict | None, timeframe: str | None
    ) -> str:
        if strategy and params is None:
            params = self.configured_params.get(strategy)
        try:
            await self.trader.reconfigure(strategy, params, timeframe)
        except ValueError as exc:
            return f"Not changed: {exc}"
        return f"Now using {self.engine.strategy!r} on {self.trader.timeframe}."

    @staticmethod
    def _transition(action: Any, message: str) -> str:
        try:
            action()
        except InvalidTransition as exc:
            return f"Not possible: {exc}"
        return message
