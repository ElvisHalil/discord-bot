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
from src.skills.voice.catalogs import VOICE_PROFILE_ALIASES, VOICE_PROFILES
from src.skills.voice.engine.vad import sanitize_text_for_speech, synthesize_tone_envelope_wav
from src.skills.voice.enums import TtsBackend
from src.skills.voice.models import TtsAudioClip, VoiceProfileSpec
from src.utils.cache import BoundedCache
from src.utils.http import ThreadLocalRequestsSession

logger = logging.getLogger(__name__)

_TTS_CACHE_LOCK = threading.Lock()
_TTS_CACHE: BoundedCache = BoundedCache(maxsize=128)
_HTTP_SESSION = ThreadLocalRequestsSession()
_EDGE_LOOP_LOCK = threading.Lock()
_EDGE_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _get_edge_tts_loop() -> asyncio.AbstractEventLoop:
    """Return a persistent background asyncio event loop for low-latency Edge TTS synthesis."""
    global _EDGE_LOOP
    if _EDGE_LOOP is not None and _EDGE_LOOP.is_running():
        return _EDGE_LOOP
    with _EDGE_LOOP_LOCK:
        if _EDGE_LOOP is None or not _EDGE_LOOP.is_running():
            loop = asyncio.new_event_loop()
            t = threading.Thread(target=loop.run_forever, name="edge-tts-loop", daemon=True)
            t.start()
            _EDGE_LOOP = loop
        return _EDGE_LOOP


def clear_voice_tts_cache() -> None:
    """Clear the in-memory synthesized speech audio cache."""
    with _TTS_CACHE_LOCK:
        _TTS_CACHE.clear()


