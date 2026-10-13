"""
NBA (Basketball) Sports Aggregator.
Fetches real-time NBA scores, quarter breakdowns, upcoming matchups,
and Eastern/Western Conference standings via ESPN's official public API.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.utils.http import fetch_json


class NbaAggregator(BaseSportsAggregator):
    """Aggregator for NBA basketball scores, linescores by quarter, schedules, and standings."""

    SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"

    def get_info(self, query: str = "") -> str:
        """Entry point for NBA queries. Dispatches between standings and game schedules/scores."""
        q = (query or "").lower().strip()
        if any(w in q for w in ("standing", "table", "rank", "conference", "seeds")):
            return self.get_standings()

        date_param = None
        now = datetime.now()
        if "yesterday" in q:
            date_param = (now - timedelta(days=1)).strftime("%Y%m%d")
        elif "tomorrow" in q:
            date_param = (now + timedelta(days=1)).strftime("%Y%m%d")

        return self.get_games(date_param=date_param, filter_query=q)

    def get_games(self, date_param: Optional[str] = None, filter_query: str = "") -> str:
        """Fetches live, upcoming, and recent NBA games with scores and quarter breakdowns."""
        print(f"  └─ 🏀 [NBA]: Fetching NBA games (Date: {date_param or 'Today'})...")
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
                return "=== NBA SCOREBOARD ===\nNo NBA games scheduled for the selected date."

            day_label = f" ({date_param})" if date_param else " (Today)"
            output_lines = [f"=== NBA SCOREBOARD{day_label.upper()} ===\n"]

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

                # Records (e.g. 12-4)
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
                output_lines.append(f"🏀 {away_team}{away_rec} {away_score} @ {home_team}{home_rec} {home_score}{live_badge}")
                output_lines.append(f"   Status: {status_desc}")

                # Quarter breakdown if game started or finished
                away_quarters = [str(int(l.get("value", 0))) for l in away.get("linescores", [])]
                home_quarters = [str(int(l.get("value", 0))) for l in home.get("linescores", [])]
                if away_quarters and home_quarters:
                    q_labels = [f"Q{i+1}" for i in range(len(away_quarters))]
                    # If overtime
                    if len(q_labels) > 4:
                        for idx in range(4, len(q_labels)):
                            q_labels[idx] = f"OT{idx-3}"
                    output_lines.append(f"   Quarters: {' | '.join(q_labels)}")
                    output_lines.append(f"   {away_team[:12]:<12}: {' | '.join(away_quarters)}")
                    output_lines.append(f"   {home_team[:12]:<12}: {' | '.join(home_quarters)}")

                # Venue & Broadcast
                venue_name = comp.get("venue", {}).get("fullName")
                broadcasts = [b.get("names", [""])[0] for b in comp.get("broadcasts", []) if b.get("names")]
                meta = []
                if venue_name:
                    meta.append(f"Arena: {venue_name}")
                if broadcasts:
                    meta.append(f"TV: {', '.join(broadcasts)}")
                if meta:
                    output_lines.append(f"   {' | '.join(meta)}")

                output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching NBA games: {e}"

    def get_standings(self) -> str:
        """Fetches Eastern and Western Conference standings with wins, losses, win %, and games behind."""
        print("  └─ 🏀 [NBA]: Fetching Conference Standings...")
        try:
            data = fetch_json(self.STANDINGS_URL, timeout=8)
            conferences = data.get("children", [])
            output_lines = ["=== NBA CONFERENCE STANDINGS ===\n"]

            for conf in conferences:
                conf_name = conf.get("name", "Conference")
                entries = conf.get("standings", {}).get("entries", [])
                output_lines.append(f"🏆 --- {conf_name.upper()} ---")
                output_lines.append("Seed | Team                     | W   | L   | Win% | GB   ")
                output_lines.append("-" * 54)

                for idx, entry in enumerate(entries[:10], start=1):
                    team_name = entry.get("team", {}).get("displayName", "Team")
                    stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                    w = stats.get("wins", "0")
                    l = stats.get("losses", "0")
                    pct = stats.get("winPercent", ".000")
                    gb = stats.get("gamesBehind", "-")

                    output_lines.append(f"{idx:>4} | {team_name:<24} | {w:>3} | {l:>3} | {pct:>4} | {gb:>4}")

                output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching NBA standings: {e}"

