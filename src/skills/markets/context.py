"""
Per-channel market state tracking, tool argument enrichment, and turn-0 missed-tool-call recovery.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from src.context import get_active_channel_id
from src.skills.markets.models import MarketChannelState
from src.utils.domain_vocab import contains_domain_terms

_CHANNEL_MARKET_STATE: Dict[int, MarketChannelState] = {}

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
    "canadian market", "us market", "european market", "asian market",
    "stock price", "share price", "ticker", "s&p 500", "sp500", "s&p/tsx",
    "tsx 60", "dow jones", "nasdaq", "russell 2000", "ftse 100", "nikkei 225",
    "hang seng", "precious metals", "gold price", "silver price", "crude oil",
    "wti crude", "brent crude", "natural gas price", "crypto market",
    "bitcoin price", "ethereum price", "top gainers", "top losers",
    "market movers", "stocks going up", "stocks going down",
)

_FOLLOWUP_TIMEFRAME_RE = re.compile(
    r"\b(?:what\s+about|how\s+about|and\s+(?:over|in|for|since)|how\s+did\s+(?:it|they)\s+(?:do|perform)|"
    r"compare\s+(?:it|them)\s+(?:with|to|vs|versus|against)|"
    r"(?:over|in|during|for)\s+(?:the\s+)?(?:past|last)?\s*(?:\d+\s*(?:days?|weeks?|months?|years?)|ytd|20\d{2}|all[\s\-]?time))\b",
    re.IGNORECASE,
)

_CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Z]{1,5}(?:\.TO)?)\b")


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


def enrich_markets_tool_args(
    args: dict,
    history: list,
    active_channel_id: Optional[int] = None,
) -> dict:
    """Ensure `skill_markets_info` preserves the user's latest prompt (timeframes, comparison cues,
    custom date ranges) and resolves follow-up pronoun references ('it', 'compare it with X')
    using the channel's last queried ticker."""
    enriched = dict(args or {})
    latest_user_text = ""
    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            break

    if latest_user_text:
        cur_q = str(enriched.get("query") or "").strip()
        if not cur_q:
            enriched["query"] = latest_user_text
        elif latest_user_text.lower() not in cur_q.lower():
            enriched["query"] = f"{latest_user_text} ({cur_q})"

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
        elif not t_val and _FOLLOWUP_TIMEFRAME_RE.search(q_val):
            enriched["ticker"] = ", ".join(state.last_tickers) if len(state.last_tickers) > 1 else state.last_tickers[0]

    return enriched


def detect_missed_markets_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """Detect when a 4B/8B model forgets to call `skill_markets_info` on turn 0 for an explicit
    stock, index, commodity, crypto, or follow-up market timeframe query."""
    if not history:
        return None

    latest_user_text = ""
    for msg in reversed(history):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            break
    if not latest_user_text:
        return None

    q_low = latest_user_text.lower()
    if q_low.startswith("!"):
        return None

    # Avoid intercepting sports or aviation queries
    if contains_domain_terms(q_low, _NON_MARKET_SPORT_FLIGHT_WORDS):
        return None

    # 1. Cashtag like $NVDA, $SHOP.TO, $BTC
    cashtags = _CASHTAG_RE.findall(latest_user_text)
    if cashtags:
        return ("skill_markets_info", {"query": latest_user_text, "ticker": ", ".join(cashtags)})

    # 2. Explicit market phrases
    if any(p in q_low for p in _EXPLICIT_MARKET_PHRASES):
        return ("skill_markets_info", {"query": latest_user_text})

    # 3. Follow-up refinement in a channel with active market context
    state = get_channel_market_state(active_channel_id)
    had_prior_markets_tool = any(
        (m.get("name") if isinstance(m, dict) else getattr(m, "name", None)) == "skill_markets_info"
        for m in history
    )
    if (state and state.last_tickers or had_prior_markets_tool) and _FOLLOWUP_TIMEFRAME_RE.search(q_low):
        args: dict = {"query": latest_user_text}
        if state and state.last_tickers and not re.search(r"\b(?:vs\.?|versus|compare)\b", q_low):
            args["ticker"] = state.last_tickers[0]
        return ("skill_markets_info", args)

    return None

