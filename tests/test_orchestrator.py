import sys
import unittest
from typing import Optional
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ollama._utils import convert_function_to_tool

from src.config import MODES
from src.orchestrator import (
    normalize_tool_args,
    trim_history,
    apply_auto_ping,
    join_orphan_bullets,
)
from src.skills.music.utils import standardize_song_reply
from src.utils import suppress_discord_embeds


def search_web(query: str, news: Optional[bool] = None, timelimit: Optional[str] = None):
    """Stand-in with the real search_web signature (no network)."""


def skill_youtube_searcher(query: str, max_results: Optional[int] = 1, is_music: Optional[bool] = False):
    """Stand-in with the real skill_youtube_searcher signature (no network)."""


def skill_sports_info(sport: Optional[str] = None, query: Optional[str] = None):
    """Stand-in with the real skill_sports_info signature (no network)."""


class TestNormalizeToolArgs(unittest.TestCase):
    def test_alias_maps_to_canonical(self):
        self.assertEqual(normalize_tool_args("search_web", search_web, {"q": "gpu prices"}), {"query": "gpu prices"})

    def test_canonical_wins_over_alias(self):
        args = normalize_tool_args("search_web", search_web, {"query": "real", "q": "alias"})
        self.assertEqual(args, {"query": "real"})

    def test_unknown_and_null_args_dropped(self):
        args = normalize_tool_args("search_web", search_web, {"query": "x", "news": None, "bogus": 1})
        self.assertEqual(args, {"query": "x"})

    def test_string_bool_and_int_coercion(self):
        args = normalize_tool_args(
            "skill_youtube_searcher", skill_youtube_searcher,
            {"query": "x", "max_results": "3", "is_music": "false"},
        )
        self.assertEqual(args, {"query": "x", "max_results": 3, "is_music": False})
        args = normalize_tool_args("search_web", search_web, {"query": "x", "news": "True"})
        self.assertIs(args["news"], True)

    def test_sports_info_aliases(self):
        args = normalize_tool_args(
            "skill_sports_info", skill_sports_info,
            {"league": "soccer", "outcomes": "previous matches and outcomes"}
        )
        self.assertEqual(args, {"sport": "soccer", "query": "previous matches and outcomes"})

    def test_non_dict_args(self):
        self.assertEqual(normalize_tool_args("search_web", search_web, "oops"), {})


class TestToolSchemas(unittest.TestCase):
    """Ollama marks every non-Optional parameter as required and exposes **kwargs as a
    required 'kwargs' parameter. Guard against both regressions."""

    TRULY_REQUIRED = {"query", "url", "city", "sport", "expression", "fact"}

    def test_schemas_for_all_modes(self):
        for mode, cfg in MODES.items():
            for name, func in cfg["tools"].items():
                with self.subTest(mode=mode, tool=name):
                    params = convert_function_to_tool(func).function.parameters
                    props = params.properties or {}
                    self.assertNotIn("kwargs", props)
                    self.assertNotIn("args", props)
                    required = set(params.required or [])
                    self.assertTrue(required <= self.TRULY_REQUIRED, f"unexpected required params: {required}")
                    for pname, prop in props.items():
                        self.assertTrue(getattr(prop, "description", None), f"{name}.{pname} has no description")


class TestTrimHistory(unittest.TestCase):
    def _history(self, turns, tool_len=2000):
        h = [{"role": "system", "content": "sys"}]
        for i in range(turns):
            h.append({"role": "user", "content": f"q{i}"})
            h.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "search_web"}}]})
            h.append({"role": "tool", "name": "search_web", "content": "x" * tool_len})
            h.append({"role": "assistant", "content": f"a{i}"})
        return h

    def test_keeps_system_and_starts_on_user(self):
        trimmed = trim_history(self._history(5), limit=6)
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[1]["role"], "user")
        self.assertLessEqual(len(trimmed) - 1, 6)
        self.assertEqual(trimmed[-1]["content"], "a4")

    def test_old_tool_output_truncated_latest_kept(self):
        trimmed = trim_history(self._history(2), limit=40, old_tool_chars=100)
        tools = [m for m in trimmed if m["role"] == "tool"]
        self.assertIn("truncated", tools[0]["content"])
        self.assertLess(len(tools[0]["content"]), 200)
        self.assertEqual(len(tools[1]["content"]), 2000)

    def test_oversized_current_turn_not_orphaned(self):
        h = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
        h += [{"role": "tool", "content": "t"} for _ in range(10)]
        trimmed = trim_history(h, limit=4)
        self.assertEqual(trimmed[1]["role"], "user")

    def test_does_not_mutate_input(self):
        h = self._history(2)
        before = [dict(m) for m in h]
        trim_history(h, limit=2, old_tool_chars=10)
        self.assertEqual(h, before)


class TestAutoPing(unittest.TestCase):
    def test_pings_names(self):
        self.assertEqual(apply_auto_ping("yo alex, nice", {"alex": 42}), "yo <@42>, nice")
        self.assertEqual(apply_auto_ping("@Alex lol", {"alex": 42}), "<@42> lol")

    def test_ignores_short_names_and_substrings(self):
        self.assertEqual(apply_auto_ping("it is what it is", {"it": 1}), "it is what it is")
        self.assertEqual(apply_auto_ping("samsung phone", {"sam": 1}), "samsung phone")

    def test_does_not_touch_urls_mentions_or_code(self):
        reply = "see https://alex.example.com/page and <@99> and `alex`"
        self.assertEqual(apply_auto_ping(reply, {"alex": 42}), reply)


class TestOrphanBullets(unittest.TestCase):
    def test_joins_marker_with_next_line(self):
        self.assertEqual(join_orphan_bullets("-\nHeadline one\n*\nHeadline two"), "- Headline one\n* Headline two")

    def test_leaves_normal_bullets(self):
        text = "- one\n- two"
        self.assertEqual(join_orphan_bullets(text), text)


