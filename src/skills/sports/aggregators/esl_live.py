import os
from typing import Optional
import requests
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.catalogs import ESPORTS_STREAM_CHANNELS
from src.utils.workers import submit_to_worker


class EslLiveAggregator(BaseSportsAggregator):
    """Aggregator that checks ESLCS, BLASTPremier, and thunderpickesports channels for live CS matches via YouTube Data API,
    plus live BO1/BO3/BO5 CS2 scoreboards from BO3.gg and KashRock."""

    CHANNEL_ID = ESPORTS_STREAM_CHANNELS[0].channel_id
    CHANNELS = ESPORTS_STREAM_CHANNELS

    def get_live_matches(self, match_query: Optional[str] = None) -> str:
        """Checks if ESLCS, BLASTPremier, or thunderpickesports are currently streaming a live Counter-Strike match on YouTube
        and fetches live in-game CS2 series scoreboards."""
        print("  └─ 🔴 [CS Live]: Checking live CS2 scoreboards and ESL/BLAST/Thunderpick streams...")

        def _fetch_kr_live() -> str:
            try:
                from src.skills.sports.aggregators.kashrock import KashrockAggregator
                kr = KashrockAggregator()
                return kr.get_live_scoreboards(match_query) if match_query else kr.get_live_scoreboards()
            except Exception:
                return ""

        kr_fut = submit_to_worker(_fetch_kr_live)

        api_key = os.getenv("YOUTUBE_API_KEY")
        if not api_key:
            kr_live = kr_fut.result()
            if kr_live:
                return f"[LIVE CS2 STREAMS & SCOREBOARDS]\n{kr_live.strip()}\n\n"
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

        kr_live = kr_fut.result()
        if errors and len(errors) == len(self.CHANNELS) and not kr_live:
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

        if kr_live:
            output += "\n" + kr_live.strip() + "\n"

        return output + "\n"


