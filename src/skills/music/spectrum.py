"""
3x3 Tempo x Intensity Music Spectrum Shift Engine.

Allows users to step 1 notch Harder/Softer (Intensity axis) or Faster/Slower (Tempo axis)
from the currently playing song's genre, or from the center zone ('mid_mid') when no song
is playing or its genre cannot be identified.
"""

import re
from typing import Any, Dict, List, Optional, Tuple, Union

TEMPO_AXIS: Tuple[str, str, str] = ("slow", "mid", "fast")
INTENSITY_AXIS: Tuple[str, str, str] = ("soft", "mid", "hard")

SPECTRUM_CHART: List[Dict[str, Any]] = [
    {
        "ui_zone": "slow_soft",
        "tempo": "slow",
        "intensity": "soft",
        "mainstream_tags": ["ambient", "downtempo", "modern classical", "chillout"],
        "deep_cut_tags": ["space ambient", "lowercase", "tape loops", "drone"],
        "intersection_modifiers": ["sleep", "relax", "piano", "minimalist"],
    },
    {
        "ui_zone": "mid_soft",
        "tempo": "mid",
        "intensity": "soft",
        "mainstream_tags": ["rnb", "neo-soul", "indie folk", "soft pop"],
        "deep_cut_tags": ["sophisti-pop", "bossanova", "quiet storm", "freak folk"],
        "intersection_modifiers": ["warm", "mellow", "intimate", "acoustic"],
    },
    {
        "ui_zone": "fast_soft",
        "tempo": "fast",
        "intensity": "soft",
        "mainstream_tags": ["liquid dnb", "dream pop", "shoegaze", "minimal techno"],
        "deep_cut_tags": ["microhouse", "ambient techno", "ethereal wave"],
        "intersection_modifiers": ["hypnotic", "floating", "atmospheric"],
    },
    {
        "ui_zone": "slow_mid",
        "tempo": "slow",
        "intensity": "mid",
        "mainstream_tags": ["trip-hop", "slowcore", "blues rock", "sadcore"],
        "deep_cut_tags": ["dark jazz", "desert rock", "dub", "slow motion"],
        "intersection_modifiers": ["melancholic", "moody", "groove"],
    },
    {
        "ui_zone": "mid_mid",
        "tempo": "mid",
        "intensity": "mid",
        "mainstream_tags": ["indie pop", "alt-rock", "synthpop", "pop"],
        "deep_cut_tags": ["jangle pop", "twee pop", "vaporwave", "city pop"],
        "intersection_modifiers": ["catchy", "upbeat", "radio"],
    },
    {
        "ui_zone": "fast_mid",
        "tempo": "fast",
        "intensity": "mid",
        "mainstream_tags": ["house", "nu-disco", "pop-punk", "electropop"],
        "deep_cut_tags": ["hyperpop", "italo-disco", "bubblegum bass"],
        "intersection_modifiers": ["dance", "party", "energetic"],
    },
    {
        "ui_zone": "slow_hard",
        "tempo": "slow",
        "intensity": "hard",
        "mainstream_tags": ["doom metal", "stoner rock", "industrial", "sludge"],
        "deep_cut_tags": ["drone metal", "death doom", "power electronics"],
        "intersection_modifiers": ["heavy", "dark", "brutal", "oppressive"],
    },
    {
        "ui_zone": "mid_hard",
        "tempo": "mid",
        "intensity": "hard",
        "mainstream_tags": ["grunge", "hard rock", "metalcore", "post-hardcore"],
        "deep_cut_tags": ["noise rock", "ebm", "industrial metal", "mathcore"],
        "intersection_modifiers": ["angry", "distorted", "raw", "aggressive"],
    },
    {
        "ui_zone": "fast_hard",
        "tempo": "fast",
        "intensity": "hard",
        "mainstream_tags": ["thrash metal", "drum and bass", "hardcore punk"],
        "deep_cut_tags": ["breakcore", "grindcore", "gabber", "hardstyle"],
        "intersection_modifiers": ["fast", "intense", "chaos", "frenzy"],
    },
]

