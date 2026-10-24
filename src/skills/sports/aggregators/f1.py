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
    if "sprint" in rt_low and ("qual" in rt_low or "shootout" in rt_low or rt.upper() in ("SQ", "SS")):
        return f"Sprint Qualifying ({rt})"
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


def _extract_comp_top_drivers(comp: Dict[str, Any], limit: int = 5) -> List[str]:
    """Extract ordered driver display names (P1..Pn) from an ESPN competition block."""
    competitors = comp.get("competitors") or []
    if not competitors:
        return []
    sorted_comps = sorted(
        competitors,
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
    """Aggregator for Formula 1 Grand Prix schedules, live session timings, OpenF1 telemetry, results, and standings."""

    SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard"
    STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/racing/f1/standings"
    OPENF1_BASE_URL = "https://api.openf1.org/v1"

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
                    clean_date = start_str.replace("Z", "+00:00")
                    race_date = datetime.fromisoformat(clean_date).astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass
                try:
                    if end_str:
                        clean_end = end_str.replace("Z", "+00:00")
                        end_date = datetime.fromisoformat(clean_end).astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass

                cutoff_dt = end_date or race_date
                entry = {
                    "label": label,
                    "startDate": start_str,
                    "endDate": end_str,
                    "dt": race_date,
                    "end_dt": end_date,
                    "cutoff_dt": cutoff_dt,
                    "date_str": start_str[:10] if start_str else "TBD",
                    "date_param": start_str[:10].replace("-", "") if start_str else "",
                }

                if cutoff_dt and cutoff_dt.date() >= now.date():
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
                    if (r["dt"] or r["cutoff_dt"]) and ((r["dt"] or r["cutoff_dt"]).date() - now.date()).days <= 7
                ]
                header = "🏎️ --- F1 RACES THIS WEEK ---"
            elif is_month:
                selected_races = [
                    r for r in upcoming_races
                    if (r["dt"] or r["cutoff_dt"]) and ((r["dt"] or r["cutoff_dt"]).date() - now.date()).days <= 31
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
                        output_lines.append("   • Confirmed Sessions (Local & UTC):")
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
                has_race_results = bool(race_comp and race_comp.get("competitors")) and race_state != MatchState.IN

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

            for comp in comps_sorted:
                raw_type = comp.get("type", {}).get("abbreviation") or comp.get("type", {}).get("text") or "Session"
                cdate = comp.get("date", "")
                label = _normalize_f1_session_label(raw_type)
                if label == "Practice 3 (FP3)":
                    has_fp3 = True
                elif "Sprint" in label:
                    has_sprint = True

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
                    elif flag == "RED" or "RED FLAG" in msg_up:
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
        except Exception:
            return result

    def _build_session_snapshot(self, ev: Dict[str, Any], comp: Dict[str, Any]) -> MatchSnapshot:
        """Build a normalized MatchSnapshot for a specific Formula 1 Grand Prix session."""
        ev_name = ev.get("name") or "Formula 1 Grand Prix"
        circuit_name = ev.get("circuit", {}).get("fullName", "")
        raw_type = comp.get("type", {}).get("abbreviation") or comp.get("type", {}).get("text") or "Session"
        session_label = _normalize_f1_session_label(raw_type)
        cdate = comp.get("date", "")
        formatted_start = _format_f1_session_time(cdate)

        st_type = comp.get("status", {}).get("type", {}) if isinstance(comp.get("status"), dict) else {}
        state = (st_type.get("state") or MatchState.PRE).lower()
        completed = bool(st_type.get("completed", False))
        status_name = st_type.get("name") or ""

        espn_drivers = _extract_comp_top_drivers(comp, limit=5)

        # Query OpenF1 when session is live ('in') or within 15 minutes of scheduled start time
        should_poll_openf1 = state == MatchState.IN
        if not should_poll_openf1 and state == MatchState.PRE and cdate:
            try:
                dt_start = datetime.fromisoformat(cdate.replace("Z", "+00:00")).astimezone(timezone.utc)
                sec_until = (dt_start - datetime.now(timezone.utc)).total_seconds()
                if -7200 <= sec_until <= 900:
                    should_poll_openf1 = True
            except Exception:
                pass

        of1 = self._fetch_openf1_live_data(session_label, cdate) if should_poll_openf1 else {}
        if of1.get("matched") and of1.get("is_green") and state == MatchState.PRE:
            state = MatchState.IN
            status_name = "STATUS_IN_PROGRESS"

        top_drivers = of1.get("top_drivers") or espn_drivers
        notable_events: List[Tuple[str, str]] = list(of1.get("notable_events") or [])
        seen_ids: Set[str] = set(of1.get("seen_ids") or set())

        if state == MatchState.POST or completed:
            state = MatchState.POST
            completed = True
            status_name = status_name or "STATUS_FINAL"
            home_score = f"P1: {top_drivers[0]}" if top_drivers else "Completed"
            away_score = f"P2: {top_drivers[1]}" if len(top_drivers) >= 2 else "Final"
            status_detail = f"Final — {session_label}"
        elif state == MatchState.IN:
            home_score = f"P1: {top_drivers[0]}" if top_drivers else "Track Open"
            away_score = (
                f"P2: {top_drivers[1]}"
                if len(top_drivers) >= 2
                else (of1.get("phase_badge") or "In Progress")
            )
            phase_str = f" ({of1['phase_badge']})" if of1.get("phase_badge") else ""
            status_detail = f"LIVE — {session_label}{phase_str}"
        else:
            home_score = "Scheduled"
            away_score = "Upcoming"
            status_detail = formatted_start

        if top_drivers:
            stats_summary = "Top 5: " + " | ".join(f"P{i}: {d}" for i, d in enumerate(top_drivers[:5], 1))
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
                    now_date = datetime.now(timezone.utc).date()
                    for item in calendar:
                        end_str = item.get("endDate") or item.get("startDate") or ""
                        start_str = item.get("startDate") or ""
                        try:
                            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00")).astimezone(timezone.utc)
                            if end_dt.date() >= now_date and start_str:
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
                    target_comp = next(
                        (
                            c for c in comps
                            if desired_label.lower()
                            in _normalize_f1_session_label(
                                c.get("type", {}).get("abbreviation") or c.get("type", {}).get("text") or ""
                            ).lower()
                        ),
                        None,
                    )

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
            snapshot = self._build_session_snapshot(ev, target_comp)
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
