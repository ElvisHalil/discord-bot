"""
Per-channel state management, tool argument enrichment, and turn-0 recovery for the Example skill.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from src.context import get_active_channel_id

# Per-channel active query state: {channel_id: last_query}
_CHANNEL_LAST_EXAMPLE_QUERY: Dict[int, str] = {}


def record_channel_example_query(query: str, channel_id: Optional[int] = None) -> None:
    """Record the latest resolved query for a Discord/Telegram channel."""
    q = (query or "").strip()
    if not q:
        return
    cid = channel_id if channel_id is not None else get_active_channel_id()
    _CHANNEL_LAST_EXAMPLE_QUERY[cid] = q


def get_channel_last_example_query(channel_id: Optional[int] = None) -> Optional[str]:
    """Retrieve the most recent query for a channel."""
    cid = channel_id if channel_id is not None else get_active_channel_id()
    return _CHANNEL_LAST_EXAMPLE_QUERY.get(cid)


def clear_channel_example_state(channel_id: Optional[int] = None) -> None:
    """Clear per-channel state (for one channel if specified, else all)."""
    if channel_id is not None:
        _CHANNEL_LAST_EXAMPLE_QUERY.pop(channel_id, None)
    else:
        _CHANNEL_LAST_EXAMPLE_QUERY.clear()


def enrich_example_tool_args(args: dict, history: List[dict]) -> dict:
    """Enrich tool arguments with per-channel context when the LLM omits a follow-up query."""
    enriched = dict(args or {})
    if not enriched.get("query"):
        last_q = get_channel_last_example_query()
        if last_q:
            enriched["query"] = last_q
    return enriched


def detect_missed_example_tool_call(
    user_text: str,
    reply_text: str,
    history: List[dict],
) -> Optional[Tuple[str, dict]]:
    """Optional turn-0 guardrail: return `('skill_example_info', {...})` if the LLM skipped the tool."""
    return None

