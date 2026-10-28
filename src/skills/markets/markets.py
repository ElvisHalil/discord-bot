"""
Global Markets, Commodities, Crypto & Macro Intelligence Skill (`skill_markets_info`).

Provides real-time coverage across:
- Canadian (S&P/TSX Composite, S&P/TSX 60 movers, CAD/USD), American (S&P 500, NASDAQ, Dow, Russell 2000, VIX, 10Y Yield),
  European (EURO STOXX 50, FTSE 100, DAX, CAC 40, SMI), and Asian (Nikkei 225, Hang Seng, Shanghai, KOSPI, ASX 200, SENSEX) markets.
- Precious Metals (Gold, Silver, Platinum, Palladium) & Energy/Industrial Commodities (WTI Crude, Brent Crude, Natural Gas, Copper).
- Cryptocurrencies (BTC, ETH, SOL, XRP, BNB, DOGE, ADA + top 24h crypto gainers/losers in USD & CAD).
- Deep Single-Ticker Snapshots (defaulting Canadian dual-listed companies to TSX/CAD, with 50d/200d moving averages,
  52-week range position, P/E, Market Cap, Dividend Yield, Earnings Date, and ticker-specific catalyst news).
"""

import calendar
from datetime import datetime, timezone
import re
from typing import Dict, List, Optional, Tuple

from src.skills.markets.aggregators import (
    CryptoAggregator,
    MacroCalendarAggregator,
    YahooFinanceAggregator,
    clear_crypto_market_cache,
    clear_macro_calendar_cache,
    clear_yahoo_market_cache,
)
from src.skills.markets.catalogs import (
    CANADIAN_DUAL_LISTED_MAP,
    COMMODITY_BENCHMARKS,
    COMMON_TICKER_ALIASES,
    FOREX_BENCHMARKS,
    GLOBAL_INDICES,
    MARKET_QUERY_STOPWORDS,
    TSX_60_LEADERS,
)
from src.skills.markets.context import (
    clear_channel_market_state,
    get_channel_market_state,
    is_macro_calendar_query,
    update_channel_market_state,
)
from src.skills.markets.enums import MarketRegion, MarketView
from src.skills.markets.models import (
    BenchmarkSpec,
    CryptoAssetQuote,
    HistoricalSummary,
    TickerQuote,
)
from src.skills.markets.templates import (
    build_macro_calendar_llm_instructions,
    build_markets_llm_instructions,
    build_news_market_pulse_instructions,
)
from src.utils.domain_vocab import MONTH_MAP_SYNC
from src.utils.workers import submit_to_worker

_yahoo_agg = YahooFinanceAggregator()
_crypto_agg = CryptoAggregator()
_macro_agg = MacroCalendarAggregator()


def clear_markets_cache() -> None:
    """Clear all in-memory market, screener, crypto, macro calendar, and per-channel market state caches."""
    clear_yahoo_market_cache()
    clear_crypto_market_cache()
    clear_macro_calendar_cache()
    clear_channel_market_state()



def _fmt_arrow(pct: float) -> str:
    if pct > 0.02:
        return "🟢 ▲"
    if pct < -0.02:
        return "🔴 ▼"
    return "⚪ •"


def _fmt_num(val: float) -> str:
    abs_v = abs(val)
    if abs_v >= 1000:
        return f"{val:,.2f}"
    if abs_v >= 1:
        return f"{val:.2f}"
    if abs_v >= 0.01:
        return f"{val:.4f}"
    if abs_v >= 0.0001:
        return f"{val:.6f}"
    return f"{val:.8f}"


def _fmt_signed(val: float) -> str:
    abs_v = abs(val)
    if abs_v >= 1:
        return f"{val:+.2f}"
    if abs_v >= 0.01:
        return f"{val:+.4f}"
    if abs_v >= 0.0001:
        return f"{val:+.6f}"
    return f"{val:+.8f}"


def _fmt_price(val: float, currency: str = "USD") -> str:
    return f"{_fmt_num(val)} {currency}".strip()


def _fmt_large_number(num: Optional[float], prefix: str = "$") -> str:
    if num is None or num <= 0:
        return "N/A"
    if num >= 1e12:
        return f"{prefix}{num / 1e12:.2f}T"
    if num >= 1e9:
        return f"{prefix}{num / 1e9:.2f}B"
    if num >= 1e6:
        return f"{prefix}{num / 1e6:.2f}M"
    return f"{prefix}{num:,.0f}"


def _compute_macro_regime_signals(
    quotes: Dict[str, TickerQuote],
    crypto_coins: List[CryptoAssetQuote],
) -> List[str]:
    """Synthesize high-level macro regime, volatility, and cross-asset rotation signals from live quotes."""
    signals: List[str] = []

    tsx = quotes.get("^GSPTSE")
    sp500 = quotes.get("^GSPC")
    nasdaq = quotes.get("^IXIC")
    vix = quotes.get("^VIX")
    tnx = quotes.get("^TNX")
    gold = quotes.get("GC=F")
    silver = quotes.get("SI=F")
    wti = quotes.get("CL=F")
    cad = quotes.get("CADUSD=X")

    # 1. Volatility & Risk Posture
    if vix and vix.price > 0:
        if vix.price >= 22 or vix.change_percent >= 7.0:
            posture = "Risk-Off / Elevated Hedging"
        elif vix.price <= 16 and (sp500 and sp500.change_percent >= 0):
            posture = "Risk-On / Calm Volatility"
        else:
            posture = "Mixed / Selective Rotation"
        signals.append(
            f"- **Market Regime:** **{posture}** (CBOE VIX at **{vix.price:.2f}** [{vix.change_percent:+.2f}%])"
        )

    # 2. North American Equity Divergence (TSX vs. US Tech/Broad Market)
    if tsx and sp500 and nasdaq:
        if tsx.change_percent > 0 and sp500.change_percent < -0.3:
            signals.append(
                f"- **Regional Divergence:** **Canadian S&P/TSX ({tsx.change_percent:+.2f}%)** is outperforming "
                f"**US S&P 500 ({sp500.change_percent:+.2f}%)** & **NASDAQ ({nasdaq.change_percent:+.2f}%)**, "
                "supported by resource/commodity resilience while US growth/tech pulls back."
            )
        elif nasdaq.change_percent - sp500.change_percent <= -0.6:
            signals.append(
                f"- **Sector Rotation:** Growth/Tech (**NASDAQ {nasdaq.change_percent:+.2f}%**) is lagging the broader "
                f"**S&P 500 ({sp500.change_percent:+.2f}%)** and value/cyclical benchmarks."
            )
        elif nasdaq.change_percent - sp500.change_percent >= 0.6:
            signals.append(
                f"- **Tech Leadership:** **NASDAQ ({nasdaq.change_percent:+.2f}%)** is leading the **S&P 500 ({sp500.change_percent:+.2f}%)** "
                f"and **S&P/TSX ({tsx.change_percent:+.2f}%)** on strong growth appetite."
            )

    # 3. Safe-Haven & Precious Metals Flow
    if gold and gold.price > 0:
        sil_str = f", Silver **${silver.price:.2f}** ({silver.change_percent:+.2f}%)" if silver and silver.price > 0 else ""
        if gold.change_percent > 0.2 and sp500 and sp500.change_percent < -0.3:
            signals.append(
                f"- **Safe-Haven Bid:** Precious metals are catching a defensive bid while equities dip "
                f"(**Gold ${gold.price:,.2f}/oz [{gold.change_percent:+.2f}%]**{sil_str})."
            )
        else:
            signals.append(
                f"- **Precious Metals:** **Gold** at **${gold.price:,.2f}/oz ({gold.change_percent:+.2f}%)**{sil_str}."
            )

    # 4. Energy & Commodity Tape
    if wti and wti.price > 0:
        signals.append(
            f"- **Energy & Rates:** **WTI Crude Oil** at **${wti.price:.2f}/bbl ({wti.change_percent:+.2f}%)**"
            + (f" | **US 10Y Treasury Yield:** **{tnx.price:.2f}% ({tnx.change_percent:+.2f}%)**" if tnx and tnx.price > 0 else "")
            + (f" | **Canadian Dollar (CAD/USD):** **{cad.price:.4f} USD ({cad.change_percent:+.2f}%)**" if cad and cad.price > 0 else "")
        )

    # 5. Crypto Sentiment
    if crypto_coins:
        btc = next((c for c in crypto_coins if c.symbol == "BTC"), None)
        eth = next((c for c in crypto_coins if c.symbol == "ETH"), None)
        if btc:
            cad_tag = f" / ${btc.price_cad:,.0f} CAD" if btc.price_cad else ""
            eth_tag = f" | **ETH:** ${eth.price_usd:,.2f} USD ({eth.change_24h_pct:+.2f}%)" if eth else ""
            signals.append(
                f"- **Digital Assets:** **Bitcoin (BTC)** at **${btc.price_usd:,.0f} USD{cad_tag} ({btc.change_24h_pct:+.2f}% 24h)**{eth_tag}."
            )

    return signals


