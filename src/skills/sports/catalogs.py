"""
Static sports & esports league registries, team aliases, broadcast channel catalogs,
and stopword sets for the sports skill package.
"""

from typing import Dict, Tuple

from src.skills.sports.models import (
    EsportsStreamChannel,
    SoccerLeagueConfig,
    UniversalLeagueConfig,
)

SOCCER_LEAGUES: Dict[str, SoccerLeagueConfig] = {
    "epl": SoccerLeagueConfig(
        code="eng.1",
        name="English Premier League",
        aliases=("epl", "premier league", "premier", "english premier league", "england"),
    ),
    "ucl": SoccerLeagueConfig(
        code="uefa.champions",
        name="UEFA Champions League",
        aliases=("ucl", "champions league", "champions", "european cup"),
    ),
    "uel": SoccerLeagueConfig(
        code="uefa.europa",
        name="UEFA Europa League",
        aliases=("uel", "europa league", "europa"),
    ),
    "laliga": SoccerLeagueConfig(
        code="esp.1",
        name="Spanish La Liga",
        aliases=("laliga", "la liga", "spanish league", "spain", "primera division"),
    ),
    "seriea": SoccerLeagueConfig(
        code="ita.1",
        name="Italian Serie A",
        aliases=("seriea", "serie a", "italian league", "italy", "calcio"),
    ),
    "bundesliga": SoccerLeagueConfig(
        code="ger.1",
        name="German Bundesliga",
        aliases=("bundesliga", "german league", "germany"),
    ),
    "mls": SoccerLeagueConfig(
        code="usa.1",
        name="Major League Soccer (MLS)",
        aliases=("mls", "major league soccer", "usa soccer"),
    ),
    "ligue1": SoccerLeagueConfig(
        code="fra.1",
        name="French Ligue 1",
        aliases=("ligue1", "ligue 1", "french league", "france"),
    ),
}

SOCCER_MULTI_LEAGUE_KEYS: Tuple[str, ...] = (
    "ucl", "uel", "epl", "laliga", "seriea", "bundesliga", "ligue1", "mls"
)

CLUB_LEAGUE_HINTS: Dict[str, str] = {
    "arsenal": "epl", "chelsea": "epl", "liverpool": "epl", "manchester city": "epl",
    "man city": "epl", "manchester united": "epl", "man utd": "epl", "tottenham": "epl",
    "spurs": "epl", "aston villa": "epl", "villa": "epl", "newcastle": "epl", "leeds": "epl",
    "brighton": "epl", "west ham": "epl", "wolves": "epl", "wolverhampton": "epl",
    "fulham": "epl", "brentford": "epl", "crystal palace": "epl", "palace": "epl", "everton": "epl",
    "bournemouth": "epl", "nottingham": "epl", "forest": "epl", "sunderland": "epl",
    "burnley": "epl", "leicester": "epl",
    "real madrid": "laliga", "madrid": "laliga", "barcelona": "laliga", "barca": "laliga", "atletico": "laliga",
    "sevilla": "laliga", "real sociedad": "laliga", "sociedad": "laliga", "villarreal": "laliga", "betis": "laliga",
    "athletic bilbao": "laliga", "bilbao": "laliga", "valencia": "laliga", "girona": "laliga",
    "bayern": "bundesliga", "munich": "bundesliga", "dortmund": "bundesliga", "leverkusen": "bundesliga",
    "leipzig": "bundesliga", "frankfurt": "bundesliga", "stuttgart": "bundesliga",
    "inter": "seriea", "milan": "seriea", "juventus": "seriea", "juve": "seriea",
    "napoli": "seriea", "roma": "seriea", "lazio": "seriea", "atalanta": "seriea", "fiorentina": "seriea",
    "psg": "ligue1", "paris": "ligue1", "marseille": "ligue1", "lyon": "ligue1", "monaco": "ligue1",
    "inter miami": "mls", "la galaxy": "mls", "lafc": "mls",
}

