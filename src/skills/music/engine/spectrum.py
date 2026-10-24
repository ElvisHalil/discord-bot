"""
3x3 Tempo x Intensity Music Spectrum Shift Engine.

Allows users to step 1 notch Harder/Softer (Intensity axis) or Faster/Slower (Tempo axis)
from the currently playing song's genre, or from the center zone ('mid_mid') when no song
is playing or its genre cannot be identified.
"""

import re
from typing import Any, Dict, List, Optional, Tuple, Union

from src.skills.music.catalogs import (
    BROAD_GENRE_TO_ZONE,
    COORDS_BY_ZONE,
    DEFAULT_CENTER_ZONE,
    EXPLICIT_MUSIC_CUE_WORDS as _EXPLICIT_MUSIC_CUE_WORDS,
    FASTER_PHRASES as _FASTER_PHRASES,
    HARDER_PHRASES as _HARDER_PHRASES,
    INTENSITY_AXIS,
    SLOWER_PHRASES as _SLOWER_PHRASES,
    SOFTER_PHRASES as _SOFTER_PHRASES,
    SPECTRUM_CHART,
    TEMPO_AXIS,
    ZONE_BY_COORDS,
    ZONE_BY_NAME,
)
from src.skills.music.context import (
    clear_channel_played_songs,
    detect_missed_music_shift_tool_call,
    enrich_music_tool_args,
    get_channel_last_played_song,
    record_channel_played_song,
)
from src.skills.music.enums import (
    IntensityLevel,
    SpectrumZoneId,
    TempoLevel,
)
from src.skills.music.models import (
    PlayedSongRecord,
    SpectrumZone,
)
from src.utils.domain_vocab import compile_word_boundary_re, has_non_music_domain_signal

__all__ = [
    "BROAD_GENRE_TO_ZONE",
    "COORDS_BY_ZONE",
    "DEFAULT_CENTER_ZONE",
    "INTENSITY_AXIS",
    "IntensityLevel",
    "PlayedSongRecord",
    "SPECTRUM_CHART",
    "SpectrumZone",
    "SpectrumZoneId",
    "TEMPO_AXIS",
    "TempoLevel",
    "ZONE_BY_COORDS",
    "ZONE_BY_NAME",
    "classify_genre_family",
    "classify_genre_to_zone",
    "clear_channel_played_songs",
    "detect_missed_music_shift_tool_call",
    "enrich_music_tool_args",
    "get_channel_last_played_song",
    "get_zone_tags_for_family",
    "has_non_music_domain_context",
    "is_valid_music_shift_context",
    "parse_spectrum_shift",
    "record_channel_played_song",
    "step_spectrum_zone",
]

_ROCK_METAL_FAMILY_RE = re.compile(
    r"\b(?:"
    r"rock|metal|punk|grunge|shoegaze|metalcore|deathcore|mathcore|grindcore"
    r"|post-hardcore|hardcore\s+punk|doom|sludge|stoner|slowcore|sadcore|emo"
    r"|alt-rock|pop-punk|post-punk|post-rock|noise\s+rock|industrial\s+metal"
    r")\b",
    re.IGNORECASE,
)

_HIPHOP_RNB_FAMILY_RE = re.compile(
    r"\b(?:"
    r"hip-hop|hip\s+hop|rap|trap|drill|phonk|boom\s+bap|g-funk"
    r"|r&b|rnb|neo-soul|soul|quiet\s+storm|lofi|lo-fi|chillhop|cloud\s+rap"
    r")\b",
    re.IGNORECASE,
)

