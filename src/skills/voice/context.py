"""
Per-channel voice session state, tool argument enrichment, and deterministic missed-tool
detection for the Discord Voice-Channel AI Co-Host skill (`src/skills/voice/context.py`).
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from src.context import get_active_channel_id
from src.skills.voice.catalogs import VOICE_ACTION_ALIASES, VOICE_MODE_ALIASES
from src.skills.voice.enums import VoiceAction, VoiceMode
from src.skills.voice.models import VoiceSessionRecord
from src.utils.cache import BoundedCache

_CHANNEL_VOICE_SESSIONS: Dict[int, VoiceSessionRecord] = BoundedCache(maxsize=128)

_EXPLICIT_VOICE_JOIN_RE = re.compile(
    r"\b(?:join|connect\s+to|hop\s+in(?:to)?|come\s+in(?:to)?|enter)\s+(?:the\s+|our\s+|my\s+)?(?:voice(?:\s+channel|\s+chat|\s+call)?|vc)\b",
    re.IGNORECASE,
)
_EXPLICIT_VOICE_LEAVE_RE = re.compile(
    r"\b(?:leave|disconnect\s+from|hop\s+out\s+of|exit)\s+(?:the\s+|our\s+|my\s+)?(?:voice(?:\s+channel|\s+chat|\s+call)?|vc)\b",
    re.IGNORECASE,
)
_EXPLICIT_VOICE_RECAP_RE = re.compile(
    r"\b(?:recap|summarize|summary\s+of|transcript\s+of|what\s+did\s+we\s+talk\s+about\s+in)\s+(?:the\s+|our\s+)?(?:voice(?:\s+channel|\s+chat|\s+call)?|vc)\b",
    re.IGNORECASE,
)
_EXPLICIT_VOICE_SPEAK_RE = re.compile(
    r"\b(?:say|announce|speak|read)\s+(?:in(?:to)?\s+(?:the\s+)?(?:voice(?:\s+channel|\s+chat)?|vc)\s+)(.+)$",
    re.IGNORECASE,
)


def _resolve_cid(channel_id: Optional[int] = None) -> int:
    return channel_id if channel_id is not None else (get_active_channel_id() or 0)


def get_channel_voice_session(channel_id: Optional[int] = None) -> Optional[VoiceSessionRecord]:
    """Return the active VoiceSessionRecord for `channel_id`, if any."""
    return _CHANNEL_VOICE_SESSIONS.get(_resolve_cid(channel_id))


def set_channel_voice_session(
    session: VoiceSessionRecord,
    channel_id: Optional[int] = None,
) -> VoiceSessionRecord:
    """Store or update the active VoiceSessionRecord for `channel_id`."""
    cid = _resolve_cid(channel_id or session.channel_id)
    _CHANNEL_VOICE_SESSIONS[cid] = session
    return session


def clear_channel_voice_state(channel_id: Optional[int] = None) -> None:
    """Clear per-channel voice session state (all channels when `channel_id` is None)."""
    if channel_id is None:
        _CHANNEL_VOICE_SESSIONS.clear()
        return
    _CHANNEL_VOICE_SESSIONS.pop(_resolve_cid(channel_id), None)


def enrich_voice_tool_args(
    args: dict,
    history: list,
    active_channel_id: Optional[int] = None,
) -> dict:
    """Normalize and enrich `skill_voice_info` arguments from the user's latest prompt."""
    enriched = dict(args or {})
    latest_user_text = ""
    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            latest_user_text = re.sub(r"^User\s+[^:]+:\s*", "", latest_user_text).strip()
            break

    raw_action = str(enriched.get("action") or "").strip().lower()
    if raw_action in VOICE_ACTION_ALIASES:
        enriched["action"] = str(VOICE_ACTION_ALIASES[raw_action])

    raw_mode = str(enriched.get("mode") or "").strip().lower()
    if raw_mode in VOICE_MODE_ALIASES:
        enriched["mode"] = str(VOICE_MODE_ALIASES[raw_mode])

    if latest_user_text and not enriched.get("action"):
        q_low = latest_user_text.lower()
        if _EXPLICIT_VOICE_JOIN_RE.search(q_low):
            enriched["action"] = str(VoiceAction.JOIN)
        elif _EXPLICIT_VOICE_LEAVE_RE.search(q_low):
            enriched["action"] = str(VoiceAction.LEAVE)
        elif _EXPLICIT_VOICE_RECAP_RE.search(q_low):
            enriched["action"] = str(VoiceAction.RECAP)
        elif _EXPLICIT_VOICE_SPEAK_RE.search(latest_user_text):
            m = _EXPLICIT_VOICE_SPEAK_RE.search(latest_user_text)
            enriched["action"] = str(VoiceAction.SPEAK)
            if m and not enriched.get("query"):
                enriched["query"] = m.group(1).strip()

    if latest_user_text and not enriched.get("query"):
        enriched["query"] = latest_user_text

    return enriched


def detect_missed_voice_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """
    Detect when a 4B/8B model forgets to call `skill_voice_info` on turn 0 for an explicit
    voice-channel join, leave, speak, or voice recap request.
    """
    if not history:
        return None

    latest_user_text = ""
    for msg in reversed(history):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            latest_user_text = re.sub(r"^User\s+[^:]+:\s*", "", latest_user_text).strip()
            break
    if not latest_user_text or latest_user_text.startswith("!"):
        return None

    q_low = latest_user_text.lower()
    if _EXPLICIT_VOICE_JOIN_RE.search(q_low):
        mode = str(VoiceMode.CONVERSATIONAL) if ("open mic" in q_low or "conversational" in q_low) else str(VoiceMode.WAKE_WORD)
        return ("skill_voice_info", {"action": str(VoiceAction.JOIN), "query": latest_user_text, "mode": mode})

    if _EXPLICIT_VOICE_LEAVE_RE.search(q_low):
        return ("skill_voice_info", {"action": str(VoiceAction.LEAVE), "query": latest_user_text})

    if _EXPLICIT_VOICE_RECAP_RE.search(q_low):
        return ("skill_voice_info", {"action": str(VoiceAction.RECAP), "query": latest_user_text})

    m_speak = _EXPLICIT_VOICE_SPEAK_RE.search(latest_user_text)
    if m_speak:
        return ("skill_voice_info", {"action": str(VoiceAction.SPEAK), "query": m_speak.group(1).strip()})

    return None