DEFAULT_CENTER_ZONE = "mid_mid"

ZONE_BY_NAME: Dict[str, Dict[str, Any]] = {z["ui_zone"]: z for z in SPECTRUM_CHART}
COORDS_BY_ZONE: Dict[str, Tuple[int, int]] = {
    z["ui_zone"]: (TEMPO_AXIS.index(z["tempo"]), INTENSITY_AXIS.index(z["intensity"]))
    for z in SPECTRUM_CHART
}
ZONE_BY_COORDS: Dict[Tuple[int, int], Dict[str, Any]] = {
    coords: ZONE_BY_NAME[name] for name, coords in COORDS_BY_ZONE.items()
}

# Broad parent genre synonyms mapped onto the 9 spectrum cells so common labels from
# iTunes / Deezer / Last.fm (e.g. "Hip-Hop/Rap", "Techno", "Heavy Metal", "Jazz") resolve cleanly.
BROAD_GENRE_TO_ZONE: Dict[str, str] = {
    # slow_soft
    "classical": "slow_soft",
    "new age": "slow_soft",
    "meditation": "slow_soft",
    "soundtrack": "slow_soft",
    "instrumental": "slow_soft",
    "lofi": "slow_soft",
    "lo-fi": "slow_soft",
    "lo-fi beats": "slow_soft",
    # mid_soft
    "r&b": "mid_soft",
    "r&b/soul": "mid_soft",
    "soul": "mid_soft",
    "folk": "mid_soft",
    "acoustic": "mid_soft",
    "singer-songwriter": "mid_soft",
    "singer/songwriter": "mid_soft",
    "bossa nova": "mid_soft",
    "jazz": "mid_soft",
    "soft rock": "mid_soft",
    "country": "mid_soft",
    "easy listening": "mid_soft",
    # fast_soft
    "liquid funk": "fast_soft",
    "liquid drum and bass": "fast_soft",
    "intelligent drum and bass": "fast_soft",
    "chillstep": "fast_soft",
    "deep house": "fast_soft",
    "melodic techno": "fast_soft",
    "melodic house": "fast_soft",
    "progressive house": "fast_soft",
    # slow_mid
    "trip hop": "slow_mid",
    "blues": "slow_mid",
    "reggae": "slow_mid",
    "roots reggae": "slow_mid",
    "boom bap": "slow_mid",
    "g-funk": "slow_mid",
    "west coast hip hop": "slow_mid",
    "psychedelic rock": "slow_mid",
    # mid_mid
    "alternative rock": "mid_mid",
    "alternative": "mid_mid",
    "indie rock": "mid_mid",
    "indie": "mid_mid",
    "rock": "mid_mid",
    "classic rock": "mid_mid",
    "synthwave": "mid_mid",
    "retrowave": "mid_mid",
    "new wave": "mid_mid",
    "hip-hop/rap": "mid_mid",
    "hip-hop": "mid_mid",
    "hip hop": "mid_mid",
    "rap": "mid_mid",
    "funk": "mid_mid",
    # fast_mid
    "dance": "fast_mid",
    "edm": "fast_mid",
    "electronic": "fast_mid",
    "electronica": "fast_mid",
    "techno": "fast_mid",
    "tech house": "fast_mid",
    "trance": "fast_mid",
    "disco": "fast_mid",
    "electro": "fast_mid",
    "uk garage": "fast_mid",
    "pop punk": "fast_mid",
    "ska": "fast_mid",
    # slow_hard
    "sludge metal": "slow_hard",
    "stoner metal": "slow_hard",
    "dark electro": "slow_hard",
    "dark ambient": "slow_hard",
    "phonk": "slow_hard",
    "doom": "slow_hard",
    # mid_hard
    "metal": "mid_hard",
    "heavy metal": "mid_hard",
    "alternative metal": "mid_hard",
    "nu metal": "mid_hard",
    "progressive metal": "mid_hard",
    "deathcore": "mid_hard",
    "trap": "mid_hard",
    "drill": "mid_hard",
    "gangsta rap": "mid_hard",
    "dubstep": "mid_hard",
    "bass house": "mid_hard",
    "punk rock": "mid_hard",
    "punk": "mid_hard",
    # fast_hard
    "dnb": "fast_hard",
    "drum & bass": "fast_hard",
    "neurofunk": "fast_hard",
    "jump up": "fast_hard",
    "jungle": "fast_hard",
    "hard techno": "fast_hard",
    "schranz": "fast_hard",
    "industrial techno": "fast_hard",
    "speed metal": "fast_hard",
    "death metal": "fast_hard",
    "black metal": "fast_hard",
    "power metal": "fast_hard",
    "thrash": "fast_hard",
    "hardcore": "fast_hard",
}


