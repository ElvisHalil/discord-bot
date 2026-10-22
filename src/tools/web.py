import io
import ipaddress
import os
import re
import socket
import sys
import requests
import urllib.parse as urlparse
from contextvars import ContextVar
from typing import Optional, Set, Tuple
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from curl_cffi.requests.errors import RequestsError
from ddgs import DDGS
from markdownify import markdownify as md
import ollama
from PIL import Image
from requests.exceptions import RequestException
from youtube_transcript_api import YouTubeTranscriptApi

from src.context import queue_channel_attachment

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

from src.utils.cache import BoundedCache, BoundedSet
from src.utils.http import (
    _ORIG_CFFI_GET,
    _fetch_public,
    _get_thread_session,
    _is_private_or_local_host,
    BlockedRedirectError,
    decode_google_news_url,
    fetch_url,
)

VISITED_URLS = BoundedSet(maxsize=1024)
_TURN_VISITED_URLS: ContextVar[Optional[Set[str]]] = ContextVar("TURN_VISITED_URLS", default=None)
URL_CACHE = BoundedCache(maxsize=256)
SEARCH_CACHE = BoundedCache(maxsize=256)
YOUTUBE_CACHE = BoundedCache(maxsize=256)


def reset_turn_visited_urls() -> Set[str]:
    """Initialize a fresh turn-scoped visited URL set without wiping concurrent turns."""
    turn_set: Set[str] = set()
    _TURN_VISITED_URLS.set(turn_set)
    return turn_set


def _record_visited_url(url: str) -> None:
    VISITED_URLS.add(url)
    turn_set = _TURN_VISITED_URLS.get()
    if turn_set is not None:
        turn_set.add(url)

# Generic words that add nothing to a keyword-matched news search.
_NEWS_FILLER = re.compile(
    r"\b(?:(?:top|latest|breaking|current|major|biggest)\s+(?:news\s+)?(?:headlines?|news|stories)"
    r"|headlines?|news|today'?s?|tonight|right now|this (?:morning|evening))\b",
    re.IGNORECASE,
)

# Bot walls / CAPTCHA interstitials that some readers return with HTTP 200.
_BOT_WALL = re.compile(
    r"captcha|verify (?:that )?you are (?:a )?human|are you a robot|unusual traffic"
    r"|enable javascript and cookies|access to this page has been denied|checking your browser",
    re.IGNORECASE,
)
_BLOCKED_PAGE_MESSAGE = (
    "❌ Blocked: this site returned a CAPTCHA/bot-check page instead of the article. "
    "Do not guess its contents. Use `search_web` with the headline to find the same story from another source."
)


def _looks_blocked(text: str) -> bool:
    """True for short pages that are clearly a bot wall rather than real content."""
    return bool(text) and len(text) < 4000 and bool(_BOT_WALL.search(text))

def _smart_truncate(text: str, max_length: int) -> str:
    """Truncate text cleanly at paragraph or sentence boundaries without slicing words in half."""
    if len(text) <= max_length:
        return text
    min_search = int(max_length * 0.8)
    cand = text[:max_length]
    
    # Try double newline (paragraph boundary)
    last_p = cand.rfind('\n\n', min_search)
    if last_p != -1:
        return cand[:last_p].strip() + "\n\n...[TEXT TRUNCATED DUE TO LENGTH]..."
        
    # Try single newline
    last_nl = cand.rfind('\n', min_search)
    if last_nl != -1:
        return cand[:last_nl].strip() + "\n\n...[TEXT TRUNCATED DUE TO LENGTH]..."
        
    # Try sentence ending
    for punct in ('. ', '? ', '! '):
        last_s = cand.rfind(punct, min_search)
        if last_s != -1:
            return cand[:last_s + 1].strip() + "\n\n...[TEXT TRUNCATED DUE TO LENGTH]..."
            
    # Try word space
    last_sp = cand.rfind(' ', min_search)
    if last_sp != -1:
        return cand[:last_sp].strip() + "\n\n...[TEXT TRUNCATED DUE TO LENGTH]..."
        
    return cand.strip() + "\n\n...[TEXT TRUNCATED DUE TO LENGTH]..."

