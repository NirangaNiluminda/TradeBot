# tradebot

A Binance **spot** trading bot that I'm building step by step:
strategy backtesting → news/LLM risk filter → testnet → live with a small budget.

> ⚠️ Trading crypto is risky. Most automated strategies lose money after fees.
> This bot is built to be tested on past data and fake money first.

See [PLAN.md](PLAN.md) for the full plan and [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md)
for a beginner-friendly explanation.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                          # install dependencies
cp config/.env.example .env      # then put your API keys in .env (not needed for backtests)
```

## Commands

```bash
uv run tradebot strategies                 # list strategies
uv run tradebot download                   # download candles into data/
uv run tradebot backtest                   # backtest the strategy in config/config.yaml
uv run tradebot backtest -s breakout -t 4h # another strategy / timeframe
uv run tradebot backtest --days 730 --save # last 2 years, save trades to data/backtests/
uv run tradebot compare -t 4h              # rank ALL strategies on the same data
uv run tradebot optimize                   # HONEST test: tune on old data, score on unseen data
uv run tradebot optimize -s breakout --years 5
uv run tradebot optimize --symbols SOL/USDT,BNB/USDT,XRP/USDT   # coins never used for tuning
```

Settings (symbols, timeframe, strategy, risk numbers, backtest period) are in
[config/config.yaml](config/config.yaml).

## Development

```bash
uv run pytest           # run tests
uv run ruff check src   # lint
uv run ruff format src  # format
```

## Project status

- [x] Phase 1 — foundation, 5 strategies, backtester, CLI
- [x] Phase 1.5 — ATR stops, walk-forward optimizer, 4h default, breakout chosen
- [ ] Phase 2 — news + LLM risk gate
- [ ] Phase 3 — Binance testnet trading
- [ ] Phase 4 — Telegram + web dashboard
- [ ] Phase 5 — evaluate on testnet
- [ ] Phase 6 — live with $10
- [ ] Phase 7 — Docker / VPS
