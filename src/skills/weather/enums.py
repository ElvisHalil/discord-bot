"""
Typed string enums for the Weather domain skill.
"""

from __future__ import annotations

from enum import StrEnum


class WeatherProvider(StrEnum):
    """Supported upstream weather data providers."""
    OPEN_METEO = "open_meteo"
    WTTR = "wttr"


class WeatherFormat(StrEnum):
    """Supported wttr.in format query presets."""
    COMPACT = "%C+%t+%w+%h"
    JSON = "j1"
