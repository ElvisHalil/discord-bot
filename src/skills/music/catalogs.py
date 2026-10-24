"""
Static music genre, region, chart, mood matrix, 3x3 spectrum grid, and filter catalogs
for the music skill package.
"""

from typing import Dict, List, Tuple

from src.skills.music.enums import (
    CanonicalGenre,
    IntensityLevel,
    MoodCategory,
    SpectrumZoneId,
    TempoLevel,
)
from src.skills.music.models import (
    MoodProfile,
    RegionalGenreConfig,
    SpectrumZone,
)

# ---------------------------------------------------------------------------
# 1. Chart, Region & Genre Catalogs
# ---------------------------------------------------------------------------

GENRE_MAP: Dict[str, int] = {
    "pop": 14,
    "rock": 21,
    "country": 6,
    "countrymusic": 6,
    "hiphop": 18,
    "rap": 18,
    "hiphoprap": 18,
    "drill": 18,
    "grime": 18,
    "trap": 18,
    "randb": 15,
    "rnb": 15,
    "rb": 15,
    "soul": 15,
    "rbsoul": 15,
    "funk": 15,
    "dance": 17,
    "electronic": 7,
    "edm": 7,
    "jazz": 11,
    "alternative": 20,
    "indie": 20,
    "punk": 20,
    "christian": 22,
    "gospel": 22,
    "kpop": 50,
    "jpop": 27,
    "anime": 29,
    "latino": 12,
    "latin": 12,
    "reggaeton": 12,
    "salsa": 12,
    "classical": 5,
    "blues": 2,
    "reggae": 24,
    "soundtrack": 16,
    "dubstep": 100014,
    "bass": 100015,
    "drumandbass": 1049,
    "drumnbass": 1049,
    "dnb": 1049,
    "jungle": 1049,
    "house": 1048,
    "techhouse": 1048,
    "deephouse": 1048,
    "techno": 1050,
    "trance": 1051,
    "afrohouse": 100049,
    "afrobeats": 19,
    "world": 19,
    "metal": 1153,
    "heavymetal": 1153,
    "deathmetal": 1149,
    "blackmetal": 1149,
    "hairmetal": 1151,
    "christianmetal": 1095,
    "Minimalism": 1767,
    "minimalism": 1767,
}

REGION_MODIFIERS: Dict[str, str] = {
    "german": "de",
    "deutsch": "de",
    "germany": "de",
    "uk": "gb",
    "british": "gb",
    "english": "gb",
    "french": "fr",
    "france": "fr",
    "japanese": "jp",
    "japan": "jp",
    "korean": "kr",
    "korea": "kr",
    "spanish": "es",
    "spain": "es",
    "mexican": "mx",
    "mexico": "mx",
    "brazilian": "br",
    "brazil": "br",
    "italian": "it",
    "italy": "it",
    "swedish": "se",
    "sweden": "se",
    "dutch": "nl",
    "netherlands": "nl",
    "canadian": "ca",
    "canada": "ca",
    "australian": "au",
    "australia": "au",
    "turkish": "tr",
    "turkey": "tr",
    "indian": "in",
    "india": "in",
    "nigerian": "za",
    "southafrican": "za",
    "american": "us",
    "us": "us",
}

COMPOUND_REGIONAL_GENRES: Dict[str, RegionalGenreConfig] = {
    "deutschrap": RegionalGenreConfig("de", 18),
    "jpop": RegionalGenreConfig("jp", 27),
    "jrock": RegionalGenreConfig("jp", 21),
    "kpop": RegionalGenreConfig("kr", 50),
    "krap": RegionalGenreConfig("kr", 18),
    "khiphop": RegionalGenreConfig("kr", 18),
    "khop": RegionalGenreConfig("kr", 18),
    "ukdrill": RegionalGenreConfig("gb", 18),
    "ukgrime": RegionalGenreConfig("gb", 18),
    "ukgarage": RegionalGenreConfig("gb", 17),
    "ukg": RegionalGenreConfig("gb", 17),
    "frenchhouse": RegionalGenreConfig("fr", 1048),
    "frenchtouch": RegionalGenreConfig("fr", 7),
    "bailefunk": RegionalGenreConfig("br", 17),
    "brazilianfunk": RegionalGenreConfig("br", 17),
    "bollywood": RegionalGenreConfig("in", 14),
    "punjabi": RegionalGenreConfig("in", 14),
    "afrobeats": RegionalGenreConfig("za", 19),
    "amapiano": RegionalGenreConfig("za", 100049),
}

