# Crypto Trading Bot — Project Plan

> Status: **Phase 1 + 1.5 complete (ATR stops, walk-forward optimizer; `breakout` on 4h chosen) — next: Phase 2 (news + LLM risk gate)**
> Last updated: 2026-10-02

---

## 1. What we are building (in one paragraph)

A Python bot that watches live Binance prices, uses a **strategy** (a set of rules
based on price indicators) to find good moments to buy or sell, and asks an **LLM**
to read the latest crypto news and say whether it is safe to trade right now.
A **risk manager** has the final word and protects the money with stop-losses and
loss limits. You control everything from a **web dashboard** and get alerts with
Approve / Reject buttons on **Telegram**.

We always go in this order: **Backtest → Testnet (fake money) → Live ($10)**.

---

## 2. Agreed decisions

| Topic | Decision |
|---|---|
| Capital | $10 to start. Bot respects Binance minimum order (~$5) automatically |
| Market | **Spot only** — no leverage, no futures |
| Coins | BTC/USDT, ETH/USDT (more can be added from dashboard) |
| Timeframe | Changeable at runtime: 15m / 1h / 4h. Default **4h** (1h lost more to fees and noise in tests) |
| Strategies | Plug-in system; compare via backtest; switch at runtime |
| Trade mode | Runtime switch: **Manual** (approve each trade) or **Auto** |
| LLM | Cloud APIs only (laptop: 16 GB RAM, no GPU). Start with **free tiers** (Gemini, Groq). Provider swappable via config |
| News | Free sources: RSS (CoinDesk, Cointelegraph, Decrypt), Fear & Greed Index, optional Reddit |
| Alerts | Telegram bot |
| UI | Web dashboard |
| Runs on | Laptop first → Docker → VPS later |
| Language | Python 3.11+ |
| Sharing | Friends run **their own copy with their own keys** — never pool money |

### Default safety numbers

| Rule | Value | Meaning |
|---|---|---|
| Stop-loss | 2% | If price falls 2% below buy price → sell automatically |
| Take-profit | 4% | If price rises 4% above buy price → sell automatically |
| Daily loss limit | 5% | If the bot loses 5% of the balance in one day → stop until tomorrow |
| Max open trades | 1 | Only one position at a time (because of the $5 minimum) |
| Trading budget | $10 | Even on testnet (which gives lots of fake money) the bot only uses $10, so results are realistic |

---

## 3. Simple glossary

| Term | Simple meaning |
|---|---|
| **Candle (OHLCV)** | Price summary for a time period: Open, High, Low, Close price + Volume traded |
| **Timeframe** | The size of each candle (15 minutes, 1 hour, 4 hours...) |
| **Indicator** | A calculation on past candles, e.g. average price of the last 20 candles |
| **EMA** | Exponential Moving Average — average price that cares more about recent candles |
| **RSI** | Relative Strength Index (0–100). Below 30 = "oversold" (maybe too cheap), above 70 = "overbought" |
| **Signal** | The strategy's suggestion: BUY, SELL, or HOLD |
| **Backtest** | Running a strategy on past data to see how it *would* have performed |
| **Testnet** | Binance's practice exchange — real prices, fake money |
| **Slippage** | Difference between the price you expected and the price you actually got |
| **Fee** | Binance charges ~0.1% per trade (less if paying with BNB) |
| **Drawdown** | The biggest drop from a high point in your balance — measures pain |
| **Win rate** | % of trades that made money |
| **Spot** | Buying the actual coin with money you have (no borrowing) |

---

## 4. Architecture

We use **Clean Architecture** (also called *Hexagonal* or *Ports & Adapters*).

**The core idea:** the important business logic (strategies, risk rules, trading
decisions) lives in the middle and **does not know** about Binance, Gemini,
Telegram, or the database. It only talks to **interfaces** ("ports"). The outside
world plugs in through **adapters** that implement those interfaces.

**Why this matters for you:**
- Change LLM provider (Gemini → Groq → Claude) = write/choose one adapter, change one config line. Nothing else changes.
- The **same strategy code** runs in backtest, testnet and live — only the adapters differ. So backtest results are trustworthy.
- Every part can be tested alone with fake ("mock") adapters.

### 4.1 Layers (dependencies point inward only)

