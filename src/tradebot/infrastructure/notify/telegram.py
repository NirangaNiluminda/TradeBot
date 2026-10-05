"""Telegram: notifications + remote control + approve/reject buttons.

Uses the Telegram Bot HTTP API directly (no extra library).

Security: the bot ONLY reacts to messages from your own chat id
(TELEGRAM_CHAT_ID). Messages from anyone else are ignored.

Commands:  /trade /analyze /status /trades /risk /pause /loop /explain /manual /auto /stop
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from tradebot.application.approvals import ApprovalRequested, ApprovalResolved
from tradebot.application.bot_control import BotControl
from tradebot.application.bot_facade import BotFacade
from tradebot.application.event_bus import EventBus
from tradebot.domain.models import target_text

log = logging.getLogger(__name__)

HELP = (
    "Commands:\n"
    "/trade – START trading: analyse now and act\n"
    "/analyze – market picture for every coin\n"
    "/status – equity, positions, news risk\n"
    "/trades – accuracy of finished trades\n"
    "/risk – latest news risk\n"
    "/pause – no new buys (exits stay active)\n"
    "/loop on|off – keep trading after each trade, or stop after one\n"
    "/explain on|off – simple-words explanations from the LLM\n"
    "/manual – every buy needs your OK\n"
    "/auto – bot buys by itself\n"
    "/stop confirm – KILL SWITCH: sell everything and stop"
)


class TelegramClient:
    def __init__(self, token: str, http: httpx.AsyncClient) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = http

    async def call(self, method: str, http_timeout: float = 30, **params: Any) -> Any:
        r = await self._http.post(f"{self._base}/{method}", json=params, timeout=http_timeout)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data["result"]


class TelegramNotifier:
    """NotifierPort: send a text message to your chat."""

    def __init__(self, client: TelegramClient, chat_id: str, label: str = "") -> None:
        self.client, self.chat_id = client, chat_id
        self.prefix = f"[{label}] " if label else ""  # tells two bots' messages apart

    async def send(self, text: str) -> None:
        await self.client.call("sendMessage", chat_id=self.chat_id, text=self.prefix + text)


class TelegramController:
    """Listens for your commands and button presses, and posts approval requests."""

    def __init__(
        self,
        client: TelegramClient,
        chat_id: str,
        facade: BotFacade,
        control: BotControl,
        bus: EventBus,
    ) -> None:
        self.client, self.chat_id, self.facade, self.control = client, str(chat_id), facade, control
        self._offset = 0
        self._tasks: set[asyncio.Task] = set()
        bus.subscribe(ApprovalRequested, lambda e: self._spawn(self._ask_approval(e)))
        bus.subscribe(ApprovalResolved, lambda e: self._spawn(self._resolved(e)))

    async def run(self) -> None:
        await self.client.call("sendMessage", chat_id=self.chat_id, text="🤖 Bot online.\n" + HELP)
        while self.control.state.keeps_running:
            try:
                # long polling: Telegram holds the request up to 25s until a message arrives
                updates = await self.client.call(
                    "getUpdates", http_timeout=40, offset=self._offset, timeout=25
                )
            except Exception as exc:
                log.warning("Telegram polling failed: %s", exc)
                await asyncio.sleep(5)
                continue
            for update in updates:
                self._offset = update["update_id"] + 1
                try:
                    await self.handle(update)
                except Exception:
                    log.exception("Telegram update failed")

    # ------------------------------------------------------------------ incoming
    async def handle(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._button(update["callback_query"])
            return
        message = update.get("message") or {}
        if str(message.get("chat", {}).get("id")) != self.chat_id:
            return  # not you -> ignore
        text = (message.get("text") or "").strip()
        await self._reply(await self.command(text))

    async def command(self, text: str) -> str:
        cmd, *args = text.split() or [""]
        cmd = cmd.split("@")[0].lower()
        f = self.facade
        if cmd == "/status":
            return f.status_text()
        if cmd == "/trades":
            return f.trades_text()
        if cmd == "/risk":
            r = f.state()["risk"]
            return f"News risk: {r['level']} (sentiment {r['sentiment']:+.2f})\n{r['reason']}"
        if cmd in ("/trade", "/resume"):
            return await f.start()
        if cmd == "/analyze":
            return await f.analyze()
        if cmd == "/pause":
            return f.pause()
        if cmd in ("/loop", "/explain"):
            if args[:1] not in (["on"], ["off"]):
                return f"Use: {cmd} on   or   {cmd} off"
            on = args[0] == "on"
            return f.set_loop(on) if cmd == "/loop" else f.set_explain(on)
        if cmd == "/manual":
            return f.set_manual(True)
        if cmd == "/auto":
            return f.set_manual(False)
        if cmd == "/stop":
            if args[:1] == ["confirm"]:
                return f.stop()
            return "This sells EVERYTHING and stops the bot. Send: /stop confirm"
        return HELP

    async def _button(self, query: dict[str, Any]) -> None:
        chat = str(query.get("message", {}).get("chat", {}).get("id"))
        if chat != self.chat_id:
            return
        action, _, request_id = str(query.get("data", "")).partition(":")
        if action == "approve":
            result = await self.facade.approve(request_id)
        elif action == "reject":
            result = self.facade.reject(request_id)
        else:
            result = "Unknown button"
        await self.client.call("answerCallbackQuery", callback_query_id=query["id"], text=result)

    # ------------------------------------------------------------------ outgoing
    async def _ask_approval(self, e: ApprovalRequested) -> None:
        r, p = e.request, e.request.proposal
        text = (
            f"⏳ Approve BUY {p.symbol}?\n"
            f"≈ ${p.quote_amount:.2f} at ~{p.price:,.2f}\n"
            f"Stop {p.stop_loss:,.2f} ({(p.stop_loss / p.price - 1) * 100:+.1f}%) · "
            f"Target {target_text(p.take_profit, p.price)}\n"
            f"Why: {r.reason}\n"
            f"Expires {r.expires:%H:%M} UTC"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{r.id}"},
                    {"text": "❌ Reject", "callback_data": f"reject:{r.id}"},
                ]
            ]
        }
        await self.client.call(
            "sendMessage", chat_id=self.chat_id, text=text, reply_markup=keyboard
        )

    async def _resolved(self, e: ApprovalResolved) -> None:
        if e.outcome in ("expired", "failed", "replaced"):
            await self._reply(f"Request #{e.request.id} ({e.request.proposal.symbol}) {e.outcome}.")

    async def _reply(self, text: str) -> None:
        await self.client.call("sendMessage", chat_id=self.chat_id, text=text)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


async def find_chat_ids(client: TelegramClient) -> list[tuple[str, str]]:
    """Chats that recently messaged the bot: [(chat_id, name)]. Used by `telegram-setup`."""
    updates = await client.call("getUpdates")
    found: dict[str, str] = {}
    for u in updates:
        chat = (u.get("message") or {}).get("chat")
        if chat:
            found[str(chat["id"])] = chat.get("username") or chat.get("first_name") or ""
    return list(found.items())