def _format_benchmark_line(spec: BenchmarkSpec, q: Optional[TickerQuote]) -> Optional[str]:
    if not q or q.price <= 0:
        return None
    arrow = _fmt_arrow(q.change_percent)
    unit_or_curr = spec.unit or q.currency
    price_str = _fmt_price(q.price, unit_or_curr)
    range_tag = ""
    if q.day_low and q.day_high and q.day_high > q.day_low:
        range_tag = f" | Day: {q.day_low:,.2f}–{q.day_high:,.2f}"
    return f"- {arrow} **{spec.label} (`{spec.symbol}`)**: **{price_str}** ({q.change:+.2f} / **{q.change_percent:+.2f}%**){range_tag}"


def _format_single_ticker_snapshot(
    primary: TickerQuote,
    dual: Optional[TickerQuote],
    headlines: List[str],
    history: Optional[HistoricalSummary] = None,
) -> str:
    """Format a comprehensive technical, fundamental, historical, and news catalyst snapshot for a single ticker."""
    arrow = _fmt_arrow(primary.change_percent)
    lines = [
        f"=== 📊 TICKER INTELLIGENCE SNAPSHOT: {primary.name} ({primary.symbol}) ===",
        f"- **Live Price:** {arrow} **{_fmt_price(primary.price, primary.currency)}** "
        f"({_fmt_signed(primary.change)} / **{primary.change_percent:+.2f}%**) "
        f"[{primary.exchange or 'Exchange'} • State: {primary.market_state}]",
    ]

    if dual and dual.price > 0:
        d_arrow = _fmt_arrow(dual.change_percent)
        lines.append(
            f"- **Dual-Listed Quote (`{dual.symbol}` — {dual.exchange or dual.currency}):** "
            f"{d_arrow} **{_fmt_price(dual.price, dual.currency)}** ({_fmt_signed(dual.change)} / **{dual.change_percent:+.2f}%**)"
        )

    if primary.pre_market_price and primary.pre_market_change_pct is not None:
        lines.append(
            f"- **Pre-Market:** {_fmt_price(primary.pre_market_price, primary.currency)} ({primary.pre_market_change_pct:+.2f}%)"
        )
    if primary.post_market_price and primary.post_market_change_pct is not None:
        lines.append(
            f"- **After-Hours:** {_fmt_price(primary.post_market_price, primary.currency)} ({primary.post_market_change_pct:+.2f}%)"
        )

    # Multi-Horizon Performance Strip (1W, 1M, 3M, 6M, YTD, 1Y, 3Y, 5Y)
    if history and history.horizon_returns:
        h_parts = [
            f"**{lbl}:** {ret:+.2f}%"
            for lbl in ("1W", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y")
            if (ret := history.horizon_returns.get(lbl)) is not None
        ]
        if h_parts:
            lines.append("- **Multi-Horizon Returns:** " + " | ".join(h_parts))

    # Technical Posture
    tech_parts: List[str] = []
    if primary.day_low is not None and primary.day_high is not None:
        tech_parts.append(f"Intraday Range: **{_fmt_num(primary.day_low)} – {_fmt_num(primary.day_high)} {primary.currency}**")
    if primary.week52_low is not None and primary.week52_high is not None and primary.week52_high > primary.week52_low:
        pct_52w = (primary.price - primary.week52_low) / (primary.week52_high - primary.week52_low) * 100.0
        pct_52w = max(0.0, min(100.0, pct_52w))
        tech_parts.append(
            f"52-Week Range: **{_fmt_num(primary.week52_low)} – {_fmt_num(primary.week52_high)}** (Trading at **{pct_52w:.0f}%** of 52w range)"
        )
    if tech_parts:
        lines.append("- **Range Position:** " + " | ".join(tech_parts))

    ma_parts: List[str] = []
    if primary.ma50 and primary.ma50 > 0:
        diff50 = (primary.price - primary.ma50) / primary.ma50 * 100.0
        ma_parts.append(f"50-Day MA: **{_fmt_num(primary.ma50)}** ({diff50:+.1f}%)")
    if primary.ma200 and primary.ma200 > 0:
        diff200 = (primary.price - primary.ma200) / primary.ma200 * 100.0
        ma_parts.append(f"200-Day MA: **{_fmt_num(primary.ma200)}** ({diff200:+.1f}%)")
    if primary.ma50 and primary.ma200 and primary.ma50 > 0 and primary.ma200 > 0:
        structure = "Bullish (Price > 50d & 200d MA)" if (primary.price > primary.ma50 and primary.price > primary.ma200) else (
            "Bearish / Pullback (Below Key MAs)" if (primary.price < primary.ma50 and primary.price < primary.ma200) else "Consolidating Between MAs"
        )
        ma_parts.append(f"Trend Structure: **{structure}**")
    if ma_parts:
        lines.append("- **Moving Averages & Trend:** " + " | ".join(ma_parts))

    if primary.volume is not None and primary.volume > 0:
        vol_str = f"{primary.volume:,}"
        if primary.avg_volume_3m and primary.avg_volume_3m > 0:
            rel_vol = primary.volume / primary.avg_volume_3m
            vol_str += f" (**{rel_vol:.2f}x** 3M avg volume of {primary.avg_volume_3m:,})"
        lines.append(f"- **Volume Activity:** {vol_str}")

    # Valuation & Fundamentals
    fund_parts: List[str] = []
    if primary.market_cap:
        fund_parts.append(f"Market Cap: **{_fmt_large_number(primary.market_cap, '$' if primary.currency in ('USD', 'CAD') else '')} {primary.currency}**")
    if primary.trailing_pe is not None:
        fund_parts.append(f"Trailing P/E: **{primary.trailing_pe:.2f}x**")
    if primary.forward_pe is not None:
        fund_parts.append(f"Forward P/E: **{primary.forward_pe:.2f}x**")
    if primary.eps_ttm is not None:
        fund_parts.append(f"EPS (TTM): **{primary.eps_ttm:.2f}**")
    if primary.price_to_book is not None:
        fund_parts.append(f"P/B: **{primary.price_to_book:.2f}x**")
    if primary.beta is not None:
        fund_parts.append(f"Beta: **{primary.beta:.2f}**")
    if primary.dividend_yield is not None and primary.dividend_yield > 0:
        fund_parts.append(f"Dividend Yield: **{primary.dividend_yield:.2f}%**")
    if primary.ex_dividend_timestamp and primary.ex_dividend_timestamp > 0:
        ex_dt = datetime.fromtimestamp(primary.ex_dividend_timestamp, tz=timezone.utc).strftime("%b %d, %Y")
        fund_parts.append(f"Ex-Dividend Date: **{ex_dt}**")
    if primary.target_mean_price and primary.target_mean_price > 0 and primary.price > 0:
        upside = (primary.target_mean_price - primary.price) / primary.price * 100.0
        rating_tag = f" [{primary.analyst_rating}]" if primary.analyst_rating else ""
        fund_parts.append(
            f"Analyst Target: **{_fmt_price(primary.target_mean_price, primary.currency)} ({upside:+.1f}%)**{rating_tag}"
        )
    elif primary.analyst_rating:
        fund_parts.append(f"Analyst Rating: **{primary.analyst_rating}**")
    if primary.earnings_timestamp and primary.earnings_timestamp > 0:
        earn_dt_obj = datetime.fromtimestamp(primary.earnings_timestamp, tz=timezone.utc)
        earn_dt = earn_dt_obj.strftime("%b %d, %Y")
        now_utc = datetime.now(timezone.utc)
        earn_lbl = "Next Earnings" if earn_dt_obj.date() >= now_utc.date() else "Last/Recent Earnings"
        fund_parts.append(f"{earn_lbl}: **{earn_dt}** (Earnings Date: **{earn_dt}**)")
    if fund_parts:
        lines.append("- **Valuation & Fundamentals:** " + " | ".join(fund_parts))

    # Deep Historical Window Analysis
    if history and history.start_price > 0 and history.end_price > 0:
        h_arrow = _fmt_arrow(history.pct_change)
        cagr_str = f" | **Annualized CAGR: {history.cagr_pct:+.2f}%/yr**" if history.cagr_pct is not None else ""
        tot_ret_str = ""
        if history.total_return_pct is not None:
            tot_cagr_tag = (
                f" / **{history.total_return_cagr_pct:+.2f}%/yr CAGR**"
                if history.total_return_cagr_pct is not None
                else ""
            )
            tot_ret_str = f" | **Total Return (Div-Adjusted): {history.total_return_pct:+.2f}%**{tot_cagr_tag}"
        vol_str = (
            f" | **Annualized Volatility:** {history.annualized_volatility_pct:.1f}%"
            if history.annualized_volatility_pct is not None
            else ""
        )
        lines.append(
            f"\n=== 📅 HISTORICAL PERFORMANCE & TREND: {history.timeframe_label} ({history.start_date} → {history.end_date}) ==="
        )
        if history.exact_date_bar:
            eb = history.exact_date_bar
            lines.append(
                f"- **Exact Historical Session ({eb['date']}):** Close: **{_fmt_price(eb['close'], history.currency)}** "
                f"(Open: {_fmt_num(eb['open'])} | High: {_fmt_num(eb['high'])} | Low: {_fmt_num(eb['low'])}) | "
                f"**Return Since {eb['date']}:** **{eb['return_since_pct']:+.2f}%**"
            )
        lines.append(
            f"- **Window Return:** {h_arrow} **{_fmt_price(history.start_price, history.currency)} → "
            f"{_fmt_price(history.end_price, history.currency)}** "
            f"({_fmt_signed(history.abs_change)} / **{history.pct_change:+.2f}%**){cagr_str}{tot_ret_str}"
        )
        lines.append(
            f"- **Period Extremes & Risk:** **Period High:** {_fmt_num(history.period_high)} ({history.period_high_date}) | "
            f"**Period Low:** {_fmt_num(history.period_low)} ({history.period_low_date}) | "
            f"**Max Drawdown:** {history.max_drawdown_pct:.2f}%{vol_str}"
        )
        corp_parts: List[str] = []
        if history.dividends_count > 0 and history.dividends_paid > 0:
            corp_parts.append(
                f"**Dividends Paid in Window:** {history.dividends_paid:.2f} {history.currency}/sh across {history.dividends_count} distributions"
            )
        if history.splits:
            corp_parts.append(f"**Stock Splits:** {', '.join(history.splits)}")
        if corp_parts:
            lines.append("- **Corporate Actions (Dividends & Splits):** " + " | ".join(corp_parts))
        if history.checkpoints:
            lines.append("- **Chronological Price Trajectory:** " + " → ".join(history.checkpoints))

    if headlines:
        lines.append("\n=== 📰 RECENT CATALYST HEADLINES FOR THIS TICKER ===")
        for idx, h in enumerate(headlines, start=1):
            lines.append(f"{idx}. {h}")

    lines.append(build_markets_llm_instructions(is_ticker_view=True))
    return "\n".join(lines)


def _format_multi_ticker_comparison(
    items: List[Tuple[TickerQuote, Optional[TickerQuote], Optional[HistoricalSummary], List[str]]],
    timeframe_label: str,
) -> str:
    """Format a side-by-side institutional head-to-head comparison across 2 to 5 tickers/assets."""
    tf_title = items[0][2].timeframe_label if (items and items[0][2]) else timeframe_label
    lines = [
        f"=== ⚖️ MULTI-ASSET HEAD-TO-HEAD COMPARISON ({tf_title}) ===",
    ]

    # Rank by historical window return if available, else intraday % change
    ranked = sorted(
        items,
        key=lambda x: x[2].pct_change if (x[2] and x[2].start_price > 0) else x[0].change_percent,
        reverse=True,
    )
    if len(ranked) >= 2:
        top_q, _, top_h, _ = ranked[0]
        sec_q, _, sec_h, _ = ranked[1]
        top_ret = top_h.pct_change if (top_h and top_h.start_price > 0) else top_q.change_percent
        sec_ret = sec_h.pct_change if (sec_h and sec_h.start_price > 0) else sec_q.change_percent
        spread = top_ret - sec_ret
        lines.append(
            f"- 🏆 **Relative Outperformer ({tf_title}):** **{top_q.name} (`{top_q.symbol}`)** at **{top_ret:+.2f}%**, "
            f"outperforming **{sec_q.name} (`{sec_q.symbol}`)** ({sec_ret:+.2f}%) by **{spread:+.2f}%**."
        )

    for idx, (primary, dual, history, headlines) in enumerate(ranked, start=1):
        arrow = _fmt_arrow(primary.change_percent)
        dual_tag = (
            f" | Dual-Listed `{dual.symbol}`: {_fmt_price(dual.price, dual.currency)} ({dual.change_percent:+.2f}%)"
            if (dual and dual.price > 0)
            else ""
        )
        lines.append(
            f"\n=== #{idx}. {primary.name} (`{primary.symbol}`) ==="
        )
        lines.append(
            f"- **Live Price:** {arrow} **{_fmt_price(primary.price, primary.currency)}** "
            f"({_fmt_signed(primary.change)} / **{primary.change_percent:+.2f}%** today){dual_tag}"
        )
        if history and history.start_price > 0:
            h_arrow = _fmt_arrow(history.pct_change)
            cagr_tag = f" | **CAGR:** {history.cagr_pct:+.2f}%/yr" if history.cagr_pct is not None else ""
            tot_tag = (
                f" | **Total Return (w/ Divs):** {history.total_return_pct:+.2f}%"
                if history.total_return_pct is not None
                else ""
            )
            vol_tag = (
                f" | **Volatility:** {history.annualized_volatility_pct:.1f}%"
                if history.annualized_volatility_pct is not None
                else ""
            )
            lines.append(
                f"- **{history.timeframe_label} Window ({history.start_date} → {history.end_date}):** "
                f"{h_arrow} **{_fmt_price(history.start_price, history.currency)} → {_fmt_price(history.end_price, history.currency)}** "
                f"(**{history.pct_change:+.2f}%**){cagr_tag}{tot_tag}"
            )
            lines.append(
                f"- **Risk & Range:** Period High: {_fmt_num(history.period_high)} ({history.period_high_date}) | "
                f"Period Low: {_fmt_num(history.period_low)} ({history.period_low_date}) | "
                f"**Max Drawdown:** {history.max_drawdown_pct:.2f}%{vol_tag}"
            )
            if history.horizon_returns:
                h_strip = " | ".join(
                    f"**{lbl}:** {ret:+.2f}%"
                    for lbl in ("1W", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y")
                    if (ret := history.horizon_returns.get(lbl)) is not None
                )
                if h_strip:
                    lines.append(f"- **Multi-Horizon Strip:** {h_strip}")

        f_parts: List[str] = []
        if primary.market_cap:
            f_parts.append(f"MCap: **{_fmt_large_number(primary.market_cap, '$' if primary.currency in ('USD', 'CAD') else '')} {primary.currency}**")
        if primary.trailing_pe is not None:
            f_parts.append(f"P/E: **{primary.trailing_pe:.2f}x**")
        if primary.forward_pe is not None:
            f_parts.append(f"Fwd P/E: **{primary.forward_pe:.2f}x**")
        if primary.dividend_yield is not None and primary.dividend_yield > 0:
            f_parts.append(f"Div Yield: **{primary.dividend_yield:.2f}%**")
        if primary.beta is not None:
            f_parts.append(f"Beta: **{primary.beta:.2f}**")
        if primary.target_mean_price and primary.price > 0:
            up = (primary.target_mean_price - primary.price) / primary.price * 100.0
            f_parts.append(f"Target: **{_fmt_price(primary.target_mean_price, primary.currency)} ({up:+.1f}%)**")
        if f_parts:
            lines.append("- **Fundamentals:** " + " | ".join(f_parts))
        if headlines:
            lines.append(f"- **Latest Catalyst:** {headlines[0]}")

    lines.append(build_markets_llm_instructions(is_compare_view=True))
    return "\n".join(lines)


def _parse_natural_date(text_frag: str, is_end: bool = False) -> Optional[str]:
    """Parse an ISO date ('2024-10-15'), 'Oct 15, 2024', '15 Oct 2024', or 'March 2022' into 'YYYY-MM-DD'."""
    frag = (text_frag or "").strip().strip(",.")
    if not frag:
        return None
    # 1. ISO YYYY-MM-DD
    m_iso = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", frag)
    if m_iso:
        y, mo, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"

    month_names = "|".join(sorted(MONTH_MAP_SYNC.keys(), key=len, reverse=True))
    # 2. Month DD, YYYY (e.g. Oct 15, 2024 or October 15 2024)
    m_mdy = re.search(rf"\b({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d{{2}})\b", frag, re.IGNORECASE)
    if m_mdy:
        mo = MONTH_MAP_SYNC[m_mdy.group(1).lower()]
        d, y = int(m_mdy.group(2)), int(m_mdy.group(3))
        max_d = calendar.monthrange(y, mo)[1]
        return f"{y:04d}-{mo:02d}-{min(d, max_d):02d}"

    # 3. DD Month YYYY (e.g. 15 Oct 2024)
    m_dmy = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names})\.?,?\s+(20\d{{2}})\b", frag, re.IGNORECASE)
    if m_dmy:
        d = int(m_dmy.group(1))
        mo = MONTH_MAP_SYNC[m_dmy.group(2).lower()]
        y = int(m_dmy.group(3))
        max_d = calendar.monthrange(y, mo)[1]
        return f"{y:04d}-{mo:02d}-{min(d, max_d):02d}"

    # 4. Month YYYY (e.g. March 2022)
    m_my = re.search(rf"\b({month_names})\.?,?\s+(20\d{{2}})\b", frag, re.IGNORECASE)
    if m_my:
        mo = MONTH_MAP_SYNC[m_my.group(1).lower()]
        y = int(m_my.group(2))
        d = calendar.monthrange(y, mo)[1] if is_end else 1
        return f"{y:04d}-{mo:02d}-{d:02d}"

    return None


