"""
Static color palettes, chart-type alias mappings, and ticker catalogs for `src/skills/graphing/`.
"""

from typing import Dict, List, Optional, Tuple
import re

from src.skills.graphing.enums import ChartType, DiagramType


# High-contrast Discord/Telegram dark-mode color palette
DARK_THEME_PALETTE: Tuple[str, ...] = (
    "#58A6FF",  # Electric Blue
    "#3FB950",  # Emerald Green
    "#F78166",  # Coral Orange
    "#D2A8FF",  # Soft Purple
    "#E3B341",  # Amber Gold
    "#39C5CF",  # Cyan Teal
    "#FF7B72",  # Salmon Red
    "#A5D6FF",  # Sky Blue
)

DARK_BG_COLOR = "#0D1117"
DARK_PANEL_COLOR = "#161B22"
DARK_GRID_COLOR = "#30363D"
DARK_TEXT_COLOR = "#E6EDF3"
DARK_MUTED_TEXT_COLOR = "#8B949E"
BULLISH_GREEN = "#2EA043"
BEARISH_RED = "#F85149"

# Dark-mode Calendar & Matrix Heatmap color tiers (Fill, Border, Accent Text)
HEATMAP_TIER_DEAL = ("#123824", "#3FB950", "#7EE787")      # Emerald Green (Lowest / Best Deal)
HEATMAP_TIER_GOOD = ("#143340", "#39C5CF", "#A5D6FF")      # Cyan / Teal (Below Median)
HEATMAP_TIER_TYPICAL = ("#1B2738", "#58A6FF", "#E6EDF3")   # Slate Blue (Typical / Average)
HEATMAP_TIER_ELEVATED = ("#3D2E14", "#E3B341", "#F2CC60")  # Amber Gold (Above Typical)
HEATMAP_TIER_PEAK = ("#421B20", "#F85149", "#FFA198")      # Coral Red (High / Peak Price)
HEATMAP_EMPTY_TILE = ("#11161D", "#21262D", "#484F58")     # Recessed Empty Day Tile

CHART_TYPE_ALIASES: Dict[str, str] = {
    "line": ChartType.LINE,
    "lines": ChartType.LINE,
    "trend": ChartType.LINE,
    "timeseries": ChartType.LINE,
    "time_series": ChartType.LINE,
    "bar": ChartType.BAR,
    "bars": ChartType.BAR,
    "column": ChartType.BAR,
    "columns": ChartType.BAR,
    "histogram": ChartType.BAR,
    "hbar": ChartType.HORIZONTAL_BAR,
    "horizontal_bar": ChartType.HORIZONTAL_BAR,
    "horizontal": ChartType.HORIZONTAL_BAR,
    "stacked": ChartType.STACKED_BAR,
    "stacked_bar": ChartType.STACKED_BAR,
    "area": ChartType.AREA,
    "scatter": ChartType.SCATTER,
    "points": ChartType.SCATTER,
    "pie": ChartType.PIE,
    "donut": ChartType.DONUT,
    "doughnut": ChartType.DONUT,
    "heatmap": ChartType.HEATMAP,
    "heat_map": ChartType.HEATMAP,
    "matrix": ChartType.HEATMAP,
    "grid": ChartType.HEATMAP,
    "calendar": ChartType.CALENDAR_HEATMAP,
    "calendar_heatmap": ChartType.CALENDAR_HEATMAP,
    "fare_calendar": ChartType.CALENDAR_HEATMAP,
    "price_calendar": ChartType.CALENDAR_HEATMAP,
    "market": ChartType.MARKET,
    "stock": ChartType.MARKET,
    "stocks": ChartType.MARKET,
    "crypto": ChartType.MARKET,
    "price": ChartType.MARKET,
    "candlestick": ChartType.MARKET,
    "ohlc": ChartType.MARKET,
    "compare": ChartType.MARKET,
    "comparison": ChartType.MARKET,
    "diagram": ChartType.DIAGRAM,
    "flowchart": ChartType.DIAGRAM,
    "flow": ChartType.DIAGRAM,
    "sequence": ChartType.DIAGRAM,
    "sequence_diagram": ChartType.DIAGRAM,
    "architecture": ChartType.DIAGRAM,
    "state": ChartType.DIAGRAM,
    "class": ChartType.DIAGRAM,
    "er": ChartType.DIAGRAM,
    "mindmap": ChartType.DIAGRAM,
    "mermaid": ChartType.DIAGRAM,
}

