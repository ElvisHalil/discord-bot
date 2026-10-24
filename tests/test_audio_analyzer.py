import unittest
from unittest.mock import patch, MagicMock
import numpy as np

from src.skills.music.models import Track
from src.skills.music.engine.dsp import (
    analyze_waveform,
    classify_scores_to_zone,
    rank_candidates_by_acoustic_shift,
    listen_with_gemma_audio,
    clear_audio_feature_cache,
)


def _synth_pulsed_wave(
    bpm: float,
    freq_hz: float,
    amplitude: float = 0.2,
    duration_sec: float = 12.0,
    sr: int = 22050,
    add_hf_harshness: bool = False,
) -> np.ndarray:
    """Generate a synthetic rhythmic pulsed waveform at a given BPM and carrier frequency."""
    t = np.linspace(0.0, duration_sec, int(sr * duration_sec), endpoint=False, dtype=np.float32)
    carrier = np.sin(2.0 * np.pi * freq_hz * t).astype(np.float32)
    if add_hf_harshness:
        # Add high-frequency overtones (3.2 kHz and 4.8 kHz) to simulate distorted synths/guitars
        carrier = (
            0.4 * carrier
            + 0.35 * np.sin(2.0 * np.pi * 3200.0 * t)
            + 0.25 * np.sin(2.0 * np.pi * 4800.0 * t)
        ).astype(np.float32)

    beat_period = 60.0 / bpm
    phase = np.mod(t, beat_period) / beat_period
    envelope = np.exp(-6.0 * phase).astype(np.float32)
    return (amplitude * envelope * carrier).astype(np.float32)


