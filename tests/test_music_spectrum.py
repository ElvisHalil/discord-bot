import unittest
from unittest.mock import patch

from src.skills.music.models import Track
from src.skills.music.engine.spectrum import (
    SPECTRUM_CHART,
    ZONE_BY_NAME,
    classify_genre_to_zone,
    parse_spectrum_shift,
    step_spectrum_zone,
    has_non_music_domain_context,
    is_valid_music_shift_context,
    record_channel_played_song,
    get_channel_last_played_song,
    clear_channel_played_songs,
)
from src.skills.music.engine.formatting import format_song_card
from src.skills.music.music import skill_music_discovery
from src.orchestrator import (
    normalize_tool_args,
    enrich_music_tool_args,
    detect_missed_music_shift_tool_call,
)


class TestMusicSpectrumGridAndClassifier(unittest.TestCase):
    def setUp(self):
        clear_channel_played_songs()

    def test_all_nine_zones_present(self):
        expected_zones = {
            "slow_soft", "mid_soft", "fast_soft",
            "slow_mid", "mid_mid", "fast_mid",
            "slow_hard", "mid_hard", "fast_hard",
        }
        self.assertEqual(set(ZONE_BY_NAME.keys()), expected_zones)
        self.assertEqual(len(SPECTRUM_CHART), 9)

    def test_classify_genre_to_zone_exact_and_parent(self):
        # Exact mainstream tags
        self.assertEqual(classify_genre_to_zone("ambient"), "slow_soft")
        self.assertEqual(classify_genre_to_zone("indie folk"), "mid_soft")
        self.assertEqual(classify_genre_to_zone("liquid dnb"), "fast_soft")
        self.assertEqual(classify_genre_to_zone("trip-hop"), "slow_mid")
        self.assertEqual(classify_genre_to_zone("synthpop"), "mid_mid")
        self.assertEqual(classify_genre_to_zone("house"), "fast_mid")
        self.assertEqual(classify_genre_to_zone("doom metal"), "slow_hard")
        self.assertEqual(classify_genre_to_zone("hard rock"), "mid_hard")
        self.assertEqual(classify_genre_to_zone("neurofunk"), "fast_hard")

        # Deep cut tags
        self.assertEqual(classify_genre_to_zone("bossanova"), "mid_soft")
        self.assertEqual(classify_genre_to_zone("microhouse"), "fast_soft")
        self.assertEqual(classify_genre_to_zone("city pop"), "mid_mid")
        self.assertEqual(classify_genre_to_zone("gabber"), "fast_hard")

        # Broad parent genres from Deezer/iTunes
        self.assertEqual(classify_genre_to_zone("Classical"), "slow_soft")
        self.assertEqual(classify_genre_to_zone("R&B/Soul"), "mid_soft")
        self.assertEqual(classify_genre_to_zone("Alternative"), "mid_mid")
        self.assertEqual(classify_genre_to_zone("Hip-Hop/Rap"), "mid_mid")
        self.assertEqual(classify_genre_to_zone("Dance / Electronic"), "fast_mid")
        self.assertEqual(classify_genre_to_zone("Heavy Metal"), "mid_hard")

        # Unidentifiable genre returns None
        self.assertIsNone(classify_genre_to_zone(""))
        self.assertIsNone(classify_genre_to_zone("Unknown Genre XYZ"))

    def test_parse_spectrum_shift_axes_and_diagonals(self):
        self.assertEqual(parse_spectrum_shift("harder"), (0, 1, "Harder (+1 Intensity)"))
        self.assertEqual(parse_spectrum_shift("go heavier"), (0, 1, "Harder (+1 Intensity)"))
        self.assertEqual(parse_spectrum_shift("more intense"), (0, 1, "Harder (+1 Intensity)"))
        self.assertEqual(parse_spectrum_shift("softer"), (0, -1, "Softer (-1 Intensity)"))
        self.assertEqual(parse_spectrum_shift("more chill"), (0, -1, "Softer (-1 Intensity)"))
        self.assertEqual(parse_spectrum_shift("faster"), (1, 0, "Faster (+1 Tempo)"))
        self.assertEqual(parse_spectrum_shift("speed it up"), (1, 0, "Faster (+1 Tempo)"))
        self.assertEqual(parse_spectrum_shift("slower"), (-1, 0, "Slower (-1 Tempo)"))
        self.assertEqual(parse_spectrum_shift("slow it down"), (-1, 0, "Slower (-1 Tempo)"))

        # Diagonal shifts
        self.assertEqual(
            parse_spectrum_shift("faster and harder"),
            (1, 1, "Faster (+1 Tempo) & Harder (+1 Intensity)"),
        )
        self.assertEqual(
            parse_spectrum_shift("slower and softer"),
            (-1, -1, "Slower (-1 Tempo) & Softer (-1 Intensity)"),
        )

        # Non-comparative mood words should NOT be parsed as relative notch shifts
        self.assertIsNone(parse_spectrum_shift("play something hard"))
        self.assertIsNone(parse_spectrum_shift("play me a banger"))
        self.assertIsNone(parse_spectrum_shift("play chill music"))

    def test_step_spectrum_zone_and_default_center(self):
        # Default center start (mid_mid) when current_zone is None
        res_default = step_spectrum_zone(None, 0, 1)  # harder from mid_mid -> mid_hard
        self.assertTrue(res_default["used_default_start"])
        self.assertEqual(res_default["start_zone"], "mid_mid")
        self.assertEqual(res_default["target_zone_name"], "mid_hard")
        self.assertFalse(res_default["at_boundary"])

        # Step faster from mid_soft -> fast_soft
        res_fast = step_spectrum_zone("mid_soft", 1, 0)
        self.assertFalse(res_fast["used_default_start"])
        self.assertEqual(res_fast["target_zone_name"], "fast_soft")

        # Step slower from mid_mid -> slow_mid
        res_slow = step_spectrum_zone("mid_mid", -1, 0)
        self.assertEqual(res_slow["target_zone_name"], "slow_mid")

        # Boundary clamping: harder from fast_hard stays at fast_hard with at_boundary=True
        res_bound = step_spectrum_zone("fast_hard", 0, 1)
        self.assertEqual(res_bound["target_zone_name"], "fast_hard")
        self.assertTrue(res_bound["at_boundary"])

        # Boundary clamping: softer from slow_soft stays at slow_soft with at_boundary=True
        res_low_bound = step_spectrum_zone("slow_soft", 0, -1)
        self.assertEqual(res_low_bound["target_zone_name"], "slow_soft")
        self.assertTrue(res_low_bound["at_boundary"])


