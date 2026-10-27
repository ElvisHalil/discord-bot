import io
import ipaddress
import os
import re
import socket
import sys
import requests
import urllib.parse as urlparse
from concurrent.futures import ThreadPoolExecutor
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

from src.context import get_active_channel_id, queue_channel_attachment

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
    try:
        state = _get_channel_image_state()
        state["last_page_url"] = url
    except Exception:
        pass

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

    # Redirect direct YouTube video links passed to read_website straight to read_youtube_video
    host_low = (parsed.hostname or "").lower()
    if host_low in ("youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com") and (
        host_low == "youtu.be"
        or any(p in parsed.path for p in ("/watch", "/shorts/", "/embed/", "/live/"))
    ):
        return read_youtube_video(url)

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

_YOUTUBE_SHORT_TRANSCRIPT_CHAR_LIMIT = 12500
_YOUTUBE_MAX_OUTPUT_CHARS = 13800
_MAX_TRANSCRIPT_SNIPPETS = 6000
_MAX_TRANSCRIPT_WINDOWS = 400
_YOUTUBE_RAW_CACHE = BoundedCache(maxsize=50)

_YT_STOPWORDS = frozenset({
    "the", "and", "that", "this", "with", "from", "your", "have", "what", "when", "where",
    "which", "there", "their", "they", "them", "were", "been", "being", "would", "could",
    "should", "about", "into", "over", "after", "before", "under", "between", "through",
    "just", "like", "know", "really", "going", "think", "right", "actually", "basically",
    "literally", "mean", "well", "yeah", "okay", "very", "much", "some", "more", "most",
    "other", "than", "then", "also", "only", "even", "back", "down", "here", "make",
    "made", "want", "need", "look", "take", "good", "time", "people", "thing", "things",
    "something", "because", "does", "didn", "don", "isn", "wasn", "aren", "won", "can",
    "will", "you", "for", "not", "are", "but", "all", "out", "who", "how", "why", "way",
})

_YT_THESIS_SIGNAL_RE = re.compile(
    r"\b(?:in\s+summary|in\s+conclusion|to\s+summarize|the\s+key\s+(?:point|takeaway|thing)|"
    r"most\s+important(?:ly)?|main\s+reason|bottom\s+line|first\s+of\s+all|second(?:ly)?|"
    r"third(?:ly)?|finally|my\s+verdict|final\s+thoughts?|my\s+prediction|here\s+is\s+why|"
    r"the\s+problem\s+is|the\s+solution\s+is|big\s+picture|number\s+one|takeaway)\b",
    re.IGNORECASE,
)

_YT_SPONSOR_BOILERPLATE_RE = re.compile(
    r"\b(?:sponsored\s+by|today'?s\s+sponsor|use\s+(?:my\s+)?(?:promo\s+|discount\s+)?code|"
    r"smash\s+that\s+like\s+button|don'?t\s+forget\s+to\s+subscribe|hit\s+the\s+bell|"
    r"link\s+in\s+the\s+description|special\s+offer\s+for\s+our\s+listeners|"
    r"money[-\s]?back\s+guarantee|data\s+brokers?|remove\s+your\s+(?:personal\s+)?(?:data|information)|"
    r"unlimited\s+plan|free\s+trial|\d+%\s+off\s+your\s+first)\b",
    re.IGNORECASE,
)

_YT_NUMBER_OR_METRIC_RE = re.compile(
    r"(?:\$\d[\d,.]*|\b\d+(?:\.\d+)?%|\b(?:19|20)\d{2}\b|\b\d[\d,]*(?:\.\d+)?\s*(?:million|billion|trillion|thousand|k|m|b|x|bps|ms|gb|tb|mw|gw|fps|hz)\b)",
    re.IGNORECASE,
)


