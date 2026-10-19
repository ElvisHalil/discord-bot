"""
Yahoo Finance JSON API aggregator for global indices, single-ticker snapshots,
commodities, FX, US/Canadian market movers, and ticker-specific catalyst news.
"""

from datetime import datetime, timezone
import logging
import math
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from curl_cffi import requests as creq

from src.skills.markets.aggregators.base import BaseMarketAggregator
from src.skills.markets.catalogs import (
    CANADIAN_DUAL_LISTED_MAP,
    COMMON_TICKER_ALIASES,
    TSX_60_LEADERS,
)
from src.skills.markets.models import HistoricalSummary, TickerQuote
from src.utils.workers import map_in_workers

logger = logging.getLogger(__name__)

_SESSION: Optional[creq.Session] = None
_CRUMB: Optional[str] = None
_CRUMB_TS: float = 0.0
_CRUMB_TTL: float = 900.0  # 15 minutes

_QUOTE_CACHE: Dict[str, Tuple[float, TickerQuote]] = {}
_QUOTE_CACHE_TTL: float = 45.0

_SCREENER_CACHE: Dict[str, Tuple[float, List[TickerQuote]]] = {}
_SCREENER_CACHE_TTL: float = 90.0

_NEWS_CACHE: Dict[str, Tuple[float, List[str]]] = {}
_NEWS_CACHE_TTL: float = 180.0

_CHART_SERIES_CACHE: Dict[str, Tuple[float, str, List[Tuple[int, float, float, float, float]]]] = {}
_CHART_SERIES_TTL: float = 300.0


def clear_yahoo_market_cache() -> None:
    """Clear in-memory Yahoo Finance quote, screener, chart, and news caches."""
    global _CRUMB, _CRUMB_TS
    _QUOTE_CACHE.clear()
    _SCREENER_CACHE.clear()
    _NEWS_CACHE.clear()
    _CHART_SERIES_CACHE.clear()
    _CRUMB = None
    _CRUMB_TS = 0.0


def _get_session_and_crumb(force_refresh: bool = False) -> Tuple[creq.Session, str]:
    """Return a Chrome-impersonated curl_cffi session and a valid Yahoo Finance crumb."""
    global _SESSION, _CRUMB, _CRUMB_TS
    now = time.time()
    if _SESSION is None:
        _SESSION = creq.Session(impersonate="chrome")
    if not force_refresh and _CRUMB and (now - _CRUMB_TS) < _CRUMB_TTL:
        return _SESSION, _CRUMB

    try:
        _SESSION.get("https://fc.yahoo.com", timeout=5)
        resp = _SESSION.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=5)
        if resp.status_code == 200 and resp.text and "<" not in resp.text:
            _CRUMB = resp.text.strip()
            _CRUMB_TS = now
    except Exception as e:
        logger.debug("Yahoo crumb fetch failed: %s", e)

    return _SESSION, (_CRUMB or "")