def _extract_timeframe(query: str, timeframe: str) -> Optional[str]:
    """Extract a canonical historical timeframe token from explicit `timeframe` arg or natural-language `query`."""
    tf_raw = (timeframe or "").strip().lower()
    if tf_raw:
        alias_map = {
            "1d": "1d", "intraday": "1d", "today": "1d", "1 day": "1d", "session": "1d",
            "1w": "5d", "5d": "5d", "7d": "5d", "week": "5d", "1 week": "5d",
            "1m": "1mo", "1mo": "1mo", "month": "1mo", "1 month": "1mo", "30d": "1mo",
            "3m": "3mo", "3mo": "3mo", "3 months": "3mo", "quarter": "3mo", "90d": "3mo",
            "6m": "6mo", "6mo": "6mo", "6 months": "6mo", "half year": "6mo", "180d": "6mo",
            "ytd": "ytd", "year to date": "ytd", "this year": "ytd",
            "1y": "1y", "1yr": "1y", "year": "1y", "1 year": "1y", "12m": "1y",
            "2y": "2y", "2yr": "2y", "2 years": "2y",
            "3y": "3y", "3yr": "3y", "3 years": "3y",
            "5y": "5y", "5yr": "5y", "5 years": "5y",
            "10y": "10y", "10yr": "10y", "10 years": "10y", "decade": "10y",
            "max": "max", "all": "max", "all time": "max", "lifetime": "max",
        }
        if tf_raw in alias_map:
            return alias_map[tf_raw]
        if re.fullmatch(r"20\d{2}", tf_raw):
            return tf_raw
        if tf_raw.startswith(("date:", "range:")):
            return tf_raw
        parsed_tf_date = _parse_natural_date(tf_raw)
        if parsed_tf_date:
            return f"date:{parsed_tf_date}"

    q_low = (query or "").strip().lower()
    if not q_low:
        return None

    # 1. Custom Date-to-Date Range: e.g. 'from March 2022 to October 2023', 'between 2022-03-01 and 2023-10-15'
    m_range = re.search(
        r"\b(?:from|between)\s+([a-z0-9,\s\-]+?)\s+(?:to|and|until|till|through|thru)\s+([a-z0-9,\s\-]+)",
        q_low,
    )
    if m_range:
        d1 = _parse_natural_date(m_range.group(1), is_end=False)
        d2 = _parse_natural_date(m_range.group(2), is_end=True)
        if d1 and d2:
            return f"range:{d1}:{d2}"

    # 2. 'since <Date/Month Year>': e.g. 'since March 2023' or 'since 2023-06-01'
    m_since = re.search(r"\bsince\s+([a-z0-9,\s\-]+)", q_low)
    if m_since:
        d_since = _parse_natural_date(m_since.group(1), is_end=False)
        if d_since:
            return f"date:{d_since}"

    # 3. Single Historical Date Lookup: e.g. 'on Oct 15, 2024', 'on 2024-10-15'
    d_single = _parse_natural_date(q_low, is_end=False)
    if d_single and re.search(r"\b(?:on|at|close|closed|closing|open|opened|price|trading|was|were)\b|\b20\d{2}-\d{2}-\d{2}\b", q_low):
        # Make sure it had a specific day or explicit 'on <Month Year>'
        if re.search(r"\b\d{1,2}(?:st|nd|rd|th)?,?\s+20\d{2}\b|\b20\d{2}-\d{1,2}-\d{1,2}\b|\bon\s+", q_low):
            return f"date:{d_single}"

    # 4. Previous Calendar Year ('last calendar year', 'previous year')
    if re.search(r"\b(?:last|previous)\s+calendar\s+year\b|\bprevious\s+year\b", q_low):
        return str(datetime.now(timezone.utc).year - 1)

    patterns = [
        (r"\b(all[- ]time|lifetime|max\s+history)\b", "max"),
        (r"\b(10\s*y(?:ears?|rs?)?|ten\s+years?|past\s+decade|last\s+decade)\b", "10y"),
        (r"\b(5\s*y(?:ears?|rs?)?|five\s+years?)\b", "5y"),
        (r"\b(3\s*y(?:ears?|rs?)?|three\s+years?)\b", "3y"),
        (r"\b(2\s*y(?:ears?|rs?)?|two\s+years?)\b", "2y"),
        (r"\b(ytd|year[- ]to[- ]date|so\s+far\s+this\s+year|this\s+year)\b", "ytd"),
        (r"\b(1\s*y(?:ear|r)?|one\s+year|12\s*months?|twelve\s+months?|past\s+year|last\s+year)\b", "1y"),
        (r"\b(6\s*m(?:onths?|o)?|six\s+months?|half\s+a?\s*year)\b", "6mo"),
        (r"\b(3\s*m(?:onths?|o)?|three\s+months?|past\s+quarter|last\s+quarter)\b", "3mo"),
        (r"\b(1\s*m(?:onth|o)?|one\s+month|30\s*days?|thirty\s+days?|past\s+month|last\s+month)\b", "1mo"),
        (r"\b(1\s*w(?:eek|k)?|5\s*d(?:ays?)?|7\s*d(?:ays?)?|seven\s+days?|past\s+week|last\s+week)\b", "5d"),
        (r"\b(intraday|1\s*d(?:ay)?|today'?s\s+(?:session|chart|intraday))\b", "1d"),
        (r"\b(?:in|during|for)\s+(20\d{2})\b", None),
        (r"(?<!russell\s)\b(20\d{2})\b", None),
    ]
    for pat, code in patterns:
        m = re.search(pat, q_low)
        if m:
            return code if code is not None else m.group(1)

    if any(w in q_low for w in ("historical", "history", "chart")):
        return "1y"
    return None


