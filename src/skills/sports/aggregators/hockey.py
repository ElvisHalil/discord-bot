"""
Hockey (NHL) Sports Aggregator.
Fetches real-time NHL scores, period breakdowns, top game performers,
team schedules, rosters, player stats, injury reports, and Conference / Division standings
via ESPN's official public v2 API.
"""

from datetime import datetime, timedelta
import re
from typing import Optional, Dict, Any, List
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.aggregators.espn_utils import (
    resolve_espn_team,
    parse_team_schedule,
    parse_team_roster_and_stats,
    parse_league_injuries,
)
from src.utils.http import fetch_json


class HockeyAggregator(BaseSportsAggregator):
    """Aggregator for NHL hockey scores, period breakdowns, leaders, injuries, rosters, schedules, and standings."""

    SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
    STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings"

    def get_info(self, query: str = "") -> str:
        """Entry point for Hockey queries: standings, injuries, rosters/player stats, team schedules, live games, and results."""
        q = (query or "").lower().strip()
        matched_team = resolve_espn_team("hockey", "nhl", q)

        # 1. Injuries & Player Availability
        if re.search(r'\b(?:injur(?:y|ies|ed)|unavailable|availability|day-to-day|questionable|injured\s*reserve)\b', q):
            return parse_league_injuries("hockey", "nhl", "NHL", q)

        # 2. Team Roster & Player Stats
        is_live_or_sched = bool(re.search(r'\b(?:status|current|currently|live|playing|right\s*now|score|scores|next|upcoming|when|schedule)\b', q))
        if (
            re.search(r'\b(?:rosters?|squads?)\b', q)
            or (not is_live_or_sched and re.search(r'\b(?:players?|stats|statistics)\b', q))
        ) and matched_team:
            return parse_team_roster_and_stats(
                "hockey", "nhl", matched_team["id"], matched_team["displayName"], player_filter=q
            )

        # 3. Conference Standings
        if any(w in q for w in ("standing", "table", "rank", "points", "wildcard", "division", "conference")):
            return self.get_standings()

        now = datetime.now()
        is_past = any(w in q for w in (
            "past", "recent", "played already", "already played", "have been played", "already",
            "last week", "previous week", "go back", "prior", "completed", "results",
            "last game", "previous game", "earlier", "history"
        )) or (any(w in q for w in ("played", "result", "results", "back")) and not any(w in q for w in ("upcoming", "next", "future", "schedule", "tomorrow")))

        is_month = "month" in q and not is_past
        is_week = any(w in q for w in ("week", "upcoming", "schedule", "fixtures", "next games")) and not is_past

        team_sched_block = ""
        if matched_team:
            team_sched_block = parse_team_schedule("hockey", "nhl", matched_team["id"], matched_team["displayName"])

        if is_past and "yesterday" not in q:
            res = self.get_past_schedule_range(days_back=7, scope_label="RECENT NHL RESULTS")
        elif is_month:
            res = self.get_schedule_range(days_ahead=30, scope_label="MONTHLY SCHEDULE")
        elif is_week:
            res = self.get_schedule_range(days_ahead=7, scope_label="WEEKLY SCHEDULE")
        elif "yesterday" in q:
            date_param = (now - timedelta(days=1)).strftime("%Y%m%d")
            res = self.get_games(date_param=date_param, filter_query=q)
        elif "tomorrow" in q:
            date_param = (now + timedelta(days=1)).strftime("%Y%m%d")
            res = self.get_games(date_param=date_param, filter_query=q)
        else:
            res = self.get_games(filter_query=q)

        if team_sched_block:
            return f"{team_sched_block}\n\n{res}"
        return res

    @staticmethod
    def _extract_team_leaders(competitor: Dict[str, Any]) -> str:
        """Extract top game stat leaders (G, A, PTS) for an NHL competitor."""
        leaders = competitor.get("leaders", [])
        parts: List[str] = []
        for l_cat in leaders:
            cat_name = l_cat.get("name", "")
            abbr = l_cat.get("abbreviation", "")
            if cat_name not in ("goals", "assists", "points"):
                continue
            top_list = l_cat.get("leaders", [])
            if top_list:
                top = top_list[0]
                p_name = top.get("athlete", {}).get("shortName") or top.get("athlete", {}).get("displayName", "")
                val = top.get("displayValue", "")
                if p_name and val and val != "0":
                    parts.append(f"{p_name} ({val} {abbr})")
        return ", ".join(parts)

    def get_past_schedule_range(self, days_back: int = 7, scope_label: str = "RECENT NHL RESULTS") -> str:
        """Fetches completed NHL games and scores from recent game days."""
        print(f"  └─ 🏒 [NHL]: Fetching {scope_label} (Past {days_back} days)...")
        now = datetime.now()
        start_date = now - timedelta(days=days_back)

        try:
            base_data = fetch_json(self.SCOREBOARD_URL, timeout=8)
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
                return f"=== NHL {scope_label} ===\nNo recent completed games found in the current season calendar."

            recent_dates = [d for d in past_dates if datetime.strptime(d, "%Y%m%d").date() >= start_date.date()]
            recent_dates = recent_dates[-3:] if recent_dates else past_dates[-3:]

            output_lines = [
                f"=== NHL {scope_label} ===\n",
                f"📅 Current Date: {self.format_human_date(now)}\n"
            ]

            total_games = 0
            for pdate in recent_dates:
                day_title = self.format_human_date(pdate)
                day_data = fetch_json(f"{self.SCOREBOARD_URL}?dates={pdate}", timeout=6)
                events = day_data.get("events", [])
                if not events:
                    continue

                output_lines.append(f"📅 --- {day_title.upper()} ({len(events)} Results) ---")
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})
                    home_team = home.get("team", {}).get("displayName", "Home")
                    away_team = away.get("team", {}).get("displayName", "Away")
                    home_score = home.get("score", "0")
                    away_score = away.get("score", "0")
                    status_desc = comp.get("status", {}).get("type", {}).get("detail") or "Final"

                    venue = comp.get("venue", {}).get("fullName")
                    meta_str = f" | Arena: {venue}" if venue else ""

                    output_lines.append(f"  • {away_team} {away_score} @ {home_team} {home_score} [{status_desc}{meta_str}]")
                    away_ldr = self._extract_team_leaders(away)
                    home_ldr = self._extract_team_leaders(home)
                    if away_ldr or home_ldr:
                        ldr_bits = []
                        if away_ldr:
                            ldr_bits.append(f"{away.get('team', {}).get('abbreviation', away_team)}: {away_ldr}")
                        if home_ldr:
                            ldr_bits.append(f"{home.get('team', {}).get('abbreviation', home_team)}: {home_ldr}")
                        output_lines.append(f"    🌟 Leaders: {' | '.join(ldr_bits)}")
                    total_games += 1
                output_lines.append("")

            if total_games == 0:
                output_lines.append("No completed game records found for recent game days.")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching recent NHL results: {e}"

    def get_schedule_range(self, days_ahead: int = 7, scope_label: str = "WEEKLY SCHEDULE") -> str:
        """Fetches upcoming NHL games across multiple days within the specified time window (week or month)."""
        print(f"  └─ 🏒 [NHL]: Fetching {scope_label} (Next {days_ahead} days)...")
        now = datetime.now()
        end_date = now + timedelta(days=days_ahead)

        try:
            base_data = fetch_json(self.SCOREBOARD_URL, timeout=8)
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
                for i in range(min(days_ahead, 7)):
                    upcoming_dates.append((now + timedelta(days=i)).strftime("%Y%m%d"))

            output_lines = [f"=== NHL {scope_label} ===\n"]

            total_games = 0
            for udate in upcoming_dates[:5]:
                day_title = self.format_human_date(udate)

                day_data = fetch_json(f"{self.SCOREBOARD_URL}?dates={udate}", timeout=6)
                events = day_data.get("events", [])
                if not events:
                    continue

                output_lines.append(f"📅 --- {day_title.upper()} ({len(events)} Games) ---")
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})
                    home_team = home.get("team", {}).get("displayName", "Home")
                    away_team = away.get("team", {}).get("displayName", "Away")
                    status_desc = comp.get("status", {}).get("type", {}).get("detail") or "Scheduled"

                    venue = comp.get("venue", {}).get("fullName")
                    broadcasts = [b.get("names", [""])[0] for b in comp.get("broadcasts", []) if b.get("names")]
                    meta = []
                    if venue:
                        meta.append(f"Arena: {venue}")
                    if broadcasts:
                        meta.append(f"TV: {', '.join(broadcasts)}")
                    meta_str = f" ({' | '.join(meta)})" if meta else ""

                    output_lines.append(f"  • {away_team} at {home_team}{meta_str} [{status_desc}]")
                    total_games += 1
                output_lines.append("")

            if total_games == 0:
                output_lines.append("No NHL games scheduled for this period.")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching NHL schedule: {e}"

    def get_games(self, date_param: Optional[str] = None, filter_query: str = "") -> str:
        """Fetches live, upcoming, and recent NHL hockey games with period linescores and top performers."""
        print(f"  └─ 🏒 [NHL]: Fetching Hockey games (Date: {date_param or 'Today'})...")
        now = datetime.now()
        url = self.SCOREBOARD_URL
        if date_param:
            url += f"?dates={date_param}"

        try:
            data = fetch_json(url, timeout=8)
            events = data.get("events", [])

            if not events and not date_param:
                yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")
                alt_data = fetch_json(f"{self.SCOREBOARD_URL}?dates={yesterday}", timeout=5)
                if alt_data.get("events"):
                    events = alt_data["events"]
                    date_param = yesterday

            if not events:
                day_display = self.format_human_date(date_param) if date_param else self.format_human_date(now)
                upcoming_preview = self.get_schedule_range(days_ahead=7, scope_label="NEXT UPCOMING GAMES")
                return (
                    f"=== NHL SCOREBOARD — NO GAMES SCHEDULED ===\n"
                    f"📅 Date Checked: {day_display} (No games scheduled for this date).\n\n"
                    f"ℹ️ There are no NHL games scheduled for {day_display}.\n\n"
                    f"🔜 {upcoming_preview}"
                )

            day_display = self.format_human_date(date_param) if date_param else self.format_human_date(now)
            output_lines = [f"=== NHL SCOREBOARD — {day_display.upper()} ===\n"]

            for ev in events:
                comps = ev.get("competitions", [])
                if not comps:
                    continue
                comp = comps[0]

                competitors = comp.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                home_team = home.get("team", {}).get("displayName", "Home")
                away_team = away.get("team", {}).get("displayName", "Away")
                home_score = home.get("score", "0")
                away_score = away.get("score", "0")

                home_records = home.get("records") or []
                away_records = away.get("records") or []
                home_rec = f" ({home_records[0].get('summary')})" if home_records else ""
                away_rec = f" ({away_records[0].get('summary')})" if away_records else ""

                status_obj = comp.get("status", {})
                status_desc = status_obj.get("type", {}).get("detail") or status_obj.get("type", {}).get("description") or "Scheduled"
                is_completed = status_obj.get("type", {}).get("completed", False)
                is_live = status_obj.get("type", {}).get("state") == "in"

                live_badge = " 🔴 [LIVE]" if is_live else (" ✅ [FINAL]" if is_completed else "")

                output_lines.append(f"🏒 {away_team}{away_rec} {away_score} @ {home_team}{home_rec} {home_score}{live_badge}")
                output_lines.append(f"   Status: {status_desc}")

                away_periods = [str(int(l.get("value", 0))) for l in away.get("linescores", [])]
                home_periods = [str(int(l.get("value", 0))) for l in home.get("linescores", [])]
                if away_periods and home_periods:
                    p_labels = [f"P{i+1}" for i in range(len(away_periods))]
                    if len(p_labels) > 3:
                        p_labels[3] = "OT"
                    if len(p_labels) > 4:
                        p_labels[4] = "SO"
                    output_lines.append(f"   Periods:  {' | '.join(p_labels)}")
                    output_lines.append(f"   {away_team[:12]:<12}: {' | '.join(away_periods)}")
                    output_lines.append(f"   {home_team[:12]:<12}: {' | '.join(home_periods)}")

                away_ldr = self._extract_team_leaders(away)
                home_ldr = self._extract_team_leaders(home)
                if away_ldr or home_ldr:
                    ldr_bits = []
                    if away_ldr:
                        ldr_bits.append(f"{away.get('team', {}).get('abbreviation', away_team)}: {away_ldr}")
                    if home_ldr:
                        ldr_bits.append(f"{home.get('team', {}).get('abbreviation', home_team)}: {home_ldr}")
                    output_lines.append(f"   🌟 Leaders: {' | '.join(ldr_bits)}")

                venue_name = comp.get("venue", {}).get("fullName")
                broadcasts = [b.get("names", [""])[0] for b in comp.get("broadcasts", []) if b.get("names")]
                meta = []
                if venue_name:
                    meta.append(f"Venue: {venue_name}")
                if broadcasts:
                    meta.append(f"TV: {', '.join(broadcasts)}")
                if meta:
                    output_lines.append(f"   {' | '.join(meta)}")

                output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching NHL games: {e}"

    def get_standings(self) -> str:
        """Fetches NHL Conference standings with GP, W, L, OTL, and Points."""
        print("  └─ 🏒 [NHL]: Fetching Conference Standings...")
        try:
            data = fetch_json(self.STANDINGS_URL, timeout=8)
            conferences = data.get("children", [])
            output_lines = ["=== NHL CONFERENCE STANDINGS ===\n"]

            for conf in conferences:
                conf_name = conf.get("name", "Conference")
                entries = conf.get("standings", {}).get("entries", [])
                output_lines.append(f"🏆 --- {conf_name.upper()} ---")
                output_lines.append("Seed | Team                     | GP  | W   | L   | OTL | PTS ")
                output_lines.append("-" * 58)

                for idx, entry in enumerate(entries[:10], start=1):
                    team_name = entry.get("team", {}).get("displayName", "Team")
                    stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                    gp = stats.get("gamesPlayed", "0")
                    w = stats.get("wins", "0")
                    l = stats.get("losses", "0")
                    otl = stats.get("otLosses", "0")
                    pts = stats.get("points", "0")

                    output_lines.append(f"{idx:>4} | {team_name:<24} | {gp:>3} | {w:>3} | {l:>3} | {otl:>3} | {pts:>3}")

                output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching NHL standings: {e}"
