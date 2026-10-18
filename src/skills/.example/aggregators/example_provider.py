"""
Concrete example provider implementation.
In a real skill, use `fetch_json` / `fetch_text` from `src.utils.http` and `map_in_workers` from `src.utils.workers`.
"""

from __future__ import annotations

from ..catalogs import EXAMPLE_STOPWORDS
from ..models import ExampleItemRecord
from .base import BaseExampleAggregator


class ExampleProviderAggregator(BaseExampleAggregator):
    """Example upstream provider returning a typed `ExampleItemRecord`."""

    def fetch_item(self, query: str, action: str = "lookup") -> ExampleItemRecord:
        tokens = [w for w in query.lower().split() if w not in EXAMPLE_STOPWORDS]
        slug = "-".join(tokens) or "default"
        return ExampleItemRecord(
            item_id=slug,
            title=f"Example Result for '{query}'",
            status=f"Ready ({action})",
            url=f"https://example.com/items/{slug}",
        )

