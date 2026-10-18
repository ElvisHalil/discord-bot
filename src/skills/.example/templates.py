"""
Centralized prompt templates and LLM formatting instructions for the Example skill skeleton.
Rule: Keep LLM instructions concise (3–4 lines max) to preserve context window tokens.
"""

from __future__ import annotations

EXAMPLE_LLM_INSTRUCTIONS = (
    "\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===\n"
    "1. Present the exact title and status from the data above.\n"
    "2. Format any source link as `[Source](<URL>)` inside angle brackets.\n"
    "3. Keep the summary under 3 sentences."
)


def build_example_llm_instructions() -> str:
    """Return the standard LLM instruction footer for the example skill."""
    return EXAMPLE_LLM_INSTRUCTIONS

