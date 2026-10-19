"""
Per-channel visualization state tracking, tool argument enrichment, and selective on-demand turn-0 recovery (`src/skills/graphing/context.py`).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from src.context import get_active_channel_id
from src.skills.graphing.catalogs import CHART_TICKER_ALIASES
from src.skills.graphing.enums import ChartType, DiagramType
from src.skills.graphing.models import ChartChannelState, ChartSpec
from src.utils.cache import BoundedCache
from src.utils.domain_vocab import MUSIC_DOMAIN_WORDS, contains_domain_terms

_CHANNEL_CHART_STATE: Dict[int, ChartChannelState] = BoundedCache(maxsize=128)

_EXPLICIT_DIAGRAM_RE = re.compile(
    r"\b(?:draw|create|make|generate|render|show)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?"
    r"(?:flowchart|sequence\s+diagram|architecture\s+diagram|state\s+diagram|class\s+diagram|er\s+diagram|mermaid\s+diagram|diagram)\b",
    re.IGNORECASE,
)

_EXPLICIT_MARKET_PLOT_RE = re.compile(
    r"\b(?:plot|graph|visualize|draw\s+a\s+chart|chart\s+the\s+price|price\s+chart|candlestick\s+chart)\b",
    re.IGNORECASE,
)


def _resolve_cid(channel_id: Optional[int] = None) -> int:
    return channel_id if channel_id is not None else (get_active_channel_id() or 0)


def get_channel_chart_state(channel_id: Optional[int] = None) -> Optional[ChartChannelState]:
    return _CHANNEL_CHART_STATE.get(_resolve_cid(channel_id))


def update_channel_chart_state(
    chart_type: str = "",
    title: str = "",
    tickers: Optional[List[str]] = None,
    timeframe: Optional[str] = None,
    spec: Optional[ChartSpec] = None,
    diagram_code: Optional[str] = None,
    channel_id: Optional[int] = None,
) -> ChartChannelState:
    cid = _resolve_cid(channel_id)
    st = _CHANNEL_CHART_STATE.get(cid)
    if st is None:
        st = ChartChannelState()
        _CHANNEL_CHART_STATE[cid] = st
    if chart_type:
        st.last_chart_type = chart_type
    if title:
        st.last_title = title
    if tickers is not None:
        st.last_tickers = list(tickers)
    if timeframe:
        st.last_timeframe = timeframe
    if spec is not None:
        st.last_spec = spec
    if diagram_code is not None:
        st.last_diagram_code = diagram_code
    return st


def clear_channel_chart_state(channel_id: Optional[int] = None) -> None:
    if channel_id is None:
        _CHANNEL_CHART_STATE.clear()
        return
    _CHANNEL_CHART_STATE.pop(_resolve_cid(channel_id), None)


_TIMEFRAME_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:5\s*d(?:ays?)?|1\s*w(?:eek)?|past\s+week)\b", re.IGNORECASE), "5d"),
    (re.compile(r"\b(?:1\s*mo(?:nth)?|past\s+month|30\s*days?)\b", re.IGNORECASE), "1mo"),
    (re.compile(r"\b(?:3\s*mo(?:nths?)?|past\s+3\s+months?|quarter)\b", re.IGNORECASE), "3mo"),
    (re.compile(r"\b(?:6\s*mo(?:nths?)?|past\s+6\s+months?|half\s+year)\b", re.IGNORECASE), "6mo"),
    (re.compile(r"\b(?:ytd|year\s+to\s+date)\b", re.IGNORECASE), "ytd"),
    (re.compile(r"\b(?:1\s*y(?:ear|r)?|past\s+year|12\s*months?)\b", re.IGNORECASE), "1y"),
    (re.compile(r"\b(?:5\s*y(?:ears?|rs?)?|past\s+5\s+years?)\b", re.IGNORECASE), "5y"),
    (re.compile(r"\b(?:10\s*y(?:ears?|rs?)?|past\s+10\s+years?|decade)\b", re.IGNORECASE), "10y"),
    (re.compile(r"\b(?:all\s+time|max\s+history)\b", re.IGNORECASE), "max"),
)


def _extract_timeframe_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    for pat, tf_code in _TIMEFRAME_PATTERNS:
        if pat.search(text):
            return tf_code
    return None


def _extract_tickers_from_text(text: str) -> List[str]:
    if not text:
        return []
    q_low = text.lower()
    found: List[str] = []
    for alias in sorted(CHART_TICKER_ALIASES.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", q_low):
            sym = CHART_TICKER_ALIASES[alias]
            if sym not in found:
                found.append(sym)
    for m in re.finditer(r"\b([A-Z]{2,5}(?:\.[A-Z]{1,2})?)\b", text):
        tok = m.group(1)
        if tok not in ("USD", "CAD", "EUR", "GBP", "YTD", "PNG", "API", "LLM", "CPU", "GPU"):
            mapped = CHART_TICKER_ALIASES.get(tok.lower(), tok)
            if mapped not in found:
                found.append(mapped)
    return found


def enrich_graphing_tool_args(
    args: dict,
    history: list,
    active_channel_id: Optional[int] = None,
) -> dict:
    """Preserve the user's latest prompt and resolve follow-up graphing adjustments using channel state."""
    enriched = dict(args or {})
    latest_user_text = ""
    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = str(content or "").strip()
            latest_user_text = re.sub(r"^\[[^\]]+\]:\s*", "", latest_user_text).strip()
            break

    if latest_user_text and not enriched.get("query"):
        enriched["query"] = latest_user_text

    if latest_user_text and not enriched.get("timeframe"):
        inferred_tf = _extract_timeframe_from_text(latest_user_text)
        if inferred_tf:
            enriched["timeframe"] = inferred_tf

    if latest_user_text and not enriched.get("ticker") and not enriched.get("data") and not enriched.get("diagram"):
        extracted_tickers = _extract_tickers_from_text(latest_user_text)
        if extracted_tickers:
            enriched["ticker"] = ", ".join(extracted_tickers)

    state = get_channel_chart_state(active_channel_id)
    if state and state.last_tickers and not enriched.get("ticker") and not enriched.get("data"):
        q_low = (latest_user_text or "").lower()
        if re.search(r"\b(?:it|them|same|instead)\b", q_low):
            enriched["ticker"] = ", ".join(state.last_tickers)

    return enriched