_ELECTRONIC_FAMILY_RE = re.compile(
    r"\b(?:"
    r"electronic|electronica|edm|dance|house|microhouse|techno|trance|psytrance"
    r"|dnb|drum\s+and\s+bass|drum\s*&\s*bass|liquid|neurofunk|jump\s+up|jungle"
    r"|breakcore|breakbeat|dubstep|chillstep|riddim|uk\s+garage|bassline"
    r"|hardstyle|happy\s+hardcore|uk\s+hardcore|gabber|frenchcore"
    r"|synthwave|retrowave|vaporwave|chillwave|electropop|synthpop|hyperpop"
    r"|bubblegum\s+bass|eurobeat|nu-disco|disco|electro|ebm|industrial"
    r"|ambient|downtempo|chillout|trip-hop|trip\s+hop|drone|dub|power\s+electronics"
    r")\b",
    re.IGNORECASE,
)

_POP_INDIE_FOLK_FAMILY_RE = re.compile(
    r"\b(?:"
    r"pop|indie\s+pop|jangle\s+pop|twee\s+pop|city\s+pop|soft\s+pop|sophisti-pop|dream\s+pop"
    r"|folk|indie\s+folk|freak\s+folk|acoustic|singer-songwriter|country"
    r"|bossanova|bossa\s+nova|jazz|dark\s+jazz|blues|classical|modern\s+classical"
    r")\b",
    re.IGNORECASE,
)

_ZONE_FAMILY_TAGS: Dict[Tuple[str, str], List[str]] = {
    # Electronic macro family across all 9 zones
    ("slow_soft", "electronic"): ["ambient", "downtempo", "chillout", "space ambient"],
    ("mid_soft", "electronic"): ["chillwave", "downtempo", "melodic house", "deep house"],
    ("fast_soft", "electronic"): ["liquid dnb", "minimal techno", "microhouse", "ambient techno"],
    ("slow_mid", "electronic"): ["trip-hop", "dub", "downtempo", "chillstep"],
    ("mid_mid", "electronic"): ["synthpop", "synthwave", "vaporwave", "electropop"],
    ("fast_mid", "electronic"): ["house", "tech house", "techno", "trance", "uk garage", "electropop"],
    ("slow_hard", "electronic"): ["industrial", "dark electro", "power electronics", "ebm"],
    ("mid_hard", "electronic"): ["dubstep", "ebm", "hard techno", "electro house"],
    ("fast_hard", "electronic"): ["drum and bass", "neurofunk", "hardstyle", "breakcore", "gabber"],
    # Rock / Metal / Punk macro family across all 9 zones
    ("slow_soft", "rock_metal"): ["slowcore", "post-rock", "acoustic", "indie folk"],
    ("mid_soft", "rock_metal"): ["indie folk", "soft rock", "acoustic", "freak folk"],
    ("fast_soft", "rock_metal"): ["shoegaze", "dream pop", "jangle pop", "post-punk"],
    ("slow_mid", "rock_metal"): ["blues rock", "slowcore", "sadcore", "desert rock"],
    ("mid_mid", "rock_metal"): ["alt-rock", "indie rock", "classic rock", "jangle pop"],
    ("fast_mid", "rock_metal"): ["pop-punk", "punk rock", "garage rock", "post-punk"],
    ("slow_hard", "rock_metal"): ["doom metal", "stoner rock", "sludge", "drone metal", "death doom"],
    ("mid_hard", "rock_metal"): ["grunge", "hard rock", "metalcore", "post-hardcore", "noise rock", "industrial metal"],
    ("fast_hard", "rock_metal"): ["thrash metal", "hardcore punk", "grindcore", "death metal"],
    # Hip-Hop / Rap / R&B / Phonk macro family across all 9 zones
    ("slow_soft", "hiphop_rnb"): ["lo-fi beats", "lofi", "quiet storm", "chillout"],
    ("mid_soft", "hiphop_rnb"): ["rnb", "neo-soul", "quiet storm", "soul"],
    ("fast_soft", "hiphop_rnb"): ["liquid funk", "neo-soul", "rnb"],
    ("slow_mid", "hiphop_rnb"): ["trip-hop", "boom bap", "g-funk", "west coast hip hop"],
    ("mid_mid", "hiphop_rnb"): ["hip-hop", "rap", "boom bap", "cloud rap"],
    ("fast_mid", "hiphop_rnb"): ["trap", "hip-hop", "uk drill", "rap"],
    ("slow_hard", "hiphop_rnb"): ["phonk", "dark trap", "memphis rap"],
    ("mid_hard", "hiphop_rnb"): ["trap", "drill", "uk drill", "hardcore hip hop"],
    ("fast_hard", "hiphop_rnb"): ["drift phonk", "phonk", "rage rap", "uk drill"],
}