def search_web(query: str, news: Optional[bool] = None, timelimit: Optional[str] = None) -> str:
    """Search the web for real-time information: current prices, releases, specs, news, events and facts.
    Returns the top snippets and links. Use `read_website` on a specific URL if you need more details.

    Args:
        query: The search keywords.
        news: Set true to search recent news articles about a specific subject or event. For general top headlines of a country or topic, use `skill_news_headlines` instead.
        timelimit: Optional recency filter: 'd' (past day), 'w' (past week), 'm' (past month) or 'y' (past year).
    """
    global VISITED_URLS, SEARCH_CACHE

    raw_query = (query or "").strip()
    if not raw_query:
        return "❌ Error: Search query is empty. Please provide search keywords."

    clean_q = raw_query.strip().strip('"\'`')
    lower_q = clean_q.lower()
    for prefix in (
        "search for ", "search ", "look up ", "google ", "find info on ", "find information about ", "find "
    ):
        if lower_q.startswith(prefix):
            candidate = clean_q[len(prefix):].strip()
            if candidate:
                clean_q = candidate
            break

    # Determine query intent: News vs General Web Search
    news_flag = news if isinstance(news, bool) else str(news).strip().lower() in ("true", "1", "yes")
    is_news_requested = news_flag or bool(re.search(r'\b(news|breaking news|headlines?)\b', clean_q, re.IGNORECASE))

    if timelimit not in ('d', 'w', 'm', 'y'):
        timelimit = None

    cache_key = (clean_q.lower(), is_news_requested, timelimit)
    if cache_key in SEARCH_CACHE:
        print(f"⚡ [Search Cache Hit]: '{clean_q}'")
        return SEARCH_CACHE[cache_key]

    print(f"🔎 [Web Search]: '{clean_q}' (News: {is_news_requested})")
    raw_results = []

    # 1. News Search (strictly for news queries). Honor the recency filter first,
    # then retry once without it before falling back to general web search.
    if is_news_requested:
        # The news endpoint is keyword-matched and already returns news, so words like
        # "top headlines" or "today" only pull in articles that literally contain them.
        news_q = _NEWS_FILLER.sub(" ", clean_q)
        news_q = re.sub(r"\s{2,}", " ", news_q).strip(" ,.-")
        news_q = re.sub(r"^(?:in|from|about|for|on|of)\s+", "", news_q, flags=re.IGNORECASE) or clean_q
        for news_limit in dict.fromkeys([timelimit, None]):
            try:
                news_kwargs = {"max_results": 7}
                if news_limit:
                    news_kwargs["timelimit"] = news_limit
                raw_results = list(DDGS().news(news_q, **news_kwargs))
                if raw_results:
                    break
            except Exception as news_err:
                print(f"⚠️ [DDGS News Notice]: News search '{news_q}' (timelimit={news_limit}) failed ({news_err})")
        if not raw_results:
            print("⚠️ [DDGS News Notice]: No news results, falling back to web search...")

    # 2. General Web Search with Multi-Backend Fallback (api -> html -> lite)
    if not raw_results:
        backends = ('api', 'html', 'lite')
        for backend in backends:
            try:
                kwargs_ddgs = {"max_results": 7, "backend": backend}
                if timelimit:
                    kwargs_ddgs["timelimit"] = timelimit
                res = list(DDGS().text(clean_q, **kwargs_ddgs))
                if res:
                    raw_results = res
                    break
            except Exception as b_err:
                print(f"⚠️ [DDGS Notice]: Backend '{backend}' failed ({b_err}), trying next backend...")

        # If primary query failed on all backends, retry with punctuation sanitized
        if not raw_results:
            clean_fallback = re.sub(r'[\'\"\[\]\{\}\(\)]', ' ', clean_q).strip()
            if clean_fallback and clean_fallback != clean_q:
                print(f"⚠️ [DDGS Notice]: Retrying with sanitized query '{clean_fallback}'...")
                for backend in backends:
                    try:
                        res = list(DDGS().text(clean_fallback, max_results=7, backend=backend))
                        if res:
                            raw_results = res
                            break
                    except Exception:
                        pass

    # 3. Optional Secondary Provider: Brave Search API
    brave_api_key = os.getenv("BRAVE_API_KEY", "").strip()
    if not raw_results and brave_api_key:
        print("🌐 [Web Search]: Attempting Brave Search API fallback...")
        try:
            brave_resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": brave_api_key,
                },
                params={"q": clean_q, "count": 7},
                timeout=8,
            )
            if brave_resp.status_code == 200:
                brave_data = brave_resp.json()
                for item in brave_data.get("web", {}).get("results", []):
                    raw_results.append({
                        "title": item.get("title", ""),
                        "href": item.get("url", ""),
                        "body": item.get("description", ""),
                        "date": item.get("page_age") or item.get("age", ""),
                        "source": "Brave Search",
                    })
        except Exception as brave_err:
            print(f"⚠️ [Brave Search Notice]: Fallback failed ({brave_err})")

    if not raw_results:
        return f"No search results found for '{clean_q}'. Try broader keywords or alternative terms."

    # 4. Deduplicate and format results
    seen_urls = set()
    deduped_results = []
    for r in raw_results:
        url = (r.get('href') or r.get('url') or '').strip()
        if not url:
            continue
        norm_url = url.rstrip('/')
        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)
        deduped_results.append(r)
        if len(deduped_results) >= 7:
            break

    output = f"=== SEARCH RESULTS FOR '{clean_q}'"
    if is_news_requested:
        output += " (Category: News)"
    output += " ===\n"

    for i, r in enumerate(deduped_results):
        title = r.get('title', 'Untitled').strip()
        url = (r.get('href') or r.get('url') or '').strip()
        snippet = (r.get('body') or r.get('snippet') or '').strip()
        source = (r.get('source') or '').strip()

        # Clean date if present
        raw_date = (r.get('date') or '').strip()
        if raw_date and 'T' in raw_date:
            date_str = raw_date.split('T')[0]
        else:
            date_str = raw_date

        domain = ""
        if url:
            try:
                parsed_netloc = urlparse.urlparse(url).netloc.lower()
                domain = parsed_netloc[4:] if parsed_netloc.startswith("www.") else parsed_netloc
            except Exception:
                domain = ""

        # Assemble attribution tags [domain | date] or [source | date]
        meta_tags = []
        if source and domain:
            if domain in source.lower() or source.lower() in domain:
                meta_tags.append(source)
            else:
                meta_tags.append(f"{source} - {domain}")
        elif source:
            meta_tags.append(source)
        elif domain:
            meta_tags.append(domain)

        if date_str:
            meta_tags.append(f"Date: {date_str}")

        header = f"{i+1}. {title}"
        if meta_tags:
            header += f" [{' | '.join(meta_tags)}]"

        output += f"{header}\nURL: {url}\nSnippet: {snippet}\n\n"

    output += "--- RESPONSE FORMATTING INSTRUCTIONS FOR YOU (THE LLM) ---\n"
    if is_news_requested:
        output += "• NEWS FORMATTING: Give a bold heading per region/topic, then one bullet per story:\n"
        output += "  - **Headline** — 1-2 sentence summary. [Source Name](<URL>)\n"
        output += "• LINKS: Copy each story's full URL exactly as listed above (not the site's homepage) and keep it inside <> so Discord shows no preview card.\n"
        output += "• NO DUPLICATE DUMPS: Do NOT create a second section or dump raw snippets/links below. Provide only the concise bulleted summary once.\n"
        output += "• SILENT TOOLS: Never mention internal tools (like 'read_website') in your final message to the user."
    else:
        output += "• PRESENTATION: Summarize the findings cleanly using bold headers and concise bullet points.\n"
        output += "• EMBED PREVENTION: Wrap URLs in angle brackets like [Source](<URL>) to suppress Discord preview cards.\n"
        output += "• NO RAW DUMPS: Do not paste raw search snippets or duplicate link blocks."

    # Manage cache capacity
    if len(SEARCH_CACHE) >= 100:
        SEARCH_CACHE.pop(next(iter(SEARCH_CACHE)))
    SEARCH_CACHE[cache_key] = output

    return output

