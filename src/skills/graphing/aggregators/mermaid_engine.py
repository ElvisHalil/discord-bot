"""
Mermaid & Directed Diagram Rendering Engine (`src/skills/graphing/aggregators/mermaid_engine.py`).

Supports flowcharts, sequence diagrams, architecture diagrams, state/class/ER diagrams, and mindmaps:
1. Auto-converts shorthand arrow chains ('A -> B -> C') into valid Mermaid syntax.
2. Sanitizes unquoted parentheses/special characters in Mermaid node labels.
3. Renders dark-mode PNG via Mermaid.ink / Kroki, with an automatic local Matplotlib directed-graph
   fallback so diagrams always render even when offline.
"""

from __future__ import annotations

import base64
import json
import re
from typing import List, Optional, Tuple
import zlib

from src.skills.graphing.aggregators.base import BaseGraphingAggregator
from src.skills.graphing.aggregators.matplotlib_engine import MatplotlibChartAggregator, _slugify_filename
from src.skills.graphing.models import RenderedVisual
from src.utils.cache import BoundedCache
from src.utils.http import DEFAULT_UA, ThreadLocalRequestsSession

_HTTP_SESSION = ThreadLocalRequestsSession({"User-Agent": DEFAULT_UA})
_DIAGRAM_CACHE: BoundedCache = BoundedCache(maxsize=128)

_MERMAID_HEADER_RE = re.compile(
    r"^\s*(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|gantt|pie|mindmap|timeline)\b",
    re.IGNORECASE,
)


def normalize_and_sanitize_mermaid(raw_code: str, diagram_type: str = "flowchart") -> str:
    """
    Convert shorthand arrow notation or fenced Mermaid blocks into clean, valid Mermaid syntax,
    automatically quoting node labels that contain parentheses.
    """
    code = (raw_code or "").strip()
    # Strip markdown fences if present
    code = re.sub(r"^```(?:mermaid)?\s*", "", code, flags=re.IGNORECASE).strip()
    code = re.sub(r"\s*```$", "", code).strip()

    # Unescape literal \n sequences if the LLM passed escaped newlines in a single line
    if "\n" not in code and "\\n" in code:
        code = code.replace("\\n", "\n")

    if not code:
        return 'flowchart TD\n    N1["Start"] --> N2["End"]'

    # If the user/model passed a simple arrow chain like "User -> Gateway -> Orchestrator -> Ollama"
    if not _MERMAID_HEADER_RE.match(code):
        dtype = (diagram_type or "flowchart").lower().strip()
        if dtype == "sequence" or (":" in code and ("->" in code or "->>" in code)):
            lines = ["sequenceDiagram"]
            segments = [s.strip() for s in re.split(r"[\n;]+", code) if s.strip()]
            for seg in segments:
                m_seq = re.match(r"^([^-\n]+?)\s*-+>>?\s*([^:\n]+?)\s*:\s*(.+)$", seg)
                if m_seq:
                    lines.append(f"    {m_seq.group(1).strip()}->>{m_seq.group(2).strip()}: {m_seq.group(3).strip()}")
                else:
                    lines.append(f"    {seg}")
            return "\n".join(lines)

        # Convert arrow chains (e.g. "Step 1 -> Step 2 -> Step 3; Step 2 -> Step 4") into flowchart TD
        direction = "LR" if dtype in ("architecture", "lr", "horizontal", "timeline") else "TD"
        lines = [f"flowchart {direction}"]
        node_ids: dict[str, str] = {}

        def _get_nid(label: str) -> str:
            lbl = label.strip().strip('"').strip("'")
            if lbl not in node_ids:
                nid = f"N{len(node_ids) + 1}"
                node_ids[lbl] = nid
            return node_ids[lbl]

        clauses = [c.strip() for c in re.split(r"[\n;]+", code) if c.strip()]
        has_edges = False
        for clause in clauses:
            parts = [p.strip() for p in re.split(r"\s*(?:-->|->|==>)\s*", clause) if p.strip()]
            if len(parts) >= 2:
                has_edges = True
                for i in range(len(parts) - 1):
                    src_lbl, dst_lbl = parts[i], parts[i + 1]
                    src_id, dst_id = _get_nid(src_lbl), _get_nid(dst_lbl)
                    safe_src = src_lbl.replace('"', "'")
                    safe_dst = dst_lbl.replace('"', "'")
                    lines.append(f'    {src_id}["{safe_src}"] --> {dst_id}["{safe_dst}"]')
        if has_edges:
            return "\n".join(lines)

        # Fallback: wrap raw Mermaid body in flowchart TD
        code = f"flowchart TD\n    {code}"

    # Sanitize unquoted square-bracket labels containing parentheses or slashes: id[Text (Info)] -> id["Text (Info)"]
    def _quote_bracket_label(match: re.Match) -> str:
        prefix = match.group(1)
        inner = match.group(2).strip()
        if inner.startswith('"') and inner.endswith('"'):
            return match.group(0)
        if any(ch in inner for ch in ("(", ")", "/", ":", "&")):
            safe_inner = inner.replace('"', "'")
            return f'{prefix}["{safe_inner}"]'
        return match.group(0)

    code = re.sub(r"(\b[A-Za-z0-9_]+)\[([^\]\n]+)\]", _quote_bracket_label, code)
    return code


