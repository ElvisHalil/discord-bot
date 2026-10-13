"""
Orchestrator helpers.

Pure functions used by main.py's message loop. They live here (instead of
inline in on_message) so they can be unit-tested without Discord or Ollama.
"""

import inspect
import re
import types
import typing

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
        "seed": "similar_to",
        "song": "similar_to",
        "track": "similar_to",
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
    Canonical names always win over aliases."""
    if not isinstance(args, dict):
        return {}

    params = inspect.signature(func).parameters
    aliases = TOOL_ARG_ALIASES.get(tool_name, {})

    normalized = {k: v for k, v in args.items() if k in params and v is not None}
    for key, value in args.items():
        target = aliases.get(key)
        if target and target in params and target not in normalized and value not in (None, ""):
            normalized[target] = value

    return {k: _coerce(v, params[k].annotation) for k, v in normalized.items()}


# ---------------------------------------------------------------------------
# Chat history management
# ---------------------------------------------------------------------------

OLD_TOOL_OUTPUT_CHARS = 800


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
            content = str(msg.get("content") or "")
            if len(content) > old_tool_chars:
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

