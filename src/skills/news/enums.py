"""
Typed string enums for the News domain skill.
"""

from __future__ import annotations

from enum import StrEnum


class NewsTopic(StrEnum):
    """Canonical Google News RSS section topics."""
    WORLD = "WORLD"
    NATION = "NATION"
    BUSINESS = "BUSINESS"
    TECHNOLOGY = "TECHNOLOGY"
    ENTERTAINMENT = "ENTERTAINMENT"
    SPORTS = "SPORTS"
    SCIENCE = "SCIENCE"
    HEALTH = "HEALTH"

