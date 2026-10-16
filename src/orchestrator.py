"""
Orchestrator helpers.

Pure functions used by main.py's message loop. They live here (instead of
inline in on_message) so they can be unit-tested without Discord or Ollama.
"""

import inspect
import os
import re
import types
import typing
from datetime import date, datetime, timedelta, timezone, tzinfo
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

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
        "artist_name": "artist",
        "band": "artist",
        "creator": "artist",
        "style": "genre",
        "vibe": "mood",
        "energy": "mood",
        "feel": "mood",
        "seed": "similar_to",
        "song": "similar_to",
        "track": "similar_to",
        "direction": "shift",
        "notch": "shift",
        "scale": "shift",
        "tempo": "shift",
        "intensity": "shift",
    },
    "skill_csgo_esports": {
        "type": "query_type",
        "category": "query_type",
        "query": "query_type",
        "mode": "query_type",
        "event": "event_page",
        "page": "event_page",
        "tournament": "event_page",
        "player": "player_url",
        "player_name": "player_url",
        "name": "player_url",
        "target": "player_url",
        "team": "matchup",
        "matchup": "matchup",
        "match": "matchup",
        "teams": "matchup",
    },
    "skill_betting_odds": {
        "matchup": "query",
        "match": "query",
        "game": "query",
        "event": "query",
        "teams": "query",
        "category": "sport",
        "league": "sport",
    },
    "skill_sports_info": {
        "league": "sport",
        "category": "sport",
        "type": "sport",
        "sport_type": "sport",
        "sport_name": "sport",
        "discipline": "sport",
        "competition": "sport",
        "club": "team",
        "franchise": "team",
        "squad": "team",
        "teams": "team",
        "competitor": "team",
        "side": "team",
        "organization": "team",
        "athlete": "player",
        "player_name": "player",
        "fighter": "player",
        "driver": "player",
        "golfer": "player",
        "q": "query",
        "text": "query",
        "prompt": "query",
        "search": "query",
        "question": "query",
        "matchup": "query",
        "match": "query",
        "matches": "query",
        "filter": "query",
        "outcomes": "query",
        "outcome": "query",
    },
    "skill_track_live_game": {
        "team": "query",
        "club": "query",
        "match": "query",
        "matchup": "query",
        "game": "query",
        "event": "query",
        "target": "query",
        "q": "query",
        "command": "action",
        "mode": "action",
        "type": "action",
        "league": "sport",
        "category": "sport",
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


def _extract_raw_user_text(msg) -> str:
    """Strip Discord wrapper prefixes ('User X says: ...') from a user message in history."""
    content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
    if not content:
        return ""
    text = str(content).strip()
    m = re.match(r"^(?:User\s+.+?\s+says(?:\s*\[.*?\])*|.+\(User ID:\s*\d+\)(?:\s*\[.*?\])*)\s*:\s*(.+)$", text, flags=re.DOTALL)
    return m.group(1).strip() if m else text


_INTENT_WORDS_TO_STRIP = {
    "roster", "squad", "schedule", "fixtures", "fixture", "standings", "table",
    "rank", "points", "injuries", "injured", "injury", "lineup", "lineups",
    "starting", "xi", "formation", "stats", "statistics", "stat", "scores",
    "score", "results", "result", "yesterday", "tomorrow", "today", "play",
    "playing", "played", "next", "previous", "past", "recent", "upcoming",
    "whats", "what", "is", "are", "the", "this", "that", "it", "its", "their",
    "they", "them", "his", "her", "status", "of", "game", "games", "match", "matches",
    "hows", "how", "going", "who", "scored", "last", "goal", "goals", "time", "minute",
    "still", "live", "not", "over", "currently", "on", "right", "now",
    "track", "tracking", "tracked", "untrack", "stop", "start", "monitor", "follow",
    "odds", "odd", "bet", "betting", "moneyline", "spread", "line", "lines",
    "check", "get", "both", "these", "those",
    "please", "can", "you", "for", "me", "in", "at",
}

_FOLLOWUP_MATCH_PATTERN = re.compile(
    r"\b(?:"
    r"their|they|them|his|her|its|that\s+team|same\s+team|"
    r"this\s+game|that\s+game|the\s+game|this\s+match|that\s+match|the\s+match|"
    r"tracked\s+games?|tracked\s+matches|both\s+games|both\s+matches|these\s+games|these\s+matches|"
    r"track\s+this|track\s+that|track\s+the|track\s+it|untrack|"
    r"hows?\s+the\s+game|how\s+is\s+the\s+game|hows?\s+it\s+going|"
    r"who\s+scored|last\s+goal|still\s+live|not\s+over|what\s+minute|"
    r"whats?\s+the\s+score|whats?\s+the\s+status|status\s+of\s+(?:the|that|this)\s+(?:game|match)"
    r")\b",
    re.IGNORECASE,
)


def _extract_subject_entities(text: str) -> str:
    """Strip generic sports intent and follow-up filler words to isolate team/player/league names."""
    return " ".join(
        w for w in re.findall(r"[A-Za-z0-9À-ÿ'.-]+", text or "")
        if w.lower().strip("'.-") not in _INTENT_WORDS_TO_STRIP
    ).strip()


def enrich_sports_tool_args(args: dict, history: list) -> dict:
    """Ensure sports tools never lose team/player/league context from the user's prompt
    or from earlier user turns on pronoun/match follow-ups (e.g. 'track this game', 'hows the game going')."""
    if not isinstance(args, dict) or not history:
        return args

    user_texts = [
        _extract_raw_user_text(m)
        for m in history
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    ]
    user_texts = [t for t in user_texts if t and not t.startswith("Now summarize") and not t.startswith("You have used")]
    if not user_texts:
        return args

    latest_user = user_texts[-1]
    combined_args_str = " ".join(str(v) for v in args.values() if v).lower()

    # Check if latest user turn is a pronoun or demonstrative match follow-up
    is_plural_tracked = bool(
        re.search(
            r"\b(?:tracked\s+games|tracked\s+matches|both\s+games|both\s+matches|these\s+games|these\s+matches)\b",
            latest_user.lower(),
        )
    )
    has_followup_cue = (
        bool(_FOLLOWUP_MATCH_PATTERN.search(latest_user.lower())) or not _extract_subject_entities(latest_user)
    ) and not is_plural_tracked
    context_snippets = []

    if has_followup_cue and len(user_texts) >= 2 and not args.get("team") and not args.get("player"):
        # Scan backwards through prior user messages to find the most recent message with a concrete subject/team entity
        for prev_user in reversed(user_texts[:-1]):
            prev_entities = _extract_subject_entities(prev_user)
            if prev_entities:
                if prev_entities.lower() not in combined_args_str:
                    context_snippets.append(prev_entities)
                break

    if latest_user.lower() not in combined_args_str:
        context_snippets.append(latest_user)

    if context_snippets:
        enriched = dict(args)
        existing_q = str(enriched.get("query") or "").strip()
        extra_context = " ".join(context_snippets).strip()
        enriched["query"] = f"{existing_q} {extra_context}".strip() if existing_q else extra_context
        return enriched

    return args


_NON_SPORTS_TRACK_RE = re.compile(
    r"\b(?:soundtrack|backing\s+track|diss\s+track|title\s+track|bonus\s+track|"
    r"similar\s+track|next\s+track|previous\s+track|play\s+track|song|album|artist|"
    r"spotify|youtube|package|parcel|order|shipment|flight|reacted\s+with)\b",
    re.IGNORECASE,
)


def detect_explicit_tracker_intent(user_text: str) -> typing.Optional[typing.Tuple[str, str]]:
    """Detect if a user message is explicitly asking to start, stop, or list live sports match tracking.
    Returns (action, query) or None."""
    if not user_text:
        return None
    q = _extract_raw_user_text({"content": user_text}).strip()
    q_low = q.lower()
    if not q_low or _NON_SPORTS_TRACK_RE.search(q_low):
        return None

    m_stop = re.search(r"\b(?:untrack|stop\s+tracking|cancel\s+tracking|end\s+tracking)\b(?:\s+(.*))?", q_low)
    if m_stop:
        return ("stop", (m_stop.group(1) or "").strip(" ?.!,"))

    if re.search(
        r"\b(?:what\s+(?:games?|matches?)\s+are\s+(?:you|we)\s+tracking|"
        r"which\s+(?:games?|matches?)\s+are\s+(?:being\s+)?tracked|"
        r"list\s+tracked|are\s+you\s+tracking\s+(?:any|the|this))\b",
        q_low,
    ):
        return ("list", "")

    m_start = re.match(
        r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+|yo\s+|hey\s+)?"
        r"(?:start\s+tracking|track|monitor|follow\s+live)\s+(.+?)\s*[?.!]*$",
        q_low,
    )
    if m_start:
        return ("start", m_start.group(1).strip())

    m_mid = re.search(
        r"\b(?:track\s+(?:this|that|the)\s+(?:game|match)|start\s+tracking\s+(?:this|that|the)\s+(?:game|match))\b",
        q_low,
    )
    if m_mid:
        return ("start", m_mid.group(0).strip())

    return None