def _extract_comparison_tickers(query: str, ticker: str) -> List[str]:
    """Extract 2 to 5 distinct ticker/company candidates when the user requests a multi-asset comparison."""
    candidates: List[str] = []

    def _add_unique(sym_or_name: str) -> None:
        cleaned = sym_or_name.strip().strip("$")
        if not cleaned:
            return
        # Normalize known alias to avoid duplicate entries (e.g. 'Shopify' and 'SHOP.TO')
        low = cleaned.lower()
        norm_key = (
            CANADIAN_DUAL_LISTED_MAP[low][0]
            if low in CANADIAN_DUAL_LISTED_MAP
            else COMMON_TICKER_ALIASES.get(low, cleaned.upper())
        )
        for existing in candidates:
            ex_low = existing.lower()
            ex_key = (
                CANADIAN_DUAL_LISTED_MAP[ex_low][0]
                if ex_low in CANADIAN_DUAL_LISTED_MAP
                else COMMON_TICKER_ALIASES.get(ex_low, existing.upper())
            )
            if ex_key == norm_key:
                return
        candidates.append(cleaned)

    # 1. Explicit multi-ticker string in `ticker` argument (e.g. "SHOP.TO, NVDA" or "AAPL vs MSFT")
    if ticker and re.search(r"[,/]|\b(?:vs\.?|versus|against|and)\b", ticker, re.IGNORECASE):
        parts = re.split(r"\s*(?:,|/|\bvs\.?\b|\bversus\b|\bagainst\b|\band\b)\s*", ticker, flags=re.IGNORECASE)
        for p in parts:
            cand = _extract_candidate_ticker_query(p, "", has_timeframe=True)
            if cand:
                _add_unique(cand)
        if len(candidates) >= 2:
            return candidates[:5]

    q_clean = (query or "").strip()
    if not q_clean:
        return []
    q_low = q_clean.lower()

    has_compare_cue = bool(
        re.search(r"\b(?:compare|comparing|comparison|vs\.?|versus|against|outperform(?:ed|ing)?)\b", q_low)
    )
    cashtag_matches = re.findall(r"(?<![A-Za-z0-9])\$([A-Z]{1,5}(?:\.TO)?)\b", q_clean)

    # 2. Check comparison queries (or multi-cashtag queries) in exact left-to-right appearance order
    if has_compare_cue or len(cashtag_matches) >= 2:
        alias_pool = sorted(set(CANADIAN_DUAL_LISTED_MAP) | set(COMMON_TICKER_ALIASES), key=len, reverse=True)
        matched_spans: List[Tuple[int, int, str]] = []
        for name in alias_pool:
            if len(name) < 2:
                continue
            for m in re.finditer(rf"\b{re.escape(name)}\b", q_low):
                s_idx, e_idx = m.start(), m.end()
                if not any(not (e_idx <= ms or s_idx >= me) for ms, me, _ in matched_spans):
                    matched_spans.append((s_idx, e_idx, name))

        for m_tok in re.finditer(r"\$?([A-Z]{2,5}(?:-[A-Z])?(?:\.[A-Z]{1,2})?)\b", q_clean):
            s_idx, e_idx = m_tok.start(), m_tok.end()
            tok = m_tok.group(1)
            if (
                tok.lower() not in MARKET_QUERY_STOPWORDS
                and tok not in ("USD", "CAD", "EUR", "GBP", "JPY", "YTD", "MAX", "TSX", "CEO", "IPO", "ETF", "GDP", "CPI")
                and not any(not (e_idx <= ms or s_idx >= me) for ms, me, _ in matched_spans)
            ):
                matched_spans.append((s_idx, e_idx, tok))

        matched_spans.sort(key=lambda x: x[0])
        for _, _, name in matched_spans:
            _add_unique(name)

        # Fallback split on 'vs' / 'versus' / 'against' for unknown lowercase company/ticker names
        if len(candidates) < 2 and re.search(r"\b(?:vs\.?|versus|against)\b", q_low):
            parts = re.split(r"\b(?:vs\.?|versus|against)\b", q_clean, flags=re.IGNORECASE)
            for p in parts:
                cand = _extract_candidate_ticker_query(p, "", has_timeframe=True)
                if cand:
                    _add_unique(cand)

    if len(candidates) >= 2:
        return candidates[:5]
    return []


