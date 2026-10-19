"""
Text-to-Speech (TTS) aggregator (`src/skills/voice/aggregators/tts.py`):
1. Local HTTP Neural TTS (`local_http`):
   Connects to an optional local Kokoro-82M / Piper / OpenAI-compatible `/v1/audio/speech` endpoint
   configured via `VOICE_TTS_URL`.
2. Edge Neural TTS (`edge_neural`):
   Uses `edge_tts` (if installed) to synthesize ultra-low-latency neural voices (`en-CA-LiamNeural`,
   `en-US-AndrewMultilingualNeural`, `en-GB-RyanNeural`).
3. In-Memory Waveform Synthesizer (`wave_synth`):
   Zero-dependency, offline fallback that generates a valid 24 kHz 16-bit mono WAV audio buffer
   proportional to the spoken utterance so Discord voice playback and unit tests always succeed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
from typing import Optional

from src.skills.voice.aggregators.base import BaseVoiceAggregator
from src.skills.voice.catalogs import VOICE_PROFILES
from src.skills.voice.engine.vad import sanitize_text_for_speech, synthesize_tone_envelope_wav
from src.skills.voice.enums import TtsBackend
from src.skills.voice.models import TtsAudioClip, VoiceProfileSpec
from src.utils.cache import BoundedCache
from src.utils.http import ThreadLocalRequestsSession

logger = logging.getLogger(__name__)

_TTS_CACHE_LOCK = threading.Lock()
_TTS_CACHE: BoundedCache = BoundedCache(maxsize=128)
_HTTP_SESSION = ThreadLocalRequestsSession()


def clear_voice_tts_cache() -> None:
    """Clear the in-memory synthesized speech audio cache."""
    with _TTS_CACHE_LOCK:
        _TTS_CACHE.clear()


class VoiceTtsAggregator(BaseVoiceAggregator):
    """Multi-backend Text-to-Speech synthesizer with persona-matched voice profiles and bounded LRU caching."""

    def resolve_voice_profile(self, profile_or_mode: Optional[str] = None) -> VoiceProfileSpec:
        """Resolve a mode or profile key (`research`, `chat`, `chappie`, `sportscaster`) to a VoiceProfileSpec."""
        key = (profile_or_mode or "research").strip().lower()
        return VOICE_PROFILES.get(key, VOICE_PROFILES["research"])

    def synthesize_speech(
        self,
        text: str,
        profile_or_mode: Optional[str] = "research",
        backend: str = TtsBackend.AUTO,
        max_chars: int = 380,
    ) -> TtsAudioClip:
        """
        Sanitize `text` for spoken delivery and synthesize it into a `TtsAudioClip`.
        """
        spoken = sanitize_text_for_speech(text, max_chars=max_chars)
        if not spoken:
            spoken = "Ready."

        profile = self.resolve_voice_profile(profile_or_mode)
        raw_backend = (backend or "").strip().lower()
        preferred = (
            os.getenv("VOICE_TTS_BACKEND", TtsBackend.AUTO).strip().lower()
            if (not raw_backend or raw_backend == TtsBackend.AUTO)
            else raw_backend
        )

        cache_key = hashlib.sha256(f"{profile.profile_id}|{preferred}|{spoken}".encode("utf-8")).hexdigest()[:24]
        with _TTS_CACHE_LOCK:
            cached = _TTS_CACHE.get(cache_key)
            if cached is not None:
                return cached

        # 1. Local HTTP Neural TTS endpoint (Kokoro-82M / Piper / OpenAI-compatible /v1/audio/speech)
        tts_url = os.getenv("VOICE_TTS_URL", "").strip()
        if preferred in (TtsBackend.LOCAL_HTTP, TtsBackend.AUTO) and tts_url:
            clip = self._synthesize_via_local_http(text, spoken, profile, tts_url)
            if clip is not None:
                with _TTS_CACHE_LOCK:
                    _TTS_CACHE[cache_key] = clip
                return clip

        # 2. Edge Neural TTS (if `edge_tts` package is available)
        if preferred in (TtsBackend.EDGE_NEURAL, TtsBackend.AUTO):
            clip = self._synthesize_via_edge_tts(text, spoken, profile)
            if clip is not None:
                with _TTS_CACHE_LOCK:
                    _TTS_CACHE[cache_key] = clip
                return clip

        # 3. Deterministic In-Memory WAV Tone-Envelope Fallback (zero external dependencies)
        base_freq = 215.0 if profile.profile_id == "chappie" else (185.0 if profile.profile_id == "chat" else 170.0)
        wav_bytes, duration_sec = synthesize_tone_envelope_wav(spoken, sample_rate=24000, base_freq_hz=base_freq)
        clip = TtsAudioClip(
            text=text,
            spoken_text=spoken,
            audio_bytes=wav_bytes,
            audio_format="wav",
            sample_rate=24000,
            duration_sec=duration_sec,
            voice_id=profile.edge_voice,
            backend=str(TtsBackend.WAVE_SYNTH),
        )
        with _TTS_CACHE_LOCK:
            _TTS_CACHE[cache_key] = clip
        return clip

    def _synthesize_via_local_http(
        self,
        raw_text: str,
        spoken_text: str,
        profile: VoiceProfileSpec,
        tts_url: str,
    ) -> Optional[TtsAudioClip]:
        """Call a local Kokoro / Piper / OpenAI-compatible HTTP TTS endpoint."""
        try:
            resp = _HTTP_SESSION.post(
                tts_url,
                json={
                    "input": spoken_text,
                    "voice": profile.edge_voice,
                    "response_format": "wav",
                },
                timeout=8,
            )
            if resp.status_code == 200 and resp.content and len(resp.content) > 100:
                word_count = max(1, len(spoken_text.split()))
                est_dur = round(word_count * 0.32, 2)
                fmt = "mp3" if resp.content[:3] == b"ID3" or resp.content[:2] == b"\xff\xfb" else "wav"
                return TtsAudioClip(
                    text=raw_text,
                    spoken_text=spoken_text,
                    audio_bytes=resp.content,
                    audio_format=fmt,
                    sample_rate=24000,
                    duration_sec=est_dur,
                    voice_id=profile.edge_voice,
                    backend=str(TtsBackend.LOCAL_HTTP),
                )
        except Exception as e:
            logger.debug("Local HTTP TTS synthesis failed: %s", e)
        return None

    def _synthesize_via_edge_tts(
        self,
        raw_text: str,
        spoken_text: str,
        profile: VoiceProfileSpec,
    ) -> Optional[TtsAudioClip]:
        """Synthesize MP3 neural speech via `edge_tts` when installed."""
        try:
            import importlib

            if importlib.util.find_spec("edge_tts") is None:
                return None
            edge_tts = importlib.import_module("edge_tts")
        except Exception:
            return None

        rate_str = f"{profile.rate_pct:+d}%"
        pitch_str = f"{profile.pitch_hz:+d}Hz"

        async def _collect_mp3() -> bytes:
            communicate = edge_tts.Communicate(
                spoken_text,
                voice=profile.edge_voice,
                rate=rate_str,
                pitch=pitch_str,
            )
            chunks = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        try:
            loop = asyncio.new_event_loop()
            try:
                mp3_bytes = loop.run_until_complete(asyncio.wait_for(_collect_mp3(), timeout=6.0))
            finally:
                loop.close()
            if mp3_bytes and len(mp3_bytes) > 100:
                word_count = max(1, len(spoken_text.split()))
                est_dur = round(word_count * 0.32, 2)
                return TtsAudioClip(
                    text=raw_text,
                    spoken_text=spoken_text,
                    audio_bytes=mp3_bytes,
                    audio_format="mp3",
                    sample_rate=24000,
                    duration_sec=est_dur,
                    voice_id=profile.edge_voice,
                    backend=str(TtsBackend.EDGE_NEURAL),
                )
        except Exception as e:
            logger.debug("edge_tts synthesis failed: %s", e)
        return None

