"""Web dashboard (FastAPI). Talks only to the BotFacade.

Runs inside the same process as the bot, on http://127.0.0.1:8000 by default.
It listens on localhost only, so nobody else on your network can reach it.

Every endpoint is `async`, so it runs on the bot's own event loop: dashboard
actions and trading never touch the bot's data at the same moment.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from tradebot.application.bot_facade import BotFacade

STATIC = Path(__file__).parent / "static"


class Reconfigure(BaseModel):
    strategy: str | None = None
    timeframe: str | None = None


class TradeMode(BaseModel):
    manual: bool


def create_app(facade: BotFacade) -> FastAPI:
    app = FastAPI(title="tradebot", docs_url=None, redoc_url=None)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/state")
    async def state() -> dict:
        return facade.state()

    @app.get("/api/candles/{base}/{quote}")
    async def candles(base: str, quote: str) -> list[dict]:
        return facade.candles(f"{base}/{quote}")

    @app.post("/api/control/{action}")
    async def control(action: str) -> dict:
        actions = {"pause": facade.pause, "resume": facade.resume, "stop": facade.stop}
        if action not in actions:
            raise HTTPException(404, "unknown action")
        return {"message": actions[action]()}

    @app.post("/api/mode")
    async def mode(body: TradeMode) -> dict:
        return {"message": facade.set_manual(body.manual)}

    @app.post("/api/approvals/{request_id}/{decision}")
    async def decide(request_id: str, decision: str) -> dict:
        if decision == "approve":
            return {"message": await facade.approve(request_id)}
        if decision == "reject":
            return {"message": facade.reject(request_id)}
        raise HTTPException(404, "unknown decision")

    @app.post("/api/reconfigure")
    async def reconfigure(body: Reconfigure) -> dict:
        return {"message": await facade.reconfigure(body.strategy, None, body.timeframe)}

    return app
