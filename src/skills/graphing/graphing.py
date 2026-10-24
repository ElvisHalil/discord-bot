"""
Universal Graphing & Diagrams Visualization Skill (`src/skills/graphing/graphing.py`).

Provides on-demand rendering of:
  1. Universal 2D Data Graphs & Charts (`line`, `bar`, `horizontal_bar`, `stacked_bar`, `area`, `scatter`, `pie`, `donut`)
     from arbitrary structured data, JSON series, or key-value pairs across any domain.
  2. Financial & Market Time-Series Graphs (`market` — single-ticker Price + 50d/200d MA + Volume, or
     multi-ticker normalized % cumulative return comparisons across 1D to MAX timeframes).
  3. Flowcharts, Sequence, State, Class, ER & Architecture Diagrams (`diagram` — Mermaid syntax or
     shorthand arrow chains rendered via Mermaid.ink / Kroki with a guaranteed local Matplotlib fallback).
"""

import json
import re
from typing import Any, List, Optional

from src.context import (
    clear_channel_attachments,
    queue_channel_attachment,
    set_calendar_heatmap_renderer,
)
from src.skills.graphing.aggregators import (
    HeatmapAggregator,
    MarketSeriesAggregator,
    MatplotlibChartAggregator,
    MermaidDiagramAggregator,
    clear_graphing_aggregator_caches,
)
from src.skills.graphing.catalogs import (
    CHART_QUERY_STOPWORDS,
    CHART_TICKER_ALIASES,
    CHART_TYPE_ALIASES,
    CURRENCY_NAME_TO_ISO,
    DIAGRAM_TYPE_ALIASES,
    extract_fx_pairs_from_text,
    resolve_fx_pair_symbol,
)
from src.skills.graphing.context import (
    clear_channel_chart_state,
    get_channel_chart_state,
    update_channel_chart_state,
)
from src.skills.graphing.enums import ChartType, DiagramType
from src.skills.graphing.models import (
    CalendarDayCell,
    CalendarHeatmapSpec,
    ChartSeries,
    ChartSpec,
    RenderedVisual,
)
from src.skills.graphing.templates import format_visual_confirmation
from src.utils.workers import submit_to_worker

_mpl_agg = MatplotlibChartAggregator()
_heatmap_agg = HeatmapAggregator()
_mermaid_agg = MermaidDiagramAggregator()
_market_agg = MarketSeriesAggregator()


def render_and_queue_calendar_heatmap(spec: CalendarHeatmapSpec) -> RenderedVisual:
    """Render a 7-column Mon-Sun calendar heatmap card and queue it as a Discord/Telegram attachment."""
    visual = _heatmap_agg.render_calendar_heatmap(spec)
    queue_channel_attachment(
        filename=visual.filename,
        data=visual.png_bytes,
        caption=visual.title,
    )
    update_channel_chart_state(
        chart_type=ChartType.CALENDAR_HEATMAP,
        title=visual.title,
    )
    return visual


def _render_calendar_from_dicts(
    title: str,
    subtitle: str = "",
    cells: Optional[List[dict]] = None,
    value_prefix: str = "$",
    currency: str = "",
    footer_notes: Optional[List[str]] = None,
) -> Optional[str]:
    day_cells = [
        CalendarDayCell(
            date_str=str(c.get("date_str") or ""),
            value=float(c.get("value") or 0.0),
            label=str(c.get("label") or ""),
            sublabel=str(c.get("sublabel") or ""),
            is_best=bool(c.get("is_best")),
            is_direct=bool(c.get("is_direct")),
        )
        for c in (cells or [])
        if c.get("date_str")
    ]
    if not day_cells:
        return None
    spec = CalendarHeatmapSpec(
        title=title,
        subtitle=subtitle,
        cells=day_cells,
        value_prefix=value_prefix,
        currency=currency,
        footer_notes=list(footer_notes or []),
    )
    visual = render_and_queue_calendar_heatmap(spec)
    return visual.filename


set_calendar_heatmap_renderer(_render_calendar_from_dicts)


def clear_graphing_cache() -> None:
    """Clear all in-memory graphing/diagram caches, channel state, and pending attachments."""
    clear_graphing_aggregator_caches()
    clear_channel_chart_state()
    clear_channel_attachments()


