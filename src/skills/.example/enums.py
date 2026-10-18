"""
Typed string enums for the Example skill skeleton.
Rule: Only `enum.StrEnum` classes belong in `enums.py`.
"""

from __future__ import annotations

from enum import StrEnum


class ExampleAction(StrEnum):
    """Supported actions for the example skill."""
    LOOKUP = "lookup"
    SUMMARY = "summary"


class ExampleTrackerAction(StrEnum):
    """Supported actions for the optional background tracker."""
    START = "start"
    STOP = "stop"
    LIST = "list"


class ExampleProviderId(StrEnum):
    """Registered upstream data providers for the example skill."""
    PRIMARY = "primary"
    FALLBACK = "fallback"

