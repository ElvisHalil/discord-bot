"""
Global Central Bank Rate Decisions & Economic Calendar Aggregator (`MacroCalendarAggregator`).

Combines four authoritative sources:
1. TradingView Economic Calendar API (`economic-calendar.tradingview.com/events`) — live UTC timestamps,
   actuals (populated the instant decisions drop), consensus forecasts, and previous readings across
   G10 economies (US, CA, EU, GB, JP, AU, NZ, CH).
2. ForexFactory / FairEconomy Weekly Calendar JSON (`nfs.faireconomy.media/ff_calendar_thisweek.json`) —
   high-impact weekly macro & central bank cross-check and fallback.
3. Official Federal Reserve FOMC Calendar (`federalreserve.gov/monetarypolicy/fomccalendars.htm`) —
   full-year FOMC meeting dates, statement release times (2:00 PM ET / 12:00 PM MT), and press conferences.
4. Official Bank of Canada Policy Rate & Schedule (`bankofcanada.ca/core-functions/monetary-policy/key-interest-rate/`) —
   official target overnight rate history and full-year announcement schedule (9:45 AM ET / 7:45 AM MT).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
import requests

from src.skills.markets.aggregators.base import BaseMarketAggregator
from src.skills.markets.models import MacroCalendarEvent
from src.utils.cache import BoundedCache
from src.utils.domain_vocab import MONTH_MAP_SYNC
from src.utils.timezones import (
    _format_relative_delta,
    _resolve_tz_token,
    _short_tz_abbr,
    build_reference_clock,
    get_local_now,
    resolve_configured_timezone,
)
from src.utils.workers import submit_to_worker

_MACRO_CALENDAR_CACHE = BoundedCache(maxsize=64, ttl=120)
_OFFICIAL_SCHEDULE_CACHE = BoundedCache(maxsize=16, ttl=3600)

_COUNTRY_FLAG_MAP: Dict[str, Tuple[str, str]] = {
    "US": ("🇺🇸", "United States"),
    "CA": ("🇨🇦", "Canada"),
    "EU": ("🇪🇺", "Eurozone"),
    "GB": ("🇬🇧", "United Kingdom"),
    "JP": ("🇯🇵", "Japan"),
    "AU": ("🇦🇺", "Australia"),
    "NZ": ("🇳🇿", "New Zealand"),
    "CH": ("🇨🇭", "Switzerland"),
    "CN": ("🇨🇳", "China"),
}

_FF_CURRENCY_TO_COUNTRY: Dict[str, str] = {
    "USD": "US",
    "CAD": "CA",
    "EUR": "EU",
    "GBP": "GB",
    "JPY": "JP",
    "AUD": "AU",
    "NZD": "NZ",
    "CHF": "CH",
    "CNY": "CN",
}

# Verified official FOMC and Bank of Canada schedules (used both to enrich long-horizon calendar
# lookups beyond TradingView's 3-week window and as a zero-downtime fallback).
_OFFICIAL_FOMC_MEETINGS_FALLBACK: Dict[int, List[Tuple[int, str, int, bool]]] = {
    2026: [
        (1, "27-28", 28, False),
        (3, "17-18", 18, True),
        (4, "28-29", 29, False),
        (6, "16-17", 17, True),
        (7, "28-29", 29, False),
        (9, "15-16", 16, True),
        (10, "27-28", 28, False),
        (12, "8-9", 9, True),
    ],
    2027: [
        (1, "26-27", 27, False),
        (3, "16-17", 17, True),
        (4, "27-28", 28, False),
        (6, "8-9", 9, True),
        (7, "27-28", 28, False),
        (9, "14-15", 15, True),
        (10, "26-27", 27, False),
        (12, "7-8", 8, True),
    ],
}

_OFFICIAL_BOC_MEETINGS_FALLBACK: Dict[int, List[Tuple[int, int, bool]]] = {
    2026: [
        (1, 28, True),
        (3, 18, False),
        (4, 29, True),
        (6, 10, False),
        (7, 15, True),
        (9, 2, False),
        (10, 28, True),
        (12, 9, False),
    ],
    2027: [
        (1, 27, True),
        (3, 3, False),
        (4, 28, True),
        (6, 2, False),
        (7, 21, True),
        (9, 8, False),
        (10, 27, True),
        (12, 8, False),
    ],
}

_CENTRAL_BANK_KEYWORDS_RE = re.compile(
    r"\b(?:interest\s+rate\s+decision|overnight\s+rate|federal\s+funds\s+rate|official\s+cash\s+rate|"
    r"fomc|fed\s+(?:interest\s+rate|press\s+conference|statement|chair)|"
    r"boc\s+(?:interest\s+rate|rate\s+statement|monetary\s+policy|press\s+conference)|"
    r"ecb\s+(?:interest\s+rate|press\s+conference|monetary\s+policy)|"
    r"boe\s+interest\s+rate|monetary\s+policy\s+summary|"
    r"boj\s+(?:interest\s+rate|policy\s+rate|outlook\s+report|press\s+conference)|"
    r"rba\s+(?:interest\s+rate|rate\s+statement)|"
    r"rbnz\s+(?:interest\s+rate|rate\s+statement|official\s+cash\s+rate)|"
    r"snb\s+(?:interest\s+rate|policy\s+rate))\b",
    re.IGNORECASE,
)

_RATE_DECISION_TITLE_RE = re.compile(
    r"\b(?:interest\s+rate\s+decision|overnight\s+rate|federal\s+funds\s+rate|official\s+cash\s+rate|policy\s+rate)\b",
    re.IGNORECASE,
)


def clear_macro_calendar_cache() -> None:
    """Clear all in-memory economic calendar and official central bank schedule caches."""
    _MACRO_CALENDAR_CACHE.clear()
    _OFFICIAL_SCHEDULE_CACHE.clear()


def _classify_event_category(title: str) -> str:
    low = (title or "").lower()
    if _RATE_DECISION_TITLE_RE.search(low):
        return "rate_decision"
    if _CENTRAL_BANK_KEYWORDS_RE.search(low):
        return "central_bank"
    if re.search(r"\b(?:cpi|pce|ppi|inflation)\b", low):
        return "inflation"
    if re.search(r"\b(?:non\s*farm\s*payrolls?|nfp|unemployment\s+rate|employment\s+change|jobless\s+claims)\b", low):
        return "labor"
    if re.search(r"\b(?:gdp|retail\s+sales|ism\s+|pmi)\b", low):
        return "growth"
    return "macro"


def _fmt_calendar_val(val: object, unit: str = "") -> Optional[str]:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        u = (unit or "").strip()
        if u == "%":
            num_str = f"{float(val):.2f}".rstrip("0").rstrip(".")
            return f"{num_str}%"
        if u == "$":
            return f"${val}"
        return f"{val}{u}"
    s = str(val).strip()
    if not s:
        return None
    if unit and not s.endswith(unit) and unit in ("%",):
        return f"{s}{unit}"
    return s


def _fmt_rate_val(val: object, unit: str = "") -> Optional[str]:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return f"{float(val):.2f}%"
    s = str(val).strip()
    if not s:
        return None
    try:
        num = float(s.rstrip("%"))
        return f"{num:.2f}%"
    except ValueError:
        return s if s.endswith("%") or not unit else f"{s}{unit}"


def _compute_bps_change_label(actual_str: Optional[str], prev_str: Optional[str]) -> str:
    """Compute basis points change label (e.g. '+25 bps hike', '-25 bps cut', 'Unchanged / Hold') when both are % rates."""
    if not actual_str or not prev_str:
        return ""
    try:
        act = float(str(actual_str).replace("%", "").strip())
        prv = float(str(prev_str).replace("%", "").strip())
        diff_bps = int(round((act - prv) * 100))
        if diff_bps > 0:
            return f" (**+{diff_bps} bps rate hike**)"
        if diff_bps < 0:
            return f" (**{diff_bps} bps rate cut**)"
        return " (**Unchanged / Hold**)"
    except ValueError:
        return ""


def _et_to_utc(year: int, month: int, day: int, hour_et: int, minute_et: int) -> datetime:
    """Convert an Eastern Time wall clock `(year, month, day, hour_et, minute_et)` into an exact UTC datetime."""
    prelim_utc = datetime(year, month, day, hour_et + 4, minute_et, tzinfo=timezone.utc)
    et_tz = _resolve_tz_token("ET", ref_utc=prelim_utc) or timezone(timedelta(hours=-4), "EDT")
    return datetime(year, month, day, hour_et, minute_et, tzinfo=et_tz).astimezone(timezone.utc)


def _format_event_timestamp_multi_tz(
    ev_utc: datetime,
    local_now: datetime,
    tz_override: Optional[str] = None,
) -> str:
    """Format an event UTC timestamp with Local Time FIRST, followed by Eastern Time, UTC, and relative delta."""
    utc_now = local_now.astimezone(timezone.utc)
    ev_utc = ev_utc.astimezone(timezone.utc)
    local_tz = resolve_configured_timezone(tz_override=tz_override, ref_utc=ev_utc)
    et_tz = _resolve_tz_token("ET", ref_utc=ev_utc) or timezone(timedelta(hours=-4), "EDT")

    ev_local = ev_utc.astimezone(local_tz)
    ev_et = ev_utc.astimezone(et_tz)
    local_abbr = _short_tz_abbr(ev_local)
    et_abbr = _short_tz_abbr(ev_et)

    day_diff = (ev_local.date() - local_now.date()).days
    if day_diff == 0:
        day_prefix = f"TODAY, {ev_local.strftime('%a %b %d, %Y')}"
    elif day_diff == 1:
        day_prefix = f"Tomorrow, {ev_local.strftime('%a %b %d, %Y')}"
    elif day_diff == -1:
        day_prefix = f"Yesterday, {ev_local.strftime('%a %b %d, %Y')}"
    else:
        day_prefix = ev_local.strftime("%a, %b %d, %Y")

    local_clock = ev_local.strftime("%I:%M %p").lstrip("0")
    et_clock = ev_et.strftime("%I:%M %p").lstrip("0")
    utc_clock = ev_utc.strftime("%H:%M UTC")
    rel_str = _format_relative_delta(ev_utc, utc_now)

    if local_abbr in ("EDT", "EST"):
        return f"**{day_prefix} at {local_clock} {local_abbr} (Local)** ({utc_clock}, {rel_str})"
    return f"**{day_prefix} at {local_clock} {local_abbr} (Local)** ({et_clock} {et_abbr} / {utc_clock}, {rel_str})"


class MacroCalendarAggregator(BaseMarketAggregator):
    """Multi-provider aggregator for central bank rate decisions and high-impact economic calendar events."""

    name = "macro_calendar"

    def fetch_tradingview_events(
        self,
        start_utc: datetime,
        end_utc: datetime,
        countries: str = "US,CA,EU,GB,JP,AU,NZ,CH",
    ) -> List[MacroCalendarEvent]:
        """Fetch structured economic calendar events from TradingView's public calendar endpoint."""
        cache_key = ("tv_events", start_utc.strftime("%Y-%m-%d"), end_utc.strftime("%Y-%m-%d"), countries)
        if cache_key in _MACRO_CALENDAR_CACHE:
            return list(_MACRO_CALENDAR_CACHE[cache_key])

        url = "https://economic-calendar.tradingview.com/events"
        headers = {
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
            "Accept": "application/json",
        }
        params = {
            "from": start_utc.strftime("%Y-%m-%dT00:00:00.000Z"),
            "to": end_utc.strftime("%Y-%m-%dT23:59:59.000Z"),
            "countries": countries,
        }
        events: List[MacroCalendarEvent] = []
        try:
            resp = cffi_requests.get(url, params=params, headers=headers, impersonate="chrome120", timeout=8)
            if resp.status_code == 200:
                payload = resp.json()
                for item in payload.get("result") or []:
                    title = str(item.get("title") or "").strip()
                    country = str(item.get("country") or "").strip().upper()
                    date_str = str(item.get("date") or "").strip()
                    if not title or not date_str:
                        continue
                    try:
                        dt_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(timezone.utc)
                    except ValueError:
                        continue
                    imp = int(item.get("importance") or 0)
                    cat = _classify_event_category(title)
                    # Keep all High-impact events (importance >= 1) and all Central Bank / Rate / CPI / NFP / GDP events
                    if imp < 1 and cat == "macro":
                        continue
                    unit = str(item.get("unit") or "").strip()
                    fmt_fn = _fmt_rate_val if cat == "rate_decision" else _fmt_calendar_val
                    events.append(
                        MacroCalendarEvent(
                            title=title,
                            country=country,
                            timestamp_utc=dt_utc,
                            importance=max(imp, 1 if cat in ("rate_decision", "central_bank") else imp),
                            actual=fmt_fn(item.get("actual"), unit),
                            forecast=fmt_fn(item.get("forecast"), unit),
                            previous=fmt_fn(item.get("previous"), unit),
                            unit=unit,
                            category=cat,
                            source="TradingView",
                        )
                    )
        except Exception as e:
            print(f"⚠️ [MacroCalendar Notice]: TradingView calendar fetch failed ({e})")

        if events:
            _MACRO_CALENDAR_CACHE[cache_key] = events
        return events

    def fetch_forexfactory_week_events(self) -> List[MacroCalendarEvent]:
        """Fetch this week's economic calendar from ForexFactory / FairEconomy JSON CDN."""
        cache_key = ("ff_week",)
        if cache_key in _MACRO_CALENDAR_CACHE:
            return list(_MACRO_CALENDAR_CACHE[cache_key])

        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        events: List[MacroCalendarEvent] = []
        try:
            resp = cffi_requests.get(url, impersonate="chrome120", timeout=7)
            if resp.status_code == 200:
                items = resp.json()
                if isinstance(items, list):
                    for item in items:
                        title = str(item.get("title") or "").strip()
                        curr = str(item.get("country") or "").strip().upper()
                        country = _FF_CURRENCY_TO_COUNTRY.get(curr, curr[:2])
                        date_str = str(item.get("date") or "").strip()
                        impact_str = str(item.get("impact") or "").strip().lower()
                        if not title or not date_str:
                            continue
                        try:
                            dt_utc = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                        except ValueError:
                            continue
                        imp = 1 if impact_str == "high" else (0 if impact_str == "medium" else -1)
                        cat = _classify_event_category(title)
                        if imp < 1 and cat == "macro":
                            continue
                        fc = str(item.get("forecast") or "").strip() or None
                        pv = str(item.get("previous") or "").strip() or None
                        act = str(item.get("actual") or "").strip() or None
                        events.append(
                            MacroCalendarEvent(
                                title=title,
                                country=country,
                                timestamp_utc=dt_utc,
                                importance=max(imp, 1 if cat in ("rate_decision", "central_bank") else imp),
                                actual=act,
                                forecast=fc,
                                previous=pv,
                                unit="%" if ((fc and "%" in fc) or (pv and "%" in pv)) else "",
                                category=cat,
                                source="ForexFactory",
                            )
                        )
        except Exception as e:
            print(f"⚠️ [MacroCalendar Notice]: ForexFactory weekly JSON fetch failed ({e})")

        if events:
            _MACRO_CALENDAR_CACHE[cache_key] = events
        return events

    def fetch_official_fomc_schedule(self, ref_year: int) -> List[Dict[str, object]]:
        """Fetch official Federal Reserve FOMC meeting dates for `ref_year` (with static verified fallback)."""
        cache_key = ("fomc_official", ref_year)
        if cache_key in _OFFICIAL_SCHEDULE_CACHE:
            return list(_OFFICIAL_SCHEDULE_CACHE[cache_key])

        meetings: List[Dict[str, object]] = []
        try:
            resp = requests.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", timeout=7)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for panel in soup.find_all("div", class_="panel"):
                    heading = panel.find("div", class_="panel-heading")
                    if not heading:
                        continue
                    h_text = heading.get_text(" ", strip=True)
                    if f"{ref_year} FOMC Meetings" not in h_text:
                        continue
                    for row in panel.find_all("div", class_="fomc-meeting"):
                        m_div = row.find("div", class_=re.compile(r"fomc-meeting__month"))
                        d_div = row.find("div", class_=re.compile(r"fomc-meeting__date"))
                        if not m_div or not d_div:
                            continue
                        m_name = m_div.get_text(" ", strip=True).split("/")[0].strip().lower()
                        d_raw = d_div.get_text(" ", strip=True)
                        mon_num = MONTH_MAP_SYNC.get(m_name[:3])
                        if not mon_num:
                            continue
                        has_sep = "*" in d_raw
                        nums = [int(x) for x in re.findall(r"\b(\d{1,2})\b", d_raw)]
                        if not nums:
                            continue
                        decision_day = nums[-1]
                        days_label = re.sub(r"[^0-9\-]", "", d_raw) or str(decision_day)
                        dec_utc = _et_to_utc(ref_year, mon_num, decision_day, 14, 0)
                        press_utc = _et_to_utc(ref_year, mon_num, decision_day, 14, 30)
                        meetings.append({
                            "year": ref_year,
                            "month": mon_num,
                            "days_label": days_label,
                            "decision_date": date(ref_year, mon_num, decision_day),
                            "decision_utc": dec_utc,
                            "press_conf_utc": press_utc,
                            "has_projections": has_sep,
                        })
                    break
        except Exception as e:
            print(f"⚠️ [MacroCalendar Notice]: Official FOMC calendar scrape fallback used ({e})")

        if not meetings:
            for mon_num, days_lbl, dec_day, has_sep in _OFFICIAL_FOMC_MEETINGS_FALLBACK.get(ref_year, []):
                meetings.append({
                    "year": ref_year,
                    "month": mon_num,
                    "days_label": days_lbl,
                    "decision_date": date(ref_year, mon_num, dec_day),
                    "decision_utc": _et_to_utc(ref_year, mon_num, dec_day, 14, 0),
                    "press_conf_utc": _et_to_utc(ref_year, mon_num, dec_day, 14, 30),
                    "has_projections": has_sep,
                })

        if meetings:
            _OFFICIAL_SCHEDULE_CACHE[cache_key] = meetings
        return meetings

    def fetch_official_boc_data(self, ref_year: int) -> Tuple[List[Dict[str, str]], List[Dict[str, object]]]:
        """Fetch official Bank of Canada policy rate decisions table and full-year announcement schedule."""
        cache_key = ("boc_official", ref_year)
        if cache_key in _OFFICIAL_SCHEDULE_CACHE:
            return _OFFICIAL_SCHEDULE_CACHE[cache_key]

        rate_history: List[Dict[str, str]] = []
        schedule: List[Dict[str, object]] = []
        try:
            resp = cffi_requests.get(
                "https://www.bankofcanada.ca/core-functions/monetary-policy/key-interest-rate/",
                impersonate="chrome120",
                timeout=7,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                tables = soup.find_all("table")
                if tables:
                    # Table 0: Historical & Latest Rate Decisions (Date | Target (%) | Change (%))
                    for tr in tables[0].find_all("tr")[1:9]:
                        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                        if len(cells) >= 3 and re.search(r"\b20\d{2}\b", cells[0]):
                            t_val = cells[1].strip()
                            if t_val and not t_val.endswith("%"):
                                t_val += "%"
                            chg = cells[2].strip()
                            if chg and chg not in ("---", "—", "-") and not chg.endswith("%"):
                                chg += "%"
                            rate_history.append({
                                "date": cells[0].strip(),
                                "target": t_val,
                                "change": chg,
                            })
        except Exception as e:
            print(f"⚠️ [MacroCalendar Notice]: Official BoC page scrape fallback used ({e})")

        for mon_num, dec_day, has_mpr in _OFFICIAL_BOC_MEETINGS_FALLBACK.get(ref_year, []):
            schedule.append({
                "year": ref_year,
                "month": mon_num,
                "decision_date": date(ref_year, mon_num, dec_day),
                "decision_utc": _et_to_utc(ref_year, mon_num, dec_day, 9, 45),
                "press_conf_utc": _et_to_utc(ref_year, mon_num, dec_day, 10, 30) if has_mpr else None,
                "has_mpr": has_mpr,
            })

        result = (rate_history, schedule)
        _OFFICIAL_SCHEDULE_CACHE[cache_key] = result
        return result

    def get_merged_calendar_events(
        self,
        now_utc: datetime,
        days_back: int = 7,
        days_ahead: int = 45,
    ) -> Tuple[List[MacroCalendarEvent], List[Dict[str, object]], List[Dict[str, str]], List[Dict[str, object]]]:
        """Fetch and merge TradingView, ForexFactory, Official FOMC, and Official BoC schedules in parallel."""
        start_utc = now_utc - timedelta(days=days_back)
        end_utc = now_utc + timedelta(days=days_ahead)

        fut_tv = submit_to_worker(self.fetch_tradingview_events, start_utc, end_utc)
        fut_ff = submit_to_worker(self.fetch_forexfactory_week_events)
        fut_fomc = submit_to_worker(self.fetch_official_fomc_schedule, now_utc.year)
        fut_boc = submit_to_worker(self.fetch_official_boc_data, now_utc.year)

        tv_events = fut_tv.result()
        ff_events = fut_ff.result()
        fomc_schedule = fut_fomc.result()
        boc_history, boc_schedule = fut_boc.result()

        # Merge TradingView and ForexFactory events, avoiding duplicates on same (country, timestamp_hour, role)
        merged: List[MacroCalendarEvent] = list(tv_events)

        def _event_sig(ev: MacroCalendarEvent) -> Tuple[str, str, str]:
            t_low = ev.title.lower()
            if "press conference" in t_low:
                role = "press_conference"
            elif "statement" in t_low and "rate" not in t_low:
                role = "statement"
            elif "monetary policy report" in t_low:
                role = "mpr"
            elif ev.category == "rate_decision":
                role = "rate_decision"
            else:
                role = re.sub(r"[^a-z0-9]+", "", t_low)[:24]
            return (ev.country, ev.timestamp_utc.strftime("%Y-%m-%dT%H:%M"), role)

        by_sig: Dict[Tuple[str, str, str], MacroCalendarEvent] = {_event_sig(e): e for e in merged}

        for ff in ff_events:
            sig = _event_sig(ff)
            if sig in by_sig:
                existing = by_sig[sig]
                if not existing.actual and ff.actual:
                    existing.actual = ff.actual
                if not existing.forecast and ff.forecast:
                    existing.forecast = ff.forecast
                if not existing.previous and ff.previous:
                    existing.previous = ff.previous
            else:
                merged.append(ff)
                by_sig[sig] = ff

        # Backfill official FOMC and BoC rate decisions within [start_utc, end_utc] if not already in `by_sig`
        for m in fomc_schedule:
            dec_utc: datetime = m["decision_utc"]  # type: ignore[assignment]
            if start_utc <= dec_utc <= end_utc:
                sig = ("US", dec_utc.strftime("%Y-%m-%dT%H:%M"), "rate_decision")
                if sig not in by_sig:
                    ev = MacroCalendarEvent(
                        title="Fed Interest Rate Decision (FOMC)",
                        country="US",
                        timestamp_utc=dec_utc,
                        importance=1,
                        category="rate_decision",
                        source="FederalReserve.gov",
                    )
                    merged.append(ev)
                    by_sig[sig] = ev
                pc_utc: datetime = m["press_conf_utc"]  # type: ignore[assignment]
                pc_sig = ("US", pc_utc.strftime("%Y-%m-%dT%H:%M"), "press_conference")
                if pc_sig not in by_sig:
                    pc_ev = MacroCalendarEvent(
                        title="Fed FOMC Press Conference",
                        country="US",
                        timestamp_utc=pc_utc,
                        importance=1,
                        category="central_bank",
                        source="FederalReserve.gov",
                    )
                    merged.append(pc_ev)
                    by_sig[pc_sig] = pc_ev

        # Also check if BoC official history has today's/recent actual rate to backfill if needed
        latest_boc_target = boc_history[0]["target"] if boc_history else None
        for b in boc_schedule:
            dec_utc = b["decision_utc"]  # type: ignore[assignment]
            if start_utc <= dec_utc <= end_utc:
                sig = ("CA", dec_utc.strftime("%Y-%m-%dT%H:%M"), "rate_decision")
                if sig not in by_sig:
                    ev = MacroCalendarEvent(
                        title="BoC Interest Rate Decision",
                        country="CA",
                        timestamp_utc=dec_utc,
                        importance=1,
                        actual=latest_boc_target if dec_utc.date() <= now_utc.date() else None,
                        category="rate_decision",
                        source="BankOfCanada.ca",
                    )
                    merged.append(ev)
                    by_sig[sig] = ev

        merged.sort(key=lambda e: (e.timestamp_utc, -e.importance, e.country, e.title))
        return merged, fomc_schedule, boc_history, boc_schedule

    def format_event_line(
        self,
        ev: MacroCalendarEvent,
        local_now: datetime,
        tz_override: Optional[str] = None,
    ) -> str:
        """Format a single `MacroCalendarEvent` with flag, multi-timezone local-first timestamp, and Actual/Forecast/Previous."""
        flag, country_name = _COUNTRY_FLAG_MAP.get(ev.country, ("🌐", ev.country))
        utc_now = local_now.astimezone(timezone.utc)
        ts_str = _format_event_timestamp_multi_tz(ev.timestamp_utc, local_now, tz_override=tz_override)

        metrics: List[str] = []
        bps_tag = (
            _compute_bps_change_label(ev.actual, ev.previous)
            if ev.category == "rate_decision"
            else ""
        )
        if ev.actual is not None:
            metrics.append(f"**Actual: {ev.actual}**{bps_tag}")
        if ev.forecast is not None:
            metrics.append(f"Forecast: **{ev.forecast}**")
        if ev.previous is not None:
            metrics.append(f"Previous: **{ev.previous}**")

        local_ev_date = ev.timestamp_utc.astimezone(resolve_configured_timezone(tz_override=tz_override, ref_utc=ev.timestamp_utc)).date()
        is_past = ev.timestamp_utc <= utc_now
        if ev.actual is not None or is_past:
            status_badge = "✅ RELEASED TODAY" if local_ev_date == local_now.date() else "✅ RELEASED"
        elif local_ev_date == local_now.date():
            status_badge = "⏳ UPCOMING TODAY"
        else:
            status_badge = "📅 SCHEDULED"

        metric_str = (" | " + " | ".join(metrics)) if metrics else ""
        return f"- {flag} **{ev.title} ({country_name})** — {ts_str}{metric_str} [{status_badge}]"

    def format_macro_calendar_report(
        self,
        query: str = "",
        region: str = "",
        now: Optional[datetime] = None,
        tz_override: Optional[str] = None,
        live_rate_quotes: Optional[Dict[str, object]] = None,
    ) -> str:
        """Build a complete, local-time-first Central Bank & Economic Calendar Intelligence report."""
        local_now = get_local_now(now=now, tz_override=tz_override)
        utc_now = local_now.astimezone(timezone.utc)
        local_abbr = _short_tz_abbr(local_now)

        events, fomc_schedule, boc_history, boc_schedule = self.get_merged_calendar_events(utc_now)

        q_low = f"{query} {region}".lower()
        focus_us = bool(re.search(r"\b(?:fed\b|fomc|federal\s+reserve|powell|united\s+states|\bus\b|american)\b", q_low))
        focus_ca = bool(re.search(r"\b(?:bank\s+of\s+canada|\bboc\b|canada|canadian|macklem|overnight\s+rate)\b", q_low))

        sections: List[str] = [
            f"[{build_reference_clock(now=local_now, tz_override=tz_override)}]",
            "=== 🏦 CENTRAL BANK RATE DECISIONS & ECONOMIC CALENDAR INTELLIGENCE ===",
            f"- **User / Bot Local Timezone:** **{local_abbr}** (All event times below are pre-converted to **{local_abbr}** first, alongside Eastern Time `ET` and `UTC`).",
        ]

        # 1. TODAY'S CENTRAL BANK & HIGH-IMPACT EVENTS BANNER
        def _ev_local_date(ev: MacroCalendarEvent) -> date:
            ltz = resolve_configured_timezone(tz_override=tz_override, ref_utc=ev.timestamp_utc)
            return ev.timestamp_utc.astimezone(ltz).date()

        todays_events = [e for e in events if _ev_local_date(e) == local_now.date()]
        todays_cb = [e for e in todays_events if e.category in ("rate_decision", "central_bank")]
        todays_other = [e for e in todays_events if e.category not in ("rate_decision", "central_bank")]

        if todays_cb or todays_other:
            today_lines = [
                f"=== 🚨 TODAY'S SCHEDULED DECISIONS & RELEASES ({local_now.strftime('%A, %b %d, %Y')}) ==="
            ]
            if todays_cb:
                today_lines.append("🔥 **CENTRAL BANK RATE DECISION DAY TODAY:**")
                for ev in todays_cb:
                    today_lines.append(self.format_event_line(ev, local_now, tz_override=tz_override))
            if todays_other:
                today_lines.append("📊 **Other Major Macro Releases Today:**")
                for ev in todays_other[:8]:
                    today_lines.append(self.format_event_line(ev, local_now, tz_override=tz_override))
            sections.append("\n".join(today_lines))
        else:
            sections.append(
                f"=== 📅 TODAY'S CENTRAL BANK STATUS ({local_now.strftime('%A, %b %d, %Y')}) ===\n"
                "- No G10 central bank interest rate decision is scheduled on today's calendar."
            )

        # 2. FEDERAL RESERVE (FOMC) RATE DECISION & SCHEDULE SECTION
        fomc_events = [
            e for e in events
            if e.country == "US" and e.category in ("rate_decision", "central_bank")
        ]
        fomc_lines = ["=== 🇺🇸 US FEDERAL RESERVE (FOMC) INTEREST RATE DECISION & SCHEDULE ==="]
        fomc_lines.append(
            f"- **Standard FOMC Release Time:** Rate decisions & FOMC Statements are always released on **Day 2 of the meeting at 2:00 PM Eastern Time**, which is **12:00 PM {local_abbr} (Local Time)** (18:00 UTC during EDT / 19:00 UTC during EST). Chair Jerome Powell's Press Conference follows 30 minutes later at **12:30 PM {local_abbr} (2:30 PM ET)**."
        )
        if fomc_events:
            for ev in fomc_events:
                fomc_lines.append(self.format_event_line(ev, local_now, tz_override=tz_override))
        if fomc_schedule:
            upcoming_fomc = [m for m in fomc_schedule if m["decision_date"] >= local_now.date()]  # type: ignore[operator]
            past_fomc = [m for m in fomc_schedule if m["decision_date"] < local_now.date()]  # type: ignore[operator]
            if upcoming_fomc:
                sched_items: List[str] = []
                for m in upcoming_fomc[:4]:
                    dec_utc = m["decision_utc"]  # type: ignore[assignment]
                    ts_lbl = _format_event_timestamp_multi_tz(dec_utc, local_now, tz_override=tz_override)
                    sep_tag = " *(includes Economic Projections / Dot Plot)*" if m.get("has_projections") else ""
                    sched_items.append(
                        f"  • **{m['decision_date'].strftime('%B')} {m['days_label']}, {m['year']} Meeting:** Decision on {ts_lbl}{sep_tag}"  # type: ignore[union-attr]
                    )
                fomc_lines.append("- **Official Remaining/Upcoming FOMC Meeting Schedule:**\n" + "\n".join(sched_items))
            elif past_fomc:
                last_m = past_fomc[-1]
                fomc_lines.append(
                    f"- **Most Recent FOMC Meeting:** {last_m['decision_date'].strftime('%b %d, %Y')}"  # type: ignore[union-attr]
                )

        # 3. BANK OF CANADA (BoC) OVERNIGHT RATE DECISION & SCHEDULE SECTION
        boc_events = [
            e for e in events
            if e.country == "CA" and e.category in ("rate_decision", "central_bank")
        ]
        boc_lines = ["=== 🇨🇦 BANK OF CANADA (BoC) INTEREST RATE DECISION & SCHEDULE ==="]
        boc_lines.append(
            f"- **Standard BoC Release Time:** Bank of Canada Overnight Rate decisions are released at **9:45 AM Eastern Time**, which is **7:45 AM {local_abbr} (Local Time)**, with Monetary Policy Report (MPR) Press Conferences at **8:30 AM {local_abbr} (10:30 AM ET)**."
        )
        if boc_events:
            for ev in boc_events:
                boc_lines.append(self.format_event_line(ev, local_now, tz_override=tz_override))
        if boc_history:
            hist_str = " | ".join(
                f"**{h['date']}:** {h['target']} ({h['change']})"
                for h in boc_history[:5]
            )
            boc_lines.append(f"- **Official Bank of Canada Target Overnight Rate History:** {hist_str}")
        if boc_schedule:
            upcoming_boc = [b for b in boc_schedule if b["decision_date"] >= local_now.date()]  # type: ignore[operator]
            if upcoming_boc:
                b_items: List[str] = []
                for b in upcoming_boc[:4]:
                    dec_utc = b["decision_utc"]  # type: ignore[assignment]
                    ts_lbl = _format_event_timestamp_multi_tz(dec_utc, local_now, tz_override=tz_override)
                    mpr_tag = " *(+ Monetary Policy Report & 8:30 AM Local / 10:30 AM ET Press Conference)*" if b.get("has_mpr") else ""
                    b_items.append(f"  • **{b['decision_date'].strftime('%b %d, %Y')}:** Decision on {ts_lbl}{mpr_tag}")  # type: ignore[union-attr]
                boc_lines.append("- **Official Remaining/Upcoming BoC Announcement Dates:**\n" + "\n".join(b_items))

        if focus_ca and not focus_us:
            sections.append("\n".join(boc_lines))
            sections.append("\n".join(fomc_lines))
        else:
            sections.append("\n".join(fomc_lines))
            sections.append("\n".join(boc_lines))

        # 4. OTHER GLOBAL CENTRAL BANKS (ECB, BoE, BoJ, RBA, RBNZ, SNB)
        other_cb_events = [
            e for e in events
            if e.country not in ("US", "CA") and e.category in ("rate_decision", "central_bank")
        ]
        if other_cb_events:
            g10_lines = ["=== 🌍 OTHER G10 CENTRAL BANK DECISIONS (ECB, BoE, BoJ, RBA, RBNZ) ==="]
            for ev in other_cb_events[:10]:
                g10_lines.append(self.format_event_line(ev, local_now, tz_override=tz_override))
            sections.append("\n".join(g10_lines))

        # 5. HIGH-IMPACT MACROECONOMIC INDICATORS (CPI, Jobs/NFP, GDP, PCE, Retail Sales — US/CA & Near-Term Priority)
        near_cutoff_past = utc_now - timedelta(days=2)
        near_cutoff_future = utc_now + timedelta(days=14)
        macro_events = [
            e for e in events
            if (e.category in ("inflation", "labor", "growth") or (e.importance >= 1 and e.category == "macro"))
            and near_cutoff_past <= e.timestamp_utc <= near_cutoff_future
            and (e.country in ("US", "CA") or e.importance >= 1)
        ]
        macro_events.sort(key=lambda e: (0 if e.country in ("US", "CA") else 1, e.timestamp_utc))
        if macro_events:
            macro_lines = ["=== 📊 HIGH-IMPACT MACROECONOMIC RELEASES (CPI, JOBS, GDP, PCE) ==="]
            for ev in macro_events[:10]:
                macro_lines.append(self.format_event_line(ev, local_now, tz_override=tz_override))
            sections.append("\n".join(macro_lines))

        # 6. LIVE TREASURY YIELDS, FX & BENCHMARK REACTION
        if live_rate_quotes:
            q_lines = ["=== 📈 LIVE RATES, FX & MARKET BENCHMARK REACTION ==="]
            for sym, lbl in (
                ("^TNX", "US 10-Year Treasury Yield"),
                ("^IRX", "US 13-Week Treasury Bill Yield"),
                ("CADUSD=X", "Canadian Dollar (CAD/USD)"),
                ("USDCAD=X", "USD/CAD Exchange Rate"),
                ("^GSPTSE", "S&P/TSX Composite (Canada)"),
                ("^GSPC", "S&P 500 (US)"),
                ("GC=F", "Gold Futures (USD/oz)"),
            ):
                q_obj = live_rate_quotes.get(sym)
                price = getattr(q_obj, "price", 0.0) if q_obj else 0.0
                chg_pct = getattr(q_obj, "change_percent", 0.0) if q_obj else 0.0
                if price and price > 0:
                    if sym in ("^TNX", "^IRX"):
                        q_lines.append(f"- **{lbl} (`{sym}`)**: **{price:.2f}%** ({chg_pct:+.2f}%)")
                    elif "CAD" in sym:
                        q_lines.append(f"- **{lbl} (`{sym}`)**: **{price:.4f}** ({chg_pct:+.2f}%)")
                    else:
                        q_lines.append(f"- **{lbl} (`{sym}`)**: **{price:,.2f}** ({chg_pct:+.2f}%)")
            if len(q_lines) > 1:
                sections.append("\n".join(q_lines))

        return "\n\n".join(sections)

