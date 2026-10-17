"""
Centralized Time & Timezone Awareness Utilities.

Handles BOT_TIMEZONE env resolution, US/EU Daylight Saving Time calculations,
multi-zone reference clocks, and compact inline timestamp enrichment on tool outputs.
"""

import os
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

_FIXED_TZ_MAP: dict[str, tuple[int, int, str]] = {
    "UTC": (0, 0, "UTC"),
    "GMT": (0, 0, "GMT"),
    "Z": (0, 0, "UTC"),
    "BST": (1, 0, "BST"),
    "CET": (1, 0, "CET"),
    "CEST": (2, 0, "CEST"),
    "EET": (2, 0, "EET"),
    "EEST": (3, 0, "EEST"),
    "EDT": (-4, 0, "EDT"),
    "EST": (-5, 0, "EST"),
    "CDT": (-5, 0, "CDT"),
    "CST": (-6, 0, "CST"),
    "MDT": (-6, 0, "MDT"),
    "MST": (-7, 0, "MST"),
    "PDT": (-7, 0, "PDT"),
    "PST": (-8, 0, "PST"),
}

_WINDOWS_TZ_ABBR_MAP: dict[str, str] = {
    "Coordinated Universal Time": "UTC",
    "GMT Standard Time": "GMT",
    "GMT Daylight Time": "BST",
    "W. Europe Standard Time": "CET",
    "W. Europe Daylight Time": "CEST",
    "Central European Standard Time": "CET",
    "Central European Summer Time": "CEST",
    "Central Europe Standard Time": "CET",
    "Central Europe Daylight Time": "CEST",
    "Romance Standard Time": "CET",
    "Romance Daylight Time": "CEST",
    "E. Europe Standard Time": "EET",
    "E. Europe Daylight Time": "EEST",
    "Eastern Standard Time": "EST",
    "Eastern Daylight Time": "EDT",
    "Central Standard Time": "CST",
    "Central Daylight Time": "CDT",
    "Mountain Standard Time": "MST",
    "Mountain Daylight Time": "MDT",
    "US Mountain Standard Time": "MST",
    "Pacific Standard Time": "PST",
    "Pacific Daylight Time": "PDT",
}

_MONTH_NUMS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the n-th occurrence (1-indexed) of weekday (0=Mon..6=Sun) in a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_sunday_of_month(year: int, month: int) -> date:
    """Return the last Sunday of the given month."""
    if month == 12:
        last_day = date(year, 12, 31)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last_day.weekday() - 6) % 7
    return last_day - timedelta(days=offset)


