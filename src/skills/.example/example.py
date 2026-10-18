"""
LLM-facing tool entrypoint(s) for the Example skill skeleton (`example.py`).
"""

from __future__ import annotations

from typing import Optional

from .aggregators.example_provider import ExampleProviderAggregator
from .catalogs import EXAMPLE_QUERY_ALIASES
from .context import record_channel_example_query
from .enums import ExampleAction
from .templates import build_example_llm_instructions

_aggregator = ExampleProviderAggregator()


def skill_example_info(
    query: str,
    action: Optional[str] = ExampleAction.LOOKUP,
) -> str:
    """
    Example skill tool entrypoint demonstrating the standardized skill pipeline:
    1. Normalize & resolve aliases from `catalogs.py` and `enums.py`.
    2. Record per-channel state in `context.py`.
    3. Fetch typed `models.py` records via `aggregators/`.
    4. Append concise LLM formatting instructions from `templates.py`.

    Args:
        query: Search term or identifier.
        action: Action mode ('lookup' or 'summary').
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return "Please provide a non-empty query."

    canonical_q = EXAMPLE_QUERY_ALIASES.get(clean_q.lower(), clean_q)
    record_channel_example_query(canonical_q)

    act = (action or ExampleAction.LOOKUP).strip().lower()
    record = _aggregator.fetch_item(canonical_q, action=act)
    output = (
        f"=== EXAMPLE SKILL DATA ({act.upper()}) ===\n"
        f"ID: {record.item_id}\n"
        f"Title: {record.title}\n"
        f"Status: {record.status}\n"
        f"URL: {record.url or 'N/A'}\n"
    )
    return output + build_example_llm_instructions()

