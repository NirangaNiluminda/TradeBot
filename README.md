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

## Running the bot

```bash
uv run tradebot analyze --explain          # market picture now, in simple words (never trades)
uv run tradebot run                        # TESTNET + dashboard; waits for you to press ▶ Start
uv run tradebot run --start                # ...start trading immediately
uv run tradebot run --no-loop              # ...do one trade, then pause
uv run tradebot run --explain              # ...LLM explains analyses and trades
uv run tradebot run --manual               # ...approve every buy yourself
uv run tradebot status                     # cash, open positions, live P/L
uv run tradebot trades                     # accuracy of the bot's real trades
uv run tradebot close-all                  # emergency: sell everything (stop `run` first)
uv run tradebot telegram-setup             # find your Telegram chat id
```

While `run` is going, open the dashboard at **http://127.0.0.1:8000**:
**▶ Start trading** (analyses the market immediately and acts), Pause, **Loop** and
**Explain** switches, market analysis per coin, equity, positions with stop/target lines,
news risk + headlines, approve/reject buttons, strategy & timeframe switch, kill switch.
Telegram has the same controls: `/trade`, `/analyze`, `/loop on|off`, `/explain on|off`, `/pause`.

Ctrl+C stops the program, but open positions **keep their stop-loss and
take-profit on Binance** (an OCO order), and the bot picks them up again on restart.

Real money (only after the testnet results are good):
`uv run tradebot run --mode live --confirm-real-money`. It refuses to start if the API
key is allowed to withdraw funds.

## Development

```bash
uv run pytest           # run tests
uv run ruff check src   # lint
uv run ruff format src  # format
```

## Project status

- [x] Phase 1 — foundation, 5 strategies, backtester, CLI
- [x] Phase 1.5 — ATR stops, walk-forward optimizer, 4h default, breakout chosen
- [x] Phase 2 — news + LLM risk gate (Gemini → Groq fallback)
- [x] Phase 3 — Binance testnet trading (OCO stops, SQLite, restart-safe)
- [x] Phase 4 — Telegram + web dashboard + manual approval
- [ ] Phase 5 — evaluate on testnet
- [ ] Phase 6 — live with $10
- [ ] Phase 7 — Docker / VPS
