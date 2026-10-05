"""Settings: `config/config.yaml` for behaviour, `.env` for secrets.

Pydantic checks every value on startup, so a typo fails fast with a clear message
instead of causing strange behaviour while trading.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "config.yaml"


class ExchangeConfig(BaseModel):
    name: str = "binance"
    mode: Literal["backtest", "paper", "testnet", "live"] = "backtest"
    quote_asset: str = "USDT"


class MarketConfig(BaseModel):
    symbols: list[str] = ["BTC/USDT", "ETH/USDT"]
    timeframe: str = "4h"


class StrategyConfig(BaseModel):
    name: str = "ema_cross"
    params: dict[str, Any] = Field(default_factory=dict)


class RiskConfig(BaseModel):
    budget_usdt: float = Field(10, gt=0)
    position_size_pct: float = Field(95, gt=0, le=100)
    exit_mode: Literal["atr", "percent", "stop_only"] = "atr"  # stop_only: no take-profit
    split_budget: bool = False  # True: budget / max_open_positions per position
    # ATR mode: distances measured in "typical candle moves"
    stop_atr_mult: float = Field(2.0, gt=0)
    take_profit_atr_mult: float = Field(4.0, gt=0)
    min_stop_pct: float = Field(1.0, gt=0)
    max_stop_pct: float = Field(10.0, gt=0, lt=100)
    # percent mode (also the fallback while ATR is warming up)
    stop_loss_pct: float = Field(5, gt=0, lt=100)
    take_profit_pct: float = Field(10, gt=0)
    daily_loss_limit_pct: float = Field(5, gt=0, le=100)
    max_open_positions: int = Field(1, ge=1)
    caution_size_mult: float = Field(0.5, gt=0, le=1)
    caution_stop_mult: float = Field(0.75, gt=0, le=1)


class BacktestConfig(BaseModel):
    days: int = Field(365, gt=0)
    start: date | None = None  # overrides `days` when set
    end: date | None = None  # default: now
    starting_balance: float = Field(10, gt=0)
    fee_pct: float = Field(0.1, ge=0)
    slippage_pct: float = Field(0.05, ge=0)

    def period(self) -> tuple[datetime, datetime]:
        end = _utc(self.end) if self.end else datetime.now(UTC)
        start = _utc(self.start) if self.start else end - timedelta(days=self.days)
        return start, end


class NewsConfig(BaseModel):
    enabled: bool = True
    refresh_minutes: float = Field(20, ge=5)
    lookback_hours: float = Field(12, gt=0)
    max_headlines: int = Field(40, ge=5, le=100)
    stale_after_minutes: float = Field(90, gt=0)
    fail_safe_level: Literal["NORMAL", "CAUTION", "DANGER"] = "CAUTION"
    calm_down_checks: int = Field(3, ge=1)  # calmer readings in a row needed to lower the level
    fear_greed: bool = True
    rss_feeds: dict[str, str] = Field(
        default_factory=lambda: {
            "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "cointelegraph": "https://cointelegraph.com/rss",
            "decrypt": "https://decrypt.co/feed",
        }
    )


class LLMConfig(BaseModel):
    providers: list[str] = ["groq", "gemini"]  # order = fallback order
    models: dict[str, str] = Field(
        default_factory=lambda: {
            "gemini": "gemini-3.5-flash",
            "groq": "openai/gpt-oss-120b",
            "openai": "gpt-5-mini",
            "openrouter": "openai/gpt-oss-120b",
        }
    )
    timeout_seconds: float = Field(60, gt=0)
    min_interval_seconds: float = Field(5, ge=0)
    cache_minutes: float = Field(10, ge=0)


class TradingConfig(BaseModel):
    auto_start: bool = True  # false = wait until you press Start (dashboard / Telegram /trade)
    loop: bool = True  # after a trade finishes, look for the next one (false = one trade)
    explain: bool = False  # LLM explains analyses and trades in simple words
    manual_approval: bool = False  # true = every buy waits for your OK (Telegram/dashboard)
    approval_timeout_minutes: float = Field(15, gt=0)


class DashboardConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"  # localhost only — not reachable from other devices
    port: int = 8000


class TelegramConfig(BaseModel):
    commands: bool = True  # only ONE bot per Telegram token may receive commands
    label: str = ""  # prefix for this bot's messages, e.g. "TREND"


class DataConfig(BaseModel):
    dir: Path = Path("data")
    database: str | None = None  # default: tradebot_<mode>.sqlite

    def resolved(self) -> Path:
        return self.dir if self.dir.is_absolute() else ROOT / self.dir


class AppConfig(BaseModel):
    exchange: ExchangeConfig = ExchangeConfig()
    market: MarketConfig = MarketConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    backtest: BacktestConfig = BacktestConfig()
    news: NewsConfig = NewsConfig()
    llm: LLMConfig = LLMConfig()
    trading: TradingConfig = TradingConfig()
    telegram: TelegramConfig = TelegramConfig()
    dashboard: DashboardConfig = DashboardConfig()
    data: DataConfig = DataConfig()


class Secrets(BaseSettings):
    """API keys, read from environment variables or the `.env` file. Never put these in YAML."""

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    binance_testnet_api_key: SecretStr | None = None
    binance_testnet_api_secret: SecretStr | None = None
    binance_api_key: SecretStr | None = None
    binance_api_secret: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    def llm_key(self, provider: str) -> str | None:
        """API key for an LLM provider, e.g. 'gemini' -> GEMINI_API_KEY (None if missing)."""
        value = getattr(self, f"{provider}_api_key", None)
        secret = value.get_secret_value().strip() if value else ""
        return secret or None


def load_config(path: Path = DEFAULT_CONFIG) -> AppConfig:
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    return AppConfig.model_validate(raw or {})


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)
