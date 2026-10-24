import html
import os
import re
from typing import Optional
import requests
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.catalogs import CS_TEAMS, ESPORTS_STREAM_CHANNELS
from src.utils.workers import submit_to_worker

# Non-CS games streamed on multi-title channels like PGL (e.g. PGL Wallachia Dota 2, Marvel Rivals, Quake, Apex)
_NON_CS_GAME_RE = re.compile(
    r"\b(?:"
    r"dota\s*2?|pgl\s+wallachia|wallachia|the\s+international|"
    r"valorant|vct|champions\s+tour|"
    r"league\s+of\s+legends|lol\s+esports|lck|lec|lcs|"
    r"apex\s+legends|algs|"
    r"marvel\s+rivals|"
    r"quake|quakecon|"
    r"overwatch|owcs|"
    r"rainbow\s+six|r6\s+siege|six\s+invitational|"
    r"rocket\s+league|rlcs|"
    r"pubg|fortnite|fncs|"
    r"call\s+of\s+duty|cdl|warzone|"
    r"ea\s*fc|fifa|starcraft|hearthstone|street\s+fighter|tekken|deadlock|mobile\s+legends|mlbb"
    r")\b",
    re.IGNORECASE,
)

# Positive Counter-Strike signals required for multi-game broadcast channels (e.g. @PGL)
_CS_POSITIVE_SIGNAL_RE = re.compile(
    r"(?:"
    r"\bcounter[\s\-]*strike(?:\s*2|\s*global\s+offensive)?\b|"
    r"(?:^|[\s#(\[|])(?:cs2|cs:go|csgo)\b|"
    r"\bpgl\s+(?:cs2\s+)?(?:major|masters|astana|cluj[\s\-]*napoca|bucharest|copenhagen|antwerp|stockholm|krakow|belgrade|rmr)\b|"
    r"\bthunderpick\s+world\s+championship\b|"
    r"\biem\b|\bintel\s+extreme\s+masters\b|\besl\s+pro\s+league\b|\bblast\s+premier\b"
    r")",
    re.IGNORECASE,
)

_DEDICATED_CS_HANDLES = frozenset({"eslcs", "blastpremier"})
_GENERIC_CS_ORG_TOKENS = frozenset({"pgl", "esl", "blast", "thunderpick", "iem", "cs", "cs2", "csgo"})


class EslLiveAggregator(BaseSportsAggregator):
    """Aggregator that checks ESLCS, BLASTPremier, thunderpickesports, and PGL channels for live CS matches via YouTube Data API,
    plus live BO1/BO3/BO5 CS2 scoreboards from BO3.gg and KashRock."""

    CHANNEL_ID = ESPORTS_STREAM_CHANNELS[0].channel_id
    CHANNELS = ESPORTS_STREAM_CHANNELS

    @staticmethod
    def is_cs_broadcast(title: str, description: str = "", handle: str = "") -> bool:
        """
        Verify that a live YouTube stream is a Counter-Strike broadcast and not another game
        (such as PGL Wallachia Dota 2, Valorant, Marvel Rivals, or Quake).
        """
        combined = f"{title or ''} {description or ''}".strip()
        if not combined:
            return False

        # 1. Hard exclusion: reject any stream mentioning Dota 2, Wallachia, Valorant, Marvel Rivals, etc.
        if _NON_CS_GAME_RE.search(combined):
            return False

        # 2. Dedicated CS-only channels (ESLCS, BLASTPremier) pass once non-CS games are excluded
        if (handle or "").strip().lstrip("@").lower() in _DEDICATED_CS_HANDLES:
            return True

        # 3. Multi-game channels (PGL, Thunderpick) must have a positive CS2 tournament/game marker
        #    or a head-to-head matchup featuring known CS teams (with non-CS tournaments already excluded above).
        if _CS_POSITIVE_SIGNAL_RE.search(combined):
            return True

        if re.search(r"\bvs\.?\b", title or "", re.IGNORECASE):
            for team in CS_TEAMS:
                if team in _GENERIC_CS_ORG_TOKENS:
                    continue
                if re.search(rf"\b{re.escape(team)}\b", title or "", re.IGNORECASE):
                    return True

        return False

    def get_live_matches(self, match_query: Optional[str] = None) -> str:
        """Checks if ESLCS, BLASTPremier, thunderpickesports, or PGL are currently streaming a live Counter-Strike match on YouTube
        and fetches live in-game CS2 series scoreboards."""
        print("  └─ 🔴 [CS Live]: Checking live CS2 scoreboards and ESL/BLAST/Thunderpick/PGL streams...")

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
                    snippet = video.get('snippet', {}) or {}
                    title = html.unescape(snippet.get('title', 'Unknown Title'))
                    description = html.unescape(snippet.get('description', ''))
                    video_id = video.get('id', {}).get('videoId', '')
                    if video_id and self.is_cs_broadcast(title=title, description=description, handle=handle):
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
            output += "No live matches are currently broadcasting on ESLCS, BLASTPremier, thunderpickesports, or PGL.\n"
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


