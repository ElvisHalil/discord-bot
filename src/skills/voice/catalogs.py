"""
Static voice profile catalogs, wake-word vocabularies, and command alias tables (`src/skills/voice/catalogs.py`).
"""

from typing import Dict, Tuple

from .enums import VoiceAction, VoiceMode
from .models import VoiceProfileSpec

VOICE_PROFILES: Dict[str, VoiceProfileSpec] = {
    "research": VoiceProfileSpec(
        profile_id="research",
        display_name="Broadcast Desk Analyst (Canadian English)",
        edge_voice="en-CA-LiamNeural",
        rate_pct=4,
        pitch_hz=0,
        speaking_style="clear, authoritative broadcast co-host",
    ),
    "research-gemma": VoiceProfileSpec(
        profile_id="research-gemma",
        display_name="Broadcast Desk Analyst (Canadian English)",
        edge_voice="en-CA-LiamNeural",
        rate_pct=4,
        pitch_hz=0,
        speaking_style="clear, authoritative broadcast co-host",
    ),
    "chat": VoiceProfileSpec(
        profile_id="chat",
        display_name="Casual Hangout Co-Host",
        edge_voice="en-US-AndrewMultilingualNeural",
        rate_pct=6,
        pitch_hz=-2,
        speaking_style="relaxed, witty Discord friend",
    ),
    "chappie": VoiceProfileSpec(
        profile_id="chappie",
        display_name="Chappie Neural Voice",
        edge_voice="en-GB-RyanNeural",
        rate_pct=10,
        pitch_hz=6,
        speaking_style="energetic, curious third-person AI sidekick",
    ),
    "sportscaster": VoiceProfileSpec(
        profile_id="sportscaster",
        display_name="Live Stadium Commentator",
        edge_voice="en-US-GuyNeural",
        rate_pct=12,
        pitch_hz=2,
        speaking_style="high-energy live play-by-play announcer",
    ),
}

DEFAULT_WAKE_WORDS: Tuple[str, ...] = (
    "hey chappie",
    "yo chappie",
    "ok chappie",
    "chappie",
    "hey bot",
    "yo bot",
    "ok bot",
    "hey cohost",
    "hey co-host",
    "cohost",
    "co-host",
    "hey assistant",
)

VOICE_ACTION_ALIASES: Dict[str, str] = {
    "join": VoiceAction.JOIN,
    "connect": VoiceAction.JOIN,
    "hop in": VoiceAction.JOIN,
    "come in": VoiceAction.JOIN,
    "start": VoiceAction.JOIN,
    "enter": VoiceAction.JOIN,
    "leave": VoiceAction.LEAVE,
    "disconnect": VoiceAction.LEAVE,
    "hop out": VoiceAction.LEAVE,
    "stop": VoiceAction.LEAVE,
    "exit": VoiceAction.LEAVE,
    "speak": VoiceAction.SPEAK,
    "say": VoiceAction.SPEAK,
    "announce": VoiceAction.SPEAK,
    "read": VoiceAction.SPEAK,
    "talk": VoiceAction.SPEAK,
    "listen": VoiceAction.LISTEN,
    "hear": VoiceAction.LISTEN,
    "transcribe": VoiceAction.TRANSCRIBE,
    "status": VoiceAction.STATUS,
    "info": VoiceAction.STATUS,
    "state": VoiceAction.STATUS,
    "recap": VoiceAction.RECAP,
    "summary": VoiceAction.RECAP,
    "summarize": VoiceAction.RECAP,
    "transcript": VoiceAction.RECAP,
    "minutes": VoiceAction.RECAP,
    "mute": VoiceAction.MUTE,
    "quiet": VoiceAction.MUTE,
    "shush": VoiceAction.MUTE,
    "unmute": VoiceAction.UNMUTE,
    "mode": VoiceAction.MODE,
}

VOICE_MODE_ALIASES: Dict[str, str] = {
    "wake": VoiceMode.WAKE_WORD,
    "wake_word": VoiceMode.WAKE_WORD,
    "wakeword": VoiceMode.WAKE_WORD,
    "conversational": VoiceMode.CONVERSATIONAL,
    "always": VoiceMode.CONVERSATIONAL,
    "open_mic": VoiceMode.CONVERSATIONAL,
    "open mic": VoiceMode.CONVERSATIONAL,
    "free": VoiceMode.CONVERSATIONAL,
    "listen_only": VoiceMode.LISTEN_ONLY,
    "listen only": VoiceMode.LISTEN_ONLY,
    "passive": VoiceMode.LISTEN_ONLY,
    "scribe": VoiceMode.LISTEN_ONLY,
    "ptt": VoiceMode.PUSH_TO_TALK,
    "push_to_talk": VoiceMode.PUSH_TO_TALK,
}

VOICE_QUERY_STOPWORDS: frozenset[str] = frozenset({
    "voice", "channel", "vc", "call", "chat", "please", "can", "you", "the",
    "into", "in", "to", "from", "our", "my", "right", "now", "cohost", "co-host",
})

