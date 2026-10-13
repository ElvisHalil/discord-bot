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
        self.assertEqual(apply_auto_ping("yo jubkill, nice", {"jubkill": 42}), "yo <@42>, nice")
        self.assertEqual(apply_auto_ping("@Jubkill lol", {"jubkill": 42}), "<@42> lol")

    def test_ignores_short_names_and_substrings(self):
        self.assertEqual(apply_auto_ping("it is what it is", {"it": 1}), "it is what it is")
        self.assertEqual(apply_auto_ping("samsung phone", {"sam": 1}), "samsung phone")

    def test_does_not_touch_urls_mentions_or_code(self):
        reply = "see https://jubkill.com/page and <@99> and `jubkill`"
        self.assertEqual(apply_auto_ping(reply, {"jubkill": 42}), reply)


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


if __name__ == "__main__":
    unittest.main()