def classify_genre_family(genre_or_tag: Optional[str]) -> Optional[str]:
    """
    Classify a genre or subgenre tag into a macro genre family:
    'electronic', 'rock_metal', 'hiphop_rnb', or 'pop_indie_folk'.
    Used to keep 1-notch spectrum shifts ('harder', 'faster', 'softer', 'slower')
    within the listener's current musical universe instead of jumping from Electronic to Thrash Metal.
    """
    if not genre_or_tag:
        return None
    s = str(genre_or_tag).strip().lower()
    if not s or s in ("acoustic dsp", "detected", "unknown"):
        return None
    # Check specific compound exceptions before general regexes
    if "phonk" in s:
        return "hiphop_rnb"
    if _ROCK_METAL_FAMILY_RE.search(s):
        return "rock_metal"
    if _ELECTRONIC_FAMILY_RE.search(s):
        return "electronic"
    if _HIPHOP_RNB_FAMILY_RE.search(s):
        return "hiphop_rnb"
    if _POP_INDIE_FOLK_FAMILY_RE.search(s):
        return "pop_indie_folk"
    return None


def get_zone_tags_for_family(ui_zone: str, family: Optional[str]) -> List[str]:
    """Return family-consistent genre tags for a given (ui_zone, family) pair."""
    if not ui_zone or not family:
        return []
    return list(_ZONE_FAMILY_TAGS.get((str(ui_zone).lower(), family), []))


def classify_genre_to_zone(genre_or_tags: Union[str, List[str], None]) -> Optional[str]:
    """
    Map a genre string or list of Last.fm/Deezer tags onto one of the 9 spectrum `ui_zone` cells.
    Returns None if the genre cannot be identified.
    """
    if not genre_or_tags:
        return None

    candidates: List[str] = (
        [genre_or_tags] if isinstance(genre_or_tags, str) else [str(t) for t in genre_or_tags if t]
    )
    cleaned_candidates = [c.strip().lower() for c in candidates if c and c.strip()]
    if not cleaned_candidates:
        return None

    # Build ordered tag -> ui_zone pairs (longest phrase first to avoid 'dnb' shadowing 'liquid dnb')
    chart_tag_pairs: List[Tuple[str, str]] = []
    for zone in SPECTRUM_CHART:
        z_name = str(zone.ui_zone)
        for tag in zone.mainstream_tags + zone.deep_cut_tags:
            chart_tag_pairs.append((tag.lower(), z_name))
    chart_tag_pairs.sort(key=lambda x: len(x[0]), reverse=True)

    broad_pairs = sorted(BROAD_GENRE_TO_ZONE.items(), key=lambda x: len(x[0]), reverse=True)

    # Pass 1: Direct exact match against ui_zone, chart tags, or broad genre map
    for cand in cleaned_candidates:
        if cand in ZONE_BY_NAME:
            return cand
        for tag, z_name in chart_tag_pairs:
            if cand == tag:
                return z_name
        if cand in BROAD_GENRE_TO_ZONE:
            return BROAD_GENRE_TO_ZONE[cand]

    # Pass 2: Substring / word-boundary match against chart tags (longest first)
    for cand in cleaned_candidates:
        for tag, z_name in chart_tag_pairs:
            if re.search(rf"(?<![a-z0-9]){re.escape(tag)}(?![a-z0-9])", cand):
                return z_name

    # Pass 3: Substring / word-boundary match against broad genre map (longest first)
    for cand in cleaned_candidates:
        for b_tag, z_name in broad_pairs:
            if re.search(rf"(?<![a-z0-9]){re.escape(b_tag)}(?![a-z0-9])", cand):
                return z_name

    # Pass 4: Match against intersection_modifiers if present
    for cand in cleaned_candidates:
        for zone in SPECTRUM_CHART:
            for mod in zone.intersection_modifiers:
                if re.search(rf"\b{re.escape(mod.lower())}\b", cand):
                    return str(zone.ui_zone)

    return None


