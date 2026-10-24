"""
String enumerations (`StrEnum`) for the Job, Reminder, Condition & Music Autoplay skill (`src/skills/jobs/enums.py`).
"""

from enum import StrEnum


class JobManagerAction(StrEnum):
    """Supported actions for `skill_job_manager` and `!remind` / `!jobs` / `!autoplay` commands."""

    CREATE = "create"
    AUTOPLAY = "autoplay"
    SKIP = "skip"
    LIST = "list"
    STATUS = "status"
    CANCEL = "cancel"
    STOP = "stop"
    CLEAR = "clear"


class TriggerType(StrEnum):
    """Scheduling trigger families supported by the Unified Job Engine."""

    ONCE_AT = "once_at"
    INTERVAL = "interval"
    DAILY_SCHEDULE = "daily_schedule"
    CONDITION = "condition"
    AUTOPLAY = "autoplay"


class ActionType(StrEnum):
    """Execution payload types when a scheduled job fires."""

    REMINDER = "reminder"
    AGENT_TASK = "agent_task"
    MUSIC_AUTOPLAY = "music_autoplay"


class ConditionKind(StrEnum):
    """Deterministic and hash-gated condition families."""

    MARKET_PRICE = "market_price"
    WEATHER = "weather"
    YOUTUBE_UPLOAD = "youtube_upload"
    WEB_CONDITION = "web_condition"

