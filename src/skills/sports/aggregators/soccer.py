"""
Soccer (Association Football) Sports Aggregator.
Fetches real-time match scores, goal scorers, fixtures, and league standings
across major soccer competitions (EPL, Champions League, La Liga, Serie A,
Bundesliga, MLS, etc.) via ESPN's official public API.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.utils.http import fetch_json


class SoccerAggregator(BaseSportsAggregator):
    """Aggregator for Soccer matches, live scoreboards, goal details, and league standings."""

    LEAGUES = {
        "epl": {
            "code": "eng.1",
            "name": "English Premier League",
            "aliases": ["epl", "premier league", "premier", "english premier league", "england"]
        },
        "ucl": {
            "code": "uefa.champions",
            "name": "UEFA Champions League",
            "aliases": ["ucl", "champions league", "champions", "european cup"]
        },
        "uel": {
            "code": "uefa.europa",
            "name": "UEFA Europa League",
            "aliases": ["uel", "europa league", "europa"]
        },
        "laliga": {
            "code": "esp.1",
            "name": "Spanish La Liga",
            "aliases": ["laliga", "la liga", "spanish league", "spain", "primera division"]
        },
        "seriea": {
            "code": "ita.1",
            "name": "Italian Serie A",
            "aliases": ["seriea", "serie a", "italian league", "italy", "calcio"]
        },
        "bundesliga": {
            "code": "ger.1",
            "name": "German Bundesliga",
            "aliases": ["bundesliga", "german league", "germany"]
        },
        "mls": {
            "code": "usa.1",
            "name": "Major League Soccer (MLS)",
            "aliases": ["mls", "major league soccer", "usa soccer"]
        },
        "ligue1": {
            "code": "fra.1",
            "name": "French Ligue 1",
            "aliases": ["ligue1", "ligue 1", "french league", "france"]
        }
    }

    # Famous club aliases to league codes
    CLUB_LEAGUE_HINTS = {
        "arsenal": "epl", "chelsea": "epl", "liverpool": "epl", "manchester city": "epl",
        "man city": "epl", "manchester united": "epl", "man utd": "epl", "tottenham": "epl",
        "spurs": "epl", "aston villa": "epl", "newcastle": "epl",
        "real madrid": "laliga", "barcelona": "laliga", "barca": "laliga", "atletico": "laliga",
        "bayern": "bundesliga", "dortmund": "bundesliga", "leverkusen": "bundesliga",
        "inter": "seriea", "milan": "seriea", "juventus": "seriea", "juve": "seriea", "napoli": "seriea",
        "psg": "ligue1", "paris": "ligue1",
        "inter miami": "mls", "la galaxy": "mls", "lafc": "mls"
    }

    SCOREBOARD_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
    STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports/soccer"

    def get_info(self, query: str = "") -> str:
        """Smart entry point for soccer queries. Dispatches between standings, live matches, and weekly/monthly schedule."""
        q = (query or "").lower().strip()
        league_key = self._detect_league(q)
        league_info = self.LEAGUES[league_key]

        if any(w in q for w in ("standing", "table", "rank", "points")):
            return self.get_standings(league_info["code"], league_info["name"])

        # Temporal Scope detection (week vs month vs single day)
        now = datetime.now()
        is_month = "month" in q
        is_week = any(w in q for w in ("week", "upcoming", "next", "fixtures", "schedule", "soon", "weekend", "saturday", "sunday"))

        if is_month:
            return self.get_schedule_range(league_info["code"], league_info["name"], days_ahead=30, scope_label="MONTHLY FIXTURES", filter_query=q)
        elif is_week:
            return self.get_schedule_range(league_info["code"], league_info["name"], days_ahead=8, scope_label="WEEKLY FIXTURES", filter_query=q)
        elif "yesterday" in q:
            date_param = (now - timedelta(days=1)).strftime("%Y%m%d")
            return self.get_matches(league_info["code"], league_info["name"], date_param=date_param, filter_query=q)
        elif "tomorrow" in q:
            date_param = (now + timedelta(days=1)).strftime("%Y%m%d")
            return self.get_matches(league_info["code"], league_info["name"], date_param=date_param, filter_query=q)

        return self.get_matches(league_info["code"], league_info["name"], date_param=None, filter_query=q)

    def _detect_league(self, query: str) -> str:
        """Maps query keywords or team names to the most relevant soccer league key."""
        q = query.lower()
        # Direct league alias match
        for key, info in self.LEAGUES.items():
            for alias in info["aliases"]:
                if alias in q:
                    return key
        # Club hint match
        for club, league_key in self.CLUB_LEAGUE_HINTS.items():
            if club in q:
                return league_key
        # Default to Premier League if unspecified
        return "epl"

    def get_schedule_range(self, league_code: str, league_name: str, days_ahead: int = 8, scope_label: str = "WEEKLY FIXTURES", filter_query: str = "") -> str:
        """Fetches upcoming fixtures across multiple matchdays within the specified time window (week or month)."""
        print(f"  └─ ⚽ [Soccer]: Fetching {scope_label} for {league_name} (Code: {league_code}, Next {days_ahead} days)...")
        now = datetime.now()
        end_date = now + timedelta(days=days_ahead)

        try:
            base_url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard"
            base_data = fetch_json(base_url, timeout=8)
            leagues = base_data.get("leagues", [])
            calendar = leagues[0].get("calendar", []) if leagues else []

            # Extract upcoming matchday dates within the range
            upcoming_dates = []
            for item in calendar:
                try:
                    clean_dt = datetime.fromisoformat(item.replace("Z", "+00:00")).replace(tzinfo=None)
                    if now.date() <= clean_dt.date() <= end_date.date():
                        d_str = clean_dt.strftime("%Y%m%d")
                        if d_str not in upcoming_dates:
                            upcoming_dates.append(d_str)
                except Exception:
                    pass

            # Fallback if calendar is empty: probe upcoming weekend dates
            if not upcoming_dates:
                for d in range(1, min(days_ahead + 1, 8)):
                    cand = now + timedelta(days=d)
                    if cand.weekday() in (5, 6):  # Sat, Sun
                        upcoming_dates.append(cand.strftime("%Y%m%d"))

            if not upcoming_dates:
                return f"=== {league_name.upper()} {scope_label} ===\nNo upcoming matches scheduled in the next {days_ahead} days."

            output_lines = [f"=== {league_name.upper()} {scope_label} ===\n"]

            total_matches = 0
            for udate in upcoming_dates[:5]:
                try:
                    dt_obj = datetime.strptime(udate, "%Y%m%d")
                    day_title = dt_obj.strftime("%A, %B %d, %Y")
                except Exception:
                    day_title = udate

                day_url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard?dates={udate}"
                day_data = fetch_json(day_url, timeout=6)
                events = day_data.get("events", [])
                if not events:
                    continue

                output_lines.append(f"📅 --- {day_title.upper()} ({len(events)} Matches) ---")
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})
                    home_name = home.get("team", {}).get("displayName", "Home")
                    away_name = away.get("team", {}).get("displayName", "Away")
                    status_obj = comp.get("status", {})
                    status_desc = status_obj.get("type", {}).get("detail") or "Scheduled"

                    venue = comp.get("venue", {}).get("fullName")
                    m_date = ev.get("date", "")
                    time_str = m_date[11:16] + " UTC" if len(m_date) >= 16 else ""

                    details_list = []
                    if time_str:
                        details_list.append(f"Kickoff: {time_str}")
                    if venue:
                        details_list.append(f"Venue: {venue}")
                    details_str = f" ({' | '.join(details_list)})" if details_list else ""

                    output_lines.append(f"  • {away_name} at {home_name}{details_str} [{status_desc}]")
                    total_matches += 1
                output_lines.append("")

            if total_matches == 0:
                return f"=== {league_name.upper()} {scope_label} ===\nNo upcoming matches scheduled in the next {days_ahead} days."

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching {scope_label} for {league_name}: {e}"

    def get_matches(self, league_code: str, league_name: str, date_param: Optional[str] = None, filter_query: str = "") -> str:
        """Fetches live matches, recent results, and upcoming fixtures for a league with goal details."""
        print(f"  └─ ⚽ [Soccer]: Fetching matches for {league_name} (Code: {league_code}, Date: {date_param or 'Current'})...")
        url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard"
        if date_param:
            url += f"?dates={date_param}"

        try:
            data = fetch_json(url, timeout=8)
            events = data.get("events", [])

            # If current day only has past completed events and no date was passed, automatically attach upcoming matchdays
            now = datetime.now()
            all_completed = events and all(ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed", False) for ev in events)
            if (not events or all_completed) and not date_param:
                # Append upcoming weekend fixtures so the user gets upcoming games too
                upcoming_preview = self.get_schedule_range(league_code, league_name, days_ahead=7, scope_label="UPCOMING MATCHES THIS WEEK")
                if "No upcoming matches" not in upcoming_preview and "No fixtures found" not in upcoming_preview:
                    return upcoming_preview

            if not events:
                return f"=== {league_name.upper()} MATCHES ===\nNo matches scheduled for the selected period ({date_param or 'today'})."

            output_lines = [f"=== {league_name.upper()} MATCHES ===\n"]

            for ev in events:
                ev_name = ev.get("name", "Match")
                comps = ev.get("competitions", [])
                if not comps:
                    continue
                comp = comps[0]

                # Extract competitors
                competitors = comp.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                home_name = home.get("team", {}).get("displayName", "Home")
                away_name = away.get("team", {}).get("displayName", "Away")
                home_score = home.get("score", "0")
                away_score = away.get("score", "0")

                status_obj = comp.get("status", {})
                status_desc = status_obj.get("type", {}).get("detail") or status_obj.get("type", {}).get("description") or "Scheduled"
                is_completed = status_obj.get("type", {}).get("completed", False)
                is_live = status_obj.get("type", {}).get("state") == "in"

                # Match header
                live_marker = " 🔴 [LIVE]" if is_live else (" ✅ [FT]" if is_completed else "")
                output_lines.append(f"⚽ {home_name} {home_score} - {away_score} {away_name}{live_marker}")
                output_lines.append(f"   Status: {status_desc}")

                # Venue & Date
                venue_name = comp.get("venue", {}).get("fullName")
                match_date = ev.get("date", "")
                date_display = match_date[:16].replace("T", " ") + " UTC" if match_date else ""
                meta_parts = []
                if date_display and not is_live:
                    meta_parts.append(f"Kickoff: {date_display}")
                if venue_name:
                    meta_parts.append(f"Venue: {venue_name}")
                if meta_parts:
                    output_lines.append(f"   {' | '.join(meta_parts)}")

                # Detailed Events: Goals, Red Cards
                details = comp.get("details", [])
                goals = []
                red_cards = []
                for det in details:
                    item_type = det.get("type", {}).get("text", "")
                    clock_str = det.get("clock", {}).get("displayValue", "")
                    athletes = det.get("athletesInvolved", [])
                    player_name = athletes[0].get("displayName") if athletes else "Player"
                    team_id = det.get("team", {}).get("id")
                    scoring_team = home_name if team_id == home.get("team", {}).get("id") else away_name

                    if det.get("scoringPlay") or item_type == "Goal":
                        penalty_str = " (PEN)" if det.get("penaltyKick") else ""
                        og_str = " (OG)" if det.get("ownGoal") else ""
                        goals.append(f"{clock_str} {player_name} ({scoring_team}){penalty_str}{og_str}")
                    elif det.get("redCard") or "Red" in item_type:
                        red_cards.append(f"{clock_str} {player_name} ({scoring_team})")

                if goals:
                    output_lines.append(f"   🎯 Goals: {', '.join(goals)}")
                if red_cards:
                    output_lines.append(f"   🟥 Red Cards: {', '.join(red_cards)}")

                # Recent Form
                home_form = home.get("form")
                away_form = away.get("form")
                if home_form and away_form:
                    output_lines.append(f"   Form: {home_name} [{home_form}] vs {away_name} [{away_form}]")

                output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching soccer matches for {league_name}: {e}"

    def get_standings(self, league_code: str, league_name: str) -> str:
        """Fetches the current league table with points, wins, draws, losses, and goal difference."""
        print(f"  └─ ⚽ [Soccer]: Fetching standings for {league_name} (Code: {league_code})...")
        url = f"{self.STANDINGS_BASE}/{league_code}/standings"
        try:
            data = fetch_json(url, timeout=8)
            children = data.get("children", [])
            entries = []
            if children:
                entries = children[0].get("standings", {}).get("entries", [])
            elif "standings" in data:
                entries = data["standings"].get("entries", [])

            if not entries:
                return f"No standings data currently available for {league_name}."

            output_lines = [f"=== {league_name.upper()} STANDINGS ===\n"]
            output_lines.append("Pos | Club                      | MP | W  | D  | L  | GD  | PTS")
            output_lines.append("-" * 62)

            for entry in entries[:15]:
                team_name = entry.get("team", {}).get("displayName", "Team")
                stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                rank = stats.get("rank") or "?"
                mp = stats.get("gamesPlayed", "0")
                w = stats.get("wins", "0")
                d = stats.get("ties", "0")
                l = stats.get("losses", "0")
                gd = stats.get("pointDifferential", "0")
                pts = stats.get("points", "0")

                # Format neatly aligned
                output_lines.append(f"{rank:>3} | {team_name:<25} | {mp:>2} | {w:>2} | {d:>2} | {l:>2} | {gd:>3} | {pts:>3}")

            output_lines.append("")
            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching standings for {league_name}: {e}"

