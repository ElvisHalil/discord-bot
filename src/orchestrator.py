"""
Orchestrator helpers.

Core agent-loop functions used by main.py's message loop:
- Tool argument normalization (`TOOL_ARG_ALIASES`, `normalize_tool_args`)
- Chat history management (`trim_history`)
- Reply post-processing & sanitization (`apply_auto_ping`, `join_orphan_bullets`,
  `extract_reply_context`, `sanitize_agent_reply`)

Domain-specific helpers live in their respective modules and are re-exported here
for convenience:
- Timezone & clock enrichment: `src.utils.timezones`
- Sports follow-up & tracker context: `src.skills.sports.context`
- Music spectrum shift context: `src.skills.music.spectrum`
"""

import inspect
import re
import types
import typing

from src.skills.music.spectrum import (
    detect_missed_music_shift_tool_call,
    enrich_music_tool_args,
)
from src.skills.sports.context import (
    detect_explicit_tracker_intent,
    detect_missed_sports_tool_call,
    enrich_sports_tool_args,
)
from src.utils.timezones import (
    build_reference_clock,
    enrich_tool_output_with_time_context,
    get_local_now,
    resolve_configured_timezone,
)

# ---------------------------------------------------------------------------
# Tool argument normalization
# ---------------------------------------------------------------------------

# Small models sometimes call tools with a synonym for the real parameter
# name (e.g. search_web(q=...) or read_website(link=...)). We remap those here,
# BEFORE filtering against the function signature. Doing it in the tool
# signature via **kwargs does not work: Ollama advertises `kwargs` to the model
# as a required parameter, and the signature filter drops the aliases anyway.
TOOL_ARG_ALIASES = {
    "search_web": {
        "q": "query", "search_query": "query", "keywords": "query",
        "keyword": "query", "prompt": "query", "text": "query", "search": "query",
    },
    "read_website": {
        "link": "url", "website": "url", "target_url": "url", "uri": "url",
        "site": "url", "href": "url", "page": "url",
    },
    "read_youtube_video": {
        "link": "url", "video_url": "url", "video": "url", "uri": "url",
        "href": "url", "video_id": "url", "id": "url",
    },
    "skill_top_headlines": {
        "country": "region", "location": "region", "place": "region", "nation": "region",
        "category": "topic", "section": "topic",
    },
    "get_top_headlines": {
        "country": "region", "location": "region", "place": "region", "nation": "region",
        "category": "topic", "section": "topic",
    },
    "skill_top_music": {
        "artist_name": "artist", "band": "artist", "creator": "artist",
        "style": "genre", "vibe": "mood", "energy": "mood", "feel": "mood",
        "seed": "similar_to", "song": "similar_to", "track": "similar_to",
        "direction": "shift", "notch": "shift", "scale": "shift", "tempo": "shift", "intensity": "shift",
    },
    "skill_csgo_esports": {
        "type": "query_type", "category": "query_type", "query": "query_type", "mode": "query_type",
        "event": "event_page", "page": "event_page", "tournament": "event_page",
        "player": "player_url", "player_name": "player_url", "name": "player_url", "target": "player_url",
        "team": "matchup", "matchup": "matchup", "match": "matchup", "teams": "matchup",
    },
    "skill_betting_odds": {
        "matchup": "query", "match": "query", "game": "query", "event": "query", "teams": "query",
        "category": "sport", "league": "sport",
    },
    "skill_sports_info": {
        "league": "sport", "category": "sport", "type": "sport", "sport_type": "sport",
        "sport_name": "sport", "discipline": "sport", "competition": "sport",
        "club": "team", "franchise": "team", "squad": "team", "teams": "team",
        "competitor": "team", "side": "team", "organization": "team",
        "athlete": "player", "player_name": "player", "fighter": "player", "driver": "player", "golfer": "player",
        "q": "query", "text": "query", "prompt": "query", "search": "query", "question": "query",
        "matchup": "query", "match": "query", "matches": "query", "filter": "query",
        "outcomes": "query", "outcome": "query",
    },
    "skill_track_live_game": {
        "team": "query", "club": "query", "match": "query", "matchup": "query",
        "game": "query", "event": "query", "target": "query", "q": "query",
        "command": "action", "mode": "action", "type": "action",
        "league": "sport", "category": "sport",
    },
    "skill_weather_reporter": {
        "location": "city", "place": "city", "town": "city", "region": "city", "q": "city", "query": "city",
    },
    "skill_youtube_searcher": {
        "q": "query", "search_query": "query", "search": "query", "video": "query",
        "song": "query", "track": "query", "title": "query",
        "limit": "max_results", "count": "max_results", "music": "is_music",
    },
    "skill_audio_intelligence": {
        "song": "track", "query": "track", "q": "track", "title": "track", "seed": "track",
        "mode": "action", "type": "action", "bpm": "target_bpm", "tempo": "target_bpm",
    },
    "skill_flight_info": {
        "q": "query", "route": "query", "search": "query",
        "from": "origin", "from_airport": "origin", "departure": "origin", "dep": "origin", "source": "origin",
        "to": "destination", "to_airport": "destination", "arrival": "destination", "dest": "destination", "arr": "destination",
        "departure_date": "date", "dep_date": "date", "when": "date",
        "ret_date": "return_date", "return": "return_date",
        "flight": "flight_number", "callsign": "flight_number", "code": "flight_number", "tail": "flight_number",
        "seat": "cabin", "class": "cabin", "cabin_class": "cabin",
        "curr": "currency",
        "stops": "max_stops", "num_stops": "max_stops", "stop_limit": "max_stops",
        "sort": "sort_by", "sort_preference": "sort_by", "order": "sort_by",
        "trip": "trip_type", "type": "trip_type",
    },
    "skill_track_flight": {
        "mode": "action", "command": "action", "cmd": "action",
        "q": "query", "flight": "query", "flight_number": "query", "callsign": "query", "route": "query",
        "from": "origin", "from_airport": "origin", "departure": "origin",
        "to": "destination", "to_airport": "destination", "arrival": "destination", "dest": "destination",
        "departure_date": "date", "dep_date": "date",
        "ret_date": "return_date", "return": "return_date",
        "price": "target_price", "max_price": "target_price", "threshold": "target_price",
    },
    "evaluate_math": {
        "math": "expression", "expr": "expression", "equation": "expression",
        "formula": "expression", "query": "expression", "q": "expression",
    },
    "save_memory": {
        "memory": "fact", "note": "fact", "info": "fact", "text": "fact", "content": "fact",
    },
}


