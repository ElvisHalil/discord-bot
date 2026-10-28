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

    def test_genre_family_continuity_on_spectrum_shift_and_mood(self):
        from unittest.mock import MagicMock
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.engine.spectrum import classify_genre_family

        self.assertEqual(classify_genre_family("Tech House"), "electronic")
        self.assertEqual(classify_genre_family("Drum and Bass"), "electronic")
        self.assertEqual(classify_genre_family("Thrash Metal"), "rock_metal")
        self.assertEqual(classify_genre_family("Hardcore Punk"), "rock_metal")
        self.assertEqual(classify_genre_family("Boom Bap"), "hiphop_rnb")
        self.assertEqual(classify_genre_family("Drift Phonk"), "hiphop_rnb")

        mgr = MusicAggregatorManager()
        mock_lfm = MagicMock()
        mock_lfm.get_charts.return_value = []
        mock_dz = MagicMock()
        mock_dz.get_charts.return_value = []
        mock_bp = MagicMock()
        mock_bp.get_charts.return_value = []
        mgr.aggregators = {"lastfm": mock_lfm, "deezer": mock_dz, "beatport": mock_bp}

        # 1. Shifting into fast_hard from 'Tech House' must stay in Electronic (no thrash metal / hardcore punk)
        fast_hard = ZONE_BY_NAME["fast_hard"]
        mgr.get_spectrum_tracks(
            target_zone=fast_hard,
            prefer_deep_cuts=False,
            d_tempo=0,
            d_intensity=1,
            seed_genre="Tech House",
        )
        queried_tags = [c.args[0].lower() for c in mock_lfm.get_charts.call_args_list]
        self.assertTrue(len(queried_tags) > 0)
        for off_family in ("thrash metal", "hardcore punk", "grindcore"):
            self.assertNotIn(off_family, queried_tags)
        for tag in queried_tags:
            self.assertEqual(classify_genre_family(tag), "electronic")

        # 2. Standalone mood ('play a banger') in enrich_music_tool_args inherits the channel's active genre ('Drum and Bass')
        record_channel_played_song(
            song="Solar System",
            artist="Sub Focus",
            genre="Drum and Bass",
            ui_zone="fast_hard",
        )
        history = [{"role": "user", "content": "User Alex says: play a banger"}]
        enriched = enrich_music_tool_args({"mood": "banger"}, history)
        self.assertEqual(enriched.get("genre"), "Drum and Bass")
        self.assertEqual(enriched.get("mood"), "banger")


