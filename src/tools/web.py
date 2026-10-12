import requests
import urllib.parse as urlparse
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from ddgs import DDGS

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

VISITED_URLS = set()

def search_web(query: str) -> str:
    """Search the web. Returns the top snippets and links. Use `read_website` on a specific URL if you need more details."""
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
    """Read and extract the main paragraph text of a website URL."""
    print(f"🌐 [Reading Website]: {url}")
    try:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        paragraphs = soup.find_all('p')
        text = "\n".join([p.get_text(strip=True) for p in paragraphs])
        
        if not text.strip():
            text = soup.get_text(separator='\n', strip=True)
            
        return text[:20000]
    except Exception as e:
        return f"Failed to read URL: {e}"

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