def _parse_quote_dict(q: dict) -> TickerQuote:
    """Convert a raw Yahoo Finance v7 quote object into a typed TickerQuote dataclass."""
    symbol = str(q.get("symbol") or "").strip()
    name = str(q.get("longName") or q.get("shortName") or symbol).strip()
    div_yield = q.get("dividendYield")
    if div_yield is None and q.get("trailingAnnualDividendYield") is not None:
        div_yield = float(q["trailingAnnualDividendYield"]) * 100.0

    return TickerQuote(
        symbol=symbol,
        name=name,
        exchange=str(q.get("fullExchangeName") or q.get("exchange") or "").strip(),
        currency=str(q.get("currency") or "USD").strip(),
        market_state=str(q.get("marketState") or "REGULAR").strip(),
        price=float(q.get("regularMarketPrice") or 0.0),
        change=float(q.get("regularMarketChange") or 0.0),
        change_percent=float(q.get("regularMarketChangePercent") or 0.0),
        day_low=float(q["regularMarketDayLow"]) if q.get("regularMarketDayLow") is not None else None,
        day_high=float(q["regularMarketDayHigh"]) if q.get("regularMarketDayHigh") is not None else None,
        week52_low=float(q["fiftyTwoWeekLow"]) if q.get("fiftyTwoWeekLow") is not None else None,
        week52_high=float(q["fiftyTwoWeekHigh"]) if q.get("fiftyTwoWeekHigh") is not None else None,
        ma50=float(q["fiftyDayAverage"]) if q.get("fiftyDayAverage") is not None else None,
        ma200=float(q["twoHundredDayAverage"]) if q.get("twoHundredDayAverage") is not None else None,
        volume=int(q["regularMarketVolume"]) if q.get("regularMarketVolume") is not None else None,
        avg_volume_3m=int(q["averageDailyVolume3Month"]) if q.get("averageDailyVolume3Month") is not None else None,
        market_cap=int(q["marketCap"]) if q.get("marketCap") is not None else None,
        trailing_pe=float(q["trailingPE"]) if q.get("trailingPE") is not None else None,
        forward_pe=float(q["forwardPE"]) if q.get("forwardPE") is not None else None,
        eps_ttm=float(q["epsTrailingTwelveMonths"]) if q.get("epsTrailingTwelveMonths") is not None else None,
        dividend_yield=float(div_yield) if div_yield is not None else None,
        earnings_timestamp=int(q["earningsTimestamp"]) if q.get("earningsTimestamp") is not None else None,
        pre_market_price=float(q["preMarketPrice"]) if q.get("preMarketPrice") is not None else None,
        pre_market_change_pct=float(q["preMarketChangePercent"]) if q.get("preMarketChangePercent") is not None else None,
        post_market_price=float(q["postMarketPrice"]) if q.get("postMarketPrice") is not None else None,
        post_market_change_pct=float(q["postMarketChangePercent"]) if q.get("postMarketChangePercent") is not None else None,
    )