class TestReplyPostProcessing(unittest.TestCase):
    @patch("src.skills.music.utils.get_music_metadata")
    def test_news_reply_not_treated_as_song_card(self, mock_meta):
        reply = (
            "**Canada**\n"
            "- **Fast Track: Ottawa approves new rail line** - [CBC](<https://www.cbc.ca/news/1>)\n"
            "- **Budget update** - [CTV](<https://www.ctvnews.ca/2>)"
        )
        self.assertEqual(standardize_song_reply(reply), reply)
        mock_meta.assert_not_called()

    @patch("src.skills.youtube.client.verify_embeddable", return_value=True)
    @patch("src.skills.music.utils.get_music_metadata")
    def test_compound_prompt_and_song_reply_preserved(self, mock_meta, mock_embed):
        mock_meta.return_value = {
            "song": "Strobe",
            "artist": "deadmau5",
            "album": "For Lack of a Better Name",
            "genre": "Electronic",
        }
        # Case 1: Answer before song card (with markdown web links and multiple paragraphs) + text after song card
        reply = (
            "Here are today's NHL games:\n"
            "- **Boston Bruins @ Toronto Maple Leafs** (7:00 PM EDT) - [Preview](<https://www.nhl.com/game/1>)\n\n"
            "And here is a track for you:\n"
            "**Song:** Strobe\n"
            "**Artist:** deadmau5\n"
            "https://music.youtube.com/watch?v=tKi9Z-f6qX4\n\n"
            "Enjoy the games tonight!"
        )
        out = standardize_song_reply(reply)
        self.assertIn("Here are today's NHL games:", out)
        self.assertIn("[Preview](<https://www.nhl.com/game/1>)", out)
        self.assertIn("Enjoy the games tonight!", out)
        self.assertIn("**Song:** Strobe", out)
        self.assertIn("**Artist:** deadmau5", out)
        self.assertTrue(out.endswith("https://music.youtube.com/watch?v=tKi9Z-f6qX4"))

    def test_wikipedia_parentheses_preserved(self):
        out = suppress_discord_embeds("See https://en.wikipedia.org/wiki/Python_(programming_language).")
        self.assertIn("<https://en.wikipedia.org/wiki/Python_(programming_language)>", out)
        self.assertTrue(out.endswith(">."))

    def test_youtube_left_bare(self):
        out = suppress_discord_embeds("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(out, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_normal_sentence_mentioning_tool_kept(self):
        text = "The bot calls search_web first."
        self.assertEqual(suppress_discord_embeds(text), text)

    def test_masked_link_missing_closing_bracket_repaired(self):
        out = suppress_discord_embeds("Story. [Source](<https://www.example.com/world-news/)).")
        self.assertIn("[Source](<https://www.example.com/world-news/>)", out)

    def test_news_filler_stripped(self):
        import re
        from src.tools.web import _NEWS_FILLER

        def clean(q):
            return re.sub(r"\s{2,}", " ", _NEWS_FILLER.sub(" ", q)).strip(" ,.-")

        self.assertEqual(clean("Canada top headlines"), "Canada")
        self.assertEqual(clean("latest news headlines USA today"), "USA")
        self.assertEqual(clean("top gun news"), "top gun")


class TestSanitizeAgentReply(unittest.TestCase):
    def test_strips_ai_meta_refusal_headers(self):
        from src.orchestrator import sanitize_agent_reply
        text = (
            "No, I Don’t Have Direct Website Access—But I Can Help!\n"
            "While I can’t browse the web in real time or access private databases, I can:\n"
            "- Falcons are the favorites to win against NIP."
        )
        cleaned = sanitize_agent_reply(text)
        self.assertNotIn("No, I Don’t Have Direct Website Access", cleaned)
        self.assertNotIn("While I can’t browse the web in real time", cleaned)
        self.assertIn("- Falcons are the favorites to win against NIP.", cleaned)

    def test_rewrites_tool_name_leaks(self):
        from src.orchestrator import sanitize_agent_reply
        text = "Use tools like search_web or read_website to fetch details."
        cleaned = sanitize_agent_reply(text)
        self.assertNotIn("search_web", cleaned)
        self.assertNotIn("read_website", cleaned)
        self.assertIn("search and web research", cleaned)

    def test_sanitizes_hallucinated_bet365_links(self):
        from src.orchestrator import sanitize_agent_reply
        text = "Check odds at <https://www.bet365.com/esports/counter-strike/betting-odds?event=Thunderpick>."
        cleaned = sanitize_agent_reply(text)
        self.assertNotIn("event=Thunderpick", cleaned)
        self.assertIn("[Bet365](<https://www.bet365.com>)", cleaned)

    def test_strips_simulated_data_and_cutoff_excuses(self):
        from src.orchestrator import sanitize_agent_reply
        text = (
            "⚠️ Clarification: Data Limitations & Simulated Standings\n"
            "My knowledge cutoff is 2024, so all \"live\" sports data (including F1) is simulated for context-based storytelling.\n"
            "The standings you see are not real-world updates but crafted examples to match your query’s structure.\n"
            "For genuine 2026 F1 results, I’d need live web access, which I cannot provide.\n"
            "Here are the verified standings from the race weekend:\n"
            "- Kimi Antonelli - 340 pts"
        )
        cleaned = sanitize_agent_reply(text)
        self.assertNotIn("Data Limitations & Simulated Standings", cleaned)
        self.assertNotIn("My knowledge cutoff is 2024", cleaned)
        self.assertNotIn("standings you see are not real-world", cleaned)
        self.assertIn("- Kimi Antonelli - 340 pts", cleaned)


class TestExtractReplyContext(unittest.TestCase):
    def test_extract_song_card_reply(self):
        from src.orchestrator import extract_reply_context
        song_card = (
            "<@123456> User Alex reacted with 👍 to 'Strobe by deadmau5'. Check out this similar song:\n\n"
            "**Song:** Apex\n"
            "**Artist:** Bicep\n"
            "**Genre:** Techno\n\n"
            "https://music.youtube.com/watch?v=12345678901"
        )
        ctx = extract_reply_context(song_card)
        self.assertEqual(ctx, " [Replying to song card: 'Apex' by 'Bicep']")

    def test_extract_generic_snippet(self):
        from src.orchestrator import extract_reply_context
        msg = "What is the best GPU to buy in 2026?"
        ctx = extract_reply_context(msg)
        self.assertEqual(ctx, ' [Replying to: "What is the best GPU to buy in 2026?"]')

    def test_extract_empty(self):
        from src.orchestrator import extract_reply_context
        self.assertEqual(extract_reply_context(""), "")
        self.assertEqual(extract_reply_context(None), "")


class TestSportsFollowupAndMissedToolEnforcement(unittest.TestCase):
    def test_multi_turn_followup_enrichment_scans_backwards(self):
        from src.orchestrator import enrich_sports_tool_args

        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: whats status of real madrid game"},
            {"role": "assistant", "content": "AS Roma 2 - 1 Real Madrid (55')"},
            {"role": "user", "content": "User Alex says: track this game"},
            {"role": "assistant", "content": "Tracking active"},
            {"role": "user", "content": "User Alex says: who scored last goal what time?"},
            {"role": "assistant", "content": "Malen at 51'"},
            {"role": "user", "content": "User Alex says: hows the game going"},
        ]
        enriched = enrich_sports_tool_args({"query": "status"}, history)
        self.assertIn("real madrid", enriched["query"].lower())

    def test_detect_missed_sports_tool_call_on_track_and_status(self):
        from src.orchestrator import detect_missed_sports_tool_call

        history_track = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: whats status of real madrid game"},
            {"role": "tool", "name": "skill_sports_info", "content": "AS Roma 2 - 1 Real Madrid (55')"},
            {"role": "assistant", "content": "AS Roma 2 - 1 Real Madrid (55')"},
            {"role": "user", "content": "User Alex says: track this game"},
        ]
        missed_track = detect_missed_sports_tool_call(history_track, active_channel_id=101)
        self.assertIsNotNone(missed_track)
        self.assertEqual(missed_track[0], "skill_track_live_game")
        self.assertEqual(missed_track[1]["action"], "start")
        self.assertIn("real madrid", missed_track[1]["query"].lower())

        history_status = history_track + [
            {"role": "assistant", "content": "Tracking active"},
            {"role": "user", "content": "User Alex says: hows the game going"},
        ]
        missed_status = detect_missed_sports_tool_call(history_status, active_channel_id=101)
        self.assertIsNotNone(missed_status)
        self.assertEqual(missed_status[0], "skill_sports_info")
        self.assertIn("real madrid", missed_status[1]["query"].lower())

        # Music reaction / track queries must never trigger sports tracker
        history_music = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Alex reacted with 👍 to 'Song' requesting a similar track."},
        ]
        self.assertIsNone(detect_missed_sports_tool_call(history_music, active_channel_id=101))

    def test_trim_history_expires_earlier_sports_snapshots(self):
        h = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "whats status of real madrid game"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "skill_sports_info"}}]},
            {"role": "tool", "name": "skill_sports_info", "content": "AS Roma 2 - 1 Real Madrid (55')"},
            {"role": "assistant", "content": "AS Roma 2 - 1 Real Madrid (55')"},
            {"role": "user", "content": "hows the game going"},
        ]
        trimmed = trim_history(h, limit=20)
        old_tool = [m for m in trimmed if m.get("role") == "tool"][0]
        self.assertIn("EXPIRED SPORTS SNAPSHOT", old_tool["content"])
        self.assertNotIn("55'", old_tool["content"])