GENRE_REGION_MAP: Dict[str, List[str]] = {
    "dnb": ["gb", "us", "de"],
    "drumandbass": ["gb", "us", "de"],
    "drumnbass": ["gb", "us", "de"],
    "jungle": ["gb", "us"],
    "dubstep": ["gb", "us"],
    "house": ["gb", "us", "de", "nl"],
    "techhouse": ["gb", "us", "de", "es"],
    "deephouse": ["gb", "de", "nl", "za"],
    "dance": ["gb", "us", "de"],
    "electronic": ["gb", "de", "us", "fr"],
    "edm": ["us", "gb", "nl", "de"],
    "techno": ["de", "gb", "nl", "us"],
    "trance": ["nl", "de", "gb", "us"],
    "metal": ["de", "gb", "se", "us"],
    "heavymetal": ["de", "gb", "se", "us"],
    "deathmetal": ["se", "de", "us", "gb"],
    "blackmetal": ["se", "de", "gb"],
    "kpop": ["kr", "jp", "us"],
    "jpop": ["jp", "us"],
    "latin": ["mx", "es", "br", "us"],
    "latino": ["mx", "es", "br", "us"],
    "reggaeton": ["mx", "es", "us"],
    "afrohouse": ["za", "gb", "fr"],
}

BEATPORT_GENRE_QUERIES: Dict[str, str] = {
    "techno": "Beatport Top 100 Techno",
    "driving techno": "Beatport Top 100 Techno",
    "peak time techno": "Beatport Top 100 Techno",
    "acid techno": "Beatport Acid Techno",
    "industrial techno": "Beatport Top 100 Hard Techno",
    "raw techno": "Beatport Raw Deep Hypnotic Techno",
    "hypnotic techno": "Beatport Raw Deep Hypnotic Techno",
    "deep techno": "Beatport Raw Deep Hypnotic Techno",
    "dub techno": "Beatport Dub Techno",
    "schranz": "Beatport Top 100 Hard Techno",
    "melodic techno": "Beatport Top 100 Melodic House & Techno",
    "melodic house": "Beatport Top 100 Melodic House & Techno",
    "hard techno": "Beatport Top 100 Hard Techno",
    "dnb": "Beatport Top 100 Drum & Bass",
    "drum and bass": "Beatport Top 100 Drum & Bass",
    "drumnbass": "Beatport Top 100 Drum & Bass",
    "drum & bass": "Beatport Top 100 Drum & Bass",
    "liquid dnb": "Beatport Liquid Drum & Bass",
    "liquid drum and bass": "Beatport Liquid Drum & Bass",
    "neurofunk": "Beatport Neurofunk Drum & Bass",
    "jump up": "Beatport Jump Up Drum & Bass",
    "rollers": "Beatport Top 100 Drum & Bass",
    "jungle": "Beatport Top 100 Drum & Bass",
    "house": "Beatport Top 100 House",
    "tech house": "Beatport Top 100 Tech House",
    "deep house": "Beatport Top 100 Deep House",
    "afro house": "Beatport Top 100 Afro House",
    "afrohouse": "Beatport Top 100 Afro House",
    "progressive house": "Beatport Top 100 Progressive House",
    "uk garage": "Beatport Top 100 UK Garage",
    "ukg": "Beatport Top 100 UK Garage",
    "bassline": "Beatport Top 100 UK Garage",
    "breaks": "Beatport Top 100 Breaks",
    "breakbeat": "Beatport Top 100 Breaks",
    "hardstyle": "Beatport Hard Dance",
    "hardcore": "Beatport Hard Dance",
    "trance": "Beatport Top 100 Trance",
    "psytrance": "Beatport Top 100 Psy-Trance",
    "dubstep": "Beatport Top 100 Dubstep",
    "bass": "Beatport Top 100 Bass",
    "electro": "Beatport Top 100 Electro",
    "minimal": "Beatport Top 100 Minimal Deep Tech",
    "electronic": "Beatport Top 100",
    "edm": "Beatport Top 100",
    "dance": "Beatport Top 100",
    "hype": "Beatport Hype 100",
}

GARBAGE_MUSIC_KEYWORDS: Tuple[str, ...] = (
    "tutorial", "how to play", "how to make", "reaction", "reacting",
    "reacts", "review", "podcast", "interview", "behind the scenes",
    "gameplay", "walkthrough", "tier list", "parody", "lesson",
    "drum cover", "synth tutorial", "unboxing", "vlog", "prank",
    "meme", "tiktok compilation", "nightcore", "sped up", "speed up",
    "slowed", "super slowed", "ultra slowed", "vocal coach", "reverb",
    "reverbed", "top 50", "top 100", "top 20", "top 10",
    "top 25", "most viral", "compilation", "playlist", "best of",
    "1 hour", "10 hours", "full album", "tracklist", "track list",
    "full set", "dj set", "live set", "club set", "festival set",
    "b2b set", "vinyl set", "radio set", "closing set", "opening set",
    "extended set", "boiler room", "continuous mix", "megamix",
    "hour mix", "min mix", "guest mix", "promo mix", "yearmix",
    "bootleg", "ai cover", "ai version", "type beat", "karaoke",
)

# ---------------------------------------------------------------------------
# 2. Mood & Vibe Profiles & Genre x Mood Matrix
# ---------------------------------------------------------------------------

MOOD_FILLER_WORDS: frozenset[str] = frozenset({
    "play", "me", "a", "an", "some", "something", "song", "songs",
    "track", "tracks", "music", "vibe", "vibes", "mood", "please", "the", "of",
})

