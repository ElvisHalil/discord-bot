"""
Condition Evaluators for 'Remind Me When X Happens' Jobs (`src/jobs/evaluators.py`).

Provides zero-LLM deterministic polling evaluators for:
1. Markets / Equities / Crypto / Commodities (`ConditionKind.MARKET_PRICE`)
2. Weather Conditions & Temperature Thresholds (`ConditionKind.WEATHER`)
3. YouTube Channel New Video Uploads via Atom/RSS (`ConditionKind.YOUTUBE_UPLOAD`)
4. Hash-Gated Web & Breaking News Conditions (`ConditionKind.WEB_CONDITION`)
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests

from src.jobs.models import ConditionKind

logger = logging.getLogger(__name__)

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def evaluate_market_condition(spec: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Evaluate a Market / Crypto / Commodity price or % move condition.
    Returns `(triggered, alert_message)`.
    """
    raw_symbol = str(spec.get("symbol") or "").strip().upper()
    operator = str(spec.get("operator") or ">=").strip()
    threshold = float(spec.get("threshold") or 0.0)
    if not raw_symbol:
        return (False, None)

    try:
        from src.skills.markets.aggregators import YahooFinanceAggregator

        agg = YahooFinanceAggregator()
        quotes = agg.fetch_quotes([raw_symbol], ttl=30.0)
        quote = quotes.get(raw_symbol)
        if quote is None:
            resolved_sym, _, _ = agg.resolve_ticker_and_news(raw_symbol)
            if resolved_sym:
                spec["symbol"] = resolved_sym
                quotes = agg.fetch_quotes([resolved_sym], ttl=30.0)
                quote = quotes.get(resolved_sym)
        if quote is None or quote.price <= 0:
            return (False, None)
    except Exception as e:
        logger.debug("Market condition evaluation failed for %s: %s", raw_symbol, e)
        return (False, None)

    price = float(quote.price)
    chg_pct = float(quote.change_percent or 0.0)
    curr = quote.currency or "USD"
    sym_label = f"{quote.name} ({quote.symbol})" if quote.name and quote.name != quote.symbol else quote.symbol

    if operator == ">=" and price >= threshold:
        return (
            True,
            f"📈 **MARKET PRICE ALERT HIT — {sym_label}**\n"
            f"• **Current Price**: **${price:,.2f} {curr}** ({chg_pct:+.2f}% today)\n"
            f"• **Trigger Condition**: Price >= ${threshold:,.2f}",
        )
    if operator == "<=" and price <= threshold:
        return (
            True,
            f"📉 **MARKET PRICE ALERT HIT — {sym_label}**\n"
            f"• **Current Price**: **${price:,.2f} {curr}** ({chg_pct:+.2f}% today)\n"
            f"• **Trigger Condition**: Price <= ${threshold:,.2f}",
        )
    if operator == "pct_up" and chg_pct >= abs(threshold):
        return (
            True,
            f"🚀 **MARKET MOVE ALERT — {sym_label}**\n"
            f"• **Current Move**: **{chg_pct:+.2f}%** today (Price: **${price:,.2f} {curr}**)\n"
            f"• **Trigger Condition**: Gain >= +{abs(threshold):g}%",
        )
    if operator == "pct_down" and chg_pct <= -abs(threshold):
        return (
            True,
            f"🔻 **MARKET MOVE ALERT — {sym_label}**\n"
            f"• **Current Move**: **{chg_pct:+.2f}%** today (Price: **${price:,.2f} {curr}**)\n"
            f"• **Trigger Condition**: Drop >= -{abs(threshold):g}%",
        )
    if operator == "pct_move" and abs(chg_pct) >= abs(threshold):
        return (
            True,
            f"⚡ **MARKET VOLATILITY ALERT — {sym_label}**\n"
            f"• **Current Move**: **{chg_pct:+.2f}%** today (Price: **${price:,.2f} {curr}**)\n"
            f"• **Trigger Condition**: Intraday move >= {abs(threshold):g}%",
        )

    return (False, None)


