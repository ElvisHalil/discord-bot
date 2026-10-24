"""
Typed domain models (`@dataclass(slots=True)`) for the Job, Reminder, Condition & Music Autoplay skill (`src/skills/jobs/models.py`).
"""

from dataclasses import dataclass

from src.jobs.models import SystemJobProvider, UserJob


@dataclass(slots=True)
class JobSummaryRecord:
    """Lightweight summary record for an active channel job or reminder."""

    job_id: str
    channel_id: int
    trigger_type: str
    action_type: str
    description: str
    next_fire_at: float


__all__ = [
    "JobSummaryRecord",
    "SystemJobProvider",
    "UserJob",
]