class TestMusicSpectrumContextDisambiguation(unittest.TestCase):
    def test_non_music_domain_conversations_rejected(self):
        non_music_prompts = [
            "User Alex says: Is the RTX 5090 faster than the 4090?",
            "User Alex says: Why is Python slower than C++?",
            "User Alex says: Leclerc needs to pit for softer tires in the next lap",
            "User Alex says: Real Madrid has a harder schedule next month in La Liga",
            "User Alex says: This math exam was way harder than last week's",
            "User Alex says: The new update runs much faster on Windows",
        ]
        song_card_history = [
            {"role": "assistant", "content": "**Song:** Nightcall\n**Artist:** Kavinsky\nhttps://music.youtube.com/watch?v=MV_3Dpw-BRY"},
        ]
        for prompt in non_music_prompts:
            history = song_card_history + [{"role": "user", "content": prompt}]
            self.assertFalse(
                is_valid_music_shift_context(prompt, history),
                f"Expected non-music prompt to be rejected: {prompt}",
            )
            enriched = enrich_music_tool_args({"shift": "faster"}, history)
            self.assertTrue(
                str(enriched.get("shift", "")).startswith("NON_MUSIC_CONVERSATION:"),
                f"Expected enrich_music_tool_args to flag non-music prompt: {prompt}",
            )

    def test_casual_followup_after_non_music_reply_rejected(self):
        history = [
            {"role": "assistant", "content": "Quantum entanglement occurs when two particles remain correlated across distance."},
            {"role": "user", "content": "User Alex says: can you explain that slower?"},
        ]
        self.assertFalse(is_valid_music_shift_context(history[-1]["content"], history))
        self.assertIsNone(detect_missed_music_shift_tool_call(history))

    def test_tier_1a_reply_to_song_card_accepted(self):
        user_msg = "User Alex says [Replying to song card: 'Nightcall' by 'Kavinsky']: harder"
        history = [{"role": "user", "content": user_msg}]
        self.assertTrue(is_valid_music_shift_context(user_msg, history))

        enriched = enrich_music_tool_args({}, history)
        self.assertEqual(enriched.get("similar_to"), "Nightcall by Kavinsky")
        self.assertEqual(enriched.get("shift"), "harder")

        missed = detect_missed_music_shift_tool_call(history)
        self.assertEqual(missed, ("skill_music_discovery", {"shift": "harder", "similar_to": "Nightcall by Kavinsky"}))

    def test_tier_1b_explicit_music_shift_accepted(self):
        user_msg = "User Alex says: play something faster and harder"
        history = [{"role": "user", "content": user_msg}]
        self.assertTrue(is_valid_music_shift_context(user_msg, history))

        enriched = enrich_music_tool_args({}, history)
        self.assertEqual(enriched.get("shift"), "play something faster and harder")

    def test_tier_2_immediate_followup_after_song_card_accepted(self):
        history = [
            {
                "role": "assistant",
                "content": (
                    "Here is a great track:\n"
                    "**Song:** Holocene\n"
                    "**Artist:** Bon Iver\n"
                    "**Genre:** Indie Folk\n"
                    "https://music.youtube.com/watch?v=TWcyIpul8OE"
                ),
            },
            {"role": "user", "content": "User Alex says: faster"},
        ]
        self.assertTrue(is_valid_music_shift_context(history[-1]["content"], history))
        missed = detect_missed_music_shift_tool_call(history)
        self.assertEqual(missed, ("skill_music_discovery", {"shift": "faster"}))


