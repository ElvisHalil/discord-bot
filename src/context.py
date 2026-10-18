"""
Shared Core Execution Context (`src/context.py`).

Owns per-turn `ContextVar` state (such as `ACTIVE_CHANNEL_ID`) and the main
asyncio event loop reference so sibling skills (`music`, `sports`, `flights`)
never need to import from one another.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Optional

ACTIVE_CHANNEL_ID: ContextVar[int] = ContextVar("ACTIVE_CHANNEL_ID", default=0)
_MAIN_EVENT_LOOP: Optional[asyncio.AbstractEventLoop] = None


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


# Backward-compatible alias
bind_tracker_event_loop = bind_event_loop

