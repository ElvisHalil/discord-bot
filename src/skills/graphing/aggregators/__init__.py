from src.skills.graphing.aggregators.base import BaseGraphingAggregator
from src.skills.graphing.aggregators.market_series import (
    MarketSeriesAggregator,
    _SERIES_CACHE,
    resolve_chart_ticker,
)
from src.skills.graphing.aggregators.matplotlib_engine import MatplotlibChartAggregator
from src.skills.graphing.aggregators.mermaid_engine import (
    MermaidDiagramAggregator,
    _DIAGRAM_CACHE,
    extract_nodes_and_edges,
    normalize_and_sanitize_mermaid,
)


def clear_graphing_aggregator_caches() -> None:
    _SERIES_CACHE.clear()
    _DIAGRAM_CACHE.clear()


__all__ = [
    "BaseGraphingAggregator",
    "MarketSeriesAggregator",
    "MatplotlibChartAggregator",
    "MermaidDiagramAggregator",
    "clear_graphing_aggregator_caches",
    "extract_nodes_and_edges",
    "normalize_and_sanitize_mermaid",
    "resolve_chart_ticker",
]