```
┌───────────────────────────────────────────────────────────────────┐
│  4. INTERFACES / DRIVERS   (FastAPI dashboard, Telegram bot, CLI)  │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  3. INFRASTRUCTURE / ADAPTERS                                │  │
│  │     Binance (ccxt), Gemini, Groq, Claude, RSS, SQLite...     │  │
│  │  ┌───────────────────────────────────────────────────────┐  │  │
│  │  │  2. APPLICATION  (use cases / services)                │  │  │
│  │  │     TradingEngine, NewsRiskService, BacktestRunner     │  │  │
│  │  │  ┌─────────────────────────────────────────────────┐  │  │  │
│  │  │  │  1. DOMAIN  (pure Python, no libraries)          │  │  │  │
│  │  │  │     Candle, Signal, Order, Position, RiskLevel,  │  │  │  │
│  │  │  │     Strategy interface, Risk rules               │  │  │  │
│  │  │  └─────────────────────────────────────────────────┘  │  │  │
│  │  └───────────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
          Rule: inner layers NEVER import from outer layers.
```

### 4.2 Data flow

```
 Binance candles ──► MarketDataPort ──► Strategy ──► Signal (BUY/SELL/HOLD)
                                                          │
 RSS / Fear&Greed ──► NewsSourcePort ──► NewsRiskService  │
                                           │ (LLMPort)    │
                                           ▼              ▼
                                      RiskLevel ──► RiskManager (chain of rules)
                                                          │ approved / rejected / resized
                                                          ▼
                                            Mode: AUTO ──► OrderExecutor ──► ExchangePort
                                            Mode: MANUAL ─► NotifierPort (Telegram) ─► you approve
                                                          │
                                         EventBus ──► Dashboard, Telegram, Database logs
```

### 4.3 Ports (the contracts / interfaces)

All defined in `domain/ports/` or `application/ports/` as Python `Protocol` or `ABC` classes.

| Port | Responsibility | Adapters (implementations) |
|---|---|---|
| `MarketDataPort` | Get historical candles; subscribe to live candles | `CcxtMarketData` (live/testnet), `CsvMarketData` (backtest) |
| `ExchangePort` | Place/cancel orders, read balance, read min order size | `CcxtExchange` (live/testnet), `SimulatedExchange` (backtest & paper) |
| `LLMPort` | `analyze(prompt, schema) -> structured result` | `GeminiLLM`, `GroqLLM`, `ClaudeLLM`, `OpenAILLM`, `FakeLLM` (tests) |
| `NewsSourcePort` | Fetch recent headlines | `RssNewsSource`, `FearGreedSource`, `RedditSource` |
| `NotifierPort` | Send alerts; ask for approval | `TelegramNotifier`, `ConsoleNotifier` |
| `TradeRepository` | Save/load trades, signals, risk decisions | `SqliteTradeRepository`, `InMemoryRepository` (tests) |
| `Clock` | Current time | `SystemClock` (live), `BacktestClock` (replay) |

> The `Clock` port is small but important: it lets the **same engine** run on
> historical data at full speed during backtests.

### 4.4 Design patterns used (and why)

| Pattern | Where | Why |
|---|---|---|
| **Strategy** | `Strategy` interface — `EmaCross`, `RsiReversion`, `Breakout`... | Swap trading logic at runtime without touching the engine |
| **Adapter** | Every class in `infrastructure/` | Wraps a 3rd-party API (ccxt, Gemini SDK...) behind our port |
| **Factory + Registry** | `LLMFactory`, `StrategyRegistry`, `NewsSourceFactory` | Build the right implementation from a config name, e.g. `llm.provider: groq`. New strategies register themselves with a decorator |
| **Chain of Responsibility** | Risk rules: `RiskLevelRule → DailyLossRule → MaxPositionsRule → MinNotionalRule → PositionSizeRule` | Each rule is small and independent; add/remove rules without editing others |
| **Decorator** | `RetryingLLM`, `CachingLLM`, `RateLimitedLLM` wrap any `LLMPort` | Retries, caching & free-tier rate limits added without changing provider code |
| **Fallback (Composite)** | `FallbackLLM([gemini, groq])` | If Gemini fails/limits out, try Groq. If all fail → treat as **CAUTION** (fail-safe) |
| **Composite** | `EnsembleStrategy([s1, s2, s3])` | Combine strategies by voting — our "innovative" experiments |
| **Observer / Event Bus** | `EventBus` publishes `SignalGenerated`, `OrderFilled`, `RiskChanged`... | Dashboard, Telegram and DB logger listen without the engine knowing they exist |
| **State** | `BotState`: `Stopped`, `Running`, `Paused`, `Halted(daily-loss)` | Clear, safe behaviour per state; kill switch = move to `Stopped` |
| **Command** | `PlaceOrderCommand`, `ApproveTradeCommand` | Manual approvals queue up as commands; easy to log/undo/expire |
| **Repository** | `TradeRepository` | Hides SQL from the business logic |
| **Dependency Injection / Composition Root** | `bootstrap.py` | The **only** place that wires concrete adapters to ports, based on config |

