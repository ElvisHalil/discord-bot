"""
Typed StrEnums for the global markets, commodities, and crypto skill package.
"""

from enum import StrEnum


class MarketView(StrEnum):
    OVERVIEW = "overview"
    PULSE = "pulse"
    TICKER = "ticker"
    HISTORY = "history"
    COMPARE = "compare"
    MOVERS = "movers"
    COMMODITIES = "commodities"
    CRYPTO = "crypto"


class MarketRegion(StrEnum):
    GLOBAL = "global"
    CANADA = "canada"
    US = "us"
    EUROPE = "europe"
    ASIA = "asia"


class AssetClass(StrEnum):
    INDEX = "index"
    EQUITY = "equity"
    COMMODITY = "commodity"
    CRYPTO = "crypto"
    FOREX = "forex"
    BOND = "bond"


class HistoricalTimeframe(StrEnum):
    ONE_DAY = "1d"
    ONE_WEEK = "5d"
    ONE_MONTH = "1mo"
    THREE_MONTHS = "3mo"
    SIX_MONTHS = "6mo"
    YTD = "ytd"
    ONE_YEAR = "1y"
    TWO_YEARS = "2y"
    THREE_YEARS = "3y"
    FIVE_YEARS = "5y"
    TEN_YEARS = "10y"
    MAX = "max"


