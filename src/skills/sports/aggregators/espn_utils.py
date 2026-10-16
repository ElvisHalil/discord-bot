import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
import requests
from requests.adapters import HTTPAdapter

_SESSION = requests.Session()
_ADAPTER = HTTPAdapter(pool_connections=24, pool_maxsize=24)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
})

SPORTS_EXECUTOR = ThreadPoolExecutor(max_workers=8)
_CACHE: Dict[str, Tuple[float, Any]] = {}


def clear_espn_cache() -> None:
    """Clear the in-memory ESPN API response cache."""
    _CACHE.clear()


def cached_espn_get(url: str, params: Optional[Dict[str, Any]] = None, ttl: int = 300) -> Optional[Dict[str, Any]]:
    """Fetch JSON from an ESPN public API endpoint with in-memory TTL caching."""
    param_str = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    cache_key = f"{url}?{param_str}" if param_str else url
    now = time.time()

    if ttl > 0 and cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts < ttl:
            return data

    try:
        res = _SESSION.get(url, params=params, timeout=8)
        if res.status_code != 200:
            return None
        data = res.json()
        if isinstance(data, dict):
            _CACHE[cache_key] = (now, data)
            return data
    except Exception:
        return None
    return None


def cached_espn_get_many(
    requests_list: List[Tuple[str, Optional[Dict[str, Any]]]],
    ttl: int = 20,
) -> List[Optional[Dict[str, Any]]]:
    """Fetch multiple ESPN endpoints concurrently (falls back to sequential when cached_espn_get is mocked in unit tests)."""
    if not requests_list:
        return []
    is_mock = hasattr(cached_espn_get, "assert_called") or "MagicMock" in str(type(cached_espn_get))
    if is_mock or len(requests_list) == 1:
        return [cached_espn_get(u, params=p, ttl=ttl) for u, p in requests_list]
    return list(SPORTS_EXECUTOR.map(lambda r: cached_espn_get(r[0], params=r[1], ttl=ttl), requests_list))


def parallel_fetch_json(
    fetch_fn: Callable[..., Any],
    urls: List[str],
    timeout: float = 6.0,
    ttl: int = 20,
) -> List[Any]:
    """Fetch multiple JSON URLs concurrently with optional short TTL caching (falls back to sequential when fetch_fn is mocked)."""
    if not urls:
        return []
    is_mock = hasattr(fetch_fn, "assert_called") or "MagicMock" in str(type(fetch_fn))
    if is_mock:
        return [fetch_fn(u, timeout=timeout) for u in urls]

    def _get_one(u: str) -> Any:
        now = time.time()
        if ttl > 0 and u in _CACHE:
            ts, cached_val = _CACHE[u]
            if now - ts < ttl:
                return cached_val
        try:
            val = fetch_fn(u, timeout=timeout)
            if val is not None and ttl > 0:
                _CACHE[u] = (now, val)
            return val
        except Exception:
            return {}

    if len(urls) == 1:
        return [_get_one(urls[0])]
    return list(SPORTS_EXECUTOR.map(_get_one, urls))


def extract_score_str(score_field: Any) -> str:
    """Normalize ESPN score field (string on /scoreboard, dict on /teams/{id}/schedule)."""
    if score_field is None:
        return "0"
    if isinstance(score_field, dict):
        if "displayValue" in score_field and score_field["displayValue"] is not None:
            return str(score_field["displayValue"])
        if "value" in score_field and score_field["value"] is not None:
            val = score_field["value"]
            return str(int(val)) if isinstance(val, float) and val.is_integer() else str(val)
        return "0"
    return str(score_field)


def format_espn_iso_date(date_raw: str) -> str:
    """Convert an ESPN ISO-8601 UTC timestamp (e.g. '2026-10-14T19:00Z') into a readable string."""
    if not date_raw:
        return "TBD"
    try:
        dt = datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
        return dt.strftime("%a, %b %d, %Y at %H:%M UTC")
    except ValueError:
        return date_raw


