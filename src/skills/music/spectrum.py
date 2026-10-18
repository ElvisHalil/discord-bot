"""
3x3 Tempo x Intensity Music Spectrum Shift Engine.

Allows users to step 1 notch Harder/Softer (Intensity axis) or Faster/Slower (Tempo axis)
from the currently playing song's genre, or from the center zone ('mid_mid') when no song
is playing or its genre cannot be identified.
"""

import re
from typing import Any, Dict, List, Optional, Tuple, Union

from src.context import get_active_channel_id
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

# Per-channel active song context: {channel_id: PlayedSongRecord}
_CHANNEL_LAST_PLAYED_SONG: Dict[int, PlayedSongRecord] = {}


def _get_channel_id(channel_id: Optional[int] = None) -> int:
    if channel_id is not None:
        return channel_id
    return get_active_channel_id()


def _norm_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def record_channel_played_song(
    song: str,
    artist: str,
    genre: Optional[str] = None,
    ui_zone: Optional[str] = None,
    channel_id: Optional[int] = None,
    alt_song: Optional[str] = None,
    bpm: Optional[float] = None,
    speed_score: Optional[float] = None,
    intensity_score: Optional[float] = None,
    gain_db: Optional[float] = None,
) -> None:
    """Record the most recently served song in a channel along with its genre, spectrum zone, and acoustic metrics."""
    s_clean = (song or "").strip()
    a_clean = (artist or "").strip()
    alt_clean = (alt_song or "").strip() or None
    if not s_clean or s_clean.lower() in ("unknown track", "unknown"):
        return

    cid = _get_channel_id(channel_id)
    prev = _CHANNEL_LAST_PLAYED_SONG.get(cid)
    norm_s = _norm_token(s_clean)
    norm_alt = _norm_token(alt_clean or "")
    prev_s = _norm_token(prev.song if prev else "")
    prev_alt = _norm_token(prev.alt_song if prev and prev.alt_song else "")
    same_track = bool(
        prev
        and (norm_s in (prev_s, prev_alt) or (norm_alt and norm_alt in (prev_s, prev_alt)))
        and _norm_token(prev.artist) == _norm_token(a_clean)
    )

    eff_genre = (genre or "").strip() or (prev.genre if same_track and prev else None)
    eff_zone = ui_zone or (prev.ui_zone if same_track and prev else None)
    if not eff_zone and eff_genre:
        eff_zone = classify_genre_to_zone(eff_genre)

    entry = PlayedSongRecord(
        song=s_clean,
        alt_song=alt_clean or (prev.alt_song if same_track and prev else None),
        artist=a_clean,
        genre=eff_genre,
        ui_zone=eff_zone,
        bpm=bpm if bpm is not None else (prev.bpm if same_track and prev else None),
        speed_score=speed_score if speed_score is not None else (prev.speed_score if same_track and prev else None),
        intensity_score=intensity_score if intensity_score is not None else (prev.intensity_score if same_track and prev else None),
        gain_db=gain_db if gain_db is not None else (prev.gain_db if same_track and prev else None),
    )
    _CHANNEL_LAST_PLAYED_SONG[cid] = entry


def get_channel_last_played_song(channel_id: Optional[int] = None) -> Optional[PlayedSongRecord]:
    """Retrieve the most recently served song in a specific channel."""
    cid = _get_channel_id(channel_id)
    return _CHANNEL_LAST_PLAYED_SONG.get(cid)


def clear_channel_played_songs(channel_id: Optional[int] = None) -> None:
    """Clear per-channel active song state (for one channel if channel_id is given, else all)."""
    if channel_id is not None:
        _CHANNEL_LAST_PLAYED_SONG.pop(channel_id, None)
    else:
        _CHANNEL_LAST_PLAYED_SONG.clear()


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


# ---------------------------------------------------------------------------
# Conversational Disambiguation & Shift Parsing
# ---------------------------------------------------------------------------

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
    # Strip any [Replying to song card: ...] wrapper before checking domain keywords
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
        # Find the most recent assistant message before the current user turn
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
                if t_name == "skill_top_music":
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


_REPLY_SONG_CARD_RE = re.compile(
    r"\[Replying to song card:\s*'([^']+)'\s*by\s*'([^']+)'\]",
    re.IGNORECASE,
)