class TestSkillMusicDiscoverySpectrumIntegration(unittest.TestCase):
    def setUp(self):
        clear_channel_played_songs()

    @patch("src.skills.music.engine.dsp.rank_candidates_by_acoustic_shift", side_effect=lambda pool, **kw: pool)
    @patch("src.skills.music.engine.dsp.get_track_acoustic_profile", return_value=None)
    @patch("src.skills.music.music._pre_resolve_top_candidate", return_value="")
    @patch("src.skills.music.music.manager")
    def test_chained_notches_from_playing_song(self, mock_manager, _mock_pre, _mock_prof, _mock_rank):
        # 1. Simulate a song card being formatted in the channel ("Holocene" by "Bon Iver", Indie Folk -> mid_soft)
        format_song_card(
            song="Holocene",
            artist="Bon Iver",
            url="https://music.youtube.com/watch?v=TWcyIpul8OE",
            genre="Indie Folk",
        )
        last = get_channel_last_played_song()
        self.assertIsNotNone(last)
        self.assertEqual(last["ui_zone"], "mid_soft")

        # Configure mock manager to return real zone resolution and dummy tracks
        def fake_resolve(song, artist, known_genre=None, known_zone=None):
            if known_zone:
                return (known_genre or "Detected", known_zone)
            z = classify_genre_to_zone(known_genre or "")
            return (known_genre, z)

        mock_manager.resolve_track_genre_and_zone.side_effect = fake_resolve
        mock_manager.get_spectrum_tracks.side_effect = lambda target_zone, prefer_deep_cuts=False, exclude_query=None, **kw: [
            Track(
                title=f"Track in {target_zone['ui_zone']}",
                artist=f"Artist {target_zone['ui_zone']}",
                source="Last.fm",
                genre=(target_zone["deep_cut_tags"][0] if prefer_deep_cuts else target_zone["mainstream_tags"][0]).title(),
            )
        ]

        # 2. User says "faster" -> moves 1 notch from mid_soft to fast_soft
        out1 = skill_music_discovery(shift="faster")
        self.assertIn("New Target Zone: fast_soft", out1)
        self.assertEqual(get_channel_last_played_song()["ui_zone"], "fast_soft")

        # 3. User says "harder" -> moves 1 notch from fast_soft to fast_mid
        out2 = skill_music_discovery(shift="harder")
        self.assertIn("New Target Zone: fast_mid", out2)
        self.assertEqual(get_channel_last_played_song()["ui_zone"], "fast_mid")

        # 4. User says "harder" -> moves 1 notch from fast_mid to fast_hard
        out3 = skill_music_discovery(shift="harder")
        self.assertIn("New Target Zone: fast_hard", out3)
        self.assertEqual(get_channel_last_played_song()["ui_zone"], "fast_hard")

        # 5. User says "harder" again at boundary -> stays at fast_hard and uses deep_cut_tags
        out4 = skill_music_discovery(shift="harder")
        self.assertIn("New Target Zone: fast_hard", out4)
        _, kwargs = mock_manager.get_spectrum_tracks.call_args
        self.assertTrue(kwargs.get("prefer_deep_cuts"))

    @patch("src.skills.music.engine.dsp.rank_candidates_by_acoustic_shift", side_effect=lambda pool, **kw: pool)
    @patch("src.skills.music.engine.dsp.get_track_acoustic_profile", return_value=None)
    @patch("src.skills.music.music._pre_resolve_top_candidate", return_value="")
    @patch("src.skills.music.music.manager")
    def test_default_center_start_when_no_song_playing(self, mock_manager, _mock_pre, _mock_prof, _mock_rank):
        mock_manager.resolve_track_genre_and_zone.return_value = (None, None)
        mock_manager.get_spectrum_tracks.return_value = [
            Track(title=" Neon Drive ", artist="Kavinsky", source="Deezer", genre="Synthwave")
        ]

        out = skill_music_discovery(shift="faster")
        self.assertIn("DEFAULT CENTER START", out)
        self.assertIn("Starting Zone: mid_mid", out)
        self.assertIn("New Target Zone: fast_mid", out)
        self.assertEqual(get_channel_last_played_song()["ui_zone"], "fast_mid")

    def test_skill_music_discovery_rejects_non_music_shift_string(self):
        out = skill_music_discovery(shift="Is the RTX 5090 faster than the 4090?")
        self.assertIn("non-music comparison or domain question", out)

    def test_normalize_tool_args_maps_shift_aliases(self):
        norm = normalize_tool_args("skill_music_discovery", skill_music_discovery, {"direction": "harder"})
        self.assertEqual(norm, {"shift": "harder"})


if __name__ == "__main__":
    unittest.main()