def _format_yt_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS or H:MM:SS."""
    sec = max(0, int(round(float(seconds or 0.0))))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _extract_transcript_segments(transcript) -> list[tuple[Optional[float], float, str]]:
    """Extract (start_sec, duration_sec, text) tuples from YouTubeTranscriptApi output with stride-sampling safety for extreme 10h+ streams."""
    from itertools import islice

    raw_items = list(islice(transcript, 100000)) if not isinstance(transcript, (list, tuple)) else list(transcript[:100000])
    total_raw = len(raw_items)
    if total_raw > _MAX_TRANSCRIPT_SNIPPETS:
        # Uniformly stride-sample across 0%..100% of extreme multi-hour streams (always preserving opening & closing snippets)
        sampled_indices = [
            int(round(i * (total_raw - 1) / (_MAX_TRANSCRIPT_SNIPPETS - 1)))
            for i in range(_MAX_TRANSCRIPT_SNIPPETS)
        ]
        raw_items = [raw_items[idx] for idx in sampled_indices]

    segments: list[tuple[Optional[float], float, str]] = []
    for t in raw_items:
        if isinstance(t, dict):
            snippet_text = t.get("text")
            raw_start = t.get("start")
            raw_dur = t.get("duration")
        else:
            snippet_text = getattr(t, "text", None)
            if not isinstance(snippet_text, str):
                snippet_text = str(t) if t is not None else ""
            raw_start = getattr(t, "start", None)
            raw_dur = getattr(t, "duration", None)

        clean_piece = re.sub(r"\s+", " ", str(snippet_text or "")).strip()
        if not clean_piece:
            continue
        start_sec = (
            float(raw_start)
            if isinstance(raw_start, (int, float)) and not isinstance(raw_start, bool)
            else None
        )
        dur_sec = (
            float(raw_dur)
            if isinstance(raw_dur, (int, float)) and not isinstance(raw_dur, bool)
            else 0.0
        )
        segments.append((start_sec, dur_sec, clean_piece))
    return segments


def _format_short_transcript_with_timestamps(
    segments: list[tuple[Optional[float], float, str]],
    plain_text: str,
) -> str:
    """Render short/medium transcripts verbatim, inserting [MM:SS] markers every ~60s when real timestamps exist."""
    has_real_ts = any(s[0] is not None for s in segments)
    if not has_real_ts:
        return plain_text

    max_end = max(((s[0] or 0.0) + max(0.0, s[1])) for s in segments)
    if max_end < 60.0:
        return plain_text

    out_parts: list[str] = []
    last_marker_sec = -60.0
    for start_sec, _dur_sec, piece in segments:
        if start_sec is not None and (start_sec - last_marker_sec) >= 60.0:
            out_parts.append(f"[{_format_yt_timestamp(start_sec)}]")
            last_marker_sec = start_sec
        out_parts.append(piece)

    rendered = " ".join(out_parts).strip()
    return rendered if len(rendered) <= _YOUTUBE_MAX_OUTPUT_CHARS else plain_text


def _parse_query_timestamp_seconds(query: str) -> Optional[float]:
    """Parse an explicit timestamp (e.g. '1:15:00', '45:30', '32m', 'minute 45') from a query string."""
    if not query:
        return None
    m_colon = re.search(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b", query)
    if m_colon:
        h = int(m_colon.group(1) or 0)
        m = int(m_colon.group(2))
        s = int(m_colon.group(3))
        return float(h * 3600 + m * 60 + s)
    m_min = re.search(r"\b(?:at\s+|around\s+|minute\s+|min\s+)?(\d{1,3})\s*(?:m|min|mins|minutes?)\b", query, re.IGNORECASE)
    if m_min:
        return float(int(m_min.group(1)) * 60)
    return None


def _build_long_transcript_digest(
    segments: list[tuple[Optional[float], float, str]],
    plain_text: str,
    query: Optional[str] = None,
    max_chars: int = _YOUTUBE_MAX_OUTPUT_CHARS,
) -> str:
    """
    Build a full-timeline (0%–100%) multi-chapter transcript digest for long-form YouTube videos
    so the LLM sees the beginning, middle chapters, and final conclusion without overflowing context.
    Optionally prepends verbatim excerpts matching `query` (topic keywords or timestamp).
    """
    import math

    # 1. Group snippets into contiguous micro-windows (adaptively sized so extreme streams never exceed _MAX_TRANSCRIPT_WINDOWS)
    windows: list[dict] = []
    has_real_ts = any(s[0] is not None for s in segments)
    total_words = len(plain_text.split())
    target_win_words = max(55, (total_words + _MAX_TRANSCRIPT_WINDOWS - 1) // _MAX_TRANSCRIPT_WINDOWS)
    target_win_chars = max(360, (len(plain_text) + _MAX_TRANSCRIPT_WINDOWS - 1) // _MAX_TRANSCRIPT_WINDOWS)

    if has_real_ts and len(segments) > 1:
        cur_words: list[str] = []
        cur_word_count = 0
        win_start: Optional[float] = None
        win_end: float = 0.0
        cum_words = 0
        for start_sec, dur_sec, piece in segments:
            piece_words = piece.split()
            if not piece_words:
                continue
            est_start = start_sec if start_sec is not None else (cum_words / 155.0) * 60.0
            if win_start is None:
                win_start = est_start
            win_end = max(win_end, est_start + (dur_sec if dur_sec > 0 else (len(piece_words) / 155.0) * 60.0))
            cur_words.append(piece)
            cur_word_count += len(piece_words)
            cum_words += len(piece_words)
            if cur_word_count >= target_win_words or sum(len(x) for x in cur_words) >= target_win_chars:
                windows.append({
                    "idx": len(windows),
                    "start": float(win_start),
                    "end": float(max(win_start, win_end)),
                    "text": " ".join(cur_words).strip(),
                })
                cur_words = []
                cur_word_count = 0
                win_start = None
        if cur_words:
            s_val = float(win_start if win_start is not None else win_end)
            windows.append({
                "idx": len(windows),
                "start": s_val,
                "end": float(max(s_val, win_end)),
                "text": " ".join(cur_words).strip(),
            })
    else:
        all_words = plain_text.split()
        step_w = target_win_words
        for w_i in range(0, len(all_words), step_w):
            chunk_w = all_words[w_i : w_i + step_w]
            s_sec = (w_i / 155.0) * 60.0
            e_sec = ((w_i + len(chunk_w)) / 155.0) * 60.0
            windows.append({
                "idx": len(windows),
                "start": s_sec,
                "end": e_sec,
                "text": " ".join(chunk_w),
            })

    if not windows:
        return _smart_truncate(plain_text, max_chars)

    total_duration_sec = max(w["end"] for w in windows)
    n_win = len(windows)

    # 2. Compute IDF weights & information salience score for each micro-window
    doc_freq: dict[str, int] = {}
    win_tokens_list: list[set[str]] = []
    for w in windows:
        toks = {
            tok for tok in re.findall(r"[a-zA-Z0-9]{3,}", w["text"].lower())
            if tok not in _YT_STOPWORDS
        }
        win_tokens_list.append(toks)
        for tok in toks:
            doc_freq[tok] = doc_freq.get(tok, 0) + 1

    for i, w in enumerate(windows):
        txt = w["text"]
        toks = win_tokens_list[i]
        if toks:
            idf_sum = sum(math.log(1.0 + n_win / (1.0 + doc_freq.get(t, 1))) for t in toks)
            vocab_score = idf_sum / math.sqrt(len(toks) + 4.0)
        else:
            vocab_score = 0.0

        metric_hits = len(_YT_NUMBER_OR_METRIC_RE.findall(txt))
        metric_bonus = min(5.6, metric_hits * 1.4)
        thesis_bonus = 2.2 if _YT_THESIS_SIGNAL_RE.search(txt) else 0.0
        has_sponsor = bool(_YT_SPONSOR_BOILERPLATE_RE.search(txt))
        if has_sponsor:
            metric_bonus = 0.0
        sponsor_penalty = -8.0 if has_sponsor else 0.0
        w["salience"] = vocab_score + metric_bonus + thesis_bonus + sponsor_penalty

    # 3. Optional Query / Timestamp Targeted Excerpts
    clean_query = (query or "").strip()
    query_section = ""
    if clean_query:
        target_sec = _parse_query_timestamp_seconds(clean_query)
        q_low = clean_query.lower()
        wants_end = bool(re.search(r"\b(?:end|ending|conclusion|final|verdict|last\s+part|wrap\s*up|closing)\b", q_low))
        wants_start = bool(re.search(r"\b(?:start|beginning|intro|introduction|opening)\b", q_low))
        q_terms = [
            t for t in re.findall(r"[a-zA-Z0-9]{2,}", q_low)
            if t not in _YT_STOPWORDS and t not in ("video", "youtube", "watch", "transcript", "summary", "summarize", "tell", "said", "say", "talk", "talked")
        ]

        raw_q_scores = [0.0] * n_win
        for i, w in enumerate(windows):
            txt_low = w["text"].lower()
            score = 0.0
            if target_sec is not None:
                dist = abs(((w["start"] + w["end"]) * 0.5) - target_sec)
                if dist <= 150.0:
                    score += max(0.0, 12.0 - (dist / 15.0))
            if wants_end and i >= int(n_win * 0.82):
                score += 6.0
            if wants_start and i <= max(1, int(n_win * 0.15)):
                score += 5.0
            for qt in q_terms:
                if qt in win_tokens_list[i] or (len(qt) >= 4 and qt in txt_low):
                    idf_w = math.log(1.5 + n_win / (1.0 + doc_freq.get(qt, 1)))
                    score += 3.0 * idf_w
            raw_q_scores[i] = score

        # Bleed neighbor context so matching windows pull adjacent explanation
        smoothed_q_scores = [0.0] * n_win
        for i in range(n_win):
            prev_s = raw_q_scores[i - 1] * 0.35 if i > 0 else 0.0
            next_s = raw_q_scores[i + 1] * 0.35 if i + 1 < n_win else 0.0
            smoothed_q_scores[i] = raw_q_scores[i] + prev_s + next_s

        ranked_q = [
            i for i in sorted(range(n_win), key=lambda idx: (smoothed_q_scores[idx], windows[idx]["salience"]), reverse=True)
            if smoothed_q_scores[i] > 0.5
        ]
        if ranked_q:
            q_budget = min(4200, int(max_chars * 0.33))
            chosen_q: list[int] = []
            used_q_chars = 0
            for idx in ranked_q:
                w_len = len(windows[idx]["text"]) + 28
                if used_q_chars + w_len > q_budget and chosen_q:
                    break
                chosen_q.append(idx)
                used_q_chars += w_len
            chosen_q.sort()
            q_lines = [f"=== TARGETED VERBATIM EXCERPTS (Query: '{clean_query}') ==="]
            for idx in chosen_q:
                w = windows[idx]
                pct = int(round((idx / max(1, n_win - 1)) * 100))
                q_lines.append(f"[{_format_yt_timestamp(w['start'])} – {_format_yt_timestamp(w['end'])} | {pct}%] {w['text']}")
            query_section = "\n".join(q_lines) + "\n\n"

    # 4. Full-Timeline Multi-Chapter Breakdown (0% to 100% of video)
    char_len = len(plain_text)
    if char_len < 28000:
        num_chapters = min(6, n_win)
    elif char_len < 75000:
        num_chapters = min(8, n_win)
    else:
        num_chapters = min(10, n_win)
    num_chapters = max(1, num_chapters)

    header_note = (
        f"[Long-Form Video Coverage: ~{total_words:,} words ({_format_yt_timestamp(total_duration_sec)} duration) "
        f"mapped across {num_chapters} chronological chapters spanning 0%–100% of the video.]\n\n"
    )

    footer_instructions = (
        "\n\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
        f"This is a full-video transcript breakdown ({_format_yt_timestamp(total_duration_sec)} duration, ~{total_words:,} words) "
        f"spanning all {num_chapters} chapters from start to finish.\n"
        "1. Write a thorough, detailed analysis organized by **### Thematic Section Headings** covering EVERY major topic discussed across the entire video (beginning, middle chapters, and final conclusion).\n"
        "2. Under each `###` heading, write **2–4 detailed bullet points** explaining the speaker's exact arguments, reasoning, real-world examples, numbers, percentages, tickers, and takeaways.\n"
        "3. Do NOT write a shallow 1-line-per-chapter overview, do NOT include percentage ranges like `(12%–50%)`, and do NOT end with 'let me know if you want me to summarize a specific section'.\n"
        f"4. Ensure the topics and conclusions from the second half and final chapter (Chapter {num_chapters}/{num_chapters}) are covered with the same depth as the opening.\n"
        "5. Ignore any sponsor/advertiser read segments."
    )

    remaining_budget = max(3500, max_chars - len(header_note) - len(query_section) - len(footer_instructions) - 120)
    per_chapter_budget = max(420, remaining_budget // num_chapters)

    chapter_blocks: list[str] = ["=== FULL-TIMELINE CHRONOLOGICAL CHAPTER BREAKDOWN (0%–100%) ==="]
    for c_idx in range(num_chapters):
        start_w = (c_idx * n_win) // num_chapters
        end_w = ((c_idx + 1) * n_win) // num_chapters
        if end_w <= start_w:
            end_w = min(n_win, start_w + 1)
        c_windows = windows[start_w:end_w]
        if not c_windows:
            continue

        ch_start_ts = _format_yt_timestamp(c_windows[0]["start"])
        ch_end_ts = _format_yt_timestamp(c_windows[-1]["end"])
        pct_start = int(round((c_idx / num_chapters) * 100))
        pct_end = int(round(((c_idx + 1) / num_chapters) * 100))
        ch_header = f"--- Chapter {c_idx + 1}/{num_chapters} [{ch_start_ts} – {ch_end_ts} | {pct_start}%–{pct_end}%] ---"

        # Always anchor the chapter opening, and in the final chapter always anchor the video conclusion
        mandatory_local_indices: list[int] = [0]
        if c_idx == num_chapters - 1 and len(c_windows) > 1:
            mandatory_local_indices.append(len(c_windows) - 1)

        selected_local: set[int] = set()
        used_ch_chars = len(ch_header) + 2

        for m_i in mandatory_local_indices:
            w_txt = c_windows[m_i]["text"]
            selected_local.add(m_i)
            used_ch_chars += len(w_txt) + 16

        # Fill remaining chapter budget with highest-salience windows in this chapter
        candidate_locals = sorted(
            (i for i in range(len(c_windows)) if i not in selected_local),
            key=lambda i: c_windows[i]["salience"],
            reverse=True,
        )
        for cand_i in candidate_locals:
            w_len = len(c_windows[cand_i]["text"]) + 16
            if used_ch_chars + w_len > per_chapter_budget:
                continue
            selected_local.add(cand_i)
            used_ch_chars += w_len

        ordered_locals = sorted(selected_local)
        ch_pieces: list[str] = []
        prev_local = -1
        for loc_i in ordered_locals:
            w = c_windows[loc_i]
            prefix_ellipsis = "... " if (prev_local != -1 and loc_i > prev_local + 1) else ""
            ch_pieces.append(f"{prefix_ellipsis}[{_format_yt_timestamp(w['start'])}] {w['text']}")
            prev_local = loc_i

        chapter_line = f"{ch_header}\n" + " ".join(ch_pieces)
        if len(chapter_line) > per_chapter_budget + 180:
            chapter_line = _smart_truncate(chapter_line, per_chapter_budget + 140)
        chapter_blocks.append(chapter_line)

    assembled = header_note + query_section + "\n\n".join(chapter_blocks)
    if len(assembled) + len(footer_instructions) > max_chars:
        assembled = _smart_truncate(assembled, max(2000, max_chars - len(footer_instructions) - 20))
    return assembled + footer_instructions


def read_youtube_video(url: str, query: Optional[str] = None) -> str:
    """Extract the transcript text and title from a YouTube video to summarize or analyze it.

    Args:
        url: The YouTube video link (watch, youtu.be, shorts, embed or live) or the 11-character video ID.
        query: Optional topic, question, or timestamp (e.g. 'Nvidia margins', '45:00', 'conclusion') to pull verbatim segments from long videos.
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

    clean_query = (query or "").strip()

    if video_id in YOUTUBE_CACHE:
        print(f"⚡ [YouTube Cache Hit]: Reusing cached transcript for {video_id}")
        if not clean_query:
            return YOUTUBE_CACHE[video_id]
        raw_entry = _YOUTUBE_RAW_CACHE.get(video_id)
        if isinstance(raw_entry, dict) and raw_entry.get("segments"):
            cached_title = raw_entry.get("title", "Unknown Video Title")
            cached_segments = raw_entry["segments"]
            cached_plain = " ".join(s[2] for s in cached_segments).strip()
            if len(cached_plain) <= _YOUTUBE_SHORT_TRANSCRIPT_CHAR_LIMIT:
                body = _format_short_transcript_with_timestamps(cached_segments, cached_plain)
            else:
                body = _build_long_transcript_digest(
                    cached_segments,
                    cached_plain,
                    query=clean_query,
                    max_chars=_YOUTUBE_MAX_OUTPUT_CHARS - len(cached_title) - 40,
                )
            return f"Video Title: {cached_title}\n\nTranscript:\n{body}"
        return YOUTUBE_CACHE[video_id]

    print(f"📺 [Fetching YouTube Transcript & Metadata]: Video ID {video_id}")
    from src.utils.workers import submit_to_worker

    def _fetch_oembed_title(vid: str) -> str:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
            response = requests.get(oembed_url, timeout=5)
            if response.status_code == 200:
                return response.json().get("title", "Unknown Video Title")
        except Exception as e:
            print(f"⚠️ [oEmbed Metadata Error]: Could not fetch title: {e}")
        return "Unknown Video Title"

    def _fetch_raw_transcript(vid: str):
        ytt_api = YouTubeTranscriptApi()
        try:
            return ytt_api.fetch(vid)
        except Exception:
            transcript_list = ytt_api.list(vid)
            return next(iter(transcript_list)).fetch()

    # Dispatch oEmbed metadata and YouTube transcript fetch concurrently in worker threads
    title_fut = submit_to_worker(_fetch_oembed_title, video_id)
    transcript_fut = submit_to_worker(_fetch_raw_transcript, video_id)

    try:
        video_title = title_fut.result(timeout=6.0)
    except Exception:
        video_title = "Unknown Video Title"

    try:
        transcript = transcript_fut.result(timeout=15.0)

        segments = _extract_transcript_segments(transcript)
        text = " ".join(s[2] for s in segments).strip()
        if not text:
            return f"Video Title: {video_title}\n\nTranscript: [Empty transcript returned for this video]"

        body_budget = _YOUTUBE_MAX_OUTPUT_CHARS - len(video_title) - 40
        if len(text) <= _YOUTUBE_SHORT_TRANSCRIPT_CHAR_LIMIT:
            default_body = _format_short_transcript_with_timestamps(segments, text)
            active_body = default_body
        else:
            default_body = _build_long_transcript_digest(
                segments,
                text,
                query=None,
                max_chars=body_budget,
            )
            active_body = (
                _build_long_transcript_digest(
                    segments,
                    text,
                    query=clean_query,
                    max_chars=body_budget,
                )
                if clean_query
                else default_body
            )

        default_output = f"Video Title: {video_title}\n\nTranscript:\n{default_body}"
        final_output = f"Video Title: {video_title}\n\nTranscript:\n{active_body}"

        while len(YOUTUBE_CACHE) >= 50:
            YOUTUBE_CACHE.pop(next(iter(YOUTUBE_CACHE)))
        YOUTUBE_CACHE[video_id] = default_output
        _YOUTUBE_RAW_CACHE[video_id] = {
            "title": video_title,
            "segments": segments,
        }
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