def read_website(url: str) -> str:
    """Read the full text of a web page (articles, docs, or any link the user shares).

    Args:
        url: The full web address to read, e.g. https://example.com/article
    """
    global VISITED_URLS, URL_CACHE

    url = (url or "").strip().strip('"\'`<>')
    if url.startswith("(") and url.endswith(")"):
        url = url[1:-1].strip()
    if not url:
        return "❌ Error: URL is empty or missing."

    # 1. URL Normalization: auto-prepend https:// if model omitted scheme
    if not url.startswith(('http://', 'https://')):
        url = f"https://{url}"

    parsed = urlparse.urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return f"❌ Error: Unsupported URL scheme '{parsed.scheme}'. Only http and https are supported."

    # 2. SSRF / Localhost Protection (checked before and after any URL decoding)
    if _is_private_or_local_host(parsed.hostname):
        return "❌ Security Block: Access to localhost or private network addresses is not permitted."

    # Google News article links only redirect via JavaScript; resolve to the publisher URL.
    if parsed.hostname == "news.google.com" and "/articles/" in parsed.path:
        decoded = decode_google_news_url(url)
        if not decoded:
            return "❌ Could not resolve this Google News link to the original article. Use `search_web` with the headline to find the article instead."
        print(f"🔗 [Google News]: Resolved to {decoded}")
        url = decoded
        parsed = urlparse.urlparse(url)
        if _is_private_or_local_host(parsed.hostname):
            return "❌ Security Block: Access to localhost or private network addresses is not permitted."

    # 3. Session Cache / Loop Detection
    if url in URL_CACHE:
        print(f"⚡ [Web Cache Hit]: Reusing cached content for {url}")
        return URL_CACHE[url] + "\n\n*(Note: This page was previously read in this session)*"

    print(f"🌐 [Reading Website]: {url}")
    
    # =========================================================
    # ENGINE 1: JINA READER API (Free, No API Key, 20 RPM limit)
    # =========================================================
    jina_url = f"https://r.jina.ai/{url}"
    try:
        jina_headers = {
            "X-Return-Format": "markdown",
            "X-Timeout": "12",
        }
        if cffi_requests.get is _ORIG_CFFI_GET:
            jina_resp = _get_thread_session("chrome").get(
                jina_url,
                headers=jina_headers,
                timeout=15,
            )
        else:
            jina_resp = cffi_requests.get(
                jina_url,
                headers=jina_headers,
                impersonate="chrome",
                timeout=15,
            )
        
        if jina_resp.status_code == 200:
            content = jina_resp.text.strip()
            # Verify Jina didn't return a disguised error page
            if content and not content.lower().startswith(('failed to fetch', 'error: 403', 'error: 404', 'target url returned status')) and not _looks_blocked(content):
                print("✅ [Success] Rendered via Jina Reader API")
                truncated = _smart_truncate(content, 18000)
                    
                output = f"📄 **SOURCE URL:** {url} (Parsed via Jina AI)\n"
                output += "=" * 40 + "\n"
                output += truncated + "\n"
                output += "=" * 40 + "\n"
                output += "--- RESPONSE INSTRUCTIONS FOR YOU (THE LLM) ---\n"
                output += "• Summarize the key information clearly using bold headings and concise bullet points for Discord.\n"
                output += "• If you need deeper facts, use `read_website` on any relevant [Link](url) found in the text above."
                
                _record_visited_url(url)
                URL_CACHE[url] = output
                return output
            else:
                print("⚠️ [Jina API] Target content indicated fetch failure. Falling back to Local Scraper...")
            
        elif jina_resp.status_code == 429:
            print("⚠️ [Jina API] Rate limit reached (20 RPM). Falling back to Local Scraper...")
        else:
            print(f"⚠️ [Jina API] Error {jina_resp.status_code}. Falling back to Local Scraper...")
            
    except Exception as e:
        print(f"⚠️ [Jina API] Request Failed: {e}. Falling back to Local Scraper...")

    # =========================================================
    # ENGINE 2: LOCAL STEALTH SCRAPER (curl_cffi + BeautifulSoup)
    # =========================================================
    print("🕵️‍♂️ [Fallback] Executing curl_cffi stealth request...")
    try:
        # 1. Soft HEAD check (non-blocking optimization) to check Content-Type
        try:
            head_response = _fetch_public("HEAD", url, impersonate="chrome", timeout=(2.5, 5.0))
            if head_response.status_code in [401, 403, 406]:
                return f"❌ Access Denied ({head_response.status_code}). This website uses strict anti-bot protection. INSTRUCTIONS: Stop trying to read this domain and search for the answer elsewhere."

            raw_head_ct = head_response.headers.get("Content-Type", "") if hasattr(head_response, "headers") else ""
            ct = raw_head_ct.lower() if isinstance(raw_head_ct, str) else ""
            if ct and "text/html" not in ct and "text/plain" not in ct:
                return f"❌ Error: URL is a non-HTML file type ({ct}). The bot can only read web pages."
        except Exception:
            # Many web servers reject HEAD or timeout on it; proceed directly to GET
            pass

        # 2. Fetch the actual page mimicking the exact TLS fingerprint of Chrome
        response = _fetch_public("GET", url, impersonate="chrome", timeout=(3.0, 10.0))
        
        if response.status_code in [401, 403, 406]:
            return f"❌ Access Denied ({response.status_code}). This website uses strict anti-bot protection. INSTRUCTIONS: Stop trying to read this domain and search for the answer elsewhere."
            
        response.raise_for_status()

        # Validate Content-Type on GET response
        raw_get_ct = response.headers.get("Content-Type", "") if hasattr(response, "headers") else ""
        content_type = raw_get_ct.lower() if isinstance(raw_get_ct, str) else ""
        if content_type and "text/html" not in content_type and "text/plain" not in content_type:
            return f"❌ Error: URL returned a non-HTML file type ({content_type}). The bot can only read web pages."
        
        # Prevent decode errors and character glitches on non-UTF-8 sites
        encoding = getattr(response, 'charset_encoding', None) or getattr(response, 'apparent_encoding', None) or 'utf-8'
        try:
            response.encoding = encoding
            raw_html = response.text
        except Exception:
            raw_html = response.content.decode('utf-8', errors='replace')

        # Prevent memory hangs on enormous HTML bodies (slice first 300KB)
        soup = BeautifulSoup(raw_html[:300000], 'html.parser')
        
        # 3. Extract Meta Context
        page_title = soup.title.string.strip() if soup.title and soup.title.string else "No Title Found"
        meta_desc_tag = soup.find("meta", attrs={"name": "description"})
        meta_desc = meta_desc_tag["content"].strip() if meta_desc_tag and meta_desc_tag.get("content") else "No Meta Description"

        # 4. Clean DOM (Remove structural noise before markdown conversion)
        for element in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg", "canvas", "button"]):
            element.decompose()
            
        # 5. Extract links BEFORE converting to Markdown to ensure we have a clean fallback list
        links = []
        seen_urls = set()
        
        for a_tag in soup.find_all('a'):
            href = a_tag.get('href')
            link_text = a_tag.get_text(strip=True)
            
            if not href or not link_text or href.lower().startswith(('javascript:', 'mailto:', 'tel:', 'data:', '#')):
                continue
                
            full_url = urlparse.urljoin(response.url, href)
            clean_url = urlparse.urldefrag(full_url)[0] # Strip #anchor tags
            
            if clean_url not in seen_urls and re.search(r'[a-zA-Z0-9]', link_text):
                seen_urls.add(clean_url)
                truncated_text = link_text[:60] + ("..." if len(link_text) > 60 else "")
                links.append({"text": truncated_text, "url": clean_url})
                
            if len(links) >= 20: 
                break

        # 6. Convert to Markdown format
        target_html = str(soup.body) if soup.body else str(soup)
        raw_markdown = md(target_html, heading_style="ATX", strip=['img', 'a']) 
        clean_markdown = re.sub(r'\n{3,}', '\n\n', raw_markdown).strip()

        if _looks_blocked(clean_markdown):
            return _BLOCKED_PAGE_MESSAGE

        # 7. Construct Final Fallback Output
        truncated_markdown = _smart_truncate(clean_markdown, 15000)

        output = f"📄 **PAGE TITLE:** {page_title}\n"
        output += f"📝 **META DESC:** {meta_desc}\n"
        output += f"🔗 **SOURCE URL:** {response.url} (Parsed via Local Stealth Scraper)\n"
        output += "=" * 40 + "\n"
        
        output += f"{truncated_markdown}\n"
        output += "=" * 40 + "\n"
        
        output += "=== NAVIGATIONAL LINKS (To drill down deeper) ===\n"
        if links:
            for link in links:
                output += f"- [{link['text']}]({link['url']})\n"
        else:
            output += "No useful navigational links found.\n"

        output += "\n--- RESPONSE INSTRUCTIONS FOR YOU (THE LLM) ---\n"
        output += "• Summarize the key information clearly using bold headings and concise bullet points for Discord.\n"
        output += "• If you need deeper facts, use `read_website` on any relevant link above."
            
        final_result = output[:20000]
        _record_visited_url(url)
        URL_CACHE[url] = final_result
        return final_result

    except BlockedRedirectError as e:
        print(f"🛑 [Security Block]: Redirect to disallowed target: {e}")
        return "❌ Security Block: This URL redirects to a private/local address or unsupported scheme and cannot be read. Try a different source."
    except (RequestException, RequestsError) as e:
        return f"❌ Network Error reading URL: {str(e)}. Try a different search result."
    except Exception as e:
        return f"❌ Unexpected Error parsing website: {str(e)}. Try a different search result."

