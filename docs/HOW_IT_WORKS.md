# How the bot works (beginner guide)

## 1. The big picture

Every time a candle closes (for example every hour on the `1h` timeframe), the bot runs these steps:

```
new candle ─► strategy looks at indicators ─► BUY / SELL / HOLD
                                                  │
                         BUY ─► risk manager checks it ─► buy + set stop-loss & take-profit
                         SELL ─► sell what we hold (exits are never blocked)
```

Meanwhile, the **stop-loss** and **take-profit** orders wait on the exchange. If the price
touches one of them, that order sells the coin automatically, even between candles.

## 2. Candles and indicators

A **candle** summarises a time period with 5 numbers: open, high, low, close and volume.

**Indicators** are calculations on past candles:

| Indicator | What it tells you |
|---|---|
| **EMA** (Exponential Moving Average) | The "average price lately". Price above a long EMA usually means an uptrend |
| **RSI** (0–100) | How fast price moved recently. Below 30 means it fell a lot (possible bounce); above 70 means it rose a lot |
| **Donchian channel** | The highest high and lowest low of the last N candles. Breaking above the high can start a move |
| **ADX** (0–100) | How *strong* a trend is (not its direction). Above about 25 means trending; below that means sideways |

## 3. The strategies

| Name | Idea in one line |
|---|---|
| `ema_cross` | Buy when the short-term average crosses above the medium-term one in an uptrend |
| `rsi_reversion` | Buy a dip that starts recovering in an uptrend; sell when RSI gets high |
| `breakout` | Buy when price breaks the recent high with strong volume |
| `regime_switch` | Uses ADX to pick `ema_cross` in trends or `rsi_reversion` in sideways markets |
| `ensemble` | Asks several strategies and only acts when at least 2 agree |

## 4. Safety rules (risk manager)

Every BUY idea goes through these checks, in this order. Any of them can say no:

1. **Daily loss limit:** lost 5% today? Then no more buying until tomorrow (UTC).
2. **Max open positions:** already holding 1 position? Then wait.
3. **Position size:** spend 95% of what's left of the $10 budget. The other 5% covers fees.
4. **Exit levels:** stop-loss and take-profit based on **ATR** (the typical price move per
   candle). By default the stop is 2.5 × ATR below the buy price and the target is 6.25 × ATR
   above it. In calm markets that's a tight stop; in wild markets a wider one, so normal noise
   doesn't knock you out. The stop always stays between 1% and 10%.
5. **News risk** (Phase 2): DANGER blocks the trade; CAUTION halves the size and tightens the stop.
6. **Exchange minimum:** Binance refuses orders under about $5.

Note: with $10, a CAUTION trade (half size, about $4.75) is below Binance's $5 minimum, so
it is skipped. That's intended: when in doubt, don't trade.

## 5. How to read a backtest report

| Number | Meaning | Good sign |
|---|---|---|
| Total return | Profit or loss over the whole period | Positive, and better than buy & hold |
| Buy & hold | What you'd get by just buying on day 1 and waiting | The benchmark to beat |
| Trades | How many round trips (buy then sell) | 30+ before trusting the result |
| Win rate | % of trades that made money | Can be low (30–40%) if wins are bigger than losses |
| Profit factor | Money won ÷ money lost | **Above 1.0** means profitable; 1.3+ is promising |
| Max drawdown | Worst drop from a high point | Smaller is better. Could you handle seeing it live? |
| Fees paid | Total Binance fees | Many small trades means fees eat the profit |

## 6. Honest testing: `tradebot optimize` (walk-forward)

A normal backtest uses the same data to choose settings AND to judge them. That's like
marking your own exam with the answer sheet. `optimize` does it properly:

```
|---- train 1 year ----|-- test 3 months --|
     |---- train 1 year ----|-- test 3 months --|   ...repeated
```

For each step it tries every setting on the training year, picks the best one, and then
scores it **only on the next 3 months it has never seen**. Only those test results count.

How to read it:
- **test return / profit factor:** performance on unseen data. This is the honest number.
- **steps won:** in how many 3-month periods it made money. Consistency matters.
- **train vs TEST:** training numbers are always better. A big gap means the settings were
  fitted to the past.
