"""
Natural Language Time, Schedule, Interval, Autoplay & Condition Parser (`src/jobs/parser.py`).

Handles diverse user phrasings across Chat and Research modes:
- Relative one-shot times: "in 20 mins", "in 2 hours and 15 minutes", "in half an hour", "after 45s"
- Calendar one-shot times: "at 4:05pm", "tomorrow at 9am", "tonight at 8:30pm", "on Friday at 5pm"
- Recurring clock schedules: "every day at 8am", "every weekday at 9:30am", "every Friday at 5pm"
- Fixed-interval loops (with 3-layer auto-stop caps): "every 5 mins", "every 2 hours", "every 15m for 1 hour"
- Music Autoplay: "autoplay dnb", "start liquid dnb radio", "play techno songs back to back"
- Condition triggers ("when/if X happens"):
  * Market/Crypto/Commodity price or % move ("when BTC hits $100k", "if NVDA drops below $120", "if TSLA moves 5%")
  * Weather alerts ("when it snows in Calgary", "if temp drops below -10C in Edmonton")
  * YouTube channel uploads ("when @MarquesBrownlee uploads a new video")
  * Open web/news conditions ("when news breaks about X", "when https://... changes/mentions Y")
"""

from __future__ import annotations

from datetime import datetime, timedelta
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from src.jobs.models import (
    AUTOPLAY_DEFAULT_MAX_RUNS,
    AUTOPLAY_DEFAULT_TRACK_SECONDS,
    AUTOPLAY_DEFAULT_TTL_SECONDS,
    ActionType,
    ConditionKind,
    MARKET_INTRADAY_MOVE_TTL_SECONDS,
    MARKET_PRICE_WATCH_TTL_SECONDS,
    MIN_ONE_SHOT_DELAY_SECONDS,
    MIN_RECURRING_INTERVAL_SECONDS,
    MULTI_HOUR_DEFAULT_MAX_RUNS,
    MULTI_HOUR_DEFAULT_TTL_SECONDS,
    SUB_HOUR_DEFAULT_MAX_RUNS,
    SUB_HOUR_DEFAULT_TTL_SECONDS,
    TriggerType,
    WEATHER_CONDITION_TTL_SECONDS,
    WEB_CONDITION_TTL_SECONDS,
    YOUTUBE_CONDITION_TTL_SECONDS,
)
from src.utils.timezones import get_local_now

_WORD_NUMBERS: Dict[str, int] = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "couple": 2,
    "three": 3,
    "few": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "forty-five": 45,
    "fifty": 50,
    "sixty": 60,
}

_WEEKDAY_MAP: Dict[str, int] = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

_ASSET_SYMBOL_ALIASES: Dict[str, str] = {
    "bitcoin": "BTC-USD",
    "btc": "BTC-USD",
    "ethereum": "ETH-USD",
    "ether": "ETH-USD",
    "eth": "ETH-USD",
    "solana": "SOL-USD",
    "sol": "SOL-USD",
    "xrp": "XRP-USD",
    "ripple": "XRP-USD",
    "dogecoin": "DOGE-USD",
    "doge": "DOGE-USD",
    "cardano": "ADA-USD",
    "ada": "ADA-USD",
    "bnb": "BNB-USD",
    "gold": "GC=F",
    "silver": "SI=F",
    "oil": "CL=F",
    "wti": "CL=F",
    "crude": "CL=F",
    "crude oil": "CL=F",
    "brent": "BZ=F",
    "natural gas": "NG=F",
    "copper": "HG=F",
    "s&p 500": "^GSPC",
    "s&p500": "^GSPC",
    "sp500": "^GSPC",
    "s&p": "^GSPC",
    "nasdaq": "^IXIC",
    "dow": "^DJI",
    "dow jones": "^DJI",
    "tsx": "^GSPTSE",
    "s&p/tsx": "^GSPTSE",
    "vix": "^VIX",
    "nvidia": "NVDA",
    "apple": "AAPL",
    "tesla": "TSLA",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "amd": "AMD",
    "palantir": "PLTR",
    "broadcom": "AVGO",
    "netflix": "NFLX",
    "shopify": "SHOP.TO",
    "enbridge": "ENB.TO",
    "royal bank": "RY.TO",
    "td bank": "TD.TO",
}

