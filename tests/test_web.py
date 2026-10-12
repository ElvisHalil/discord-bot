import sys
import unittest
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.tools.web import read_website, _smart_truncate, URL_CACHE, VISITED_URLS


class TestWebTools(unittest.TestCase):
    """Test suite for read_website dual-engine parsing."""

    def setUp(self):
        URL_CACHE.clear()
        VISITED_URLS.clear()

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


if __name__ == "__main__":
    unittest.main()
