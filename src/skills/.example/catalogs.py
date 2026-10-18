"""
Static lookup registries, alias maps, and stopwords for the Example skill skeleton.
Rule: Only static collections (`dict`, `frozenset`, `tuple`) belong in `catalogs.py`.
"""

from __future__ import annotations

from typing import Dict

EXAMPLE_QUERY_ALIASES: Dict[str, str] = {
    "demo": "example-demo",
    "sample": "example-sample",
}

EXAMPLE_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "please", "check", "show", "find", "get",
})

