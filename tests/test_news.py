import sys
import unittest
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import src.skills.news.headlines as news
from src.skills.news import _build_feed_url, skill_top_headlines, get_top_headlines

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Top stories</title>
<item>
  <title>Carney addresses Alberta ahead of referendum - CBC</title>
  <link>https://news.google.com/rss/articles/AAA111</link>
  <pubDate>Mon, 12 Oct 2026 20:25:02 GMT</pubDate>
  <source url="https://www.cbc.ca">CBC</source>
</item>
<item>
  <title>Advance voting opens in Alberta - Global News</title>
  <link>https://news.google.com/rss/articles/BBB222</link>
  <pubDate>Mon, 12 Oct 2026 18:08:35 GMT</pubDate>
  <source url="https://globalnews.ca">Global News</source>
</item>
</channel></rss>"""


class TestFeedUrls(unittest.TestCase):
    def test_country_edition_top_stories(self):
        url, label = _build_feed_url("Canada", "")
        self.assertEqual(url, "https://news.google.com/rss?hl=en-CA&gl=CA&ceid=CA:en")
        self.assertIn("Canada", label)

    def test_topic_section_in_edition(self):
        url, _ = _build_feed_url("USA", "tech")
        self.assertIn("/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US", url)

    def test_world_when_empty(self):
        url, _ = _build_feed_url("", "")
        self.assertIn("/topic/WORLD?", url)

    def test_region_without_edition_uses_recent_search(self):
        url, _ = _build_feed_url("Europe", "")
        self.assertIn("/rss/search?q=Europe+when%3A1d", url)

    def test_generic_topic_with_region_means_top_stories(self):
        self.assertEqual(_build_feed_url("Canada", "world")[0], _build_feed_url("Canada", "")[0])
        self.assertEqual(_build_feed_url("Europe", "news")[0], _build_feed_url("Europe", "")[0])

    def test_bot_wall_detection(self):
        from src.tools.web import _looks_blocked
        self.assertTrue(_looks_blocked("Please complete the CAPTCHA to continue."))
        self.assertFalse(_looks_blocked("A normal article. " * 300 + "It mentions captcha once."))
        self.assertFalse(_looks_blocked("Short normal article about the economy."))


class TestTopHeadlinesSkill(unittest.TestCase):
    def setUp(self):
        news._CACHE.clear()

    @patch("src.skills.news.headlines.decode_google_news_url")
    @patch("src.skills.news.headlines.requests.get")
    def test_formats_stories_with_decoded_urls(self, mock_get, mock_decode):
        resp = MagicMock(content=SAMPLE_RSS)
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        mock_decode.side_effect = lambda link: {
            "https://news.google.com/rss/articles/AAA111": "https://www.cbc.ca/news/carney-alberta",
        }.get(link)

        out = skill_top_headlines(region="Canada")
        self.assertIn("1. Carney addresses Alberta ahead of referendum [CBC | Oct 12, 2026 20:25 UTC]", out)
        self.assertIn("URL: https://www.cbc.ca/news/carney-alberta", out)
        # Undecodable link falls back to the Google News URL
        self.assertIn("URL: https://news.google.com/rss/articles/BBB222", out)
        self.assertNotIn(" - Global News [", out)

        # Second call is served from cache
        get_top_headlines(region="Canada")
        self.assertEqual(mock_get.call_count, 1)

    @patch("src.skills.news.headlines.requests.get", side_effect=Exception("boom"))
    def test_feed_failure_points_to_search_web(self, _):
        out = skill_top_headlines(region="Canada")
        self.assertIn("search_web", out)

    def test_news_argument_aliases(self):
        from src.orchestrator import normalize_tool_args
        args = normalize_tool_args(
            "skill_top_headlines",
            skill_top_headlines,
            {"country": "USA", "category": "technology"},
        )
        self.assertEqual(args, {"region": "USA", "topic": "technology"})


if __name__ == "__main__":
    unittest.main()
