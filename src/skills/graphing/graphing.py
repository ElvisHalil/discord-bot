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

from src.context import clear_channel_attachments, queue_channel_attachment
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
from src.skills.graphing.models import CalendarHeatmapSpec, ChartSeries, ChartSpec, RenderedVisual
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


def clear_graphing_cache() -> None:
    """Clear all in-memory graphing/diagram caches, channel state, and pending attachments."""
    clear_graphing_aggregator_caches()
    clear_channel_chart_state()
    clear_channel_attachments()


def _extract_chart_timeframe(query: str, timeframe: str) -> str:
    tf = (timeframe or "").strip().lower()
    if tf:
        return tf
    q_low = (query or "").lower()
    patterns = [
        (r"\b(all[- ]time|max)\b", "max"),
        (r"\b(10\s*y(?:ears?|rs?)?|decade)\b", "10y"),
        (r"\b(5\s*y(?:ears?|rs?)?|five\s+years?)\b", "5y"),
        (r"\b(3\s*y(?:ears?|rs?)?|three\s+years?)\b", "3y"),
        (r"\b(2\s*y(?:ears?|rs?)?|two\s+years?)\b", "2y"),
        (r"\b(ytd|year[- ]to[- ]date)\b", "ytd"),
        (r"\b(1\s*y(?:ear|r)?|one\s+year|12\s*months?)\b", "1y"),
        (r"\b(6\s*m(?:onths?|o)?|six\s+months?)\b", "6mo"),
        (r"\b(3\s*m(?:onths?|o)?|three\s+months?)\b", "3mo"),
        (r"\b(1\s*m(?:onth|o)?|one\s+month|30\s*days?)\b", "1mo"),
        (r"\b(1\s*w(?:eek|k)?|5\s*d(?:ays?)?|7\s*d(?:ays?)?)\b", "5d"),
        (r"\b(intraday|1\s*d(?:ay)?|today)\b", "1d"),
        (r"\b(20\d{2})\b", None),
    ]
    for pat, code in patterns:
        m = re.search(pat, q_low)
        if m:
            return code if code is not None else m.group(1)
    return "1y"


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

    if ticker and ticker.strip():
        t_clean = ticker.strip()
        fx_direct = resolve_fx_pair_symbol(t_clean)
        if fx_direct and "," not in t_clean:
            _add(fx_direct)
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
        if candidates:
            return candidates[:5]

    q_clean = (query or "").strip()
    if not q_clean:
        return []
    q_low = q_clean.lower()

    # Check explicit FX pairs in query first (e.g. 'CAD/EURO', 'CAD-EUR', 'Canadian dollar to Euro')
    for fx_sym in extract_fx_pairs_from_text(q_clean):
        _add(fx_sym)

    # Check cashtags
    for m_ct in re.finditer(r"(?<![A-Za-z0-9])\$([A-Za-z]{1,6}(?:\.TO|-USD)?)\b", q_clean):
        _add(m_ct.group(1))

    # Match known ticker aliases in query order
    spans: List[tuple[int, int, str]] = []
    for alias in sorted(CHART_TICKER_ALIASES.keys(), key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(alias)}\b", q_low):
            s_idx, e_idx = m.start(), m.end()
            if not any(not (e_idx <= a or s_idx >= b) for a, b, _ in spans):
                spans.append((s_idx, e_idx, alias))

    # Match explicit uppercase symbols like NVDA, SHOP.TO, BTC-USD
    for m in re.finditer(r"\b([A-Z]{2,5}(?:\.TO|-USD|-CAD)?)\b", q_clean):
        tok = m.group(1)
        if tok.lower() not in CHART_QUERY_STOPWORDS and tok not in ("USD", "CAD", "EUR", "GBP", "YTD", "MAX", "CEO", "IPO", "ETF"):
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
    On-demand universal graphing and diagramming skill that renders high-resolution dark-mode PNG graphs, charts,
    and diagrams and attaches them directly to the Discord or Telegram reply.

    Use this tool ONLY when the user asks for a graph, plot, chart, heatmap, visual comparison, or diagram:
      1. **Arbitrary Data Graphs & Heatmaps (`chart_type`: 'bar', 'horizontal_bar', 'stacked_bar', 'line', 'area', 'scatter', 'pie', 'donut', 'heatmap', 'calendar_heatmap')**:
         Pass `labels` (e.g. 'Q1, Q2, Q3, Q4' or ISO dates '2027-05-01, 2027-05-02, ...') and `data` (e.g. '120, 145, 160, 195', or key-value pairs
         'AWS: 31, Azure: 25, GCP: 12, Other: 32', or multi-series 'Revenue: 10, 20, 30; Cost: 8, 12, 18',
         or JSON `[{\"name\": \"2025\", \"values\": [10, 20]}]`).
      2. **Financial & Market Graphs (`chart_type='market'` or pass `ticker`)**:
         Pass `ticker` (e.g. 'SHOP.TO', 'NVDA, AMD', 'BTC-USD', 'GC=F') and `timeframe` ('1d', '5d', '1mo', '3mo',
         '6mo', 'ytd', '1y', '2y', '5y', '10y', 'max', or a year like '2024') to plot price + moving averages + volume
         or a multi-asset normalized % return comparison graph.
      3. **Flowcharts, Sequence & Architecture Diagrams (`chart_type='diagram'` or 'flowchart' / 'sequence')**:
         Pass `diagram` (or `data`) with Mermaid diagram code (e.g. `flowchart TD\\n A[\"User\"] --> B[\"API\"]`) or shorthand arrow chains
         (e.g. `User -> Discord Gateway -> Orchestrator -> Skills -> Ollama`).

    Args:
        chart_type: One of 'line', 'bar', 'horizontal_bar', 'stacked_bar', 'area', 'scatter', 'pie', 'donut', 'heatmap', 'calendar_heatmap', 'market', or 'diagram'.
        title: Title displayed at the top of the rendered graph or diagram.
        data: Series numbers, key-value pairs, JSON dataset, or Mermaid/arrow diagram definition.
        labels: Optional comma-separated X-axis categories, ISO dates, or pie slice labels (e.g. 'Jan, Feb, Mar, Apr').
        ticker: Optional market symbol(s) for financial graphs (e.g. 'SHOP.TO' or 'NVDA, AAPL, MSFT').
        timeframe: Optional market graph window ('1d', '5d', '1mo', '3mo', '6mo', 'ytd', '1y', '5y', '10y', 'max').
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
    if not resolved_type:
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

    # 2. Financial / Market Time-Series Mode
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
        effective_ctype = resolved_type if resolved_type not in (ChartType.MARKET, ChartType.DIAGRAM) else ChartType.LINE
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
