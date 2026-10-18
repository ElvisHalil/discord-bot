"""
Typed dataclass models for the News domain skill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Tuple, Union


@dataclass(slots=True, frozen=True)
class NewsEdition:
    """Typed Google News regional edition specification (hl, gl)."""
    hl: str
    gl: str

    def as_tuple(self) -> Tuple[str, str]:
        return (self.hl, self.gl)

    def __iter__(self) -> Iterator[str]:
        yield self.hl
        yield self.gl

    def __getitem__(self, index: Union[int, slice]) -> Any:
        return self.as_tuple()[index]

    def __len__(self) -> int:
        return 2

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NewsEdition):
            return self.as_tuple() == other.as_tuple()
        if isinstance(other, tuple):
            return self.as_tuple() == other
        return False

