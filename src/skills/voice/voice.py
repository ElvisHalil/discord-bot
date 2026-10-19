"""
LLM-facing tool entrypoint for the Discord Voice-Channel AI Co-Host skill (`src/skills/voice/voice.py`).
"""

from __future__ import annotations

from typing import Optional

from src.context import get_active_channel_id
from src.skills.voice.catalogs import VOICE_ACTION_ALIASES, VOICE_MODE_ALIASES, VOICE_PROFILES
from src.skills.voice.enums import VoiceAction, VoiceMode
from src.skills.voice.templates import build_voice_llm_instructions
from src.skills.voice.tracker import VOICE_COHOST


def skill_voice_info(
    action: Optional[str] = VoiceAction.STATUS,
    query: Optional[str] = "",
    mode: Optional[str] = "",
    voice_profile: Optional[str] = "",
) -> str:
    """
    Control and query the Real-Time Discord Voice-Channel AI Co-Host:
    - `action="join"`: Join or configure the active voice co-host session for the channel.
    - `action="leave"`: Disconnect the bot from the active Discord voice channel.
    - `action="speak"`: Synthesize `query` via Neural TTS and speak it out loud in the voice channel.
    - `action="recap"`: Retrieve a speaker-attributed chronological transcript of the voice call.
    - `action="mode"`: Switch listening mode between `wake_word`, `conversational` (open mic), and `listen_only`.
    - `action="mute"` / `"unmute"`: Mute or unmute spoken voice responses.
    - `action="status"`: View current voice channel connection, active mode, voice profile, and utterance stats.

    Args:
        action: One of 'join', 'leave', 'speak', 'recap', 'mode', 'mute', 'unmute', or 'status'.
        query: Text to speak aloud (when action='speak') or channel/query context.
        mode: Listening mode ('wake_word', 'conversational', or 'listen_only').
        voice_profile: Persona voice preset ('research', 'chat', 'chappie', or 'sportscaster').
    """
    cid = get_active_channel_id() or 0
    raw_act = (action or VoiceAction.STATUS).strip().lower()
    act = VOICE_ACTION_ALIASES.get(raw_act, raw_act)
    clean_q = (query or "").strip()

    if act == VoiceAction.JOIN:
        sess = VOICE_COHOST.get_session(cid)
        target_mode = VOICE_MODE_ALIASES.get((mode or "").strip().lower(), str(VoiceMode.WAKE_WORD))
        prof_key = (voice_profile or (sess.voice_profile if sess else "research")).strip().lower()
        if prof_key not in VOICE_PROFILES:
            prof_key = "research"

        if sess is None:
            sess = VOICE_COHOST.start_session(
                channel_id=cid,
                voice_channel_id=cid or 1,
                voice_channel_name=clean_q if (clean_q and len(clean_q) <= 32 and "join" not in clean_q.lower()) else "Discord Voice Channel",
                mode=target_mode,
                voice_profile=prof_key,
            )
        else:
            if mode:
                VOICE_COHOST.set_mode(cid, target_mode)
            if voice_profile and prof_key in VOICE_PROFILES:
                sess.voice_profile = prof_key

        status_block = VOICE_COHOST.format_session_status(cid)
        return status_block + build_voice_llm_instructions(action=VoiceAction.JOIN)

    if act == VoiceAction.LEAVE:
        stopped = VOICE_COHOST.stop_session(cid)
        if stopped is not None:
            return (
                f"=== 🎙️ VOICE CO-HOST DISCONNECTED ===\n"
                f"Left voice channel **{stopped.voice_channel_name}** after logging "
                f"**{stopped.utterances_count}** utterances and **{stopped.spoken_replies_count}** spoken replies."
                + build_voice_llm_instructions(action=VoiceAction.LEAVE)
            )
        return (
            "=== 🎙️ VOICE CO-HOST DISCONNECTED ===\n"
            "No active voice channel session was connected in this channel."
            + build_voice_llm_instructions(action=VoiceAction.LEAVE)
        )

    if act == VoiceAction.SPEAK:
        if not clean_q:
            return "Please provide the text or announcement to speak in `query`."
        clip, played = VOICE_COHOST.synthesize_and_play(cid, clean_q, profile_or_mode=voice_profile or None)
        delivery_state = "Streamed live to Discord VoiceClient" if played else "Synthesized audio clip ready"
        return (
            f"=== 🔊 VOICE CO-HOST SPOKEN OUTPUT ===\n"
            f"- **Spoken Text:** \"{clip.spoken_text}\"\n"
            f"- **Voice Profile:** `{clip.voice_id}` (Backend: `{clip.backend}`, Duration: {clip.duration_sec:.2f}s)\n"
            f"- **Delivery:** {delivery_state}"
            + build_voice_llm_instructions(action=VoiceAction.SPEAK, is_spoken_turn=True)
        )

    if act == VoiceAction.RECAP:
        return VOICE_COHOST.format_session_recap(cid) + build_voice_llm_instructions(action=VoiceAction.RECAP)

    if act == VoiceAction.MODE:
        target_mode = VOICE_MODE_ALIASES.get(
            (mode or clean_q).strip().lower(),
            str(VoiceMode.CONVERSATIONAL if "open" in clean_q.lower() or "conversational" in clean_q.lower() else VoiceMode.WAKE_WORD),
        )
        sess = VOICE_COHOST.set_mode(cid, target_mode)
        if sess is None:
            sess = VOICE_COHOST.start_session(channel_id=cid, voice_channel_id=cid or 1, mode=target_mode)
        return VOICE_COHOST.format_session_status(cid) + build_voice_llm_instructions(action=VoiceAction.MODE)

    if act in (VoiceAction.MUTE, VoiceAction.UNMUTE):
        is_muted = act == VoiceAction.MUTE
        sess = VOICE_COHOST.set_mute(cid, is_muted=is_muted)
        if sess is None:
            return "No active voice session in this channel to mute/unmute."
        return VOICE_COHOST.format_session_status(cid) + build_voice_llm_instructions(action=act)

    return VOICE_COHOST.format_session_status(cid) + build_voice_llm_instructions(action=VoiceAction.STATUS)

