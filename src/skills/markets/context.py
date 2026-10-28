"""
Per-channel market state tracking, tool argument enrichment, and turn-0 missed-tool-call recovery.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from src.context import get_active_channel_id
from src.skills.markets.models import MarketChannelState
from src.utils.cache import BoundedCache
from src.utils.domain_vocab import contains_domain_terms

_CHANNEL_MARKET_STATE: Dict[int, MarketChannelState] = BoundedCache(maxsize=128)

# Unambiguous sports/aviation operational terms (excludes words like 'tesla', 'economy', 'business', 'boeing', 'delta' that also appear in stock/macro queries)
_NON_MARKET_SPORT_FLIGHT_WORDS: frozenset[str] = frozenset({
    "match", "matches", "game", "games", "tournament", "playoffs", "score", "scores",
    "scoreboard", "goalie", "quarterback", "striker", "midfielder", "defender", "pitcher",
    "skater", "hltv", "liquipedia", "esports", "csgo", "cs2", "qualifying", "pitstop",
    "flight", "flights", "airport", "airports", "airfare", "layover", "layovers",
    "stopover", "nonstop", "roundtrip", "oneway", "boarding", "baggage", "luggage",
    "itinerary", "itineraries", "callsign",
})

_EXPLICIT_MARKET_PHRASES: Tuple[str, ...] = (
    "stock market", "global markets", "market overview", "market pulse",
    "how are the markets", "how is the market", "markets doing", "market doing",
    "canadian market", "us market", "european market", "asian market",
    "stock price", "share price", "ticker", "s&p 500", "sp500", "s&p/tsx",
    "tsx 60", "dow jones", "nasdaq", "russell 2000", "ftse 100", "nikkei 225",
    "hang seng", "precious metals", "gold price", "silver price", "crude oil",
    "wti crude", "brent crude", "natural gas price", "crypto market",
    "bitcoin price", "ethereum price", "top gainers", "top losers",
    "market movers", "stocks going up", "stocks going down",
)

_MACRO_CALENDAR_VIEW_ALIASES: frozenset[str] = frozenset({
    "calendar", "macro", "rates", "central_bank", "central_banks", "fomc", "boc", "economic_calendar",
})

_MACRO_CALENDAR_INTENT_RE = re.compile(
    r"\b(?:"
    r"federal\s+reserve|fomc|the\s+fed\b|bank\s+of\s+canada|\bboc\b|"
    r"european\s+central\s+bank|\becb\b|bank\s+of\s+england|\bboe\b|bank\s+of\s+japan|\bboj\b|"
    r"reserve\s+bank\s+of\s+(?:australia|new\s+zealand)|\brba\b|\brbnz\b|swiss\s+national\s+bank|\bsnb\b|"
    r"central\s+banks?|jerome\s+powell|chair\s+powell|tiff\s+macklem|christine\s+lagarde|"
    r"fed\s+(?:rate|rates|interest|decision|decisions|meeting|meetings|announcement|announcements|cut|cuts|hike|hikes|hold|pause|pivot|funds|minutes|press\s+conference|chair|policy|schedule|calendar|today)|"
    r"(?:next|today'?s|upcoming|latest|recent|last)\s+fed\b|"
    r"interest\s+rates?\s+(?:decision|decisions|announcement|meeting|cut|cuts|hike|hikes|raise|raised|raising|lower|lowered|hold|held|pause|paused|schedule|calendar|today|change|move|target|outlook)|"
    r"rate\s+(?:decision|decisions|announcement|announcements|cut|cuts|hike|hikes|hold|pause|meeting|meetings|schedule|calendar)|"
    r"(?:raise|raised|raising|cut|cutting|lower|lowered|lowering|hike|hiked|hiking|hold|held|holding|pause|paused|pausing|slash|slashed)\s+(?:the\s+)?(?:interest\s+|policy\s+|overnight\s+|key\s+|benchmark\s+)?rates?\b|"
    r"overnight\s+rate|federal\s+funds\s+rate|fed\s+funds\s+rate|policy\s+rate|key\s+interest\s+rate|"
    r"monetary\s+policy\s+(?:report|decision|meeting|statement|announcement)|dot\s+plot|"
    r"economic\s+calendar|macro\s+calendar|macroeconomic\s+calendar|central\s+bank\s+calendar|"
    r"(?:when\s+is|what\s+time\s+is|next|today'?s|latest)\s+(?:the\s+)?(?:us\s+|canadian\s+|canada\s+)?(?:cpi|inflation|pce|non[-\s]?farm\s+payrolls?|nfp|jobs\s+report|unemployment\s+report|gdp)\b|"
    r"(?:cpi|pce|non[-\s]?farm\s+payrolls?|nfp|jobs\s+report)\s+(?:release|report|print|schedule|time|date|decision|numbers?\s+today)"
    r")\b",
    re.IGNORECASE,
)

_MACRO_HISTORY_SIGNAL_RE = re.compile(
    r"\b(?:federal\s+reserve|fomc|bank\s+of\s+canada|\bboc\b|interest\s+rate\s+decision|rate\s+decision|"
    r"overnight\s+rate|federal\s+funds\s+rate|monetary\s+policy|jerome\s+powell|tiff\s+macklem|"
    r"central\s+bank\s+rate\s+decisions)\b",
    re.IGNORECASE,
)

_MACRO_CALENDAR_FOLLOWUP_RE = re.compile(
    r"\b(?:"
    r"is\s+it\s+today|it'?s\s+today|how\s+don'?t\s+you\s+know|"
    r"what\s+time\s+is\s+(?:it|that|the\s+meeting|the\s+decision|the\s+announcement|the\s+press\s+conference)|"
    r"when\s+is\s+(?:it|that|the\s+next\s+one|the\s+meeting|the\s+decision|the\s+announcement|the\s+press\s+conference)|"
    r"(?:my|your|our)\s+local\s+time|convert\s+(?:it|that|the\s+time|the\s+meeting)|"
    r"in\s+(?:my|your|our)\s+(?:local\s+)?time(?:\s*zone)?|in\s+(?:mdt|mst|mt|edt|est|et|pdt|pst|pt)\b|"
    r"did\s+they\s+(?:hike|cut|raise|lower|hold|pause|decide)|what\s+did\s+they\s+decide|"
    r"how\s+much\s+did\s+they\s+(?:hike|cut|raise|lower)|"
    r"what\s+about\s+(?:the\s+)?(?:fed|fomc|boc|bank\s+of\s+canada|ecb|boe|boj|rba|rbnz)"
    r")\b",
    re.IGNORECASE,
)

_FOLLOWUP_TIMEFRAME_RE = re.compile(
    r"\b(?:what\s+about|how\s+about|and\s+(?:over|in|for|since)|how\s+did\s+(?:it|they)\s+(?:do|perform)|"
    r"compare\s+(?:it|them)\s+(?:with|to|vs|versus|against)|"
    r"(?:over|in|during|for)\s+(?:the\s+)?(?:past|last)?\s*(?:\d+\s*(?:days?|weeks?|months?|years?)|ytd|20\d{2}|all[\s\-]?time))\b",
    re.IGNORECASE,
)

_CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Z]{1,5}(?:\.TO)?)\b")


_BARE_FOLLOWUP_START_RE = re.compile(
    r"^\s*(?:what\s+about|how\s+about|and\s+(?:over|in|for|since|what|how|ytd)|what\s+if)\b",
    re.IGNORECASE,
)


def _resolve_cid(channel_id: Optional[int] = None) -> int:
    return channel_id if channel_id is not None else (get_active_channel_id() or 0)


def get_channel_market_state(channel_id: Optional[int] = None) -> Optional[MarketChannelState]:
    """Return the active channel's MarketChannelState, if any."""
    return _CHANNEL_MARKET_STATE.get(_resolve_cid(channel_id))


