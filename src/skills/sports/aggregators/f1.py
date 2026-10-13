"""
Formula 1 Sports Aggregator.
Fetches real-time Grand Prix schedules, session timings, race results,
and driver/constructor standings from ESPN's official public API.
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.utils.http import fetch_json


class F1Aggregator(BaseSportsAggregator):
    """Aggregator for Formula 1 Grand Prix schedules, live session timings, results, and standings."""

    SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard"
    STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/racing/f1/standings"

    def get_info(self, query: str = "schedule") -> str:
        """Routes F1 queries between schedule, race results, and championship standings."""
        q = (query or "").lower().strip()
        if any(w in q for w in ("standing", "driver", "constructor", "point", "table", "rank")):
            return self.get_standings()
        return self.get_schedule(filter_query=q)

    def get_schedule(self, filter_query: str = "") -> str:
        """Fetches the current Formula 1 season calendar, next upcoming Grand Prix,
        session breakdown (FP1-3, Qual, Sprint, Race), and most recent race results.
        Supports filtering by month or week.
        """
        print("  └─ 🏎️ [F1]: Fetching F1 calendar and race info from ESPN...")
        try:
            data = fetch_json(self.SCOREBOARD_URL, timeout=8)
            leagues = data.get("leagues", [])
            calendar = leagues[0].get("calendar", []) if leagues else []
            season_year = data.get("season", {}).get("year", datetime.now().year)

            # 1. Parse Calendar & Find Next/Recent Races
            now = datetime.now()
            upcoming_races: List[Dict[str, Any]] = []
            past_races: List[Dict[str, Any]] = []

            for item in calendar:
                label = item.get("label", "")
                start_str = item.get("startDate", "")
                end_str = item.get("endDate", "")
                race_date = None
                try:
                    # Parse ISO format e.g. 2026-10-23T16:30Z
                    clean_date = start_str.replace("Z", "+00:00")
                    race_dt = datetime.fromisoformat(clean_date).replace(tzinfo=None)
                    race_date = race_dt
                except Exception:
                    pass

                entry = {
                    "label": label,
                    "startDate": start_str,
                    "endDate": end_str,
                    "dt": race_date,
                    "date_str": start_str[:10] if start_str else "TBD"
                }

                if race_date and race_date >= now:
                    upcoming_races.append(entry)
                else:
                    past_races.append(entry)

            output_lines = [f"=== FORMULA 1 {season_year} WORLD CHAMPIONSHIP ===\n"]

            q = (filter_query or "").lower()
            is_month = "month" in q
            is_week = "week" in q

            if is_week:
                week_races = [r for r in upcoming_races if r["dt"] and (r["dt"].date() - now.date()).days <= 7]
                output_lines.append("🏎️ --- F1 RACES THIS WEEK ---")
                if week_races:
                    for r in week_races:
                        output_lines.append(f"• Event: {r['label']}")
                        output_lines.append(f"  Dates: {r['date_str']} to {r['endDate'][:10]}")
                        session_lines = self._fetch_sessions_for_date(r["date_str"].replace("-", ""))
                        if session_lines:
                            output_lines.append("  Sessions (UTC):")
                            output_lines.extend(session_lines)
                        output_lines.append("")
                else:
                    next_note = f" (Next race: {upcoming_races[0]['label']} on {upcoming_races[0]['date_str']})" if upcoming_races else ""
                    output_lines.append(f"ℹ️ No Grand Prix scheduled for this week{next_note}.\n")

            elif is_month:
                month_races = [r for r in upcoming_races if r["dt"] and 0 <= (r["dt"].date() - now.date()).days <= 31]
                output_lines.append(f"🏎️ --- UPCOMING GRAND PRIX THIS MONTH ({len(month_races)} Races) ---")
                if month_races:
                    for idx, r in enumerate(month_races, 1):
                        output_lines.append(f"{idx}. {r['label']}")
                        output_lines.append(f"   Dates: {r['date_str']} to {r['endDate'][:10]}")
                        if idx == 1:
                            session_lines = self._fetch_sessions_for_date(r["date_str"].replace("-", ""))
                            if session_lines:
                                output_lines.append("   Sessions (UTC):")
                                output_lines.extend(session_lines)
                        output_lines.append("")
                else:
                    output_lines.append("No Grand Prix scheduled for this month.\n")

            # 2. Next Upcoming Grand Prix Details (if general query)
            elif upcoming_races:
                next_race = upcoming_races[0]
                output_lines.append("🏎️ --- NEXT UPCOMING GRAND PRIX ---")
                output_lines.append(f"Event: {next_race['label']}")
                output_lines.append(f"Dates: {next_race['date_str']} to {next_race['endDate'][:10]}")

                # Attempt to query session schedule for next race weekend
                session_lines = self._fetch_sessions_for_date(next_race["date_str"].replace("-", ""))
                if session_lines:
                    output_lines.append("Sessions (UTC):")
                    output_lines.extend(session_lines)
                else:
                    output_lines.append("Session times: Check closer to race weekend.")
                output_lines.append("")

                # Later Upcoming Races
                if len(upcoming_races) > 1:
                    output_lines.append("📅 --- LATER UPCOMING RACES ---")
                    for r in upcoming_races[1:6]:
                        output_lines.append(f"• {r['date_str']} - {r['label']}")
                    output_lines.append("")
            else:
                output_lines.append("No upcoming races remaining in the current calendar.\n")

            # 3. Recent Race Results & Active Events
            events = data.get("events", [])
            if events:
                recent_ev = events[0]
                ev_name = recent_ev.get("name", "Grand Prix")
                ev_status = recent_ev.get("status", {}).get("type", {}).get("detail", "Completed")
                output_lines.append(f"🏁 --- MOST RECENT GRAND PRIX: {ev_name} ({ev_status}) ---")

                # Find the main race competition
                comps = recent_ev.get("competitions", [])
                race_comp = next((c for c in comps if c.get("type", {}).get("abbreviation") == "Race"), comps[0] if comps else None)

                if race_comp and race_comp.get("competitors"):
                    output_lines.append("Top Finishers / Results:")
                    for comp in race_comp.get("competitors", [])[:5]:
                        athlete = comp.get("athlete", {}).get("displayName", "Unknown")
                        order = comp.get("order", "?")
                        is_winner = comp.get("winner", False)
                        crown = " 🏆 (Winner)" if is_winner else ""
                        output_lines.append(f"  P{order}: {athlete}{crown}")
                else:
                    output_lines.append(f"Date: {recent_ev.get('date', '')[:10]}")
                output_lines.append("")
            elif past_races:
                output_lines.append("🏁 --- RECENT GRAND PRIX RACES ---")
                for pr in past_races[-3:]:
                    output_lines.append(f"• {pr['date_str']} - {pr['label']}")
                output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching Formula 1 schedule from ESPN: {e}"

    def _fetch_sessions_for_date(self, date_param: str) -> List[str]:
        """Queries ESPN scoreboard for a specific date to extract detailed practice/qualifying sessions."""
        try:
            url = f"{self.SCOREBOARD_URL}?dates={date_param}"
            data = fetch_json(url, timeout=5)
            events = data.get("events", [])
            if not events:
                return []
            sessions = []
            for comp in events[0].get("competitions", []):
                ctype = comp.get("type", {}).get("text") or comp.get("type", {}).get("abbreviation") or "Session"
                cdate = comp.get("date", "")
                if cdate:
                    time_part = cdate[11:16] if len(cdate) >= 16 else ""
                    day_part = cdate[:10]
                    sessions.append(f"  • {ctype}: {day_part} at {time_part} UTC")
            return sessions
        except Exception:
            return []

    def get_standings(self) -> str:
        """Fetches Driver and Constructor Championship standings."""
        print("  └─ 🏎️ [F1]: Fetching F1 Championship Standings...")
        try:
            data = fetch_json(self.STANDINGS_URL, timeout=8)
            children = data.get("children", [])
            output_lines = ["=== FORMULA 1 CHAMPIONSHIP STANDINGS ===\n"]

            for child in children:
                name = child.get("name", "")
                entries = child.get("standings", {}).get("entries", [])
                if "Driver" in name:
                    output_lines.append("🏆 --- DRIVERS' WORLD CHAMPIONSHIP (Top 10) ---")
                    for entry in entries[:10]:
                        driver = entry.get("athlete", {}).get("displayName", "Unknown")
                        stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                        rank = stats.get("rank") or entry.get("athlete", {}).get("order", "?")
                        points = stats.get("championshipPts") or stats.get("points") or "0"
                        output_lines.append(f"  {rank}. {driver} - {points} pts")
                    output_lines.append("")
                elif "Constructor" in name:
                    output_lines.append("🏎️ --- CONSTRUCTORS' CHAMPIONSHIP ---")
                    for entry in entries:
                        team = entry.get("team", {}).get("displayName", "Unknown")
                        stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                        rank = stats.get("rank", "?")
                        points = stats.get("points", "0")
                        output_lines.append(f"  {rank}. {team} - {points} pts")
                    output_lines.append("")

            return "\n".join(output_lines)
        except Exception as e:
            return f"⚠️ Error fetching Formula 1 standings from ESPN: {e}"