def evaluate_weather_condition(spec: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Evaluate a Weather condition (rain, snow, storm, temp_below, temp_above).
    Returns `(triggered, alert_message)`.
    """
    location = str(spec.get("location") or "Calgary").strip()
    cond_type = str(spec.get("condition_type") or "").strip().lower()
    threshold_c = spec.get("threshold_c")

    try:
        from src.skills.weather.aggregators import WeatherAggregatorManager

        weather_text = str(WeatherAggregatorManager().get_weather(location, days=1, hours=6) or "")
    except Exception as e:
        logger.debug("Weather condition evaluation failed for %s: %s", location, e)
        return (False, None)

    if not weather_text or "error" in weather_text.lower()[:40]:
        return (False, None)

    low_text = weather_text.lower()
    # Extract current temperature in °C
    m_temp = re.search(r"(-?\d+(?:\.\d+)?)\s*°\s*c\b", low_text)
    cur_temp_c = float(m_temp.group(1)) if m_temp else None

    if cond_type == "temp_below" and threshold_c is not None and cur_temp_c is not None:
        if cur_temp_c <= float(threshold_c):
            return (
                True,
                f"🥶 **WEATHER TEMPERATURE ALERT ({location})**\n"
                f"• **Current Temperature**: **{cur_temp_c:g}°C** (Target: <= {float(threshold_c):g}°C)",
            )
        return (False, None)

    if cond_type == "temp_above" and threshold_c is not None and cur_temp_c is not None:
        if cur_temp_c >= float(threshold_c):
            return (
                True,
                f"🌡️ **WEATHER TEMPERATURE ALERT ({location})**\n"
                f"• **Current Temperature**: **{cur_temp_c:g}°C** (Target: >= {float(threshold_c):g}°C)",
            )
        return (False, None)

    if cond_type == "snow" and re.search(r"\b(?:snow|snowing|flurries|blizzard|sleet)\b", low_text):
        return (
            True,
            f"❄️ **WEATHER ALERT ({location})**: Snow conditions detected!\n• **Latest Report**: {weather_text.splitlines()[0][:220]}",
        )
    if cond_type == "rain" and re.search(r"\b(?:rain|raining|drizzle|showers|downpour)\b", low_text):
        return (
            True,
            f"🌧️ **WEATHER ALERT ({location})**: Rain conditions detected!\n• **Latest Report**: {weather_text.splitlines()[0][:220]}",
        )
    if cond_type == "storm" and re.search(r"\b(?:storm|thunderstorm|lightning|hail|squall)\b", low_text):
        return (
            True,
            f"⛈️ **WEATHER ALERT ({location})**: Storm conditions detected!\n• **Latest Report**: {weather_text.splitlines()[0][:220]}",
        )

    return (False, None)


def resolve_youtube_channel_id(channel_query: str) -> Optional[str]:
    """Resolve a YouTube `@handle`, channel URL, or channel name into a `UC...` channel_id."""
    q = (channel_query or "").strip()
    if not q:
        return None
    if re.fullmatch(r"UC[0-9A-Za-z_-]{22}", q):
        return q

    handle = q if q.startswith("@") else f"@{q.replace(' ', '')}"
    urls_to_try = [
        f"https://www.youtube.com/{handle}",
        f"https://www.youtube.com/results?search_query={quote_plus(q)}&sp=EgIQAg%253D%253D",
    ]
    for u in urls_to_try:
        try:
            resp = requests.get(u, headers=_UA, timeout=6)
            if resp.status_code == 200:
                m_cid = re.search(r'"(?:externalId|channelId|browseId)"\s*:\s*"(UC[0-9A-Za-z_-]{22})"', resp.text)
                if m_cid:
                    return m_cid.group(1)
        except Exception:
            continue
    return None


def evaluate_youtube_condition(spec: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Check a YouTube channel's Atom/RSS feed for a new video upload.
    Returns `(triggered, alert_message)`.
    """
    channel_query = str(spec.get("channel_query") or "").strip()
    channel_id = spec.get("channel_id")
    if not channel_id:
        channel_id = resolve_youtube_channel_id(channel_query)
        if channel_id:
            spec["channel_id"] = channel_id
        else:
            return (False, None)

    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        resp = requests.get(feed_url, headers=_UA, timeout=8)
        if resp.status_code != 200:
            return (False, None)
        root = ET.fromstring(resp.content)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
        }
        channel_title = root.findtext("atom:title", default=channel_query, namespaces=ns)
        entry = root.find("atom:entry", ns)
        if entry is None:
            return (False, None)
        vid = (entry.findtext("yt:videoId", default="", namespaces=ns) or "").strip()
        title = (entry.findtext("atom:title", default="New Video", namespaces=ns) or "").strip()
        if not vid:
            return (False, None)

        last_seen = spec.get("last_seen_video_id")
        if last_seen is None:
            # First poll establishes the baseline latest video unless `trigger_on_first_seen` is set
            spec["last_seen_video_id"] = vid
            if not spec.get("trigger_on_first_seen"):
                return (False, None)

        if vid != last_seen or spec.get("trigger_on_first_seen"):
            spec["last_seen_video_id"] = vid
            spec["trigger_on_first_seen"] = False
            return (
                True,
                f"📺 **NEW YOUTUBE UPLOAD — {channel_title}**\n"
                f"• **Title**: **{title}**\n"
                f"https://www.youtube.com/watch?v={vid}",
            )
    except Exception as e:
        logger.debug("YouTube RSS condition check failed for %s: %s", channel_query, e)

    return (False, None)


