"""
Static color palettes, chart-type alias mappings, and ticker catalogs for `src/skills/graphing/`.
"""

from typing import Dict, Tuple

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

# Common ticker aliases for quick resolution when charting stocks/crypto/commodities directly
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
}

CHART_QUERY_STOPWORDS: frozenset[str] = frozenset({
    "chart", "charts", "plot", "plots", "graph", "graphs", "visualize", "visualization",
    "diagram", "diagrams", "flowchart", "draw", "create", "make", "generate", "show",
    "me", "a", "an", "the", "of", "for", "over", "in", "on", "with", "and", "vs",
    "versus", "compare", "comparing", "comparison", "between", "from", "to", "past",
    "last", "history", "historical", "price", "prices", "performance", "return", "returns",
    "stock", "stocks", "share", "shares", "crypto", "cryptocurrency", "line", "bar", "pie",
})