MOOD_ALIASES: dict[MoodCategory, set[str]] = {
    MoodCategory.BANGER: {
        "banger", "bangers", "hype", "hyped", "party", "club", "energetic",
        "high energy", "upbeat", "anthem", "anthems", "peak time", "rave",
        "turnt", "fire", "lit", "festival"
    },
    MoodCategory.WORKOUT: {
        "workout", "gym", "running", "cardio", "training", "pump", "lifting", "fitness"
    },
    MoodCategory.MELODIC: {
        "melodic", "euphoric", "uplifting", "emotional", "dreamy", "atmospheric",
        "beautiful", "vocal", "liquid", "ethereal", "soaring", "melody"
    },
    MoodCategory.HARD: {
        "hard", "heavy", "aggressive", "brutal", "industrial",
        "filthy", "dirty", "intense", "rage", "mosh", "hardcore"
    },
    MoodCategory.DARK: {
        "dark", "darker", "gritty", "underground", "hypnotic", "ominous", "shadow"
    },
    MoodCategory.CHILL: {
        "chill", "chillout", "chilled", "relaxed", "relaxing", "calm", "mellow",
        "smooth", "lofi", "lo-fi", "ambient", "downtempo", "study", "late night",
        "cozy", "easy", "laid back", "laidback"
    },
    MoodCategory.SAD: {
        "sad", "melancholic", "melancholy", "heartbreak", "depressing", "crying",
        "lonely", "somber", "bittersweet", "tearjerker"
    },
    MoodCategory.LOVE: {
        "love", "romantic", "romance", "slow jam", "slow jams", "sensual",
        "intimate", "crush", "valentine", "affection", "sweet", "ballad", "ballads"
    },
}

ALIAS_TO_MOOD: dict[str, MoodCategory] = {
    w: canon for canon, words in MOOD_ALIASES.items() for w in words
}

STANDALONE_MOOD_PROFILES: dict[str, MoodProfile] = {
    MoodCategory.BANGER: MoodProfile(
        deezer_queries=[
            "Festival Bangers",
            "Club Bangers",
            "Rap Bangers",
            "Drum and Bass Bangers",
            "Dance Anthems",
            "Rock Anthems",
        ],
        lastfm_tags=["banger", "party", "dance", "club", "hip-hop"],
        beatport_query="Beatport Hype 100",
    ),
    MoodCategory.WORKOUT: MoodProfile(
        deezer_queries=[
            "Workout Bangers",
            "Gym Drum & Bass",
            "Workout Hip Hop",
            "Gym Hardstyle",
            "Rock Workout",
        ],
        lastfm_tags=["workout", "energetic", "high energy", "drum and bass", "hip-hop"],
        beatport_query="Beatport Top 100",
    ),
    MoodCategory.MELODIC: MoodProfile(
        deezer_queries=[
            "Melodic House & Techno",
            "Liquid Drum & Bass",
            "Progressive House Melodic",
            "Dream Pop Melodic Indie",
            "Melodic Bass",
        ],
        lastfm_tags=["melodic", "melodic techno", "liquid funk", "synthwave", "dream pop"],
        beatport_query="Beatport Top 100 Melodic House & Techno",
    ),
    MoodCategory.HARD: MoodProfile(
        deezer_queries=[
            "Hard Techno",
            "Neurofunk Drum & Bass",
            "Hardstyle Bangers",
            "Heavy Metal Workout",
            "Bass House & Dubstep",
        ],
        lastfm_tags=["hard techno", "neurofunk", "hardstyle", "phonk", "heavy metal", "industrial"],
        beatport_query="Beatport Top 100 Hard Techno",
    ),
    MoodCategory.DARK: MoodProfile(
        deezer_queries=[
            "Dark Techno",
            "Neurofunk Drum & Bass",
            "Dark Industrial",
        ],
        lastfm_tags=["dark", "darkwave", "darksynth", "phonk", "industrial", "neurofunk", "dark ambient"],
        beatport_query="Beatport Top 100 Techno",
    ),
    MoodCategory.CHILL: MoodProfile(
        deezer_queries=[
            "Chill Vibes",
            "Lo-Fi Beats",
            "Chill House",
            "Liquid Drum & Bass Chill",
            "Acoustic Chill Indie",
            "Downtempo Lounge",
        ],
        lastfm_tags=["chillout", "lo-fi", "downtempo", "chill", "ambient"],
        beatport_query="Beatport Top 100 Deep House",
    ),
    MoodCategory.SAD: MoodProfile(
        deezer_queries=[
            "Sad Songs",
            "Melancholic Indie",
            "Sad Rap & R&B",
            "Heartbreak Acoustic",
            "Emotional Piano & Strings",
        ],
        lastfm_tags=["sad", "melancholy", "heartbreak", "indie folk", "acoustic"],
        beatport_query=None,
    ),
    MoodCategory.LOVE: MoodProfile(
        deezer_queries=[
            "Love Songs",
            "R&B Slow Jams",
            "Romantic Pop Ballads",
            "Acoustic Love Songs",
            "Indie Love Songs",
            "Classic Love Songs",
        ],
        lastfm_tags=["love", "romantic", "love songs", "slow jams", "rnb"],
        beatport_query=None,
    ),
}