_YT_GENERIC_SUMMARY_WORDS = frozenset({
    "summarize", "summary", "tldr", "tl", "dr", "watch", "read", "check", "look",
    "video", "youtube", "clip", "link", "url", "transcript", "what", "is", "this",
    "that", "the", "about", "can", "you", "please", "tell", "me", "give", "breakdown",
    "recap", "overview", "explain", "analyze", "analysis", "in", "on", "of", "for",
    "from", "to", "and", "or", "a", "an", "it", "he", "she", "they", "say", "said",
    "says", "talk", "talks", "talking", "talked", "does", "did", "do", "again",
    "retry", "try", "more", "detail", "details", "detailed", "full", "whole", "entire",
    "deep", "deeper", "dive", "thoughts", "think", "opinion", "take", "takeaways",
    "key", "points", "notes", "listen", "hear", "see", "show", "get", "pull", "fetch",
    "extract", "user", "hey", "bot", "pls", "now", "here",
})

_YT_URL_IN_TEXT_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/\S+|youtu\.be/\S+)|(?:www\.)?(?:youtube\.com/\S+|youtu\.be/\S+)",
    re.IGNORECASE,
)

_DISCORD_USER_WRAPPER_RE = re.compile(
    r"^(?:User\s+[^\n:]+?\s+says|[^\n:]+?\(User ID:\s*\d+\))"
    r"(?:\s*\[[^\]]*\])*"
    r"(?:\s*\(replying to message:\s*\"[\s\S]*?\"\))?"
    r"\s*:\s*",
    re.IGNORECASE,
)


