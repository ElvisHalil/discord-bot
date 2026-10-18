"""
Optional background polling tracker skeleton (`tracker.py`).
Demonstrates how a skill registers per-channel watches using `src/context.py`.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from src.context import get_active_channel_id
from .enums import ExampleTrackerAction
from .models import TrackedExampleWatch

logger = logging.getLogger(__name__)


class ExampleTrackerService:
    """Skeleton background polling service for per-channel alerts."""

    def __init__(self) -> None:
        self.watches: Dict[str, TrackedExampleWatch] = {}

    def start_tracking(self, query: str, channel_id: int) -> str:
        key = f"{channel_id}:{query.strip().lower()}"
        self.watches[key] = TrackedExampleWatch(
            watch_key=key,
            query=query.strip(),
            channel_id=channel_id,
        )
        return f"Started tracking '{query}' in channel {channel_id}."

    def stop_tracking(self, query: str, channel_id: int) -> str:
        key = f"{channel_id}:{query.strip().lower()}"
        removed = self.watches.pop(key, None)
        if removed:
            return f"Stopped tracking '{query}'."
        return f"No active watch found for '{query}'."

    def list_tracked(self, channel_id: int) -> str:
        active = [w.query for w in self.watches.values() if w.channel_id == channel_id]
        if not active:
            return "No active example watches in this channel."
        return "Active watches: " + ", ".join(active)


EXAMPLE_TRACKER = ExampleTrackerService()


def skill_example_tracker(
    action: str = ExampleTrackerAction.START,
    query: Optional[str] = None,
) -> str:
    """
    Skeleton background tracker tool entrypoint.

    Args:
        action: One of 'start', 'stop', or 'list'.
        query: Item identifier or search query to watch.
    """
    channel_id = get_active_channel_id()
    act = (action or ExampleTrackerAction.START).strip().lower()
    if act == ExampleTrackerAction.LIST:
        return EXAMPLE_TRACKER.list_tracked(channel_id)
    if not query:
        return "Please provide a query to track or stop tracking."
    if act == ExampleTrackerAction.STOP:
        return EXAMPLE_TRACKER.stop_tracking(query, channel_id)
    return EXAMPLE_TRACKER.start_tracking(query, channel_id)


