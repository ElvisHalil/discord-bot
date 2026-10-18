import sys
import unittest
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.utils.cache import BoundedCache, BoundedSet
from src.utils.dispatcher import extract_song_card_target
from src.utils.http import (
    _is_private_or_local_host,
    BlockedRedirectError,
    ThreadLocalRequestsSession,
    clear_http_caches,
    fetch_url,
    fetch_soup,
    fetch_json,
    fetch_text,
)
from src.skills.music.aggregators.beatport import BeatportAggregator


class TestHttpClient(unittest.TestCase):
    """Test suite for centralized stealth HTTP client and SSRF protection."""

    def test_ssrf_host_detection(self):
        # Localhost and loopback (IPv4 and IPv6)
        self.assertTrue(_is_private_or_local_host("localhost"))
        self.assertTrue(_is_private_or_local_host("127.0.0.1"))
        self.assertTrue(_is_private_or_local_host("127.0.0.2"))
        self.assertTrue(_is_private_or_local_host("::1"))
        self.assertTrue(_is_private_or_local_host("fe80::1"))
        self.assertTrue(_is_private_or_local_host("::ffff:127.0.0.1"))
        self.assertTrue(_is_private_or_local_host("0.0.0.0"))

        # Private / link-local / cloud metadata ranges
        self.assertTrue(_is_private_or_local_host("10.0.0.1"))
        self.assertTrue(_is_private_or_local_host("172.16.0.1"))
        self.assertTrue(_is_private_or_local_host("192.168.1.1"))
        self.assertTrue(_is_private_or_local_host("169.254.169.254"))

        # Local domain suffixes
        self.assertTrue(_is_private_or_local_host("router.local"))
        self.assertTrue(_is_private_or_local_host("service.internal"))
        self.assertTrue(_is_private_or_local_host("nas.lan"))
        self.assertTrue(_is_private_or_local_host(""))

        # Public domains allowed
        self.assertFalse(_is_private_or_local_host("example.com"))
        self.assertFalse(_is_private_or_local_host("google.com"))
        self.assertFalse(_is_private_or_local_host("api.deezer.com"))

    @patch("src.utils.http.socket.getaddrinfo")
    def test_ssrf_dns_ipv6_rebind_blocked(self, mock_getaddrinfo):
        clear_http_caches()
        mock_getaddrinfo.return_value = [
            (10, 1, 6, "", ("::1", 80, 0, 0)),
        ]
        self.assertTrue(_is_private_or_local_host("evil-ipv6-rebind.example.org"))
        clear_http_caches()

    def test_bounded_cache_and_set_eviction(self):
        cache = BoundedCache(maxsize=3)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3
        # Touch "a" so "b" becomes least-recently-used
        _ = cache.get("a")
        cache["d"] = 4
        self.assertEqual(list(cache.keys()), ["c", "a", "d"])
        self.assertNotIn("b", cache)

        bset = BoundedSet(maxsize=2)
        bset.add("x")
        bset.add("y")
        bset.add("z")
        self.assertEqual(len(bset), 2)
        self.assertNotIn("x", bset)
        self.assertIn("y", bset)
        self.assertIn("z", bset)

    def test_thread_local_requests_session_isolation(self):
        import threading

        tls_session = ThreadLocalRequestsSession(headers={"User-Agent": "TestAgent"})
        sessions = []

        def _worker():
            sessions.append(tls_session._get_session())

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(len(sessions), 2)
        self.assertIsNot(sessions[0], sessions[1])
        self.assertEqual(sessions[0].headers.get("User-Agent"), "TestAgent")

    def test_extract_song_card_target(self):
        card = (
            "**Song:** Midnight City (Official Video)\n"
            "**Artist:** M83\n"
            "**Genre:** Synthpop\n"
            "https://music.youtube.com/watch?v=dX3k_QDnzHE"
        )
        parsed = extract_song_card_target(card)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["clean_title"], "Midnight City")
        self.assertEqual(parsed["orig_artist"], "M83")
        self.assertEqual(parsed["search_query"], "Midnight City by M83")
        self.assertEqual(parsed["video_id"], "dX3k_QDnzHE")

    def test_direct_private_target_blocked(self):
        with self.assertRaises(BlockedRedirectError):
            fetch_url("http://127.0.0.1:8000/api")

        with self.assertRaises(BlockedRedirectError):
            fetch_url("http://localhost:5000")

    def test_unsupported_scheme_rejected(self):
        with self.assertRaises(ValueError):
            fetch_url("ftp://example.com/file")
        with self.assertRaises(ValueError):
            fetch_url("file:///etc/passwd")

    @patch("src.utils.http.cffi_requests.get")
    def test_redirect_to_private_target_blocked(self, mock_get):
        resp_302 = MagicMock()
        resp_302.status_code = 302
        resp_302.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        mock_get.return_value = resp_302

        with self.assertRaises(BlockedRedirectError):
            fetch_url("https://example.com/redirector")

    @patch("src.utils.http.cffi_requests.get")
    def test_fetch_soup_parses_html(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><h1>Hello World</h1><p>Test</p></body></html>"
        mock_resp.charset_encoding = "utf-8"
        mock_get.return_value = mock_resp

        soup = fetch_soup("https://example.com")
        self.assertEqual(soup.h1.string, "Hello World")
        self.assertEqual(soup.p.string, "Test")

    @patch("src.utils.http.cffi_requests.get")
    def test_fetch_json_parses_dict(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "items": [1, 2, 3]}
        mock_get.return_value = mock_resp

        data = fetch_json("https://example.com/api")
        self.assertEqual(data["status"], "ok")
        self.assertEqual(len(data["items"]), 3)

    @patch("src.utils.http.cffi_requests.get")
    def test_fetch_text_returns_string(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "sunny +20C"
        mock_get.return_value = mock_resp

        text = fetch_text("https://wttr.in/Paris")
        self.assertEqual(text, "sunny +20C")


class TestBeatportGracefulFallback(unittest.TestCase):
    """Test that BeatportAggregator resolves Beatport charts directly via Deezer playlists."""

    @patch("src.skills.music.aggregators.deezer.DeezerAggregator.get_charts")
    def test_beatport_falls_back_to_deezer_on_error(self, mock_deezer_charts):
        from src.skills.music.models import Track
        mock_deezer_charts.return_value = [
            Track(title="Strobe", artist="Deadmau5", genre="Electronic", album="For Lack of A Better Name", source="deezer_charts")
        ]

        agg = BeatportAggregator()
        tracks = agg.get_charts()
        mock_deezer_charts.assert_called_once_with(genre="Beatport Top 100")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Strobe")
        self.assertEqual(tracks[0].artist, "Deadmau5")
        self.assertEqual(tracks[0].source, "beatport_fallback_deezer")


if __name__ == "__main__":
    unittest.main()