def detect_missed_graphing_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """
    Strictly on-demand turn-0 recovery when the user explicitly requests a diagram or a market price plot
    and a small model forgets to call `skill_graphing_info`. Never triggers on music charts ('Beatport chart')
    or general data queries that require research first.
    """
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
    if not latest_user_text or latest_user_text.startswith("!"):
        return None

    q_low = latest_user_text.lower()
    # Never intercept music chart queries ("Beatport Top 100 chart", "Billboard chart")
    if contains_domain_terms(q_low, MUSIC_DOMAIN_WORDS):
        return None

    # 1. Explicit diagram request containing arrow notation or Mermaid block
    if _EXPLICIT_DIAGRAM_RE.search(q_low) and ("->" in latest_user_text or "```" in latest_user_text):
        diag_type = DiagramType.FLOWCHART if "flowchart" in q_low else ChartType.DIAGRAM
        return (
            "skill_graphing_info",
            {
                "chart_type": str(diag_type),
                "query": latest_user_text,
                "diagram": latest_user_text,
            },
        )

    # 2. Explicit market plot request ("plot NVDA vs SHOP.TO over 5 years")
    if _EXPLICIT_MARKET_PLOT_RE.search(q_low):
        tickers = _extract_tickers_from_text(latest_user_text)
        if tickers:
            raw_args = enrich_graphing_tool_args(
                {"chart_type": str(ChartType.MARKET), "query": latest_user_text},
                history,
                active_channel_id=active_channel_id,
            )
            return ("skill_graphing_info", raw_args)

    return None
