"""
Typed dataclass models for the Example skill skeleton.
Rule: Only `@dataclass(slots=True)` classes belong in `models.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.utils.domain_vocab import DictLikeMixin


@dataclass(slots=True)
class ExampleItemRecord(DictLikeMixin):
    """Normalized record returned by an example skill aggregator."""
    item_id: str
    title: str
    status: str
    url: Optional[str] = None


@dataclass(slots=True)
class TrackedExampleWatch(DictLikeMixin):
    """Per-channel background watch entry managed by `tracker.py`."""
    watch_key: str
    query: str
    channel_id: int
    last_status: Optional[str] = None

