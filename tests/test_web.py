import sys
import unittest
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.tools.web import read_website


class TestWebTools(unittest.TestCase):
    """Test suite for read_website dual-engine parsing."""

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


if __name__ == "__main__":
    unittest.main()