def extract_nodes_and_edges(mermaid_code: str) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str, Optional[str]]], str]:
    """Extract `(nodes, edges, direction)` from Mermaid code for summary reporting and local Matplotlib fallback."""
    direction = "TD"
    m_dir = re.search(r"^\s*(?:flowchart|graph)\s+(TD|TB|BT|RL|LR)\b", mermaid_code, re.IGNORECASE | re.MULTILINE)
    if m_dir:
        direction = m_dir.group(1).upper()

    nodes_map: dict[str, str] = {}
    edges: List[Tuple[str, str, Optional[str]]] = []

    def _parse_node_token(token: str) -> Tuple[str, str]:
        tok = token.strip()
        m = re.match(r'^([A-Za-z0-9_]+)\s*(?:\[\s*"?([^"\]\n]+)"?\s*\]|\(\s*"?([^"\)\n]+)"?\s*\)|\{\s*"?([^"\}\n]+)"?\s*\})?$', tok)
        if m:
            nid = m.group(1)
            lbl = (m.group(2) or m.group(3) or m.group(4) or "").strip()
            if lbl:
                nodes_map[nid] = lbl
            elif nid not in nodes_map:
                nodes_map[nid] = nid
            return nid, nodes_map[nid]
        clean = tok.strip('"').strip("'")
        nid = re.sub(r"[^A-Za-z0-9_]", "_", clean)[:24] or f"N{len(nodes_map) + 1}"
        if nid not in nodes_map:
            nodes_map[nid] = clean
        return nid, clean

    for raw_line in mermaid_code.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("%%") or _MERMAID_HEADER_RE.match(line) or line.lower().startswith(("subgraph", "end", "style", "classdef", "class ")):
            continue
        # Sequence diagram line: A->>B: Message
        m_seq = re.match(r"^([A-Za-z0-9_ \"']+?)\s*-+>>?\s*([A-Za-z0-9_ \"']+?)\s*:\s*(.+)$", line)
        if m_seq:
            s_id, _ = _parse_node_token(m_seq.group(1))
            d_id, _ = _parse_node_token(m_seq.group(2))
            edges.append((s_id, d_id, m_seq.group(3).strip()))
            continue
        # Flowchart edge: A[...] -->|label| B[...] or A --> B
        m_edge = re.match(
            r"^(.+?)\s*(?:-->|---|-\.->|==>|->)\s*(?:\|([^|]+)\|\s*)?(.+)$",
            line,
        )
        if m_edge:
            s_id, _ = _parse_node_token(m_edge.group(1))
            d_id, _ = _parse_node_token(m_edge.group(3))
            edge_lbl = m_edge.group(2).strip() if m_edge.group(2) else None
            edges.append((s_id, d_id, edge_lbl))

    if not nodes_map:
        nodes_map = {"N1": "Start", "N2": "Process", "N3": "Complete"}
        edges = [("N1", "N2", None), ("N2", "N3", None)]

    return list(nodes_map.items()), edges, direction


