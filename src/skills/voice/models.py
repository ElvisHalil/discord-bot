"""
Typed slotted dataclasses (`@dataclass(slots=True)`) for the Discord Voice-Channel AI Co-Host skill (`src/skills/voice/models.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .enums import SttBackend, TtsBackend, VoiceMode, VoiceSessionState


@dataclass(slots=True)
class VoiceProfileSpec:
    """Configuration record for a persona-matched neural TTS voice profile."""

    profile_id: str
    display_name: str
    edge_voice: str
    rate_pct: int = 0
    pitch_hz: int = 0
    speaking_style: str = "conversational"


@dataclass(slots=True)
class SpeakerUtterance:
    """A single segmented and transcribed spoken utterance from a Discord voice channel member."""

    user_id: int
    display_name: str
    transcript: str
    duration_sec: float
    rms_dbfs: float
    timestamp: float
    stt_backend: str = SttBackend.GEMMA_NATIVE
    is_wake_word_triggered: bool = False
    bot_reply: Optional[str] = None


@dataclass(slots=True)
class TtsAudioClip:
    """Synthesized speech audio clip ready for in-memory PCM conversion and Discord voice playback."""

    text: str
    spoken_text: str
    audio_bytes: bytes
    audio_format: str = "wav"
    sample_rate: int = 24000
    duration_sec: float = 0.0
    voice_id: str = "en-CA-LiamNeural"
    backend: str = TtsBackend.WAVE_SYNTH


@dataclass(slots=True)
class VoiceSessionRecord:
    """Live state of an active Discord voice-channel AI co-host session."""

    channel_id: int
    voice_channel_id: int
    voice_channel_name: str
    guild_id: Optional[int] = None
    state: str = VoiceSessionState.IDLE
    mode: str = VoiceMode.WAKE_WORD
    voice_profile: str = "research"
    stt_backend: str = SttBackend.AUTO
    tts_backend: str = TtsBackend.AUTO
    started_at: float = 0.0
    last_activity_at: float = 0.0
    utterances_count: int = 0
    spoken_replies_count: int = 0
    announce_live_trackers: bool = True
    is_muted: bool = False
    utterances: List[SpeakerUtterance] = field(default_factory=list)

