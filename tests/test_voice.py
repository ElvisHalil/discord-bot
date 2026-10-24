import io
import math
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import wave

from src.context import set_active_channel_id
from src.skills.voice import (
    VOICE_COHOST,
    clear_voice_cache,
    skill_voice_info,
)
from src.skills.voice.aggregators.stt import VoiceSttAggregator, clear_voice_stt_cache
from src.skills.voice.aggregators.tts import VoiceTtsAggregator, clear_voice_tts_cache
from src.skills.voice.context import (
    detect_missed_voice_tool_call,
    enrich_voice_tool_args,
)
from src.skills.voice.engine.vad import (
    audio_bytes_to_discord_pcm_48k_stereo,
    compute_pcm_rms_dbfs,
    extract_wake_word_command,
    is_speech_frame,
    pcm_48k_stereo_to_16k_mono_wav,
    sanitize_text_for_speech,
)
from src.skills.voice.enums import VoiceMode, VoiceSessionState
from src.skills.voice.playback.discord_audio import PerSpeakerVoiceAccumulator
from src.utils.dispatcher import dispatch_voice_command


def _make_pcm_48k_stereo(duration_sec: float = 0.5, amplitude: int = 12000, freq_hz: float = 220.0) -> bytes:
    """Generate synthetic 48 kHz 16-bit stereo PCM bytes."""
    sample_rate = 48000
    n_samples = int(sample_rate * duration_sec)
    buf = bytearray(n_samples * 4)
    for i in range(n_samples):
        val = int(amplitude * math.sin(2.0 * math.pi * freq_hz * (i / sample_rate))) if amplitude > 0 else 0
        struct.pack_into("<hh", buf, i * 4, val, val)
    return bytes(buf)


class TestVoiceDspAndVad(unittest.TestCase):
    def test_compute_pcm_rms_and_speech_detection(self):
        silence = _make_pcm_48k_stereo(duration_sec=0.1, amplitude=0)
        speech = _make_pcm_48k_stereo(duration_sec=0.1, amplitude=14000)

        self.assertEqual(compute_pcm_rms_dbfs(silence), -96.0)
        self.assertFalse(is_speech_frame(silence))
        self.assertGreater(compute_pcm_rms_dbfs(speech), -15.0)
        self.assertTrue(is_speech_frame(speech))

    def test_pcm_48k_stereo_to_16k_mono_wav_and_back_to_48k_stereo(self):
        raw_48k = _make_pcm_48k_stereo(duration_sec=0.4, amplitude=10000)
        wav_16k, dur_sec, rms_dbfs = pcm_48k_stereo_to_16k_mono_wav(raw_48k)

        self.assertTrue(wav_16k.startswith(b"RIFF"))
        self.assertAlmostEqual(dur_sec, 0.4, places=2)
        self.assertGreater(rms_dbfs, -20.0)

        with wave.open(io.BytesIO(wav_16k), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 16000)

        # Round-trip back to Discord 48 kHz stereo PCM
        discord_pcm = audio_bytes_to_discord_pcm_48k_stereo(wav_16k)
        self.assertGreater(len(discord_pcm), 48000)

    def test_extract_wake_word_command(self):
        trig, q = extract_wake_word_command("Hey bot, what is the price of Shopify stock today?", require_wake_word=True)
        self.assertTrue(trig)
        self.assertEqual(q, "what is the price of Shopify stock today?")

        trig_punct, q_punct = extract_wake_word_command("Hey, bot, what time is it?", require_wake_word=True)
        self.assertTrue(trig_punct)
        self.assertEqual(q_punct, "what time is it?")

        trig2, q2 = extract_wake_word_command("just talking to my friend about dinner", require_wake_word=True)
        self.assertFalse(trig2)

        trig3, q3 = extract_wake_word_command("what are the NHL scores tonight?", require_wake_word=False)
        self.assertTrue(trig3)
        self.assertEqual(q3, "what are the NHL scores tonight?")

    def test_sanitize_text_for_speech(self):
        md = (
            "### 📈 Market Snapshot\n"
            "- **Shopify (`$SHOP.TO`)** is trading at **$148.20 CAD** (+2.45%) today, while **Gold** is -0.80%. "
            "[Yahoo Finance](<https://finance.yahoo.com/quote/SHOP.TO>)\n"
            "| Col | Val |\n"
            "| --- | --- |\n"
        )
        spoken = sanitize_text_for_speech(md)
        self.assertIn("SHOP dot T O", spoken)
        self.assertIn("up 2.45 percent", spoken)
        self.assertIn("down 0.80 percent", spoken)
        self.assertNotIn("https://", spoken)
        self.assertNotIn("|", spoken)


