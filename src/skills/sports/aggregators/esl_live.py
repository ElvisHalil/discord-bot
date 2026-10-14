import os
import requests
from src.skills.sports.aggregators.base import BaseSportsAggregator

class EslLiveAggregator(BaseSportsAggregator):
    """Aggregator that checks ESL CS channel for live matches via YouTube Data API."""

    CHANNEL_ID = "UCPq2ETz4aAGo2Z-8JisDPIA"

    def get_live_matches(self) -> str:
        """Checks if ESL is currently streaming a live Counter-Strike match on YouTube."""
        print("  └─ 🔴 [ESL Live]: Checking ESLCS YouTube for live matches...")
        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            return "[LIVE STREAMS]\nError: YOUTUBE_API_KEY is missing.\n\n"

        try:
            url = "https://www.googleapis.com/youtube/v3/search"
            params = {
                'part': 'snippet',
                'channelId': self.CHANNEL_ID,
                'eventType': 'live',
                'type': 'video',
                'key': api_key
            }
            r = requests.get(url, params=params, timeout=5)
            data = r.json()
            items = data.get('items', [])
            
            output = "[LIVE ESL MATCHES]\n"
            if not items:
                output += "ESL is not currently broadcasting any live matches.\n"
            else:
                for video in items:
                    title = video['snippet'].get('title', 'Unknown Title')
                    video_id = video['id'].get('videoId', '')
                    link = f"https://www.youtube.com/watch?v={video_id}"
                    output += f"- Title: {title}\n  Link: {link}\n"

            try:
                from src.skills.sports.aggregators.kashrock import KashrockAggregator
                kr_live = KashrockAggregator().get_live_scoreboards()
                if kr_live:
                    output += "\n" + kr_live.strip() + "\n"
            except Exception:
                pass

            return output + "\n"
        except Exception as e:
            return f"[LIVE STREAMS]\nError fetching live streams: {e}\n\n"

