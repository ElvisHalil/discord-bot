"""
Static catalogs and HTTP headers for the Weather domain skill.
"""

from __future__ import annotations

from typing import Dict, Tuple

WEATHER_DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": "curl/8.0",
}

WEATHER_HTML_ERROR_PREFIXES: Tuple[str, ...] = (
    "<!doctype",
    "<html",
)

