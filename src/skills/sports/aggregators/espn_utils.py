import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import requests

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
})

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
        res = _SESSION.get(url, params=params, timeout=10)
        if res.status_code != 200:
            return None
        data = res.json()
        if isinstance(data, dict):
            _CACHE[cache_key] = (now, data)
            return data
    except Exception:
        return None
    return None


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
    "leverkusen": "bayer leverkusen",
    "dortmund": "borussia dortmund",
    "barca": "barcelona",
    "real": "real madrid",
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
        # Also check significant tokens (len >= 4) excluding generic suffixes
        for tok in c_low.split():
            if len(tok) >= 4 and tok not in ("united", "city", "real", "club", "sporting", "athletic", "fc", "sc"):
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
    """Fetch and format a specific team's past results and upcoming schedule from ESPN."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/teams/{team_id}/schedule"
    data = cached_espn_get(url, ttl=300)
    if not data:
        return ""

    events = list(data.get("events", []))
    if sport_path == "soccer":
        fix_data = cached_espn_get(url, params={"fixture": "true"}, ttl=300)
        if fix_data and fix_data.get("events"):
            seen_ids = {str(e.get("id")) for e in events if e.get("id")}
            for fev in fix_data["events"]:
                if str(fev.get("id")) not in seen_ids:
                    events.append(fev)

    if not events:
        return ""

    # Always sort chronologically (oldest -> newest) so [-max_past:] are the latest results and [:max_future] are next up
    events.sort(key=lambda e: e.get("date", ""))

    past_lines: List[str] = []
    live_lines: List[str] = []
    future_lines: List[str] = []

    for ev in events:
        date_str = format_espn_iso_date(ev.get("date", ""))
        comp = ev.get("competitions", [{}])[0]
        status_obj = comp.get("status", {}).get("type", {})
        state = status_obj.get("state", "pre")
        detail = status_obj.get("shortDetail") or status_obj.get("detail") or ""
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
        h_name = home.get("team", {}).get("displayName") or home.get("team", {}).get("shortDisplayName", "Home")
        a_name = away.get("team", {}).get("displayName") or away.get("team", {}).get("shortDisplayName", "Away")
        h_score = extract_score_str(home.get("score"))
        a_score = extract_score_str(away.get("score"))

        if state == "post" or status_obj.get("completed"):
            past_lines.append(f"• **{date_str}**: **{h_name}** {h_score} - {a_score} **{a_name}** ({detail or 'FT'})")
        elif state == "in":
            live_lines.append(f"• 🔴 **LIVE ({detail})**: **{h_name}** {h_score} - {a_score} **{a_name}**")
        else:
            future_lines.append(f"• **{date_str}**: **{a_name}** @ **{h_name}**")

    out = f"=== 📅 {team_name.upper()} TEAM SCHEDULE & RESULTS ===\n"
    if live_lines:
        out += "🔴 **Live Now:**\n" + "\n".join(live_lines) + "\n\n"
    if past_lines:
        out += f"✅ **Recent Results (Last {min(len(past_lines), max_past)}):**\n"
        out += "\n".join(past_lines[-max_past:]) + "\n\n"
    if future_lines:
        out += f"⏭️ **Upcoming Fixtures (Next {min(len(future_lines), max_future)}):**\n"
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
        matched_players = [
            p for p in parsed_players
            if p["name"].lower() in pf_low or any(
                tok in pf_low for tok in p["name"].lower().split() if len(tok) >= 4
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
    """Fetch and format injury reports for NBA, NHL, or Soccer clubs."""
    if sport_path in ("basketball", "hockey"):
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
                    k in q_low for k in ("nba", "nhl", "hockey", "basketball", "injur", "report", "all", "today", "who")
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
            # Fallback to showing top teams with injuries
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
    data = cached_espn_get(url, params={"event": event_id}, ttl=60)
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
        status_det = header_comp.get("status", {}).get("type", {}).get("detail", "")
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

    # 3. Key Match Events Timeline (Goals, Yellow Cards, Red Cards, Substitutions, Injury Delays)
    key_events = data.get("keyEvents", [])
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
            ("wonCorners", "Corners"),
            ("foulsCommitted", "Fouls"),
            ("yellowCards", "🟨 Yellow Cards"),
            ("redCards", "🟥 Red Cards"),
            ("offsides", "Offsides"),
            ("saves", "Saves"),
            ("accuratePasses", "Accurate Passes"),
            ("effectiveTackles", "Effective Tackles"),
        ]
        stat_lines: List[str] = []
        for key, label in wanted_stats:
            if key in s1 or key in s2:
                v1 = s1.get(key, "0")
                v2 = s2.get(key, "0")
                if key == "possessionPct":
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

                if p.get("starter"):
                    sub_mark = " 🔻" if p.get("subbedOut") else ""
                    starters.append(f"{j_str}**{p_name}** ({pos}){stat_suffix}{sub_mark}")
                elif p.get("subbedIn"):
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