def detect_missed_sports_tool_call(
    history: list,
    active_channel_id: int = 0,
) -> typing.Optional[typing.Tuple[str, dict]]:
    """Detect when an 8B model skips calling a sports tool on iteration 0 (e.g. roleplaying
    'MATCH TRACKING ACTIVE' or repeating a stale 55' score from chat_history without fetching fresh ESPN data).
    Returns (tool_name, tool_args) or None."""
    if not history:
        return None

    user_texts = [
        _extract_raw_user_text(m)
        for m in history
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    ]
    user_texts = [t for t in user_texts if t and not t.startswith("Now summarize") and not t.startswith("You have used")]
    if not user_texts:
        return None

    latest_user = user_texts[-1]
    q_low = latest_user.lower()

    # 1. Explicit live match tracker command ("track this game", "track real madrid", "untrack")
    tracker_intent = detect_explicit_tracker_intent(latest_user)
    if tracker_intent is not None:
        action, target_q = tracker_intent
        args = {"action": action, "query": target_q}
        if action == "start":
            args = enrich_sports_tool_args(args, history)
        return ("skill_track_live_game", args)

    # 2. Live sports status / score / match follow-up when channel already has sports context
    if _NON_SPORTS_TRACK_RE.search(q_low) or q_low.startswith("!"):
        return None

    has_prior_sports_turn = any(
        (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "tool"
        and (m.get("name") if isinstance(m, dict) else getattr(m, "name", None))
        in ("skill_sports_info", "skill_track_live_game", "skill_betting_odds")
        for m in history
    )
    last_match_q = ""
    try:
        from src.skills.sports.live_tracker import get_channel_last_match_query
        last_match_q, _ = get_channel_last_match_query(active_channel_id)
    except Exception:
        pass

    if has_prior_sports_turn or last_match_q:
        is_live_match_followup = bool(
            re.search(
                r"\b(?:status|score|scores|scored|scorer|goal|goals|game|match|going|"
                r"live|playing|minute|half|halftime|over|finished|winning|losing|leading|"
                r"lineup|lineups|stats|possession|shots|cards?)\b",
                q_low,
            )
        )
        if is_live_match_followup:
            args = enrich_sports_tool_args({"query": latest_user}, history)
            return ("skill_sports_info", args)

    return None


_REPLY_SONG_CARD_RE = re.compile(
    r"\[Replying to song card:\s*'([^']+)'\s*by\s*'([^']+)'\]",
    re.IGNORECASE,
)


def enrich_music_tool_args(args: dict, history: list) -> dict:
    """Enrich skill_top_music arguments with song-card reply seeds and validate 3-Tier
    music spectrum shift context so comparative words in normal conversation are never treated as shifts."""
    if not isinstance(args, dict) or not history:
        return args

    user_msgs = [
        m for m in history
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    ]
    if not user_msgs:
        return args

    last_msg = user_msgs[-1]
    raw_content = str(last_msg.get("content", "") if isinstance(last_msg, dict) else getattr(last_msg, "content", "") or "")
    clean_user = _extract_raw_user_text(last_msg)
    if not clean_user or clean_user.startswith("Now summarize") or clean_user.startswith("You have used"):
        return args

    enriched = dict(args)

    # 1. If the user is replying to a song card and didn't specify a seed song or artist, attach it
    m_reply = _REPLY_SONG_CARD_RE.search(raw_content)
    if m_reply and not enriched.get("similar_to") and not enriched.get("artist"):
        enriched["similar_to"] = f"{m_reply.group(1).strip()} by {m_reply.group(2).strip()}"

    # 2. Check 3-Tier Music Spectrum Shift context disambiguation
    try:
        from src.skills.music.spectrum import (
            parse_spectrum_shift,
            is_valid_music_shift_context,
            has_non_music_domain_context,
        )
        valid_shift_ctx = is_valid_music_shift_context(raw_content, history)
        user_shift = parse_spectrum_shift(clean_user)
        arg_shift = (
            parse_spectrum_shift(str(enriched.get("shift") or ""))
            or parse_spectrum_shift(str(enriched.get("mood") or ""))
        )

        if valid_shift_ctx and user_shift is not None:
            if not enriched.get("shift"):
                enriched["shift"] = clean_user
        elif (user_shift is not None or arg_shift is not None) and not valid_shift_ctx:
            if has_non_music_domain_context(clean_user) or not enriched.get("genre"):
                enriched["shift"] = f"NON_MUSIC_CONVERSATION: {clean_user}"
    except Exception:
        pass

    return enriched


def detect_missed_music_shift_tool_call(history: list) -> typing.Optional[typing.Tuple[str, dict]]:
    """Detect when an 8B model skips calling skill_top_music on a valid 3-Tier music spectrum shift
    (e.g. replying 'faster' to a song card or typing 'go harder' right after a song card)."""
    if not history:
        return None

    user_msgs = [
        m for m in history
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    ]
    if not user_msgs:
        return None

    last_msg = user_msgs[-1]
    raw_content = str(last_msg.get("content", "") if isinstance(last_msg, dict) else getattr(last_msg, "content", "") or "")
    clean_user = _extract_raw_user_text(last_msg)
    if not clean_user or clean_user.startswith("!") or clean_user.startswith("Now summarize") or clean_user.startswith("You have used"):
        return None

    try:
        from src.skills.music.spectrum import is_valid_music_shift_context
        if is_valid_music_shift_context(raw_content, history):
            args = enrich_music_tool_args({"shift": clean_user}, history)
            return ("skill_top_music", args)
    except Exception:
        pass

    return None


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
      are shortened, since the model has already summarized them. This keeps
      large search/website dumps from overflowing the context window, which
      would otherwise make Ollama silently drop the system prompt.
    - Earlier turns' live sports tool outputs are explicitly marked as expired
      snapshots so 8B models never regurgitate a stale minute clock or score
      on subsequent turns without calling the tool again.
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
            elif len(content) > old_tool_chars:
                rest[i] = {**msg, "content": content[:old_tool_chars].rstrip() + "\n...[older tool output truncated]"}

    if len(rest) > limit:
        start = len(rest) - limit
        forward = next((i for i in range(start, len(rest)) if _role(rest[i]) == "user"), None)
        if forward is None:
            # The current turn alone exceeds the limit; keep it whole rather than orphan it.
            forward = max((i for i in range(start) if _role(rest[i]) == "user"), default=0)
        rest = rest[forward:]

    return [system] + rest


# ---------------------------------------------------------------------------
# Reply post-processing
# ---------------------------------------------------------------------------

# Spans that auto-ping must never rewrite: <...> (mentions, escaped links), URLs, inline code.
_PROTECTED_SPANS = re.compile(r'(<[^>\s]+>|https?://\S+|`[^`]*`)')
MIN_PING_NAME_LENGTH = 3


def apply_auto_ping(reply: str, known_users: dict) -> str:
    """Replace known user names in the reply with Discord mentions.
    Skips names shorter than MIN_PING_NAME_LENGTH to avoid pinging on common short words."""
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

    # Check if the referenced message contains a song card
    m_song = re.search(r'(?im)^[^\S\n]*(?:[-*•][^\S\n]*)?(?:\*\*)?(?:Song|Track):\*{0,2}\s*(.+)', text)
    m_art = re.search(r'(?:\*\*|\b)Artist:\*{0,2}\s*(.+)', text, re.IGNORECASE)
    if m_song and m_art:
        clean_s = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_song.group(1)).strip(" *-_#:`\"'")
        clean_a = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_art.group(1)).strip(" *-_#:`\"'")
        return f" [Replying to song card: '{clean_s}' by '{clean_a}']"

    snippet = text[:100].replace('\n', ' ').strip()
    return f" [Replying to: \"{snippet}\"]"