DIAGRAM_TYPE_ALIASES: Dict[str, str] = {
    "flowchart": DiagramType.FLOWCHART,
    "flow": DiagramType.FLOWCHART,
    "graph": DiagramType.FLOWCHART,
    "process": DiagramType.FLOWCHART,
    "sequence": DiagramType.SEQUENCE,
    "sequence_diagram": DiagramType.SEQUENCE,
    "architecture": DiagramType.ARCHITECTURE,
    "system": DiagramType.ARCHITECTURE,
    "state": DiagramType.STATE,
    "class": DiagramType.CLASS,
    "er": DiagramType.ER,
    "entity": DiagramType.ER,
    "mindmap": DiagramType.MINDMAP,
    "timeline": DiagramType.TIMELINE,
}

CURRENCY_NAME_TO_ISO: Dict[str, str] = {
    "usd": "USD",
    "us dollar": "USD",
    "us dollars": "USD",
    "american dollar": "USD",
    "cad": "CAD",
    "canadian dollar": "CAD",
    "canadian dollars": "CAD",
    "loonie": "CAD",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "gbp": "GBP",
    "british pound": "GBP",
    "pound": "GBP",
    "sterling": "GBP",
    "jpy": "JPY",
    "japanese yen": "JPY",
    "yen": "JPY",
    "chf": "CHF",
    "swiss franc": "CHF",
    "franc": "CHF",
    "aud": "AUD",
    "australian dollar": "AUD",
    "nzd": "NZD",
    "new zealand dollar": "NZD",
    "cny": "CNY",
    "rmb": "CNY",
    "chinese yuan": "CNY",
    "yuan": "CNY",
    "hkd": "HKD",
    "sgd": "SGD",
    "sek": "SEK",
    "nok": "NOK",
    "mxn": "MXN",
    "inr": "INR",
    "brl": "BRL",
    "krw": "KRW",
    "zar": "ZAR",
    "try": "TRY",
}

_CCY_PATTERN = "|".join(sorted((re.escape(k) for k in CURRENCY_NAME_TO_ISO), key=len, reverse=True))
_FX_PAIR_RE = re.compile(
    rf"\b(?P<base>{_CCY_PATTERN})\s*(?:/|-|\bto\b|\bvs\.?\b|\bversus\b|\bagainst\b)\s*(?P<quote>{_CCY_PATTERN})\b",
    re.IGNORECASE,
)


def resolve_fx_pair_symbol(raw_text: str) -> Optional[str]:
    """If `raw_text` represents a currency pair (e.g. 'CAD-EUR', 'CAD/EURO', 'Canadian dollar to Euro'), return `<BASE><QUOTE>=X`."""
    if not raw_text:
        return None
    cleaned = raw_text.strip().upper()
    if re.fullmatch(r"[A-Z]{6}=X", cleaned):
        return cleaned
    m = _FX_PAIR_RE.search(raw_text.strip())
    if m:
        base_iso = CURRENCY_NAME_TO_ISO.get(m.group("base").lower())
        quote_iso = CURRENCY_NAME_TO_ISO.get(m.group("quote").lower())
        if base_iso and quote_iso and base_iso != quote_iso:
            return f"{base_iso}{quote_iso}=X"
    return None


