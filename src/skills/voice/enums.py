"""
String enumerations (`StrEnum`) for the Discord Voice-Channel AI Co-Host skill (`src/skills/voice/enums.py`).
"""

from enum import StrEnum


class VoiceAction(StrEnum):
    """Supported actions for `skill_voice_info` and `!voice` commands."""

    JOIN = "join"
    LEAVE = "leave"
    SPEAK = "speak"
    LISTEN = "listen"
    STATUS = "status"
    RECAP = "recap"
    MODE = "mode"
    MUTE = "mute"
    UNMUTE = "unmute"
    TRANSCRIBE = "transcribe"


class VoiceMode(StrEnum):
    """Listening activation mode for an active Discord voice-channel co-host session."""

    WAKE_WORD = "wake_word"
    CONVERSATIONAL = "conversational"
    LISTEN_ONLY = "listen_only"
    PUSH_TO_TALK = "push_to_talk"


class VoiceSessionState(StrEnum):
    """Lifecycle states of a Discord voice-channel co-host session."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    MUTED = "muted"


class SttBackend(StrEnum):
    """Speech-to-Text engine backends."""

    AUTO = "auto"
    GEMMA_NATIVE = "gemma_native"
    FASTER_WHISPER = "faster_whisper"


class TtsBackend(StrEnum):
    """Text-to-Speech synthesis backends."""

    AUTO = "auto"
    EDGE_NEURAL = "edge_neural"
    LOCAL_HTTP = "local_http"
    WAVE_SYNTH = "wave_synth"
