"""
Discord voice playback and per-speaker audio receive sink (`src/skills/voice/playback/`).
"""

from .discord_audio import (
    PerSpeakerVoiceAccumulator,
    attach_discord_voice_receiver,
    build_discord_pcm_source,
)

__all__ = [
    "PerSpeakerVoiceAccumulator",
    "attach_discord_voice_receiver",
    "build_discord_pcm_source",
]