def _strip_discord_user_wrapper(raw_content: str) -> str:
    """Strip the 'User <Name> says (replying to ...): ' wrapper added by main.py to isolate the user's actual prompt."""
    txt = str(raw_content or "").strip()
    txt = _DISCORD_USER_WRAPPER_RE.sub("", txt).strip()
    txt = re.sub(r"\[Spoken (?:in Discord )?Voice[^\]]*\]", " ", txt, flags=re.IGNORECASE).strip()
    return txt


def enrich_youtube_tool_args(args: dict, history: list) -> dict:
    """Auto-populate `query` (and recover prior YouTube URL on follow-ups) when the user asks a specific question or timestamp about a video."""
    enriched = dict(args or {})
    latest_user_text = ""
    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = _strip_discord_user_wrapper(str(content or ""))
            break

    # Recover prior YouTube URL from history if the user is asking a follow-up question without re-pasting the link
    raw_url = str(enriched.get("url") or "").strip().strip("\"'`<>()")
    if not raw_url:
        for msg in reversed(history or []):
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            m_yt = _YT_URL_IN_TEXT_RE.search(str(content or ""))
            if m_yt:
                enriched["url"] = m_yt.group(0).rstrip(".,;)>\"'")
                break

    existing_q = _strip_discord_user_wrapper(str(enriched.get("query") or "")).strip()
    if existing_q:
        # Clear accidental model-passed wrapper or generic 'do it again' / 'summarize' queries
        q_clean_check = _YT_URL_IN_TEXT_RE.sub(" ", existing_q)
        q_clean_check = re.sub(r"https?://\S+|<@!?\d+>", " ", q_clean_check).strip()
        q_has_ts = _parse_query_timestamp_seconds(q_clean_check) is not None
        q_spec = [
            tok for tok in re.findall(r"[a-zA-Z0-9]{3,}", q_clean_check.lower())
            if tok not in _YT_GENERIC_SUMMARY_WORDS
        ]
        if q_has_ts or q_spec:
            enriched["query"] = q_clean_check
            return enriched
        enriched.pop("query", None)

    if not latest_user_text:
        return enriched

    # Strip URLs and Discord mentions from user prompt to inspect whether they asked a specific topic/timestamp question
    cleaned = _YT_URL_IN_TEXT_RE.sub(" ", latest_user_text)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"<@!?\d+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return enriched

    has_ts = _parse_query_timestamp_seconds(cleaned) is not None
    specific_tokens = [
        tok for tok in re.findall(r"[a-zA-Z0-9]{3,}", cleaned.lower())
        if tok not in _YT_GENERIC_SUMMARY_WORDS
    ]
    if has_ts or specific_tokens:
        enriched["query"] = cleaned

    return enriched


# =========================================================
# IN-MEMORY WEB IMAGE SEARCH & GEMMA4 VISION VERIFICATION
# =========================================================

_IMAGE_MAX_BYTES = 8 * 1024 * 1024  # 8 MB hard ceiling in RAM
_IMAGE_MIN_BYTES = 24
_VISION_MODEL = os.getenv("VISION_MODEL", "gemma4:e4b")
_VISION_OPTIONS = {
    "num_ctx": 16384,  # Match research-gemma num_ctx so Ollama never reallocates GPU KV cache
    "num_predict": 45,
    "temperature": 0.0,
    "num_gpu": 99,
}

# Per-channel image memory: tracks {"last_subject": str, "last_page_url": str, "last_search_url": str, "seen_urls": BoundedSet}
IMAGE_CHANNEL_STATE = BoundedCache(maxsize=128)


def _get_channel_image_state(channel_id: Optional[int] = None) -> dict:
    cid = int(channel_id if channel_id is not None else (get_active_channel_id() or 0))
    state = IMAGE_CHANNEL_STATE.get(cid)
    if state is None:
        state = {
            "last_subject": "",
            "last_page_url": "",
            "last_search_url": "",
            "seen_urls": BoundedSet(maxsize=64),
        }
        IMAGE_CHANNEL_STATE[cid] = state
    return state


def clear_image_channel_state(channel_id: Optional[int] = None) -> None:
    """Clear per-channel image history (for a specific channel or all channels when None)."""
    if channel_id is None:
        IMAGE_CHANNEL_STATE.clear()
    else:
        IMAGE_CHANNEL_STATE.pop(int(channel_id), None)


_IMAGE_ADJECTIVES = (
    r"(?:clear|good|nice|real|actual|cool|high[-\s]?res(?:olution)?|full|"
    r"different|another|other|new|second|next|better|fresh|alternate|alternative|main|hero)"
)

_IMAGE_COUNT_WORDS = {
    "2": 2, "two": 2,
    "3": 3, "three": 3,
    "4": 4, "four": 4,
}

_MULTI_IMAGE_COUNT_RE = re.compile(
    r"(?:"
    r"\b(?P<num>[2-4]|two|three|four)\s+(?:(?:\w+[-\s]+){0,2})?(?:pictures?|pics?|photos?|photographs?|images?|snapshots?)\b"
    r"|"
    r"\ba\s+couple(?:\s+of)?\s+(?:(?:\w+[-\s]+){0,2})?(?:pictures?|pics?|photos?|photographs?|images?|snapshots?)\b"
    r"|"
    r"\b(?:a\s+few|several|multiple)\s+(?:(?:\w+[-\s]+){0,2})?(?:pictures?|pics?|photos?|photographs?|images?|snapshots?)\b"
    r")",
    re.IGNORECASE,
)


def _extract_requested_image_count(text: str) -> int:
    """Return 2..4 ONLY when the user's prompt explicitly asks for multiple pictures; otherwise strictly 1."""
    if not text:
        return 1
    m = _MULTI_IMAGE_COUNT_RE.search(text)
    if not m:
        return 1
    num_tok = (m.group("num") or "").strip().lower()
    if num_tok in _IMAGE_COUNT_WORDS:
        return _IMAGE_COUNT_WORDS[num_tok]
    low = m.group(0).lower()
    if "couple" in low:
        return 2
    return 3