def update_channel_market_state(
    tickers: Optional[List[str]] = None,
    timeframe: Optional[str] = None,
    region: Optional[str] = None,
    view: Optional[str] = None,
    channel_id: Optional[int] = None,
) -> MarketChannelState:
    """Record the latest queried ticker(s), timeframe, region, and view for the active channel."""
    cid = _resolve_cid(channel_id)
    st = _CHANNEL_MARKET_STATE.get(cid)
    if st is None:
        st = MarketChannelState()
        _CHANNEL_MARKET_STATE[cid] = st
    if tickers:
        st.last_tickers = [t.strip().upper() for t in tickers if t and t.strip()]
    if timeframe:
        st.last_timeframe = timeframe
    if region:
        st.last_region = region
    if view:
        st.last_view = view
    return st


def clear_channel_market_state(channel_id: Optional[int] = None) -> None:
    """Clear per-channel market state (all channels when `channel_id` is None)."""
    if channel_id is None:
        _CHANNEL_MARKET_STATE.clear()
        return
    _CHANNEL_MARKET_STATE.pop(_resolve_cid(channel_id), None)


def is_macro_calendar_query(query: str, view: str = "") -> bool:
    """Return True when the query or view requests Central Bank Rate Decisions or Economic Calendar data."""
    v_low = (view or "").strip().lower()
    if v_low in _MACRO_CALENDAR_VIEW_ALIASES:
        return True
    q_clean = (query or "").strip()
    if not q_clean:
        return False
    q_low = q_clean.lower()
    if contains_domain_terms(q_low, _NON_MARKET_SPORT_FLIGHT_WORDS):
        return False
    return bool(_MACRO_CALENDAR_INTENT_RE.search(q_low))