def _extract_chart_timeframe(query: str, timeframe: str) -> str:
    tf_raw = (timeframe or "").strip().lower()
    q_low = (query or "").lower()

    # Check if query or timeframe explicitly requests a bar interval ('hourly', 'by hr', '1hr', '15m', '5m', 'daily', 'weekly')
    int_code = ""
    for src in (q_low, tf_raw):
        m_int = re.search(
            r"\b(hourly|by\s+h(?:our|r)|per\s+h(?:our|r)|1\s*h(?:r|our)\b|60\s*m(?:in)?|15\s*m(?:in)?|5\s*m(?:in)?|30\s*m(?:in)?|daily|weekly)\b",
            src,
        )
        if m_int:
            tok = m_int.group(1)
            if "h" in tok or "60" in tok:
                int_code = "1h"
            elif "15" in tok:
                int_code = "15m"
            elif "30" in tok:
                int_code = "30m"
            elif "5" in tok:
                int_code = "5m"
            elif "week" in tok:
                int_code = "1wk"
            elif "dai" in tok:
                int_code = "1d"
            break

    patterns = [
        (r"\b(all[- ]time|max)\b", "max"),
        (r"\b(10\s*y(?:ears?|rs?)?|decade)\b", "10y"),
        (r"\b(5\s*y(?:ears?|rs?)?|five\s+years?)\b", "5y"),
        (r"\b(3\s*y(?:ears?|rs?)?|three\s+years?)\b", "3y"),
        (r"\b(2\s*y(?:ears?|rs?)?|two\s+years?)\b", "2y"),
        (r"\b(ytd|year[- ]to[- ]date)\b", "ytd"),
        (r"\b(1\s*y(?:ear|r)?|one\s+year|12\s*months?|(?:past|last|next)\s+year)\b", "1y"),
        (r"\b(6\s*m(?:onths?|o)?|six\s+months?|half\s+year)\b", "6mo"),
        (r"\b(3\s*m(?:onths?|o)?|three\s+months?|90\s*days?|quarter)\b", "3mo"),
        (r"\b(2\s*m(?:onths?|o)?|two\s+months?|60\s*days?)\b", "2mo"),
        (r"\b(1\s*m(?:onth|o)?|one\s+month|30\s*days?|(?:past|last)\s+month)\b", "1mo"),
        (r"\b(3\s*w(?:eeks?|ks?)|21\s*d(?:ays?)?)\b", "21d"),
        (r"\b(2\s*w(?:eeks?|ks?)|14\s*d(?:ays?)?)\b", "14d"),
        (r"\b(10\s*d(?:ays?)?)\b", "10d"),
        (r"\b(7\s*d(?:ays?)?|1\s*w(?:eek|k)?|(?:past|last)\s+week|seven\s+days?)\b", "7d"),
        (r"\b(5\s*d(?:ays?)?|five\s+days?)\b", "5d"),
        (r"\b(4\s*d(?:ays?)?|four\s+days?)\b", "4d"),
        (r"\b(3\s*d(?:ays?)?|72\s*h(?:ours?|rs?)?|three\s+days?)\b", "3d"),
        (r"\b(2\s*d(?:ays?)?|48\s*h(?:ours?|rs?)?|two\s+days?)\b", "2d"),
        (r"\b(24\s*h(?:ours?|rs?)?)\b", "24h"),
        (r"\b(intraday|1\s*d(?:ay)?|today)\b", "1d"),
        (r"\b(20\d{2})\b", None),
    ]

    # Prefer explicit timeframe in query when present so stale LLM timeframe defaults don't override user prompt
    resolved_tf = ""
    for pat, code in patterns:
        m = re.search(pat, q_low)
        if m:
            resolved_tf = code if code is not None else m.group(1)
            break

    if not resolved_tf:
        m_y = re.search(r"\b(\d{1,2})\s*y(?:ears?|rs?)?\b", q_low)
        m_mo = re.search(r"\b(\d{1,2})\s*mo(?:nths?)?\b", q_low)
        m_w = re.search(r"\b(\d{1,2})\s*w(?:eeks?|ks?)?\b", q_low)
        m_d = re.search(r"\b(\d{1,3})\s*d(?:ays?)?\b", q_low)
        if m_y:
            resolved_tf = f"{int(m_y.group(1))}y"
        elif m_mo:
            resolved_tf = f"{int(m_mo.group(1))}mo"
        elif m_w:
            resolved_tf = f"{int(m_w.group(1))}w"
        elif m_d:
            resolved_tf = f"{int(m_d.group(1))}d"

    if not resolved_tf and tf_raw:
        tf_stripped = re.sub(r"\b(?:hourly|by\s+h(?:our|r)|1h|1hr|60m|15m|5m|30m|daily|weekly)\b", "", tf_raw).strip()
        resolved_tf = tf_stripped

    if not resolved_tf:
        resolved_tf = "7d" if int_code in ("1h", "15m", "30m") else ("1d" if int_code == "5m" else "1y")

    if int_code and int_code not in resolved_tf.split():
        return f"{resolved_tf} {int_code}"
    return resolved_tf