def _base_type(annotation):
    """Unwrap Optional[X] / X | None to X."""
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _coerce(value, annotation):
    """Coerce string values from the model into bool/int/float when the parameter expects them."""
    target = _base_type(annotation)
    if not isinstance(value, str):
        return value
    text = value.strip().lower()
    if target is bool:
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off", "", "none", "null"):
            return False
    elif target is int:
        try:
            return int(float(text))
        except ValueError:
            return value
    elif target is float:
        try:
            return float(text)
        except ValueError:
            return value
    return value


def normalize_tool_args(tool_name: str, func, args) -> dict:
    """Map aliased argument names onto the function's real parameters, drop anything the
    function does not accept (and explicit nulls, so defaults apply), and coerce types.
    Canonical names always win over aliases (or merge for skill_sports_info so team/league context isn't lost)."""
    if not isinstance(args, dict):
        return {}

    params = inspect.signature(func).parameters
    aliases = TOOL_ARG_ALIASES.get(tool_name, {})

    normalized = {k: v for k, v in args.items() if k in params and v is not None}
    for key, value in args.items():
        if key in params or value in (None, ""):
            continue
        target = aliases.get(key)
        if target and target in params:
            if target not in normalized:
                normalized[target] = value
            elif tool_name == "skill_sports_info" and isinstance(normalized[target], str) and isinstance(value, str):
                if value.strip().lower() not in normalized[target].lower():
                    normalized[target] = f"{normalized[target]} {value.strip()}".strip()

    return {k: _coerce(v, params[k].annotation) for k, v in normalized.items()}


def enrich_flight_tool_args(args: dict, history: list) -> dict:
    """Ensure `query` on `skill_flight_info` / `skill_track_flight` preserves the user's latest prompt
    (including flexible-date intent, direct-schedule questions, sort preferences, and return dates)
    even when an 8B model only populates `origin` and `destination`."""
    enriched = dict(args or {})
    latest_user_text = ""
    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            break
    if not latest_user_text:
        return enriched
    cur_q = str(enriched.get("query") or "").strip()
    if not cur_q:
        enriched["query"] = latest_user_text
    elif latest_user_text.lower() not in cur_q.lower():
        enriched["query"] = f"{cur_q} ({latest_user_text})"
    return enriched


