import os
import requests
from dotenv import load_dotenv
from src.skills.youtube.aggregators.base import BaseYouTubeAggregator

load_dotenv()

class YouTubeApiAggregator(BaseYouTubeAggregator):
    """Searches YouTube using the official Google YouTube Data API v3."""

    def search(self, query: str, max_results: int = 1) -> list[dict]:
        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            return []

        try:
            url = "https://www.googleapis.com/youtube/v3/search"
            params = {
                'part': 'snippet',
                'q': query,
                'type': 'video',
                'videoEmbeddable': 'true',
                'maxResults': max_results,
                'key': api_key
            }
            res = requests.get(url, params=params, timeout=10)
            if res.status_code != 200:
                return []
                
            data = res.json()
            results = []
            for item in data.get('items', []):
                vid = item['id'].get('videoId', '')
                if vid:
                    results.append({
                        "id": vid,
                        "title": item['snippet'].get('title', 'Unknown Title'),
                        "channel": item['snippet'].get('channelTitle', 'Unknown Channel'),
                        "url": f"https://www.youtube.com/watch?v={vid}"
                    })
            return results
        except Exception as e:
            print(f"⚠️ [YouTube API Error]: {e}")
            return []