### 4.5 Example: how LLM swapping looks in code

```python
# domain/ports/llm.py  — the contract (knows nothing about any vendor)
class LLMPort(Protocol):
    async def analyze(self, prompt: str, schema: type[BaseModel]) -> BaseModel: ...

# infrastructure/llm/groq_llm.py — one adapter
class GroqLLM(LLMPort):
    async def analyze(self, prompt, schema): ...   # calls Groq SDK, parses JSON

# infrastructure/llm/factory.py
@llm_registry.register("groq")
def build_groq(cfg) -> LLMPort: return GroqLLM(api_key=cfg.api_key, model=cfg.model)

# config.yaml — the ONLY thing you change to switch provider
llm:
  providers: [gemini, groq]     # order = fallback order
  cache_minutes: 15
```

---

## 5. Folder structure

```
crypto/
├── PLAN.md
├── README.md                     # how to install & run (simple steps)
├── docs/
│   └── HOW_IT_WORKS.md           # beginner guide to the bot & trading terms
├── pyproject.toml                # dependencies (managed with uv or pip)
├── config/
│   ├── config.yaml               # strategy, timeframe, risk numbers, providers
│   └── .env.example              # API key template (real .env is never committed)
├── src/tradebot/
│   ├── domain/                   # LAYER 1 — pure Python, no external libs
│   │   ├── models.py             # Candle, Signal, Order, Position, Trade, RiskLevel
│   │   ├── events.py             # SignalGenerated, OrderFilled, RiskChanged...
│   │   ├── ports/                # interfaces: market_data, exchange, llm, news, notifier, repository, clock
│   │   ├── strategies/
│   │   │   ├── base.py           # Strategy interface + registry
│   │   │   ├── indicators.py     # EMA, RSI, ATR, Bollinger, ADX (pandas/numpy)
│   │   │   ├── ema_cross.py
│   │   │   ├── rsi_reversion.py
│   │   │   ├── breakout.py
│   │   │   └── ensemble.py
│   │   └── risk/
│   │       ├── rules.py          # chain-of-responsibility rules
│   │       └── manager.py
│   ├── application/              # LAYER 2 — use cases
│   │   ├── trading_engine.py     # main loop: candle → signal → risk → execute/approve
│   │   ├── news_risk_service.py  # news → LLM → RiskLevel (runs every 15–30 min)
│   │   ├── backtest_runner.py    # replays history through the SAME engine
│   │   ├── approval_service.py   # manual-mode approval queue (Command pattern)
│   │   ├── bot_state.py          # State pattern
│   │   └── event_bus.py
│   ├── infrastructure/           # LAYER 3 — adapters
│   │   ├── exchange/             # ccxt_exchange.py, simulated_exchange.py
│   │   ├── market_data/          # ccxt_market_data.py, csv_market_data.py
│   │   ├── llm/                  # gemini.py, groq.py, claude.py, openai.py, fake.py,
│   │   │                         # decorators.py (retry/cache/rate-limit), fallback.py, factory.py
│   │   ├── news/                 # rss.py, fear_greed.py, reddit.py
│   │   ├── notify/               # telegram.py, console.py
│   │   └── persistence/          # sqlite_repository.py
│   ├── interfaces/               # LAYER 4 — entry points
│   │   ├── api/                  # FastAPI routes + WebSocket for live updates
│   │   ├── web/                  # dashboard HTML/JS (TradingView Lightweight Charts)
│   │   ├── telegram_bot.py       # /status, /stop, Approve/Reject buttons
│   │   └── cli.py                # `tradebot backtest`, `tradebot run`
│   ├── config.py                 # loads config.yaml + .env (pydantic-settings)
│   └── bootstrap.py              # composition root — wires everything
├── tests/
│   ├── unit/                     # strategies, risk rules, with fake adapters
│   └── integration/              # testnet, LLM adapters (optional, needs keys)
├── data/                         # downloaded candles (CSV/Parquet), sqlite db
└── docker/                       # Dockerfile, docker-compose.yml (Phase 5)
```