class YahooFinanceAggregator(BaseMarketAggregator):
    """High-speed Yahoo Finance v7/v8/v1 client for global equities, indices, commodities, and news."""

    def fetch_quotes(self, symbols: List[str], ttl: float = _QUOTE_CACHE_TTL) -> Dict[str, TickerQuote]:
        """Batch-fetch quotes for up to 40 symbols in a single HTTP request, with v8 chart fallback."""
        now = time.time()
        results: Dict[str, TickerQuote] = {}
        missing: List[str] = []

        for sym in symbols:
            clean_sym = sym.strip().upper()
            if not clean_sym:
                continue
            cached = _QUOTE_CACHE.get(clean_sym)
            if cached and (now - cached[0]) < ttl:
                results[clean_sym] = cached[1]
            else:
                missing.append(clean_sym)

        if not missing:
            return results

        session, crumb = _get_session_and_crumb()
        encoded_syms = ",".join(quote_plus(s) for s in missing)
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={encoded_syms}"
        if crumb:
            url += f"&crumb={quote_plus(crumb)}"

        fetched_any = False
        try:
            resp = session.get(url, timeout=6)
            if resp.status_code in (401, 403):
                session, crumb = _get_session_and_crumb(force_refresh=True)
                url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={encoded_syms}"
                if crumb:
                    url += f"&crumb={quote_plus(crumb)}"
                resp = session.get(url, timeout=6)

            if resp.status_code == 200:
                raw_list = (resp.json().get("quoteResponse") or {}).get("result") or []
                for item in raw_list:
                    parsed = _parse_quote_dict(item)
                    if parsed.symbol and parsed.price > 0:
                        _QUOTE_CACHE[parsed.symbol.upper()] = (now, parsed)
                        results[parsed.symbol.upper()] = parsed
                        fetched_any = True
        except Exception as e:
            logger.debug("Yahoo v7 batch quote failed: %s", e)

        # Fallback to v8/finance/chart for any symbol not returned by v7/finance/quote
        still_missing = [s for s in missing if s not in results]
        if still_missing and (not fetched_any or len(still_missing) <= 6):
            chart_quotes = map_in_workers(self._fetch_single_v8_chart, still_missing[:8])
            for cq in chart_quotes:
                if cq and cq.symbol:
                    _QUOTE_CACHE[cq.symbol.upper()] = (now, cq)
                    results[cq.symbol.upper()] = cq

        return results

    def _fetch_single_v8_chart(self, symbol: str) -> Optional[TickerQuote]:
        """Fallback keyless quote fetcher using Yahoo Finance v8/finance/chart."""
        try:
            session, _ = _get_session_and_crumb()
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}?interval=1d&range=5d"
            resp = session.get(url, timeout=5)
            if resp.status_code != 200:
                return None
            res_list = (resp.json().get("chart") or {}).get("result") or []
            if not res_list:
                return None
            meta = res_list[0].get("meta") or {}
            price = float(meta.get("regularMarketPrice") or 0.0)
            prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
            change = price - prev_close if prev_close else 0.0
            change_pct = (change / prev_close * 100.0) if prev_close else 0.0
            return TickerQuote(
                symbol=str(meta.get("symbol") or symbol).upper(),
                name=str(meta.get("longName") or meta.get("shortName") or symbol),
                exchange=str(meta.get("fullExchangeName") or meta.get("exchangeName") or ""),
                currency=str(meta.get("currency") or "USD"),
                market_state="REGULAR",
                price=price,
                change=change,
                change_percent=change_pct,
                day_low=float(meta["regularMarketDayLow"]) if meta.get("regularMarketDayLow") is not None else None,
                day_high=float(meta["regularMarketDayHigh"]) if meta.get("regularMarketDayHigh") is not None else None,
                week52_low=float(meta["fiftyTwoWeekLow"]) if meta.get("fiftyTwoWeekLow") is not None else None,
                week52_high=float(meta["fiftyTwoWeekHigh"]) if meta.get("fiftyTwoWeekHigh") is not None else None,
                volume=int(meta["regularMarketVolume"]) if meta.get("regularMarketVolume") is not None else None,
            )
        except Exception as e:
            logger.debug("Yahoo v8 chart fallback failed for %s: %s", symbol, e)
            return None

    def resolve_ticker_and_news(
        self,
        raw_query: str,
        prefer_canada: bool = True,
    ) -> Tuple[Optional[str], Optional[str], List[str]]:
        """
        Resolve a user's company name or ticker symbol into `(primary_symbol, dual_listed_symbol, headlines)`.
        Defaults to TSX (.TO / CAD) for Canadian dual-listed companies when `prefer_canada=True`.
        """
        q_clean = (raw_query or "").strip()
        q_low = q_clean.lower()

        primary_sym: Optional[str] = None
        dual_sym: Optional[str] = None

        # 1. Check Canadian dual-listed map first (e.g. 'Shopify' -> ('SHOP.TO', 'SHOP'))
        if prefer_canada and q_low in CANADIAN_DUAL_LISTED_MAP:
            primary_sym, dual_sym = CANADIAN_DUAL_LISTED_MAP[q_low]
        elif q_low in COMMON_TICKER_ALIASES:
            primary_sym = COMMON_TICKER_ALIASES[q_low]

        if primary_sym and primary_sym.endswith("-USD") and not dual_sym:
            dual_sym = primary_sym.removesuffix("-USD") + "-CAD"

        # 2. Query Yahoo v1/finance/search for ticker resolution and breaking ticker news
        # Note: Yahoo Search attaches news to the base symbol/company name rather than '.TO' or '-USD' suffixes.
        if primary_sym and primary_sym.endswith(".TO"):
            search_q = dual_sym or q_clean.removesuffix(".TO").removesuffix(".to")
        elif primary_sym and primary_sym.endswith("-USD"):
            search_q = {"BTC-USD": "IBIT", "ETH-USD": "ETHA"}.get(primary_sym, "COIN")
        else:
            search_q = primary_sym or q_clean
        headlines: List[str] = []
        try:
            session, _ = _get_session_and_crumb()
            url = (
                f"https://query1.finance.yahoo.com/v1/finance/search"
                f"?q={quote_plus(search_q)}&quotesCount=8&newsCount=5"
            )
            resp = session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                quotes = data.get("quotes") or []
                if not primary_sym and quotes:
                    # Prefer Toronto (.TO) if a Canadian listing exists for the same company name
                    tor_quote = next(
                        (
                            item for item in quotes
                            if str(item.get("symbol") or "").endswith(".TO")
                            and item.get("quoteType") in ("EQUITY", "ETF")
                        ),
                        None,
                    )
                    us_quote = next(
                        (
                            item for item in quotes
                            if item.get("quoteType") in ("EQUITY", "ETF", "INDEX", "FUTURE", "CRYPTOCURRENCY", "CURRENCY")
                            and not str(item.get("symbol") or "").endswith(".TO")
                        ),
                        None,
                    )
                    if prefer_canada and tor_quote:
                        primary_sym = str(tor_quote["symbol"]).upper()
                        if us_quote and str(us_quote.get("shortname") or "")[:6].lower() == str(tor_quote.get("shortname") or "")[:6].lower():
                            dual_sym = str(us_quote["symbol"]).upper()
                    elif quotes[0].get("symbol"):
                        primary_sym = str(quotes[0]["symbol"]).upper()

                for n in (data.get("news") or [])[:4]:
                    title = str(n.get("title") or "").strip()
                    pub = str(n.get("publisher") or "").strip()
                    link = str(n.get("link") or "").strip()
                    ts = n.get("providerPublishTime")
                    date_str = ""
                    if isinstance(ts, (int, float)) and ts > 0:
                        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %H:%M UTC")
                    meta = " | ".join(p for p in (pub, date_str) if p)
                    if title:
                        line = f"{title} [{meta}]" if meta else title
                        if link:
                            line += f" (<{link}>)"
                        headlines.append(line)
        except Exception as e:
            logger.debug("Yahoo v1 search failed for %s: %s", search_q, e)

        if not primary_sym and q_clean:
            primary_sym = q_clean.upper()

        return primary_sym, dual_sym, headlines

    def fetch_us_movers(self, count: int = 5) -> Dict[str, List[TickerQuote]]:
        """Fetch US Day Gainers, Day Losers, and Most Actives from Yahoo Finance predefined screeners."""
        now = time.time()
        out: Dict[str, List[TickerQuote]] = {"gainers": [], "losers": [], "actives": []}
        screener_map = {
            "gainers": "day_gainers",
            "losers": "day_losers",
            "actives": "most_actives",
        }
        session, _ = _get_session_and_crumb()
        for key, scr_id in screener_map.items():
            cached = _SCREENER_CACHE.get(scr_id)
            if cached and (now - cached[0]) < _SCREENER_CACHE_TTL:
                out[key] = cached[1][:count]
                continue
            try:
                url = (
                    f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
                    f"?scrIds={scr_id}&count={max(count, 6)}"
                )
                resp = session.get(url, timeout=5)
                if resp.status_code == 200:
                    res_list = (resp.json().get("finance") or {}).get("result") or []
                    if res_list:
                        quotes_raw = res_list[0].get("quotes") or []
                        parsed = [_parse_quote_dict(q) for q in quotes_raw if q.get("symbol")]
                        _SCREENER_CACHE[scr_id] = (now, parsed)
                        out[key] = parsed[:count]
            except Exception as e:
                logger.debug("Yahoo screener %s failed: %s", scr_id, e)
        return out

    def fetch_tsx_movers(self, count: int = 5) -> Dict[str, List[TickerQuote]]:
        """Compute top Canadian S&P/TSX 60 gainers and losers from a single batch quote call."""
        quotes_map = self.fetch_quotes(list(TSX_60_LEADERS))
        valid = [q for q in quotes_map.values() if q.price > 0]
        sorted_desc = sorted(valid, key=lambda x: x.change_percent, reverse=True)
        sorted_asc = sorted(valid, key=lambda x: x.change_percent)
        gainers = [q for q in sorted_desc if q.change_percent > 0][:count] or sorted_desc[:count]
        losers = [q for q in sorted_asc if q.change_percent < 0][:count] or sorted_asc[:count]
        return {"gainers": gainers, "losers": losers}

    def fetch_macro_market_headlines(self, limit: int = 5) -> List[str]:
        """Fetch top macro/financial market-moving headlines from Yahoo Finance news feeds."""
        now = time.time()
        cache_key = "macro_market_news"
        cached = _NEWS_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _NEWS_CACHE_TTL:
            return cached[1][:limit]

        headlines: List[str] = []
        seen_titles: set[str] = set()
        session, _ = _get_session_and_crumb()
        for q_term in ("stock market economy", "TSX Canada economy"):
            try:
                url = f"https://query1.finance.yahoo.com/v1/finance/search?q={quote_plus(q_term)}&quotesCount=0&newsCount=5"
                resp = session.get(url, timeout=5)
                if resp.status_code == 200:
                    for n in resp.json().get("news") or []:
                        title = str(n.get("title") or "").strip()
                        if not title or title.lower() in seen_titles:
                            continue
                        seen_titles.add(title.lower())
                        pub = str(n.get("publisher") or "").strip()
                        link = str(n.get("link") or "").strip()
                        line = f"{title} [{pub}]" if pub else title
                        if link:
                            line += f" (<{link}>)"
                        headlines.append(line)
                        if len(headlines) >= limit:
                            break
            except Exception as e:
                logger.debug("Macro headline fetch failed for %s: %s", q_term, e)
            if len(headlines) >= limit:
                break

        if headlines:
            _NEWS_CACHE[cache_key] = (now, headlines)
        return headlines[:limit]

    def _fetch_chart_bars(
        self, symbol: str, range_str: str = "5y", interval: str = "1d"
    ) -> Tuple[str, List[Tuple[int, float, float, float, float]]]:
        """Fetch and cache (currency, [(timestamp, open, high, low, close), ...]) from Yahoo v8 chart API."""
        clean_sym = symbol.strip().upper()
        cache_key = f"{clean_sym}:{range_str}:{interval}"
        now = time.time()
        cached = _CHART_SERIES_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _CHART_SERIES_TTL:
            return cached[1], cached[2]

        try:
            session, _ = _get_session_and_crumb()
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(clean_sym)}"
                f"?range={range_str}&interval={interval}"
            )
            resp = session.get(url, timeout=6)
            if resp.status_code != 200:
                return "USD", []
            results = resp.json().get("chart", {}).get("result")
            if not results:
                return "USD", []
            res0 = results[0]
            meta = res0.get("meta") or {}
            currency = str(meta.get("currency") or "USD").strip()
            timestamps = res0.get("timestamp") or []
            quotes_list = (res0.get("indicators") or {}).get("quote") or []
            q0 = quotes_list[0] if quotes_list else {}
            opens = q0.get("open") or []
            highs = q0.get("high") or []
            lows = q0.get("low") or []
            closes = q0.get("close") or []

            bars: List[Tuple[int, float, float, float, float]] = []
            for idx, ts in enumerate(timestamps):
                c_val = closes[idx] if idx < len(closes) else None
                if c_val is None or ts is None:
                    continue
                c_f = float(c_val)
                if c_f <= 0:
                    continue
                o_f = float(opens[idx]) if (idx < len(opens) and opens[idx] is not None) else c_f
                h_f = float(highs[idx]) if (idx < len(highs) and highs[idx] is not None) else c_f
                l_f = float(lows[idx]) if (idx < len(lows) and lows[idx] is not None) else c_f
                bars.append((int(ts), o_f, h_f, l_f, c_f))

            if bars:
                _CHART_SERIES_CACHE[cache_key] = (now, currency, bars)
            return currency, bars
        except Exception as e:
            logger.debug("Yahoo historical chart fetch failed for %s (%s): %s", clean_sym, range_str, e)
            return "USD", []

    def fetch_historical_summary(self, symbol: str, timeframe: str = "1y") -> Optional[HistoricalSummary]:
        """
        Compute multi-horizon returns (1W, 1M, 3M, 6M, YTD, 1Y, 3Y, 5Y) plus a deep historical window
        analysis (start/end price, %/$, CAGR, period peak/trough with dates, max drawdown, realized volatility,
        and chronological price checkpoints) for any global stock, index, commodity, FX pair, or crypto.
        """
        clean_sym = symbol.strip().upper()
        if not clean_sym:
            return None

        # 1. Always fetch 5-year daily series first (115ms) so we can compute all standard horizon returns
        currency, daily_bars = self._fetch_chart_bars(clean_sym, range_str="5y", interval="1d")
        if len(daily_bars) < 2:
            return None

        latest_ts, _, _, _, latest_close = daily_bars[-1]
        latest_dt = datetime.fromtimestamp(latest_ts, tz=timezone.utc)

        # 2. Compute standard multi-horizon return strip
        horizon_returns: Dict[str, float] = {}
        horizon_specs = [
            ("1W", 7),
            ("1M", 31),
            ("3M", 92),
            ("6M", 183),
            ("YTD", -1),
            ("1Y", 365),
            ("3Y", 3 * 365),
            ("5Y", 5 * 365),
        ]
        jan1_ts = int(datetime(latest_dt.year, 1, 1, tzinfo=timezone.utc).timestamp())
        earliest_ts = daily_bars[0][0]

        for label, days in horizon_specs:
            ref_price: Optional[float] = None
            if label == "1W":
                ref_idx = max(0, len(daily_bars) - 6)
                ref_price = daily_bars[ref_idx][4]
            elif label == "YTD":
                # Standard YTD baseline: last trading close of previous calendar year (or first bar of current year)
                prev_year_bars = [b for b in daily_bars if b[0] < jan1_ts]
                if prev_year_bars:
                    ref_price = prev_year_bars[-1][4]
                else:
                    curr_year_bars = [b for b in daily_bars if b[0] >= jan1_ts]
                    if curr_year_bars:
                        ref_price = curr_year_bars[0][4]
            else:
                cutoff_ts = latest_ts - days * 86400
                # Require at least 80% of the horizon span to exist in the dataset for 3Y/5Y
                if days >= 365 and (latest_ts - earliest_ts) < (days * 86400 * 0.80):
                    continue
                for b in daily_bars:
                    if b[0] >= cutoff_ts:
                        ref_price = b[4]
                        break
            if ref_price and ref_price > 0:
                horizon_returns[label] = round((latest_close - ref_price) / ref_price * 100.0, 2)

        # 3. Determine target historical window slice from `timeframe`
        tf_norm = (timeframe or "1y").strip().lower()
        window_bars = daily_bars
        tf_label = "1-Year (1Y)"
        is_daily = True

        # Check if user asked for a specific calendar year (e.g. '2024', '2025')
        year_match = re.fullmatch(r"(20\d{2})", tf_norm)
        if year_match:
            yr = int(year_match.group(1))
            y_start = int(datetime(yr, 1, 1, tzinfo=timezone.utc).timestamp())
            y_end = int(datetime(yr, 12, 31, 23, 59, 59, tzinfo=timezone.utc).timestamp())
            if y_start < earliest_ts:
                _, max_bars = self._fetch_chart_bars(clean_sym, range_str="max", interval="1wk")
                source_bars = max_bars or daily_bars
                is_daily = False
            else:
                source_bars = daily_bars
            yr_bars = [b for b in source_bars if y_start <= b[0] <= y_end]
            if len(yr_bars) >= 2:
                window_bars = yr_bars
                tf_label = f"Calendar Year {yr}"
        elif tf_norm in ("10y", "10yr", "10 years", "decade"):
            _, bars_10y = self._fetch_chart_bars(clean_sym, range_str="10y", interval="1wk")
            if len(bars_10y) >= 2:
                window_bars = bars_10y
                is_daily = False
            tf_label = "10-Year (10Y)"
        elif tf_norm in ("max", "all", "all time", "lifetime"):
            _, bars_max = self._fetch_chart_bars(clean_sym, range_str="max", interval="1mo")
            if len(bars_max) >= 2:
                window_bars = bars_max
                is_daily = False
            tf_label = "All-Time (MAX)"
        else:
            tf_map = {
                "5d": ("1-Week (5D)", 7),
                "1w": ("1-Week (5D)", 7),
                "1mo": ("1-Month (1M)", 31),
                "1m": ("1-Month (1M)", 31),
                "3mo": ("3-Month (3M)", 92),
                "3m": ("3-Month (3M)", 92),
                "6mo": ("6-Month (6M)", 183),
                "6m": ("6-Month (6M)", 183),
                "ytd": ("Year-to-Date (YTD)", -1),
                "1y": ("1-Year (1Y)", 365),
                "2y": ("2-Year (2Y)", 730),
                "3y": ("3-Year (3Y)", 1095),
                "5y": ("5-Year (5Y)", 1825),
            }
            tf_label, days_back = tf_map.get(tf_norm, ("1-Year (1Y)", 365))
            if days_back == -1:
                prev_year_bars = [b for b in daily_bars if b[0] < jan1_ts]
                curr_year_bars = [b for b in daily_bars if b[0] >= jan1_ts]
                if prev_year_bars and curr_year_bars:
                    window_bars = [prev_year_bars[-1]] + curr_year_bars
                elif len(curr_year_bars) >= 2:
                    window_bars = curr_year_bars
            elif days_back == 7 and len(daily_bars) >= 6:
                window_bars = daily_bars[-6:]
            else:
                cutoff = latest_ts - days_back * 86400
                sliced = [b for b in daily_bars if b[0] >= cutoff]
                if len(sliced) >= 2:
                    window_bars = sliced

        # 4. Calculate Window Statistics
        start_ts, _, _, _, start_price = window_bars[0]
        end_ts, _, _, _, end_price = window_bars[-1]
        start_date_str = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%b %d, %Y")
        end_date_str = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%b %d, %Y")

        abs_change = round(end_price - start_price, 4)
        pct_change = round((end_price - start_price) / start_price * 100.0, 2) if start_price > 0 else 0.0

        span_years = (end_ts - start_ts) / (365.25 * 86400.0)
        cagr_pct: Optional[float] = None
        if span_years >= 1.5 and start_price > 0 and end_price > 0:
            cagr_pct = round(((end_price / start_price) ** (1.0 / span_years) - 1.0) * 100.0, 2)

        # Period Peak, Trough, and Max Drawdown
        high_bar = max(window_bars, key=lambda b: b[2])
        low_bar = min(window_bars, key=lambda b: b[3])
        period_high = round(high_bar[2], 4)
        period_high_date = datetime.fromtimestamp(high_bar[0], tz=timezone.utc).strftime("%b %d, %Y")
        period_low = round(low_bar[3], 4)
        period_low_date = datetime.fromtimestamp(low_bar[0], tz=timezone.utc).strftime("%b %d, %Y")

        running_peak = window_bars[0][4]
        max_dd = 0.0
        daily_rets: List[float] = []
        for idx, b in enumerate(window_bars):
            close_v = b[4]
            if close_v > running_peak:
                running_peak = close_v
            if running_peak > 0:
                dd = (close_v - running_peak) / running_peak * 100.0
                if dd < max_dd:
                    max_dd = dd
            if idx > 0 and window_bars[idx - 1][4] > 0:
                daily_rets.append((close_v - window_bars[idx - 1][4]) / window_bars[idx - 1][4])

        ann_vol: Optional[float] = None
        if is_daily and len(daily_rets) >= 10:
            mean_r = sum(daily_rets) / len(daily_rets)
            variance = sum((r - mean_r) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
            ann_vol = round(math.sqrt(variance) * math.sqrt(252.0) * 100.0, 1)

        # 5. Build 6 chronological checkpoints across the window
        checkpoints: List[str] = []
        n_bars = len(window_bars)
        num_pts = min(6, n_bars)
        indices = sorted({int(round(i * (n_bars - 1) / (num_pts - 1))) for i in range(num_pts)})
        date_fmt = "%b %d, %Y" if span_years >= 0.8 else "%b %d"
        for i in indices:
            b_ts, _, _, _, b_close = window_bars[i]
            d_str = datetime.fromtimestamp(b_ts, tz=timezone.utc).strftime(date_fmt)
            p_str = f"{b_close:,.2f}" if b_close >= 1 else f"{b_close:.4f}"
            checkpoints.append(f"{d_str}: **{p_str}**")

        return HistoricalSummary(
            symbol=clean_sym,
            currency=currency,
            timeframe_label=tf_label,
            start_date=start_date_str,
            end_date=end_date_str,
            start_price=round(start_price, 4),
            end_price=round(end_price, 4),
            abs_change=abs_change,
            pct_change=pct_change,
            cagr_pct=cagr_pct,
            period_high=period_high,
            period_high_date=period_high_date,
            period_low=period_low,
            period_low_date=period_low_date,
            max_drawdown_pct=round(max_dd, 2),
            annualized_volatility_pct=ann_vol,
            horizon_returns=horizon_returns,
            checkpoints=checkpoints,
        )

