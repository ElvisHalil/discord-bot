import re
from typing import Optional

from src.skills.music.catalogs import (
    ALIAS_TO_MOOD,
    GENRE_CANONICAL_MAP,
    GENRE_MOOD_MATRIX,
    MOOD_ALIASES,
    MOOD_FILLER_WORDS as _FILLER_WORDS,
    STANDALONE_MOOD_PROFILES,
)
from src.skills.music.enums import (
    CanonicalGenre,
    MoodCategory,
)
from src.skills.music.models import MoodProfile

__all__ = [
    "ALIAS_TO_MOOD",
    "CanonicalGenre",
    "GENRE_CANONICAL_MAP",
    "GENRE_MOOD_MATRIX",
    "MOOD_ALIASES",
    "MoodCategory",
    "MoodProfile",
    "STANDALONE_MOOD_PROFILES",
    "parse_mood_and_genre",
    "resolve_mood_alias",
]


def resolve_mood_alias(text: Optional[str]) -> Optional[str]:
    """Maps a raw mood word or phrase to its canonical mood key if recognized."""
    if not text:
        return None
    clean = re.sub(r"[^a-z0-9\s&-]", "", text.lower()).strip()
    if clean in ALIAS_TO_MOOD:
        return ALIAS_TO_MOOD[clean]
    tokens = [w for w in clean.split() if w not in _FILLER_WORDS]
    joined = " ".join(tokens)
    if joined in ALIAS_TO_MOOD:
        return ALIAS_TO_MOOD[joined]
    if len(tokens) == 1 and tokens[0] in ALIAS_TO_MOOD:
        return ALIAS_TO_MOOD[tokens[0]]
    return None


def parse_mood_and_genre(genre: Optional[str] = None, mood: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """
    Decomposes optional `genre` and `mood` inputs into `(clean_genre, canonical_mood)`.
    - If `mood` is explicitly provided, resolves its canonical mood and cleans `genre`.
    - If `genre` contains a standalone mood (e.g. 'banger', 'something melodic', 'love song'),
      returns `(None, canonical_mood)`.
    - If `genre` contains both a mood and a genre (e.g. 'melodic dnb', 'hard techno', 'dnb banger', 'r&b love song'),
      splits them into `(base_genre, canonical_mood)`.
    - If `genre` is a pure genre (e.g. 'dnb', 'german rap'), returns `(genre, None)`.
    """
    explicit_mood = resolve_mood_alias(mood)
    if not genre or genre.lower().strip() in ("all", "global", "general", "none", ""):
        return None, explicit_mood

    raw_g = genre.strip()
    clean_g = re.sub(r"[^a-z0-9\s&-]", "", raw_g.lower()).strip()
    tokens = [w for w in clean_g.split() if w not in _FILLER_WORDS]
    if not tokens:
        return None, explicit_mood

    joined = " ".join(tokens)

    # 1. If the entire genre string is a mood keyword (e.g. 'banger', 'something melodic', 'late night', 'slow jams')
    if joined in ALIAS_TO_MOOD:
        return None, explicit_mood or ALIAS_TO_MOOD[joined]

    # 2. If mood was already explicitly passed, check if genre is redundant with mood or a real genre
    if explicit_mood:
        rem_tokens = [w for w in tokens if w not in ALIAS_TO_MOOD]
        rem_genre = " ".join(rem_tokens).strip()
        return (rem_genre if rem_genre else None), explicit_mood

    # 3. Check for multi-word mood + genre combinations inside `genre`
    # Check 2-word mood prefixes/suffixes first (e.g. 'high energy dnb', 'r&b slow jams')
    if len(tokens) >= 3:
        first_two = f"{tokens[0]} {tokens[1]}"
        last_two = f"{tokens[-2]} {tokens[-1]}"
        if first_two in ALIAS_TO_MOOD:
            rem = " ".join(tokens[2:]).strip()
            return (rem or None), ALIAS_TO_MOOD[first_two]
        if last_two in ALIAS_TO_MOOD:
            rem = " ".join(tokens[:-2]).strip()
            return (rem or None), ALIAS_TO_MOOD[last_two]

    # Check 1-word mood prefix or suffix (e.g. 'melodic dnb', 'dnb banger', 'hard techno', 'sad rap')
    if len(tokens) >= 2:
        if tokens[0] in ALIAS_TO_MOOD:
            rem = " ".join(tokens[1:]).strip()
            return (rem or None), ALIAS_TO_MOOD[tokens[0]]
        if tokens[-1] in ALIAS_TO_MOOD:
            rem = " ".join(tokens[:-1]).strip()
            return (rem or None), ALIAS_TO_MOOD[tokens[-1]]

    return raw_g, None
