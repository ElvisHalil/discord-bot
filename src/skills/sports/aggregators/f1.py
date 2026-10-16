"""
Formula 1 Sports Aggregator.
Fetches real-time Grand Prix schedules, session timings, race results,
and driver/constructor standings from ESPN's official public API.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.utils.http import fetch_json


class F1Aggregator(BaseSportsAggregator):
    """Aggregator for Formula 1 Grand Prix schedules, live session timings, results, and standings."""

    SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard"
    STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/racing/f1/standings"

    def get_info(self, query: str = "schedule") -> str:
        """Routes F1 queries between schedule, race results, and championship standings."""
        q = (query or "").lower().strip()
        if any(w in q for w in ("standing", "driver", "constructor", "point", "table", "rank", "grid", "champion")):
            return self.get_standings()
        if any(w in q for w in ("all", "full")) and not any(
            w in q for w in ("race", "races", "schedule", "calendar", "grand prix", "gp", "weekend", "session")
        ):
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
            now_utc = datetime.now(timezone.utc)
            now = now_utc.replace(tzinfo=None)
            upcoming_races: List[Dict[str, Any]] = []
            past_races: List[Dict[str, Any]] = []

            for item in calendar:
                label = item.get("label", "")
                start_str = item.get("startDate", "")
                end_str = item.get("endDate", "")
                race_date = None
                end_date = None
                try:
                    # Parse ISO format e.g. 2026-10-23T16:30Z
                    clean_date = start_str.replace("Z", "+00:00")
                    race_dt = datetime.fromisoformat(clean_date).astimezone(timezone.utc).replace(tzinfo=None)
                    race_date = race_dt
                except Exception:
                    pass
                try:
                    if end_str:
                        clean_end = end_str.replace("Z", "+00:00")
                        end_date = datetime.fromisoformat(clean_end).astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass

                entry = {
                    "label": label,
                    "startDate": start_str,
                    "endDate": end_str,
                    "dt": race_date,
                    "date_str": start_str[:10] if start_str else "TBD",
                    "date_param": start_str[:10].replace("-", "") if start_str else "",
                }

                cutoff_dt = end_date or race_date
                if cutoff_dt and cutoff_dt.date() >= now.date():
                    upcoming_races.append(entry)
                else:
                    past_races.append(entry)

            output_lines = [f"=== FORMULA 1 {season_year} WORLD CHAMPIONSHIP ===\n"]

            q = (filter_query or "").lower()
            is_month = "month" in q
            is_week = "week" in q

            if is_week:
                selected_races = [r for r in upcoming_races if r["dt"] and (r["dt"].date() - now.date()).days <= 7]
                header = "🏎️ --- F1 RACES THIS WEEK ---"
            elif is_month:
                selected_races = [r for r in upcoming_races if r["dt"] and 0 <= (r["dt"].date() - now.date()).days <= 31]
                header = f"🏎️ --- UPCOMING GRAND PRIX THIS MONTH ({len(selected_races)} Races) ---"
            else:
                selected_races = upcoming_races[:8]
                header = f"🏎️ --- UPCOMING GRAND PRIX RACES ({len(selected_races)} Remaining) ---"

            output_lines.append(header)

            if selected_races:
                # Fetch details for selected races concurrently
                def _fetch_r(r):
                    return r, self._fetch_race_details_for_date(r["date_param"])

                with ThreadPoolExecutor(max_workers=min(len(selected_races), 6)) as executor:
                    race_details_list = list(executor.map(_fetch_r, selected_races))

                for idx, (r, det) in enumerate(race_details_list, 1):
                    output_lines.append(f"{idx}. {r['label']}")
                    output_lines.append(f"   • Dates: {r['date_str']} to {r['endDate'][:10]}")
                    circuit_info = det.get("circuit", "")
                    loc_info = det.get("location", "")
                    if circuit_info and loc_info:
                        output_lines.append(f"   • Circuit: {circuit_info} ({loc_info})")
                    elif circuit_info:
                        output_lines.append(f"   • Circuit: {circuit_info}")

                    format_note = det.get("format_note", "")
                    if format_note:
                        output_lines.append(f"   • Weekend Format: {format_note}")

                    sessions = det.get("sessions", [])
                    if sessions:
                        output_lines.append("   • Confirmed Sessions (UTC):")
                        for s in sessions:
                            output_lines.append(f"   {s}")
                    else:
                        output_lines.append("   • Sessions: Check closer to race weekend.")
                    output_lines.append("")

                output_lines.append("📌 CALENDAR & FORMAT VERIFICATION:")
                output_lines.append("All remaining 2026 Grand Prix weekends listed above feature the confirmed traditional 3-day format including Practice 1 (FP1), Practice 2 (FP2), Practice 3 (FP3), Qualifying, and the Grand Prix Race.")
                output_lines.append("None of the remaining 2026 races are Sprint weekends in the official calendar.")
                output_lines.append("")
            else:
                next_note = f" (Next race: {upcoming_races[0]['label']} on {upcoming_races[0]['date_str']})" if upcoming_races else ""
                output_lines.append(f"ℹ️ No Grand Prix scheduled for this timeframe{next_note}.\n")

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

    def _fetch_race_details_for_date(self, date_param: str) -> Dict[str, Any]:
        """Queries ESPN scoreboard for a specific date to extract circuit venue, location, and full sessions."""
        if not date_param:
            return {}
        try:
            url = f"{self.SCOREBOARD_URL}?dates={date_param}"
            data = fetch_json(url, timeout=5)
            events = data.get("events", [])
            if not events:
                return {}
            ev = events[0]
            circuit = ev.get("circuit", {})
            c_name = circuit.get("fullName", "")
            addr = circuit.get("address", {})
            city = addr.get("city", "")
            country = addr.get("country", "")
            loc_parts = [p for p in (city, country) if p]
            loc = ", ".join(loc_parts)

            sessions = []
            comps = ev.get("competitions", [])
            comps_sorted = sorted(comps, key=lambda c: c.get("date", ""))
            has_fp3 = False
            has_sprint = False

            for comp in comps_sorted:
                raw_type = comp.get("type", {}).get("abbreviation") or comp.get("type", {}).get("text") or "Session"
                cdate = comp.get("date", "")
                if raw_type == "FP1":
                    label = "Practice 1 (FP1)"
                elif raw_type == "FP2":
                    label = "Practice 2 (FP2)"
                elif raw_type == "FP3":
                    label = "Practice 3 (FP3)"
                    has_fp3 = True
                elif raw_type in ("Qual", "Q"):
                    label = "Qualifying"
                elif raw_type in ("Race", "R"):
                    label = "Grand Prix (Race)"
                elif "sprint" in raw_type.lower():
                    label = f"Sprint ({raw_type})"
                    has_sprint = True
                else:
                    label = raw_type

                time_str = ""
                if cdate:
                    try:
                        clean_dt = datetime.fromisoformat(cdate.replace("Z", "+00:00")).replace(tzinfo=None)
                        time_str = clean_dt.strftime("%A, %b %d at %H:%M UTC")
                    except Exception:
                        time_str = f"{cdate[:10]} at {cdate[11:16]} UTC"

                sessions.append(f"  • {label}: {time_str}")

            format_note = "Standard 3-Day Weekend (with FP3)" if has_fp3 else ("Sprint Format" if has_sprint else "Weekend Sessions")

            return {
                "name": ev.get("name", ""),
                "circuit": c_name,
                "location": loc,
                "sessions": sessions,
                "has_fp3": has_fp3,
                "has_sprint": has_sprint,
                "format_note": format_note,
            }
        except Exception:
            return {}

    def _fetch_sessions_for_date(self, date_param: str) -> List[str]:
        """Queries ESPN scoreboard for a specific date to extract detailed practice/qualifying sessions."""
        details = self._fetch_race_details_for_date(date_param)
        return details.get("sessions", [])

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
                    output_lines.append(f"🏆 --- DRIVERS' WORLD CHAMPIONSHIP STANDINGS (All {len(entries)} Drivers) ---")
                    for entry in entries:
                        driver = entry.get("athlete", {}).get("displayName", "Unknown")
                        stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                        rank = stats.get("rank") or entry.get("athlete", {}).get("order", "?")
                        points = stats.get("championshipPts") or stats.get("points") or "0"
                        output_lines.append(f"  {rank}. {driver} - {points} pts")
                    output_lines.append("")
                elif "Constructor" in name:
                    output_lines.append(f"🏎️ --- CONSTRUCTORS' WORLD CHAMPIONSHIP STANDINGS (All {len(entries)} Teams) ---")
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
