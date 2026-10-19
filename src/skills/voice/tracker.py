"""
Real-Time Discord Voice-Channel AI Co-Host Service (`src/skills/voice/tracker.py`).
Manages live Discord `VoiceClient` connections, per-speaker VAD audio buffers,
`gemma4:e4b` native `gemma4a` / `faster-whisper` STT transcription, neural TTS playback,
rolling voice conversation transcripts, and live voice announcements for tracked games/flights.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.context import get_active_channel_id, get_bound_event_loop
from src.skills.voice.aggregators.stt import VoiceSttAggregator, clear_voice_stt_cache
from src.skills.voice.aggregators.tts import VoiceTtsAggregator, clear_voice_tts_cache
from src.skills.voice.catalogs import DEFAULT_WAKE_WORDS, VOICE_ACTION_ALIASES, VOICE_MODE_ALIASES, VOICE_PROFILES
from src.skills.voice.context import (
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
)
from src.utils.workers import run_in_worker, submit_to_worker

logger = logging.getLogger(__name__)

MAX_SESSION_UTTERANCES = 120


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
        self._utterance_callback: Optional[Callable[[VoiceSessionRecord, SpeakerUtterance, str], Awaitable[Optional[str]]]] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._stop_event = threading.Event()

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
        mode: str = VoiceMode.CONVERSATIONAL,
        voice_profile: str = "research",
        voice_client: Any = None,
        announce_live_trackers: bool = True,
    ) -> VoiceSessionRecord:
        """Create and register a live `VoiceSessionRecord` and per-speaker VAD accumulator for `channel_id`."""
        now = time.time()
        norm_mode = VOICE_MODE_ALIASES.get((mode or "").strip().lower(), str(VoiceMode.CONVERSATIONAL))
        prof_key = (voice_profile or "research").strip().lower()
        if prof_key not in VOICE_PROFILES:
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
            if voice_client is not None:
                self._voice_clients[channel_id] = voice_client

        set_channel_voice_session(record, channel_id=channel_id)

        if voice_client is not None:
            attach_discord_voice_receiver(
                voice_client,
                lambda uid, uname, pcm: self.ingest_pcm_frame(channel_id, uid, uname, pcm),
            )

        self.ensure_loop_running()
        return record

    def stop_session(self, channel_id: Optional[int] = None) -> Optional[VoiceSessionRecord]:
        """Stop and remove the voice co-host session for `channel_id` (or all sessions if `None`)."""
        if channel_id is None:
            with self._lock:
                cids = list(self._sessions.keys())
                self._author_voice_channels.clear()
            for cid in cids:
                self.stop_session(cid)
            clear_channel_voice_state(None)
            return None

        with self._lock:
            record = self._sessions.pop(channel_id, None)
            acc = self._accumulators.pop(channel_id, None)
            vc = self._voice_clients.pop(channel_id, None)
            self._author_voice_channels.pop(channel_id, None)

        if acc is not None:
            acc.clear()
        if record is not None:
            record.state = str(VoiceSessionState.DISCONNECTED)
        clear_channel_voice_state(channel_id)

        if vc is not None:
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
        if acc is None or sess is None:
            return None

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

        sess.state = str(VoiceSessionState.THINKING)
        transcript, backend_used = self._stt.transcribe_audio(
            wav_bytes,
            backend=sess.stt_backend,
            model=model,
        )
        sess.state = str(VoiceSessionState.MUTED if sess.is_muted else VoiceSessionState.LISTENING)
        if not transcript:
            return None

        require_wake = sess.mode == str(VoiceMode.WAKE_WORD)
        if sess.mode == str(VoiceMode.LISTEN_ONLY):
            is_triggered, cleaned_query = False, transcript
        else:
            is_triggered, cleaned_query = extract_wake_word_command(
                transcript,
                wake_words=DEFAULT_WAKE_WORDS,
                require_wake_word=require_wake,
            )

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
        try:
            session.state = str(VoiceSessionState.THINKING)
            reply_text = await self._utterance_callback(session, utterance, cleaned_query)
            if reply_text:
                utterance.bot_reply = sanitize_text_for_speech(reply_text, max_chars=240)
                if not session.is_muted:
                    await self.speak_async(session.channel_id, reply_text, profile_or_mode=session.voice_profile)
            return reply_text
        except Exception as e:
            print(f"⚠️ [Voice Co-Host Turn Error]: {e}")
            logger.debug("Voice utterance callback error: %s", e)
            return None
        finally:
            session.state = str(VoiceSessionState.MUTED if session.is_muted else VoiceSessionState.LISTENING)

    def synthesize_and_play(
        self,
        channel_id: int,
        text: str,
        profile_or_mode: Optional[str] = None,
    ) -> Tuple[TtsAudioClip, bool]:
        """
        Synthesize `text` via `VoiceTtsAggregator` and play it on the active Discord `VoiceClient`
        for `channel_id` (if connected and unmuted). Returns `(tts_clip, played_on_voice_client)`.
        """
        sess = self.get_session(channel_id)
        prof_key = profile_or_mode or (sess.voice_profile if sess else "research")
        clip = self._tts.synthesize_speech(text, profile_or_mode=prof_key)

        played = False
        with self._lock:
            vc = self._voice_clients.get(channel_id)

        if sess is not None and not sess.is_muted:
            sess.spoken_replies_count += 1
            sess.last_activity_at = time.time()
            if vc is not None and hasattr(vc, "play"):
                try:
                    if hasattr(vc, "is_playing") and vc.is_playing() and hasattr(vc, "stop"):
                        vc.stop()
                    pcm_source = build_discord_pcm_source(clip.audio_bytes)
                    if pcm_source is not None:
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

        prof = VOICE_PROFILES.get(sess.voice_profile, VOICE_PROFILES["research"])
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
        """Periodically flush speaker buffers whose silence gap has elapsed."""
        while not self._stop_event.is_set():
            await asyncio.sleep(0.35)
            with self._lock:
                items = list(self._accumulators.items())
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


def skill_voice_tracker(
    action: str = VoiceAction.STATUS,
    query: str = "",
    mode: str = "",
    voice_profile: str = "",
) -> str:
    """Manage or inspect the live Discord Voice-Channel AI Co-Host session."""
    from src.skills.voice.voice import skill_voice_info

    return skill_voice_info(action=action, query=query, mode=mode, voice_profile=voice_profile)