_MC_LOOKBACK_PATTERNS = (
    re.compile(
        r"\b(?:base(?:d)?\s+(?:it\s+)?(?:on|off(?:\s+of)?|of)|calibrated\s+(?:it\s+)?on|use(?:d|ing)?|from|with)\s+"
        r"(?:the\s+)?(?:last\s+|past\s+|previous\s+|prior\s+)?"
        r"(all[- ]time|max(?:\s+history)?|entire\s+history|full\s+history|"
        r"\d+\s*(?:y(?:ears?|rs?)?|mo(?:nths?)?|m\b|w(?:eeks?|ks?)?|d(?:ays?)?)|"
        r"one\s+year|two\s+years|three\s+years|five\s+years|decade|ytd|year[- ]to[- ]date)"
        r"(?!\s*(?:into|in)\s+(?:the\s+)?future|\s+forward)"
        r"(?:\s+(?:of\s+)?(?:history|historical(?:\s+data)?|price\s+history|data|returns|timeframe))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:over\s+)?(?:the\s+)?(?:past|last|previous|prior)\s+"
        r"(all[- ]time|max|\d+\s*(?:y(?:ears?|rs?)?|mo(?:nths?)?|w(?:eeks?|ks?)?|d(?:ays?)?)|"
        r"one\s+year|two\s+years|three\s+years|five\s+years|decade)"
        r"(?:\s+(?:of\s+)?(?:history|historical(?:\s+data)?|price\s+history|data|returns|lookback|calibration))?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:last\s+|past\s+)?(all[- ]time|max|\d+\s*(?:y(?:ears?|rs?)?|mo(?:nths?)?|w(?:eeks?|ks?)?|d(?:ays?)?)|"
        r"one\s+year|two\s+years|three\s+years|five\s+years|decade|ytd|year[- ]to[- ]date)\s+"
        r"(?:of\s+)?(?:history|historical(?:\s+data)?|price\s+history|data|returns|lookback|calibration)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(all[- ]time(?:\s+history)?|max\s+history|entire\s+history|full\s+history)\b",
        re.IGNORECASE,
    ),
)