GENRE_CANONICAL_MAP: dict[str, CanonicalGenre] = {
    "dnb": CanonicalGenre.DNB,
    "drum and bass": CanonicalGenre.DNB,
    "drumnbass": CanonicalGenre.DNB,
    "drum & bass": CanonicalGenre.DNB,
    "jungle": CanonicalGenre.DNB,
    "techno": CanonicalGenre.TECHNO,
    "house": CanonicalGenre.HOUSE,
    "tech house": CanonicalGenre.HOUSE,
    "deep house": CanonicalGenre.HOUSE,
    "trance": CanonicalGenre.TRANCE,
    "dubstep": CanonicalGenre.DUBSTEP,
    "electronic": CanonicalGenre.ELECTRONIC,
    "edm": CanonicalGenre.ELECTRONIC,
    "dance": CanonicalGenre.ELECTRONIC,
    "rap": CanonicalGenre.RAP,
    "hip hop": CanonicalGenre.RAP,
    "hip-hop": CanonicalGenre.RAP,
    "hiphop": CanonicalGenre.RAP,
    "trap": CanonicalGenre.RAP,
    "rnb": CanonicalGenre.RNB,
    "r&b": CanonicalGenre.RNB,
    "r and b": CanonicalGenre.RNB,
    "soul": CanonicalGenre.RNB,
    "rock": CanonicalGenre.ROCK,
    "indie": CanonicalGenre.ROCK,
    "alternative": CanonicalGenre.ROCK,
    "metal": CanonicalGenre.METAL,
    "pop": CanonicalGenre.POP,
    "country": CanonicalGenre.COUNTRY,
}

