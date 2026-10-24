"""
Real-Time Discord Voice-Channel AI Co-Host Service (`src/skills/voice/tracker.py`).
Manages live Discord `VoiceClient` connections, per-speaker VAD audio buffers,
`gemma4:e4b` native `gemma4a` / `faster-whisper` STT transcription, neural TTS playback,
rolling voice conversation transcripts, and live voice announcements for tracked games/flights.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
import re
import threading
import time
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

from src.context import get_active_channel_id, get_bound_event_loop
from src.skills.voice.aggregators.stt import VoiceSttAggregator, clear_voice_stt_cache
from src.skills.voice.aggregators.tts import VoiceTtsAggregator, clear_voice_tts_cache
from src.skills.voice.catalogs import (
    DEFAULT_WAKE_WORDS,
    VOICE_ACTION_ALIASES,
    VOICE_MODE_ALIASES,
    VOICE_PROFILE_ALIASES,
    VOICE_PROFILES,
)
from src.skills.voice.context import (
    _EXPLICIT_VOICE_JOIN_RE,
    clear_channel_voice_state,
    get_channel_voice_session,
    set_channel_voice_session,
)
from src.skills.voice.engine.vad import extract_wake_word_command, sanitize_text_for_speech
from src.skills.voice.enums import SttBackend, TtsBackend, VoiceAction, VoiceMode, VoiceSessionState
from src.skills.voice.models import SpeakerUtterance, TtsAudioClip, VoiceSessionRecord
from src.skills.voice.playback.discord_audio import (
    PerSpeakerVoiceAccumulator,
    attach_discord_voice_receiver,
    build_discord_pcm_source,
    build_ffmpeg_music_source,
    clear_ytdlp_stream_cache,
    extract_youtube_audio_stream,
)
from src.utils.workers import run_in_worker, submit_to_worker

logger = logging.getLogger(__name__)

MAX_SESSION_UTTERANCES = 120

_VOICE_MUSIC_INTENT_RE = re.compile(
    r"\b(?:in|into|on)\s+(?:the\s+|our\s+|my\s+)?(?:voice(?:\s+channel|\s+chat|\s+call)?|vc)\b|"
    r"\bstream\s+(?:some\s+|a\s+|the\s+)?(?:music|song|songs|track|tracks|dnb|drum\s+and\s+bass|house|techno|beats|tunes)\b",
    re.IGNORECASE,
)


class VoiceCohostService:
    """
    Background voice-channel AI co-host manager:
    - Connects/disconnects Discord `VoiceClient` instances per text channel / guild.
    - Ingests per-speaker 48 kHz stereo PCM audio via `PerSpeakerVoiceAccumulator`.
    - Flushes completed utterances on silence gaps (`~0.85s`), transcribes via `VoiceSttAggregator`,
      detects wake words (or open-mic conversational turns), invokes the agent callback, and streams
      neural TTS replies back into the Discord voice channel via `discord.PCMAudio`.
    """

    def __init__(
        self,
        stt_agg: Optional[VoiceSttAggregator] = None,
        tts_agg: Optional[VoiceTtsAggregator] = None,
    ) -> None:
        self._stt = stt_agg or VoiceSttAggregator()
        self._tts = tts_agg or VoiceTtsAggregator()
        self._lock = threading.Lock()
        # { channel_id: VoiceSessionRecord }
        self._sessions: Dict[int, VoiceSessionRecord] = {}
        # { channel_id: PerSpeakerVoiceAccumulator }
        self._accumulators: Dict[int, PerSpeakerVoiceAccumulator] = {}
        # { channel_id: discord.VoiceClient | Any }
        self._voice_clients: Dict[int, Any] = {}
        # { channel_id: discord.VoiceChannel }
        self._author_voice_channels: Dict[int, Any] = {}
        # { channel_id: str }
        self._connect_errors: Dict[int, str] = {}
        # { channel_id: float } monotonic timestamp until which a bare "Hey bot" arms follow-up speech
        self._wake_armed_until: Dict[int, float] = {}
        # { channel_id: asyncio.Lock } serializes LLM/TTS turns per voice channel
        self._turn_locks: Dict[int, asyncio.Lock] = {}
        # { channel_id: Dict[str, Any] } currently streaming ad-free music track metadata
        self._current_music_track: Dict[int, Dict[str, Any]] = {}
        # { channel_id: Deque[Dict[str, Any]] } per-channel voice music queue
        self._music_queues: Dict[int, Deque[Dict[str, Any]]] = {}
        # { channel_id: bool } whether ad-free music is actively playing (or paused) on the VoiceClient
        self._is_playing_music: Dict[int, bool] = {}
        # { channel_id: bool } whether the active music track is currently paused
        self._music_paused: Dict[int, bool] = {}
        # { channel_id: int } monotonic generation counter so replaced/stopped tracks never trigger stale `after` callbacks
        self._music_play_generation: Dict[int, int] = {}
        self._utterance_callback: Optional[Callable[[VoiceSessionRecord, SpeakerUtterance, str], Awaitable[Optional[str]]]] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._stop_event = threading.Event()

    def _on_ssrc_mapped(self, channel_id: int, ssrc: int, user_id: int, display_name: str) -> None:
        """Merge any initial audio frames buffered under `ssrc` into `user_id` once Discord maps SSRC -> User."""
        with self._lock:
            acc = self._accumulators.get(channel_id)
        if acc is not None:
            acc.merge_speaker_key(ssrc, user_id, display_name)

    def register_author_voice_channel(self, channel_id: int, author_voice_channel: Any) -> None:
        """Record the message author's active Discord `VoiceChannel` so natural-language join calls can connect."""
        with self._lock:
            if author_voice_channel is not None:
                self._author_voice_channels[channel_id] = author_voice_channel
            else:
                self._author_voice_channels.pop(channel_id, None)

    async def ensure_discord_voice_connected(self, channel_id: int) -> Any:
        """
        Ensure that if a `VoiceSessionRecord` is active for `channel_id` and a target Discord `VoiceChannel`
        is registered, the underlying `discord.VoiceClient` is connected and receiving audio.
        """
        with self._lock:
            sess = self._sessions.get(channel_id)
            vc = self._voice_clients.get(channel_id)
            author_vc = self._author_voice_channels.get(channel_id)

        if sess is None:
            return vc
        if vc is not None and getattr(vc, "is_connected", lambda: True)():
            return vc
        if author_vc is None or not hasattr(author_vc, "connect"):
            return vc

        vc_client = None
        try:
            import importlib
            from src.skills.voice.playback.discord_audio import ensure_opus_loaded

            ensure_opus_loaded()
            guild = getattr(author_vc, "guild", None)
            existing_vc = getattr(guild, "voice_client", None) if guild is not None else None
            if existing_vc is not None and getattr(existing_vc, "is_connected", lambda: True)():
                if (
                    getattr(getattr(existing_vc, "channel", None), "id", None) != getattr(author_vc, "id", None)
                    and hasattr(existing_vc, "move_to")
                ):
                    await existing_vc.move_to(author_vc)
                vc_client = existing_vc
            else:
                connect_kwargs: Dict[str, Any] = {}
                if importlib.util.find_spec("discord.ext.voice_recv") is not None:
                    voice_recv = importlib.import_module("discord.ext.voice_recv")
                    connect_kwargs["cls"] = voice_recv.VoiceRecvClient
                try:
                    vc_client = await author_vc.connect(**connect_kwargs)
                except Exception:
                    if connect_kwargs:
                        vc_client = await author_vc.connect()
                    else:
                        raise
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            print(f"⚠️ [Voice Co-Host] Discord voice channel connect failed: {err_msg}")
            logger.warning("Discord voice channel connect failed: %s", err_msg)
            with self._lock:
                self._connect_errors[channel_id] = err_msg
            self.stop_session(channel_id)
            return None

        if vc_client is not None:
            with self._lock:
                self._connect_errors.pop(channel_id, None)
                self._voice_clients[channel_id] = vc_client
                if sess is not None:
                    sess.voice_channel_id = int(getattr(author_vc, "id", sess.voice_channel_id) or sess.voice_channel_id)
                    sess.voice_channel_name = str(getattr(author_vc, "name", sess.voice_channel_name) or sess.voice_channel_name)
            print(f"🎙️ [Voice Co-Host] Connected to Discord voice channel '{getattr(author_vc, 'name', 'Voice')}' (ID: {getattr(author_vc, 'id', '?')})")
            attach_discord_voice_receiver(
                vc_client,
                lambda uid, uname, pcm: self.ingest_pcm_frame(channel_id, uid, uname, pcm),
                on_ssrc_mapped=lambda ssrc, uid, uname: self._on_ssrc_mapped(channel_id, ssrc, uid, uname),
            )
        return vc_client

    def set_utterance_callback(
        self,
        callback: Callable[[VoiceSessionRecord, SpeakerUtterance, str], Awaitable[Optional[str]]],
    ) -> None:
        """Register the async callback invoked when a voice utterance triggers the AI co-host."""
        self._utterance_callback = callback

    def get_session(self, channel_id: Optional[int] = None) -> Optional[VoiceSessionRecord]:
        """Return the active `VoiceSessionRecord` for `channel_id`."""
        cid = channel_id if channel_id is not None else (get_active_channel_id() or 0)
        with self._lock:
            sess = self._sessions.get(cid)
        if sess is not None:
            return sess
        return get_channel_voice_session(cid)

    def start_session(
        self,
        channel_id: int,
        voice_channel_id: int,
        voice_channel_name: str = "General Voice",
        guild_id: Optional[int] = None,
        mode: str = VoiceMode.WAKE_WORD,
        voice_profile: str = "research",
        voice_client: Any = None,
        announce_live_trackers: bool = True,
    ) -> VoiceSessionRecord:
        """Create and register a live `VoiceSessionRecord` and per-speaker VAD accumulator for `channel_id`."""
        now = time.time()
        norm_mode = VOICE_MODE_ALIASES.get((mode or "").strip().lower(), str(VoiceMode.WAKE_WORD))
        raw_prof = (voice_profile or "research").strip().lower()
        prof_key = VOICE_PROFILE_ALIASES.get(raw_prof, raw_prof)
        if prof_key not in VOICE_PROFILES and not (voice_profile or "").strip().endswith("Neural"):
            prof_key = "research"

        with self._lock:
            author_vc = self._author_voice_channels.get(channel_id)

        if author_vc is not None:
            if voice_channel_name in ("General Voice", "Discord Voice Channel"):
                voice_channel_name = str(getattr(author_vc, "name", voice_channel_name) or voice_channel_name)
            if voice_channel_id in (0, 1, channel_id):
                voice_channel_id = int(getattr(author_vc, "id", voice_channel_id) or voice_channel_id)
            if guild_id is None:
                guild_id = int(getattr(getattr(author_vc, "guild", None), "id", 0) or 0) or None

        record = VoiceSessionRecord(
            channel_id=channel_id,
            voice_channel_id=voice_channel_id,
            voice_channel_name=voice_channel_name,
            guild_id=guild_id,
            state=str(VoiceSessionState.LISTENING),
            mode=norm_mode,
            voice_profile=prof_key,
            stt_backend=str(SttBackend.AUTO),
            tts_backend=str(TtsBackend.AUTO),
            started_at=now,
            last_activity_at=now,
            announce_live_trackers=announce_live_trackers,
        )
        accumulator = PerSpeakerVoiceAccumulator()

        with self._lock:
            self._sessions[channel_id] = record
            self._accumulators[channel_id] = accumulator
            self._wake_armed_until.pop(channel_id, None)
            if voice_client is not None:
                self._voice_clients[channel_id] = voice_client

        set_channel_voice_session(record, channel_id=channel_id)

        if voice_client is not None:
            attach_discord_voice_receiver(
                voice_client,
                lambda uid, uname, pcm: self.ingest_pcm_frame(channel_id, uid, uname, pcm),
                on_ssrc_mapped=lambda ssrc, uid, uname: self._on_ssrc_mapped(channel_id, ssrc, uid, uname),
            )

        if hasattr(self._stt, "warmup_whisper_model"):
            submit_to_worker(self._stt.warmup_whisper_model)
        self.ensure_loop_running()
        return record

    def stop_session(self, channel_id: Optional[int] = None) -> Optional[VoiceSessionRecord]:
        """Stop and remove the voice co-host session for `channel_id` (or all sessions if `None`)."""
        if channel_id is None:
            with self._lock:
                cids = list(self._sessions.keys())
                self._author_voice_channels.clear()
                self._wake_armed_until.clear()
            for cid in cids:
                self.stop_session(cid)
            clear_channel_voice_state(None)
            return None

        with self._lock:
            record = self._sessions.pop(channel_id, None)
            acc = self._accumulators.pop(channel_id, None)
            vc = self._voice_clients.pop(channel_id, None)
            self._author_voice_channels.pop(channel_id, None)
            self._wake_armed_until.pop(channel_id, None)
            self._music_play_generation[channel_id] = self._music_play_generation.get(channel_id, 0) + 1
            self._current_music_track.pop(channel_id, None)
            self._is_playing_music.pop(channel_id, None)
            self._music_paused.pop(channel_id, None)
            if channel_id in self._music_queues:
                self._music_queues[channel_id].clear()
                self._music_queues.pop(channel_id, None)

        if acc is not None:
            acc.clear()
        if record is not None:
            record.state = str(VoiceSessionState.DISCONNECTED)
        clear_channel_voice_state(channel_id)

        if vc is not None:
            try:
                if hasattr(vc, "stop"):
                    vc.stop()
            except Exception:
                pass
            loop = get_bound_event_loop()
            if loop and loop.is_running() and hasattr(vc, "disconnect"):
                try:
                    asyncio.run_coroutine_threadsafe(vc.disconnect(force=True), loop)
                except Exception as e:
                    logger.debug("VoiceClient disconnect failed: %s", e)

        return record

    def set_mode(self, channel_id: int, mode: str) -> Optional[VoiceSessionRecord]:
        """Update the listening activation mode (`wake_word`, `conversational`, `listen_only`, `push_to_talk`)."""
        norm_mode = VOICE_MODE_ALIASES.get((mode or "").strip().lower(), str(VoiceMode.WAKE_WORD))
        sess = self.get_session(channel_id)
        if sess is None:
            return None
        sess.mode = norm_mode
        sess.last_activity_at = time.time()
        return sess

    def set_voice_profile(self, channel_id: int, voice_profile: str) -> Optional[VoiceSessionRecord]:
        """Update the spoken neural TTS voice profile/persona for `channel_id`."""
        raw_prof = (voice_profile or "research").strip()
        prof_key = VOICE_PROFILE_ALIASES.get(raw_prof.lower(), raw_prof.lower())
        if prof_key not in VOICE_PROFILES and not raw_prof.endswith("Neural"):
            prof_key = "research"
        else:
            prof_key = raw_prof if raw_prof.endswith("Neural") else prof_key
        sess = self.get_session(channel_id)
        if sess is None:
            return None
        sess.voice_profile = prof_key
        sess.last_activity_at = time.time()
        return sess

    def set_mute(self, channel_id: int, is_muted: bool) -> Optional[VoiceSessionRecord]:
        """Mute or unmute spoken TTS output for `channel_id`."""
        sess = self.get_session(channel_id)
        if sess is None:
            return None
        sess.is_muted = is_muted
        sess.state = str(VoiceSessionState.MUTED if is_muted else VoiceSessionState.LISTENING)
        sess.last_activity_at = time.time()
        return sess

    def ingest_pcm_frame(
        self,
        channel_id: int,
        user_id: int,
        display_name: str,
        pcm_48k_stereo: bytes,
        now: Optional[float] = None,
    ) -> Optional[SpeakerUtterance]:
        """
        Push a raw 48 kHz stereo PCM frame from a Discord voice channel member into the channel's
        `PerSpeakerVoiceAccumulator`. If an utterance completes immediately, transcribes and records it.
        """
        with self._lock:
            acc = self._accumulators.get(channel_id)
            sess = self._sessions.get(channel_id)
            need_loop = self._loop_task is None or self._loop_task.done()
        if acc is None or sess is None:
            return None
        if need_loop:
            self.ensure_loop_running()

        ready = acc.push_pcm_frame(user_id, display_name, pcm_48k_stereo, now=now)
        if ready is not None:
            uid, uname, wav_16k, dur_sec, rms_dbfs = ready
            return self.process_utterance_wav(
                channel_id=channel_id,
                user_id=uid,
                display_name=uname,
                wav_bytes=wav_16k,
                duration_sec=dur_sec,
                rms_dbfs=rms_dbfs,
            )
        return None

    def process_utterance_wav(
        self,
        channel_id: int,
        user_id: int,
        display_name: str,
        wav_bytes: bytes,
        duration_sec: float = 1.0,
        rms_dbfs: float = -24.0,
        model: str = "gemma4:e4b",
    ) -> Optional[SpeakerUtterance]:
        """
        Transcribe a completed 16 kHz mono WAV utterance, append it to the session's rolling
        transcript history, and schedule the async co-host turn if triggered.
        """
        sess = self.get_session(channel_id)
        if sess is None or not wav_bytes:
            return None

        transcript, backend_used = self._stt.transcribe_audio(
            wav_bytes,
            backend=sess.stt_backend,
            model=model,
        )
        if not transcript:
            return None

        now_mono = time.monotonic()
        require_wake = sess.mode == str(VoiceMode.WAKE_WORD)
        if sess.mode == str(VoiceMode.LISTEN_ONLY):
            is_triggered, cleaned_query = False, transcript
        else:
            is_triggered, cleaned_query = extract_wake_word_command(
                transcript,
                wake_words=DEFAULT_WAKE_WORDS,
                require_wake_word=require_wake,
            )
            if is_triggered and not cleaned_query:
                # Speaker said ONLY "Hey bot" and paused. Arm an 8-second follow-up window for
                # the rest of their sentence instead of sending "Hey bot" to the LLM!
                with self._lock:
                    self._wake_armed_until[channel_id] = now_mono + 8.0
                print(
                    f"🎙️ [Voice Wake Armed] {display_name or f'User-{user_id}'} said \"{transcript}\" — "
                    f"listening for follow-up command (8s window)"
                )
                return None
            if not is_triggered and require_wake:
                with self._lock:
                    armed_until = self._wake_armed_until.get(channel_id, 0.0)
                    if now_mono <= armed_until:
                        self._wake_armed_until[channel_id] = 0.0
                        is_triggered = True
                        cleaned_query = transcript.strip()
            elif is_triggered and cleaned_query:
                with self._lock:
                    self._wake_armed_until[channel_id] = 0.0

        utterance = SpeakerUtterance(
            user_id=user_id,
            display_name=display_name or f"User-{user_id}",
            transcript=transcript,
            duration_sec=duration_sec,
            rms_dbfs=rms_dbfs,
            timestamp=time.time(),
            stt_backend=backend_used,
            is_wake_word_triggered=is_triggered,
        )

        with self._lock:
            sess.utterances.append(utterance)
            if len(sess.utterances) > MAX_SESSION_UTTERANCES:
                sess.utterances = sess.utterances[-MAX_SESSION_UTTERANCES:]
            sess.utterances_count += 1
            sess.last_activity_at = utterance.timestamp

        print(
            f"🎙️ [Voice Heard] {utterance.display_name} ({utterance.duration_sec:.1f}s, {backend_used}): "
            f"\"{transcript}\" [mode={sess.mode}, triggered={is_triggered}]"
        )

        if is_triggered and cleaned_query and self._utterance_callback is not None:
            loop = get_bound_event_loop()
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._handle_triggered_utterance_async(sess, utterance, cleaned_query),
                    loop,
                )

        return utterance

    async def _handle_triggered_utterance_async(
        self,
        session: VoiceSessionRecord,
        utterance: SpeakerUtterance,
        cleaned_query: str,
    ) -> Optional[str]:
        """Invoke the registered agent callback for a voice utterance and speak the reply aloud."""
        if self._utterance_callback is None:
            return None
        with self._lock:
            turn_lock = self._turn_locks.get(session.channel_id)
            if turn_lock is None:
                turn_lock = asyncio.Lock()
                self._turn_locks[session.channel_id] = turn_lock

        async with turn_lock:
            try:
                session.state = str(VoiceSessionState.THINKING)
                reply_text = await self._utterance_callback(session, utterance, cleaned_query)
                if reply_text:
                    utterance.bot_reply = sanitize_text_for_speech(reply_text, max_chars=200)
                    # If the spoken command resolved a music card, stream the song instead of reading the card via TTS
                    if self.extract_song_card_track(reply_text) is None and not session.is_muted:
                        await self.speak_async(session.channel_id, reply_text, profile_or_mode=session.voice_profile)
                return reply_text
            except Exception as e:
                print(f"⚠️ [Voice Co-Host Turn Error]: {e}")
                logger.debug("Voice utterance callback error: %s", e)
                return None
            finally:
                session.state = str(VoiceSessionState.MUTED if session.is_muted else VoiceSessionState.LISTENING)

    @staticmethod
    def has_voice_music_intent(text: str) -> bool:
        """
        Return True if the user's message explicitly asks to join voice or stream/play music
        inside the Discord voice channel (e.g., 'join voice and play dnb', 'play house in vc').
        """
        if not text:
            return False
        return bool(_EXPLICIT_VOICE_JOIN_RE.search(text) or _VOICE_MUSIC_INTENT_RE.search(text))

    @staticmethod
    def extract_song_card_track(card_text: str) -> Optional[Dict[str, str]]:
        """
        Extract `song`, `artist`, `url`, and `video_id` from a formatted bot Song Card.
        Returns None if `card_text` is not a standardized music card.
        """
        if not card_text or ("music.youtube.com/watch?v=" not in card_text and "www.youtube.com/watch?v=" not in card_text):
            return None
        m_song = re.search(r"(?im)^\s*(?:\*\*)?(?:Song|Track):\*{0,2}\s*(.+)$", card_text)
        m_art = re.search(r"(?im)^\s*(?:\*\*)?Artist:\*{0,2}\s*(.+)$", card_text)
        m_url = re.search(
            r"(https:\/\/(?:music|www)\.youtube\.com\/watch\?v=([0-9A-Za-z_-]{11}))",
            card_text,
        )
        if not (m_song and m_url):
            return None
        song = m_song.group(1).strip().strip("*").strip()
        artist = m_art.group(1).strip().strip("*").strip() if m_art else "Unknown Artist"
        url = m_url.group(1).replace("www.youtube.com", "music.youtube.com")
        vid = m_url.group(2)
        return {
            "song": song,
            "artist": artist,
            "url": url,
            "video_id": vid,
        }

    def is_music_playing(self, channel_id: int) -> bool:
        """Return True if ad-free music is currently playing or paused on `channel_id`'s voice client."""
        with self._lock:
            is_active = bool(self._is_playing_music.get(channel_id))
            vc = self._voice_clients.get(channel_id)
        if not is_active:
            return False
        if vc is not None:
            playing = bool(getattr(vc, "is_playing", lambda: False)())
            paused = bool(getattr(vc, "is_paused", lambda: False)()) or bool(self._music_paused.get(channel_id))
            if not playing and not paused:
                with self._lock:
                    self._is_playing_music[channel_id] = False
                return False
        return True

    def get_current_music_track(self, channel_id: int) -> Optional[Dict[str, Any]]:
        """Return a copy of the currently streaming music track dictionary for `channel_id`, if any."""
        with self._lock:
            track = self._current_music_track.get(channel_id)
            return dict(track) if track else None

    async def _start_track_stream_async(
        self,
        channel_id: int,
        vc: Any,
        sess: VoiceSessionRecord,
        track: Dict[str, Any],
    ) -> Tuple[bool, int]:
        """
        Extract the direct Opus/AAC audio stream via `yt-dlp` on the worker pool and start
        streaming it through `discord.FFmpegPCMAudio` on `vc`. Returns `(started, duration_sec)`.
        """
        stream_info = await run_in_worker(extract_youtube_audio_stream, track["url"])
        if not stream_info:
            return False, 0

        audio_source = await run_in_worker(build_ffmpeg_music_source, stream_info, 0.85)
        if audio_source is None:
            return False, 0

        duration_sec = int(stream_info.get("duration") or 0)
        with self._lock:
            gen = self._music_play_generation.get(channel_id, 0) + 1
            self._music_play_generation[channel_id] = gen

        try:
            if (hasattr(vc, "is_playing") and vc.is_playing()) or (hasattr(vc, "is_paused") and vc.is_paused()):
                if hasattr(vc, "stop"):
                    vc.stop()
                elif hasattr(vc, "stop_playing"):
                    vc.stop_playing()

            track_record = {
                **track,
                "duration": duration_sec,
                "started_at": time.time(),
                "voice_channel_name": sess.voice_channel_name,
            }
            with self._lock:
                self._current_music_track[channel_id] = track_record
                self._is_playing_music[channel_id] = True
                self._music_paused[channel_id] = False
                sess.last_activity_at = time.time()

            def _after_music_track(_err: Optional[Exception]) -> None:
                if _err:
                    logger.debug("FFmpeg music playback ended with error on channel %s: %s", channel_id, _err)
                with self._lock:
                    if self._music_play_generation.get(channel_id) != gen:
                        return
                    self._is_playing_music[channel_id] = False
                    self._music_paused[channel_id] = False
                    self._current_music_track.pop(channel_id, None)
                    q = self._music_queues.get(channel_id)
                    next_track = q.popleft() if q else None
                    cur_vc = self._voice_clients.get(channel_id)
                    cur_sess = self._sessions.get(channel_id)

                if next_track is not None and cur_vc is not None and cur_sess is not None:
                    loop = get_bound_event_loop()
                    if loop and loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self._start_track_stream_async(channel_id, cur_vc, cur_sess, next_track),
                            loop,
                        )

            vc.play(audio_source, after=_after_music_track)
            print(
                f"🔊 [Voice Music Stream] Streaming '{track['song']}' by '{track['artist']}' "
                f"in '{sess.voice_channel_name}' (duration={duration_sec}s, vid={track.get('video_id')})"
            )
            return True, duration_sec
        except Exception as e:
            print(f"⚠️ [Voice Music Stream] vc.play failed: {e}")
            logger.warning("vc.play music failed: %s", e)
            with self._lock:
                self._is_playing_music[channel_id] = False
                self._current_music_track.pop(channel_id, None)
            return False, 0

    async def stream_music_from_card_async(
        self,
        channel_id: int,
        card_text: str,
        replace_current: bool = True,
        allow_auto_join: bool = False,
        active_mode: str = "research",
    ) -> Optional[str]:
        """
        If `card_text` is a valid Song Card and the bot is connected to a voice channel for `channel_id`
        (or `allow_auto_join=True` when the user requested voice in the same prompt), stream or queue
        the song ad-free via `yt-dlp` + `FFmpegPCMAudio` and return a status badge line to append to the card.
        """
        track = self.extract_song_card_track(card_text)
        if track is None:
            return None

        sess = self.get_session(channel_id)
        if sess is None:
            if not allow_auto_join:
                return None
            with self._lock:
                author_vc = self._author_voice_channels.get(channel_id)
            if author_vc is None:
                return "⚠️ **Voice Stream:** Join a Discord voice channel first (or run `!join`) so I can stream live audio!"
            self.start_session(
                channel_id=channel_id,
                voice_channel_id=int(getattr(author_vc, "id", channel_id) or channel_id),
                voice_channel_name=str(getattr(author_vc, "name", "Discord Voice") or "Discord Voice"),
                guild_id=int(getattr(getattr(author_vc, "guild", None), "id", 0) or 0) or None,
                mode=str(VoiceMode.WAKE_WORD),
                voice_profile=active_mode,
            )
            sess = self.get_session(channel_id)
            if sess is None:
                return None

        vc = await self.ensure_discord_voice_connected(channel_id)
        if vc is None or not hasattr(vc, "play"):
            return None

        if not replace_current and self.is_music_playing(channel_id):
            with self._lock:
                q = self._music_queues.setdefault(channel_id, deque())
                q.append(track)
                q_pos = len(q)
            return f"📥 **Queued in Voice ({sess.voice_channel_name} — #{q_pos}):** **{track['song']}** by **{track['artist']}**"

        started, duration_sec = await self._start_track_stream_async(channel_id, vc, sess, track)
        if not started:
            return None

        # If Music Autoplay is active in this channel and yt-dlp returned an exact duration, sync the timer!
        if duration_sec > 0:
            try:
                from src.jobs import JOB_ENGINE

                active_auto = JOB_ENGINE.get_active_autoplay_job(channel_id)
                if active_auto is not None:
                    bounded_dur = max(30, min(duration_sec + 3, 900))
                    active_auto.interval_seconds = bounded_dur
                    active_auto.next_run_at = time.time() + bounded_dur
                    JOB_ENGINE.wake()
            except Exception:
                pass

        return f"🔊 **Streaming Live in Voice ({sess.voice_channel_name})** — *Ad-Free Audio (`!pause` · `!skip` · `!stop`)*"

    def pause_music(self, channel_id: int) -> str:
        """Pause the currently streaming music track on `channel_id`'s voice client."""
        with self._lock:
            vc = self._voice_clients.get(channel_id)
            track = self._current_music_track.get(channel_id)
        if vc is not None and hasattr(vc, "pause"):
            is_playing = bool(getattr(vc, "is_playing", lambda: False)())
            if is_playing or self._is_playing_music.get(channel_id):
                vc.pause()
                with self._lock:
                    self._music_paused[channel_id] = True
                if track:
                    return f"⏸️ **Paused in Voice:** **{track['song']}** by **{track['artist']}** (Type `!resume` to continue)"
                return "⏸️ **Voice playback paused.** (Type `!resume` to continue)"
        return "⚠️ No music track is currently playing in voice."

    def resume_music(self, channel_id: int) -> str:
        """Resume a paused music track on `channel_id`'s voice client."""
        with self._lock:
            vc = self._voice_clients.get(channel_id)
            track = self._current_music_track.get(channel_id)
            was_paused = bool(self._music_paused.get(channel_id))
        if vc is not None and hasattr(vc, "resume"):
            is_paused = bool(getattr(vc, "is_paused", lambda: False)()) or was_paused
            if is_paused:
                vc.resume()
                with self._lock:
                    self._music_paused[channel_id] = False
                if track:
                    return f"▶️ **Resumed in Voice:** **{track['song']}** by **{track['artist']}**"
                return "▶️ **Voice playback resumed.**"
        return "⚠️ Voice music playback is not currently paused."

    def stop_music(self, channel_id: int, clear_queue: bool = True) -> str:
        """Stop the currently streaming music track (and optionally clear the queue) without leaving the voice channel."""
        with self._lock:
            self._music_play_generation[channel_id] = self._music_play_generation.get(channel_id, 0) + 1
            track = self._current_music_track.pop(channel_id, None)
            self._is_playing_music[channel_id] = False
            self._music_paused[channel_id] = False
            if clear_queue and channel_id in self._music_queues:
                self._music_queues[channel_id].clear()
            vc = self._voice_clients.get(channel_id)

        if vc is not None:
            try:
                if hasattr(vc, "stop"):
                    vc.stop()
                elif hasattr(vc, "stop_playing"):
                    vc.stop_playing()
            except Exception:
                pass
        if track:
            return f"⏹️ **Stopped Voice Playback:** **{track['song']}** by **{track['artist']}**"
        return "⏹️ **Stopped voice music playback.**"

    async def skip_music_async(self, channel_id: int) -> Optional[str]:
        """
        Skip the currently playing music track in `channel_id`.
        - If a track is waiting in `_music_queues[channel_id]`, starts streaming it immediately.
        - Otherwise stops the current track and returns `None` if an Autoplay job is active (so the caller
          can advance Autoplay), or a status message if no Autoplay job is active.
        """
        with self._lock:
            q = self._music_queues.get(channel_id)
            next_track = q.popleft() if q else None
            vc = self._voice_clients.get(channel_id)
            sess = self._sessions.get(channel_id)

        if next_track is not None and vc is not None and sess is not None:
            started, _ = await self._start_track_stream_async(channel_id, vc, sess, next_track)
            if started:
                return (
                    f"⏭️ **Skipped to Next Queued Track in Voice ({sess.voice_channel_name}):**\n\n"
                    f"**Song:** {next_track['song']}\n"
                    f"**Artist:** {next_track['artist']}\n"
                    f"{next_track['url']}\n"
                    f"🔊 **Streaming Live in Voice ({sess.voice_channel_name})** — *Ad-Free Audio*"
                )

        try:
            from src.jobs import JOB_ENGINE

            if JOB_ENGINE.get_active_autoplay_job(channel_id) is not None:
                self.stop_music(channel_id, clear_queue=False)
                return None
        except Exception:
            pass

        self.stop_music(channel_id, clear_queue=False)
        return "⏭️ **Skipped current track.** (Voice queue is empty — ask me to play a song or run `!autoplay <genre>`!)"

    def format_music_queue(self, channel_id: int) -> str:
        """Format the currently playing voice track and upcoming queued tracks for `channel_id`."""
        with self._lock:
            track = self._current_music_track.get(channel_id)
            paused = bool(self._music_paused.get(channel_id))
            q_list = list(self._music_queues.get(channel_id) or [])
            sess = self._sessions.get(channel_id)

        vc_name = sess.voice_channel_name if sess else "Voice"
        lines = [f"=== 🎶 VOICE MUSIC STREAM & QUEUE ({vc_name}) ==="]
        if track:
            state_badge = "⏸️ PAUSED" if paused else "🔊 STREAMING LIVE"
            dur = int(track.get("duration") or 0)
            dur_str = f" ({dur // 60}:{dur % 60:02d})" if dur > 0 else ""
            lines.append(f"- **Now Playing [{state_badge}]:** **{track['song']}** by **{track['artist']}**{dur_str}")
        else:
            lines.append("- **Now Playing:** Nothing currently streaming in voice.")

        if q_list:
            lines.append(f"\n**Up Next in Queue ({len(q_list)}):**")
            for idx, item in enumerate(q_list, start=1):
                lines.append(f"{idx}. **{item['song']}** by **{item['artist']}**")
        else:
            lines.append("- **Up Next in Queue:** Empty (React with 👍 on any song card to queue similar tracks!)")

        try:
            from src.jobs import JOB_ENGINE

            auto_job = JOB_ENGINE.get_active_autoplay_job(channel_id)
            if auto_job is not None:
                lines.append(
                    f"- **Active Radio Autoplay (`{auto_job.job_id}`):** Vibe `{auto_job.payload_text}` "
                    f"(Next track in ~{max(0, int(auto_job.next_run_at - time.time()))}s)"
                )
        except Exception:
            pass

        lines.append("\n*Controls: `!pause` · `!resume` · `!skip` · `!stop` · `!leave`*")
        return "\n".join(lines)

    def synthesize_and_play(
        self,
        channel_id: int,
        text: str,
        profile_or_mode: Optional[str] = None,
        interrupt: bool = False,
    ) -> Tuple[TtsAudioClip, bool]:
        """
        Synthesize `text` via `VoiceTtsAggregator` and play it on the active Discord `VoiceClient`
        for `channel_id` (if connected and unmuted). Returns `(tts_clip, played_on_voice_client)`.
        """
        sess = self.get_session(channel_id)
        prof_key = profile_or_mode or (sess.voice_profile if sess else "research")
        if hasattr(self._tts, "synthesize_speech_streaming"):
            clip, bind_stream_fn = self._tts.synthesize_speech_streaming(text, profile_or_mode=prof_key)
        else:
            clip, bind_stream_fn = self._tts.synthesize_speech(text, profile_or_mode=prof_key), None

        # Never let background TTS replies or tracker alerts overwrite an active ad-free music stream
        # unless the caller explicitly passed `interrupt=True` (e.g., `!speak`).
        if not interrupt and self.is_music_playing(channel_id):
            return clip, False

        played = False
        with self._lock:
            vc = self._voice_clients.get(channel_id)

        if sess is not None and not sess.is_muted:
            sess.spoken_replies_count += 1
            sess.last_activity_at = time.time()
            if vc is not None and hasattr(vc, "play"):
                try:
                    if hasattr(vc, "is_playing") and vc.is_playing():
                        if interrupt:
                            if hasattr(vc, "stop_playing"):
                                vc.stop_playing()
                            elif hasattr(vc, "stop"):
                                vc.stop()
                        else:
                            # Wait up to 6s for current spoken reply to finish so we don't cut off our own answer
                            wait_deadline = time.monotonic() + 6.0
                            while vc.is_playing() and time.monotonic() < wait_deadline:
                                time.sleep(0.05)
                            if vc.is_playing():
                                if hasattr(vc, "stop_playing"):
                                    vc.stop_playing()
                                elif hasattr(vc, "stop"):
                                    vc.stop()
                    pcm_source = build_discord_pcm_source(clip.audio_bytes, done=(bind_stream_fn is None))
                    if pcm_source is not None:
                        if bind_stream_fn is not None:
                            base_stream = getattr(pcm_source, "original", None)
                            bind_stream_fn(base_stream)
                        sess.state = str(VoiceSessionState.SPEAKING)

                        def _after_play(_err: Optional[Exception]) -> None:
                            sess.state = str(
                                VoiceSessionState.MUTED if sess.is_muted else VoiceSessionState.LISTENING
                            )

                        vc.play(pcm_source, after=_after_play)
                        played = True
                except Exception as e:
                    logger.debug("Discord VoiceClient.play failed: %s", e)
                    sess.state = str(VoiceSessionState.LISTENING)

        return clip, played

    async def speak_async(
        self,
        channel_id: int,
        text: str,
        profile_or_mode: Optional[str] = None,
    ) -> Tuple[TtsAudioClip, bool]:
        """Async wrapper that synthesizes and plays speech on `WORKER_POOL`."""
        return await run_in_worker(self.synthesize_and_play, channel_id, text, profile_or_mode)

    async def announce_tracker_event(
        self,
        channel_id: int,
        announcement_text: str,
        profile_or_mode: str = "sportscaster",
    ) -> bool:
        """
        If `channel_id` has an active unmuted voice co-host session with `announce_live_trackers=True`,
        speak a live sports or flight tracker alert directly into the Discord voice channel.
        """
        sess = self.get_session(channel_id)
        if sess is None or sess.is_muted or not sess.announce_live_trackers:
            return False
        _, played = await self.speak_async(channel_id, announcement_text, profile_or_mode=profile_or_mode)
        return played

    def transcribe_attachment_bytes(
        self,
        audio_bytes: bytes,
        model: str = "gemma4:e4b",
    ) -> str:
        """Transcribe a Discord voice message or audio file attachment (`.ogg`, `.mp3`, `.wav`)."""
        transcript, _ = self._stt.transcribe_audio(audio_bytes, model=model)
        return transcript

    def format_session_status(self, channel_id: Optional[int] = None) -> str:
        """Format a structured status block for the active voice co-host session."""
        cid = channel_id if channel_id is not None else (get_active_channel_id() or 0)
        sess = self.get_session(cid)
        with self._lock:
            last_err = self._connect_errors.get(cid)
        if sess is None or sess.state == str(VoiceSessionState.DISCONNECTED):
            err_line = f"- **Last Connection Error:** `{last_err}` (Check bot **Connect** / **Speak** voice permissions and `PyNaCl` installation)\n" if last_err else ""
            return (
                "=== 🎙️ DISCORD VOICE AI CO-HOST STATUS ===\n"
                "- **Status:** Disconnected (No active voice session in this channel)\n"
                f"{err_line}"
                "- **How to Start:** Join a Discord voice channel and type `!join` (or ask me to *\"hop in the voice channel\"*).\n"
                "- **Capabilities:** Per-speaker voice recognition (`gemma4:e4b` native `gemma4a` audio + `faster-whisper`), "
                "wake-word & open-mic conversational modes, live tool execution, Neural TTS voice replies, "
                "live sports/flight audio break-ins, and rolling voice call recaps."
            )

        prof = self._tts.resolve_voice_profile(sess.voice_profile)
        uptime_sec = max(0, int(time.time() - sess.started_at)) if sess.started_at else 0
        mins, secs = divmod(uptime_sec, 60)
        wake_list = ", ".join(f'`"{w}"`' for w in DEFAULT_WAKE_WORDS[:4])

        lines = [
            "=== 🎙️ DISCORD VOICE AI CO-HOST STATUS ===",
            f"- **Voice Channel:** **{sess.voice_channel_name}** (`ID: {sess.voice_channel_id}`)",
            f"- **Session State:** **{sess.state.upper()}** | **Uptime:** {mins}m {secs}s",
            f"- **Listening Mode:** **{sess.mode}** (Wake Words: {wake_list})",
            f"- **Voice Profile:** **{prof.display_name}** (`{prof.edge_voice}`, {prof.speaking_style})",
            f"- **Activity:** **{sess.utterances_count}** utterances transcribed | **{sess.spoken_replies_count}** spoken replies",
            f"- **Live Tracker Audio Break-Ins:** {'Enabled' if sess.announce_live_trackers else 'Disabled'}",
        ]
        cur_track = self.get_current_music_track(cid)
        if cur_track:
            paused_lbl = " (Paused)" if self._music_paused.get(cid) else ""
            lines.append(
                f"- **Now Streaming Music (Ad-Free){paused_lbl}:** **{cur_track['song']}** by **{cur_track['artist']}**"
            )
        if sess.utterances:
            last_u = sess.utterances[-1]
            lines.append(
                f"- **Latest Heard Utterance:** **{last_u.display_name}**: *\"{last_u.transcript}\"* ({last_u.duration_sec:.1f}s)"
            )
        return "\n".join(lines)

    def format_session_recap(self, channel_id: Optional[int] = None, limit: int = 25) -> str:
        """Format a chronological speaker-attributed transcript recap of the voice session."""
        cid = channel_id if channel_id is not None else (get_active_channel_id() or 0)
        sess = self.get_session(cid)
        if sess is None or not sess.utterances:
            return (
                "=== 📝 VOICE CHANNEL CONVERSATION RECAP ===\n"
                "No spoken utterances have been recorded in this channel's voice session yet."
            )

        recent = sess.utterances[-limit:]
        speakers = sorted({u.display_name for u in recent})
        lines = [
            f"=== 📝 VOICE CHANNEL CONVERSATION RECAP ({sess.voice_channel_name}) ===",
            f"- **Participants Heard ({len(speakers)}):** {', '.join(speakers)}",
            f"- **Total Utterances Logged:** {len(sess.utterances)} (showing last {len(recent)})",
            "\n=== 🗣️ CHRONOLOGICAL VOICE TRANSCRIPT ===",
        ]
        for u in recent:
            t_str = datetime.fromtimestamp(u.timestamp, tz=timezone.utc).strftime("%H:%M:%S UTC")
            trig_badge = " [🎙️ Co-Host Triggered]" if u.is_wake_word_triggered else ""
            lines.append(f"- `[{t_str}]` **{u.display_name}** ({u.duration_sec:.1f}s){trig_badge}: \"{u.transcript}\"")
            if u.bot_reply:
                lines.append(f"  ↳ 🤖 **AI Co-Host Spoken Reply:** \"{u.bot_reply}\"")

        return "\n".join(lines)

    def ensure_loop_running(self) -> None:
        """Start the background silence-flush asyncio task on the bound event loop if not already running."""
        loop = get_bound_event_loop()
        if loop is None or not loop.is_running():
            return
        with self._lock:
            if self._loop_task is not None and not self._loop_task.done():
                return
            self._stop_event.clear()
            self._loop_task = asyncio.run_coroutine_threadsafe(self._background_flush_loop(), loop)  # type: ignore[assignment]

    async def _background_flush_loop(self) -> None:
        """Periodically flush speaker buffers whose silence gap has elapsed and keep voice receivers alive."""
        last_watchdog_ts = time.monotonic()
        while not self._stop_event.is_set():
            await asyncio.sleep(0.025)
            try:
                with self._lock:
                    items = list(self._accumulators.items())
                    vc_items = list(self._voice_clients.items())
                if not items:
                    break
                for cid, acc in items:
                    ready_list = acc.flush_ready_utterances()
                    for uid, uname, wav_16k, dur_sec, rms_dbfs in ready_list:
                        submit_to_worker(
                            self.process_utterance_wav,
                            cid,
                            uid,
                            uname,
                            wav_16k,
                            dur_sec,
                            rms_dbfs,
                        )
                now_mono = time.monotonic()
                if now_mono - last_watchdog_ts >= 2.0:
                    last_watchdog_ts = now_mono
                    for cid, vc in vc_items:
                        if (
                            vc is not None
                            and getattr(vc, "is_connected", lambda: False)()
                            and hasattr(vc, "is_listening")
                            and not vc.is_listening()
                        ):
                            logger.info("Re-attaching stopped voice receiver on channel %s", cid)
                            attach_discord_voice_receiver(
                                vc,
                                lambda uid, uname, pcm, _cid=cid: self.ingest_pcm_frame(_cid, uid, uname, pcm),
                                on_ssrc_mapped=lambda ssrc, uid, uname, _cid=cid: self._on_ssrc_mapped(_cid, ssrc, uid, uname),
                            )
            except Exception as e:
                logger.debug("Voice background flush loop error: %s", e)

    def shutdown(self) -> None:
        """Stop the background loop and disconnect all active voice sessions."""
        self._stop_event.set()
        self.stop_session(None)


VOICE_COHOST = VoiceCohostService()


def clear_voice_cache(channel_id: Optional[int] = None) -> None:
    """Clear voice STT/TTS caches and per-channel session state."""
    VOICE_COHOST.stop_session(channel_id)
    clear_channel_voice_state(channel_id)
    if channel_id is None:
        clear_voice_stt_cache()
        clear_voice_tts_cache()
        clear_ytdlp_stream_cache()


def skill_voice_tracker(
    action: str = VoiceAction.STATUS,
    query: str = "",
    mode: str = "",
    voice_profile: str = "",
) -> str:
    """Manage or inspect the live Discord Voice-Channel AI Co-Host session."""
    from src.skills.voice.voice import skill_voice_info

    return skill_voice_info(action=action, query=query, mode=mode, voice_profile=voice_profile)