# Comparative question / factual comparison patterns ("faster than", "why is X slower", "is it harder to")
_COMPARATIVE_QUESTION_RE = re.compile(
    r"(?:"
    r"\b(?:faster|slower|harder|softer|heavier|lighter)\s+(?:than|to|for|at|on|in\s+the|route|flight|layover|option|connection)\b|"
    r"\b(?:faster|slower|harder|softer|heavier|lighter|tougher|rougher)\s+(?:day|week|month|year|time|shift|workday|morning|night|life)\b|"
    r"\b(?:why|how\s+come|which\s+one|who)\s+(?:is|are|was|were|runs?|goes?)\b.*\b(?:faster|slower|harder|softer)\b|"
    r"^\s*(?:is|are|was|were|do|does|did|can|could|would|should)\s+(?:it|this|that|they|he|she)\b.*\b(?:faster|slower|harder|softer)\b|"
    r"\b(?:work|study|try|push|hit|train|play\s+the\s+game)\s+harder\b|"
    r"\b(?:easier\s+or\s+harder|harder\s+or\s+easier|faster\s+or\s+slower|slower\s+or\s+faster)\b"
    r")",
    re.IGNORECASE,
)

_EXPLICIT_MUSIC_CUE_RE = compile_word_boundary_re(_EXPLICIT_MUSIC_CUE_WORDS)
_HARDER_RE = compile_word_boundary_re(_HARDER_PHRASES)
_SOFTER_RE = compile_word_boundary_re(_SOFTER_PHRASES)
_FASTER_RE = compile_word_boundary_re(_FASTER_PHRASES)
_SLOWER_RE = compile_word_boundary_re(_SLOWER_PHRASES)


def has_non_music_domain_context(text: str) -> bool:
    """Return True if the text contains non-music domain keywords (tech, sports, flights, weather, finance) or comparative factual questions."""
    if not text:
        return False
    cleaned = re.sub(r"\[Replying to song card:.*?\]", "", text, flags=re.IGNORECASE).strip()
    if has_non_music_domain_signal(cleaned):
        return True
    if _COMPARATIVE_QUESTION_RE.search(cleaned):
        return True
    return False


def parse_spectrum_shift(text: str) -> Optional[Tuple[int, int, str]]:
    """
    Parse a relative music shift request into (d_tempo, d_intensity, human_label).
    Returns None if no shift keyword is present or if the text is a non-music comparison.
    """
    if not text:
        return None

    cleaned = re.sub(r"\[Replying to song card:.*?\]", "", text, flags=re.IGNORECASE).strip()
    if not cleaned or has_non_music_domain_context(cleaned):
        return None

    d_tempo = 0
    d_intensity = 0
    labels: List[str] = []

    if _FASTER_RE.search(cleaned):
        d_tempo += 1
        labels.append("Faster (+1 Tempo)")
    if _SLOWER_RE.search(cleaned):
        d_tempo -= 1
        labels.append("Slower (-1 Tempo)")

    if _HARDER_RE.search(cleaned):
        d_intensity += 1
        labels.append("Harder (+1 Intensity)")
    if _SOFTER_RE.search(cleaned):
        d_intensity -= 1
        labels.append("Softer (-1 Intensity)")

    if d_tempo == 0 and d_intensity == 0:
        return None

    return (d_tempo, d_intensity, " & ".join(labels))


def _has_shift_keyword(text: str) -> bool:
    return bool(
        _FASTER_RE.search(text)
        or _SLOWER_RE.search(text)
        or _HARDER_RE.search(text)
        or _SOFTER_RE.search(text)
    )


