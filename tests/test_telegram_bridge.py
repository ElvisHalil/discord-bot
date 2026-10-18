import os
import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils.telegram import (
    _SENT_SONG_CARDS,
    clean_for_telegram,
    from_tg_channel_id,
    handle_telegram_update,
    is_authorized_telegram_event,
    is_telegram_channel,
    is_telegram_enabled,
    parse_allowed_ids,
    send_telegram_message,
    start_telegram_bridge,
    to_tg_channel_id,
)


class TestTelegramBridge(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _SENT_SONG_CARDS.clear()

    def test_is_telegram_enabled_flag(self):
        with patch.dict(os.environ, {"ENABLE_TELEGRAM": "false"}, clear=False):
            self.assertFalse(is_telegram_enabled())
        with patch.dict(os.environ, {"ENABLE_TELEGRAM": "true"}, clear=False):
            self.assertTrue(is_telegram_enabled())
        with patch.dict(os.environ, {"ENABLE_TELEGRAM": "1"}, clear=False):
            self.assertTrue(is_telegram_enabled())

    def test_fail_closed_security_and_allowlist(self):
        # 1. Fail-closed when both env vars are empty
        with patch.dict(
            os.environ,
            {"TELEGRAM_OWNER_ID": "", "TELEGRAM_ALLOWED_IDS": ""},
            clear=False,
        ):
            self.assertEqual(parse_allowed_ids(), set())
            self.assertFalse(is_authorized_telegram_event(123456789, 123456789))

        # 2. Authorized by TELEGRAM_OWNER_ID
        with patch.dict(
            os.environ,
            {"TELEGRAM_OWNER_ID": "123456789", "TELEGRAM_ALLOWED_IDS": ""},
            clear=False,
        ):
            self.assertTrue(is_authorized_telegram_event(123456789, 123456789))
            self.assertFalse(is_authorized_telegram_event(999999999, 999999999))

        # 3. Authorized by TELEGRAM_ALLOWED_IDS (user ID or negative group chat ID)
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_OWNER_ID": "123456789",
                "TELEGRAM_ALLOWED_IDS": "555555555, -100987654321",
            },
            clear=False,
        ):
            # Friend DM
            self.assertTrue(is_authorized_telegram_event(555555555, 555555555))
            # Unlisted user inside an authorized group chat
            self.assertTrue(is_authorized_telegram_event(777777777, -100987654321))
            # Unlisted user in an unlisted chat
            self.assertFalse(is_authorized_telegram_event(777777777, 777777777))

    def test_channel_id_namespacing_roundtrip(self):
        # Positive DM chat_id
        dm_chat_id = 123456789
        internal_dm = to_tg_channel_id(dm_chat_id)
        self.assertTrue(is_telegram_channel(internal_dm))
        self.assertLess(internal_dm, 0)
        self.assertEqual(from_tg_channel_id(internal_dm), dm_chat_id)

        # Negative supergroup chat_id
        group_chat_id = -1001234567890
        internal_group = to_tg_channel_id(group_chat_id)
        self.assertTrue(is_telegram_channel(internal_group))
        self.assertLess(internal_group, 0)
        self.assertNotEqual(internal_dm, internal_group)
        self.assertEqual(from_tg_channel_id(internal_group), group_chat_id)

        # Positive Discord snowflake is not a Telegram channel
        self.assertFalse(is_telegram_channel(112233445566778899))

    def test_clean_for_telegram_unwraps_discord_angle_links(self):
        raw = (
            "📊 **Premier League**\n"
            "• Arsenal 2 - 1 Chelsea ([Match Report](<https://espn.com/report>))\n"
            "• Bookmakers: <https://www.Bovada.lv> | <https://www.BetOnline.ag>\n"
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        cleaned = clean_for_telegram(raw)
        self.assertNotIn("<https://", cleaned)
        self.assertIn("https://espn.com/report", cleaned)
        self.assertIn("https://www.Bovada.lv", cleaned)
        self.assertIn("https://www.youtube.com/watch?v=dQw4w9WgXcQ", cleaned)

    async def test_unauthorized_user_is_blocked_without_calling_agent(self):
        main_mod = SimpleNamespace(
            run_agent_turn=AsyncMock(return_value="should not run"),
            channel_locks=defaultdict( MagicMock ),
        )
        update = {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 999999, "is_bot": False, "first_name": "Stranger"},
                "chat": {"id": 999999, "type": "private"},
                "text": "hello bot",
            },
        }
        with patch.dict(
            os.environ,
            {"TELEGRAM_OWNER_ID": "123456789", "TELEGRAM_ALLOWED_IDS": ""},
            clear=False,
        ), patch("src.utils.telegram.send_telegram_message", new_callable=AsyncMock) as mock_send:
            await handle_telegram_update(
                update,
                main_mod=main_mod,
                bot_token="fake-token",
                bot_username="TestBot",
                bot_id=111,
            )
            mock_send.assert_not_awaited()
            main_mod.run_agent_turn.assert_not_awaited()

    async def test_authorized_commands_and_chat_turn(self):
        import asyncio

        song_card = (
            "**Song:** Harder, Better, Faster, Stronger\n"
            "**Artist:** Daft Punk\n"
            "**Genre:** Electronic\n"
            "https://music.youtube.com/watch?v=gAjR4_CbPpQ"
        )
        main_mod = SimpleNamespace(
            current_mode="research-gemma",
            chat_history={},
            channel_locks=defaultdict(asyncio.Lock),
            reset_channel_memory=MagicMock(),
            run_agent_turn=AsyncMock(return_value=song_card),
        )

        with patch.dict(
            os.environ,
            {"TELEGRAM_OWNER_ID": "123456789", "TELEGRAM_ALLOWED_IDS": ""},
            clear=False,
        ), patch("src.utils.telegram._tg_api_post") as mock_post, patch(
            "src.utils.telegram.standardize_song_reply",
            side_effect=lambda text, **kw: text,
        ):
            mock_post.return_value = {"ok": True, "result": {"message_id": 42}}

            # 1. /reset command
            reset_update = {
                "update_id": 2,
                "message": {
                    "message_id": 11,
                    "from": {"id": 123456789, "is_bot": False, "first_name": "Alex"},
                    "chat": {"id": 123456789, "type": "private"},
                    "text": "/reset",
                },
            }
            await handle_telegram_update(
                reset_update,
                main_mod=main_mod,
                bot_token="fake-token",
                bot_username="TestBot",
                bot_id=111,
            )
            main_mod.reset_channel_memory.assert_called_once_with(to_tg_channel_id(123456789))

            # 2. Normal DM message triggering a song card
            chat_update = {
                "update_id": 3,
                "message": {
                    "message_id": 12,
                    "from": {"id": 123456789, "is_bot": False, "first_name": "Alex"},
                    "chat": {"id": 123456789, "type": "private"},
                    "text": "play some daft punk",
                },
            }
            await handle_telegram_update(
                chat_update,
                main_mod=main_mod,
                bot_token="fake-token",
                bot_username="TestBot",
                bot_id=111,
            )
            main_mod.run_agent_turn.assert_awaited_once()
            # Verify song card was cached for 👍 reaction support
            self.assertIn((123456789, 42), _SENT_SONG_CARDS)
            self.assertIn("Harder, Better, Faster, Stronger", _SENT_SONG_CARDS[(123456789, 42)])

            # 3. 👍 reaction on that song card triggers similar song recommendation
            reaction_update = {
                "update_id": 4,
                "message_reaction": {
                    "chat": {"id": 123456789, "type": "private"},
                    "message_id": 42,
                    "user": {"id": 123456789, "is_bot": False, "first_name": "Alex"},
                    "new_reaction": [{"type": "emoji", "emoji": "👍"}],
                },
            }
            with patch(
                "src.utils.telegram.tools.skill_music_discovery",
                return_value=(
                    "=== VERIFIED YOUTUBE MUSIC RESULT ===\n"
                    "Song: One More Time\n"
                    "Artist: Daft Punk\n"
                    "Genre: Electronic\n"
                    "Link: https://music.youtube.com/watch?v=FGBhQbmPwH8\n"
                ),
            ) as mock_music_discovery:
                await handle_telegram_update(
                    reaction_update,
                    main_mod=main_mod,
                    bot_token="fake-token",
                    bot_username="TestBot",
                    bot_id=111,
                )
                mock_music_discovery.assert_called_once_with(
                    similar_to="Harder, Better, Faster, Stronger by Daft Punk"
                )

    async def test_mode_switch_broadcasts_globally_and_no_cross_chat_leakage(self):
        from src.skills.music.context import (
            clear_channel_played_songs,
            clear_recently_played_tracks,
            get_channel_last_played_song,
            is_recently_played,
            record_channel_played_song,
            record_played_track,
        )
        from src.skills.sports.tracker import (
            clear_channel_last_match_query,
            get_channel_last_match_query,
            record_channel_last_match_query,
        )

        discord_cid = 987654321
        tg_cid = to_tg_channel_id(123456789)

        # 1. Verify per-channel music & sports state never leaks between Discord and Telegram
        clear_channel_played_songs()
        clear_recently_played_tracks()
        clear_channel_last_match_query()

        record_channel_played_song("Strobe", "Deadmau5", "Progressive House", channel_id=discord_cid)
        record_played_track("Strobe", "Deadmau5", channel_id=discord_cid)
        record_channel_last_match_query("Washington Capitals vs Montreal Canadiens", "nhl", channel_id=discord_cid)

        self.assertIsNotNone(get_channel_last_played_song(discord_cid))
        self.assertIsNone(get_channel_last_played_song(tg_cid))
        self.assertTrue(is_recently_played("Strobe", "Deadmau5", channel_id=discord_cid))
        self.assertFalse(is_recently_played("Strobe", "Deadmau5", channel_id=tg_cid))
        self.assertEqual(get_channel_last_match_query(tg_cid), ("", None))

        # 2. Verify /mode switch in Telegram broadcasts to active Discord channel
        mock_discord_channel = SimpleNamespace(send=AsyncMock())
        mock_client = SimpleNamespace(
            get_channel=MagicMock(return_value=mock_discord_channel),
            fetch_channel=AsyncMock(return_value=mock_discord_channel),
            guilds=[],
        )
        main_mod = SimpleNamespace(
            current_mode="research-gemma",
            chat_history={discord_cid: [{"role": "user", "content": "hi"}]},
            active_channels={discord_cid},
            channel_locks=defaultdict(MagicMock),
            clear_tool_caches=MagicMock(),
            ollama_client=SimpleNamespace(generate=AsyncMock()),
            client=mock_client,
        )

        mode_update = {
            "update_id": 10,
            "message": {
                "message_id": 50,
                "from": {"id": 123456789, "is_bot": False, "first_name": "Alex"},
                "chat": {"id": 123456789, "type": "private"},
                "text": "/mode chat",
            },
        }
        with patch.dict(
            os.environ,
            {"TELEGRAM_OWNER_ID": "123456789", "TELEGRAM_ALLOWED_IDS": ""},
            clear=False,
        ), patch("src.utils.telegram._tg_api_post") as mock_post:
            mock_post.return_value = {"ok": True, "result": {"message_id": 51}}
            await handle_telegram_update(
                mode_update,
                main_mod=main_mod,
                bot_token="fake-token",
                bot_username="TestBot",
                bot_id=111,
            )

        self.assertEqual(main_mod.current_mode, "chat")
        mock_discord_channel.send.assert_awaited_once()
        sent_discord_msg = mock_discord_channel.send.call_args[0][0]
        self.assertIn("Switched to **CHAT** mode", sent_discord_msg)

    async def test_start_telegram_bridge_disabled_or_missing_token(self):
        main_mod = SimpleNamespace()
        # Disabled -> returns immediately without calling Telegram API
        with patch.dict(os.environ, {"ENABLE_TELEGRAM": "false"}, clear=False), patch(
            "src.utils.telegram._tg_api_post"
        ) as mock_post:
            await start_telegram_bridge(main_mod)
            mock_post.assert_not_called()

        # Enabled but token missing -> logs warning and returns without calling API
        with patch.dict(
            os.environ,
            {"ENABLE_TELEGRAM": "true", "TELEGRAM_BOT_TOKEN": ""},
            clear=False,
        ), patch("src.utils.telegram._tg_api_post") as mock_post:
            await start_telegram_bridge(main_mod)
            mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