class VoiceTtsAggregator(BaseVoiceAggregator):
    """Multi-backend Text-to-Speech synthesizer with persona-matched voice profiles and bounded LRU caching."""

    def resolve_voice_profile(self, profile_or_mode: Optional[str] = None) -> VoiceProfileSpec:
        """Resolve a mode, preset alias, or Edge Neural voice ID to a `VoiceProfileSpec`."""
        raw = (profile_or_mode or os.getenv("VOICE_TTS_PROFILE", "research")).strip()
        key = VOICE_PROFILE_ALIASES.get(raw.lower(), raw.lower())
        base = VOICE_PROFILES.get(key, VOICE_PROFILES["research"])

        # Allow direct Edge Neural voice IDs (e.g. `en-GB-RyanNeural`) or `.env` overrides
        env_voice = os.getenv("VOICE_TTS_VOICE", "").strip()
        env_rate = os.getenv("VOICE_TTS_RATE", "").strip()
        env_pitch = os.getenv("VOICE_TTS_PITCH", "").strip()

        custom_voice = env_voice or (raw if raw.endswith("Neural") else base.edge_voice)
        custom_rate = base.rate_pct
        if env_rate:
            try:
                custom_rate = int(env_rate.replace("%", "").replace("+", "").strip())
            except ValueError:
                pass
        custom_pitch = base.pitch_hz
        if env_pitch:
            try:
                custom_pitch = int(env_pitch.lower().replace("hz", "").replace("+", "").strip())
            except ValueError:
                pass

        if (
            custom_voice != base.edge_voice
            or custom_rate != base.rate_pct
            or custom_pitch != base.pitch_hz
        ):
            return VoiceProfileSpec(
                profile_id=base.profile_id if key in VOICE_PROFILES else raw.lower(),
                display_name=base.display_name if key in VOICE_PROFILES else f"Custom ({custom_voice})",
                edge_voice=custom_voice,
                kokoro_voice=base.kokoro_voice,
                rate_pct=custom_rate,
                pitch_hz=custom_pitch,
                speaking_style=base.speaking_style,
            )
        return base

    def synthesize_speech(
        self,
        text: str,
        profile_or_mode: Optional[str] = "research",
        backend: str = TtsBackend.AUTO,
        max_chars: int = 200,
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
        base_freq = 205.0 if profile.profile_id == "sportscaster" else (185.0 if profile.profile_id == "chat" else 170.0)
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
            import socket
            import aiohttp

            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            try:
                communicate = edge_tts.Communicate(
                    spoken_text,
                    voice=profile.edge_voice,
                    rate=rate_str,
                    pitch=pitch_str,
                    connector=connector,
                )
                chunks = []
                async for chunk in communicate.stream():
                    if chunk.get("type") == "audio" and chunk.get("data"):
                        chunks.append(chunk["data"])
                return b"".join(chunks)
            finally:
                await connector.close()

        try:
            loop = _get_edge_tts_loop()
            fut = asyncio.run_coroutine_threadsafe(_collect_mp3(), loop)
            mp3_bytes = fut.result(timeout=8.0)
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

    def synthesize_speech_streaming(
        self,
        text: str,
        profile_or_mode: Optional[str] = "research",
        backend: str = TtsBackend.AUTO,
        max_chars: int = 200,
    ):
        """
        Stream Edge Neural TTS over a single WebSocket and return `(initial_clip, bind_stream_fn)`
        as soon as the first ~1.2s of MP3 audio (~7.2 KB) arrives (~0.65s latency), while the
        remaining WebSocket chunks finish downloading in the background and append to `StreamingPcmAudioSource`.
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
        tts_url = os.getenv("VOICE_TTS_URL", "").strip()
        cache_key = hashlib.sha256(f"{profile.profile_id}|{preferred}|{spoken}".encode("utf-8")).hexdigest()[:24]

        with _TTS_CACHE_LOCK:
            cached = _TTS_CACHE.get(cache_key)
            if cached is not None:
                return cached, None

        if preferred not in (TtsBackend.EDGE_NEURAL, TtsBackend.AUTO) or tts_url:
            return self.synthesize_speech(text, profile_or_mode=profile_or_mode, backend=backend, max_chars=max_chars), None

        try:
            import concurrent.futures
            import importlib
            import socket
            import aiohttp
            from src.skills.voice.engine.vad import audio_bytes_to_discord_pcm_48k_stereo

            if importlib.util.find_spec("edge_tts") is None:
                return self.synthesize_speech(text, profile_or_mode=profile_or_mode, backend=backend, max_chars=max_chars), None
            edge_tts = importlib.import_module("edge_tts")

            rate_str = f"{profile.rate_pct:+d}%"
            pitch_str = f"{profile.pitch_hz:+d}Hz"
            first_ready: concurrent.futures.Future = concurrent.futures.Future()
            rest_pcm_ready: concurrent.futures.Future = concurrent.futures.Future()

            async def _stream_mp3() -> None:
                connector = aiohttp.TCPConnector(family=socket.AF_INET)
                first_buf = bytearray()
                rest_buf = bytearray()
                emitted_first = False
                try:
                    communicate = edge_tts.Communicate(
                        spoken,
                        voice=profile.edge_voice,
                        rate=rate_str,
                        pitch=pitch_str,
                        connector=connector,
                    )
                    async for chunk in communicate.stream():
                        if chunk.get("type") == "audio" and chunk.get("data"):
                            data = chunk["data"]
                            if not emitted_first:
                                first_buf.extend(data)
                                if len(first_buf) >= 7200:
                                    emitted_first = True
                                    first_ready.set_result((bytes(first_buf), False))
                            else:
                                rest_buf.extend(data)
                    if not emitted_first:
                        if len(first_buf) > 100:
                            first_ready.set_result((bytes(first_buf), True))
                        else:
                            first_ready.set_result((b"", True))
                        rest_pcm_ready.set_result(b"")
                    else:
                        full_mp3 = bytes(first_buf) + bytes(rest_buf)
                        word_count = max(1, len(spoken.split()))
                        with _TTS_CACHE_LOCK:
                            _TTS_CACHE[cache_key] = TtsAudioClip(
                                text=text,
                                spoken_text=spoken,
                                audio_bytes=full_mp3,
                                audio_format="mp3",
                                sample_rate=24000,
                                duration_sec=round(word_count * 0.32, 2),
                                voice_id=profile.edge_voice,
                                backend=str(TtsBackend.EDGE_NEURAL),
                            )
                        pcm2 = audio_bytes_to_discord_pcm_48k_stereo(bytes(rest_buf)) if rest_buf else b""
                        rest_pcm_ready.set_result(pcm2)
                except Exception as exc:
                    if not first_ready.done():
                        first_ready.set_exception(exc)
                    if not rest_pcm_ready.done():
                        rest_pcm_ready.set_result(b"")
                finally:
                    await connector.close()

            loop = _get_edge_tts_loop()
            asyncio.run_coroutine_threadsafe(_stream_mp3(), loop)
            first_mp3, is_complete = first_ready.result(timeout=6.0)
            if first_mp3 and len(first_mp3) > 100:
                word_count = max(1, len(spoken.split()))
                est_dur = round(word_count * 0.32, 2)
                clip = TtsAudioClip(
                    text=text,
                    spoken_text=spoken,
                    audio_bytes=first_mp3,
                    audio_format="mp3",
                    sample_rate=24000,
                    duration_sec=est_dur,
                    voice_id=profile.edge_voice,
                    backend=str(TtsBackend.EDGE_NEURAL),
                )
                if is_complete:
                    with _TTS_CACHE_LOCK:
                        _TTS_CACHE[cache_key] = clip
                    return clip, None

                def _bind_stream(base_stream) -> None:
                    def _deliver(fut: concurrent.futures.Future) -> None:
                        try:
                            pcm2 = fut.result()
                        except Exception:
                            pcm2 = b""
                        if base_stream is not None and hasattr(base_stream, "append_pcm"):
                            base_stream.append_pcm(pcm2, done=True)

                    rest_pcm_ready.add_done_callback(_deliver)

                return clip, _bind_stream
        except Exception as e:
            logger.debug("Streaming edge_tts synthesis failed: %s", e)

        return self.synthesize_speech(text, profile_or_mode=profile_or_mode, backend=backend, max_chars=max_chars), None


