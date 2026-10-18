"""
Typed dataclass models for the YouTube domain skill.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.utils.domain_vocab import DictLikeMixin


@dataclass(slots=True)
class YouTubeVideoResult(DictLikeMixin):
    """Normalized YouTube video search candidate."""
    id: str
    title: str
    channel: str
    url: str
    embeddable: bool = True

