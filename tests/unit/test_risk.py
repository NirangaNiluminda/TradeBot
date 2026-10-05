import pytest

from tradebot.bootstrap import build_risk_manager
from tradebot.config import RiskConfig
from tradebot.domain.models import RiskAssessment, RiskLevel, TradeProposal
from tradebot.domain.risk import PortfolioSnapshot, RiskContext


def ctx(
    rules, *, equity=10.0, day_start=10.0, free=10.0, invested=0.0, open_=0, level=RiskLevel.NORMAL
):
    return RiskContext(
        PortfolioSnapshot(equity, day_start, free, invested, open_),
        RiskAssessment(level, "test"),
        rules,
    )


@pytest.fixture
def manager():
    # $10 budget, 95% size, fixed 2% stop / 4% target
    return build_risk_manager(RiskConfig(exit_mode="percent", stop_loss_pct=2, take_profit_pct=4))


def test_normal_trade_is_sized_with_stop_and_target(manager, btc_rules):
    d = manager.evaluate(TradeProposal("BTC/USDT", price=100_000), ctx(btc_rules))
    assert d.approved
    assert d.proposal.quote_amount == pytest.approx(9.5)
    assert d.proposal.stop_loss == pytest.approx(98_000)
    assert d.proposal.take_profit == pytest.approx(104_000)


def test_danger_blocks_buying(manager, btc_rules):
    d = manager.evaluate(TradeProposal("BTC/USDT", 100_000), ctx(btc_rules, level=RiskLevel.DANGER))
    assert not d.approved and "DANGER" in d.reason


def test_caution_halves_size_which_is_below_binance_minimum_at_10_dollars(manager, btc_rules):
    d = manager.evaluate(
        TradeProposal("BTC/USDT", 100_000), ctx(btc_rules, level=RiskLevel.CAUTION)
    )
    assert not d.approved and "minimum" in d.reason


def test_caution_with_bigger_budget_trades_smaller_with_tighter_stop(btc_rules):
    m = build_risk_manager(
        RiskConfig(budget_usdt=100, exit_mode="percent", stop_loss_pct=2, take_profit_pct=4)
    )
    d = m.evaluate(
        TradeProposal("BTC/USDT", 100_000),
        ctx(btc_rules, equity=100, day_start=100, free=100, level=RiskLevel.CAUTION),
    )
    assert d.approved
    assert d.proposal.quote_amount == pytest.approx(47.5)
    assert d.proposal.stop_loss == pytest.approx(98_500)  # 2% * 0.75 = 1.5%


def test_daily_loss_limit(manager, btc_rules):
    d = manager.evaluate(TradeProposal("BTC/USDT", 100_000), ctx(btc_rules, equity=9.4, free=9.4))
    assert not d.approved and "daily loss" in d.reason


def test_max_open_positions(manager, btc_rules):
    d = manager.evaluate(TradeProposal("BTC/USDT", 100_000), ctx(btc_rules, open_=1))
    assert not d.approved


def test_budget_cap_applies_even_with_large_balance(manager, btc_rules):
    # testnet gives thousands of fake USDT — the bot must still only use $10
    d = manager.evaluate(TradeProposal("BTC/USDT", 100_000), ctx(btc_rules, free=10_000))
    assert d.proposal.quote_amount == pytest.approx(9.5)


def test_atr_exits_scale_with_volatility(btc_rules):
    m = build_risk_manager(RiskConfig(stop_atr_mult=2, take_profit_atr_mult=4))
    # ATR 1,500 on a 100,000 price -> stop 2 x 1.5% = 3%, target 6%
    d = m.evaluate(TradeProposal("BTC/USDT", price=100_000, atr=1_500), ctx(btc_rules))
    assert d.proposal.stop_loss == pytest.approx(97_000)
    assert d.proposal.take_profit == pytest.approx(106_000)


def test_atr_stop_is_clamped_between_min_and_max(btc_rules):
    m = build_risk_manager(RiskConfig(stop_atr_mult=2, take_profit_atr_mult=4, max_stop_pct=10))
    wild = m.evaluate(TradeProposal("BTC/USDT", price=100_000, atr=20_000), ctx(btc_rules))
    assert wild.proposal.stop_loss == pytest.approx(90_000)  # capped at -10%
    assert wild.proposal.take_profit == pytest.approx(120_000)  # ratio 2:1 kept
    calm = m.evaluate(TradeProposal("BTC/USDT", price=100_000, atr=10), ctx(btc_rules))
    assert calm.proposal.stop_loss == pytest.approx(99_000)  # at least -1%


def test_atr_missing_falls_back_to_percent(btc_rules):
    m = build_risk_manager(RiskConfig(stop_loss_pct=5, take_profit_pct=10))
    d = m.evaluate(TradeProposal("BTC/USDT", price=100_000), ctx(btc_rules))  # atr = NaN
    assert d.proposal.stop_loss == pytest.approx(95_000)


def test_stop_only_exit_has_no_take_profit(btc_rules):
    import math

    m = build_risk_manager(RiskConfig(exit_mode="stop_only", stop_loss_pct=15))
    d = m.evaluate(TradeProposal("BTC/USDT", price=100_000), ctx(btc_rules))
    assert d.proposal.stop_loss == pytest.approx(85_000) and math.isinf(d.proposal.take_profit)


def test_split_budget_gives_each_coin_its_share(btc_rules):
    m = build_risk_manager(RiskConfig(budget_usdt=50, max_open_positions=5, split_budget=True))
    d = m.evaluate(TradeProposal("BTC/USDT", 100_000), ctx(btc_rules, equity=50, day_start=50,
                                                           free=50))  # fmt: skip
    assert d.proposal.quote_amount == pytest.approx(9.5)  # 95% of 50 / 5
