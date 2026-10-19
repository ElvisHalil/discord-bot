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
        silence_gap_sec: float = 0.35,
        min_speech_sec: float = 0.20,
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
    Patch `discord.ext.voice_recv` so it:
    1. Decrypts Discord DAVE E2EE audio frames (`davey.DaveSession.decrypt`) introduced in `discord.py` 2.7+
       before passing the Opus payload to `discord.opus.Decoder.decode`.
    2. Fixes `RTPPacket._parse_bede_header` so RFC 5285 4-byte word padding on short Opus silence frames
       never raises `struct.error: unpack requires a buffer of 1 bytes`.
    3. Fixes `HeapJitterBuffer` so RTP sequence jumps (after mic mute/unmute or WebRTC stream resets)
       resync `_last_tx_seq` instead of dropping all future packets for 15+ minutes.
    4. Fixes `MultiDataEvent` duplicate decoder registration and wait race condition, and sets
       `UDPKeepAlive.delay = 5.0` (seconds instead of 5000s).
    5. Guards `PacketRouter._do_run` so a transient bad packet never kills `voice_client.listen()`.
    """
    global _DAVE_PATCH_INSTALLED
    if _DAVE_PATCH_INSTALLED:
        return
    try:
        import struct
        from discord.ext.voice_recv import (
            buffer as vr_buffer,
            opus as vr_opus,
            reader as vr_reader,
            router as vr_router,
            rtp as vr_rtp,
            utils as vr_utils,
        )

        try:
            import davey
        except Exception:
            davey = None  # type: ignore[assignment]

        # 1. Fix RFC 5285 0xBEDE extension header parsing (`length` is 32-bit words, not element count)
        def _patched_parse_bede_header(self: Any, data: bytes, length: int) -> None:
            offset = 4
            ext_end = min(len(data), 4 + int(length) * 4)
            while offset < ext_end:
                b = data[offset]
                if b == 0:
                    offset += 1
                    continue
                element_id = b >> 4
                if element_id == 15:
                    break
                element_len = 1 + (b & 0b0000_1111)
                val_end = min(ext_end, offset + 1 + element_len)
                self.extension_data[element_id] = bytes(data[offset + 1 : val_end])
                offset += 1 + element_len

        def _patched_update_ext_headers(self: Any, data: bytes) -> int:
            if not self.extended:
                return 0
            if self._rtpsize:
                data = self.header[-4:] + data
            if len(data) < 4:
                return 0
            profile, length = struct.unpack_from(">2sH", data)
            ext_total = 4 + int(length) * 4
            if len(data) < ext_total:
                return 0
            if profile == self._ext_magic:
                self._parse_bede_header(data, length)
            values = struct.unpack(">%sI" % length, data[4:ext_total])
            self.extension = self._ext_header(profile, length, values)
            offset = ext_total
            if self._rtpsize:
                offset -= 4
            return max(0, offset)

        vr_rtp.RTPPacket._parse_bede_header = _patched_parse_bede_header  # type: ignore[method-assign]
        vr_rtp.RTPPacket.update_ext_headers = _patched_update_ext_headers  # type: ignore[method-assign]

        # 2. Fix MultiDataEvent duplicate registration and wait race condition
        def _mde_register(self: Any, item: Any) -> None:
            if item not in self._items:
                self._items.append(item)
            self._ready.set()

        def _mde_unregister(self: Any, item: Any) -> None:
            while item in self._items:
                try:
                    self._items.remove(item)
                except ValueError:
                    break
            if not self._items:
                self._ready.clear()

        def _mde_wait(self: Any, timeout: Optional[float] = None) -> bool:
            self._check_ready()
            return self._ready.wait(0.2 if timeout is None else timeout)

        vr_utils.MultiDataEvent.register = _mde_register  # type: ignore[method-assign]
        vr_utils.MultiDataEvent.unregister = _mde_unregister  # type: ignore[method-assign]
        vr_utils.MultiDataEvent.wait = _mde_wait  # type: ignore[method-assign]

        # 3. Fix HeapJitterBuffer dropping all packets after sequence jumps or single packet loss
        def _hjb_update_has_item(self: Any) -> None:
            prefilled = self._prefill == 0
            packet_ready = len(self._buffer) > self.prefsize
            if prefilled and packet_ready:
                self._has_item.set()
            else:
                self._has_item.clear()

        def _hjb_push(self: Any, packet: Any) -> bool:
            seq = packet.sequence
            if self._last_tx_seq != -1:
                gap = vr_utils.gap_wrapped(self._last_tx_seq, seq)
                if gap > 60000:
                    # Genuinely late/out-of-order old packet behind _last_tx_seq
                    return False
                if gap > 50:
                    # Sequence number jumped forward (e.g. after mic mute/unmute or pause) -> resync
                    self._buffer.clear()
                    self._last_tx_seq = vr_utils.add_wrapped(seq, -1)
            self._push(packet)
            if self._prefill > 0:
                self._prefill -= 1
            self._cleanup()
            self._update_has_item()
            return True

        vr_buffer.HeapJitterBuffer._update_has_item = _hjb_update_has_item  # type: ignore[method-assign]
        vr_buffer.HeapJitterBuffer.push = _hjb_push  # type: ignore[method-assign]

        # 4. Fix UDPKeepAlive delay (5.0 seconds instead of 5000 seconds)
        vr_reader.UDPKeepAlive.delay = 5  # type: ignore[assignment]

        # 5. Guard PacketRouter._do_run so a single bad packet never kills voice_client.listen()
        def _patched_router_do_run(self: Any) -> None:
            while not self._end_thread.is_set():
                self.waiter.wait(0.2)
                with self._lock:
                    for decoder in list(self.waiter.items):
                        try:
                            data = decoder.pop_data()
                            if data is not None:
                                self.sink.write(data.source, data)
                        except Exception as exc:
                            logger.debug("PacketRouter decoder error ignored: %s", exc)

        vr_router.PacketRouter._do_run = _patched_router_do_run  # type: ignore[method-assign]

        # 6. Patch PacketDecoder._decode_packet for DAVE E2EE decryption and OpusError resilience
        def _patched_decode_packet(self: Any, packet: Any) -> Tuple[Any, bytes]:
            assert self._decoder is not None
            if packet:
                raw_data = getattr(packet, "decrypted_data", None)
                if not raw_data:
                    return packet, b"\x00" * 3840
                if raw_data != b"\xf8\xff\xfe" and davey is not None:
                    vc = getattr(self.sink, "voice_client", None)
                    conn = getattr(vc, "_connection", None)
                    dave_session = getattr(conn, "dave_session", None)
                    if dave_session is not None and getattr(conn, "dave_protocol_version", 0) > 0:
                        decrypted_ok = False
                        uid = (
                            vc._get_id_from_ssrc(self.ssrc) if hasattr(vc, "_get_id_from_ssrc") else None
                        ) or self._cached_id
                        if uid:
                            self._cached_id = int(uid)
                            try:
                                packet.decrypted_data = dave_session.decrypt(
                                    int(uid), davey.MediaType.audio, bytes(raw_data)
                                )
                                decrypted_ok = True
                            except Exception:
                                if hasattr(dave_session, "can_passthrough") and dave_session.can_passthrough(int(uid)):
                                    decrypted_ok = True
                        if not decrypted_ok:
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
                                    decrypted_ok = True
                                    break
                                except Exception:
                                    continue
                        if not decrypted_ok:
                            # Never feed undecrypted DAVE ciphertext into libopus (it decodes as loud static)
                            return packet, b"\x00" * 3840
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

            if hasattr(voice_client, "is_listening") and voice_client.is_listening():
                if hasattr(voice_client, "stop_listening"):
                    voice_client.stop_listening()
            sink = voice_recv.BasicSink(_sink_callback)
            voice_client.listen(sink)
            return True
    except Exception as e:
        logger.debug("Could not attach discord-ext-voice-recv sink: %s", e)
    return False