class TestCentralizedTimeAndTimezones(unittest.TestCase):
    def test_bot_timezone_env_override_and_fallback(self):
        from datetime import datetime, timezone
        from src.orchestrator import get_local_now

        # Fixed UTC moment: 2026-10-16 01:30 UTC
        ref_utc = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)

        # 1. Explicit IANA override: America/Edmonton (MDT, UTC-6 in October) -> Thu Oct 15, 19:30 MDT
        with patch.dict("os.environ", {"BOT_TIMEZONE": "America/Edmonton"}, clear=False):
            dt_mdt = get_local_now(now=ref_utc)
            self.assertEqual((dt_mdt.year, dt_mdt.month, dt_mdt.day, dt_mdt.hour, dt_mdt.minute), (2026, 10, 15, 19, 30))
            self.assertEqual(dt_mdt.tzname(), "MDT")

        # 2. Explicit European IANA override: Europe/Berlin (CEST, UTC+2 in mid-October) -> Fri Oct 16, 03:30 CEST
        with patch.dict("os.environ", {"BOT_TIMEZONE": "Europe/Berlin"}, clear=False):
            dt_eu = get_local_now(now=ref_utc)
            self.assertEqual((dt_eu.year, dt_eu.month, dt_eu.day, dt_eu.hour, dt_eu.minute), (2026, 10, 16, 3, 30))
            self.assertEqual(dt_eu.tzname(), "CEST")

        # 3. Blank or invalid BOT_TIMEZONE falls back cleanly to system local time without raising
        with patch.dict("os.environ", {"BOT_TIMEZONE": ""}, clear=False):
            dt_sys = get_local_now(now=ref_utc)
            self.assertIsNotNone(dt_sys.tzinfo)
        with patch.dict("os.environ", {"BOT_TIMEZONE": "Invalid/Not_A_Zone"}, clear=False):
            dt_fallback = get_local_now(now=ref_utc)
            self.assertIsNotNone(dt_fallback.tzinfo)

    def test_build_reference_clock_includes_local_us_europe_and_utc(self):
        from datetime import datetime, timezone
        from src.orchestrator import build_reference_clock

        ref_utc = datetime(2026, 10, 16, 1, 45, tzinfo=timezone.utc)
        clock = build_reference_clock(now=ref_utc, tz_override="MDT")
        self.assertIn("Thu, Oct 15, 2026 at 7:45 PM MDT (Local)", clock)
        self.assertIn("9:45 PM EDT", clock)
        self.assertIn("6:45 PM PDT", clock)
        self.assertIn("Fri, Oct 16, 2026 at 03:45 CEST (Europe)", clock)
        self.assertIn("Fri, Oct 16, 2026 at 01:45 UTC", clock)

    def test_enrich_tool_output_leaves_non_time_outputs_untouched(self):
        from src.orchestrator import enrich_tool_output_with_time_context

        music_card = (
            "=== VERIFIED YOUTUBE MUSIC RESULT ===\n"
            "**Song:** Live Forever\n"
            "**Artist:** Oasis\n"
            "https://www.youtube.com/watch?v=123456"
        )
        self.assertEqual(enrich_tool_output_with_time_context(music_card), music_card)
        self.assertEqual(enrich_tool_output_with_time_context("200"), "200")

    def test_enrich_tool_output_annotates_mixed_timezones_and_utc_midnight_rollover(self):
        from datetime import datetime, timezone
        from src.orchestrator import enrich_tool_output_with_time_context

        # Current time: Thu Oct 15, 2026 at 7:30 PM MDT (= Fri Oct 16, 01:30 UTC = Fri Oct 16, 03:30 CEST)
        ref_utc = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
        raw_schedule = (
            "=== NBA SCHEDULE ===\n"
            "  • Lakers 0 @ Warriors 0 [Thu, Oct 15 - 10:00 PM EDT]\n"
            "  • Celtics 0 @ Knicks 0 [Fri, Oct 16, 2026 at 01:00 UTC]\n"
            "📅 --- SATURDAY, OCTOBER 17, 2026 (1 Match) ---\n"
            "  • Arsenal at Chelsea (Kickoff: 16:30 UTC) [Scheduled]"
        )
        enriched = enrich_tool_output_with_time_context(raw_schedule, now=ref_utc, tz_override="MDT")
        self.assertTrue(enriched.startswith("[🕒 Current Time:"))
        # 10:00 PM EDT on Oct 15 = Today 8:00 PM MDT / Fri 04:00 CEST (in 30m)
        self.assertIn("Thu, Oct 15 - 10:00 PM EDT (Today 8:00 PM MDT / Fri 04:00 CEST, in 30m)", enriched)
        # Fri, Oct 16 at 01:00 UTC = Today 7:00 PM MDT / Fri 03:00 CEST (30m ago) -> no false "tomorrow" confusion!
        self.assertIn("Fri, Oct 16, 2026 at 01:00 UTC (Today 7:00 PM MDT / Fri 03:00 CEST, 30m ago)", enriched)
        # Kickoff: 16:30 UTC under Saturday Oct 17 header -> Sat Oct 17 10:30 AM MDT / 18:30 CEST (in 1d 15h)
        self.assertIn("16:30 UTC (Sat Oct 17 10:30 AM MDT / 18:30 CEST, in 1d 15h)", enriched)


