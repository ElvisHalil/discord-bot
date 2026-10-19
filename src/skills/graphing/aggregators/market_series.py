"""
Self-contained Yahoo Finance v8 time-series fetcher for `src/skills/graphing/` (`src/skills/graphing/aggregators/market_series.py`).

Enables `skill_graphing_info` to render single-ticker OHLCV/MA charts and multi-ticker normalized return
comparison charts across any timeframe (1D, 5D, 1M, 3M, 6M, YTD, 1Y, 2Y, 5Y, 10Y, MAX) without
cross-importing `src.skills.markets`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import time
from typing import Dict, Optional, Tuple
from urllib.parse import quote_plus

from src.skills.graphing.aggregators.base import BaseGraphingAggregator
from src.skills.graphing.catalogs import CHART_TICKER_ALIASES, resolve_fx_pair_symbol
from src.skills.graphing.models import MarketBarSeries
from src.utils.cache import BoundedCache
from src.utils.http import DEFAULT_UA, ThreadLocalRequestsSession

_HTTP_SESSION = ThreadLocalRequestsSession({"User-Agent": DEFAULT_UA})
_SERIES_CACHE: BoundedCache = BoundedCache(maxsize=128)
_SERIES_TTL = 180.0

_TIMEFRAME_SPECS: Dict[str, Tuple[str, str, str]] = {
    "1d": ("1d", "5m", "Intraday (1D)"),
    "5d": ("5d", "15m", "1-Week (5D)"),
    "1w": ("5d", "15m", "1-Week (5D)"),
    "1mo": ("1mo", "1d", "1-Month (1M)"),
    "1m": ("1mo", "1d", "1-Month (1M)"),
    "3mo": ("3mo", "1d", "3-Month (3M)"),
    "3m": ("3mo", "1d", "3-Month (3M)"),
    "6mo": ("6mo", "1d", "6-Month (6M)"),
    "6m": ("6mo", "1d", "6-Month (6M)"),
    "ytd": ("ytd", "1d", "Year-to-Date (YTD)"),
    "1y": ("1y", "1d", "1-Year (1Y)"),
    "2y": ("2y", "1d", "2-Year (2Y)"),
    "3y": ("5y", "1wk", "3-Year (3Y)"),
    "5y": ("5y", "1wk", "5-Year (5Y)"),
    "10y": ("10y", "1mo", "10-Year (10Y)"),
    "max": ("max", "1mo", "All-Time (MAX)"),
}


def _search_yahoo_symbol(query_text: str) -> Optional[str]:
    """Query Yahoo Finance v1 search to resolve an unmapped company name, ticker, or FX pair."""
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={quote_plus(query_text)}&quotesCount=5&newsCount=0"
        resp = _HTTP_SESSION.get(url, timeout=5)
        if resp.status_code == 200:
            quotes = (resp.json() or {}).get("quotes") or []
            for q in quotes:
                sym = q.get("symbol")
                if sym:
                    return str(sym).upper()
    except Exception:
        pass
    return None


def resolve_chart_ticker(raw_target: str) -> str:
    """Resolve a company/asset alias, FX pair, or cashtag into a canonical Yahoo Finance symbol."""
    cleaned = (raw_target or "").strip().lstrip("$")
    if not cleaned:
        return ""
    fx_sym = resolve_fx_pair_symbol(cleaned)
    if fx_sym:
        return fx_sym
    low = cleaned.lower()
    if low in CHART_TICKER_ALIASES:
        return CHART_TICKER_ALIASES[low]
    if re.fullmatch(r"[A-Za-z0-9.^=\-]+", cleaned) and len(cleaned) <= 10:
        return cleaned.upper()
    searched = _search_yahoo_symbol(cleaned)
    if searched:
        return searched
    return cleaned.upper()


class MarketSeriesAggregator(BaseGraphingAggregator):
    """Fetches historical OHLCV bars from Yahoo Finance v8 chart API for chart rendering."""

    name = "market_series"

    def fetch_bar_series(self, target: str, timeframe: str = "1y") -> Optional[MarketBarSeries]:
        sym = resolve_chart_ticker(target)
        if not sym:
            return None

        tf_norm = (timeframe or "1y").strip().lower()
        yf_range, yf_interval, tf_label = _TIMEFRAME_SPECS.get(tf_norm, ("1y", "1d", "1-Year (1Y)"))
        if re.fullmatch(r"20\d{2}", tf_norm):
            yf_range, yf_interval, tf_label = ("10y", "1d", f"Calendar Year {tf_norm}")

        cache_key = f"{sym}::{tf_norm}"
        now = time.monotonic()
        cached = _SERIES_CACHE.get(cache_key)
        if cached is not None:
            ts_cached, series_obj = cached
            if now - ts_cached < _SERIES_TTL:
                return series_obj

        try:
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(sym)}"
                f"?range={yf_range}&interval={yf_interval}&includePrePost=false"
            )
            resp = _HTTP_SESSION.get(url, timeout=7)
            results = ((resp.json() or {}).get("chart") or {}).get("result") if resp.status_code == 200 else None
            if not results:
                alt_sym = _search_yahoo_symbol(target)
                if alt_sym and alt_sym != sym:
                    sym = alt_sym
                    url = (
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(sym)}"
                        f"?range={yf_range}&interval={yf_interval}&includePrePost=false"
                    )
                    resp = _HTTP_SESSION.get(url, timeout=7)
                    results = ((resp.json() or {}).get("chart") or {}).get("result") if resp.status_code == 200 else None
            if not results:
                return None
            res0 = results[0]
            meta = res0.get("meta") or {}
            raw_ts = res0.get("timestamp") or []
            quote0 = ((res0.get("indicators") or {}).get("quote") or [{}])[0]
            raw_open = quote0.get("open") or []
            raw_high = quote0.get("high") or []
            raw_low = quote0.get("low") or []
            raw_close = quote0.get("close") or []
            raw_vol = quote0.get("volume") or []

            timestamps, opens, highs, lows, closes, volumes = [], [], [], [], [], []
            for i, ts in enumerate(raw_ts):
                c = raw_close[i] if i < len(raw_close) else None
                if ts is None or c is None:
                    continue
                c_f = float(c)
                o_f = float(raw_open[i]) if (i < len(raw_open) and raw_open[i] is not None) else c_f
                h_f = float(raw_high[i]) if (i < len(raw_high) and raw_high[i] is not None) else c_f
                l_f = float(raw_low[i]) if (i < len(raw_low) and raw_low[i] is not None) else c_f
                v_f = float(raw_vol[i]) if (i < len(raw_vol) and raw_vol[i] is not None) else 0.0
                timestamps.append(int(ts))
                opens.append(o_f)
                highs.append(h_f)
                lows.append(l_f)
                closes.append(c_f)
                volumes.append(v_f)

            # Filter for specific calendar year or 3Y window if requested
            if re.fullmatch(r"20\d{2}", tf_norm):
                yr = int(tf_norm)
                start_ts = int(datetime(yr, 1, 1, tzinfo=timezone.utc).timestamp())
                end_ts = int(datetime(yr, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
                idxs = [i for i, t in enumerate(timestamps) if start_ts <= t <= end_ts]
                if len(idxs) >= 2:
                    timestamps = [timestamps[i] for i in idxs]
                    opens = [opens[i] for i in idxs]
                    highs = [highs[i] for i in idxs]
                    lows = [lows[i] for i in idxs]
                    closes = [closes[i] for i in idxs]
                    volumes = [volumes[i] for i in idxs]
            elif tf_norm == "3y" and timestamps:
                cutoff = timestamps[-1] - (3 * 365 * 86400)
                idxs = [i for i, t in enumerate(timestamps) if t >= cutoff]
                if len(idxs) >= 2:
                    timestamps = [timestamps[i] for i in idxs]
                    opens = [opens[i] for i in idxs]
                    highs = [highs[i] for i in idxs]
                    lows = [lows[i] for i in idxs]
                    closes = [closes[i] for i in idxs]
                    volumes = [volumes[i] for i in idxs]

            if len(closes) < 2:
                return None

            pct_chg = ((closes[-1] - closes[0]) / closes[0]) * 100.0 if closes[0] > 0 else 0.0
            series_obj = MarketBarSeries(
                symbol=str(meta.get("symbol") or sym).upper(),
                name=str(meta.get("longName") or meta.get("shortName") or sym),
                currency=str(meta.get("currency") or "USD").upper(),
                timeframe_label=tf_label,
                timestamps=timestamps,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                volumes=volumes,
                pct_change=round(pct_chg, 2),
                period_high=max(highs),
                period_low=min(lows),
            )
            _SERIES_CACHE[cache_key] = (now, series_obj)
            return series_obj
        except Exception:
            return None

