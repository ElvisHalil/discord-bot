"""
Centralized prompt templates and LLM formatting instructions for the Weather skill.
"""

from __future__ import annotations

WEATHER_INSTRUCTIONS = (
    "=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    "1. Act like an enthusiastic TV weatherman.\n"
    "2. State the exact temperature and conditions.\n"
    "3. Recommend one fun activity based on this weather.\n"
    "4. Keep the weather section under 4 sentences (and also answer any other questions or requests from the user in full)."
)