class MermaidDiagramAggregator(BaseGraphingAggregator):
    """Renders Mermaid diagrams to PNG via Mermaid.ink / Kroki with a local Matplotlib fallback."""

    name = "mermaid"

    def __init__(self) -> None:
        self._mpl_fallback = MatplotlibChartAggregator()

    def render_diagram(
        self,
        diagram_code: str,
        title: str = "System & Process Diagram",
        diagram_type: str = "flowchart",
    ) -> RenderedVisual:
        sanitized = normalize_and_sanitize_mermaid(diagram_code, diagram_type=diagram_type)
        nodes, edges, direction = extract_nodes_and_edges(sanitized)

        cache_key = f"{title}::{sanitized}"
        cached = _DIAGRAM_CACHE.get(cache_key)
        if cached is not None:
            png_bytes, renderer_name = cached
        else:
            png_bytes, renderer_name = self._fetch_remote_mermaid_png(sanitized)
            if not png_bytes:
                png_bytes = self._mpl_fallback.render_flowchart_fallback(
                    title=title,
                    nodes=nodes,
                    edges=edges,
                    direction=direction,
                )
                renderer_name = "matplotlib_diagram_fallback"
            _DIAGRAM_CACHE[cache_key] = (png_bytes, renderer_name)

        node_preview = " → ".join(lbl for _, lbl in nodes[:6])
        summary_lines = [
            f"**Diagram Structure:** {len(nodes)} nodes, {len(edges)} directed connections ({direction})",
            f"**Key Flow:** {node_preview}",
        ]
        return RenderedVisual(
            filename=_slugify_filename(title, default="diagram"),
            png_bytes=png_bytes,
            title=title or "Diagram",
            summary_lines=summary_lines,
            renderer_used=renderer_name,
        )

    @staticmethod
    def _fetch_remote_mermaid_png(mermaid_code: str) -> Tuple[Optional[bytes], str]:
        """Try Mermaid.ink (dark theme) first, then Kroki.io, returning `(png_bytes, renderer_name)`."""
        # 1. Mermaid.ink pako/base64 dark-mode endpoint
        try:
            state = {
                "code": mermaid_code,
                "mermaid": json.dumps({"theme": "dark"}),
            }
            compressed = zlib.compress(json.dumps(state).encode("utf-8"), level=9)
            pako_b64 = base64.urlsafe_b64encode(compressed).decode("ascii")
            url = f"https://mermaid.ink/img/pako:{pako_b64}?type=png&bgColor=!0D1117"
            resp = _HTTP_SESSION.get(url, timeout=6)
            if resp.status_code == 200 and resp.content and resp.content[:8] == b"\x89PNG\r\n\x1a\n":
                return resp.content, "mermaid.ink"
        except Exception:
            pass

        # 2. Kroki.io Mermaid PNG endpoint
        try:
            compressed_raw = zlib.compress(mermaid_code.encode("utf-8"), level=9)
            kroki_b64 = base64.urlsafe_b64encode(compressed_raw).decode("ascii")
            resp = _HTTP_SESSION.get(f"https://kroki.io/mermaid/png/{kroki_b64}", timeout=6)
            if resp.status_code == 200 and resp.content and resp.content[:8] == b"\x89PNG\r\n\x1a\n":
                return resp.content, "kroki.io"
        except Exception:
            pass

        return None, ""

