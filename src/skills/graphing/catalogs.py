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
    "monte_carlo": ChartType.MONTE_CARLO,
    "montecarlo": ChartType.MONTE_CARLO,
    "monte carlo": ChartType.MONTE_CARLO,
    "gbm": ChartType.MONTE_CARLO,
    "simulation": ChartType.MONTE_CARLO,
    "price_simulation": ChartType.MONTE_CARLO,
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
    "dollar": "USD",
    "dollars": "USD",
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
    "pounds": "GBP",
    "sterling": "GBP",
    "jpy": "JPY",
    "japanese yen": "JPY",
    "yen": "JPY",
    "chf": "CHF",
    "swiss franc": "CHF",
    "franc": "CHF",
    "aud": "AUD",
    "australian dollar": "AUD",
    "aussie dollar": "AUD",
    "nzd": "NZD",
    "new zealand dollar": "NZD",
    "kiwi dollar": "NZD",
    "cny": "CNY",
    "rmb": "CNY",
    "chinese yuan": "CNY",
    "yuan": "CNY",
    "renminbi": "CNY",
    "hkd": "HKD",
    "hong kong dollar": "HKD",
    "sgd": "SGD",
    "singapore dollar": "SGD",
    "sek": "SEK",
    "swedish krona": "SEK",
    "krona": "SEK",
    "nok": "NOK",
    "norwegian krone": "NOK",
    "krone": "NOK",
    "mxn": "MXN",
    "mexican peso": "MXN",
    "peso": "MXN",
    "inr": "INR",
    "indian rupee": "INR",
    "rupee": "INR",
    "brl": "BRL",
    "brazilian real": "BRL",
    "real": "BRL",
    "krw": "KRW",
    "korean won": "KRW",
    "south korean won": "KRW",
    "won": "KRW",
    "zar": "ZAR",
    "south african rand": "ZAR",
    "rand": "ZAR",
    "try": "TRY",
    "turkish lira": "TRY",
    "lira": "TRY",
    "pln": "PLN",
    "polish zloty": "PLN",
    "zloty": "PLN",
    "aed": "AED",
    "uae dirham": "AED",
    "dirham": "AED",
    "sar": "SAR",
    "saudi riyal": "SAR",
    "riyal": "SAR",
    "thb": "THB",
    "thai baht": "THB",
    "baht": "THB",
    "twd": "TWD",
    "taiwan dollar": "TWD",
}

_BTC_SATS_TO_ISO: Dict[str, str] = {
    "btc": "BTC",
    "bitcoin": "BTC",
    "sats": "SATS",
    "sat": "SATS",
    "satoshi": "SATS",
    "satoshis": "SATS",
}

_ALL_PAIR_UNITS: Dict[str, str] = {**CURRENCY_NAME_TO_ISO, **_BTC_SATS_TO_ISO}
_CCY_PATTERN = "|".join(sorted((re.escape(k) for k in CURRENCY_NAME_TO_ISO), key=len, reverse=True))
_UNIT_PATTERN = "|".join(sorted((re.escape(k) for k in _ALL_PAIR_UNITS), key=len, reverse=True))
_FX_PAIR_RE = re.compile(
    rf"\b(?P<base>{_UNIT_PATTERN})\s*(?:/|-|\bto\b|\bin\b|\bper\b|\bvs\.?\b|\bversus\b|\bagainst\b)\s*(?P<quote>{_UNIT_PATTERN})\b",
    re.IGNORECASE,
)
_BTC_PURCHASING_POWER_RE = re.compile(
    r"\b(?:how\s+much\s+(?:btc|bitcoin)\b.*?\b(?:buy|buys|bought|get|gets|worth)\b|"
    r"purchasing\s+power\b.*?\b(?:btc|bitcoin)\b|"
    r"(?:\$\s*1\b|1\s+(?:dollar|usd|cad|eur|euro|gbp|pound|yen|jpy|chf|franc|aud|nzd|yuan|cny|peso|mxn|rupee|inr|real|brl|won|krw|rand|zar|lira|try|zloty|pln|krona|sek|krone|nok))\b.*?\b(?:btc|bitcoin)\b)",
    re.IGNORECASE,
)


