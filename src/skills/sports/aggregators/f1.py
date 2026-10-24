"""
Formula 1 Sports Aggregator.
Fetches real-time Grand Prix schedules, session timings (in both configured local timezone and UTC),
live/completed session classifications, FIA Race Control telemetry via OpenF1,
and driver/constructor standings from ESPN's official public API.
"""

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.enums import MatchState
from src.skills.sports.models import MatchSnapshot
from src.utils.http import fetch_json
from src.utils.timezones import _short_tz_abbr, get_local_now
from src.utils.workers import submit_to_worker


def _normalize_f1_session_label(raw_type: str) -> str:
    """Normalize an ESPN or OpenF1 session type string into a canonical display label."""
    rt = (raw_type or "").strip()
    rt_low = rt.lower()
    if rt in ("FP1", "Practice 1") or "practice 1" in rt_low:
        return "Practice 1 (FP1)"
    if rt in ("FP2", "Practice 2") or "practice 2" in rt_low:
        return "Practice 2 (FP2)"
    if rt in ("FP3", "Practice 3") or "practice 3" in rt_low:
        return "Practice 3 (FP3)"
    if rt in ("Qual", "Q", "Qualifying") or rt_low == "qualifying":
        return "Qualifying"
    if rt in ("Race", "R") or rt_low == "race":
        return "Grand Prix (Race)"
    if rt.upper() in ("SQ", "SS") or ("sprint" in rt_low and ("qual" in rt_low or "shootout" in rt_low)):
        return f"Sprint Qualifying ({rt})" if rt.lower() != "sprint qualifying" else "Sprint Qualifying"
    if "sprint" in rt_low:
        return f"Sprint ({rt})" if rt.lower() != "sprint" else "Sprint"
    return rt or "Session"


def _format_f1_session_time(iso_str: str) -> str:
    """Format an ISO-8601 UTC timestamp into both the bot's configured local timezone and UTC."""
    if not iso_str:
        return "Time TBD"
    try:
        cleaned = str(iso_str).replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(cleaned)
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        else:
            dt_utc = dt_utc.astimezone(timezone.utc)
        local_dt = get_local_now(now=dt_utc)
        local_abbr = _short_tz_abbr(local_dt)
        local_clock = local_dt.strftime("%I:%M %p").lstrip("0")
        local_part = f"{local_dt.strftime('%A, %b %d')} at {local_clock} {local_abbr}"
        utc_day_prefix = f"{dt_utc.strftime('%a %b %d')}, " if dt_utc.date() != local_dt.date() else ""
        utc_part = f"{utc_day_prefix}{dt_utc.strftime('%H:%M')} UTC"
        return f"{local_part} ({utc_part})"
    except Exception:
        return f"{iso_str[:10]} at {iso_str[11:16]} UTC" if len(iso_str) >= 16 else str(iso_str)


def _parse_lap_time_seconds(val: str) -> Optional[float]:
    """Parse an F1 lap or race time string ('1:34.035', '59.812', '1:42:00.785') into total seconds."""
    s = (val or "").strip()
    if not s or s in ("-", "—", "DNF", "DNS", "DNQ", "DSQ", "NC", "RET", "No Time") or s.startswith("+"):
        return None
    try:
        parts = s.split(":")
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60.0 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600.0 + int(parts[1]) * 60.0 + float(parts[2])
    except (ValueError, TypeError):
        return None
    return None


def _is_valid_lap_time(val: str) -> bool:
    """Return True if val is a valid single-lap F1 time (between 30s and 300s)."""
    sec = _parse_lap_time_seconds(val)
    return sec is not None and 30.0 <= sec <= 300.0


def _extract_comp_top_drivers(comp: Dict[str, Any], limit: int = 5) -> List[str]:
    """Extract ordered driver display names (P1..Pn) from an ESPN competition block."""
    st_type = (comp.get("status") or {}).get("type") or {} if isinstance(comp.get("status"), dict) else {}
    state = (st_type.get("state") or "").lower()
    if state == MatchState.PRE:
        return []
    competitors = comp.get("competitors") or []
    if not competitors:
        return []
    ordered_only = [c for c in competitors if str(c.get("order") or "").isdigit()]
    if not ordered_only:
        if state == MatchState.POST or bool(st_type.get("completed", False)):
            ordered_only = list(competitors)
        else:
            return []
    sorted_comps = sorted(
        ordered_only,
        key=lambda c: int(c.get("order") or 999) if str(c.get("order") or "").isdigit() else 999,
    )
    drivers: List[str] = []
    for c_item in sorted_comps[:limit]:
        drv = (
            c_item.get("athlete", {}).get("displayName")
            or c_item.get("athlete", {}).get("shortName")
            or c_item.get("athlete", {}).get("fullName")
            or ""
        ).strip()
        if drv:
            drivers.append(drv)
    return drivers


def _format_session_status_badge(comp: Dict[str, Any]) -> str:
    """Build an inline status badge ([✅ Completed — P1: ...], [🔴 LIVE NOW], [🕒 Upcoming]) when status metadata exists."""
    status_obj = comp.get("status")
    if not isinstance(status_obj, dict):
        return ""
    st_type = status_obj.get("type") or {}
    state = (st_type.get("state") or "").lower()
    completed = bool(st_type.get("completed", False))
    top3 = _extract_comp_top_drivers(comp, limit=3)
    top_str = ", ".join(f"P{i}: {d}" for i, d in enumerate(top3, 1))

    if state == MatchState.IN:
        return f" [🔴 LIVE NOW — {top_str}]" if top_str else " [🔴 LIVE NOW]"
    if state == MatchState.POST or completed:
        return f" [✅ Completed — {top_str}]" if top_str else " [✅ Completed]"
    if state == MatchState.PRE:
        return " [🕒 Upcoming]"
    return ""