# ---------------------------------------------------------------------------
# Tool leakage and AI meta-refusal sanitization
# ---------------------------------------------------------------------------

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
    re.IGNORECASE
)

_HALLUCINATED_BETTING_URL_RE = re.compile(
    r'<https?://www\.bet365\.com/[^\s>]+>',
    re.IGNORECASE
)


def sanitize_agent_reply(reply: str) -> str:
    """Sanitizes replies by removing accidental leaks of internal function names,
    hallucinated bookmaker URLs, and canned AI meta-excuses."""
    if not reply:
        return reply

    # 1. Remove meta-lecture lines
    lines = reply.splitlines()
    cleaned_lines = []
    for line in lines:
        if _DISCLAIMER_HEADER_RE.search(line):
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # 2. Rewrite explicit internal tool mentions in prose to natural phrases
    text = re.sub(r'(?i)\buse\s+(?:my\s+)?search_web(?:\s+tool)?\b', 'search online', text)
    text = re.sub(r'(?i)\buse\s+(?:my\s+)?read_website(?:\s+tool)?\b', 'inspect the web page', text)
    text = re.sub(r'(?i)\btools like search_web or read_website\b', 'search and web research', text)
    text = re.sub(r'(?i)\baccessible via my tools\b', 'found in current public records', text)
    text = re.sub(r'(?i)\bvia my tools\b', 'in current records', text)

    # 3. Strip any remaining raw tool function names
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

    # 4. Remove hallucinated fake query-string bookmaker links
    text = _HALLUCINATED_BETTING_URL_RE.sub('[Bet365](<https://www.bet365.com>)', text)

    # Clean up any excessive blank lines created by removals
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# Centralized Time & Timezone Awareness
# ---------------------------------------------------------------------------