class TestWorkerConcurrencyAndLockFreeTurns(unittest.IsolatedAsyncioTestCase):
    async def test_contextvar_propagation_across_nested_workers(self):
        import asyncio
        import time
        from src.skills.sports.live_tracker import get_active_channel_id, set_active_channel_id
        from src.tools.system import _get_active_memory_file, set_memory_file
        from src.utils.workers import map_in_workers, run_in_worker, submit_to_worker

        async def simulate_channel_task(cid: int, mem_path: str, delay: float):
            set_active_channel_id(cid)
            set_memory_file(mem_path)

            def blocking_skill():
                time.sleep(delay)
                outer_cid = get_active_channel_id()
                outer_mem = _get_active_memory_file()
                # Fan out sub-workers from inside the worker thread
                sub_cids = map_in_workers(lambda _: get_active_channel_id(), [1, 2, 3])
                fut_mem = submit_to_worker(_get_active_memory_file)
                return outer_cid, outer_mem, sub_cids, fut_mem.result()

            return await run_in_worker(blocking_skill)

        res_a, res_b = await asyncio.gather(
            simulate_channel_task(1001, "data/mem_a.json", 0.06),
            simulate_channel_task(2002, "data/mem_b.json", 0.02),
        )
        self.assertEqual(res_a, (1001, "data/mem_a.json", [1001, 1001, 1001], "data/mem_a.json"))
        self.assertEqual(res_b, (2002, "data/mem_b.json", [2002, 2002, 2002], "data/mem_b.json"))

    async def test_concurrent_turns_fast_turn_finishes_while_slow_worker_fetches(self):
        import asyncio
        import time
        from collections import defaultdict
        from types import SimpleNamespace
        from unittest.mock import patch
        from src.utils.telegram import handle_telegram_update, to_tg_channel_id
        from src.utils.workers import run_in_worker

        completion_order = []
        cid = to_tg_channel_id(555000)

        def slow_sports_scrape():
            time.sleep(0.18)
            return "=== NHL SCHEDULE ===\n• Oilers vs Flames"

        async def fake_run_agent_turn(channel_id, mode_config, turn_history=None):
            history = turn_history if turn_history is not None else main_mod.chat_history[channel_id]
            last_user = history[-1]["content"]
            if "hockey games" in last_user:
                # Simulate LLM step 1 -> slow background worker tool -> LLM step 2
                history.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "skill_sports_info", "arguments": {"sport": "nhl"}}}],
                })
                tool_out = await run_in_worker(slow_sports_scrape)
                history.append({
                    "role": "tool",
                    "name": "skill_sports_info",
                    "content": tool_out,
                })
                reply = "Here are today's NHL games: Oilers vs Flames."
                history.append({"role": "assistant", "content": reply})
                completion_order.append("turn_1_slow")
                return reply
            else:
                # Fast prompt that does not need to wait for Turn 1's worker
                await asyncio.sleep(0.01)
                reply = "25 * 14 is 350."
                history.append({"role": "assistant", "content": reply})
                completion_order.append("turn_2_fast")
                return reply

        main_mod = SimpleNamespace(
            current_mode="research-gemma",
            chat_history={},
            channel_locks=defaultdict(asyncio.Lock),
            run_agent_turn=fake_run_agent_turn,
        )

        update_slow = {
            "update_id": 100,
            "message": {
                "message_id": 1,
                "from": {"id": 123456789, "is_bot": False, "first_name": "Alex"},
                "chat": {"id": 555000, "type": "private"},
                "text": "what are the hockey games today?",
            },
        }
        update_fast = {
            "update_id": 101,
            "message": {
                "message_id": 2,
                "from": {"id": 123456789, "is_bot": False, "first_name": "Alex"},
                "chat": {"id": 555000, "type": "private"},
                "text": "what is 25 * 14?",
            },
        }

        with patch.dict(
            "os.environ",
            {"TELEGRAM_OWNER_ID": "123456789", "TELEGRAM_ALLOWED_IDS": "555000"},
            clear=False,
        ), patch("src.utils.telegram._tg_api_post", return_value={"ok": True, "result": {"message_id": 99}}):
            t1 = asyncio.create_task(handle_telegram_update(update_slow, main_mod, bot_token="tok"))
            await asyncio.sleep(0.02)  # Let Turn 1 enter its background worker
            t2 = asyncio.create_task(handle_telegram_update(update_fast, main_mod, bot_token="tok"))
            await asyncio.gather(t1, t2)

        # 1. Fast Turn 2 finished before Slow Turn 1!
        self.assertEqual(completion_order, ["turn_2_fast", "turn_1_slow"])

        # 2. Both turns' complete message blocks are committed atomically without interleaving
        final_hist = main_mod.chat_history[cid]
        roles = [m["role"] for m in final_hist]
        # Expected: [system, user_2, assistant_2, user_1, assistant(tool_call_1), tool(output_1), assistant_1]
        self.assertEqual(roles, ["system", "user", "assistant", "user", "assistant", "tool", "assistant"])
        self.assertIn("25 * 14", final_hist[1]["content"])
        self.assertEqual(final_hist[2]["content"], "25 * 14 is 350.")
        self.assertIn("hockey games", final_hist[3]["content"])
        self.assertEqual(final_hist[5]["name"], "skill_sports_info")
        self.assertIn("Oilers vs Flames", final_hist[6]["content"])


class TestTimeConverterAccuracy(unittest.TestCase):
    def test_ordinal_dates_and_section_headers_parsed(self):
        from datetime import datetime, timezone
        from src.orchestrator import enrich_tool_output_with_time_context

        ref_utc = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
        raw = (
            "📅 --- SATURDAY, OCTOBER 24TH, 2026 ---\n"
            "  • Arsenal vs Chelsea (Kickoff: 16:30 UTC)\n"
            "  • Formula 1 Qualifying [Sat, Oct 24th - 4:00 PM EDT]"
        )
        enriched = enrich_tool_output_with_time_context(raw, now=ref_utc, tz_override="MDT")
        self.assertIn("16:30 UTC (Sat Oct 24 10:30 AM MDT / 18:30 CEST", enriched)
        self.assertIn("Sat, Oct 24th - 4:00 PM EDT (Sat Oct 24 2:00 PM MDT / 22:00 CEST", enriched)

    def test_cross_year_december_to_january_rollover(self):
        from datetime import datetime, timezone
        from src.orchestrator import enrich_tool_output_with_time_context

        # Current time: Dec 28, 2026 at 20:00 UTC -> Jan 3 without a year must resolve to Jan 3, 2027 (in ~6d), NOT 359d ago!
        ref_utc = datetime(2026, 12, 28, 20, 0, tzinfo=timezone.utc)
        raw = "  • Winter Classic: Bruins @ Blackhawks [Sun, Jan 3 - 2:00 PM EST]"
        enriched = enrich_tool_output_with_time_context(raw, now=ref_utc, tz_override="MST")
        self.assertIn("Sun, Jan 3 - 2:00 PM EST (Sun Jan 03 12:00 PM MST / 20:00 CET, in 5d 23h)", enriched)

    def test_liquipedia_discord_epoch_line_not_double_annotated_and_uses_bot_tz(self):
        from datetime import datetime, timezone
        from src.orchestrator import enrich_tool_output_with_time_context, sanitize_agent_reply
        from src.skills.music.utils import standardize_song_reply
        from src.skills.sports.aggregators.liquipedia import LiquipediaAggregator

        ref_utc = datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc)
        agg = LiquipediaAggregator()
        with patch.dict("os.environ", {"BOT_TIMEZONE": "America/Edmonton"}, clear=False):
            ts_str = agg._format_match_timestamp("October 16, 2026 - 14:00 UTC", str(int(ref_utc.timestamp())))
        self.assertIn("8:00 AM MDT", ts_str)
        self.assertIn("Oct 16, 14:00 UTC", ts_str)
        self.assertNotIn("<t:", ts_str)

        line = f"  • Team Spirit vs Vitality — {ts_str}"
        enriched = enrich_tool_output_with_time_context(line, now=ref_utc, tz_override="MDT")
        # Must not inject nested parentheses inside an already-formatted (UTC | Local) line
        self.assertTrue(enriched.endswith(line))

        # Regression: Gemma NHL+CS output with [Time](<t:...>) and [Playoffs](<t:...>) must not leave unresolved <t:...> or orphaned '*   :'
        raw_gemma_reply = (
            "*   Pittsburgh Penguins at Colorado Avalanche [9:00 PM EDT](<t:1705347600:t>)\n"
            "*   [Playoffs](<t:1792245600:t>): Aurora Gaming vs FURIA (Bo3)\n"
            "*   : Team Falcons vs BetBoom Team (Bo3)"
        )
        cleaned = sanitize_agent_reply(standardize_song_reply(raw_gemma_reply))
        self.assertIn("Pittsburgh Penguins at Colorado Avalanche 9:00 PM EDT", cleaned)
        self.assertIn("Playoffs: Aurora Gaming vs FURIA (Bo3)", cleaned)
        self.assertIn("*   Team Falcons vs BetBoom Team (Bo3)", cleaned)
        self.assertNotIn("<t:", cleaned)
        self.assertNotIn("*   :", cleaned)

    def test_dst_transition_mismatch_october_eu_cet_vs_us_mdt(self):
        from datetime import datetime, timezone
        from src.orchestrator import enrich_tool_output_with_time_context

        # Oct 23, 2026: Europe is still on CEST (UTC+2), US is on MDT (UTC-6).
        # Event is on Oct 28, 2026: Europe has switched to CET (UTC+1 on Oct 25), while US is still on MDT (UTC-6 until Nov 1)!
        ref_utc = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        raw = "  • Champions League: Bayern vs PSG [Wed, Oct 28, 2026 at 20:00 UTC]"
        enriched = enrich_tool_output_with_time_context(raw, now=ref_utc, tz_override="MDT")
        # 20:00 UTC on Oct 28 = 2:00 PM MDT (UTC-6) and 21:00 CET (UTC+1)
        self.assertIn("Wed, Oct 28, 2026 at 20:00 UTC (Wed Oct 28 2:00 PM MDT / 21:00 CET", enriched)

    def test_soccer_kickoff_full_utc_date_prevents_midnight_drift(self):
        from datetime import datetime, timezone
        from src.orchestrator import enrich_tool_output_with_time_context

        # Section header says Thursday Oct 15 (US local date), but match kicks off at 2026-10-16 01:30 UTC (= Thu Oct 15 7:30 PM MDT)
        ref_utc = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)
        raw = (
            "📅 --- THURSDAY, OCTOBER 15, 2026 (1 Match) ---\n"
            "  • LAFC at Inter Miami (Kickoff: 2026-10-16 01:30 UTC) [Scheduled]"
        )
        enriched = enrich_tool_output_with_time_context(raw, now=ref_utc, tz_override="MDT")
        self.assertIn("2026-10-16 01:30 UTC (Today 7:30 PM MDT / Fri 03:30 CEST, in 30m)", enriched)

    def test_12h_noon_midnight_and_24h_boundaries(self):
        from datetime import datetime, timezone
        from src.orchestrator import enrich_tool_output_with_time_context

        ref_utc = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
        raw = (
            "  • Noon Event [Fri, Oct 16 - 12:00 PM UTC]\n"
            "  • Midnight Event [Fri, Oct 16 - 12:00 AM UTC]"
        )
        enriched = enrich_tool_output_with_time_context(raw, now=ref_utc, tz_override="MDT")
        self.assertIn("12:00 PM UTC (Today 6:00 AM MDT / 14:00 CEST, now)", enriched)
        self.assertIn("12:00 AM UTC (Yesterday 6:00 PM MDT / Fri 02:00 CEST, 12h ago)", enriched)


