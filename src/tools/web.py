import ipaddress
import re
import socket
import requests
import urllib.parse as urlparse
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from curl_cffi.requests.errors import RequestsError
from ddgs import DDGS
from markdownify import markdownify as md
from requests.exceptions import RequestException
from youtube_transcript_api import YouTubeTranscriptApi

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

VISITED_URLS = set()
URL_CACHE = {}

def _is_private_or_local_host(hostname: str) -> bool:
    """Check if a hostname resolves to a local, private, loopback, or metadata IP."""
    if not hostname:
        return True
    clean_host = hostname.strip().lower().split(':')[0]
    if clean_host in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
        return True
    if clean_host.endswith(('.local', '.internal', '.lan', '.localdomain')):
        return True
    try:
        ip = ipaddress.ip_address(clean_host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    try:
        resolved_ip = socket.gethostbyname(clean_host)
        ip = ipaddress.ip_address(resolved_ip)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except Exception:
        return False

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

def search_web(query: str) -> str:
    """Search the web for real-time information, current product prices, releases, hardware specs, news, events and facts. Returns the top snippets and links. Use `read_website` on a specific URL if you need more details."""
    global VISITED_URLS
    print(f"🔎 [Web Search]: {query}")
    try:
        results = list(DDGS().text(query, max_results=7))
        if not results:
            return "No search results found."

        output = f"=== SEARCH RESULTS FOR '{query}' ===\n"
        for i, r in enumerate(results):
            title = r.get('title', '')
            href = r.get('href', '')
            snippet = r.get('body', '')
            output += f"{i+1}. {title}\nURL: {href}\nSnippet: {snippet}\n\n"
            
        output += "--- SKILL INSTRUCTIONS FOR YOU (THE LLM) ---\n"
        output += "Review the snippets above. If they contain the answer, use them directly.\n"
        output += "If you need deeper facts, use the `read_website` tool on the most promising URL."
        return output

    except Exception as e:
        return f"Search failed: {e}"

def read_website(url: str) -> str:
    """Read a website using Jina Reader (cloud) with a stealth fallback (local curl_cffi)."""
    global VISITED_URLS, URL_CACHE
    url = url.strip()
    if not url:
        return "❌ Error: URL is empty."

    # 1. URL Normalization: auto-prepend https:// if model omitted scheme
    if not url.startswith(('http://', 'https://')):
        url = f"https://{url}"

    parsed = urlparse.urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return f"❌ Error: Unsupported URL scheme '{parsed.scheme}'. Only http and https are supported."

    # 2. SSRF / Localhost Protection
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
        jina_resp = cffi_requests.get(
            jina_url, 
            headers=jina_headers,
            impersonate="chrome", 
            timeout=15
        )
        
        if jina_resp.status_code == 200:
            print("✅ [Success] Rendered via Jina Reader API")
            content = jina_resp.text
            
            # Smart Truncate to save LLM context cleanly
            truncated = _smart_truncate(content, 18000)
                
            output = f"📄 **SOURCE URL:** {url} (Parsed via Jina AI)\n"
            output += "=" * 40 + "\n"
            output += truncated + "\n"
            output += "=" * 40 + "\n"
            output += "INSTRUCTIONS FOR AI: If the answer isn't above, use the `read_website` tool on any [Link](url) found in the text to drill down."
            
            VISITED_URLS.add(url)
            URL_CACHE[url] = output
            return output
            
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
        # 1. Check Content-Type to avoid downloading PDFs/videos (Timeout is fast)
        head_response = cffi_requests.head(url, impersonate="chrome", timeout=(3.0, 7.0), allow_redirects=True)
        
        if head_response.status_code in [401, 403, 406]:
            return f"❌ Access Denied ({head_response.status_code}). This website uses strict anti-bot protection. INSTRUCTIONS: Stop trying to read this domain and search for the answer elsewhere."

        content_type = head_response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "text/plain" not in content_type:
            return f"❌ Error: URL is a non-HTML file type ({content_type}). The bot can only read web pages."

        # 2. Fetch the actual page mimicking the exact TLS fingerprint of Chrome
        response = cffi_requests.get(url, impersonate="chrome", timeout=(3.0, 10.0), allow_redirects=True)
        
        if response.status_code in [401, 403, 406]:
            return f"❌ Access Denied ({response.status_code}). This website uses strict anti-bot protection. INSTRUCTIONS: Stop trying to read this domain and search for the answer elsewhere."
            
        response.raise_for_status()
        
        # Prevent weird character glitches on non-UTF-8 sites
        encoding = getattr(response, 'charset_encoding', None) or getattr(response, 'apparent_encoding', None) or 'utf-8'
        response.encoding = encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        
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
            output += "\nINSTRUCTIONS FOR AI: If the answer isn't in the markdown above, use the `read_website` tool on one of these links to drill down."
        else:
            output += "No useful navigational links found."
            
        final_result = output[:20000]
        VISITED_URLS.add(url)
        URL_CACHE[url] = final_result
        return final_result

    except (RequestException, RequestsError) as e:
        return f"❌ Network Error reading URL: {str(e)}. Try a different search result."
    except Exception as e:
        return f"❌ Unexpected Error parsing website: {str(e)}. Try a different search result."

def read_youtube_video(url: str) -> str:
    """Extract the transcript text and title from a YouTube video URL to summarize it."""
    print(f"📺 [Fetching YouTube Transcript & Metadata]: {url}")
    try:
        parsed = urlparse.urlparse(url)
        video_id = ""
        
        if 'youtu.be' in parsed.netloc:
            video_id = parsed.path.strip('/')
        elif 'youtube.com' in parsed.netloc:
            if '/shorts/' in parsed.path:
                video_id = parsed.path.split('/shorts/')[1].strip('/')
            else:
                video_id = urlparse.parse_qs(parsed.query).get('v', [''])[0]
                
        if not video_id:
            return "Failed to extract YouTube video ID from the provided URL. CRITICAL: Do NOT guess or invent a YouTube link! Tell the user you couldn't find it."

        video_title = "Unknown Video Title"
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            response = requests.get(oembed_url, timeout=5)
            if response.status_code == 200:
                video_title = response.json().get("title", "Unknown Video Title")
        except Exception as e:
            print(f"⚠️ [oEmbed Metadata Error]: Could not fetch title: {e}")

        ytt_api = YouTubeTranscriptApi()

        try:
            transcript = ytt_api.fetch(video_id)
        except Exception:
            transcript_list = ytt_api.list(video_id)
            transcript = next(iter(transcript_list)).fetch()

        text = " ".join([t.text for t in transcript])
        
        final_output = f"Video Title: {video_title}\n\nTranscript:\n{text}"
        return final_output[:20000]

    except Exception as e:
        error_msg = str(e)
        print(f"⚠️ [YouTube API Error]: {error_msg}")
        
        # If we successfully fetched the title but failed on transcript, return the title anyway!
        prefix = f"Video Title: {video_title}\n\n" if 'video_title' in locals() and video_title != "Unknown Video Title" else ""
        
        if "TranscriptsDisabled" in error_msg:
            return f"{prefix}Transcript Failed: The creator disabled transcripts for this video."
        elif "NoTranscriptFound" in error_msg:
            return f"{prefix}Transcript Failed: No transcript found for this video."
        elif "age restricted" in error_msg.lower():
            return f"{prefix}Transcript Failed: Video is age-restricted."
        elif "RequestBlocked" in error_msg or "TooManyRequests" in error_msg:
            return f"{prefix}Transcript Failed: YouTube temporarily blocked the bot's IP for rate limiting."
            
        return f"{prefix}Failed to fetch transcript: {error_msg}"

WEB_TOOLS = {
    "search_web": search_web,
    "read_website": read_website,
    "read_youtube_video": read_youtube_video,
}

