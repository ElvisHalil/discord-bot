"""
Discord voice channel audio playback and per-speaker VAD utterance accumulator (`src/skills/voice/playback/discord_audio.py`).
- Converts synthesized TTS (`TtsAudioClip`) or 30s music previews into `discord.PCMAudio` (48 kHz 16-bit stereo PCM)
  completely in memory via `miniaudio` / `numpy`—no external `ffmpeg` subprocess required for TTS playback.
- Accumulates per-speaker 48 kHz stereo PCM frames from Discord voice channels, detects speech start and
  end-of-turn silence (~0.85s), and emits normalized 16 kHz mono WAV buffers ready for STT.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import discord

from src.skills.voice.engine.vad import (
    audio_bytes_to_discord_pcm_48k_stereo,
    compute_pcm_rms_dbfs,
    pcm_48k_stereo_to_16k_mono_wav,
)

logger = logging.getLogger(__name__)


def ensure_opus_loaded() -> bool:
    """
    Ensure `discord.opus` has loaded the native Opus codec library across Windows (bundled DLL),
    Linux Docker (`libopus.so.0`), and macOS (`libopus.0.dylib`). Never raises if unavailable.
    """
    try:
        if discord.opus.is_loaded():
            return True
        if hasattr(discord.opus, "_load_default") and discord.opus._load_default():
            return True
        for lib_name in ("libopus.so.0", "libopus.so", "libopus.0.dylib", "opus"):
            try:
                discord.opus.load_opus(lib_name)
                if discord.opus.is_loaded():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def build_discord_pcm_source(audio_bytes: bytes, volume: float = 0.9) -> Optional[discord.AudioSource]:
    """
    Decode MP3/WAV audio bytes into 48 kHz 16-bit stereo PCM in memory and wrap in a
    `discord.PCMVolumeTransformer(discord.PCMAudio(...))` ready for `voice_client.play(...)`.
    """
    ensure_opus_loaded()
    pcm_48k = audio_bytes_to_discord_pcm_48k_stereo(audio_bytes)
    if not pcm_48k:
        return None
    base_source = discord.PCMAudio(io.BytesIO(pcm_48k))
    return discord.PCMVolumeTransformer(base_source, volume=max(0.05, min(2.0, volume)))


class PerSpeakerVoiceAccumulator:
    """
    Thread-safe per-speaker voice buffer that segments continuous 48 kHz stereo PCM frames into
    discrete utterances using RMS energy Voice Activity Detection (VAD) and silence-gap timing.
    """

    def __init__(
        self,
        speech_threshold_dbfs: float = -42.0,
        silence_gap_sec: float = 0.45,
        min_speech_sec: float = 0.25,
        max_utterance_sec: float = 12.0,
    ) -> None:
        self.speech_threshold_dbfs = speech_threshold_dbfs
        self.silence_gap_sec = silence_gap_sec
        self.min_speech_sec = min_speech_sec
        self.max_utterance_sec = max_utterance_sec
        self._lock = threading.Lock()
        # { user_id: { "display_name": str, "chunks": bytearray, "speech_bytes": int, "first_ts": float, "last_speech_ts": float } }
        self._buffers: Dict[int, Dict[str, Any]] = {}

    def push_pcm_frame(
        self,
        user_id: int,
        display_name: str,
        pcm_48k_stereo: bytes,
        now: Optional[float] = None,
    ) -> Optional[Tuple[int, str, bytes, float, float]]:
        """
        Append a 48 kHz stereo PCM frame for `user_id`.
        If the utterance reaches `max_utterance_sec` or a silence gap >= `silence_gap_sec` follows active speech,
        returns `(user_id, display_name, wav_16k_bytes, duration_sec, rms_dbfs)`. Otherwise returns `None`.
        """
        if not pcm_48k_stereo:
            return None
        ts = now if now is not None else time.monotonic()
        frame_dbfs = compute_pcm_rms_dbfs(pcm_48k_stereo)
        is_speech = frame_dbfs >= self.speech_threshold_dbfs

        with self._lock:
            state = self._buffers.get(user_id)
            if state is None:
                if not is_speech:
                    return None
                state = {
                    "display_name": display_name or f"User-{user_id}",
                    "chunks": bytearray(),
                    "speech_bytes": 0,
                    "first_ts": ts,
                    "last_speech_ts": ts,
                }
                self._buffers[user_id] = state

            if display_name:
                state["display_name"] = display_name
            state["chunks"].extend(pcm_48k_stereo)
            if is_speech:
                state["speech_bytes"] += len(pcm_48k_stereo)
                state["last_speech_ts"] = ts

            # 48,000 Hz * 2 channels * 2 bytes/sample = 192,000 bytes/sec
            total_sec = len(state["chunks"]) / 192000.0
            speech_sec = state["speech_bytes"] / 192000.0
            silence_elapsed = ts - state["last_speech_ts"]

            if total_sec >= self.max_utterance_sec or (
                not is_speech and silence_elapsed >= self.silence_gap_sec
            ):
                popped = self._buffers.pop(user_id, None)
                if not popped or speech_sec < self.min_speech_sec:
                    return None
                wav_bytes, dur_sec, rms_dbfs = pcm_48k_stereo_to_16k_mono_wav(bytes(popped["chunks"]))
                if not wav_bytes:
                    return None
                return (user_id, popped["display_name"], wav_bytes, dur_sec, rms_dbfs)

        return None

    def flush_ready_utterances(
        self,
        now: Optional[float] = None,
        force_all: bool = False,
    ) -> List[Tuple[int, str, bytes, float, float]]:
        """
        Flush any speaker buffers whose trailing silence has exceeded `silence_gap_sec` (or all active buffers when `force_all=True`).
        """
        ts = now if now is not None else time.monotonic()
        ready: List[Tuple[int, str, bytes, float, float]] = []
        with self._lock:
            for uid in list(self._buffers.keys()):
                st = self._buffers[uid]
                silence_elapsed = ts - st["last_speech_ts"]
                speech_sec = st["speech_bytes"] / 192000.0
                if force_all or silence_elapsed >= self.silence_gap_sec:
                    popped = self._buffers.pop(uid)
                    if speech_sec >= self.min_speech_sec:
                        wav_bytes, dur_sec, rms_dbfs = pcm_48k_stereo_to_16k_mono_wav(bytes(popped["chunks"]))
                        if wav_bytes:
                            ready.append((uid, popped["display_name"], wav_bytes, dur_sec, rms_dbfs))
        return ready

    def clear(self) -> None:
        """Discard all buffered PCM frames."""
        with self._lock:
            self._buffers.clear()


_DAVE_PATCH_INSTALLED = False


def _install_voice_recv_dave_patch() -> None:
    """
    Patch `discord.ext.voice_recv.opus.PacketDecoder._decode_packet` so it:
    1. Decrypts Discord DAVE E2EE audio frames (`davey.DaveSession.decrypt`) introduced in `discord.py` 2.7+
       before passing the Opus payload to `discord.opus.Decoder.decode`.
    2. Automatically maps unknown SSRCs to their DAVE MLS `user_id` if a speaker was already talking
       when the bot joined the voice channel.
    3. Catches transient `OpusError` frames and returns 20ms of silence (`b'\\x00' * 3840`) instead of
       crashing `PacketRouter.run()` and killing `voice_client.listen()`.
    """
    global _DAVE_PATCH_INSTALLED
    if _DAVE_PATCH_INSTALLED:
        return
    try:
        from discord.ext.voice_recv import opus as vr_opus

        try:
            import davey
        except Exception:
            davey = None  # type: ignore[assignment]

        def _patched_decode_packet(self: Any, packet: Any) -> Tuple[Any, bytes]:
            assert self._decoder is not None
            if packet:
                raw_data = getattr(packet, "decrypted_data", None)
                if raw_data and raw_data != b"\xf8\xff\xfe" and davey is not None:
                    vc = getattr(self.sink, "voice_client", None)
                    conn = getattr(vc, "_connection", None)
                    dave_session = getattr(conn, "dave_session", None)
                    if dave_session is not None and getattr(conn, "dave_protocol_version", 0) > 0:
                        uid = self._cached_id or (
                            vc._get_id_from_ssrc(self.ssrc) if hasattr(vc, "_get_id_from_ssrc") else None
                        )
                        if uid:
                            try:
                                packet.decrypted_data = dave_session.decrypt(
                                    int(uid), davey.MediaType.audio, bytes(raw_data)
                                )
                            except Exception:
                                pass
                        else:
                            bot_uid = int(getattr(getattr(conn, "user", None), "id", 0) or 0)
                            cand_uids = list(
                                dave_session.get_user_ids() if hasattr(dave_session, "get_user_ids") else []
                            )
                            for cand_uid in cand_uids:
                                if int(cand_uid) == bot_uid:
                                    continue
                                try:
                                    decrypted = dave_session.decrypt(
                                        int(cand_uid), davey.MediaType.audio, bytes(raw_data)
                                    )
                                    packet.decrypted_data = decrypted
                                    self._cached_id = int(cand_uid)
                                    if hasattr(vc, "_add_ssrc"):
                                        vc._add_ssrc(int(cand_uid), self.ssrc)
                                    break
                                except Exception:
                                    continue
                try:
                    pcm = self._decoder.decode(packet.decrypted_data, fec=False)
                except Exception:
                    pcm = b"\x00" * 3840
                return packet, pcm

            next_packet = self._buffer.peek_next()
            if next_packet is not None:
                nextdata = getattr(next_packet, "decrypted_data", None)
                try:
                    pcm = self._decoder.decode(nextdata, fec=True)
                except Exception:
                    pcm = b"\x00" * 3840
            else:
                try:
                    pcm = self._decoder.decode(None, fec=False)
                except Exception:
                    pcm = b"\x00" * 3840
            return packet, pcm

        vr_opus.PacketDecoder._decode_packet = _patched_decode_packet  # type: ignore[method-assign]
        _DAVE_PATCH_INSTALLED = True
    except Exception as e:
        logger.debug("Could not install DAVE voice_recv patch: %s", e)


def attach_discord_voice_receiver(
    voice_client: Any,
    on_pcm_chunk: Callable[[int, str, bytes], None],
) -> bool:
    """
    Attach a per-speaker live audio receive sink to `voice_client` if `discord-ext-voice-recv`
    (or a compatible `listen` sink API) is available on the connected `VoiceClient`.
    Returns True if live packet capture was attached, False if in transmit-only mode.
    """
    if voice_client is None or not hasattr(voice_client, "listen"):
        return False
    try:
        import importlib

        ensure_opus_loaded()
        if importlib.util.find_spec("discord.ext.voice_recv") is not None:
            _install_voice_recv_dave_patch()
            voice_recv = importlib.import_module("discord.ext.voice_recv")

            def _sink_callback(user: Any, data: Any) -> None:
                if user is not None and getattr(user, "bot", False):
                    return
                pcm = getattr(data, "pcm", None)
                if not pcm:
                    return
                if user is not None:
                    uid = int(getattr(user, "id", 0) or 0)
                    uname = str(getattr(user, "display_name", None) or getattr(user, "name", f"User-{uid}"))
                else:
                    ssrc = int(getattr(getattr(data, "packet", None), "ssrc", 0) or 0)
                    uid = 0
                    if ssrc and hasattr(voice_client, "_get_id_from_ssrc"):
                        uid = int(voice_client._get_id_from_ssrc(ssrc) or 0)
                    uid = uid or ssrc or 1
                    uname = f"Speaker-{uid}"
                on_pcm_chunk(uid, uname, bytes(pcm))

            sink = voice_recv.BasicSink(_sink_callback)
            voice_client.listen(sink)
            return True
    except Exception as e:
        logger.debug("Could not attach discord-ext-voice-recv sink: %s", e)
    return False

