"""
Soccer (Association Football) Sports Aggregator.
Fetches real-time match scores, goal scorers, yellow/red cards, possession & match stats,
starting lineups, substitutions, team schedules, player season stats, league leaders,
injuries, and league standings across major soccer competitions via ESPN's public v2 API.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.aggregators.espn_utils import (
    cached_espn_get,
    resolve_espn_team,
    match_team_in_query,
    parse_team_schedule,
    parse_team_roster_and_stats,
    parse_league_injuries,
    parse_match_summary,
)
from src.utils.http import fetch_json


class SoccerAggregator(BaseSportsAggregator):
    """Aggregator for Soccer matches, live scoreboards, cards, stats, lineups, rosters, and standings."""

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
        "spurs": "epl", "aston villa": "epl", "newcastle": "epl", "leeds": "epl",
        "brighton": "epl", "west ham": "epl", "wolves": "epl", "wolverhampton": "epl",
        "fulham": "epl", "brentford": "epl", "crystal palace": "epl", "everton": "epl",
        "bournemouth": "epl", "nottingham": "epl", "forest": "epl", "sunderland": "epl",
        "burnley": "epl", "leicester": "epl",
        "real madrid": "laliga", "barcelona": "laliga", "barca": "laliga", "atletico": "laliga",
        "sevilla": "laliga", "real sociedad": "laliga", "villarreal": "laliga", "betis": "laliga",
        "athletic bilbao": "laliga", "valencia": "laliga", "girona": "laliga",
        "bayern": "bundesliga", "dortmund": "bundesliga", "leverkusen": "bundesliga",
        "leipzig": "bundesliga", "frankfurt": "bundesliga", "stuttgart": "bundesliga",
        "inter": "seriea", "milan": "seriea", "juventus": "seriea", "juve": "seriea",
        "napoli": "seriea", "roma": "seriea", "lazio": "seriea", "atalanta": "seriea", "fiorentina": "seriea",
        "psg": "ligue1", "paris": "ligue1", "marseille": "ligue1", "lyon": "ligue1", "monaco": "ligue1",
        "inter miami": "mls", "la galaxy": "mls", "lafc": "mls"
    }

    SCOREBOARD_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
    STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports/soccer"

    def get_info(self, query: str = "") -> str:
        """Smart entry point for soccer queries: standings, lineups/summaries, injuries, player stats, rosters, and schedules."""
        q = (query or "").lower().strip()
        league_key = self._detect_league(q)
        league_info = self.LEAGUES[league_key]
        league_code = league_info["code"]
        league_name = league_info["name"]

        matched_team = resolve_espn_team("soccer", league_code, q)

        # 1. Deep Match Summary / Starting Lineups / Substitutions / Boxscore
        if any(w in q for w in ("lineup", "line-up", "starting xi", "starting 11", "formation", "sub", "substitution", "subs", "boxscore", "match stats", "match summary", "possession", "corners", "fouls")):
            summary_out = self.get_match_summary(league_code, league_name, q, matched_team=matched_team)
            if summary_out:
                return summary_out

        # 2. Injuries / Suspensions / Availability
        if any(w in q for w in ("injur", "injured", "unavailable", "availability", "fitness", "suspended", "suspension")):
            return parse_league_injuries("soccer", league_code, league_name, q)

        # 3. Squad Roster, Player Season Stats, Top Scorers, Assists, or Cards
        if any(w in q for w in ("roster", "squad", "player", "players", "stat", "stats", "scorer", "scorers", "golden boot", "assist", "assists", "yellow card", "red card", "cards", "appearances")):
            if matched_team:
                roster_out = parse_team_roster_and_stats(
                    "soccer", league_code, matched_team["id"], matched_team["displayName"], player_filter=q
                )
                # If the user specifically asked about cards or match events, also append the latest match summary
                if any(w in q for w in ("card", "yellow", "red", "last match", "last game", "recent")):
                    match_sum = self.get_match_summary(league_code, league_name, q, matched_team=matched_team)
                    if match_sum:
                        return f"{roster_out}\n\n{match_sum}"
                return roster_out
            return self.get_league_leaders(league_code, league_name, filter_query=q)

        # 4. League Standings / Table
        if any(w in q for w in ("standing", "table", "rank", "points")):
            return self.get_standings(league_code, league_name)

        # 5. Temporal Scope & History detection
        now = datetime.now()
        is_past = any(w in q for w in (
            "past", "recent", "played already", "already played", "have been played", "already",
            "last week", "previous week", "go back", "prior", "completed", "results",
            "last game", "last match", "previous match", "earlier", "history"
        )) or (any(w in q for w in ("played", "result", "results", "back")) and not any(w in q for w in ("upcoming", "next", "future", "schedule", "tomorrow")))

        is_month = "month" in q and not is_past
        is_week = any(w in q for w in ("week", "upcoming", "next", "fixtures", "schedule", "soon", "weekend", "saturday", "sunday")) and not is_past

        team_sched_block = ""
        if matched_team:
            team_sched_block = parse_team_schedule("soccer", league_code, matched_team["id"], matched_team["displayName"])

        if is_past and "yesterday" not in q:
            res = self.get_past_schedule_range(league_code, league_name, days_back=7, scope_label="RECENT MATCH RESULTS", filter_query=q)
        elif is_month:
            res = self.get_schedule_range(league_code, league_name, days_ahead=30, scope_label="MONTHLY FIXTURES", filter_query=q)
        elif is_week:
            res = self.get_schedule_range(league_code, league_name, days_ahead=8, scope_label="WEEKLY FIXTURES", filter_query=q)
        elif "yesterday" in q:
            date_param = (now - timedelta(days=1)).strftime("%Y%m%d")
            res = self.get_matches(league_code, league_name, date_param=date_param, filter_query=q)
        elif "tomorrow" in q:
            date_param = (now + timedelta(days=1)).strftime("%Y%m%d")
            res = self.get_matches(league_code, league_name, date_param=date_param, filter_query=q)
        else:
            res = self.get_matches(league_code, league_name, date_param=None, filter_query=q)

        if team_sched_block:
            return f"{team_sched_block}\n\n{res}"
        return res

    def _detect_league(self, query: str) -> str:
        """Maps query keywords or team names to the most relevant soccer league key."""
        q = query.lower()
        for key, info in self.LEAGUES.items():
            for alias in info["aliases"]:
                if alias in q:
                    return key
        for club, league_key in self.CLUB_LEAGUE_HINTS.items():
            if club in q:
                return league_key
        return "epl"

    @staticmethod
    def _extract_match_details(
        comp: Dict[str, Any],
        home: Dict[str, Any],
        away: Dict[str, Any],
        home_name: str,
        away_name: str,
    ) -> Tuple[List[str], List[str], List[str], str]:
        """Extract Goals, Yellow Cards, Red Cards, and Team Match Stats from an ESPN competition object."""
        details = comp.get("details", [])
        goals: List[str] = []
        yellow_cards: List[str] = []
        red_cards: List[str] = []

        home_id = home.get("team", {}).get("id")
        for det in details:
            item_type = det.get("type", {}).get("text", "")
            clock_str = det.get("clock", {}).get("displayValue", "")
            athletes = det.get("athletesInvolved", [])
            player_name = athletes[0].get("displayName") if athletes else "Player"
            team_id = det.get("team", {}).get("id")
            ev_team = home_name if team_id == home_id else away_name

            if det.get("scoringPlay") or "Goal" in item_type or "Penalty - Scored" in item_type:
                pen = " (PEN)" if det.get("penaltyKick") else ""
                og = " (OG)" if det.get("ownGoal") else ""
                assist_str = ""
                if len(athletes) > 1 and athletes[1].get("displayName"):
                    assist_str = f" [ast: {athletes[1].get('displayName')}]"
                goals.append(f"{clock_str} {player_name} ({ev_team}){pen}{og}{assist_str}".strip())
            elif det.get("redCard") or "Red" in item_type or "Second Yellow" in item_type:
                red_cards.append(f"{clock_str} {player_name} ({ev_team})".strip())
            elif det.get("yellowCard") or "Yellow" in item_type:
                yellow_cards.append(f"{clock_str} {player_name} ({ev_team})".strip())

        # Extract inline team match stats from competitors[i]["statistics"]
        h_stats = {s.get("name"): s.get("displayValue") for s in home.get("statistics", []) if s.get("name")}
        a_stats = {s.get("name"): s.get("displayValue") for s in away.get("statistics", []) if s.get("name")}

        stat_parts: List[str] = []
        if "possessionPct" in h_stats or "possessionPct" in a_stats:
            hp = h_stats.get("possessionPct", "0")
            ap = a_stats.get("possessionPct", "0")
            stat_parts.append(f"Possession: {hp}% - {ap}%")
        if "totalShots" in h_stats or "totalShots" in a_stats:
            hs = h_stats.get("totalShots", "0")
            as_ = a_stats.get("totalShots", "0")
            hsot = h_stats.get("shotsOnTarget", "0")
            asot = a_stats.get("shotsOnTarget", "0")
            stat_parts.append(f"Shots (On Target): {hs} ({hsot}) - {as_} ({asot})")
        if "wonCorners" in h_stats or "wonCorners" in a_stats:
            stat_parts.append(f"Corners: {h_stats.get('wonCorners', '0')} - {a_stats.get('wonCorners', '0')}")
        if "foulsCommitted" in h_stats or "foulsCommitted" in a_stats:
            stat_parts.append(f"Fouls: {h_stats.get('foulsCommitted', '0')} - {a_stats.get('foulsCommitted', '0')}")

        stats_line = " | ".join(stat_parts)
        return goals, yellow_cards, red_cards, stats_line

    @classmethod
    def _event_matches_team_filter(cls, home: Dict[str, Any], away: Dict[str, Any], filter_query: str) -> bool:
        """Check if a match involves a team specifically queried in filter_query."""
        if not filter_query:
            return True
        # Only filter if filter_query actually mentions a known club or specific team token
        q_low = filter_query.lower()
        has_club_hint = any(club in q_low for club in cls.CLUB_LEAGUE_HINTS)
        if not has_club_hint:
            return True

        for side in (home, away):
            t = side.get("team", {})
            if match_team_in_query(
                t.get("displayName", ""),
                t.get("shortDisplayName", ""),
                t.get("abbreviation", ""),
                q_low,
            ):
                return True
        return False

    def get_league_leaders(self, league_code: str, league_name: str, filter_query: str = "") -> str:
        """Fetch top goalscorers and assist leaders for a soccer league from ESPN /statistics."""
        url = f"{self.SCOREBOARD_BASE}/{league_code}/statistics"
        data = cached_espn_get(url, ttl=300)
        if not data or not data.get("stats"):
            return f"No player statistical leaders currently available for {league_name}."

        out = [f"=== 🏆 {league_name.upper()} PLAYER STATISTICAL LEADERS ==="]
        q_low = (filter_query or "").lower()

        for cat in data.get("stats", []):
            cat_title = cat.get("displayName") or cat.get("name", "Leaders")
            leaders = cat.get("leaders", [])
            if not leaders:
                continue

            out.append(f"\n⚽ **Top {cat_title}:**")
            shown = 0
            for idx, entry in enumerate(leaders, 1):
                ath = entry.get("athlete", {})
                p_name = ath.get("displayName", "Player")
                t_name = ath.get("team", {}).get("displayName", "Club")
                stats_map = {s.get("name"): s.get("displayValue") for s in ath.get("statistics", []) if s.get("name")}
                apps = stats_map.get("appearances", "?")
                goals = stats_map.get("totalGoals", str(int(entry.get("value", 0))))
                assists = stats_map.get("goalAssists", "0")

                if shown < 12 or (q_low and p_name.lower() in q_low):
                    out.append(
                        f"  {idx}. **{p_name}** ({t_name}) — **{goals} Goals, {assists} Assists** ({apps} matches)"
                    )
                    shown += 1

        return "\n".join(out)

    def get_match_summary(
        self,
        league_code: str,
        league_name: str,
        query: str = "",
        matched_team: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Locate the most relevant match event_id and return its full ESPN /summary (lineups, subs, cards, stats)."""
        event_id = None

        # 1. If a specific team was matched, check its schedule for the latest completed or live match
        if matched_team:
            sched_url = f"{self.SCOREBOARD_BASE}/{league_code}/teams/{matched_team['id']}/schedule"
            sched_data = cached_espn_get(sched_url, ttl=300)
            if sched_data and sched_data.get("events"):
                events = list(sched_data["events"])
                events.sort(key=lambda e: e.get("date", ""))
                # Prefer live or most recently completed match
                live_or_done = [
                    ev for ev in events
                    if ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("state") in ("in", "post")
                    or ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed")
                ]
                if live_or_done:
                    event_id = str(live_or_done[-1].get("id", ""))
                elif events:
                    event_id = str(events[-1].get("id", ""))

        # 2. Otherwise inspect current/recent league scoreboard
        if not event_id:
            sb_url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard"
            sb_data = cached_espn_get(sb_url, ttl=120) or fetch_json(sb_url, timeout=8)
            events = sb_data.get("events", []) if sb_data else []
            if events:
                event_id = str(events[0].get("id", ""))

        if not event_id:
            return ""
        return parse_match_summary("soccer", league_code, event_id)

    def get_past_schedule_range(self, league_code: str, league_name: str, days_back: int = 7, scope_label: str = "RECENT MATCH RESULTS", filter_query: str = "") -> str:
        """Fetches completed matches and results from recent matchdays with goals, yellow/red cards, and team stats."""
        print(f"  └─ ⚽ [Soccer]: Fetching {scope_label} for {league_name} (Code: {league_code}, Past {days_back} days)...")
        now = datetime.now()
        start_date = now - timedelta(days=days_back)

        try:
            base_url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard"
            base_data = fetch_json(base_url, timeout=8)
            leagues = base_data.get("leagues", [])
            calendar = leagues[0].get("calendar", []) if leagues else []

            past_dates = []
            for item in calendar:
                try:
                    clean_dt = datetime.fromisoformat(item.replace("Z", "+00:00")).replace(tzinfo=None)
                    if clean_dt.date() < now.date():
                        d_str = clean_dt.strftime("%Y%m%d")
                        if d_str not in past_dates:
                            past_dates.append(d_str)
                except Exception:
                    pass

            if not past_dates:
                return f"=== {league_name.upper()} {scope_label} ===\nNo recent completed matches found in the current season calendar."

            recent_dates = [d for d in past_dates if datetime.strptime(d, "%Y%m%d").date() >= start_date.date()]
            recent_dates = recent_dates[-3:] if recent_dates else past_dates[-3:]

            output_lines = [
                f"=== {league_name.upper()} {scope_label} ===\n",
                f"📅 Current Date: {self.format_human_date(now)}\n"
            ]

            total_matches = 0
            for pdate in recent_dates:
                day_title = self.format_human_date(pdate)
                day_url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard?dates={pdate}"
                day_data = fetch_json(day_url, timeout=6)
                events = day_data.get("events", [])
                if not events:
                    continue

                day_match_lines: List[str] = []
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                    if not self._event_matches_team_filter(home, away, filter_query):
                        continue

                    home_name = home.get("team", {}).get("displayName", "Home")
                    away_name = away.get("team", {}).get("displayName", "Away")
                    home_score = home.get("score", "0")
                    away_score = away.get("score", "0")
                    status_desc = comp.get("status", {}).get("type", {}).get("detail") or "FT"

                    goals, yellow_cards, red_cards, stats_line = self._extract_match_details(
                        comp, home, away, home_name, away_name
                    )

                    venue = comp.get("venue", {}).get("fullName")
                    venue_str = f" | Venue: {venue}" if venue else ""

                    day_match_lines.append(f"  • {home_name} {home_score} - {away_score} {away_name} [{status_desc}{venue_str}]")
                    if goals:
                        day_match_lines.append(f"    🎯 Goals: {', '.join(goals)}")
                    if yellow_cards:
                        day_match_lines.append(f"    🟨 Yellow Cards: {', '.join(yellow_cards)}")
                    if red_cards:
                        day_match_lines.append(f"    🟥 Red Cards: {', '.join(red_cards)}")
                    if stats_line:
                        day_match_lines.append(f"    📊 Stats: {stats_line}")
                    total_matches += 1

                if day_match_lines:
                    output_lines.append(f"📅 --- {day_title.upper()} ---")
                    output_lines.extend(day_match_lines)
                    output_lines.append("")

            if total_matches == 0:
                output_lines.append(f"No completed match records found for recent matchdays in {league_name}.")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching recent results for {league_name}: {e}"

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

            if not upcoming_dates:
                for d in range(1, min(days_ahead + 1, 8)):
                    cand = now + timedelta(days=d)
                    if cand.weekday() in (5, 6):
                        upcoming_dates.append(cand.strftime("%Y%m%d"))

            if not upcoming_dates:
                return f"=== {league_name.upper()} {scope_label} ===\nNo upcoming matches scheduled in the next {days_ahead} days."

            output_lines = [f"=== {league_name.upper()} {scope_label} ===\n"]

            total_matches = 0
            for udate in upcoming_dates[:5]:
                day_title = self.format_human_date(udate)

                day_url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard?dates={udate}"
                day_data = fetch_json(day_url, timeout=6)
                events = day_data.get("events", [])
                if not events:
                    continue

                day_match_lines: List[str] = []
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                    if not self._event_matches_team_filter(home, away, filter_query):
                        continue

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

                    day_match_lines.append(f"  • {away_name} at {home_name}{details_str} [{status_desc}]")
                    total_matches += 1

                if day_match_lines:
                    output_lines.append(f"📅 --- {day_title.upper()} ({len(day_match_lines)} Matches) ---")
                    output_lines.extend(day_match_lines)
                    output_lines.append("")

            if total_matches == 0:
                return f"=== {league_name.upper()} {scope_label} ===\nNo upcoming matches scheduled in the next {days_ahead} days."

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching {scope_label} for {league_name}: {e}"

    def get_matches(self, league_code: str, league_name: str, date_param: Optional[str] = None, filter_query: str = "") -> str:
        """Fetches live matches, recent results, and upcoming fixtures for a league with goals, cards, and team stats."""
        print(f"  └─ ⚽ [Soccer]: Fetching matches for {league_name} (Code: {league_code}, Date: {date_param or 'Current'})...")
        url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard"
        if date_param:
            url += f"?dates={date_param}"

        try:
            data = fetch_json(url, timeout=8)
            events = data.get("events", [])

            now = datetime.now()
            all_completed = events and all(ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed", False) for ev in events)
            if (not events or all_completed) and not date_param:
                upcoming_preview = self.get_schedule_range(league_code, league_name, days_ahead=7, scope_label="UPCOMING MATCHES THIS WEEK", filter_query=filter_query)
                if "No upcoming matches" not in upcoming_preview and "No fixtures found" not in upcoming_preview:
                    if not events:
                        return upcoming_preview

            if not events:
                day_display = self.format_human_date(date_param) if date_param else self.format_human_date(now)
                upcoming_preview = self.get_schedule_range(league_code, league_name, days_ahead=7, scope_label="NEXT UPCOMING FIXTURES", filter_query=filter_query)
                return (
                    f"=== {league_name.upper()} — NO MATCHES SCHEDULED ===\n"
                    f"📅 Date Checked: {day_display} (No matches scheduled for this date).\n\n"
                    f"ℹ️ There are no {league_name} matches scheduled for {day_display}.\n\n"
                    f"🔜 {upcoming_preview}"
                )

            day_display = self.format_human_date(date_param) if date_param else self.format_human_date(now)
            output_lines = [f"=== {league_name.upper()} MATCHES — {day_display.upper()} ===\n"]

            for ev in events:
                comps = ev.get("competitions", [])
                if not comps:
                    continue
                comp = comps[0]

                competitors = comp.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                if not self._event_matches_team_filter(home, away, filter_query):
                    continue

                home_name = home.get("team", {}).get("displayName", "Home")
                away_name = away.get("team", {}).get("displayName", "Away")
                home_score = home.get("score", "0")
                away_score = away.get("score", "0")

                status_obj = comp.get("status", {})
                status_desc = status_obj.get("type", {}).get("detail") or status_obj.get("type", {}).get("description") or "Scheduled"
                is_completed = status_obj.get("type", {}).get("completed", False)
                is_live = status_obj.get("type", {}).get("state") == "in"

                live_marker = " 🔴 [LIVE]" if is_live else (" ✅ [FT]" if is_completed else "")
                output_lines.append(f"⚽ {home_name} {home_score} - {away_score} {away_name}{live_marker}")
                output_lines.append(f"   Status: {status_desc}")

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

                goals, yellow_cards, red_cards, stats_line = self._extract_match_details(
                    comp, home, away, home_name, away_name
                )

                if goals:
                    output_lines.append(f"   🎯 Goals: {', '.join(goals)}")
                if yellow_cards:
                    output_lines.append(f"   🟨 Yellow Cards: {', '.join(yellow_cards)}")
                if red_cards:
                    output_lines.append(f"   🟥 Red Cards: {', '.join(red_cards)}")
                if stats_line:
                    output_lines.append(f"   📊 Stats: {stats_line}")

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

                output_lines.append(f"{rank:>3} | {team_name:<25} | {mp:>2} | {w:>2} | {d:>2} | {l:>2} | {gd:>3} | {pts:>3}")

            output_lines.append("")
            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching standings for {league_name}: {e}"