GENRE_MOOD_MATRIX: dict[tuple[str, str], MoodProfile] = {
    (CanonicalGenre.DNB, MoodCategory.MELODIC): MoodProfile(
        deezer_queries=["Liquid Drum & Bass", "Melodic Drum and Bass"],
        lastfm_tags=["liquid funk", "liquid dnb", "drum and bass"],
        beatport_query="Beatport Top 100 Drum & Bass",
    ),
    (CanonicalGenre.DNB, MoodCategory.CHILL): MoodProfile(
        deezer_queries=["Liquid Drum & Bass", "Chill Drum and Bass"],
        lastfm_tags=["liquid funk", "intelligent drum and bass"],
        beatport_query=None,
    ),
    (CanonicalGenre.DNB, MoodCategory.HARD): MoodProfile(
        deezer_queries=["Neurofunk Drum & Bass", "Jump Up Drum & Bass", "Dark Drum and Bass"],
        lastfm_tags=["neurofunk", "darkstep", "jump up"],
        beatport_query="Beatport Top 100 Drum & Bass",
    ),
    (CanonicalGenre.DNB, MoodCategory.DARK): MoodProfile(
        deezer_queries=["Neurofunk Drum & Bass", "Dark Drum and Bass"],
        lastfm_tags=["neurofunk", "darkstep", "techstep"],
        beatport_query="Beatport Top 100 Drum & Bass",
    ),
    (CanonicalGenre.DNB, MoodCategory.BANGER): MoodProfile(
        deezer_queries=["Drum & Bass Bangers", "UKF Drum & Bass", "Dancefloor Drum & Bass"],
        lastfm_tags=["drum and bass", "dnb"],
        beatport_query="Beatport Top 100 Drum & Bass",
    ),
    (CanonicalGenre.DNB, MoodCategory.WORKOUT): MoodProfile(
        deezer_queries=["Gym Drum & Bass", "Workout Drum and Bass", "Drum & Bass Bangers"],
        lastfm_tags=["drum and bass", "dnb"],
        beatport_query="Beatport Top 100 Drum & Bass",
    ),
    (CanonicalGenre.TECHNO, MoodCategory.MELODIC): MoodProfile(
        deezer_queries=["Melodic House & Techno", "Melodic Techno"],
        lastfm_tags=["melodic techno", "progressive house"],
        beatport_query="Beatport Top 100 Melodic House & Techno",
    ),
    (CanonicalGenre.TECHNO, MoodCategory.HARD): MoodProfile(
        deezer_queries=["Hard Techno", "Schranz", "Industrial Techno"],
        lastfm_tags=["hard techno", "industrial techno", "schranz"],
        beatport_query="Beatport Top 100 Hard Techno",
    ),
    (CanonicalGenre.TECHNO, MoodCategory.DARK): MoodProfile(
        deezer_queries=["Dark Techno", "Raw Hypnotic Techno", "Industrial Techno"],
        lastfm_tags=["dark techno", "minimal techno", "industrial techno"],
        beatport_query="Beatport Top 100 Techno",
    ),
    (CanonicalGenre.TECHNO, MoodCategory.BANGER): MoodProfile(
        deezer_queries=["Peak Time Techno", "Rave Techno", "Techno Bangers"],
        lastfm_tags=["techno", "acid techno"],
        beatport_query="Beatport Top 100 Techno",
    ),
    (CanonicalGenre.HOUSE, MoodCategory.MELODIC): MoodProfile(
        deezer_queries=["Melodic House & Techno", "Progressive House"],
        lastfm_tags=["progressive house", "melodic house"],
        beatport_query="Beatport Top 100 Melodic House & Techno",
    ),
    (CanonicalGenre.HOUSE, MoodCategory.CHILL): MoodProfile(
        deezer_queries=["Chill House", "Deep House", "Lounge House"],
        lastfm_tags=["deep house", "chill house"],
        beatport_query="Beatport Top 100 Deep House",
    ),
    (CanonicalGenre.HOUSE, MoodCategory.BANGER): MoodProfile(
        deezer_queries=["Tech House Bangers", "Bass House", "Club House Anthems"],
        lastfm_tags=["tech house", "bass house", "house"],
        beatport_query="Beatport Top 100 Tech House",
    ),
    (CanonicalGenre.HOUSE, MoodCategory.HARD): MoodProfile(
        deezer_queries=["Bass House", "Electro House Bangers", "Tech House"],
        lastfm_tags=["bass house", "electro house"],
        beatport_query="Beatport Top 100 Tech House",
    ),
    (CanonicalGenre.RAP, MoodCategory.BANGER): MoodProfile(
        deezer_queries=["Rap Bangers", "Trap Bangers", "Hip Hop Club Hits"],
        lastfm_tags=["rap", "trap", "hip-hop"],
        beatport_query=None,
    ),
    (CanonicalGenre.RAP, MoodCategory.HARD): MoodProfile(
        deezer_queries=["Hard Trap", "Drill Bangers", "Aggressive Rap"],
        lastfm_tags=["trap", "drill", "hardcore hip-hop"],
        beatport_query=None,
    ),
    (CanonicalGenre.RAP, MoodCategory.MELODIC): MoodProfile(
        deezer_queries=["Melodic Rap", "Emotional Rap", "Melodic Trap"],
        lastfm_tags=["melodic rap", "cloud rap", "hip-hop"],
        beatport_query=None,
    ),
    (CanonicalGenre.RAP, MoodCategory.CHILL): MoodProfile(
        deezer_queries=["Chill Hip Hop", "Lo-Fi Hip Hop", "Jazz Rap"],
        lastfm_tags=["chillhop", "jazz hop", "lo-fi"],
        beatport_query=None,
    ),
    (CanonicalGenre.RAP, MoodCategory.SAD): MoodProfile(
        deezer_queries=["Sad Rap", "Emo Rap", "Heartbreak Hip Hop"],
        lastfm_tags=["emo rap", "sad rap"],
        beatport_query=None,
    ),
    (CanonicalGenre.RAP, MoodCategory.LOVE): MoodProfile(
        deezer_queries=["Hip Hop Love Songs", "R&B & Hip Hop Slow Jams"],
        lastfm_tags=["hip-hop", "rnb", "slow jams"],
        beatport_query=None,
    ),
    (CanonicalGenre.RNB, MoodCategory.LOVE): MoodProfile(
        deezer_queries=["R&B Love Songs", "R&B Slow Jams", "Romantic R&B"],
        lastfm_tags=["rnb", "slow jams", "contemporary rnb"],
        beatport_query=None,
    ),
    (CanonicalGenre.RNB, MoodCategory.CHILL): MoodProfile(
        deezer_queries=["Chill R&B", "Late Night R&B", "Neo Soul Chill"],
        lastfm_tags=["neo-soul", "rnb", "alternative rnb"],
        beatport_query=None,
    ),
    (CanonicalGenre.RNB, MoodCategory.SAD): MoodProfile(
        deezer_queries=["Heartbreak R&B", "Sad R&B", "Emotional R&B"],
        lastfm_tags=["rnb", "heartbreak", "soul"],
        beatport_query=None,
    ),
    (CanonicalGenre.ROCK, MoodCategory.BANGER): MoodProfile(
        deezer_queries=["Rock Anthems", "Stadium Rock Bangers", "Party Rock"],
        lastfm_tags=["rock", "hard rock", "alternative rock"],
        beatport_query=None,
    ),
    (CanonicalGenre.ROCK, MoodCategory.HARD): MoodProfile(
        deezer_queries=["Hard Rock", "Heavy Metal Bangers", "Nu Metal"],
        lastfm_tags=["hard rock", "heavy metal", "nu metal"],
        beatport_query=None,
    ),
    (CanonicalGenre.ROCK, MoodCategory.MELODIC): MoodProfile(
        deezer_queries=["Melodic Rock", "Dream Pop Indie", "Post-Rock"],
        lastfm_tags=["indie rock", "dream pop", "post-rock"],
        beatport_query=None,
    ),
    (CanonicalGenre.ROCK, MoodCategory.LOVE): MoodProfile(
        deezer_queries=["Rock Love Ballads", "Power Ballads", "Indie Love Songs"],
        lastfm_tags=["power ballad", "soft rock", "indie"],
        beatport_query=None,
    ),
    (CanonicalGenre.POP, MoodCategory.LOVE): MoodProfile(
        deezer_queries=["Pop Love Songs", "Romantic Pop Hits", "Pop Ballads"],
        lastfm_tags=["pop", "love songs", "romantic"],
        beatport_query=None,
    ),
    (CanonicalGenre.POP, MoodCategory.BANGER): MoodProfile(
        deezer_queries=["Pop Bangers", "Dance Pop Hits", "Party Pop Anthems"],
        lastfm_tags=["dance-pop", "electropop", "pop"],
        beatport_query=None,
    ),
}