class TestConversationWithAllToolsAndSkills(unittest.IsolatedAsyncioTestCase):
    """Verifies that a casual conversation / greeting / comparative remark paired with
    each of the registered tools and skills normalizes, enriches, executes, and post-processes cleanly."""

    def test_conversation_plus_music_genre_mood_artist_and_shift_variants(self):
        from src.orchestrator import enrich_music_tool_args

        # 1. Conversation with comparative words ("slower", "harder day") + explicit genre request -> must NOT trigger NON_MUSIC_CONVERSATION
        hist_genre = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: Yo how's it going! I had a harder day today and traffic was slower than usual, can you play a drum and bass song?"},
        ]
        args_genre = enrich_music_tool_args({"genre": "drum and bass", "shift": "slower"}, hist_genre)
        self.assertEqual(args_genre.get("genre"), "drum and bass")
        self.assertNotIn("shift", args_genre)

        # 2. Conversation + Artist request
        hist_artist = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: Hey bot! Why is Python slower than C++? Also play a song by Daft Punk"},
        ]
        args_artist = enrich_music_tool_args({"artist": "Daft Punk"}, hist_artist)
        self.assertEqual(args_artist.get("artist"), "Daft Punk")
        self.assertNotIn("shift", args_artist)

        # 3. Conversation + Mood request
        hist_mood = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: Good morning! Play something chill while I work"},
        ]
        args_mood = enrich_music_tool_args({"mood": "chill"}, hist_mood)
        self.assertEqual(args_mood.get("mood"), "chill")
        self.assertNotIn("shift", args_mood)

        # 4. Conversation + Relative Spectrum Shift
        hist_shift = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: That last song was nice, now play something faster and harder"},
        ]
        args_shift = enrich_music_tool_args({}, hist_shift)
        self.assertEqual(args_shift.get("shift"), "That last song was nice, now play something faster and harder")

    def test_conversation_plus_all_registered_tools_argument_enrichment_and_execution(self):
        from src.orchestrator import enrich_sports_tool_args, normalize_tool_args
        from src.skills.weather.reporter import skill_weather_reporter

        # Weather skill prompt instruction must allow answering other questions in full
        with patch("src.skills.weather.reporter._weather_aggregator.get_weather", return_value="Sunny, +22°C"):
            w_out = skill_weather_reporter("Tokyo")
        self.assertIn("Keep the weather section under 4 sentences", w_out)
        self.assertIn("answer any other questions or requests from the user in full", w_out)

        # Sports enrichment with conversational + music clause strips the music clause cleanly
        hist_sports_conv = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: hey how's it going? what are the NHL games today and play a rock track"},
        ]
        s_args = enrich_sports_tool_args({"sport": "nhl", "query": "games today"}, hist_sports_conv)
        self.assertNotIn("rock track", s_args["query"].lower())

        # Verify all registered tools in research-gemma mode normalize arguments cleanly
        tools_map = MODES["research-gemma"]["tools"]
        expected_tools = {
            "search_web",
            "read_website",
            "read_youtube_video",
            "skill_weather_reporter",
            "skill_top_headlines",
            "get_top_headlines",
            "skill_youtube_searcher",
            "skill_top_music",
            "skill_audio_intelligence",
            "skill_csgo_esports",
            "skill_betting_odds",
            "skill_sports_info",
            "skill_track_live_game",
            "evaluate_math",
            "get_current_datetime",
            "save_memory",
            "recall_memories",
        }
        self.assertEqual(set(tools_map.keys()), expected_tools)

        sample_calls = [
            ("search_web", {"q": "quantum computing"}, {"query": "quantum computing"}),
            ("read_website", {"link": "https://example.com"}, {"url": "https://example.com"}),
            ("read_youtube_video", {"link": "https://www.youtube.com/watch?v=12345678901"}, {"url": "https://www.youtube.com/watch?v=12345678901"}),
            ("skill_weather_reporter", {"location": "Paris"}, {"city": "Paris"}),
            ("skill_top_headlines", {"country": "Canada"}, {"region": "Canada"}),
            ("get_top_headlines", {"category": "Technology"}, {"topic": "Technology"}),
            ("skill_youtube_searcher", {"q": "elden ring trailer", "is_music": "false"}, {"query": "elden ring trailer", "is_music": False}),
            ("skill_top_music", {"vibe": "chill", "style": "synthwave"}, {"mood": "chill", "genre": "synthwave"}),
            ("skill_audio_intelligence", {"song": "Strobe by deadmau5"}, {"track": "Strobe by deadmau5"}),
            ("skill_csgo_esports", {"type": "rankings"}, {"query_type": "rankings"}),
            ("skill_betting_odds", {"match": "Falcons vs Vitality", "league": "cs2"}, {"query": "Falcons vs Vitality", "sport": "cs2"}),
            ("skill_sports_info", {"league": "nba", "q": "lakers schedule"}, {"sport": "nba", "query": "lakers schedule"}),
            ("skill_track_live_game", {"team": "Real Madrid"}, {"query": "Real Madrid"}),
            ("evaluate_math", {"math": "25 * 14"}, {"expression": "25 * 14"}),
            ("get_current_datetime", {}, {}),
            ("save_memory", {"memory": "Likes synthwave"}, {"fact": "Likes synthwave"}),
            ("recall_memories", {}, {}),
        ]
        for t_name, raw_in, expected_out in sample_calls:
            with self.subTest(tool=t_name):
                norm = normalize_tool_args(t_name, tools_map[t_name], raw_in)
                self.assertEqual(norm, expected_out)


