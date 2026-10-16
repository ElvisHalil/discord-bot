"""
Sports Conversational Context & Turn-0 Intent Helpers.

Handles pronoun/demonstrative match follow-up enrichment ('track this game',
'hows the game going', 'who scored last goal') and turn-0 missed sports tool detection.
"""

import re
import typing


def extract_raw_user_text(msg) -> str:
    """Strip Discord/Telegram wrapper prefixes ('User X says: ...') from a user message in history."""
    content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
    if not content:
        return ""
    text = str(content).strip()
    m = re.match(
        r"^(?:User\s+.+?\s+says(?:\s*\[.*?\])*|.+\(User ID:\s*\d+\)(?:\s*\[.*?\])*)\s*:\s*(.+)$",
        text,
        flags=re.DOTALL,
    )
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

_NON_SPORTS_TRACK_RE = re.compile(
    r"\b(?:soundtrack|backing\s+track|diss\s+track|title\s+track|bonus\s+track|"
    r"similar\s+track|next\s+track|previous\s+track|play\s+track|song|album|artist|"
    r"spotify|youtube|package|parcel|order|shipment|flight|reacted\s+with)\b",
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
        extract_raw_user_text(m)
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


def detect_explicit_tracker_intent(user_text: str) -> typing.Optional[typing.Tuple[str, str]]:
    """Detect if a user message is explicitly asking to start, stop, or list live sports match tracking.
    Returns (action, query) or None."""
    if not user_text:
        return None
    q = extract_raw_user_text({"content": user_text}).strip()
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
        extract_raw_user_text(m)
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