# ---------------------------------------------------------------------------
# Chat history management
# ---------------------------------------------------------------------------

OLD_TOOL_OUTPUT_CHARS = 800
_EPHEMERAL_SPORTS_TOOLS = {"skill_sports_info", "skill_track_live_game", "skill_betting_odds"}


def _role(msg):
    if isinstance(msg, dict):
        return msg.get("role")
    return getattr(msg, "role", None)


def trim_history(history: list, limit: int, old_tool_chars: int = OLD_TOOL_OUTPUT_CHARS) -> list:
    """Keep the system prompt plus at most `limit` recent messages.

    - Tool outputs from earlier turns (anything before the latest user message)
      are shortened, since the model has already summarized them.
    - Earlier turns' live sports tool outputs are explicitly marked as expired
      snapshots so 8B models never regurgitate a stale minute clock or score.
    - The window always starts on a user message, so an assistant tool call is
      never separated from its tool results.
    """
    if not history:
        return history

    system, rest = history[0], list(history[1:])

    last_user = max((i for i, m in enumerate(rest) if _role(m) == "user"), default=-1)
    for i in range(max(last_user, 0)):
        msg = rest[i]
        if isinstance(msg, dict) and msg.get("role") == "tool":
            tool_name = msg.get("name", "")
            content = str(msg.get("content") or "")
            if tool_name in _EPHEMERAL_SPORTS_TOOLS:
                rest[i] = {
                    **msg,
                    "content": (
                        "[EXPIRED SPORTS SNAPSHOT FROM EARLIER TURN — DO NOT REUSE OLD SCORES, MINUTE CLOCKS, "
                        "OR TRACKING STATUS. You MUST call skill_sports_info or skill_track_live_game again "
                        "for fresh real-time data.]"
                    ),
                }
            elif tool_name == "skill_flight_info":
                rest[i] = {
                    **msg,
                    "content": (
                        "[EXPIRED FLIGHT SEARCH SNAPSHOT FROM EARLIER TURN — DO NOT REUSE OLD FARES OR BOOKING LINKS "
                        "WHEN THE USER REFINES STOPS, DATES, OR ROUND-TRIP OPTIONS. You MUST call skill_flight_info "
                        "again for the updated search.]"
                    ),
                }
            elif len(content) > old_tool_chars:
                rest[i] = {**msg, "content": content[:old_tool_chars].rstrip() + "\n...[older tool output truncated]"}

    if len(rest) > limit:
        start = len(rest) - limit
        forward = next((i for i in range(start, len(rest)) if _role(rest[i]) == "user"), None)
        if forward is None:
            forward = max((i for i in range(start) if _role(rest[i]) == "user"), default=0)
        rest = rest[forward:]

    return [system] + rest


# ---------------------------------------------------------------------------
# Reply post-processing & sanitization
# ---------------------------------------------------------------------------

_PROTECTED_SPANS = re.compile(
    r'(<[^>\s]+>|https?://\S+|`[^`]*`|(?m:^[^\S\n]*(?:[-*•][^\S\n]*)?(?:\*\*)?(?:Song|Track|Artist|Album|Genre):\*{0,2}\s*.+$))',
    re.IGNORECASE,
)
MIN_PING_NAME_LENGTH = 3


def apply_auto_ping(reply: str, known_users: dict) -> str:
    """Replace known user names in the reply with Discord mentions.
    Skips names shorter than MIN_PING_NAME_LENGTH to avoid pinging on common short words,
    and protects URLs, code spans, mentions, and song-card metadata lines."""
    if not reply or not known_users:
        return reply

    names = sorted(
        ((n, uid) for n, uid in known_users.items() if n and len(n) >= MIN_PING_NAME_LENGTH),
        key=lambda x: len(x[0]),
        reverse=True,
    )
    if not names:
        return reply

    parts = _PROTECTED_SPANS.split(reply)
    for idx in range(0, len(parts), 2):  # even indices are unprotected text
        segment = parts[idx]
        for name, uid in names:
            segment = re.sub(rf'@?(?<!\w){re.escape(name)}(?!\w)', f"<@{uid}>", segment, flags=re.IGNORECASE)
        parts[idx] = segment
    return "".join(parts)


def join_orphan_bullets(reply: str) -> str:
    """Join a bullet marker left alone on its line with the text on the next line:
    '-\\nHeadline' -> '- Headline'."""
    return re.sub(r'^([ \t]*[-*•])[ \t]*\n[ \t]*(?=\S)', r'\1 ', reply, flags=re.MULTILINE)


