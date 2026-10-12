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
    _smart_truncate,
    URL_CACHE,
    VISITED_URLS,
    SEARCH_CACHE,
)


class TestWebTools(unittest.TestCase):
    """Test suite for read_website and search_web tools."""

    def setUp(self):
        URL_CACHE.clear()
        VISITED_URLS.clear()
        SEARCH_CACHE.clear()

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
        res = read_website(link="https://example.com/custom")
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
        result = search_web(q="search for rtx 5080")
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


if __name__ == "__main__":
    unittest.main()