- **Stronger proof:** run it on coins that were never used for tuning
  (`--symbols SOL/USDT,BNB/USDT,XRP/USDT`) and on a longer history (`--years 5`).
  A real edge survives both.

What we found (2026-10-02): only `breakout` was profitable in all three tests (profit factor
about 1.2–1.3). `regime_switch` looked best on BTC/ETH over 2 years (+43%) but lost 62% on other
coins, so it was luck. Even `breakout` trails buy & hold in strong bull markets. Its value is
losing much less in crashes.

**Common traps:**
- **Overfitting:** tweaking settings until the past looks perfect. The future won't match.
  Always check results on a *different* period (e.g. `--days 730` vs `--days 365`).
- **Too few trades:** 10 lucky trades prove nothing.
- **One market type only:** a strategy that only worked in a bull market will fail in a bear market.

## 7. How the code is organised (Clean Architecture)

```
src/tradebot/
  domain/          the rules of trading. No Binance, no internet, no database
    models.py        Candle, Signal, Position, Trade...
    ports/           CONTRACTS that outside services must follow (interfaces)
    strategies/      trading strategies (one file each)
    risk/            safety rules (one class each)
  application/     use cases that coordinate the domain
    trading_engine.py   the main loop. Used by backtest AND live trading
    backtest.py         replays history through the engine
  infrastructure/  ADAPTERS that connect to the real world
    exchange/simulated.py           fake exchange for backtests
    market_data/ccxt_market_data.py Binance price data
    market_data/cached.py           saves downloaded data to disk
  interfaces/      how you talk to the bot (CLI now; dashboard + Telegram later)
  bootstrap.py     the ONE place that plugs adapters into ports
```

**The golden rule:** inner layers never import outer layers. The trading engine doesn't know
whether it's talking to the simulator or the real Binance; both follow the same `ExchangePort`
contract. That's why the backtest runs the exact same code that will trade live.

### Design patterns used so far

| Pattern | Where | Plain-English reason |
|---|---|---|
| Strategy | `domain/strategies/*` | Swap trading logic like changing a game cartridge |
| Factory + Registry | `StrategyFactory`, `@register(...)` | Build a strategy from its name in the config |
| Composite | `regime_switch`, `ensemble` | Strategies made of other strategies |
| Chain of Responsibility | `domain/risk/rules.py` | Each safety rule is a small, independent check in a line |
| Adapter | `infrastructure/*` | Wraps Binance/ccxt behind our own interface |
| Decorator | `CachedMarketData` | Adds disk caching to any market data source without changing it |
| Observer | `EventBus` + `domain/events.py` | The report (and later the dashboard/Telegram) listens to what the engine does |
| Dependency Injection | `bootstrap.py` | Objects receive what they need instead of creating it themselves |
| Strategy (again) | `PercentExitLevels` / `AtrExitLevels` | Two interchangeable ways to place stops, chosen in config |
| Protocol + injection | `BacktestBatch` → `ParallelBacktestBatch` | The optimizer doesn't know backtests run on 3 CPU cores; tests give it a fake |

## 8. Adding your own strategy

1. Create `src/tradebot/domain/strategies/my_idea.py`:

```python
from tradebot.domain.models import Action, Signal
from tradebot.domain.strategies.base import Strategy, StrategyContext, register
from tradebot.domain.strategies.indicators import ema


@register("my_idea")
class MyIdea(Strategy):
    description = "Buy when price is above EMA 50, sell when below."

    def __init__(self, period: int = 50):
        self.period = period

    @property
    def warmup(self):
        return self.period

    def indicators(self, df):
        out = df.copy()
        out["ema"] = ema(out["close"], self.period)
        return out

    def signal(self, history, ctx: StrategyContext):
        # history["column"][-1] = value on the latest candle, [-2] = the candle before
        if history["close"][-1] > history["ema"][-1]:
            return Signal(Action.BUY, "price above EMA")
        return Signal(Action.SELL, "price below EMA")
```

2. Add it to the import line in `src/tradebot/domain/strategies/__init__.py`.
3. Optionally add `param_grid = {"period": [20, 50, 100]}` to the class so `optimize` can tune it.
4. Run `uv run tradebot backtest -s my_idea`, then `uv run tradebot optimize -s my_idea`.

The test `test_signal_identical_with_and_without_future_data` automatically checks that
your strategy doesn't cheat by looking at future prices.