# Per-channel active song context: {channel_id: {"song": str, "artist": str, "genre": Optional[str], "ui_zone": Optional[str]}}
_CHANNEL_LAST_PLAYED_SONG: Dict[int, Dict[str, Optional[str]]] = {}


def _get_channel_id(channel_id: Optional[int] = None) -> int:
    if channel_id is not None:
        return channel_id
    try:
        from src.skills.sports.live_tracker import get_active_channel_id
        return get_active_channel_id()
    except Exception:
        return 0


def _norm_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def record_channel_played_song(
    song: str,
    artist: str,
    genre: Optional[str] = None,
    ui_zone: Optional[str] = None,
    channel_id: Optional[int] = None,
) -> None:
    """Record the most recently served song in a Discord channel along with its genre and spectrum zone."""
    s_clean = (song or "").strip()
    a_clean = (artist or "").strip()
    if not s_clean or s_clean.lower() in ("unknown track", "unknown"):
        return

    cid = _get_channel_id(channel_id)
    prev = _CHANNEL_LAST_PLAYED_SONG.get(cid) or _CHANNEL_LAST_PLAYED_SONG.get(0) or {}
    same_track = (
        _norm_token(prev.get("song") or "") == _norm_token(s_clean)
        and _norm_token(prev.get("artist") or "") == _norm_token(a_clean)
    )

    eff_genre = (genre or "").strip() or (prev.get("genre") if same_track else None)
    eff_zone = ui_zone or (prev.get("ui_zone") if same_track else None)
    if not eff_zone and eff_genre:
        eff_zone = classify_genre_to_zone(eff_genre)

    entry = {
        "song": s_clean,
        "artist": a_clean,
        "genre": eff_genre,
        "ui_zone": eff_zone,
    }
    _CHANNEL_LAST_PLAYED_SONG[cid] = entry
    _CHANNEL_LAST_PLAYED_SONG[0] = entry


def get_channel_last_played_song(channel_id: Optional[int] = None) -> Optional[Dict[str, Optional[str]]]:
    """Retrieve the most recently served song in a Discord channel."""
    cid = _get_channel_id(channel_id)
    if cid in _CHANNEL_LAST_PLAYED_SONG:
        return dict(_CHANNEL_LAST_PLAYED_SONG[cid])
    if 0 in _CHANNEL_LAST_PLAYED_SONG:
        return dict(_CHANNEL_LAST_PLAYED_SONG[0])
    return None


def clear_channel_played_songs() -> None:
    """Clear per-channel active song state (used on reset/tests)."""
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
        z_name = zone["ui_zone"]
        for tag in zone["mainstream_tags"] + zone["deep_cut_tags"]:
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
            for mod in zone["intersection_modifiers"]:
                if re.search(rf"\b{re.escape(mod.lower())}\b", cand):
                    return zone["ui_zone"]

    return None


# ---------------------------------------------------------------------------
# Conversational Disambiguation & Shift Parsing
# ---------------------------------------------------------------------------

