from abc import ABC
from datetime import datetime
from typing import Any, Optional

class BaseSportsAggregator(ABC):
    """Abstract base class for sports data and esports providers."""

    @staticmethod
    def format_human_date(date_input: Any) -> str:
        """Converts YYYYMMDD, ISO timestamp, or datetime into 'DayOfWeek, Month DD, YYYY'."""
        if not date_input:
            return ""
        if isinstance(date_input, datetime):
            return date_input.strftime("%A, %B %d, %Y")
        s = str(date_input).strip()
        if len(s) == 8 and s.isdigit():
            try:
                return datetime.strptime(s, "%Y%m%d").strftime("%A, %B %d, %Y")
            except Exception:
                pass
        try:
            clean = s.replace("Z", "+00:00")
            return datetime.fromisoformat(clean).strftime("%A, %B %d, %Y")
        except Exception:
            return s