def _extract_monte_carlo_horizon_and_lookback(
    query: str,
    timeframe: str,
    prev_horizon: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """
    Separate the forward simulation horizon (e.g. '2y') from an optional historical calibration
    lookback (e.g. 'based on 10 years' -> '10y', 'all-time history' -> 'max').
    When no lookback is specified by the user, returns `(horizon, None)` so `run_monte_carlo`
    uses its 5-year history default.
    """
    q_clean = (query or "").strip()
    lookback_code: Optional[str] = None
    remaining_q = q_clean

    for pat in _MC_LOOKBACK_PATTERNS:
        m = pat.search(q_clean)
        if m:
            lb_span = m.group(1)
            lookback_code = "max" if "entire" in lb_span.lower() or "full" in lb_span.lower() else _extract_chart_timeframe(lb_span, "")
            remaining_q = (q_clean[: m.start()] + " " + q_clean[m.end() :]).strip()
            break

    # Extract forward horizon from the remaining query (excluding the lookback phrase)
    rem_tf = _extract_chart_timeframe(remaining_q, "") if remaining_q else ""
    has_explicit_rem_tf = bool(
        remaining_q
        and re.search(
            r"\b(?:\d+\s*(?:y(?:ears?|rs?)?|mo(?:nths?)?|w(?:eeks?|ks?)?|d(?:ays?)?)|"
            r"one\s+year|two\s+years|three\s+years|five\s+years|decade|next\s+year|next\s+month)\b",
            remaining_q,
            re.IGNORECASE,
        )
    )

    if has_explicit_rem_tf and rem_tf:
        horizon_code = rem_tf
    elif not lookback_code and timeframe:
        horizon_code = _extract_chart_timeframe("", timeframe)
    elif prev_horizon:
        horizon_code = prev_horizon
    elif timeframe and _extract_chart_timeframe("", timeframe) != lookback_code:
        horizon_code = _extract_chart_timeframe("", timeframe)
    else:
        horizon_code = "1y"

    return horizon_code, lookback_code


_MC_PATHS_RE = re.compile(
    r"\b(?:(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*[- ]?(?:paths?|simulations?|sims?|runs?|trials?|trajectories)|"
    r"(?:paths?|simulations?|sims?|runs?|trials?)\s*[:=]?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?|"
    r"(?:with|use|using|do|try|run)\s+(\d[\d,]*(?:\.\d+)?)\s*(k))\b",
    re.IGNORECASE,
)


def _extract_monte_carlo_paths(query: str, default_paths: int = 10000) -> int:
    """Extract custom Monte Carlo path count from natural language (e.g. '50k paths', '100,000 simulations')."""
    if not query:
        return default_paths
    m = _MC_PATHS_RE.search(query)
    if not m:
        return default_paths
    num_str = m.group(1) or m.group(3) or m.group(5)
    k_str = m.group(2) or m.group(4) or m.group(6)
    if not num_str:
        return default_paths
    try:
        val = float(num_str.replace(",", ""))
        if k_str:
            val *= 1000.0
        return max(100, min(100000, int(round(val))))
    except ValueError:
        return default_paths


def _extract_market_tickers(query: str, ticker: str) -> List[str]:
    candidates: List[str] = []

    def _add(sym: str) -> None:
        s = sym.strip().strip("$")
        if not s:
            return
        fx_canon = resolve_fx_pair_symbol(s)
        if fx_canon:
            s = fx_canon
        canon = CHART_TICKER_ALIASES.get(s.lower(), s.upper())
        if canon not in [CHART_TICKER_ALIASES.get(c.lower(), c.upper()) for c in candidates]:
            candidates.append(s)

    q_clean = (query or "").strip()
    q_fx_pairs = extract_fx_pairs_from_text(q_clean) if q_clean else []
    has_q_btc_pair = any(s.endswith("-BTC") or s.startswith(("BTC-", "SATS-")) for s in q_fx_pairs)

    if ticker and ticker.strip():
        t_clean = ticker.strip()
        fx_direct = resolve_fx_pair_symbol(t_clean)
        if fx_direct and "," not in t_clean:
            _add(fx_direct)
            return candidates[:5]
        # If the user's query explicitly requests an FX / Currency-vs-BTC / SATS pair and the LLM only passed 'BTC' / 'BTC-USD', use the pair
        if (
            len(q_fx_pairs) == 1
            and (
                q_fx_pairs[0].endswith(("=X", "-BTC"))
                or q_fx_pairs[0].startswith("SATS-")
                or (q_fx_pairs[0].startswith("BTC-") and q_fx_pairs[0] != "BTC-USD")
            )
            and t_clean.upper() in ({"BTC", "BTC-USD"} | set(CURRENCY_NAME_TO_ISO.values()))
        ):
            _add(q_fx_pairs[0])
            return candidates[:5]
        for part in re.split(r"\s*(?:,|\bvs\.?\b|\bversus\b|\band\b)\s*", t_clean, flags=re.IGNORECASE):
            p = part.strip()
            if not p:
                continue
            fx_part = resolve_fx_pair_symbol(p)
            if fx_part:
                _add(fx_part)
            elif "/" in p and p.lower() not in CHART_TICKER_ALIASES:
                for sub in p.split("/"):
                    if sub.strip():
                        _add(sub.strip())
            else:
                _add(p)
        if candidates and not re.search(r"\b(?:vs\.?|versus|compare|comparing|comparison|overlay|against|and|with|between)\b", str(query or ""), re.IGNORECASE):
            return candidates[:5]

    if not q_clean:
        return candidates[:5]
    q_low = q_clean.lower()

    # Check explicit FX / Currency-vs-BTC / SATS pairs in query first (e.g. 'CAD/EURO', 'CAD/BTC', '$1 in BTC', 'SATS/USD')
    for fx_sym in q_fx_pairs:
        _add(fx_sym)

    # Check cashtags
    for m_ct in re.finditer(r"(?<![A-Za-z0-9])\$([A-Za-z]{1,6}(?:\.TO|-USD)?)\b", q_clean):
        _add(m_ct.group(1))

    # Match known ticker aliases in query order
    spans: List[tuple[int, int, str]] = []
    for alias in sorted(CHART_TICKER_ALIASES.keys(), key=len, reverse=True):
        if has_q_btc_pair and alias in ("btc", "bitcoin"):
            continue
        for m in re.finditer(rf"\b{re.escape(alias)}\b", q_low):
            s_idx, e_idx = m.start(), m.end()
            if not any(not (e_idx <= a or s_idx >= b) for a, b, _ in spans):
                spans.append((s_idx, e_idx, alias))

    all_ccy_codes = set(CURRENCY_NAME_TO_ISO.values()) | {"EURO", "SATS", "SAT"}
    # Match explicit uppercase symbols like NVDA, SHOP.TO, BTC-USD
    for m in re.finditer(r"\b([A-Z]{2,5}(?:\.TO|-USD|-CAD)?)\b", q_clean):
        tok = m.group(1)
        if has_q_btc_pair and tok == "BTC":
            continue
        if tok.lower() not in CHART_QUERY_STOPWORDS and tok not in all_ccy_codes and tok not in ("YTD", "MAX", "CEO", "IPO", "ETF", "GBM"):
            s_idx, e_idx = m.start(), m.end()
            if not any(not (e_idx <= a or s_idx >= b) for a, b, _ in spans):
                spans.append((s_idx, e_idx, tok))

    spans.sort(key=lambda item: item[0])
    for _, _, sym in spans:
        _add(sym)

    return candidates[:5]


def _parse_labels_arg(labels_raw: Optional[str]) -> List[str]:
    if not labels_raw:
        return []
    raw = str(labels_raw).strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
    return [p.strip().strip('"').strip("'") for p in raw.split(",") if p.strip()]


def _parse_chart_spec(
    chart_type: str,
    title: str,
    labels_raw: Optional[str],
    data_raw: Optional[str],
    query: str,
    x_label: str = "",
    y_label: str = "",
) -> Optional[ChartSpec]:
    """Parse flexible LLM or user inputs into a normalized `ChartSpec`."""
    labels = _parse_labels_arg(labels_raw)
    series_list: List[ChartSeries] = []
    raw = (data_raw or "").strip()

    # 1. Try JSON object or array
    if raw.startswith(("{", "[")):
        try:
            parsed: Any = json.loads(raw)
            if isinstance(parsed, dict):
                if "labels" in parsed and isinstance(parsed["labels"], list) and not labels:
                    labels = [str(x) for x in parsed["labels"]]
                if "series" in parsed and isinstance(parsed["series"], list):
                    for idx, item in enumerate(parsed["series"]):
                        if isinstance(item, dict) and "values" in item:
                            vals = [float(v) for v in item["values"]]
                            series_list.append(ChartSeries(name=str(item.get("name") or f"Series {idx + 1}"), values=vals))
                else:
                    # Either {"Series A": [1, 2, 3], "Series B": [4, 5, 6]} OR {"AWS": 31, "Azure": 25}
                    all_lists = all(isinstance(v, list) for v in parsed.values()) if parsed else False
                    if all_lists:
                        for k, v_list in parsed.items():
                            series_list.append(ChartSeries(name=str(k), values=[float(x) for x in v_list]))
                    else:
                        kv_labels, kv_vals = [], []
                        for k, v in parsed.items():
                            try:
                                kv_vals.append(float(v))
                                kv_labels.append(str(k))
                            except (TypeError, ValueError):
                                continue
                        if kv_vals:
                            if not labels:
                                labels = kv_labels
                            series_list.append(ChartSeries(name=title or "Values", values=kv_vals))
            elif isinstance(parsed, list) and parsed:
                if all(isinstance(x, (int, float)) for x in parsed):
                    series_list.append(ChartSeries(name=title or "Series 1", values=[float(x) for x in parsed]))
                elif all(isinstance(x, dict) for x in parsed):
                    if "values" in parsed[0]:
                        for idx, item in enumerate(parsed):
                            vals = [float(v) for v in item.get("values", [])]
                            series_list.append(ChartSeries(name=str(item.get("name") or f"Series {idx + 1}"), values=vals))
                    else:
                        kv_labels, kv_vals = [], []
                        for item in parsed:
                            lbl = item.get("label") or item.get("name") or item.get("category") or item.get("x")
                            val = item.get("value") if "value" in item else item.get("y")
                            if lbl is not None and val is not None:
                                kv_labels.append(str(lbl))
                                kv_vals.append(float(val))
                        if kv_vals:
                            if not labels:
                                labels = kv_labels
                            series_list.append(ChartSeries(name=title or "Values", values=kv_vals))
        except Exception:
            pass

    # 2. Try semicolon-separated multi-series ("Revenue: 100, 120, 150; Cost: 70, 80, 90")
    #    or key-value pairs ("AWS: 31, Azure: 25, GCP: 12, Other: 32")
    if not series_list and raw:
        if ";" in raw and ":" in raw:
            chunks = [c.strip() for c in raw.split(";") if c.strip()]
            for ch in chunks:
                if ":" in ch:
                    s_name, s_nums = ch.split(":", 1)
                    nums = [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", s_nums)]
                    if nums:
                        series_list.append(ChartSeries(name=s_name.strip(), values=nums))
        if not series_list:
            # Check for "Label: Value, Label2: Value2"
            kv_matches = re.findall(r"([A-Za-z0-9_ ./&+\-()]+?)\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*%?", raw)
            if len(kv_matches) >= 2:
                if not labels:
                    labels = [k.strip(" ,;") for k, _ in kv_matches]
                vals = [float(v) for _, v in kv_matches]
                series_list.append(ChartSeries(name=title or "Series 1", values=vals))
            else:
                nums = [float(m) for m in re.findall(r"-?\d+(?:\.\d+)?", raw)]
                if nums:
                    series_list.append(ChartSeries(name=title or "Series 1", values=nums))

    # 3. Last resort: extract "Label: Value" or "Label Value%" pairs from `query`
    if not series_list and query:
        kv_q = re.findall(r"([A-Za-z0-9_ ./&+\-]+?)\s*(?::|=|\s+)\s*(-?\d+(?:\.\d+)?)\s*%?(?:,|$|\band\b)", query)
        cleaned_kv = [
            (re.sub(r"^(?:chart|plot|graph|bar|pie|line|of|for|with|and)\s+", "", k.strip(), flags=re.IGNORECASE).strip(), float(v))
            for k, v in kv_q
            if k.strip()
        ]
        if len(cleaned_kv) >= 2:
            if not labels:
                labels = [k for k, _ in cleaned_kv]
            series_list.append(ChartSeries(name=title or "Values", values=[v for _, v in cleaned_kv]))

    if not series_list or not any(s.values for s in series_list):
        return None

    return ChartSpec(
        chart_type=chart_type,
        title=title or "Data Chart",
        labels=labels,
        series=series_list,
        x_label=x_label,
        y_label=y_label,
    )


def skill_graphing_info(
    chart_type: Optional[str] = None,
    title: Optional[str] = None,
    data: Optional[str] = None,
    labels: Optional[str] = None,
    ticker: Optional[str] = None,
    timeframe: Optional[str] = None,
    x_label: Optional[str] = None,
    y_label: Optional[str] = None,
    diagram: Optional[str] = None,
    query: Optional[str] = None,
) -> str:
    """
    On-demand universal graphing, Monte Carlo simulation, and diagramming tool that renders dark-mode PNG charts, heatmaps, stochastic simulations, or diagrams and attaches them to the reply.
    Use ONLY when the user explicitly asks to graph, plot, chart, heatmap, run a Monte Carlo price simulation, or draw a diagram.

    Args:
        chart_type: One of 'line', 'bar', 'horizontal_bar', 'stacked_bar', 'area', 'scatter', 'pie', 'donut', 'heatmap', 'calendar_heatmap', 'market', 'monte_carlo', or 'diagram' ('flowchart'/'sequence').
        title: Title displayed at the top of the rendered graph or diagram.
        data: Series numbers ('120, 145, 160'), key-value pairs ('AWS: 31, Azure: 25'), multi-series ('Rev: 10, 20; Cost: 8, 12'), JSON dataset, or Mermaid/arrow diagram code.
        labels: Optional comma-separated X-axis categories, ISO dates ('2027-05-01, 2027-05-02'), or pie slice labels (e.g. 'Q1, Q2, Q3, Q4').
        ticker: Optional market symbol(s) for financial graphs or Monte Carlo simulations (e.g. 'SHOP.TO', 'NVDA, AMD', 'BTC-USD', 'CADEUR=X').
        timeframe: Optional market graph window or forward simulation horizon ('1d', '3d', '5d', '7d', '14d', '1mo', '3mo', '6mo', 'ytd', '1y', '2y', '3y', '5y', '10y', 'max'; supports interval suffix like '7d 1h' or '1mo 1h').
        x_label: Optional X-axis label.
        y_label: Optional Y-axis label.
        diagram: Optional Mermaid syntax or shorthand arrow chain ('Node A -> Node B -> Node C') for flowcharts/diagrams.
        query: Optional natural-language description of the graph/diagram request.
    """
    ct_raw = (chart_type or "").strip().lower()
    q_raw = (query or "").strip()
    t_raw = (ticker or "").strip()
    d_raw = (data or diagram or "").strip()
    title_clean = (title or "").strip()

    resolved_type = CHART_TYPE_ALIASES.get(ct_raw, "")
    if any(w in q_raw.lower() for w in ("monte carlo", "montecarlo", "gbm simulation", "stochastic simulation")):
        resolved_type = ChartType.MONTE_CARLO
    elif not resolved_type:
        if ct_raw in DIAGRAM_TYPE_ALIASES or any(
            w in q_raw.lower() for w in ("flowchart", "sequence diagram", "architecture diagram", "mermaid", "state diagram", "er diagram")
        ) or ("->" in d_raw or "-->" in d_raw or d_raw.lower().startswith(("flowchart", "graph ", "sequencediagram"))):
            resolved_type = ChartType.DIAGRAM
        elif any(w in q_raw.lower() for w in ("heatmap", "heat map", "price calendar", "fare calendar", "calendar grid")):
            resolved_type = ChartType.HEATMAP
        elif t_raw or (not d_raw and _extract_market_tickers(q_raw, t_raw)):
            resolved_type = ChartType.MARKET
        elif "pie" in q_raw.lower() or "donut" in q_raw.lower():
            resolved_type = ChartType.PIE
        elif "line" in q_raw.lower():
            resolved_type = ChartType.LINE
        else:
            resolved_type = ChartType.BAR

    print(
        f"📊 [Executing Graphing Skill] Type: '{resolved_type}', Title: '{title_clean}', "
        f"Ticker: '{t_raw}', Timeframe: '{timeframe or ''}'"
    )

    visual: Optional[RenderedVisual] = None

    # 1. Diagram Mode (Mermaid / Flowchart / Sequence / Architecture)
    if resolved_type == ChartType.DIAGRAM:
        diag_subtype = DIAGRAM_TYPE_ALIASES.get(ct_raw, DiagramType.FLOWCHART)
        if "sequence" in q_raw.lower():
            diag_subtype = DiagramType.SEQUENCE
        code_input = d_raw or q_raw
        visual = _mermaid_agg.render_diagram(
            diagram_code=code_input,
            title=title_clean or "Architecture & Process Diagram",
            diagram_type=diag_subtype,
        )
        update_channel_chart_state(
            chart_type=ChartType.DIAGRAM,
            title=visual.title,
            diagram_code=code_input,
        )

    # 2. Monte Carlo GBM Simulation Mode
    elif resolved_type == ChartType.MONTE_CARLO:
        prev_st = get_channel_chart_state()
        targets = _extract_market_tickers(q_raw, t_raw)
        if not targets and prev_st and prev_st.last_tickers:
            targets = list(prev_st.last_tickers)
        prev_mc_horizon = (
            prev_st.last_timeframe
            if (prev_st and prev_st.last_chart_type == ChartType.MONTE_CARLO and prev_st.last_timeframe)
            else None
        )
        horizon_code, lookback_code = _extract_monte_carlo_horizon_and_lookback(
            q_raw,
            timeframe or "",
            prev_horizon=prev_mc_horizon,
        )
        num_paths = _extract_monte_carlo_paths(q_raw, default_paths=10000)
        if targets:
            mc_res = submit_to_worker(_market_agg.run_monte_carlo, targets[0], horizon_code, num_paths, lookback_code).result()
            if mc_res is not None:
                visual = _mpl_agg.render_monte_carlo_chart(mc_res)
                update_channel_chart_state(
                    chart_type=ChartType.MONTE_CARLO,
                    title=visual.title,
                    tickers=[mc_res.symbol],
                    timeframe=horizon_code,
                )

    # 3. Financial / Market Time-Series Mode
    elif resolved_type == ChartType.MARKET or (t_raw and not d_raw):
        targets = _extract_market_tickers(q_raw, t_raw)
        if not targets:
            prev_st = get_channel_chart_state()
            if prev_st and prev_st.last_tickers:
                targets = list(prev_st.last_tickers)
        tf_code = _extract_chart_timeframe(q_raw, timeframe or "")
        if targets:
            futs = [submit_to_worker(_market_agg.fetch_bar_series, sym, tf_code) for sym in targets[:5]]
            series_objs = [s for f in futs if (s := f.result()) is not None]
            if len(series_objs) >= 2:
                combined_intent = f"{q_raw} {t_raw}".lower()
                wants_separate = bool(
                    re.search(
                        r"\b(?:separate|individually|individual|(?:one|1)\s+(?:chart|graph|plot)\s+(?:for\s+)?each|"
                        r"for\s+each\s+(?:currency|ticker|pair|coin|asset)|"
                        r"(?:[2-5]|two|three|four|five)\s+(?:separate\s+|individual\s+|different\s+)?(?:charts?|graphs?|plots?))\b",
                        combined_intent,
                    )
                )
                wants_overlay = bool(
                    re.search(
                        r"\b(?:vs\.?|versus|compare|comparing|comparison|overlay|against|relative\s+to|"
                        r"(?:in|on)\s+(?:a|one|the\s+same)\s+(?:single\s+)?(?:chart|graph|plot))\b",
                        combined_intent,
                    )
                )
                if wants_separate or not wants_overlay:
                    visuals = [_mpl_agg.render_single_market_chart(s) for s in series_objs]
                    for v in visuals:
                        queue_channel_attachment(
                            filename=v.filename,
                            data=v.png_bytes,
                            caption=v.title,
                        )
                    update_channel_chart_state(
                        chart_type=ChartType.MARKET,
                        title=visuals[-1].title,
                        tickers=[s.symbol for s in series_objs],
                        timeframe=tf_code,
                    )
                    return "\n\n".join(format_visual_confirmation(v) for v in visuals)

                visual = _mpl_agg.render_multi_market_comparison(series_objs)
                update_channel_chart_state(
                    chart_type=ChartType.MARKET,
                    title=visual.title,
                    tickers=[s.symbol for s in series_objs],
                    timeframe=tf_code,
                )
            elif len(series_objs) == 1:
                visual = _mpl_agg.render_single_market_chart(series_objs[0])
                update_channel_chart_state(
                    chart_type=ChartType.MARKET,
                    title=visual.title,
                    tickers=[series_objs[0].symbol],
                    timeframe=tf_code,
                )

    # 3. Universal 2D Data Chart & Heatmap Mode (bar, horizontal_bar, stacked_bar, line, area, scatter, pie, donut, heatmap, calendar_heatmap)
    if visual is None:
        effective_ctype = resolved_type if resolved_type not in (ChartType.MARKET, ChartType.MONTE_CARLO, ChartType.DIAGRAM) else ChartType.LINE
        spec = _parse_chart_spec(
            chart_type=effective_ctype,
            title=title_clean or q_raw[:60] or "Data Visualization",
            labels_raw=labels,
            data_raw=d_raw,
            query=q_raw,
            x_label=(x_label or "").strip(),
            y_label=(y_label or "").strip(),
        )
        if spec is None:
            return (
                "❌ Could not parse chartable data or resolve market tickers. "
                "To plot arbitrary data, provide `labels` (e.g. 'Q1, Q2, Q3, Q4') and `data` (e.g. '10, 25, 40, 55' or "
                "'AWS: 31, Azure: 25, GCP: 12'). To plot stocks/crypto, provide `ticker` (e.g. 'SHOP.TO' or 'NVDA, AMD'). "
                "To draw a diagram, provide `chart_type='diagram'` and `data='A -> B -> C'`."
            )
        if effective_ctype in (ChartType.HEATMAP, ChartType.CALENDAR_HEATMAP):
            visual = _heatmap_agg.render_matrix_heatmap(spec)
        else:
            visual = _mpl_agg.render_data_chart(spec)
        update_channel_chart_state(
            chart_type=effective_ctype,
            title=visual.title,
            spec=spec,
        )

    queue_channel_attachment(
        filename=visual.filename,
        data=visual.png_bytes,
        caption=visual.title,
    )
    return format_visual_confirmation(visual)