class TestSongCardButtons(unittest.IsolatedAsyncioTestCase):
    """Tests for the Discord Song Card `⏭️ Next` and `👍 Like` button interactions."""

    async def test_build_song_card_view_and_send_discord_message(self):
        from unittest.mock import AsyncMock, MagicMock
        import main as bot_main

        song_card = (
            "Here is a track:\n\n"
            "**Song:** Solar System\n"
            "**Artist:** Sub Focus\n"
            "**Genre:** Drum and Bass\n\n"
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        plain_text = "The Oilers won 4-2 last night."

        view_card = bot_main.build_song_card_view(song_card)
        self.assertIsNotNone(view_card)
        custom_ids = [c.custom_id for c in view_card.children]
        # `⏭️` in front of `👍`, disabled spacer in between, and `📻 Autoplay` on the right
        self.assertEqual(custom_ids, ["song_card:next", "song_card:like", "song_card:spacer", "song_card:autoplay"])

        self.assertIsNone(bot_main.build_song_card_view(plain_text))

        channel = MagicMock()
        channel.send = AsyncMock()

        await bot_main._send_discord_message(channel, plain_text)
        channel.send.assert_awaited_once_with(plain_text)

        channel.send.reset_mock()
        bot_main._last_song_card_messages.clear()
        bot_main._channel_song_card_messages.clear()
        bot_main._song_card_button_states.clear()
        msg1 = MagicMock(id=1001, view=None, edit=AsyncMock())
        msg2 = MagicMock(id=1002, view=None, edit=AsyncMock())
        channel.id = 777100
        channel.send = AsyncMock(side_effect=[msg1, msg2])

        await bot_main._send_discord_message(channel, song_card)
        self.assertEqual(channel.send.await_count, 1)
        sent_args, sent_kwargs = channel.send.call_args
        self.assertEqual(sent_args[0], song_card)
        first_view = sent_kwargs.get("view")
        self.assertIsInstance(first_view, bot_main.SongCardView)
        self.assertEqual(
            [(c.custom_id, c.disabled, c.label) for c in first_view.children],
            [
                ("song_card:next", False, None),
                ("song_card:like", False, None),
                ("song_card:spacer", True, "\u2800"),
                ("song_card:autoplay", False, "Autoplay"),
            ],
        )
        msg1.edit.assert_not_awaited()

        # Sending a second Song Card (while Autoplay is OFF) disables `⏭️ Next` on `msg1` while keeping `👍 Like` and `📻 Autoplay` enabled
        await bot_main._send_discord_message(channel, song_card)
        msg1.edit.assert_awaited_once()
        disabled_view = msg1.edit.call_args.kwargs.get("view")
        self.assertIsInstance(disabled_view, bot_main.SongCardView)
        self.assertEqual(
            [(c.custom_id, c.disabled, c.label) for c in disabled_view.children],
            [
                ("song_card:next", True, None),
                ("song_card:like", False, None),
                ("song_card:spacer", True, "\u2800"),
                ("song_card:autoplay", False, "Autoplay"),
            ],
        )
        self.assertIs(bot_main._last_song_card_messages.get(777100), msg2)

    async def test_like_button_interaction_matches_reaction_flow_and_deduplicates(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        import main as bot_main

        channel_id = 777101
        msg_id = 888202
        user_id = 999303
        bot_main.handled_reactions.clear()
        bot_main._song_card_button_states.clear()
        bot_main.chat_history.pop(channel_id, None)

        card_text = (
            "**Song:** Solar System\n"
            "**Artist:** Sub Focus\n"
            "**Genre:** Drum and Bass\n\n"
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        )

        channel = MagicMock()
        channel.id = channel_id
        channel.send = AsyncMock()
        typing_cm = MagicMock()
        typing_cm.__aenter__ = AsyncMock(return_value=None)
        typing_cm.__aexit__ = AsyncMock(return_value=None)
        channel.typing = MagicMock(return_value=typing_cm)

        interaction = MagicMock()
        interaction.guild_id = 12345
        interaction.channel = channel
        interaction.channel_id = channel_id
        interaction.user = SimpleNamespace(
            id=user_id,
            name="halil",
            display_name="Halil",
            mention=f"<@{user_id}>",
            voice=None,
        )
        msg_edit = AsyncMock()
        interaction.message = SimpleNamespace(id=msg_id, content=card_text, view=None, edit=msg_edit)
        interaction.response = SimpleNamespace(
            is_done=MagicMock(return_value=False),
            defer=AsyncMock(),
        )

        fake_rec = {
            "verified_url": "https://music.youtube.com/watch?v=abc123def45",
            "chosen_track": "Desire by Sub Focus",
            "vid": "abc123def45",
            "is_unrelated_fallback": False,
        }
        fake_meta = {
            "song": "Desire",
            "artist": "Sub Focus",
            "genre": "Drum and Bass",
            "album": "Portals",
        }
        fake_ollama_resp = SimpleNamespace(
            message=SimpleNamespace(content="Here is a similar drum and bass roller:")
        )

        disabled_before_rec = []

        async def _fake_resolve(*_args, **_kwargs):
            # Verify BOTH `⏭️ Next` and `👍 Like` buttons were disabled IMMEDIATELY before `resolve_reaction_recommendation` ran
            if msg_edit.call_args:
                edited_view = msg_edit.call_args.kwargs.get("view")
                disabled_before_rec.append(
                    {c.custom_id: c.disabled for c in edited_view.children}
                )
            return fake_rec

        with (
            patch.object(bot_main, "resolve_reaction_recommendation", AsyncMock(side_effect=_fake_resolve)),
            patch.object(bot_main, "get_music_metadata", return_value=fake_meta),
            patch.object(bot_main.ollama_client, "chat", AsyncMock(return_value=fake_ollama_resp)),
        ):
            await bot_main.handle_song_card_button_interaction(interaction, action="like")
            # Second click by same user on same message is deduplicated via handled_reactions
            await bot_main.handle_song_card_button_interaction(interaction, action="like")

        self.assertEqual(
            disabled_before_rec,
            [{"song_card:next": True, "song_card:like": True, "song_card:spacer": True, "song_card:autoplay": False}],
        )
        self.assertEqual(channel.send.await_count, 1)
        sent_text = channel.send.call_args.args[0]
        sent_view = channel.send.call_args.kwargs.get("view")
        self.assertIn("**Song:** Desire", sent_text)
        self.assertIn("https://music.youtube.com/watch?v=abc123def45", sent_text)
        self.assertIsInstance(sent_view, bot_main.SongCardView)
        self.assertIn((msg_id, user_id), bot_main.handled_reactions)
        self.assertEqual(len(bot_main.chat_history[channel_id]), 3)  # system + user 👍 + assistant card

    async def test_next_button_during_autoplay_uses_skip_command(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        import main as bot_main

        channel_id = 777102
        user_id = 999304
        bot_main.chat_history.pop(channel_id, None)

        channel = MagicMock()
        channel.id = channel_id
        channel.send = AsyncMock()
        typing_cm = MagicMock()
        typing_cm.__aenter__ = AsyncMock(return_value=None)
        typing_cm.__aexit__ = AsyncMock(return_value=None)
        channel.typing = MagicMock(return_value=typing_cm)

        interaction = MagicMock()
        interaction.guild_id = 12345
        interaction.channel = channel
        interaction.channel_id = channel_id
        interaction.user = SimpleNamespace(
            id=user_id,
            name="halil",
            display_name="Halil",
            mention=f"<@{user_id}>",
            voice=None,
        )
        interaction.message = SimpleNamespace(
            id=888203,
            content="**Song:** Solar System\n**Artist:** Sub Focus\nhttps://music.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        interaction.response = SimpleNamespace(
            is_done=MagicMock(return_value=False),
            defer=AsyncMock(),
        )

        autoplay_skip_card = (
            "📻 **Autoplay (dnb)** — Next track in `3:30`:\n\n"
            "**Song:** Wilkinson - Afterglow\n"
            "**Artist:** Wilkinson\n"
            "**Genre:** Drum and Bass\n\n"
            "https://music.youtube.com/watch?v=1234567890a"
        )

        with (
            patch.object(bot_main.JOB_ENGINE, "get_active_autoplay_job", return_value=SimpleNamespace(job_id="J-101", payload_text="dnb")),
            patch.object(bot_main, "dispatch_voice_command", AsyncMock(return_value=autoplay_skip_card)) as mock_skip,
            patch.object(bot_main, "run_agent_turn", AsyncMock()) as mock_turn,
        ):
            await bot_main.handle_song_card_button_interaction(interaction, action="next")

        mock_skip.assert_awaited_once()
        self.assertEqual(mock_skip.call_args.kwargs.get("text"), "!skip")
        mock_turn.assert_not_awaited()
        self.assertEqual(channel.send.await_count, 1)
        self.assertEqual(channel.send.call_args.args[0], autoplay_skip_card)
        self.assertIsInstance(channel.send.call_args.kwargs.get("view"), bot_main.SongCardView)

    async def test_next_button_outside_autoplay_runs_conversational_next_turn(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        import main as bot_main

        channel_id = 777103
        user_id = 999305
        bot_main.chat_history.pop(channel_id, None)
        clear_channel_played_songs(channel_id)

        channel = MagicMock()
        channel.id = channel_id
        channel.send = AsyncMock()
        typing_cm = MagicMock()
        typing_cm.__aenter__ = AsyncMock(return_value=None)
        typing_cm.__aexit__ = AsyncMock(return_value=None)
        channel.typing = MagicMock(return_value=typing_cm)

        card_text = (
            "**Song:** Solar System\n"
            "**Artist:** Sub Focus\n"
            "**Genre:** Drum and Bass\n\n"
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        interaction = MagicMock()
        interaction.guild_id = 12345
        interaction.channel = channel
        interaction.channel_id = channel_id
        interaction.user = SimpleNamespace(
            id=user_id,
            name="halil",
            display_name="Halil",
            mention=f"<@{user_id}>",
            voice=None,
        )
        interaction.message = SimpleNamespace(id=888204, content=card_text)
        interaction.response = SimpleNamespace(
            is_done=MagicMock(return_value=False),
            defer=AsyncMock(),
        )

        next_card_reply = (
            "📻 **Autoplay (`J-108` — similar to Breathing by Chase & Status)** — Next track in `3:45`:\n\n"
            "Here is the next track:\n\n"
            "**Song:** Timewarp\n"
            "**Artist:** Sub Focus\n"
            "**Genre:** Drum and Bass\n\n"
            "https://music.youtube.com/watch?v=z9876543210"
        )

        with (
            patch.object(bot_main.JOB_ENGINE, "get_active_autoplay_job", return_value=None),
            patch.object(bot_main, "run_agent_turn", AsyncMock(return_value=next_card_reply)) as mock_turn,
            patch.object(bot_main, "standardize_song_reply", side_effect=lambda r: r),
        ):
            await bot_main.handle_song_card_button_interaction(interaction, action="next")

        mock_turn.assert_awaited_once()
        passed_history = mock_turn.call_args.kwargs.get("turn_history")
        self.assertTrue(any("next" in str(m.get("content", "")).lower() for m in passed_history if m.get("role") == "user"))
        self.assertEqual(channel.send.await_count, 1)
        self.assertIn("**Song:** Timewarp", channel.send.call_args.args[0])
        self.assertNotIn("📻 **Autoplay", channel.send.call_args.args[0])
        self.assertIsInstance(channel.send.call_args.kwargs.get("view"), bot_main.SongCardView)

    async def test_mixed_next_like_and_random_song_request_chain_only_keeps_latest_next_enabled(self):
        """Verify across a multi-step chain (initial song -> Next button -> Like button -> random specific song
        request in chat -> liking an old card -> Next button) that EVERY previous song card always has its
        `⏭️ Next` button disabled while `👍 Like` stays enabled, and ONLY the most recent song card has `⏭️ Next` enabled."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        import main as bot_main

        channel_id = 777104
        user_id = 999306
        bot_user = SimpleNamespace(id=111000, name="Bot", display_name="Bot")
        bot_main.chat_history.pop(channel_id, None)
        bot_main.handled_reactions.clear()
        bot_main._last_song_card_messages.clear()
        bot_main._channel_song_card_messages.clear()
        bot_main._song_card_button_states.clear()
        clear_channel_played_songs(channel_id)

        sent_cards = []
        liked_card_indices = set()
        autoplay_active_state = {"job": None}

        async def _fake_send(text="", **kwargs):
            view = kwargs.get("view")
            msg_obj = SimpleNamespace(
                id=2000 + len(sent_cards) + 1,
                content=text,
                view=view,
                author=bot_user,
            )

            async def _edit_msg(**edit_kwargs):
                if "view" in edit_kwargs:
                    msg_obj.view = edit_kwargs["view"]
                return msg_obj

            msg_obj.edit = AsyncMock(side_effect=_edit_msg)
            if view is not None:
                sent_cards.append(msg_obj)
            return msg_obj

        channel = MagicMock()
        channel.id = channel_id
        channel.send = AsyncMock(side_effect=_fake_send)
        typing_cm = MagicMock()
        typing_cm.__aenter__ = AsyncMock(return_value=None)
        typing_cm.__aexit__ = AsyncMock(return_value=None)
        channel.typing = MagicMock(return_value=typing_cm)

        user_obj = SimpleNamespace(
            id=user_id,
            name="halil",
            display_name="Halil",
            mention=f"<@{user_id}>",
            voice=None,
        )

        def _button_states(msg_obj):
            return {c.custom_id: c.disabled for c in msg_obj.view.children}

        def _button_labels(msg_obj):
            return {c.custom_id: c.label for c in msg_obj.view.children}

        def _assert_only_last_card_next_enabled():
            self.assertGreaterEqual(len(sent_cards), 1)
            is_auto_on = autoplay_active_state["job"] is not None
            for idx, card_msg in enumerate(sent_cards):
                self.assertEqual(
                    [c.custom_id for c in card_msg.view.children],
                    ["song_card:next", "song_card:like", "song_card:spacer", "song_card:autoplay"],
                )
                states = _button_states(card_msg)
                self.assertTrue(states["song_card:spacer"])
                labels = _button_labels(card_msg)
                # `👍 Like` is disabled once pressed on that card, and enabled otherwise
                if idx in liked_card_indices:
                    self.assertTrue(states["song_card:like"], f"Card #{idx + 1} Like button must be disabled after press")
                else:
                    self.assertFalse(states["song_card:like"], f"Card #{idx + 1} Like button should be enabled")
                if idx < len(sent_cards) - 1:
                    self.assertTrue(states["song_card:next"], f"Previous Card #{idx + 1} Next button must be disabled")
                    if is_auto_on:
                        self.assertTrue(states["song_card:autoplay"], f"Previous Card #{idx + 1} Autoplay must be disabled when Autoplay is ON")
                    else:
                        self.assertFalse(states["song_card:autoplay"], f"Previous Card #{idx + 1} Autoplay must be enabled when Autoplay is OFF")
                        self.assertEqual(labels["song_card:autoplay"], "Autoplay")
                else:
                    self.assertFalse(states["song_card:next"], f"Latest Card #{idx + 1} Next button must be enabled")
                    self.assertFalse(states["song_card:autoplay"], f"Latest Card #{idx + 1} Autoplay button must be enabled")
                    self.assertEqual(
                        labels["song_card:autoplay"],
                        "Autoplay Off" if is_auto_on else "Autoplay",
                    )

        card1_text = (
            "**Song:** Solar System\n"
            "**Artist:** Sub Focus\n"
            "**Genre:** Drum and Bass\n\n"
            "https://music.youtube.com/watch?v=vid_card_001"
        )
        card2_text = (
            "**Song:** Timewarp\n"
            "**Artist:** Sub Focus\n"
            "**Genre:** Drum and Bass\n\n"
            "https://music.youtube.com/watch?v=vid_card_002"
        )
        card3_meta = {
            "song": "Desire",
            "artist": "Sub Focus",
            "genre": "Drum and Bass",
            "album": "Portals",
        }
        card4_text = (
            "**Song:** Strobe\n"
            "**Artist:** deadmau5\n"
            "**Genre:** Progressive House\n\n"
            "https://music.youtube.com/watch?v=vid_card_004"
        )
        card5_meta = {
            "song": "iren",
            "artist": "Sub Focus",
            "genre": "Drum and Bass",
            "album": "Torus",
        }
        card6_text = (
            "**Song:** I Remember\n"
            "**Artist:** deadmau5\n"
            "**Genre:** Progressive House\n\n"
            "https://music.youtube.com/watch?v=vid_card_006"
        )

        def _make_interaction(target_msg):
            inter = MagicMock()
            inter.guild_id = 12345
            inter.channel = channel
            inter.channel_id = channel_id
            inter.user = user_obj
            inter.message = target_msg
            inter.response = SimpleNamespace(
                is_done=MagicMock(return_value=False),
                defer=AsyncMock(),
            )
            return inter

        with (
            patch.object(bot_main, "client", SimpleNamespace(user=bot_user, get_channel=lambda _cid: channel, get_user=lambda _uid: user_obj)),
            patch.object(bot_main.JOB_ENGINE, "get_active_autoplay_job", side_effect=lambda _cid: autoplay_active_state["job"]),
            patch.object(bot_main, "standardize_song_reply", side_effect=lambda r: r),
            patch.object(
                bot_main.ollama_client,
                "chat",
                AsyncMock(return_value=SimpleNamespace(message=SimpleNamespace(content="Here is a similar track:"))),
            ),
        ):
            # Step 1: User asks in chat to play dnb -> Card #1 posted
            msg_prompt_1 = SimpleNamespace(
                author=user_obj,
                channel=channel,
                content=f"<@{bot_user.id}> play some dnb",
                mentions=[bot_user],
                reference=None,
                attachments=[],
                guild=None,
            )
            with patch.object(bot_main, "run_agent_turn", AsyncMock(return_value=card1_text)):
                await bot_main.on_message(msg_prompt_1)
            self.assertEqual(len(sent_cards), 1)
            _assert_only_last_card_next_enabled()

            # Step 2: User clicks `⏭️ Next` on Card #1 -> Card #2 posted, Card #1 Next disabled
            with patch.object(bot_main, "run_agent_turn", AsyncMock(return_value=card2_text)):
                await bot_main.handle_song_card_button_interaction(_make_interaction(sent_cards[0]), action="next")
            self.assertEqual(len(sent_cards), 2)
            _assert_only_last_card_next_enabled()

            # Step 3: User clicks `👍 Like` on Card #2 -> Card #2 Next & Like disabled IMMEDIATELY before recommendation resolves, then Card #3 posted
            fake_rec_3 = {
                "verified_url": "https://music.youtube.com/watch?v=vid_card_003",
                "chosen_track": "Desire by Sub Focus",
                "vid": "vid_card_003",
                "is_unrelated_fallback": False,
            }

            async def _resolve_rec_3(*_a, **_kw):
                self.assertEqual(
                    _button_states(sent_cards[1]),
                    {"song_card:next": True, "song_card:like": True, "song_card:spacer": True, "song_card:autoplay": False},
                )
                return fake_rec_3

            with (
                patch.object(bot_main, "resolve_reaction_recommendation", AsyncMock(side_effect=_resolve_rec_3)),
                patch.object(bot_main, "get_music_metadata", return_value=card3_meta),
            ):
                await bot_main.handle_song_card_button_interaction(_make_interaction(sent_cards[1]), action="like")
            liked_card_indices.add(1)
            self.assertEqual(len(sent_cards), 3)
            _assert_only_last_card_next_enabled()

            # Step 4: Someone randomly asks in chat for a specific song ("play Strobe by Deadmau5") mid-chain
            # -> Card #3 Next disabled IMMEDIATELY when the music prompt starts, then Card #4 posted
            msg_specific_song = SimpleNamespace(
                author=user_obj,
                channel=channel,
                content=f"<@{bot_user.id}> play Strobe by Deadmau5",
                mentions=[bot_user],
                reference=None,
                attachments=[],
                guild=None,
            )

            async def _turn_card_4(*_a, **_kw):
                self.assertTrue(_button_states(sent_cards[2])["song_card:next"])
                return card4_text

            with patch.object(bot_main, "run_agent_turn", AsyncMock(side_effect=_turn_card_4)):
                await bot_main.on_message(msg_specific_song)
            self.assertEqual(len(sent_cards), 4)
            _assert_only_last_card_next_enabled()

            # Step 5: Clicking `⏭️ Next` on an OLD card (Card #2) does NOT spawn a new turn and keeps only Card #4 Next enabled
            with patch.object(bot_main, "run_agent_turn", AsyncMock(return_value=card6_text)) as mock_stale_turn:
                await bot_main.handle_song_card_button_interaction(_make_interaction(sent_cards[1]), action="next")
            mock_stale_turn.assert_not_awaited()
            self.assertEqual(len(sent_cards), 4)
            _assert_only_last_card_next_enabled()

            # Step 6: Clicking `👍 Like` on an OLD card (Card #1) still works
            # -> immediately disables Card #1 Like AND Card #4 (active card) Next before recommendation resolves, then posts Card #5
            fake_rec_5 = {
                "verified_url": "https://music.youtube.com/watch?v=vid_card_005",
                "chosen_track": "Siren by Sub Focus",
                "vid": "vid_card_005",
                "is_unrelated_fallback": False,
            }

            async def _resolve_rec_5(*_a, **_kw):
                self.assertEqual(
                    _button_states(sent_cards[0]),
                    {"song_card:next": True, "song_card:like": True, "song_card:spacer": True, "song_card:autoplay": False},
                )
                self.assertTrue(_button_states(sent_cards[3])["song_card:next"])
                return fake_rec_5

            with (
                patch.object(bot_main, "resolve_reaction_recommendation", AsyncMock(side_effect=_resolve_rec_5)),
                patch.object(bot_main, "get_music_metadata", return_value=card5_meta),
            ):
                await bot_main.handle_song_card_button_interaction(_make_interaction(sent_cards[0]), action="like")
            liked_card_indices.add(0)
            self.assertEqual(len(sent_cards), 5)
            _assert_only_last_card_next_enabled()

            # Step 7: Clicking `⏭️ Next` on the newest card (Card #5) -> posts Card #6, disables Card #5 Next, enables Card #6 Next
            with patch.object(bot_main, "run_agent_turn", AsyncMock(return_value=card6_text)):
                await bot_main.handle_song_card_button_interaction(_make_interaction(sent_cards[4]), action="next")
            self.assertEqual(len(sent_cards), 6)
            _assert_only_last_card_next_enabled()

            # Step 8: User clicks `📻 Autoplay` on an OLD card (Card #4: Strobe by deadmau5)
            # -> immediately disables Autoplay & Next on ALL existing cards (Cards #1-#6), starts Autoplay, and posts Card #7 with `Autoplay Off`
            card7_autoplay_text = (
                "📻 **Autoplay (similar to Strobe by deadmau5)** — Next track in `3:45`:\n\n"
                "**Song:** Faxing Berlin\n"
                "**Artist:** deadmau5\n"
                "**Genre:** Progressive House\n\n"
                "https://music.youtube.com/watch?v=vid_card_007\n\n"
                "[SYSTEM INSTRUCTION: You MUST include the exact `📻 **Autoplay (...)` header and the full card above in your reply.]"
            )

            async def _fake_start_autoplay(**kw):
                # Before the new Autoplay track finishes resolving, all 6 existing cards have Autoplay & Next disabled
                for existing_c in sent_cards:
                    st = _button_states(existing_c)
                    self.assertTrue(st["song_card:next"])
                    self.assertTrue(st["song_card:autoplay"])
                autoplay_active_state["job"] = SimpleNamespace(
                    job_id="J-777",
                    payload_text="similar to Strobe by deadmau5",
                )
                self.assertIn("Strobe", kw.get("text", ""))
                return card7_autoplay_text

            with patch.object(bot_main, "dispatch_tracker_command", AsyncMock(side_effect=_fake_start_autoplay)):
                await bot_main.handle_song_card_button_interaction(_make_interaction(sent_cards[3]), action="autoplay")
            self.assertEqual(len(sent_cards), 7)
            self.assertNotIn("[SYSTEM INSTRUCTION", sent_cards[6].content)
            _assert_only_last_card_next_enabled()

            # Step 9: While Autoplay is ON, user clicks `⏭️ Next` on Card #7 -> Card #7 Next AND Autoplay Off are disabled, Card #8 posted with `Autoplay Off`
            card8_autoplay_text = (
                "📻 **Autoplay (similar to Strobe by deadmau5)** — Next track in `4:00`:\n\n"
                "**Song:** Arguru\n"
                "**Artist:** deadmau5\n"
                "**Genre:** Progressive House\n\n"
                "https://music.youtube.com/watch?v=vid_card_008"
            )
            with patch.object(bot_main, "dispatch_voice_command", AsyncMock(return_value=card8_autoplay_text)):
                await bot_main.handle_song_card_button_interaction(_make_interaction(sent_cards[6]), action="next")
            self.assertEqual(len(sent_cards), 8)
            _assert_only_last_card_next_enabled()

            # Step 10: User clicks `📻 Autoplay Off` on the latest card (Card #8)
            # -> stops Autoplay and re-enables `📻 Autoplay` on ALL 8 cards while keeping `⏭️ Next` enabled only on Card #8
            async def _fake_stop_autoplay(**kw):
                self.assertEqual(kw.get("text"), "!stopautoplay")
                autoplay_active_state["job"] = None
                return "🛑 Stopped: `J-777` (Music Autoplay)"

            with patch.object(bot_main, "dispatch_tracker_command", AsyncMock(side_effect=_fake_stop_autoplay)):
                await bot_main.handle_song_card_button_interaction(_make_interaction(sent_cards[7]), action="autoplay")
            self.assertEqual(len(sent_cards), 8)
            _assert_only_last_card_next_enabled()

    def test_enrich_music_tool_args_on_next_strips_hallucinated_similar_to_and_preserves_genre(self):
        """When the user plays a genre (e.g. 'play liquid drum and bass') and hits Next / says 'next',
        even if the LLM proposes `similar_to='<the song just skipped>'` (e.g. 'Pathways by Nu:Logic'),
        `enrich_music_tool_args` must strip `similar_to` and keep the active genre."""
        from unittest.mock import patch
        from src.context import set_active_channel_id
        from src.jobs.engine import JOB_ENGINE
        from src.skills.music.context import (
            clear_channel_played_songs,
            enrich_music_tool_args,
            record_channel_played_song,
        )

        cid = 888901
        set_active_channel_id(cid)
        clear_channel_played_songs(cid)
        record_channel_played_song(
            song="Pathways",
            artist="Nu:Logic",
            genre="Liquid Funk",
            channel_id=cid,
        )

        history = [
            {"role": "user", "content": "User jubkill says: play liquid drum and bass"},
            {
                "role": "assistant",
                "content": (
                    "Here’s a piece of liquid funk!\n\n"
                    "**Song:** Pathways\n"
                    "**Artist:** Nu:Logic\n"
                    "**Album:** Somewhere Between The Light\n"
                    "**Genre:** Liquid Funk\n\n"
                    "https://music.youtube.com/watch?v=M-x9ipZ-ed8"
                ),
            },
            {"role": "user", "content": "User jubkill says: next"},
        ]

        with patch.object(JOB_ENGINE, "get_active_autoplay_job", return_value=None):
            enriched = enrich_music_tool_args({"similar_to": "Pathways by Nu:Logic"}, history)

        self.assertEqual(enriched, {"genre": "liquid drum and bass"})
        self.assertNotIn("similar_to", enriched)

    def test_enrich_music_tool_args_on_next_after_like_keeps_original_liked_seed_not_skipped_song(self):
        """When the user liked 'The Trip by Logistics' (👍) and then hits Next on the resulting track
        ('Cowgirl by Underworld' or 'Downpipe by Mark Knight'), `enrich_music_tool_args` must keep
        the original liked seed ('The Trip by Logistics') rather than chaining `similar_to` off the skipped song."""
        from unittest.mock import patch
        from src.context import set_active_channel_id
        from src.jobs.engine import JOB_ENGINE
        from src.skills.music.context import (
            clear_channel_played_songs,
            enrich_music_tool_args,
            record_channel_played_song,
        )

        cid = 888902
        set_active_channel_id(cid)
        clear_channel_played_songs(cid)
        record_channel_played_song(
            song="Cowgirl",
            artist="Underworld",
            genre="Electronic",
            channel_id=cid,
        )

        history = [
            {
                "role": "user",
                "content": "jubkill reacted with 👍 to ''The Trip' by 'Logistics'' requesting a similar track.",
            },
            {
                "role": "assistant",
                "content": (
                    "**Song:** Cowgirl\n"
                    "**Artist:** Underworld\n\n"
                    "https://music.youtube.com/watch?v=qEYeT-f1Fdo"
                ),
            },
            {"role": "user", "content": "User jubkill says: next"},
        ]

        with patch.object(JOB_ENGINE, "get_active_autoplay_job", return_value=None):
            enriched = enrich_music_tool_args({"similar_to": "Cowgirl by Underworld"}, history)

        self.assertEqual(enriched, {"similar_to": "The Trip by Logistics"})

    def test_active_genre_persists_in_played_song_record_across_stay_with_and_like_and_next(self):
        """Verify that:
        1. active_genre is stored on PlayedSongRecord when skill_music_discovery(genre=...) runs,
           so hitting 'next' during a genre session stays on that genre.
        2. Clicking 👍 Like on a song anchors active_similar_to to that liked song on PlayedSongRecord,
           so subsequent 'next' presses stay on the liked song's YouTube Music Radio queue instead of
           chaining off skipped tracks."""
        from unittest.mock import patch
        from src.context import set_active_channel_id
        from src.jobs.engine import JOB_ENGINE
        from src.skills.music.context import (
            clear_channel_played_songs,
            enrich_music_tool_args,
            get_channel_last_played_song,
        )
        from src.skills.music.engine.formatting import (
            MUSIC_METADATA_CACHE,
            standardize_song_reply,
        )
        from src.skills.music.models import Track
        from src.skills.music import music as music_mod

        cid = 888903
        set_active_channel_id(cid)
        clear_channel_played_songs(cid)

        liquid_track = Track(
            title="Desperado",
            artist="Artificial Intelligence",
            genre="Liquid Drum & Bass",
            source="deezer",
        )
        liked_track = Track(
            title="By Your Side",
            artist="S.P.Y",
            genre="Liquid Drum & Bass",
            source="deezer",
        )

        with (
            patch.object(JOB_ENGINE, "get_active_autoplay_job", return_value=None),
            patch.object(music_mod.manager, "get_mood_tracks", return_value=[liquid_track]),
            patch.object(music_mod.manager, "get_similar_tracks", return_value=[liked_track]),
            patch.object(
                music_mod,
                "resolve_youtube_music_link",
                side_effect=[
                    [{"id": "vidLiquid01", "title": "Desperado", "artist": "Artificial Intelligence", "url": "https://music.youtube.com/watch?v=vidLiquid01"}],
                    [{"id": "vidLiquid02", "title": "By Your Side", "artist": "S.P.Y", "url": "https://music.youtube.com/watch?v=vidLiquid02"}],
                ],
            ),
        ):
            # Step 1: User says "please stay with LIQUID drum and bass" -> LLM calls skill_music_discovery(genre='liquid drum and bass')
            music_mod.skill_music_discovery(genre="liquid drum and bass")
            rec1 = get_channel_last_played_song(cid)
            self.assertIsNotNone(rec1)
            self.assertEqual(rec1.active_genre, "Liquid Drum & Bass")

            # Step 1b: Hitting ⏭️ Next during the genre session resolves to active_genre ('Liquid Drum & Bass')
            history_genre = [
                {"role": "user", "content": "User jubkill says: please stay with LIQUID drum and bass"},
                {"role": "assistant", "content": "**Song:** Desperado\n**Artist:** Artificial Intelligence\nhttps://music.youtube.com/watch?v=vidLiquid01"},
                {"role": "user", "content": "User jubkill says: next"},
            ]
            enriched_genre = enrich_music_tool_args({"similar_to": "Desperado by Artificial Intelligence"}, history_genre)
            self.assertEqual(enriched_genre, {"genre": "Liquid Drum & Bass"})

            # Step 2: User clicks 👍 Like on 'Desperado by Artificial Intelligence'
            music_mod.skill_music_discovery(similar_to="Desperado by Artificial Intelligence")
            rec2 = get_channel_last_played_song(cid)
            self.assertIsNotNone(rec2)
            # Active similarity anchor is now locked to the liked song ('Desperado by Artificial Intelligence')
            self.assertEqual(rec2.active_similar_to, "Desperado by Artificial Intelligence")
            self.assertIsNone(rec2.active_genre)

            # Step 3: User hits ⏭️ Next on the recommended track -> stays anchored to the liked song's Radio ('Desperado by Artificial Intelligence'), NOT the skipped track ('By Your Side by S.P.Y')!
            history = [
                {"role": "user", "content": "User jubkill says: please stay with LIQUID drum and bass"},
                {"role": "assistant", "content": "**Song:** Desperado\n**Artist:** Artificial Intelligence\nhttps://music.youtube.com/watch?v=vidLiquid01"},
                {"role": "user", "content": "jubkill reacted with 👍 to ''Desperado' by 'Artificial Intelligence'' requesting a similar track."},
                {"role": "assistant", "content": "**Song:** By Your Side\n**Artist:** S.P.Y\nhttps://music.youtube.com/watch?v=vidLiquid02"},
                {"role": "user", "content": "User jubkill says: next"},
            ]
            enriched = enrich_music_tool_args({"similar_to": "By Your Side by S.P.Y"}, history)
            self.assertEqual(enriched, {"similar_to": "Desperado by Artificial Intelligence"})

        # Step 4: Verify standardize_song_reply strips hallucinated 'Genre: Similar to ...' and restores real cached genre
        MUSIC_METADATA_CACHE["vidLiquid02"] = {
            "song": "By Your Side",
            "artist": "S.P.Y",
            "genre": "Liquid Drum & Bass",
            "album": "What The Future Holds",
        }
        with patch("src.skills.youtube.engine.client.verify_embeddable", return_value=True):
            cleaned_card = standardize_song_reply(
                "Here is a track for you!\n\n"
                "**Song:** By Your Side\n"
                "**Artist:** S.P.Y\n"
                "**Genre:** Similar to Desperado by Artificial Intelligence\n\n"
                "https://music.youtube.com/watch?v=vidLiquid02"
            )
        self.assertIn("**Genre:** Liquid Drum & Bass", cleaned_card)
        self.assertNotIn("Similar to Desperado", cleaned_card)


if __name__ == "__main__":
    unittest.main()