# ---------------------------------------------------------------------------
# 3. 3x3 Tempo x Intensity Music Spectrum Grid & Shift Phrases
# ---------------------------------------------------------------------------

TEMPO_AXIS: Tuple[TempoLevel, TempoLevel, TempoLevel] = (
    TempoLevel.SLOW,
    TempoLevel.MID,
    TempoLevel.FAST,
)

INTENSITY_AXIS: Tuple[IntensityLevel, IntensityLevel, IntensityLevel] = (
    IntensityLevel.SOFT,
    IntensityLevel.MID,
    IntensityLevel.HARD,
)

SPECTRUM_CHART: List[SpectrumZone] = [
    SpectrumZone(
        ui_zone=SpectrumZoneId.SLOW_SOFT,
        tempo=TempoLevel.SLOW,
        intensity=IntensityLevel.SOFT,
        mainstream_tags=["ambient", "downtempo", "modern classical", "chillout"],
        deep_cut_tags=["space ambient", "lowercase", "tape loops", "drone"],
        intersection_modifiers=["sleep", "relax", "piano", "minimalist"],
    ),
    SpectrumZone(
        ui_zone=SpectrumZoneId.MID_SOFT,
        tempo=TempoLevel.MID,
        intensity=IntensityLevel.SOFT,
        mainstream_tags=["rnb", "neo-soul", "indie folk", "soft pop"],
        deep_cut_tags=["sophisti-pop", "bossanova", "quiet storm", "freak folk"],
        intersection_modifiers=["warm", "mellow", "intimate", "acoustic"],
    ),
    SpectrumZone(
        ui_zone=SpectrumZoneId.FAST_SOFT,
        tempo=TempoLevel.FAST,
        intensity=IntensityLevel.SOFT,
        mainstream_tags=["liquid dnb", "dream pop", "shoegaze", "minimal techno"],
        deep_cut_tags=["microhouse", "ambient techno", "ethereal wave"],
        intersection_modifiers=["hypnotic", "floating", "atmospheric"],
    ),
    SpectrumZone(
        ui_zone=SpectrumZoneId.SLOW_MID,
        tempo=TempoLevel.SLOW,
        intensity=IntensityLevel.MID,
        mainstream_tags=["trip-hop", "slowcore", "blues rock", "sadcore"],
        deep_cut_tags=["dark jazz", "desert rock", "dub", "slow motion"],
        intersection_modifiers=["melancholic", "moody", "groove"],
    ),
    SpectrumZone(
        ui_zone=SpectrumZoneId.MID_MID,
        tempo=TempoLevel.MID,
        intensity=IntensityLevel.MID,
        mainstream_tags=["indie pop", "alt-rock", "synthpop", "pop"],
        deep_cut_tags=["jangle pop", "twee pop", "vaporwave", "city pop"],
        intersection_modifiers=["catchy", "upbeat", "radio"],
    ),
    SpectrumZone(
        ui_zone=SpectrumZoneId.FAST_MID,
        tempo=TempoLevel.FAST,
        intensity=IntensityLevel.MID,
        mainstream_tags=["house", "nu-disco", "pop-punk", "electropop"],
        deep_cut_tags=["hyperpop", "bubblegum bass", "eurobeat", "happy hardcore"],
        intersection_modifiers=["dance", "party", "energetic"],
    ),
    SpectrumZone(
        ui_zone=SpectrumZoneId.SLOW_HARD,
        tempo=TempoLevel.SLOW,
        intensity=IntensityLevel.HARD,
        mainstream_tags=["doom metal", "stoner rock", "industrial", "sludge"],
        deep_cut_tags=["drone metal", "death doom", "power electronics"],
        intersection_modifiers=["heavy", "dark", "brutal", "oppressive"],
    ),
    SpectrumZone(
        ui_zone=SpectrumZoneId.MID_HARD,
        tempo=TempoLevel.MID,
        intensity=IntensityLevel.HARD,
        mainstream_tags=["grunge", "hard rock", "metalcore", "post-hardcore"],
        deep_cut_tags=["noise rock", "ebm", "industrial metal", "mathcore"],
        intersection_modifiers=["angry", "distorted", "raw", "aggressive"],
    ),
    SpectrumZone(
        ui_zone=SpectrumZoneId.FAST_HARD,
        tempo=TempoLevel.FAST,
        intensity=IntensityLevel.HARD,
        mainstream_tags=["thrash metal", "drum and bass", "hardcore punk"],
        deep_cut_tags=["breakcore", "grindcore", "gabber", "hardstyle"],
        intersection_modifiers=["fast", "intense", "chaos", "frenzy"],
    ),
]