class TestPerSpeakerVoiceAccumulator(unittest.TestCase):
    def test_segments_utterance_after_silence_gap(self):
        acc = PerSpeakerVoiceAccumulator(
            speech_threshold_dbfs=-40.0,
            silence_gap_sec=0.8,
            min_speech_sec=0.3,
        )
        speech_chunk = _make_pcm_48k_stereo(duration_sec=0.25, amplitude=12000)
        silence_chunk = _make_pcm_48k_stereo(duration_sec=0.25, amplitude=0)

        self.assertIsNone(acc.push_pcm_frame(101, "Alice", speech_chunk, now=1.0))
        self.assertIsNone(acc.push_pcm_frame(101, "Alice", speech_chunk, now=1.25))
        # Silence gap not yet >= 0.8s
        self.assertIsNone(acc.push_pcm_frame(101, "Alice", silence_chunk, now=1.60))
        # Silence gap reaches 0.90s -> flushes utterance
        flushed = acc.push_pcm_frame(101, "Alice", silence_chunk, now=2.15)
        self.assertIsNotNone(flushed)
        uid, uname, wav_bytes, dur_sec, _ = flushed
        self.assertEqual(uid, 101)
        self.assertEqual(uname, "Alice")
        self.assertTrue(wav_bytes.startswith(b"RIFF"))
        self.assertGreaterEqual(dur_sec, 0.5)


class TestVoiceSttAndTtsAggregators(unittest.TestCase):
    def setUp(self):
        clear_voice_stt_cache()
        clear_voice_tts_cache()

    def test_gemma_native_stt_transcription_and_silence_filtering(self):
        stt = VoiceSttAggregator()
        wav_bytes, _, _ = pcm_48k_stereo_to_16k_mono_wav(_make_pcm_48k_stereo(duration_sec=0.3, amplitude=10000))

        mock_resp = SimpleNamespace(
            status_code=200,
            json=lambda: {"message": {"content": "Transcript: \"Hey bot, how is Bitcoin doing?\""}},
        )
        with patch("src.skills.voice.aggregators.stt._HTTP_SESSION.post", return_value=mock_resp) as mock_post:
            text, backend = stt.transcribe_audio(wav_bytes, backend="gemma_native")
            self.assertEqual(text, "Hey bot, how is Bitcoin doing?")
            self.assertEqual(backend, "gemma_native")
            self.assertEqual(mock_post.call_count, 1)

            # Second call with identical audio hits BoundedCache
            text2, _ = stt.transcribe_audio(wav_bytes, backend="gemma_native")
            self.assertEqual(text2, "Hey bot, how is Bitcoin doing?")
            self.assertEqual(mock_post.call_count, 1)

        self.assertEqual(VoiceSttAggregator._clean_transcript("[SILENCE]"), "")

    def test_tts_aggregator_wave_synth_fallback_and_profiles(self):
        tts = VoiceTtsAggregator()
        clip = tts.synthesize_speech(
            "**Shopify (`$SHOP.TO`)** is up +3.10%! [Source](<https://example.com>)",
            profile_or_mode="sportscaster",
            backend="wave_synth",
        )
        self.assertIn("SHOP dot T O", clip.spoken_text)
        self.assertIn("up 3.10 percent", clip.spoken_text)
        self.assertTrue(clip.audio_bytes.startswith(b"RIFF"))
        self.assertEqual(clip.voice_id, "en-US-GuyNeural")


