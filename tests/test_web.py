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
    search_images,
    detect_missed_image_tool_call,
    enrich_image_tool_args,
    clear_image_channel_state,
    _clean_image_query,
    _prepare_image_bytes,
    read_youtube_video,
    _smart_truncate,
    URL_CACHE,
    VISITED_URLS,
    SEARCH_CACHE,
    YOUTUBE_CACHE,
)
from src.context import (
    clear_channel_attachments,
    pop_channel_attachments,
    set_active_channel_id,
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
        clear_image_channel_state()

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

    @patch("src.tools.web.YouTubeTranscriptApi")
    @patch("src.tools.web.requests.get")
    def test_read_youtube_video_long_form_chapter_digest_and_query_zoom(self, mock_requests_get, mock_ytt_cls):
        from src.tools.web import enrich_youtube_tool_args
        from src.utils.timezones import enrich_tool_output_with_time_context

        oembed_resp = MagicMock()
        oembed_resp.status_code = 200
        oembed_resp.json.return_value = {"title": "2-Hour Deep Tech & Macro Podcast"}
        mock_requests_get.return_value = oembed_resp

        # Build a realistic 60-minute (120 snippets x 30s = 3600s, ~45,000 chars) long-form transcript
        snippets = []
        for idx in range(120):
            start_s = float(idx * 30)
            if idx == 0:
                txt = (
                    "Welcome everyone to the opening chapter of our two hour deep dive where we introduce "
                    "semiconductor lithography roadmaps and global foundry capital expenditure trends for sixty words "
                    + ("foundry wafer capacity allocation " * 12)
                )
            elif idx == 60:
                # 30:00 mark (50% through the video — previously completely truncated beyond 10k/18k chars!)
                txt = (
                    "In the middle of the episode at thirty minutes we examine quantum error correction breakthroughs "
                    "achieving 99.94% logical qubit fidelity with superconducting surface codes and $4.2 billion R&D "
                    + ("logical qubit surface code threshold " * 10)
                )
            elif idx == 90:
                # 45:00 mark (75% through the video)
                txt = (
                    "At the forty five minute mark our focus shifts to sovereign bond auctions and why 10-year yields "
                    "hit 4.65% after the central bank balance sheet runoff accelerated by $60 billion per month "
                    + ("treasury auction term premium yield " * 10)
                )
            elif idx == 119:
                # 59:30 mark (99%-100% final conclusion at the very end of the video!)
                txt = (
                    "In conclusion our final verdict at the end of this podcast is that optical interconnects "
                    "will replace copper backplanes by 2028 with a 35% power efficiency gain across data centers "
                    + ("final closing takeaway optical interconnect " * 10)
                )
            else:
                txt = (
                    f"Segment {idx} general discussion covering hardware architecture pipeline stage {idx} "
                    + ("system throughput latency benchmark analysis " * 12)
                )
            snippets.append({"text": txt.strip(), "start": start_s, "duration": 30.0})

        mock_ytt_instance = MagicMock()
        mock_ytt_instance.fetch.return_value = snippets
        mock_ytt_cls.return_value = mock_ytt_instance

        # 1. Default long-video call: must cover 0%–100% across all chapters AND survive enrich_tool_output_with_time_context
        YOUTUBE_CACHE.clear()
        res_default = read_youtube_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        enriched_out = enrich_tool_output_with_time_context(res_default)

        self.assertIn("Long-Form Video Coverage", enriched_out)
        self.assertIn("FULL-TIMELINE CHRONOLOGICAL CHAPTER BREAKDOWN (0%–100%)", enriched_out)
        self.assertIn("Chapter 1/8", enriched_out)
        self.assertIn("Chapter 8/8", enriched_out)
        # Opening, midpoint (30:00), 45:00 mark, and final closing verdict (59:30) must ALL be present!
        self.assertIn("semiconductor lithography roadmaps", enriched_out)
        self.assertIn("quantum error correction breakthroughs", enriched_out)
        self.assertIn("sovereign bond auctions", enriched_out)
        self.assertIn("optical interconnects will replace copper backplanes by 2028", enriched_out)
        self.assertNotIn("...[tool output truncated to fit context window]...", enriched_out)

        # 2. Follow-up call with a topic/timestamp query: must reuse cached raw transcript (fetch count stays 1)
        # and prepend TARGETED VERBATIM EXCERPTS for the requested topic/timestamp!
        res_query = call_as_model(
            read_youtube_video,
            video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            topic="45:00 sovereign bond auctions",
        )
        self.assertEqual(mock_ytt_instance.fetch.call_count, 1)
        self.assertIn("TARGETED VERBATIM EXCERPTS (Query: '45:00 sovereign bond auctions')", res_query)
        self.assertIn("sovereign bond auctions", res_query)
        self.assertIn("45:00", res_query)

        # 3. Verify enrich_youtube_tool_args keeps generic summary prompts with query=None,
        # but auto-populates `query` and recovers prior URL when user asks a specific question
        generic_hist = [{"role": "user", "content": "Can you summarize this YouTube video https://youtu.be/dQw4w9WgXcQ"}]
        gen_args = enrich_youtube_tool_args({"url": "https://youtu.be/dQw4w9WgXcQ"}, generic_hist)
        self.assertIsNone(gen_args.get("query"))

        specific_hist = [
            {"role": "user", "content": "Check https://youtu.be/dQw4w9WgXcQ"},
            {"role": "assistant", "content": "Here is the summary."},
            {"role": "user", "content": "What did they say at 45:00 about sovereign bond auctions?"},
        ]
        spec_args = enrich_youtube_tool_args({}, specific_hist)
        self.assertEqual(spec_args.get("url"), "https://youtu.be/dQw4w9WgXcQ")
        self.assertIn("45:00", spec_args.get("query", ""))
        self.assertIn("sovereign bond auctions", spec_args.get("query", ""))

    @patch("src.tools.web.YouTubeTranscriptApi")
    @patch("src.tools.web.requests.get")
    def test_read_youtube_video_short_medium_and_extreme_10h_safety_and_concurrency(
        self, mock_requests_get, mock_ytt_cls
    ):
        import threading
        import time
        from src.tools.web import _MAX_TRANSCRIPT_SNIPPETS, _YOUTUBE_RAW_CACHE

        observed_threads: list[str] = []
        t_lock = threading.Lock()

        def fake_oembed(*args, **kwargs):
            with t_lock:
                observed_threads.append(threading.current_thread().name)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"title": "Tiered Length & Concurrency Video"}
            return resp

        mock_requests_get.side_effect = fake_oembed
        mock_ytt_instance = MagicMock()
        mock_ytt_cls.return_value = mock_ytt_instance

        # 1. SHORT VIDEO (< 60s): plain verbatim transcript without unnecessary [00:00] markers
        YOUTUBE_CACHE.clear()
        _YOUTUBE_RAW_CACHE.clear()
        mock_ytt_instance.fetch.return_value = [
            {"text": "Quick thirty second clip intro.", "start": 0.0, "duration": 15.0},
            {"text": "And here is the punchline right away.", "start": 15.0, "duration": 15.0},
        ]
        short_out = read_youtube_video("https://youtu.be/shortVideo1")
        self.assertIn("Quick thirty second clip intro. And here is the punchline right away.", short_out)
        self.assertNotIn("[00:00]", short_out)
        self.assertNotIn("FULL-TIMELINE CHRONOLOGICAL CHAPTER BREAKDOWN", short_out)
        # Verify oEmbed ran on a daemon worker thread via submit_to_worker
        self.assertTrue(any(name.startswith(("BotWorker", "BotSubWorker", "BotLeafWorker")) for name in observed_threads))

        # 2. MEDIUM VIDEO (4 minutes, ~1,800 chars <= 12,500 limit): 100% verbatim with periodic [MM:SS] markers every ~60s
        YOUTUBE_CACHE.clear()
        _YOUTUBE_RAW_CACHE.clear()
        medium_snippets = [
            {
                "text": f"Minute {m} segment {s} verbatim explanation of distributed consensus protocols.",
                "start": float(m * 60 + s * 20),
                "duration": 20.0,
            }
            for m in range(4)
            for s in range(3)
        ]
        mock_ytt_instance.fetch.return_value = medium_snippets
        med_out = read_youtube_video("https://youtu.be/medVideo123")
        self.assertNotIn("FULL-TIMELINE CHRONOLOGICAL CHAPTER BREAKDOWN", med_out)
        self.assertIn("[00:00]", med_out)
        self.assertIn("[01:00]", med_out)
        self.assertIn("[02:00]", med_out)
        self.assertIn("[03:00]", med_out)
        self.assertIn("Minute 0 segment 0 verbatim explanation", med_out)
        self.assertIn("Minute 3 segment 2 verbatim explanation", med_out)

        # 3. EXTREME 10-HOUR LIVESTREAM ARCHIVE (18,000 snippets = 36,000s = 10h00m, > 900,000 chars):
        # Must stride-sample down to _MAX_TRANSCRIPT_SNIPPETS (6,000), cap micro-windows at <= 400,
        # complete in < 0.5s, and preserve 0% (00:00) through 100% (10:00:00) across 10 chapters.
        YOUTUBE_CACHE.clear()
        _YOUTUBE_RAW_CACHE.clear()
        extreme_snippets = []
        for idx in range(18000):
            start_s = float(idx * 2)
            if idx == 0:
                txt = "Opening hour zero kickoff of the ten hour endurance engineering livestream."
            elif idx == 9000:
                txt = "Midstream five hour milestone reviewing fusion plasma confinement at 150 million degrees."
            elif idx == 17999:
                txt = "In conclusion at hour ten our final closing verdict is that the marathon test succeeded 100%."
            else:
                txt = f"Stream tick {idx} telemetry check nominal."
            extreme_snippets.append({"text": txt, "start": start_s, "duration": 2.0})

        mock_ytt_instance.fetch.return_value = extreme_snippets
        t0 = time.perf_counter()
        extreme_out = read_youtube_video("https://youtu.be/extreme10hr")
        elapsed = time.perf_counter() - t0

        self.assertLess(elapsed, 0.8, f"Extreme 10h transcript took too long: {elapsed:.3f}s")
        self.assertEqual(len(_YOUTUBE_RAW_CACHE["extreme10hr"]["segments"]), _MAX_TRANSCRIPT_SNIPPETS)
        self.assertIn("10:00:00 duration", extreme_out)
        self.assertIn("Chapter 1/10", extreme_out)
        self.assertIn("Chapter 10/10", extreme_out)
        self.assertIn("Opening hour zero kickoff of the ten hour endurance engineering livestream.", extreme_out)
        self.assertIn("In conclusion at hour ten our final closing verdict is that the marathon test succeeded 100%.", extreme_out)
        self.assertLessEqual(len(extreme_out), 14000)

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

    def test_clean_image_query_and_missed_tool_detection(self):
        self.assertEqual(_clean_image_query("get a picture of a capybara"), "capybara")
        self.assertEqual(
            _clean_image_query("Can you show me a high-res photo of the James Webb Space Telescope please?"),
            "the James Webb Space Telescope",
        )
        self.assertEqual(_clean_image_query("send me an image of 1995 Porsche 911 GT2"), "1995 Porsche 911 GT2")

        # Explicit image requests trigger detect_missed_image_tool_call
        missed = detect_missed_image_tool_call([
            {"role": "user", "content": "can you get me a picture of a snow leopard?"}
        ])
        self.assertEqual(missed, ("search_images", {"query": "snow leopard"}))

        missed_photo = detect_missed_image_tool_call([
            {"role": "user", "content": "show me a photo of the Northern Lights"}
        ])
        self.assertEqual(missed_photo, ("search_images", {"query": "the Northern Lights"}))

        # Chart / graph / diagram prompts MUST NOT collide with search_images
        self.assertIsNone(detect_missed_image_tool_call([
            {"role": "user", "content": "show me a chart of BTC price history"}
        ]))
        self.assertIsNone(detect_missed_image_tool_call([
            {"role": "user", "content": "get a graph of DXY vs Gold"}
        ]))

    def test_prepare_image_bytes_in_memory_validation_and_webp_conversion(self):
        import io
        from PIL import Image

        # 1. Non-image HTML or tiny tracking pixel is rejected
        self.assertIsNone(_prepare_image_bytes(b"<html><body>403 Forbidden</body></html>" * 30))
        tiny_buf = io.BytesIO()
        Image.new("RGB", (16, 16), color=(255, 0, 0)).save(tiny_buf, format="PNG")
        self.assertIsNone(_prepare_image_bytes(tiny_buf.getvalue()))

        # 2. Valid in-memory WEBP image is converted in RAM to standard JPEG for Discord/Telegram & Ollama vision
        webp_buf = io.BytesIO()
        Image.new("RGB", (320, 240), color=(20, 120, 220)).save(webp_buf, format="WEBP", quality=95)
        prepared = _prepare_image_bytes(webp_buf.getvalue())
        self.assertIsNotNone(prepared)
        attach_bytes, vision_bytes, ext = prepared
        self.assertEqual(ext, ".jpg")
        self.assertTrue(attach_bytes.startswith(b"\xff\xd8\xff"))
        self.assertTrue(vision_bytes.startswith(b"\xff\xd8\xff"))

    @patch("src.tools.web.ollama.chat")
    @patch("src.tools.web._fetch_public")
    @patch("src.tools.web.DDGS")
    def test_search_images_vision_verification_and_in_memory_attachment(
        self, mock_ddgs_cls, mock_fetch_public, mock_ollama_chat
    ):
        import io
        from PIL import Image

        set_active_channel_id(987654321)
        clear_channel_attachments(987654321)

        # Build two valid in-memory images (zero disk writes)
        img1_buf = io.BytesIO()
        Image.new("RGB", (300, 300), color=(200, 50, 50)).save(img1_buf, format="JPEG", quality=92)
        img1_bytes = img1_buf.getvalue()

        img2_buf = io.BytesIO()
        Image.new("RGB", (400, 300), color=(60, 180, 90)).save(img2_buf, format="PNG")
        img2_bytes = img2_buf.getvalue()

        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.images.return_value = [
            {
                "title": "Unrelated Red Car",
                "image": "https://blocked.example.com/car.jpg",
                "thumbnail": "https://tse1.mm.bing.net/th?id=OIP.car123",
                "url": "https://example.com/car-page",
                "width": 800,
                "height": 600,
            },
            {
                "title": "Capybara Relaxing in Hot Spring",
                "image": "https://wildlife.example.org/capybara.png",
                "thumbnail": "https://tse2.mm.bing.net/th?id=OIP.capy456",
                "url": "https://wildlife.example.org/capybara-article",
                "width": 1024,
                "height": 768,
            },
        ]
        mock_ddgs_cls.return_value = mock_ddgs_instance

        # Candidate #1 primary URL returns 403, falls back to thumbnail (img1_bytes)
        # Candidate #2 primary URL returns 200 (img2_bytes)
        def fetch_side_effect(method, url, **kwargs):
            resp = MagicMock()
            if "blocked.example.com" in url:
                resp.status_code = 403
                resp.content = b"Forbidden"
            elif "OIP.car123" in url:
                resp.status_code = 200
                resp.content = img1_bytes
            elif "capybara.png" in url:
                resp.status_code = 200
                resp.content = img2_bytes
            else:
                resp.status_code = 404
                resp.content = b""
            return resp

        mock_fetch_public.side_effect = fetch_side_effect

        # Vision model (gemma4:e4b) rejects Candidate #1 ("NO") and verifies Candidate #2 ("YES")
        vis_reject = MagicMock()
        vis_reject.message.content = "NO. This image shows a red sports car, not a capybara."
        vis_accept = MagicMock()
        vis_accept.message.content = "YES. The image clearly depicts a capybara relaxing in water."
        mock_ollama_chat.side_effect = [vis_reject, vis_accept]

        out = call_as_model(search_images, picture="get a picture of a capybara")
        self.assertIn("[IMAGE VERIFIED & ATTACHED IN MEMORY]", out)
        self.assertIn("capybara.png", out)
        self.assertIn("Capybara Relaxing in Hot Spring", out)
        self.assertIn("clearly depicts a capybara", out)
        self.assertEqual(mock_ollama_chat.call_count, 2)

        # Verify the exact verified PNG bytes are queued in memory for channel 987654321 and popped cleanly
        queued = pop_channel_attachments(987654321)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["filename"], "capybara.png")
        self.assertEqual(queued[0]["data"], img2_bytes)
        self.assertEqual(pop_channel_attachments(987654321), [])

        # Follow-up Turn: user asks "get a different picture" or "show me another picture of the same thing"
        # 1. detect_missed_image_tool_call & enrich_image_tool_args recover 'capybara' from channel 987654321
        followup_missed = detect_missed_image_tool_call(
            [{"role": "user", "content": "can you get a different picture?"}],
            active_channel_id=987654321,
        )
        self.assertEqual(followup_missed, ("search_images", {"query": "capybara"}))

        enriched = enrich_image_tool_args(
            {"query": "different picture of the same thing"},
            [{"role": "user", "content": "get a different picture of the same thing"}],
            active_channel_id=987654321,
        )
        self.assertEqual(enriched["query"], "capybara")

        # 2. Calling search_images again in the same channel automatically skips the already-sent
        #    Candidate #2 ('capybara.png') and selects Candidate #3 ('capybara_swimming.jpg')
        img3_buf = io.BytesIO()
        Image.new("RGB", (360, 260), color=(30, 90, 210)).save(img3_buf, format="JPEG", quality=92)
        img3_bytes = img3_buf.getvalue()

        mock_ddgs_instance.images.return_value = [
            {
                "title": "Capybara Relaxing in Hot Spring",
                "image": "https://wildlife.example.org/capybara.png",
                "thumbnail": "https://tse2.mm.bing.net/th?id=OIP.capy456",
                "url": "https://wildlife.example.org/capybara-article",
                "width": 1024,
                "height": 768,
            },
            {
                "title": "Capybara Swimming in River",
                "image": "https://wildlife.example.org/capybara_swimming.jpg",
                "thumbnail": "https://tse3.mm.bing.net/th?id=OIP.capy789",
                "url": "https://wildlife.example.org/capybara-river",
                "width": 1200,
                "height": 800,
            },
        ]

        def fetch_side_effect_turn2(method, url, **kwargs):
            resp = MagicMock()
            if "capy789" in url or "capybara_swimming.jpg" in url:
                resp.status_code = 200
                resp.content = img3_bytes
            else:
                resp.status_code = 200
                resp.content = img2_bytes
            return resp

        mock_fetch_public.side_effect = fetch_side_effect_turn2
        vis_accept_2 = MagicMock()
        vis_accept_2.message.content = "YES. A capybara swimming in a river."
        mock_ollama_chat.side_effect = [vis_accept_2]

        out2 = search_images("get a different picture")
        self.assertIn("Capybara Swimming in River", out2)
        queued2 = pop_channel_attachments(987654321)
        self.assertEqual(len(queued2), 1)
        self.assertEqual(queued2[0]["filename"], "capybara.jpg")
        self.assertEqual(queued2[0]["data"], img3_bytes)

    @patch("src.tools.web.ollama.chat")
    @patch("src.tools.web._fetch_public")
    def test_webpage_targeted_image_extraction_and_multi_image_count_guard(
        self, mock_fetch_public, mock_ollama_chat
    ):
        import io
        from PIL import Image

        set_active_channel_id(112233)
        clear_channel_attachments(112233)
        clear_image_channel_state(112233)

        hero_buf = io.BytesIO()
        Image.new("RGB", (600, 400), color=(40, 40, 180)).save(hero_buf, format="JPEG", quality=90)
        hero_bytes = hero_buf.getvalue()

        wing_buf = io.BytesIO()
        Image.new("RGB", (500, 350), color=(220, 80, 30)).save(wing_buf, format="PNG")
        wing_bytes = wing_buf.getvalue()

        page_html = """
        <html>
          <head>
            <title>2026 F1 Technical Analysis</title>
            <meta property="og:image" content="https://f1tech.example.com/images/full_car_hero.jpg" />
          </head>
          <body>
            <nav><img src="/assets/logo.svg" width="120" height="40" /></nav>
            <article>
              <h2>Aerodynamic Upgrades</h2>
              <figure>
                <img srcset="/images/rear_wing_sm.png 480w, /images/rear_wing_hd.png 1200w" alt="Carbon Rear Wing Assembly" />
                <figcaption>Detailed view of the new low-drag rear wing.</figcaption>
              </figure>
              <img src="/images/pixel.gif" width="1" height="1" />
            </article>
          </body>
        </html>
        """

        def fetch_side_effect(method, url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.url = url
            resp.charset_encoding = "utf-8"
            if url == "https://f1tech.example.com/review":
                resp.text = page_html
                resp.content = page_html.encode("utf-8")
            elif "rear_wing_hd.png" in url:
                resp.content = wing_bytes
            elif "full_car_hero.jpg" in url:
                resp.content = hero_bytes
            else:
                resp.status_code = 404
                resp.content = b""
            return resp

        mock_fetch_public.side_effect = fetch_side_effect
        vis_ok = MagicMock()
        vis_ok.message.content = "YES. Shows the carbon fiber rear wing assembly."
        mock_ollama_chat.return_value = vis_ok

        # 1. Targeted image from webpage: 'rear wing' ranks the <figure> srcset 1200w image first
        res_wing = call_as_model(
            search_images,
            query="get the picture of the rear wing",
            link="https://f1tech.example.com/review",
        )
        self.assertIn("[IMAGE VERIFIED & ATTACHED IN MEMORY]", res_wing)
        self.assertIn("Carbon Rear Wing Assembly", res_wing)
        atts = pop_channel_attachments(112233)
        self.assertEqual(len(atts), 1)
        self.assertEqual(atts[0]["data"], wing_bytes)

        # 2. Guardrail: single-picture prompt locks count=1 even if model hallucinated count=3
        single_enriched = enrich_image_tool_args(
            {"query": "rear wing", "count": 3},
            [{"role": "user", "content": "get a picture of the rear wing from that article"}],
            active_channel_id=112233,
        )
        self.assertEqual(single_enriched["count"], 1)
        self.assertEqual(single_enriched["url"], "https://f1tech.example.com/review")

        # 3. Explicit multi-image prompt ('get 2 pictures from that page') sets count=2
        multi_enriched = enrich_image_tool_args(
            {"query": "car photos"},
            [{"role": "user", "content": "get 2 pictures from that article"}],
            active_channel_id=112233,
        )
        self.assertEqual(multi_enriched["count"], 2)
        self.assertEqual(multi_enriched["url"], "https://f1tech.example.com/review")

        clear_image_channel_state(112233)
        res_multi = search_images(query="F1 car", url="https://f1tech.example.com/review", count=2)
        self.assertIn("2 images", res_multi)
        atts_multi = pop_channel_attachments(112233)
        self.assertEqual(len(atts_multi), 2)


if __name__ == "__main__":
    unittest.main()