def extract_fx_pairs_from_text(text: str) -> List[str]:
    """Extract all FX pair symbols (`<BASE><QUOTE>=X`) mentioned in natural language or slash/hyphen notation."""
    if not text:
        return []
    found: List[str] = []
    for m in _FX_PAIR_RE.finditer(text):
        base_iso = CURRENCY_NAME_TO_ISO.get(m.group("base").lower())
        quote_iso = CURRENCY_NAME_TO_ISO.get(m.group("quote").lower())
        if base_iso and quote_iso and base_iso != quote_iso:
            sym = f"{base_iso}{quote_iso}=X"
            if sym not in found:
                found.append(sym)
    return found


# Common ticker aliases for quick resolution when charting stocks/crypto/commodities/FX directly
CHART_TICKER_ALIASES: Dict[str, str] = {
    "shopify": "SHOP.TO",
    "royal bank": "RY.TO",
    "rbc": "RY.TO",
    "td bank": "TD.TO",
    "enbridge": "ENB.TO",
    "brookfield": "BN.TO",
    "cn rail": "CNR.TO",
    "cpkc": "CP.TO",
    "air canada": "AC.TO",
    "barrick": "ABX.TO",
    "suncor": "SU.TO",
    "cnq": "CNQ.TO",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "tesla": "TSLA",
    "amd": "AMD",
    "intel": "INTC",
    "broadcom": "AVGO",
    "netflix": "NFLX",
    "palantir": "PLTR",
    "crowdstrike": "CRWD",
    "coinbase": "COIN",
    "microstrategy": "MSTR",
    "bitcoin": "BTC-USD",
    "btc": "BTC-USD",
    "ethereum": "ETH-USD",
    "eth": "ETH-USD",
    "solana": "SOL-USD",
    "sol": "SOL-USD",
    "xrp": "XRP-USD",
    "ripple": "XRP-USD",
    "dogecoin": "DOGE-USD",
    "doge": "DOGE-USD",
    "cardano": "ADA-USD",
    "zcash": "ZEC-USD",
    "conflux": "CFX-USD",
    "radiant": "RXD-USD",
    "rxd": "RXD-USD",
    "gold": "GC=F",
    "silver": "SI=F",
    "oil": "CL=F",
    "wti": "CL=F",
    "brent": "BZ=F",
    "copper": "HG=F",
    "natural gas": "NG=F",
    "s&p 500": "^GSPC",
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "dow": "^DJI",
    "dow jones": "^DJI",
    "tsx": "^GSPTSE",
    "s&p/tsx": "^GSPTSE",
    "vix": "^VIX",
    "dxy": "DX-Y.NYB",
    "cad/usd": "CADUSD=X",
    "cad-usd": "CADUSD=X",
    "usd/cad": "USDCAD=X",
    "usd-cad": "USDCAD=X",
    "cad/eur": "CADEUR=X",
    "cad-eur": "CADEUR=X",
    "cad/euro": "CADEUR=X",
    "cad-euro": "CADEUR=X",
    "eur/cad": "EURCAD=X",
    "eur-cad": "EURCAD=X",
    "euro/cad": "EURCAD=X",
    "eur/usd": "EURUSD=X",
    "eur-usd": "EURUSD=X",
    "gbp/usd": "GBPUSD=X",
    "gbp-usd": "GBPUSD=X",
    "usd/jpy": "USDJPY=X",
    "usd-jpy": "USDJPY=X",
}

CHART_QUERY_STOPWORDS: frozenset[str] = frozenset({
    "chart", "charts", "plot", "plots", "graph", "graphs", "visualize", "visualization",
    "diagram", "diagrams", "flowchart", "draw", "create", "make", "generate", "show",
    "me", "a", "an", "the", "of", "for", "over", "in", "on", "with", "and", "vs",
    "versus", "compare", "comparing", "comparison", "between", "from", "to", "past",
    "last", "history", "historical", "price", "prices", "performance", "return", "returns",
    "stock", "stocks", "share", "shares", "crypto", "cryptocurrency", "line", "bar", "pie",
    "pair", "based", "years", "year", "months", "month", "days", "day",
})