DEFAULT_CENTER_ZONE = SpectrumZoneId.MID_MID

ZONE_BY_NAME: Dict[str, SpectrumZone] = {z.ui_zone: z for z in SPECTRUM_CHART}
COORDS_BY_ZONE: Dict[str, Tuple[int, int]] = {
    z.ui_zone: (TEMPO_AXIS.index(z.tempo), INTENSITY_AXIS.index(z.intensity))
    for z in SPECTRUM_CHART
}
ZONE_BY_COORDS: Dict[Tuple[int, int], SpectrumZone] = {
    coords: ZONE_BY_NAME[name] for name, coords in COORDS_BY_ZONE.items()
}

BROAD_GENRE_TO_ZONE: Dict[str, SpectrumZoneId] = {
    # slow_soft
    "classical": SpectrumZoneId.SLOW_SOFT,
    "new age": SpectrumZoneId.SLOW_SOFT,
    "meditation": SpectrumZoneId.SLOW_SOFT,
    "soundtrack": SpectrumZoneId.SLOW_SOFT,
    "instrumental": SpectrumZoneId.SLOW_SOFT,
    "lofi": SpectrumZoneId.SLOW_SOFT,
    "lo-fi": SpectrumZoneId.SLOW_SOFT,
    "lo-fi beats": SpectrumZoneId.SLOW_SOFT,
    # mid_soft
    "r&b": SpectrumZoneId.MID_SOFT,
    "r&b/soul": SpectrumZoneId.MID_SOFT,
    "soul": SpectrumZoneId.MID_SOFT,
    "folk": SpectrumZoneId.MID_SOFT,
    "acoustic": SpectrumZoneId.MID_SOFT,
    "singer-songwriter": SpectrumZoneId.MID_SOFT,
    "singer/songwriter": SpectrumZoneId.MID_SOFT,
    "bossa nova": SpectrumZoneId.MID_SOFT,
    "jazz": SpectrumZoneId.MID_SOFT,
    "soft rock": SpectrumZoneId.MID_SOFT,
    "country": SpectrumZoneId.MID_SOFT,
    "easy listening": SpectrumZoneId.MID_SOFT,
    # fast_soft
    "liquid funk": SpectrumZoneId.FAST_SOFT,
    "liquid drum and bass": SpectrumZoneId.FAST_SOFT,
    "intelligent drum and bass": SpectrumZoneId.FAST_SOFT,
    "chillstep": SpectrumZoneId.FAST_SOFT,
    "deep house": SpectrumZoneId.FAST_SOFT,
    "melodic techno": SpectrumZoneId.FAST_SOFT,
    "melodic house": SpectrumZoneId.FAST_SOFT,
    "progressive house": SpectrumZoneId.FAST_SOFT,
    # slow_mid
    "trip hop": SpectrumZoneId.SLOW_MID,
    "blues": SpectrumZoneId.SLOW_MID,
    "reggae": SpectrumZoneId.SLOW_MID,
    "roots reggae": SpectrumZoneId.SLOW_MID,
    "boom bap": SpectrumZoneId.SLOW_MID,
    "g-funk": SpectrumZoneId.SLOW_MID,
    "west coast hip hop": SpectrumZoneId.SLOW_MID,
    "psychedelic rock": SpectrumZoneId.SLOW_MID,
    # mid_mid
    "alternative rock": SpectrumZoneId.MID_MID,
    "alternative": SpectrumZoneId.MID_MID,
    "indie rock": SpectrumZoneId.MID_MID,
    "indie": SpectrumZoneId.MID_MID,
    "rock": SpectrumZoneId.MID_MID,
    "classic rock": SpectrumZoneId.MID_MID,
    "synthwave": SpectrumZoneId.MID_MID,
    "retrowave": SpectrumZoneId.MID_MID,
    "new wave": SpectrumZoneId.MID_MID,
    "hip-hop/rap": SpectrumZoneId.MID_MID,
    "hip-hop": SpectrumZoneId.MID_MID,
    "hip hop": SpectrumZoneId.MID_MID,
    "rap": SpectrumZoneId.MID_MID,
    "funk": SpectrumZoneId.MID_MID,
    # fast_mid
    "dance": SpectrumZoneId.FAST_MID,
    "edm": SpectrumZoneId.FAST_MID,
    "electronic": SpectrumZoneId.FAST_MID,
    "electronica": SpectrumZoneId.FAST_MID,
    "techno": SpectrumZoneId.FAST_MID,
    "tech house": SpectrumZoneId.FAST_MID,
    "trance": SpectrumZoneId.FAST_MID,
    "disco": SpectrumZoneId.FAST_MID,
    "electro": SpectrumZoneId.FAST_MID,
    "uk garage": SpectrumZoneId.FAST_MID,
    "pop punk": SpectrumZoneId.FAST_MID,
    "ska": SpectrumZoneId.FAST_MID,
    # slow_hard
    "sludge metal": SpectrumZoneId.SLOW_HARD,
    "stoner metal": SpectrumZoneId.SLOW_HARD,
    "dark electro": SpectrumZoneId.SLOW_HARD,
    "dark ambient": SpectrumZoneId.SLOW_HARD,
    "phonk": SpectrumZoneId.SLOW_HARD,
    "doom": SpectrumZoneId.SLOW_HARD,
    # mid_hard
    "metal": SpectrumZoneId.MID_HARD,
    "heavy metal": SpectrumZoneId.MID_HARD,
    "alternative metal": SpectrumZoneId.MID_HARD,
    "nu metal": SpectrumZoneId.MID_HARD,
    "progressive metal": SpectrumZoneId.MID_HARD,
    "deathcore": SpectrumZoneId.MID_HARD,
    "trap": SpectrumZoneId.MID_HARD,
    "drill": SpectrumZoneId.MID_HARD,
    "gangsta rap": SpectrumZoneId.MID_HARD,
    "dubstep": SpectrumZoneId.MID_HARD,
    "bass house": SpectrumZoneId.MID_HARD,
    "punk rock": SpectrumZoneId.MID_HARD,
    "punk": SpectrumZoneId.MID_HARD,
    # fast_hard
    "dnb": SpectrumZoneId.FAST_HARD,
    "drum & bass": SpectrumZoneId.FAST_HARD,
    "neurofunk": SpectrumZoneId.FAST_HARD,
    "jump up": SpectrumZoneId.FAST_HARD,
    "jungle": SpectrumZoneId.FAST_HARD,
    "hard techno": SpectrumZoneId.FAST_HARD,
    "schranz": SpectrumZoneId.FAST_HARD,
    "industrial techno": SpectrumZoneId.FAST_HARD,
    "speed metal": SpectrumZoneId.FAST_HARD,
    "death metal": SpectrumZoneId.FAST_HARD,
    "black metal": SpectrumZoneId.FAST_HARD,
    "power metal": SpectrumZoneId.FAST_HARD,
    "thrash": SpectrumZoneId.FAST_HARD,
    "hardcore": SpectrumZoneId.FAST_HARD,
}

