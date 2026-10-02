from __future__ import annotations

from tradebot.domain.models import RiskAssessment


class StaticMarketRisk:
    """A MarketRiskPort that always returns the same opinion.

    Used for backtests (we have no historical news) and until the news/LLM
    service is added in Phase 2.
    """

    def __init__(self, assessment: RiskAssessment | None = None) -> None:
        self._assessment = assessment or RiskAssessment()

    def current(self) -> RiskAssessment:
        return self._assessment