UNIVERSAL_SPORT_LEAGUE_REGISTRY: Dict[str, UniversalLeagueConfig] = {
    "nfl": UniversalLeagueConfig(
        sport="football",
        league="nfl",
        label="NFL Football",
        icon="🏈",
        aliases=(
            "nfl", "national football league", "super bowl", "touchdown", "quarterback",
            "chiefs", "eagles", "cowboys", "49ers", "niners", "ravens", "bills", "lions",
            "packers", "bengals", "texans", "steelers", "jets", "giants", "patriots",
            "dolphins", "chargers", "raiders", "broncos", "seahawks", "rams", "cardinals",
            "falcons", "saints", "buccaneers", "bucs", "panthers", "bears", "vikings",
            "commanders", "browns", "colts", "jaguars", "titans",
        ),
    ),
    "ncaaf": UniversalLeagueConfig(
        sport="football",
        league="college-football",
        label="NCAA College Football",
        icon="🏈",
        aliases=(
            "ncaaf", "college football", "cfb", "heisman", "crimson tide", "longhorns",
            "buckeyes", "wolverines", "bulldogs", "fighting irish", "sooners", "oregon ducks",
        ),
    ),
    "mlb": UniversalLeagueConfig(
        sport="baseball",
        league="mlb",
        label="MLB Baseball",
        icon="⚾",
        aliases=(
            "mlb", "baseball", "world series", "home run", "innings",
            "yankees", "dodgers", "red sox", "blue jays", "mets", "astros", "braves",
            "phillies", "padres", "cubs", "cardinals", "orioles", "guardians", "twins",
            "mariners", "rangers", "diamondbacks", "d-backs", "brewers", "reds", "pirates",
            "royals", "tigers", "white sox", "angels", "athletics", "marlins", "nationals", "rockies",
        ),
    ),
    "wnba": UniversalLeagueConfig(
        sport="basketball",
        league="wnba",
        label="WNBA Basketball",
        icon="🏀",
        aliases=(
            "wnba", "indiana fever", "las vegas aces", "new york liberty", "minnesota lynx",
            "connecticut sun", "seattle storm", "chicago sky", "caitlin clark", "aja wilson",
        ),
    ),
    "ncaab": UniversalLeagueConfig(
        sport="basketball",
        league="mens-college-basketball",
        label="NCAA Men's College Basketball",
        icon="🏀",
        aliases=(
            "ncaab", "college basketball", "march madness", "final four", "duke blue devils",
            "kansas jayhawks", "kentucky wildcats", "uconn huskies", "unc tar heels",
        ),
    ),
    "ufc": UniversalLeagueConfig(
        sport="mma",
        league="ufc",
        label="UFC / MMA",
        icon="🥊",
        aliases=(
            "ufc", "mma", "mixed martial arts", "fight night", "octagon",
            "makhachev", "pereira", "topuria", "jones", "adesanya", "chimaev",
            "holloway", "oliveira", "omalley", "o'malley", "dvalishvili", "strickland", "du plessis",
        ),
    ),
    "atp": UniversalLeagueConfig(
        sport="tennis",
        league="atp",
        label="ATP Tennis",
        icon="🎾",
        aliases=(
            "tennis", "atp", "wimbledon", "us open tennis", "roland garros", "french open",
            "australian open", "alcaraz", "sinner", "djokovic", "zverev", "medvedev",
        ),
    ),
    "wta": UniversalLeagueConfig(
        sport="tennis",
        league="wta",
        label="WTA Tennis",
        icon="🎾",
        aliases=("wta", "swiatek", "sabalenka", "gauff", "rybakina"),
    ),
    "pga": UniversalLeagueConfig(
        sport="golf",
        league="pga",
        label="PGA Tour Golf",
        icon="⛳",
        aliases=("pga", "golf", "masters tournament", "scheffler", "mcilroy", "rahm", "schauffele"),
    ),
}

ESPORTS_STREAM_CHANNELS: Tuple[EsportsStreamChannel, ...] = (
    EsportsStreamChannel("ESL Counter-Strike", "ESLCS", "UCPq2ETz4aAGo2Z-8JisDPIA"),
    EsportsStreamChannel("BLAST Premier", "BLASTPremier", "UC9k--dE_UE0Faxzgb_DDkYQ"),
    EsportsStreamChannel("Thunderpick Esports", "thunderpickesports", "UCDs5D1luLRP2oo3lk71MaCA"),
)

HLTV_TEAM_ALIASES: Dict[str, str] = {
    "falcons": "falcons",
    "team falcons": "falcons",
    "vitality": "vitality",
    "team vitality": "vitality",
    "spirit": "spirit",
    "team spirit": "spirit",
    "mouz": "mouz",
    "mousesports": "mouz",
    "furia": "furia",
    "navi": "natus vincere",
    "natus vincere": "natus vincere",
    "g2": "g2",
    "g2 esports": "g2",
    "faze": "faze",
    "faze clan": "faze",
    "aurora": "aurora",
    "aurora gaming": "aurora",
    "legacy": "legacy",
    "nip": "ninjas in pyjamas",
    "ninjas in pyjamas": "ninjas in pyjamas",
    "mongolz": "the mongolz",
    "the mongolz": "the mongolz",
    "astralis": "astralis",
    "liquid": "liquid",
    "team liquid": "liquid",
    "heroic": "heroic",
    "virtus.pro": "virtus.pro",
    "vp": "virtus.pro",
    "eternal fire": "eternal fire",
    "3dmax": "3dmax",
    "pain": "pain",
    "mibr": "mibr",
    "complexity": "complexity",
    "big": "big",
    "fnatic": "fnatic",
    "9z": "9z",
    "betboom": "betboom",
    "parivision": "parivision",
    "fut": "fut",
}

