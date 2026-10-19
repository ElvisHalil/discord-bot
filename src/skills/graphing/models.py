"""
Slotted dataclass models for the Graphing & Diagrams Visualization Skill (`src/skills/graphing/models.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(slots=True)
class ChartSeries:
    """Single named data series for 2D charts (line, bar, area, scatter, stacked_bar)."""
    name: str
    values: List[float]
    color: Optional[str] = None
    x_values: Optional[List[float]] = None


@dataclass(slots=True)
class ChartSpec:
    """Normalized specification for rendering a chart via Matplotlib."""
    chart_type: str
    title: str
    labels: List[str] = field(default_factory=list)
    series: List[ChartSeries] = field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    subtitle: str = ""
    value_prefix: str = ""
    value_suffix: str = ""


@dataclass(slots=True)
class MarketBarSeries:
    """Historical price/volume time series for a single market ticker."""
    symbol: str
    name: str
    currency: str
    timeframe_label: str
    timestamps: List[int] = field(default_factory=list)
    opens: List[float] = field(default_factory=list)
    highs: List[float] = field(default_factory=list)
    lows: List[float] = field(default_factory=list)
    closes: List[float] = field(default_factory=list)
    volumes: List[float] = field(default_factory=list)
    pct_change: float = 0.0
    period_high: float = 0.0
    period_low: float = 0.0


@dataclass(slots=True)
class RenderedVisual:
    """Result of rendering a chart or diagram image into memory."""
    filename: str
    png_bytes: bytes
    title: str
    summary_lines: List[str] = field(default_factory=list)
    renderer_used: str = "matplotlib"


@dataclass(slots=True)
class CalendarDayCell:
    """Single dated cell in a monthly/window Calendar Heatmap (e.g. flight fare calendar or daily metric grid)."""
    date_str: str
    value: float
    label: str = ""
    sublabel: str = ""
    is_best: bool = False
    is_direct: bool = False


@dataclass(slots=True)
class CalendarHeatmapSpec:
    """Specification for rendering a 7-column Mon-Sun calendar heatmap card."""
    title: str
    subtitle: str = ""
    cells: List[CalendarDayCell] = field(default_factory=list)
    value_prefix: str = "$"
    value_suffix: str = ""
    currency: str = ""
    footer_notes: List[str] = field(default_factory=list)
    low_threshold: Optional[float] = None
    high_threshold: Optional[float] = None
    higher_is_better: bool = False


@dataclass(slots=True)
class ChartChannelState:
    """Per-channel state remembering the most recent chart or diagram rendered."""
    last_chart_type: str = ""
    last_title: str = ""
    last_tickers: List[str] = field(default_factory=list)
    last_timeframe: Optional[str] = None
    last_spec: Optional[ChartSpec] = None
    last_diagram_code: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)
    last_bounds: Optional[Tuple[float, float]] = None

