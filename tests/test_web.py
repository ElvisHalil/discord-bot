import sys
import unittest
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.tools.web import (
    read_website,
    search_web,
    read_youtube_video,
    _smart_truncate,
    URL_CACHE,
    VISITED_URLS,
    SEARCH_CACHE,
    YOUTUBE_CACHE,
)
from src.utils import suppress_discord_embeds
from src.orchestrator import normalize_tool_args


def call_as_model(func, **raw_args):
    """Call a tool the way main.py does for a model tool call (aliases mapped, types coerced)."""
    return func(**normalize_tool_args(func.__name__, func, raw_args))


class TestWebTools(unittest.TestCase):
    """Test suite for web tools (read_website, search_web, read_youtube_video)."""

    def setUp(self):
        URL_CACHE.clear()
        VISITED_URLS.clear()
        SEARCH_CACHE.clear()
        YOUTUBE_CACHE.clear()

    @patch("src.tools.web.cffi_requests.get")
    def test_read_website_jina_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "# Title\n\nSample page content"
        mock_get.return_value = mock_resp

        result = read_website("https://example.com/article")
        self.assertIn("Parsed via Jina AI", result)
        self.assertIn("Sample page content", result)

    @patch("src.tools.web.cffi_requests.get")
    @patch("src.tools.web.cffi_requests.head")
    def test_read_website_fallback_stealth_scraper(self, mock_head, mock_get):
        # Engine 1 (Jina) fails with 429
        jina_resp = MagicMock()
        jina_resp.status_code = 429

        # Engine 2 HEAD returns text/html
        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_head.return_value = head_resp

        # Engine 2 GET returns HTML
        local_resp = MagicMock()
        local_resp.status_code = 200
        local_resp.headers = {"Content-Type": "text/html"}
        local_resp.url = "https://example.com/test"
        local_resp.charset_encoding = "utf-8"
        local_resp.text = "<html><head><title>Test Page</title></head><body><p>Hello world from local</p><a href='/next'>Next link</a></body></html>"

        def get_dispatcher(url, *args, **kwargs):
            if "r.jina.ai" in url:
                return jina_resp
            return local_resp

        mock_get.side_effect = get_dispatcher

        result = read_website("https://example.com/test")
        self.assertIn("Parsed via Local Stealth Scraper", result)
        self.assertIn("Test Page", result)
        self.assertIn("Hello world from local", result)
        self.assertIn("Next link", result)

    def test_ssrf_and_private_network_block(self):
        self.assertIn("Security Block", read_website("http://localhost:8080/secret"))
        self.assertIn("Security Block", read_website("http://127.0.0.1:11434/api/tags"))
        self.assertIn("Security Block", read_website("http://192.168.1.1/admin"))
        self.assertIn("Security Block", read_website("http://169.254.169.254/metadata"))

    @patch("src.tools.web.cffi_requests.get")
    @patch("src.tools.web.cffi_requests.head")
    def test_redirect_to_private_host_blocked(self, mock_head, mock_get):
        jina_resp = MagicMock()
        jina_resp.status_code = 429
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        mock_head.return_value = redirect_resp
        requested = []

        def get_dispatcher(url, *args, **kwargs):
            requested.append(url)
            return jina_resp if "r.jina.ai" in url else redirect_resp

        mock_get.side_effect = get_dispatcher
        result = read_website("https://example.com/redirector")
        self.assertIn("Security Block", result)
        self.assertFalse(any("169.254.169.254" in u and "jina" not in u for u in requested))

    @patch("src.tools.web.cffi_requests.get")
    def test_url_normalization_and_caching(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "Normalized content"
        mock_get.return_value = mock_resp

        # Call with scheme omitted
        res1 = read_website("example.com/doc")
        self.assertIn("Normalized content", res1)
        self.assertEqual(mock_get.call_count, 1)

        # Call same URL again -> should hit cache without calling cffi_requests
        res2 = read_website("https://example.com/doc")
        self.assertIn("previously read in this session", res2)
        self.assertEqual(mock_get.call_count, 1)

    def test_smart_truncation(self):
        text = "Paragraph 1\n\nParagraph 2\n\nParagraph 3"
        truncated = _smart_truncate(text, 25)
        self.assertIn("Paragraph 1", truncated)
        self.assertIn("...[TEXT TRUNCATED DUE TO LENGTH]...", truncated)
        self.assertNotIn("Paragraph 3", truncated)

    @patch("src.tools.web.cffi_requests.get")
    def test_flexible_tool_call_kwargs(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "Content from link"
        mock_get.return_value = mock_resp

        # Model used 'link' instead of 'url'
        res = call_as_model(read_website, link="https://example.com/custom")
        self.assertIn("Content from link", res)

    @patch("src.tools.web.cffi_requests.get")
    @patch("src.tools.web.cffi_requests.head")
    def test_jina_error_text_falls_back_to_local_scraper(self, mock_head, mock_get):
        # Jina returns 200 with error message text
        jina_resp = MagicMock()
        jina_resp.status_code = 200
        jina_resp.text = "Failed to fetch content from the target URL"

        local_resp = MagicMock()
        local_resp.status_code = 200
        local_resp.url = "https://example.com/retry"
        local_resp.headers = {"Content-Type": "text/html"}
        local_resp.charset_encoding = "utf-8"
        local_resp.text = "<html><body><p>Recovered content from local</p></body></html>"

        def get_dispatcher(url, *args, **kwargs):
            if "r.jina.ai" in url:
                return jina_resp
            return local_resp

        mock_get.side_effect = get_dispatcher
        result = read_website("https://example.com/retry")
        self.assertIn("Parsed via Local Stealth Scraper", result)
        self.assertIn("Recovered content from local", result)

    def test_search_web_empty_query(self):
        res = search_web("")
        self.assertIn("Search query is empty", res)
        res_spaces = search_web("   ")
        self.assertIn("Search query is empty", res_spaces)

    @patch("src.tools.web.DDGS")
    def test_search_web_alias_and_prefix_stripping(self, mock_ddgs_cls):
        mock_instance = MagicMock()
        mock_instance.text.return_value = [
            {"title": "RTX 5080 Specs", "href": "https://nvidia.com/specs", "body": "16GB GDDR7"}
        ]
        mock_ddgs_cls.return_value = mock_instance

        # Call with alias 'q' and conversational prefix
        result = call_as_model(search_web, q="search for rtx 5080")
        self.assertIn("RTX 5080 Specs", result)
        self.assertIn("nvidia.com", result)
        # Verify the stripped query was passed to text()
        mock_instance.text.assert_called()
        called_query = mock_instance.text.call_args[0][0]
        self.assertEqual(called_query, "rtx 5080")

    @patch("src.tools.web.DDGS")
    def test_search_web_multi_backend_fallback(self, mock_ddgs_cls):
        mock_instance = MagicMock()

        def text_side_effect(q, **kwargs):
            backend = kwargs.get("backend")
            if backend == "api":
                raise Exception("429 Too Many Requests")
            elif backend == "html":
                return [
                    {"title": "Recovered Result", "href": "https://techpowerup.com/gpu", "body": "Specs details"}
                ]
            return []

        mock_instance.text.side_effect = text_side_effect
        mock_ddgs_cls.return_value = mock_instance

        result = search_web("gpu benchmarks")
        self.assertIn("Recovered Result", result)
        self.assertIn("techpowerup.com", result)

    @patch("src.tools.web.DDGS")
    def test_search_web_news_intent_and_metadata(self, mock_ddgs_cls):
        mock_instance = MagicMock()
        mock_instance.news.return_value = [
            {
                "title": "NVIDIA Keynote Announced",
                "url": "https://reuters.com/tech/nvidia",
                "body": "Jensen Huang announces Blackwell",
                "date": "2026-10-10T14:00:00Z",
                "source": "Reuters",
            }
        ]
        mock_ddgs_cls.return_value = mock_instance

        result = search_web("nvidia latest news")
        self.assertIn("Category: News", result)
        self.assertIn("NVIDIA Keynote Announced", result)
        self.assertIn("Reuters", result)
        self.assertIn("Date: 2026-10-10", result)
        mock_instance.news.assert_called_once()

    @patch("src.tools.web.DDGS")
    def test_search_web_caching(self, mock_ddgs_cls):
        mock_instance = MagicMock()
        mock_instance.text.return_value = [
            {"title": "Cached Title", "href": "https://example.com/item", "body": "Cached body"}
        ]
        mock_ddgs_cls.return_value = mock_instance

        # Call once
        res1 = search_web("quantum computing")
        self.assertIn("Cached Title", res1)
        self.assertEqual(mock_instance.text.call_count, 1)

        # Call again with identical query -> hits SEARCH_CACHE
        res2 = search_web("quantum computing")
        self.assertIn("Cached Title", res2)
        self.assertEqual(mock_instance.text.call_count, 1)

    @patch("src.tools.web.requests.get")
    @patch("src.tools.web.DDGS")
    @patch.dict("os.environ", {"BRAVE_API_KEY": "fake-brave-key"})
    def test_search_web_brave_fallback(self, mock_ddgs_cls, mock_requests_get):
        mock_instance = MagicMock()
        mock_instance.text.return_value = []  # DDGS returns empty
        mock_ddgs_cls.return_value = mock_instance

        brave_resp = MagicMock()
        brave_resp.status_code = 200
        brave_resp.json.return_value = {
            "web": {
                "results": [
                    {
                        "title": "Brave Fallback Result",
                        "url": "https://brave.com/search-result",
                        "description": "Found via Brave index",
                        "page_age": "2026-10-09",
                    }
                ]
            }
        }
        mock_requests_get.return_value = brave_resp

        result = search_web("rare obscure query")
        self.assertIn("Brave Fallback Result", result)
        self.assertIn("Brave Search", result)
        self.assertIn("Date: 2026-10-09", result)

    def test_read_youtube_video_empty_and_invalid(self):
        self.assertIn("Error: YouTube URL is empty", read_youtube_video(""))
        self.assertIn("Failed to extract valid YouTube video ID", read_youtube_video("https://youtube.com/not-a-video"))
        self.assertIn("Failed to extract valid YouTube video ID", read_youtube_video("https://google.com/search"))

    @patch("src.tools.web.YouTubeTranscriptApi")
    @patch("src.tools.web.requests.get")
    def test_read_youtube_video_url_formats_and_aliases(self, mock_requests_get, mock_ytt_cls):
        oembed_resp = MagicMock()
        oembed_resp.status_code = 200
        oembed_resp.json.return_value = {"title": "Test YouTube Video"}
        mock_requests_get.return_value = oembed_resp

        snippet1 = MagicMock()
        snippet1.text = "Hello from transcript"
        mock_ytt_instance = MagicMock()
        mock_ytt_instance.fetch.return_value = [snippet1]
        mock_ytt_cls.return_value = mock_ytt_instance

        # 1. Standard watch URL
        res1 = read_youtube_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("Test YouTube Video", res1)
        self.assertIn("Hello from transcript", res1)

        # 2. youtu.be shortlink
        YOUTUBE_CACHE.clear()
        res2 = read_youtube_video("https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("Test YouTube Video", res2)

        # 3. Shorts URL
        YOUTUBE_CACHE.clear()
        res3 = read_youtube_video("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        self.assertIn("Test YouTube Video", res3)

        # 4. Embed URL
        YOUTUBE_CACHE.clear()
        res4 = read_youtube_video("https://www.youtube.com/embed/dQw4w9WgXcQ")
        self.assertIn("Test YouTube Video", res4)

        # 5. Live stream URL
        YOUTUBE_CACHE.clear()
        res5 = read_youtube_video("https://www.youtube.com/live/dQw4w9WgXcQ")
        self.assertIn("Test YouTube Video", res5)

        # 6. Bare 11-char ID
        YOUTUBE_CACHE.clear()
        res6 = read_youtube_video("dQw4w9WgXcQ")
        self.assertIn("Test YouTube Video", res6)

        # 7. Alias argument
        YOUTUBE_CACHE.clear()
        res7 = call_as_model(read_youtube_video, video_url="<https://youtu.be/dQw4w9WgXcQ>")
        self.assertIn("Test YouTube Video", res7)

    @patch("src.tools.web.YouTubeTranscriptApi")
    @patch("src.tools.web.requests.get")
    def test_read_youtube_video_caching(self, mock_requests_get, mock_ytt_cls):
        oembed_resp = MagicMock()
        oembed_resp.status_code = 200
        oembed_resp.json.return_value = {"title": "Cached Video"}
        mock_requests_get.return_value = oembed_resp

        snippet = MagicMock()
        snippet.text = "Cached transcript line"
        mock_ytt_instance = MagicMock()
        mock_ytt_instance.fetch.return_value = [snippet]
        mock_ytt_cls.return_value = mock_ytt_instance

        # Call once
        res1 = read_youtube_video("https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("Cached transcript line", res1)
        self.assertEqual(mock_ytt_instance.fetch.call_count, 1)

        # Call again -> should hit YOUTUBE_CACHE
        res2 = read_youtube_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("Cached transcript line", res2)
        self.assertEqual(mock_ytt_instance.fetch.call_count, 1)

    @patch("src.tools.web.YouTubeTranscriptApi")
    @patch("src.tools.web.requests.get")
    def test_read_youtube_video_error_handling(self, mock_requests_get, mock_ytt_cls):
        oembed_resp = MagicMock()
        oembed_resp.status_code = 200
        oembed_resp.json.return_value = {"title": "Disabled Video"}
        mock_requests_get.return_value = oembed_resp

        mock_ytt_instance = MagicMock()
        mock_ytt_instance.fetch.side_effect = Exception("TranscriptsDisabled for this video")
        mock_ytt_instance.list.side_effect = Exception("TranscriptsDisabled for this video")
        mock_ytt_cls.return_value = mock_ytt_instance

        res = read_youtube_video("https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("Disabled Video", res)
        self.assertIn("creator disabled transcripts", res)

    def test_suppress_discord_embeds(self):
        sample = (
            "Canada Top Headlines Today:\n"
            "• **National News** - [globalnews.ca](https://globalnews.ca/canada/): Updates.\n"
            "• Direct: https://cbc.ca/news\n"
            "• YouTube Video: https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
            "• YouTube Markdown: [Watch](https://youtu.be/dQw4w9WgXcQ)\n"
            "• Already escaped: [Site](<https://example.com>)\n\n"
            "---\n"
            "Need deeper details on any story? Use read_website to explore specific URLs!"
        )
        cleaned = suppress_discord_embeds(sample)
        # Markdown links should have angle brackets around URL
        self.assertIn("[globalnews.ca](<https://globalnews.ca/canada/>)", cleaned)
        # Raw web URLs should be angle-bracketed
        self.assertIn("<https://cbc.ca/news>", cleaned)
        # YouTube URLs must remain unbracketed so video embeds work
        self.assertIn("https://www.youtube.com/watch?v=dQw4w9WgXcQ", cleaned)
        self.assertNotIn("<https://www.youtube.com/watch?v=dQw4w9WgXcQ>", cleaned)
        self.assertIn("[Watch](https://youtu.be/dQw4w9WgXcQ)", cleaned)
        # Already escaped stays clean
        self.assertIn("[Site](<https://example.com>)", cleaned)
        # Leaked tool instruction footer must be stripped
        self.assertNotIn("read_website", cleaned)
        self.assertNotIn("Need deeper details", cleaned)


if __name__ == "__main__":
    unittest.main()