class TestVoiceCohostServiceAndSkill(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_voice_cache()
        set_active_channel_id(777888)

    def tearDown(self):
        clear_voice_cache()

    async def test_voice_skill_lifecycle_recap_and_dispatcher(self):
        # 1. Missed tool detection & enrichment
        hist = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Elvis says: hop into the voice channel in conversational mode"},
        ]
        missed = detect_missed_voice_tool_call(hist, active_channel_id=777888)
        self.assertEqual(missed, ("skill_voice_info", {"action": "join", "query": "hop into the voice channel in conversational mode", "mode": "conversational"}))
        enriched = enrich_voice_tool_args({}, hist, active_channel_id=777888)
        self.assertEqual(enriched["action"], "join")

        # 2. Join via dispatch_voice_command with a mock Discord VoiceChannel
        mock_vc = MagicMock()
        mock_vc.is_playing.return_value = False
        mock_voice_channel = SimpleNamespace(
            id=555111,
            name="Squad Hangout",
            guild=SimpleNamespace(id=999),
            connect=AsyncMock(return_value=mock_vc),
        )
        join_out = await dispatch_voice_command(
            text="!join conversational",
            channel_id=777888,
            voice_info_fn=skill_voice_info,
            voice_cohost_service=VOICE_COHOST,
            author_voice_channel=mock_voice_channel,
            active_mode="chat",
        )
        self.assertIn("Squad Hangout", join_out)
        self.assertIn("conversational", join_out)
        self.assertIn("Casual Hangout Co-Host", join_out)
        self.assertNotIn("[SYSTEM INSTRUCTION", join_out)

        # 3. Simulate speaker utterances and verify recap
        wav_bytes, _, _ = pcm_48k_stereo_to_16k_mono_wav(_make_pcm_48k_stereo(duration_sec=0.4, amplitude=10000))
        with patch.object(
            VOICE_COHOST._stt,
            "transcribe_audio",
            return_value=("Hey bot, what time is the Oilers game?", "gemma_native"),
        ):
            utt = VOICE_COHOST.process_utterance_wav(
                channel_id=777888,
                user_id=42,
                display_name="Elvis",
                wav_bytes=wav_bytes,
                duration_sec=1.8,
            )
            self.assertIsNotNone(utt)
            self.assertTrue(utt.is_wake_word_triggered)
            utt.bot_reply = "The Oilers drop the puck at 7 PM Mountain Time tonight."

        recap_out = skill_voice_info(action="recap")
        self.assertIn("VOICE CHANNEL CONVERSATION RECAP (Squad Hangout)", recap_out)
        self.assertIn("Elvis", recap_out)
        self.assertIn("what time is the Oilers game?", recap_out)
        self.assertIn("The Oilers drop the puck at 7 PM Mountain Time tonight.", recap_out)
        self.assertIn("[SYSTEM INSTRUCTION", recap_out)

        # 4. Speak action streams PCM to mock_vc.play (using deterministic offline wave_synth)
        with patch.dict("os.environ", {"VOICE_TTS_BACKEND": "wave_synth"}):
            speak_out = skill_voice_info(action="speak", query="Goal for Arsenal! 2-1 in the 88th minute!")
            self.assertIn("Streamed live to Discord VoiceClient", speak_out)
            self.assertTrue(mock_vc.play.called)

            # 5. Live tracker announcement break-in
            played = await VOICE_COHOST.announce_tracker_event(777888, "Flight AC850 has landed in London.")
            self.assertTrue(played)

        # 6. Leave session
        leave_out = await dispatch_voice_command(
            text="!leave",
            channel_id=777888,
            voice_info_fn=skill_voice_info,
            voice_cohost_service=VOICE_COHOST,
        )
        self.assertIn("VOICE CO-HOST DISCONNECTED", leave_out)
        self.assertNotIn("[SYSTEM INSTRUCTION", leave_out)
        self.assertIsNone(VOICE_COHOST.get_session(777888))

        # 7. Natural-language join via skill_voice_info + ensure_discord_voice_connected
        VOICE_COHOST.register_author_voice_channel(777888, mock_voice_channel)
        nl_join = skill_voice_info(action="join", mode="wake_word", voice_profile="research")
        self.assertIn("Squad Hangout", nl_join)
        connected_vc = await VOICE_COHOST.ensure_discord_voice_connected(777888)
        self.assertIs(connected_vc, mock_vc)

        # 8. Switch speaking voice & tone via !voice <preset>
        voice_switch = await dispatch_voice_command(
            text="!voice british",
            channel_id=777888,
            voice_info_fn=skill_voice_info,
            voice_cohost_service=VOICE_COHOST,
        )
        self.assertIn("en-GB-RyanNeural", voice_switch)
        self.assertEqual(VOICE_COHOST.get_session(777888).voice_profile, "british")

        voice_help = await dispatch_voice_command(
            text="!voice",
            channel_id=777888,
            voice_info_fn=skill_voice_info,
            voice_cohost_service=VOICE_COHOST,
        )
        self.assertIn("AVAILABLE VOICE & TONE PRESETS", voice_help)
        VOICE_COHOST.stop_session(777888)

    def test_explicit_music_play_missed_tool_detection(self):
        from src.skills.music.context import detect_missed_music_shift_tool_call

        self.assertEqual(
            detect_missed_music_shift_tool_call([{"role": "user", "content": "User Elvis says: play me a song"}]),
            ("skill_music_discovery", {}),
        )
        self.assertEqual(
            detect_missed_music_shift_tool_call([{"role": "user", "content": "User Elvis says: play some house music"}]),
            ("skill_music_discovery", {"genre": "house"}),
        )
        self.assertEqual(
            detect_missed_music_shift_tool_call([{"role": "user", "content": "User Elvis says: play a song by Daft Punk"}]),
            ("skill_music_discovery", {"artist": "Daft Punk"}),
        )
        self.assertEqual(
            detect_missed_music_shift_tool_call([{"role": "user", "content": "User Elvis says: play Strobe by deadmau5"}]),
            ("skill_youtube_search", {"query": "Strobe by deadmau5", "max_results": 1, "is_music": True}),
        )

    def test_bare_wake_word_arms_followup_and_ssrc_merge(self):
        # 1. Bare "Hey bot?" returns (True, "") instead of sending "Hey bot?" to the LLM
        trig, rem = extract_wake_word_command("Hey bot?", require_wake_word=True)
        self.assertTrue(trig)
        self.assertEqual(rem, "")

        # 2. Bare "Hey bot" arms 8s window on VOICE_COHOST so follow-up speech without wake word triggers
        VOICE_COHOST.start_session(channel_id=777888, voice_channel_id=555111, mode="wake_word")
        wav_bytes, _, _ = pcm_48k_stereo_to_16k_mono_wav(_make_pcm_48k_stereo(duration_sec=0.4, amplitude=10000))
        with patch.object(VOICE_COHOST._stt, "transcribe_audio", return_value=("Hey bot", "faster_whisper")):
            utt_bare = VOICE_COHOST.process_utterance_wav(777888, 42, "Elvis", wav_bytes)
            self.assertIsNone(utt_bare)

        with patch.object(VOICE_COHOST._stt, "transcribe_audio", return_value=("what time is the game?", "faster_whisper")):
            utt_follow = VOICE_COHOST.process_utterance_wav(777888, 42, "Elvis", wav_bytes)
            self.assertIsNotNone(utt_follow)
            self.assertTrue(utt_follow.is_wake_word_triggered)

        # 3. SSRC buffer merge combines initial unmapped SSRC audio into resolved user_id
        acc = PerSpeakerVoiceAccumulator(silence_gap_sec=0.55, min_speech_sec=0.25)
        chunk = _make_pcm_48k_stereo(duration_sec=0.2, amplitude=12000)
        acc.push_pcm_frame(99999, "Speaker-99999", chunk, now=1.0)
        acc.merge_speaker_key(99999, 42, "Elvis")
        self.assertNotIn(99999, acc._buffers)
        self.assertIn(42, acc._buffers)
        self.assertEqual(acc._buffers[42]["display_name"], "Elvis")
        VOICE_COHOST.stop_session(777888)

    async def test_voice_profile_persists_across_speak_and_spoken_turns(self):
        mock_vc = MagicMock()
        mock_vc.is_playing.return_value = False
        mock_voice_channel = SimpleNamespace(
            id=555111,
            name="Pwn Zone",
            guild=SimpleNamespace(id=999),
            connect=AsyncMock(return_value=mock_vc),
        )
        await dispatch_voice_command(
            text="!join",
            channel_id=777888,
            voice_info_fn=skill_voice_info,
            voice_cohost_service=VOICE_COHOST,
            author_voice_channel=mock_voice_channel,
            active_mode="research-gemma",
        )
        self.assertEqual(VOICE_COHOST.get_session(777888).voice_profile, "research-gemma")

        # User runs !voice ava
        ava_out = await dispatch_voice_command(
            text="!voice ava",
            channel_id=777888,
            voice_info_fn=skill_voice_info,
            voice_cohost_service=VOICE_COHOST,
            active_mode="research-gemma",
        )
        self.assertIn("en-US-AvaNeural", ava_out)
        self.assertEqual(VOICE_COHOST.get_session(777888).voice_profile, "ava")

        # !speak must NOT overwrite 'ava' back to active_mode ('research-gemma')
        with patch.dict("os.environ", {"VOICE_TTS_BACKEND": "wave_synth"}):
            speak_out = await dispatch_voice_command(
                text="!speak Hello from Ava",
                channel_id=777888,
                voice_info_fn=skill_voice_info,
                voice_cohost_service=VOICE_COHOST,
                active_mode="research-gemma",
            )
            self.assertIn("en-US-AvaNeural", speak_out)
            self.assertEqual(VOICE_COHOST.get_session(777888).voice_profile, "ava")

            # Spoken utterance TTS synthesis also uses 'ava' and only speaks once
            clip, _ = VOICE_COHOST.synthesize_and_play(777888, "Doing great, ready to help!")
            self.assertEqual(clip.voice_id, "en-US-AvaNeural")
            self.assertEqual(VOICE_COHOST.get_session(777888).voice_profile, "ava")

        # User runs !voice sonia
        sonia_out = await dispatch_voice_command(
            text="!voice sonia",
            channel_id=777888,
            voice_info_fn=skill_voice_info,
            voice_cohost_service=VOICE_COHOST,
            active_mode="research-gemma",
        )
        self.assertIn("en-GB-SoniaNeural", sonia_out)
        self.assertEqual(VOICE_COHOST.get_session(777888).voice_profile, "sonia")
        VOICE_COHOST.stop_session(777888)

    async def test_ad_free_voice_music_streaming_queue_and_controls(self):
        from src.skills.voice.playback.discord_audio import (
            clear_ytdlp_stream_cache,
            extract_youtube_audio_stream,
        )

        clear_ytdlp_stream_cache()
        sample_card_1 = (
            "Here is a Drum & Bass roller:\n\n"
            "**Song:** Solar System\n"
            "**Artist:** Sub Focus\n"
            "**Genre:** Drum & Bass\n"
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        sample_card_2 = (
            "Here is a similar track:\n\n"
            "**Song:** Desire\n"
            "**Artist:** Sub Focus & Dimension\n"
            "**Genre:** Drum & Bass\n"
            "https://music.youtube.com/watch?v=9bZkp7q19f0"
        )

        # 1. Opt-in guard: when NOT in a voice session and allow_auto_join=False, returns None
        badge_idle = await VOICE_COHOST.stream_music_from_card_async(
            777888,
            sample_card_1,
            replace_current=True,
            allow_auto_join=False,
        )
        self.assertIsNone(badge_idle)

        # 2. Voice intent detection for compound prompts
        self.assertTrue(VOICE_COHOST.has_voice_music_intent("join voice and play some dnb"))
        self.assertTrue(VOICE_COHOST.has_voice_music_intent("play some house music in vc"))
        self.assertFalse(VOICE_COHOST.has_voice_music_intent("play some house music"))

        # 3. Auto-join + stream when allow_auto_join=True and author_voice_channel is registered
        mock_vc = MagicMock()
        is_playing_state = {"playing": False, "paused": False}
        mock_vc.is_playing.side_effect = lambda: is_playing_state["playing"]
        mock_vc.is_paused.side_effect = lambda: is_playing_state["paused"]

        def _on_play(source, after=None):
            is_playing_state["playing"] = True
            is_playing_state["paused"] = False
            mock_vc._last_after = after

        def _on_stop():
            is_playing_state["playing"] = False
            is_playing_state["paused"] = False

        def _on_pause():
            is_playing_state["playing"] = False
            is_playing_state["paused"] = True

        def _on_resume():
            is_playing_state["playing"] = True
            is_playing_state["paused"] = False

        mock_vc.play.side_effect = _on_play
        mock_vc.stop.side_effect = _on_stop
        mock_vc.pause.side_effect = _on_pause
        mock_vc.resume.side_effect = _on_resume

        mock_voice_channel = SimpleNamespace(
            id=555111,
            name="DnB Arena",
            guild=SimpleNamespace(id=999),
            connect=AsyncMock(return_value=mock_vc),
        )
        VOICE_COHOST.register_author_voice_channel(777888, mock_voice_channel)

        fake_stream_info = {
            "stream_url": "https://rr1---sn-mock.googlevideo.com/videoplayback?id=123",
            "webpage_url": "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
            "video_id": "dQw4w9WgXcQ",
            "title": "Solar System",
            "uploader": "Sub Focus",
            "duration": 248,
            "http_headers": {"User-Agent": "Mozilla/5.0"},
            "acodec": "opus",
        }
        mock_audio_source = MagicMock()

        with patch(
            "src.skills.voice.tracker.extract_youtube_audio_stream",
            return_value=fake_stream_info,
        ), patch(
            "src.skills.voice.tracker.build_ffmpeg_music_source",
            return_value=mock_audio_source,
        ):
            badge_live = await VOICE_COHOST.stream_music_from_card_async(
                777888,
                sample_card_1,
                replace_current=True,
                allow_auto_join=True,
            )
            self.assertIsNotNone(badge_live)
            self.assertIn("Streaming Live in Voice (DnB Arena)", badge_live)
            self.assertTrue(VOICE_COHOST.is_music_playing(777888))
            self.assertEqual(VOICE_COHOST.get_current_music_track(777888)["song"], "Solar System")

            # 4. Background TTS must NOT overwrite or interrupt active music playback
            with patch.dict("os.environ", {"VOICE_TTS_BACKEND": "wave_synth"}):
                _, tts_played = VOICE_COHOST.synthesize_and_play(777888, "Hello there!", interrupt=False)
                self.assertFalse(tts_played)
                self.assertTrue(VOICE_COHOST.is_music_playing(777888))

            # 5. 👍 Reaction (replace_current=False) queues Track 2 while Track 1 is playing
            badge_queued = await VOICE_COHOST.stream_music_from_card_async(
                777888,
                sample_card_2,
                replace_current=False,
            )
            self.assertIn("Queued in Voice (DnB Arena — #1)", badge_queued)
            self.assertIn("Desire", badge_queued)

            # 6. Check !queue, !pause, !resume, !skip, !stop via dispatch_voice_command
            q_out = await dispatch_voice_command(
                text="!queue",
                channel_id=777888,
                voice_info_fn=skill_voice_info,
                voice_cohost_service=VOICE_COHOST,
            )
            self.assertIn("Solar System", q_out)
            self.assertIn("Desire", q_out)

            pause_out = await dispatch_voice_command(
                text="!pause",
                channel_id=777888,
                voice_info_fn=skill_voice_info,
                voice_cohost_service=VOICE_COHOST,
            )
            self.assertIn("Paused in Voice", pause_out)
            self.assertTrue(is_playing_state["paused"])

            resume_out = await dispatch_voice_command(
                text="!resume",
                channel_id=777888,
                voice_info_fn=skill_voice_info,
                voice_cohost_service=VOICE_COHOST,
            )
            self.assertIn("Resumed in Voice", resume_out)
            self.assertTrue(is_playing_state["playing"])

            # !skip advances immediately to Desire from the queue
            skip_out = await dispatch_voice_command(
                text="!skip",
                channel_id=777888,
                voice_info_fn=skill_voice_info,
                voice_cohost_service=VOICE_COHOST,
            )
            self.assertIn("Skipped to Next Queued Track", skip_out)
            self.assertIn("Desire", skip_out)
            self.assertEqual(VOICE_COHOST.get_current_music_track(777888)["song"], "Desire")

            stop_out = await dispatch_voice_command(
                text="!stop",
                channel_id=777888,
                voice_info_fn=skill_voice_info,
                voice_cohost_service=VOICE_COHOST,
            )
            self.assertIn("Stopped Voice Playback", stop_out)
            self.assertFalse(VOICE_COHOST.is_music_playing(777888))

        # 7. Verify yt-dlp stream extractor caching with mocked YoutubeDL
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.__enter__.return_value = mock_ydl_instance
        mock_ydl_instance.extract_info.return_value = {
            "url": "https://cdn.googlevideo.com/stream123",
            "id": "dQw4w9WgXcQ",
            "title": "Solar System",
            "artist": "Sub Focus",
            "duration": 248,
            "http_headers": {"User-Agent": "TestUA"},
            "acodec": "opus",
        }
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl_instance) as mock_ydl_cls:
            s1 = extract_youtube_audio_stream("https://music.youtube.com/watch?v=dQw4w9WgXcQ")
            s2 = extract_youtube_audio_stream("https://music.youtube.com/watch?v=dQw4w9WgXcQ")
            self.assertEqual(s1["stream_url"], "https://cdn.googlevideo.com/stream123")
            self.assertEqual(s2["duration"], 248)
            self.assertEqual(mock_ydl_cls.call_count, 1)

        VOICE_COHOST.stop_session(777888)


if __name__ == "__main__":
    unittest.main()


