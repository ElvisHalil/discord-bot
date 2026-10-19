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
from typing import Any, Dict, List, Optional

ACTIVE_CHANNEL_ID: ContextVar[int] = ContextVar("ACTIVE_CHANNEL_ID", default=0)
_MAIN_EVENT_LOOP: Optional[asyncio.AbstractEventLoop] = None
_ATTACHMENTS_LOCK = threading.Lock()
_PENDING_CHANNEL_ATTACHMENTS: Dict[int, List[Dict[str, Any]]] = defaultdict(list)


def set_active_channel_id(channel_id: Optional[int]) -> None:
    """Record the active Discord/Telegram channel ID for the current turn context."""
    ACTIVE_CHANNEL_ID.set(int(channel_id or 0))


def get_active_channel_id() -> int:
    """Return the active Discord/Telegram channel ID for the current turn context."""
    return ACTIVE_CHANNEL_ID.get()


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


