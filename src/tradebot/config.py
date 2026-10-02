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
    mode: Literal["backtest", "testnet", "live"] = "backtest"
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
    exit_mode: Literal["atr", "percent"] = "atr"
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


class DataConfig(BaseModel):
    dir: Path = Path("data")

    def resolved(self) -> Path:
        return self.dir if self.dir.is_absolute() else ROOT / self.dir


class AppConfig(BaseModel):
    exchange: ExchangeConfig = ExchangeConfig()
    market: MarketConfig = MarketConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    backtest: BacktestConfig = BacktestConfig()
    data: DataConfig = DataConfig()


class Secrets(BaseSettings):
    """API keys, read from environment variables or the `.env` file. Never put these in YAML."""

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    binance_testnet_api_key: SecretStr | None = None
    binance_testnet_api_secret: SecretStr | None = None
    binance_api_key: SecretStr | None = None
    binance_api_secret: SecretStr | None = None


def load_config(path: Path = DEFAULT_CONFIG) -> AppConfig:
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    return AppConfig.model_validate(raw or {})


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)