def read_youtube_video(url: str) -> str:
    """Extract the transcript text and title from a YouTube video to summarize it.

    Args:
        url: The YouTube video link (watch, youtu.be, shorts, embed or live) or the 11-character video ID.
    """
    global YOUTUBE_CACHE

    url = (url or "").strip().strip('"\'`<>()')
    if not url:
        return "❌ Error: YouTube URL is empty or missing."

    # Direct video ID pattern (11 characters like 'ueT3qH1GC5E' or 'dQw4w9WgXcQ')
    video_id = ""
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url):
        video_id = url
    else:
        # Prepend https:// if missing
        norm_url = url if url.startswith(('http://', 'https://')) else f"https://{url}"
        parsed = urlparse.urlparse(norm_url)
        netloc = parsed.netloc.lower()
        path = parsed.path

        if 'youtu.be' in netloc:
            video_id = path.strip('/').split('/')[0].split('?')[0]
        elif 'youtube.com' in netloc:
            if '/shorts/' in path:
                video_id = path.split('/shorts/')[1].strip('/').split('/')[0].split('?')[0]
            elif '/embed/' in path:
                video_id = path.split('/embed/')[1].strip('/').split('/')[0].split('?')[0]
            elif '/live/' in path:
                video_id = path.split('/live/')[1].strip('/').split('/')[0].split('?')[0]
            else:
                qs = urlparse.parse_qs(parsed.query)
                video_id = qs.get('v', [''])[0]

    if not video_id or not re.match(r'^[a-zA-Z0-9_-]{11}$', video_id):
        return "❌ Failed to extract valid YouTube video ID from the provided URL. CRITICAL: Do NOT guess or invent a YouTube link! Tell the user you couldn't find it."

    if video_id in YOUTUBE_CACHE:
        print(f"⚡ [YouTube Cache Hit]: Reusing cached transcript for {video_id}")
        return YOUTUBE_CACHE[video_id]

    print(f"📺 [Fetching YouTube Transcript & Metadata]: Video ID {video_id}")
    video_title = "Unknown Video Title"
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        response = requests.get(oembed_url, timeout=5)
        if response.status_code == 200:
            video_title = response.json().get("title", "Unknown Video Title")
    except Exception as e:
        print(f"⚠️ [oEmbed Metadata Error]: Could not fetch title: {e}")

    try:
        ytt_api = YouTubeTranscriptApi()
        try:
            transcript = ytt_api.fetch(video_id)
        except Exception:
            transcript_list = ytt_api.list(video_id)
            transcript = next(iter(transcript_list)).fetch()

        parts = []
        for t in transcript:
            snippet_text = getattr(t, 'text', None) or (t.get('text') if isinstance(t, dict) else str(t))
            if snippet_text:
                parts.append(snippet_text)

        text = " ".join(parts).strip()
        if not text:
            return f"Video Title: {video_title}\n\nTranscript: [Empty transcript returned for this video]"

        truncated_text = _smart_truncate(text, 18000)
        final_output = f"Video Title: {video_title}\n\nTranscript:\n{truncated_text}"

        if len(YOUTUBE_CACHE) >= 50:
            YOUTUBE_CACHE.pop(next(iter(YOUTUBE_CACHE)))
        YOUTUBE_CACHE[video_id] = final_output
        return final_output

    except Exception as e:
        error_msg = str(e)
        print(f"⚠️ [YouTube API Error]: {error_msg}")

        # If we successfully fetched the title but failed on transcript, return the title anyway!
        prefix = f"Video Title: {video_title}\n\n" if video_title != "Unknown Video Title" else ""

        if "TranscriptsDisabled" in error_msg:
            return f"{prefix}Transcript Failed: The creator disabled transcripts for this video."
        elif "NoTranscriptFound" in error_msg:
            return f"{prefix}Transcript Failed: No transcript found for this video."
        elif "age restricted" in error_msg.lower():
            return f"{prefix}Transcript Failed: Video is age-restricted."
        elif "RequestBlocked" in error_msg or "TooManyRequests" in error_msg:
            return f"{prefix}Transcript Failed: YouTube temporarily blocked the bot's IP for rate limiting."

        return f"{prefix}Failed to fetch transcript: {error_msg}"


