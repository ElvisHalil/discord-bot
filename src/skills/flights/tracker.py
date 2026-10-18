import asyncio
from dataclasses import asdict
import json
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.context import get_active_channel_id, get_bound_event_loop
from src.skills.flights.aggregators import GoogleFlightsPriceAggregator, LiveFlightStatusAggregator
from src.skills.flights.engine.resolver import extract_flight_numbers, parse_route_from_query
from src.skills.flights.enums import (
    CabinClass,
    FlightState,
    FlightTrackerAction,
)
from src.skills.flights.models import (
    TrackedFlight,
    TrackedPriceWatch,
)
from src.utils.workers import run_in_worker

PRICE_WATCH_FILE = os.path.join("data", "flight_price_watches.json")
FLIGHT_SAFETY_TIMEOUT_SECONDS = 16 * 3600  # 16 hours for ultra-long-haul flights
DEFAULT_FLIGHT_POLL_SECONDS = 60
DEFAULT_PRICE_POLL_SECONDS = 1800  # 30 minutes


class FlightTrackerService:
    """
    Background manager that polls:
      1. Active live flights (every 60s) for takeoff, gate changes, delays (>=15m), and landing.
      2. Watched route fares (every 30m, persisted to data/flight_price_watches.json) for price drops.
    """

    def __init__(self, watch_file: str = PRICE_WATCH_FILE):
        self.watch_file = watch_file
        self.tracked_flights: Dict[Tuple[int, str], TrackedFlight] = {}
        self.price_watches: Dict[Tuple[int, str], TrackedPriceWatch] = {}
        self.flight_poll_interval: int = DEFAULT_FLIGHT_POLL_SECONDS
        self.price_poll_interval: int = DEFAULT_PRICE_POLL_SECONDS
        self._notify_callback: Optional[Callable[[int, str], Awaitable[None]]] = None
        self._task: Optional[asyncio.Task] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._status_agg = LiveFlightStatusAggregator()
        self._price_agg = GoogleFlightsPriceAggregator()
        self._load_price_watches()

    def bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = loop

    def set_notify_callback(self, callback: Callable[[int, str], Awaitable[None]]) -> None:
        self._notify_callback = callback

    def _load_price_watches(self) -> None:
        if not os.path.exists(self.watch_file):
            return
        try:
            with open(self.watch_file, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
            if isinstance(raw_list, list):
                for item in raw_list:
                    if isinstance(item, dict) and "channel_id" in item and "origin_code" in item and "dest_code" in item:
                        w = TrackedPriceWatch(**item)
                        self.price_watches[(w.channel_id, w.watch_id)] = w
        except Exception as e:
            print(f"⚠️ [Flight Tracker]: Could not load price watches: {e}")

    def _save_price_watches(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.watch_file), exist_ok=True)
            payload = [asdict(w) for w in self.price_watches.values()]
            with open(self.watch_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            print(f"⚠️ [Flight Tracker]: Could not save price watches: {e}")

    def ensure_loop_running(self) -> None:
        if not self.tracked_flights and not self.price_watches:
            return
        try:
            loop = asyncio.get_running_loop()
            self._event_loop = loop
            if self._task is None or self._task.done():
                self._task = loop.create_task(self._poll_loop())
        except RuntimeError:
            target_loop = self._event_loop or get_bound_event_loop()
            if target_loop and target_loop.is_running():
                def _start():
                    if self._task is None or self._task.done():
                        self._task = asyncio.create_task(self._poll_loop())
                target_loop.call_soon_threadsafe(_start)

    @staticmethod
    def diff_flight_snapshots(
        flight: TrackedFlight,
        new_snap: Dict[str, Any],
    ) -> Tuple[List[str], bool]:
        """
        Compares a TrackedFlight's previous state against a fresh snapshot.
        Returns (alert_lines, should_remove_flight).
        """
        alerts: List[str] = []
        should_remove = False

        new_state = new_snap.get("state") or flight.last_state
        new_origin_gate = new_snap.get("origin_gate")
        new_dest_gate = new_snap.get("dest_gate")
        new_delay = int(new_snap.get("delay_mins") or 0)

        # 1. State transitions
        if flight.last_state == FlightState.SCHEDULED and new_state == FlightState.AIRBORNE:
            dep_str = new_snap.get("est_dep_str") or "Just now"
            eta_str = new_snap.get("est_arr_str") or "TBD"
            alerts.append(
                f"🛫 **TAKEOFF ALERT**: **{flight.flight_number}** ({flight.origin_code} -> {flight.dest_code}) "
                f"is now **Airborne / En Route**! (Departed: {dep_str} | ETA: {eta_str})"
            )
        elif new_state == FlightState.LANDED and flight.last_state != FlightState.LANDED:
            arr_str = new_snap.get("est_arr_str") or "Just now"
            gate_info = []
            if new_snap.get("dest_terminal"):
                gate_info.append(f"Terminal {new_snap['dest_terminal']}")
            if new_dest_gate:
                gate_info.append(f"Gate {new_dest_gate}")
            if new_snap.get("dest_baggage"):
                gate_info.append(f"Baggage {new_snap['dest_baggage']}")
            gate_suffix = f" [{' | '.join(gate_info)}]" if gate_info else ""
            alerts.append(
                f"🛬 **LANDED**: **{flight.flight_number}** has touched down at **{flight.dest_code}** "
                f"({arr_str}){gate_suffix}!"
            )
            should_remove = True
        elif new_state == FlightState.CANCELLED and flight.last_state != FlightState.CANCELLED:
            alerts.append(
                f"🛑 **FLIGHT CANCELLED**: **{flight.flight_number}** ({flight.origin_code} -> {flight.dest_code}) "
                f"has been marked **Cancelled**."
            )
            should_remove = True
        elif new_state == FlightState.DIVERTED and flight.last_state != FlightState.DIVERTED:
            alerts.append(
                f"⚠️ **FLIGHT DIVERTED**: **{flight.flight_number}** ({flight.origin_code} -> {flight.dest_code}) "
                f"has been **Diverted** ({new_snap.get('status_text')})."
            )

        # 2. Gate changes
        if new_origin_gate and new_origin_gate != flight.last_origin_gate and flight.last_state == FlightState.SCHEDULED:
            alerts.append(
                f"🚪 **DEPARTURE GATE UPDATE ({flight.flight_number})**: Gate at {flight.origin_code} is now **Gate {new_origin_gate}**."
            )
        if new_dest_gate and new_dest_gate != flight.last_dest_gate and not should_remove:
            alerts.append(
                f"🚪 **ARRIVAL GATE UPDATE ({flight.flight_number})**: Arrival gate at {flight.dest_code} is now **Gate {new_dest_gate}**."
            )

        # 3. Significant delay shift (>= 15 min change and total delay >= 15 min)
        if new_delay >= 15 and (new_delay - flight.last_delay_mins) >= 15 and not should_remove:
            alerts.append(
                f"⏰ **DELAY ALERT ({flight.flight_number})**: Estimated delay is now **+{new_delay} min** "
                f"(Updated ETA at {flight.dest_code}: {new_snap.get('est_arr_str', 'TBD')})."
            )

        # Update stored fields on flight
        flight.last_state = new_state
        flight.last_status_text = new_snap.get("status_text") or flight.last_status_text
        if new_origin_gate:
            flight.last_origin_gate = new_origin_gate
        if new_dest_gate:
            flight.last_dest_gate = new_dest_gate
        flight.last_delay_mins = new_delay
        flight.last_snapshot = new_snap
        flight.last_polled_at = time.time()

        return alerts, should_remove

    @staticmethod
    def evaluate_price_update(
        watch: TrackedPriceWatch,
        fare_data: Dict[str, Any],
    ) -> Optional[str]:
        """
        Checks if a fresh Google Flights fare result triggers a price-drop alert
        for the given TrackedPriceWatch. Updates watch state in-place.
        """
        watch.last_polled_at = time.time()
        if watch.direct_only:
            lowest = fare_data.get("lowest_nonstop")
        else:
            lowest = (
                fare_data.get("lowest_reasonable")
                or fare_data.get("best_overall")
                or fare_data.get("lowest_overall")
            )
        if not lowest or not lowest.get("price"):
            return None

        cur_price = int(lowest["price"])
        cur_airline = lowest.get("airlines") or watch.best_airline
        cur_stops = lowest.get("stops_label") or ""
        cur_dur = lowest.get("duration_str") or ""
        booking_url = fare_data.get("booking_url") or watch.booking_url
        watch.booking_url = booking_url

        if watch.lowest_seen_price <= 0 or cur_price < watch.lowest_seen_price:
            prev_low = watch.lowest_seen_price if watch.lowest_seen_price > 0 else watch.baseline_price
            watch.lowest_seen_price = cur_price
            watch.best_airline = cur_airline
        else:
            prev_low = watch.lowest_seen_price

        # Condition 1: Target price reached
        if watch.target_price and cur_price <= watch.target_price:
            if watch.last_alerted_price is None or cur_price < watch.last_alerted_price:
                watch.last_alerted_price = cur_price
                diff_base = watch.baseline_price - cur_price
                saved_str = f" (Down ${diff_base} {watch.currency} from ${watch.baseline_price})" if diff_base > 0 else ""
                return (
                    f"🎯 **FLIGHT PRICE TARGET HIT ({watch.origin_code} -> {watch.dest_code})**!\n"
                    f"• **New Lowest Fare**: **${cur_price} {watch.currency}** via **{cur_airline}** ({cur_stops}, {cur_dur}){saved_str}\n"
                    f"• **Your Target**: ${watch.target_price} {watch.currency} | **Dates**: {watch.date}"
                    + (f" to {watch.return_date}" if watch.return_date else "")
                    + f"\n• **Book on Google Flights**: [View Deal](<{booking_url}>)"
                )

        # Condition 2: Significant price drop (>= $25 or >= 5% below previous low)
        drop_amt = prev_low - cur_price
        drop_pct = (drop_amt / prev_low) if prev_low > 0 else 0.0
        if prev_low > 0 and (drop_amt >= 25 or drop_pct >= 0.05):
            if watch.last_alerted_price is None or cur_price < watch.last_alerted_price:
                watch.last_alerted_price = cur_price
                return (
                    f"📉 **FLIGHT PRICE DROP ALERT ({watch.origin_code} -> {watch.dest_code})**!\n"
                    f"• **New Lowest Fare**: **${cur_price} {watch.currency}** (Dropped **-${drop_amt} {watch.currency}** / {drop_pct*100:.0f}% from ${prev_low}!)\n"
                    f"• **Best Option**: **{cur_airline}** ({cur_stops}, {cur_dur}) | **Dates**: {watch.date}"
                    + (f" to {watch.return_date}" if watch.return_date else "")
                    + f"\n• **Book on Google Flights**: [View Deal](<{booking_url}>)"
                )

        return None

    def _fetch_watch_fare_data(
        self,
        orig_code: str,
        dest_code: str,
        dep_date: str,
        ret_date: Optional[str],
        cabin: str,
        currency: str,
    ) -> dict[str, Any]:
        fare_data = self._price_agg.fetch_structured_fares(
            origin_code=orig_code,
            dest_code=dest_code,
            date=dep_date,
            return_date=ret_date,
            cabin=cabin,
            currency=currency,
        )
        if ret_date and not fare_data.get("lowest_overall"):
            ow_out = self._price_agg.fetch_structured_fares(
                origin_code=orig_code,
                dest_code=dest_code,
                date=dep_date,
                return_date=None,
                cabin=cabin,
                currency=currency,
            )
            ow_ret = self._price_agg.fetch_structured_fares(
                origin_code=dest_code,
                dest_code=orig_code,
                date=ret_date,
                return_date=None,
                cabin=cabin,
                currency=currency,
            )
            low_out = ow_out.get("best_overall") or ow_out.get("lowest_reasonable") or ow_out.get("lowest_overall")
            low_ret = ow_ret.get("best_overall") or ow_ret.get("lowest_reasonable") or ow_ret.get("lowest_overall")
            if low_out and low_ret:
                combined = dict(low_out)
                combined["price"] = int(low_out["price"]) + int(low_ret["price"])
                combined["airlines"] = f"{low_out['airlines']} / {low_ret['airlines']}"
                combined["stops_label"] = f"{low_out.get('stops_label', '')} out / {low_ret.get('stops_label', '')} back".strip(" /")
                combined["duration_str"] = f"{low_out.get('duration_str', '')} out, {low_ret.get('duration_str', '')} back".strip(", ")
                fare_data = dict(fare_data)
                fare_data["lowest_overall"] = combined
                fare_data["lowest_reasonable"] = combined
                fare_data["best_overall"] = combined
                fare_data["options"] = [combined]
                ns_out = ow_out.get("lowest_nonstop")
                ns_ret = ow_ret.get("lowest_nonstop")
                if ns_out and ns_ret:
                    ns_combined = dict(ns_out)
                    ns_combined["price"] = int(ns_out["price"]) + int(ns_ret["price"])
                    ns_combined["airlines"] = f"{ns_out['airlines']} / {ns_ret['airlines']}"
                    ns_combined["stops_label"] = "Nonstop"
                    ns_combined["duration_str"] = f"{ns_out.get('duration_str', '')} out, {ns_ret.get('duration_str', '')} back".strip(", ")
                    fare_data["lowest_nonstop"] = ns_combined
        return fare_data

    async def _poll_loop(self) -> None:
        print("✈️ [Flight Tracker]: Background polling loop started.")

        try:
            while self.tracked_flights or self.price_watches:
                await asyncio.sleep(15)
                now = time.time()

                # 1. Poll live flights due for a check (every 60s)
                for key, flight in list(self.tracked_flights.items()):
                    if (now - flight.created_at) > FLIGHT_SAFETY_TIMEOUT_SECONDS:
                        self.tracked_flights.pop(key, None)
                        continue
                    if (now - flight.last_polled_at) < self.flight_poll_interval:
                        continue
                    try:
                        snap = await run_in_worker(self._status_agg.fetch_flight_snapshot, flight.flight_number)
                        if not snap:
                            flight.last_polled_at = now
                            continue
                        alerts, should_remove = self.diff_flight_snapshots(flight, snap)
                        if should_remove:
                            self.tracked_flights.pop(key, None)
                        if alerts and self._notify_callback:
                            msg = (
                                f"✈️ **LIVE FLIGHT TRACKER — {flight.flight_number} ({flight.origin_code} -> {flight.dest_code})**\n"
                                + "\n".join(alerts)
                            )
                            await self._notify_callback(flight.channel_id, msg)
                    except Exception as e:
                        print(f"⚠️ [Flight Tracker Poll Error ({flight.flight_number})]: {e}")

                # 2. Poll route price watches due for a check (every 30m)
                dirty_watches = False
                for key, watch in list(self.price_watches.items()):
                    if (now - watch.last_polled_at) < self.price_poll_interval:
                        continue
                    try:
                        fare_data = await run_in_worker(
                            self._fetch_watch_fare_data,
                            watch.origin_code,
                            watch.dest_code,
                            watch.date,
                            watch.return_date,
                            watch.cabin,
                            watch.currency,
                        )
                        alert_msg = self.evaluate_price_update(watch, fare_data)
                        dirty_watches = True
                        if alert_msg and self._notify_callback:
                            await self._notify_callback(watch.channel_id, alert_msg)
                    except Exception as e:
                        print(f"⚠️ [Price Watch Poll Error ({watch.watch_id})]: {e}")


                if dirty_watches:
                    self._save_price_watches()
        finally:
            print("✈️ [Flight Tracker]: Background polling loop stopped (no active watches).")

    def track_live_flight(self, channel_id: int, flight_code: str) -> str:
        snap = self._status_agg.fetch_flight_snapshot(flight_code)
        if not snap:
            return (
                f"⚠️ Could not find an active or scheduled flight matching **{flight_code}** to track. "
                f"Please verify the airline code and flight number (e.g. `AC850`, `WS102`, `UA123`)."
            )

        f_num = snap["flight_number"]
        key = (channel_id, f_num.upper())
        tf = TrackedFlight(
            channel_id=channel_id,
            flight_number=f_num,
            callsign=snap["callsign"],
            airline=snap["airline"],
            origin_code=snap["origin_code"],
            dest_code=snap["dest_code"],
            last_state=snap["state"],
            last_status_text=snap["status_text"],
            last_origin_gate=snap.get("origin_gate"),
            last_dest_gate=snap.get("dest_gate"),
            last_delay_mins=int(snap.get("delay_mins") or 0),
            last_snapshot=snap,
        )
        self.tracked_flights[key] = tf
        self.ensure_loop_running()

        status_block = self._status_agg.get_flight_status(f_num)
        return (
            f"✅ **NOW TRACKING LIVE FLIGHT: {f_num} ({snap['origin_code']} -> {snap['dest_code']})**\n"
            f"I will automatically notify this channel when **{f_num}** takes off, changes gates, experiences a delay (≥15 min), or lands!\n\n"
            f"{status_block}"
        )

    def track_route_price(
        self,
        channel_id: int,
        query: str,
        origin: Optional[str] = None,
        destination: Optional[str] = None,
        date: Optional[str] = None,
        return_date: Optional[str] = None,
        target_price: Optional[int] = None,
        cabin: Optional[str] = None,
        currency: Optional[str] = None,
        direct_only: Optional[bool] = None,
    ) -> str:
        parsed = parse_route_from_query(query, origin=origin, destination=destination, date=date, return_date=return_date)
        orig_info = parsed["origin"]
        dest_info = parsed["destination"]
        if not orig_info or not dest_info:
            return (
                "⚠️ Could not identify both the origin and destination airports for a price watch. "
                "Please specify a route like `Calgary to Tokyo on Nov 15` or flight number like `AC850`."
            )

        # Extract target price from query if not explicitly passed (e.g. "under $800", "below 500")
        if target_price is None and query:
            m_tp = re.search(r"(?:under|below|target|less\s+than|\$)\s*\$?(\d{2,5})\b", query, re.IGNORECASE)
            if m_tp:
                target_price = int(m_tp.group(1))

        orig_code, _, orig_city, _ = orig_info
        dest_code, _, dest_city, _ = dest_info
        dep_date = parsed["date"]
        ret_date = parsed["return_date"]
        eff_cabin = (cabin or parsed["cabin"] or "economy").lower()
        eff_currency = (currency or parsed["currency"] or "CAD").upper()
        eff_direct_only = bool(direct_only if direct_only is not None else parsed.get("wants_direct_schedule"))

        fare_data = self._fetch_watch_fare_data(
            orig_code,
            dest_code,
            dep_date,
            ret_date,
            eff_cabin,
            eff_currency,
        )
        if eff_direct_only:
            lowest = fare_data.get("lowest_nonstop")
        else:
            lowest = (
                fare_data.get("best_overall")
                or fare_data.get("lowest_reasonable")
                or fare_data.get("lowest_overall")
            )
        cur_price = int(lowest["price"]) if lowest and lowest.get("price") else 0
        best_airline = (lowest.get("airlines") if lowest else "") or "Unknown"
        best_stops = (lowest.get("stops_label") if lowest else "") or ""
        best_dur = (lowest.get("duration_str") if lowest else "") or ""

        watch = TrackedPriceWatch(
            channel_id=channel_id,
            origin_code=orig_code,
            origin_city=orig_city,
            dest_code=dest_code,
            dest_city=dest_city,
            date=dep_date,
            return_date=ret_date,
            cabin=eff_cabin,
            currency=eff_currency,
            baseline_price=cur_price,
            lowest_seen_price=cur_price,
            target_price=target_price,
            best_airline=best_airline,
            booking_url=fare_data.get("booking_url") or "",
            direct_only=eff_direct_only,
        )
        self.price_watches[(channel_id, watch.watch_id)] = watch
        self._save_price_watches()
        self.ensure_loop_running()

        target_str = f"**${target_price} {eff_currency}**" if target_price else "any drop of **≥$25 / ≥5%** below current lowest"
        itin_meta = ", ".join(p for p in (best_stops, best_dur) if p)
        itin_suffix = f" [{itin_meta}]" if itin_meta else ""
        baseline_str = f"**${cur_price} {eff_currency}** ({best_airline}{itin_suffix})" if cur_price else "No matching fares currently listed"
        dates_str = f"{dep_date} to {ret_date}" if ret_date else dep_date
        filter_str = " (Direct / Nonstop Only)" if eff_direct_only else " (Quality-Filtered — excludes >22h marathon layovers)"
        verdict_line = f"\n• **Google Price Insight**: {fare_data['verdict']} — {fare_data['verdict_detail']}" if fare_data.get("verdict_detail") else ""

        return (
            f"✅ **PRICE WATCH CREATED: {orig_city} ({orig_code}) -> {dest_city} ({dest_code})**{filter_str}\n"
            f"• **Watch ID**: `{watch.watch_id}`\n"
            f"• **Dates**: {dates_str} | **Cabin**: {eff_cabin.title()}\n"
            f"• **Current Baseline Fare**: {baseline_str}{verdict_line}\n"
            f"• **Alert Trigger**: Notify channel when price hits {target_str} (checked every 30 mins, persisted across restarts).\n"
            f"• **Direct Link**: [Google Flights](<{watch.booking_url}>)"
        )

    def untrack(self, channel_id: int, query: str) -> str:
        q_clean = (query or "").strip().upper()
        if not q_clean or q_clean in ("ALL", "*"):
            return self.clear_channel(channel_id)

        removed = []
        # Check tracked live flights
        for (cid, f_num), tf in list(self.tracked_flights.items()):
            if cid == channel_id and (q_clean in f_num or q_clean in tf.callsign.upper()):
                self.tracked_flights.pop((cid, f_num), None)
                removed.append(f"Live Flight **{tf.flight_number}** ({tf.origin_code}->{tf.dest_code})")

        # Check price watches
        parsed = parse_route_from_query(query)
        orig_c = parsed["origin"][0] if parsed["origin"] else ""
        dest_c = parsed["destination"][0] if parsed["destination"] else ""

        for (cid, wid), pw in list(self.price_watches.items()):
            if cid != channel_id:
                continue
            if (
                q_clean in wid.upper()
                or (orig_c and dest_c and pw.origin_code == orig_c and pw.dest_code == dest_c)
                or q_clean in pw.origin_city.upper()
                or q_clean in pw.dest_city.upper()
            ):
                self.price_watches.pop((cid, wid), None)
                removed.append(f"Price Watch **{pw.origin_code}->{pw.dest_code}** ({pw.date})")

        if removed:
            self._save_price_watches()
            return "🛑 Stopped tracking: " + ", ".join(removed)
        return f"ℹ️ No active flight or price watch matching `{query}` was found in this channel."

    def clear_channel(self, channel_id: int) -> str:
        f_count = 0
        p_count = 0
        for (cid, f_num) in list(self.tracked_flights.keys()):
            if cid == channel_id:
                self.tracked_flights.pop((cid, f_num), None)
                f_count += 1
        for (cid, wid) in list(self.price_watches.keys()):
            if cid == channel_id:
                self.price_watches.pop((cid, wid), None)
                p_count += 1
        if p_count:
            self._save_price_watches()
        return f"🧹 Cleared **{f_count}** live flight(s) and **{p_count}** route price watch(es) in this channel."

    def list_channel_watches(self, channel_id: int) -> str:
        flights = [tf for (cid, _), tf in self.tracked_flights.items() if cid == channel_id]
        watches = [pw for (cid, _), pw in self.price_watches.items() if cid == channel_id]

        if not flights and not watches:
            return "ℹ️ There are currently no tracked live flights or route price watches in this channel."

        lines = ["=== ACTIVE FLIGHT & PRICE WATCHES IN THIS CHANNEL ==="]
        if flights:
            lines.append("\n📡 **Live Tracked Flights (Polled every 60s)**:")
            for tf in flights:
                delay_str = f" (+{tf.last_delay_mins}m delay)" if tf.last_delay_mins >= 15 else ""
                lines.append(
                    f"• **{tf.flight_number}** ({tf.origin_code} -> {tf.dest_code}) — "
                    f"State: **{tf.last_state.upper()}** ({tf.last_status_text}){delay_str}"
                )
        if watches:
            lines.append("\n💰 **Route Ticket Price Watches (Polled every 30m)**:")
            for pw in watches:
                dates_str = f"{pw.date} to {pw.return_date}" if pw.return_date else pw.date
                target_str = f"Target: ≤${pw.target_price} {pw.currency}" if pw.target_price else "Target: ≥$25 / 5% drop"
                lines.append(
                    f"• **{pw.origin_city} ({pw.origin_code}) -> {pw.dest_city} ({pw.dest_code})** [{dates_str}] — "
                    f"Lowest Seen: **${pw.lowest_seen_price} {pw.currency}** ({pw.best_airline}) | {target_str}"
                )
        return "\n".join(lines)


FLIGHT_TRACKER = FlightTrackerService()


def skill_flight_tracker(
    action: Optional[str] = "track",
    query: Optional[str] = None,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    date: Optional[str] = None,
    return_date: Optional[str] = None,
    target_price: Optional[int] = None,
) -> str:
    """
    Subscribe or unsubscribe the current Discord channel / Telegram chat to:
      1. Live Flight Status Alerts (takeoff, gate changes, delays >=15m, landing) for a commercial flight number.
      2. Google Flights Ticket Price Drop Alerts for a route and travel date.

    Args:
        action: One of 'track' (default), 'untrack' / 'stop', 'list' / 'status', or 'clear'.
        query: Flight number (e.g. 'AC850', 'WS102') OR route description (e.g. 'Calgary to Tokyo Nov 15 under $900').
        origin: Optional departure city or IATA code for route price watches.
        destination: Optional arrival city or IATA code for route price watches.
        date: Optional departure date ('YYYY-MM-DD' or natural language).
        return_date: Optional return date ('YYYY-MM-DD' or natural language).
        target_price: Optional target fare alert threshold (e.g. 850).
    """
    act = (action or "track").strip().lower()
    q_str = (query or "").strip()
    channel_id = get_active_channel_id()

    print(
        f"✈️ [Executing Flight Tracker Skill] Action: {act}, Query: '{q_str}', "
        f"Origin: {origin}, Dest: {destination}, Date: {date}, TargetPrice: {target_price}"
    )

    if act in ("list", "status", "show", "active") or (not q_str and not origin and not destination and act == "track"):
        return FLIGHT_TRACKER.list_channel_watches(channel_id)
    if act in ("clear", "reset", "stop_all", "untrack_all"):
        return FLIGHT_TRACKER.clear_channel(channel_id)
    if act in ("untrack", "stop", "remove", "delete", "cancel"):
        target_q = q_str or f"{origin or ''} to {destination or ''}".strip()
        return FLIGHT_TRACKER.untrack(channel_id, target_q)

    # Check if user is asking to untrack/list inside natural language query
    q_low = q_str.lower()
    if re.search(r"\b(?:untrack|stop\s+tracking|remove\s+watch)\b", q_low):
        cleaned = re.sub(r"\b(?:untrack|stop\s+tracking|remove\s+watch|flight|price)\b", "", q_str, flags=re.IGNORECASE).strip()
        return FLIGHT_TRACKER.untrack(channel_id, cleaned or "ALL")
    if re.search(r"\b(?:list|show|what)\b.*\b(?:tracked\s+flights|price\s+watches|flight\s+watches)\b", q_low):
        return FLIGHT_TRACKER.list_channel_watches(channel_id)

    # Determine if this is a live flight number watch vs. a route price watch
    flight_codes = extract_flight_numbers(q_str)
    parsed_route = parse_route_from_query(q_str, origin=origin, destination=destination, date=date, return_date=return_date)
    has_route = bool(parsed_route["origin"] and parsed_route["destination"])

    if flight_codes and not has_route:
        results = [FLIGHT_TRACKER.track_live_flight(channel_id, fc) for fc in flight_codes[:3]]
        return "\n\n".join(results)

    return FLIGHT_TRACKER.track_route_price(
        channel_id=channel_id,
        query=q_str,
        origin=origin,
        destination=destination,
        date=date,
        return_date=return_date,
        target_price=target_price,
    )

