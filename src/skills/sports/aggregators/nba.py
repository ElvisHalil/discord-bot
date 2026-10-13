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
        """Entry point for NBA queries. Dispatches between standings, live games, recent results, and weekly/monthly schedules."""
        q = (query or "").lower().strip()
        if any(w in q for w in ("standing", "table", "rank", "conference", "seeds")):
            return self.get_standings()

        now = datetime.now()
        is_past = any(w in q for w in (
            "past", "recent", "played already", "already played", "have been played", "already",
            "last week", "previous week", "go back", "prior", "completed", "results",
            "last game", "previous game", "earlier", "history"
        )) or (any(w in q for w in ("played", "result", "results", "back")) and not any(w in q for w in ("upcoming", "next", "future", "schedule", "tomorrow")))

        is_month = "month" in q and not is_past
        is_week = any(w in q for w in ("week", "upcoming", "schedule", "fixtures", "next games")) and not is_past

        if is_past and "yesterday" not in q:
            return self.get_past_schedule_range(days_back=7, scope_label="RECENT NBA RESULTS")
        elif is_month:
            return self.get_schedule_range(days_ahead=30, scope_label="MONTHLY SCHEDULE")
        elif is_week:
            return self.get_schedule_range(days_ahead=7, scope_label="WEEKLY SCHEDULE")
        elif "yesterday" in q:
            date_param = (now - timedelta(days=1)).strftime("%Y%m%d")
            return self.get_games(date_param=date_param, filter_query=q)
        elif "tomorrow" in q:
            date_param = (now + timedelta(days=1)).strftime("%Y%m%d")
            return self.get_games(date_param=date_param, filter_query=q)

        return self.get_games(filter_query=q)

    def get_past_schedule_range(self, days_back: int = 7, scope_label: str = "RECENT NBA RESULTS") -> str:
        """Fetches completed NBA games and scores from recent game days."""
        print(f"  └─ 🏀 [NBA]: Fetching {scope_label} (Past {days_back} days)...")
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
                return f"=== NBA {scope_label} ===\nNo recent completed games found in the current season calendar."

            recent_dates = [d for d in past_dates if datetime.strptime(d, "%Y%m%d").date() >= start_date.date()]
            if not recent_dates:
                recent_dates = past_dates[-3:]
            else:
                recent_dates = recent_dates[-3:]

            output_lines = [
                f"=== NBA {scope_label} ===\n",
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
                    total_games += 1
                output_lines.append("")

            if total_games == 0:
                output_lines.append("No completed game records found for recent game days.")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching recent NBA results: {e}"

    def get_schedule_range(self, days_ahead: int = 7, scope_label: str = "WEEKLY SCHEDULE") -> str:
        """Fetches upcoming NBA games across multiple days within the specified time window (week or month)."""
        print(f"  └─ 🏀 [NBA]: Fetching {scope_label} (Next {days_ahead} days)...")
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
                # Fallback: check next few days directly
                for i in range(min(days_ahead, 7)):
                    upcoming_dates.append((now + timedelta(days=i)).strftime("%Y%m%d"))

            output_lines = [f"=== NBA {scope_label} ===\n"]

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
                output_lines.append("No NBA games scheduled for this period.")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching NBA schedule: {e}"

    def get_games(self, date_param: Optional[str] = None, filter_query: str = "") -> str:
        """Fetches live, upcoming, and recent NBA games with scores and quarter breakdowns."""
        print(f"  └─ 🏀 [NBA]: Fetching NBA games (Date: {date_param or 'Today'})...")
        now = datetime.now()
        url = self.SCOREBOARD_URL
        if date_param:
            url += f"?dates={date_param}"

        try:
            data = fetch_json(url, timeout=8)
            events = data.get("events", [])

            # If no games today and no specific date requested, fetch yesterday's results
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
                    f"=== NBA SCOREBOARD — NO GAMES SCHEDULED ===\n"
                    f"📅 Date Checked: {day_display} (No games scheduled for this date).\n\n"
                    f"ℹ️ There are no NBA games scheduled for {day_display}.\n\n"
                    f"🔜 {upcoming_preview}"
                )

            day_display = self.format_human_date(date_param) if date_param else self.format_human_date(now)
            output_lines = [f"=== NBA SCOREBOARD — {day_display.upper()} ===\n"]

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