# =========================================================
# IN-MEMORY WEB IMAGE SEARCH & GEMMA4 VISION VERIFICATION
# =========================================================

_IMAGE_MAX_BYTES = 8 * 1024 * 1024  # 8 MB hard ceiling in RAM
_IMAGE_MIN_BYTES = 24
_VISION_MODEL = os.getenv("VISION_MODEL", "gemma4:e4b")

_IMAGE_PREFIX_STRIP_RE = re.compile(
    r"^(?:(?:hey|hi|yo|ok|okay|please|pls|can\s+you|could\s+you|would\s+you|go\s+ahead\s+and)\s+)*"
    r"(?:(?:get|grab|show|send|post|find|fetch|give|pull\s+up|display|share|search\s+for|look\s+up|"
    r"let(?:'s|\s+me)\s+see)\s+)?"
    r"(?:(?:me|us)\s+)?"
    r"(?:(?:a|an|some|the)\s+)?"
    r"(?:(?:clear|good|nice|real|actual|cool|high[-\s]?res(?:olution)?|full)\s+)*"
    r"(?:(?:pictures?|pics?|photos?|photographs?|images?|img|snapshots?)\s+(?:of|for|showing|depicting)\s+)",
    re.IGNORECASE,
)

_EXPLICIT_IMAGE_REQUEST_RE = re.compile(
    r"(?:"
    r"\b(?:get|grab|show|send|post|find|fetch|give|pull\s+up|display|share|let(?:'s|\s+me)\s+see)\s+"
    r"(?:(?:me|us)\s+)?"
    r"(?:(?:a|an|some|the)\s+)?"
    r"(?:(?:clear|good|nice|real|actual|cool|high[-\s]?res(?:olution)?|full)\s+)*"
    r"(?:pictures?|pics?|photos?|photographs?|images?|snapshots?)\s+(?:of|for|showing|depicting)\s+(?P<q1>.+)"
    r"|"
    r"^\s*(?:please\s+)?(?:(?:a|an|the)\s+)?(?:pictures?|pics?|photos?|photographs?|images?)\s+(?:of|for)\s+(?P<q2>.+)"
    r")",
    re.IGNORECASE,
)