class TestAudioAnalyzer(unittest.TestCase):
    def setUp(self):
        clear_audio_feature_cache()

    def test_soft_slow_vs_fast_hard_waveform_classification(self):
        """A quiet 80 BPM low-frequency tone should classify as slow_soft, while a loud 144 BPM harsh wave is fast_hard."""
        slow_soft_wave = _synth_pulsed_wave(
            bpm=80.0,
            freq_hz=350.0,
            amplitude=0.06,
            add_hf_harshness=False,
        )
        fast_hard_wave = _synth_pulsed_wave(
            bpm=144.0,
            freq_hz=1200.0,
            amplitude=0.55,
            add_hf_harshness=True,
        )

        prof_soft = analyze_waveform(slow_soft_wave, catalog_gain=-18.5)
        prof_hard = analyze_waveform(fast_hard_wave, catalog_gain=-5.2)

        self.assertLess(prof_soft["bpm"], 95.0)
        self.assertGreater(prof_hard["bpm"], 130.0)
        self.assertLess(prof_soft["speed_score"], prof_hard["speed_score"])
        self.assertLess(prof_soft["intensity_score"], 3.5)
        self.assertGreater(prof_hard["intensity_score"], 7.5)
        self.assertEqual(prof_soft["ui_zone"], "slow_soft")
        self.assertEqual(prof_hard["ui_zone"], "fast_hard")

    def test_warm_loud_drums_not_fooled_into_hard_zone(self):
        """A warm low-frequency beat with loud -6.3 dB gain (like chill jazz-hop) should score much softer than a harsh track at -6.3 dB."""
        warm_chill_wave = _synth_pulsed_wave(
            bpm=90.0,
            freq_hz=420.0,
            amplitude=0.35,
            add_hf_harshness=False,
        )
        harsh_aggro_wave = _synth_pulsed_wave(
            bpm=90.0,
            freq_hz=1400.0,
            amplitude=0.35,
            add_hf_harshness=True,
        )

        prof_chill = analyze_waveform(warm_chill_wave, catalog_gain=-6.3)
        prof_aggro = analyze_waveform(harsh_aggro_wave, catalog_gain=-6.3)

        # Despite identical -6.3 dB gain, the warm wave lacks high-frequency harshness
        self.assertLess(prof_chill["hf_energy_ratio"], 0.01)
        self.assertGreater(prof_aggro["hf_energy_ratio"], 0.10)
        self.assertLess(prof_chill["intensity_score"] + 2.5, prof_aggro["intensity_score"])
        self.assertNotIn("hard", prof_chill["ui_zone"])
        self.assertIn("hard", prof_aggro["ui_zone"])

    def test_rank_candidates_by_acoustic_shift_directions(self):
        """Candidate re-ranking should accurately order tracks for faster, slower, harder, and softer shifts."""
        anchor = {"bpm": 120.0, "speed_score": 5.5, "intensity_score": 5.5, "gain_db": -10.0}

        t_slow_soft = Track(title="Slow Soft", artist="Artist A")
        t_fast_mid = Track(title="Fast Mid", artist="Artist B")
        t_mid_hard = Track(title="Mid Hard", artist="Artist C")

        fake_profiles = {
            "Slow Soft": {"bpm": 85.0, "speed_score": 2.5, "intensity_score": 2.5, "gain_db": -16.0},
            "Fast Mid": {"bpm": 142.0, "speed_score": 8.2, "intensity_score": 5.6, "gain_db": -10.2},
            "Mid Hard": {"bpm": 122.0, "speed_score": 5.7, "intensity_score": 9.1, "gain_db": -5.4},
        }

        def _fake_get_profile(title, artist="", deezer_id=None, preview_url=None, deezer_agg=None):
            return dict(fake_profiles[title])

        with patch("src.skills.music.engine.dsp.get_track_acoustic_profile", side_effect=_fake_get_profile):
            # 1. Faster (+1 tempo, 0 intensity) -> Fast Mid should win
            ranked_faster = rank_candidates_by_acoustic_shift(
                [t_slow_soft, t_mid_hard, t_fast_mid],
                d_tempo=1,
                d_intensity=0,
                anchor_profile=anchor,
            )
            self.assertEqual(ranked_faster[0].title, "Fast Mid")

            # 2. Harder (0 tempo, +1 intensity) -> Mid Hard should win
            ranked_harder = rank_candidates_by_acoustic_shift(
                [t_slow_soft, t_fast_mid, t_mid_hard],
                d_tempo=0,
                d_intensity=1,
                anchor_profile=anchor,
            )
            self.assertEqual(ranked_harder[0].title, "Mid Hard")

            # 3. Slower & Softer (-1 tempo, -1 intensity) -> Slow Soft should win
            ranked_slower_softer = rank_candidates_by_acoustic_shift(
                [t_fast_mid, t_mid_hard, t_slow_soft],
                d_tempo=-1,
                d_intensity=-1,
                anchor_profile=anchor,
            )
            self.assertEqual(ranked_slower_softer[0].title, "Slow Soft")

    def test_skill_music_analysis_uses_dsp_and_gemma_when_freqblog_empty(self):
        """When FreqBlog has no API key or returns empty, skill_music_analysis(action='bpm_key') should use DSP + gemma4:e4b."""
        from src.skills.music.music import skill_music_analysis

        fake_prof = {
            "title": "Feather",
            "artist": "Nujabes",
            "bpm": 89.1,
            "onset_rate": 4.33,
            "speed_score": 4.4,
            "intensity_score": 5.2,
            "gain_db": -6.3,
            "spectral_centroid_hz": 1677.0,
            "ui_zone": "mid_mid",
            "preview_url": "https://cdnt-preview.dzcdn.net/stream/fake.mp3",
            "_mp3_bytes": b"fake-mp3",
        }
        fake_gemma = {
            "instruments": "Muffled boom-bap drums, melancholic piano & flute loop",
            "vocals": "Smooth narrative rap delivery",
            "vibe": "Introspective, chill, nostalgic jazz-hop",
        }

        with (
            patch("src.skills.music.engine.dsp.get_track_acoustic_profile", return_value=fake_prof),
            patch("src.skills.music.engine.dsp.listen_with_gemma_audio", return_value=fake_gemma),
        ):
            out = skill_music_analysis(track="Feather by Nujabes", action="bpm_key")

        self.assertIn("AUDIO INTELLIGENCE FOR 'Feather'", out)
        self.assertIn("BPM: ~89.1", out)
        self.assertIn("Acoustic Intensity: 5.2 / 10.0", out)
        self.assertIn("Muffled boom-bap drums", out)
        self.assertIn("Introspective, chill, nostalgic jazz-hop", out)

    def test_deezer_resolve_track_details_rejects_mismatched_artist(self):
        """When searching Deezer for an underground artist ('Spinee'), fuzzy results by a different artist ('DeathbyRomy') must be rejected."""
        from src.skills.music.aggregators.deezer import DeezerAggregator, clear_deezer_cache
        clear_deezer_cache()
        dz = DeezerAggregator()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {
                    "id": 2487463931,
                    "title": "Hellhound",
                    "artist": {"id": 99, "name": "DeathbyRomy"},
                    "album": {"title": "Hellhound"},
                    "preview": "https://cdnt-preview.dzcdn.net/stream/fake.mp3",
                }
            ]
        }
        with patch("src.skills.music.aggregators.deezer.requests.get", return_value=mock_resp):
            res = dz.resolve_track_details("Hell Hound", "Spinee")
            self.assertIsNone(res)

    def test_youtube_topic_channel_requires_artist_match_when_artist_specified(self):
        """Topic channels from a different artist ('Robert Johnson - Topic') must be rejected when an explicit artist ('Spinee') was requested."""
        from src.skills.music.playback.youtube_music import score_music_candidate
        is_valid, score, reason = score_music_candidate(
            "Hell Hound by Spinee",
            "Hell Hound On My Trail",
            "Robert Johnson - Topic",
        )
        self.assertFalse(is_valid)
        self.assertEqual(score, 0)

    def test_channel_played_song_preserves_alt_song_and_acoustic_metrics(self):
        """record_channel_played_song should store alt_song, bpm, speed_score, intensity_score, and gain_db across chained shifts."""
        from src.skills.music.engine.spectrum import (
            record_channel_played_song,
            get_channel_last_played_song,
            clear_channel_played_songs,
        )
        clear_channel_played_songs()
        record_channel_played_song(
            song="Dancing with your eyes closed (Official Audio)",
            artist="Jane Remover",
            genre="Hyperpop",
            ui_zone="fast_mid",
            channel_id=777,
            alt_song="Dancing with your eyes closed",
            bpm=152.0,
            speed_score=8.1,
            intensity_score=6.2,
            gain_db=-7.5,
        )
        st = get_channel_last_played_song(channel_id=777)
        self.assertIsNotNone(st)
        self.assertEqual(st["alt_song"], "Dancing with your eyes closed")
        self.assertAlmostEqual(st["bpm"], 152.0)
        self.assertAlmostEqual(st["speed_score"], 8.1)
        self.assertEqual(st["ui_zone"], "fast_mid")

    def test_boundary_spectrum_shift_excludes_mainstream_and_escalates_high_velocity(self):
        """At the fast boundary (prefer_deep_cuts=True, d_tempo=1), get_spectrum_tracks must not dilute with slow mainstream tags."""
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.engine.spectrum import ZONE_BY_NAME

        mgr = MusicAggregatorManager()
        mock_lfm = MagicMock()
        mock_lfm.get_charts.return_value = []
        mock_dz = MagicMock()
        mock_dz.get_charts.return_value = []
        mgr.aggregators = {"lastfm": mock_lfm, "deezer": mock_dz}

        fast_mid = ZONE_BY_NAME["fast_mid"]
        mgr.get_spectrum_tracks(
            target_zone=fast_mid,
            prefer_deep_cuts=True,
            d_tempo=1,
            anchor_bpm=158.0,
            seed_genre="Hyperpop",
        )
        queried_tags = [c.args[0].lower() for c in mock_lfm.get_charts.call_args_list]
        for mainstream_slow in ("house", "nu-disco"):
            self.assertNotIn(mainstream_slow, queried_tags)
        self.assertIn("hyperpop", queried_tags)

    def test_rank_candidates_by_acoustic_similarity_filters_outliers_and_respects_half_time(self):
        """Acoustic similarity re-ranker should demote slow/low-energy outliers while accepting half-time/full-time DnB matches."""
        from src.skills.music.engine.dsp import rank_candidates_by_acoustic_similarity

        anchor = {"bpm": 174.0, "speed_score": 8.8, "intensity_score": 7.6}

        t_outlier = Track(title="Slow Ambient Skit", artist="Artist A", tier=1)
        t_halftime_dnb = Track(title="Liquid Roller", artist="Artist B", tier=2)
        t_fulltime_dnb = Track(title="Dancefloor Anthem", artist="Artist C", tier=1)

        fake_profiles = {
            "Slow Ambient Skit": {"bpm": 105.0, "speed_score": 3.1, "intensity_score": 2.4, "gain_db": -16.0},
            "Liquid Roller": {"bpm": 87.0, "speed_score": 8.0, "intensity_score": 6.9, "gain_db": -8.2},
            "Dancefloor Anthem": {"bpm": 174.0, "speed_score": 8.7, "intensity_score": 7.8, "gain_db": -6.8},
        }

        def _fake_get_profile(title, artist="", deezer_id=None, preview_url=None, deezer_agg=None):
            return dict(fake_profiles[title])

        with patch("src.skills.music.engine.dsp.get_track_acoustic_profile", side_effect=_fake_get_profile):
            ranked = rank_candidates_by_acoustic_similarity(
                [t_outlier, t_halftime_dnb, t_fulltime_dnb],
                seed_song="Desire",
                seed_artist="Sub Focus",
                anchor_profile=anchor,
            )
            # Slow Ambient Skit (slot 0) must be pushed to the back behind both DnB tracks
            self.assertEqual(ranked[-1].title, "Slow Ambient Skit")
            self.assertIn(ranked[0].title, {"Liquid Roller", "Dancefloor Anthem"})


if __name__ == "__main__":
    unittest.main()