EXPLICIT_MUSIC_CUE_WORDS: frozenset[str] = frozenset({
    "play", "song", "songs", "track", "tracks", "music", "tune", "tunes",
    "vibe", "vibes", "beat", "beats", "bpm", "tempo", "genre", "queue",
    "spin", "jam", "jams", "melody", "sound", "sounds", "listen", "listening",
    "something", "somethin", "anything", "notch", "dial",
})

HARDER_PHRASES: frozenset[str] = frozenset({
    "harder", "heavier", "more intense", "more aggressive", "more brutal",
    "up a notch", "turn it up", "crank it up", "too soft",
})

SOFTER_PHRASES: frozenset[str] = frozenset({
    "softer", "lighter", "gentler", "mellower", "calmer", "more chill",
    "less intense", "less heavy", "less aggressive", "down a notch",
    "take it down", "tone it down", "too hard", "too heavy", "too loud",
})

FASTER_PHRASES: frozenset[str] = frozenset({
    "faster", "quicker", "speed it up", "speed up", "higher tempo",
    "more upbeat", "higher bpm", "pick up the pace", "too slow",
})

SLOWER_PHRASES: frozenset[str] = frozenset({
    "slower", "slow it down", "slow down", "lower tempo", "lower bpm",
    "bring the tempo down", "too fast",
})

TRUSTED_MUSIC_LABELS: tuple[str, ...] = (
    "afterlife", "ukf", "hospital records", "spinnin", "monstercat",
    "armada music", "defected", "ultra music", "ultra records", "drumcode",
    "anjunadeep", "anjunabeats", "liquicity", "blackout music", "ram records",
    "selected.", "mrsuicidesheep", "suicidesheep", "proximity", "stmpd rcrds",
    "musical freedom", "kontor.tv", "napalm records", "nuclear blast",
    "fueled by ramen", "atlantic records", "interscope", "death row",
    "aftermath", "shady records", "top dawg", "empire", "epitaph",
    "sub pop", "xl recordings", "warp records", "ninja tune", "owsla",
    "mad decent", "spinning records", "ministry of sound",
)

MUSIC_TOKEN_STOPWORDS: frozenset[str] = frozenset({
    "by", "the", "a", "an", "and", "or", "of", "in", "on", "at", "to",
    "for", "with", "feat", "ft", "official", "video", "audio", "music",
    "topic", "remix", "original", "mix", "full", "hd", "4k", "hq",
    "unknown", "artist", "track", "song",
})

