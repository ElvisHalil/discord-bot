"""
CoinGecko v3 REST API + Yahoo Finance fallback aggregator for cryptocurrencies,
USD/CAD pricing, 24h & 7d momentum, and top 24h crypto gainers/losers.
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

from curl_cffi import requests as creq

from src.skills.markets.aggregators.base import BaseMarketAggregator
from src.skills.markets.catalogs import CRYPTO_BENCHMARKS
from src.skills.markets.models import CryptoAssetQuote

logger = logging.getLogger(__name__)

_CRYPTO_CACHE: Optional[Tuple[float, List[CryptoAssetQuote]]] = None
_CRYPTO_CACHE_TTL: float = 60.0

COINGECKO_MARKETS_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page=40&page=1"
    "&sparkline=false&price_change_percentage=24h,7d"
)

_STABLECOINS = frozenset({"USDT", "USDC", "DAI", "FDUSD", "USDE", "USDS", "PYUSD", "TUSD"})


def clear_crypto_market_cache() -> None:
    """Clear in-memory cryptocurrency market cache."""
    global _CRYPTO_CACHE
    _CRYPTO_CACHE = None


class CryptoAggregator(BaseMarketAggregator):
    """Fetches top cryptocurrencies by market cap plus 24h gainers & losers via CoinGecko v3."""

    def fetch_top_coins(self, usdcad_rate: float = 1.41, ttl: float = _CRYPTO_CACHE_TTL) -> List[CryptoAssetQuote]:
        """Return top cryptocurrencies with USD and CAD prices, 24h/7d % changes, and market caps."""
        global _CRYPTO_CACHE
        now = time.time()
        if _CRYPTO_CACHE and (now - _CRYPTO_CACHE[0]) < ttl:
            return _CRYPTO_CACHE[1]

        coins: List[CryptoAssetQuote] = []
        try:
            resp = creq.get(COINGECKO_MARKETS_URL, impersonate="chrome", timeout=6)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        sym = str(item.get("symbol") or "").upper()
                        if not sym:
                            continue
                        price_usd = float(item.get("current_price") or 0.0)
                        chg_24h = item.get("price_change_percentage_24h_in_currency")
                        if chg_24h is None:
                            chg_24h = item.get("price_change_percentage_24h") or 0.0
                        chg_7d = item.get("price_change_percentage_7d_in_currency")
                        coins.append(
                            CryptoAssetQuote(
                                symbol=sym,
                                name=str(item.get("name") or sym),
                                price_usd=price_usd,
                                price_cad=round(price_usd * usdcad_rate, 2) if usdcad_rate > 0 else None,
                                change_24h_pct=float(chg_24h or 0.0),
                                change_7d_pct=float(chg_7d) if chg_7d is not None else None,
                                market_cap_usd=float(item["market_cap"]) if item.get("market_cap") is not None else None,
                                volume_24h_usd=float(item["total_volume"]) if item.get("total_volume") is not None else None,
                                rank=int(item["market_cap_rank"]) if item.get("market_cap_rank") is not None else None,
                            )
                        )
        except Exception as e:
            logger.debug("CoinGecko markets fetch failed: %s", e)

        if coins:
            _CRYPTO_CACHE = (now, coins)
            return coins

        # Fallback to Yahoo Finance crypto symbols if CoinGecko is unreachable
        from src.skills.markets.aggregators.yahoo import YahooFinanceAggregator

        yf = YahooFinanceAggregator()
        yf_quotes = yf.fetch_quotes([b.symbol for b in CRYPTO_BENCHMARKS])
        for idx, b in enumerate(CRYPTO_BENCHMARKS, start=1):
            q = yf_quotes.get(b.symbol.upper())
            if q and q.price > 0:
                sym_clean = b.symbol.split("-")[0]
                coins.append(
                    CryptoAssetQuote(
                        symbol=sym_clean,
                        name=b.label,
                        price_usd=q.price,
                        price_cad=round(q.price * usdcad_rate, 2) if usdcad_rate > 0 else None,
                        change_24h_pct=q.change_percent,
                        market_cap_usd=float(q.market_cap) if q.market_cap else None,
                        volume_24h_usd=float(q.volume) if q.volume else None,
                        rank=idx,
                    )
                )
        return coins

    def get_crypto_movers(
        self,
        coins: List[CryptoAssetQuote],
        count: int = 3,
    ) -> Dict[str, List[CryptoAssetQuote]]:
        """Filter out stablecoins and return top 24h gainers and losers among top market-cap coins."""
        non_stable = [c for c in coins if c.symbol.upper() not in _STABLECOINS]
        sorted_desc = sorted(non_stable, key=lambda c: c.change_24h_pct, reverse=True)
        sorted_asc = sorted(non_stable, key=lambda c: c.change_24h_pct)
        return {
            "gainers": sorted_desc[:count],
            "losers": sorted_asc[:count],
        }

