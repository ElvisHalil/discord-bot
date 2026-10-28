"""
Centralized analytical LLM instructions for the markets and macro synthesis skill package.
"""


def build_markets_llm_instructions(is_ticker_view: bool = False, is_compare_view: bool = False) -> str:
    """Build concise, high-IQ analytical instructions for presenting market and ticker data."""
    if is_compare_view:
        return (
            "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
            "1. Present a sharp, institutional-grade **Multi-Asset Head-to-Head Comparison**: lead with the **Relative Performance Winner & Spread** over the requested timeframe.\n"
            "2. Compare each asset side-by-side across **Live Price & Intraday Move**, **Multi-Horizon Returns (1W, 1M, 3M, 6M, YTD, 1Y, 3Y, 5Y)**, and **Deep Window Risk/Return Metrics** (Window Price Return, Dividend-Adjusted Total Return, CAGR, Max Drawdown, and Realized Volatility).\n"
            "3. Contrast **Valuation & Fundamentals** (P/E, Forward P/E, Market Cap, Dividend Yield, Beta, Analyst Target) and explain the risk-adjusted tradeoff (e.g. higher return vs. deeper drawdown or lower volatility).\n"
            "4. Use exact prices, currencies, and percentages from the tool output—never invent numbers.\n"
        )
    if is_ticker_view:
        return (
            "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
            "1. Present a sharp, institutional-grade Ticker & Historical Snapshot: lead with the current price, currency (CAD for TSX stocks), intraday $/%, and session status.\n"
            "2. Highlight the **Multi-Horizon Performance Strip (1W, 1M, 3M, 6M, YTD, 1Y, 3Y, 5Y)** and the **Historical Window Deep-Dive** (start → end price, Price Return & Dividend-Adjusted Total Return, CAGR, period peak/trough with dates, max drawdown, realized volatility, dividends/splits, any exact-date OHLC lookup, and chronological price checkpoints).\n"
            "3. Include the **Technical Posture** (50-Day & 200-Day Moving Averages, 52-Week Range percentile, volume vs. 3M average) and **Valuation Fundamentals** (Market Cap, Trailing/Forward P/E, EPS, P/B, Beta, Dividend Yield, Analyst Consensus Target, Next/Last Earnings).\n"
            "4. Connect the price action and historical trend to the **Recent Catalyst Headlines**, and if a dual-listed price (e.g. TSX CAD and NYSE/NASDAQ USD) is shown, include both clearly.\n"
        )
    return (
        "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
        "1. Act as a sharp, well-informed macro & markets analyst: lead with the **Macro Regime & Cross-Asset Takeaways** (risk-on vs. risk-off posture, VIX/10Y Yield moves, sector/commodity divergences, and key financial headlines driving the tape).\n"
        "2. Present a clean, scannable breakdown across requested regions/assets: **North America (TSX & US)**, **Europe & Asia**, **Precious Metals & Commodities (Gold, Silver, Oil, NatGas, Copper)**, **Crypto (BTC, ETH & top movers)**, and **FX (CAD/USD)**.\n"
        "3. Call out **What's Going Up & What's Going Down** (TSX & US top gainers/losers and 24h crypto movers) and synthesize *why* based on the macro/financial headlines.\n"
        "4. Use exact prices, currencies, and percentage changes from the tool output—never invent numbers.\n"
    )



def build_news_market_pulse_instructions() -> str:
    """Build synthesis instructions when a Daily Market Pulse is attached to a general/world news query."""
    return (
        "\n=== MACRO & NEWS SYNTHESIS INSTRUCTIONS ===\n"
        "1. After covering the top news stories, include a concise **📈 Daily Market & Macro Pulse** section that connects today's world/business events to how global markets are reacting.\n"
        "2. Highlight key benchmarks from the pulse data: **Canada (S&P/TSX, CAD/USD)**, **US (S&P 500, NASDAQ, Dow, VIX)**, **Europe & Asia (FTSE, DAX, Nikkei, Hang Seng)**, **Precious Metals & Oil (Gold, Silver, WTI Crude)**, and **Bitcoin**.\n"
        "3. Point out any notable cross-asset themes (e.g. safe-haven flows into Gold, energy/oil moves tied to geopolitics, or tech/rate moves) so your briefing reads like an informed executive summary.\n"
    )


def build_macro_calendar_llm_instructions() -> str:
    """Build analytical instructions for presenting Central Bank Rate Decisions & Economic Calendar data."""
    return (
        "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
        "1. **ALWAYS Lead with Local Time (`MDT`/`MST`) First:** The bot's Local Time (`MDT`/`MST`) IS the user's local timezone. Whenever stating any meeting, decision, or release time, state the **Local Time (`MDT`/`MST`) FIRST**, followed by Eastern Time (`EDT`/`EST`) in parentheses—for example: **12:00 PM MDT (2:00 PM EDT)** or **7:45 AM MDT (9:45 AM EDT)**. Never give only Eastern Time and never claim you don't know the user's local timezone.\n"
        "2. **State Same-Day (`TODAY`) Decisions with 100% Confidence:** Check the `=== 🚨 TODAY'S SCHEDULED DECISIONS & RELEASES ===` section first. If a Federal Reserve (FOMC) or Bank of Canada (BoC) decision is listed under `TODAY`, state clearly that it is **today**, whether it has already been released (`✅ RELEASED TODAY`) or is still upcoming (`⏳ UPCOMING TODAY`), and give the exact Local Time and countdown.\n"
        "3. **Include Exact Rate Numbers & Basis Point Changes:** State the **Actual** rate (if released), the **bps change** (e.g. `+25 bps hike`, `-25 bps cut`, or `Hold`), the consensus **Forecast**, and the **Previous** rate, plus any scheduled Press Conference time.\n"
        "4. Connect the central bank / macro event to the **Live Rates, FX & Market Benchmark Reaction** (US 10Y Treasury Yield, CAD/USD Loonie, S&P/TSX, S&P 500, and Gold) when relevant.\n"
    )