def _is_us_dst(utc_dt: datetime) -> bool:
    """US Daylight Saving Time: 2nd Sunday in March (07:00 UTC) to 1st Sunday in November (06:00 UTC)."""
    y = utc_dt.year
    start = datetime.combine(_nth_weekday_of_month(y, 3, 6, 2), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=7)
    end = datetime.combine(_nth_weekday_of_month(y, 11, 6, 1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=6)
    return start <= utc_dt.astimezone(timezone.utc) < end


def _is_eu_dst(utc_dt: datetime) -> bool:
    """EU Summer Time: Last Sunday in March (01:00 UTC) to Last Sunday in October (01:00 UTC)."""
    y = utc_dt.year
    start = datetime.combine(_last_sunday_of_month(y, 3), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=1)
    end = datetime.combine(_last_sunday_of_month(y, 10), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=1)
    return start <= utc_dt.astimezone(timezone.utc) < end


def _short_tz_abbr(dt: datetime) -> str:
    """Return a clean, compact timezone abbreviation (e.g. 'MDT', 'CEST', 'UTC') even on Windows."""
    raw = (dt.tzname() or "").strip()
    if not raw:
        return "UTC"
    if raw in _WINDOWS_TZ_ABBR_MAP:
        return _WINDOWS_TZ_ABBR_MAP[raw]
    if len(raw) <= 5 and " " not in raw:
        return raw
    initials = "".join(w[0].upper() for w in raw.split() if w and w[0].isalpha())
    return initials or raw


def _resolve_tz_token(tz_str: str, ref_utc: datetime | None = None) -> tzinfo | None:
    """Resolve an IANA name, timezone abbreviation, or UTC±N offset into a Python tzinfo."""
    clean = (tz_str or "").strip()
    if not clean:
        return None
    ref = (ref_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    up = clean.upper()

    # 1. Fixed abbreviation table
    if up in _FIXED_TZ_MAP:
        h, m, label = _FIXED_TZ_MAP[up]
        return timezone(timedelta(hours=h, minutes=m), label)

    # 2. UTC±HH[:MM] or GMT±HH[:MM]
    m_off = re.match(r'^(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::(\d{2}))?$', up)
    if m_off:
        sign = 1 if m_off.group(1) == "+" else -1
        hh = int(m_off.group(2))
        mm = int(m_off.group(3) or 0)
        label = f"UTC{m_off.group(1)}{hh:02d}:{mm:02d}" if mm else f"UTC{m_off.group(1)}{hh}"
        return timezone(timedelta(minutes=sign * (hh * 60 + mm)), label)

    # 3. Dynamic DST-aware regional aliases & IANA names (works even on Windows without tzdata)
    low = clean.lower().replace(" ", "_")
    us_dst = _is_us_dst(ref)
    eu_dst = _is_eu_dst(ref)

    if up == "ET" or low in ("america/new_york", "america/toronto", "america/detroit", "us/eastern"):
        return timezone(timedelta(hours=-4 if us_dst else -5), "EDT" if us_dst else "EST")
    if up == "CT" or low in ("america/chicago", "america/winnipeg", "us/central"):
        return timezone(timedelta(hours=-5 if us_dst else -6), "CDT" if us_dst else "CST")
    if up == "MT" or low in ("america/edmonton", "america/denver", "america/calgary", "us/mountain"):
        return timezone(timedelta(hours=-6 if us_dst else -7), "MDT" if us_dst else "MST")
    if low in ("america/phoenix", "us/arizona"):
        return timezone(timedelta(hours=-7), "MST")
    if up == "PT" or low in ("america/los_angeles", "america/vancouver", "us/pacific"):
        return timezone(timedelta(hours=-7 if us_dst else -8), "PDT" if us_dst else "PST")
    if up in ("CE", "CET/CEST", "EUROPE") or low in (
        "europe/berlin", "europe/paris", "europe/sarajevo", "europe/zagreb", "europe/belgrade",
        "europe/vienna", "europe/rome", "europe/madrid", "europe/amsterdam", "europe/warsaw",
        "europe/stockholm", "europe/zurich", "europe/prague", "europe/budapest", "europe/brussels",
    ):
        return timezone(timedelta(hours=2 if eu_dst else 1), "CEST" if eu_dst else "CET")
    if up == "UK" or low in ("europe/london", "europe/dublin"):
        return timezone(timedelta(hours=1 if eu_dst else 0), "BST" if eu_dst else "GMT")
    if low in ("europe/athens", "europe/bucharest", "europe/helsinki", "europe/kyiv", "europe/sofia"):
        return timezone(timedelta(hours=3 if eu_dst else 2), "EEST" if eu_dst else "EET")

    # 4. Try standard library ZoneInfo if available on the host OS
    if ZoneInfo is not None:
        try:
            zi = ZoneInfo(clean)
            dt_test = ref.astimezone(zi)
            return timezone(dt_test.utcoffset() or timedelta(0), _short_tz_abbr(dt_test))
        except Exception:
            pass
    return None


def resolve_configured_timezone(tz_override: str | None = None, ref_utc: datetime | None = None) -> tzinfo:
    """Resolve BOT_TIMEZONE env override (or explicit tz_override), falling back to system local timezone."""
    raw = tz_override if tz_override is not None else os.getenv("BOT_TIMEZONE", "")
    raw = (raw or "").strip()
    if raw:
        resolved = _resolve_tz_token(raw, ref_utc=ref_utc)
        if resolved is not None:
            return resolved
    # Default: host system local timezone
    sys_dt = (ref_utc or datetime.now(timezone.utc)).astimezone()
    abbr = _short_tz_abbr(sys_dt)
    offset = sys_dt.utcoffset() or timedelta(0)
    return timezone(offset, abbr)


def get_local_now(now: datetime | None = None, tz_override: str | None = None) -> datetime:
    """Return the current datetime projected into the bot's configured local timezone (or system time if blank)."""
    if now is None:
        utc_now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        utc_now = now.astimezone().astimezone(timezone.utc)
    else:
        utc_now = now.astimezone(timezone.utc)
    local_tz = resolve_configured_timezone(tz_override=tz_override, ref_utc=utc_now)
    return utc_now.astimezone(local_tz)


def build_reference_clock(now: datetime | None = None, tz_override: str | None = None) -> str:
    """Build a 1-line multi-zone reference clock covering Local, US (ET/PT), Europe (CET/CEST), and UTC."""
    local_dt = get_local_now(now=now, tz_override=tz_override)
    utc_dt = local_dt.astimezone(timezone.utc)
    et_tz = _resolve_tz_token("ET", ref_utc=utc_dt) or timezone(timedelta(hours=-4), "EDT")
    pt_tz = _resolve_tz_token("PT", ref_utc=utc_dt) or timezone(timedelta(hours=-7), "PDT")
    eu_tz = _resolve_tz_token("Europe/Berlin", ref_utc=utc_dt) or timezone(timedelta(hours=2), "CEST")

    et_dt = utc_dt.astimezone(et_tz)
    pt_dt = utc_dt.astimezone(pt_tz)
    eu_dt = utc_dt.astimezone(eu_tz)

    local_abbr = _short_tz_abbr(local_dt)
    local_time = local_dt.strftime("%I:%M %p").lstrip("0")
    local_part = f"{local_dt.strftime('%a, %b %d, %Y')} at {local_time} {local_abbr} (Local)"

    def _fmt_us(dt: datetime) -> str:
        t_str = dt.strftime("%I:%M %p").lstrip("0")
        abbr = _short_tz_abbr(dt)
        if dt.date() != local_dt.date():
            return f"{dt.strftime('%a %b %d')} {t_str} {abbr}"
        return f"{t_str} {abbr}"

    eu_abbr = _short_tz_abbr(eu_dt)
    eu_part = f"{eu_dt.strftime('%a, %b %d, %Y')} at {eu_dt.strftime('%H:%M')} {eu_abbr} (Europe)"
    utc_part = f"{utc_dt.strftime('%a, %b %d, %Y')} at {utc_dt.strftime('%H:%M')} UTC"

    parts = [local_part]
    if local_abbr not in ("EDT", "EST"):
        parts.append(_fmt_us(et_dt))
    if local_abbr not in ("PDT", "PST"):
        parts.append(_fmt_us(pt_dt))
    if local_abbr not in ("CET", "CEST"):
        parts.append(eu_part)
    if local_abbr != "UTC":
        parts.append(utc_part)

    return "🕒 Current Time: " + " | ".join(parts)


def _format_relative_delta(event_utc: datetime, now_utc: datetime) -> str:
    """Format a compact human-readable countdown or elapsed time (e.g. 'in 2h 15m', '40m ago')."""
    total_sec = int(round((event_utc - now_utc).total_seconds()))
    is_past = total_sec < 0
    abs_min = abs(total_sec) // 60

    if abs_min < 2:
        return "now"
    days, rem_min = divmod(abs_min, 1440)
    hours, mins = divmod(rem_min, 60)

    if days >= 2:
        span = f"{days}d {hours}h" if hours else f"{days}d"
    elif days == 1:
        span = f"1d {hours}h" if hours else "1d"
    elif hours > 0:
        span = f"{hours}h {mins}m" if mins else f"{hours}h"
    else:
        span = f"{mins}m"

    return f"{span} ago" if is_past else f"in {span}"


_SECTION_DATE_HEADER_RE = re.compile(
    r'(?i)(?:📅|---|\bDates?:)\s*.*?\b(?:'
    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?'
    r'|(\d{4})-(\d{2})-(\d{2})'
    r')\b'
)

_TIMESTAMP_TOKEN_RE = re.compile(
    r'(?P<iso>\b(?P<iso_y>\d{4})-(?P<iso_m>\d{2})-(?P<iso_d>\d{2})[T\s](?P<iso_t>\d{2}:\d{2})(?::\d{2})?(?:Z|\s*UTC)\b)'
    r'|(?P<md>(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+)?'
    r'(?P<md_mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(?P<md_day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(?P<md_year>\d{4}))?'
    r'\s*(?:at\b|-|–|,)?\s*(?P<md_time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\s*(?P<md_tz>UTC|GMT|BST|CEST|CET|EEST|EET|EDT|EST|CDT|CST|MDT|MST|PDT|PST|ET|CT|MT|PT)\b)'
    r'|(?P<sl>\b(?P<sl_m>\d{1,2})/(?P<sl_d>\d{1,2})(?:/(?P<sl_y>\d{2,4}))?\s*(?:at\b|-|–|,)?\s*(?P<sl_time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\s*(?P<sl_tz>UTC|GMT|BST|CEST|CET|EEST|EET|EDT|EST|CDT|CST|MDT|MST|PDT|PST|ET|CT|MT|PT)\b)'
    r'|(?P<to>\b(?P<to_time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)\s*(?P<to_tz>UTC|GMT|BST|CEST|CET|EEST|EET|EDT|EST|CDT|CST|MDT|MST|PDT|PST|ET|CT|MT|PT)\b)',
    re.IGNORECASE,
)


def _parse_clock_str(clock_raw: str) -> tuple[int, int] | None:
    """Parse '10:00 PM', '19:00', or '14:30:00' into (hour_24, minute)."""
    s = (clock_raw or "").strip().upper()
    m = re.match(r'^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?$', s)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    ampm = m.group(3)
    if ampm == "PM" and hh < 12:
        hh += 12
    elif ampm == "AM" and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def _infer_nearest_year(mon: int, day: int, ref_date: date) -> int:
    """Infer year for a yearless month/day so Dec->Jan and Jan->Dec schedules don't jump 11 months."""
    yr = ref_date.year
    try:
        cand = date(yr, mon, day)
        diff = (cand - ref_date).days
        if diff < -180:
            return yr + 1
        if diff > 180:
            return yr - 1
    except ValueError:
        pass
    return yr


MAX_TURN_TOOL_CHARS = 10000
_RAW_HTML_DOC_RE = re.compile(r'(?is)<!doctype\s+html\b.*?</html>|<html\b.*?</html>')


def _sanitize_and_bound_tool_output(text: str, max_chars: int = MAX_TURN_TOOL_CHARS) -> str:
    """Guard the LLM context window against accidental raw HTML dumps or oversized tool payloads,
    while preserving any trailing '=== ... INSTRUCTIONS ... ===' footer block."""
    if not text:
        return text
    if "<!doctype html" in text.lower() or "<html" in text.lower():
        def _strip_html(m: re.Match) -> str:
            raw = m.group(0)
            raw = re.sub(r'(?is)<(?:script|style|head)\b.*?</(?:script|style|head)>', ' ', raw)
            raw = re.sub(r'<[^>]+>', ' ', raw)
            return re.sub(r'\s+', ' ', raw).strip()[:600]
        text = _RAW_HTML_DOC_RE.sub(_strip_html, text)
    if len(text) <= max_chars:
        return text
    m_inst = re.search(r'\n(===\s*(?:CRITICAL|SKILL)\s+INSTRUCTIONS\b[\s\S]*)$', text)
    if m_inst:
        footer = m_inst.group(1)
        head_budget = max(1000, max_chars - len(footer) - 80)
        return text[:head_budget].rstrip() + "\n...[tool output truncated to fit context window]...\n\n" + footer
    return text[:max_chars].rstrip() + "\n...[tool output truncated to fit context window]..."


def enrich_tool_output_with_time_context(
    tool_output: str,
    now: datetime | None = None,
    tz_override: str | None = None,
) -> str:
    """
    Centrally enrich any tool output that contains schedule/article timestamps with:
      1. A 1-line multi-zone reference clock header (`🕒 Current Time: ...`).
      2. Compact inline conversions (`(Today 8:00 PM MDT / Fri 04:00 CEST, in 15m)`)
         so the LLM never miscalculates timezone offsets, European dates, or UTC midnight rollovers.
    """
    if not tool_output or not isinstance(tool_output, str):
        return tool_output
    tool_output = _sanitize_and_bound_tool_output(tool_output)
    if (
        tool_output.startswith("🕒 Current Time:")
        or tool_output.startswith("[🕒 Current Time:")
        or "PARALLEL_EXECUTION_BLOCKED" in tool_output
        or "=== VERIFIED YOUTUBE" in tool_output
        or "=== TOP " in tool_output
    ):
        return tool_output

    local_now = get_local_now(now=now, tz_override=tz_override)
    utc_now = local_now.astimezone(timezone.utc)
    local_abbr = _short_tz_abbr(local_now)

    annotated_count = 0
    current_section_date: date | None = None
    out_lines: list[str] = []

    for line in tool_output.splitlines():
        # Update section date if this line is a schedule date header
        m_hdr = _SECTION_DATE_HEADER_RE.search(line)
        if m_hdr:
            try:
                if m_hdr.group(1):
                    mon = _MONTH_NUMS[m_hdr.group(1)[:3].lower()]
                    day = int(m_hdr.group(2))
                    yr = int(m_hdr.group(3)) if m_hdr.group(3) else _infer_nearest_year(mon, day, local_now.date())
                    current_section_date = date(yr, mon, day)
                elif m_hdr.group(4):
                    current_section_date = date(int(m_hdr.group(4)), int(m_hdr.group(5)), int(m_hdr.group(6)))
            except Exception:
                pass

        # Skip lines that were already annotated or contain pre-formatted (UTC | Local) or Discord epoch tags
        if re.search(r'<t:\d+:[a-zA-Z]>', line) or re.search(rf'\bUTC\s*\|\s*[^)\n]*\b{re.escape(local_abbr)}\b', line):
            annotated_count += 1
            out_lines.append(line)
            continue
        if f"{local_abbr} /" in line or "🕒 Current Time:" in line:
            out_lines.append(line)
            continue

        def _replace_ts(m: re.Match) -> str:
            nonlocal annotated_count
            full_match = m.group(0)
            try:
                tz_tok: str | None = None
                if m.group("iso"):
                    yr = int(m.group("iso_y"))
                    mon = int(m.group("iso_m"))
                    day = int(m.group("iso_d"))
                    hm = _parse_clock_str(m.group("iso_t"))
                    if not hm:
                        return full_match
                    src_tz: tzinfo = timezone.utc
                elif m.group("md"):
                    mon = _MONTH_NUMS[m.group("md_mon")[:3].lower()]
                    day = int(m.group("md_day"))
                    yr = int(m.group("md_year")) if m.group("md_year") else _infer_nearest_year(mon, day, local_now.date())
                    hm = _parse_clock_str(m.group("md_time"))
                    if not hm:
                        return full_match
                    tz_tok = m.group("md_tz")
                elif m.group("sl"):
                    mon = int(m.group("sl_m"))
                    day = int(m.group("sl_d"))
                    raw_y = m.group("sl_y")
                    yr = (2000 + int(raw_y) if len(raw_y) == 2 else int(raw_y)) if raw_y else _infer_nearest_year(mon, day, local_now.date())
                    hm = _parse_clock_str(m.group("sl_time"))
                    if not hm:
                        return full_match
                    tz_tok = m.group("sl_tz")
                else:
                    hm = _parse_clock_str(m.group("to_time"))
                    if not hm:
                        return full_match
                    tz_tok = m.group("to_tz")
                    prelim_tz = _resolve_tz_token(tz_tok, ref_utc=utc_now) or timezone.utc
                    base_d = current_section_date or utc_now.astimezone(prelim_tz).date()
                    yr, mon, day = base_d.year, base_d.month, base_d.day

                if tz_tok is not None:
                    est_utc = datetime(yr, mon, day, hm[0], hm[1], tzinfo=timezone.utc)
                    src_tz = _resolve_tz_token(tz_tok, ref_utc=est_utc) or timezone.utc
                    prelim_utc = datetime(yr, mon, day, hm[0], hm[1], tzinfo=src_tz).astimezone(timezone.utc)
                    src_tz = _resolve_tz_token(tz_tok, ref_utc=prelim_utc) or src_tz

                src_dt = datetime(yr, mon, day, hm[0], hm[1], tzinfo=src_tz)
                ev_utc = src_dt.astimezone(timezone.utc)
                ev_local_tz = resolve_configured_timezone(tz_override=tz_override, ref_utc=ev_utc)
                ev_eu_tz = _resolve_tz_token("Europe/Berlin", ref_utc=ev_utc) or timezone(timedelta(hours=2), "CEST")

                ev_local = ev_utc.astimezone(ev_local_tz)
                ev_local_abbr = _short_tz_abbr(ev_local)
                ev_eu = ev_utc.astimezone(ev_eu_tz)
                eu_abbr = _short_tz_abbr(ev_eu)

                # Format Local time + relative day label
                day_diff = (ev_local.date() - local_now.date()).days
                if day_diff == 0:
                    day_lbl = "Today"
                elif day_diff == 1:
                    day_lbl = f"Tomorrow {ev_local.strftime('%a %b %d')}"
                elif day_diff == -1:
                    day_lbl = "Yesterday"
                else:
                    day_lbl = ev_local.strftime("%a %b %d")

                local_clock = ev_local.strftime("%I:%M %p").lstrip("0")
                local_piece = f"{day_lbl} {local_clock} {ev_local_abbr}"

                # Format European time (unless Local is already CET/CEST)
                if ev_local_abbr in ("CET", "CEST"):
                    zone_str = local_piece
                else:
                    eu_day_prefix = f"{ev_eu.strftime('%a')} " if ev_eu.date() != ev_local.date() else ""
                    eu_piece = f"{eu_day_prefix}{ev_eu.strftime('%H:%M')} {eu_abbr}"
                    zone_str = f"{local_piece} / {eu_piece}"

                rel_str = _format_relative_delta(ev_utc, utc_now)
                annotated_count += 1
                return f"{full_match} ({zone_str}, {rel_str})"
            except Exception:
                return full_match

        out_lines.append(_TIMESTAMP_TOKEN_RE.sub(_replace_ts, line))

    enriched_body = "\n".join(out_lines)
    has_date_context = (
        annotated_count > 0
        or current_section_date is not None
        or bool(re.search(r'\b(?:schedule|fixtures?|kickoff|tournament|grand\s+prix|matchup|scoreboard)\b', tool_output, re.IGNORECASE))
    )
    if not has_date_context:
        return tool_output

    header = f"[{build_reference_clock(now=local_now, tz_override=tz_override)}]"
    return f"{header}\n{enriched_body}"