def _extract_raw_user_text(msg) -> str:
    """Strip Discord/Telegram wrapper prefixes ('User X says: ...') from a user message in history."""
    content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
    if not content:
        return ""
    text = str(content).strip()
    m = re.match(
        r"^(?:User\s+.+?\s+says(?:\s*\[.*?\])*|.+\(User ID:\s*\d+\)(?:\s*\[.*?\])*)\s*:\s*(.+)$",
        text,
        flags=re.DOTALL,
    )
    return m.group(1).strip() if m else text


def enrich_music_tool_args(args: dict, history: list) -> dict:
    """Enrich skill_top_music arguments with song-card reply seeds and validate 3-Tier
    music spectrum shift context so comparative words in normal conversation are never treated as shifts."""
    if not isinstance(args, dict) or not history:
        return args

    user_msgs = [
        m for m in history
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    ]
    if not user_msgs:
        return args

    last_msg = user_msgs[-1]
    raw_content = str(last_msg.get("content", "") if isinstance(last_msg, dict) else getattr(last_msg, "content", "") or "")
    clean_user = _extract_raw_user_text(last_msg)
    if not clean_user or clean_user.startswith("Now summarize") or clean_user.startswith("You have used"):
        return args

    enriched = dict(args)

    # Check 3-Tier Music Spectrum Shift context disambiguation
    valid_shift_ctx = is_valid_music_shift_context(raw_content, history)
    user_shift = parse_spectrum_shift(clean_user)
    arg_shift = (
        parse_spectrum_shift(str(enriched.get("shift") or ""))
        or parse_spectrum_shift(str(enriched.get("mood") or ""))
    )

    # 1. If the user is replying to a song card and didn't specify a seed song or artist, attach it
    # (unless they are explicitly switching to a specific genre without asking for similarity/shift)
    m_reply = _REPLY_SONG_CARD_RE.search(raw_content)
    if m_reply and not enriched.get("similar_to") and not enriched.get("artist"):
        wants_card_similarity = bool(
            not enriched.get("genre")
            or enriched.get("shift")
            or user_shift is not None
            or re.search(r"\b(?:similar|like\s+this|like\s+that|same\s+vibe|another\s+one)\b", clean_user, re.IGNORECASE)
        )
        if wants_card_similarity:
            enriched["similar_to"] = f"{m_reply.group(1).strip()} by {m_reply.group(2).strip()}"

    # 2. Apply or sanitize spectrum shift parameter
    if valid_shift_ctx and user_shift is not None:
        if not enriched.get("shift") and not enriched.get("genre") and not enriched.get("artist"):
            enriched["shift"] = clean_user
    elif (user_shift is not None or arg_shift is not None) and not valid_shift_ctx:
        has_explicit_music_request = bool(
            enriched.get("genre")
            or enriched.get("artist")
            or enriched.get("similar_to")
            or (enriched.get("mood") and not parse_spectrum_shift(str(enriched.get("mood") or "")))
            or _EXPLICIT_MUSIC_CUE_RE.search(clean_user)
        )
        if has_explicit_music_request:
            enriched.pop("shift", None)
        elif has_non_music_domain_context(clean_user) or not enriched.get("genre"):
            enriched["shift"] = f"NON_MUSIC_CONVERSATION: {clean_user}"

    return enriched


def detect_missed_music_shift_tool_call(history: list) -> Optional[Tuple[str, dict]]:
    """Detect when an 8B model skips calling skill_top_music on a valid 3-Tier music spectrum shift
    (e.g. replying 'faster' to a song card or typing 'go harder' right after a song card)."""
    if not history:
        return None

    user_msgs = [
        m for m in history
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    ]
    if not user_msgs:
        return None

    last_msg = user_msgs[-1]
    raw_content = str(last_msg.get("content", "") if isinstance(last_msg, dict) else getattr(last_msg, "content", "") or "")
    clean_user = _extract_raw_user_text(last_msg)
    if not clean_user or clean_user.startswith("!") or clean_user.startswith("Now summarize") or clean_user.startswith("You have used"):
        return None

    if is_valid_music_shift_context(raw_content, history):
        args = enrich_music_tool_args({"shift": clean_user}, history)
        return ("skill_top_music", args)

    return None