class TestAllToolAndSkillCombinations(unittest.IsolatedAsyncioTestCase):
    """Tests cross-domain multi-tool combinations (Music + Sports, CS + Soccer, Video + Song Card,
    Sports + CS sibling scoping, and 4-tool mega-turns) through orchestration and post-processing."""

    @patch("src.skills.youtube.client.verify_embeddable", return_value=True)
    @patch("src.skills.music.utils.get_music_metadata")
    def test_video_link_and_music_song_card_both_preserved_in_standardize_song_reply(self, mock_meta, _mock_embed):
        mock_meta.return_value = {
            "song": "Nightcall",
            "artist": "Kavinsky",
            "album": "OutRun",
            "genre": "Synthwave",
        }
        compound_reply = (
            "Here is the GTA 6 trailer you asked for:\n"
            "https://www.youtube.com/watch?v=QdBZY2fkU-0\n\n"
            "And here is a synthwave track to vibe to:\n"
            "**Song:** Nightcall\n"
            "**Artist:** Kavinsky\n"
            "https://music.youtube.com/watch?v=MV_3Dpw-BRY"
        )
        out = standardize_song_reply(compound_reply)
        # Both the regular YouTube trailer link AND the YouTube Music song card link must be preserved!
        self.assertIn("https://www.youtube.com/watch?v=QdBZY2fkU-0", out)
        self.assertIn("**Song:** Nightcall", out)
        self.assertIn("**Artist:** Kavinsky", out)
        self.assertTrue(out.endswith("https://music.youtube.com/watch?v=MV_3Dpw-BRY"))

    def test_sibling_tools_scoping_prevents_duplicate_cs_and_odds_in_sports_info(self):
        from src.orchestrator import enrich_sports_tool_args

        # When model calls both skill_sports_info(sport='nhl') and skill_csgo_esports in parallel
        hist = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: when does Team Spirit play in CS2 and what are the NHL games today?"},
        ]
        enriched = enrich_sports_tool_args(
            {"sport": "nhl", "query": "NHL games today"},
            hist,
            sibling_tools=["skill_csgo_esports"],
        )
        self.assertNotIn("spirit", enriched["query"].lower())
        self.assertNotIn("cs2", enriched["query"].lower())

        # When model calls both skill_sports_info(sport='nba') and skill_betting_odds in parallel
        hist_odds = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: what are the NBA games today and their betting odds"},
        ]
        enriched_odds = enrich_sports_tool_args(
            {"sport": "nba", "query": "NBA games today"},
            hist_odds,
            sibling_tools=["skill_betting_odds"],
        )
        self.assertNotIn("odds", enriched_odds["query"].lower())

    async def test_parallel_tool_execution_allows_non_music_video_with_top_music_and_blocks_music_video(self):
        from types import SimpleNamespace
        with patch("main.ensure_ollama_running"):
            import main as main_mod

        called_tools = []

        def fake_top_music(genre: Optional[str] = None, mood: Optional[str] = None):
            called_tools.append(("skill_top_music", genre))
            return "=== TODAY'S TOP CHARTING MUSIC ===\n**Song:** Strobe\n**Artist:** deadmau5\nhttps://music.youtube.com/watch?v=tKi9Z-f6qX4"

        def fake_yt_searcher(query: str, max_results: Optional[int] = 1, is_music: Optional[bool] = False):
            called_tools.append(("skill_youtube_searcher", query, is_music))
            return "https://www.youtube.com/watch?v=QdBZY2fkU-0"

        def fake_weather(city: str):
            called_tools.append(("skill_weather_reporter", city))
            return "Sunny +20C"

        def fake_sports(sport: Optional[str] = None, query: Optional[str] = None):
            called_tools.append(("skill_sports_info", sport, query))
            return "=== NHL SCOREBOARD ===\nOilers vs Flames"

        fake_mode_cfg = {
            "model": "test-model",
            "max_iterations": 3,
            "is_research": True,
            "supports_tools": True,
            "history_limit": 40,
            "ollama_options": {},
            "tools": {
                "skill_top_music": fake_top_music,
                "skill_youtube_searcher": fake_yt_searcher,
                "skill_weather_reporter": fake_weather,
                "skill_sports_info": fake_sports,
            },
        }

        # Case 1: 4 tools in parallel including skill_youtube_searcher(is_music=False) + skill_top_music -> ALL 4 execute!
        step1_msg = SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name="skill_top_music", arguments={"genre": "synthwave"})),
                SimpleNamespace(function=SimpleNamespace(name="skill_youtube_searcher", arguments={"query": "GTA 6 trailer", "is_music": False})),
                SimpleNamespace(function=SimpleNamespace(name="skill_weather_reporter", arguments={"city": "Calgary"})),
                SimpleNamespace(function=SimpleNamespace(name="skill_sports_info", arguments={"sport": "nhl", "query": "games today"})),
            ],
        )
        step2_msg = SimpleNamespace(content="Here is everything you asked for!", tool_calls=None)

        mock_chat = unittest.mock.AsyncMock(side_effect=[
            SimpleNamespace(message=step1_msg),
            SimpleNamespace(message=step2_msg),
        ])
        turn_hist = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: check Calgary weather, NHL games today, find the GTA 6 trailer video, and play a synthwave song"},
        ]
        with patch.object(main_mod.ollama_client, "chat", mock_chat):
            reply = await main_mod.run_agent_turn(777, fake_mode_cfg, turn_history=turn_hist)

        self.assertEqual(reply, "Here is everything you asked for!")
        self.assertEqual(len(called_tools), 4)
        tool_msgs = [m for m in turn_hist if isinstance(m, dict) and m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 4)
        self.assertTrue(all("PARALLEL_EXECUTION_BLOCKED" not in m["content"] for m in tool_msgs))

        # Case 2: skill_youtube_searcher(is_music=True) called in parallel with skill_top_music -> blocked!
        called_tools.clear()
        step1_blocked = SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name="skill_top_music", arguments={"genre": "rock"})),
                SimpleNamespace(function=SimpleNamespace(name="skill_youtube_searcher", arguments={"query": "rock song", "is_music": True})),
            ],
        )
        mock_chat_blocked = unittest.mock.AsyncMock(side_effect=[
            SimpleNamespace(message=step1_blocked),
            SimpleNamespace(message=step2_msg),
        ])
        turn_hist_blocked = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: play a rock song"},
        ]
        with patch.object(main_mod.ollama_client, "chat", mock_chat_blocked):
            await main_mod.run_agent_turn(778, fake_mode_cfg, turn_history=turn_hist_blocked)

        blocked_msgs = [m for m in turn_hist_blocked if isinstance(m, dict) and m.get("role") == "tool" and m.get("name") == "skill_youtube_searcher"]
        self.assertEqual(len(blocked_msgs), 1)
        self.assertIn("PARALLEL_EXECUTION_BLOCKED", blocked_msgs[0]["content"])


