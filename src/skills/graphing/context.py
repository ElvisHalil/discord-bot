"""
Per-channel visualization state tracking, tool argument enrichment, and selective on-demand turn-0 recovery (`src/skills/graphing/context.py`).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from src.context import get_active_channel_id
from src.skills.graphing.catalogs import (
    CHART_TICKER_ALIASES,
    CURRENCY_NAME_TO_ISO,
    extract_fx_pairs_from_text,
)
from src.skills.graphing.enums import ChartType, DiagramType
from src.skills.graphing.models import ChartChannelState, ChartSpec
from src.utils.cache import BoundedCache
from src.utils.domain_vocab import MUSIC_DOMAIN_WORDS, contains_domain_terms

_CHANNEL_CHART_STATE: Dict[int, ChartChannelState] = BoundedCache(maxsize=128)

_EXPLICIT_DIAGRAM_RE = re.compile(
    r"(?:\b(?:draw|create|make|generate|render|show|build|give|zeig\w*|mach\w*|erstell\w*)\b.*"
    r"\b(?:flow\s*chart|flowchart|sequence\s+diagram|architecture\s+diagram|state\s+diagram|class\s+diagram|er\s+diagram|mermaid\s+diagram|diagram|flussdiagramm|ablaufdiagramm)\b"
    r"|\b(?:example|sample|demo|beispiel)\s+(?:flow\s*chart|flowchart|sequence\s+diagram|architecture\s+diagram|mermaid\s+diagram|diagram)\b"
    r"|\b(?:flow\s*chart|flowchart|sequence\s+diagram|architecture\s+diagram|mermaid\s+diagram)\b.*\b(?:draw|create|make|generate|render|show|machen|erstellen|zeigen)\b)",
    re.IGNORECASE,
)

_EXPLICIT_MARKET_PLOT_RE = re.compile(
    r"\b(?:plot|graph|visualize|overlay|chart\s+out|chart\s+of|draw\s+a\s+chart|graph\s+a\s+chart|"
    r"chart\s+the\s+price|price\s+chart|candlestick\s+chart|in\s+a\s+chart|on\s+a\s+chart|chart|charts)\b",
    re.IGNORECASE,
)

_NON_MARKET_CHART_RE = re.compile(
    r"\b(?:depth\s+chart|seating\s+chart|size\s+chart|sizing\s+chart|natal\s+chart|birth\s+chart|"
    r"star\s+chart|org\s+chart|organization\s+chart|chore\s+chart|eye\s+chart|color\s+chart|gantt\s+chart)\b",
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


_EXPLICIT_MONTE_CARLO_RE = re.compile(
    r"\b(?:monte\s*carlo|gbm\s+simulation|stochastic\s+simulation|price\s+simulation)\b",
    re.IGNORECASE,
)

_TIMEFRAME_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:intraday|1\s*d(?:ay)?|today)\b", re.IGNORECASE), "1d"),
    (re.compile(r"\b(?:24\s*h(?:ours?|rs?)?)\b", re.IGNORECASE), "24h"),
    (re.compile(r"\b(?:48\s*h(?:ours?|rs?)?|2\s*d(?:ays?)?|(?:past|last)\s+2\s+days?)\b", re.IGNORECASE), "2d"),
    (re.compile(r"\b(?:72\s*h(?:ours?|rs?)?|3\s*d(?:ays?)?|(?:past|last)\s+3\s+days?|three\s+days?)\b", re.IGNORECASE), "3d"),
    (re.compile(r"\b(?:4\s*d(?:ays?)?|(?:past|last)\s+4\s+days?)\b", re.IGNORECASE), "4d"),
    (re.compile(r"\b(?:5\s*d(?:ays?)?|(?:past|last)\s+5\s+days?|five\s+days?)\b", re.IGNORECASE), "5d"),
    (re.compile(r"\b(?:7\s*d(?:ays?)?|1\s*w(?:eek|k)?|(?:past|last)\s+week|(?:past|last)\s+7\s+days?|seven\s+days?)\b", re.IGNORECASE), "7d"),
    (re.compile(r"\b(?:10\s*d(?:ays?)?|(?:past|last)\s+10\s+days?)\b", re.IGNORECASE), "10d"),
    (re.compile(r"\b(?:14\s*d(?:ays?)?|2\s*w(?:eeks?|ks?)?|(?:past|last)\s+2\s+weeks?)\b", re.IGNORECASE), "14d"),
    (re.compile(r"\b(?:21\s*d(?:ays?)?|3\s*w(?:eeks?|ks?)?|(?:past|last)\s+3\s+weeks?)\b", re.IGNORECASE), "21d"),
    (re.compile(r"\b(?:1\s*mo(?:nth)?|(?:past|last)\s+(?:1\s+)?month|one\s+month|30\s*days?)\b", re.IGNORECASE), "1mo"),
    (re.compile(r"\b(?:2\s*mo(?:nths?)?|(?:past|last)\s+2\s+months?|two\s+months?|60\s*days?)\b", re.IGNORECASE), "2mo"),
    (re.compile(r"\b(?:3\s*mo(?:nths?)?|(?:past|last)\s+3\s+months?|three\s+months?|90\s*days?|quarter)\b", re.IGNORECASE), "3mo"),
    (re.compile(r"\b(?:6\s*mo(?:nths?)?|(?:past|last)\s+6\s+months?|six\s+months?|half\s+year)\b", re.IGNORECASE), "6mo"),
    (re.compile(r"\b(?:ytd|year\s+to\s+date)\b", re.IGNORECASE), "ytd"),
    (re.compile(r"\b(?:1\s*y(?:ear|r)?|(?:past|last|next)\s+(?:1\s+)?year|one\s+year|12\s*months?)\b", re.IGNORECASE), "1y"),
    (re.compile(r"\b(?:2\s*y(?:ears?|rs?)?|(?:past|last|next)\s+2\s+years?|two\s+years?)\b", re.IGNORECASE), "2y"),
    (re.compile(r"\b(?:3\s*y(?:ears?|rs?)?|(?:past|last|next)\s+3\s+years?|three\s+years?)\b", re.IGNORECASE), "3y"),
    (re.compile(r"\b(?:5\s*y(?:ears?|rs?)?|(?:past|last|next)\s+5\s+years?|five\s+years?)\b", re.IGNORECASE), "5y"),
    (re.compile(r"\b(?:10\s*y(?:ears?|rs?)?|(?:past|last|next)\s+10\s+years?|decade)\b", re.IGNORECASE), "10y"),
    (re.compile(r"\b(?:all\s+time|max\s+history)\b", re.IGNORECASE), "max"),
)

_INTERVAL_RE = re.compile(
    r"\b(hourly|by\s+h(?:our|r)|per\s+h(?:our|r)|1\s*h(?:r|our)\b|60\s*m(?:in)?|15\s*m(?:in)?|5\s*m(?:in)?|30\s*m(?:in)?|daily|weekly)\b",
    re.IGNORECASE,
)


def _strip_user_prefix(text: str) -> str:
    s = str(text or "").strip()
    s = re.sub(r"\[Spoken in Discord Voice Channel[^\]]*\]\s*", "", s).strip()
    s = re.sub(r"^\[[^\]]+\]:\s*", "", s).strip()
    s = re.sub(
        r"^(?:User\s+)?[^:\n]{1,80}(?:\bsays\b|\(User ID:\s*\d+\))\s*:\s*",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()
    return s


def _extract_timeframe_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    for pat, tf_code in _TIMEFRAME_PATTERNS:
        if pat.search(text):
            return tf_code
    # Generic fallback for arbitrary N days / weeks / months / years (e.g. "last 8 days", "4 years")
    m_d = re.search(r"\b(\d{1,3})\s*d(?:ays?)\b", text, re.IGNORECASE)
    if m_d:
        return f"{int(m_d.group(1))}d"
    m_w = re.search(r"\b(\d{1,2})\s*w(?:eeks?|ks?)\b", text, re.IGNORECASE)
    if m_w:
        return f"{int(m_w.group(1))}w"
    m_m = re.search(r"\b(\d{1,2})\s*mo(?:nths?)?\b", text, re.IGNORECASE)
    if m_m:
        return f"{int(m_m.group(1))}mo"
    m_y = re.search(r"\b(\d{1,2})\s*y(?:ears?|rs?)\b", text, re.IGNORECASE)
    if m_y:
        return f"{int(m_y.group(1))}y"
    return None


def _extract_interval_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = _INTERVAL_RE.search(text)
    if not m:
        return None
    tok = m.group(1).lower()
    if "h" in tok or "60" in tok:
        return "1h"
    if "15" in tok:
        return "15m"
    if "30" in tok:
        return "30m"
    if "5" in tok:
        return "5m"
    if "week" in tok:
        return "1wk"
    if "dai" in tok:
        return "1d"
    return None


def _extract_tickers_from_text(text: str) -> List[str]:
    if not text:
        return []
    cleaned_text = _strip_user_prefix(text)
    found: List[str] = []
    # 1. Extract explicit FX / Currency-vs-BTC / SATS pairs first (e.g. "CAD/EURO", "CAD/BTC", "$1 in BTC", "SATS/CAD")
    fx_pairs = extract_fx_pairs_from_text(cleaned_text)
    for fx_sym in fx_pairs:
        if fx_sym not in found:
            found.append(fx_sym)
    has_btc_fx_pair = any(s.endswith("-BTC") or s.startswith(("BTC-", "SATS-")) for s in fx_pairs)
    all_ccy_codes = set(CURRENCY_NAME_TO_ISO.values()) | {"EURO", "SATS", "SAT"}

    q_low = cleaned_text.lower()
    # Scrub matched FX pair substrings before scanning general aliases so "euro" inside "cad/euro"
    # doesn't also trigger a second standalone EURUSD=X ticker.
    q_low_scrubbed = re.sub(
        r"\b[a-z]{3,10}\s*(?:/|-|\bto\b|\bin\b|\bper\b)\s*[a-z]{3,10}\b",
        " ",
        q_low,
    )
    for alias in sorted(CHART_TICKER_ALIASES.keys(), key=len, reverse=True):
        if has_btc_fx_pair and alias in ("btc", "bitcoin"):
            continue
        if re.search(rf"\b{re.escape(alias)}\b", q_low_scrubbed):
            sym = CHART_TICKER_ALIASES[alias]
            if sym not in found:
                found.append(sym)
    for m in re.finditer(r"\b([A-Z]{2,5}(?:\.[A-Z]{1,2})?)\b", cleaned_text):
        tok = m.group(1)
        if has_btc_fx_pair and tok == "BTC":
            continue
        if tok not in all_ccy_codes and tok not in ("YTD", "PNG", "API", "LLM", "CPU", "GPU", "MAX", "GBM"):
            mapped = CHART_TICKER_ALIASES.get(tok.lower(), tok)
            if mapped not in found:
                found.append(mapped)
    return found


def _extract_prior_history_context(history: list) -> Tuple[List[str], Optional[str]]:
    """Scan up to 3 prior user messages in `history` (excluding the latest user message) for tickers and timeframe."""
    if not history:
        return [], None
    seen_latest_user = False
    checked = 0
    for msg in reversed(history):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role != "user":
            continue
        if not seen_latest_user:
            seen_latest_user = True
            continue
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        text = _strip_user_prefix(str(content or ""))
        if not text:
            continue
        checked += 1
        tickers = _extract_tickers_from_text(text)
        if tickers:
            return tickers, _extract_timeframe_from_text(text)
        if checked >= 3:
            break
    return [], None


def enrich_graphing_tool_args(
    args: dict,
    history: list,
    active_channel_id: Optional[int] = None,
    has_sibling_graph_calls: bool = False,
) -> dict:
    """Preserve the user's latest prompt and resolve follow-up graphing adjustments using channel state."""
    enriched = dict(args or {})
    latest_user_text = ""
    for msg in reversed(history or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = _strip_user_prefix(str(content or ""))
            break

    if latest_user_text and not has_sibling_graph_calls:
        cur_q = str(enriched.get("query") or "").strip()
        if not cur_q or latest_user_text.lower() not in cur_q.lower():
            enriched["query"] = latest_user_text

    state = get_channel_chart_state(active_channel_id)
    prior_tickers, prior_tf = _extract_prior_history_context(history or [])
    fallback_tf = (state.last_timeframe if state and state.last_timeframe else None) or prior_tf

    if latest_user_text:
        inferred_tf = _extract_timeframe_from_text(latest_user_text)
        inferred_int = _extract_interval_from_text(latest_user_text)
        # Always prioritize the explicit timeframe in the user's latest message over stale LLM timeframe args
        if inferred_tf:
            enriched["timeframe"] = f"{inferred_tf} {inferred_int}".strip() if inferred_int else inferred_tf
        elif inferred_int:
            base_tf = str(enriched.get("timeframe") or fallback_tf or "7d").strip()
            base_tf_clean = re.sub(r"\b(?:1h|15m|5m|30m|1d|1wk)\b", "", base_tf).strip() or "7d"
            enriched["timeframe"] = f"{base_tf_clean} {inferred_int}".strip()
        elif not enriched.get("timeframe") and fallback_tf:
            enriched["timeframe"] = fallback_tf

    _mc_followup_re = re.compile(
        r"\b(?:base(?:d)?\s+(?:it\s+)?(?:on|off|of)|calibrated\s+on|use(?:d|ing)?\s+(?:the\s+)?(?:last|past|\d+|all)|"
        r"(?:into|in)\s+(?:the\s+)?future|do\s+it\s+again|all[- ]time\s+history|\d+\s*(?:y(?:ears?|rs?)?|mo(?:nths?)?)\s+of\s+history|"
        r"\d[\d,]*(?:\.\d+)?\s*k?\s*[- ]?(?:paths?|simulations?|sims?|runs?|trials?)|(?:with|use|try|do|run)\s+\d[\d,]*\s*k)\b",
        re.IGNORECASE,
    )
    if latest_user_text and (
        _EXPLICIT_MONTE_CARLO_RE.search(latest_user_text)
        or (state and state.last_chart_type == ChartType.MONTE_CARLO and _mc_followup_re.search(latest_user_text))
    ):
        enriched["chart_type"] = str(ChartType.MONTE_CARLO)

    ct_low = str(enriched.get("chart_type") or "").strip().lower()
    is_market_compatible = ct_low in (
        "", "market", "stock", "stocks", "crypto", "price", "candlestick", "ohlc", "line", "compare", "comparison",
        "monte_carlo", "montecarlo", "monte carlo", "gbm", "simulation", "price_simulation",
    )

    if latest_user_text and not enriched.get("data") and not enriched.get("diagram") and is_market_compatible:
        extracted_tickers = _extract_tickers_from_text(latest_user_text)
        cur_ticker = str(enriched.get("ticker") or "").strip()
        if not cur_ticker and extracted_tickers:
            enriched["ticker"] = ", ".join(extracted_tickers)
        elif (
            cur_ticker
            and len(extracted_tickers) == 1
            and (
                extracted_tickers[0].endswith(("=X", "-BTC"))
                or extracted_tickers[0].startswith("SATS-")
                or (extracted_tickers[0].startswith("BTC-") and extracted_tickers[0] != "BTC-USD")
            )
            and cur_ticker.upper() in ({"BTC", "BTC-USD"} | set(CURRENCY_NAME_TO_ISO.values()))
        ):
            enriched["ticker"] = extracted_tickers[0]
        elif cur_ticker and len(extracted_tickers) >= 2 and not has_sibling_graph_calls:
            merged: List[str] = []
            for raw_t in re.split(r"\s*(?:,|\bvs\.?\b|\bversus\b|\band\b)\s*", cur_ticker, flags=re.IGNORECASE):
                rt = raw_t.strip().lstrip("$")
                if rt:
                    canon_rt = CHART_TICKER_ALIASES.get(rt.lower(), rt.upper())
                    if canon_rt not in merged:
                        merged.append(canon_rt)
            for ext_t in extracted_tickers:
                canon_ext = CHART_TICKER_ALIASES.get(ext_t.lower(), ext_t.upper())
                if canon_ext not in merged:
                    merged.append(canon_ext)
            enriched["ticker"] = ", ".join(merged[:5])
        elif cur_ticker and has_sibling_graph_calls:
            canon_single = CHART_TICKER_ALIASES.get(cur_ticker.lower(), cur_ticker.upper())
            enriched["ticker"] = canon_single
            if not enriched.get("query"):
                enriched["query"] = f"{canon_single} {enriched.get('timeframe', '')}".strip()

    fallback_tickers = (list(state.last_tickers) if state and state.last_tickers else None) or prior_tickers
    if (
        fallback_tickers
        and not enriched.get("ticker")
        and not enriched.get("data")
        and not enriched.get("diagram")
        and is_market_compatible
    ):
        q_low = (latest_user_text or "").lower()
        has_tf_or_int = bool(_extract_timeframe_from_text(latest_user_text) or _extract_interval_from_text(latest_user_text))
        if (
            re.search(r"\b(?:it|them|same|instead|past|last|hourly|daily|weekly|graph|chart|plot|monte\s*carlo|base|based|history|use|future)\b", q_low)
            or has_tf_or_int
        ):
            enriched["ticker"] = ", ".join(fallback_tickers)

    return enriched


def detect_missed_graphing_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """
    Strictly on-demand turn-0 recovery when the user explicitly requests a diagram, a Monte Carlo simulation,
    or a market price plot and a small model forgets to call `skill_graphing_info`. Never triggers on music
    charts ('Beatport chart') or general data queries that require research first.
    """
    if not history:
        return None

    latest_user_text = ""
    for msg in reversed(history):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "user":
            content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
            latest_user_text = _strip_user_prefix(str(content or ""))
            break
    if not latest_user_text or latest_user_text.startswith("!"):
        return None

    q_low = latest_user_text.lower()
    # Never intercept music chart queries ("Beatport Top 100 chart", "Billboard chart")
    if contains_domain_terms(q_low, MUSIC_DOMAIN_WORDS):
        return None

    # 1. Explicit diagram request (with arrow notation, Mermaid fence, or explicit flowchart/diagram creation prompt)
    if _EXPLICIT_DIAGRAM_RE.search(q_low):
        is_flowchart = bool(re.search(r"\b(?:flow\s*chart|flowchart|flussdiagramm|ablaufdiagramm)\b", q_low))
        if is_flowchart or "->" in latest_user_text or "```" in latest_user_text:
            diag_type = DiagramType.FLOWCHART if is_flowchart else ChartType.DIAGRAM
            return (
                "skill_graphing_info",
                {
                    "chart_type": str(diag_type),
                    "query": latest_user_text,
                    "diagram": latest_user_text,
                },
            )

    state = get_channel_chart_state(active_channel_id)
    prior_tickers, _ = _extract_prior_history_context(history)
    fallback_tickers = (list(state.last_tickers) if state and state.last_tickers else None) or prior_tickers

    # 2. Explicit Monte Carlo simulation request or follow-up lookback/horizon adjustment
    is_mc_followup = bool(
        state
        and state.last_chart_type == ChartType.MONTE_CARLO
        and re.search(
            r"\b(?:base(?:d)?\s+(?:it\s+)?(?:on|off|of)|calibrated\s+on|use(?:d|ing)?\s+(?:the\s+)?(?:last|past|\d+|all)|"
            r"(?:into|in)\s+(?:the\s+)?future|do\s+it\s+again|all[- ]time\s+history|\d+\s*(?:y(?:ears?|rs?)?|mo(?:nths?)?)\s+of\s+history|"
            r"\d[\d,]*(?:\.\d+)?\s*k?\s*[- ]?(?:paths?|simulations?|sims?|runs?|trials?)|(?:with|use|try|do|run)\s+\d[\d,]*\s*k)\b",
            q_low,
        )
    )
    if _EXPLICIT_MONTE_CARLO_RE.search(q_low) or is_mc_followup:
        tickers = _extract_tickers_from_text(latest_user_text) or fallback_tickers
        if tickers:
            raw_args = enrich_graphing_tool_args(
                {
                    "chart_type": str(ChartType.MONTE_CARLO),
                    "ticker": ", ".join(tickers),
                    "query": latest_user_text,
                },
                history,
                active_channel_id=active_channel_id,
            )
            return ("skill_graphing_info", raw_args)

    # 3. Explicit market plot request ("plot NVDA vs SHOP.TO over 5 years", "give last 9 years of dxy chart", "graph it")
    if _EXPLICIT_MARKET_PLOT_RE.search(q_low) and not _NON_MARKET_CHART_RE.search(q_low):
        tickers = _extract_tickers_from_text(latest_user_text) or fallback_tickers
        if tickers:
            raw_args = enrich_graphing_tool_args(
                {
                    "chart_type": str(ChartType.MARKET),
                    "ticker": ", ".join(tickers),
                    "query": latest_user_text,
                },
                history,
                active_channel_id=active_channel_id,
            )
            return ("skill_graphing_info", raw_args)

    return None
