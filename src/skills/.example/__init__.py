"""
Public exports for the Example skill skeleton.
Uses relative imports so copying `.example` to `src/skills/<new_skill>` works immediately.
"""

from .example import skill_example_info
from .tracker import EXAMPLE_TRACKER, ExampleTrackerService, skill_example_tracker

__all__ = [
    "EXAMPLE_TRACKER",
    "ExampleTrackerService",
    "skill_example_info",
    "skill_example_tracker",
]
