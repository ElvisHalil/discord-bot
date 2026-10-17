from datetime import datetime, timezone
import math
import re
from types import SimpleNamespace
from typing import Any, Optional
from zoneinfo import ZoneInfo

from FlightRadarAPI import FlightRadar24API

from src.utils.http import fetch_json
from src.utils.timezones import get_local_now


class LiveFlightStatusAggregator:
    """
    Aggregates real-time commercial flight status, gate/terminal assignments,
    scheduled vs. estimated departure/arrival times, and live airborne radar
    telemetry from Flightradar24 and ADSB.lol (100% keyless).
    """

    def __init__(self):
        self._fr_api: Optional[FlightRadar24API] = None

    @property
    def fr_api(self) -> FlightRadar24API:
        if self._fr_api is None:
            self._fr_api = FlightRadar24API()
        return self._fr_api

    @staticmethod
    def _normalize_flight_code(raw_code: str) -> str:
        return re.sub(r"[\s\-]+", "", (raw_code or "").strip()).upper()

    @staticmethod
    def _format_ts(
        ts: Optional[int],
        tz_name: Optional[str] = None,
        tz_abbr: Optional[str] = None,
        tz_offset: Optional[int] = None,
    ) -> str:
        if not ts or not isinstance(ts, (int, float)) or ts <= 0:
            return "TBD"
        try:
            from datetime import timedelta as _td
            utc_dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            if tz_offset is not None and isinstance(tz_offset, (int, float)):
                loc_dt = utc_dt.astimezone(timezone(_td(seconds=int(tz_offset))))
                abbr = tz_abbr or "Local"
                return f"{loc_dt.strftime('%a %b %d, %I:%M %p').replace(' 0', ' ')} {abbr}"
            if tz_name:
                try:
                    loc_dt = utc_dt.astimezone(ZoneInfo(tz_name))
                    abbr = tz_abbr or loc_dt.strftime("%Z")
                    return f"{loc_dt.strftime('%a %b %d, %I:%M %p').replace(' 0', ' ')} {abbr}"
                except Exception:
                    pass
            loc_dt = get_local_now(now=utc_dt)
            abbr = tz_abbr or loc_dt.strftime("%Z") or "Local"
            return f"{loc_dt.strftime('%a %b %d, %I:%M %p').replace(' 0', ' ')} {abbr}"
        except Exception:
            return "TBD"

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _query_adsb_lol(self, callsign: Optional[str] = None, registration: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Queries open ADSB.lol v2 endpoints by ICAO callsign or tail registration."""
        endpoints = []
        if callsign:
            cs = re.sub(r"[^A-Za-z0-9]", "", callsign).upper()
            if cs:
                endpoints.append(f"https://api.adsb.lol/v2/callsign/{cs}")
        if registration:
            reg = re.sub(r"[^A-Za-z0-9\-]", "", registration).upper()
            if reg:
                endpoints.append(f"https://api.adsb.lol/v2/reg/{reg}")

        for url in endpoints:
            try:
                data = fetch_json(url, timeout=6)
                ac_list = (data or {}).get("ac") or []
                if ac_list and isinstance(ac_list[0], dict):
                    return ac_list[0]
            except Exception:
                continue
        return None

    def fetch_flight_snapshot(self, flight_code: str) -> Optional[dict[str, Any]]:
        """
        Fetches a unified snapshot dictionary for a flight number, callsign, or tail number.
        Combines Flightradar24 live/schedule data with ADSB.lol transponder telemetry.
        """
        norm_code = self._normalize_flight_code(flight_code)
        if not norm_code:
            return None

        print(f"  └─ 📡 [Flight Status]: Looking up live status & telemetry for {norm_code}...")

        live_detail: Optional[dict[str, Any]] = None
        live_summary: Optional[dict[str, Any]] = None
        flight_id: Optional[str] = None

        # 1. Search Flightradar24 for active airborne match
        try:
            search_res = self.fr_api.search(norm_code, limit=8)
            live_matches = (search_res or {}).get("live") or []
            for item in live_matches:
                det = item.get("detail") or {}
                f_num = self._normalize_flight_code(det.get("flight") or "")
                c_sign = self._normalize_flight_code(det.get("callsign") or "")
                reg_no = self._normalize_flight_code(det.get("reg") or "")
                if norm_code in (f_num, c_sign, reg_no) or f_num.startswith(norm_code) or c_sign.startswith(norm_code):
                    live_summary = item
                    flight_id = str(item.get("id") or "")
                    break
            if not live_summary and live_matches:
                live_summary = live_matches[0]
                flight_id = str(live_summary.get("id") or "")

            if flight_id:
                live_detail = self.fr_api.get_flight_details(SimpleNamespace(id=flight_id))
        except Exception:
            live_detail = None

        # 2. If not currently airborne (or if details are incomplete), check FR24 flight schedule list
        schedule_item: Optional[dict[str, Any]] = None
        if not live_detail:
            try:
                list_url = (
                    f"https://api.flightradar24.com/common/v1/flight/list.json"
                    f"?query={norm_code}&fetchBy=flight&page=1&limit=8"
                )
                list_data = fetch_json(list_url, timeout=8)
                rows = ((list_data or {}).get("result") or {}).get("response", {}).get("data") or []
                now_ts = int(datetime.now(timezone.utc).timestamp())
                # Prefer a live flight first, otherwise the flight closest to current time (within -6h to +36h)
                best_row = None
                best_score = 10**12
                for row in rows:
                    st = (row.get("status") or {})
                    if st.get("live"):
                        best_row = row
                        break
                    t_sched = ((row.get("time") or {}).get("scheduled") or {})
                    dep_ts = t_sched.get("departure") or 0
                    arr_ts = t_sched.get("arrival") or 0
                    ref_ts = dep_ts or arr_ts
                    if ref_ts:
                        # Prefer upcoming/recent today flights over far-future scheduled rows
                        delta = abs(ref_ts - now_ts)
                        if delta < best_score:
                            best_score = delta
                            best_row = row
                schedule_item = best_row or (rows[0] if rows else None)
                if schedule_item and not flight_id:
                    cand_id = (schedule_item.get("identification") or {}).get("id")
                    if cand_id:
                        flight_id = str(cand_id)
                        try:
                            live_detail = self.fr_api.get_flight_details(SimpleNamespace(id=flight_id))
                        except Exception:
                            pass
            except Exception:
                schedule_item = None

        source_doc = live_detail or schedule_item
        det_hint = (live_summary or {}).get("detail") or {}

        # Extract core identification
        ident = (source_doc or {}).get("identification") or {}
        flight_number = (
            (ident.get("number") or {}).get("default")
            or det_hint.get("flight")
            or norm_code
        )
        callsign = ident.get("callsign") or det_hint.get("callsign") or norm_code

        aircraft_block = (source_doc or {}).get("aircraft") or {}
        aircraft_model = (
            (aircraft_block.get("model") or {}).get("text")
            or (aircraft_block.get("model") or {}).get("code")
            or det_hint.get("ac_type")
            or "Commercial Aircraft"
        )
        registration = aircraft_block.get("registration") or det_hint.get("reg") or ""
        icao_hex = aircraft_block.get("hex") or ""

        airline_block = (source_doc or {}).get("airline") or {}
        airline_name = (
            airline_block.get("name")
            or airline_block.get("short")
            or det_hint.get("operator")
            or ""
        )

        # Extract airport info
        airport_block = (source_doc or {}).get("airport") or {}
        orig = airport_block.get("origin") or {}
        dest = airport_block.get("destination") or {}

        orig_code = ((orig.get("code") or {}).get("iata") or det_hint.get("schd_from") or "TBD").upper()
        orig_name = orig.get("name") or orig_code
        orig_city = ((orig.get("position") or {}).get("region") or {}).get("city") or orig_code
        orig_tz = (orig.get("timezone") or {}).get("name")
        orig_tz_abbr = (orig.get("timezone") or {}).get("abbr")
        orig_tz_offset = (orig.get("timezone") or {}).get("offset")
        orig_info = orig.get("info") or {}
        orig_terminal = orig_info.get("terminal")
        orig_gate = orig_info.get("gate")

        dest_code = ((dest.get("code") or {}).get("iata") or det_hint.get("schd_to") or "TBD").upper()
        dest_name = dest.get("name") or dest_code
        dest_city = ((dest.get("position") or {}).get("region") or {}).get("city") or dest_code
        dest_tz = (dest.get("timezone") or {}).get("name")
        dest_tz_abbr = (dest.get("timezone") or {}).get("abbr")
        dest_tz_offset = (dest.get("timezone") or {}).get("offset")
        dest_info = dest.get("info") or {}
        dest_terminal = dest_info.get("terminal")
        dest_gate = dest_info.get("gate")
        dest_baggage = dest_info.get("baggage")

        # Extract timestamps & delay
        time_block = (source_doc or {}).get("time") or {}
        sched_block = time_block.get("scheduled") or {}
        real_block = time_block.get("real") or {}
        est_block = time_block.get("estimated") or {}
        other_block = time_block.get("other") or {}

        sched_dep_ts = sched_block.get("departure")
        est_dep_ts = real_block.get("departure") or est_block.get("departure")
        sched_arr_ts = sched_block.get("arrival")
        est_arr_ts = real_block.get("arrival") or est_block.get("arrival") or other_block.get("eta")

        delay_mins = 0
        if sched_arr_ts and est_arr_ts:
            delay_mins = int(round((est_arr_ts - sched_arr_ts) / 60.0))
        elif sched_dep_ts and est_dep_ts:
            delay_mins = int(round((est_dep_ts - sched_dep_ts) / 60.0))

        # Status classification
        status_block = (source_doc or {}).get("status") or {}
        is_live = bool(status_block.get("live") or live_summary)
        status_text = status_block.get("text") or ("En Route" if is_live else "Scheduled")
        generic_status = (((status_block.get("generic") or {}).get("status") or {}).get("text") or "").lower()

        st_low = f"{status_text} {generic_status}".lower()
        if "cancel" in st_low:
            state = "cancelled"
        elif "divert" in st_low:
            state = "diverted"
        elif "landed" in st_low or real_block.get("arrival"):
            state = "landed"
            is_live = False
        elif is_live or real_block.get("departure"):
            state = "airborne"
        else:
            state = "scheduled"

        # Trail / telemetry from FR24
        trail = (live_detail or {}).get("trail") or []
        latest_point = trail[0] if trail and isinstance(trail[0], dict) else {}
        lat = latest_point.get("lat") or det_hint.get("lat")
        lon = latest_point.get("lng") or det_hint.get("lon")
        altitude_ft = latest_point.get("alt")
        speed_kts = latest_point.get("spd")
        heading_deg = latest_point.get("hd")
        vertical_rate_fpm: Optional[int] = None
        squawk: Optional[str] = None

        # Stage 2: Enrich with ADSB.lol live transponder telemetry
        adsb_ac = self._query_adsb_lol(callsign=callsign, registration=registration)
        if adsb_ac:
            if state == "scheduled" and adsb_ac.get("alt_baro") not in (None, "ground"):
                state = "airborne"
                is_live = True
            if adsb_ac.get("alt_baro") not in (None, "ground"):
                altitude_ft = int(adsb_ac["alt_baro"])
            elif adsb_ac.get("alt_baro") == "ground" and altitude_ft is None:
                altitude_ft = 0
            if adsb_ac.get("gs") is not None:
                speed_kts = round(float(adsb_ac["gs"]))
            if adsb_ac.get("baro_rate") is not None:
                vertical_rate_fpm = int(adsb_ac["baro_rate"])
            if adsb_ac.get("track") is not None:
                heading_deg = round(float(adsb_ac["track"]))
            if adsb_ac.get("lat") is not None and adsb_ac.get("lon") is not None:
                lat = float(adsb_ac["lat"])
                lon = float(adsb_ac["lon"])
            if adsb_ac.get("squawk"):
                squawk = str(adsb_ac["squawk"])
            if adsb_ac.get("hex"):
                icao_hex = str(adsb_ac["hex"])
            if not registration and adsb_ac.get("r"):
                registration = str(adsb_ac["r"])

        if not source_doc and not live_summary and not adsb_ac:
            return None

        # Compute route progress / remaining distance if coordinates exist
        rem_km: Optional[int] = None
        pct_complete: Optional[int] = None
        try:
            orig_pos = orig.get("position") or {}
            dest_pos = dest.get("position") or {}
            if lat is not None and lon is not None and dest_pos.get("latitude") is not None and dest_pos.get("longitude") is not None:
                rem_d = self._haversine_km(float(lat), float(lon), float(dest_pos["latitude"]), float(dest_pos["longitude"]))
                rem_km = int(round(rem_d))
                if orig_pos.get("latitude") is not None and orig_pos.get("longitude") is not None:
                    tot_d = self._haversine_km(
                        float(orig_pos["latitude"]),
                        float(orig_pos["longitude"]),
                        float(dest_pos["latitude"]),
                        float(dest_pos["longitude"]),
                    )
                    if tot_d > 50:
                        pct_complete = max(0, min(100, int(round(100.0 * (1.0 - rem_d / tot_d)))))
        except Exception:
            pass

        fr24_url = (
            f"https://www.flightradar24.com/data/flights/{flight_number.lower()}"
            if not flight_id
            else f"https://www.flightradar24.com/{callsign}/{flight_id}"
        )
        adsb_url = f"https://globe.adsb.lol/?icao={icao_hex}" if icao_hex else None

        return {
            "flight_number": flight_number,
            "callsign": callsign,
            "flight_id": flight_id,
            "airline": airline_name,
            "aircraft_model": aircraft_model,
            "registration": registration,
            "icao_hex": icao_hex,
            "state": state,
            "is_live": is_live,
            "status_text": status_text,
            "origin_code": orig_code,
            "origin_name": orig_name,
            "origin_city": orig_city,
            "origin_tz": orig_tz,
            "origin_tz_abbr": orig_tz_abbr,
            "origin_terminal": orig_terminal,
            "origin_gate": orig_gate,
            "dest_code": dest_code,
            "dest_name": dest_name,
            "dest_city": dest_city,
            "dest_tz": dest_tz,
            "dest_tz_abbr": dest_tz_abbr,
            "dest_terminal": dest_terminal,
            "dest_gate": dest_gate,
            "dest_baggage": dest_baggage,
            "sched_dep_ts": sched_dep_ts,
            "est_dep_ts": est_dep_ts,
            "sched_arr_ts": sched_arr_ts,
            "est_arr_ts": est_arr_ts,
            "sched_dep_str": self._format_ts(sched_dep_ts, orig_tz, orig_tz_abbr, orig_tz_offset),
            "est_dep_str": self._format_ts(est_dep_ts, orig_tz, orig_tz_abbr, orig_tz_offset),
            "sched_arr_str": self._format_ts(sched_arr_ts, dest_tz, dest_tz_abbr, dest_tz_offset),
            "est_arr_str": self._format_ts(est_arr_ts, dest_tz, dest_tz_abbr, dest_tz_offset),
            "delay_mins": delay_mins,
            "altitude_ft": altitude_ft,
            "speed_kts": speed_kts,
            "vertical_rate_fpm": vertical_rate_fpm,
            "heading_deg": heading_deg,
            "squawk": squawk,
            "lat": lat,
            "lon": lon,
            "remaining_km": rem_km,
            "pct_complete": pct_complete,
            "fr24_url": fr24_url,
            "adsb_url": adsb_url,
        }

    def get_flight_status(self, flight_code: str) -> str:
        """Formats a detailed live flight status & radar telemetry block for the LLM."""
        snap = self.fetch_flight_snapshot(flight_code)
        if not snap:
            return (
                f"[LIVE FLIGHT STATUS]\n"
                f"Could not find active or scheduled commercial flight matching '{flight_code}'.\n"
                f"Verify the 2-letter airline code and flight number (e.g. AC850, WS102, UA123, BA117).\n"
            )

        state_badge = {
            "airborne": "🟢 EN ROUTE / AIRBORNE (LIVE RADAR)",
            "landed": "🏁 LANDED",
            "scheduled": "🕒 SCHEDULED",
            "cancelled": "🛑 CANCELLED",
            "diverted": "⚠️ DIVERTED",
        }.get(snap["state"], snap["status_text"].upper())

        delay_str = ""
        if snap["delay_mins"] >= 15:
            delay_str = f" (⚠️ Delayed +{snap['delay_mins']} min)"
        elif snap["delay_mins"] <= -10:
            delay_str = f" (⚡ Early {abs(snap['delay_mins'])} min)"
        elif snap["sched_arr_ts"]:
            delay_str = " (On Time)"

        airline_hdr = f"{snap['airline']} — " if snap["airline"] else ""
        reg_hdr = f" (Reg: {snap['registration']})" if snap["registration"] else ""

        orig_gate_parts = []
        if snap["origin_terminal"]:
            orig_gate_parts.append(f"Terminal {snap['origin_terminal']}")
        if snap["origin_gate"]:
            orig_gate_parts.append(f"Gate {snap['origin_gate']}")
        orig_gate_str = f" [{' | '.join(orig_gate_parts)}]" if orig_gate_parts else ""

        dest_gate_parts = []
        if snap["dest_terminal"]:
            dest_gate_parts.append(f"Terminal {snap['dest_terminal']}")
        if snap["dest_gate"]:
            dest_gate_parts.append(f"Gate {snap['dest_gate']}")
        if snap["dest_baggage"]:
            dest_gate_parts.append(f"Baggage {snap['dest_baggage']}")
        dest_gate_str = f" [{' | '.join(dest_gate_parts)}]" if dest_gate_parts else ""

        lines = [
            f"=== LIVE FLIGHT STATUS: {airline_hdr}{snap['flight_number']} ({snap['callsign']}) ===",
            f"• **Status**: {state_badge} — {snap['status_text']}{delay_str}",
            f"• **Route**: **{snap['origin_city']} ({snap['origin_code']})** -> **{snap['dest_city']} ({snap['dest_code']})**",
            f"• **Aircraft**: {snap['aircraft_model']}{reg_hdr}",
            f"• **Origin**: {snap['origin_name']} ({snap['origin_code']}){orig_gate_str}",
            f"  - Scheduled Departure: {snap['sched_dep_str']}",
            f"  - Actual/Est Departure: {snap['est_dep_str']}",
            f"• **Destination**: {snap['dest_name']} ({snap['dest_code']}){dest_gate_str}",
            f"  - Scheduled Arrival: {snap['sched_arr_str']}",
            f"  - Actual/Est Arrival: {snap['est_arr_str']}",
        ]

        # Include live airborne telemetry when available
        if snap["state"] == "airborne" or snap["altitude_ft"] is not None or snap["speed_kts"] is not None:
            telem_parts = []
            if snap["altitude_ft"] is not None:
                telem_parts.append(f"Altitude: **{snap['altitude_ft']:,} ft**")
            if snap["speed_kts"] is not None:
                kmh = int(round(snap["speed_kts"] * 1.852))
                mph = int(round(snap["speed_kts"] * 1.15078))
                telem_parts.append(f"Ground Speed: **{snap['speed_kts']} kts** ({mph} mph / {kmh} km/h)")
            if snap["vertical_rate_fpm"] is not None and snap["vertical_rate_fpm"] != 0:
                v_dir = "Climbing" if snap["vertical_rate_fpm"] > 0 else "Descending"
                telem_parts.append(f"Vertical Rate: {v_dir} ({snap['vertical_rate_fpm']:+d} ft/min)")
            if snap["heading_deg"] is not None:
                telem_parts.append(f"Heading: {snap['heading_deg']}°")
            if snap["squawk"]:
                telem_parts.append(f"Squawk: {snap['squawk']}")
            if telem_parts:
                lines.append(f"• **Live Radar Telemetry**: {' | '.join(telem_parts)}")

            prog_parts = []
            if snap["pct_complete"] is not None:
                prog_parts.append(f"**{snap['pct_complete']}%** of route completed")
            if snap["remaining_km"] is not None:
                rem_mi = int(round(snap["remaining_km"] * 0.621371))
                prog_parts.append(f"**{snap['remaining_km']:,} km** ({rem_mi:,} mi) remaining to {snap['dest_code']}")
            if snap["lat"] is not None and snap["lon"] is not None:
                prog_parts.append(f"Coordinates: ({snap['lat']:.2f}, {snap['lon']:.2f})")
            if prog_parts:
                lines.append(f"• **Flight Progress**: {' | '.join(prog_parts)}")

        links = [f"[Flightradar24 Live Map](<{snap['fr24_url']}>)"]
        if snap["adsb_url"]:
            links.append(f"[ADSB.lol Uncensored Radar](<{snap['adsb_url']}>)")
        lines.append(f"• **Live Tracking Links**: {' | '.join(links)}")

        return "\n".join(lines) + "\n"