def extract_reply_context(referenced_content: typing.Optional[str]) -> str:
    """Extract context from a Discord message being replied to (e.g. song cards or text snippet)."""
    if not referenced_content:
        return ""
    text = referenced_content.strip()
    if not text:
        return ""

    m_song = re.search(r'(?im)^[^\S\n]*(?:[-*•][^\S\n]*)?(?:\*\*)?(?:Song|Track):\*{0,2}\s*(.+)', text)
    m_art = re.search(r'(?:\*\*|\b)Artist:\*{0,2}\s*(.+)', text, re.IGNORECASE)
    if m_song and m_art:
        clean_s = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_song.group(1)).strip(" *-_#:`\"'")
        clean_a = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_art.group(1)).strip(" *-_#:`\"'")
        return f" [Replying to song card: '{clean_s}' by '{clean_a}']"

    snippet = text[:100].replace('\n', ' ').strip()
    return f" [Replying to: \"{snippet}\"]"


_DISCLAIMER_HEADER_RE = re.compile(
    r'(?im)^#*\s*(?:no,?\s*)?(?:'
    r'i don[\'’]t have direct (?:website|internet|live) access|'
    r'my capabilities \(without direct website access\)|'
    r'unable to check live odds|'
    r'use my search_web tool|'
    r'while i can[\'’]t browse the web in real time|'
    r'⚠️\s*(?:clarification|data limitations|simulated standings|disclaimer)|'
    r'clarification:\s*(?:data limitations|simulated standings)|'
    r'my (?:knowledge|training) cutoff (?:is|was)|'
    r'all ["\'\s]*(?:live|current) sports data.*(?:is simulated|for context-based)|'
    r'the standings you see are not real-world|'
    r'for genuine \d{4}.*results).*$',
)

_TOOL_CALL_MENTION_RE = re.compile(
    r'`?(?:search_web|read_website|read_youtube_video|skill_[a-z0-9_]+)`?',
    re.IGNORECASE,
)

_HALLUCINATED_BETTING_URL_RE = re.compile(
    r'<https?://www\.bet365\.com/[^\s>]+>',
    re.IGNORECASE,
)


def sanitize_agent_reply(reply: str) -> str:
    """Sanitizes replies by removing accidental leaks of internal function names,
    hallucinated bookmaker URLs, and canned AI meta-excuses."""
    if not reply:
        return reply

    lines = [line for line in reply.splitlines() if not _DISCLAIMER_HEADER_RE.search(line)]
    text = "\n".join(lines)

    text = re.sub(r'(?i)\buse\s+(?:my\s+)?search_web(?:\s+tool)?\b', 'search online', text)
    text = re.sub(r'(?i)\buse\s+(?:my\s+)?read_website(?:\s+tool)?\b', 'inspect the web page', text)
    text = re.sub(r'(?i)\btools like search_web or read_website\b', 'search and web research', text)
    text = re.sub(r'(?i)\baccessible via my tools\b', 'found in current public records', text)
    text = re.sub(r'(?i)\bvia my tools\b', 'in current records', text)

    def _clean_tool_name(m):
        t = m.group(0).strip('`')
        if t.startswith('skill_'):
            return t.replace('skill_', '').replace('_', ' ')
        if t == 'search_web':
            return 'web search'
        if t == 'read_website':
            return 'web reading'
        return t

    text = _TOOL_CALL_MENTION_RE.sub(_clean_tool_name, text)
    text = _HALLUCINATED_BETTING_URL_RE.sub('[Bet365](<https://www.bet365.com>)', text)
    # Unwrap malformed Markdown links targeting Discord timestamp tags: [9:00 PM EDT](<t:1705347600:t>) -> 9:00 PM EDT
    text = re.sub(r'\[([^\]]+)\]\(<?t:\d+(?::[a-zA-Z])?>?\)', r'\1', text)
    # Clean up orphaned bullet colons left when a stage label was stripped: "*   : Matchup" -> "*   Matchup"
    text = re.sub(r'(?m)^([ \t]*[-*•][ \t]+):\s+', r'\1', text)
    # Strip standalone Markdown horizontal rules (*** / ___) which Discord parses as unclosed bold/underline tags
    text = re.sub(r'(?m)^[ \t]*(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})[ \t]*$\n?', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
