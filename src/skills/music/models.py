"""
Typed @dataclass(slots=True) domain models for the music skill package.
"""

from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional, Tuple, Union

from src.skills.music.enums import (
    IntensityLevel,
    SpectrumZoneId,
    TempoLevel,
)
from src.utils.domain_vocab import DictLikeMixin


@dataclass(slots=True, frozen=True)
class RegionalGenreConfig:
    """Typed compound regional-genre configuration supporting attribute and tuple-unpacking access."""
    region: str
    genre_id: int

    def as_tuple(self) -> Tuple[str, int]:
        return (self.region, self.genre_id)

    def __iter__(self) -> Iterator[Union[str, int]]:
        yield self.region
        yield self.genre_id

    def __getitem__(self, index: Union[int, slice]) -> Any:
        return self.as_tuple()[index]

    def __len__(self) -> int:
        return 2

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RegionalGenreConfig):
            return self.as_tuple() == other.as_tuple()
        if isinstance(other, tuple):
            return self.as_tuple() == other
        return False


@dataclass(slots=True)
class MoodProfile(DictLikeMixin):
    """Typed mood query profile for Deezer, Last.fm, and Beatport."""
    deezer_queries: List[str] = field(default_factory=list)
    lastfm_tags: List[str] = field(default_factory=list)
    beatport_query: Optional[str] = None


@dataclass(slots=True)
class SpectrumZone(DictLikeMixin):
    """Typed model for one cell in the 3x3 Tempo x Intensity Music Spectrum chart."""
    ui_zone: SpectrumZoneId
    tempo: TempoLevel
    intensity: IntensityLevel
    mainstream_tags: List[str] = field(default_factory=list)
    deep_cut_tags: List[str] = field(default_factory=list)
    intersection_modifiers: List[str] = field(default_factory=list)


@dataclass(slots=True)
class PlayedSongRecord(DictLikeMixin):
    """Per-channel record of the most recently served track, its acoustic/spectrum metadata,
    and the active discovery session state (`active_genre`, `active_mood`, `active_artist`, `active_similar_to`)."""
    song: str
    artist: str
    alt_song: Optional[str] = None
    genre: Optional[str] = None
    ui_zone: Optional[str] = None
    bpm: Optional[float] = None
    speed_score: Optional[float] = None
    intensity_score: Optional[float] = None
    gain_db: Optional[float] = None
    active_genre: Optional[str] = None
    active_mood: Optional[str] = None
    active_artist: Optional[str] = None
    active_similar_to: Optional[str] = None


@dataclass(slots=True)
class Track:
    """Represents a music track with structured metadata and optional acoustic features."""
    title: str
    artist: str
    album: Optional[str] = None
    genre: Optional[str] = None
    url: Optional[str] = None
    source: Optional[str] = None
    deezer_id: Optional[int] = None
    preview_url: Optional[str] = None
    bpm: Optional[float] = None
    gain: Optional[float] = None
    speed_score: Optional[float] = None
    intensity_score: Optional[float] = None
    acoustic_vibe: Optional[str] = None
    tier: int = 1
    relevance_score: float = 1.0
    verified_genre: bool = False

    def display_line(self) -> str:
        """Formatted string representation for tool output."""
        meta_parts = []
        if self.album:
            meta_parts.append(f"(Album: {self.album})")
        if self.genre:
            meta_parts.append(f"(Genre: {self.genre})")
        if self.bpm and self.bpm > 0:
            meta_parts.append(f"[~{int(round(self.bpm))} BPM]")
        if self.intensity_score is not None:
            meta_parts.append(f"[Intensity: {self.intensity_score:.1f}/10]")
        extra = f" {' '.join(meta_parts)}" if meta_parts else ""
        return f"- {self.title} by {self.artist}{extra}"