HLTV_STOPWORDS: frozenset[str] = frozenset({
    "what", "whats", "what's", "are", "is", "the", "current", "player", "players",
    "stat", "stats", "statistics", "for", "of", "in", "on", "cs", "cs2", "csgo",
    "team", "roster", "lineup", "rating", "ratings", "kd", "kdr", "performance",
    "how", "doing", "right", "now", "today", "show", "me", "get", "tell", "about",
})

LIQUIPEDIA_GENERIC_EVENT_QUERIES: frozenset[str] = frozenset({
    "current", "next", "upcoming", "today", "tomorrow", "schedule",
    "matches", "all", "tournaments", "event_info",
})

LIQUIPEDIA_SLUG_STOPWORDS: frozenset[str] = frozenset({
    "the", "cs2", "schedule", "matches", "today", "time", "tournament",
    "event", "table", "standings", "teams", "roster", "rosters",
})

ESPN_TEAM_ALIASES: Dict[str, str] = {
    # Soccer
    "man city": "manchester city",
    "mancheser city": "manchester city",
    "man utd": "manchester united",
    "man united": "manchester united",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "newcastle": "newcastle united",
    "west ham": "west ham united",
    "brighton": "brighton & hove albion",
    "nottingham": "nottingham forest",
    "forest": "nottingham forest",
    "leeds": "leeds united",
    "psg": "paris saint-germain",
    "inter": "internazionale",
    "inter milan": "internazionale",
    "ac milan": "milan",
    "atletico": "atlético madrid",
    "atletico madrid": "atlético madrid",
    "athletic bilbao": "athletic club",
    "bayern": "bayern munich",
    "munich": "bayern munich",
    "leverkusen": "bayer leverkusen",
    "dortmund": "borussia dortmund",
    "frankfurt": "eintracht frankfurt",
    "barca": "barcelona",
    "real": "real madrid",
    "madrid": "real madrid",
    "sociedad": "real sociedad",
    "villa": "aston villa",
    "palace": "crystal palace",
    # NBA
    "sixers": "philadelphia 76ers",
    "76ers": "philadelphia 76ers",
    "mavs": "dallas mavericks",
    "timberwolves": "minnesota timberwolves",
    "blazers": "portland trail blazers",
    "knicks": "new york knicks",
    "dubs": "golden state warriors",
    "cavs": "cleveland cavaliers",
    "clips": "la clippers",
    "clippers": "la clippers",
    "lakers": "los angeles lakers",
    "pels": "new orleans pelicans",
    "thunder": "oklahoma city thunder",
    "okc": "oklahoma city thunder",
    # NHL
    "habs": "montreal canadiens",
    "leafs": "toronto maple leafs",
    "bolts": "tampa bay lightning",
    "pens": "pittsburgh penguins",
    "caps": "washington capitals",
    "avs": "colorado avalanche",
    "golden knights": "vegas golden knights",
    "knights": "vegas golden knights",
    "red wings": "detroit red wings",
    "blue jackets": "columbus blue jackets",
}

TRACKING_STRIP_PHRASES: Tuple[str, ...] = (
    "can you track", "please track", "start tracking", "stop tracking",
    "keep me updated on", "keep me posted on", "let me know when",
    "live updates for", "live updates on", "track the", "watch the",
    "monitor the", "follow the", "right now", "currently playing",
    "this game", "that game", "the game", "this match", "that match", "the match",
)

TRACKING_STRIP_WORDS: Tuple[str, ...] = (
    "track", "tracking", "untrack", "watch", "monitor", "follow",
    "live", "updates", "update", "score", "scores", "game", "games", "match", "matches",
    "current", "currently", "playing", "today", "tonight", "next",
    "the", "this", "that", "it", "same", "again", "now", "right",
    "for", "on", "in", "please", "me", "us",
)

ESPN_TO_ODDS_API_LEAGUE: Dict[str, str] = {
    "uefa.champions": "soccer_uefa_champs_league",
    "ucl": "soccer_uefa_champs_league",
    "uefa.europa": "soccer_uefa_europa_league",
    "uefa.europa.conf": "soccer_uefa_europa_conference_league",
    "eng.1": "soccer_epl",
    "epl": "soccer_epl",
    "eng.2": "soccer_efl_champ",
    "esp.1": "soccer_spain_la_liga",
    "laliga": "soccer_spain_la_liga",
    "ger.1": "soccer_germany_bundesliga",
    "bundesliga": "soccer_germany_bundesliga",
    "ita.1": "soccer_italy_serie_a",
    "seriea": "soccer_italy_serie_a",
    "fra.1": "soccer_france_ligue_one",
    "ligue1": "soccer_france_ligue_one",
    "usa.1": "soccer_usa_mls",
    "mls": "soccer_usa_mls",
    "ned.1": "soccer_netherlands_eredivisie",
    "por.1": "soccer_portugal_primeira_liga",
    "nba": "basketball_nba",
    "wnba": "basketball_wnba",
    "mens-college-basketball": "basketball_ncaab",
    "nhl": "icehockey_nhl",
    "nfl": "americanfootball_nfl",
    "college-football": "americanfootball_ncaaf",
    "mlb": "baseball_mlb",
    "ufc": "mma_mixed_martial_arts",
}

