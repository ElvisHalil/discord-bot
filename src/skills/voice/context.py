"""
Per-channel voice session state, tool argument enrichment, and deterministic missed-tool
detection for the Discord Voice-Channel AI Co-Host skill (`src/skills/voice/context.py`).
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from src.context import get_active_channel_id
from src.skills.voice.catalogs import (
    VOICE_ACTION_ALIASES,
    VOICE_MODE_ALIASES,
    VOICE_PROFILE_ALIASES,
    VOICE_PROFILES,
)
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
_EXPLICIT_VOICE_PROFILE_RE = re.compile(
    r"\b(?:change|switch|set|use)\s+(?:your\s+|the\s+|a\s+)?(?:voice|accent|tone|persona)\b|"
    r"\b(?:sound\s+like|speak\s+in|talk\s+in)\s+(?:a\s+|an\s+)?(?:british|uk|english|australian|aussie|female|woman|girl|deep|sportscaster|announcer|canadian|casual)\b",
    re.IGNORECASE,
)


def _extract_voice_profile_from_text(text: str) -> Optional[str]:
    """Extract a canonical voice_profile preset from a natural-language prompt if mentioned."""
    # Strip 'voice chat' / 'voice channel' / 'voice call' phrases before matching preset names like 'chat'
    low = re.sub(r"\bvoice\s+(?:chat|channel|call)\b", "", (text or "").lower())
    if "british" in low and ("female" in low or "woman" in low or "sonia" in low):
        return "sonia"
    if "canadian" in low and ("female" in low or "woman" in low or "clara" in low):
        return "clara"
    for alias, target in sorted(VOICE_PROFILE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in ("chat", "research") and not _EXPLICIT_VOICE_PROFILE_RE.search(text or ""):
            continue
        if re.search(rf"\b{re.escape(alias)}\b", low):
            return target
    for prof_id in VOICE_PROFILES:
        if prof_id in ("chat", "research") and not _EXPLICIT_VOICE_PROFILE_RE.search(text or ""):
            continue
        if re.search(rf"\b{re.escape(prof_id)}\b", low):
            return prof_id
    return None


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

    raw_prof = str(enriched.get("voice_profile") or "").strip().lower()
    if raw_prof in VOICE_PROFILE_ALIASES:
        enriched["voice_profile"] = VOICE_PROFILE_ALIASES[raw_prof]
    elif not raw_prof and latest_user_text:
        inferred_prof = _extract_voice_profile_from_text(latest_user_text)
        if inferred_prof:
            enriched["voice_profile"] = inferred_prof

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

    if latest_user_text and not enriched.get("query") and enriched.get("action") != str(VoiceAction.SPEAK):
        enriched["query"] = latest_user_text

    return enriched


def detect_missed_voice_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """
    Detect when a 4B/8B model forgets to call `skill_voice_info` on turn 0 for an explicit
    voice-channel join, leave, speak, voice profile switch, or voice recap request.
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
        args = {"action": str(VoiceAction.JOIN), "query": latest_user_text, "mode": mode}
        prof = _extract_voice_profile_from_text(latest_user_text)
        if prof:
            args["voice_profile"] = prof
        return ("skill_voice_info", args)

    if _EXPLICIT_VOICE_LEAVE_RE.search(q_low):
        return ("skill_voice_info", {"action": str(VoiceAction.LEAVE), "query": latest_user_text})

    if _EXPLICIT_VOICE_RECAP_RE.search(q_low):
        return ("skill_voice_info", {"action": str(VoiceAction.RECAP), "query": latest_user_text})

    m_speak = _EXPLICIT_VOICE_SPEAK_RE.search(latest_user_text)
    if m_speak:
        return ("skill_voice_info", {"action": str(VoiceAction.SPEAK), "query": m_speak.group(1).strip()})

    if _EXPLICIT_VOICE_PROFILE_RE.search(latest_user_text):
        prof = _extract_voice_profile_from_text(latest_user_text)
        if prof:
            return ("skill_voice_info", {"action": str(VoiceAction.STATUS), "voice_profile": prof, "query": latest_user_text})

    return None

