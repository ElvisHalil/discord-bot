"""
Typed StrEnums for the global markets, commodities, and crypto skill package.
"""

from enum import StrEnum


class MarketView(StrEnum):
    OVERVIEW = "overview"
    PULSE = "pulse"
    TICKER = "ticker"
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

