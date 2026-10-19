"""
Typed slotted dataclasses for global market indices, ticker snapshots, commodities, and cryptocurrencies.
"""

from dataclasses import dataclass, field
from typing import Optional

from src.utils.domain_vocab import DictLikeMixin


@dataclass(slots=True)
class BenchmarkSpec(DictLikeMixin):
    """Static configuration for a tracked global index, commodity future, FX pair, or benchmark."""

    symbol: str
    label: str
    region: str
    asset_class: str
    unit: str = ""


@dataclass(slots=True)
class TickerQuote(DictLikeMixin):
    """Normalized real-time quote, technical range, and fundamental snapshot for any global symbol."""

    symbol: str
    name: str
    exchange: str = ""
    currency: str = "USD"
    market_state: str = "REGULAR"
    price: float = 0.0
    change: float = 0.0
    change_percent: float = 0.0
    day_low: Optional[float] = None
    day_high: Optional[float] = None
    week52_low: Optional[float] = None
    week52_high: Optional[float] = None
    ma50: Optional[float] = None
    ma200: Optional[float] = None
    volume: Optional[int] = None
    avg_volume_3m: Optional[int] = None
    market_cap: Optional[int] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    eps_ttm: Optional[float] = None
    dividend_yield: Optional[float] = None
    earnings_timestamp: Optional[int] = None
    pre_market_price: Optional[float] = None
    pre_market_change_pct: Optional[float] = None
    post_market_price: Optional[float] = None
    post_market_change_pct: Optional[float] = None
    dual_listed_symbol: Optional[str] = None
    dual_listed_price: Optional[float] = None
    dual_listed_currency: Optional[str] = None
    headlines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CryptoAssetQuote(DictLikeMixin):
    """Normalized cryptocurrency market quote with USD/CAD pricing and 24h/7d momentum."""

    symbol: str
    name: str
    price_usd: float
    price_cad: Optional[float] = None
    change_24h_pct: float = 0.0
    change_7d_pct: Optional[float] = None
    market_cap_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    rank: Optional[int] = None


@dataclass(slots=True)
class HistoricalSummary(DictLikeMixin):
    """Multi-horizon return strip and deep historical window statistics for a ticker or benchmark."""

    symbol: str
    currency: str = "USD"
    timeframe_label: str = "1-Year (1Y)"
    start_date: str = ""
    end_date: str = ""
    start_price: float = 0.0
    end_price: float = 0.0
    abs_change: float = 0.0
    pct_change: float = 0.0
    cagr_pct: Optional[float] = None
    period_high: float = 0.0
    period_high_date: str = ""
    period_low: float = 0.0
    period_low_date: str = ""
    max_drawdown_pct: float = 0.0
    annualized_volatility_pct: Optional[float] = None
    horizon_returns: dict[str, float] = field(default_factory=dict)
    checkpoints: list[str] = field(default_factory=list)


