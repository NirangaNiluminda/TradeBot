from __future__ import annotations

from typing import Protocol

from tradebot.domain.models import RiskAssessment


class MarketRiskPort(Protocol):
    """Gives the current market risk opinion.

    Phase 1: a fixed NORMAL value. Phase 2: the news + LLM service implements this,
    and nothing in the trading engine has to change.
    """

    def current(self) -> RiskAssessment: ...