class TestMultiTurnAndStatefulFlows(unittest.TestCase):
    def test_casual_greetings_after_sports_turn_do_not_trigger_missed_sports_tool(self):
        from src.orchestrator import detect_missed_sports_tool_call

        prior_sports = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "User Alex says: whats the score of the real madrid game"},
            {"role": "tool", "name": "skill_sports_info", "content": "Real Madrid 2 - 1 Barcelona (60')"},
            {"role": "assistant", "content": "Real Madrid leads 2-1 in the 60th minute."},
        ]
        casual_turns = [
            "User Alex says: hey how's it going?",
            "User Alex says: where do you live?",
            "User Alex says: glad the workday is over!",
            "User Alex says: I ate half a pizza earlier",
            "User Alex says: how's it going? also play a synthwave song",
        ]
        for msg in casual_turns:
            with self.subTest(msg=msg):
                self.assertIsNone(detect_missed_sports_tool_call(prior_sports + [{"role": "user", "content": msg}], active_channel_id=101))

        # Genuine match follow-ups after a sports turn MUST still trigger skill_sports_info
        genuine_followups = [
            "User Alex says: hows the game going?",
            "User Alex says: is it still live?",
            "User Alex says: is the game over?",
            "User Alex says: who scored last goal?",
        ]
        for msg in genuine_followups:
            with self.subTest(msg=msg):
                missed = detect_missed_sports_tool_call(prior_sports + [{"role": "user", "content": msg}], active_channel_id=101)
                self.assertIsNotNone(missed)
                self.assertEqual(missed[0], "skill_sports_info")

    def test_reply_to_song_card_with_genre_override_vs_similarity(self):
        from src.orchestrator import enrich_music_tool_args

        # Replying to a song card with a brand-new genre request ("nice song! now play some jazz")
        # should NOT force similar_to='Nightcall by Kavinsky'
        hist_override = [
            {"role": "user", "content": "User Alex says [Replying to song card: 'Nightcall' by 'Kavinsky']: nice song! now play some jazz"},
        ]
        enriched_override = enrich_music_tool_args({"genre": "jazz"}, hist_override)
        self.assertEqual(enriched_override.get("genre"), "jazz")
        self.assertNotIn("similar_to", enriched_override)

        # Replying to a song card asking for "something similar in synthwave" SHOULD keep similar_to
        hist_sim = [
            {"role": "user", "content": "User Alex says [Replying to song card: 'Nightcall' by 'Kavinsky']: play something similar to this in synthwave"},
        ]
        enriched_sim = enrich_music_tool_args({"genre": "synthwave"}, hist_sim)
        self.assertEqual(enriched_sim.get("genre"), "synthwave")
        self.assertEqual(enriched_sim.get("similar_to"), "Nightcall by Kavinsky")

    def test_auto_ping_protects_song_card_metadata_lines(self):
        reply = (
            "Yo future, here is that track you wanted:\n\n"
            "**Song:** Mask Off\n"
            "**Artist:** Future\n"
            "**Album:** FUTURE\n"
            "**Genre:** Hip-Hop\n\n"
            "https://music.youtube.com/watch?v=xvZqHgFz51I"
        )
        out = apply_auto_ping(reply, {"future": 987654321})
        # Conversational intro pings the user "future", while the song card lines remain untouched!
        self.assertIn("Yo <@987654321>, here is that track you wanted:", out)
        self.assertIn("**Song:** Mask Off", out)
        self.assertIn("**Artist:** Future", out)
        self.assertIn("**Album:** FUTURE", out)