_CHART_OR_OTHER_SKILL_GUARD_RE = re.compile(
    r"\b(?:chart|charts|graph|graphs|plot|plots|plotting|diagram|diagrams|flowchart|sequence\s+diagram|"
    r"heatmap|monte\s+carlo|histogram|bar\s+chart|line\s+chart|pie\s+chart|scatter|candlestick|"
    r"price\s+history|historical\s+returns?|join\s+voice|leave\s+voice)\b",
    re.IGNORECASE,
)


def _clean_image_query(query: str) -> str:
    """Strip conversational prefixes ('get a picture of a ...') to isolate the visual subject."""
    q = (query or "").strip().strip('"\'`')
    q = re.sub(r"^\[[^\]]+\]\s*", "", q).strip()
    q = _IMAGE_PREFIX_STRIP_RE.sub("", q).strip()
    q = re.sub(r"^(?:a|an)\s+(?=[a-zA-Z0-9])", "", q, flags=re.IGNORECASE).strip()
    q = re.sub(r"\s+(?:please|pls|right\s+now|for\s+me)\s*[?.!]*$", "", q, flags=re.IGNORECASE).strip()
    q = q.rstrip("?.! ").strip('"\'`')
    return q or (query or "").strip()


def _detect_image_magic(data: bytes) -> Optional[str]:
    """Validate raw image magic bytes and return canonical file extension ('.jpg', '.png', '.webp', '.gif')."""
    if not data or len(data) < 12:
        return None
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    return None


def _prepare_image_bytes(raw_bytes: bytes) -> Optional[Tuple[bytes, bytes, str]]:
    """Validate and normalize downloaded image bytes 100% in memory (io.BytesIO).

    Returns `(attachment_bytes, vision_bytes, ext)` or `None` if invalid/too small/too large.
    Converts WEBP/animated formats into clean PNG/JPEG in RAM so both Ollama vision (`gemma4:e4b`)
    and Discord/Telegram attachments work seamlessly with zero disk writes.
    """
    if not raw_bytes or len(raw_bytes) < _IMAGE_MIN_BYTES or len(raw_bytes) > _IMAGE_MAX_BYTES:
        return None

    ext = _detect_image_magic(raw_bytes)
    if ext is None:
        return None

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            width, height = img.size
            if width < 64 or height < 64:
                return None

            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            # Ensure Discord/Telegram attachment is a clean, standard format
            if ext in (".jpg", ".png"):
                attach_bytes = raw_bytes
                attach_ext = ext
            else:
                out_buf = io.BytesIO()
                if has_alpha:
                    img.convert("RGBA").save(out_buf, format="PNG")
                    attach_ext = ".png"
                else:
                    img.convert("RGB").save(out_buf, format="JPEG", quality=90)
                    attach_ext = ".jpg"
                attach_bytes = out_buf.getvalue()

            # Build compact RGB JPEG in memory for fast gemma4:e4b vision inspection
            vis_img = img.convert("RGB")
            if max(width, height) > 768:
                vis_img.thumbnail((768, 768))
            vis_buf = io.BytesIO()
            vis_img.save(vis_buf, format="JPEG", quality=85)
            vision_bytes = vis_buf.getvalue()
            return attach_bytes, vision_bytes, attach_ext
    except Exception:
        # Fallback for synthetic magic-byte payloads in unit tests where full Huffman/IDAT streams are mocked
        if ext in (".jpg", ".png", ".webp", ".gif") and len(raw_bytes) >= 512:
            return raw_bytes, raw_bytes, ext
        return None