CS_TEAMS: Tuple[str, ...] = (
    "falcons", "team falcons", "nip", "ninjas in pyjamas", "spirit", "team spirit",
    "vitality", "team vitality", "mouz", "mousesports", "furia", "faze", "g2",
    "navi", "natus vincere", "astralis", "complexity", "mongolz", "heroic",
    "eternal fire", "virtus.pro", "9z", "aurora", "betboom", "parivision",
    "thunderpick", "blast", "pgl", "iem", "esl", "cs", "cs2", "csgo",
)

CS_KEYWORDS: frozenset[str] = frozenset({
    "cs", "cs2", "csgo", "counter-strike", "counter strike", "esports", "e-sports",
    "falcons", "nip", "ninjas in pyjamas", "vitality", "spirit", "team spirit",
    "navi", "natus vincere", "furia", "mouz", "faze", "g2", "virtus.pro",
    "astralis", "complexity", "the mongolz", "mongolz", "heroic", "eternal fire",
    "thunderpick", "thunderpicks", "blast", "pgl", "iem", "esl",
})

F1_DRIVERS: Tuple[str, ...] = (
    "max verstappen", "verstappen", "lando norris", "norris", "charles leclerc", "leclerc",
    "oscar piastri", "piastri", "lewis hamilton", "hamilton", "george russell", "russell",
    "kimi antonelli", "antonelli", "carlos sainz", "sainz", "fernando alonso", "alonso",
    "alex albon", "albon", "pierre gasly", "gasly", "esteban ocon", "ocon",
    "nico hulkenberg", "hulkenberg", "yuki tsunoda", "tsunoda", "lance stroll", "stroll",
    "oliver bearman", "bearman", "liam lawson", "lawson", "isack hadjar", "hadjar",
    "gabriel bortoleto", "bortoleto", "franco colapinto", "colapinto", "arvid lindblad",
    "sergio perez", "perez", "valtteri bottas", "bottas",
)

ODDS_STRIP_PHRASES: Tuple[str, ...] = (
    "if i bet", "i bet", "bet on", "will i win and how much",
    "will i win", "how much", "what are the", "can you check",
    "right now", "next game", "next match", "moneyline", "betting line",
    "betting odds", "live odds", "match odds", "game odds",
    "point spread", "over under", "tracked games", "tracked matches",
    "tracked game", "tracked match", "both games", "both matches",
    "these games", "these matches", "the games", "the matches",
)

ODDS_STRIP_WORDS: Tuple[str, ...] = (
    "bet", "betting", "odds", "odd", "line", "lines", "spread", "moneyline",
    "tomorrow", "today", "tonight", "live", "now", "currently",
    "next", "match", "matches", "game", "games", "tracked", "tracking", "track",
    "soccer", "football", "basketball", "hockey", "nhl", "nba", "nfl", "mlb",
    "dollars", "bucks", "usd", "on", "for", "the", "in", "at", "of", "are", "is",
    "what", "whats", "please", "both", "these", "those", "get", "check", "why", "cant",
)

SPORTS_INTENT_STOPWORDS: frozenset[str] = frozenset({
    "roster", "squad", "schedule", "fixtures", "fixture", "standings", "table",
    "rank", "points", "injuries", "injured", "injury", "lineup", "lineups",
    "starting", "xi", "formation", "stats", "statistics", "stat", "scores",
    "score", "results", "result", "yesterday", "tomorrow", "today", "play",
    "playing", "played", "next", "previous", "past", "recent", "upcoming",
    "whats", "what", "is", "are", "the", "this", "that", "it", "its", "their",
    "they", "them", "his", "her", "status", "of", "game", "games", "match", "matches",
    "hows", "how", "going", "who", "scored", "last", "goal", "goals", "time", "minute",
    "still", "live", "not", "over", "currently", "on", "right", "now",
    "track", "tracking", "tracked", "untrack", "stop", "start", "monitor", "follow",
    "odds", "odd", "bet", "betting", "moneyline", "spread", "line", "lines",
    "check", "get", "both", "these", "those",
    "please", "can", "you", "for", "me", "in", "at",
})

GENERIC_SINGLE_WORD_QUERIES: frozenset[str] = frozenset({
    "", "schedule", "games", "game", "matches", "match", "scores", "score", "status", "info", "upcoming", "next"
})

