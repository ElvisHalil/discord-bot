from dataclasses import dataclass
from typing import Optional

@dataclass
class Track:
    """Represents a music track with structured metadata."""
    title: str
    artist: str
    album: Optional[str] = None
    genre: Optional[str] = None
    url: Optional[str] = None
    source: Optional[str] = None

    def display_line(self) -> str:
        """Formatted string representation for tool output."""
        meta_parts = []
        if self.album:
            meta_parts.append(f"(Album: {self.album})")
        if self.genre:
            meta_parts.append(f"(Genre: {self.genre})")
        extra = f" {' '.join(meta_parts)}" if meta_parts else ""
        return f"- {self.title} by {self.artist}{extra}"