---

## 6. Tech stack

| Purpose | Library | Why |
|---|---|---|
| Exchange + market data | **ccxt** (async) | Well-maintained, supports Binance testnet ("sandbox mode"), and other exchanges later for free |
| Data / indicators | **pandas**, **numpy** | Standard; we write our own small indicator module to avoid unmaintained libs |
| Config & validation | **pydantic**, **pydantic-settings**, PyYAML | Typed config, catches mistakes on startup |
| LLM SDKs | `google-genai`, `groq`, `anthropic`, `openai` | Each used **only inside its adapter**; install only what you use |
| News | `feedparser`, `httpx` | RSS + simple HTTP |
| Telegram | **python-telegram-bot** | Mature, supports inline Approve/Reject buttons |
| Web dashboard | **FastAPI** + WebSocket + plain HTML/JS + **Lightweight Charts** | Simple, fast, no heavy frontend build |
| Database | **SQLite** via SQLAlchemy | Zero setup; swap to Postgres later via the repository port |
| Scheduling | `asyncio` tasks (APScheduler if needed) | News check every 15–30 min, candle handling on close |
| Testing | **pytest**, pytest-asyncio | |
| Code quality | ruff (lint/format), mypy (types) | Keeps the architecture honest |
| Packaging | uv / pip, Docker (later) | |

---

## 7. Strategies to test first

All strategies implement one method:
`generate_signal(candles: list[Candle], context: MarketContext) -> Signal`

| # | Strategy | Simple idea | Good for |
|---|---|---|---|
| 1 | **EMA Cross + Trend filter** | Buy when the fast average (EMA 9) crosses above the slow average (EMA 21) **and** price is above EMA 200 (long-term uptrend) | Trending markets |
| 2 | **RSI Mean Reversion** | Buy when RSI < 30 (oversold) in an uptrend; sell when RSI > 60 | Sideways markets |
| 3 | **Breakout (Donchian + Volume)** | Buy when price breaks above the highest high of the last 20 candles with above-average volume | Big moves |
| 4 | **Regime Switcher** *(innovative)* | Use ADX to detect "trending" vs "ranging" market, then automatically use #1 or #2 | Adapting to the market |
| 5 | **Ensemble Voting** *(innovative)* | Ask several strategies; trade only when most agree | Fewer but higher-quality trades |
| 6 | **Sentiment-boosted** *(innovative, later)* | Use the LLM news score + Fear & Greed as an extra input (e.g. buy fear in uptrends) | Experiment |

**Backtest report for each strategy** (on 1–2 years of BTC & ETH data, including fees & slippage):
total return, win rate, number of trades, max drawdown, profit factor, and
comparison vs. simply buying and holding. We also test on data the strategy
wasn't tuned on ("out-of-sample") to avoid fooling ourselves.

---

## 8. LLM news risk gate (how the news part works)

1. Every **15–30 minutes**, collect headlines from the last few hours (RSS + Fear & Greed).
2. Remove duplicates, keep only crypto-relevant items for our coins.
3. Send them to the LLM with a strict instruction to return **JSON**:
   ```json
   { "risk_level": "NORMAL | CAUTION | DANGER",
     "sentiment": -1.0,
     "affected_coins": ["BTC"],
     "reason": "Major exchange reported a hack; market-wide selling likely." }
   ```
4. Validate the JSON with pydantic. Invalid answer → retry → still invalid → **CAUTION**.
5. The result is cached and published on the event bus (dashboard + Telegram if it changes).

| Risk level | What the bot does |
|---|---|
| 🟢 NORMAL | Trade normally |
| 🟡 CAUTION | Half position size (or skip if below $5 minimum), tighter stop-loss |
| 🔴 DANGER | **No new buys**; alert you on Telegram; optional: close open position |