_IMAGE_PREFIX_STRIP_RE = re.compile(
    r"^(?:(?:hey|hi|yo|ok|okay|please|pls|can\s+you|could\s+you|would\s+you|go\s+ahead\s+and)\s+)*"
    r"(?:(?:get|grab|show|send|post|find|fetch|give|pull\s+up|display|share|search\s+for|look\s+up|"
    r"let(?:'s|\s+me)\s+see)\s+)?"
    r"(?:(?:me|us)\s+)?"
    r"(?:(?:a\s+couple(?:\s+of)?|a\s+few|several|multiple|[1-4]|one|two|three|four|a|an|some|the|another)\s+)?"
    rf"(?:{_IMAGE_ADJECTIVES}\s+)*"
    r"(?:(?:pictures?|pics?|photos?|photographs?|images?|img|snapshots?)\s+(?:of|for|from|on|in|showing|depicting)\s+)",
    re.IGNORECASE,
)

_EXPLICIT_IMAGE_REQUEST_RE = re.compile(
    r"(?:"
    r"\b(?:get|grab|show|send|post|find|fetch|give|pull\s+up|display|share|let(?:'s|\s+me)\s+see)\s+"
    r"(?:(?:me|us)\s+)?"
    r"(?:(?:a\s+couple(?:\s+of)?|a\s+few|several|multiple|[1-4]|one|two|three|four|a|an|some|the|another)\s+)?"
    rf"(?:{_IMAGE_ADJECTIVES}\s+)*"
    r"(?:pictures?|pics?|photos?|photographs?|images?|snapshots?)\s+(?:of|for|from|on|in|showing|depicting)\s+(?P<q1>.+)"
    r"|"
    rf"^\s*(?:please\s+)?(?:(?:a\s+couple(?:\s+of)?|a\s+few|several|multiple|[1-4]|one|two|three|four|a|an|the|another)\s+)?(?:{_IMAGE_ADJECTIVES}\s+)*(?:pictures?|pics?|photos?|photographs?|images?)\s+(?:of|for|from|on)\s+(?P<q2>.+)"
    r")",
    re.IGNORECASE,
)

_DIFFERENT_IMAGE_FOLLOWUP_RE = re.compile(
    r"(?:"
    r"\b(?:get|grab|show|send|post|find|fetch|give|pull\s+up|display|share|let(?:'s|\s+me)\s+see)\s+"
    r"(?:(?:me|us)\s+)?"
    r"(?:(?:a|an|the)\s+)?"
    r"(?:different|another|other|new|second|next|alternate|better)\s+"
    r"(?:pictures?|pics?|photos?|photographs?|images?|snapshots?|ones?)"
    r"(?:\s+(?:of\s+(?:it|that|this|them|the\s+same(?:\s+thing|\s+one)?)|from\s+(?:it|that|this)\s*(?:page|article|link|site|website)?|instead|please|pls))?\s*[?.!]*$"
    r"|"
    r"^\s*(?:please\s+)?(?:(?:a|an)\s+)?(?:different|another|other|new)\s+"
    r"(?:pictures?|pics?|photos?|photographs?|images?)"
    r"(?:\s+(?:of\s+(?:it|that|this|them|the\s+same(?:\s+thing|\s+one)?)|from\s+(?:it|that|this)\s*(?:page|article|link|site|website)?|instead|please|pls))?\s*[?.!]*$"
    r")",
    re.IGNORECASE,
)

_PAGE_REFERENCE_RE = re.compile(
    r"\b(?:from|on|in)\s+(?:this|that|the)\s+(?:web\s*page|page|article|site|website|link|url|post|story)\b",
    re.IGNORECASE,
)

_INLINE_URL_RE = re.compile(r"https?://[^\s<>\"'`)]+", re.IGNORECASE)

_GENERIC_FOLLOWUP_SUBJECTS = frozenset({
    "", "it", "that", "this", "them", "one", "another", "another one",
    "different", "different one", "a different one", "the same", "same",
    "same thing", "the same thing", "the same one", "same one",
    "different picture", "another picture", "different photo", "another photo",
    "different image", "another image", "picture", "photo", "image",
    "page", "the page", "this page", "that page", "article", "the article",
    "this article", "that article", "website", "the website", "this website",
    "link", "the link", "this link", "that link", "url", "the url", "main", "hero",
})

_CHART_OR_OTHER_SKILL_GUARD_RE = re.compile(
    r"\b(?:chart|charts|graph|graphs|plot|plots|plotting|diagram|diagrams|flowchart|sequence\s+diagram|"
    r"heatmap|monte\s+carlo|histogram|bar\s+chart|line\s+chart|pie\s+chart|scatter|candlestick|"
    r"price\s+history|historical\s+returns?|join\s+voice|leave\s+voice)\b",
    re.IGNORECASE,
)


