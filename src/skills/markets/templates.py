"""
Centralized analytical LLM instructions for the markets and macro synthesis skill package.
"""


def build_markets_llm_instructions(is_ticker_view: bool = False) -> str:
    """Build concise, high-IQ analytical instructions for presenting market and ticker data."""
    if is_ticker_view:
        return (
            "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
            "1. Present a sharp, institutional-grade Ticker & Historical Snapshot: lead with the current price, currency (CAD for TSX stocks), intraday $/%, and session status.\n"
            "2. Highlight the **Multi-Horizon Performance Strip (1W, 1M, 3M, 6M, YTD, 1Y, 5Y)** and the **Historical Window Deep-Dive** (start → end price, period peak/trough with dates, max drawdown, and chronological price checkpoints).\n"
            "3. Include the **Technical Posture** (50-Day & 200-Day Moving Averages, 52-Week Range percentile, volume vs. 3M average) and **Valuation Fundamentals** (Market Cap, Trailing/Forward P/E, EPS, Dividend Yield, Next Earnings).\n"
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