def evaluate_web_condition(
    spec: Dict[str, Any],
    llm_check_fn: Optional[Callable[[str, str], bool]] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Evaluate an open-ended web page or news condition using a SHA-256 content-hash gate
    so unchanged pages never invoke the LLM.
    """
    target_url = spec.get("target_url")
    search_query = spec.get("search_query")
    condition_text = str(spec.get("condition_text") or "").strip()
    if not condition_text:
        return (False, None)

    try:
        if target_url:
            from src.tools.web import read_website

            content = str(read_website(target_url) or "")
        else:
            from src.tools.web import search_web

            content = str(search_web(search_query or condition_text, news=True) or "")
    except Exception as e:
        logger.debug("Web condition fetch failed for '%s': %s", condition_text, e)
        return (False, None)

    cleaned = content.split("[SYSTEM INSTRUCTION")[0].strip()
    if not cleaned or cleaned.startswith("❌") or cleaned.startswith("⚠️"):
        return (False, None)

    digest = hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()
    prev_hash = spec.get("last_content_hash")
    if prev_hash and prev_hash == digest:
        # Content has not changed since last poll -> 0ms exit!
        return (False, None)

    spec["last_content_hash"] = digest

    # If caller supplied an LLM boolean verifier, use it; otherwise check deterministic keyword overlap
    matched = False
    if llm_check_fn is not None:
        try:
            matched = bool(llm_check_fn(condition_text, cleaned[:3500]))
        except Exception:
            matched = False
    else:
        stop = {"when", "if", "once", "the", "is", "are", "on", "in", "at", "to", "for", "of", "and", "or", "news", "breaks", "about"}
        tokens = [w for w in re.findall(r"[a-z0-9]{3,}", condition_text.lower()) if w not in stop]
        if tokens:
            low_clean = cleaned.lower()
            hits = sum(1 for t in tokens if t in low_clean)
            matched = hits >= max(1, (len(tokens) + 1) // 2)

    if matched:
        src_line = f"\n• **Source**: <{target_url}>" if target_url else ""
        snippet = " ".join(cleaned.split())[:260]
        return (
            True,
            f"🔔 **CONDITION TRIGGERED**: *{condition_text}*{src_line}\n• **Latest Snippet**: {snippet}...",
        )

    return (False, None)


def evaluate_job_condition(
    spec: Dict[str, Any],
    llm_check_fn: Optional[Callable[[str, str], bool]] = None,
) -> Tuple[bool, Optional[str]]:
    """Dispatch a `condition_spec` dictionary to its domain evaluator."""
    if not isinstance(spec, dict):
        return (False, None)
    kind = spec.get("kind")
    if kind == ConditionKind.MARKET_PRICE:
        return evaluate_market_condition(spec)
    if kind == ConditionKind.WEATHER:
        return evaluate_weather_condition(spec)
    if kind == ConditionKind.YOUTUBE_UPLOAD:
        return evaluate_youtube_condition(spec)
    if kind == ConditionKind.WEB_CONDITION:
        return evaluate_web_condition(spec, llm_check_fn=llm_check_fn)
    return (False, None)

