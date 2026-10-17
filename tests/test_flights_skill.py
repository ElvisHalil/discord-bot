from datetime import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.orchestrator import normalize_tool_args
from src.skills.flights import FLIGHT_TRACKER, skill_flight_info, skill_track_flight
from src.skills.flights.aggregators.live_status import LiveFlightStatusAggregator
from src.skills.flights.aggregators.prices import GoogleFlightsPriceAggregator
from src.skills.flights.airports import (
    extract_flight_numbers,
    parse_natural_date,
    parse_route_from_query,
    resolve_airport,
)
from src.skills.flights.tracker import (
    FlightTrackerManager,
    TrackedFlight,
    TrackedPriceWatch,
)
from src.skills.sports.live_tracker import set_active_channel_id


class TestFlightsSkill(unittest.TestCase):
    def test_airport_and_route_parsing(self):
        # 1. Airport & city resolution
        yyc = resolve_airport("Calgary")
        self.assertEqual(yyc[0], "YYC")
        self.assertEqual(yyc[2], "Calgary")

        hnd = resolve_airport("Tokyo")
        self.assertEqual(hnd[0], "HND")

        lhr = resolve_airport("London Heathrow")
        self.assertEqual(lhr[0], "LHR")

        # 2. Flight number extraction (without false positives on prices or dates)
        codes = extract_flight_numbers("check status of AC850 and WS 102 under $400 on Nov 15")
        self.assertEqual(codes, ["AC850", "WS102"])

        no_codes = extract_flight_numbers("flights from Calgary to Toronto on Nov 15 under $250")
        self.assertEqual(no_codes, [])

        # 3. Natural date parsing
        ref = datetime(2026, 10, 17, 12, 0)
        self.assertEqual(parse_natural_date("tomorrow", ref), "2026-10-18")
        self.assertEqual(parse_natural_date("Nov 15", ref), "2026-11-15")
        self.assertEqual(parse_natural_date("in 2 weeks", ref), "2026-10-31")

        # 4. Full route parsing
        parsed = parse_route_from_query("nonstop business flights from Calgary to Tokyo Nov 15 to Nov 25 in USD")
        self.assertEqual(parsed["origin"][0], "YYC")
        self.assertEqual(parsed["destination"][0], "HND")
        self.assertEqual(parsed["date"], "2026-11-15")
        self.assertEqual(parsed["return_date"], "2026-11-25")
        self.assertEqual(parsed["cabin"], "business")
        self.assertEqual(parsed["currency"], "USD")
        self.assertEqual(parsed["max_stops"], 0)

    def test_orchestrator_flight_arg_aliases(self):
        norm_info = normalize_tool_args(
            "skill_flight_info",
            skill_flight_info,
            {"from": "Calgary", "to": "London", "when": "2026-11-15", "flight": "AC850", "seat": "business"},
        )
        self.assertEqual(norm_info["origin"], "Calgary")
        self.assertEqual(norm_info["destination"], "London")
        self.assertEqual(norm_info["date"], "2026-11-15")
        self.assertEqual(norm_info["flight_number"], "AC850")
        self.assertEqual(norm_info["cabin"], "business")

        norm_track = normalize_tool_args(
            "skill_track_flight",
            skill_track_flight,
            {"cmd": "track", "route": "Calgary to Toronto", "max_price": "200"},
        )
        self.assertEqual(norm_track["action"], "track")
        self.assertEqual(norm_track["query"], "Calgary to Toronto")
        self.assertEqual(norm_track["target_price"], 200)

    @patch("src.skills.flights.aggregators.prices.fetch_flights_html")
    def test_google_flights_price_aggregator_best_and_insights(self, mock_fetch_html):
        # Construct synthetic Google Flights script.ds:1 payload with payload[2][0] (Best), payload[3][0] (Other), and payload[5] (Insights)
        def make_raw_flight(airline_code, airline_name, price, dep_h, arr_h, duration, plane, stops_via=None):
            legs = []
            if not stops_via:
                leg = [None] * 23
                leg[3] = "YYC"
                leg[4] = "Calgary International Airport"
                leg[5] = "Toronto Pearson International Airport"
                leg[6] = "YYZ"
                leg[8] = [dep_h, 0]
                leg[10] = [arr_h, 0]
                leg[11] = duration
                leg[17] = plane
                leg[20] = [2026, 11, 15]
                leg[21] = [2026, 11, 15]
                legs.append(leg)
            else:
                leg1 = [None] * 23
                leg1[3], leg1[4], leg1[5], leg1[6] = "YYC", "Calgary", "Montreal", stops_via
                leg1[8], leg1[10], leg1[11], leg1[17] = [dep_h, 0], [dep_h + 3, 0], 180, plane
                leg1[20], leg1[21] = [2026, 11, 15], [2026, 11, 15]
                leg2 = [None] * 23
                leg2[3], leg2[4], leg2[5], leg2[6] = stops_via, "Montreal", "Toronto", "YYZ"
                leg2[8], leg2[10], leg2[11], leg2[17] = [dep_h + 4, 0], [arr_h, 0], 90, plane
                leg2[20], leg2[21] = [2026, 11, 15], [2026, 11, 15]
                legs.extend([leg1, leg2])

            flight_arr = [None] * 23
            flight_arr[0] = airline_code
            flight_arr[1] = [airline_name]
            flight_arr[2] = legs
            extras = [None] * 10
            extras[7] = 190000
            extras[8] = 250000
            flight_arr[22] = extras
            return [flight_arr, [[None, price]]]

        payload = [None] * 12
        payload[2] = [[make_raw_flight("F8", "Flair Airlines", 138, 8, 14, 240, "Boeing 737MAX 8")]]
        payload[3] = [[make_raw_flight("PD", "Porter Airlines", 215, 9, 16, 330, "Embraer 195 E2", stops_via="YUL")]]
        payload[5] = [
            3,
            [None, 138],
            [None, 146],
            [None, 8],
            [None, 150],
            [None, 240],
            1,
            None,
            None,
            None,
            [[[1787032800000, 138], [1787119200000, 180], [1787205600000, 220]]],
        ]

        html = f'<html><head><script class="ds:1">AF_initDataCallback({{key: \'ds:1\', data:{json.dumps(payload)}, sideChannel: {{}}}});</script></head></html>'
        mock_fetch_html.return_value = html

        agg = GoogleFlightsPriceAggregator()
        report = agg.search_prices("flights from Calgary to Toronto on 2026-11-15")
        self.assertIn("GOOGLE FLIGHTS LIVE TICKET PRICES: CALGARY (YYC) -> TORONTO (YYZ)", report)
        self.assertIn("**$138 CAD** via **Flair Airlines**", report)
        self.assertIn("🟢 LOW", report)
        self.assertIn("60-Day Historical Trend", report)
        self.assertIn("Porter Airlines", report)
        self.assertIn("https://www.google.com/travel/flights/search?tfs=", report)

    @patch("src.skills.flights.aggregators.live_status.fetch_json")
    def test_live_flight_status_and_adsb_enrichment(self, mock_fetch_json):
        agg = LiveFlightStatusAggregator()
        mock_fr = MagicMock()
        mock_fr.search.return_value = {
            "live": [{
                "id": "42288817",
                "detail": {
                    "flight": "AC850",
                    "callsign": "ACA850",
                    "reg": "C-GKUG",
                    "schd_from": "YYC",
                    "schd_to": "LHR",
                    "ac_type": "A333",
                    "lat": 60.0,
                    "lon": -45.0,
                },
            }]
        }
        mock_fr.get_flight_details.return_value = {
            "identification": {"number": {"default": "AC850"}, "callsign": "ACA850"},
            "status": {"live": True, "text": "En Route - On Time"},
            "aircraft": {"model": {"text": "Airbus A330-343"}, "registration": "C-GKUG", "hex": "c01abc"},
            "airline": {"name": "Air Canada"},
            "airport": {
                "origin": {
                    "name": "Calgary International Airport",
                    "code": {"iata": "YYC"},
                    "position": {"latitude": 51.12, "longitude": -114.02, "region": {"city": "Calgary"}},
                    "timezone": {"name": "America/Edmonton", "abbr": "MDT"},
                    "info": {"terminal": "D", "gate": "D76"},
                },
                "destination": {
                    "name": "London Heathrow Airport",
                    "code": {"iata": "LHR"},
                    "position": {"latitude": 51.47, "longitude": -0.45, "region": {"city": "London"}},
                    "timezone": {"name": "Europe/London", "abbr": "GMT"},
                    "info": {"terminal": "2", "gate": "B34", "baggage": "9"},
                },
            },
            "time": {
                "scheduled": {"departure": 1792202100, "arrival": 1792234200},
                "real": {"departure": 1792202400, "arrival": None},
                "estimated": {"departure": None, "arrival": 1792234200},
            },
            "trail": [{"lat": 60.0, "lng": -45.0, "alt": 35000, "spd": 465, "hd": 92}],
        }
        agg._fr_api = mock_fr

        # Mock ADSB.lol response
        mock_fetch_json.return_value = {
            "ac": [{
                "hex": "c01abc",
                "flight": "ACA850 ",
                "r": "C-GKUG",
                "alt_baro": 37000,
                "gs": 482.4,
                "baro_rate": 64,
                "track": 94.2,
                "squawk": "2241",
                "lat": 60.5,
                "lon": -44.0,
            }]
        }

        status_out = agg.get_flight_status("AC850")
        self.assertIn("LIVE FLIGHT STATUS: Air Canada — AC850 (ACA850)", status_out)
        self.assertIn("EN ROUTE / AIRBORNE (LIVE RADAR)", status_out)
        self.assertIn("Gate D76", status_out)
        self.assertIn("Gate B34", status_out)
        self.assertIn("37,000 ft", status_out)
        self.assertIn("482 kts", status_out)
        self.assertIn("globe.adsb.lol/?icao=c01abc", status_out)

    def test_flight_tracker_state_transitions_and_price_alerts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_path = os.path.join(tmpdir, "watches.json")
            mgr = FlightTrackerManager(watch_file=watch_path)

            # 1. Test live flight state transitions (scheduled -> airborne -> delayed -> landed)
            tf = TrackedFlight(
                channel_id=101,
                flight_number="AC850",
                callsign="ACA850",
                airline="Air Canada",
                origin_code="YYC",
                dest_code="LHR",
                last_state="scheduled",
                last_origin_gate="D70",
            )

            # Gate change while scheduled
            alerts_gate, remove = mgr.diff_flight_snapshots(
                tf,
                {"state": "scheduled", "origin_gate": "D76", "delay_mins": 0},
            )
            self.assertFalse(remove)
            self.assertTrue(any("DEPARTURE GATE UPDATE" in a and "Gate D76" in a for a in alerts_gate))

            # Takeoff + 25 min delay
            alerts_takeoff, remove = mgr.diff_flight_snapshots(
                tf,
                {
                    "state": "airborne",
                    "est_dep_str": "6:50 PM MDT",
                    "est_arr_str": "10:45 AM BST",
                    "delay_mins": 25,
                },
            )
            self.assertFalse(remove)
            self.assertTrue(any("TAKEOFF ALERT" in a for a in alerts_takeoff))
            self.assertTrue(any("DELAY ALERT" in a and "+25 min" in a for a in alerts_takeoff))

            # Landing auto-removes flight
            alerts_land, remove = mgr.diff_flight_snapshots(
                tf,
                {
                    "state": "landed",
                    "est_arr_str": "10:42 AM BST",
                    "dest_terminal": "2",
                    "dest_gate": "B34",
                    "dest_baggage": "9",
                },
            )
            self.assertTrue(remove)
            self.assertTrue(any("LANDED" in a and "Gate B34" in a for a in alerts_land))

            # 2. Test price drop evaluation (target hit and >= $25 drop)
            pw = TrackedPriceWatch(
                channel_id=101,
                origin_code="YYC",
                origin_city="Calgary",
                dest_code="HND",
                dest_city="Tokyo",
                date="2026-11-15",
                baseline_price=980,
                lowest_seen_price=980,
                target_price=900,
                best_airline="WestJet",
                booking_url="https://www.google.com/travel/flights",
            )

            # Minor drop ($980 -> $965) should not trigger alert yet
            no_alert = mgr.evaluate_price_update(
                pw,
                {"lowest_overall": {"price": 965, "airlines": "WestJet", "stops_label": "Nonstop", "duration_str": "10h 30m"}},
            )
            self.assertIsNone(no_alert)
            self.assertEqual(pw.lowest_seen_price, 965)

            # Drop below target ($885 <= $900) triggers target alert
            hit_alert = mgr.evaluate_price_update(
                pw,
                {"lowest_overall": {"price": 885, "airlines": "WestJet", "stops_label": "Nonstop", "duration_str": "10h 30m"}},
            )
            self.assertIsNotNone(hit_alert)
            self.assertIn("FLIGHT PRICE TARGET HIT", hit_alert)
            self.assertIn("$885 CAD", hit_alert)

            # Calling again at same price ($885) does not spam duplicate alert
            dup_alert = mgr.evaluate_price_update(
                pw,
                {"lowest_overall": {"price": 885, "airlines": "WestJet", "stops_label": "Nonstop", "duration_str": "10h 30m"}},
            )
            self.assertIsNone(dup_alert)

    @patch.object(FLIGHT_TRACKER._price_agg, "fetch_structured_fares")
    @patch.object(FLIGHT_TRACKER._status_agg, "fetch_flight_snapshot")
    def test_skill_track_flight_end_to_end(self, mock_snap, mock_fares):
        set_active_channel_id(555)
        FLIGHT_TRACKER.clear_channel(555)

        mock_snap.return_value = {
            "flight_number": "AC850",
            "callsign": "ACA850",
            "flight_id": "42288817",
            "airline": "Air Canada",
            "aircraft_model": "Airbus A330",
            "registration": "C-GKUG",
            "icao_hex": "c01abc",
            "state": "scheduled",
            "is_live": False,
            "status_text": "Scheduled",
            "origin_code": "YYC",
            "origin_name": "Calgary Intl",
            "origin_city": "Calgary",
            "origin_tz": "America/Edmonton",
            "origin_tz_abbr": "MDT",
            "origin_terminal": "D",
            "origin_gate": "D76",
            "dest_code": "LHR",
            "dest_name": "London Heathrow",
            "dest_city": "London",
            "dest_tz": "Europe/London",
            "dest_tz_abbr": "GMT",
            "dest_terminal": "2",
            "dest_gate": None,
            "dest_baggage": None,
            "sched_dep_ts": 1792202100,
            "est_dep_ts": 1792202100,
            "sched_arr_ts": 1792234200,
            "est_arr_ts": 1792234200,
            "sched_dep_str": "Sat Oct 17, 6:35 PM MDT",
            "est_dep_str": "Sat Oct 17, 6:35 PM MDT",
            "sched_arr_str": "Sun Oct 18, 10:30 AM GMT",
            "est_arr_str": "Sun Oct 18, 10:30 AM GMT",
            "delay_mins": 0,
            "altitude_ft": None,
            "speed_kts": None,
            "vertical_rate_fpm": None,
            "heading_deg": None,
            "squawk": None,
            "lat": None,
            "lon": None,
            "remaining_km": None,
            "pct_complete": None,
            "fr24_url": "https://www.flightradar24.com/ACA850/42288817",
            "adsb_url": None,
        }
        mock_fares.return_value = {
            "origin": "YYC",
            "destination": "HND",
            "date": "2026-11-15",
            "return_date": None,
            "trip_type": "one-way",
            "cabin": "economy",
            "currency": "CAD",
            "booking_url": "https://www.google.com/travel/flights",
            "lowest_overall": {
                "price": 820,
                "currency": "CAD",
                "airlines": "WestJet",
                "stops": 0,
                "stops_label": "Nonstop",
                "from_code": "YYC",
                "to_code": "HND",
                "departure_time": "1:00 PM",
                "arrival_time": "4:15 PM (+1d)",
                "duration_mins": 675,
                "duration_str": "11h 15m",
                "flight_mins": 675,
                "aircraft": "Boeing 787-9",
            },
            "lowest_nonstop": None,
            "verdict": "🟢 LOW",
            "verdict_detail": "Prices are currently LOW",
            "insights": {},
            "options": [],
        }

        try:
            res_f = skill_track_flight(action="track", query="track flight AC850")
            self.assertIn("NOW TRACKING LIVE FLIGHT: AC850", res_f)

            res_p = skill_track_flight(action="track", query="Calgary to Tokyo on 2026-11-15 under $750")
            self.assertIn("PRICE WATCH CREATED: Calgary (YYC) -> Tokyo (HND)", res_p)
            self.assertIn("$750 CAD", res_p)

            res_list = skill_track_flight(action="list")
            self.assertIn("AC850", res_list)
            self.assertIn("Calgary (YYC) -> Tokyo (HND)", res_list)

            res_untrack = skill_track_flight(action="untrack", query="AC850")
            self.assertIn("Stopped tracking: Live Flight **AC850**", res_untrack)
        finally:
            FLIGHT_TRACKER.clear_channel(555)

    def test_multi_date_route_memory_and_horizon_fallback(self):
        set_active_channel_id(777)
        # 1. Zagreb resolves from fast directory
        zag = resolve_airport("Zagreb")
        self.assertIsNotNone(zag)
        self.assertEqual(zag[0], "ZAG")

        # 2. Initial query records route in channel memory
        r1 = parse_route_from_query("flight from edmonton to zagreb one way dec 12")
        self.assertEqual(r1["origin"][0], "YEG")
        self.assertEqual(r1["destination"][0], "ZAG")
        self.assertIsNone(r1["return_date"])

        # 3. Follow-up date-only query inherits YEG -> ZAG and parses both outbound & return dates
        r2 = parse_route_from_query("how about May 20, 2027 with return trip August 27, 2027")
        self.assertEqual(r2["origin"][0], "YEG")
        self.assertEqual(r2["destination"][0], "ZAG")
        self.assertEqual(r2["date"], "2027-05-20")
        self.assertEqual(r2["return_date"], "2027-08-27")

        # 4. Horizon query sets wants_latest_horizon=True
        r3 = parse_route_from_query("whats the latest flight in 2027 you have data for from Edmonton to Zagreb")
        self.assertTrue(r3["wants_latest_horizon"])

        # 5. Test search_prices schedule horizon + nearby hub fallback when requested date has 0 flights
        agg = GoogleFlightsPriceAggregator()
        sample_opt = {
            "price": 1300,
            "currency": "CAD",
            "airlines": "Alaska, Condor, Croatia",
            "stops": 2,
            "stops_label": "2 stops (SEA, FRA)",
            "from_code": "YYC",
            "to_code": "ZAG",
            "departure_time": "8:10 AM",
            "arrival_time": "7:20 PM (+1d)",
            "duration_mins": 1630,
            "duration_str": "27h 10m",
            "flight_mins": 1100,
            "aircraft": "Boeing 737",
        }
        horizon_opt = dict(sample_opt, price=1328, airlines="Air Canada, Austrian, Croatia", from_code="YEG")

        def fake_fetch(origin_code, dest_code, date, return_date=None, cabin="economy", currency="CAD", max_stops=None, sort_preference="best"):
            if origin_code == "YEG" and date == "2027-05-20":
                return {
                    "origin": "YEG",
                    "destination": "ZAG",
                    "date": date,
                    "return_date": return_date,
                    "trip_type": "round-trip" if return_date else "one-way",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": None,
                    "lowest_nonstop": None,
                    "verdict": None,
                    "verdict_detail": None,
                    "insights": {},
                    "options": [],
                }
            if origin_code == "YYC" and date == "2027-05-20" and return_date is None:
                return {
                    "origin": "YYC",
                    "destination": "ZAG",
                    "date": date,
                    "return_date": None,
                    "trip_type": "one-way",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": sample_opt,
                    "lowest_nonstop": None,
                    "verdict": "🟡 TYPICAL",
                    "verdict_detail": "Prices are TYPICAL",
                    "insights": {},
                    "options": [sample_opt],
                }
            return {
                "origin": origin_code,
                "destination": dest_code,
                "date": date,
                "return_date": return_date,
                "trip_type": "one-way",
                "cabin": cabin,
                "currency": currency,
                "booking_url": "https://www.google.com/travel/flights",
                "lowest_overall": None,
                "lowest_nonstop": None,
                "verdict": None,
                "verdict_detail": None,
                "insights": {},
                "options": [],
            }

        with patch.object(agg, "fetch_structured_fares", side_effect=fake_fetch), patch.object(
            agg,
            "find_latest_available_date",
            return_value={
                "origin": "YEG",
                "destination": "ZAG",
                "date": "2027-05-03",
                "return_date": None,
                "trip_type": "one-way",
                "cabin": "economy",
                "currency": "CAD",
                "booking_url": "https://www.google.com/travel/flights",
                "lowest_overall": horizon_opt,
                "lowest_nonstop": None,
                "verdict": "🟡 TYPICAL",
                "verdict_detail": "Prices are TYPICAL",
                "insights": {},
                "options": [horizon_opt],
            },
        ):
            out = agg.search_prices(query="how about May 20, 2027 with return trip August 27, 2027")
            self.assertIn("Why 2027-05-20 Returned No Flights", out)
            self.assertIn("2027-05-03", out)
            self.assertIn("$1328 CAD", out)
            self.assertIn("NEARBY MAJOR HUB ALTERNATIVE", out)
            self.assertIn("CALGARY (YYC) -> ZAGREB (ZAG)", out)
            self.assertIn("$1300 CAD", out)

    def test_multi_carrier_round_trip_two_leg_combination(self):
        set_active_channel_id(888)
        parsed = parse_route_from_query("dus to yeg nov 21, 2026 to Dec 12, 2026")
        self.assertEqual(parsed["origin"][0], "DUS")
        self.assertEqual(parsed["destination"][0], "YEG")
        self.assertEqual(parsed["date"], "2026-11-21")
        self.assertEqual(parsed["return_date"], "2026-12-12")

        # Also verify when LLM passes both dates inside `date=` argument without `return_date=`
        parsed_combined_date_arg = parse_route_from_query(
            query="",
            origin="DUS",
            destination="YEG",
            date="Nov 21, 2026 to Dec 12, 2026",
        )
        self.assertEqual(parsed_combined_date_arg["date"], "2026-11-21")
        self.assertEqual(parsed_combined_date_arg["return_date"], "2026-12-12")

        agg = GoogleFlightsPriceAggregator()
        out_opt = {
            "price": 1298,
            "currency": "CAD",
            "airlines": "Air France, WestJet",
            "stops": 2,
            "stops_label": "2 stops (CDG, YYC)",
            "from_code": "DUS",
            "to_code": "YEG",
            "departure_time": "6:20 AM",
            "arrival_time": "5:05 PM",
            "duration_mins": 1125,
            "duration_str": "18h 45m",
            "flight_mins": 950,
            "aircraft": "Boeing 787-9",
        }
        ret_opt = {
            "price": 895,
            "currency": "CAD",
            "airlines": "Porter Airlines, British Airways",
            "stops": 2,
            "stops_label": "2 stops (YYZ, LHR)",
            "from_code": "YEG",
            "to_code": "DUS",
            "departure_time": "1:30 PM",
            "arrival_time": "2:50 PM (+1d)",
            "duration_mins": 1100,
            "duration_str": "18h 20m",
            "flight_mins": 850,
            "aircraft": "Airbus A350",
        }

        def fake_fetch(origin_code, dest_code, date, return_date=None, cabin="economy", currency="CAD", max_stops=None, sort_preference="best"):
            if return_date:
                # Simulate empty single-ticket SSR round-trip table on multi-carrier route
                return {
                    "origin": origin_code,
                    "destination": dest_code,
                    "date": date,
                    "return_date": return_date,
                    "trip_type": "round-trip",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": None,
                    "lowest_nonstop": None,
                    "verdict": None,
                    "verdict_detail": None,
                    "insights": {},
                    "options": [],
                }
            if origin_code == "DUS" and dest_code == "YEG":
                return {
                    "origin": "DUS",
                    "destination": "YEG",
                    "date": date,
                    "return_date": None,
                    "trip_type": "one-way",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": out_opt,
                    "lowest_reasonable": out_opt,
                    "best_overall": out_opt,
                    "lowest_nonstop": None,
                    "verdict": "🟡 TYPICAL",
                    "verdict_detail": "Prices are TYPICAL",
                    "insights": {},
                    "options": [out_opt],
                }
            return {
                "origin": "YEG",
                "destination": "DUS",
                "date": date,
                "return_date": None,
                "trip_type": "one-way",
                "cabin": cabin,
                "currency": currency,
                "booking_url": "https://www.google.com/travel/flights",
                "lowest_overall": ret_opt,
                "lowest_reasonable": ret_opt,
                "best_overall": ret_opt,
                "lowest_nonstop": None,
                "verdict": "🟢 LOW",
                "verdict_detail": "Prices are LOW",
                "insights": {},
                "options": [ret_opt],
            }

        with patch.object(agg, "fetch_structured_fares", side_effect=fake_fetch):
            out = agg.search_prices(query="dus to yeg nov 21, 2026 to Dec 12, 2026")
            self.assertIn("🏆 **Best Recommended Round-Trip Total**: **$2193 CAD Total**", out)
            self.assertIn("OUTBOUND LEG: DÜSSELDORF (DUS) -> EDMONTON (YEG) (2026-11-21)", out)
            self.assertIn("RETURN LEG: EDMONTON (YEG) -> DÜSSELDORF (DUS) (2026-12-12)", out)
            self.assertIn("$1298 CAD", out)
            self.assertIn("$895 CAD", out)

    def test_anti_marathon_filter_and_best_vs_shortest_vs_cheapest_scoring(self):
        nonstop_klm = {
            "price": 1500,
            "currency": "CAD",
            "airlines": "KLM",
            "stops": 0,
            "stops_label": "Nonstop",
            "duration_mins": 535,  # 8h 55m
            "duration_str": "8h 55m",
            "max_layover_mins": 0,
            "is_best_flight": True,
            "best_rank": 1,
        }
        reasonable_united = {
            "price": 693,
            "currency": "CAD",
            "airlines": "United",
            "stops": 1,
            "stops_label": "1 stop (IAH [3h 10m])",
            "duration_mins": 997,  # 16h 37m
            "duration_str": "16h 37m",
            "max_layover_mins": 190,
            "is_best_flight": True,
            "best_rank": 0,
        }
        marathon_layover = {
            "price": 663,
            "currency": "CAD",
            "airlines": "WestJet, KLM",
            "stops": 2,
            "stops_label": "2 stops (YYZ [16h 55m], AMS)",
            "duration_mins": 2070,  # 34h 30m (>22h and >1.8x fastest, plus >10h layover)
            "duration_str": "34h 30m",
            "max_layover_mins": 1015,
            "is_best_flight": False,
            "best_rank": 999,
        }

        # 1. Default "best" sort preference
        classified_best = GoogleFlightsPriceAggregator._score_and_classify_itineraries(
            [marathon_layover, reasonable_united, nonstop_klm],
            sort_preference="best",
        )
        # Marathon 34h 30m flight is filtered into filtered_marathon and excluded from lowest_reasonable!
        self.assertIsNotNone(classified_best["filtered_marathon"])
        self.assertEqual(classified_best["filtered_marathon"]["price"], 663)
        self.assertEqual(classified_best["lowest_reasonable"]["price"], 693)
        self.assertEqual(classified_best["best_overall"]["price"], 693)
        self.assertEqual(classified_best["lowest_nonstop"]["price"], 1500)
        self.assertEqual(classified_best["fastest_overall"]["duration_mins"], 535)

        # 2. "shortest" sort preference ranks the 8h 55m KLM Nonstop #1
        classified_shortest = GoogleFlightsPriceAggregator._score_and_classify_itineraries(
            [marathon_layover, reasonable_united, nonstop_klm],
            sort_preference="shortest",
        )
        self.assertEqual(classified_shortest["sorted_options"][0]["airlines"], "KLM")
        self.assertEqual(classified_shortest["sorted_options"][0]["duration_mins"], 535)

    def test_flexible_date_and_direct_schedule_scanner(self):
        set_active_channel_id(999)
        # 1. Verify route parser detects flexible scan & direct schedule intent
        p_direct = parse_route_from_query(
            "when are the direct flights from Edmonton to Amsterdam in November and when is the best day to fly?"
        )
        self.assertEqual(p_direct["origin"][0], "YEG")
        self.assertEqual(p_direct["destination"][0], "AMS")
        self.assertTrue(p_direct["wants_direct_schedule"])
        self.assertTrue(p_direct["wants_flexible_scan"])
        self.assertEqual(p_direct["sort_preference"], "best")

        p_shortest = parse_route_from_query("what is the shortest flight from Edmonton to Zagreb in November?")
        self.assertEqual(p_shortest["origin"][0], "YEG")
        self.assertEqual(p_shortest["destination"][0], "ZAG")
        self.assertEqual(p_shortest["sort_preference"], "shortest")
        self.assertTrue(p_shortest["wants_flexible_scan"])

        # 2. Test scan_flexible_dates_and_direct_schedule with synthetic daily fares
        agg = GoogleFlightsPriceAggregator()

        def fake_daily_fetch(origin_code, dest_code, date, return_date=None, cabin="economy", currency="CAD", max_stops=None, sort_preference="best"):
            dt = datetime.strptime(date, "%Y-%m-%d")
            # Simulate KLM nonstop operating on Tuesdays (weekday=1) and Thursdays (weekday=3)
            has_direct = dt.weekday() in (1, 3)
            ns_opt = {
                "price": 1120 if dt.weekday() == 1 else 1190,
                "currency": currency,
                "airlines": "KLM",
                "stops": 0,
                "stops_label": "Nonstop",
                "from_code": origin_code,
                "to_code": dest_code,
                "departure_time": "5:45 PM",
                "arrival_time": "10:40 AM (+1d)",
                "duration_mins": 535,
                "duration_str": "8h 55m",
                "flight_mins": 535,
                "aircraft": "Airbus A330-200",
                "value_score": 1150.0,
            } if has_direct else None

            one_stop = {
                "price": 690 if dt.weekday() == 2 else 740,
                "currency": currency,
                "airlines": "United",
                "stops": 1,
                "stops_label": "1 stop (ORD)",
                "from_code": origin_code,
                "to_code": dest_code,
                "departure_time": "7:00 AM",
                "arrival_time": "8:30 AM (+1d)",
                "duration_mins": 990,
                "duration_str": "16h 30m",
                "flight_mins": 810,
                "aircraft": "Boeing 787",
                "value_score": 880.0 if dt.weekday() == 2 else 930.0,
            }
            best = ns_opt if (sort_preference == "shortest" and ns_opt) else one_stop
            fastest = ns_opt if ns_opt else one_stop
            return {
                "origin": origin_code,
                "destination": dest_code,
                "date": date,
                "return_date": return_date,
                "trip_type": "one-way",
                "cabin": cabin,
                "currency": currency,
                "booking_url": f"https://www.google.com/travel/flights?d={date}",
                "best_overall": best,
                "lowest_reasonable": one_stop,
                "lowest_overall": one_stop,
                "lowest_nonstop": ns_opt,
                "fastest_overall": fastest,
                "filtered_marathon": None,
                "options": [o for o in (ns_opt, one_stop) if o],
            }

        with patch.object(agg, "fetch_structured_fares", side_effect=fake_daily_fetch):
            scan_out = agg.scan_flexible_dates_and_direct_schedule(
                orig_info=("YEG", "Edmonton International Airport", "Edmonton", "Canada"),
                dest_info=("AMS", "Amsterdam Airport Schiphol", "Amsterdam", "Netherlands"),
                start_date="2026-11-01",
                end_date="2026-11-07",
                window_label="Nov 1 - Nov 7, 2026",
                cabin="economy",
                currency="CAD",
                sort_preference="best",
            )
            self.assertIn("FLEXIBLE DATE & DIRECT FLIGHT SCHEDULE SCANNER", scan_out)
            self.assertIn("Direct Flight Operating Days**: **Tuesday, Thursday**", scan_out)
            self.assertIn("KLM", scan_out)
            self.assertIn("$1120 CAD", scan_out)
            self.assertIn("TOP 5 BEST-VALUE DAYS TO FLY", scan_out)
            self.assertIn("DAY-BY-DAY PRICE & DIRECT FLIGHT MATRIX", scan_out)

    def test_quality_filtered_and_direct_only_price_watch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            watch_path = os.path.join(tmpdir, "watches.json")
            mgr = FlightTrackerManager(watch_file=watch_path)

            # 1. Standard quality-filtered watch ignores raw 34h marathon fare if lowest_reasonable is $950
            pw_quality = TrackedPriceWatch(
                channel_id=202,
                origin_code="DUS",
                origin_city="Düsseldorf",
                dest_code="YEG",
                dest_city="Edmonton",
                date="2026-11-21",
                baseline_price=950,
                lowest_seen_price=950,
                target_price=900,
                best_airline="Air France",
                direct_only=False,
            )
            no_alert = mgr.evaluate_price_update(
                pw_quality,
                {
                    "lowest_reasonable": {"price": 945, "airlines": "Air France", "stops_label": "1 stop", "duration_str": "16h 40m"},
                    "lowest_overall": {"price": 945, "airlines": "Air France", "stops_label": "1 stop", "duration_str": "16h 40m"},
                },
            )
            self.assertIsNone(no_alert)

            # 2. Direct-only watch only triggers when lowest_nonstop drops
            pw_direct = TrackedPriceWatch(
                channel_id=202,
                origin_code="YEG",
                origin_city="Edmonton",
                dest_code="AMS",
                dest_city="Amsterdam",
                date="2026-11-03",
                baseline_price=1200,
                lowest_seen_price=1200,
                target_price=1050,
                best_airline="KLM",
                direct_only=True,
            )
            # 1-stop is $690, but lowest_nonstop is $1190 -> should NOT trigger target $1050
            self.assertIsNone(
                mgr.evaluate_price_update(
                    pw_direct,
                    {
                        "lowest_reasonable": {"price": 690, "airlines": "United", "stops_label": "1 stop", "duration_str": "16h 30m"},
                        "lowest_nonstop": {"price": 1190, "airlines": "KLM", "stops_label": "Nonstop", "duration_str": "8h 55m"},
                    },
                )
            )
            # Now lowest_nonstop drops to $995 -> triggers alert!
            direct_alert = mgr.evaluate_price_update(
                pw_direct,
                {
                    "lowest_reasonable": {"price": 690, "airlines": "United", "stops_label": "1 stop", "duration_str": "16h 30m"},
                    "lowest_nonstop": {"price": 995, "airlines": "KLM", "stops_label": "Nonstop", "duration_str": "8h 55m"},
                },
            )
            self.assertIsNotNone(direct_alert)
            self.assertIn("$995 CAD", direct_alert)
            self.assertIn("KLM", direct_alert)

    def test_multi_month_range_1stop_followup_and_gateway_verification(self):
        from src.skills.flights.airports import clear_channel_flight_state
        set_active_channel_id(909)
        clear_channel_flight_state(909)

        # Turn 1: User asks "planning trip from Edmonton to Zagreb from May to August next year. What are best flight options?"
        p1 = parse_route_from_query(
            "planning trip from Edmonton to Zagreb from May to August next year. What are best flight options?",
            origin="Edmonton",
            destination="Zagreb",
            date="2027-05-01",
            return_date="2027-08-01",
        )
        self.assertEqual(p1["origin"][0], "YEG")
        self.assertEqual(p1["destination"][0], "ZAG")
        self.assertTrue(p1["wants_flexible_scan"])
        self.assertEqual(p1["trip_type"], "round-trip")
        self.assertEqual(p1["date"], "2027-05-04")
        self.assertEqual(p1["return_date"], "2027-08-10")
        self.assertEqual(p1["flex_label"], "May 2027 – August 2027")

        # Turn 2: Short constraint follow-up "1 stop only" preserves May–August round-trip and sets max_stops=1
        p2 = parse_route_from_query("1 stop only")
        self.assertEqual(p2["origin"][0], "YEG")
        self.assertEqual(p2["destination"][0], "ZAG")
        self.assertEqual(p2["max_stops"], 1)
        self.assertEqual(p2["trip_type"], "round-trip")
        self.assertEqual(p2["date"], "2027-05-04")
        self.assertEqual(p2["return_date"], "2027-08-10")
        self.assertTrue(p2["wants_flexible_scan"])
        self.assertEqual(p2["flex_label"], "May 2027 – August 2027")

        # Turn 3: Return-month follow-up "i need round trip back in August" preserves May outbound AND max_stops=1
        p3 = parse_route_from_query("i need round trip back in August")
        self.assertEqual(p3["origin"][0], "YEG")
        self.assertEqual(p3["destination"][0], "ZAG")
        self.assertEqual(p3["max_stops"], 1)
        self.assertEqual(p3["trip_type"], "round-trip")
        self.assertEqual(p3["date"], "2027-05-04")
        self.assertEqual(p3["return_date"], "2027-08-10")
        self.assertEqual(p3["flex_label"], "May 2027 – August 2027")

        # Turn 4 & 5: !reset clears state, and "Leaving May 2027 returning August 2027. 1 stop only." parses both months + 1 stop
        clear_channel_flight_state(909)
        p5 = parse_route_from_query(
            "best flights Edmonton to Zagreb with return trip. Leaving May 2027 returning August 2027. 1 stop only.",
            origin="Edmonton",
            destination="Zagreb",
            date="2027-05-01",
            return_date="2027-08-01",
        )
        self.assertEqual(p5["origin"][0], "YEG")
        self.assertEqual(p5["destination"][0], "ZAG")
        self.assertEqual(p5["max_stops"], 1)
        self.assertEqual(p5["trip_type"], "round-trip")
        self.assertEqual(p5["date"], "2027-05-04")
        self.assertEqual(p5["return_date"], "2027-08-10")
        self.assertEqual(p5["flex_label"], "May 2027 – August 2027")

        # Verify sparse >6-month window strictly filters out 2-stop flights when max_stops=1,
        # surfaces the Round-Trip 6-month benchmark fare, and verifies both Outbound & Return 1-stop gateway schedules via AMS
        agg = GoogleFlightsPriceAggregator()
        stray_2stop = {
            "price": 2496,
            "currency": "CAD",
            "airlines": "Air Canada, Croatia",
            "stops": 2,
            "stops_label": "2 stops (YYZ, VIE)",
            "from_code": "YEG",
            "to_code": "ZAG",
            "departure_time": "9:10 AM",
            "arrival_time": "10:40 AM (+1d)",
            "duration_mins": 1050,
            "duration_str": "17h 30m",
            "flight_mins": 780,
            "aircraft": "Boeing 787",
            "is_marathon": False,
        }
        klm_1stop_horizon = {
            "price": 1329,
            "currency": "CAD",
            "airlines": "KLM, Croatia",
            "stops": 1,
            "stops_label": "1 stop (AMS)",
            "from_code": "YEG",
            "to_code": "ZAG",
            "departure_time": "5:35 PM",
            "arrival_time": "1:15 PM (+1d)",
            "duration_mins": 700,
            "duration_str": "11h 40m",
            "flight_mins": 630,
            "aircraft": "Airbus A330, Airbus A220",
            "is_marathon": False,
        }
        klm_1stop_rt_benchmark = dict(klm_1stop_horizon, price=1695)
        klm_leg1 = {
            "price": 1962,
            "currency": "CAD",
            "airlines": "KLM",
            "stops": 0,
            "stops_label": "Nonstop",
            "from_code": "YEG",
            "to_code": "AMS",
            "departure_time": "5:35 PM",
            "arrival_time": "10:10 AM (+1d)",
            "duration_mins": 515,
            "duration_str": "8h 35m",
            "flight_mins": 515,
            "aircraft": "Airbus A330",
            "is_marathon": False,
        }
        klm_leg1_rt = dict(klm_leg1, price=1368)
        ou_leg2 = {
            "price": 352,
            "currency": "CAD",
            "airlines": "Croatia Airlines",
            "stops": 0,
            "stops_label": "Nonstop",
            "from_code": "AMS",
            "to_code": "ZAG",
            "departure_time": "11:20 AM",
            "arrival_time": "1:15 PM",
            "duration_mins": 115,
            "duration_str": "1h 55m",
            "flight_mins": 115,
            "aircraft": "Airbus A220",
            "is_marathon": False,
        }
        klm_ret_leg2 = {
            "price": 2605,
            "currency": "CAD",
            "airlines": "KLM",
            "stops": 0,
            "stops_label": "Nonstop",
            "from_code": "AMS",
            "to_code": "YEG",
            "departure_time": "2:55 PM",
            "arrival_time": "3:45 PM",
            "duration_mins": 530,
            "duration_str": "8h 50m",
            "flight_mins": 530,
            "aircraft": "Airbus A330",
            "is_marathon": False,
        }

        def fake_fetch(origin_code, dest_code, date, return_date=None, cabin="economy", currency="CAD", max_stops=None, sort_preference="best"):
            if origin_code == "YEG" and dest_code == "AMS":
                opt = klm_leg1_rt if return_date else klm_leg1
                return {
                    "origin": "YEG",
                    "destination": "AMS",
                    "date": date,
                    "return_date": return_date,
                    "trip_type": "round-trip" if return_date else "one-way",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": opt,
                    "lowest_reasonable": opt,
                    "best_overall": opt,
                    "fastest_overall": opt,
                    "lowest_nonstop": opt,
                    "options": [opt],
                }
            if origin_code == "AMS" and dest_code == "ZAG":
                return {
                    "origin": "AMS",
                    "destination": "ZAG",
                    "date": date,
                    "return_date": None,
                    "trip_type": "one-way",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": ou_leg2,
                    "lowest_reasonable": ou_leg2,
                    "best_overall": ou_leg2,
                    "fastest_overall": ou_leg2,
                    "lowest_nonstop": ou_leg2,
                    "options": [ou_leg2],
                }
            if origin_code == "AMS" and dest_code == "YEG":
                return {
                    "origin": "AMS",
                    "destination": "YEG",
                    "date": date,
                    "return_date": None,
                    "trip_type": "one-way",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": klm_ret_leg2,
                    "lowest_reasonable": klm_ret_leg2,
                    "best_overall": klm_ret_leg2,
                    "fastest_overall": klm_ret_leg2,
                    "lowest_nonstop": klm_ret_leg2,
                    "options": [klm_ret_leg2],
                }
            if origin_code == "YEG" and dest_code == "ZAG" and return_date == "2027-04-17":
                return {
                    "origin": "YEG",
                    "destination": "ZAG",
                    "date": date,
                    "return_date": return_date,
                    "trip_type": "round-trip",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": klm_1stop_rt_benchmark,
                    "lowest_reasonable": klm_1stop_rt_benchmark,
                    "best_overall": klm_1stop_rt_benchmark,
                    "fastest_overall": klm_1stop_rt_benchmark,
                    "lowest_nonstop": None,
                    "options": [klm_1stop_rt_benchmark],
                }
            if origin_code == "YEG" and dest_code == "ZAG" and date == "2027-05-02":
                return {
                    "origin": "YEG",
                    "destination": "ZAG",
                    "date": date,
                    "return_date": None,
                    "trip_type": "one-way",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": stray_2stop,
                    "lowest_reasonable": stray_2stop,
                    "best_overall": stray_2stop,
                    "fastest_overall": stray_2stop,
                    "lowest_nonstop": None,
                    "options": [stray_2stop],
                }
            return {
                "origin": origin_code,
                "destination": dest_code,
                "date": date,
                "return_date": return_date,
                "trip_type": "round-trip" if return_date else "one-way",
                "cabin": cabin,
                "currency": currency,
                "booking_url": "https://www.google.com/travel/flights",
                "lowest_overall": None,
                "lowest_reasonable": None,
                "best_overall": None,
                "fastest_overall": None,
                "lowest_nonstop": None,
                "options": [],
            }

        with (
            patch.object(agg, "fetch_structured_fares", side_effect=fake_fetch),
            patch.object(
                agg,
                "find_latest_available_date",
                return_value={
                    "origin": "YEG",
                    "destination": "ZAG",
                    "date": "2027-04-17",
                    "return_date": None,
                    "trip_type": "one-way",
                    "cabin": "economy",
                    "currency": "CAD",
                    "booking_url": "https://www.google.com/travel/flights",
                    "lowest_overall": klm_1stop_horizon,
                    "lowest_reasonable": klm_1stop_horizon,
                    "best_overall": klm_1stop_horizon,
                    "fastest_overall": klm_1stop_horizon,
                    "lowest_nonstop": None,
                    "options": [klm_1stop_horizon],
                },
            ),
        ):
            out = agg.search_prices("1 stop only")
            self.assertIn("Filter**: Max 1 Stop(s)", out)
            # Stray 2-stop flights must NEVER appear when max_stops == 1
            self.assertNotIn("2496", out)
            self.assertNotIn("2 stops (YYZ, VIE)", out)
            self.assertIn("No Combined 1-Stop Fares Returned in", out)
            self.assertIn("CONFIRMED 1-STOP GATEWAY SCHEDULE VIA AMSTERDAM (AMS): YEG -> AMS -> ZAG", out)
            self.assertIn("Outbound Leg 1 (Nonstop Edmonton YEG -> Amsterdam AMS", out)
            self.assertIn("Round-Trip YEG<->AMS nonstop base fare **$1368 CAD**", out)
            self.assertIn("Croatia Airlines** Nonstop", out)
            self.assertIn("Concrete Recommended 1-Stop Travel Dates in Your Window", out)
            self.assertIn("Return 1-Stop Schedule (Zagreb ZAG -> Amsterdam AMS -> Edmonton YEG around 2027-08-10)", out)
            self.assertIn("Return Segment 2 (Nonstop Amsterdam AMS -> Edmonton YEG on 2027-08-10)**: **KLM** Nonstop", out)
            self.assertIn("Published 1-Stop Round-Trip Benchmark (YEG <-> ZAG)**: **$1695 CAD Round-Trip** via **KLM, Croatia**", out)

    def test_december_three_weeks_round_trip_and_paired_dates(self):
        from src.skills.flights.airports import extract_flight_numbers, parse_natural_date

        set_active_channel_id(903)
        # 1. Duration phrases ("for 3 weeks", "max 1 stop") must never be extracted as flight numbers
        self.assertEqual(
            extract_flight_numbers("ok give me best flight from Edmonton to Zagreb in december for 3 weeks"),
            [],
        )
        # 2. Invalid calendar dates like 2027-12-40 must be rejected
        self.assertIsNone(parse_natural_date("2027-12-40"))

        # 3. Prior turns set May-August 2027 + max_stops=1
        parse_route_from_query("planning trip from Edmonton to Zagreb from May to August next year")
        parse_route_from_query("1 stop only")

        # 4. Turn 4: "in december for 3 weeks" must resolve to December 2026 (21-day Round-Trip)
        #    even if the LLM hallucinated date="2027-12-01" in tool args, while preserving max_stops=1
        r4 = parse_route_from_query(
            "ok give me best flight from Edmonton to Zagreb in december for 3 weeks",
            origin="Edmonton",
            destination="Zagreb",
            date="2027-12-01",
        )
        self.assertEqual(r4["origin"][0], "YEG")
        self.assertEqual(r4["destination"][0], "ZAG")
        self.assertEqual(r4["dep_month_window"], ("2026-12-01", "2026-12-21", "December 2026"))
        self.assertEqual(r4["trip_days"], 21)
        self.assertEqual(r4["trip_type"], "round-trip")
        self.assertEqual(r4["date"], "2026-12-08")
        self.assertEqual(r4["return_date"], "2026-12-29")
        self.assertEqual(r4["max_stops"], 1)
        self.assertTrue(r4["wants_flexible_scan"])

        agg = GoogleFlightsPriceAggregator()
        dec_1stop = {
            "price": 1671,
            "currency": "CAD",
            "airlines": "KLM, Croatia",
            "stops": 1,
            "stops_label": "1 stop (AMS)",
            "from_code": "YEG",
            "to_code": "ZAG",
            "departure_time": "6:05 PM",
            "arrival_time": "1:15 PM (+1d)",
            "duration_mins": 730,
            "duration_str": "12h 10m",
            "flight_mins": 640,
            "aircraft": "Airbus A330-200",
        }

        def fake_dec_fetch(origin_code, dest_code, date, return_date=None, cabin="economy", currency="CAD", max_stops=None, *args, **kwargs):
            if date == "2026-12-01":
                return {
                    "origin": origin_code,
                    "destination": dest_code,
                    "date": date,
                    "return_date": return_date,
                    "trip_type": "round-trip",
                    "cabin": cabin,
                    "currency": currency,
                    "booking_url": f"https://www.google.com/travel/flights?d={date}&r={return_date}",
                    "lowest_overall": dec_1stop,
                    "lowest_reasonable": dec_1stop,
                    "best_overall": dec_1stop,
                    "fastest_overall": dec_1stop,
                    "lowest_nonstop": None,
                    "options": [dec_1stop],
                }
            return {
                "origin": origin_code,
                "destination": dest_code,
                "date": date,
                "return_date": return_date,
                "trip_type": "round-trip",
                "cabin": cabin,
                "currency": currency,
                "booking_url": "https://www.google.com/travel/flights",
                "lowest_overall": None,
                "lowest_reasonable": None,
                "best_overall": None,
                "fastest_overall": None,
                "lowest_nonstop": None,
                "options": [],
            }

        with patch.object(agg, "fetch_structured_fares", side_effect=fake_dec_fetch):
            out = agg.search_prices("ok give me best flight from Edmonton to Zagreb in december for 3 weeks")
            self.assertIn("December 2026 (~21-Day Round-Trip)", out)
            self.assertIn("Tuesday, 2026-12-01 ↔ Return Tue 2026-12-22", out)
            self.assertIn("$1671 CAD Round-Trip", out)


if __name__ == "__main__":
    unittest.main()

