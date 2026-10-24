"""
Typed domain models for the Unified Background Job, Reminder, Condition & Autoplay Engine (`src/jobs/models.py`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional


class TriggerType:
    ONCE_AT = "once_at"
    INTERVAL = "interval"
    DAILY_SCHEDULE = "daily_schedule"
    CONDITION = "condition"
    AUTOPLAY = "autoplay"


class ActionType:
    REMINDER = "reminder"
    AGENT_TASK = "agent_task"
    MUSIC_AUTOPLAY = "music_autoplay"


class ConditionKind:
    MARKET_PRICE = "market_price"
    WEATHER = "weather"
    YOUTUBE_UPLOAD = "youtube_upload"
    WEB_CONDITION = "web_condition"


# Guardrail constants
MIN_RECURRING_INTERVAL_SECONDS = 60  # Hard 1-minute floor on recurring intervals to prevent sub-minute spam
MIN_ONE_SHOT_DELAY_SECONDS = 60  # Hard 1-minute floor on one-shot reminders/tasks to prevent sub-minute spam
SUB_HOUR_DEFAULT_MAX_RUNS = 10
SUB_HOUR_DEFAULT_TTL_SECONDS = 3600  # 1 hour
MULTI_HOUR_DEFAULT_MAX_RUNS = 6
MULTI_HOUR_DEFAULT_TTL_SECONDS = 12 * 3600  # 12 hours (one waking day)
AUTOPLAY_DEFAULT_MAX_RUNS = 10
AUTOPLAY_DEFAULT_TTL_SECONDS = 45 * 60  # 45 minutes
AUTOPLAY_MIN_TRACK_SECONDS = 90
AUTOPLAY_MAX_TRACK_SECONDS = 480
AUTOPLAY_DEFAULT_TRACK_SECONDS = 210
DEFAULT_MAX_UNACKNOWLEDGED_FIRES = 5

MARKET_PRICE_WATCH_TTL_SECONDS = 14 * 86400  # 14 days for target price watches
MARKET_INTRADAY_MOVE_TTL_SECONDS = 24 * 3600  # 24 hours for intraday % move watches
WEATHER_CONDITION_TTL_SECONDS = 48 * 3600  # 48 hours for weather watches
YOUTUBE_CONDITION_TTL_SECONDS = 14 * 86400  # 14 days for YouTube upload watches
WEB_CONDITION_TTL_SECONDS = 7 * 86400  # 7 days for custom web/news condition watches

MAX_ACTIVE_USER_JOBS_PER_CHANNEL = 15


@dataclass(slots=True)
class UserJob:
    """
    Serializable representation of a scheduled reminder, recurring task,
    conditional alert ('when X happens'), or song-length music autoplay loop.
    """

    job_id: str
    channel_id: int
    creator_id: int = 0
    creator_name: str = ""
    trigger_type: str = TriggerType.ONCE_AT
    action_type: str = ActionType.REMINDER
    description: str = ""
    payload_text: str = ""
    created_at: float = field(default_factory=time.time)
    next_fire_at: float = field(default_factory=time.time)
    interval_seconds: Optional[int] = None
    schedule_spec: Optional[Dict[str, Any]] = None
    condition_spec: Optional[Dict[str, Any]] = None
    autoplay_spec: Optional[Dict[str, Any]] = None
    run_count: int = 0
    max_runs: Optional[int] = None
    expires_at: Optional[float] = None
    unacknowledged_fires: int = 0
    max_unacknowledged_fires: int = DEFAULT_MAX_UNACKNOWLEDGED_FIRES
    last_fired_at: Optional[float] = None
    is_system: bool = False
    paused: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserJob":
        valid_keys = {
            "job_id",
            "channel_id",
            "creator_id",
            "creator_name",
            "trigger_type",
            "action_type",
            "description",
            "payload_text",
            "created_at",
            "next_fire_at",
            "interval_seconds",
            "schedule_spec",
            "condition_spec",
            "autoplay_spec",
            "run_count",
            "max_runs",
            "expires_at",
            "unacknowledged_fires",
            "max_unacknowledged_fires",
            "last_fired_at",
            "is_system",
            "paused",
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass(slots=True)
class SystemJobProvider:
    """
    Adapter for domain-specific background services (such as `SPORTS_TRACKER`,
    `FLIGHT_TRACKER`, or Phase 2's `DAILY_DIGEST`) managed by the central `JobEngine`.
    """

    name: str
    interval_seconds: int
    is_active_fn: Callable[[], bool]
    poll_fn: Callable[[], Awaitable[None]]
    list_channel_fn: Optional[Callable[[int], List[str]]] = None
    clear_channel_fn: Optional[Callable[[int], int]] = None
    is_system_locked: bool = False
    last_run_at: float = 0.0