def _extract_recent_macro_subject_from_history(history: list) -> str:
    """Check the last few messages in history for active Central Bank / FOMC / BoC context."""
    seen_user = 0
    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = str((msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")) or "")
        if role == "user":
            seen_user += 1
            if seen_user == 1:
                continue
        if seen_user > 4:
            break
        if _MACRO_HISTORY_SIGNAL_RE.search(content):
            if re.search(r"\b(?:federal\s+reserve|fomc|powell|\bfed\b)\b", content, re.IGNORECASE):
                return "Federal Reserve FOMC interest rate decision"
            if re.search(r"\b(?:bank\s+of\s+canada|\bboc\b|macklem|overnight\s+rate)\b", content, re.IGNORECASE):
                return "Bank of Canada BoC interest rate decision"
            return "central bank interest rate decision schedule"
    return ""


def detect_macro_calendar_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """Detect when a prompt (or follow-up in an active macro-calendar conversation) is asking about
    central bank rate decisions (Fed/FOMC, Bank of Canada/BoC, ECB, BoE, BoJ) or major economic calendar releases."""
    if not history:
        return None

    latest_user_text = ""
    for msg in reversed(history):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"\[Spoken in Discord Voice Channel[^\]]*\]\s*", "", latest_user_text).strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            latest_user_text = re.sub(r"^(?:User\s+)?[^:\n]{1,80}(?:\bsays\b|\(User ID:\s*\d+\))\s*:\s*", "", latest_user_text, flags=re.IGNORECASE).strip()
            break
    if not latest_user_text:
        return None

    q_low = latest_user_text.lower()
    if q_low.startswith("!"):
        return None
    if contains_domain_terms(q_low, _NON_MARKET_SPORT_FLIGHT_WORDS):
        return None
    # Do not intercept explicit graphing or scheduled-job creation commands
    if re.search(r"\b(?:graph|plot|chart|heatmap|monte\s+carlo|diagram|remind\s+me|set\s+a\s+reminder|every\s+\d+\s*[mhd]|every\s+(?:morning|day|monday|weekday))\b", q_low):
        return None

    if is_macro_calendar_query(latest_user_text):
        return ("skill_markets_info", {"query": latest_user_text, "view": "calendar"})

    state = get_channel_market_state(active_channel_id)
    prior_macro_subj = _extract_recent_macro_subject_from_history(history)
    has_macro_ctx = bool((state and state.last_view == "calendar") or prior_macro_subj)
    if has_macro_ctx and _MACRO_CALENDAR_FOLLOWUP_RE.search(q_low):
        ctx_tag = prior_macro_subj or "central bank interest rate decision"
        enriched_q = f"{latest_user_text} ({ctx_tag})" if ctx_tag.lower() not in q_low else latest_user_text
        return ("skill_markets_info", {"query": enriched_q, "view": "calendar"})

    return None


def enrich_markets_tool_args(
    args: dict,
    history: list,
    active_channel_id: Optional[int] = None,
) -> dict:
    """Ensure `skill_markets_info` preserves the user's latest prompt (timeframes, comparison cues,
    custom date ranges, central bank calendar intent) and resolves follow-up pronoun references ('it',
    'compare it with X') using the channel's last queried ticker."""
    enriched = dict(args or {})
    latest_user_text = ""
    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"\[Spoken in Discord Voice Channel[^\]]*\]\s*", "", latest_user_text).strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            latest_user_text = re.sub(r"^(?:User\s+)?[^:\n]{1,80}(?:\bsays\b|\(User ID:\s*\d+\))\s*:\s*", "", latest_user_text, flags=re.IGNORECASE).strip()
            break

    if latest_user_text:
        cur_q = str(enriched.get("query") or "").strip()
        if not cur_q:
            enriched["query"] = latest_user_text
        elif latest_user_text.lower() not in cur_q.lower():
            enriched["query"] = f"{latest_user_text} ({cur_q})"

    v_cur = str(enriched.get("view") or "").strip().lower()
    q_cur = str(enriched.get("query") or latest_user_text).strip()
    if is_macro_calendar_query(q_cur, v_cur):
        enriched["view"] = "calendar"
        return enriched

    macro_followup = detect_macro_calendar_tool_call(history, active_channel_id=active_channel_id)
    if macro_followup and not enriched.get("ticker"):
        enriched["view"] = "calendar"
        enriched["query"] = macro_followup[1].get("query", q_cur)
        return enriched

    state = get_channel_market_state(active_channel_id)
    if state and state.last_tickers:
        t_val = str(enriched.get("ticker") or "").strip()
        q_val = str(enriched.get("query") or latest_user_text).strip()
        if t_val.lower() in ("it", "its", "them", "they", "same"):
            enriched["ticker"] = ", ".join(state.last_tickers)
        elif re.search(r"\b(?:it|its|them|they)\b", q_val, re.IGNORECASE):
            # If user says "compare it with NVDA", replace pronoun with the channel's primary ticker
            if re.search(r"\b(?:vs\.?|versus|against|compare|with)\b", q_val, re.IGNORECASE):
                enriched["query"] = re.sub(
                    r"\b(?:it|them)\b",
                    state.last_tickers[0],
                    q_val,
                    flags=re.IGNORECASE,
                )
                if t_val and state.last_tickers[0].lower() not in t_val.lower():
                    enriched["ticker"] = f"{state.last_tickers[0]}, {t_val}"
                enriched.setdefault("view", "compare")
            elif not t_val:
                enriched["ticker"] = state.last_tickers[0]
        elif not t_val and _BARE_FOLLOWUP_START_RE.search(q_val) and _FOLLOWUP_TIMEFRAME_RE.search(q_val):
            enriched["ticker"] = ", ".join(state.last_tickers) if len(state.last_tickers) > 1 else state.last_tickers[0]

    return enriched