_FIXED_TZ_MAP: dict[str, tuple[int, int, str]] = {
    "UTC": (0, 0, "UTC"),
    "GMT": (0, 0, "GMT"),
    "Z": (0, 0, "UTC"),
    "BST": (1, 0, "BST"),
    "CET": (1, 0, "CET"),
    "CEST": (2, 0, "CEST"),
    "EET": (2, 0, "EET"),
    "EEST": (3, 0, "EEST"),
    "EDT": (-4, 0, "EDT"),
    "EST": (-5, 0, "EST"),
    "CDT": (-5, 0, "CDT"),
    "CST": (-6, 0, "CST"),
    "MDT": (-6, 0, "MDT"),
    "MST": (-7, 0, "MST"),
    "PDT": (-7, 0, "PDT"),
    "PST": (-8, 0, "PST"),
}

_WINDOWS_TZ_ABBR_MAP: dict[str, str] = {
    "Coordinated Universal Time": "UTC",
    "GMT Standard Time": "GMT",
    "GMT Daylight Time": "BST",
    "W. Europe Standard Time": "CET",
    "W. Europe Daylight Time": "CEST",
    "Central European Standard Time": "CET",
    "Central European Summer Time": "CEST",
    "Central Europe Standard Time": "CET",
    "Central Europe Daylight Time": "CEST",
    "Romance Standard Time": "CET",
    "Romance Daylight Time": "CEST",
    "E. Europe Standard Time": "EET",
    "E. Europe Daylight Time": "EEST",
    "Eastern Standard Time": "EST",
    "Eastern Daylight Time": "EDT",
    "Central Standard Time": "CST",
    "Central Daylight Time": "CDT",
    "Mountain Standard Time": "MST",
    "Mountain Daylight Time": "MDT",
    "US Mountain Standard Time": "MST",
    "Pacific Standard Time": "PST",
    "Pacific Daylight Time": "PDT",
}