def is_valid_music_shift_context(user_text: str, history: Optional[list] = None) -> bool:
    """
    3-Tier Context Disambiguation Check ensuring comparative words ('harder', 'softer',
    'faster', 'slower') only trigger a music shift when the user is genuinely adjusting music:
      - Tier 1A: User is replying directly to a song card ([Replying to song card: ...]).
      - Tier 1B: User explicitly mentions a music cue ('play something harder', 'faster song', 'slower track').
      - Tier 2: The IMMEDIATELY preceding assistant turn in history was a music song card,
        and the user's message is a short directive ('go harder', 'a bit slower', 'faster please').
    """
    if not user_text:
        return False

    shift = parse_spectrum_shift(user_text)
    if shift is None:
        return False

    # Tier 1A: Direct reply to a Discord song card
    if re.search(r"\[Replying to song card:", user_text, flags=re.IGNORECASE):
        return True

    cleaned = re.sub(r"^(?:User\s+.+?\s+says(?:\s*\[.*?\])*|.+\(User ID:\s*\d+\)(?:\s*\[.*?\])*)\s*:\s*", "", user_text.strip(), flags=re.DOTALL)
    cleaned = re.sub(r"\[Replying to:.*?\]", "", cleaned, flags=re.IGNORECASE).strip()

    # Tier 1B: Explicit music noun/verb in the prompt ("play something harder", "faster song", "go up a notch")
    if _EXPLICIT_MUSIC_CUE_RE.search(cleaned):
        clauses = [
            c.strip()
            for c in re.split(r"(?:[.!?;]+|\b(?:also|and\s+also|by\s+the\s+way|btw)\b)", cleaned, flags=re.IGNORECASE)
            if c and c.strip()
        ]
        if len(clauses) <= 1:
            return True
        if any(_EXPLICIT_MUSIC_CUE_RE.search(c) and _has_shift_keyword(c) for c in clauses):
            return True

    # Tier 2: Short directive immediately following a music turn in chat_history
    word_count = len(re.findall(r"\w+", cleaned))
    if word_count <= 10 and not cleaned.endswith("?") and history:
        prior_msgs = list(history[:-1]) if history else []
        for msg in reversed(prior_msgs):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            content = (msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")) or ""
            if role == "assistant" and content.strip():
                is_song_card = bool(
                    re.search(r"(?:\*\*|\b)Song:\*{0,2}\s*\S+", content, re.IGNORECASE)
                    and re.search(r"(?:\*\*|\b)Artist:\*{0,2}\s*\S+", content, re.IGNORECASE)
                )
                return is_song_card
            if role == "tool":
                t_name = msg.get("name", "") if isinstance(msg, dict) else getattr(msg, "name", "")
                if t_name == "skill_music_discovery":
                    return True

    return False


def step_spectrum_zone(
    current_zone: Optional[str],
    d_tempo: int,
    d_intensity: int,
) -> Dict[str, Any]:
    """
    Move 1 notch on the 3x3 Tempo x Intensity grid from `current_zone`
    (or from 'mid_mid' if `current_zone` is None or unknown).
    """
    used_default_start = False
    if not current_zone or current_zone not in COORDS_BY_ZONE:
        start_zone = DEFAULT_CENTER_ZONE
        used_default_start = True
    else:
        start_zone = current_zone

    start_t, start_i = COORDS_BY_ZONE[start_zone]
    new_t = max(0, min(2, start_t + d_tempo))
    new_i = max(0, min(2, start_i + d_intensity))

    target_zone_dict = ZONE_BY_COORDS[(new_t, new_i)]
    target_zone_name = target_zone_dict["ui_zone"]
    at_boundary = (new_t == start_t and new_i == start_i and not used_default_start)

    return {
        "start_zone": start_zone,
        "start_coords": (start_t, start_i),
        "target_zone": target_zone_dict,
        "target_zone_name": target_zone_name,
        "target_coords": (new_t, new_i),
        "used_default_start": used_default_start,
        "at_boundary": at_boundary,
    }

