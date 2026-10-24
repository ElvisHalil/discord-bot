"""
Formatting and prompt templates for the Job, Reminder, Condition & Music Autoplay skill (`src/skills/jobs/templates.py`).
"""


def build_job_llm_instructions() -> str:
    """Return concise LLM instructions for presenting scheduled job/reminder confirmations."""
    return (
        "\n\n[SYSTEM INSTRUCTION — JOB & REMINDER ENGINE]\n"
        "- Present the scheduled job ID, trigger timing/condition, and auto-stop guardrails clearly.\n"
        "- Do not mention internal tool or function names."
    )

