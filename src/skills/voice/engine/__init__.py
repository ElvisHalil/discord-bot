"""
Voice signal processing, Voice Activity Detection (VAD), PCM/WAV transcoding,
wake-word extraction, and TTS speech sanitization (`src/skills/voice/engine/`).
"""

from .vad import (
    audio_bytes_to_discord_pcm_48k_stereo,
    compute_pcm_rms_dbfs,
    extract_wake_word_command,
    is_speech_frame,
    pcm_48k_stereo_to_16k_mono_wav,
    sanitize_text_for_speech,
    synthesize_tone_envelope_wav,
)

__all__ = [
    "audio_bytes_to_discord_pcm_48k_stereo",
    "compute_pcm_rms_dbfs",
    "extract_wake_word_command",
    "is_speech_frame",
    "pcm_48k_stereo_to_16k_mono_wav",
    "sanitize_text_for_speech",
    "synthesize_tone_envelope_wav",
]
