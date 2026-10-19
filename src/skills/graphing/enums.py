"""
Typed StrEnum definitions for the Graphing & Diagrams Visualization Skill (`src/skills/graphing/enums.py`).
"""

from enum import StrEnum


class ChartType(StrEnum):
    LINE = "line"
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    STACKED_BAR = "stacked_bar"
    AREA = "area"
    SCATTER = "scatter"
    PIE = "pie"
    DONUT = "donut"
    HEATMAP = "heatmap"
    CALENDAR_HEATMAP = "calendar_heatmap"
    MARKET = "market"
    MONTE_CARLO = "monte_carlo"
    DIAGRAM = "diagram"


class DiagramType(StrEnum):
    FLOWCHART = "flowchart"
    SEQUENCE = "sequence"
    ARCHITECTURE = "architecture"
    STATE = "state"
    CLASS = "class"
    ER = "er"
    MINDMAP = "mindmap"
    TIMELINE = "timeline"