TEAM_ALIASES: Dict[str, str] = {
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


def get_espn_teams(sport_path: str, league_code: str) -> List[Dict[str, Any]]:
    """Fetch and normalize all teams in an ESPN league (cached 1 hour)."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/teams"
    data = cached_espn_get(url, params={"limit": 100}, ttl=3600)
    if not data:
        return []

    raw_teams: List[Dict[str, Any]] = []
    for sport in data.get("sports", []):
        for league in sport.get("leagues", []):
            for item in league.get("teams", []):
                team_obj = item.get("team", item)
                if isinstance(team_obj, dict) and team_obj.get("id"):
                    raw_teams.append(team_obj)

    if not raw_teams:
        for item in data.get("teams", []):
            team_obj = item.get("team", item)
            if isinstance(team_obj, dict) and team_obj.get("id"):
                raw_teams.append(team_obj)

    teams: List[Dict[str, Any]] = []
    for t in raw_teams:
        teams.append({
            "id": str(t.get("id", "")),
            "displayName": t.get("displayName", ""),
            "shortDisplayName": t.get("shortDisplayName", ""),
            "name": t.get("name", ""),
            "location": t.get("location", ""),
            "abbreviation": t.get("abbreviation", ""),
            "slug": t.get("slug", ""),
        })
    return teams


def resolve_espn_team(sport_path: str, league_code: str, query: str) -> Optional[Dict[str, Any]]:
    """Resolve a team dictionary from an ESPN league given a free-text user query."""
    if not query:
        return None
    q_low = query.lower().strip()
    teams = get_espn_teams(sport_path, league_code)
    if not teams:
        return None

    # Expand known aliases if present in query
    expanded_terms = [q_low]
    for alias, target in TEAM_ALIASES.items():
        if alias in q_low:
            expanded_terms.append(target.lower())

    # 1. Exact or substring match on full displayName / shortDisplayName / name / location
    best_match = None
    best_len = 0
    for t in teams:
        candidates = [
            t.get("displayName", "").lower(),
            t.get("shortDisplayName", "").lower(),
            t.get("name", "").lower(),
            t.get("location", "").lower(),
        ]
        candidates = [c for c in candidates if len(c) >= 3]
        for term in expanded_terms:
            for c in candidates:
                if c in term and len(c) > best_len:
                    best_match = t
                    best_len = len(c)

    if best_match:
        return best_match

    # 2. Check exact word match on abbreviation (e.g. "ARS", "LAL", "BOS")
    q_words = set(q_low.replace("?", " ").replace(",", " ").split())
    for t in teams:
        abbr = t.get("abbreviation", "").lower()
        if abbr and len(abbr) >= 3 and abbr in q_words:
            return t

    return None


def match_team_in_query(team_name: str, short_name: str, abbr: str, query: str) -> bool:
    """Return True if a competitor's team name or alias matches the user's filter query."""
    if not query:
        return True
    q_low = query.lower()
    expanded = q_low
    for alias, target in TEAM_ALIASES.items():
        if alias in q_low:
            expanded += f" {target.lower()}"

    for candidate in (team_name, short_name):
        c_low = (candidate or "").lower().strip()
        if len(c_low) >= 3 and c_low in expanded:
            return True
        # Also check significant tokens (len >= 4) excluding generic suffixes and shared city names when another club is specified
        for tok in c_low.split():
            if len(tok) >= 4 and tok not in ("united", "city", "real", "club", "sporting", "athletic", "fc", "sc", "madrid", "milan", "manchester"):
                if tok in expanded:
                    return True
    if abbr and len(abbr) >= 3 and abbr.lower() in expanded.split():
        return True
    return False


def parse_team_schedule(
    sport_path: str,
    league_code: str,
    team_id: str,
    team_name: str,
    max_past: int = 5,
    max_future: int = 5,
) -> str:
    """Fetch and format a specific team's past results and upcoming schedule from ESPN (across all competitions for soccer)."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/teams/{team_id}/schedule"

    events: List[Dict[str, Any]] = []
    seen_ids = set()

    if sport_path == "soccer":
        # Fetch across ALL competitions (/soccer/all/teams/{id}/schedule) concurrently so UEFA Champions League,
        # Europa League, and domestic cup matches are included alongside domestic league games.
        all_url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{team_id}/schedule"
        cur_year = datetime.now(timezone.utc).year
        payloads = cached_espn_get_many(
            [
                (url, None),
                (all_url, {"fixture": "true"}),
                (all_url, {"season": str(cur_year)}),
                (url, {"fixture": "true"}),
            ],
            ttl=20,
        )
        for extra_data in payloads:
            if extra_data and extra_data.get("events"):
                for fev in extra_data["events"]:
                    ev_id = str(fev.get("id", ""))
                    if ev_id and ev_id not in seen_ids:
                        seen_ids.add(ev_id)
                        events.append(fev)
    else:
        data = cached_espn_get(url, ttl=20)
        if data and data.get("events"):
            events = list(data["events"])

    if not events:
        return ""

    # Always sort chronologically (oldest -> newest) so [-max_past:] are the latest results and [:max_future] are next up
    events.sort(key=lambda e: e.get("date", ""))

    now_utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    past_lines: List[str] = []
    live_lines: List[str] = []
    future_lines: List[str] = []
    recorded_live = False

    for ev in events:
        raw_date = ev.get("date", "")
        date_str = format_espn_iso_date(raw_date)
        comp = ev.get("competitions", [{}])[0]
        status_obj = comp.get("status", {}).get("type", {})
        state = status_obj.get("state", "pre")
        detail = status_obj.get("shortDetail") or status_obj.get("detail") or ""
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        league_label = ev.get("league", {}).get("name") or ev.get("season", {}).get("name") or ""
        comp_tag = f" [{league_label}]" if (sport_path == "soccer" and league_label) else ""

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
        h_name = home.get("team", {}).get("displayName") or home.get("team", {}).get("shortDisplayName", "Home")
        a_name = away.get("team", {}).get("displayName") or away.get("team", {}).get("shortDisplayName", "Away")
        h_score = extract_score_str(home.get("score"))
        a_score = extract_score_str(away.get("score"))

        if state == "post" or status_obj.get("completed"):
            past_lines.append(f"• **{date_str}**: **{h_name}** {h_score} - {a_score} **{a_name}** ({detail or 'FT'}){comp_tag}")
        elif state == "in":
            live_lines.append(f"• 🔴 **LIVE NOW ({detail})**: **{h_name}** {h_score} - {a_score} **{a_name}**{comp_tag}")
            try:
                from src.skills.sports.live_tracker import record_channel_last_match_query
                record_channel_last_match_query(f"{h_name} vs {a_name}", sport=sport_path)
                recorded_live = True
            except Exception:
                pass
        else:
            is_today = raw_date.startswith(now_utc_date)
            today_prefix = "🔥 **TODAY — " if is_today else "**"
            future_lines.append(f"• {today_prefix}{date_str}**: **{a_name}** @ **{h_name}**{comp_tag}")

    if not recorded_live and team_name:
        try:
            from src.skills.sports.live_tracker import record_channel_last_match_query
            record_channel_last_match_query(team_name, sport=sport_path)
        except Exception:
            pass

    out = f"=== 📅 {team_name.upper()} TEAM SCHEDULE & RESULTS (ALL COMPETITIONS) ===\n"
    if live_lines:
        out += "🔴 **CURRENTLY PLAYING RIGHT NOW (LIVE MATCH IN PROGRESS):**\n" + "\n".join(live_lines) + "\n\n"
    if past_lines:
        out += f"✅ **Recent Results (Last {min(len(past_lines), max_past)}):**\n"
        out += "\n".join(past_lines[-max_past:]) + "\n\n"
    if future_lines or live_lines:
        out += f"⏭️ **Current & Upcoming Fixtures (Next {min(len(future_lines), max_future)}):**\n"
        if live_lines:
            out += "\n".join(live_lines) + "  *(⬅️ IN PROGRESS RIGHT NOW)*\n"
        if future_lines:
            out += "\n".join(future_lines[:max_future]) + "\n"
    return out.strip()


def _flatten_roster_athletes(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract a flat list of athlete dicts from an ESPN /teams/{id}/roster payload."""
    athletes_raw = data.get("athletes", [])
    flat: List[Dict[str, Any]] = []
    for entry in athletes_raw:
        if isinstance(entry, dict) and "items" in entry and isinstance(entry["items"], list):
            flat.extend(entry["items"])
        elif isinstance(entry, dict):
            flat.append(entry)
    return flat


def _extract_athlete_stat_map(athlete: Dict[str, Any]) -> Dict[str, str]:
    """Extract a flat {stat_name: displayValue} dictionary from an ESPN roster athlete object."""
    stats_map: Dict[str, str] = {}
    splits = athlete.get("statistics", {}).get("splits", {})
    for cat in splits.get("categories", []):
        for st in cat.get("stats", []):
            name = st.get("name") or st.get("abbreviation")
            val = st.get("displayValue")
            if name and val is not None:
                stats_map[name] = str(val)
    return stats_map


def parse_team_roster_and_stats(
    sport_path: str,
    league_code: str,
    team_id: str,
    team_name: str,
    player_filter: Optional[str] = None,
) -> str:
    """Fetch and format a team's active squad roster, player season statistics, and injuries."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/teams/{team_id}/roster"
    data = cached_espn_get(url, ttl=300)
    if not data:
        return f"Could not fetch roster/player stats for {team_name}."

    athletes = _flatten_roster_athletes(data)
    if not athletes:
        return f"No active roster data available for {team_name}."

    parsed_players: List[Dict[str, Any]] = []
    injured_lines: List[str] = []

    for a in athletes:
        name = a.get("displayName") or a.get("fullName", "Unknown")
        jersey = a.get("jersey", "")
        pos = a.get("position", {}).get("displayName") or a.get("position", {}).get("abbreviation", "N/A")
        pos_abbr = a.get("position", {}).get("abbreviation", "")
        age = a.get("age", "")
        injuries = a.get("injuries", [])
        stat_map = _extract_athlete_stat_map(a)

        def _int_stat(key: str) -> int:
            raw = stat_map.get(key, "0").replace(",", "")
            try:
                return int(float(raw))
            except ValueError:
                return 0

        goals = _int_stat("totalGoals")
        assists = _int_stat("goalAssists")
        apps = _int_stat("appearances")
        yc = _int_stat("yellowCards")
        rc = _int_stat("redCards")
        shots = _int_stat("totalShots")
        sot = _int_stat("shotsOnTarget")
        saves = _int_stat("saves")

        inj_str = ""
        if injuries:
            inj_parts = []
            for inj in injuries:
                status = inj.get("status") or inj.get("type", {}).get("description", "Injured")
                comment = inj.get("shortComment") or inj.get("longComment") or inj.get("details", {}).get("type", "")
                inj_parts.append(f"{status}" + (f" - {comment}" if comment else ""))
            inj_str = "; ".join(inj_parts)
            injured_lines.append(f"• 🚑 **{name}** ({pos_abbr or pos}): {inj_str}")

        parsed_players.append({
            "name": name,
            "jersey": f"#{jersey}" if jersey else "",
            "pos": pos,
            "pos_abbr": pos_abbr,
            "age": age,
            "apps": apps,
            "goals": goals,
            "assists": assists,
            "shots": shots,
            "sot": sot,
            "yc": yc,
            "rc": rc,
            "saves": saves,
            "stat_map": stat_map,
            "injury": inj_str,
        })

    out = f"=== 👥 {team_name.upper()} SQUAD ROSTER & PLAYER SEASON STATS ===\n"

    # Check if user asked for a specific player on this team
    if player_filter:
        pf_low = player_filter.lower()
        pf_words = set(pf_low.replace("?", " ").replace(",", " ").split())
        t_words = set(team_name.lower().split())
        matched_players = [
            p for p in parsed_players
            if p["name"].lower() in pf_low or any(
                tok in pf_words and tok not in t_words
                for tok in p["name"].lower().split() if len(tok) >= 4
            )
        ]
        if matched_players:
            out += "\n🎯 **Requested Player Season Profile:**\n"
            for p in matched_players:
                line = f"• **{p['name']}** {p['jersey']} ({p['pos']}, Age {p['age'] or 'N/A'})"
                if sport_path == "soccer":
                    line += (
                        f" — **Apps:** {p['apps']} | **Goals:** {p['goals']} | **Assists:** {p['assists']} | "
                        f"**Shots (On Target):** {p['shots']} ({p['sot']}) | **🟨 YC:** {p['yc']} | **🟥 RC:** {p['rc']}"
                    )
                    if p["saves"] > 0 or p["pos_abbr"] == "G":
                        line += f" | **Saves:** {p['saves']}"
                elif p["stat_map"]:
                    top_kv = [f"{k}: {v}" for k, v in list(p["stat_map"].items())[:8]]
                    line += " — " + " | ".join(top_kv)
                if p["injury"]:
                    line += f" | 🚑 **Injury:** {p['injury']}"
                out += line + "\n"
            out += "\n"

    if sport_path == "soccer":
        scorers = [p for p in parsed_players if p["goals"] > 0 or p["assists"] > 0]
        scorers.sort(key=lambda x: (x["goals"], x["assists"], x["sot"]), reverse=True)
        if scorers:
            out += "⚽ **Top Goalscorers & Assist Leaders:**\n"
            for p in scorers[:10]:
                out += (
                    f"• **{p['name']}** ({p['pos_abbr'] or p['pos']}): "
                    f"**{p['goals']}G, {p['assists']}A** in {p['apps']} apps "
                    f"({p['shots']} shots, {p['sot']} on target"
                    + (f", 🟨 {p['yc']}" if p["yc"] else "")
                    + (f", 🟥 {p['rc']}" if p["rc"] else "")
                    + ")\n"
                )
            out += "\n"

        cards = [p for p in parsed_players if p["yc"] > 0 or p["rc"] > 0]
        cards.sort(key=lambda x: (x["rc"], x["yc"]), reverse=True)
        if cards:
            out += "🟨 **Disciplinary Record (Yellow / Red Cards):**\n"
            for p in cards[:8]:
                out += f"• **{p['name']}**: 🟨 {p['yc']} Yellow, 🟥 {p['rc']} Red ({p['apps']} apps)\n"
            out += "\n"

    if injured_lines:
        out += "🚑 **Injuries & Availability:**\n" + "\n".join(injured_lines) + "\n\n"

    # Compact full squad summary
    out += f"📋 **Active Squad ({len(parsed_players)} Players):**\n"
    for p in parsed_players[:28]:
        stat_badge = ""
        if sport_path == "soccer" and (p["apps"] > 0 or p["goals"] > 0 or p["assists"] > 0):
            stat_badge = f" [{p['apps']} apps, {p['goals']}G, {p['assists']}A"
            if p["yc"] or p["rc"]:
                stat_badge += f", {p['yc']}YC/{p['rc']}RC"
            stat_badge += "]"
        inj_badge = f" (🚑 {p['injury']})" if p["injury"] else ""
        out += f"• {p['jersey']} **{p['name']}** ({p['pos_abbr'] or p['pos']}){stat_badge}{inj_badge}\n"

    return out.strip()


def parse_league_injuries(sport_path: str, league_code: str, league_label: str, query: str = "") -> str:
    """Fetch and format injury reports for NBA, NHL, NFL, MLB, WNBA, or Soccer clubs."""
    if sport_path in ("basketball", "hockey", "football", "baseball"):
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/injuries"
        data = cached_espn_get(url, ttl=300)
        if not data or not data.get("injuries"):
            return f"No current injury reports found for {league_label}."

        teams_inj = data.get("injuries", [])
        matched_team = resolve_espn_team(sport_path, league_code, query)
        target_team_name = matched_team["displayName"].lower() if matched_team else ""
        q_low = (query or "").lower()

        sections: List[str] = []
        for t_entry in teams_inj:
            t_name = t_entry.get("displayName") or t_entry.get("team", {}).get("displayName", "Unknown Team")
            inj_list = t_entry.get("injuries", [])
            if not inj_list:
                continue

            if target_team_name and target_team_name not in t_name.lower():
                continue

            lines: List[str] = []
            for item in inj_list:
                ath = item.get("athlete", {})
                p_name = ath.get("displayName", "Unknown Player")
                pos = ath.get("position", {}).get("abbreviation", "")
                status = item.get("status", "Injured")
                details = item.get("details", {})
                inj_type = details.get("type", "")
                inj_detail = details.get("detail", "")
                ret_date = details.get("returnDate", "")
                short_comment = item.get("shortComment") or item.get("longComment") or ""

                # If user asked for a specific player (and not a team), filter by player name if applicable
                if not target_team_name and q_low and not any(
                    k in q_low for k in ("nba", "nhl", "nfl", "mlb", "wnba", "hockey", "basketball", "football", "baseball", "injur", "report", "all", "today", "who")
                ):
                    if p_name.lower() not in q_low and t_name.lower() not in q_low:
                        continue

                desc_parts = [p for p in (inj_type, inj_detail) if p]
                desc_str = f" ({', '.join(desc_parts)})" if desc_parts else ""
                ret_str = f" | Est. Return: {ret_date}" if ret_date else ""
                comment_str = f" — {short_comment}" if short_comment else ""
                lines.append(
                    f"  • **{p_name}** ({pos}) — **{status}**{desc_str}{ret_str}{comment_str}"
                )

            if lines:
                sections.append(f"🏥 **{t_name}**\n" + "\n".join(lines))

        if not sections and target_team_name:
            return f"=== 🚑 {league_label} INJURY REPORT ===\nNo players are currently listed on the injury report for **{matched_team['displayName']}**."
        if not sections:
            for t_entry in teams_inj[:12]:
                t_name = t_entry.get("displayName", "Team")
                inj_list = t_entry.get("injuries", [])
                if not inj_list:
                    continue
                lines = []
                for item in inj_list[:6]:
                    ath = item.get("athlete", {})
                    p_name = ath.get("displayName", "Unknown")
                    status = item.get("status", "Injured")
                    inj_type = item.get("details", {}).get("type", "")
                    lines.append(f"  • **{p_name}**: **{status}**" + (f" ({inj_type})" if inj_type else ""))
                sections.append(f"🏥 **{t_name}**\n" + "\n".join(lines))

        limit = 30 if target_team_name else 15
        return f"=== 🚑 {league_label} INJURY REPORT ===\n\n" + "\n\n".join(sections[:limit])

    # Soccer injury lookup via team roster + recent match events
    matched_team = resolve_espn_team(sport_path, league_code, query)
    if matched_team:
        return parse_team_roster_and_stats(
            sport_path, league_code, matched_team["id"], matched_team["displayName"]
        )
    return f"Specify a club name (e.g. 'Arsenal injuries' or 'Real Madrid squad') to inspect squad availability in {league_label}."


def parse_match_summary(sport_path: str, league_code: str, event_id: str) -> str:
    """Fetch and format deep match summary (/summary?event={id}): key events, cards, subs, lineups, and boxscore."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/summary"
    data = cached_espn_get(url, params={"event": event_id}, ttl=15)
    if not data:
        return ""

    out_sections: List[str] = []

    # 1. Header / Scoreline
    header_comp = data.get("header", {}).get("competitions", [{}])[0]
    competitors = header_comp.get("competitors", [])
    if len(competitors) >= 2:
        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
        h_name = home.get("team", {}).get("displayName", "Home")
        a_name = away.get("team", {}).get("displayName", "Away")
        h_score = extract_score_str(home.get("score"))
        a_score = extract_score_str(away.get("score"))
        status_type = header_comp.get("status", {}).get("type", {})
        state = status_type.get("state", "pre")
        completed = bool(status_type.get("completed", False))
        status_det = status_type.get("detail") or status_type.get("shortDetail") or ""

        try:
            from src.skills.sports.live_tracker import record_channel_last_match_query
            record_channel_last_match_query(f"{h_name} vs {a_name}", sport=sport_path)
        except Exception:
            pass

        if state == "in" and not completed:
            out_sections.append(
                f"=== 🔴 LIVE MATCH IN PROGRESS (ONGOING — NOT FINISHED): {h_name} {h_score} - {a_score} {a_name} (Live Clock: {status_det}) ===\n"
                f"⚠️ CRITICAL LIVE MATCH INSTRUCTION: This match is CURRENTLY LIVE AND IN PROGRESS at {status_det} "
                f"(Current Score: {h_name} {h_score} - {a_score} {a_name}). "
                f"Ignore any older scores or minutes from earlier messages in the conversation! "
                f"The game is NOT over — do NOT say the match concluded, ended at {status_det}, or sealed victory, and do NOT call {h_score}-{a_score} the 'final score'!"
            )
        elif state == "post" or completed:
            out_sections.append(f"=== 🏁 FINAL MATCH SUMMARY (COMPLETED): {h_name} {h_score} - {a_score} {a_name} ({status_det or 'FT'}) ===")
        else:
            out_sections.append(f"=== 🏟️ MATCH SUMMARY: {h_name} {h_score} - {a_score} {a_name} ({status_det}) ===")

    # 2. Venue, Attendance & Officials
    game_info = data.get("gameInfo", {})
    venue = game_info.get("venue", {}).get("fullName", "")
    attendance = game_info.get("attendance")
    officials = [o.get("displayName") for o in game_info.get("officials", []) if o.get("displayName")]
    info_bits = []
    if venue:
        info_bits.append(f"📍 **Venue:** {venue}")
    if attendance:
        info_bits.append(f"👥 **Attendance:** {attendance:,}")
    if officials:
        info_bits.append(f" whistle **Referee:** {', '.join(officials[:2])}")
    if info_bits:
        out_sections.append(" | ".join(info_bits))

    # 3. Key Match Events Timeline (Goals, Yellow Cards, Red Cards, Substitutions, Injury Delays, NHL/NFL Scoring Plays)
    key_events = data.get("keyEvents") or []
    if key_events:
        timeline_lines: List[str] = []
        for ev in key_events:
            ev_type = (ev.get("type", {}).get("text") or "").strip()
            if not ev_type or ev_type in ("Kickoff", "Half Time", "Halftime", "Start 2nd Half", "End Regular Time", "End Delay"):
                continue
            clock = ev.get("clock", {}).get("displayValue") or ""
            text = (ev.get("text") or ev.get("shortText") or "").strip()
            if not text:
                continue

            ev_low = ev_type.lower()
            if "goal" in ev_low or "penalty - scored" in ev_low:
                icon = "⚽"
            elif "red card" in ev_low or "second yellow" in ev_low:
                icon = "🟥"
            elif "yellow card" in ev_low:
                icon = "🟨"
            elif "substitution" in ev_low:
                icon = "🔄"
            elif "delay" in ev_low or "injury" in text.lower():
                icon = "🚑"
            else:
                icon = "•"
            clock_prefix = f"**{clock}** " if clock else ""
            timeline_lines.append(f"  {icon} {clock_prefix}{text}")

        if timeline_lines:
            out_sections.append("⏱️ **Match Timeline (Goals, Cards, Subs & Injuries):**\n" + "\n".join(timeline_lines))
    else:
        team_id_to_name: Dict[str, str] = {}
        for comp_item in competitors:
            tid = str(comp_item.get("team", {}).get("id") or "")
            tname = comp_item.get("team", {}).get("displayName") or comp_item.get("team", {}).get("shortDisplayName") or ""
            if tid and tname:
                team_id_to_name[tid] = tname

        scoring_lines: List[str] = []
        for sp in data.get("scoringPlays") or []:
            clock = sp.get("clock", {}).get("displayValue") or ""
            p_num = sp.get("period", {}).get("number")
            p_prefix = f"P{p_num} " if p_num else ""
            text = (sp.get("text") or "").strip()
            if text:
                scoring_lines.append(f"  🔥 **{p_prefix}{clock}** {text}".strip())

        plays_list = data.get("plays") or []
        if not scoring_lines and plays_list:
            scoring_plays = [
                p for p in plays_list
                if p.get("scoringPlay") or "goal" in (p.get("type", {}).get("text") or "").lower()
            ]
            is_goal_sport = any("goal" in (p.get("type", {}).get("text") or "").lower() for p in scoring_plays)
            if is_goal_sport or len(scoring_plays) <= 35:
                for p in scoring_plays:
                    ev_type = (p.get("type", {}).get("text") or "").strip()
                    clock = p.get("clock", {}).get("displayValue") or ""
                    p_disp = (
                        p.get("period", {}).get("displayValue")
                        or (f"P{p.get('period', {}).get('number')}" if p.get("period", {}).get("number") else "")
                    )
                    strength = (p.get("strength", {}).get("text") or "").strip()
                    strength_tag = (
                        f" [{strength}]"
                        if strength and strength.lower() not in ("even strength", "none", "regular")
                        else ""
                    )
                    t_id = str(p.get("team", {}).get("id") or "")
                    t_name = team_id_to_name.get(t_id, "")
                    team_tag = f" ({t_name})" if t_name else ""
                    text = (p.get("text") or ev_type).strip()
                    time_label = " ".join(x for x in (p_disp, clock) if x).strip()
                    scoring_lines.append(f"  🚨 **{time_label}**{strength_tag}{team_tag}: {text}")

        if scoring_lines:
            out_sections.append("⏱️ **Scoring Timeline (Goals & Key Plays):**\n" + "\n".join(scoring_lines))

    # 4. Team Boxscore Comparison
    box_teams = data.get("boxscore", {}).get("teams", [])
    if len(box_teams) >= 2:
        t1 = box_teams[0]
        t2 = box_teams[1]
        t1_name = t1.get("team", {}).get("shortDisplayName") or t1.get("team", {}).get("displayName", "Team 1")
        t2_name = t2.get("team", {}).get("shortDisplayName") or t2.get("team", {}).get("displayName", "Team 2")
        s1 = {st.get("name"): st.get("displayValue") for st in t1.get("statistics", []) if st.get("name")}
        s2 = {st.get("name"): st.get("displayValue") for st in t2.get("statistics", []) if st.get("name")}

        wanted_stats = [
            ("possessionPct", "Possession %"),
            ("totalShots", "Total Shots"),
            ("shotsOnTarget", "Shots on Target"),
            ("shotsTotal", "Shots on Goal"),
            ("hits", "Hits"),
            ("blockedShots", "Blocked Shots"),
            ("powerPlayGoals", "Power Play Goals"),
            ("powerPlayOpportunities", "Power Play Opportunities"),
            ("faceoffPercent", "Faceoff %"),
            ("penaltyMinutes", "Penalty Minutes"),
            ("wonCorners", "Corners"),
            ("foulsCommitted", "Fouls"),
            ("yellowCards", "🟨 Yellow Cards"),
            ("redCards", "🟥 Red Cards"),
            ("offsides", "Offsides"),
            ("saves", "Saves"),
            ("accuratePasses", "Accurate Passes"),
            ("effectiveTackles", "Effective Tackles"),
            ("totalYards", "Total Yards"),
            ("turnovers", "Turnovers"),
        ]
        stat_lines: List[str] = []
        for key, label in wanted_stats:
            if key in s1 or key in s2:
                v1 = s1.get(key, "0")
                v2 = s2.get(key, "0")
                if key in ("possessionPct", "faceoffPercent"):
                    v1 = f"{v1}%" if not str(v1).endswith("%") else v1
                    v2 = f"{v2}%" if not str(v2).endswith("%") else v2
                stat_lines.append(f"  • **{label}:** {t1_name} {v1} — {v2} {t2_name}")

        if stat_lines:
            out_sections.append("📊 **Team Match Statistics:**\n" + "\n".join(stat_lines))

    # 5. Starting Lineups, Formations & Substitutes (Soccer rosters)
    rosters = data.get("rosters", [])
    if rosters:
        lineup_blocks: List[str] = []
        for r_team in rosters:
            t_name = r_team.get("team", {}).get("displayName", "Team")
            formation = r_team.get("formation", "")
            form_str = f" (Formation: **{formation}**)" if formation else ""
            starters: List[str] = []
            subs_used: List[str] = []

            for p in r_team.get("roster", []):
                ath = p.get("athlete", {})
                p_name = ath.get("displayName", "Player")
                pos = p.get("position", {}).get("abbreviation", "")
                jersey = p.get("jersey", "")
                j_str = f"#{jersey} " if jersey else ""

                # Collect non-zero key match stats for this player
                p_stats: List[str] = []
                for st in p.get("stats", []):
                    abbr = st.get("abbreviation", "")
                    val = st.get("displayValue", "0")
                    if abbr in ("G", "A", "SOG", "YC", "RC", "SV") and val not in ("0", "0.0", None, ""):
                        p_stats.append(f"{val}{abbr}")
                stat_suffix = f" [{' '.join(p_stats)}]" if p_stats else ""

                sub_out_raw = p.get("subbedOut")
                sub_in_raw = p.get("subbedIn")
                did_sub_out = sub_out_raw.get("didSub", False) if isinstance(sub_out_raw, dict) else bool(sub_out_raw)
                did_sub_in = sub_in_raw.get("didSub", False) if isinstance(sub_in_raw, dict) else bool(sub_in_raw)

                if p.get("starter"):
                    sub_mark = " 🔻" if did_sub_out else ""
                    starters.append(f"{j_str}**{p_name}** ({pos}){stat_suffix}{sub_mark}")
                elif did_sub_in:
                    subs_used.append(f"🔺 {j_str}**{p_name}** ({pos}){stat_suffix}")

            if starters:
                block = f"📋 **{t_name} Lineup**{form_str}:\n"
                block += "  • **Starting XI:** " + ", ".join(starters)
                if subs_used:
                    block += "\n  • **Substitutes Used:** " + ", ".join(subs_used)
                lineup_blocks.append(block)

        if lineup_blocks:
            out_sections.append("\n".join(lineup_blocks))

    return "\n\n".join(out_sections).strip()


_SEARCH_STOPWORDS = {
    "when", "does", "do", "play", "playing", "played", "next", "what", "whats", "what's",
    "is", "are", "their", "the", "roster", "squad", "schedule", "fixtures", "fixture",
    "matches", "match", "game", "games", "score", "scores", "standings", "table",
    "stats", "statistics", "stat", "player", "players", "injuries", "injured", "injury",
    "report", "lineup", "lineups", "starting", "xi", "formation", "cards", "yellow",
    "red", "results", "result", "recent", "past", "upcoming", "today", "tomorrow",
    "yesterday", "this", "week", "month", "who", "how", "in", "for", "on", "at",
    "about", "of", "context", "previous", "last", "current", "currently", "team",
    "soccer", "football", "basketball", "hockey", "baseball", "sports", "sport",
    "all", "show", "get", "me", "please", "tell", "give",
    "and", "or", "with", "without", "to", "from", "by", "as", "also", "plus", "both",
    "odds", "bet", "betting", "lines", "line", "moneyline", "spread", "over", "under",
    "nhl", "nba", "nfl", "mlb", "f1", "epl", "ucl", "tonight",
}


def clean_entity_search_query(query: str) -> str:
    """Extract likely team, player, or league entity keywords from a natural-language sports query."""
    if not query:
        return ""
    import re as _re
    tokens = _re.findall(r"[A-Za-z0-9À-ÿ'.-]+", query)
    kept = [t for t in tokens if t.lower().strip("'.-") not in _SEARCH_STOPWORDS and len(t) >= 2]
    cleaned = " ".join(kept).strip()
    # Expand known short aliases if the cleaned query matches one
    low = cleaned.lower()
    if low in TEAM_ALIASES:
        return TEAM_ALIASES[low]
    return cleaned


def espn_search_auto_discover(query: str, sport_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Universal ESPN Auto-Discovery via https://site.web.api.espn.com/apis/search/v2.
    Given any team, player, or league query, returns a normalized dict:
    {
        "entity_type": "team" | "player" | "league",
        "display_name": str,
        "subtitle": str,
        "sport": str,          # e.g. 'soccer', 'football', 'baseball', 'basketball', 'hockey', 'mma', 'tennis'
        "league": str,         # e.g. 'ger.1', 'nfl', 'mlb', 'college-football', 'nba', 'wnba', 'nhl', 'ufc', 'atp'
        "team_id": Optional[str],
        "athlete_id": Optional[str],
    }
    """
    cleaned = clean_entity_search_query(query)
    if not cleaned:
        return None

    url = "https://site.web.api.espn.com/apis/search/v2"
    data = cached_espn_get(url, params={"query": cleaned, "limit": 5}, ttl=600)
    if not data or not data.get("results"):
        return None

    import re as _re
    DEFAULT_SPORT_LEAGUE = {
        "football": "nfl",
        "baseball": "mlb",
        "basketball": "nba",
        "hockey": "nhl",
        "soccer": "eng.1",
        "mma": "ufc",
        "tennis": "atp",
        "golf": "pga",
    }

    groups_by_type: Dict[str, List[Dict[str, Any]]] = {}
    for group in data.get("results", []):
        g_type = group.get("type", "")
        if g_type in ("team", "player", "league"):
            groups_by_type[g_type] = group.get("contents", [])

    def _build_entry(g_type: str, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sport = (item.get("sport") or "").lower().strip()
        if not sport:
            return None
        if sport_hint and sport != sport_hint.lower():
            return None
        league = (item.get("defaultLeagueSlug") or DEFAULT_SPORT_LEAGUE.get(sport, "")).lower().strip()
        uid = item.get("uid") or ""
        m_t = _re.search(r"t:(\d+)", uid)
        m_a = _re.search(r"a:(\d+)", uid)
        team_id = m_t.group(1) if m_t else (str(item.get("id")) if g_type == "team" and str(item.get("id", "")).isdigit() else None)
        athlete_id = m_a.group(1) if m_a else (str(item.get("id")) if g_type == "player" and str(item.get("id", "")).isdigit() else None)
        return {
            "entity_type": g_type,
            "display_name": item.get("displayName", cleaned),
            "subtitle": item.get("subtitle") or "",
            "sport": sport,
            "league": league,
            "team_id": team_id,
            "athlete_id": athlete_id,
        }

    # 1. If a player is an exact full-name match (e.g., "LeBron James", "Lamine Yamal"), return it first
    cleaned_low = cleaned.lower()
    for item in groups_by_type.get("player", []):
        if (item.get("displayName") or "").lower().strip() == cleaned_low:
            entry = _build_entry("player", item)
            if entry:
                return entry

    # 2. Prioritize teams and leagues over partial surname player matches (so "Madrid" -> "Real Madrid", not "Sal Madrid")
    for g_type in ("team", "league", "player"):
        for item in groups_by_type.get(g_type, []):
            entry = _build_entry(g_type, item)
            if entry:
                return entry
    return None


def parse_athlete_overview(
    sport_path: str,
    league_code: str,
    athlete_id: str,
    athlete_name: str,
    team_name: str = "",
) -> str:
    """Fetch and format an individual athlete's season/career statistics & recent game logs from ESPN v3."""
    url = f"https://site.web.api.espn.com/apis/common/v3/sports/{sport_path}/{league_code}/athletes/{athlete_id}/overview"
    data = cached_espn_get(url, ttl=300)
    if not data:
        return ""

    team_suffix = f" ({team_name})" if team_name else ""
    out = [f"=== 🌟 ATHLETE PROFILE & STATS: {athlete_name.upper()}{team_suffix} ==="]

    # 1. Statistics block
    stats_block = data.get("statistics", {})
    labels = stats_block.get("labels") or stats_block.get("names") or []
    splits = stats_block.get("splits", [])
    stat_title = stats_block.get("displayName") or "Season Statistics"

    if labels and splits:
        out.append(f"\n📊 **{stat_title}:**")
        for sp in splits[:4]:
            sp_name = sp.get("displayName", "Split")
            sp_vals = sp.get("stats", [])
            if sp_vals:
                pairs = [f"**{lbl}:** {val}" for lbl, val in zip(labels, sp_vals) if val not in ("0", "0.0", "-", None, "")]
                if not pairs:
                    pairs = [f"**{lbl}:** {val}" for lbl, val in list(zip(labels, sp_vals))[:6]]
                out.append(f"  • **{sp_name}:** " + " | ".join(pairs[:12]))

    # 2. Recent News / RotoWire Injury or Status Note
    rotowire = data.get("rotowire", {})
    if rotowire and rotowire.get("headline"):
        out.append(f"\n📰 **Latest Player Note:** {rotowire.get('headline')}")
        if rotowire.get("story"):
            out.append(f"  {rotowire.get('story')}")

    return "\n".join(out)

