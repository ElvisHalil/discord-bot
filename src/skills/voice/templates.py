"""
LLM formatting and spoken-delivery prompt templates for the Voice-Channel AI Co-Host skill (`src/skills/voice/templates.py`).
"""

from .enums import VoiceAction


def build_voice_llm_instructions(action: str = VoiceAction.STATUS, is_spoken_turn: bool = False) -> str:
    """Return concise LLM formatting instructions tailored to the voice co-host action."""
    if is_spoken_turn:
        return (
            "\n\n[VOICE CO-HOST SPOKEN DELIVERY INSTRUCTIONS]\n"
            "- Your reply will be spoken out loud in the Discord voice channel via Neural TTS AND posted in text.\n"
            "- Lead with 1 to 2 natural, conversational sentences (under 40 words) that sound great spoken aloud.\n"
            "- If you used tools that returned links or detailed stats, put the links/details below the opening spoken summary.\n"
            "- Never mention internal tool names (`skill_*`) or system tags."
        )
    if action == VoiceAction.RECAP:
        return (
            "\n\n[VOICE RECAP INSTRUCTIONS]\n"
            "- Summarize the voice channel conversation clearly, attributing key points, decisions, and questions to each speaker by name.\n"
            "- Highlight any action items, predictions, or follow-up questions raised during the call."
        )
    return (
        "\n\n[VOICE CO-HOST SKILL INSTRUCTIONS]\n"
        "- Confirm the voice channel status, active listening mode, voice profile, or spoken action concisely in 1-2 sentences.\n"
        "- Never mention internal tool or function names."
    )
