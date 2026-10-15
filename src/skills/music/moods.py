import re
from typing import Optional

# Canonical mood/vibe families and their trigger keywords/phrases
MOOD_ALIASES: dict[str, set[str]] = {
    "banger": {
        "banger", "bangers", "hype", "hyped", "party", "club", "energetic",
        "high energy", "upbeat", "anthem", "anthems", "peak time", "rave",
        "turnt", "fire", "lit", "festival"
    },
    "workout": {
        "workout", "gym", "running", "cardio", "training", "pump", "lifting", "fitness"
    },
    "melodic": {
        "melodic", "euphoric", "uplifting", "emotional", "dreamy", "atmospheric",
        "beautiful", "vocal", "liquid", "ethereal", "soaring", "melody"
    },
    "hard": {
        "hard", "heavy", "aggressive", "brutal", "industrial",
        "filthy", "dirty", "intense", "rage", "mosh", "hardcore"
    },
    "dark": {
        "dark", "darker", "gritty", "underground", "hypnotic", "ominous", "shadow"
    },
    "chill": {
        "chill", "chillout", "chilled", "relaxed", "relaxing", "calm", "mellow",
        "smooth", "lofi", "lo-fi", "ambient", "downtempo", "study", "late night",
        "cozy", "easy", "laid back", "laidback"
    },
    "sad": {
        "sad", "melancholic", "melancholy", "heartbreak", "depressing", "crying",
        "lonely", "somber", "bittersweet", "tearjerker"
    },
    "love": {
        "love", "romantic", "romance", "slow jam", "slow jams", "sensual",
        "intimate", "crush", "valentine", "affection", "sweet", "ballad", "ballads"
    },
}

# Flat lookup from alias token/phrase -> canonical mood
ALIAS_TO_MOOD: dict[str, str] = {}
for _canon, _words in MOOD_ALIASES.items():
    for _w in _words:
        ALIAS_TO_MOOD[_w] = _canon

# Standalone multi-genre profiles when no specific genre is requested
STANDALONE_MOOD_PROFILES: dict[str, dict] = {
    "banger": {
        "deezer_queries": [
            "Festival Bangers",
            "Club Bangers",
            "Rap Bangers",
            "Drum and Bass Bangers",
            "Dance Anthems",
            "Rock Anthems",
        ],
        "lastfm_tags": ["banger", "party", "dance", "club", "hip-hop"],
        "beatport_query": "Beatport Hype 100",
    },
    "workout": {
        "deezer_queries": [
            "Workout Bangers",
            "Gym Drum & Bass",
            "Workout Hip Hop",
            "Gym Hardstyle",
            "Rock Workout",
        ],
        "lastfm_tags": ["workout", "energetic", "high energy", "drum and bass", "hip-hop"],
        "beatport_query": "Beatport Top 100",
    },
    "melodic": {
        "deezer_queries": [
            "Melodic House & Techno",
            "Liquid Drum & Bass",
            "Progressive House Melodic",
            "Dream Pop Melodic Indie",
            "Melodic Bass",
        ],
        "lastfm_tags": ["melodic", "melodic techno", "liquid funk", "synthwave", "dream pop"],
        "beatport_query": "Beatport Top 100 Melodic House & Techno",
    },
    "hard": {
        "deezer_queries": [
            "Hard Techno",
            "Neurofunk Drum & Bass",
            "Hardstyle Bangers",
            "Heavy Metal Workout",
            "Bass House & Dubstep",
        ],
        "lastfm_tags": ["hard techno", "neurofunk", "hardstyle", "phonk", "heavy metal", "industrial"],
        "beatport_query": "Beatport Top 100 Hard Techno",
    },
    "dark": {
        "deezer_queries": [
            "Dark Techno",
            "Neurofunk Drum & Bass",
            "Dark Industrial",
        ],
        "lastfm_tags": ["dark", "darkwave", "darksynth", "phonk", "industrial", "neurofunk", "dark ambient"],
        "beatport_query": "Beatport Top 100 Techno",
    },
    "chill": {
        "deezer_queries": [
            "Chill Vibes",
            "Lo-Fi Beats",
            "Chill House",
            "Liquid Drum & Bass Chill",
            "Acoustic Chill Indie",
            "Downtempo Lounge",
        ],
        "lastfm_tags": ["chillout", "lo-fi", "downtempo", "chill", "ambient"],
        "beatport_query": "Beatport Top 100 Deep House",
    },
    "sad": {
        "deezer_queries": [
            "Sad Songs",
            "Melancholic Indie",
            "Sad Rap & R&B",
            "Heartbreak Acoustic",
            "Emotional Piano & Strings",
        ],
        "lastfm_tags": ["sad", "melancholy", "heartbreak", "indie folk", "acoustic"],
        "beatport_query": None,
    },
    "love": {
        "deezer_queries": [
            "Love Songs",
            "R&B Slow Jams",
            "Romantic Pop Ballads",
            "Acoustic Love Songs",
            "Indie Love Songs",
            "Classic Love Songs",
        ],
        "lastfm_tags": ["love", "romantic", "love songs", "slow jams", "rnb"],
        "beatport_query": None,
    },
}