_AGENT_TASK_ACTION_RE = re.compile(
    r"\b(?:"
    r"skill_[a-z0-9_]+|search_web|search_images|read_website|fetch_youtube_transcript"
    r"|(?:create|display|generate|render|draw|plot|chart|graph|visualize|overlay|compare)\b.*\b(?:chart|charts|graph|graphs|plot|plots|diagram|flowchart|heatmap|monte\s+carlo|performance|return|returns|price|prices|btc|bitcoin|eth|stocks?|markets?)\b"
    r"|\b(?:monte\s+carlo|gbm\s+simulation|stochastic\s+simulation|comparison\s+charts?|price\s+charts?)\b"
    r"|(?:play|stream|queue)\s+(?:a\s+|an\s+|some\s+|another\s+)?.*?\b(?:song|track|music|dnb|drum\s+and\s+bass|techno|house|jazz|rock|hip\s*hop|metal|pop|trance|dubstep|ambient|lofi|lo-fi|phonk|synthwave)\b"
    r"|recommend\s+(?:a\s+|an\s+|some\s+)?(?:song|track|music)"
    r"|(?:check|fetch|pull|get|show|give(?:\s+me)?|send(?:\s+me)?|tell(?:\s+me)?|find|look\s+up|what(?:'s|\s+is|\s+are)|how(?:'s|\s+is|\s+are)|summarize|analyze|compare|run|execute|calculate|report|post)\b.*\b(?:weather|forecast|temperature|market|markets|stocks?|s&p|nasdaq|tsx|dow|btc|bitcoin|eth|ethereum|sol|crypto|gold|oil|dxy|price|prices|performance|headlines|news|digest|brief|score|scores|standings|schedule|matches|games?|odds|betting|nhl|nba|nfl|mlb|epl|ucl|f1|ufc|cs2?|esports|flights?|fares?|images?|pictures?|photos?)"
    r"|search\s+(?:for\s+)?(?:images?|pictures?|photos?|news|web)"
    r"|(?:think\s+of|come\s+up\s+with|brainstorm|invent|devise|suggest|propose|write|compose|draft|design|generate|quiz\s+me|challenge\s+me|ask\s+me|teach\s+me|explain)\b"
    r"|(?:give\s+me|send\s+me|tell\s+me|share|post)\s+(?:a|an|some|one|two|three|\d+|another|new|random|daily|weekly|quick|short|low[\s\-]+effort)\s+.*?\b(?:task|tasks|idea|ideas|exercise|exercises|cognition|cognitive|puzzle|riddle|brain\s*teaser|trivia|fact|facts|joke|jokes|quote|quotes|prompt|prompts|question|questions|challenge|workout|stretch|recipe|story|poem|haiku|tip|tips|advice|thought|reflection|topic|summary|recap)\b"
    r")\b",
    re.IGNORECASE,
)


def classify_action_type(task_text: str) -> str:
    """Determine whether `task_text` is a static text reminder or a dynamic tool/LLM task."""
    cleaned = (task_text or "").strip()
    if not cleaned:
        return ActionType.REMINDER
    # Explicit personal reminder verbs ("to call", "to take out", "to check the oven", "about the meeting")
    if re.match(
        r"^(?:to\s+)?(?:call|text|email|message|buy|pick\s+up|take\s+out|turn\s+off|check\s+the\s+(?:oven|stove|laundry|dryer|door|mail)|submit|pay|drink|stretch|go\s+to|leave\s+for|join\s+the\s+meeting|wake\s+up)\b",
        cleaned,
        re.IGNORECASE,
    ):
        return ActionType.REMINDER
    if _AGENT_TASK_ACTION_RE.search(cleaned):
        return ActionType.AGENT_TASK
    return ActionType.REMINDER


def _parse_num_token(tok: str) -> Optional[float]:
    if not tok:
        return None
    t = tok.strip().lower()
    if t in _WORD_NUMBERS:
        return float(_WORD_NUMBERS[t])
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def _unit_to_seconds(unit: str) -> int:
    u = (unit or "").strip().lower()
    if u.startswith("s"):
        return 1
    if u.startswith("m"):
        return 60
    if u.startswith("h"):
        return 3600
    if u.startswith("d"):
        return 86400
    if u.startswith("w"):
        return 7 * 86400
    return 60


def parse_duration_seconds(text: str) -> Optional[int]:
    """
    Parse a duration expression like '15 mins', '2 hours and 30 minutes', 'half an hour', '45s'
    into total seconds.
    """
    if not text:
        return None
    low = text.strip().lower()
    if re.search(r"\bhalf\s+an?\s+hour\b", low):
        return 1800
    if re.search(r"\ba\s+quarter\s+(?:of\s+an\s+)?hour\b", low):
        return 900

    pattern = re.compile(
        r"\b(?:(\d+(?:\.\d+)?)\s*|(a|an|one|two|couple(?:\s+of)?|three|few|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|forty-five|fifty|sixty)\s+)"
        r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)\b",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(low))
    if not matches:
        return None

    total = 0.0
    for m in matches:
        raw_num = (m.group(1) or m.group(2) or "").lower().replace("couple of", "couple").strip()
        val = _parse_num_token(raw_num)
        if val is None:
            continue
        total += val * _unit_to_seconds(m.group(3))
    return int(round(total)) if total > 0 else None


