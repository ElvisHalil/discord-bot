"""
Centralized prompt templates and LLM formatting instructions for the News skill.
"""

from __future__ import annotations

HEADLINES_INSTRUCTIONS = (
    "--- RESPONSE FORMATTING INSTRUCTIONS FOR YOU (THE LLM) ---\n"
    "• Bold heading for this region/topic, then one bullet per story: - **Headline** — one-sentence summary. [Source Name](<URL>)\n"
    "• Only headlines are listed above, not article text: keep summaries to what the headline says. "
    "To expand on a story, call `read_website` on its URL first.\n"
    "• Copy each URL exactly and keep it inside <> so Discord shows no preview card. No second list or raw link dump."
)