def _pair_tokens_to_symbol(base_tok: str, quote_tok: str) -> Optional[str]:
    b_low = (base_tok or "").strip().lower()
    q_low = (quote_tok or "").strip().lower()
    b_fiat = CURRENCY_NAME_TO_ISO.get(b_low)
    q_fiat = CURRENCY_NAME_TO_ISO.get(q_low)
    b_crypto = _BTC_SATS_TO_ISO.get(b_low)
    q_crypto = _BTC_SATS_TO_ISO.get(q_low)

    if b_fiat and q_fiat and b_fiat != q_fiat:
        return f"{b_fiat}{q_fiat}=X"
    if b_fiat and q_crypto == "BTC":
        return f"{b_fiat}-BTC"
    if b_crypto == "BTC" and q_fiat:
        return f"BTC-{q_fiat}"
    if (b_crypto == "SATS" and q_fiat) or (b_fiat and q_crypto == "SATS"):
        fiat_iso = q_fiat or b_fiat
        return f"SATS-{fiat_iso}"
    return None


def _detect_btc_purchasing_power_ccy(text: str) -> Optional[str]:
    """Detect natural-language 'how much BTC can $1 / 1 <currency> buy' queries for any currency."""
    if not text or not _BTC_PURCHASING_POWER_RE.search(text):
        return None
    low = text.lower()
    # Check specific currencies first (excluding generic 'dollar'/'dollars' so '$1 CAD' or '1 Canadian dollar' resolves to CAD)
    for k in sorted(CURRENCY_NAME_TO_ISO.keys(), key=len, reverse=True):
        if k in ("dollar", "dollars"):
            continue
        if re.search(rf"\b{re.escape(k)}\b", low):
            return f"{CURRENCY_NAME_TO_ISO[k]}-BTC"
    return "USD-BTC"


def resolve_fx_pair_symbol(raw_text: str) -> Optional[str]:
    """If `raw_text` represents a currency pair ('CAD-EUR', 'CAD/BTC', 'SATS/USD', '$1 in BTC'), return its canonical symbol."""
    if not raw_text:
        return None
    cleaned = raw_text.strip().upper()
    if re.fullmatch(r"[A-Z]{6}=X", cleaned):
        return cleaned
    if re.fullmatch(r"[A-Z]{3}-BTC", cleaned) and cleaned[:3] in CURRENCY_NAME_TO_ISO.values():
        return cleaned
    if re.fullmatch(r"SATS-[A-Z]{3}", cleaned) and cleaned[5:] in CURRENCY_NAME_TO_ISO.values():
        return cleaned
    if re.fullmatch(r"BTC-[A-Z]{3}", cleaned) and cleaned[4:] in CURRENCY_NAME_TO_ISO.values():
        return cleaned
    m = _FX_PAIR_RE.search(raw_text.strip())
    if m:
        sym = _pair_tokens_to_symbol(m.group("base"), m.group("quote"))
        if sym:
            return sym
    pp_sym = _detect_btc_purchasing_power_ccy(raw_text)
    if pp_sym:
        return pp_sym
    return None


def extract_fx_pairs_from_text(text: str) -> List[str]:
    """Extract all FX pair symbols (`<BASE><QUOTE>=X`, `<CCY>-BTC`, `SATS-<CCY>`, `BTC-<CCY>`) mentioned in natural language."""
    if not text:
        return []
    found: List[str] = []
    for m in _FX_PAIR_RE.finditer(text):
        sym = _pair_tokens_to_symbol(m.group("base"), m.group("quote"))
        if sym and sym not in found:
            found.append(sym)
    if not found:
        pp_sym = _detect_btc_purchasing_power_ccy(text)
        if pp_sym:
            found.append(pp_sym)
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
    "dx-y.nyb": "DX-Y.NYB",
    "us dollar index": "DX-Y.NYB",
    "dollar index": "DX-Y.NYB",
    "usd index": "DX-Y.NYB",
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


