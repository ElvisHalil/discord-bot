"""
Shared Cross-Skill Domain Vocabularies, Token Set Helpers & Dataclass Mixins.

Replaces sprawling, duplicated monolithic regex strings across `music`, `sports`,
and `flights` with categorized `frozenset` vocabularies, $O(N)$ token-set lookup
helpers, and programmatic regex compilers.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Dict, Iterable, Iterator, Set, Tuple


class DictLikeMixin:
    """Mixin for `@dataclass(slots=True)` models that provides backwards-compatible
    dictionary access (`obj['key']`, `obj.get('key')`, `'key' in obj`, `obj.to_dict()`)."""

    __slots__ = ()

    def _field_names(self) -> Tuple[str, ...]:
        return tuple(f.name for f in dataclasses.fields(self))  # type: ignore[arg-type]

    def __getitem__(self, key: str) -> Any:
        if key in self._field_names():
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self._field_names():
            setattr(self, key, value)
            return
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._field_names():
            return getattr(self, key)
        return default

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._field_names()

    def keys(self) -> Tuple[str, ...]:
        return self._field_names()

    def values(self) -> Tuple[Any, ...]:
        return tuple(getattr(self, k) for k in self._field_names())

    def items(self) -> Tuple[Tuple[str, Any], ...]:
        return tuple((k, getattr(self, k)) for k in self._field_names())

    def __iter__(self) -> Iterator[str]:
        return iter(self._field_names())

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self._field_names()}


# ---------------------------------------------------------------------------
# Categorized Cross-Domain Token Sets
# ---------------------------------------------------------------------------

TECH_HARDWARE_WORDS: frozenset[str] = frozenset({
    "cpu", "cpus", "gpu", "gpus", "ram", "vram", "ssd", "nvme", "pc", "laptop",
    "phone", "iphone", "android", "macbook", "apple", "intel", "amd", "ryzen",
    "nvidia", "rtx", "gtx", "radeon", "dlss", "fsr", "fps", "hz", "ghz", "mhz",
    "overclock", "clock", "wifi", "internet", "ping", "latency", "download",
    "upload", "server", "router", "browser", "chrome", "firefox", "windows",
    "linux", "python", "rust", "java", "javascript", "typescript", "golang",
    "code", "coding", "algorithm", "compile", "compiler", "database", "sql",
    "query", "model", "llm", "llms", "gpt", "chatgpt", "claude", "gemini",
    "qwen", "llama", "deepseek", "opus", "sonnet", "ollama", "token", "tokens",
    "inference", "benchmark", "benchmarks", "antigravity",
})

SPORTS_DOMAIN_WORDS: frozenset[str] = frozenset({
    "car", "cars", "truck", "engine", "motor", "horsepower", "mph", "kph",
    "kmh", "ferrari", "porsche", "tesla", "bmw", "mercedes", "redbull",
    "mclaren", "verstappen", "hamilton", "leclerc", "norris", "f1", "lap",
    "laps", "tire", "tires", "tyre", "tyres", "compound", "pit", "pitstop",
    "qualifying", "race", "driver", "drivers", "team", "teams", "player",
    "players", "runner", "skater", "goalie", "pitcher", "quarterback",
    "striker", "midfielder", "defender", "match", "matches", "game", "games",
    "tournament", "league", "season", "playoffs", "schedule", "opponent",
    "group", "bracket", "difficulty", "mode", "level", "boss", "quest",
    "epl", "ucl", "laliga", "bundesliga", "seriea", "nba", "nhl", "nfl",
    "mlb", "ufc", "mma", "hltv", "liquipedia", "esports", "csgo", "cs2",
})

FLIGHTS_DOMAIN_WORDS: frozenset[str] = frozenset({
    "flight", "flights", "airline", "airlines", "airport", "airports",
    "plane", "aircraft", "jet", "boeing", "airbus", "layover", "layovers",
    "stopover", "nonstop", "roundtrip", "oneway", "fare", "fares", "airfare",
    "ticket", "tickets", "departure", "departures", "arrival", "arrivals",
    "boarding", "gate", "terminal", "baggage", "luggage", "cabin", "economy",
    "business", "firstclass", "airborne", "landed", "diverted", "delayed",
    "cancelled", "canceled", "callsign", "tail", "altitude", "heading",
    "knots", "itinerary", "itineraries", "connection", "connections",
    "route", "routes", "fly", "flying", "flew", "klm", "westjet", "lufthansa",
    "aircanada", "united", "delta", "emirates", "qatar", "britishairways",
})

FLIGHTS_DOMAIN_PHRASES: Tuple[str, ...] = (
    "round trip", "round-trip", "one way", "one-way", "direct flight",
    "direct flights", "non-stop", "track flight", "untrack flight",
    "flight status", "price alert", "price drop", "faster route",
    "Faster flight", "shorter flight", "shorter layover", "cheaper flight",
)

WEATHER_GEO_FINANCE_WORDS: frozenset[str] = frozenset({
    "weather", "wind", "rain", "storm", "snow", "temperature", "forecast",
    "climate", "water", "hardness", "mineral", "calcium", "wood", "steel",
    "iron", "diamond", "metal", "plastic", "glass", "concrete", "rock",
    "stone", "mattress", "pillow", "bed", "suspension", "spring", "springs",
    "brake", "brakes", "steering", "stock", "stocks", "crypto", "bitcoin",
    "market", "economy", "inflation", "shipping", "delivery", "package",
    "parcel", "order", "shipment", "exam", "test", "math", "physics",
    "chemistry", "homework", "workout", "diet", "recipe", "cooking", "bake",
    "meat", "steak", "egg", "eggs", "cheese", "dough", "bread",
})

NON_MUSIC_SINGLE_WORDS: frozenset[str] = (
    TECH_HARDWARE_WORDS
    | SPORTS_DOMAIN_WORDS
    | FLIGHTS_DOMAIN_WORDS
    | WEATHER_GEO_FINANCE_WORDS
)

NON_MUSIC_PHRASES: Tuple[str, ...] = FLIGHTS_DOMAIN_PHRASES + (
    "c++", "c#", "gpt-4", "gpt-5",
)

NON_SPORTS_TRACK_WORDS: frozenset[str] = (
    FLIGHTS_DOMAIN_WORDS
    | frozenset({
        "soundtrack", "song", "album", "artist", "spotify", "youtube",
        "package", "parcel", "order", "shipment",
    })
)

NON_SPORTS_TRACK_PHRASES: Tuple[str, ...] = FLIGHTS_DOMAIN_PHRASES + (
    "backing track", "diss track", "title track", "bonus track",
    "similar track", "next track", "previous track", "play track",
    "reacted with",
)

# Matches commercial IATA/ICAO flight numbers such as AC850, KL675, WS102, UA 1234, BAW123
_FLIGHT_NUMBER_CODE_RE = re.compile(
    r"\b(?:AC|WS|KL|LH|BA|AF|DL|UA|AA|EK|QR|TK|JL|NH|SQ|CX|QF|OS|LX|IB|AZ|SK|AY|EI|VS|WN|AS|B6|NK|F9|PD|TS|F8)\s?\d{1,4}\b",
    re.IGNORECASE,
)


def tokenize_words(text: str) -> Set[str]:
    """Extract lowercase alphanumeric word tokens from text in O(N) time."""
    if not text:
        return set()
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def contains_domain_terms(
    text: str,
    words: Iterable[str],
    phrases: Iterable[str] = (),
) -> bool:
    """Return True if `text` contains any token in `words` or any substring phrase in `phrases`."""
    if not text:
        return False
    low = text.lower()
    tokens = tokenize_words(low)
    word_set = words if isinstance(words, (set, frozenset)) else set(words)
    if tokens & word_set:
        return True
    for phrase in phrases:
        if phrase.lower() in low:
            return True
    return False


def compile_word_boundary_re(terms: Iterable[str]) -> re.Pattern[str]:
    """Programmatically compile a case-insensitive word-boundary regex from a collection of terms."""
    cleaned = sorted({t.strip() for t in terms if t and t.strip()}, key=len, reverse=True)
    if not cleaned:
        return re.compile(r"(?!x)x")
    alternation = "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in cleaned)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


def compile_phrase_stripper(phrases: Iterable[str]) -> re.Pattern[str]:
    """Compile a regex matching any of the given multi-word or single-word filler phrases."""
    return compile_word_boundary_re(phrases)


def has_non_music_domain_signal(text: str) -> bool:
    """Return True if `text` contains technology, sports, flights/aviation, weather, or finance vocabulary."""
    if not text:
        return False
    if contains_domain_terms(text, NON_MUSIC_SINGLE_WORDS, NON_MUSIC_PHRASES):
        return True
    if _FLIGHT_NUMBER_CODE_RE.search(text):
        return True
    return False


def has_non_sports_track_signal(text: str) -> bool:
    """Return True if a 'track ...' prompt is about music tracks, parcels, or flights/airfare rather than sports."""
    if not text:
        return False
    if contains_domain_terms(text, NON_SPORTS_TRACK_WORDS, NON_SPORTS_TRACK_PHRASES):
        return True
    if _FLIGHT_NUMBER_CODE_RE.search(text):
        return True
    return False


# ---------------------------------------------------------------------------
# Tool Argument Alias Registry & Orchestrator Lookup Tables
# ---------------------------------------------------------------------------

EPHEMERAL_SPORTS_TOOLS: frozenset[str] = frozenset({
    "skill_sports_info",
    "skill_sports_tracker",
    "skill_sports_odds",
})

MONTH_MAP_SYNC: Dict[str, int] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

TOOL_ARG_ALIASES: Dict[str, Dict[str, str]] = {
    "search_web": {
        "q": "query", "search_query": "query", "keywords": "query",
        "keyword": "query", "prompt": "query", "text": "query", "search": "query",
    },
    "read_website": {
        "link": "url", "website": "url", "target_url": "url", "uri": "url",
        "site": "url", "href": "url", "page": "url",
    },
    "read_youtube_video": {
        "link": "url", "video_url": "url", "video": "url", "uri": "url",
        "href": "url", "video_id": "url", "id": "url",
    },
    "skill_news_headlines": {
        "country": "region", "location": "region", "place": "region", "nation": "region",
        "category": "topic", "section": "topic",
    },
    "skill_music_discovery": {
        "artist_name": "artist", "band": "artist", "creator": "artist",
        "style": "genre", "vibe": "mood", "energy": "mood", "feel": "mood",
        "seed": "similar_to", "song": "similar_to", "track": "similar_to",
        "direction": "shift", "notch": "shift", "scale": "shift", "tempo": "shift", "intensity": "shift",
    },
    "skill_sports_esports": {
        "type": "query_type", "category": "query_type", "query": "query_type", "mode": "query_type",
        "event": "event_page", "page": "event_page", "tournament": "event_page",
        "player": "player_url", "player_name": "player_url", "name": "player_url", "target": "player_url",
        "team": "matchup", "matchup": "matchup", "match": "matchup", "teams": "matchup",
    },
    "skill_sports_odds": {
        "matchup": "query", "match": "query", "game": "query", "event": "query", "teams": "query",
        "category": "sport", "league": "sport",
    },
    "skill_sports_info": {
        "league": "sport", "category": "sport", "type": "sport", "sport_type": "sport",
        "sport_name": "sport", "discipline": "sport", "competition": "sport",
        "club": "team", "franchise": "team", "squad": "team", "teams": "team",
        "competitor": "team", "side": "team", "organization": "team",
        "athlete": "player", "player_name": "player", "fighter": "player", "driver": "player", "golfer": "player",
        "q": "query", "text": "query", "prompt": "query", "search": "query", "question": "query",
        "matchup": "query", "match": "query", "matches": "query", "filter": "query",
        "outcomes": "query", "outcome": "query",
    },
    "skill_sports_tracker": {
        "team": "query", "club": "query", "match": "query", "matchup": "query",
        "game": "query", "event": "query", "target": "query", "q": "query",
        "command": "action", "mode": "action", "type": "action",
        "league": "sport", "category": "sport",
    },
    "skill_weather_info": {
        "location": "city", "place": "city", "town": "city", "region": "city", "q": "city", "query": "city",
        "forecast_days": "days", "num_days": "days", "horizon": "days",
        "forecast_hours": "hours", "num_hours": "hours", "hourly_hours": "hours",
    },
    "skill_youtube_search": {
        "q": "query", "search_query": "query", "search": "query", "video": "query",
        "song": "query", "track": "query", "title": "query",
        "limit": "max_results", "count": "max_results", "music": "is_music",
    },
    "skill_music_analysis": {
        "song": "track", "query": "track", "q": "track", "title": "track", "seed": "track",
        "mode": "action", "type": "action", "bpm": "target_bpm", "tempo": "target_bpm",
    },
    "skill_flight_info": {
        "q": "query", "route": "query", "search": "query",
        "from": "origin", "from_airport": "origin", "departure": "origin", "dep": "origin", "source": "origin",
        "to": "destination", "to_airport": "destination", "arrival": "destination", "dest": "destination", "arr": "destination",
        "departure_date": "date", "dep_date": "date", "when": "date",
        "ret_date": "return_date", "return": "return_date",
        "flight": "flight_number", "callsign": "flight_number", "code": "flight_number", "tail": "flight_number",
        "seat": "cabin", "class": "cabin", "cabin_class": "cabin",
        "curr": "currency",
        "stops": "max_stops", "num_stops": "max_stops", "stop_limit": "max_stops",
        "sort": "sort_by", "sort_preference": "sort_by", "order": "sort_by",
        "trip": "trip_type", "type": "trip_type",
    },
    "skill_flight_tracker": {
        "mode": "action", "command": "action", "cmd": "action",
        "q": "query", "flight": "query", "flight_number": "query", "callsign": "query", "route": "query",
        "from": "origin", "from_airport": "origin", "departure": "origin",
        "to": "destination", "to_airport": "destination", "arrival": "destination", "dest": "destination",
        "departure_date": "date", "dep_date": "date",
        "ret_date": "return_date", "return": "return_date",
        "price": "target_price", "max_price": "target_price", "threshold": "target_price",
    },
    "skill_markets_info": {
        "q": "query", "search": "query", "topic": "query", "prompt": "query",
        "symbol": "ticker", "symbols": "ticker", "tickers": "ticker", "stock": "ticker",
        "stocks": "ticker", "asset": "ticker", "assets": "ticker", "coin": "ticker",
        "company": "ticker", "companies": "ticker", "compare": "ticker",
        "market": "region", "country": "region", "area": "region",
        "mode": "view", "type": "view", "category": "view", "section": "view",
        "period": "timeframe", "range": "timeframe", "horizon": "timeframe", "duration": "timeframe", "window": "timeframe", "time_range": "timeframe",
    },
    "skill_voice_info": {
        "command": "action", "cmd": "action", "type": "action", "operation": "action",
        "q": "query", "text": "query", "speech": "query", "message": "query", "say": "query", "channel": "query",
        "listen_mode": "mode", "activation": "mode",
        "voice": "voice_profile", "profile": "voice_profile", "persona": "voice_profile", "speaker": "voice_profile",
    },
    "evaluate_math": {
        "math": "expression", "expr": "expression", "equation": "expression",
        "formula": "expression", "query": "expression", "q": "expression",
    },
    "save_memory": {
        "memory": "fact", "note": "fact", "info": "fact", "text": "fact", "content": "fact",
    },
}