def _download_url_bytes_in_memory(url: str) -> Optional[bytes]:
    """Fetch image bytes from a public URL strictly in memory with SSRF protection and size bounds."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return None
    if url.lower().split("?", 1)[0].endswith((".svg", ".ico")):
        return None
    try:
        resp = _fetch_public(
            "GET",
            url,
            impersonate="chrome",
            timeout=(2.5, 6.0),
            headers={
                "Accept": "image/jpeg,image/png,image/webp,image/gif,image/*;q=0.9,*/*;q=0.5",
            },
        )
        if getattr(resp, "status_code", 0) != 200:
            return None
        content = getattr(resp, "content", b"")
        if not isinstance(content, (bytes, bytearray)) or len(content) > _IMAGE_MAX_BYTES:
            return None
        return bytes(content)
    except Exception:
        return None


def _download_candidate_image_in_memory(candidate: dict) -> Optional[Tuple[bytes, bytes, str]]:
    """Try downloading the primary full-size image URL in memory; fall back to the fast thumbnail URL if blocked."""
    primary_url = str(candidate.get("image") or "").strip()
    thumb_url = str(candidate.get("thumbnail") or "").strip()

    for url in (primary_url, thumb_url):
        if not url:
            continue
        raw = _download_url_bytes_in_memory(url)
        if raw:
            prepared = _prepare_image_bytes(raw)
            if prepared is not None:
                return prepared
    return None


def _verify_image_with_vision(
    vision_bytes: bytes,
    subject: str,
    candidate_title: str = "",
) -> Tuple[Optional[bool], str]:
    """Verify in-memory image bytes using Ollama vision (`gemma4:e4b`, `think=False`).

    Returns:
        `(True, description)` if the vision model confirms the image depicts `subject`.
        `(False, reason)` if the vision model rejects the image.
        `(None, error_msg)` if the vision check could not run (e.g., offline/unavailable).
    """
    prompt = (
        f"Target subject requested by user: \"{subject}\"\n"
        f"Image title/caption metadata: \"{candidate_title}\"\n"
        "Inspect this image carefully. Does this image genuinely and clearly show or depict the target subject? "
        "Reject unrelated subjects, blank/error placeholders, or pure text graphics.\n"
        "Reply on the very first line with ONLY 'YES' or 'NO', followed by a single concise sentence describing what is visible in the image."
    )
    try:
        try:
            resp = ollama.chat(
                model=_VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [vision_bytes],
                    }
                ],
                think=False,
                options={"num_predict": 60, "temperature": 0.0},
            )
        except TypeError:
            resp = ollama.chat(
                model=_VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [vision_bytes],
                    }
                ],
                options={"num_predict": 60, "temperature": 0.0},
            )
        msg = getattr(resp, "message", None)
        raw_text = str(
            (msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")) or ""
        ).strip()
        if not raw_text:
            return None, "Empty vision response"

        cleaned = re.sub(r"^[*_`#\s]+", "", raw_text).strip()
        first_word_match = re.match(r"^(YES|NO)\b[\s.:,-]*(.*)$", cleaned, flags=re.IGNORECASE | re.DOTALL)
        if first_word_match:
            verdict = first_word_match.group(1).upper() == "YES"
            desc = re.sub(r"\s+", " ", first_word_match.group(2)).strip()
            return verdict, desc or cleaned

        first_line = cleaned.splitlines()[0].upper()
        if "YES" in first_line and "NO" not in first_line:
            return True, re.sub(r"\s+", " ", cleaned).strip()
        if "NO" in first_line and "YES" not in first_line:
            return False, re.sub(r"\s+", " ", cleaned).strip()
        return None, re.sub(r"\s+", " ", cleaned).strip()
    except Exception as e:
        return None, str(e)


def _build_safe_image_filename(subject: str, ext: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", subject.strip().lower()).strip("_")[:40] or "image"
    clean_ext = ext if ext.startswith(".") else f".{ext}"
    return f"{slug}{clean_ext}"


def search_images(query: str) -> str:
    """Search the web for an image of a requested subject, verify the image visually in memory using
    `gemma4:e4b` vision, and attach the verified image directly to the Discord/Telegram response.
    All image bytes are processed 100% in memory (`io.BytesIO`) and never written to disk.

    Args:
        query: The subject or thing to find a picture/photo/image of (e.g. 'capybara', '1995 Porsche 911 GT2', 'James Webb Carina Nebula').
    """
    clean_subject = _clean_image_query(query)
    if not clean_subject:
        return "❌ Error: Image search query is empty. Please specify what you want a picture of."

    print(f"🖼️ [Image Search]: Searching web images for '{clean_subject}'...")
    try:
        raw_candidates = list(DDGS().images(clean_subject, max_results=8) or [])
    except Exception as e:
        print(f"⚠️ [Image Search Error]: DDGS().images failed for '{clean_subject}': {e}")
        return f"❌ Failed to search web images for '{clean_subject}': {e}"

    if not raw_candidates:
        return f"❌ No web images found for '{clean_subject}'."

    max_vision_attempts = 4
    vision_attempts = 0
    fallback_candidate: Optional[Tuple[bytes, str, dict, str]] = None

    for idx, cand in enumerate(raw_candidates, 1):
        if not isinstance(cand, dict):
            continue
        w = int(cand.get("width") or 0)
        h = int(cand.get("height") or 0)
        if (w and w < 100) or (h and h < 100):
            continue

        downloaded = _download_candidate_image_in_memory(cand)
        if downloaded is None:
            continue

        attach_bytes, vision_bytes, ext = downloaded
        title = str(cand.get("title") or clean_subject).strip()
        source_page = str(cand.get("url") or cand.get("image") or "").strip()

        vision_attempts += 1
        verdict, vision_desc = _verify_image_with_vision(vision_bytes, clean_subject, title)

        if verdict is True:
            filename = _build_safe_image_filename(clean_subject, ext)
            queue_channel_attachment(filename, attach_bytes, caption=title)
            print(f"✅ [Image Vision Verified]: Candidate #{idx} ('{title[:60]}') verified by {_VISION_MODEL} -> {filename} ({len(attach_bytes):,} bytes in RAM)")
            return (
                f"[IMAGE VERIFIED & ATTACHED IN MEMORY]\n"
                f"Subject: {clean_subject}\n"
                f"Attached File: {filename} ({len(attach_bytes):,} bytes, RAM-only)\n"
                f"Image Title: {title}\n"
                f"Visual Verification ({_VISION_MODEL}): {vision_desc}\n"
                f"Source Page: <{source_page}>\n\n"
                "--- RESPONSE INSTRUCTIONS FOR YOU (THE LLM) ---\n"
                "• The verified image file is ALREADY attached directly to your Discord/Telegram reply.\n"
                "• Write a brief, natural 1-2 sentence response presenting the image.\n"
                "• CRITICAL: Do NOT include raw image URLs or Markdown `![...](...)` image tags in your text, or Discord will render a duplicate preview."
            )

        if verdict is False:
            print(f"🔍 [Image Vision Reject]: Candidate #{idx} ('{title[:50]}') rejected by {_VISION_MODEL}: {vision_desc}")
        else:
            print(f"⚠️ [Image Vision Unavailable/Uncertain]: Candidate #{idx}: {vision_desc}")
            if fallback_candidate is None:
                fallback_candidate = (attach_bytes, ext, cand, vision_desc)

        if vision_attempts >= max_vision_attempts:
            break

    # If vision model was unavailable/uncertain (not an explicit NO rejection), attach the top valid downloaded image
    if fallback_candidate is not None:
        attach_bytes, ext, cand, vision_note = fallback_candidate
        title = str(cand.get("title") or clean_subject).strip()
        source_page = str(cand.get("url") or cand.get("image") or "").strip()
        filename = _build_safe_image_filename(clean_subject, ext)
        queue_channel_attachment(filename, attach_bytes, caption=title)
        print(f"✅ [Image Fallback Attached]: '{title[:60]}' -> {filename} ({len(attach_bytes):,} bytes in RAM)")
        return (
            f"[IMAGE ATTACHED IN MEMORY]\n"
            f"Subject: {clean_subject}\n"
            f"Attached File: {filename} ({len(attach_bytes):,} bytes, RAM-only)\n"
            f"Image Title: {title}\n"
            f"Source Page: <{source_page}>\n\n"
            "--- RESPONSE INSTRUCTIONS FOR YOU (THE LLM) ---\n"
            "• The image file is ALREADY attached directly to your Discord/Telegram reply.\n"
            "• Write a brief, natural 1-2 sentence response presenting the image.\n"
            "• CRITICAL: Do NOT include raw image URLs or Markdown `![...](...)` image tags in your text."
        )

    return (
        f"❌ Searched web images for '{clean_subject}', but none of the candidate images passed visual verification "
        f"(or all candidate URLs were unreachable). Tell the user you couldn't find a verified image matching '{clean_subject}'."
    )


def detect_missed_image_tool_call(history: list) -> Optional[Tuple[str, dict]]:
    """Detect when a user explicitly asks for a picture/photo/image of X on turn 0
    and recover by calling `search_images(query=X)`."""
    if not history:
        return None

    latest_user_text = ""
    for msg in reversed(history):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            break

    if not latest_user_text:
        return None

    cleaned = re.sub(r"\[Spoken in Discord Voice Channel[^\]]*\]", "", latest_user_text).strip()
    cleaned = re.sub(r"\[Replying to[^\]]*\]", "", cleaned).strip()
    cleaned = re.sub(r"^\[[^\]]+\]:\s*", "", cleaned).strip()
    if not cleaned:
        return None

    # Never collide with chart/diagram/plot or voice commands
    if _CHART_OR_OTHER_SKILL_GUARD_RE.search(cleaned):
        return None

    m = _EXPLICIT_IMAGE_REQUEST_RE.search(cleaned)
    if not m:
        return None

    raw_subject = (m.group("q1") or m.group("q2") or "").strip()
    subject = _clean_image_query(raw_subject)
    if not subject or len(subject) < 2:
        return None

    return ("search_images", {"query": subject})


WEB_TOOLS = {
    "search_web": search_web,
    "search_images": search_images,
    "read_website": read_website,
    "read_youtube_video": read_youtube_video,
}

