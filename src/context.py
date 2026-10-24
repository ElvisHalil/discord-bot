"""
Shared Core Execution Context (`src/context.py`).

Owns per-turn `ContextVar` state (such as `ACTIVE_CHANNEL_ID`) and the main
asyncio event loop reference so sibling skills (`music`, `sports`, `flights`)
never need to import from one another.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextvars import ContextVar
import threading
import time
from typing import Any, Dict, List, Optional

ACTIVE_CHANNEL_ID: ContextVar[int] = ContextVar("ACTIVE_CHANNEL_ID", default=0)
ACTIVE_USER_ID: ContextVar[int] = ContextVar("ACTIVE_USER_ID", default=0)
ACTIVE_USER_NAME: ContextVar[str] = ContextVar("ACTIVE_USER_NAME", default="")
_MAIN_EVENT_LOOP: Optional[asyncio.AbstractEventLoop] = None
_ATTACHMENTS_LOCK = threading.Lock()
_PENDING_CHANNEL_ATTACHMENTS: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
_HUMAN_ACTIVITY_LOCK = threading.Lock()
_LAST_CHANNEL_HUMAN_ACTIVITY: Dict[int, float] = {}


def set_active_channel_id(channel_id: Optional[int]) -> None:
    """Record the active Discord/Telegram channel ID for the current turn context."""
    ACTIVE_CHANNEL_ID.set(int(channel_id or 0))


def get_active_channel_id() -> int:
    """Return the active Discord/Telegram channel ID for the current turn context."""
    return ACTIVE_CHANNEL_ID.get()


def set_active_user(user_id: Optional[int], display_name: str = "") -> None:
    """Record the active Discord/Telegram user ID and display name for the current turn context."""
    ACTIVE_USER_ID.set(int(user_id or 0))
    ACTIVE_USER_NAME.set(str(display_name or "").strip())


def get_active_user_id() -> int:
    """Return the active Discord/Telegram user ID for the current turn context."""
    return ACTIVE_USER_ID.get()


def get_active_user_name() -> str:
    """Return the active Discord/Telegram user display name for the current turn context."""
    return ACTIVE_USER_NAME.get()


def record_channel_human_activity(channel_id: Optional[int], timestamp: Optional[float] = None) -> None:
    """Record a human message or reaction timestamp in the given channel (used by the Quiet-Channel Idle Brake)."""
    cid = int(channel_id or 0)
    if not cid:
        return
    ts = float(timestamp if timestamp is not None else time.time())
    with _HUMAN_ACTIVITY_LOCK:
        _LAST_CHANNEL_HUMAN_ACTIVITY[cid] = ts


def get_last_channel_human_activity(channel_id: Optional[int]) -> float:
    """Return the last recorded human activity epoch timestamp for `channel_id` (0.0 if none)."""
    cid = int(channel_id or 0)
    if not cid:
        return 0.0
    with _HUMAN_ACTIVITY_LOCK:
        return _LAST_CHANNEL_HUMAN_ACTIVITY.get(cid, 0.0)


def clear_channel_human_activity(channel_id: Optional[int] = None) -> None:
    """Clear recorded human activity timestamps (for one channel or all channels when None)."""
    with _HUMAN_ACTIVITY_LOCK:
        if channel_id is None:
            _LAST_CHANNEL_HUMAN_ACTIVITY.clear()
        else:
            _LAST_CHANNEL_HUMAN_ACTIVITY.pop(int(channel_id), None)


def bind_event_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    """Bind the main Discord/Telegram asyncio event loop so worker threads can schedule background tasks."""
    global _MAIN_EVENT_LOOP
    _MAIN_EVENT_LOOP = loop


def get_bound_event_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Return the bound main asyncio event loop, or the running loop if called from an async context."""
    if _MAIN_EVENT_LOOP is not None and not _MAIN_EVENT_LOOP.is_closed():
        return _MAIN_EVENT_LOOP
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return _MAIN_EVENT_LOOP


def queue_channel_attachment(
    filename: str,
    data: bytes,
    caption: str = "",
    channel_id: Optional[int] = None,
) -> None:
    """Queue a rendered binary attachment (e.g. PNG chart/diagram) for the active Discord/Telegram channel."""
    if not data:
        return
    cid = int(channel_id if channel_id is not None else (get_active_channel_id() or 0))
    safe_name = (filename or "chart.png").strip() or "chart.png"
    with _ATTACHMENTS_LOCK:
        bucket = _PENDING_CHANNEL_ATTACHMENTS[cid]
        # Cap at 5 attachments per turn to avoid flooding
        if len(bucket) < 5:
            bucket.append({"filename": safe_name, "data": data, "caption": caption or ""})


def pop_channel_attachments(channel_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Pop and return all pending `{'filename': ..., 'data': ..., 'caption': ...}` attachments for the channel."""
    cid = int(channel_id if channel_id is not None else (get_active_channel_id() or 0))
    with _ATTACHMENTS_LOCK:
        items = list(_PENDING_CHANNEL_ATTACHMENTS.pop(cid, []))
    return items


def clear_channel_attachments(channel_id: Optional[int] = None) -> None:
    """Clear pending binary attachments (for a specific channel or all channels when None)."""
    with _ATTACHMENTS_LOCK:
        if channel_id is None:
            _PENDING_CHANNEL_ATTACHMENTS.clear()
        else:
            _PENDING_CHANNEL_ATTACHMENTS.pop(int(channel_id), None)


_CALENDAR_HEATMAP_RENDERER: Optional[Any] = None


def set_calendar_heatmap_renderer(renderer_fn: Any) -> None:
    """Register the calendar heatmap renderer callback from `src.skills.graphing`."""
    global _CALENDAR_HEATMAP_RENDERER
    _CALENDAR_HEATMAP_RENDERER = renderer_fn


def queue_calendar_heatmap(
    title: str,
    subtitle: str = "",
    cells: Optional[List[Dict[str, Any]]] = None,
    value_prefix: str = "$",
    currency: str = "",
    footer_notes: Optional[List[str]] = None,
) -> Optional[str]:
    """Render and queue a calendar heatmap attachment via the registered renderer, returning the filename."""
    if _CALENDAR_HEATMAP_RENDERER is None or not cells:
        return None
    return _CALENDAR_HEATMAP_RENDERER(
        title=title,
        subtitle=subtitle,
        cells=cells,
        value_prefix=value_prefix,
        currency=currency,
        footer_notes=footer_notes or [],
    )


