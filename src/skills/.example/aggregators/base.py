"""
Abstract base class for Example skill aggregators.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ExampleItemRecord


class BaseExampleAggregator(ABC):
    """Contract that all providers in this skill must implement."""

    @abstractmethod
    def fetch_item(self, query: str, action: str = "lookup") -> ExampleItemRecord:
        """Fetch and return a typed ExampleItemRecord for `query`."""
        raise NotImplementedError