GENRE_CANONICAL_MAP: dict[str, str] = {
    "dnb": "dnb",
    "drum and bass": "dnb",
    "drumnbass": "dnb",
    "drum & bass": "dnb",
    "jungle": "dnb",
    "techno": "techno",
    "house": "house",
    "tech house": "house",
    "deep house": "house",
    "trance": "trance",
    "dubstep": "dubstep",
    "electronic": "electronic",
    "edm": "electronic",
    "dance": "electronic",
    "rap": "rap",
    "hip hop": "rap",
    "hip-hop": "rap",
    "hiphop": "rap",
    "trap": "rap",
    "rnb": "rnb",
    "r&b": "rnb",
    "r and b": "rnb",
    "soul": "rnb",
    "rock": "rock",
    "indie": "rock",
    "alternative": "rock",
    "metal": "metal",
    "pop": "pop",
    "country": "country",
}

# Specialized (canonical_genre, canonical_mood) subgenre combinations
GENRE_MOOD_MATRIX: dict[tuple[str, str], dict] = {
    ("dnb", "melodic"): {
        "deezer_queries": ["Liquid Drum & Bass", "Melodic Drum and Bass"],
        "lastfm_tags": ["liquid funk", "liquid dnb", "drum and bass"],
        "beatport_query": "Beatport Top 100 Drum & Bass",
    },
    ("dnb", "chill"): {
        "deezer_queries": ["Liquid Drum & Bass", "Chill Drum and Bass"],
        "lastfm_tags": ["liquid funk", "intelligent drum and bass"],
        "beatport_query": None,
    },
    ("dnb", "hard"): {
        "deezer_queries": ["Neurofunk Drum & Bass", "Jump Up Drum & Bass", "Dark Drum and Bass"],
        "lastfm_tags": ["neurofunk", "darkstep", "jump up"],
        "beatport_query": "Beatport Top 100 Drum & Bass",
    },
    ("dnb", "dark"): {
        "deezer_queries": ["Neurofunk Drum & Bass", "Dark Drum and Bass"],
        "lastfm_tags": ["neurofunk", "darkstep", "techstep"],
        "beatport_query": "Beatport Top 100 Drum & Bass",
    },
    ("dnb", "banger"): {
        "deezer_queries": ["Drum & Bass Bangers", "UKF Drum & Bass", "Dancefloor Drum & Bass"],
        "lastfm_tags": ["drum and bass", "dnb"],
        "beatport_query": "Beatport Top 100 Drum & Bass",
    },
    ("dnb", "workout"): {
        "deezer_queries": ["Gym Drum & Bass", "Workout Drum and Bass", "Drum & Bass Bangers"],
        "lastfm_tags": ["drum and bass", "dnb"],
        "beatport_query": "Beatport Top 100 Drum & Bass",
    },
    ("techno", "melodic"): {
        "deezer_queries": ["Melodic House & Techno", "Melodic Techno"],
        "lastfm_tags": ["melodic techno", "progressive house"],
        "beatport_query": "Beatport Top 100 Melodic House & Techno",
    },
    ("techno", "hard"): {
        "deezer_queries": ["Hard Techno", "Schranz", "Industrial Techno"],
        "lastfm_tags": ["hard techno", "industrial techno", "schranz"],
        "beatport_query": "Beatport Top 100 Hard Techno",
    },
    ("techno", "dark"): {
        "deezer_queries": ["Dark Techno", "Raw Hypnotic Techno", "Industrial Techno"],
        "lastfm_tags": ["dark techno", "minimal techno", "industrial techno"],
        "beatport_query": "Beatport Top 100 Techno",
    },
    ("techno", "banger"): {
        "deezer_queries": ["Peak Time Techno", "Rave Techno", "Techno Bangers"],
        "lastfm_tags": ["techno", "acid techno"],
        "beatport_query": "Beatport Top 100 Techno",
    },
    ("house", "melodic"): {
        "deezer_queries": ["Melodic House & Techno", "Progressive House"],
        "lastfm_tags": ["progressive house", "melodic house"],
        "beatport_query": "Beatport Top 100 Melodic House & Techno",
    },
    ("house", "chill"): {
        "deezer_queries": ["Chill House", "Deep House", "Lounge House"],
        "lastfm_tags": ["deep house", "chill house"],
        "beatport_query": "Beatport Top 100 Deep House",
    },
    ("house", "banger"): {
        "deezer_queries": ["Tech House Bangers", "Bass House", "Club House Anthems"],
        "lastfm_tags": ["tech house", "bass house", "house"],
        "beatport_query": "Beatport Top 100 Tech House",
    },
    ("house", "hard"): {
        "deezer_queries": ["Bass House", "Electro House Bangers", "Tech House"],
        "lastfm_tags": ["bass house", "electro house"],
        "beatport_query": "Beatport Top 100 Tech House",
    },
    ("rap", "banger"): {
        "deezer_queries": ["Rap Bangers", "Trap Bangers", "Hip Hop Club Hits"],
        "lastfm_tags": ["rap", "trap", "hip-hop"],
        "beatport_query": None,
    },
    ("rap", "hard"): {
        "deezer_queries": ["Hard Trap", "Drill Bangers", "Aggressive Rap"],
        "lastfm_tags": ["trap", "drill", "hardcore hip-hop"],
        "beatport_query": None,
    },
    ("rap", "melodic"): {
        "deezer_queries": ["Melodic Rap", "Emotional Rap", "Melodic Trap"],
        "lastfm_tags": ["melodic rap", "cloud rap", "hip-hop"],
        "beatport_query": None,
    },
    ("rap", "chill"): {
        "deezer_queries": ["Chill Hip Hop", "Lo-Fi Hip Hop", "Jazz Rap"],
        "lastfm_tags": ["chillhop", "jazz hop", "lo-fi"],
        "beatport_query": None,
    },
    ("rap", "sad"): {
        "deezer_queries": ["Sad Rap", "Emo Rap", "Heartbreak Hip Hop"],
        "lastfm_tags": ["emo rap", "sad rap"],
        "beatport_query": None,
    },
    ("rap", "love"): {
        "deezer_queries": ["Hip Hop Love Songs", "R&B & Hip Hop Slow Jams"],
        "lastfm_tags": ["hip-hop", "rnb", "slow jams"],
        "beatport_query": None,
    },
    ("rnb", "love"): {
        "deezer_queries": ["R&B Love Songs", "R&B Slow Jams", "Romantic R&B"],
        "lastfm_tags": ["rnb", "slow jams", "contemporary rnb"],
        "beatport_query": None,
    },
    ("rnb", "chill"): {
        "deezer_queries": ["Chill R&B", "Late Night R&B", "Neo Soul Chill"],
        "lastfm_tags": ["neo-soul", "rnb", "alternative rnb"],
        "beatport_query": None,
    },
    ("rnb", "sad"): {
        "deezer_queries": ["Heartbreak R&B", "Sad R&B", "Emotional R&B"],
        "lastfm_tags": ["rnb", "heartbreak", "soul"],
        "beatport_query": None,
    },
    ("rock", "banger"): {
        "deezer_queries": ["Rock Anthems", "Stadium Rock Bangers", "Party Rock"],
        "lastfm_tags": ["rock", "hard rock", "alternative rock"],
        "beatport_query": None,
    },
    ("rock", "hard"): {
        "deezer_queries": ["Hard Rock", "Heavy Metal Bangers", "Nu Metal"],
        "lastfm_tags": ["hard rock", "heavy metal", "nu metal"],
        "beatport_query": None,
    },
    ("rock", "melodic"): {
        "deezer_queries": ["Melodic Rock", "Dream Pop Indie", "Post-Rock"],
        "lastfm_tags": ["indie rock", "dream pop", "post-rock"],
        "beatport_query": None,
    },
    ("rock", "love"): {
        "deezer_queries": ["Rock Love Ballads", "Power Ballads", "Indie Love Songs"],
        "lastfm_tags": ["power ballad", "soft rock", "indie"],
        "beatport_query": None,
    },
    ("pop", "love"): {
        "deezer_queries": ["Pop Love Songs", "Romantic Pop Hits", "Pop Ballads"],
        "lastfm_tags": ["pop", "love songs", "romantic"],
        "beatport_query": None,
    },
    ("pop", "banger"): {
        "deezer_queries": ["Pop Bangers", "Dance Pop Hits", "Party Pop Anthems"],
        "lastfm_tags": ["dance-pop", "electropop", "pop"],
        "beatport_query": None,
    },
}

_FILLER_WORDS = {
    "play", "me", "a", "an", "some", "something", "song", "songs",
    "track", "tracks", "music", "vibe", "vibes", "mood", "please", "the", "of"
}


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

