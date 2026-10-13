"""
Hockey (NHL) Sports Aggregator.
Fetches real-time NHL scores, period breakdowns, upcoming matchups,
and Conference / Division standings via ESPN's official public API.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.utils.http import fetch_json


class HockeyAggregator(BaseSportsAggregator):
    """Aggregator for NHL hockey scores, period breakdowns, schedules, and standings."""

    SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
    STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings"

    def get_info(self, query: str = "") -> str:
        """Entry point for Hockey queries. Dispatches between standings and game schedules/scores."""
        q = (query or "").lower().strip()
        if any(w in q for w in ("standing", "table", "rank", "points", "wildcard", "division", "conference")):
            return self.get_standings()

        date_param = None
        now = datetime.now()
        if "yesterday" in q:
            date_param = (now - timedelta(days=1)).strftime("%Y%m%d")
        elif "tomorrow" in q:
            date_param = (now + timedelta(days=1)).strftime("%Y%m%d")

        return self.get_games(date_param=date_param, filter_query=q)

    def get_games(self, date_param: Optional[str] = None, filter_query: str = "") -> str:
        """Fetches live, upcoming, and recent NHL hockey games with period linescores."""
        print(f"  └─ 🏒 [NHL]: Fetching Hockey games (Date: {date_param or 'Today'})...")
        url = self.SCOREBOARD_URL
        if date_param:
            url += f"?dates={date_param}"

        try:
            data = fetch_json(url, timeout=8)
            events = data.get("events", [])

            # If no games today and no specific date requested, fetch yesterday's results
            if not events and not date_param:
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
                alt_data = fetch_json(f"{self.SCOREBOARD_URL}?dates={yesterday}", timeout=5)
                if alt_data.get("events"):
                    events = alt_data["events"]
                    date_param = f"Yesterday ({yesterday})"

            if not events:
                return "=== NHL SCOREBOARD ===\nNo NHL games scheduled for the selected date."

            day_label = f" ({date_param})" if date_param else " (Today)"
            output_lines = [f"=== NHL SCOREBOARD{day_label.upper()} ===\n"]

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

                # Records (e.g. 4-1-1)
                home_records = home.get("records") or []
                away_records = away.get("records") or []
                home_rec = f" ({home_records[0].get('summary')})" if home_records else ""
                away_rec = f" ({away_records[0].get('summary')})" if away_records else ""

                status_obj = comp.get("status", {})
                status_desc = status_obj.get("type", {}).get("detail") or status_obj.get("type", {}).get("description") or "Scheduled"
                is_completed = status_obj.get("type", {}).get("completed", False)
                is_live = status_obj.get("type", {}).get("state") == "in"

                live_badge = " 🔴 [LIVE]" if is_live else (" ✅ [FINAL]" if is_completed else "")

                # Header
                output_lines.append(f"🏒 {away_team}{away_rec} {away_score} @ {home_team}{home_rec} {home_score}{live_badge}")
                output_lines.append(f"   Status: {status_desc}")

                # Period breakdown if game started or finished
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

                # Venue & Broadcast
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

