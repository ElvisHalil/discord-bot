"""
Abstract base class for Graphing & Diagrams aggregators and rendering engines (`src/skills/graphing/aggregators/base.py`).
"""

from abc import ABC


class BaseGraphingAggregator(ABC):
    """Base class for all graphing data fetchers and visual rendering engines."""

    name: str = "base_graphing"
