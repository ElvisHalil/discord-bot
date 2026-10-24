"""
Abstract base contract for Job & Condition Evaluators (`src/skills/jobs/aggregators/base.py`).
"""

from abc import ABC


class BaseJobsAggregator(ABC):
    """Base contract for background condition evaluators and job providers."""

