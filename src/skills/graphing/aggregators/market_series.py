"""
Self-contained Yahoo Finance v8 time-series fetcher for `src/skills/graphing/` (`src/skills/graphing/aggregators/market_series.py`).

Enables `skill_graphing_info` to render single-ticker OHLCV/MA charts and multi-ticker normalized return
comparison charts across any timeframe (1D, 5D, 1M, 3M, 6M, YTD, 1Y, 2Y, 5Y, 10Y, MAX) without
cross-importing `src.skills.markets`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import numpy as np

from src.skills.graphing.aggregators.base import BaseGraphingAggregator
from src.skills.graphing.catalogs import CHART_TICKER_ALIASES, resolve_fx_pair_symbol
from src.skills.graphing.models import MarketBarSeries, MonteCarloResult
from src.utils.cache import BoundedCache
from src.utils.http import DEFAULT_UA, ThreadLocalRequestsSession

_HTTP_SESSION = ThreadLocalRequestsSession({"User-Agent": DEFAULT_UA})
_SERIES_CACHE: BoundedCache = BoundedCache(maxsize=128)
_SERIES_TTL = 180.0

# (yf_range, default_yf_interval, base_label, optional_cutoff_days)
_TIMEFRAME_SPECS: Dict[str, Tuple[str, str, str, Optional[int]]] = {
    "1d": ("1d", "5m", "Intraday (1D)", None),
    "24h": ("2d", "15m", "24-Hour (24H)", 1),
    "2d": ("5d", "15m", "2-Day (2D)", 2),
    "48h": ("5d", "15m", "48-Hour (48H)", 2),
    "3d": ("5d", "15m", "3-Day (3D)", 3),
    "72h": ("5d", "15m", "72-Hour (72H)", 3),
    "4d": ("5d", "15m", "4-Day (4D)", 4),
    "5d": ("5d", "15m", "5-Day (5D)", None),
    "7d": ("1mo", "1h", "7-Day (7D)", 7),
    "1w": ("1mo", "1h", "1-Week (7D)", 7),
    "10d": ("1mo", "1h", "10-Day (10D)", 10),
    "14d": ("1mo", "1h", "2-Week (14D)", 14),
    "2w": ("1mo", "1h", "2-Week (14D)", 14),
    "3w": ("1mo", "1d", "3-Week (21D)", 21),
    "21d": ("1mo", "1d", "21-Day (21D)", 21),
    "1mo": ("1mo", "1d", "1-Month (1M)", None),
    "1m": ("1mo", "1d", "1-Month (1M)", None),
    "30d": ("1mo", "1d", "30-Day (1M)", None),
    "2mo": ("3mo", "1d", "2-Month (2M)", 60),
    "60d": ("3mo", "1d", "60-Day (2M)", 60),
    "3mo": ("3mo", "1d", "3-Month (3M)", None),
    "3m": ("3mo", "1d", "3-Month (3M)", None),
    "90d": ("3mo", "1d", "90-Day (3M)", None),
    "6mo": ("6mo", "1d", "6-Month (6M)", None),
    "6m": ("6mo", "1d", "6-Month (6M)", None),
    "ytd": ("ytd", "1d", "Year-to-Date (YTD)", None),
    "1y": ("1y", "1d", "1-Year (1Y)", None),
    "2y": ("2y", "1d", "2-Year (2Y)", None),
    "3y": ("5y", "1wk", "3-Year (3Y)", 3 * 365),
    "5y": ("5y", "1wk", "5-Year (5Y)", None),
    "10y": ("10y", "1mo", "10-Year (10Y)", None),
    "max": ("max", "1mo", "All-Time (MAX)", None),
}

_VALID_YF_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"}


def normalize_chart_interval(raw_interval: Optional[str]) -> Optional[str]:
    """Normalize natural-language or shorthand bar intervals into Yahoo Finance interval codes."""
    if not raw_interval:
        return None
    s = raw_interval.strip().lower()
    if s in _VALID_YF_INTERVALS:
        return s
    if re.search(r"\b(?:1\s*h(?:our|r)?s?|hourly|by\s+h(?:our|r)|per\s+h(?:our|r)|60\s*m(?:in)?)\b", s):
        return "1h"
    if re.search(r"\b(?:15\s*m(?:in(?:ute)?s?)?)\b", s):
        return "15m"
    if re.search(r"\b(?:5\s*m(?:in(?:ute)?s?)?)\b", s):
        return "5m"
    if re.search(r"\b(?:30\s*m(?:in(?:ute)?s?)?)\b", s):
        return "30m"
    if re.search(r"\b(?:daily|by\s+day|1\s*d\s+bars?)\b", s):
        return "1d"
    if re.search(r"\b(?:weekly|by\s+week|1\s*w(?:k)?\s+bars?)\b", s):
        return "1wk"
    return None


def _resolve_timeframe_and_interval(
    timeframe: str,
    interval: Optional[str] = None,
) -> Tuple[str, str, str, Optional[int]]:
    """
    Resolve arbitrary timeframe strings (e.g. '7d', '3d', '2w', '1mo', '48h', '7d 1h') and optional
    bar intervals ('1h', '15m', '1d') into `(yf_range, yf_interval, tf_label, cutoff_days)`.
    """
    tf_raw = (timeframe or "1y").strip().lower()
    inferred_int = normalize_chart_interval(interval) or normalize_chart_interval(tf_raw)

    # Strip interval tokens from tf_raw if combined (e.g. "7d 1h" -> "7d")
    tf_clean = re.sub(
        r"\b(?:hourly|by\s+h(?:our|r)|1\s*h(?:r|our)?|60m|15m|5m|30m|daily|weekly)\b",
        "",
        tf_raw,
    ).strip(" ,;-_/")
    if not tf_clean:
        tf_clean = "7d" if inferred_int in ("1h", "15m", "30m") else ("1d" if inferred_int == "5m" else "1y")

    # Normalize common phrasing inside timeframe argument ("last 7 days" -> "7d", "3 days" -> "3d")
    m_hours = re.search(r"^(\d+)\s*h(?:ours?|rs?)?$", tf_clean)
    m_days = re.search(r"^(?:last\s+|past\s+)?(\d+)\s*d(?:ays?)?$", tf_clean)
    m_weeks = re.search(r"^(?:last\s+|past\s+)?(\d+)\s*w(?:eeks?|ks?)?$", tf_clean)
    m_months = re.search(r"^(?:last\s+|past\s+)?(\d+)\s*m(?:onths?|o)?$", tf_clean)
    m_years = re.search(r"^(?:last\s+|past\s+)?(\d+)\s*y(?:ears?|rs?)?$", tf_clean)

    if tf_clean in _TIMEFRAME_SPECS:
        yf_range, default_int, tf_label, cutoff_days = _TIMEFRAME_SPECS[tf_clean]
    elif re.fullmatch(r"20\d{2}", tf_clean):
        yf_range, default_int, tf_label, cutoff_days = ("10y", "1d", f"Calendar Year {tf_clean}", None)
    elif m_hours:
        hrs = max(1, int(m_hours.group(1)))
        days = max(1, math.ceil(hrs / 24.0))
        yf_range = "5d" if days <= 5 else "1mo"
        default_int = "5m" if hrs <= 24 else ("15m" if hrs <= 72 else "1h")
        tf_label = f"{hrs}-Hour ({hrs}H)"
        cutoff_days = days
    elif m_days:
        days = max(1, int(m_days.group(1)))
        if days == 1:
            yf_range, default_int, tf_label, cutoff_days = ("1d", "5m", "Intraday (1D)", None)
        elif days <= 5:
            yf_range, default_int, tf_label, cutoff_days = ("5d", "15m", f"{days}-Day ({days}D)", days if days < 5 else None)
        elif days <= 30:
            yf_range, default_int, tf_label, cutoff_days = ("1mo", "1h" if days <= 14 else "1d", f"{days}-Day ({days}D)", days if days < 30 else None)
        elif days <= 90:
            yf_range, default_int, tf_label, cutoff_days = ("3mo", "1d", f"{days}-Day ({days}D)", days if days < 90 else None)
        elif days <= 180:
            yf_range, default_int, tf_label, cutoff_days = ("6mo", "1d", f"{days}-Day ({days}D)", days)
        else:
            yf_range, default_int, tf_label, cutoff_days = ("1y", "1d", f"{days}-Day ({days}D)", days)
    elif m_weeks:
        wks = max(1, int(m_weeks.group(1)))
        days = wks * 7
        yf_range = "1mo" if days <= 28 else ("3mo" if days <= 90 else "1y")
        default_int = "1h" if wks <= 2 else "1d"
        tf_label = f"{wks}-Week ({days}D)"
        cutoff_days = days
    elif m_months:
        mos = max(1, int(m_months.group(1)))
        days = mos * 30
        yf_range = "1mo" if mos == 1 else ("3mo" if mos <= 3 else ("6mo" if mos <= 6 else ("1y" if mos <= 12 else "2y")))
        default_int = "1d"
        tf_label = f"{mos}-Month ({mos}M)"
        cutoff_days = days if mos not in (1, 3, 6, 12, 24) else None
    elif m_years:
        yrs = max(1, int(m_years.group(1)))
        yf_range = "1y" if yrs == 1 else ("2y" if yrs == 2 else ("5y" if yrs <= 5 else ("10y" if yrs <= 10 else "max")))
        default_int = "1d" if yrs <= 2 else "1wk"
        tf_label = f"{yrs}-Year ({yrs}Y)"
        cutoff_days = yrs * 365 if yrs not in (1, 2, 5, 10) else None
    else:
        yf_range, default_int, tf_label, cutoff_days = ("1y", "1d", "1-Year (1Y)", None)

    eff_interval = default_int
    if inferred_int:
        # Ensure Yahoo Finance supports the requested interval for the range (1h supported up to 2y; 15m/5m up to 1mo)
        if inferred_int in ("5m", "15m", "30m") and yf_range not in ("1d", "2d", "5d", "1mo"):
            eff_interval = "1h" if yf_range in ("3mo", "6mo", "ytd", "1y", "2y") else default_int
        elif inferred_int == "1h" and yf_range in ("5y", "10y", "max"):
            eff_interval = "1d"
        else:
            eff_interval = inferred_int
        if eff_interval != default_int or inferred_int:
            int_badge = eff_interval.upper()
            if int_badge not in tf_label:
                tf_label = f"{tf_label} [{int_badge} Bars]"

    return yf_range, eff_interval, tf_label, cutoff_days


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
    """Fetches historical OHLCV bars from Yahoo Finance v8 chart API for chart rendering and Monte Carlo simulation."""

    name = "market_series"

    def fetch_bar_series(
        self,
        target: str,
        timeframe: str = "1y",
        interval: Optional[str] = None,
    ) -> Optional[MarketBarSeries]:
        sym = resolve_chart_ticker(target)
        if not sym:
            return None

        tf_norm = (timeframe or "1y").strip().lower()
        yf_range, yf_interval, tf_label, cutoff_days = _resolve_timeframe_and_interval(tf_norm, interval=interval)

        cache_key = f"{sym}::{tf_norm}::{yf_interval}::{cutoff_days or 0}"
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

            # Filter for specific calendar year or exact N-day sub-window
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
            elif cutoff_days and timestamps:
                cutoff = timestamps[-1] - int(cutoff_days * 86400)
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

    def run_monte_carlo(
        self,
        target: str,
        horizon: str = "1y",
        num_simulations: int = 10000,
        lookback: Optional[str] = None,
    ) -> Optional[MonteCarloResult]:
        """
        Execute a vectorized Geometric Brownian Motion (GBM) Monte Carlo simulation calibrated
        from historical daily log returns of `target`. Defaults to 5 years of daily history ('5y')
        unless a custom `lookback` timeframe (e.g. '10y', 'max', '3y', '1y', '6mo') is provided.
        """
        lb_clean = (lookback or "").strip().lower()
        is_default_lb = not lb_clean
        eff_lb = lb_clean if lb_clean else "5y"

        # Fetch daily ('1d') bars for the requested lookback window, with graceful fallback for newer listings
        hist_series = (
            self.fetch_bar_series(target, timeframe=eff_lb, interval="1d")
            or self.fetch_bar_series(target, timeframe="5y", interval="1d")
            or self.fetch_bar_series(target, timeframe="2y", interval="1d")
            or self.fetch_bar_series(target, timeframe="1y", interval="1d")
        )
        if not hist_series or len(hist_series.closes) < 20:
            return None

        _, _, raw_lb_label, _ = _resolve_timeframe_and_interval(eff_lb)
        base_lb_name = re.sub(r"\s*\[.*?\]", "", raw_lb_label).strip()
        if is_default_lb:
            lookback_label = "5-Year History (Default)"
        else:
            lookback_label = f"{base_lb_name} History"

        closes = np.asarray(hist_series.closes, dtype=np.float64)
        valid_mask = closes > 0
        closes = closes[valid_mask]
        if len(closes) < 20:
            return None

        spot_price = float(closes[-1])
        is_crypto = hist_series.symbol.endswith(("-USD", "-CAD", "-EUR", "-GBP"))
        days_per_year = 365 if is_crypto else 252

        # Parse forward horizon into simulation steps
        h_clean = (horizon or "1y").strip().lower()
        m_y = re.search(r"(\d+)\s*y(?:ears?|rs?)?", h_clean)
        m_m = re.search(r"(\d+)\s*m(?:onths?|o)?", h_clean)
        m_w = re.search(r"(\d+)\s*w(?:eeks?|ks?)?", h_clean)
        m_d = re.search(r"(\d+)\s*d(?:ays?)?", h_clean)

        if m_y:
            yrs = max(1, min(10, int(m_y.group(1))))
            horizon_days = yrs * days_per_year
            horizon_label = f"{yrs}-Year Forward ({horizon_days} {'Calendar' if is_crypto else 'Trading'} Days)"
        elif m_m:
            mos = max(1, min(60, int(m_m.group(1))))
            horizon_days = max(5, int(round((mos / 12.0) * days_per_year)))
            horizon_label = f"{mos}-Month Forward ({horizon_days} Days)"
        elif m_w:
            wks = max(1, min(104, int(m_w.group(1))))
            horizon_days = wks * (7 if is_crypto else 5)
            horizon_label = f"{wks}-Week Forward ({horizon_days} Days)"
        elif m_d:
            days = max(3, min(1825, int(m_d.group(1))))
            horizon_days = days
            horizon_label = f"{days}-Day Forward ({days} Days)"
        else:
            horizon_days = days_per_year
            horizon_label = f"1-Year Forward ({horizon_days} Days)"

        log_returns = np.diff(np.log(closes))
        daily_mean_log_ret = float(np.mean(log_returns))
        daily_vol = float(np.std(log_returns, ddof=1))
        if daily_vol <= 1e-8:
            daily_vol = 0.015

        raw_ann_drift = daily_mean_log_ret * days_per_year
        # Bound annualized log-drift to [-40%, +45%] so multi-year crypto/growth simulations
        # reflect empirical directional bias without compounding outlier bull-run windows into 50x medians
        eff_ann_drift = max(-0.40, min(0.45, raw_ann_drift))
        eff_daily_log_drift = eff_ann_drift / days_per_year
        ann_vol = daily_vol * math.sqrt(days_per_year)

        n_sims = max(100, min(100000, int(num_simulations)))
        rng = np.random.default_rng(
            seed=abs(hash((hist_series.symbol, horizon_days, len(closes), round(spot_price, 2), n_sims))) % (2**32)
        )
        # Antithetic variates (half Z, half -Z) in-place float32 for fast execution up to 100,000 paths
        half_n = n_sims // 2
        z_half = rng.standard_normal((half_n, horizon_days), dtype=np.float32)
        z_shocks = np.vstack([z_half, -z_half])
        if z_shocks.shape[0] < n_sims:
            z_shocks = np.vstack([z_shocks, rng.standard_normal((1, horizon_days), dtype=np.float32)])

        np.multiply(z_shocks, np.float32(daily_vol), out=z_shocks)
        np.add(z_shocks, np.float32(eff_daily_log_drift), out=z_shocks)
        np.cumsum(z_shocks, axis=1, out=z_shocks)
        np.exp(z_shocks, out=z_shocks)
        np.multiply(z_shocks, np.float32(spot_price), out=z_shocks)

        sim_paths = np.empty((n_sims, horizon_days + 1), dtype=np.float32)
        sim_paths[:, 0] = np.float32(spot_price)
        sim_paths[:, 1:] = z_shocks

        percentiles = np.percentile(sim_paths, [5, 25, 50, 75, 95], axis=0)
        mean_curve = np.mean(sim_paths, axis=0, dtype=np.float64)
        terminal = sim_paths[:, -1]

        prob_profit = float(np.mean(terminal > spot_price) * 100.0)
        prob_double = float(np.mean(terminal >= 2.0 * spot_price) * 100.0)
        prob_dd20 = float(np.mean(terminal <= 0.8 * spot_price) * 100.0)

        # Keep up to 90 historical bars for left-hand context on the fan chart
        hist_tail_len = min(len(hist_series.closes), max(30, min(120, horizon_days // 3)))
        hist_ts = list(hist_series.timestamps[-hist_tail_len:])
        hist_cl = [float(x) for x in hist_series.closes[-hist_tail_len:]]

        last_ts = hist_ts[-1] if hist_ts else int(time.time())
        step_sec = 86400 if is_crypto else int(round(365.25 * 86400 / 252))
        future_ts = [last_ts + i * step_sec for i in range(horizon_days + 1)]

        sample_count = min(60, n_sims)
        sample_paths = [[float(v) for v in sim_paths[i]] for i in range(sample_count)]
        term_step = max(1, len(terminal) // 10000)

        return MonteCarloResult(
            symbol=hist_series.symbol,
            name=hist_series.name,
            currency=hist_series.currency,
            spot_price=spot_price,
            horizon_label=horizon_label,
            horizon_days=horizon_days,
            num_simulations=n_sims,
            lookback_days=len(closes),
            annualized_drift_pct=round((math.exp(eff_ann_drift) - 1.0) * 100.0, 2),
            annualized_volatility_pct=round(ann_vol * 100.0, 2),
            history_timestamps=hist_ts,
            history_closes=hist_cl,
            future_timestamps=future_ts,
            sample_paths=sample_paths,
            p5_path=[float(x) for x in percentiles[0]],
            p25_path=[float(x) for x in percentiles[1]],
            p50_path=[float(x) for x in percentiles[2]],
            p75_path=[float(x) for x in percentiles[3]],
            p95_path=[float(x) for x in percentiles[4]],
            mean_path=[float(x) for x in mean_curve],
            terminal_prices=[float(x) for x in terminal[::term_step]],
            prob_profit_pct=round(prob_profit, 1),
            prob_double_pct=round(prob_double, 1),
            prob_drawdown_20_pct=round(prob_dd20, 1),
            lookback_label=lookback_label,
        )