def detect_missed_markets_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """Detect when a 4B/8B model forgets to call `skill_markets_info` on turn 0 for an explicit
    stock, index, commodity, crypto, central bank rate decision, or follow-up market query."""
    if not history:
        return None

    latest_user_text = ""
    for msg in reversed(history):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"\[Spoken in Discord Voice Channel[^\]]*\]\s*", "", latest_user_text).strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            latest_user_text = re.sub(r"^(?:User\s+)?[^:\n]{1,80}(?:\bsays\b|\(User ID:\s*\d+\))\s*:\s*", "", latest_user_text, flags=re.IGNORECASE).strip()
            break
    if not latest_user_text:
        return None

    q_low = latest_user_text.lower()
    if q_low.startswith("!"):
        return None

    # Avoid intercepting sports or aviation queries
    if contains_domain_terms(q_low, _NON_MARKET_SPORT_FLIGHT_WORDS):
        return None

    # 1. Central Bank Rate Decisions & Economic Calendar (or follow-up in active macro context)
    macro_call = detect_macro_calendar_tool_call(history, active_channel_id=active_channel_id)
    if macro_call:
        return macro_call

    # 2. Cashtag like $NVDA, $SHOP.TO, $BTC
    cashtags = _CASHTAG_RE.findall(latest_user_text)
    if cashtags:
        return ("skill_markets_info", {"query": latest_user_text, "ticker": ", ".join(cashtags)})

    # 3. Explicit market phrases
    if any(p in q_low for p in _EXPLICIT_MARKET_PHRASES):
        return ("skill_markets_info", {"query": latest_user_text})

    # 4. Follow-up refinement in a channel with active market context
    state = get_channel_market_state(active_channel_id)
    had_prior_markets_tool = any(
        (m.get("name") if isinstance(m, dict) else getattr(m, "name", None)) == "skill_markets_info"
        for m in history
    )
    if (state and state.last_tickers or had_prior_markets_tool) and _FOLLOWUP_TIMEFRAME_RE.search(q_low):
        args: dict = {"query": latest_user_text}
        is_referential = bool(
            re.search(r"\b(?:it|its|them|they)\b", q_low) or _BARE_FOLLOWUP_START_RE.search(q_low)
        )
        if (
            state
            and state.last_tickers
            and is_referential
            and not re.search(r"\b(?:vs\.?|versus|compare)\b", q_low)
        ):
            args["ticker"] = state.last_tickers[0]
        return ("skill_markets_info", args)

    return None


