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
- Music spectrum shift context: `src.skills.music.context`
"""

import base64
import datetime
import inspect
import logging
import re
import types
import typing

from src.skills.flights.context import (
    build_google_flights_url,
    detect_missed_flight_tool_call,
)
from src.skills.music.context import (
    detect_missed_music_shift_tool_call,
    enrich_music_tool_args,
)
from src.skills.sports.context import (
    detect_explicit_tracker_intent,
    detect_missed_sports_tool_call,
    enrich_sports_tool_args,
)
from src.utils.domain_vocab import (
    EPHEMERAL_SPORTS_TOOLS as _EPHEMERAL_SPORTS_TOOLS,
    MONTH_MAP_SYNC as _MONTH_MAP_SYNC,
    TOOL_ARG_ALIASES,
)
from src.utils.timezones import (
    build_reference_clock,
    enrich_tool_output_with_time_context,
    get_local_now,
    resolve_configured_timezone,
)

logger = logging.getLogger(__name__)


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
    """Ensure `query` on `skill_flight_info` / `skill_flight_tracker` preserves the user's latest prompt
    (including flexible-date intent, direct-schedule questions, sort preferences, and return dates)
    even when an 8B model only populates `origin` and `destination` or hallucinates a stale year."""
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

    user_years = set(re.findall(r"\b(20\d{2})\b", latest_user_text))
    has_next_year = bool(re.search(r"\bnext\s+year\b", latest_user_text, re.IGNORECASE))

    cur_q = str(enriched.get("query") or "").strip()
    if not has_next_year:
        if cur_q:
            cur_q = re.sub(
                r"\b(20\d{2})\b",
                lambda m: m.group(1) if m.group(1) in user_years else "",
                cur_q,
            )
            cur_q = re.sub(r"\s{2,}", " ", cur_q).strip()
        for d_key in ("date", "return_date"):
            val = enriched.get(d_key)
            if isinstance(val, str):
                arg_years = set(re.findall(r"\b(20\d{2})\b", val))
                if arg_years and not arg_years.issubset(user_years):
                    enriched.pop(d_key, None)

    if not cur_q:
        enriched["query"] = latest_user_text
    elif latest_user_text.lower() not in cur_q.lower():
        enriched["query"] = f"{latest_user_text} ({cur_q})"
    else:
        enriched["query"] = cur_q
    return enriched


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
                        "OR TRACKING STATUS. You MUST call skill_sports_info or skill_sports_tracker again "
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


INTRA_TURN_MAX_TOOL_CHARS = 24000
INTRA_TURN_OLDER_STEP_CHARS = 2500


def compact_intra_turn_tool_history(
    history: list,
    max_total_tool_chars: int = INTRA_TURN_MAX_TOOL_CHARS,
    older_step_chars: int = INTRA_TURN_OLDER_STEP_CHARS,
) -> None:
    """Compact older tool outputs within a multi-step research turn (in-place) when accumulated
    tool payloads exceed `max_total_tool_chars`, preserving the latest step's tool outputs in full
    so the model never overflows `num_ctx` during deep web research."""
    if not history:
        return

    tool_indices = [
        i for i, m in enumerate(history)
        if isinstance(m, dict) and m.get("role") == "tool"
    ]
    if len(tool_indices) <= 1:
        return

    total_chars = sum(len(str(history[i].get("content") or "")) for i in tool_indices)
    if total_chars <= max_total_tool_chars:
        return

    # Find where the latest assistant tool-call batch begins so its tool results stay untouched
    last_assistant_idx = max(
        (i for i, m in enumerate(history) if _role(m) == "assistant"),
        default=-1,
    )
    from src.utils.timezones import _sanitize_and_bound_tool_output

    for idx in tool_indices:
        if idx > last_assistant_idx:
            continue
        content = str(history[idx].get("content") or "")
        if len(content) > older_step_chars:
            history[idx] = {
                **history[idx],
                "content": _sanitize_and_bound_tool_output(content, max_chars=older_step_chars),
            }
            total_chars = sum(len(str(history[i].get("content") or "")) for i in tool_indices)
            if total_chars <= max_total_tool_chars:
                break



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


def _convert_latex_tokens(fragment: str) -> str:
    """Convert common LaTeX commands inside a math or text fragment into Discord-friendly Unicode/Markdown."""
    s = fragment
    s = re.sub(r'\\(?:longleftrightarrow|leftrightarrow|Leftrightarrow)\b\s*', '↔ ', s)
    s = re.sub(r'\\(?:longrightarrow|rightarrow|Rightarrow|to)\b\s*', '→ ', s)
    s = re.sub(r'\\(?:longleftarrow|leftarrow|Leftarrow)\b\s*', '← ', s)
    s = re.sub(r'\\(?:sim|approx)\b\s*', '~', s)
    s = re.sub(r'\\times\b\s*', '× ', s)
    s = re.sub(r'\\(?:text|mathrm)\{([^}]*)\}', r'\1', s)
    s = re.sub(r'\\(?:mathbf|textbf)\{([^}]*)\}', r'**\1**', s)
    s = s.replace(r'\$', '$')
    s = re.sub(r'[ \t]{2,}', ' ', s)
    s = re.sub(r'\s+([,.:;)\]])', r'\1', s)
    s = re.sub(r'([(\[])\s+', r'\1', s)
    return s.strip()


def _normalize_discord_latex(text: str) -> str:
    r"""Strip inline LaTeX math wrappers ($...$) and LaTeX commands (\rightarrow, \to, \sim, \text{...})
    that Discord cannot render, without touching normal currency dollar amounts ($1,328)."""
    if not text or ('$' not in text and '\\' not in text):
        return text

    def _replace_math_span(m: re.Match) -> str:
        inner = m.group(1)
        # Only unwrap $...$ if it contains LaTeX commands or wraps an uppercase airport/route token (e.g. $JFK$)
        if (
            '\\' in inner
            or re.fullmatch(r'\s*[A-Z]{3}(?:\s*(?:→|->|-)\s*[A-Z]{3})?\s*', inner)
        ):
            return _convert_latex_tokens(inner)
        return m.group(0)

    # Match inline $...$ where the opening $ is NOT immediately followed by a digit (preserving $1,328 to $1,671)
    # and allow escaped \$ inside the math span without terminating early.
    out = re.sub(r'\$(?!\d)((?:\\\$|[^$\n])+?)(?<!\\)\$', _replace_math_span, text)
    if '\\' in out:
        out = re.sub(r'\\(?:longleftrightarrow|leftrightarrow|Leftrightarrow)\b', '↔', out)
        out = re.sub(r'\\(?:longrightarrow|rightarrow|Rightarrow|to)\b', '→', out)
        out = re.sub(r'\\(?:longleftarrow|leftarrow|Leftarrow)\b', '←', out)
        out = re.sub(r'\\(?:sim|approx)\b\s*', '~', out)
        out = re.sub(r'\\times\b', '×', out)
        out = re.sub(r'\\(?:text|mathrm)\{([^}]*)\}', r'\1', out)
        out = re.sub(r'\\(?:mathbf|textbf)\{([^}]*)\}', r'**\1**', out)
        out = out.replace(r'\$', '$')
    out = re.sub(r'\*{4,}', '**', out)
    out = re.sub(r'~[ \t]+\$', '~$', out)
    return out


_PROSE_DATE_TOKEN_RE = re.compile(
    r"\b(?P<iso>20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))\b"
    r"|\b(?P<m1>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(?P<d1>[0-3]?\d)(?:st|nd|rd|th)?(?:,?\s+(?P<y1>20\d{2}))?\b"
    r"|\b(?P<d2>[0-3]?\d)(?:st|nd|rd|th)?\s+(?P<m2>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?(?:,?\s+(?P<y2>20\d{2}))?\b",
    re.IGNORECASE,
)

_GF_MD_OR_BARE_LINK_RE = re.compile(
    r"(?:\[(?P<label>[^\]]+)\]\(\s*<?(?P<md_url>https?://www\.google\.com/travel/flights/search\?tfs=[^>\s)]+)>?\s*\))"
    r"|(?P<bare_wrap><(?P<bare_url>https?://www\.google\.com/travel/flights/search\?tfs=[^>\s]+)>)",
    re.IGNORECASE,
)


def _extract_prose_dates(fragment: str, default_year: typing.Optional[int] = None) -> list[str]:
    """Extract ordered YYYY-MM-DD dates from prose after stripping markdown links and URLs."""
    cleaned = re.sub(r"\[[^\]]*\]\(\s*<?https?://[^)\s]+>?\s*\)", " ", fragment)
    cleaned = re.sub(r"<?https?://[^\s>]+>?", " ", cleaned)
    explicit_years = [int(y) for y in re.findall(r"\b(20\d{2})\b", cleaned)]
    eff_year = explicit_years[0] if explicit_years else default_year
    results: list[str] = []
    for m in _PROSE_DATE_TOKEN_RE.finditer(cleaned):
        iso = m.group("iso")
        if iso:
            if iso not in results:
                results.append(iso)
            continue
        m_str = m.group("m1") or m.group("m2")
        d_str = m.group("d1") or m.group("d2")
        y_str = m.group("y1") or m.group("y2")
        yr = int(y_str) if y_str else eff_year
        if not (m_str and d_str and yr):
            continue
        month_num = _MONTH_MAP_SYNC.get(m_str.lower().rstrip("."))
        if not month_num:
            continue
        try:
            dt_iso = datetime.date(yr, month_num, int(d_str)).strftime("%Y-%m-%d")
            if dt_iso not in results:
                results.append(dt_iso)
        except ValueError:
            continue
    return results


def _decode_google_flights_tfs(url: str) -> typing.Optional[dict[str, typing.Any]]:
    """Decode a Google Flights ?tfs= URL into origin, destination, encoded dates, max_stops, and currency."""
    m_tfs = re.search(r"[?&]tfs=([^&#\s>]+)", url)
    if not m_tfs:
        return None
    tfs_b64 = m_tfs.group(1)
    try:
        padded = tfs_b64 + "=" * (-len(tfs_b64) % 4)
        raw = base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError) as e:
        logger.debug(f"Failed to base64 decode Google Flights tfs parameter: {e}")
        return None
    legs = re.findall(rb"j\x05\x12\x03([A-Z]{3})r\x05\x12\x03([A-Z]{3})", raw)
    if not legs:
        return None
    orig_code = legs[0][0].decode("ascii")
    dest_code = legs[0][1].decode("ascii")
    url_dates = [d.decode("ascii") for d in re.findall(rb"20\d{2}-\d{2}-\d{2}", raw)]
    if not url_dates:
        return None
    if b"(\x00" in raw:
        max_stops: typing.Optional[int] = 0
    elif b"(\x01" in raw:
        max_stops = 1
    elif b"(\x02" in raw:
        max_stops = 2
    else:
        max_stops = None
    m_curr = re.search(r"[?&]curr=([A-Z]{3})", url)
    currency = m_curr.group(1) if m_curr else "CAD"
    return {
        "origin_code": orig_code,
        "dest_code": dest_code,
        "url_dates": url_dates,
        "max_stops": max_stops,
        "currency": currency,
    }


def _sync_google_flights_links_with_reply_dates(text: str) -> str:
    """Ensure every Google Flights ?tfs= booking link in the reply encodes the exact travel dates stated in the reply."""
    if not text or "google.com/travel/flights/search?tfs=" not in text:
        return text

    all_links = list(_GF_MD_OR_BARE_LINK_RE.finditer(text))
    if not all_links:
        return text

    first_decoded = _decode_google_flights_tfs(all_links[0].group("md_url") or all_links[0].group("bare_url"))
    fallback_year = int(first_decoded["url_dates"][0][:4]) if first_decoded and first_decoded.get("url_dates") else None

    lines = text.splitlines()
    line_dates: list[list[str]] = []
    benchmark_line_Re = re.compile(r"\b(?:horizon|benchmark|published up through|6-month)\b", re.IGNORECASE)
    for line in lines:
        if benchmark_line_Re.search(line) and "google.com/travel/flights/search?tfs=" not in line:
            line_dates.append([])
        else:
            line_dates.append(_extract_prose_dates(line, default_year=fallback_year))

    primary_dates: list[str] = []
    for d_list in line_dates:
        if len(d_list) >= 2:
            primary_dates = d_list[:2]
            break
    if not primary_dates:
        collected: list[str] = []
        for d_list in line_dates:
            for d_val in d_list:
                if d_val not in collected:
                    collected.append(d_val)
                if len(collected) >= 2:
                    break
            if len(collected) >= 2:
                break
        primary_dates = collected

    total_gf_links = len(all_links)

    new_lines: list[str] = []
    for idx, line in enumerate(lines):
        if "google.com/travel/flights/search?tfs=" not in line:
            new_lines.append(line)
            continue

        target_dates = line_dates[idx]
        if not target_dates:
            if total_gf_links == 1 and primary_dates:
                target_dates = primary_dates
            else:
                for back_idx in range(idx - 1, -1, -1):
                    if line_dates[back_idx]:
                        target_dates = line_dates[back_idx]
                        break
                if not target_dates:
                    target_dates = primary_dates

        def _replace_link(m: re.Match) -> str:
            orig_url = m.group("md_url") or m.group("bare_url")
            info = _decode_google_flights_tfs(orig_url)
            if not info or not target_dates:
                return m.group(0)
            url_dates = info["url_dates"]
            new_dep = url_dates[0]
            new_ret = url_dates[1] if len(url_dates) >= 2 else None

            if len(url_dates) >= 2 and len(target_dates) >= 2:
                if target_dates[0] <= target_dates[1]:
                    new_dep, new_ret = target_dates[0], target_dates[1]
            elif len(url_dates) == 1 and len(target_dates) >= 1:
                if len(target_dates) >= 2 and target_dates[0] < target_dates[1] and re.search(r"\bround[-\s]?trip\b|↔|<->", text, re.IGNORECASE):
                    new_dep, new_ret = target_dates[0], target_dates[1]
                else:
                    new_dep = target_dates[0]

            expected_dates = [new_dep, new_ret] if new_ret else [new_dep]
            if expected_dates == url_dates[: len(expected_dates)] and len(expected_dates) == len(url_dates):
                return m.group(0)

            try:
                rebuilt_url = build_google_flights_url(
                    origin_code=info["origin_code"],
                    dest_code=info["dest_code"],
                    date=new_dep,
                    return_date=new_ret,
                    currency=info["currency"],
                    max_stops=info["max_stops"],
                )
            except Exception as e:
                logger.debug(f"Failed to rebuild Google Flights URL in reply sync: {e}")
                return m.group(0)

            label = m.group("label")
            if label is not None:
                new_label = label.replace(url_dates[0], new_dep)
                if new_ret and len(url_dates) >= 2:
                    new_label = new_label.replace(url_dates[1], new_ret)
                return f"[{new_label}](<{rebuilt_url}>)"
            return f"<{rebuilt_url}>"

        new_lines.append(_GF_MD_OR_BARE_LINK_RE.sub(_replace_link, line))

    return "\n".join(new_lines)


def sanitize_agent_reply(reply: str) -> str:
    """Sanitizes replies by removing accidental leaks of internal function names,
    hallucinated bookmaker URLs, unrendered LaTeX math, and canned AI meta-excuses."""
    if not reply:
        return reply

    lines = [line for line in reply.splitlines() if not _DISCLAIMER_HEADER_RE.search(line)]
    text = "\n".join(lines)
    text = _normalize_discord_latex(text)
    text = _sync_google_flights_links_with_reply_dates(text)

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
