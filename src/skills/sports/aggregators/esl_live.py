import os
import requests
from src.skills.sports.aggregators.base import BaseSportsAggregator

class EslLiveAggregator(BaseSportsAggregator):
    """Aggregator that checks ESLCS, BLASTPremier, and thunderpickesports channels for live CS matches via YouTube Data API."""

    CHANNEL_ID = "UCPq2ETz4aAGo2Z-8JisDPIA"
    CHANNELS = (
        ("ESL Counter-Strike", "ESLCS", "UCPq2ETz4aAGo2Z-8JisDPIA"),
        ("BLAST Premier", "BLASTPremier", "UC9k--dE_UE0Faxzgb_DDkYQ"),
        ("Thunderpick Esports", "thunderpickesports", "UCDs5D1luLRP2oo3lk71MaCA"),
    )

    def get_live_matches(self) -> str:
        """Checks if ESLCS, BLASTPremier, or thunderpickesports are currently streaming a live Counter-Strike match on YouTube."""
        print("  └─ 🔴 [CS Live]: Checking ESLCS, BLASTPremier, and thunderpickesports YouTube for live matches...")
        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            return "[LIVE STREAMS]\nError: YOUTUBE_API_KEY is missing.\n\n"

        url = "https://www.googleapis.com/youtube/v3/search"
        live_streams = []
        errors = []

        for channel_name, handle, channel_id in self.CHANNELS:
            try:
                params = {
                    'part': 'snippet',
                    'channelId': channel_id,
                    'eventType': 'live',
                    'type': 'video',
                    'key': api_key,
                }
                r = requests.get(url, params=params, timeout=5)
                data = r.json()
                items = data.get('items', [])
                for video in items:
                    title = video.get('snippet', {}).get('title', 'Unknown Title')
                    video_id = video.get('id', {}).get('videoId', '')
                    if video_id:
                        live_streams.append({
                            "channel": channel_name,
                            "handle": handle,
                            "title": title,
                            "link": f"https://www.youtube.com/watch?v={video_id}",
                        })
            except Exception as e:
                errors.append(e)

        if errors and len(errors) == len(self.CHANNELS):
            return f"[LIVE STREAMS]\nError fetching live streams: {errors[0]}\n\n"

        output = "[LIVE CS2 STREAMS]\n"
        if not live_streams:
            output += "No live matches are currently broadcasting on ESLCS, BLASTPremier, or thunderpickesports.\n"
        else:
            for stream in live_streams:
                output += (
                    f"- Channel: {stream['channel']} (@{stream['handle']})\n"
                    f"  Title: {stream['title']}\n"
                    f"  Link: {stream['link']}\n"
                )

        try:
            from src.skills.sports.aggregators.kashrock import KashrockAggregator
            kr_live = KashrockAggregator().get_live_scoreboards()
            if kr_live:
                output += "\n" + kr_live.strip() + "\n"
        except Exception:
            pass

        return output + "\n"