**Fail-safe principle:** if news or LLM is unavailable, the bot becomes *more careful*, never less.

**Free-tier plan:** ~48–96 LLM calls/day — well inside free limits of Gemini/Groq
(exact limits verified when we build). The `RateLimitedLLM` and `CachingLLM`
decorators make sure we never exceed them.

---

## 9. Safety & security checklist

- [ ] API keys stored in `.env` only, never in code, never committed
- [ ] Binance API key: **Enable Reading + Spot Trading only. Withdrawals DISABLED.**
- [ ] Restrict API key to your IP address when on VPS
- [ ] Bot reads min order size / step size from Binance before every order
- [ ] Trading budget cap (`$10`) enforced in config, independent of real balance
- [ ] Stop-loss placed for every position
- [ ] Daily loss limit → `Halted` state
- [ ] Kill switch on dashboard and Telegram (`/stop`)
- [ ] Manual-mode approvals **expire** after N minutes (prices move)
- [ ] Live mode requires an explicit config flag **and** a confirmation on startup
- [ ] Every decision logged with reason (signal, risk check, LLM reason)
- [ ] Dashboard is local-only (localhost) until we add a password for VPS

---

## 10. Build phases

| Phase | Goal | Done when... |
|---|---|---|
| **1. Foundation & Backtest** | Project skeleton, domain models, ports, config, download history, strategies 1–3, backtest runner + report | You can run `tradebot backtest --strategy ema_cross` and see a clear report |
| **2. News + LLM gate** | News adapters, LLM adapters (Gemini + Groq), factory, decorators, fallback, risk rules chain | LLM risk level shows in the console every 15 min; swapping provider = 1 config line |
| **3. Testnet paper trading** | ccxt testnet adapter, live candle loop, trading engine, state machine, SQLite logging | Bot trades on testnet in Auto mode for days without crashing |
| **4. Telegram + Dashboard** | Telegram alerts & Approve/Reject, FastAPI dashboard (chart, trades, P&L, risk, settings, kill switch), runtime switching of strategy/timeframe/mode | You can control everything from phone & browser |
| **5. Evaluate (2–4 weeks)** | Run on testnet, compare strategies 4–6, tune | Testnet results are positive after fees, and close to backtest |
| **6. Live with $10** | Switch to live keys, Manual mode first | A few weeks of real results |
| **7. VPS & sharing** | Docker, deploy, setup guide so friends can run their own copy | `docker compose up` works on a clean machine |

> **Go-live rule:** we only move to Phase 6 if Phase 5 shows a positive result after
> fees over a meaningful number of trades (e.g. 30+). If not, we go back and improve
> strategies — that is normal and expected.

---

## 11. Setup steps for you

### 11.1 Binance Spot Testnet (fake money)

1. You need a **GitHub account** (free) — create one at https://github.com if you don't have it.
2. Go to **https://testnet.binance.vision** and click **Log In with GitHub**.
3. Click **Authorize binance-exchange**.
4. Click **Generate HMAC_SHA256 Key**, give it a name (e.g. `tradebot`).
5. Copy the **API Key** and **Secret Key** — the secret is shown **only once**.
6. Keep them for the `.env` file (we'll create it in Phase 1/3).

Notes: the testnet is reset roughly once a month (balances refresh, your API keys stay).
Testnet gives a lot of fake coins — our config caps the bot to **$10** so results are realistic.

### 11.2 Telegram bot

1. In Telegram, search **@BotFather** → send `/newbot` → choose a name and a username ending in `bot`.
2. BotFather gives you a **bot token** — save it.
3. Open a chat with your new bot and send `hi`.
4. We'll get your **chat ID** with a small helper script in Phase 4.

### 11.3 Free LLM API keys (Phase 2)

- **Google Gemini:** https://aistudio.google.com → *Get API key*
- **Groq:** https://console.groq.com → *API Keys*

### 11.4 On your laptop

- Python **3.11 or newer** (`python3 --version`)
- Git (recommended so we can track changes)

---

## 12. Open items / future ideas

- Add more coins and a "coin scanner" once $10 constraint is lifted
- Trailing stop-loss (stop-loss that moves up as price rises)
- Paid LLM (Claude / OpenAI) for comparison of news-analysis quality
- Walk-forward optimisation of strategy parameters
- Password-protected dashboard for VPS
