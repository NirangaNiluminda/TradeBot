from __future__ import annotations

from dataclasses import dataclass

from tradebot.domain.models import TradeProposal
from tradebot.domain.risk.rules import Rejected, RiskContext, RiskRule


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    proposal: TradeProposal
    reason: str = ""


class RiskManager:
    """Runs a proposal through every rule in order. First rejection stops the chain."""

    def __init__(self, rules: list[RiskRule]) -> None:
        self.rules = rules

    def evaluate(self, proposal: TradeProposal, ctx: RiskContext) -> RiskDecision:
        for rule in self.rules:
            try:
                rule.apply(proposal, ctx)
            except Rejected as exc:
                return RiskDecision(False, proposal, str(exc))
        return RiskDecision(True, proposal, "; ".join(proposal.notes))