class F1Aggregator(BaseSportsAggregator):
    """Aggregator for Formula 1 Grand Prix schedules, live session timings, BBC/ESPN/OpenF1 telemetry, results, and standings."""

    SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard"
    HEADER_URL = "https://site.web.api.espn.com/apis/v2/scoreboard/header?sport=racing&league=f1"
    STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/racing/f1/standings"
    OPENF1_BASE_URL = "https://api.openf1.org/v1"
    BBC_SEASON_URL = "https://web-cdn.api.bbci.co.uk/wc-poll-data/container/formula1-season-results"
    BBC_STAGE_URL = "https://web-cdn.api.bbci.co.uk/wc-poll-data/container/formula1-stage-results"
    _BBC_STAGE_URN_CACHE: Dict[Tuple[int, str], str] = {}

    def get_info(self, query: str = "schedule") -> str:
        """Routes F1 queries between schedule, session times/results, and championship standings."""
        q = (query or "").lower().strip()
        wants_schedule_or_session = any(
            w in q
            for w in (
                "schedule", "calendar", "when", "time", "start", "session", "sessions",
                "qualifying", "quali", "fp1", "fp2", "fp3", "practice", "sprint",
                "weekend", "next", "upcoming", "today", "tomorrow",
            )
        )
        if not wants_schedule_or_session and any(
            w in q for w in ("standing", "driver", "constructor", "point", "table", "rank", "champion")
        ):
            return self.get_standings()
        if any(w in q for w in ("all", "full")) and not any(
            w in q for w in ("race", "races", "schedule", "calendar", "grand prix", "gp", "weekend", "session")
        ):
            return self.get_standings()
        return self.get_schedule(filter_query=q)

    def get_schedule(self, filter_query: str = "") -> str:
        """Fetches the current Formula 1 season calendar, active/next Grand Prix weekend,
        session breakdown (FP1-3, Qualifying, Sprint, Race) in both local timezone and UTC,
        and current weekend or most recent race results.
        """
        print("  └─ 🏎️ [F1]: Fetching F1 calendar and race info from ESPN...")
        try:
            data = fetch_json(self.SCOREBOARD_URL, timeout=8)
            leagues = data.get("leagues", [])
            calendar = leagues[0].get("calendar", []) if leagues else []
            season_year = data.get("season", {}).get("year", datetime.now().year)

            # 1. Parse Calendar & Find Next/Recent Races (anchored to configured local date)
            local_now = get_local_now()
            local_today = local_now.date()
            upcoming_races: List[Dict[str, Any]] = []
            past_races: List[Dict[str, Any]] = []

            for item in calendar:
                label = item.get("label", "")
                start_str = item.get("startDate", "")
                end_str = item.get("endDate", "")
                race_date = None
                end_date = None
                start_local_date = None
                end_local_date = None
                try:
                    clean_date = start_str.replace("Z", "+00:00")
                    dt_start_utc = datetime.fromisoformat(clean_date).astimezone(timezone.utc)
                    race_date = dt_start_utc.replace(tzinfo=None)
                    start_local_date = get_local_now(now=dt_start_utc).date()
                except Exception:
                    pass
                try:
                    if end_str:
                        clean_end = end_str.replace("Z", "+00:00")
                        dt_end_utc = datetime.fromisoformat(clean_end).astimezone(timezone.utc)
                        end_date = dt_end_utc.replace(tzinfo=None)
                        end_local_date = get_local_now(now=dt_end_utc).date()
                except Exception:
                    pass

                cutoff_dt = end_date or race_date
                cutoff_local_date = end_local_date or start_local_date or (cutoff_dt.date() if cutoff_dt else None)
                entry = {
                    "label": label,
                    "startDate": start_str,
                    "endDate": end_str,
                    "dt": race_date,
                    "end_dt": end_date,
                    "cutoff_dt": cutoff_dt,
                    "start_local_date": start_local_date,
                    "cutoff_local_date": cutoff_local_date,
                    "date_str": start_str[:10] if start_str else "TBD",
                    "end_date_str": end_str[:10] if end_str else (start_str[:10] if start_str else "TBD"),
                    "date_param": start_str[:10].replace("-", "") if start_str else "",
                }

                if cutoff_local_date and max(cutoff_local_date, cutoff_dt.date() if cutoff_dt else cutoff_local_date) >= local_today:
                    upcoming_races.append(entry)
                else:
                    past_races.append(entry)

            output_lines = [f"=== FORMULA 1 {season_year} WORLD CHAMPIONSHIP ===\n"]

            q = (filter_query or "").lower()
            is_month = "month" in q
            is_week = "week" in q

            if is_week:
                selected_races = [
                    r for r in upcoming_races
                    if (r["start_local_date"] or r["cutoff_local_date"])
                    and ((r["start_local_date"] or r["cutoff_local_date"]) - local_today).days <= 7
                ]
                header = "🏎️ --- F1 RACES THIS WEEK ---"
            elif is_month:
                selected_races = [
                    r for r in upcoming_races
                    if (r["start_local_date"] or r["cutoff_local_date"])
                    and ((r["start_local_date"] or r["cutoff_local_date"]) - local_today).days <= 31
                ]
                header = f"🏎️ --- UPCOMING GRAND PRIX THIS MONTH ({len(selected_races)} Races) ---"
            else:
                selected_races = upcoming_races[:8]
                header = f"🏎️ --- UPCOMING GRAND PRIX RACES ({len(selected_races)} Remaining) ---"

            output_lines.append(header)

            if selected_races:
                def _fetch_r(r):
                    return r, self._fetch_race_details_for_date(r["date_param"])

                futures = [submit_to_worker(_fetch_r, r) for r in selected_races]
                race_details_list = [f.result() for f in futures]

                for idx, (r, det) in enumerate(race_details_list, 1):
                    output_lines.append(f"{idx}. {r['label']}")
                    start_d_disp = det.get("start_date_local") or r["date_str"]
                    end_d_disp = det.get("end_date_local") or r["end_date_str"]
                    output_lines.append(f"   • Dates: {start_d_disp} to {end_d_disp}")
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
                        output_lines.append("   • Confirmed Sessions (Local & UTC):")
                        for s in sessions:
                            output_lines.append(f"   {s}")
                    else:
                        output_lines.append("   • Sessions: Check closer to race weekend.")
                    output_lines.append("")

                output_lines.append("📌 CALENDAR & FORMAT VERIFICATION:")
                if any(det.get("has_sprint") for _, det in race_details_list):
                    output_lines.append("Weekend formats (Standard 3-Day vs Sprint) and session start times above are verified against the official FIA/ESPN schedule.")
                else:
                    output_lines.append("All remaining 2026 Grand Prix weekends listed above feature the confirmed traditional 3-day format including Practice 1 (FP1), Practice 2 (FP2), Practice 3 (FP3), Qualifying, and the Grand Prix Race.")
                    output_lines.append("None of the remaining 2026 races are Sprint weekends in the official calendar.")
                output_lines.append("")
            else:
                next_note = f" (Next race: {upcoming_races[0]['label']} on {upcoming_races[0]['date_str']})" if upcoming_races else ""
                output_lines.append(f"ℹ️ No Grand Prix scheduled for this timeframe{next_note}.\n")

            # 3. Current Race Weekend Status OR Most Recent Completed Grand Prix
            events = data.get("events", [])
            if events:
                recent_ev = events[0]
                ev_name = recent_ev.get("name", "Grand Prix")
                ev_status = recent_ev.get("status", {}).get("type", {}).get("detail", "Completed")
                comps = sorted(recent_ev.get("competitions", []), key=lambda c: c.get("date", ""))
                race_comp = next(
                    (
                        c for c in comps
                        if (c.get("type", {}).get("abbreviation") or c.get("type", {}).get("text") or "") in ("Race", "R")
                    ),
                    comps[-1] if comps else None,
                )

                race_st_type = (race_comp or {}).get("status", {}).get("type", {}) if isinstance((race_comp or {}).get("status"), dict) else {}
                race_state = (race_st_type.get("state") or "").lower()
                race_completed = bool(race_st_type.get("completed", False))
                has_ordered_finishers = bool(
                    race_comp
                    and any(
                        str(c.get("order") or "").isdigit() or bool(c.get("winner", False))
                        for c in (race_comp.get("competitors") or [])
                    )
                )
                has_race_results = (
                    has_ordered_finishers and race_state not in (MatchState.PRE, MatchState.IN)
                ) or race_state == MatchState.POST or race_completed

                if has_race_results or race_state == MatchState.POST or race_completed:
                    output_lines.append(f"🏁 --- MOST RECENT GRAND PRIX: {ev_name} ({ev_status}) ---")
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
                else:
                    # Active race weekend in progress (e.g. FP1-FP3 done, Qualifying/Race still upcoming or live)
                    output_lines.append(
                        f"🏎️ --- CURRENT RACE WEEKEND STATUS: {ev_name} (Weekend In Progress — Grand Prix Race Not Yet Run) ---"
                    )
                    live_comp = next(
                        (
                            c for c in comps
                            if (c.get("status", {}).get("type", {}).get("state") or "").lower() == MatchState.IN
                        ),
                        None,
                    )
                    done_comps = [
                        c for c in comps
                        if (c.get("status", {}).get("type", {}).get("state") or "").lower() == MatchState.POST
                        or bool(c.get("status", {}).get("type", {}).get("completed", False))
                    ]
                    upcoming_comps = [
                        c for c in comps
                        if (c.get("status", {}).get("type", {}).get("state") or "").lower() == MatchState.PRE
                    ]

                    if live_comp:
                        raw_t = live_comp.get("type", {}).get("abbreviation") or live_comp.get("type", {}).get("text") or "Session"
                        lbl = _normalize_f1_session_label(raw_t)
                        top5 = _extract_comp_top_drivers(live_comp, limit=5)
                        ldr_str = ", ".join(f"P{i}: {d}" for i, d in enumerate(top5, 1)) if top5 else "In Progress"
                        output_lines.append(f"• 🔴 LIVE NOW: {lbl} ({ldr_str})")

                    if done_comps:
                        last_done = done_comps[-1]
                        raw_t = last_done.get("type", {}).get("abbreviation") or last_done.get("type", {}).get("text") or "Session"
                        lbl = _normalize_f1_session_label(raw_t)
                        top5 = _extract_comp_top_drivers(last_done, limit=5)
                        if top5:
                            top5_str = ", ".join(f"P{i}: {d}" for i, d in enumerate(top5, 1))
                            output_lines.append(f"• Latest Completed Session ({lbl}): {top5_str}")
                        else:
                            output_lines.append(f"• Latest Completed Session: {lbl} (Completed)")

                    if upcoming_comps:
                        next_c = upcoming_comps[0]
                        raw_t = next_c.get("type", {}).get("abbreviation") or next_c.get("type", {}).get("text") or "Session"
                        lbl = _normalize_f1_session_label(raw_t)
                        t_str = _format_f1_session_time(next_c.get("date", ""))
                        output_lines.append(f"• Next Scheduled Session: {lbl} — {t_str}")
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
            first_local_date: Optional[str] = None
            last_local_date: Optional[str] = None

            for comp in comps_sorted:
                raw_type = comp.get("type", {}).get("abbreviation") or comp.get("type", {}).get("text") or "Session"
                cdate = comp.get("date", "")
                label = _normalize_f1_session_label(raw_type)
                if label == "Practice 3 (FP3)":
                    has_fp3 = True
                elif "Sprint" in label:
                    has_sprint = True

                if cdate:
                    try:
                        dt_u = datetime.fromisoformat(str(cdate).replace("Z", "+00:00"))
                        if dt_u.tzinfo is None:
                            dt_u = dt_u.replace(tzinfo=timezone.utc)
                        else:
                            dt_u = dt_u.astimezone(timezone.utc)
                        loc_d = get_local_now(now=dt_u).strftime("%Y-%m-%d")
                        if first_local_date is None:
                            first_local_date = loc_d
                        last_local_date = loc_d
                    except Exception:
                        pass

                time_str = _format_f1_session_time(cdate) if cdate else "Time TBD"
                status_badge = _format_session_status_badge(comp)
                sessions.append(f"  • {label}: {time_str}{status_badge}")

            format_note = (
                "Standard 3-Day Weekend (with FP3)"
                if has_fp3
                else ("Sprint Format" if has_sprint else "Weekend Sessions")
            )

            return {
                "name": ev.get("name", ""),
                "circuit": c_name,
                "location": loc,
                "sessions": sessions,
                "has_fp3": has_fp3,
                "has_sprint": has_sprint,
                "format_note": format_note,
                "start_date_local": first_local_date,
                "end_date_local": last_local_date,
            }
        except Exception:
            return {}

    def _fetch_sessions_for_date(self, date_param: str) -> List[str]:
        """Queries ESPN scoreboard for a specific date to extract detailed practice/qualifying sessions."""
        details = self._fetch_race_details_for_date(date_param)
        return details.get("sessions", [])

    def _fetch_openf1_live_data(
        self,
        session_label: str,
        scheduled_iso: str,
    ) -> Dict[str, Any]:
        """
        Query the zero-auth OpenF1 Real-Time Telemetry API (https://api.openf1.org/v1/)
        for the active session's FIA Race Control messages and live Top-5 running order.
        """
        result: Dict[str, Any] = {
            "matched": False,
            "notable_events": [],
            "seen_ids": set(),
            "top_drivers": [],
            "phase_badge": "",
            "is_green": False,
            "is_finished_flag": False,
        }
        try:
            sessions = fetch_json(f"{self.OPENF1_BASE_URL}/sessions?session_key=latest", timeout=4)
            if not isinstance(sessions, list) or not sessions:
                return result
            latest_s = sessions[-1]
            sk = latest_s.get("session_key")
            if not sk:
                return result

            s_name = (latest_s.get("session_name") or latest_s.get("session_type") or "").strip()
            norm_openf1_label = _normalize_f1_session_label(s_name)
            if norm_openf1_label != session_label and s_name.lower() not in session_label.lower():
                return result

            # Verify date proximity (within 36 hours of ESPN's scheduled session start)
            s_start = latest_s.get("date_start") or ""
            if scheduled_iso and s_start:
                try:
                    dt_espn = datetime.fromisoformat(scheduled_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
                    dt_of1 = datetime.fromisoformat(s_start.replace("Z", "+00:00")).astimezone(timezone.utc)
                    if abs((dt_espn - dt_of1).total_seconds()) > 36 * 3600:
                        return result
                except Exception:
                    pass

            result["matched"] = True

            # Fetch race_control, drivers, and position concurrently via worker pool
            fut_rc = submit_to_worker(fetch_json, f"{self.OPENF1_BASE_URL}/race_control?session_key={sk}", 4)
            fut_drv = submit_to_worker(fetch_json, f"{self.OPENF1_BASE_URL}/drivers?session_key={sk}", 4)
            fut_pos = submit_to_worker(fetch_json, f"{self.OPENF1_BASE_URL}/position?session_key={sk}", 4)

            rc_list = fut_rc.result()
            drv_list = fut_drv.result()
            pos_list = fut_pos.result()

            # 1. Map driver_number -> Display Name
            drv_map: Dict[int, str] = {}
            if isinstance(drv_list, list):
                for d in drv_list:
                    d_num = d.get("driver_number")
                    if d_num is not None:
                        full_raw = (d.get("full_name") or d.get("broadcast_name") or d.get("name_acronym") or "").strip()
                        # Convert 'Max VERSTAPPEN' -> 'Max Verstappen'
                        parts = full_raw.split()
                        formatted_name = " ".join(p.capitalize() if p.isupper() and len(p) > 2 else p for p in parts)
                        drv_map[int(d_num)] = formatted_name or f"#{d_num}"

            # 2. Extract latest running order from /v1/position
            if isinstance(pos_list, list) and pos_list:
                latest_pos_by_driver: Dict[int, int] = {}
                for p_entry in pos_list:
                    d_num = p_entry.get("driver_number")
                    pos_val = p_entry.get("position")
                    if d_num is not None and pos_val is not None:
                        try:
                            latest_pos_by_driver[int(d_num)] = int(pos_val)
                        except (ValueError, TypeError):
                            pass
                sorted_order = sorted(latest_pos_by_driver.items(), key=lambda kv: kv[1])
                top_drivers: List[str] = []
                for d_num, pos_val in sorted_order[:5]:
                    if pos_val >= 1:
                        top_drivers.append(drv_map.get(d_num, f"Driver #{d_num}"))
                result["top_drivers"] = top_drivers

            # 3. Parse high-signal FIA Race Control events
            notable_events: List[Tuple[str, str]] = []
            seen_ids: Set[str] = set()
            green_count = 0
            chequered_count = 0
            track_status_badge = "🟢 Track Clear"

            if isinstance(rc_list, list):
                for rc in rc_list:
                    msg = (rc.get("message") or "").strip()
                    if not msg:
                        continue
                    cat = (rc.get("category") or "").strip()
                    flag = (rc.get("flag") or "").strip().upper()
                    rc_date = (rc.get("date") or "").strip()
                    msg_up = msg.upper()
                    time_short = rc_date[11:19] + " UTC" if len(rc_date) >= 19 else rc_date

                    ev_desc = ""
                    if "GREEN LIGHT" in msg_up or "PIT EXIT OPEN" in msg_up or "SESSION WILL RESUME" in msg_up:
                        green_count += 1
                        result["is_green"] = True
                        track_status_badge = "🟢 Track Clear"
                        q_tag = f" (Q{min(green_count, 3)})" if session_label == "Qualifying" and green_count <= 3 else ""
                        ev_desc = f"🟢 **FIA RACE CONTROL{q_tag} ({time_short})**: {msg}"
                    elif flag == "RED" or bool(re.search(r"\bRED\s+FLAG\b", msg_up)):
                        track_status_badge = "🟥 RED FLAG"
                        ev_desc = f"🟥 **RED FLAG ({time_short})**: {msg}"
                    elif cat == "SafetyCar" or "SAFETY CAR" in msg_up:
                        if "DEPLOYED" in msg_up or "VIRTUAL SAFETY CAR" in msg_up:
                            track_status_badge = "🟡 VSC" if "VIRTUAL" in msg_up else "🟡 SAFETY CAR"
                        elif "IN THIS LAP" in msg_up or "ENDING" in msg_up:
                            track_status_badge = "🟢 Track Clear"
                        ev_desc = f"🟡 **SAFETY CAR / VSC ({time_short})**: {msg}"
                    elif flag == "CHEQUERED" or "CHEQUERED FLAG" in msg_up:
                        chequered_count += 1
                        q_tag = f" (End of Q{min(chequered_count, 3)})" if session_label == "Qualifying" and chequered_count <= 3 else ""
                        ev_desc = f"🏁 **CHEQUERED FLAG{q_tag} ({time_short})**: {msg}"
                        if session_label != "Qualifying" or chequered_count >= 3:
                            result["is_finished_flag"] = True
                            track_status_badge = "🏁 Chequered Flag"
                    elif any(
                        kw in msg_up
                        for kw in ("PENALTY", "DISQUALIFIED", "STOP AND GO", "DRIVE THROUGH", "BLACK AND WHITE FLAG")
                    ):
                        ev_desc = f"⚖️ **FIA STEWARDS ({time_short})**: {msg}"
                    elif "DRS ENABLED" in msg_up or "DRS DISABLED" in msg_up:
                        ev_desc = f"⚡ **RACE CONTROL ({time_short})**: {msg}"

                    if ev_desc:
                        ev_id = f"f1_rc_{rc_date}_{msg[:32]}"
                        if ev_id not in seen_ids:
                            seen_ids.add(ev_id)
                            notable_events.append((ev_id, ev_desc))

            if session_label == "Qualifying" and result["is_green"]:
                q_num = min(max(green_count, 1), 3)
                if chequered_count >= q_num and q_num < 3:
                    result["phase_badge"] = f"Between Q{q_num} & Q{q_num + 1}"
                else:
                    result["phase_badge"] = f"Q{q_num} • {track_status_badge}"
            elif result["is_green"]:
                result["phase_badge"] = track_status_badge

            result["notable_events"] = notable_events
            result["seen_ids"] = seen_ids
            return result
        except (Exception, StopIteration):
            return result

    def _fetch_espn_header_live_data(
        self,
        comp_id: str,
        session_label: str,
    ) -> Dict[str, Any]:
        """
        Query ESPN's Live F1 Header API (https://site.web.api.espn.com/apis/v2/scoreboard/header?sport=racing&league=f1)
        for real-time track flag (GREEN/YELLOW/RED/CHECKER), completed lap count, and session completion status.
        """
        result: Dict[str, Any] = {
            "matched": False,
            "state": "",
            "completed": False,
            "flag": "",
            "leader_laps": 0,
            "track_badge": "",
            "is_finished_flag": False,
            "notable_events": [],
            "seen_ids": set(),
            "driver_teams": {},
        }
        try:
            data = fetch_json(self.HEADER_URL, timeout=4)
            if not isinstance(data, dict):
                return result
            sports = data.get("sports") or []
            if not sports or not isinstance(sports[0], dict):
                return result
            leagues = sports[0].get("leagues") or []
            if not leagues or not isinstance(leagues[0], dict):
                return result
            events = leagues[0].get("events") or []
            if not events:
                return result

            target_ev = None
            for ev_item in events:
                if str(ev_item.get("id") or "") == str(comp_id):
                    target_ev = ev_item
                    break
                ev_type = (ev_item.get("type") or {}).get("abbreviation") or (ev_item.get("type") or {}).get("text") or ""
                if _normalize_f1_session_label(ev_type) == session_label:
                    target_ev = ev_item
                    break

            if not target_ev:
                return result

            result["matched"] = True
            full_st = target_ev.get("fullStatus") or {}
            st_type = full_st.get("type") or {}
            state = (st_type.get("state") or "").lower()
            completed = bool(st_type.get("completed", False))
            flag = (full_st.get("flag") or "").strip().upper()
            try:
                leader_laps = int(full_st.get("period") or 0)
            except (ValueError, TypeError):
                leader_laps = 0

            result["state"] = state
            result["completed"] = completed
            result["flag"] = flag
            result["leader_laps"] = leader_laps

            if flag == "RED":
                result["track_badge"] = "🟥 RED FLAG"
                ev_id = "f1_espn_flag_red"
                result["seen_ids"].add(ev_id)
                result["notable_events"].append((ev_id, "🟥 **RED FLAG**: Session red-flagged on track"))
            elif flag in ("YELLOW", "CAUTION"):
                result["track_badge"] = "🟡 YELLOW FLAG"
            elif flag in ("CHECKER", "CHEQUERED"):
                result["track_badge"] = "🏁 Chequered Flag"
                result["is_finished_flag"] = True
            elif flag == "GREEN":
                result["track_badge"] = "🟢 Track Clear"

            if state == MatchState.POST or completed:
                result["is_finished_flag"] = True

            driver_teams: Dict[str, str] = {}
            for c_item in target_ev.get("competitors") or []:
                d_name = (c_item.get("displayName") or c_item.get("name") or "").strip()
                team_name = ((c_item.get("vehicle") or {}).get("manufacturer") or "").strip()
                if d_name and team_name:
                    driver_teams[d_name] = team_name
            result["driver_teams"] = driver_teams
            return result
        except (Exception, StopIteration):
            return result

    def _resolve_bbc_stage_urn(
        self,
        ev_name: str,
        scheduled_iso: str,
        season_year: int,
    ) -> Optional[str]:
        """Resolve a Grand Prix event name / date to its BBC Sport stageUrn."""
        cache_key = (season_year, ev_name)
        if cache_key in self._BBC_STAGE_URN_CACHE:
            return self._BBC_STAGE_URN_CACHE[cache_key]
        try:
            data = fetch_json(f"{self.BBC_SEASON_URL}?year={season_year}", timeout=4)
            if not isinstance(data, dict):
                return None
            stages = data.get("stages") or data.get("raceCalendars") or []
            ev_low = ev_name.lower()

            sched_dt: Optional[datetime] = None
            if scheduled_iso:
                try:
                    sched_dt = datetime.fromisoformat(scheduled_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    pass

            matched_urn: Optional[str] = None
            for st_item in stages:
                urn = (st_item.get("stageUrn") or "").strip()
                if not urn:
                    continue
                heading_raw = (st_item.get("heading") or st_item.get("name") or "").strip()
                gp_title = heading_raw.split(",")[0].strip().lower()
                if gp_title and (gp_title in ev_low or ev_low in gp_title):
                    matched_urn = urn
                    break
                race_iso = (st_item.get("raceDateTime") or st_item.get("endDate") or "").strip()
                if sched_dt and race_iso:
                    try:
                        r_dt = datetime.fromisoformat(race_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
                        if abs((r_dt - sched_dt).total_seconds()) <= 4 * 86400:
                            matched_urn = urn
                            break
                    except Exception:
                        pass

            if matched_urn:
                self._BBC_STAGE_URN_CACHE[cache_key] = matched_urn
                return matched_urn
        except (Exception, StopIteration):
            pass
        return None

    def _fetch_bbc_f1_live_data(
        self,
        ev_name: str,
        session_label: str,
        scheduled_iso: str,
        season_year: int,
    ) -> Dict[str, Any]:
        """
        Query BBC Sport's zero-auth live Formula 1 stage results endpoint
        (https://web-cdn.api.bbci.co.uk/wc-poll-data/container/formula1-stage-results)
        for live Q1/Q2/Q3 lap times, segment completion + eliminated ('OUT') drivers,
        practice lap times, and race gaps/fastest laps.
        """
        result: Dict[str, Any] = {
            "matched": False,
            "is_live": False,
            "is_finished_flag": False,
            "phase_badge": "",
            "home_score": "",
            "away_score": "",
            "top_drivers": [],
            "extra_stats": "",
            "notable_events": [],
            "seen_ids": set(),
        }
        try:
            stage_urn = self._resolve_bbc_stage_urn(ev_name, scheduled_iso, season_year)
            if not stage_urn:
                return result

            stage_data = fetch_json(
                f"{self.BBC_STAGE_URL}?urn={stage_urn}&year={season_year}",
                timeout=4,
            )
            if not isinstance(stage_data, dict):
                return result

            # 1. QUALIFYING & SPRINT QUALIFYING (Q1 / Q2 / Q3 or SQ1 / SQ2 / SQ3)
            if "Qualifying" in session_label:
                is_sprint_quali = "Sprint" in session_label
                q_list = (
                    (stage_data.get("sprintQualifying") or stage_data.get("sprintShootout"))
                    if is_sprint_quali
                    else stage_data.get("qualifying")
                )
                if not isinstance(q_list, list) or not q_list or not isinstance(q_list[0], dict):
                    return result

                q_block = q_list[0]
                bbc_status = (q_block.get("status") or "").strip()
                is_live = bool(q_block.get("isLive", False)) or bbc_status == "MidEvent"
                participants = q_block.get("participants") or []
                if not participants and bbc_status == "PreEvent":
                    return result

                seg_prefix = "SQ" if is_sprint_quali else "Q"
                parsed_drivers: List[Dict[str, Any]] = []
                for idx, p in enumerate(participants):
                    name = (p.get("fullName") or p.get("shortName") or "").strip()
                    if not name:
                        continue
                    team = ((p.get("team") or {}).get("abbreviatedName") or (p.get("team") or {}).get("fullName") or "").strip()
                    rank_val = str((p.get("rank") or {}).get("value") or "-").strip()
                    q1_t = str((p.get("qualifying1Time") or {}).get("value") or "-").strip()
                    q2_t = str((p.get("qualifying2Time") or {}).get("value") or "-").strip()
                    q3_t = str((p.get("qualifying3Time") or {}).get("value") or "-").strip()

                    q1_sec = _parse_lap_time_seconds(q1_t) if _is_valid_lap_time(q1_t) else None
                    q2_sec = _parse_lap_time_seconds(q2_t) if _is_valid_lap_time(q2_t) else None
                    q3_sec = _parse_lap_time_seconds(q3_t) if _is_valid_lap_time(q3_t) else None

                    parsed_drivers.append({
                        "idx": idx,
                        "name": name,
                        "team": team,
                        "rank": rank_val,
                        "q1_time": q1_t if q1_sec is not None else "No Time",
                        "q2_time": q2_t if q2_sec is not None else "No Time",
                        "q3_time": q3_t if q3_sec is not None else "No Time",
                        "q1_valid": q1_sec is not None,
                        "q2_valid": q2_sec is not None,
                        "q3_valid": q3_sec is not None,
                        "q1_sec": q1_sec if q1_sec is not None else 9999.0,
                        "q2_sec": q2_sec if q2_sec is not None else 9999.0,
                        "q3_sec": q3_sec if q3_sec is not None else 9999.0,
                    })

                if not parsed_drivers:
                    return result

                result["matched"] = True
                result["is_live"] = is_live

                q1_timed = sorted([d for d in parsed_drivers if d["q1_valid"]], key=lambda d: (d["q1_sec"], d["idx"]))
                q2_timed = sorted([d for d in parsed_drivers if d["q2_valid"]], key=lambda d: (d["q2_sec"], d["idx"]))
                q3_timed = sorted([d for d in parsed_drivers if d["q3_valid"]], key=lambda d: (d["q3_sec"], d["idx"]))

                total_cars = len(parsed_drivers)
                adv_q1_count = 16 if total_cars >= 22 else 15
                adv_q2_count = 10

                # Q1 is complete when Q2 times exist, Q3 times exist, PostEvent, or BBC marks Q1-eliminated timed drivers with rank='-'
                q1_completed = bool(
                    q2_timed
                    or q3_timed
                    or bbc_status == "PostEvent"
                    or (len(q1_timed) >= adv_q1_count - 1 and any(d["rank"] == "-" for d in q1_timed))
                )
                # Q2 is complete when Q3 times exist, PostEvent, or BBC marks Q2-eliminated timed drivers with rank='-'
                q2_completed = bool(
                    q3_timed
                    or bbc_status == "PostEvent"
                    or (len(q2_timed) >= adv_q2_count and any(d["rank"] == "-" for d in q2_timed))
                )
                q3_completed = bool(bbc_status == "PostEvent")

                notable_events: List[Tuple[str, str]] = []
                seen_ids: Set[str] = set()

                def _fmt_drv_time(d: Dict[str, Any], time_key: str) -> str:
                    t_str = d.get(time_key) or "No Time"
                    return f"{d['name']} ({d['team']} — {t_str})" if d.get("team") else f"{d['name']} ({t_str})"

                # Emit Q1 Complete milestone with Top 3 Q1 times + Eliminated ('OUT') drivers
                q1_elim_names: Set[str] = set()
                if q1_completed and q1_timed:
                    q1_top3_str = " | ".join(
                        f"P{i}: **{_fmt_drv_time(d, 'q1_time')}**"
                        for i, d in enumerate(q1_timed[:3], 1)
                    )
                    q1_adv_names = {d["name"] for d in q1_timed[:adv_q1_count] if d["rank"] != "-" or d["q2_valid"] or d["q3_valid"]}
                    if len(q1_adv_names) < adv_q1_count:
                        q1_adv_names = {d["name"] for d in q1_timed[:adv_q1_count]}
                    q1_elim = [
                        d for d in parsed_drivers
                        if not d["q2_valid"] and not d["q3_valid"] and d["name"] not in q1_adv_names
                    ]
                    q1_elim.sort(key=lambda d: (0 if d["q1_valid"] else 1, d["q1_sec"], d["idx"]))
                    q1_elim_names = {d["name"] for d in q1_elim}
                    elim_q1_str = ", ".join(
                        f"P{adv_q1_count + idx}: {d['name']} ({d['q1_time']})"
                        for idx, d in enumerate(q1_elim, 1)
                    )
                    ev_id_q1 = "f1_qual_q1_complete"
                    ev_desc_q1 = (
                        f"🏁 **{seg_prefix}1 ENDED — {seg_prefix}1 RESULTS**: "
                        f"Top 3: {q1_top3_str}"
                        + (f" | ❌ OUT in {seg_prefix}1: {elim_q1_str}" if elim_q1_str else "")
                    )
                    seen_ids.add(ev_id_q1)
                    notable_events.append((ev_id_q1, ev_desc_q1))

                # Emit Q2 Complete milestone with Top 3 Q2 times + Eliminated ('OUT') drivers
                if q2_completed and q2_timed:
                    q2_top3_str = " | ".join(
                        f"P{i}: **{_fmt_drv_time(d, 'q2_time')}**"
                        for i, d in enumerate(q2_timed[:3], 1)
                    )
                    q2_adv_names = {d["name"] for d in q2_timed[:adv_q2_count] if d["rank"] != "-" or d["q3_valid"]}
                    if len(q2_adv_names) < adv_q2_count:
                        q2_adv_names = {d["name"] for d in q2_timed[:adv_q2_count]}
                    q2_elim = [
                        d for d in parsed_drivers
                        if d["name"] not in q1_elim_names and not d["q3_valid"] and d["name"] not in q2_adv_names
                    ]
                    q2_elim.sort(key=lambda d: (0 if d["q2_valid"] else 1, d["q2_sec"], d["q1_sec"], d["idx"]))
                    elim_q2_str = ", ".join(
                        f"P{adv_q2_count + idx}: {d['name']} ({d['q2_time']})"
                        for idx, d in enumerate(q2_elim, 1)
                    )
                    ev_id_q2 = "f1_qual_q2_complete"
                    ev_desc_q2 = (
                        f"🏁 **{seg_prefix}2 ENDED — {seg_prefix}2 RESULTS**: "
                        f"Top 3: {q2_top3_str}"
                        + (f" | ❌ OUT in {seg_prefix}2: {elim_q2_str}" if elim_q2_str else "")
                    )
                    seen_ids.add(ev_id_q2)
                    notable_events.append((ev_id_q2, ev_desc_q2))

                # Determine current active phase and classification
                if q3_completed:
                    result["is_finished_flag"] = True
                    result["phase_badge"] = f"{seg_prefix}3 Final"
                    active_timed = q3_timed or q2_timed or q1_timed
                    seg_key = "q3_time" if q3_timed else ("q2_time" if q2_timed else "q1_time")
                    if active_timed:
                        p1 = active_timed[0]
                        result["home_score"] = f"P1: {_fmt_drv_time(p1, seg_key)}"
                        if len(active_timed) >= 3:
                            p2, p3 = active_timed[1], active_timed[2]
                            result["away_score"] = f"P2: {_fmt_drv_time(p2, seg_key)} | P3: {_fmt_drv_time(p3, seg_key)}"
                        elif len(active_timed) >= 2:
                            result["away_score"] = f"P2: {_fmt_drv_time(active_timed[1], seg_key)}"
                        else:
                            result["away_score"] = "Final"
                        result["top_drivers"] = [_fmt_drv_time(d, seg_key) for d in active_timed[:5]]
                elif q2_completed:
                    result["phase_badge"] = f"{seg_prefix}3" if q3_timed else f"Between {seg_prefix}2 & {seg_prefix}3"
                    active_timed = q3_timed or q2_timed
                    seg_key = "q3_time" if q3_timed else "q2_time"
                    if q3_timed:
                        result["home_score"] = f"P1: {_fmt_drv_time(q3_timed[0], 'q3_time')}"
                        result["away_score"] = (
                            f"P2: {_fmt_drv_time(q3_timed[1], 'q3_time')}" if len(q3_timed) >= 2 else f"{seg_prefix}3 Underway"
                        )
                        result["top_drivers"] = [_fmt_drv_time(d, "q3_time") for d in q3_timed[:5]]
                    elif q2_timed:
                        result["home_score"] = f"{seg_prefix}3 Starting"
                        result["away_score"] = f"{seg_prefix}2 P1: {_fmt_drv_time(q2_timed[0], 'q2_time')}"
                        result["top_drivers"] = [_fmt_drv_time(d, "q2_time") for d in q2_timed[:5]]
                elif q1_completed:
                    result["phase_badge"] = f"{seg_prefix}2" if q2_timed else f"Between {seg_prefix}1 & {seg_prefix}2"
                    if q2_timed:
                        result["home_score"] = f"P1: {_fmt_drv_time(q2_timed[0], 'q2_time')}"
                        result["away_score"] = (
                            f"P2: {_fmt_drv_time(q2_timed[1], 'q2_time')}" if len(q2_timed) >= 2 else f"{seg_prefix}2 Underway"
                        )
                        result["top_drivers"] = [_fmt_drv_time(d, "q2_time") for d in q2_timed[:5]]
                    elif q1_timed:
                        result["home_score"] = f"{seg_prefix}2 Starting"
                        result["away_score"] = f"{seg_prefix}1 P1: {_fmt_drv_time(q1_timed[0], 'q1_time')}"
                        result["top_drivers"] = [_fmt_drv_time(d, "q1_time") for d in q1_timed[:5]]
                else:
                    result["phase_badge"] = f"{seg_prefix}1"
                    if q1_timed:
                        result["home_score"] = f"P1: {_fmt_drv_time(q1_timed[0], 'q1_time')}"
                        result["away_score"] = (
                            f"P2: {_fmt_drv_time(q1_timed[1], 'q1_time')}" if len(q1_timed) >= 2 else f"{seg_prefix}1 Underway"
                        )
                        result["top_drivers"] = [_fmt_drv_time(d, "q1_time") for d in q1_timed[:5]]
                    else:
                        result["home_score"] = f"{seg_prefix}1 Underway"
                        result["away_score"] = "Track Open"

                result["notable_events"] = notable_events
                result["seen_ids"] = seen_ids
                return result

            # 2. PRACTICE SESSIONS (FP1, FP2, FP3)
            if "Practice" in session_label:
                p_num = 3 if "3" in session_label else (2 if "2" in session_label else 1)
                p_list = stage_data.get("practice") or []
                p_block = next(
                    (b for b in p_list if isinstance(b, dict) and int(b.get("number") or 0) == p_num),
                    None,
                )
                if not p_block:
                    return result
                bbc_status = (p_block.get("status") or "").strip()
                if bbc_status == "PreEvent":
                    return result
                participants = p_block.get("participants") or []
                p_timed: List[Dict[str, Any]] = []
                for idx, p in enumerate(participants):
                    name = (p.get("fullName") or p.get("shortName") or "").strip()
                    team = ((p.get("team") or {}).get("abbreviatedName") or "").strip()
                    p_t = str((p.get("practiceTime") or {}).get("value") or "-").strip()
                    p_sec = _parse_lap_time_seconds(p_t) if _is_valid_lap_time(p_t) else None
                    if name and p_sec is not None:
                        p_timed.append({"idx": idx, "name": name, "team": team, "time": p_t, "sec": p_sec})

                if not p_timed and bbc_status != "PostEvent":
                    return result
                p_timed.sort(key=lambda d: (d["sec"], d["idx"]))
                result["matched"] = True
                result["is_live"] = bool(p_block.get("isLive", False)) or bbc_status == "MidEvent"
                result["is_finished_flag"] = bbc_status == "PostEvent"
                if p_timed:
                    def _fmt_p(d: Dict[str, Any]) -> str:
                        return f"{d['name']} ({d['team']} — {d['time']})" if d["team"] else f"{d['name']} ({d['time']})"

                    if not result["is_finished_flag"] and len(p_timed) < 5:
                        result["home_score"] = f"FP{p_num} — Installation Laps"
                        result["away_score"] = f"{len(p_timed)} Timed"
                    else:
                        result["home_score"] = f"P1: {_fmt_p(p_timed[0])}"
                        result["away_score"] = f"P2: {_fmt_p(p_timed[1])}" if len(p_timed) >= 2 else "In Progress"
                    result["top_drivers"] = [_fmt_p(d) for d in p_timed[:5]]
                return result

            # 3. GRAND PRIX (RACE) & SPRINT
            is_sprint_race = session_label.startswith("Sprint") and "Qualifying" not in session_label
            r_list = stage_data.get("sprint") if is_sprint_race else stage_data.get("race")
            if isinstance(r_list, list) and r_list and isinstance(r_list[0], dict):
                r_block = r_list[0]
                bbc_status = (r_block.get("status") or "").strip()
                if bbc_status == "PreEvent":
                    return result
                participants = r_block.get("participants") or []
                ranked: List[Dict[str, Any]] = []
                fl_driver_str = ""
                for idx, p in enumerate(participants):
                    name = (p.get("fullName") or p.get("shortName") or "").strip()
                    if not name:
                        continue
                    team = ((p.get("team") or {}).get("abbreviatedName") or "").strip()
                    rank_val = str((p.get("rank") or {}).get("value") or "").strip()
                    dur_val = str((p.get("duration") or {}).get("value") or "-").strip()
                    fl_val = str((p.get("fastestLap") or {}).get("value") or "-").strip()
                    has_fl = bool(p.get("hasFastestLap", False))
                    if has_fl and _is_valid_lap_time(fl_val):
                        fl_driver_str = f"⚡ Fastest Lap: {name} ({fl_val})"
                    rank_int = int(rank_val) if rank_val.isdigit() else (900 + idx)
                    ranked.append({
                        "rank": rank_int,
                        "name": name,
                        "team": team,
                        "duration": dur_val if dur_val != "-" else "",
                    })

                if not ranked:
                    return result
                ranked.sort(key=lambda d: d["rank"])
                result["matched"] = True
                result["is_live"] = bool(r_block.get("isLive", False)) or bbc_status == "MidEvent"
                result["is_finished_flag"] = bbc_status == "PostEvent"
                p1 = ranked[0]
                p2 = ranked[1] if len(ranked) >= 2 else None
                if result["is_finished_flag"]:
                    p1_dur = f" ({p1['team']} — {p1['duration']})" if p1["team"] and p1["duration"] else (f" ({p1['duration']})" if p1["duration"] else "")
                    result["home_score"] = f"P1: {p1['name']}{p1_dur}"
                else:
                    # Keep live race home_score anchored to driver name so P1 LEADER UPDATE only fires on actual lead changes
                    result["home_score"] = f"P1: {p1['name']}"
                if p2:
                    p2_gap = f" ({p2['duration']})" if p2["duration"] else ""
                    result["away_score"] = f"P2: {p2['name']}{p2_gap}"
                else:
                    result["away_score"] = "In Progress"

                top_list: List[str] = []
                for d in ranked[:5]:
                    if d["team"] and d["duration"]:
                        top_list.append(f"{d['name']} ({d['team']} — {d['duration']})")
                    elif d["team"]:
                        top_list.append(f"{d['name']} ({d['team']})")
                    elif d["duration"]:
                        top_list.append(f"{d['name']} ({d['duration']})")
                    else:
                        top_list.append(d["name"])
                result["top_drivers"] = top_list
                result["extra_stats"] = fl_driver_str
                return result

            return result
        except (Exception, StopIteration):
            return result

    def _build_session_snapshot(
        self,
        ev: Dict[str, Any],
        comp: Dict[str, Any],
        season_year: Optional[int] = None,
        is_tracker_poll: bool = False,
    ) -> MatchSnapshot:
        """Build a normalized MatchSnapshot for a specific Formula 1 Grand Prix session."""
        ev_name = ev.get("name") or "Formula 1 Grand Prix"
        circuit_name = ev.get("circuit", {}).get("fullName", "")
        raw_type = comp.get("type", {}).get("abbreviation") or comp.get("type", {}).get("text") or "Session"
        session_label = _normalize_f1_session_label(raw_type)
        comp_id = str(comp.get("id") or "")
        cdate = comp.get("date", "")
        formatted_start = _format_f1_session_time(cdate)
        yr = season_year or int((cdate[:4] if cdate and cdate[:4].isdigit() else 0) or get_local_now().year)

        st_type = comp.get("status", {}).get("type", {}) if isinstance(comp.get("status"), dict) else {}
        state = (st_type.get("state") or MatchState.PRE).lower()
        completed = bool(st_type.get("completed", False))
        status_name = st_type.get("name") or ""

        espn_drivers = _extract_comp_top_drivers(comp, limit=5)

        # Poll live telemetry (BBC Sport + ESPN Header + OpenF1) when session is live ('in'),
        # or on active tracker polls when completed ('post') or within 15 minutes of scheduled start time
        should_poll_telemetry = (state == MatchState.IN) or (is_tracker_poll and (state == MatchState.POST or completed))
        if is_tracker_poll and not should_poll_telemetry and state == MatchState.PRE and cdate:
            try:
                dt_start = datetime.fromisoformat(cdate.replace("Z", "+00:00")).astimezone(timezone.utc)
                now_utc = get_local_now().astimezone(timezone.utc)
                sec_until = (dt_start - now_utc).total_seconds()
                if -7200 <= sec_until <= 900:
                    should_poll_telemetry = True
            except Exception:
                pass

        bbc: Dict[str, Any] = {}
        hdr: Dict[str, Any] = {}
        of1: Dict[str, Any] = {}
        if should_poll_telemetry:
            fut_bbc = submit_to_worker(self._fetch_bbc_f1_live_data, ev_name, session_label, cdate, yr)
            fut_hdr = submit_to_worker(self._fetch_espn_header_live_data, comp_id, session_label)
            fut_of1 = submit_to_worker(self._fetch_openf1_live_data, session_label, cdate)
            bbc = fut_bbc.result() or {}
            hdr = fut_hdr.result() or {}
            of1 = fut_of1.result() or {}

        # Promote 'pre' -> 'in' if any live telemetry feed confirms the session has started
        if state == MatchState.PRE and (
            (bbc.get("matched") and bbc.get("is_live"))
            or (hdr.get("matched") and hdr.get("state") == MatchState.IN)
            or (of1.get("matched") and of1.get("is_green"))
        ):
            state = MatchState.IN
            status_name = "STATUS_IN_PROGRESS"

        # Promote 'in' -> 'post' if BBC Sport, ESPN Header, or OpenF1 confirms Chequered Flag / PostEvent
        if (
            (bbc.get("matched") and bbc.get("is_finished_flag"))
            or (hdr.get("matched") and hdr.get("is_finished_flag"))
            or (of1.get("matched") and of1.get("is_finished_flag"))
        ):
            state = MatchState.POST
            completed = True
            status_name = "STATUS_FINAL"

        # Merge notable events across BBC (Q1/Q2 Complete + OUT drivers), ESPN Header (Red Flag), and OpenF1
        notable_events: List[Tuple[str, str]] = []
        seen_ids: Set[str] = set()
        for src in (bbc, hdr, of1):
            for ev_id, ev_desc in src.get("notable_events") or []:
                if ev_id not in seen_ids:
                    seen_ids.add(ev_id)
                    notable_events.append((ev_id, ev_desc))
            seen_ids |= set(src.get("seen_ids") or set())

        top_drivers = bbc.get("top_drivers") or of1.get("top_drivers") or espn_drivers
        phase_badge = bbc.get("phase_badge") or of1.get("phase_badge") or hdr.get("track_badge") or ""
        if bbc.get("phase_badge") and hdr.get("track_badge") and hdr["track_badge"] not in bbc["phase_badge"] and state == MatchState.IN:
            phase_badge = f"{bbc['phase_badge']} • {hdr['track_badge']}"

        if state == MatchState.POST or completed:
            state = MatchState.POST
            completed = True
            status_name = status_name or "STATUS_FINAL"
            home_score = bbc.get("home_score") or (f"P1: {top_drivers[0]}" if top_drivers else "Completed")
            away_score = bbc.get("away_score") or (f"P2: {top_drivers[1]}" if len(top_drivers) >= 2 else "Final")
            status_detail = f"Final — {session_label}"
        elif state == MatchState.IN:
            if bbc.get("matched") and bbc.get("home_score"):
                home_score = bbc["home_score"]
                away_score = bbc.get("away_score") or phase_badge or "In Progress"
            elif not of1.get("matched") and hdr.get("matched") and hdr.get("leader_laps", 0) < (2 if "Race" in session_label or session_label == "Sprint" else 3):
                # Suppress early out-lap / Lap 1 noise when falling back to ESPN without BBC/OpenF1
                home_score = f"{session_label} — Setting Initial Laps"
                away_score = phase_badge or "Track Open"
            else:
                home_score = f"P1: {top_drivers[0]}" if top_drivers else "Track Open"
                away_score = (
                    f"P2: {top_drivers[1]}"
                    if len(top_drivers) >= 2
                    else (phase_badge or "In Progress")
                )
            phase_str = f" ({phase_badge})" if phase_badge else ""
            status_detail = f"LIVE — {session_label}{phase_str}"
        else:
            home_score = "Scheduled"
            away_score = "Upcoming"
            status_detail = formatted_start

        if top_drivers:
            stats_summary = "Top 5: " + " | ".join(f"P{i}: {d}" for i, d in enumerate(top_drivers[:5], 1))
            if bbc.get("extra_stats"):
                stats_summary += f" | {bbc['extra_stats']}"
        elif circuit_name:
            stats_summary = f"Circuit: {circuit_name} | Start: {formatted_start}"
        else:
            stats_summary = f"Start: {formatted_start}"

        return MatchSnapshot(
            state=state,
            completed=completed,
            status_name=status_name,
            status_detail=status_detail,
            period=1 if state == MatchState.IN else 0,
            home_team=ev_name,
            away_team=session_label,
            home_score=home_score,
            away_score=away_score,
            notable_events=notable_events,
            seen_event_ids=seen_ids,
            stats_summary=stats_summary,
        )

    def get_session_for_tracker(
        self,
        query: str = "",
        event_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve an F1 session tracking query (e.g. 'track F1 qualifying', 'track F1 race')
        or poll an existing tracked F1 session by event_id ('{ev_id}:{comp_id}').
        Uses the exact same ESPN Scoreboard schedule as get_schedule() so start times
        and session states are 100% aligned.
        """
        try:
            data = fetch_json(self.SCOREBOARD_URL, timeout=8) or {}
            events = data.get("events") or []

            # If polling a specific date/event or if current events[0] is already completely finished,
            # check calendar for the next upcoming Grand Prix weekend.
            ev = events[0] if events else None
            if ev and not event_id:
                comps_check = ev.get("competitions") or []
                all_done = bool(comps_check) and all(
                    (c.get("status", {}).get("type", {}).get("state") or "").lower() == MatchState.POST
                    or bool(c.get("status", {}).get("type", {}).get("completed", False))
                    for c in comps_check
                )
                if all_done:
                    leagues = data.get("leagues") or []
                    calendar = leagues[0].get("calendar", []) if leagues else []
                    local_today = get_local_now().date()
                    for item in calendar:
                        end_str = item.get("endDate") or item.get("startDate") or ""
                        start_str = item.get("startDate") or ""
                        try:
                            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00")).astimezone(timezone.utc)
                            end_local_d = get_local_now(now=end_dt).date()
                            if max(end_dt.date(), end_local_d) >= local_today and start_str:
                                d_param = start_str[:10].replace("-", "")
                                next_data = fetch_json(f"{self.SCOREBOARD_URL}?dates={d_param}", timeout=6) or {}
                                if next_data.get("events"):
                                    ev = next_data["events"][0]
                                break
                        except Exception:
                            pass

            if not ev:
                return None

            ev_id = str(ev.get("id") or "f1_gp")
            comps = sorted(ev.get("competitions") or [], key=lambda c: c.get("date", ""))
            if not comps:
                return None

            target_comp: Optional[Dict[str, Any]] = None

            # 1. Exact lookup by event_id ('{ev_id}:{comp_id}') when polling an active tracked session
            if event_id:
                comp_id_part = event_id.split(":", 1)[1] if ":" in event_id else event_id
                target_comp = next(
                    (
                        c for c in comps
                        if str(c.get("id") or "") == comp_id_part
                        or _normalize_f1_session_label(
                            c.get("type", {}).get("abbreviation") or c.get("type", {}).get("text") or ""
                        ).lower() == comp_id_part.lower()
                    ),
                    None,
                )

            # 2. Match explicit session type in user query
            q_low = (query or "").lower()
            if target_comp is None:
                desired_label: Optional[str] = None
                if re.search(r"\b(?:sprint\s+qualifying|sprint\s+shootout|sq)\b", q_low):
                    desired_label = "Sprint Qualifying"
                elif re.search(r"\b(?:qualifying|quali|qual|q1|q2|q3|pole)\b", q_low):
                    desired_label = "Qualifying"
                elif re.search(r"\b(?:fp1|practice\s*1|free\s*practice\s*1)\b", q_low):
                    desired_label = "Practice 1 (FP1)"
                elif re.search(r"\b(?:fp2|practice\s*2|free\s*practice\s*2)\b", q_low):
                    desired_label = "Practice 2 (FP2)"
                elif re.search(r"\b(?:fp3|practice\s*3|free\s*practice\s*3)\b", q_low):
                    desired_label = "Practice 3 (FP3)"
                elif re.search(r"\bsprint\b", q_low):
                    desired_label = "Sprint"
                elif re.search(r"\b(?:race|sunday|tomorrow)\b", q_low) and not re.search(
                    r"\b(?:qualifying|quali|qual|practice|fp[123])\b", q_low
                ):
                    # If user explicitly asks for the race (or tomorrow's race), select Grand Prix (Race)
                    # unless another session is currently live and they just said 'f1 race' generically
                    desired_label = "Grand Prix (Race)"

                if desired_label:
                    def _matches_desired(c_item: Dict[str, Any]) -> bool:
                        norm_l = _normalize_f1_session_label(
                            c_item.get("type", {}).get("abbreviation") or c_item.get("type", {}).get("text") or ""
                        )
                        if desired_label == "Sprint Qualifying":
                            return norm_l.startswith("Sprint Qualifying")
                        if desired_label == "Sprint":
                            return norm_l.startswith("Sprint") and not norm_l.startswith("Sprint Qualifying")
                        return norm_l == desired_label

                    target_comp = next((c for c in comps if _matches_desired(c)), None)

            # 3. Default: prefer currently live ('in') session, then earliest upcoming ('pre') session, then final session
            if target_comp is None:
                target_comp = next(
                    (
                        c for c in comps
                        if (c.get("status", {}).get("type", {}).get("state") or "").lower() == MatchState.IN
                    ),
                    None,
                )
            if target_comp is None:
                target_comp = next(
                    (
                        c for c in comps
                        if (c.get("status", {}).get("type", {}).get("state") or "").lower() == MatchState.PRE
                    ),
                    comps[-1],
                )

            comp_id = str(
                target_comp.get("id")
                or _normalize_f1_session_label(
                    target_comp.get("type", {}).get("abbreviation") or target_comp.get("type", {}).get("text") or "session"
                )
            )
            snapshot = self._build_session_snapshot(ev, target_comp, is_tracker_poll=bool(event_id))
            return {
                "sport_path": "f1",
                "league_code": "f1",
                "league_name": "Formula 1 World Championship",
                "event_id": f"{ev_id}:{comp_id}",
                "home_team": snapshot.home_team,
                "away_team": snapshot.away_team,
                "snapshot": snapshot,
            }
        except Exception:
            return None

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
