"""
Typed dataclass models for the Weather domain skill.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.utils.domain_vocab import DictLikeMixin


@dataclass(slots=True)
class WeatherSnapshot(DictLikeMixin):
    """Normalized weather report snapshot for a city."""
    city: str
    conditions: str
    provider: str = "wttr"

