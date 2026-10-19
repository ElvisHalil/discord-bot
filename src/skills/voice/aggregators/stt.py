"""
Speech-to-Text (STT) aggregator (`src/skills/voice/aggregators/stt.py`):
1. Primary Zero-VRAM Backend (`gemma_native`):
   Uses `gemma4:e4b`'s built-in `gemma4a` mel-spectrogram audio encoder in Ollama (`think=False`)
   to transcribe 16 kHz mono WAV utterances or `.ogg`/`.mp3` Discord voice messages with zero
   extra GPU memory or external STT model weights.
2. Optional Local Whisper Backend (`faster_whisper`):
   Automatically uses `faster_whisper` if installed in the environment and requested.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import re
import threading
from typing import Optional, Tuple

from src.skills.voice.aggregators.base import BaseVoiceAggregator
from src.skills.voice.enums import SttBackend
from src.utils.cache import BoundedCache
from src.utils.http import ThreadLocalRequestsSession

logger = logging.getLogger(__name__)

_STT_CACHE_LOCK = threading.Lock()
_STT_CACHE: BoundedCache = BoundedCache(maxsize=128)
_HTTP_SESSION = ThreadLocalRequestsSession(headers={"Content-Type": "application/json"})
_WHISPER_LOCK = threading.Lock()
_WHISPER_MODEL = None


def clear_voice_stt_cache() -> None:
    """Clear the in-memory speech transcription cache."""
    with _STT_CACHE_LOCK:
        _STT_CACHE.clear()


class VoiceSttAggregator(BaseVoiceAggregator):
    """Multi-backend local Speech-to-Text aggregator (`gemma4:e4b` native `gemma4a` + optional `faster-whisper`)."""

    def transcribe_audio(
        self,
        audio_bytes: bytes,
        backend: str = SttBackend.AUTO,
        model: str = "gemma4:e4b",
        ollama_url: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Transcribe raw WAV/MP3/OGG audio bytes into text.
        Returns `(transcript_text, backend_used)`.
        """
        if not audio_bytes or len(audio_bytes) < 64:
            return "", SttBackend.GEMMA_NATIVE

        digest = hashlib.sha256(audio_bytes).hexdigest()[:24]
        with _STT_CACHE_LOCK:
            cached = _STT_CACHE.get(digest)
            if cached is not None:
                return cached

        preferred = (backend or os.getenv("VOICE_STT_BACKEND", SttBackend.AUTO)).strip().lower()

        if preferred in (SttBackend.FASTER_WHISPER, SttBackend.AUTO):
            whisper_text = self._transcribe_with_faster_whisper(audio_bytes)
            if whisper_text:
                res = (whisper_text, str(SttBackend.FASTER_WHISPER))
                with _STT_CACHE_LOCK:
                    _STT_CACHE[digest] = res
                return res
            if preferred == SttBackend.FASTER_WHISPER:
                logger.debug("faster-whisper unavailable or returned empty; falling back to gemma4a native STT")

        gemma_text = self._transcribe_with_gemma_audio(
            audio_bytes=audio_bytes,
            model=model,
            ollama_url=ollama_url,
        )
        res = (gemma_text, str(SttBackend.GEMMA_NATIVE))
        if gemma_text:
            with _STT_CACHE_LOCK:
                _STT_CACHE[digest] = res
        return res

    def _transcribe_with_gemma_audio(
        self,
        audio_bytes: bytes,
        model: str = "gemma4:e4b",
        ollama_url: Optional[str] = None,
    ) -> str:
        """
        Transcribe speech audio directly via `gemma4:e4b`'s native mel-spectrogram audio encoder (`gemma4a`)
        in Ollama (`think=False`).
        """
        if not ollama_url:
            from src.utils.discord import get_ollama_base_url

            ollama_url = f"{get_ollama_base_url()}/api/chat"

        b64_audio = base64.b64encode(audio_bytes).decode("ascii")
        prompt = (
            "Transcribe the exact spoken words in this audio clip verbatim. "
            "Output ONLY the spoken transcript with no commentary, no labels, and no quotation marks. "
            "If there is only silence or background noise with no intelligible speech, output: [SILENCE]"
        )

        try:
            resp = _HTTP_SESSION.post(
                ollama_url,
                json={
                    "model": model,
                    "stream": False,
                    "think": False,
                    "options": {
                        "num_predict": 120,
                        "temperature": 0.0,
                    },
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                            "images": [b64_audio],
                        }
                    ],
                },
                timeout=12,
            )
            if resp.status_code != 200:
                return ""
            raw = ((resp.json() or {}).get("message") or {}).get("content", "").strip()
            return self._clean_transcript(raw)
        except Exception as e:
            logger.debug("Gemma native STT failed: %s", e)
            return ""

    def _transcribe_with_faster_whisper(self, audio_bytes: bytes) -> str:
        """Optional local faster-whisper transcription if `faster_whisper` is installed."""
        global _WHISPER_MODEL
        try:
            import importlib

            fw_spec = importlib.util.find_spec("faster_whisper")
            if fw_spec is None:
                return ""
            fw_mod = importlib.import_module("faster_whisper")
        except Exception:
            return ""

        try:
            with _WHISPER_LOCK:
                if _WHISPER_MODEL is None:
                    model_size = os.getenv("WHISPER_MODEL_SIZE", "base.en")
                    device = os.getenv("WHISPER_DEVICE", "auto")
                    _WHISPER_MODEL = fw_mod.WhisperModel(model_size, device=device, compute_type="int8")

            segments, _ = _WHISPER_MODEL.transcribe(io.BytesIO(audio_bytes), beam_size=1, vad_filter=True)
            joined = " ".join(seg.text.strip() for seg in segments if seg.text and seg.text.strip())
            return self._clean_transcript(joined)
        except Exception as e:
            logger.debug("faster-whisper transcription failed: %s", e)
            return ""

    @staticmethod
    def _clean_transcript(raw: str) -> str:
        """Strip wrapper prefixes, quotes, or hallmarked silence tokens from an STT output."""
        if not raw:
            return ""
        s = raw.strip()
        # Strip leading "Transcript:" / "Transcription:" prefixes if any
        s = re.sub(r"^(?:transcript(?:ion)?|spoken(?:\s+words|\s+text)?)\s*:\s*", "", s, flags=re.IGNORECASE).strip()
        s = s.strip("\"'` ")
        if not s:
            return ""
        if re.fullmatch(r"\[?\s*(?:silence|no\s+speech|inaudible|background\s+noise|music|blank_audio)\s*\]?\.?", s, flags=re.IGNORECASE):
            return ""
        return s