class TestMultiUserDiscordChannelConcurrency(unittest.IsolatedAsyncioTestCase):
    """Emulates 2 and 3 users interacting concurrently in a shared Discord channel
    through main.on_message, main.on_raw_reaction_add, and main.handle_live_match_update."""

    async def test_three_users_concurrent_on_message_in_shared_discord_channel(self):
        import asyncio
        import time
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from src.utils.workers import run_in_worker

        with patch("main.ensure_ollama_running"):
            import main as main_mod

        channel_id = 9001
        bot_user = SimpleNamespace(id=999, name="Bot", display_name="Bot")
        sent_messages = []

        @asynccontextmanager
        async def fake_typing():
            yield

        async def fake_send(text):
            sent_messages.append(text)

        fake_channel = SimpleNamespace(
            id=channel_id,
            typing=fake_typing,
            send=fake_send,
        )

        user_alex = SimpleNamespace(id=101, name="alex", display_name="Alex", mention="<@101>")
        user_sam = SimpleNamespace(id=102, name="sam", display_name="Sam", mention="<@102>")
        user_jordan = SimpleNamespace(id=103, name="jordan", display_name="Jordan", mention="<@103>")

        msg_alex = SimpleNamespace(
            author=user_alex,
            channel=fake_channel,
            content="<@999> what are the NHL games today?",
            mentions=[bot_user],
            reference=None,
        )
        msg_sam = SimpleNamespace(
            author=user_sam,
            channel=fake_channel,
            content="<@999> play a synthwave song",
            mentions=[bot_user],
            reference=None,
        )
        msg_jordan = SimpleNamespace(
            author=user_jordan,
            channel=fake_channel,
            content="<@999> what is 12 * 12?",
            mentions=[bot_user],
            reference=None,
        )

        async def fake_run_agent_turn(cid, mode_config, turn_history=None):
            history = turn_history
            user_prompt = history[-1]["content"]
            if "NHL games" in user_prompt:
                history.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "skill_sports_info", "arguments": {"sport": "nhl"}}}],
                })
                tool_out = await run_in_worker(lambda: (time.sleep(0.14), "=== NHL ===\nOilers vs Flames")[1])
                history.append({"role": "tool", "name": "skill_sports_info", "content": tool_out})
                raw_reply = "Hey Alex, today's NHL game is Oilers vs Flames."
                history.append({"role": "assistant", "content": raw_reply})
                return raw_reply
            elif "synthwave song" in user_prompt:
                history.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "skill_top_music", "arguments": {"genre": "synthwave"}}}],
                })
                tool_out = await run_in_worker(lambda: (time.sleep(0.06), "Song: Nightcall\nArtist: Kavinsky")[1])
                history.append({"role": "tool", "name": "skill_top_music", "content": tool_out})
                raw_reply = (
                    "Here you go Sam:\n"
                    "**Song:** Nightcall\n"
                    "**Artist:** Kavinsky\n"
                    "https://music.youtube.com/watch?v=MV_3Dpw-BRY"
                )
                history.append({"role": "assistant", "content": raw_reply})
                return raw_reply
            else:
                await asyncio.sleep(0.01)
                raw_reply = "Yo Jordan, 12 * 12 is 144."
                history.append({"role": "assistant", "content": raw_reply})
                return raw_reply

        main_mod.chat_history.pop(channel_id, None)
        main_mod.known_users.clear()
        prev_mode = main_mod.current_mode
        main_mod.current_mode = "chat"

        try:
            with patch.object(main_mod, "client", SimpleNamespace(user=bot_user)), patch.object(
                main_mod, "run_agent_turn", side_effect=fake_run_agent_turn
            ), patch(
                "src.skills.youtube.client.verify_embeddable", return_value=True
            ), patch(
                "src.skills.music.utils.get_music_metadata",
                return_value={"song": "Nightcall", "artist": "Kavinsky", "album": "OutRun", "genre": "Synthwave"},
            ):
                t_alex = asyncio.create_task(main_mod.on_message(msg_alex))
                await asyncio.sleep(0.005)
                t_sam = asyncio.create_task(main_mod.on_message(msg_sam))
                await asyncio.sleep(0.005)
                t_jordan = asyncio.create_task(main_mod.on_message(msg_jordan))
                await asyncio.gather(t_alex, t_sam, t_jordan)

            # 1. Replies sent in completion order: Jordan (fastest) -> Sam (medium) -> Alex (slowest)
            self.assertEqual(len(sent_messages), 3)
            self.assertIn("<@103>", sent_messages[0])
            self.assertIn("144", sent_messages[0])
            self.assertIn("<@102>", sent_messages[1])
            self.assertIn("**Genre:** Synthwave", sent_messages[1])
            self.assertIn("<@101>", sent_messages[2])
            self.assertIn("Oilers vs Flames", sent_messages[2])

            # 2. All 3 users registered in known_users
            self.assertEqual(main_mod.known_users.get("alex"), 101)
            self.assertEqual(main_mod.known_users.get("sam"), 102)
            self.assertEqual(main_mod.known_users.get("jordan"), 103)

            # 3. chat_history[9001] has 3 atomic, non-interleaved turn blocks AND stores the post-processed reply
            hist = main_mod.chat_history[channel_id]
            roles = [m["role"] for m in hist]
            # [system, jordan_u, jordan_a, sam_u, sam_tc, sam_tool, sam_a, alex_u, alex_tc, alex_tool, alex_a]
            self.assertEqual(
                roles,
                ["system", "user", "assistant", "user", "assistant", "tool", "assistant", "user", "assistant", "tool", "assistant"],
            )
            # Sam's committed assistant message in chat_history includes the standardized Genre line and auto-ping
            self.assertIn("**Genre:** Synthwave", hist[6]["content"])
            self.assertIn("<@102>", hist[6]["content"])
        finally:
            main_mod.current_mode = prev_mode
            main_mod.chat_history.pop(channel_id, None)

    async def test_dual_user_message_turn_concurrent_with_second_user_thumbs_up_and_live_tracker_alert(self):
        import asyncio
        import time
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from src.utils.workers import run_in_worker

        with patch("main.ensure_ollama_running"):
            import main as main_mod

        channel_id = 9002
        bot_user = SimpleNamespace(id=999, name="Bot", display_name="Bot")
        sent_messages = []

        @asynccontextmanager
        async def fake_typing():
            yield

        async def fake_send(text):
            sent_messages.append(text)

        reacted_card_msg = SimpleNamespace(
            id=5001,
            author=bot_user,
            content=(
                "**Song:** Nightcall\n"
                "**Artist:** Kavinsky\n"
                "**Genre:** Synthwave\n\n"
                "https://music.youtube.com/watch?v=MV_3Dpw-BRY"
            ),
        )

        async def fake_fetch_message(mid):
            return reacted_card_msg

        fake_channel = SimpleNamespace(
            id=channel_id,
            typing=fake_typing,
            send=fake_send,
            fetch_message=fake_fetch_message,
        )

        user_alex = SimpleNamespace(id=101, name="alex", display_name="Alex", mention="<@101>")
        user_sam = SimpleNamespace(id=102, name="sam", display_name="Sam", mention="<@102>")

        fake_client = SimpleNamespace(
            user=bot_user,
            get_channel=lambda cid: fake_channel,
            fetch_channel=AsyncMock(return_value=fake_channel),
            get_user=lambda uid: user_sam if uid == 102 else user_alex,
            fetch_user=AsyncMock(return_value=user_sam),
        )

        msg_alex = SimpleNamespace(
            author=user_alex,
            channel=fake_channel,
            content="<@999> check the weather in Tokyo",
            mentions=[bot_user],
            reference=None,
        )
        reaction_payload = SimpleNamespace(
            user_id=102,
            channel_id=channel_id,
            message_id=5001,
            emoji="👍",
        )
        live_match = SimpleNamespace(channel_id=channel_id, league_name="Premier League")
        live_snapshot = SimpleNamespace(
            home_team="Arsenal",
            away_team="Chelsea",
            home_score=2,
            away_score=1,
            status_detail="78'",
            stats_summary="Possession: ARS 58% - 42% CHE",
        )

        async def fake_run_agent_turn(cid, mode_config, turn_history=None):
            turn_history.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "skill_weather_reporter", "arguments": {"city": "Tokyo"}}}],
            })
            out = await run_in_worker(lambda: (time.sleep(0.12), "Sunny +22C")[1])
            turn_history.append({"role": "tool", "name": "skill_weather_reporter", "content": out})
            reply = "Tokyo is currently Sunny and +22C."
            turn_history.append({"role": "assistant", "content": reply})
            return reply

        main_mod.chat_history.pop(channel_id, None)
        main_mod.handled_reactions.clear()

        try:
            with patch.object(main_mod, "client", fake_client), patch.object(
                main_mod, "run_agent_turn", side_effect=fake_run_agent_turn
            ), patch.object(
                main_mod.ollama_client,
                "chat",
                AsyncMock(return_value=SimpleNamespace(message=SimpleNamespace(content="Here is a similar synthwave track!"))),
            ), patch(
                "main.tools.skill_top_music",
                return_value=(
                    "=== VERIFIED YOUTUBE MUSIC RESULT ===\n"
                    "Song: Midnight City\n"
                    "Artist: M83\n"
                    "Genre: Synthpop\n"
                    "Link: https://music.youtube.com/watch?v=dX3k_QDnzHE\n"
                ),
            ), patch(
                "main.get_music_metadata",
                return_value={"song": "Midnight City", "artist": "M83", "album": "Hurry Up, We're Dreaming", "genre": "Synthpop"},
            ):
                t_alex = asyncio.create_task(main_mod.on_message(msg_alex))
                await asyncio.sleep(0.01)
                t_sam_react = asyncio.create_task(main_mod.on_raw_reaction_add(reaction_payload))
                t_tracker = asyncio.create_task(
                    main_mod.handle_live_match_update(live_match, live_snapshot, ["⚽ GOAL! Saka 78' (Arsenal)"])
                )
                await asyncio.gather(t_alex, t_sam_react, t_tracker)

            # All 3 events sent their messages to the channel
            self.assertEqual(len(sent_messages), 3)
            self.assertTrue(any("📡 **LIVE UPDATE (Premier League)**" in m for m in sent_messages))
            self.assertTrue(any("<@102>" in m and "**Song:** Midnight City" in m for m in sent_messages))
            self.assertTrue(any("Tokyo is currently Sunny" in m for m in sent_messages))

            # Verify Alex's [assistant(tool_call), tool(output), assistant(reply)] block in chat_history was NOT split
            hist = main_mod.chat_history[channel_id]
            tool_idx = next(i for i, m in enumerate(hist) if m.get("role") == "tool" and m.get("name") == "skill_weather_reporter")
            self.assertEqual(hist[tool_idx - 1].get("role"), "assistant")
            self.assertTrue(hist[tool_idx - 1].get("tool_calls"))
            self.assertEqual(hist[tool_idx + 1].get("role"), "assistant")
            self.assertIn("Tokyo is currently Sunny", hist[tool_idx + 1].get("content", ""))
        finally:
            main_mod.chat_history.pop(channel_id, None)
            main_mod.handled_reactions.clear()

    async def test_three_users_concurrent_save_memory_and_recall_thread_safety(self):
        import asyncio
        import json
        import os
        import tempfile
        from src.tools.system import recall_memories, save_memory, set_memory_file
        from src.utils.workers import run_in_worker

        with tempfile.TemporaryDirectory() as tmpdir:
            mem_path = os.path.join(tmpdir, "shared_memory.json")
            set_memory_file(mem_path)
            try:
                facts = [
                    "Alex supports Arsenal in the Premier League",
                    "Sam loves drum and bass music",
                    "Jordan watches Formula 1 and NHL",
                    "Alex prefers dark roast coffee",
                    "Sam plays Counter-Strike 2 on weekends",
                    "Jordan lives in Mountain Time",
                ]
                await asyncio.gather(
                    *(run_in_worker(save_memory, f) for f in facts),
                    run_in_worker(recall_memories),
                )
                with open(mem_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual(sorted(saved), sorted(facts))
                recalled = recall_memories()
                for f in facts:
                    self.assertIn(f, recalled)
            finally:
                set_memory_file("data/memory.json")

    async def test_daemon_worker_pool_and_shutdown_suppresses_keyboard_interrupt(self):
        import threading
        from src.utils.workers import SUB_WORKER_POOL, WORKER_POOL, run_in_worker, submit_to_worker

        res1 = await run_in_worker(lambda: threading.current_thread().daemon)
        res2 = submit_to_worker(lambda: threading.current_thread().daemon).result(timeout=5)
        self.assertTrue(res1)
        self.assertTrue(res2)
        self.assertTrue(all(t.daemon for t in WORKER_POOL._threads))
        self.assertTrue(all(t.daemon for t in SUB_WORKER_POOL._threads))
        self.assertTrue(getattr(threading._shutdown, "_bot_wrapped", False))


if __name__ == "__main__":
    unittest.main()