_MONTH_NUMS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the n-th occurrence (1-indexed) of weekday (0=Mon..6=Sun) in a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_sunday_of_month(year: int, month: int) -> date:
    """Return the last Sunday of the given month."""
    if month == 12:
        last_day = date(year, 12, 31)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last_day.weekday() - 6) % 7
    return last_day - timedelta(days=offset)


def _is_us_dst(utc_dt: datetime) -> bool:
    """US Daylight Saving Time: 2nd Sunday in March (07:00 UTC) to 1st Sunday in November (06:00 UTC)."""
    y = utc_dt.year
    start = datetime.combine(_nth_weekday_of_month(y, 3, 6, 2), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=7)
    end = datetime.combine(_nth_weekday_of_month(y, 11, 6, 1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=6)
    return start <= utc_dt.astimezone(timezone.utc) < end


def _is_eu_dst(utc_dt: datetime) -> bool:
    """EU Summer Time: Last Sunday in March (01:00 UTC) to Last Sunday in October (01:00 UTC)."""
    y = utc_dt.year
    start = datetime.combine(_last_sunday_of_month(y, 3), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=1)
    end = datetime.combine(_last_sunday_of_month(y, 10), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=1)
    return start <= utc_dt.astimezone(timezone.utc) < end


def _short_tz_abbr(dt: datetime) -> str:
    """Return a clean, compact timezone abbreviation (e.g. 'MDT', 'CEST', 'UTC') even on Windows."""
    raw = (dt.tzname() or "").strip()
    if not raw:
        return "UTC"
    if raw in _WINDOWS_TZ_ABBR_MAP:
        return _WINDOWS_TZ_ABBR_MAP[raw]
    if len(raw) <= 5 and " " not in raw:
        return raw
    initials = "".join(w[0].upper() for w in raw.split() if w and w[0].isalpha())
    return initials or raw


def _resolve_tz_token(tz_str: str, ref_utc: datetime | None = None) -> tzinfo | None:
    """Resolve an IANA name, timezone abbreviation, or UTC±N offset into a Python tzinfo."""
    clean = (tz_str or "").strip()
    if not clean:
        return None
    ref = (ref_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    up = clean.upper()

    # 1. Fixed abbreviation table
    if up in _FIXED_TZ_MAP:
        h, m, label = _FIXED_TZ_MAP[up]
        return timezone(timedelta(hours=h, minutes=m), label)

    # 2. UTC±HH[:MM] or GMT±HH[:MM]
    m_off = re.match(r'^(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::(\d{2}))?$', up)
    if m_off:
        sign = 1 if m_off.group(1) == "+" else -1
        hh = int(m_off.group(2))
        mm = int(m_off.group(3) or 0)
        label = f"UTC{m_off.group(1)}{hh:02d}:{mm:02d}" if mm else f"UTC{m_off.group(1)}{hh}"
        return timezone(timedelta(minutes=sign * (hh * 60 + mm)), label)

    # 3. Dynamic DST-aware regional aliases & IANA names (works even on Windows without tzdata)
    low = clean.lower().replace(" ", "_")
    us_dst = _is_us_dst(ref)
    eu_dst = _is_eu_dst(ref)

    if up == "ET" or low in ("america/new_york", "america/toronto", "america/detroit", "us/eastern"):
        return timezone(timedelta(hours=-4 if us_dst else -5), "EDT" if us_dst else "EST")
    if up == "CT" or low in ("america/chicago", "america/winnipeg", "us/central"):
        return timezone(timedelta(hours=-5 if us_dst else -6), "CDT" if us_dst else "CST")
    if up == "MT" or low in ("america/edmonton", "america/denver", "america/calgary", "us/mountain"):
        return timezone(timedelta(hours=-6 if us_dst else -7), "MDT" if us_dst else "MST")
    if low in ("america/phoenix", "us/arizona"):
        return timezone(timedelta(hours=-7), "MST")
    if up == "PT" or low in ("america/los_angeles", "america/vancouver", "us/pacific"):
        return timezone(timedelta(hours=-7 if us_dst else -8), "PDT" if us_dst else "PST")
    if up in ("CE", "CET/CEST", "EUROPE") or low in (
        "europe/berlin", "europe/paris", "europe/sarajevo", "europe/zagreb", "europe/belgrade",
        "europe/vienna", "europe/rome", "europe/madrid", "europe/amsterdam", "europe/warsaw",
        "europe/stockholm", "europe/zurich", "europe/prague", "europe/budapest", "europe/brussels",
    ):
        return timezone(timedelta(hours=2 if eu_dst else 1), "CEST" if eu_dst else "CET")
    if up == "UK" or low in ("europe/london", "europe/dublin"):
        return timezone(timedelta(hours=1 if eu_dst else 0), "BST" if eu_dst else "GMT")
    if low in ("europe/athens", "europe/bucharest", "europe/helsinki", "europe/kyiv", "europe/sofia"):
        return timezone(timedelta(hours=3 if eu_dst else 2), "EEST" if eu_dst else "EET")

    # 4. Try standard library ZoneInfo if available on the host OS
    if ZoneInfo is not None:
        try:
            zi = ZoneInfo(clean)
            dt_test = ref.astimezone(zi)
            return timezone(dt_test.utcoffset() or timedelta(0), _short_tz_abbr(dt_test))
        except Exception:
            pass
    return None


def resolve_configured_timezone(tz_override: str | None = None, ref_utc: datetime | None = None) -> tzinfo:
    """Resolve BOT_TIMEZONE env override (or explicit tz_override), falling back to system local timezone."""
    raw = tz_override if tz_override is not None else os.getenv("BOT_TIMEZONE", "")
    raw = (raw or "").strip()
    if raw:
        resolved = _resolve_tz_token(raw, ref_utc=ref_utc)
        if resolved is not None:
            return resolved
    # Default: host system local timezone
    sys_dt = (ref_utc or datetime.now(timezone.utc)).astimezone()
    abbr = _short_tz_abbr(sys_dt)
    offset = sys_dt.utcoffset() or timedelta(0)
    return timezone(offset, abbr)


def get_local_now(now: datetime | None = None, tz_override: str | None = None) -> datetime:
    """Return the current datetime projected into the bot's configured local timezone (or system time if blank)."""
    if now is None:
        utc_now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        utc_now = now.astimezone().astimezone(timezone.utc)
    else:
        utc_now = now.astimezone(timezone.utc)
    local_tz = resolve_configured_timezone(tz_override=tz_override, ref_utc=utc_now)
    return utc_now.astimezone(local_tz)


def build_reference_clock(now: datetime | None = None, tz_override: str | None = None) -> str:
    """Build a 1-line multi-zone reference clock covering Local, US (ET/PT), Europe (CET/CEST), and UTC."""
    local_dt = get_local_now(now=now, tz_override=tz_override)
    utc_dt = local_dt.astimezone(timezone.utc)
    et_tz = _resolve_tz_token("ET", ref_utc=utc_dt) or timezone(timedelta(hours=-4), "EDT")
    pt_tz = _resolve_tz_token("PT", ref_utc=utc_dt) or timezone(timedelta(hours=-7), "PDT")
    eu_tz = _resolve_tz_token("Europe/Berlin", ref_utc=utc_dt) or timezone(timedelta(hours=2), "CEST")

    et_dt = utc_dt.astimezone(et_tz)
    pt_dt = utc_dt.astimezone(pt_tz)
    eu_dt = utc_dt.astimezone(eu_tz)

    local_abbr = _short_tz_abbr(local_dt)
    local_time = local_dt.strftime("%I:%M %p").lstrip("0")
    local_part = f"{local_dt.strftime('%a, %b %d, %Y')} at {local_time} {local_abbr} (Local)"

    def _fmt_us(dt: datetime) -> str:
        t_str = dt.strftime("%I:%M %p").lstrip("0")
        abbr = _short_tz_abbr(dt)
        if dt.date() != local_dt.date():
            return f"{dt.strftime('%a %b %d')} {t_str} {abbr}"
        return f"{t_str} {abbr}"

    eu_abbr = _short_tz_abbr(eu_dt)
    eu_part = f"{eu_dt.strftime('%a, %b %d, %Y')} at {eu_dt.strftime('%H:%M')} {eu_abbr} (Europe)"
    utc_part = f"{utc_dt.strftime('%a, %b %d, %Y')} at {utc_dt.strftime('%H:%M')} UTC"

    parts = [local_part]
    if local_abbr not in ("EDT", "EST"):
        parts.append(_fmt_us(et_dt))
    if local_abbr not in ("PDT", "PST"):
        parts.append(_fmt_us(pt_dt))
    if local_abbr not in ("CET", "CEST"):
        parts.append(eu_part)
    if local_abbr != "UTC":
        parts.append(utc_part)

    return "🕒 Current Time: " + " | ".join(parts)


def _format_relative_delta(event_utc: datetime, now_utc: datetime) -> str:
    """Format a compact human-readable countdown or elapsed time (e.g. 'in 2h 15m', '40m ago')."""
    total_sec = int(round((event_utc - now_utc).total_seconds()))
    is_past = total_sec < 0
    abs_min = abs(total_sec) // 60

    if abs_min < 2:
        return "now"
    days, rem_min = divmod(abs_min, 1440)
    hours, mins = divmod(rem_min, 60)

    if days >= 2:
        span = f"{days}d {hours}h" if hours else f"{days}d"
    elif days == 1:
        span = f"1d {hours}h" if hours else "1d"
    elif hours > 0:
        span = f"{hours}h {mins}m" if mins else f"{hours}h"
    else:
        span = f"{mins}m"

    return f"{span} ago" if is_past else f"in {span}"


_SECTION_DATE_HEADER_RE = re.compile(
    r'(?i)(?:📅|---|\bDates?:)\s*.*?\b(?:'
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:,?\s*(\d{4}))?'
    r'|(\d{4})-(\d{2})-(\d{2})'
    r')\b'
)

_TIMESTAMP_TOKEN_RE = re.compile(
    r'(?P<iso>\b(?P<iso_y>\d{4})-(?P<iso_m>\d{2})-(?P<iso_d>\d{2})[T\s](?P<iso_t>\d{2}:\d{2})(?::\d{2})?(?:Z|\s*UTC)\b)'
    r'|(?P<md>(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+)?'
    r'(?P<md_mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(?P<md_day>\d{1,2})(?:,?\s*(?P<md_year>\d{4}))?'
    r'\s*(?:at\b|-|–|,)?\s*(?P<md_time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\s*(?P<md_tz>UTC|GMT|BST|CEST|CET|EEST|EET|EDT|EST|CDT|CST|MDT|MST|PDT|PST|ET|CT|MT|PT)\b)'
    r'|(?P<sl>\b(?P<sl_m>\d{1,2})/(?P<sl_d>\d{1,2})(?:/(?P<sl_y>\d{2,4}))?\s*(?:at\b|-|–|,)?\s*(?P<sl_time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\s*(?P<sl_tz>UTC|GMT|BST|CEST|CET|EEST|EET|EDT|EST|CDT|CST|MDT|MST|PDT|PST|ET|CT|MT|PT)\b)'
    r'|(?P<to>\b(?P<to_time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\s*(?P<to_tz>UTC|GMT|BST|CEST|CET|EEST|EET|EDT|EST|CDT|CST|MDT|MST|PDT|PST|ET|CT|MT|PT)\b)',
    re.IGNORECASE,
)


def _parse_clock_str(clock_raw: str) -> tuple[int, int] | None:
    """Parse '10:00 PM', '19:00', or '14:30:00' into (hour_24, minute)."""
    s = (clock_raw or "").strip().upper()
    m = re.match(r'^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?$', s)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    ampm = m.group(3)
    if ampm == "PM" and hh < 12:
        hh += 12
    elif ampm == "AM" and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def enrich_tool_output_with_time_context(
    tool_output: str,
    now: datetime | None = None,
    tz_override: str | None = None,
) -> str:
    """
    Centrally enrich any tool output that contains schedule/article timestamps with:
      1. A 1-line multi-zone reference clock header (`🕒 Current Time: ...`).
      2. Compact inline conversions (`(Today 8:00 PM MDT / Fri 04:00 CEST, in 15m)`)
         so the LLM never miscalculates timezone offsets, European dates, or UTC midnight rollovers.
    """
    if not tool_output or not isinstance(tool_output, str):
        return tool_output
    if (
        tool_output.startswith("🕒 Current Time:")
        or tool_output.startswith("[🕒 Current Time:")
        or "PARALLEL_EXECUTION_BLOCKED" in tool_output
        or "=== VERIFIED YOUTUBE" in tool_output
        or "=== TOP " in tool_output
    ):
        return tool_output

    local_now = get_local_now(now=now, tz_override=tz_override)
    utc_now = local_now.astimezone(timezone.utc)
    local_tz = local_now.tzinfo or timezone.utc
    local_abbr = _short_tz_abbr(local_now)
    eu_tz = _resolve_tz_token("Europe/Berlin", ref_utc=utc_now) or timezone(timedelta(hours=2), "CEST")

    annotated_count = 0
    current_section_date: date | None = None
    out_lines: list[str] = []

    for line in tool_output.splitlines():
        # Update section date if this line is a schedule date header
        m_hdr = _SECTION_DATE_HEADER_RE.search(line)
        if m_hdr:
            try:
                if m_hdr.group(1):
                    mon = _MONTH_NUMS[m_hdr.group(1)[:3].lower()]
                    day = int(m_hdr.group(2))
                    yr = int(m_hdr.group(3)) if m_hdr.group(3) else local_now.year
                    current_section_date = date(yr, mon, day)
                elif m_hdr.group(4):
                    current_section_date = date(int(m_hdr.group(4)), int(m_hdr.group(5)), int(m_hdr.group(6)))
            except Exception:
                pass

        # Skip lines that were already annotated
        if f"{local_abbr} /" in line or "🕒 Current Time:" in line:
            out_lines.append(line)
            continue

        def _replace_ts(m: re.Match) -> str:
            nonlocal annotated_count
            full_match = m.group(0)
            try:
                if m.group("iso"):
                    yr = int(m.group("iso_y"))
                    mon = int(m.group("iso_m"))
                    day = int(m.group("iso_d"))
                    hm = _parse_clock_str(m.group("iso_t"))
                    if not hm:
                        return full_match
                    src_tz: tzinfo = timezone.utc
                elif m.group("md"):
                    mon = _MONTH_NUMS[m.group("md_mon")[:3].lower()]
                    day = int(m.group("md_day"))
                    yr = int(m.group("md_year")) if m.group("md_year") else local_now.year
                    hm = _parse_clock_str(m.group("md_time"))
                    if not hm:
                        return full_match
                    src_tz = _resolve_tz_token(m.group("md_tz"), ref_utc=utc_now) or timezone.utc
                elif m.group("sl"):
                    mon = int(m.group("sl_m"))
                    day = int(m.group("sl_d"))
                    raw_y = m.group("sl_y")
                    yr = (2000 + int(raw_y) if len(raw_y) == 2 else int(raw_y)) if raw_y else local_now.year
                    hm = _parse_clock_str(m.group("sl_time"))
                    if not hm:
                        return full_match
                    src_tz = _resolve_tz_token(m.group("sl_tz"), ref_utc=utc_now) or timezone.utc
                else:
                    hm = _parse_clock_str(m.group("to_time"))
                    if not hm:
                        return full_match
                    src_tz = _resolve_tz_token(m.group("to_tz"), ref_utc=utc_now) or timezone.utc
                    base_d = current_section_date or utc_now.astimezone(src_tz).date()
                    yr, mon, day = base_d.year, base_d.month, base_d.day

                src_dt = datetime(yr, mon, day, hm[0], hm[1], tzinfo=src_tz)
                ev_utc = src_dt.astimezone(timezone.utc)
                ev_local = ev_utc.astimezone(local_tz)
                ev_eu = ev_utc.astimezone(eu_tz)
                eu_abbr = _short_tz_abbr(ev_eu)

                # Format Local time + relative day label
                day_diff = (ev_local.date() - local_now.date()).days
                if day_diff == 0:
                    day_lbl = "Today"
                elif day_diff == 1:
                    day_lbl = f"Tomorrow {ev_local.strftime('%a %b %d')}"
                elif day_diff == -1:
                    day_lbl = "Yesterday"
                else:
                    day_lbl = ev_local.strftime("%a %b %d")

                local_clock = ev_local.strftime("%I:%M %p").lstrip("0")
                local_piece = f"{day_lbl} {local_clock} {local_abbr}"

                # Format European time (unless Local is already CET/CEST)
                if local_abbr in ("CET", "CEST"):
                    zone_str = local_piece
                else:
                    eu_day_prefix = f"{ev_eu.strftime('%a')} " if ev_eu.date() != ev_local.date() else ""
                    eu_piece = f"{eu_day_prefix}{ev_eu.strftime('%H:%M')} {eu_abbr}"
                    zone_str = f"{local_piece} / {eu_piece}"

                rel_str = _format_relative_delta(ev_utc, utc_now)
                annotated_count += 1
                return f"{full_match} ({zone_str}, {rel_str})"
            except Exception:
                return full_match

        out_lines.append(_TIMESTAMP_TOKEN_RE.sub(_replace_ts, line))

    enriched_body = "\n".join(out_lines)
    has_date_context = (
        annotated_count > 0
        or current_section_date is not None
        or bool(re.search(r'\b(?:schedule|fixtures?|kickoff|tournament|grand\s+prix|matchup|scoreboard)\b', tool_output, re.IGNORECASE))
    )
    if not has_date_context:
        return tool_output

    header = f"[{build_reference_clock(now=local_now, tz_override=tz_override)}]"
    return f"{header}\n{enriched_body}"


