from dataclasses import dataclass
from typing import Optional

@dataclass
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