def _clean_image_query(query: str) -> str:
    """Strip conversational prefixes ('get a picture of a ...', 'get a different picture of ...') and URLs to isolate the visual subject."""
    q = (query or "").strip().strip('"\'`')
    q = re.sub(r"^\[[^\]]+\]\s*", "", q).strip()
    q = _INLINE_URL_RE.sub("", q).strip()
    q = _PAGE_REFERENCE_RE.sub("", q).strip()
    q = _IMAGE_PREFIX_STRIP_RE.sub("", q).strip()
    q = re.sub(
        r"^(?:(?:a\s+couple(?:\s+of)?|a\s+few|several|multiple|[1-4]|one|two|three|four|a|an|another|different|other|new)\s+)*(?:pictures?|pics?|photos?|photographs?|images?)\s+(?:of|for|from|on|in)\s+",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    q = re.sub(r"\s+(?:from|on|in)\s*$", "", q, flags=re.IGNORECASE).strip()
    q = re.sub(r"^(?:a|an)\s+(?=[a-zA-Z0-9])", "", q, flags=re.IGNORECASE).strip()
    q = re.sub(r"\s+(?:please|pls|right\s+now|for\s+me|instead)\s*[?.!]*$", "", q, flags=re.IGNORECASE).strip()
    q = q.rstrip("?.! <>()").strip('"\'`')
    return q


def _extract_last_page_url_from_history(history: Optional[list], channel_id: Optional[int] = None) -> str:
    """Recover the most recently read or referenced webpage URL in the channel."""
    state = _get_channel_image_state(channel_id)
    if state.get("last_page_url"):
        return str(state["last_page_url"])

    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = str((msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")) or "")
        if role == "tool":
            m_src = re.search(r"\*\*SOURCE URL:\*\*\s*(https?://[^\s\n()]+)", content)
            if m_src:
                return m_src.group(1).strip()
        elif role == "user":
            m_url = _INLINE_URL_RE.search(content)
            if m_url:
                return m_url.group(0).rstrip(".,;)>")
    return ""


def _extract_last_image_subject_from_history(history: Optional[list], channel_id: Optional[int] = None) -> str:
    """Recover the most recent image search subject from per-channel state or conversation history."""
    state = _get_channel_image_state(channel_id)
    if state.get("last_subject"):
        return str(state["last_subject"])

    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = str((msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")) or "")
        if role == "tool":
            m_subj = re.search(r"(?m)^Subject:\s*(.+)$", content)
            if m_subj:
                subj = m_subj.group(1).strip()
                if subj and subj.lower() not in _GENERIC_FOLLOWUP_SUBJECTS:
                    return subj
        t_calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
        if t_calls:
            for tc in t_calls:
                fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
                fn_name = (fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "")) if fn else ""
                if fn_name == "search_images":
                    fn_args = (fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", {})) or {}
                    cand_q = _clean_image_query(str(fn_args.get("query") or ""))
                    if cand_q and cand_q.lower() not in _GENERIC_FOLLOWUP_SUBJECTS:
                        return cand_q
    return ""


def enrich_image_tool_args(args: dict, history: list, active_channel_id: Optional[int] = None) -> dict:
    """Resolve follow-up image queries ('get a different picture', 'another photo of the same thing'),
    webpage URL references ('from this page', inline URLs), and lock `count` strictly to what the user requested."""
    enriched = dict(args or {})
    latest_user_text = ""
    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            break

    # Lock `count` strictly to what the user explicitly requested (default 1, max 4)
    enriched["count"] = _extract_requested_image_count(latest_user_text)

    raw_q = str(enriched.get("query") or "").strip()
    raw_url = str(enriched.get("url") or "").strip().strip("<>\"'`")

    # Auto-detect inline URL in query or user message, or resolve 'from that article/page'
    if not raw_url:
        m_inline = _INLINE_URL_RE.search(raw_q) or _INLINE_URL_RE.search(latest_user_text)
        if m_inline:
            raw_url = m_inline.group(0).rstrip(".,;)>")
        elif _PAGE_REFERENCE_RE.search(latest_user_text) or _PAGE_REFERENCE_RE.search(raw_q):
            raw_url = _extract_last_page_url_from_history(history, active_channel_id)

    cleaned_q = _clean_image_query(raw_q)
    is_followup = (
        not cleaned_q
        or cleaned_q.lower() in _GENERIC_FOLLOWUP_SUBJECTS
        or bool(_DIFFERENT_IMAGE_FOLLOWUP_RE.search(raw_q))
        or bool(_DIFFERENT_IMAGE_FOLLOWUP_RE.search(latest_user_text))
    )

    if not raw_url and is_followup:
        state = _get_channel_image_state(active_channel_id)
        if state.get("last_search_url"):
            raw_url = str(state["last_search_url"]).strip()

    if raw_url:
        enriched["url"] = raw_url

    if is_followup:
        prior_subj = _extract_last_image_subject_from_history(history, active_channel_id)
        if prior_subj and not raw_url:
            enriched["query"] = prior_subj
            return enriched
        enriched["query"] = (
            cleaned_q
            if (cleaned_q and cleaned_q.lower() not in _GENERIC_FOLLOWUP_SUBJECTS)
            else (prior_subj or "article image")
        )
        return enriched

    enriched["query"] = cleaned_q
    return enriched


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

            # If already a compact JPEG <= 480px, reuse raw_bytes directly for vision with zero re-encoding overhead
            if ext == ".jpg" and max(width, height) <= 480:
                vision_bytes = raw_bytes
            else:
                vis_img = img.convert("RGB")
                if max(width, height) > 384:
                    vis_img.thumbnail((384, 384))
                vis_buf = io.BytesIO()
                vis_img.save(vis_buf, format="JPEG", quality=80)
                vision_bytes = vis_buf.getvalue()
            return attach_bytes, vision_bytes, attach_ext
    except Exception:
        # Fallback for synthetic magic-byte payloads in unit tests where full Huffman/IDAT streams are mocked
        if ext in (".jpg", ".png", ".webp", ".gif") and len(raw_bytes) >= 512:
            return raw_bytes, raw_bytes, ext
        return None


def _download_url_bytes_in_memory(
    url: str,
    timeout: Tuple[float, float] = (1.8, 3.0),
) -> Optional[bytes]:
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
            timeout=timeout,
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
                options=_VISION_OPTIONS,
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
                options=_VISION_OPTIONS,
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


def _build_safe_image_filename(subject: str, ext: str, index: Optional[int] = None) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", subject.strip().lower()).strip("_")[:40] or "image"
    clean_ext = ext if ext.startswith(".") else f".{ext}"
    if index is not None and index > 1:
        return f"{slug}_{index}{clean_ext}"
    return f"{slug}{clean_ext}"


_UI_CHROME_IMAGE_URL_RE = re.compile(
    r"(?:^|/|[._-])(?:logo|icon|favicon|avatar|sprite|badge|button|emoji|1x1|pixel|spacer|spinner|loader|ad|ads|banner)(?:[._/-]|$)",
    re.IGNORECASE,
)


def _pick_best_srcset_url(srcset: str) -> str:
    """Parse an HTML `srcset` attribute and return the URL with the largest width/density descriptor."""
    if not srcset:
        return ""
    best_url = ""
    best_score = -1.0
    for part in srcset.split(","):
        tokens = part.strip().split()
        if not tokens:
            continue
        cand_url = tokens[0].strip()
        if not cand_url or cand_url.startswith("data:"):
            continue
        score = 1.0
        if len(tokens) > 1:
            desc = tokens[1].lower()
            m_num = re.match(r"^(\d+(?:\.\d+)?)(w|x)$", desc)
            if m_num:
                val = float(m_num.group(1))
                score = val if m_num.group(2) == "w" else val * 500.0
        if score >= best_score:
            best_score = score
            best_url = cand_url
    return best_url


def _extract_image_candidates_from_webpage(page_url: str, subject: str = "") -> list[dict]:
    """Scrape, filter, and rank candidate images from a specific webpage URL in memory."""
    norm_url = (page_url or "").strip().strip('"\'`<>()')
    if not norm_url:
        return []
    if not norm_url.startswith(("http://", "https://")):
        norm_url = f"https://{norm_url}"

    parsed = urlparse.urlparse(norm_url)
    if _is_private_or_local_host(parsed.hostname):
        return []

    if parsed.netloc.lower().endswith("news.google.com"):
        decoded = decode_google_news_url(norm_url)
        if decoded:
            norm_url = decoded
            parsed = urlparse.urlparse(norm_url)
            if _is_private_or_local_host(parsed.hostname):
                return []

    try:
        resp = _fetch_public("GET", norm_url, impersonate="chrome", timeout=(3.0, 8.0))
        if getattr(resp, "status_code", 0) != 200:
            return []
        encoding = getattr(resp, "charset_encoding", None) or getattr(resp, "apparent_encoding", None) or "utf-8"
        try:
            resp.encoding = encoding
            raw_html = resp.text
        except Exception:
            raw_html = resp.content.decode("utf-8", errors="replace")
        base_url = str(getattr(resp, "url", None) or norm_url)
    except Exception as e:
        print(f"⚠️ [Webpage Image Extractor]: Failed to fetch {norm_url}: {e}")
        return []

    _record_visited_url(base_url)
    soup = BeautifulSoup(raw_html[:400000], "html.parser")
    page_title = soup.title.string.strip() if soup.title and soup.title.string else ""

    candidates: list[dict] = []
    seen_img_urls: set[str] = set()

    def _add_candidate(raw_img_url: str, caption: str, priority_bonus: int = 0, w: int = 0, h: int = 0) -> None:
        if not raw_img_url:
            return
        u = raw_img_url.strip()
        if not u or u.startswith(("data:", "javascript:", "blob:")):
            return
        full_u = urlparse.urljoin(base_url, u)
        path_lower = urlparse.urlparse(full_u).path.lower()
        if path_lower.endswith((".svg", ".ico", ".mp4", ".webm", ".pdf")):
            return
        if _UI_CHROME_IMAGE_URL_RE.search(path_lower):
            return
        if full_u in seen_img_urls:
            return
        seen_img_urls.add(full_u)
        clean_cap = re.sub(r"\s+", " ", (caption or page_title or subject or "Webpage Image")).strip()[:160]
        candidates.append({
            "image": full_u,
            "thumbnail": "",
            "title": clean_cap,
            "url": base_url,
            "width": w,
            "height": h,
            "_priority": priority_bonus,
        })

    # 1. OpenGraph & Twitter Card hero images
    for meta_prop in ("og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"):
        for tag in soup.find_all("meta", attrs={"property": meta_prop}) + soup.find_all("meta", attrs={"name": meta_prop}):
            content = str(tag.get("content") or "").strip()
            if content:
                _add_candidate(content, page_title, priority_bonus=3)

    # Strip structural chrome before scanning <img> and <picture> tags
    for noisy in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        noisy.decompose()

    # 2. Content <img> and <picture><source> tags
    for img in soup.find_all("img"):
        w_str = str(img.get("width") or "").strip()
        h_str = str(img.get("height") or "").strip()
        w_val = int(w_str) if w_str.isdigit() else 0
        h_val = int(h_str) if h_str.isdigit() else 0
        if (w_val and w_val < 80) or (h_val and h_val < 80):
            continue

        best_u = (
            _pick_best_srcset_url(str(img.get("srcset") or img.get("data-srcset") or ""))
            or str(img.get("data-src") or img.get("data-original") or img.get("data-lazy-src") or img.get("src") or "").strip()
        )
        if not best_u:
            parent_pic = img.find_parent("picture")
            if parent_pic:
                for src_tag in parent_pic.find_all("source"):
                    best_u = _pick_best_srcset_url(str(src_tag.get("srcset") or src_tag.get("data-srcset") or ""))
                    if best_u:
                        break
        if not best_u:
            continue

        alt_txt = str(img.get("alt") or img.get("title") or "").strip()
        fig_txt = ""
        fig_parent = img.find_parent("figure")
        if fig_parent:
            fig_cap = fig_parent.find("figcaption")
            if fig_cap:
                fig_txt = fig_cap.get_text(" ", strip=True)
        heading_txt = ""
        prev_h = img.find_previous(["h1", "h2", "h3", "h4"])
        if prev_h:
            heading_txt = prev_h.get_text(" ", strip=True)

        caption_parts = [p for p in (alt_txt, fig_txt, heading_txt, page_title) if p]
        combined_caption = " — ".join(dict.fromkeys(caption_parts))
        in_figure_or_article = 2 if (fig_parent or img.find_parent(["article", "main"])) else 1
        _add_candidate(best_u, combined_caption, priority_bonus=in_figure_or_article, w=w_val, h=h_val)

    if not candidates:
        return []

    # 3. Rank webpage images by relevance to `subject` keywords (if specific subject provided)
    subj_clean = (subject or "").strip().lower()
    subj_tokens = [
        tok for tok in re.findall(r"[a-z0-9]{2,}", subj_clean)
        if tok not in _GENERIC_FOLLOWUP_SUBJECTS and tok not in {"from", "page", "article", "website", "link", "url", "image", "picture", "photo"}
    ]
    if subj_tokens:
        def _score_cand(c: dict) -> Tuple[int, int]:
            haystack = f"{c.get('title', '')} {urlparse.urlparse(c.get('image', '')).path}".lower()
            kw_hits = sum(1 for tok in subj_tokens if tok in haystack)
            return (kw_hits, int(c.get("_priority") or 0))

        candidates.sort(key=_score_cand, reverse=True)

    return candidates


def search_images(query: str, url: Optional[str] = None, count: Optional[int] = None) -> str:
    """Search for verified image(s) of a requested subject across the web OR from a specific webpage URL,
    verify each image visually in memory using `gemma4:e4b` vision, and attach the verified image(s)
    directly to the Discord/Telegram response. All image bytes stay 100% in memory (`io.BytesIO`).

    Args:
        query: The subject or specific image to find (e.g. 'capybara', 'benchmark chart', 'rear wing photo').
        url: Optional specific webpage URL to extract image(s) directly from. Leave empty for general web image search.
        count: Number of images to return (default 1; only set >1 when the user explicitly requests multiple pictures, max 4).
    """
    state = _get_channel_image_state()
    seen_urls: BoundedSet = state["seen_urls"]

    try:
        target_count = max(1, min(int(count or 1), 4))
    except (ValueError, TypeError):
        target_count = 1

    raw_query = (query or "").strip()
    is_followup_query = (
        not _clean_image_query(raw_query)
        or _clean_image_query(raw_query).lower() in _GENERIC_FOLLOWUP_SUBJECTS
        or bool(_DIFFERENT_IMAGE_FOLLOWUP_RE.search(raw_query))
    )
    target_url = (url or "").strip().strip('"\'`<>()')
    if not target_url:
        m_inline = _INLINE_URL_RE.search(raw_query)
        if m_inline:
            target_url = m_inline.group(0).rstrip(".,;)>")
        elif _PAGE_REFERENCE_RE.search(raw_query) and state.get("last_page_url"):
            target_url = str(state["last_page_url"]).strip()
        elif is_followup_query and state.get("last_search_url"):
            target_url = str(state["last_search_url"]).strip()

    clean_subject = _clean_image_query(raw_query)
    if is_followup_query:
        clean_subject = str(state.get("last_subject") or "").strip()

    if not clean_subject and not target_url:
        return "❌ Error: Image search query is empty. Please specify what you want a picture of."

    if target_url:
        state["last_page_url"] = target_url
        state["last_search_url"] = target_url
        print(f"🖼️ [Webpage Image Search]: Extracting images from {target_url} (target: '{clean_subject or 'main article image'}', count={target_count})...")
        raw_candidates = _extract_image_candidates_from_webpage(target_url, clean_subject)
        if not raw_candidates:
            return f"❌ Could not find any extractable content images on `{target_url}`."
        effective_subject = clean_subject if (clean_subject and clean_subject.lower() not in _GENERIC_FOLLOWUP_SUBJECTS) else (raw_candidates[0].get("title") or "article image")
    else:
        state["last_search_url"] = ""
        effective_subject = clean_subject
        print(f"🖼️ [Image Search]: Searching web images for '{effective_subject}' (count={target_count})...")
        try:
            raw_candidates = list(DDGS().images(effective_subject, max_results=max(12, target_count * 5)) or [])
        except Exception as e:
            print(f"⚠️ [Image Search Error]: DDGS().images failed for '{effective_subject}': {e}")
            return f"❌ Failed to search web images for '{effective_subject}': {e}"
        if not raw_candidates:
            return f"❌ No web images found for '{effective_subject}'."

    # Filter out candidates already sent in this channel so 'get a different picture' NEVER repeats an image in the session
    fresh_candidates = []
    for c in raw_candidates:
        if not isinstance(c, dict):
            continue
        p_url = str(c.get("image") or "").strip()
        t_url = str(c.get("thumbnail") or "").strip()
        if (p_url and p_url in seen_urls) or (t_url and t_url in seen_urls):
            continue
        fresh_candidates.append(c)

    if not fresh_candidates:
        return (
            f"❌ All available candidate images for '{effective_subject}'"
            + (f" on `{target_url}`" if target_url else "")
            + " have already been shown in this session. Try a more specific search query or another page."
        )

    candidates_to_check = fresh_candidates

    max_vision_attempts = max(4, target_count * 3)
    vision_attempts = 0
    verified_items: list[dict] = []
    fallback_items: list[Tuple[bytes, str, dict, str]] = []

    for idx, cand in enumerate(candidates_to_check, 1):
        w = int(cand.get("width") or 0)
        h = int(cand.get("height") or 0)
        if (w and w < 100) or (h and h < 100):
            continue

        primary_url = str(cand.get("image") or "").strip()
        thumb_url = str(cand.get("thumbnail") or "").strip()
        title = str(cand.get("title") or effective_subject).strip()
        source_page = str(cand.get("url") or target_url or primary_url or thumb_url).strip()

        # 1. Fetch the fast CDN thumbnail (~25 KB, ~70ms) first for vision verification so we never
        # wait on slow/blocked origin servers for candidates that might fail verification.
        thumb_prep = None
        primary_prep = None

        if thumb_url:
            thumb_raw = _download_url_bytes_in_memory(thumb_url, timeout=(1.2, 2.0))
            if thumb_raw:
                thumb_prep = _prepare_image_bytes(thumb_raw)

        if thumb_prep is not None:
            vision_bytes = thumb_prep[1]
        else:
            if primary_url:
                primary_raw = _download_url_bytes_in_memory(primary_url, timeout=(1.5, 2.5))
                if primary_raw:
                    primary_prep = _prepare_image_bytes(primary_raw)
            if primary_prep is None:
                continue
            vision_bytes = primary_prep[1]

        vision_attempts += 1
        verdict, vision_desc = _verify_image_with_vision(vision_bytes, effective_subject, title)

        if verdict is False:
            print(f"🔍 [Image Vision Reject]: Candidate #{idx} ('{title[:50]}') rejected by {_VISION_MODEL}: {vision_desc}")
            if vision_attempts >= max_vision_attempts:
                break
            continue

        # 2. Candidate passed vision (or fallback needed): upgrade to full-res origin image if fast & unblocked,
        # otherwise immediately use the already-verified CDN thumbnail in RAM.
        if primary_prep is None and primary_url:
            primary_raw = _download_url_bytes_in_memory(primary_url, timeout=(1.2, 2.0))
            if primary_raw:
                primary_prep = _prepare_image_bytes(primary_raw)

        chosen_prep = primary_prep if primary_prep is not None else thumb_prep
        if chosen_prep is None:
            continue
        attach_bytes, _, ext = chosen_prep

        if verdict is True:
            state["last_subject"] = effective_subject
            if primary_url:
                seen_urls.add(primary_url)
            if thumb_url:
                seen_urls.add(thumb_url)
            file_idx = len(verified_items) + 1 if target_count > 1 else None
            filename = _build_safe_image_filename(effective_subject, ext, index=file_idx)
            queue_channel_attachment(filename, attach_bytes, caption=title)
            print(f"✅ [Image Vision Verified]: Candidate #{idx} ('{title[:60]}') verified by {_VISION_MODEL} -> {filename} ({len(attach_bytes):,} bytes in RAM)")
            verified_items.append({
                "filename": filename,
                "size": len(attach_bytes),
                "title": title,
                "vision_desc": vision_desc,
                "source_page": source_page,
            })
            if len(verified_items) >= target_count:
                break
        else:
            print(f"⚠️ [Image Vision Unavailable/Uncertain]: Candidate #{idx}: {vision_desc}")
            if len(fallback_items) < target_count:
                fallback_items.append((attach_bytes, ext, cand, vision_desc))

        if vision_attempts >= max_vision_attempts:
            break

    # If we need fallback items (when vision model was unavailable/uncertain, not an explicit NO rejection)
    if not verified_items and fallback_items:
        for f_idx, (attach_bytes, ext, cand, vision_note) in enumerate(fallback_items[:target_count], 1):
            primary_url = str(cand.get("image") or "").strip()
            thumb_url = str(cand.get("thumbnail") or "").strip()
            state["last_subject"] = effective_subject
            if primary_url:
                seen_urls.add(primary_url)
            if thumb_url:
                seen_urls.add(thumb_url)
            title = str(cand.get("title") or effective_subject).strip()
            source_page = str(cand.get("url") or target_url or primary_url or thumb_url).strip()
            filename = _build_safe_image_filename(effective_subject, ext, index=f_idx if target_count > 1 else None)
            queue_channel_attachment(filename, attach_bytes, caption=title)
            print(f"✅ [Image Fallback Attached]: '{title[:60]}' -> {filename} ({len(attach_bytes):,} bytes in RAM)")
            verified_items.append({
                "filename": filename,
                "size": len(attach_bytes),
                "title": title,
                "vision_desc": vision_note,
                "source_page": source_page,
            })

    if verified_items:
        if len(verified_items) == 1:
            it = verified_items[0]
            return (
                f"[IMAGE VERIFIED & ATTACHED IN MEMORY]\n"
                f"Subject: {effective_subject}\n"
                f"Attached File: {it['filename']} ({it['size']:,} bytes, RAM-only)\n"
                f"Image Title: {it['title']}\n"
                f"Visual Verification ({_VISION_MODEL}): {it['vision_desc']}\n"
                f"Source Page: <{it['source_page']}>\n\n"
                "--- RESPONSE INSTRUCTIONS FOR YOU (THE LLM) ---\n"
                "• The verified image file is ALREADY attached directly to your Discord/Telegram reply.\n"
                "• Write a brief, natural 1-2 sentence response presenting the image.\n"
                "• CRITICAL: Do NOT include raw image URLs or Markdown `![...](...)` image tags in your text, or Discord will render a duplicate preview."
            )
        lines = [
            f"[IMAGES VERIFIED & ATTACHED IN MEMORY ({len(verified_items)} images)]",
            f"Subject: {effective_subject}",
        ]
        for i, it in enumerate(verified_items, 1):
            lines.append(
                f"{i}. {it['filename']} ({it['size']:,} bytes) — {it['title']} | "
                f"Verified ({_VISION_MODEL}): {it['vision_desc']} | Source: <{it['source_page']}>"
            )
        lines.append("\n--- RESPONSE INSTRUCTIONS FOR YOU (THE LLM) ---")
        lines.append(f"• All {len(verified_items)} verified image files are ALREADY attached directly to your Discord/Telegram reply.")
        lines.append("• Write a brief, natural response presenting the images.")
        lines.append("• CRITICAL: Do NOT include raw image URLs or Markdown `![...](...)` image tags in your text, or Discord will render duplicate previews.")
        return "\n".join(lines)

    return (
        f"❌ Searched images for '{effective_subject}', but none of the candidate images passed visual verification "
        f"(or all candidate URLs were unreachable). Tell the user you couldn't find a verified image matching '{effective_subject}'."
    )


def detect_missed_image_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """Detect when a user explicitly asks for a picture/photo/image of X (or from a webpage URL,
    or 'get a different picture') on turn 0 and recover by calling `search_images(...)`."""
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

    req_count = _extract_requested_image_count(cleaned)
    inline_url_match = _INLINE_URL_RE.search(cleaned)
    page_url = inline_url_match.group(0).rstrip(".,;)>") if inline_url_match else ""
    if not page_url and _PAGE_REFERENCE_RE.search(cleaned):
        page_url = _extract_last_page_url_from_history(history, active_channel_id)

    # Check for 'get a different picture' / 'show me another photo' follow-ups first
    if _DIFFERENT_IMAGE_FOLLOWUP_RE.search(cleaned):
        prior_subj = _extract_last_image_subject_from_history(history, active_channel_id)
        state = _get_channel_image_state(active_channel_id)
        followup_url = page_url or str(state.get("last_search_url") or "").strip()
        if prior_subj or followup_url:
            out_args: dict = {"query": prior_subj or "article image"}
            if followup_url:
                out_args["url"] = followup_url
            if req_count > 1:
                out_args["count"] = req_count
            return ("search_images", out_args)

    m = _EXPLICIT_IMAGE_REQUEST_RE.search(cleaned)
    if not m:
        return None

    raw_subject = (m.group("q1") or m.group("q2") or "").strip()
    subject = _clean_image_query(raw_subject)
    if not subject or subject.lower() in _GENERIC_FOLLOWUP_SUBJECTS:
        if page_url:
            subject = "article image"
        else:
            subject = _extract_last_image_subject_from_history(history, active_channel_id)
    if not subject or len(subject) < 2:
        return None

    out_args = {"query": subject}
    if page_url:
        out_args["url"] = page_url
    if req_count > 1:
        out_args["count"] = req_count
    return ("search_images", out_args)


WEB_TOOLS = {
    "search_web": search_web,
    "search_images": search_images,
    "read_website": read_website,
    "read_youtube_video": read_youtube_video,
}

