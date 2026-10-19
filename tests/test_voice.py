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
        trig, q = extract_wake_word_command("Hey Chappie, what is the price of Shopify stock today?", require_wake_word=True)
        self.assertTrue(trig)
        self.assertEqual(q, "what is the price of Shopify stock today?")

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
            json=lambda: {"message": {"content": "Transcript: \"Hey Chappie, how is Bitcoin doing?\""}},
        )
        with patch("src.skills.voice.aggregators.stt._HTTP_SESSION.post", return_value=mock_resp) as mock_post:
            text, backend = stt.transcribe_audio(wav_bytes, backend="gemma_native")
            self.assertEqual(text, "Hey Chappie, how is Bitcoin doing?")
            self.assertEqual(backend, "gemma_native")
            self.assertEqual(mock_post.call_count, 1)

            # Second call with identical audio hits BoundedCache
            text2, _ = stt.transcribe_audio(wav_bytes, backend="gemma_native")
            self.assertEqual(text2, "Hey Chappie, how is Bitcoin doing?")
            self.assertEqual(mock_post.call_count, 1)

        self.assertEqual(VoiceSttAggregator._clean_transcript("[SILENCE]"), "")

    def test_tts_aggregator_wave_synth_fallback_and_profiles(self):
        tts = VoiceTtsAggregator()
        clip = tts.synthesize_speech(
            "**Shopify (`$SHOP.TO`)** is up +3.10%! [Source](<https://example.com>)",
            profile_or_mode="chappie",
            backend="wave_synth",
        )
        self.assertIn("SHOP dot T O", clip.spoken_text)
        self.assertIn("up 3.10 percent", clip.spoken_text)
        self.assertTrue(clip.audio_bytes.startswith(b"RIFF"))
        self.assertEqual(clip.voice_id, "en-GB-RyanNeural")


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
            active_mode="chappie",
        )
        self.assertIn("Squad Hangout", join_out)
        self.assertIn("conversational", join_out)
        self.assertIn("Chappie Neural Voice", join_out)

        # 3. Simulate speaker utterances and verify recap
        wav_bytes, _, _ = pcm_48k_stereo_to_16k_mono_wav(_make_pcm_48k_stereo(duration_sec=0.4, amplitude=10000))
        with patch.object(
            VOICE_COHOST._stt,
            "transcribe_audio",
            return_value=("Hey Chappie, what time is the Oilers game?", "gemma_native"),
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

        # 4. Speak action streams PCM to mock_vc.play
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
        self.assertIsNone(VOICE_COHOST.get_session(777888))


if __name__ == "__main__":
    unittest.main()