def _extract_candidate_ticker_query(
    query: str, ticker: str, has_timeframe: bool = False
) -> Optional[str]:
    """Determine if the user is asking for a single specific company/ticker/crypto snapshot rather than a broad market overview."""
    if ticker and ticker.strip():
        return ticker.strip().lstrip("$")

    q_clean = (query or "").strip()
    if not q_clean:
        return None
    q_low = q_clean.lower()

    if q_low in ("crypto", "cryptocurrency", "cryptocurrencies", "coins", "tokens"):
        return None

    broad_phrases = (
        "market", "markets", "indices", "indexes", "trends", "movers", "gainers",
        "losers", "going up", "going down", "commodities", "precious metals",
        "metals", "cryptocurrencies", "crypto market", "crypto markets", "top crypto",
        "overview", "summary", "pulse", "world", "europe", "european", "asia", "asian",
        "canadian market", "us market", "american market", "stock market",
        "what's up", "whats up", "what is up", "top stocks",
    )
    excluded_broad_aliases = (
        ()
        if has_timeframe
        else ("gold", "silver", "oil", "crude", "tsx", "nasdaq", "dow", "s&p 500", "sp500", "ftse", "dax", "nikkei", "hang seng")
    )
    if any(p in q_low for p in broad_phrases):
        for name in sorted(set(CANADIAN_DUAL_LISTED_MAP) | set(COMMON_TICKER_ALIASES), key=len, reverse=True):
            if len(name) >= 3 and name not in excluded_broad_aliases:
                if re.search(rf"\b{re.escape(name)}\b", q_low):
                    return name
        return None

    # Check exact or token match in our Canadian dual-listed map or common ticker aliases
    for name in sorted(set(CANADIAN_DUAL_LISTED_MAP) | set(COMMON_TICKER_ALIASES), key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", q_low):
            return name

    # If the user supplied an explicit parenthetical ticker like "raditan (RDX)", prefer the ticker symbol
    paren_sym = re.search(r"\(([A-Za-z]{2,6})\)", q_clean)
    if paren_sym:
        return paren_sym.group(1).strip()

    # Strip timeframe phrases, date ranges, and generic market query words to see if a ticker symbol or company/coin name remains
    month_names = "|".join(MONTH_MAP_SYNC.keys())
    q_stripped = re.sub(
        rf"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|thirty|ninety|hundred)\s*"
        rf"(?:years?|yrs?|months?|mos?|weeks?|wks?|days?)\b|\b20\d{{2}}(?:-\d{{1,2}}-\d{{1,2}})?\b|\b(?:{month_names})\b",
        " ",
        q_clean,
        flags=re.IGNORECASE,
    )
    tokens = [
        t.lstrip("$") for t in re.findall(r"\$?[A-Za-z0-9.^=-]+", q_stripped)
        if t.lower().strip("$.-") not in MARKET_QUERY_STOPWORDS and not t.strip("$.-").isdigit()
    ]
    if 1 <= len(tokens) <= 3:
        candidate = " ".join(tokens).strip()
        if candidate.lower() not in ("canada", "usa", "europe", "asia", "global", "stocks", "commodities", "crypto"):
            return candidate
    return None


def get_daily_market_pulse() -> str:
    """
    Fast (<0.4s) compact Global Market Pulse & Macro Signals block designed to be attached
    automatically to general/world news queries so the bot synthesizes world events and market reactions together.
    """
    all_bench_and_tsx = list(
        dict.fromkeys(
            [b.symbol for b in GLOBAL_INDICES]
            + [b.symbol for b in COMMODITY_BENCHMARKS]
            + [b.symbol for b in FOREX_BENCHMARKS]
            + list(TSX_60_LEADERS)
        )
    )
    fut_quotes = submit_to_worker(_yahoo_agg.fetch_quotes, all_bench_and_tsx)
    fut_crypto = submit_to_worker(_crypto_agg.fetch_top_coins)
    fut_us_movers = submit_to_worker(_yahoo_agg.fetch_us_movers, 3)

    quotes = fut_quotes.result()
    usdcad_q = quotes.get("USDCAD=X")
    usdcad_rate = usdcad_q.price if (usdcad_q and usdcad_q.price > 0) else 1.41
    from src.skills.markets.aggregators.crypto import _apply_cad_rate

    crypto_coins = _apply_cad_rate(fut_crypto.result(), usdcad_rate)
    tsx_movers = _yahoo_agg.fetch_tsx_movers(3, prefetched_quotes=quotes)
    us_movers = fut_us_movers.result()

    if not quotes and not crypto_coins:
        return ""

    lines = ["\n=== 📈 DAILY GLOBAL MARKET PULSE & MACRO SIGNALS ==="]
    signals = _compute_macro_regime_signals(quotes, crypto_coins)
    if signals:
        lines.extend(signals)

    # Compact Regional Index Board (Canada, US, Europe, Asia-Pacific)
    pulse_regions = (
        ("🇨🇦 Canada & FX", ("^GSPTSE", "CADUSD=X")),
        ("🇺🇸 United States", ("^GSPC", "^IXIC", "^DJI", "^VIX", "^TNX")),
        ("🇪🇺 Europe", ("^STOXX50E", "^FTSE", "^GDAXI", "^FCHI")),
        ("🌏 Asia-Pacific", ("^N225", "^HSI", "000001.SS", "^KS11")),
    )
    spec_by_sym = {b.symbol.upper(): b for b in (*GLOBAL_INDICES, *FOREX_BENCHMARKS, *COMMODITY_BENCHMARKS)}
    for reg_title, sym_tuple in pulse_regions:
        items: List[str] = []
        for sym in sym_tuple:
            sp = spec_by_sym.get(sym.upper())
            q = quotes.get(sym.upper())
            if sp and q and q.price > 0:
                p_fmt = f"{q.price:.4f}" if sym == "CADUSD=X" else f"{q.price:,.2f}"
                items.append(f"**{sp.label}**: {p_fmt} ({q.change_percent:+.2f}%)")
        if items:
            lines.append(f"- **{reg_title}:** " + " | ".join(items))

    # Commodities & Precious Metals
    comm_items: List[str] = []
    for sym in ("GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F"):
        sp = spec_by_sym.get(sym)
        q = quotes.get(sym)
        if sp and q and q.price > 0:
            short_lbl = sp.label.replace(" Futures (Spot Proxy)", "").replace(" Futures", "")
            comm_items.append(f"**{short_lbl}**: ${q.price:,.2f} ({q.change_percent:+.2f}%)")
    if comm_items:
        lines.append("- **🪙 Precious Metals & Commodities:** " + " | ".join(comm_items))

    # Top Movers
    if tsx_movers.get("gainers") or tsx_movers.get("losers"):
        g_str = ", ".join(f"{q.symbol} ({q.change_percent:+.1f}%)" for q in tsx_movers.get("gainers", [])[:3])
        l_str = ", ".join(f"{q.symbol} ({q.change_percent:+.1f}%)" for q in tsx_movers.get("losers", [])[:3])
        lines.append(f"- **🇨🇦 TSX Key Movers:** Up: {g_str} | Down: {l_str}")
    if us_movers.get("gainers") or us_movers.get("losers"):
        g_str = ", ".join(f"{q.symbol} ({q.change_percent:+.1f}%)" for q in us_movers.get("gainers", [])[:3])
        l_str = ", ".join(f"{q.symbol} ({q.change_percent:+.1f}%)" for q in us_movers.get("losers", [])[:3])
        lines.append(f"- **🇺🇸 US Key Movers:** Up: {g_str} | Down: {l_str}")

    lines.append(build_news_market_pulse_instructions())
    return "\n".join(lines)


def _fetch_single_asset_bundle(
    target: str,
    prefer_ca: bool,
    timeframe_code: str,
    prefer_crypto: bool = False,
) -> Optional[Tuple[TickerQuote, Optional[TickerQuote], Optional[HistoricalSummary], List[str]]]:
    """Resolve and fetch `(primary_quote, dual_quote, historical_summary, headlines)` for one target asset."""
    if prefer_crypto:
        primary_sym, dual_sym, headlines = _yahoo_agg.resolve_ticker_and_news(
            target, prefer_canada=prefer_ca, prefer_crypto=True
        )
    else:
        primary_sym, dual_sym, headlines = _yahoo_agg.resolve_ticker_and_news(
            target, prefer_canada=prefer_ca
        )
    if not primary_sym:
        return None
    is_crypto_pair = primary_sym.upper().endswith("-USD")
    syms_to_fetch = [primary_sym] + ([dual_sym] if dual_sym else [])
    if is_crypto_pair:
        syms_to_fetch.append("USDCAD=X")
    fut_fetched = submit_to_worker(_yahoo_agg.fetch_quotes, syms_to_fetch)
    fut_history = submit_to_worker(
        _yahoo_agg.fetch_historical_summary,
        primary_sym,
        timeframe_code,
    )
    fetched = fut_fetched.result()
    history_summary = fut_history.result()
    primary_q = fetched.get(primary_sym.upper())
    dual_q = fetched.get(dual_sym.upper()) if dual_sym else None
    if primary_q and primary_q.price > 0:
        if is_crypto_pair and (not dual_q or dual_q.price <= 0):
            usdcad_q = fetched.get("USDCAD=X")
            fx_rate = usdcad_q.price if (usdcad_q and usdcad_q.price > 0) else 1.41
            cad_sym = dual_sym or (primary_sym.removesuffix("-USD") + "-CAD")
            dual_q = TickerQuote(
                symbol=cad_sym.upper(),
                name=primary_q.name.replace("USD", "CAD"),
                exchange=primary_q.exchange or "CCC",
                currency="CAD",
                market_state=primary_q.market_state,
                price=primary_q.price * fx_rate,
                change=primary_q.change * fx_rate,
                change_percent=primary_q.change_percent,
            )
        return (primary_q, dual_q, history_summary, headlines)
    return None


def skill_markets_info(
    query: Optional[str] = None,
    ticker: Optional[str] = None,
    region: Optional[str] = None,
    view: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> str:
    """
    Fetch real-time Central Bank Rate Decisions & Economic Calendar (US Fed/FOMC, Bank of Canada/BoC, ECB, BoE, BoJ, CPI, Jobs/NFP, GDP, PCE with Local MDT/MST & ET timestamps), global stock indices (Canada TSX, US S&P 500/NASDAQ/Dow/VIX, Europe, Asia), daily market movers, precious metals & commodities (Gold, Silver, Oil, NatGas, Copper), FX rates, crypto (BTC, ETH, SOL, 24h movers), multi-asset comparisons, and single-ticker fundamentals & historical returns.

    Args:
        query: What to look up (e.g. 'did Bank of Canada raise rates today?', 'what time is the Federal Reserve rate decision?', 'market overview', 'compare SHOP.TO vs NVDA over 5 years').
        ticker: Specific stock, ETF, index, commodity, crypto, or comma/vs-separated comparison list (e.g. 'SHOP.TO', 'NVDA, AMD', 'GC=F', 'BTC-USD').
        region: Optional regional focus: 'global' (default), 'canada', 'us', 'europe', 'asia'.
        view: Optional view mode: 'overview' (default), 'calendar' (central bank rate decisions & economic calendar), 'ticker', 'history', 'compare', 'movers', 'commodities', 'crypto', or 'pulse'.
        timeframe: Optional historical window: '1d', '5d', '1mo', '3mo', '6mo', 'ytd', '1y', '2y', '3y', '5y', '10y', 'max', a year ('2024'), or date range.
    """
    q_raw = (query or "").strip()
    t_raw = (ticker or "").strip()
    r_raw = (region or "").strip().lower()
    v_raw = (view or "").strip().lower()
    tf_raw = (timeframe or "").strip()
    detected_tf = _extract_timeframe(q_raw, tf_raw)

    # Check active channel market context for follow-up queries ("what about over 5 years?", "compare it with NVDA")
    channel_state = get_channel_market_state()
    if channel_state and channel_state.last_tickers:
        if t_raw.lower() in ("it", "its", "them", "they", "same"):
            t_raw = ", ".join(channel_state.last_tickers)
        elif re.search(r"\b(?:it|them)\b", q_raw, re.IGNORECASE) and re.search(
            r"\b(?:vs\.?|versus|against|compare|with)\b", q_raw, re.IGNORECASE
        ):
            q_raw = re.sub(r"\b(?:it|them)\b", channel_state.last_tickers[0], q_raw, flags=re.IGNORECASE)

    print(
        f"📈 [Executing Markets Skill] Query: '{q_raw}', Ticker: '{t_raw}', "
        f"Region: '{r_raw}', View: '{v_raw}', Timeframe: '{detected_tf or ''}'"
    )

    if v_raw == MarketView.PULSE:
        return get_daily_market_pulse()

    # 0. Check if this is a Central Bank Rate Decision or Economic Calendar request
    if is_macro_calendar_query(q_raw, v_raw) and not t_raw:
        rate_bench_syms = ["^TNX", "^IRX", "CADUSD=X", "USDCAD=X", "^GSPTSE", "^GSPC", "GC=F"]
        fut_rate_quotes = submit_to_worker(_yahoo_agg.fetch_quotes, rate_bench_syms)
        fut_macro_news = submit_to_worker(_yahoo_agg.fetch_macro_market_headlines, 5)
        rate_quotes = fut_rate_quotes.result()
        macro_news = fut_macro_news.result()

        report = _macro_agg.format_macro_calendar_report(
            query=q_raw,
            region=r_raw,
            live_rate_quotes=rate_quotes,
        )
        out_parts = [report]
        if macro_news:
            h_lines = ["=== 📰 LATEST CENTRAL BANK & MACRO HEADLINES ==="]
            for idx, h in enumerate(macro_news, start=1):
                h_lines.append(f"{idx}. {h}")
            out_parts.append("\n".join(h_lines))
        out_parts.append(build_macro_calendar_llm_instructions())
        update_channel_market_state(
            region=r_raw or None,
            view=MarketView.CALENDAR,
        )
        return "\n\n".join(out_parts)

    # 1. Check if this is a Multi-Ticker Comparison request (2 to 5 tickers)
    comparison_targets = (
        _extract_comparison_tickers(q_raw, t_raw)
        if v_raw not in (MarketView.MOVERS, MarketView.COMMODITIES, MarketView.CRYPTO, MarketView.OVERVIEW)
        else []
    )
    if not comparison_targets and v_raw == MarketView.COMPARE and channel_state and len(channel_state.last_tickers) >= 2:
        comparison_targets = list(channel_state.last_tickers)

    if len(comparison_targets) >= 2:
        tf_code = detected_tf or (channel_state.last_timeframe if channel_state else None) or "1y"
        prefer_ca = r_raw != MarketRegion.US
        futs = [
            submit_to_worker(
                _fetch_single_asset_bundle,
                tgt,
                prefer_ca and not tgt.upper().endswith((".US", ":US")),
                tf_code,
            )
            for tgt in comparison_targets[:5]
        ]
        bundles = [b for f in futs if (b := f.result()) is not None]
        if len(bundles) >= 2:
            update_channel_market_state(
                tickers=[b[0].symbol for b in bundles],
                timeframe=tf_code,
                region=r_raw or None,
                view=MarketView.COMPARE,
            )
            return _format_multi_ticker_comparison(bundles, timeframe_label=tf_code.upper())

    # 2. Check if this is a Single-Ticker or Historical Asset Snapshot request
    has_tf = bool(detected_tf) or v_raw in (MarketView.HISTORY, MarketView.TICKER)
    candidate_ticker = (
        _extract_candidate_ticker_query(q_raw, t_raw, has_timeframe=has_tf)
        if v_raw not in (MarketView.MOVERS, MarketView.COMMODITIES, MarketView.OVERVIEW)
        else (t_raw or None)
    )
    # Follow-up timeframe fallback: if the user asked "what about over 5 years?" with no ticker, use channel's last ticker
    if not candidate_ticker and has_tf and channel_state and channel_state.last_tickers:
        if len(channel_state.last_tickers) >= 2:
            tf_code = detected_tf or "1y"
            prefer_ca = r_raw != MarketRegion.US
            futs = [
                submit_to_worker(_fetch_single_asset_bundle, sym, prefer_ca, tf_code)
                for sym in channel_state.last_tickers[:5]
            ]
            bundles = [b for f in futs if (b := f.result()) is not None]
            if len(bundles) >= 2:
                update_channel_market_state(
                    tickers=[b[0].symbol for b in bundles],
                    timeframe=tf_code,
                    view=MarketView.COMPARE,
                )
                return _format_multi_ticker_comparison(bundles, timeframe_label=tf_code.upper())
        candidate_ticker = channel_state.last_tickers[0]

    if candidate_ticker or v_raw in (MarketView.TICKER, MarketView.HISTORY):
        target = candidate_ticker or t_raw or q_raw
        tf_code = detected_tf or (channel_state.last_timeframe if (channel_state and not detected_tf and v_raw == MarketView.HISTORY) else None) or "1y"
        prefer_ca = r_raw != MarketRegion.US and not target.upper().endswith((".US", ":US"))
        prefer_crypto = (
            v_raw == MarketView.CRYPTO
            or bool(re.search(r"\b(crypto|cryptocurrency|crpyro|coin|token)\b", f"{q_raw} {t_raw}", re.IGNORECASE))
        )
        bundle = _fetch_single_asset_bundle(
            target,
            prefer_ca=prefer_ca,
            timeframe_code=tf_code,
            prefer_crypto=prefer_crypto,
        )
        if bundle:
            primary_q, dual_q, history_summary, headlines = bundle
            update_channel_market_state(
                tickers=[primary_q.symbol],
                timeframe=detected_tf or "1y",
                region=r_raw or None,
                view=MarketView.HISTORY if detected_tf else MarketView.TICKER,
            )
            return _format_single_ticker_snapshot(primary_q, dual_q, headlines, history=history_summary)

    # 3. Otherwise, run a parallel Global Markets, Movers, Commodities & Crypto fetch (honoring `view` and `region`)
    need_indices = v_raw not in (MarketView.COMMODITIES, MarketView.CRYPTO, MarketView.MOVERS)
    need_commodities = v_raw not in (MarketView.CRYPTO, MarketView.MOVERS)
    need_crypto = v_raw not in (MarketView.COMMODITIES,)
    need_movers = v_raw not in (MarketView.COMMODITIES, MarketView.CRYPTO)
    need_headlines = v_raw not in (MarketView.COMMODITIES, MarketView.CRYPTO, MarketView.MOVERS)

    bench_syms: List[str] = [b.symbol for b in FOREX_BENCHMARKS] + [b.symbol for b in GLOBAL_INDICES] + [b.symbol for b in COMMODITY_BENCHMARKS]
    if need_movers:
        bench_syms = list(dict.fromkeys(bench_syms + list(TSX_60_LEADERS)))

    fut_quotes = submit_to_worker(_yahoo_agg.fetch_quotes, bench_syms)
    fut_crypto = submit_to_worker(_crypto_agg.fetch_top_coins) if need_crypto else None
    fut_us_movers = submit_to_worker(_yahoo_agg.fetch_us_movers, 5) if need_movers else None
    fut_headlines = submit_to_worker(_yahoo_agg.fetch_macro_market_headlines, 5) if need_headlines else None

    quotes = fut_quotes.result()
    usdcad_q = quotes.get("USDCAD=X")
    usdcad_rate = usdcad_q.price if (usdcad_q and usdcad_q.price > 0) else 1.41
    crypto_coins: List[CryptoAssetQuote] = []
    if fut_crypto is not None:
        from src.skills.markets.aggregators.crypto import _apply_cad_rate

        crypto_coins = _apply_cad_rate(fut_crypto.result(), usdcad_rate)
    tsx_movers = _yahoo_agg.fetch_tsx_movers(5, prefetched_quotes=quotes) if need_movers else {"gainers": [], "losers": []}
    us_movers = fut_us_movers.result() if fut_us_movers is not None else {"gainers": [], "losers": [], "actives": []}
    macro_headlines = fut_headlines.result() if fut_headlines is not None else []

    out_sections: List[str] = ["=== 🌍 GLOBAL MARKETS, COMMODITIES & CRYPTO INTELLIGENCE ==="]

    # 1. Pre-computed Macro Regime & Cross-Asset Signals
    signals = _compute_macro_regime_signals(quotes, crypto_coins)
    if signals:
        out_sections.append("=== 🧠 MACRO REGIME & CROSS-ASSET SIGNALS ===\n" + "\n".join(signals))

    # 2. Global Stock Market Indices (Canada, US, Europe, Asia — filtered/ordered by `region` when specified)
    if need_indices:
        region_groups = [
            ("🇨🇦 CANADIAN MARKETS & FX (TSX & Loonie)", MarketRegion.CANADA),
            ("🇺🇸 AMERICAN MARKETS (Wall Street & Treasuries)", MarketRegion.US),
            ("🇪🇺 EUROPEAN MARKETS", MarketRegion.EUROPE),
            ("🌏 ASIAN & PACIFIC MARKETS", MarketRegion.ASIA),
        ]
        if r_raw in (MarketRegion.CANADA, MarketRegion.US, MarketRegion.EUROPE, MarketRegion.ASIA):
            region_groups = [rg for rg in region_groups if rg[1] == r_raw]

        for title, reg_enum in region_groups:
            specs = [b for b in GLOBAL_INDICES if b.region == reg_enum]
            specs += [b for b in FOREX_BENCHMARKS if b.region == reg_enum]

            sec_lines = [f"=== {title} ==="]
            for sp in specs:
                line = _format_benchmark_line(sp, quotes.get(sp.symbol.upper()))
                if line:
                    sec_lines.append(line)
            if len(sec_lines) > 1:
                out_sections.append("\n".join(sec_lines))

    # 3. Precious Metals & Commodities (including CAD Spot Gold & Silver conversion)
    if need_commodities:
        comm_lines = ["=== 🪙 PRECIOUS METALS & COMMODITIES ==="]
        for sp in COMMODITY_BENCHMARKS:
            q = quotes.get(sp.symbol.upper())
            line = _format_benchmark_line(sp, q)
            if line:
                if sp.symbol in ("GC=F", "SI=F") and q and q.price > 0 and usdcad_rate > 0:
                    cad_oz = q.price * usdcad_rate
                    line += f" | **CAD Spot Proxy:** **${cad_oz:,.2f} CAD/oz**"
                comm_lines.append(line)
        if len(comm_lines) > 1:
            out_sections.append("\n".join(comm_lines))

    # 4. Cryptocurrencies (Majors + 24h Top Gainers/Losers)
    if need_crypto and crypto_coins:
        crypto_lines = ["=== ₿ CRYPTOCURRENCY MARKETS (USD & CAD) ==="]
        majors = [c for c in crypto_coins if c.symbol in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA")][:7]
        for c in majors:
            arrow = _fmt_arrow(c.change_24h_pct)
            cad_str = f" (${c.price_cad:,.2f} CAD)" if c.price_cad else ""
            w7_str = f" | 7d: {c.change_7d_pct:+.2f}%" if c.change_7d_pct is not None else ""
            mcap_str = f" | MCap: {_fmt_large_number(c.market_cap_usd)}" if c.market_cap_usd else ""
            crypto_lines.append(
                f"- {arrow} **{c.name} (`{c.symbol}`)**: **${c.price_usd:,.2f} USD**{cad_str} "
                f"(24h: **{c.change_24h_pct:+.2f}%**{w7_str}{mcap_str})"
            )
        c_movers = _crypto_agg.get_crypto_movers(crypto_coins, count=3)
        if c_movers.get("gainers"):
            g_str = " | ".join(f"**{c.symbol}** (${c.price_usd:,.4g}, **{c.change_24h_pct:+.2f}%**)" for c in c_movers["gainers"])
            crypto_lines.append(f"  • **Top 24h Crypto Gainers (Top 40 MCap):** {g_str}")
        if c_movers.get("losers"):
            l_str = " | ".join(f"**{c.symbol}** (${c.price_usd:,.4g}, **{c.change_24h_pct:+.2f}%**)" for c in c_movers["losers"])
            crypto_lines.append(f"  • **Top 24h Crypto Losers (Top 40 MCap):** {l_str}")
        out_sections.append("\n".join(crypto_lines))

    # 5. Daily Market Trends: What's Going Up & What's Going Down (TSX 60 & US Screener)
    if need_movers:
        movers_lines = ["=== 📈📉 TODAY'S MARKET MOVERS (WHAT'S GOING UP & DOWN) ==="]
        if r_raw != MarketRegion.US:
            if tsx_movers.get("gainers"):
                g_items = [
                    f"**{q.symbol}** ({q.name[:18]}: ${q.price:.2f} CAD, **{q.change_percent:+.2f}%**)"
                    for q in tsx_movers["gainers"][:5]
                ]
                movers_lines.append("- **🇨🇦 S&P/TSX 60 Top Gainers:** " + " | ".join(g_items))
            if tsx_movers.get("losers"):
                l_items = [
                    f"**{q.symbol}** ({q.name[:18]}: ${q.price:.2f} CAD, **{q.change_percent:+.2f}%**)"
                    for q in tsx_movers["losers"][:5]
                ]
                movers_lines.append("- **🇨🇦 S&P/TSX 60 Top Losers:** " + " | ".join(l_items))

        if r_raw != MarketRegion.CANADA:
            if us_movers.get("gainers"):
                g_items = [
                    f"**{q.symbol}** ({q.name[:18]}: ${q.price:.2f}, **{q.change_percent:+.2f}%**)"
                    for q in us_movers["gainers"][:5]
                ]
                movers_lines.append("- **🇺🇸 US Market Top Gainers:** " + " | ".join(g_items))
            if us_movers.get("losers"):
                l_items = [
                    f"**{q.symbol}** ({q.name[:18]}: ${q.price:.2f}, **{q.change_percent:+.2f}%**)"
                    for q in us_movers["losers"][:5]
                ]
                movers_lines.append("- **🇺🇸 US Market Top Losers:** " + " | ".join(l_items))
            if us_movers.get("actives"):
                a_items = [
                    f"**{q.symbol}** (${q.price:.2f}, **{q.change_percent:+.2f}%**, Vol: {_fmt_large_number(q.volume, '')})"
                    for q in us_movers["actives"][:5]
                ]
                movers_lines.append("- **🔥 Most Actively Traded (US):** " + " | ".join(a_items))
        if len(movers_lines) > 1:
            out_sections.append("\n".join(movers_lines))

    # 6. Top Financial & Macro Catalyst Headlines
    if need_headlines and macro_headlines:
        h_lines = ["=== 📰 TOP FINANCIAL & MACRO CATALYST HEADLINES ==="]
        for idx, h in enumerate(macro_headlines, start=1):
            h_lines.append(f"{idx}. {h}")
        out_sections.append("\n".join(h_lines))

    out_sections.append(build_markets_llm_instructions(is_ticker_view=False))
    return "\n\n".join(out_sections)