def _parse_clock_time(text: str) -> Optional[Tuple[int, int]]:
    """Extract (hour_24, minute) from expressions like '4:05pm', '9 am', '16:30', 'noon', 'midnight'."""
    low = (text or "").lower()
    if re.search(r"\bnoon\b", low):
        return (12, 0)
    if re.search(r"\bmidnight\b", low):
        return (0, 0)

    # Match HH:MM [am/pm] or H am/pm
    m = re.search(
        r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b",
        low,
        re.IGNORECASE,
    )
    if m:
        hr = int(m.group(1))
        mn = int(m.group(2) or 0)
        mer = m.group(3).lower().replace(".", "")
        if hr < 1 or hr > 12 or mn < 0 or mn > 59:
            return None
        if mer == "pm" and hr != 12:
            hr += 12
        elif mer == "am" and hr == 12:
            hr = 0
        return (hr, mn)

    # 24-hour HH:MM (e.g. "at 14:30" or "07:30")
    m24 = re.search(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b", low)
    if m24:
        return (int(m24.group(1)), int(m24.group(2)))

    # Time-of-day words ("tomorrow morning", "tonight")
    if re.search(r"\bmorning\b", low):
        return (9, 0)
    if re.search(r"\bafternoon\b", low):
        return (14, 0)
    if re.search(r"\b(?:evening|tonight)\b", low):
        return (19, 0)

    return None


def compute_next_schedule_timestamp(
    hour: int,
    minute: int,
    days_of_week: Optional[List[int]] = None,
    now_dt: Optional[datetime] = None,
) -> float:
    """
    Compute the next UTC epoch timestamp when `(hour, minute)` occurs in the bot's local timezone.
    `days_of_week` is an optional list of weekday ints (0=Monday .. 6=Sunday).
    """
    local_now = now_dt or get_local_now()
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    if days_of_week:
        valid_days = set(days_of_week)
        for _ in range(8):
            if candidate.weekday() in valid_days and candidate > local_now:
                break
            candidate += timedelta(days=1)
    return candidate.timestamp()


def _extract_explicit_limits(text: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Extract explicit user-requested `(max_runs, duration_cap_seconds)` from phrases like:
    - "for 30 minutes", "for 2 hours", "for the next hour"
    - "5 times", "for 6 tracks", "for 8 songs", "3x"
    """
    low = (text or "").lower()
    max_runs: Optional[int] = None
    duration_cap: Optional[int] = None

    m_runs = re.search(
        r"\b(?:for\s+)?(\d+|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\s+"
        r"(?:times|runs|tracks|songs|iterations)\b|\b(\d+)x\b",
        low,
    )
    if m_runs:
        tok = m_runs.group(1) or m_runs.group(2)
        val = _parse_num_token(tok)
        if val and val >= 1:
            max_runs = int(val)

    m_for = re.search(
        r"\bfor\s+(?:the\s+next\s+)?((?:\d+(?:\.\d+)?\s*|(?:a|an|one|two|three|four|five|six|ten|fifteen|twenty|thirty|forty-five|half\s+an?)\s+)"
        r"(?:seconds?|secs?|minutes?|mins?|m|hours?|hrs?|h|days?|d))\b",
        low,
    )
    if m_for:
        dur = parse_duration_seconds(m_for.group(1))
        if dur and dur > 0:
            duration_cap = dur

    return max_runs, duration_cap


def _clean_task_description(raw_text: str) -> str:
    """Strip leading reminder/scheduling boilerplate to isolate the core task or message."""
    t = (raw_text or "").strip()
    t = re.sub(
        r"^(?:please\s+|can\s+you\s+|could\s+you\s+|hey\s+bot\s+)?(?:remind\s+(?:me|us|the\s+channel|<@!?\d+>|\@\S+)\s+|set\s+a\s+reminder\s+|schedule\s+a\s+job\s+|alert\s+(?:me|us)\s+|notify\s+(?:me|us)\s+|let\s+(?:me|us)\s+know\s+|ping\s+(?:me|us)\s+)",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    # Strip leading/trailing time clauses like "in 20 minutes to ..." or "... in 20 minutes"
    t = re.sub(
        r"^(?:in|after)\s+(?:\d+(?:\.\d+)?\s*|(?:a|an|one|two|couple(?:\s+of)?|three|few|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|forty-five|fifty|sixty|half\s+an?)\s+)"
        r"(?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)"
        r"(?:\s+(?:and\s+)?(?:\d+(?:\.\d+)?\s*|(?:a|an|one|two|three|four|five|six|ten|fifteen|twenty|thirty|forty-five)\s+)(?:seconds?|secs?|minutes?|mins?|hours?|hrs?))?\s*(?:to\s+|that\s+|about\s+|,\s*)?",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(
        r"^(?:tomorrow|tonight|today)\s+(?:morning|afternoon|evening|(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:to\s+|that\s+|about\s+|,\s*)?",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(
        r"^at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\s*(?:today|tonight|tomorrow)?\s*(?:to\s+|that\s+|about\s+|,\s*)?",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(
        r"\s+(?:in|after)\s+(?:\d+(?:\.\d+)?\s*|(?:a|an|one|two|couple(?:\s+of)?|three|few|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|forty-five|fifty|sixty|half\s+an?)\s+)"
        r"(?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)"
        r"(?:\s+(?:and\s+)?(?:\d+(?:\.\d+)?\s*|(?:a|an|one|two|three|four|five|six|ten|fifteen|twenty|thirty|forty-five)\s+)(?:seconds?|secs?|minutes?|mins?|hours?|hrs?))?\s*[.!?]*$",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(
        r"\s+(?:(?:tomorrow|tonight|today)(?:\s+(?:morning|afternoon|evening))?(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)?"
        r"|on\s+(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)?"
        r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)\s*[.!?]*$",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(r"\s*\busing\s+`?skill_[a-z0-9_]+`?\.?\s*$", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"^(?:to|that|about)\s+", "", t, flags=re.IGNORECASE).strip()
    return t or raw_text.strip()


def _parse_autoplay_request(text: str, now_ts: float) -> Optional[Dict[str, Any]]:
    """
    Detect and parse song-length music autoplay requests such as:
    - "autoplay dnb"
    - "start autoplay liquid drum and bass"
    - "play techno songs back to back"
    - "start autoplay similar to this https://music.youtube.com/watch?v=..."
    - "autoplay similar to Strobe by Deadmau5"
    """
    low = (text or "").strip().lower()
    is_autoplay = bool(
        re.search(
            r"\b(?:autoplay|auto-play|auto\s+play|radio\s+mode|play\s+.*?\bback[\s\-]+to[\s\-]+back|continuously\s+play)\b",
            low,
        )
    )
    if not is_autoplay:
        return None

    max_runs, dur_cap = _extract_explicit_limits(text)
    eff_max_runs = max_runs or AUTOPLAY_DEFAULT_MAX_RUNS
    eff_ttl = dur_cap or AUTOPLAY_DEFAULT_TTL_SECONDS

    # If the user included a YouTube / YouTube Music URL, resolve its Song & Artist metadata as the similarity seed
    working_text = re.sub(r"\[Replying to[^\]]*\]", " ", (text or ""), flags=re.IGNORECASE).strip()
    m_yt = re.search(r"https?://(?:music\.|www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([0-9A-Za-z_-]{11})\S*", working_text)
    resolved_yt_seed = ""
    if m_yt:
        vid = m_yt.group(1)
        working_text = (working_text[: m_yt.start()] + " " + working_text[m_yt.end() :]).strip()
        try:
            from src.skills.music.engine.formatting import get_music_metadata
            meta = get_music_metadata(vid)
            s_name = str(meta.get("song") or "").strip()
            a_name = str(meta.get("artist") or "").strip()
            if s_name and s_name.lower() not in ("unknown", "unknown track", "youtube music"):
                if a_name and a_name.lower() not in ("unknown", "unknown artist"):
                    resolved_yt_seed = f"similar to {s_name} by {a_name}"
                else:
                    resolved_yt_seed = f"similar to {s_name}"
        except Exception:
            pass
        if not resolved_yt_seed:
            resolved_yt_seed = f"similar to https://music.youtube.com/watch?v={vid}"

    if resolved_yt_seed:
        target_query = resolved_yt_seed
    else:
        # Extract genre / artist / similarity seed target while preserving "similar to <Song> by <Artist>"
        cleaned = re.sub(
            r"\b(?:please|can\s+(?:you|we)|could\s+(?:you|we)|let'?s|lets|start|begin|turn\s+on|enable|"
            r"autoplay|auto-play|auto\s+play|radio(?:\s+mode)?|back[\s\-]+to[\s\-]+back|continuously|"
            r"play|put\s+on|queue\s+up|queue|some|music|songs?|tracks?|tunes?|for\s+(?:me|us)|in\s+this\s+channel|"
            r"(?:switch|change|swap|shift|move)\s+(?:(?:the|genre|vibe|style|mode)\s+)?(?:over\s+)?(?:to|into)|"
            r"switch|change|swap|the|genre|vibe|style|mode|instead|now|pls)\b",
            " ",
            working_text,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(?:for\s+)?(?:\d+|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\s+(?:times|runs|tracks|songs|minutes?|mins?|hours?|hrs?)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\b(?:similar\s+to|like)\s+(?:this|that|it)(?:\s+(?:one|song|track|link|video))?\b", "similar to", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^(?:to|into|with|of|on)\s+", "", " ".join(cleaned.split()).strip(" -–—,.!?"), flags=re.IGNORECASE)
        target_query = cleaned.strip(" -–—,.!?") or "dnb"
        if target_query.lower() in ("similar", "similar to", "like", "this", "that", "it"):
            try:
                from src.skills.music.context import get_channel_last_played_song
                last_rec = get_channel_last_played_song()
                if last_rec and last_rec.song:
                    if last_rec.artist:
                        target_query = f"similar to {last_rec.song} by {last_rec.artist}"
                    else:
                        target_query = f"similar to {last_rec.song}"
                else:
                    target_query = "dnb"
            except Exception:
                target_query = "dnb"

    return {
        "trigger_type": TriggerType.AUTOPLAY,
        "action_type": ActionType.MUSIC_AUTOPLAY,
        "description": f"Music Autoplay ({target_query})",
        "payload_text": target_query,
        "next_fire_at": now_ts,  # Fire Track 1 immediately or on first tick
        "interval_seconds": AUTOPLAY_DEFAULT_TRACK_SECONDS,
        "autoplay_spec": {
            "query": target_query,
            "queue": [],
            "last_duration": AUTOPLAY_DEFAULT_TRACK_SECONDS,
        },
        "max_runs": eff_max_runs,
        "expires_at": now_ts + eff_ttl,
    }


def _parse_condition_request(text: str, now_ts: float) -> Optional[Dict[str, Any]]:
    """
    Parse conditional triggers ("when/if/as soon as X happens"):
    1. Market / Crypto / Commodity price or % move
    2. Weather condition (rain/snow/storm/temp threshold)
    3. YouTube channel new video upload
    4. Open-ended web/news condition
    """
    raw = (text or "").strip()
    low = raw.lower()

    # Check if there is a condition signal ("when", "if", "as soon as", "once", "price hits/drops/crosses")
    has_cond_word = bool(
        re.search(
            r"\b(?:when|whenever|if|as\s+soon\s+as|once|price\s+(?:hits|reaches|drops|falls|crosses|goes)|(?:hits|reaches|crosses|drops\s+below|falls\s+below|goes\s+above|goes\s+below|moves)\s+\$?\d)\b",
            low,
        )
    )
    if not has_cond_word:
        return None

    # 1. YouTube Channel Upload Condition ("when @Channel uploads a video", "when Linus Tech Tips posts a new video")
    m_yt = re.search(
        r"\b(?:when|whenever|if|once|as\s+soon\s+as)\s+(@?[A-Za-z0-9_.\-\s]{2,40}?)\s+(?:uploads?|posts?|releases?|drops?|publishes?)\s+(?:a\s+)?(?:new\s+)?(?:youtube\s+)?video\b",
        raw,
        re.IGNORECASE,
    )
    if not m_yt:
        m_yt = re.search(
            r"\b(?:new\s+video\s+(?:from|by)|uploads?\s+from)\s+(@?[A-Za-z0-9_.\-\s]{2,40})\b",
            raw,
            re.IGNORECASE,
        )
    if m_yt:
        channel_q = m_yt.group(1).strip()
        channel_q = re.sub(r"^(?:channel|youtuber|creator)\s+", "", channel_q, flags=re.IGNORECASE).strip()
        return {
            "trigger_type": TriggerType.CONDITION,
            "action_type": ActionType.REMINDER,
            "description": f"When YouTube channel '{channel_q}' uploads a new video",
            "payload_text": f"New YouTube video upload from {channel_q}",
            "next_fire_at": now_ts + 60,
            "interval_seconds": 900,  # 15m RSS check
            "condition_spec": {
                "kind": ConditionKind.YOUTUBE_UPLOAD,
                "channel_query": channel_q,
                "channel_id": None,
                "last_seen_video_id": None,
            },
            "max_runs": 1,
            "expires_at": now_ts + YOUTUBE_CONDITION_TTL_SECONDS,
        }

    # 2. Weather Condition ("when it rains/snows in Calgary", "if temp drops below -5 in Edmonton")
    if re.search(r"\b(?:rains?|raining|snows?|snowing|storms?|thunderstorms?|temp(?:erature)?|degrees?|celsius|fahrenheit|°[cf]?|freezing)\b", low):
        loc_match = re.search(r"\bin\s+([A-Za-z][A-Za-z\s,]{1,35}?)(?:\s+(?:today|tomorrow|tonight)|[?.!]|$)", raw, re.IGNORECASE)
        location = (loc_match.group(1).strip() if loc_match else "Calgary")
        m_temp = re.search(
            r"\b(?:temp(?:erature)?|it|weather)\s+(?:drops?\s+below|falls?\s+below|goes?\s+below|is\s+below|under|<=?|<|goes?\s+above|rises?\s+above|exceeds?|hits?|is\s+above|over|>=?|>)\s*(-?\d+(?:\.\d+)?)\s*(?:°?\s*[cf]|degrees?)?",
            low,
        )
        if m_temp:
            thresh = float(m_temp.group(1))
            is_below = bool(re.search(r"\b(?:below|under|drops?|falls?|<)\b", m_temp.group(0)))
            cond_type = "temp_below" if is_below else "temp_above"
            op_label = "<=" if is_below else ">="
            return {
                "trigger_type": TriggerType.CONDITION,
                "action_type": ActionType.REMINDER,
                "description": f"When {location} temperature is {op_label} {thresh:g}°C",
                "payload_text": f"Weather alert in {location}: temperature {op_label} {thresh:g}°C",
                "next_fire_at": now_ts + 30,
                "interval_seconds": 900,  # 15m weather check
                "condition_spec": {
                    "kind": ConditionKind.WEATHER,
                    "location": location,
                    "condition_type": cond_type,
                    "threshold_c": thresh,
                },
                "max_runs": 1,
                "expires_at": now_ts + WEATHER_CONDITION_TTL_SECONDS,
            }
        for precip_kw, label in (("snow", "snow"), ("rain", "rain"), ("storm", "storm")):
            if precip_kw in low:
                return {
                    "trigger_type": TriggerType.CONDITION,
                    "action_type": ActionType.REMINDER,
                    "description": f"When {label} is reported/forecast in {location}",
                    "payload_text": f"Weather alert: {label} detected in {location}",
                    "next_fire_at": now_ts + 30,
                    "interval_seconds": 900,
                    "condition_spec": {
                        "kind": ConditionKind.WEATHER,
                        "location": location,
                        "condition_type": label,
                        "threshold_c": None,
                    },
                    "max_runs": 1,
                    "expires_at": now_ts + WEATHER_CONDITION_TTL_SECONDS,
                }

    # 3. Market / Ticker / Crypto / Commodity Price or % Move Condition
    # Examples:
    # - "alert me when BTC hits $100,000" / "$100k"
    # - "remind me if NVDA drops below $120"
    # - "let me know if TSLA moves 5% today"
    m_mkt = re.search(
        r"\b(?:when|whenever|if|once|as\s+soon\s+as)\s+"
        r"(?:the\s+price\s+of\s+)?"
        r"(\$?[A-Za-z0-9.&/\-\s]{1,22}?)\s+"
        r"(?:price\s+)?"
        r"(hits?|reaches?|crosses?(?:\s+above|\s+over|\s+below|\s+under)?|goes?\s+(?:above|over|up\s+to|below|under)|rises?\s+(?:above|over|to|by)?|climbs?\s+(?:above|to)?|exceeds?|is\s+(?:above|over|below|under)|drops?\s+(?:below|under|to|by)?|falls?\s+(?:below|under|to|by)?|dips?\s+(?:below|under|to)?|moves?(?:\s+by)?|swings?(?:\s+by)?|gains?|loses?|jumps?|plunges?|"
        r">=|<=|>|<)\s*"
        r"\$?(\d[\d,]*(?:\.\d+)?)\s*(k\b|%|percent\b|usd\b|cad\b)?",
        raw,
        re.IGNORECASE,
    )
    if m_mkt:
        raw_asset = m_mkt.group(1).strip().lstrip("$").strip()
        # Strip articles like "stock" or "coin"
        raw_asset = re.sub(r"\b(?:stock|share|shares|coin|token|price|ticker)\b", "", raw_asset, flags=re.IGNORECASE).strip()
        verb = m_mkt.group(2).strip().lower()
        num_str = m_mkt.group(3).replace(",", "")
        suffix = (m_mkt.group(4) or "").strip().lower()

        val = float(num_str)
        if suffix == "k":
            val *= 1000.0

        symbol = _ASSET_SYMBOL_ALIASES.get(raw_asset.lower())
        if not symbol:
            # Check multi-word alias match
            for alias_k, alias_sym in _ASSET_SYMBOL_ALIASES.items():
                if alias_k == raw_asset.lower():
                    symbol = alias_sym
                    break
        if not symbol:
            symbol = raw_asset.upper().replace(" ", "")

        is_pct = suffix in ("%", "percent") or bool(re.search(r"\b(?:moves?|swings?|gains?|loses?|jumps?|plunges?)\b", verb) and val <= 50 and "%" in raw)
        if is_pct:
            if any(w in verb for w in ("drop", "fall", "dip", "lose", "plunge", "below", "under")):
                op = "pct_down"
                desc = f"When {symbol} drops >= {val:g}%"
            elif any(w in verb for w in ("rise", "climb", "gain", "jump", "above", "over")):
                op = "pct_up"
                desc = f"When {symbol} rises >= {val:g}%"
            else:
                op = "pct_move"
                desc = f"When {symbol} moves >= {val:g}% intraday"
            ttl = MARKET_INTRADAY_MOVE_TTL_SECONDS
        else:
            if any(w in verb for w in ("below", "under", "drop", "fall", "dip", "<")):
                op = "<="
                desc = f"When {symbol} <= ${val:,.2f}".rstrip("0").rstrip(".")
            else:
                op = ">="
                desc = f"When {symbol} >= ${val:,.2f}".rstrip("0").rstrip(".")
            ttl = MARKET_PRICE_WATCH_TTL_SECONDS

        return {
            "trigger_type": TriggerType.CONDITION,
            "action_type": ActionType.REMINDER,
            "description": desc,
            "payload_text": desc,
            "next_fire_at": now_ts + 15,
            "interval_seconds": 60,  # 60s quote check (0 LLM cost)
            "condition_spec": {
                "kind": ConditionKind.MARKET_PRICE,
                "symbol": symbol,
                "operator": op,
                "threshold": val,
            },
            "max_runs": 1,
            "expires_at": now_ts + ttl,
        }

    # 4. Open-Ended Web or Breaking News Condition ("when news breaks about X", "when https://... mentions Y")
    m_url = re.search(r"(https?://[^\s<>]+)", raw)
    m_cond_clause = re.search(r"\b(?:when|whenever|if|once|as\s+soon\s+as)\s+(.+)$", raw, re.IGNORECASE)
    if m_cond_clause:
        cond_text = m_cond_clause.group(1).strip()
        target_url = m_url.group(1) if m_url else None
        return {
            "trigger_type": TriggerType.CONDITION,
            "action_type": ActionType.REMINDER,
            "description": f"When: {cond_text}",
            "payload_text": cond_text,
            "next_fire_at": now_ts + 60,
            "interval_seconds": 900,  # 15m minimum poll interval with content-hash gate
            "condition_spec": {
                "kind": ConditionKind.WEB_CONDITION,
                "target_url": target_url,
                "search_query": cond_text if not target_url else None,
                "condition_text": cond_text,
                "last_content_hash": None,
            },
            "max_runs": 1,
            "expires_at": now_ts + WEB_CONDITION_TTL_SECONDS,
        }

    return None


def parse_job_request(
    query: str,
    when: Optional[str] = None,
    task: Optional[str] = None,
    now_ts: Optional[float] = None,
    now_dt: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Parse a natural-language or structured job/reminder request into a normalized dict
    ready to construct a `UserJob`.
    """
    eff_now_ts = float(now_ts if now_ts is not None else time.time())
    eff_now_dt = now_dt or get_local_now()

    combined = " ".join(p for p in (query, when, task) if p).strip()
    low = combined.lower()

    # 1. Check Song-Length Music Autoplay first ("autoplay dnb", "start techno radio", etc.)
    autoplay_parsed = _parse_autoplay_request(combined, eff_now_ts)
    if autoplay_parsed is not None:
        return autoplay_parsed

    # 2. Check Recurring Daily / Weekly Clock Schedule FIRST ("every day at 8am", "every weekday at 9:30am",
    #    "every Thursday, Friday and Saturday", "every week on Thursday", "on Thursdays and Fridays at 9am")
    has_daily_or_weekly_schedule = bool(
        re.search(
            r"\b(?:"
            r"every\s+(?:single\s+|other\s+)?(?:day|morning|afternoon|evening|night|weekday|weekend|week(?:\s+on)?|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*)"
            r"|daily|weekly"
            r"|(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s"
            r"|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\s*(?:,\s*|\s+and\s+|\s*&\s*)(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*"
            r")\b",
            low,
        )
    )
    if has_daily_or_weekly_schedule:
        clock = _parse_clock_time(when or combined) or (9, 0)
        hr, mn = clock
        days_list: Optional[List[int]] = None
        if "weekday" in low:
            days_list = [0, 1, 2, 3, 4]
        elif "weekend" in low:
            days_list = [5, 6]
        else:
            matched_days = [
                day_idx
                for day_name, day_idx in _WEEKDAY_MAP.items()
                if re.search(rf"\b{day_name}s?\b", low)
            ]
            if matched_days:
                days_list = sorted(set(matched_days))

        next_ts = compute_next_schedule_timestamp(hr, mn, days_of_week=days_list, now_dt=eff_now_dt)
        clean_task = task or re.sub(
            r"\b(?:every\s+(?:single\s+|other\s+)?(?:day|morning|afternoon|evening|night|weekday|weekend|week(?:\s+on)?|(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*(?:(?:\s*,\s*|\s+and\s+|\s*&\s*|\s*,\s*and\s+)(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*)*)|daily|weekly|(?:on\s+)?(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*s(?:(?:\s*,\s*|\s+and\s+|\s*&\s*|\s*,\s*and\s+)(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*s?)*)"
            r"(?:\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)?",
            "",
            combined,
            flags=re.IGNORECASE,
        ).strip()
        clean_task = _clean_task_description(clean_task)
        act_type = classify_action_type(clean_task)
        explicit_runs, explicit_dur = _extract_explicit_limits(combined)
        # Do NOT let task-internal durations (e.g. "a cognition task for 10 minutes", "summary for a day")
        # set a short `expires_at` that kills a daily/weekly routine before its scheduled day!
        min_valid_schedule_ttl = (7 * 86400) if days_list else (2 * 86400)
        has_explicit_next_horizon = bool(
            explicit_dur
            and explicit_dur >= min_valid_schedule_ttl
            and re.search(r"\bfor\s+the\s+next\s+\d+\s*(?:days?|weeks?)\b", low)
        )

        return {
            "trigger_type": TriggerType.DAILY_SCHEDULE,
            "action_type": act_type,
            "description": f"{clean_task} (scheduled at {hr:02d}:{mn:02d})",
            "payload_text": clean_task,
            "next_fire_at": next_ts,
            "schedule_spec": {
                "hour": hr,
                "minute": mn,
                "days": days_list,
            },
            "max_runs": explicit_runs,
            "expires_at": (eff_now_ts + explicit_dur) if has_explicit_next_horizon else None,
        }

    # 3. Check Recurring Interval Loop ("every 5 mins", "every 2 hours", "every hour")
    m_every_interval = re.search(
        r"\bevery\s+(?:(\d+(?:\.\d+)?|a|an|one|two|couple(?:\s+of)?|three|few|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty-five)\s+)?"
        r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b",
        low,
    )
    if m_every_interval:
        num_tok = m_every_interval.group(1) or "1"
        unit_tok = m_every_interval.group(2)
        val = _parse_num_token(num_tok.replace("couple of", "couple")) or 1.0
        raw_interval_sec = int(round(val * _unit_to_seconds(unit_tok)))
        clamped_sub_minute = raw_interval_sec < MIN_RECURRING_INTERVAL_SECONDS
        interval_sec = max(MIN_RECURRING_INTERVAL_SECONDS, raw_interval_sec)

        explicit_runs, explicit_dur = _extract_explicit_limits(combined)
        if interval_sec < 3600:
            default_runs = SUB_HOUR_DEFAULT_MAX_RUNS
            default_ttl = SUB_HOUR_DEFAULT_TTL_SECONDS
        else:
            default_runs = MULTI_HOUR_DEFAULT_MAX_RUNS
            default_ttl = MULTI_HOUR_DEFAULT_TTL_SECONDS

        eff_max_runs = explicit_runs or default_runs
        if explicit_dur:
            eff_ttl = max(MIN_RECURRING_INTERVAL_SECONDS, explicit_dur)
            if not explicit_runs:
                eff_max_runs = max(1, eff_ttl // interval_sec)
        elif explicit_runs:
            eff_ttl = max(default_ttl, explicit_runs * interval_sec + 300)
        else:
            eff_ttl = default_ttl

        # Strip the "every X mins [for Y]" clause to get the clean task
        clean_task = task or re.sub(
            r"\bevery\s+(?:(?:\d+(?:\.\d+)?|a|an|one|two|couple(?:\s+of)?|three|few|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty-five)\s+)?"
            r"(?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)"
            r"(?:\s+for\s+(?:the\s+next\s+)?[\w\s]+|\s+\d+\s+(?:times|runs|tracks|songs))?",
            "",
            combined,
            flags=re.IGNORECASE,
        ).strip()
        clean_task = _clean_task_description(clean_task)
        act_type = classify_action_type(clean_task)

        return {
            "trigger_type": TriggerType.INTERVAL,
            "action_type": act_type,
            "description": f"{clean_task} (every {_format_interval_short(interval_sec)})",
            "payload_text": clean_task,
            "next_fire_at": eff_now_ts + interval_sec,
            "interval_seconds": interval_sec,
            "max_runs": eff_max_runs,
            "expires_at": eff_now_ts + eff_ttl,
            "clamped_sub_minute": clamped_sub_minute,
            "requested_seconds": raw_interval_sec,
        }

    # 4. Check Conditional Trigger ("when BTC hits $100k", "if it snows in Calgary", "when @channel uploads")
    cond_parsed = _parse_condition_request(combined, eff_now_ts)
    if cond_parsed is not None:
        return cond_parsed

    # 5. Check Relative One-Shot Time ("in 20 minutes", "after 2 hours")
    m_rel = re.search(
        r"\b(?:in|after)\s+((?:\d+(?:\.\d+)?\s*|(?:a|an|one|two|couple(?:\s+of)?|three|few|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|forty-five|fifty|sixty|half\s+an?)\s+)"
        r"(?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)"
        r"(?:\s+(?:and\s+)?(?:\d+(?:\.\d+)?\s*|(?:a|an|one|two|three|four|five|six|ten|fifteen|twenty|thirty|forty-five)\s+)(?:seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h))?)\b",
        low,
    )
    if m_rel or (when and parse_duration_seconds(when)):
        dur_str = m_rel.group(1) if m_rel else (when or "")
        raw_offset_sec = parse_duration_seconds(dur_str) or 60
        clamped_sub_minute = raw_offset_sec < MIN_ONE_SHOT_DELAY_SECONDS
        offset_sec = max(MIN_ONE_SHOT_DELAY_SECONDS, raw_offset_sec)
        clean_task = task or _clean_task_description(query or combined)
        act_type = classify_action_type(clean_task)
        return {
            "trigger_type": TriggerType.ONCE_AT,
            "action_type": act_type,
            "description": clean_task,
            "payload_text": clean_task,
            "next_fire_at": eff_now_ts + offset_sec,
            "max_runs": 1,
            "clamped_sub_minute": clamped_sub_minute,
            "requested_seconds": raw_offset_sec,
        }

    # 6. Check Calendar One-Shot Time ("tomorrow at 9am", "at 4:05pm", "tonight at 8pm")
    clock = _parse_clock_time(when or combined)
    if clock is not None:
        hr, mn = clock
        target_dt = eff_now_dt.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if "tomorrow" in low:
            target_dt = (eff_now_dt + timedelta(days=1)).replace(hour=hr, minute=mn, second=0, microsecond=0)
        else:
            # Check specific weekday ("on Friday at 3pm")
            matched_day = next(
                (day_idx for day_name, day_idx in _WEEKDAY_MAP.items() if re.search(rf"\b{day_name}\b", low)),
                None,
            )
            if matched_day is not None:
                days_ahead = (matched_day - eff_now_dt.weekday()) % 7
                if days_ahead == 0 and target_dt <= eff_now_dt:
                    days_ahead = 7
                target_dt = (eff_now_dt + timedelta(days=days_ahead)).replace(hour=hr, minute=mn, second=0, microsecond=0)
            elif target_dt <= eff_now_dt:
                target_dt += timedelta(days=1)

        clean_task = task or _clean_task_description(query or combined)
        act_type = classify_action_type(clean_task)
        return {
            "trigger_type": TriggerType.ONCE_AT,
            "action_type": act_type,
            "description": clean_task,
            "payload_text": clean_task,
            "next_fire_at": target_dt.timestamp(),
            "max_runs": 1,
        }

    # 7. Fallback: if `when` was passed as a raw duration ("10m", "2h")
    parsed_fallback_dur = parse_duration_seconds(combined)
    if parsed_fallback_dur is None and not when:
        is_personal_reminder = bool(
            re.search(
                r"\b(?:remind|reminder|timer|alarm|alert|notify|ping|wake|call|text|email|buy|pick\s+up|take\s+out|check|submit|pay|meeting|oven|laundry|schedule|task|tasks|think|brainstorm|suggest|create|send|give|post|write|summarize|report|clean|workout|stretch|every|daily|weekly)\b",
                low,
            )
        )
        is_timeless_music_genre = bool(
            re.search(
                r"\b(?:switch\s+to|change\s+(?:genre\s+)?to|techno|dnb|drum\s+and\s+bass|jungle|house|trance|dubstep|ambient|"
                r"lofi|lo-fi|phonk|synthwave|electronic|edm|hardstyle|hardcore|gabber|breakbeat|breaks|ukg|garage|bassline|"
                r"psytrance|acid|minimal|industrial|schranz|electro|disco|funk|soul|r&b|rnb|hip\s*hop|rap|trap|drill|grime|"
                r"afrobeats|reggae|dancehall|rock|metal|punk|indie|alternative|pop|jazz|classical|blues|country|folk|latin|reggaeton|k-pop|j-pop)\b",
                low,
            )
        )
        if is_timeless_music_genre and not is_personal_reminder:
            auto_fallback = _parse_autoplay_request(f"autoplay {combined}", eff_now_ts)
            if auto_fallback is not None:
                return auto_fallback

    fallback_sec = parsed_fallback_dur or 600
    clean_task = task or _clean_task_description(query or combined)
    act_type = classify_action_type(clean_task)
    return {
        "trigger_type": TriggerType.ONCE_AT,
        "action_type": act_type,
        "description": clean_task,
        "payload_text": clean_task,
        "next_fire_at": eff_now_ts + fallback_sec,
        "max_runs": 1,
    }


def _format_interval_short(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        mins = seconds // 60
        rem_s = seconds % 60
        return f"{mins}m {rem_s}s" if rem_s else f"{mins}m"
    hrs = seconds // 3600
    rem_m = (seconds % 3600) // 60
    return f"{hrs}h {rem_m}m" if rem_m else f"{hrs}h"