# Non-music domain terms that must NEVER be treated as a music spectrum shift
_NON_MUSIC_DOMAIN_RE = re.compile(
    r"\b(?:"
    r"gpu|gpus|cpu|cpus|ram|vram|fps|hz|mhz|ghz|rtx|gtx|radeon|nvidia|amd|intel|apple|m[1-4]|"
    r"pc|laptop|phone|iphone|android|wifi|internet|download|upload|ping|latency|server|browser|"
    r"python|javascript|typescript|rust|c\+\+|java|golang|sql|code|coding|script|compiler|algorithm|"
    r"llm|llms|model|models|qwen|gemma|llama|claude|gpt|chatgpt|gemini|benchmark|benchmarks|tokens?|"
    r"f1|formula\s*1|tire|tires|tyre|tyres|compound|compounds|lap|laps|pit\s*stop|qualifying|verstappen|hamilton|leclerc|norris|"
    r"soccer|football|nba|nhl|nfl|mlb|ufc|ucl|epl|laliga|match|matches|fixture|fixtures|schedule|standings|roster|score|goal|goals|"
    r"exam|exams|test|tests|homework|class|school|job|work|salary|tax|taxes|price|prices|stock|crypto|"
    r"boss|level|levels|quest|difficulty|hitbox|nerf|buff|patch"
    r")\b",
    re.IGNORECASE,
)

# Comparative question / factual comparison patterns ("faster than", "why is X slower", "is it harder to")
_COMPARATIVE_QUESTION_RE = re.compile(
    r"(?:"
    r"\b(?:faster|slower|harder|softer|heavier|lighter)\s+(?:than|to|for|at|on|in\s+the)\b|"
    r"\b(?:why|how\s+come|which\s+one|who)\s+(?:is|are|was|were|runs?|goes?)\b.*\b(?:faster|slower|harder|softer)\b|"
    r"^\s*(?:is|are|was|were|do|does|did|can|could|would|should)\s+(?:it|this|that|they|he|she)\b.*\b(?:faster|slower|harder|softer)\b|"
    r"\b(?:work|study|try|push|hit|train|play\s+the\s+game)\s+harder\b|"
    r"\b(?:easier\s+or\s+harder|harder\s+or\s+easier|faster\s+or\s+slower|slower\s+or\s+faster)\b"
    r")",
    re.IGNORECASE,
)

_EXPLICIT_MUSIC_CUE_RE = re.compile(
    r"\b(?:"
    r"play|song|songs|track|tracks|music|tune|tunes|vibe|vibes|beat|beats|bpm|tempo|"
    r"genre|queue|spin|jam|jams|melody|sound|sounds|listen|listening|something|somethin|anything|"
    r"notch|dial"
    r")\b",
    re.IGNORECASE,
)

_HARDER_RE = re.compile(
    r"\b(?:harder|heavier|more\s+intense|more\s+aggressive|more\s+brutal|up\s+a\s+notch|turn\s+it\s+up|crank\s+it\s+up|too\s+soft)\b",
    re.IGNORECASE,
)
_SOFTER_RE = re.compile(
    r"\b(?:softer|lighter|gentler|mellower|calmer|more\s+chill|less\s+intense|less\s+heavy|less\s+aggressive|down\s+a\s+notch|take\s+it\s+down|tone\s+it\s+down|too\s+hard|too\s+heavy|too\s+loud)\b",
    re.IGNORECASE,
)
_FASTER_RE = re.compile(
    r"\b(?:faster|quicker|speed\s+it\s+up|speed\s+up|higher\s+tempo|more\s+upbeat|higher\s+bpm|pick\s+up\s+the\s+pace|too\s+slow)\b",
    re.IGNORECASE,
)
_SLOWER_RE = re.compile(
    r"\b(?:slower|slow\s+it\s+down|slow\s+down|lower\s+tempo|lower\s+bpm|bring\s+the\s+tempo\s+down|too\s+fast)\b",
    re.IGNORECASE,
)


def has_non_music_domain_context(text: str) -> bool:
    """Return True if the text contains non-music domain keywords or comparative factual questions."""
    if not text:
        return False
    # Strip any [Replying to song card: ...] wrapper before checking domain keywords
    cleaned = re.sub(r"\[Replying to song card:.*?\]", "", text, flags=re.IGNORECASE).strip()
    if _NON_MUSIC_DOMAIN_RE.search(cleaned):
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
