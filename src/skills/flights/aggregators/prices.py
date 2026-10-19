import calendar
from datetime import date as _date, datetime, timedelta
import json
import logging
import re
import time
from typing import Any, Optional

from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html, get_flights
from fast_flights.model import Airport, CarbonEmission, Flights, SimpleDatetime, SingleFlight
from fast_flights.parser import _parse_time
import primp
from selectolax.lexbor import LexborHTMLParser

from src.skills.flights.aggregators.base import BaseFlightAggregator
from src.skills.flights.engine.resolver import (
    parse_route_from_query,
    resolve_airport,
)
from src.skills.flights.catalogs import (
    NEARBY_HUB_AIRPORTS,
    ORIGIN_INTERNATIONAL_GATEWAYS,
)
from src.utils.cache import BoundedCache
from src.utils.timezones import get_local_now
from src.utils.workers import submit_to_worker

logger = logging.getLogger(__name__)


class GoogleFlightsPriceAggregator(BaseFlightAggregator):
    """
    Fetches real-time flight ticket prices, itineraries, layovers, and historical
    price insights ('Low' / 'Typical' / 'High') directly from Google Flights
    using Protobuf ('tfs') queries with zero API keys required.
    """

    def __init__(self):
        self._cache: BoundedCache = BoundedCache(maxsize=128)
        self._cache_ttl = 300.0  # 5-minute TTL for identical route/date queries
        self._horizon_cache: BoundedCache = BoundedCache(maxsize=64)
        self._horizon_ttl = 3600.0  # 1-hour TTL for route schedule horizon discovery

    @staticmethod
    def _format_hm(time_tuple: tuple[int, int]) -> str:
        h, m = time_tuple
        suffix = "AM" if h < 12 else "PM"
        h12 = h % 12
        if h12 == 0:
            h12 = 12
        return f"{h12}:{m:02d} {suffix}"

    @staticmethod
    def _format_duration(minutes: int) -> str:
        if not minutes or minutes <= 0:
            return "Duration N/A"
        h, m = divmod(int(minutes), 60)
        if h and m:
            return f"{h}h {m}m"
        if h:
            return f"{h}h"
        return f"{m}m"

    @staticmethod
    def _parse_flight_entry(entry: Any) -> Optional[Flights]:
        """Parses a single flight entry from either payload[2][0] (Best) or payload[3][0] (Other)."""
        try:
            flight = entry[0]
            price_block = entry[1]
            if not price_block or not price_block[0] or price_block[0][1] is None:
                return None
            price = int(price_block[0][1])
            typ = flight[0]
            airlines = list(flight[1] or [])
            sg_flights: list[SingleFlight] = []

            for single_flight in flight[2] or []:
                from_airport = Airport(code=single_flight[3], name=single_flight[4])
                to_airport = Airport(code=single_flight[6], name=single_flight[5])
                departure_time = _parse_time(single_flight[8])
                departure_date = tuple(single_flight[20]) if single_flight[20] else (0, 0, 0)
                departure = SimpleDatetime(date=departure_date, time=departure_time)

                arrival_time = _parse_time(single_flight[10])
                arrival_date = tuple(single_flight[21]) if single_flight[21] else (0, 0, 0)
                arrival = SimpleDatetime(date=arrival_date, time=arrival_time)

                plane_type = single_flight[17] or ""
                duration = int(single_flight[11] or 0)

                sg_flights.append(
                    SingleFlight(
                        from_airport=from_airport,
                        to_airport=to_airport,
                        departure=departure,
                        arrival=arrival,
                        duration=duration,
                        plane_type=plane_type,
                    )
                )

            extras = flight[22] if len(flight) > 22 and flight[22] else None
            carbon_emission = int(extras[7] or 0) if extras and len(extras) > 7 else 0
            typical_carbon = int(extras[8] or 0) if extras and len(extras) > 8 else 0

            return Flights(
                type=typ,
                price=price,
                airlines=airlines,
                flights=sg_flights,
                carbon=CarbonEmission(
                    typical_on_route=typical_carbon,
                    emission=carbon_emission,
                ),
            )
        except Exception:
            return None

    @classmethod
    def _parse_ds1_payload(cls, payload: Any) -> tuple[list[Flights], dict[str, Any]]:
        """
        Parses a Google Flights shopping payload array (from script.ds:1 or GetShoppingResults wrb.fr)
        into (list[Flights], price_insights_dict).
        """
        if not isinstance(payload, list):
            return [], {}
        all_flights: list[Flights] = []
        seen_keys: set[tuple] = set()

        for section_idx in (2, 3):
            if len(payload) > section_idx and payload[section_idx] and payload[section_idx][0]:
                for rank_idx, raw_entry in enumerate(payload[section_idx][0]):
                    parsed = cls._parse_flight_entry(raw_entry)
                    if not parsed or not parsed.flights:
                        continue
                    setattr(parsed, "_is_best_flight", section_idx == 2)
                    setattr(parsed, "_best_rank", rank_idx if section_idx == 2 else 50 + rank_idx)
                    first_leg = parsed.flights[0]
                    last_leg = parsed.flights[-1]
                    dedup_key = (
                        tuple(parsed.airlines),
                        first_leg.departure.time,
                        last_leg.arrival.time,
                        len(parsed.flights),
                        parsed.price,
                    )
                    if dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        all_flights.append(parsed)

        # Extract Google Price Insights from payload[5] if present
        insights: dict[str, Any] = {}
        try:
            p5 = payload[5] if len(payload) > 5 else None
            if isinstance(p5, list) and len(p5) >= 6:
                typical_low = p5[4][1] if isinstance(p5[4], list) and len(p5[4]) > 1 else None
                typical_high = p5[5][1] if isinstance(p5[5], list) and len(p5[5]) > 1 else None
                if typical_low and typical_high:
                    insights["typical_low"] = int(typical_low)
                    insights["typical_high"] = int(typical_high)
                if len(p5) > 10 and p5[10] and isinstance(p5[10][0], list):
                    hist_prices = [int(pt[1]) for pt in p5[10][0] if isinstance(pt, list) and len(pt) >= 2 and pt[1]]
                    if hist_prices:
                        insights["history_min_60d"] = min(hist_prices)
                        insights["history_max_60d"] = max(hist_prices)
                        insights["history_avg_60d"] = round(sum(hist_prices) / len(hist_prices))
        except Exception as e:
            logger.debug(f"Failed to parse Google Flights price insights: {e}")

        return all_flights, insights

    @classmethod
    def _parse_google_flights_html(cls, html: str) -> tuple[list[Flights], dict[str, Any]]:
        """
        Parses both payload[2][0] ('Best Flights') and payload[3][0] ('Other Flights')
        plus payload[5] ('Price Insights': typical price range & 60-day history)
        from Google Flights' embedded script.ds:1 JSON.
        Tags each Flights object with _is_best_flight and _best_rank so Google's
        curated 'Best Flights' ranking is preserved.
        """
        parser = LexborHTMLParser(html)
        script = parser.css_first(r"script.ds\:1")
        if not script:
            return [], {}
        js = script.text()
        if "data:" not in js:
            return [], {}
        data_str = js.split("data:", 1)[1].rsplit(",", 1)[0]
        if data_str.endswith("errorHasStatus: true"):
            return [], {}

        try:
            payload = json.loads(data_str)
        except Exception:
            return [], {}
        return cls._parse_ds1_payload(payload)

    @classmethod
    def _extract_rpc_context_from_html(cls, html: str, currency: str = "CAD") -> Optional[dict[str, Any]]:
        """
        Extracts WIZ session parameters ('FdrFJe', 'cfb2h', 'mnsUbf' BotGuard token),
        AF_dataServiceRequests['ds:1'].request, and the script.ds:1 continuation token
        needed to invoke Google Flights' GetShoppingResults and GetCalendarPicker RPCs.
        """
        if not html:
            return None
        m_req = re.search(r"'ds:1'\s*:\s*\{id:'[^']+',\s*request:(\[.*?)\}\s*(?:,\s*'ds:|\})", html, re.S)
        if not m_req:
            return None
        try:
            req_arr = json.loads(m_req.group(1))
        except Exception:
            return None
        if not isinstance(req_arr, list) or len(req_arr) < 2:
            return None

        m_sid = re.search(r'"FdrFJe":"([^"]+)"', html)
        m_bl = re.search(r'"cfb2h":"([^"]+)"', html)
        m_mns = re.search(r'"mnsUbf":(\[[^\]]+\])', html)

        f_sid = m_sid.group(1) if m_sid else ""
        bl = m_bl.group(1) if m_bl else "boq_travel-frontend-flights-ui_20260414.02_p0"
        bgr_token = "0"
        if m_mns:
            try:
                mns_arr = json.loads(m_mns.group(1))
                if isinstance(mns_arr, list) and len(mns_arr) > 7 and mns_arr[7] is not None:
                    bgr_token = str(mns_arr[7])
            except Exception as e:
                logger.debug(f"Failed to parse mnsUbf BotGuard token: {e}")

        token = None
        try:
            parser = LexborHTMLParser(html)
            script = parser.css_first(r"script.ds\:1")
            if script:
                js = script.text()
                if "data:" in js:
                    data_str = js.split("data:", 1)[1].rsplit(",", 1)[0]
                    ds1 = json.loads(data_str)
                    if isinstance(ds1, list) and ds1 and isinstance(ds1[0], list) and len(ds1[0]) > 4:
                        token = ds1[0][4]
        except Exception:
            token = None

        travel_settings = json.dumps(
            ["en-US", "CA", currency, 1, None, [360], None, None, 7, []],
            separators=(",", ":"),
        )
        bgr_header = json.dumps(
            [None, None, None, 10, 5, None, -1, 4, bgr_token],
            separators=(",", ":"),
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Same-Domain": "1",
            "x-goog-ext-259736195-jspb": travel_settings,
            "X-Goog-BatchExecute-Bgr": bgr_header,
            "Origin": "https://www.google.com",
            "Referer": "https://www.google.com/travel/flights",
        }
        params = {
            "f.sid": f_sid,
            "bl": bl,
            "hl": "en",
            "soc-app": "162",
            "soc-platform": "1",
            "soc-device": "1",
            "_reqid": "100000",
            "rt": "c",
        }
        return {
            "req_arr": req_arr,
            "token": token,
            "headers": headers,
            "params": params,
        }

    @classmethod
    def _post_google_flights_rpc(
        cls,
        service_method: str,
        params: dict[str, str],
        headers: dict[str, str],
        rpc_body: list[Any],
    ) -> str:
        """Executes a POST against FlightsFrontendService/<service_method> and returns the raw response text."""
        url = f"https://www.google.com/_/FlightsFrontendUi/data/travel.frontend.flights.FlightsFrontendService/{service_method}"
        payload_str = json.dumps([None, json.dumps(rpc_body, separators=(",", ":"))], separators=(",", ":"))
        client = primp.Client(impersonate="chrome_145", impersonate_os="windows")
        resp = client.post(url, params=params, headers=headers, data={"f.req": payload_str})
        if resp.status_code != 200:
            return ""
        return resp.text or ""

    @classmethod
    def _fetch_shopping_results_from_html(cls, html: str, currency: str = "CAD") -> tuple[list[Flights], dict[str, Any]]:
        """
        When Google Flights' initial SSR script.ds:1 returns a pending/cold cache state
        (common on >16-day round trips and >6-month future dates), invokes the live
        GetShoppingResults streaming RPC using the page's request descriptor and continuation token.
        """
        ctx = cls._extract_rpc_context_from_html(html, currency=currency)
        if not ctx:
            return [], {}
        token = ctx.get("token")
        first_arg = [None, None, None, token] if token else []
        shop_req = [first_arg, ctx["req_arr"][1], 0, 0, 0, 1]
        try:
            raw_text = cls._post_google_flights_rpc("GetShoppingResults", ctx["params"], ctx["headers"], shop_req)
        except Exception:
            return [], {}
        if not raw_text:
            return [], {}

        latest_flights: list[Flights] = []
        merged_insights: dict[str, Any] = {}
        for line in raw_text.splitlines():
            line_s = line.strip()
            if not line_s.startswith('[["wrb.fr"'):
                continue
            try:
                outer = json.loads(line_s)
            except Exception:
                continue
            for entry in outer:
                if not (isinstance(entry, list) and len(entry) > 2 and entry[0] == "wrb.fr" and entry[2]):
                    continue
                try:
                    inner = json.loads(entry[2])
                except Exception:
                    continue
                chunk_flights, chunk_insights = cls._parse_ds1_payload(inner)
                if chunk_insights:
                    merged_insights.update(chunk_insights)
                if chunk_flights:
                    # Keep the most complete/final streamed shopping chunk while preserving any earlier unique flights
                    seen = {
                        (
                            tuple(f.airlines),
                            f.flights[0].departure.time,
                            f.flights[-1].arrival.time,
                            len(f.flights),
                            f.price,
                        )
                        for f in chunk_flights
                        if f.flights
                    }
                    combined = list(chunk_flights)
                    for prev_f in latest_flights:
                        if not prev_f.flights:
                            continue
                        k = (
                            tuple(prev_f.airlines),
                            prev_f.flights[0].departure.time,
                            prev_f.flights[-1].arrival.time,
                            len(prev_f.flights),
                            prev_f.price,
                        )
                        if k not in seen:
                            seen.add(k)
                            combined.append(prev_f)
                    latest_flights = combined

        return latest_flights, merged_insights


    @classmethod
    def _summarize_itinerary(cls, f: Flights, currency: str) -> dict[str, Any]:
        legs = f.flights or []
        first_leg = legs[0]
        last_leg = legs[-1]
        num_stops = max(0, len(legs) - 1)
        stop_codes = [leg.to_airport.code for leg in legs[:-1] if leg.to_airport and leg.to_airport.code]
        stops_label = "Nonstop" if num_stops == 0 else (
            f"1 stop ({stop_codes[0]})" if num_stops == 1 and stop_codes else f"{num_stops} stops ({', '.join(stop_codes)})"
        )

        # Total elapsed duration = sum of leg air durations + same-airport layovers
        total_leg_mins = sum(int(leg.duration or 0) for leg in legs)
        layover_mins = 0
        max_layover_mins = 0
        if len(legs) > 1:
            try:
                for i in range(len(legs) - 1):
                    arr_dt = datetime(*legs[i].arrival.date, *legs[i].arrival.time)
                    dep_dt = datetime(*legs[i + 1].departure.date, *legs[i + 1].departure.time)
                    gap = int((dep_dt - arr_dt).total_seconds() // 60)
                    if 10 <= gap <= 1440:
                        layover_mins += gap
                        if gap > max_layover_mins:
                            max_layover_mins = gap
            except Exception:
                layover_mins = 0
        elapsed_mins = total_leg_mins + layover_mins

        dep_str = cls._format_hm(first_leg.departure.time)
        arr_str = cls._format_hm(last_leg.arrival.time)
        day_diff = 0
        try:
            day_diff = (_date(*last_leg.arrival.date) - _date(*first_leg.departure.date)).days
        except Exception as e:
            logger.debug(f"Itinerary day_diff calculation failed: {e}")
        if day_diff > 0:
            arr_str += f" (+{day_diff}d)"

        planes = []
        for leg in legs:
            if leg.plane_type and leg.plane_type not in planes:
                planes.append(leg.plane_type)

        airlines_str = ", ".join(f.airlines) if f.airlines else (f.type or "Airline")
        return {
            "price": int(f.price),
            "currency": currency,
            "airlines": airlines_str,
            "stops": num_stops,
            "stops_label": stops_label,
            "from_code": first_leg.from_airport.code,
            "to_code": last_leg.to_airport.code,
            "departure_time": dep_str,
            "arrival_time": arr_str,
            "duration_mins": elapsed_mins,
            "duration_str": cls._format_duration(elapsed_mins),
            "flight_mins": total_leg_mins,
            "max_layover_mins": max_layover_mins,
            "aircraft": ", ".join(planes[:2]) if planes else "",
            "is_best_flight": bool(getattr(f, "_is_best_flight", False)),
            "best_rank": int(getattr(f, "_best_rank", 99)),
        }

    @classmethod
    def _score_and_classify_itineraries(
        cls,
        summaries: list[dict[str, Any]],
        sort_preference: str = "best",
    ) -> dict[str, Any]:
        """
        Applies the Anti-Marathon Layover Filter (>22h AND >1.8x fastest, or >10h single layover)
        and computes a Smart Value Score balancing price, duration, stops, and Google's native
        'Best Flights' classification.
        """
        if not summaries:
            return {
                "best_overall": None,
                "lowest_reasonable": None,
                "lowest_nonstop": None,
                "fastest_overall": None,
                "raw_lowest": None,
                "filtered_marathon": None,
                "sorted_options": [],
            }

        valid_durs = [s["duration_mins"] for s in summaries if s.get("duration_mins", 0) > 0]
        fastest_mins = min(valid_durs) if valid_durs else 0
        min_stops = min(s["stops"] for s in summaries)

        for s in summaries:
            dur = s.get("duration_mins", 0)
            max_lay = s.get("max_layover_mins", 0)
            is_marathon = False
            if fastest_mins > 0 and dur > 0:
                if (dur > 1320 and dur > int(fastest_mins * 1.80)) or (dur > 1680 and fastest_mins <= 1320):
                    is_marathon = True
                elif max_lay > 600 and fastest_mins <= 1200:
                    is_marathon = True
                elif s["stops"] >= 3 and min_stops <= 1:
                    is_marathon = True
            s["is_marathon"] = is_marathon

            extra_mins = max(0, dur - fastest_mins) if fastest_mins > 0 and dur > 0 else 0
            best_bonus = -120 if (s.get("is_best_flight") and not is_marathon) else 0
            marathon_penalty = 2500 if is_marathon else 0
            s["value_score"] = round(s["price"] + (extra_mins * 1.35) + (s["stops"] * 55) + best_bonus + marathon_penalty)

        reasonable = [s for s in summaries if not s.get("is_marathon")]
        pool = reasonable if reasonable else summaries

        best_overall = min(pool, key=lambda x: (x["value_score"], x["price"], x["duration_mins"]))
        lowest_reasonable = min(pool, key=lambda x: (x["price"], x["duration_mins"], x["stops"]))
        raw_lowest = min(summaries, key=lambda x: (x["price"], x["duration_mins"], x["stops"]))
        nonstop_options = sorted([s for s in summaries if s["stops"] == 0], key=lambda x: (x["price"], x["duration_mins"]))
        lowest_nonstop = nonstop_options[0] if nonstop_options else None
        fastest_overall = min(
            summaries,
            key=lambda x: (x["duration_mins"] if x.get("duration_mins", 0) > 0 else 999999, x["price"]),
        )
        filtered_marathon = (
            raw_lowest
            if (raw_lowest.get("is_marathon") and raw_lowest["price"] < lowest_reasonable["price"])
            else None
        )

        sorted_opts = list(summaries)
        if sort_preference == "shortest":
            sorted_opts.sort(
                key=lambda x: (x["duration_mins"] if x.get("duration_mins", 0) > 0 else 999999, x["price"], x["stops"])
            )
        elif sort_preference == "cheapest":
            sorted_opts.sort(key=lambda x: (1 if x.get("is_marathon") else 0, x["price"], x["duration_mins"]))
        else:
            sorted_opts.sort(
                key=lambda x: (1 if x.get("is_marathon") else 0, x["value_score"], x["price"], x["duration_mins"])
            )

        return {
            "best_overall": best_overall,
            "lowest_reasonable": lowest_reasonable,
            "lowest_nonstop": lowest_nonstop,
            "fastest_overall": fastest_overall,
            "raw_lowest": raw_lowest,
            "filtered_marathon": filtered_marathon,
            "sorted_options": sorted_opts,
        }

    @staticmethod
    def build_google_flights_url(
        origin_code: str,
        dest_code: str,
        date: str,
        return_date: Optional[str] = None,
        cabin: str = "economy",
        currency: str = "CAD",
        max_stops: Optional[int] = None,
    ) -> str:
        trip_type = "round-trip" if return_date else "one-way"
        flight_queries = [FlightQuery(date=date, from_airport=origin_code, to_airport=dest_code, max_stops=max_stops)]
        if return_date:
            flight_queries.append(FlightQuery(date=return_date, from_airport=dest_code, to_airport=origin_code, max_stops=max_stops))
        seat_norm = cabin.lower().strip()
        if seat_norm not in ("economy", "premium-economy", "business", "first"):
            seat_norm = "economy"
        q_obj = create_query(
            flights=flight_queries,
            seat=seat_norm,  # type: ignore[arg-type]
            trip=trip_type,  # type: ignore[arg-type]
            passengers=Passengers(adults=1),
            currency=currency,
            language="en",
            max_stops=max_stops,
            hide_separate_and_self_transfer=False,
        )
        return q_obj.url()

    def fetch_calendar_prices(
        self,
        origin_code: str,
        dest_code: str,
        start_date: str,
        end_date: str,
        return_date: Optional[str] = None,
        return_start_date: Optional[str] = None,
        return_end_date: Optional[str] = None,
        trip_days: Optional[int] = None,
        cabin: str = "economy",
        currency: str = "CAD",
        max_stops: Optional[int] = None,
    ) -> list[tuple[int, str, Optional[str]]]:
        """
        Queries Google Flights' GetCalendarPicker RPC across a date range (or full month)
        and returns a price-sorted list of (price, departure_date, return_date) tuples.
        """
        seed_dep_date = start_date
        try:
            s_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            e_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
            cand_tue = s_dt + timedelta(days=(1 - s_dt.weekday()) % 7)
            if cand_tue <= e_dt:
                seed_dep_date = cand_tue.strftime("%Y-%m-%d")
        except Exception:
            seed_dep_date = start_date

        seed_ret_date = return_date
        if not seed_ret_date and trip_days:
            try:
                seed_ret_date = (datetime.strptime(seed_dep_date, "%Y-%m-%d").date() + timedelta(days=trip_days)).strftime("%Y-%m-%d")
            except Exception:
                seed_ret_date = None

        trip_type = "round-trip" if seed_ret_date else "one-way"
        flight_queries = [FlightQuery(date=seed_dep_date, from_airport=origin_code, to_airport=dest_code, max_stops=max_stops)]
        if seed_ret_date:
            flight_queries.append(FlightQuery(date=seed_ret_date, from_airport=dest_code, to_airport=origin_code, max_stops=max_stops))

        seat_norm = cabin.lower().strip()
        if seat_norm not in ("economy", "premium-economy", "business", "first"):
            seat_norm = "economy"

        q_obj = create_query(
            flights=flight_queries,
            seat=seat_norm,  # type: ignore[arg-type]
            trip=trip_type,  # type: ignore[arg-type]
            passengers=Passengers(adults=1),
            currency=currency,
            language="en",
            max_stops=max_stops,
            hide_separate_and_self_transfer=False,
        )

        try:
            html = fetch_flights_html(q_obj)
        except Exception:
            return []
        ctx = self._extract_rpc_context_from_html(html, currency=currency)
        if not ctx:
            return []

        def _parse_cal_rows(raw_text: str) -> list[tuple[int, str, Optional[str]]]:
            out: list[tuple[int, str, Optional[str]]] = []
            if not raw_text:
                return out
            for line in raw_text.splitlines():
                line_s = line.strip()
                if not line_s.startswith('[["wrb.fr"'):
                    continue
                try:
                    outer = json.loads(line_s)
                except Exception:
                    continue
                for entry in outer:
                    if not (isinstance(entry, list) and len(entry) > 2 and entry[0] == "wrb.fr" and entry[2]):
                        continue
                    try:
                        inner = json.loads(entry[2])
                    except Exception:
                        continue
                    if not (isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], list)):
                        continue
                    for row in inner[1]:
                        if not (isinstance(row, list) and len(row) >= 3 and row[0]):
                            continue
                        dep_d = str(row[0])
                        ret_d = str(row[1]) if row[1] else None
                        price_info = row[2]
                        if (
                            isinstance(price_info, list)
                            and price_info
                            and isinstance(price_info[0], list)
                            and len(price_info[0]) > 1
                            and price_info[0][1] is not None
                        ):
                            try:
                                p_val = int(price_info[0][1])
                                if p_val > 0:
                                    out.append((p_val, dep_d, ret_d))
                            except (ValueError, TypeError):
                                pass
            return out

        if trip_days:
            cal_req: list[Any] = [[], ctx["req_arr"][1], [start_date, end_date], None, [trip_days, trip_days]]
        elif seed_ret_date:
            cal_req = [[], ctx["req_arr"][1], [start_date, end_date], [seed_ret_date, seed_ret_date]]
        else:
            cal_req = [[], ctx["req_arr"][1], [start_date, end_date]]

        try:
            raw_cal = self._post_google_flights_rpc("GetCalendarPicker", ctx["params"], ctx["headers"], cal_req)
            rows = _parse_cal_rows(raw_cal)
            if not rows and not (seed_ret_date and return_start_date and return_end_date):
                raw_cal = self._post_google_flights_rpc("GetCalendarPicker", ctx["params"], ctx["headers"], cal_req)
                rows = _parse_cal_rows(raw_cal)
        except Exception as e:
            logger.debug(f"GetCalendarPicker RPC failed: {e}")
            rows = []

        # If a return month window was also specified (e.g. May 2027 <-> August 2027),
        # scan the return month anchored on the cheapest departure date discovered in the outbound month.
        if seed_ret_date and return_start_date and return_end_date:
            had_initial_rows = bool(rows)
            rows.sort(key=lambda x: (x[0], x[1], x[2] or ""))
            best_dep = rows[0][1] if rows else seed_dep_date
            cal_req_ret: list[Any] = [[], ctx["req_arr"][1], [best_dep, best_dep], [return_start_date, return_end_date]]
            try:
                raw_ret = self._post_google_flights_rpc("GetCalendarPicker", ctx["params"], ctx["headers"], cal_req_ret)
                ret_rows = _parse_cal_rows(raw_ret)
                seen_pairs = {(d, r) for _, d, r in rows}
                for p_val, dep_d, ret_d in ret_rows:
                    if (dep_d, ret_d) not in seen_pairs:
                        seen_pairs.add((dep_d, ret_d))
                        rows.append((p_val, dep_d, ret_d))
                # If the initial outbound-month calendar call was cold/empty, retry it now that the session is warm
                if not had_initial_rows:
                    ret_rows.sort(key=lambda x: (x[0], x[1], x[2] or ""))
                    best_ret = (ret_rows[0][2] if ret_rows and ret_rows[0][2] else seed_ret_date)
                    cal_req_warm: list[Any] = [[], ctx["req_arr"][1], [start_date, end_date], [best_ret, best_ret]]
                    raw_warm = self._post_google_flights_rpc("GetCalendarPicker", ctx["params"], ctx["headers"], cal_req_warm)
                    for p_val, dep_d, ret_d in _parse_cal_rows(raw_warm):
                        if (dep_d, ret_d) not in seen_pairs:
                            seen_pairs.add((dep_d, ret_d))
                            rows.append((p_val, dep_d, ret_d))
            except Exception as e:
                logger.debug(f"GetCalendarPicker return-window RPC failed: {e}")

        rows.sort(key=lambda x: (x[0], x[1], x[2] or ""))
        return rows

    def fetch_structured_fares(
        self,
        origin_code: str,
        dest_code: str,
        date: str,
        return_date: Optional[str] = None,
        cabin: str = "economy",
        currency: str = "CAD",
        max_stops: Optional[int] = None,
        sort_preference: str = "best",
    ) -> dict[str, Any]:
        """
        Queries Google Flights for the given route and dates and returns structured
        fare data (best overall, lowest reasonable, lowest nonstop, fastest overall,
        price insight rating, top options, booking URL).
        Automatically invokes Google Flights' GetShoppingResults RPC when initial SSR
        returns a pending/cold cache state.
        """
        cache_key = f"{origin_code}:{dest_code}:{date}:{return_date or ''}:{cabin}:{currency}:{max_stops}:{sort_preference}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < self._cache_ttl:
            return cached[1]

        trip_type = "round-trip" if return_date else "one-way"
        flight_queries = [FlightQuery(date=date, from_airport=origin_code, to_airport=dest_code, max_stops=max_stops)]
        if return_date:
            flight_queries.append(FlightQuery(date=return_date, from_airport=dest_code, to_airport=origin_code, max_stops=max_stops))

        seat_norm = cabin.lower().strip()
        if seat_norm not in ("economy", "premium-economy", "business", "first"):
            seat_norm = "economy"

        q_obj = create_query(
            flights=flight_queries,
            seat=seat_norm,  # type: ignore[arg-type]
            trip=trip_type,  # type: ignore[arg-type]
            passengers=Passengers(adults=1),
            currency=currency,
            language="en",
            max_stops=max_stops,
            hide_separate_and_self_transfer=False,
        )
        booking_url = q_obj.url()

        print(f"  └─ ✈️ [Google Flights]: Searching {origin_code} -> {dest_code} ({date}{f' to {return_date}' if return_date else ''}, {seat_norm}, {currency})...")

        flights_list: list[Flights] = []
        insights: dict[str, Any] = {}
        try:
            html = fetch_flights_html(q_obj)
            if html:
                flights_list, insights = self._parse_google_flights_html(html)
                if not flights_list:
                    flights_list, insights = self._fetch_shopping_results_from_html(html, currency=currency)
        except Exception:
            flights_list = []

        if not flights_list:
            try:
                res = get_flights(q_obj)
                flights_list = list(res or [])
            except Exception:
                flights_list = []

        summaries = [self._summarize_itinerary(f, currency) for f in flights_list if f and f.flights and f.price]
        scored = self._score_and_classify_itineraries(summaries, sort_preference=sort_preference)

        best_overall = scored["best_overall"]
        lowest_reasonable = scored["lowest_reasonable"]
        lowest_nonstop = scored["lowest_nonstop"]
        fastest_overall = scored["fastest_overall"]
        filtered_marathon = scored["filtered_marathon"]
        sorted_options = scored["sorted_options"]

        # lowest_overall points to lowest_reasonable so marathon 33h+ traps never win headline fares or alerts
        lowest_overall = lowest_reasonable

        # Compute price insight verdict ("LOW", "TYPICAL", "HIGH")
        verdict = None
        verdict_detail = None
        if lowest_overall and "typical_low" in insights and "typical_high" in insights:
            low_p = lowest_overall["price"]
            t_low = insights["typical_low"]
            t_high = insights["typical_high"]
            if low_p <= t_low:
                diff = t_low - low_p
                verdict = "🟢 LOW"
                verdict_detail = (
                    f"Prices are currently **LOW** (${diff} {currency} below the usual ${t_low}–${t_high} {currency} range)"
                    if diff > 0
                    else f"Prices are at the **bottom** of the usual ${t_low}–${t_high} {currency} range"
                )
            elif low_p <= t_high:
                verdict = "🟡 TYPICAL"
                verdict_detail = f"Prices are **TYPICAL** for this route (usual range: ${t_low}–${t_high} {currency})"
            else:
                diff = low_p - t_high
                verdict = "🔴 HIGH"
                verdict_detail = f"Prices are currently **HIGH** (${diff} {currency} above the usual ${t_low}–${t_high} {currency} range)"

        result = {
            "origin": origin_code,
            "destination": dest_code,
            "date": date,
            "return_date": return_date,
            "trip_type": trip_type,
            "cabin": seat_norm,
            "currency": currency,
            "booking_url": booking_url,
            "best_overall": best_overall,
            "lowest_overall": lowest_overall,
            "lowest_reasonable": lowest_reasonable,
            "lowest_nonstop": lowest_nonstop,
            "fastest_overall": fastest_overall,
            "filtered_marathon": filtered_marathon,
            "verdict": verdict,
            "verdict_detail": verdict_detail,
            "insights": insights,
            "options": sorted_options[:10],
        }
        if sorted_options:
            self._cache[cache_key] = (now, result)
        return result

    def _find_nearby_direct_flights(
        self,
        origin_code: str,
        dest_code: str,
        center_date: str,
        cabin: str = "economy",
        currency: str = "CAD",
    ) -> list[tuple[str, dict[str, Any]]]:
        """
        When a single-date query has no nonstop flight on center_date (e.g. a Monday when
        KLM only flies Tue/Thu/Fri/Sat/Sun), probes [-2, -1, +1, +2, +3] days in parallel
        to see if a direct flight operates on an adjacent day of the week.
        """
        try:
            base_dt = datetime.strptime(center_date, "%Y-%m-%d").date()
        except Exception:
            return []
        today = get_local_now().date()
        neighbor_dates = []
        for off in (-2, -1, 1, 2, 3):
            cand = base_dt + timedelta(days=off)
            if cand >= today:
                neighbor_dates.append(cand.strftime("%Y-%m-%d"))

        if not neighbor_dates:
            return []

        futs = {
            dt_s: submit_to_worker(
                self.fetch_structured_fares,
                origin_code,
                dest_code,
                dt_s,
                None,
                cabin,
                currency,
                None,
            )
            for dt_s in neighbor_dates
        }
        found: list[tuple[str, dict[str, Any]]] = []
        for dt_s in neighbor_dates:
            try:
                res = futs[dt_s].result()
                ns = res.get("lowest_nonstop")
                if ns:
                    found.append((dt_s, ns))
            except Exception as e:
                logger.debug(f"Nearby direct probe failed for {dt_s}: {e}")
        found.sort(key=lambda x: (x[1]["price"], x[0]))
        return found

    def find_latest_available_date(
        self,
        origin_code: str,
        dest_code: str,
        cabin: str = "economy",
        currency: str = "CAD",
        max_stops: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """
        Probes the route's published schedule horizon using two fast parallel batches
        to discover the farthest future date with live bookable itineraries on Google Flights.
        Ignores isolated 1-itinerary 2-stop outliers beyond the 6-month married-segment cutoff
        when the route normally has multi-option / 1-stop schedules.
        """
        h_key = f"{origin_code}:{dest_code}:{cabin}:{currency}:{max_stops}"
        now_ts = time.time()
        cached = self._horizon_cache.get(h_key)
        if cached and (now_ts - cached[0]) < self._horizon_ttl:
            return cached[1]

        today = get_local_now().date()
        # Round 1: Parallel coarse anchors across the 11-month schedule window
        coarse_offsets = [90, 150, 180, 210, 270, 325]
        coarse_dates = {off: (today + timedelta(days=off)).strftime("%Y-%m-%d") for off in coarse_offsets}
        futs = {
            off: submit_to_worker(
                self.fetch_structured_fares,
                origin_code,
                dest_code,
                dt_s,
                None,
                cabin,
                currency,
                max_stops,
            )
            for off, dt_s in coarse_dates.items()
        }
        coarse_results = {off: fut.result() for off, fut in futs.items()}

        baseline_count = max((len(coarse_results[off]["options"]) for off in coarse_offsets), default=0)
        baseline_has_1stop = any(
            any(o.get("stops", 99) <= 1 for o in coarse_results[off]["options"])
            for off in coarse_offsets
        )

        def _is_valid_horizon_day(res: dict[str, Any]) -> bool:
            opts = res.get("options") or []
            if not opts:
                return False
            # Reject 2-stop-only days beyond the 6-month married-segment horizon when the route has <=1-stop service
            if baseline_has_1stop and not any(o.get("stops", 99) <= 1 for o in opts):
                return False
            return True

        valid_offsets = [off for off in coarse_offsets if _is_valid_horizon_day(coarse_results[off])]
        if not valid_offsets:
            valid_offsets = [off for off in coarse_offsets if coarse_results[off]["options"]]
        if not valid_offsets:
            self._horizon_cache[h_key] = (now_ts, None)
            return None

        best_off = max(valid_offsets)
        best_data = coarse_results[best_off]

        # If the highest coarse anchor (325d) already has flights, return it directly
        higher_empty = [off for off in coarse_offsets if off > best_off and not _is_valid_horizon_day(coarse_results[off])]
        if higher_empty:
            upper_bound = min(higher_empty)
            # Round 2: Parallel fine probe inside (best_off, upper_bound)
            step = max(1, (upper_bound - best_off) // 5)
            fine_offsets = [off for off in range(best_off + step, upper_bound, step)]
            if fine_offsets:
                fine_futs = {
                    off: submit_to_worker(
                        self.fetch_structured_fares,
                        origin_code,
                        dest_code,
                        (today + timedelta(days=off)).strftime("%Y-%m-%d"),
                        None,
                        cabin,
                        currency,
                        max_stops,
                    )
                    for off in fine_offsets
                }
                for off, fut in fine_futs.items():
                    res = fut.result()
                    if _is_valid_horizon_day(res) and off > best_off:
                        best_off = off
                        best_data = res

            # Round 3: Pinpoint exact day within the remaining ~5-day gap
            next_empty = min([off for off in (coarse_offsets + fine_offsets) if off > best_off], default=best_off)
            if 1 < (next_empty - best_off) <= 6:
                day_futs = {
                    off: submit_to_worker(
                        self.fetch_structured_fares,
                        origin_code,
                        dest_code,
                        (today + timedelta(days=off)).strftime("%Y-%m-%d"),
                        None,
                        cabin,
                        currency,
                        max_stops,
                    )
                    for off in range(best_off + 1, next_empty)
                }
                for off, fut in day_futs.items():
                    res = fut.result()
                    if _is_valid_horizon_day(res) and off > best_off:
                        best_off = off
                        best_data = res

        self._horizon_cache[h_key] = (now_ts, best_data)
        return best_data

    def _probe_gateway_one_stop_schedule(
        self,
        orig_info: tuple[str, str, str, str],
        dest_info: tuple[str, str, str, str],
        sample_date: str,
        cabin: str = "economy",
        currency: str = "CAD",
        return_date: Optional[str] = None,
    ) -> list[str]:
        """
        When a date or window is >6 months out (beyond Google Flights' married-segment horizon for
        1-stop interline/codeshare connections), probes the origin's
        known international nonstop gateways (e.g. London LHR, Frankfurt FRA) to verify
        whether the individual nonstop legs (Outbound: Origin -> Hub -> Dest, and Return:
        Dest -> Hub -> Origin) are already published and bookable in that future window.
        """
        orig_code, orig_name, orig_city, _ = orig_info
        dest_code, dest_name, dest_city, _ = dest_info
        gateways = [g for g in ORIGIN_INTERNATIONAL_GATEWAYS.get(orig_code, []) if g[0] != dest_code]
        if not gateways:
            return []

        try:
            base_dt = datetime.strptime(sample_date, "%Y-%m-%d").date()
        except Exception:
            base_dt = (get_local_now() + timedelta(days=30)).date()

        # Align with Tuesday (weekday == 1) inside the target window so 5x-weekly transatlantic
        # nonstop routes (e.g. operating on Tue/Thu/Fri/Sat/Sun) are reliably hit
        days_to_tue = (1 - base_dt.weekday()) % 7
        dep_dt = base_dt + timedelta(days=days_to_tue)
        dep_date_s = dep_dt.strftime("%Y-%m-%d")
        next_date_s = (dep_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        sample_rt_end_s = (dep_dt + timedelta(days=14)).strftime("%Y-%m-%d")

        ret_date_s: Optional[str] = None
        if return_date:
            try:
                r_base = datetime.strptime(return_date, "%Y-%m-%d").date()
                r_tue = (1 - r_base.weekday()) % 7
                ret_date_s = (r_base + timedelta(days=r_tue)).strftime("%Y-%m-%d")
            except Exception:
                ret_date_s = return_date

        probe_futs: dict[tuple[str, str], Any] = {}
        for hub_code, _hub_name, _hub_city in gateways[:2]:
            probe_futs[(hub_code, "leg1")] = submit_to_worker(
                self.fetch_structured_fares,
                orig_code,
                hub_code,
                dep_date_s,
                None,
                cabin,
                currency,
                None,
            )
            probe_futs[(hub_code, "leg1_rt")] = submit_to_worker(
                self.fetch_structured_fares,
                orig_code,
                hub_code,
                dep_date_s,
                sample_rt_end_s,
                cabin,
                currency,
                0,
            )
            probe_futs[(hub_code, "leg2_next")] = submit_to_worker(
                self.fetch_structured_fares,
                hub_code,
                dest_code,
                next_date_s,
                None,
                cabin,
                currency,
                None,
            )
            probe_futs[(hub_code, "leg2_same")] = submit_to_worker(
                self.fetch_structured_fares,
                hub_code,
                dest_code,
                dep_date_s,
                None,
                cabin,
                currency,
                None,
            )
            if ret_date_s:
                probe_futs[(hub_code, "ret_leg2")] = submit_to_worker(
                    self.fetch_structured_fares,
                    hub_code,
                    orig_code,
                    ret_date_s,
                    None,
                    cabin,
                    currency,
                    None,
                )
                probe_futs[(hub_code, "ret_leg1")] = submit_to_worker(
                    self.fetch_structured_fares,
                    dest_code,
                    hub_code,
                    ret_date_s,
                    None,
                    cabin,
                    currency,
                    None,
                )

        out_lines: list[str] = []
        for hub_code, hub_name, hub_city in gateways[:2]:
            try:
                leg1_res = probe_futs[(hub_code, "leg1")].result()
                leg1_rt_res = probe_futs[(hub_code, "leg1_rt")].result()
                leg2_next_res = probe_futs[(hub_code, "leg2_next")].result()
                leg2_same_res = probe_futs[(hub_code, "leg2_same")].result()
            except Exception:
                continue

            ns1 = leg1_res.get("lowest_nonstop")
            if not ns1:
                continue
            rt_ns1 = leg1_rt_res.get("lowest_nonstop") if leg1_rt_res else None

            # Overnight flights (+1d) connect on next_date_s; daytime flights connect on dep_date_s
            is_overnight = "(+" in ns1.get("arrival_time", "")
            primary_leg2 = leg2_next_res if is_overnight else leg2_same_res
            secondary_leg2 = leg2_same_res if is_overnight else leg2_next_res
            leg2_res = primary_leg2 if primary_leg2.get("options") else secondary_leg2
            leg2_date_s = next_date_s if leg2_res is leg2_next_res else dep_date_s

            ns2_options = [o for o in (leg2_res.get("options") or []) if o.get("stops") == 0]
            if not ns2_options:
                continue

            def _parse_clock_mins(t_str: str) -> int:
                m_clk = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", t_str or "", re.IGNORECASE)
                if not m_clk:
                    return 720
                hh, mm, ap = int(m_clk.group(1)), int(m_clk.group(2)), m_clk.group(3).upper()
                if ap == "PM" and hh != 12:
                    hh += 12
                elif ap == "AM" and hh == 12:
                    hh = 0
                return hh * 60 + mm

            arr1_mins = _parse_clock_mins(ns1.get("arrival_time", ""))
            ns2_options.sort(
                key=lambda o: (
                    0 if _parse_clock_mins(o.get("departure_time", "")) >= arr1_mins + 45 else 1,
                    _parse_clock_mins(o.get("departure_time", "")) - arr1_mins
                    if _parse_clock_mins(o.get("departure_time", "")) >= arr1_mins + 45
                    else _parse_clock_mins(o.get("departure_time", "")),
                )
            )

            out_lines.append(
                f"\n=== ✈️ CONFIRMED 1-STOP GATEWAY SCHEDULE VIA {hub_city.upper()} ({hub_code}): {orig_code} -> {hub_code} -> {dest_code} ==="
            )
            out_lines.append(
                f"• **Why Combined Search Missed This (>6-Month Married-Segment Rule)**: Google Flights only merges "
                f"multi-segment/interline 1-stop fares on **{orig_code} <-> {dest_code}** up to **~6 months (182 days)** before travel. "
                f"However, **both individual nonstop legs via {hub_city} ({hub_code}) ARE already published and operating** in your requested window!"
            )

            # List concrete operating date pairs in the requested window so the LLM always returns specific travel dates
            sample_pairs: list[str] = []
            for d_off in (0, 2, 7):
                cand_dep = dep_dt + timedelta(days=d_off)
                cand_dep_s = cand_dep.strftime("%a, %Y-%m-%d")
                if ret_date_s:
                    try:
                        r_dt_base = datetime.strptime(ret_date_s, "%Y-%m-%d").date()
                        cand_ret = r_dt_base + timedelta(days=d_off)
                        sample_pairs.append(f"**{cand_dep_s} ↔ Return {cand_ret.strftime('%a, %Y-%m-%d')}**")
                    except Exception:
                        sample_pairs.append(f"**{cand_dep_s} ↔ Return {ret_date_s}**")
                else:
                    sample_pairs.append(f"**{cand_dep_s}**")
            out_lines.append(
                f"• **📅 Concrete Recommended 1-Stop Travel Dates in Your Window**: {', '.join(sample_pairs)}"
            )

            if ret_date_s:
                rt_fare_note = (
                    f", Round-Trip {orig_code}<->{hub_code} nonstop base fare **${rt_ns1['price']} {currency}**"
                    if rt_ns1
                    else ""
                )
            else:
                rt_fare_note = (
                    f", Round-Trip {orig_code}<->{hub_code} nonstop base fare **${rt_ns1['price']} {currency}** | standalone one-way **${ns1['price']} {currency}**"
                    if rt_ns1
                    else f", standalone leg price **${ns1['price']} {currency}**"
                )
            out_lines.append(
                f"• **🛫 Outbound Leg 1 (Nonstop {orig_city} {orig_code} -> {hub_city} {hub_code} on {dep_date_s})**: "
                f"**{ns1['airlines']}** Nonstop ({ns1['departure_time']} -> {ns1['arrival_time']}, **{ns1['duration_str']}**{rt_fare_note})"
            )
            out_lines.append(
                f"• **🛫 Outbound Leg 2 (Nonstop {hub_city} {hub_code} -> {dest_city} {dest_code} on {leg2_date_s})**:"
            )
            for idx, ns2 in enumerate(ns2_options[:3], start=1):
                leg2_price_suffix = "" if ret_date_s else f", standalone short-haul leg **${ns2['price']} {currency}**"
                out_lines.append(
                    f"  {idx}. **{ns2['airlines']}** Nonstop ({ns2['departure_time']} -> {ns2['arrival_time']}, **{ns2['duration_str']}**{leg2_price_suffix})"
                )

            if ret_date_s:
                try:
                    ret_leg2_res = probe_futs[(hub_code, "ret_leg2")].result()
                    ret_leg1_res = probe_futs[(hub_code, "ret_leg1")].result()
                except Exception:
                    ret_leg2_res, ret_leg1_res = {}, {}

                ret_ns2 = ret_leg2_res.get("lowest_nonstop") if ret_leg2_res else None
                ret_ns1_opts = [o for o in (ret_leg1_res.get("options") or []) if o.get("stops") == 0] if ret_leg1_res else []

                out_lines.append(
                    f"• **🛬 Return 1-Stop Schedule ({dest_city} {dest_code} -> {hub_city} {hub_code} -> {orig_city} {orig_code} around {ret_date_s})**:"
                )
                if ret_ns1_opts:
                    # Prefer a morning/midday short-haul leg (AM or early PM) so it connects before the transatlantic departure
                    am_opts = [o for o in ret_ns1_opts if "AM" in o.get("arrival_time", "") or o.get("arrival_time", "").startswith(("12:", "1:"))]
                    r1 = am_opts[0] if am_opts else ret_ns1_opts[0]
                    out_lines.append(
                        f"  - **Return Segment 1 (Nonstop {dest_city} {dest_code} -> {hub_city} {hub_code} on {ret_date_s})**: "
                        f"**{r1['airlines']}** Nonstop ({r1['departure_time']} -> {r1['arrival_time']}, **{r1['duration_str']}**)"
                    )
                else:
                    carrier_names = " / ".join(sorted({o["airlines"] for o in ns2_options[:2]}))
                    out_lines.append(
                        f"  - **Return Segment 1 (Nonstop {dest_city} {dest_code} -> {hub_city} {hub_code}, ~2h 05m)**: "
                        f"Operated daily by **{carrier_names}** (European short-haul schedules for {ret_date_s[:7]} release ~6–8 months before departure)."
                    )
                if ret_ns2:
                    out_lines.append(
                        f"  - **Return Segment 2 (Nonstop {hub_city} {hub_code} -> {orig_city} {orig_code} on {ret_date_s})**: "
                        f"**{ret_ns2['airlines']}** Nonstop ({ret_ns2['departure_time']} -> {ret_ns2['arrival_time']}, **{ret_ns2['duration_str']}**) — "
                        f"**Confirmed published & operating in {ret_date_s[:7]}!**"
                    )

            partner_pair = (
                f"{ns1['airlines']} & {ns2_options[0]['airlines']}"
                if ns1["airlines"] != ns2_options[0]["airlines"]
                else f"{ns1['airlines']} & partner airlines"
            )
            out_lines.append(
                f"• 💡 **Booking Tip**: Do **not** buy the two legs as separate one-way tickets (transatlantic one-way fares are heavily surcharged). "
                f"Once your return date enters the **6-month window**, **{partner_pair}** automatically merge "
                f"**{orig_code} <-> {hub_code} <-> {dest_code}** into a single 1-stop ticket (see the **6-Month Published Benchmark Fare** below)."
            )
        return out_lines

    @staticmethod
    def _format_fare_block(
        data: dict[str, Any],
        eff_currency: str,
        max_items: int = 8,
        nearby_direct: Optional[list[tuple[str, dict[str, Any]]]] = None,
        include_booking_link: bool = True,
    ) -> list[str]:
        lines: list[str] = []
        best = data.get("best_overall") or data.get("lowest_overall")
        low = data.get("lowest_reasonable") or data.get("lowest_overall")
        low_ns = data.get("lowest_nonstop")
        fastest = data.get("fastest_overall")
        filtered_marathon = data.get("filtered_marathon")
        orig_code = data["origin"]
        dest_code = data["destination"]
        dep_date = data["date"]
        ret_date = data.get("return_date")
        trip_tag = f"Round-Trip ({dep_date} to {ret_date})" if ret_date else f"{dep_date}"
        price_suffix = f"{eff_currency} Round-Trip" if ret_date else eff_currency

        if best:
            lines.append(
                f"• 🏆 **Best Recommended Flight (Optimal Price + Duration)**: **${best['price']} {price_suffix}** via **{best['airlines']}** "
                f"({best['stops_label']}, **{best['duration_str']}**, {best['departure_time']} -> {best['arrival_time']} on **{trip_tag}**)"
            )
        if low and low != best:
            lines.append(
                f"• 💰 **Cheapest Reasonable Fare**: **${low['price']} {price_suffix}** via **{low['airlines']}** "
                f"({low['stops_label']}, **{low['duration_str']}**, {low['departure_time']} -> {low['arrival_time']} on **{trip_tag}**)"
            )
        elif low:
            lines.append(
                f"• 💰 **Cheapest Overall Fare**: **${low['price']} {price_suffix}** via **{low['airlines']}** "
                f"({low['stops_label']}, **{low['duration_str']}**, {low['departure_time']} -> {low['arrival_time']} on **{trip_tag}**)"
            )

        if low_ns:
            if low_ns == best or low_ns == low:
                lines.append(
                    f"• ⚡ **Direct / Nonstop Highlight**: Top recommended fare (**${low_ns['price']} {price_suffix}** via **{low_ns['airlines']}**, **{low_ns['duration_str']}**) is a **Nonstop** flight!"
                )
            else:
                lines.append(
                    f"• ⚡ **Cheapest Direct / Nonstop Flight**: **${low_ns['price']} {price_suffix}** via **{low_ns['airlines']}** "
                    f"(Nonstop, **{low_ns['duration_str']}**, {low_ns['departure_time']} -> {low_ns['arrival_time']} on **{trip_tag}**)"
                )
        elif fastest and fastest != best and fastest != low:
            lines.append(
                f"• ⚡ **Shortest / Fastest Flight**: **${fastest['price']} {price_suffix}** via **{fastest['airlines']}** "
                f"({fastest['stops_label']}, **{fastest['duration_str']}**, {fastest['departure_time']} -> {fastest['arrival_time']} on **{trip_tag}**)"
            )

        if not low_ns and nearby_direct:
            nd_parts = []
            for nd_date, nd_opt in nearby_direct[:3]:
                try:
                    wd_name = datetime.strptime(nd_date, "%Y-%m-%d").strftime("%a %b %d")
                except Exception:
                    wd_name = nd_date
                nd_parts.append(
                    f"**{wd_name} ({nd_date})** for **${nd_opt['price']} {eff_currency}** ({nd_opt['airlines']}, {nd_opt['duration_str']})"
                )
            lines.append(
                f"• 💡 **Direct Flight Schedule Discovery**: No nonstop flight operates on **{dep_date}**, "
                f"but **Nonstop Direct Flights** operate on adjacent days: {', '.join(nd_parts)}!"
            )

        if filtered_marathon:
            lines.append(
                f"• 🛡️ **Long-Layover Filter**: Excluded a raw cheaper **${filtered_marathon['price']} {eff_currency}** option "
                f"({filtered_marathon['airlines']}, {filtered_marathon['stops_label']}) because total travel time is **{filtered_marathon['duration_str']}**."
            )

        if data.get("verdict_detail"):
            lines.append(f"• **Google Price Insight**: {data['verdict']} — {data['verdict_detail']}")
        insights = data.get("insights") or {}
        if "history_min_60d" in insights and "history_max_60d" in insights:
            lines.append(
                f"• **60-Day Historical Trend**: Low ${insights['history_min_60d']} {eff_currency} | "
                f"Avg ${insights['history_avg_60d']} {eff_currency} | High ${insights['history_max_60d']} {eff_currency}"
            )

        if include_booking_link:
            lines.append(f"• **Direct Booking Link**: [Google Flights: {orig_code} to {dest_code} ({trip_tag})](<{data['booking_url']}>)")
        lines.append("\n--- TOP RECOMMENDED ITINERARIES (Ranked by Best Value, Duration & Price) ---")

        for idx, opt in enumerate(data["options"][:max_items], start=1):
            ac_suffix = f" | Aircraft: {opt['aircraft']}" if opt.get("aircraft") else ""
            badges = []
            if opt == best and not opt.get("is_marathon"):
                badges.append("🏆 Best Value")
            if opt.get("stops") == 0:
                badges.append("⚡ Nonstop")
            elif opt == fastest and fastest != best:
                badges.append("⚡ Shortest Time")
            if opt == low and low != best and not opt.get("is_marathon"):
                badges.append("💰 Cheapest Reasonable")
            if opt.get("is_marathon"):
                badges.append(f"⚠️ Long Layover ({opt['duration_str']})")
            badge_str = f" ({', '.join(badges)})" if badges else ""

            lines.append(
                f"{idx}. **${opt['price']} {price_suffix}** — **{opt['airlines']}** [{opt['stops_label']}]{badge_str}\n"
                f"   📅 {trip_tag} | 🕒 {opt['departure_time']} ({opt['from_code']}) -> {opt['arrival_time']} ({opt['to_code']}) | "
                f"⏱️ {opt['duration_str']}{ac_suffix}"
            )
        return lines

    def scan_flexible_dates_and_direct_schedule(
        self,
        orig_info: tuple[str, str, str, str],
        dest_info: tuple[str, str, str, str],
        start_date: str,
        end_date: str,
        window_label: str,
        cabin: str = "economy",
        currency: str = "CAD",
        sort_preference: str = "best",
        trip_days: Optional[int] = None,
        max_stops: Optional[int] = None,
        return_date: Optional[str] = None,
        return_window_label: Optional[str] = None,
    ) -> str:
        """
        Sweeps a 14-to-21-day window in parallel (~1.2-2.0s) to answer:
          1. 'When are the direct / nonstop (or 1-stop) flights on this route and which days of the week do they fly?'
          2. 'When is the best / cheapest / shortest day to fly without 30+ hour marathon layover traps?'
          3. When the requested window is >6 months out (sparse or missing 1-stop married segments),
             automatically surfaces both the 6-month full schedule horizon (including Round-Trip benchmark fares)
             AND confirmed 1-stop gateway hub connections for both outbound and return legs.
        """
        orig_code, orig_name, orig_city, _ = orig_info
        dest_code, dest_name, dest_city, _ = dest_info

        try:
            s_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            e_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        except Exception:
            s_dt = (get_local_now() + timedelta(days=7)).date()
            e_dt = s_dt + timedelta(days=13)

        num_days = max(1, min(21, (e_dt - s_dt).days + 1))
        date_list = [(s_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(num_days)]

        # Representative Tuesday inside the outbound window for direct booking URL
        days_to_tue = (1 - s_dt.weekday()) % 7
        rep_dep_date = (s_dt + timedelta(days=days_to_tue)).strftime("%Y-%m-%d")
        requested_booking_url = self.build_google_flights_url(
            origin_code=orig_code,
            dest_code=dest_code,
            date=rep_dep_date,
            return_date=return_date,
            cabin=cabin,
            currency=currency,
            max_stops=max_stops,
        )

        print(
            f"  └─ 📅 [Google Flights Calendar Sweep]: Scanning {len(date_list)} dates ({date_list[0]} to {date_list[-1]}) "
            f"for {orig_code} -> {dest_code} (return={return_date or 'one-way'}, max_stops={max_stops})..."
        )

        scan_pairs: list[tuple[str, Optional[str]]] = [
            (
                dt_s,
                (datetime.strptime(dt_s, "%Y-%m-%d").date() + timedelta(days=trip_days)).strftime("%Y-%m-%d")
                if trip_days
                else return_date,
            )
            for dt_s in date_list
        ]

        # Use GetCalendarPicker to discover the lowest-priced date pairs across the entire month/window first.
        cal_rows: list[tuple[int, str, Optional[str]]] = []
        if len(date_list) >= 10:
            cal_end_date = end_date
            if s_dt.day == 1 and e_dt.day >= 20:
                last_m_day = calendar.monthrange(s_dt.year, s_dt.month)[1]
                cal_end_date = f"{s_dt.year:04d}-{s_dt.month:02d}-{last_m_day:02d}"

            ret_start_date: Optional[str] = None
            ret_end_date: Optional[str] = None
            if return_date and return_window_label:
                try:
                    r_dt = datetime.strptime(return_date, "%Y-%m-%d").date()
                    last_r_day = calendar.monthrange(r_dt.year, r_dt.month)[1]
                    ret_start_date = f"{r_dt.year:04d}-{r_dt.month:02d}-01"
                    ret_end_date = f"{r_dt.year:04d}-{r_dt.month:02d}-{last_r_day:02d}"
                except Exception:
                    ret_start_date = None
                    ret_end_date = None

            cal_rows = self.fetch_calendar_prices(
                origin_code=orig_code,
                dest_code=dest_code,
                start_date=start_date,
                end_date=cal_end_date,
                return_date=return_date,
                return_start_date=ret_start_date,
                return_end_date=ret_end_date,
                trip_days=trip_days,
                cabin=cabin,
                currency=currency,
                max_stops=max_stops,
            )
            if cal_rows:
                selected_pairs: list[tuple[str, Optional[str]]] = []
                seen_set: set[tuple[str, Optional[str]]] = set()
                seen_weekdays: set[int] = set()
                dep_counts: dict[str, int] = {}
                # 1. Include up to 6 lowest-priced date pairs while capping any single departure date to <=2 return dates
                for _p, dep_d, ret_d in cal_rows:
                    pair = (dep_d, ret_d)
                    if pair in seen_set or dep_counts.get(dep_d, 0) >= 2:
                        continue
                    seen_set.add(pair)
                    dep_counts[dep_d] = dep_counts.get(dep_d, 0) + 1
                    selected_pairs.append(pair)
                    try:
                        seen_weekdays.add(datetime.strptime(dep_d, "%Y-%m-%d").weekday())
                    except ValueError:
                        pass
                    if len(selected_pairs) >= 6:
                        break
                # 2. Ensure operating weekday diversity (up to 8 total pairs)
                for _p, dep_d, ret_d in cal_rows:
                    if len(selected_pairs) >= 8:
                        break
                    pair = (dep_d, ret_d)
                    if pair in seen_set:
                        continue
                    try:
                        wd_idx = datetime.strptime(dep_d, "%Y-%m-%d").weekday()
                    except ValueError:
                        continue
                    if wd_idx not in seen_weekdays:
                        seen_weekdays.add(wd_idx)
                        seen_set.add(pair)
                        selected_pairs.append(pair)
                selected_pairs.sort(key=lambda x: (x[0], x[1] or ""))
                scan_pairs = selected_pairs

        futs = {
            (dep_s, ret_s): submit_to_worker(
                self.fetch_structured_fares,
                orig_code,
                dest_code,
                dep_s,
                ret_s,
                cabin,
                currency,
                max_stops,
                sort_preference,
            )
            for dep_s, ret_s in scan_pairs
        }

        daily_results: list[tuple[str, str, dict[str, Any]]] = []
        for dep_s, ret_s in scan_pairs:
            try:
                res = futs[(dep_s, ret_s)].result()
                if res and res.get("options"):
                    wd = datetime.strptime(dep_s, "%Y-%m-%d").strftime("%A")
                    daily_results.append((dep_s, wd, res))
            except Exception as e:
                logger.debug(f"Calendar day scan failed for {dep_s}: {e}")

        def _fmt_day_label(dt_s: str, wd: str, res_obj: Optional[dict[str, Any]] = None, short_wd: bool = False) -> str:
            base_lbl = f"{wd[:3]} {dt_s}" if short_wd else f"{wd}, {dt_s}"
            r_dt_s = (res_obj or {}).get("return_date")
            if not r_dt_s and trip_days:
                try:
                    r_dt_s = (datetime.strptime(dt_s, "%Y-%m-%d").date() + timedelta(days=trip_days)).strftime("%Y-%m-%d")
                except Exception:
                    r_dt_s = None
            if r_dt_s:
                try:
                    r_wd = datetime.strptime(r_dt_s, "%Y-%m-%d").strftime("%a")
                    return f"{base_lbl} ↔ Return {r_wd} {r_dt_s}"
                except Exception:
                    return f"{base_lbl} ↔ Return {r_dt_s}"
            return base_lbl

        def _fmt_link_dates(dt_s: str, res_obj: Optional[dict[str, Any]] = None) -> str:
            r_dt_s = (res_obj or {}).get("return_date")
            if not r_dt_s and trip_days:
                try:
                    r_dt_s = (datetime.strptime(dt_s, "%Y-%m-%d").date() + timedelta(days=trip_days)).strftime("%Y-%m-%d")
                except Exception:
                    r_dt_s = None
            return f"{dt_s} to {r_dt_s}" if r_dt_s else dt_s

        is_rt_scan = bool(return_date or trip_days)
        curr_lbl = f"{currency} Round-Trip" if is_rt_scan else currency
        stops_filter_tag = f" | **Filter**: Max {max_stops} Stop(s)" if max_stops is not None else ""
        if return_date:
            trip_mode_str = f"Round-Trip (Outbound: {window_label.split('–')[0].strip()} | Return: {return_window_label or return_date})"
        elif trip_days:
            trip_mode_str = f"Round-Trip (~{trip_days}-Day Stay in {window_label})"
        else:
            trip_mode_str = f"Flexible Window ({window_label})"
        route_arrow = "<->" if is_rt_scan else "->"
        lines = [
            f"=== 📅 FLEXIBLE DATE & DIRECT FLIGHT SCHEDULE SCANNER: {orig_city.upper()} ({orig_code}) {route_arrow} {dest_city.upper()} ({dest_code}) ===",
            f"• **Route**: {orig_name} ({orig_code}) {route_arrow} {dest_name} ({dest_code})",
            f"• **Trip Type**: {trip_mode_str}",
            f"• **Scanned Window**: {window_label} ({len(date_list)} days analyzed in parallel) | **Cabin**: {cabin.title()} | **Currency**: {currency}{stops_filter_tag}",
            f"• **Direct Google Flights Search ({window_label})**: [Google Flights: {orig_code} {route_arrow} {dest_code} ({rep_dep_date}{f' to {return_date}' if return_date else ''})](<{requested_booking_url}>)",
        ]

        # --- Helper to append 6-month benchmark + 1-stop gateway schedule for >6-month future windows ---
        def _append_future_horizon_and_gateway_sections() -> None:
            horizon_data = self.find_latest_available_date(orig_code, dest_code, cabin, currency, max_stops=max_stops)
            rt_benchmark_data = None
            if return_date and horizon_data and horizon_data.get("date"):
                try:
                    h_dt = datetime.strptime(horizon_data["date"], "%Y-%m-%d").date()
                    h_dep_s = (h_dt - timedelta(days=9)).strftime("%Y-%m-%d")
                    rt_benchmark_data = self.fetch_structured_fares(
                        orig_code,
                        dest_code,
                        h_dep_s,
                        horizon_data["date"],
                        cabin,
                        currency,
                        max_stops if max_stops is not None else 1,
                        sort_preference,
                    )
                except Exception:
                    rt_benchmark_data = None

            gateway_lines = self._probe_gateway_one_stop_schedule(
                orig_info, dest_info, start_date, cabin, currency, return_date=return_date
            )
            if gateway_lines:
                lines.extend(gateway_lines)

            if horizon_data and horizon_data.get("options"):
                h_date = horizon_data["date"]
                lines.append(
                    f"\n=== 📅 LATEST FULL 1-STOP & COMBINED SCHEDULE HORIZON: {orig_code} {route_arrow} {dest_code} ({h_date}) ==="
                )
                lines.append(
                    f"• **Schedule Horizon Benchmark (For Price Reference Only — Do Not Use {h_date} as Travel Date)**: "
                    f"Google Flights currently has full 1-stop married-segment schedules published up through **{h_date}** (~6 months out). "
                    f"Here are the live 1-stop benchmark fares at the current horizon:"
                )
                active_benchmark = rt_benchmark_data if (rt_benchmark_data and rt_benchmark_data.get("options")) else horizon_data
                if rt_benchmark_data and rt_benchmark_data.get("options"):
                    rt_best = rt_benchmark_data.get("best_overall") or rt_benchmark_data["lowest_overall"]
                    lines.append(
                        f"• 🔄 **Published 1-Stop Round-Trip Benchmark ({orig_code} <-> {dest_code})**: "
                        f"**${rt_best['price']} {currency} Round-Trip** via **{rt_best['airlines']}** "
                        f"({rt_best['stops_label']}, **{rt_best['duration_str']}**, {rt_best['departure_time']} -> {rt_best['arrival_time']})"
                    )
                    rt_low = rt_benchmark_data.get("lowest_reasonable") or rt_benchmark_data["lowest_overall"]
                    if rt_low and rt_low != rt_best:
                        lines.append(
                            f"• 💰 **Lowest 1-Stop Round-Trip Benchmark**: **${rt_low['price']} {currency} Round-Trip** via **{rt_low['airlines']}** "
                            f"({rt_low['stops_label']}, **{rt_low['duration_str']}**, {rt_low['departure_time']} -> {rt_low['arrival_time']})"
                        )
                if not gateway_lines:
                    lines.extend(self._format_fare_block(active_benchmark, currency, max_items=4, include_booking_link=False))

        if not daily_results:
            _append_future_horizon_and_gateway_sections()
            if len(lines) <= 5:
                lines.append(f"• **Status**: No live flight itineraries found for {orig_code} -> {dest_code} in {window_label}.")
            return "\n".join(lines) + "\n"

        # --- SECTION 1: DIRECT / NONSTOP & 1-STOP FLIGHT SCHEDULE DISCOVERY ---
        direct_days: list[tuple[str, str, dict[str, Any], str, dict[str, Any]]] = []
        one_stop_days: list[tuple[str, str, dict[str, Any], str, dict[str, Any]]] = []
        operating_weekdays: list[str] = []
        weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for dt_s, wd, res in daily_results:
            ns = res.get("lowest_nonstop")
            if ns:
                direct_days.append((dt_s, wd, ns, res["booking_url"], res))
                if wd not in operating_weekdays:
                    operating_weekdays.append(wd)

            le1_opts = [o for o in (res.get("options") or []) if o.get("stops", 99) <= 1]
            if le1_opts:
                if sort_preference == "shortest":
                    best_le1 = min(le1_opts, key=lambda o: (o.get("duration_mins") or 999999, o["price"]))
                elif sort_preference == "cheapest":
                    best_le1 = min(le1_opts, key=lambda o: (o["price"], o.get("duration_mins") or 999999))
                else:
                    best_le1 = min(
                        le1_opts,
                        key=lambda o: (
                            o["price"] + round((o.get("duration_mins") or 0) * 0.45) + (o.get("stops", 0) * 45),
                            o["price"],
                        ),
                    )
                one_stop_days.append((dt_s, wd, best_le1, res["booking_url"], res))

        operating_weekdays.sort(key=lambda w: weekday_order.index(w) if w in weekday_order else 99)
        non_operating = [w for w in weekday_order if w not in operating_weekdays]

        lines.append("\n--- ✈️ DIRECT / NONSTOP FLIGHT SCHEDULE & BEST DIRECT FARES ---")
        if direct_days:
            sample_ns = direct_days[0][2]
            airlines_set = sorted({d[2]["airlines"] for d in direct_days})
            lines.append(
                f"• **Direct Flight Operating Days**: **{', '.join(operating_weekdays)}** "
                f"(Operated by **{', '.join(airlines_set)}**, Nonstop duration **{sample_ns['duration_str']}**, "
                f"{sample_ns['departure_time']} -> {sample_ns['arrival_time']})"
            )
            if non_operating:
                lines.append(f"• **Days Without Direct Flights**: {', '.join(non_operating)} (connecting flights only)")
            direct_sorted = sorted(direct_days, key=lambda x: (x[2]["price"], x[0]))
            best_dir_dt, best_dir_wd, best_dir_opt, best_dir_url, best_dir_res = direct_sorted[0]
            lines.append(
                f"• ⭐ **Cheapest Direct Flight Date**: **{_fmt_day_label(best_dir_dt, best_dir_wd, best_dir_res)}** at **${best_dir_opt['price']} {curr_lbl}** "
                f"via **{best_dir_opt['airlines']}** ({best_dir_opt['duration_str']}) — [Book Direct: {_fmt_link_dates(best_dir_dt, best_dir_res)}](<{best_dir_url}>)"
            )
            lines.append("• **Top Direct Flight Dates in Window**:")
            for idx, (dt_s, wd, ns, b_url, d_res) in enumerate(direct_sorted[:5], start=1):
                lines.append(
                    f"  {idx}. **{_fmt_day_label(dt_s, wd, d_res, short_wd=True)}** — **${ns['price']} {curr_lbl}** | **{ns['airlines']}** [Nonstop, {ns['duration_str']}, "
                    f"{ns['departure_time']} -> {ns['arrival_time']}] ([Google Flights: {_fmt_link_dates(dt_s, d_res)}](<{b_url}>))"
                )
        else:
            lines.append(
                f"• **Direct Flight Status**: No airline operates nonstop direct flights between **{orig_city} ({orig_code})** and **{dest_city} ({dest_code})**."
            )
            # Only show fastest connecting flight in window if it satisfies max_stops (or if max_stops is not set)
            valid_for_fastest = (
                [(dt_s, wd, {**res, "fastest_overall": o1, "lowest_overall": o1}) for dt_s, wd, o1, _u, res in one_stop_days]
                if max_stops == 1
                else ([] if max_stops == 0 else daily_results)
            )
            if valid_for_fastest:
                fastest_day = min(
                    valid_for_fastest,
                    key=lambda x: (
                        x[2]["fastest_overall"]["duration_mins"]
                        if x[2].get("fastest_overall") and x[2]["fastest_overall"].get("duration_mins", 0) > 0
                        else 999999,
                        x[2]["fastest_overall"]["price"] if x[2].get("fastest_overall") else 999999,
                    ),
                )
                f_dt, f_wd, f_res = fastest_day
                f_opt = f_res.get("fastest_overall") or f_res["lowest_overall"]
                lines.append(
                    f"• ⚡ **Shortest / Fastest Connecting Flight in Scanned Window**: **{_fmt_day_label(f_dt, f_wd, f_res)}** — **{f_opt['duration_str']}** "
                    f"({f_opt['stops_label']}) at **${f_opt['price']} {curr_lbl}** via **{f_opt['airlines']}** ([Google Flights: {_fmt_link_dates(f_dt, f_res)}](<{f_res['booking_url']}>))"
                )

        # If user requested 1-stop flights (or no direct flights exist), highlight 1-stop availability
        if max_stops == 1 or (not direct_days and one_stop_days):
            lines.append("\n--- ✈️ 1-STOP FLIGHT SCHEDULE & BEST 1-STOP FARES ---")
            if one_stop_days:
                one_stop_sorted = sorted(
                    one_stop_days,
                    key=lambda x: (
                        x[2].get("duration_mins") or 999999 if sort_preference == "shortest" else x[2]["price"],
                        x[2]["price"],
                        x[2].get("duration_mins") or 999999,
                    ),
                )
                b1_dt, b1_wd, b1_opt, b1_url, b1_res = one_stop_sorted[0]
                lines.append(
                    f"• ⭐ **Best 1-Stop Flight in Window**: **{_fmt_day_label(b1_dt, b1_wd, b1_res)}** at **${b1_opt['price']} {curr_lbl}** "
                    f"via **{b1_opt['airlines']}** ({b1_opt['stops_label']}, **{b1_opt['duration_str']}**, "
                    f"{b1_opt['departure_time']} -> {b1_opt['arrival_time']}) — [Book 1-Stop: {_fmt_link_dates(b1_dt, b1_res)}](<{b1_url}>)"
                )
                for idx, (dt_s, wd, o1, b_url, r_obj) in enumerate(one_stop_sorted[:5], start=1):
                    lines.append(
                        f"  {idx}. **{_fmt_day_label(dt_s, wd, r_obj, short_wd=True)}** — **${o1['price']} {curr_lbl}** | **{o1['airlines']}** "
                        f"[{o1['stops_label']}, **{o1['duration_str']}**, {o1['departure_time']} -> {o1['arrival_time']}] ([Google Flights: {_fmt_link_dates(dt_s, r_obj)}](<{b_url}>))"
                    )
            else:
                lines.append(
                    f"• ⚠️ **No Combined 1-Stop Fares Returned in {window_label} Yet**: Only **{len(daily_results)} of {len(date_list)}** scanned days "
                    f"returned any combined itineraries (2-stop only, which were excluded by your 1-stop filter), because **{window_label} is beyond Google Flights' 6-month (182-day) "
                    f"married-segment horizon** where KLM & partner airlines merge 1-stop connections into a single priced itinerary."
                )

        # --- SECTION 2 & 3: STRICTLY RESPECT max_stops WHEN RANKING DAYS & MATRIX ---
        if max_stops == 0:
            candidate_days = [
                (dt_s, wd, {**d_res, "best_overall": ns, "lowest_reasonable": ns, "fastest_overall": ns, "lowest_overall": ns, "lowest_nonstop": ns, "booking_url": b_url})
                for dt_s, wd, ns, b_url, d_res in direct_days
            ]
        elif max_stops == 1:
            candidate_days = [
                (dt_s, wd, {**res, "best_overall": o1, "lowest_reasonable": o1, "fastest_overall": o1, "lowest_overall": o1})
                for dt_s, wd, o1, _u, res in one_stop_days
            ]
        else:
            candidate_days = daily_results

        if candidate_days:
            if sort_preference == "shortest":
                ranked_days = sorted(
                    candidate_days,
                    key=lambda x: (
                        x[2]["fastest_overall"]["duration_mins"]
                        if x[2].get("fastest_overall") and x[2]["fastest_overall"].get("duration_mins", 0) > 0
                        else 999999,
                        x[2]["fastest_overall"]["price"] if x[2].get("fastest_overall") else 999999,
                    ),
                )
                rank_title = "⚡ TOP 5 SHORTEST / FASTEST DAYS TO FLY"
                pick_key = "fastest_overall"
            elif sort_preference == "cheapest":
                ranked_days = sorted(
                    candidate_days,
                    key=lambda x: (
                        x[2]["lowest_reasonable"]["price"],
                        x[2]["lowest_reasonable"]["duration_mins"],
                    ),
                )
                rank_title = "💰 TOP 5 CHEAPEST REASONABLE DAYS TO FLY (30h+ Marathon Layovers Filtered Out)"
                pick_key = "lowest_reasonable"
            else:
                ranked_days = sorted(
                    candidate_days,
                    key=lambda x: (
                        x[2]["best_overall"]["price"]
                        + round(x[2]["best_overall"]["duration_mins"] * 0.45)
                        + (x[2]["best_overall"]["stops"] * 45),
                        x[2]["best_overall"]["price"],
                        x[2]["best_overall"]["duration_mins"],
                    ),
                )
                rank_title = "🏆 TOP 5 BEST-VALUE DAYS TO FLY (Optimal Balance of Price & Short Duration)"
                pick_key = "best_overall"

            top_dt, _top_wd, top_res = ranked_days[0]
            lines[4] = (
                f"• **Direct Google Flights Search ({window_label})**: "
                f"[Google Flights: {orig_code} {route_arrow} {dest_code} ({_fmt_link_dates(top_dt, top_res)})](<{top_res['booking_url']}>)"
            )

            lines.append(f"\n--- {rank_title} ---")
            for idx, (dt_s, wd, res) in enumerate(ranked_days[:5], start=1):
                opt = res.get(pick_key) or res["lowest_overall"]
                verdict_tag = f" | {res['verdict']}" if res.get("verdict") else ""
                lines.append(
                    f"{idx}. **{_fmt_day_label(dt_s, wd, res)}** — **${opt['price']} {curr_lbl}** via **{opt['airlines']}** "
                    f"[{opt['stops_label']}, **{opt['duration_str']}**, {opt['departure_time']} -> {opt['arrival_time']}]{verdict_tag} "
                    f"([Google Flights: {_fmt_link_dates(dt_s, res)}](<{res['booking_url']}>))"
                )

            # --- SECTION 3: DAY-BY-DAY CALENDAR MATRIX ---
            lines.append("\n--- 📅 DAY-BY-DAY PRICE & DIRECT FLIGHT MATRIX ---")
            for dt_s, wd, res in candidate_days:
                best_opt = res.get("best_overall") or res["lowest_overall"]
                low_opt = res.get("lowest_reasonable") or res["lowest_overall"]
                ns_opt = res.get("lowest_nonstop")
                ns_col = f"⚡ Direct: **${ns_opt['price']} {curr_lbl}** ({ns_opt['airlines']}, {ns_opt['duration_str']})" if ns_opt else "Direct: —"
                if low_opt and low_opt["price"] < best_opt["price"]:
                    best_col = (
                        f"🏆 Best: **${best_opt['price']} {curr_lbl}** ({best_opt['duration_str']}, {best_opt['stops_label']}) | "
                        f"💰 Low: **${low_opt['price']} {curr_lbl}** ({low_opt['duration_str']})"
                    )
                else:
                    best_col = f"🏆 Best: **${best_opt['price']} {curr_lbl}** ({best_opt['airlines']}, {best_opt['stops_label']}, {best_opt['duration_str']})"
                lines.append(f"• **{_fmt_day_label(dt_s, wd, res, short_wd=True)}**: {best_col} | {ns_col}")

            # Delegate visual calendar heatmap rendering to src.skills.graphing
            try:
                from src.skills.graphing import (
                    CalendarDayCell,
                    CalendarHeatmapSpec,
                    render_and_queue_calendar_heatmap,
                )

                cell_map: dict[str, CalendarDayCell] = {}
                for p_val, dep_d, ret_d in cal_rows:
                    if not dep_d or not p_val:
                        continue
                    try:
                        wd_name = datetime.strptime(dep_d, "%Y-%m-%d").strftime("%A")
                    except ValueError:
                        wd_name = ""
                    prev_c = cell_map.get(dep_d)
                    if prev_c is None or float(p_val) < prev_c.value:
                        cell_map[dep_d] = CalendarDayCell(
                            date_str=dep_d,
                            value=float(p_val),
                            sublabel=f"↔ {ret_d[5:]}" if ret_d else "",
                            is_direct=bool(operating_weekdays and wd_name in operating_weekdays),
                        )

                for dt_s, _wd, res in candidate_days:
                    opt = res.get(pick_key) or res.get("lowest_reasonable") or res["lowest_overall"]
                    if not opt or not opt.get("price"):
                        continue
                    has_ns = bool(res.get("lowest_nonstop"))
                    ret_d = res.get("return_date")
                    sub_lbl = f"↔ {ret_d[5:]}" if ret_d else (str(opt.get("airlines") or "")[:12])
                    cell_map[dt_s] = CalendarDayCell(
                        date_str=dt_s,
                        value=float(opt["price"]),
                        sublabel=sub_lbl,
                        is_best=(dt_s == top_dt),
                        is_direct=has_ns,
                    )

                if len(cell_map) >= 3:
                    hm_arrow = "↔" if is_rt_scan else "→"
                    hm_spec = CalendarHeatmapSpec(
                        title=f"{orig_city} ({orig_code}) {hm_arrow} {dest_city} ({dest_code}) — Fare Calendar ({currency})",
                        subtitle=f"{trip_mode_str}  •  Cabin: {cabin.title()}{f'  •  Max {max_stops} Stop(s)' if max_stops is not None else ''}",
                        cells=list(cell_map.values()),
                        value_prefix="$",
                        currency=currency,
                        footer_notes=(
                            [f"**Direct Flight Days:** {', '.join(w[:3] for w in operating_weekdays)}"]
                            if operating_weekdays
                            else []
                        ),
                    )
                    visual = render_and_queue_calendar_heatmap(hm_spec)
                    lines.append(
                        f"\n• 📊 **Visual Fare Calendar Heatmap Attached**: `{visual.filename}` "
                        f"(Color-coded monthly fare grid attached automatically to this reply)"
                    )
            except Exception as e:
                logger.debug(f"Calendar heatmap delegation skipped: {e}")

        # --- SECTION 4: SPARSE FUTURE WINDOW / MISSING 1-STOP HORIZON & GATEWAY HUB DISCOVERY ---
        is_sparse_window = len(candidate_days) <= max(2, len(scan_pairs) // 5)
        missing_1stop = not direct_days and not one_stop_days
        if is_sparse_window or missing_1stop:
            _append_future_horizon_and_gateway_sections()

        return "\n".join(lines) + "\n"

    def search_prices(
        self,
        query: str = "",
        origin: Optional[str] = None,
        destination: Optional[str] = None,
        date: Optional[str] = None,
        return_date: Optional[str] = None,
        cabin: Optional[str] = None,
        currency: Optional[str] = None,
        max_stops: Optional[int] = None,
        sort_by: Optional[str] = None,
        trip_type: Optional[str] = None,
    ) -> str:
        """
        Formats a rich Google Flights price comparison report for the LLM.
        Automatically routes flexible-date / 'best day to fly' / 'when are direct flights'
        queries to the parallel calendar scanner, filters out 30+ hour marathon layovers,
        and includes schedule-horizon and nearby-hub fallbacks.
        """
        parsed = parse_route_from_query(
            query,
            origin=origin,
            destination=destination,
            date=date,
            return_date=return_date,
            max_stops_arg=max_stops,
            sort_by_arg=sort_by,
            trip_type_arg=trip_type,
        )
        orig_info = parsed["origin"] or (resolve_airport(origin) if origin else None)
        dest_info = parsed["destination"] or (resolve_airport(destination) if destination else None)

        if not orig_info or not dest_info:
            missing = []
            if not orig_info:
                missing.append("origin city/airport (e.g. Calgary / YYC)")
            if not dest_info:
                missing.append("destination city/airport (e.g. Toronto / YYZ)")
            return (
                f"[GOOGLE FLIGHTS PRICE SEARCH]\n"
                f"Could not determine {', '.join(missing)} from query: '{query}'.\n"
                f"Please specify both origin and destination cities or 3-letter IATA airport codes.\n"
            )

        orig_code, orig_name, orig_city, _ = orig_info
        dest_code, dest_name, dest_city, _ = dest_info
        dep_date = parsed["date"]
        ret_date = parsed["return_date"]
        eff_cabin = (cabin or parsed["cabin"] or "economy").lower()
        eff_currency = (currency or parsed["currency"] or "CAD").upper()
        eff_max_stops = parsed["max_stops"]
        sort_preference = parsed.get("sort_preference", "best")
        wants_horizon = parsed.get("wants_latest_horizon", False)
        wants_flexible_scan = parsed.get("wants_flexible_scan", False)

        # 1. If the user asked for the latest available flight/return date in the schedule:
        if wants_horizon:
            horizon_data = self.find_latest_available_date(
                origin_code=orig_code,
                dest_code=dest_code,
                cabin=eff_cabin,
                currency=eff_currency,
                max_stops=eff_max_stops,
            )
            lines = [
                f"=== GOOGLE FLIGHTS SCHEDULE HORIZON & LATEST AVAILABLE FARES: {orig_city.upper()} ({orig_code}) -> {dest_city.upper()} ({dest_code}) ===",
                f"• **Route**: {orig_name} ({orig_code}) -> {dest_name} ({dest_code})",
            ]
            if horizon_data and horizon_data["options"]:
                h_date = horizon_data["date"]
                lines.append(
                    f"• **Latest Published Schedule Date on Google Flights**: **{h_date}** "
                    f"(Airlines on {orig_code} -> {dest_code} currently have combinable schedules published up through **{h_date}**; dates beyond {h_date} have not opened for multi-carrier booking yet)."
                )
                lines.extend(self._format_fare_block(horizon_data, eff_currency, max_items=6))
            else:
                lines.append(f"• **Status**: Could not find published schedule horizon for {orig_code} -> {dest_code}.")

            hub = NEARBY_HUB_AIRPORTS.get(orig_code)
            if hub and hub[0] != dest_code:
                hub_code, hub_city = hub
                hub_horizon = self.find_latest_available_date(
                    origin_code=hub_code,
                    dest_code=dest_code,
                    cabin=eff_cabin,
                    currency=eff_currency,
                    max_stops=eff_max_stops,
                )
                if hub_horizon and hub_horizon["options"]:
                    lines.append(
                        f"\n=== NEARBY HUB EXTENDED SCHEDULE HORIZON: {hub_city.upper()} ({hub_code}) -> {dest_city.upper()} ({dest_code}) ==="
                    )
                    lines.append(
                        f"• **Latest Published Date from {hub_city} ({hub_code})**: **{hub_horizon['date']}** "
                        f"(Starting at **${hub_horizon['lowest_overall']['price']} {eff_currency}** via **{hub_horizon['lowest_overall']['airlines']}**)"
                    )
                    lines.append(
                        f"• **Direct Booking Link**: [Google Flights: {hub_code} to {dest_code} ({hub_horizon['date']})](<{hub_horizon['booking_url']}>)"
                    )
            return "\n".join(lines) + "\n"

        # 2. If the user asked "when is the best/cheapest/shortest day to fly?" or "when are the direct flights?" or a month/multi-month window:
        if wants_flexible_scan and (not ret_date or parsed.get("dep_month_window") or parsed.get("ret_month_window")):
            return self.scan_flexible_dates_and_direct_schedule(
                orig_info=orig_info,
                dest_info=dest_info,
                start_date=parsed["flex_start_date"],
                end_date=parsed["flex_end_date"],
                window_label=parsed["flex_label"],
                cabin=eff_cabin,
                currency=eff_currency,
                sort_preference=sort_preference,
                trip_days=parsed.get("trip_days"),
                max_stops=eff_max_stops,
                return_date=ret_date,
                return_window_label=parsed.get("return_flex_label"),
            )

        data = self.fetch_structured_fares(
            origin_code=orig_code,
            dest_code=dest_code,
            date=dep_date,
            return_date=ret_date,
            cabin=eff_cabin,
            currency=eff_currency,
            max_stops=eff_max_stops,
            sort_preference=sort_preference,
        )
        if not data["options"] and eff_max_stops is not None:
            data = self.fetch_structured_fares(
                origin_code=orig_code,
                dest_code=dest_code,
                date=dep_date,
                return_date=ret_date,
                cabin=eff_cabin,
                currency=eff_currency,
                max_stops=None,
                sort_preference=sort_preference,
            )

        trip_label = f"Round-Trip ({dep_date} to {ret_date})" if ret_date else f"One-Way ({dep_date})"
        if parsed["defaulted_date"]:
            trip_label += " [Defaulted to 2 weeks out — no specific date was mentioned]"

        lines = [
            f"=== GOOGLE FLIGHTS LIVE TICKET PRICES: {orig_city.upper()} ({orig_code}) -> {dest_city.upper()} ({dest_code}) ===",
            f"• **Route**: {orig_name} ({orig_code}) -> {dest_name} ({dest_code})",
            f"• **Dates**: {trip_label} | **Cabin**: {eff_cabin.title()} | **Currency**: {eff_currency}",
        ]

        if data["options"]:
            nearby_direct = None
            if not data.get("lowest_nonstop") and (parsed.get("wants_direct_schedule") or not ret_date):
                nearby_direct = self._find_nearby_direct_flights(
                    origin_code=orig_code,
                    dest_code=dest_code,
                    center_date=dep_date,
                    cabin=eff_cabin,
                    currency=eff_currency,
                )
            lines.extend(self._format_fare_block(data, eff_currency, max_items=8, nearby_direct=nearby_direct))
            # If user asked for 1-stop or all returned options are 2+ stops on a sparse date (>6 months out),
            # also check the 6-month horizon + 1-stop gateway hub schedule
            has_le1 = any(o.get("stops", 99) <= 1 for o in data["options"])
            if not has_le1 and (eff_max_stops == 1 or len(data["options"]) <= 2):
                horizon_data = self.find_latest_available_date(orig_code, dest_code, eff_cabin, eff_currency)
                gateway_lines = self._probe_gateway_one_stop_schedule(
                    orig_info, dest_info, dep_date, eff_cabin, eff_currency, return_date=ret_date
                )
                if gateway_lines:
                    lines.extend(gateway_lines)
                if horizon_data and horizon_data.get("options") and horizon_data["date"] != dep_date:
                    h_date = horizon_data["date"]
                    lines.append(
                        f"\n=== 📅 LATEST FULL 1-STOP & COMBINED SCHEDULE HORIZON: {orig_code} -> {dest_code} ({h_date}) ==="
                    )
                    lines.extend(self._format_fare_block(horizon_data, eff_currency, max_items=5, include_booking_link=False))
            return "\n".join(lines) + "\n"

        # --- FALLBACK WHEN REQUESTED DATE RETURNS 0 ITINERARIES ---
        # 1. If a round-trip was requested, fetch BOTH the outbound leg (orig -> dest on dep_date)
        #    and the return leg (dest -> orig on ret_date) in parallel!
        if ret_date:
            ow_out_fut = submit_to_worker(
                self.fetch_structured_fares,
                orig_code,
                dest_code,
                dep_date,
                None,
                eff_cabin,
                eff_currency,
                max_stops,
                sort_preference,
            )
            ow_ret_fut = submit_to_worker(
                self.fetch_structured_fares,
                dest_code,
                orig_code,
                ret_date,
                None,
                eff_cabin,
                eff_currency,
                max_stops,
                sort_preference,
            )
            ow_data = ow_out_fut.result()
            ret_ow_data = ow_ret_fut.result()

            if ow_data["options"] and ret_ow_data["options"]:
                ow_best = ow_data.get("best_overall") or ow_data["lowest_overall"]
                ret_best = ret_ow_data.get("best_overall") or ret_ow_data["lowest_overall"]
                ow_low = ow_data["lowest_overall"]
                ret_low = ret_ow_data["lowest_overall"]
                best_combined = ow_best["price"] + ret_best["price"]
                low_combined = ow_low["price"] + ret_low["price"]
                lines.append(
                    f"• 🏆 **Best Recommended Round-Trip Total**: **${best_combined} {eff_currency} Total** "
                    f"(**${ow_best['price']} {eff_currency}** outbound [{ow_best['duration_str']}] via **{ow_best['airlines']}** + "
                    f"**${ret_best['price']} {eff_currency}** return [{ret_best['duration_str']}] via **{ret_best['airlines']}**)"
                )
                if low_combined < best_combined:
                    lines.append(
                        f"• 💰 **Cheapest Reasonable Round-Trip Total**: **${low_combined} {eff_currency} Total** "
                        f"(**${ow_low['price']} {eff_currency}** outbound + **${ret_low['price']} {eff_currency}** return)"
                    )
                lines.append(
                    f"• **Direct Round-Trip Search Link**: [Google Flights Round-Trip: {orig_code} <-> {dest_code} ({dep_date} to {ret_date})](<{data['booking_url']}>)"
                )
                lines.append(
                    f"\n=== 🛫 OUTBOUND LEG: {orig_city.upper()} ({orig_code}) -> {dest_city.upper()} ({dest_code}) ({dep_date}) ==="
                )
                lines.extend(self._format_fare_block(ow_data, eff_currency, max_items=5))
                lines.append(
                    f"\n=== 🛬 RETURN LEG: {dest_city.upper()} ({dest_code}) -> {orig_city.upper()} ({orig_code}) ({ret_date}) ==="
                )
                lines.extend(self._format_fare_block(ret_ow_data, eff_currency, max_items=5))
                return "\n".join(lines) + "\n"

            if ow_data["options"] and not ret_ow_data["options"]:
                horizon_data = self.find_latest_available_date(
                    origin_code=dest_code,
                    dest_code=orig_code,
                    cabin=eff_cabin,
                    currency=eff_currency,
                    max_stops=max_stops,
                )
                h_date_str = horizon_data["date"] if (horizon_data and horizon_data["options"]) else "mid-2027"
                lines.append(
                    f"• **Schedule Horizon Notice**: Your outbound date (**{dep_date}**) HAS live flights, "
                    f"but your return date (**{ret_date}**) is beyond the current published airline schedule window "
                    f"for {dest_code} -> {orig_code} (currently published through **{h_date_str}**)."
                )
                lines.append(f"• **Showing Live One-Way Outbound Fares for {dep_date}**:")
                lines.extend(self._format_fare_block(ow_data, eff_currency, max_items=6))
                return "\n".join(lines) + "\n"

        # 2. Outbound date itself returned 0 flights: probe the route's latest published schedule date
        #    AND check nearby major hub airport on the exact requested date in parallel!
        hub = NEARBY_HUB_AIRPORTS.get(orig_code)
        horizon_fut = submit_to_worker(
            self.find_latest_available_date,
            orig_code,
            dest_code,
            eff_cabin,
            eff_currency,
            max_stops,
        )
        hub_ow_fut = None
        hub_rt_fut = None
        if hub and hub[0] != dest_code:
            hub_code, hub_city = hub
            if ret_date:
                hub_rt_fut = submit_to_worker(
                    self.fetch_structured_fares,
                    hub_code,
                    dest_code,
                    dep_date,
                    ret_date,
                    eff_cabin,
                    eff_currency,
                    max_stops,
                    sort_preference,
                )
            hub_ow_fut = submit_to_worker(
                self.fetch_structured_fares,
                hub_code,
                dest_code,
                dep_date,
                None,
                eff_cabin,
                eff_currency,
                max_stops,
                sort_preference,
            )

        horizon_data = horizon_fut.result()
        if horizon_data and horizon_data["options"]:
            h_date = horizon_data["date"]
            lines.append(
                f"• **Why {dep_date} Returned No Flights**: This is **not** a rate limit — airlines serving "
                f"**{orig_city} ({orig_code}) -> {dest_city} ({dest_code})** currently only have combinable multi-carrier "
                f"schedules published up through **{h_date}** on Google Flights. Dates after **{h_date}** (including **{dep_date}**) "
                f"have not been released by the connecting regional/partner carriers yet."
            )
            lines.append(f"• **Direct Google Flights Search ({dep_date})**: [View on Google Flights](<{data['booking_url']}>)")
            lines.append(
                f"\n=== LATEST PUBLISHED SCHEDULE FOR {orig_city.upper()} ({orig_code}) -> {dest_city.upper()} ({dest_code}): {h_date} ==="
            )
            lines.extend(self._format_fare_block(horizon_data, eff_currency, max_items=5))
        else:
            lines.append(f"• **Status**: No live flight itineraries found for {orig_code} -> {dest_code} on {dep_date}.")
            lines.append(f"• **Direct Google Flights Search**: [View on Google Flights](<{data['booking_url']}>)")

        gateway_lines = self._probe_gateway_one_stop_schedule(orig_info, dest_info, dep_date, eff_cabin, eff_currency)
        if gateway_lines:
            lines.extend(gateway_lines)

        # Check if nearby major hub has live flights on the exact requested date!
        if hub and hub[0] != dest_code:
            hub_code, hub_city = hub
            hub_rt_data = hub_rt_fut.result() if hub_rt_fut else None
            hub_ow_data = hub_ow_fut.result() if hub_ow_fut else None
            active_hub_data = (
                hub_rt_data
                if (hub_rt_data and hub_rt_data["options"])
                else (hub_ow_data if (hub_ow_data and hub_ow_data["options"]) else None)
            )
            if active_hub_data:
                hub_trip_lbl = (
                    f"Round-Trip ({dep_date} to {ret_date})"
                    if active_hub_data.get("return_date")
                    else f"One-Way ({dep_date})"
                )
                lines.append(
                    f"\n=== 💡 NEARBY MAJOR HUB ALTERNATIVE ON YOUR EXACT DATE ({hub_trip_lbl}): {hub_city.upper()} ({hub_code}) -> {dest_city.upper()} ({dest_code}) ==="
                )
                if ret_date and not active_hub_data.get("return_date"):
                    lines.append(
                        f"• **Note**: {hub_city} ({hub_code}) -> {dest_city} ({dest_code}) **DOES** have live outbound flights published on **{dep_date}** "
                        f"(return date {ret_date} is still too far out, so showing One-Way on {dep_date}):"
                    )
                else:
                    lines.append(
                        f"• **Good News**: Nearby **{hub_city} ({hub_code})** already has published schedules to **{dest_city} ({dest_code})** for **{hub_trip_lbl}**!"
                    )
                lines.extend(self._format_fare_block(active_hub_data, eff_currency, max_items=5))

        return "\n".join(lines) + "\n"


