"""
Static voice profile catalogs, wake-word vocabularies, and command alias tables (`src/skills/voice/catalogs.py`).
"""

from typing import Dict, Tuple

from .enums import VoiceAction, VoiceMode
from .models import VoiceProfileSpec

VOICE_PROFILES: Dict[str, VoiceProfileSpec] = {
    "research": VoiceProfileSpec(
        profile_id="research",
        display_name="Broadcast Desk Analyst (Canadian Male — Liam)",
        edge_voice="en-CA-LiamNeural",
        rate_pct=6,
        pitch_hz=0,
        speaking_style="clear, authoritative broadcast co-host",
    ),
    "research-gemma": VoiceProfileSpec(
        profile_id="research-gemma",
        display_name="Broadcast Desk Analyst (Canadian Male — Liam)",
        edge_voice="en-CA-LiamNeural",
        rate_pct=8,
        pitch_hz=0,
        speaking_style="clear, authoritative broadcast co-host",
    ),
    "research-qwen": VoiceProfileSpec(
        profile_id="research-qwen",
        display_name="Broadcast Desk Analyst (Canadian Male — Liam)",
        edge_voice="en-CA-LiamNeural",
        rate_pct=8,
        pitch_hz=0,
        speaking_style="clear, authoritative broadcast co-host",
    ),
    "chat": VoiceProfileSpec(
        profile_id="chat",
        display_name="Casual Hangout Co-Host (US Male — Andrew)",
        edge_voice="en-US-AndrewMultilingualNeural",
        rate_pct=10,
        pitch_hz=-2,
        speaking_style="relaxed, witty Discord friend",
    ),
    "sportscaster": VoiceProfileSpec(
        profile_id="sportscaster",
        display_name="Live Stadium Commentator (US Male — Guy)",
        edge_voice="en-US-GuyNeural",
        rate_pct=12,
        pitch_hz=2,
        speaking_style="high-energy live play-by-play announcer",
    ),
    "british": VoiceProfileSpec(
        profile_id="british",
        display_name="British Co-Host (UK Male — Ryan)",
        edge_voice="en-GB-RyanNeural",
        rate_pct=8,
        pitch_hz=0,
        speaking_style="crisp, dry-witted British co-host",
    ),
    "sonia": VoiceProfileSpec(
        profile_id="sonia",
        display_name="British Female Co-Host (UK Female — Sonia)",
        edge_voice="en-GB-SoniaNeural",
        rate_pct=8,
        pitch_hz=0,
        speaking_style="polished, articulate British co-host",
    ),
    "ava": VoiceProfileSpec(
        profile_id="ava",
        display_name="Expressive Female Co-Host (US Female — Ava)",
        edge_voice="en-US-AvaMultilingualNeural",
        rate_pct=8,
        pitch_hz=0,
        speaking_style="warm, expressive conversational co-host",
    ),
    "clara": VoiceProfileSpec(
        profile_id="clara",
        display_name="Canadian Female Co-Host (Canadian Female — Clara)",
        edge_voice="en-CA-ClaraNeural",
        rate_pct=8,
        pitch_hz=0,
        speaking_style="friendly, clear Canadian co-host",
    ),
    "deep": VoiceProfileSpec(
        profile_id="deep",
        display_name="Deep Baritone Narrator (US Male — Christopher)",
        edge_voice="en-US-ChristopherNeural",
        rate_pct=6,
        pitch_hz=-6,
        speaking_style="calm, deep studio baritone",
    ),
    "australian": VoiceProfileSpec(
        profile_id="australian",
        display_name="Australian Co-Host (AU Male — William)",
        edge_voice="en-AU-WilliamNeural",
        rate_pct=8,
        pitch_hz=0,
        speaking_style="upbeat, laid-back Australian co-host",
    ),
}

VOICE_PROFILE_ALIASES: Dict[str, str] = {
    "liam": "research",
    "canadian": "research",
    "andrew": "chat",
    "casual": "chat",
    "guy": "sportscaster",
    "sports": "sportscaster",
    "commentator": "sportscaster",
    "ryan": "british",
    "uk": "british",
    "english": "british",
    "british_female": "sonia",
    "uk_female": "sonia",
    "female": "ava",
    "girl": "ava",
    "woman": "ava",
    "canadian_female": "clara",
    "christopher": "deep",
    "baritone": "deep",
    "low": "deep",
    "william": "australian",
    "aussie": "australian",
    "au": "australian",
}

DEFAULT_WAKE_WORDS: Tuple[str, ...] = (
    "hey bot",
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

