"""
Universal Live Sports Match Tracker.
Polls active tracked games across any ESPN sport (Soccer, NBA, NHL, NFL, MLB, UFC, etc.)
on an on-demand background asyncio loop, detects kickoff, score changes, red cards,
halftime/period breaks, and full-time, triggers LLM updates in Discord, and automatically
self-terminates once all tracked matches are over or hit the 4-hour safety timeout.
"""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from src.skills.sports.aggregators.espn_utils import (
    TEAM_ALIASES,
    espn_search_auto_discover,
    extract_score_str,
    resolve_espn_team,
)
from src.utils.http import fetch_json

# Context variable set by main.py so LLM tool calls know which Discord channel invoked them
ACTIVE_CHANNEL_ID: ContextVar[Optional[int]] = ContextVar("ACTIVE_CHANNEL_ID", default=None)
_FALLBACK_CHANNEL_ID: Optional[int] = None
# Per-channel memory of the last match/team inspected so "track this game" / "track the game" resolves automatically
_CHANNEL_LAST_MATCH_QUERY: Dict[int, Tuple[str, Optional[str]]] = {}

SAFETY_TIMEOUT_SECONDS: int = 4 * 3600  # 4-hour maximum lifetime per tracked match
DEFAULT_POLL_INTERVAL_SECONDS: int = 25


def set_active_channel_id(channel_id: Optional[int]) -> None:
    """Set the active Discord channel ID for the current turn/thread."""
    global _FALLBACK_CHANNEL_ID
    ACTIVE_CHANNEL_ID.set(channel_id)
    if channel_id is not None:
        _FALLBACK_CHANNEL_ID = channel_id


def get_active_channel_id() -> int:
    """Retrieve the active Discord channel ID (defaults to 0 in standalone unit tests)."""
    cid = ACTIVE_CHANNEL_ID.get()
    if cid is not None:
        return cid
    if _FALLBACK_CHANNEL_ID is not None:
        return _FALLBACK_CHANNEL_ID
    return 0


def record_channel_last_match_query(query: str, sport: Optional[str] = None, channel_id: Optional[int] = None) -> None:
    """Record the most recently discussed team or matchup in a Discord channel."""
    cleaned = _clean_tracking_query(query)
    if not cleaned:
        return
    cid = channel_id if channel_id is not None else get_active_channel_id()
    _CHANNEL_LAST_MATCH_QUERY[cid] = (query.strip(), sport)


def get_channel_last_match_query(channel_id: Optional[int] = None) -> Tuple[str, Optional[str]]:
    """Retrieve the most recently discussed team or matchup in a Discord channel."""
    cid = channel_id if channel_id is not None else get_active_channel_id()
    if cid in _CHANNEL_LAST_MATCH_QUERY:
        return _CHANNEL_LAST_MATCH_QUERY[cid]
    if 0 in _CHANNEL_LAST_MATCH_QUERY:
        return _CHANNEL_LAST_MATCH_QUERY[0]
    for (t_cid, _), m in LIVE_TRACKER.tracked_matches.items():
        if t_cid == cid:
            return (f"{m.home_team} vs {m.away_team}", m.sport_path)
    return ("", None)


@dataclass
class MatchSnapshot:
    """Normalized state of a live or upcoming sports event at a single point in time."""
    state: str = "pre"  # 'pre', 'in', 'post'
    completed: bool = False
    status_name: str = ""  # e.g. 'STATUS_IN_PROGRESS', 'STATUS_HALFTIME', 'STATUS_FINAL'
    status_detail: str = "Scheduled"  # e.g. "33'", "HT", "Q2 04:12", "FT"
    period: int = 0
    home_team: str = "Home"
    away_team: str = "Away"
    home_score: str = "0"
    away_score: str = "0"
    notable_events: List[Tuple[str, str]] = field(default_factory=list)  # [(event_id, description)]
    seen_event_ids: Set[str] = field(default_factory=set)
    stats_summary: str = ""

    @property
    def is_halftime(self) -> bool:
        det_upper = (self.status_detail or "").strip().upper()
        name_upper = (self.status_name or "").strip().upper()
        return (
            "HALFTIME" in name_upper
            or det_upper in ("HT", "HALFTIME", "HALF TIME", "HALF")
            or "HALFTIME" in det_upper
        )

    @property
    def is_period_end(self) -> bool:
        det_upper = (self.status_detail or "").strip().upper()
        name_upper = (self.status_name or "").strip().upper()
        return "END_PERIOD" in name_upper or det_upper.startswith("END OF ")

    @property
    def is_finished(self) -> bool:
        name_upper = (self.status_name or "").strip().upper()
        return (
            self.state == "post"
            or self.completed
            or name_upper in ("STATUS_FINAL", "STATUS_FULL_TIME", "STATUS_FINAL_AET", "STATUS_FINAL_PEN")
        )

    @property
    def is_aborted(self) -> bool:
        name_upper = (self.status_name or "").strip().upper()
        return any(k in name_upper for k in ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "SUSPENDED"))


@dataclass
class TrackedMatch:
    """Represents a live or upcoming match being actively monitored for a Discord channel."""
    channel_id: int
    sport_path: str
    league_code: str
    league_name: str
    event_id: str
    home_team: str
    away_team: str
    last_snapshot: MatchSnapshot
    created_at: float = field(default_factory=time.time)
    last_notified_score_diff: int = 0  # Used for high-scoring basketball swing throttling


def _clean_tracking_query(query: str) -> str:
    """Strip tracking/conversational/pronoun filler words and expand short club/team aliases."""
    clean = (query or "").lower().strip()
    for phrase in (
        "can you track", "please track", "start tracking", "stop tracking",
        "keep me updated on", "keep me posted on", "let me know when",
        "live updates for", "live updates on", "track the", "watch the",
        "monitor the", "follow the", "right now", "currently playing",
        "this game", "that game", "the game", "this match", "that match", "the match",
    ):
        clean = re.sub(rf'\b{re.escape(phrase)}\b', ' ', clean, flags=re.IGNORECASE)
    for word in (
        "track", "tracking", "untrack", "watch", "monitor", "follow",
        "live", "updates", "update", "score", "scores", "game", "games", "match", "matches",
        "current", "currently", "playing", "today", "tonight", "next",
        "the", "this", "that", "it", "same", "again", "now", "right",
        "for", "on", "in", "please", "me", "us",
    ):
        clean = re.sub(rf'\b{re.escape(word)}\b', ' ', clean, flags=re.IGNORECASE)
    clean = re.sub(r'[^\w\s]', ' ', clean)
    clean = ' '.join(clean.split()).strip()
    if not clean:
        return ""

    if clean in TEAM_ALIASES:
        return TEAM_ALIASES[clean]
    tokens = clean.split()
    expanded = []
    for t in tokens:
        alias_val = TEAM_ALIASES.get(t, t)
        if alias_val != t and alias_val in clean:
            expanded.append(t)
        else:
            expanded.append(alias_val)
    return " ".join(expanded)


def extract_snapshot_from_summary(
    summary_data: Dict[str, Any],
    fallback_event: Optional[Dict[str, Any]] = None,
) -> MatchSnapshot:
    """Build a normalized MatchSnapshot from an ESPN /summary?event={id} payload."""
    header_comps = (summary_data or {}).get("header", {}).get("competitions", [])
    comp = header_comps[0] if header_comps else ((fallback_event or {}).get("competitions") or [{}])[0]

    status_type = comp.get("status", {}).get("type", {})
    state = status_type.get("state", "pre")
    completed = bool(status_type.get("completed", False))
    status_name = status_type.get("name", "")
    status_detail = status_type.get("shortDetail") or status_type.get("detail") or "Scheduled"
    period = int(comp.get("status", {}).get("period", 0) or 0)

    competitors = comp.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

    home_team = home.get("team", {}).get("displayName") or home.get("team", {}).get("shortDisplayName") or "Home"
    away_team = away.get("team", {}).get("displayName") or away.get("team", {}).get("shortDisplayName") or "Away"
    home_score = extract_score_str(home.get("score")) or "0"
    away_score = extract_score_str(away.get("score")) or "0"

    team_id_to_name: Dict[str, str] = {}
    for comp_item in competitors:
        tid = str(comp_item.get("team", {}).get("id") or "")
        tname = comp_item.get("team", {}).get("displayName") or comp_item.get("team", {}).get("shortDisplayName") or ""
        if tid and tname:
            team_id_to_name[tid] = tname

    notable_events: List[Tuple[str, str]] = []
    seen_ids: Set[str] = set()

    # 1. Parse soccer/general keyEvents
    for kev in (summary_data or {}).get("keyEvents") or []:
        ev_id = str(kev.get("id") or "")
        ev_type = (kev.get("type", {}).get("text") or "").strip()
        clock = kev.get("clock", {}).get("displayValue", "")
        text = (kev.get("text") or "").strip()
        is_scoring = bool(kev.get("scoringPlay")) or "Goal" in ev_type or "Penalty - Scored" in ev_type
        is_red = "Red Card" in ev_type or "Second Yellow" in ev_type
        if is_scoring or is_red:
            icon = "⚽ GOAL!" if is_scoring else "🟥 RED CARD!"
            desc = f"{icon} {clock} {text or ev_type}".strip()
            key = ev_id or desc
            if key not in seen_ids:
                seen_ids.add(key)
                notable_events.append((key, desc))

    # 2. Parse NFL/MLB/NBA scoringPlays if present
    for sp in (summary_data or {}).get("scoringPlays") or []:
        sp_id = str(sp.get("id") or "")
        clock = sp.get("clock", {}).get("displayValue", "")
        period_num = sp.get("period", {}).get("number", "")
        p_prefix = f"P{period_num} " if period_num else ""
        text = (sp.get("text") or "").strip()
        if text:
            desc = f"🔥 SCORING PLAY ({p_prefix}{clock}): {text}".strip()
            key = sp_id or desc
            if key not in seen_ids:
                seen_ids.add(key)
                notable_events.append((key, desc))

    # 3. Parse NHL / MLB / Soccer goals and scoring plays from `plays` when keyEvents/scoringPlays are absent
    plays_list = (summary_data or {}).get("plays") or []
    if plays_list and not notable_events:
        scoring_plays = [
            p for p in plays_list
            if p.get("scoringPlay") or "goal" in (p.get("type", {}).get("text") or "").lower()
        ]
        # Avoid dumping 100+ basketball jump shots/free throws; NHL/Soccer/MLB/NFL have <= 35 scoring plays
        is_goal_sport = any("goal" in (p.get("type", {}).get("text") or "").lower() for p in scoring_plays)
        if is_goal_sport or len(scoring_plays) <= 35:
            for p in scoring_plays:
                p_id = str(p.get("id") or "")
                ev_type = (p.get("type", {}).get("text") or "").strip()
                clock = p.get("clock", {}).get("displayValue", "")
                p_disp = (
                    p.get("period", {}).get("displayValue")
                    or (f"P{p.get('period', {}).get('number')}" if p.get("period", {}).get("number") else "")
                )
                strength = (p.get("strength", {}).get("text") or "").strip()
                strength_tag = (
                    f" [{strength}]"
                    if strength and strength.lower() not in ("even strength", "none", "regular")
                    else ""
                )
                t_id = str(p.get("team", {}).get("id") or "")
                t_name = team_id_to_name.get(t_id, "")
                team_tag = f" — {t_name}" if t_name else ""
                text = (p.get("text") or ev_type).strip()
                time_label = " ".join(x for x in (p_disp, clock) if x).strip()
                icon = "🚨 GOAL!" if "goal" in ev_type.lower() or is_goal_sport else "🔥 SCORING PLAY"
                desc = f"{icon} ({time_label}{strength_tag}{team_tag}): {text}".strip()
                key = p_id or desc
                if key not in seen_ids:
                    seen_ids.add(key)
                    notable_events.append((key, desc))

                # If `plays` updated a few seconds before `header` on ESPN's CDN, sync the live score
                try:
                    p_home = p.get("homeScore")
                    p_away = p.get("awayScore")
                    if p_home is not None and int(p_home) > int(home_score):
                        home_score = str(int(p_home))
                    if p_away is not None and int(p_away) > int(away_score):
                        away_score = str(int(p_away))
                except (ValueError, TypeError):
                    pass

    # 4. Extract brief team statistics (Soccer, NHL, NFL, NBA)
    stat_parts: List[str] = []
    box_teams = (summary_data or {}).get("boxscore", {}).get("teams", [])
    if len(box_teams) >= 2:
        t1_name = box_teams[0].get("team", {}).get("shortDisplayName") or box_teams[0].get("team", {}).get("displayName", "Team 1")
        t2_name = box_teams[1].get("team", {}).get("shortDisplayName") or box_teams[1].get("team", {}).get("displayName", "Team 2")
        t1_stats = {s.get("name"): s.get("displayValue") for s in box_teams[0].get("statistics", []) if s.get("name")}
        t2_stats = {s.get("name"): s.get("displayValue") for s in box_teams[1].get("statistics", []) if s.get("name")}
        for s_key, s_label in (
            ("possessionPct", "Possession"),
            ("totalShots", "Shots"),
            ("shotsOnTarget", "On Target"),
            ("shotsTotal", "Shots on Goal"),
            ("hits", "Hits"),
            ("powerPlayGoals", "Power Play Goals"),
            ("faceoffPercent", "Faceoffs"),
            ("wonCorners", "Corners"),
            ("totalYards", "Total Yards"),
            ("turnovers", "Turnovers"),
        ):
            if s_key in t1_stats and s_key in t2_stats:
                suffix = "%" if s_key in ("possessionPct", "faceoffPercent") else ""
                stat_parts.append(f"{s_label}: {t1_name} {t1_stats[s_key]}{suffix} - {t2_stats[s_key]}{suffix} {t2_name}")

    return MatchSnapshot(
        state=state,
        completed=completed,
        status_name=status_name,
        status_detail=status_detail,
        period=period,
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        notable_events=notable_events,
        seen_event_ids=seen_ids,
        stats_summary=" | ".join(stat_parts),
    )


def diff_snapshots(tracked: TrackedMatch, curr: MatchSnapshot) -> List[str]:
    """
    Compare previous MatchSnapshot against current MatchSnapshot and return a list
    of human-readable update triggers (empty list if nothing notable changed).
    """
    prev = tracked.last_snapshot
    triggers: List[str] = []

    # 1. Kickoff / Game Start
    if prev.state == "pre" and curr.state == "in":
        triggers.append(
            f"🟢 **KICKOFF / GAME START**: **{curr.away_team}** vs **{curr.home_team}** is now LIVE! "
            f"(Score: {curr.home_team} {curr.home_score} - {curr.away_score} {curr.away_team}, {curr.status_detail})"
        )

    # 2. New notable keyEvents / scoringPlays (Goals, Red Cards, Touchdowns, etc.)
    new_event_descs = [
        desc for ev_id, desc in curr.notable_events if ev_id not in prev.seen_event_ids
    ]

    is_basketball = tracked.sport_path == "basketball"
    score_changed = (curr.home_score != prev.home_score) or (curr.away_score != prev.away_score)

    if is_basketball:
        # For high-scoring basketball, avoid spamming every 2pt basket:
        # Only emit mid-period score alerts if the swing since last notification is >= 10 pts
        # or if it's a lead change in Q4/OT. Quarter ends, Halftime, and Final always fire below.
        try:
            h_int, a_int = int(curr.home_score), int(curr.away_score)
            ph_int, pa_int = int(prev.home_score), int(prev.away_score)
            curr_diff = h_int - a_int
            prev_diff = ph_int - pa_int
            lead_changed = (curr_diff * prev_diff < 0) or (curr_diff == 0 and prev_diff != 0)
            swing = abs(curr_diff - tracked.last_notified_score_diff)
            if score_changed and ((curr.period >= 4 and lead_changed) or swing >= 10):
                tracked.last_notified_score_diff = curr_diff
                triggers.append(
                    f"🏀 **SCORE UPDATE ({curr.status_detail})**: **{curr.home_team}** {curr.home_score} - {curr.away_score} **{curr.away_team}**"
                )
        except ValueError:
            pass
    else:
        # For Soccer, NHL, NFL, MLB, etc., announce every goal, red card, and score change!
        if new_event_descs:
            for d in new_event_descs:
                triggers.append(
                    f"{d} — Score: **{curr.home_team}** {curr.home_score} - {curr.away_score} **{curr.away_team}** ({curr.status_detail})"
                )
        elif score_changed:
            triggers.append(
                f"🚨 **SCORE CHANGE ({curr.status_detail})**: **{curr.home_team}** {curr.home_score} - {curr.away_score} **{curr.away_team}** "
                f"(was {prev.home_score}-{prev.away_score})"
            )

    # 3. Halftime
    if curr.is_halftime and not prev.is_halftime:
        stat_str = f" | Stats: {curr.stats_summary}" if curr.stats_summary else ""
        triggers.append(
            f"⏸️ **HALFTIME REACHED**: **{curr.home_team}** {curr.home_score} - {curr.away_score} **{curr.away_team}**{stat_str}"
        )
    # 4. Second Half / Resumption after Halftime
    elif prev.is_halftime and curr.state == "in" and not curr.is_halftime:
        triggers.append(
            f"▶️ **SECOND HALF UNDERWAY**: **{curr.home_team}** {curr.home_score} - {curr.away_score} **{curr.away_team}** ({curr.status_detail})"
        )
    # 5. End of Period / Quarter (for NHL, NBA, NFL)
    elif curr.is_period_end and not prev.is_period_end and not curr.is_halftime:
        triggers.append(
            f"⏱️ **END OF PERIOD ({curr.status_detail})**: **{curr.home_team}** {curr.home_score} - {curr.away_score} **{curr.away_team}**"
        )

    # 6. Full-Time / Final Whistle
    if curr.is_finished and not prev.is_finished:
        stat_str = f" | Final Stats: {curr.stats_summary}" if curr.stats_summary else ""
        triggers.append(
            f"🏁 **FULL-TIME / FINAL WHISTLE ({curr.status_detail})**: **{curr.home_team}** {curr.home_score} - {curr.away_score} **{curr.away_team}**{stat_str}"
        )

    return triggers


def resolve_live_or_next_event(
    query: str,
    sport: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Resolve a team or matchup query into a trackable ESPN event dict:
    {
        'sport_path': str,
        'league_code': str,
        'league_name': str,
        'event_id': str,
        'home_team': str,
        'away_team': str,
        'snapshot': MatchSnapshot,
    }
    """
    from datetime import datetime, timezone
    from src.skills.sports.aggregators.soccer import SoccerAggregator
    from src.skills.sports.aggregators.universal_espn import UniversalEspnAggregator

    target = _clean_tracking_query(query)
    if not target:
        return None
    sport_clean = (sport or "").lower().strip()
    sport_hint = None

    club_league = None
    for club, lg in sorted(SoccerAggregator.CLUB_LEAGUE_HINTS.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf'\b{re.escape(club)}\b', target.lower()):
            club_league = SoccerAggregator.LEAGUES.get(lg, {}).get("code", lg)
            sport_hint = "soccer"
            break

    if not sport_hint:
        if sport_clean in ("soccer", "epl", "ucl", "laliga", "seriea", "bundesliga", "ligue1", "mls"):
            sport_hint = "soccer"
        elif sport_clean in ("nhl", "hockey", "ice hockey") or re.search(r'\b(?:nhl|hockey)\b', target.lower()):
            sport_hint = "hockey"
            club_league = "nhl"
        elif sport_clean in ("nba", "basketball", "hoops") or re.search(r'\b(?:nba|basketball)\b', target.lower()):
            sport_hint = "basketball"
            club_league = "nba"

    # Strip sport/league name tokens from candidate targets so team resolution is clean
    target_for_team = re.sub(r'\b(?:hockey|nhl|basketball|nba|soccer|football|baseball|mlb|nfl)\b', ' ', target, flags=re.IGNORECASE)
    target_for_team = " ".join(target_for_team.split()).strip() or target

    # If target is a matchup (e.g. "as roma vs real madrid"), try each side so team auto-discovery succeeds
    candidate_targets = [target_for_team] if target_for_team == target else [target_for_team, target]
    if re.search(r'\b(?:vs|v)\b', target_for_team, flags=re.IGNORECASE):
        parts = [p.strip() for p in re.split(r'\b(?:vs|v)\b', target_for_team, flags=re.IGNORECASE) if p.strip()]
        candidate_targets = parts + candidate_targets

    disc = None
    for cand_t in candidate_targets:
        disc = espn_search_auto_discover(cand_t, sport_hint=sport_hint)
        if disc and (disc.get("team_id") or disc.get("id")):
            break

    sport_path = (disc or {}).get("sport") or sport_hint
    league_code = (disc or {}).get("league") or club_league
    team_id = (disc or {}).get("team_id") or (disc or {}).get("id")

    explicit_league = False
    if not sport_path or not league_code:
        for reg in UniversalEspnAggregator.SPORT_LEAGUE_REGISTRY.values():
            if any(alias in target.lower() or alias in sport_clean for alias in reg["aliases"]):
                sport_path = reg["sport"]
                league_code = reg["league"]
                explicit_league = True
                break

    sport_path = sport_path or "soccer"
    league_code = league_code or club_league or ("nhl" if sport_path == "hockey" else ("nba" if sport_path == "basketball" else "eng.1"))

    if not team_id:
        for cand_t in candidate_targets:
            resolved = resolve_espn_team(sport_path, league_code, cand_t)
            if resolved and resolved.get("id"):
                team_id = resolved.get("id")
                break

    # Do NOT fall back to an arbitrary league scoreboard if we couldn't identify a team or explicit league
    if not team_id and not explicit_league and not club_league:
        return None

    events: List[Dict[str, Any]] = []
    seen_ev_ids: Set[str] = set()
    if team_id:
        if sport_path == "soccer":
            cur_year = datetime.now(timezone.utc).year
            for u in (
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{team_id}/schedule?fixture=true",
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{team_id}/schedule?season={cur_year}",
                f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/teams/{team_id}/schedule",
            ):
                sched_data = fetch_json(u, timeout=8)
                for ev in (sched_data or {}).get("events", []):
                    eid = str(ev.get("id", ""))
                    if eid and eid not in seen_ev_ids:
                        seen_ev_ids.add(eid)
                        events.append(ev)
                if any((e.get("competitions") or [{}])[0].get("status", {}).get("type", {}).get("state") == "in" for e in events):
                    break
        else:
            sched_data = fetch_json(
                f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/teams/{team_id}/schedule",
                timeout=8,
            )
            events = (sched_data or {}).get("events", [])

    if not events and explicit_league:
        sb_data = fetch_json(
            f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/scoreboard",
            timeout=8,
        )
        events = (sb_data or {}).get("events", [])

    if not events:
        return None

    # Prefer in-progress ('in') event first, then earliest upcoming ('pre') event
    chosen_ev = None
    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        st = comp.get("status", {}).get("type", {}).get("state", "")
        if st == "in":
            chosen_ev = ev
            break
    if not chosen_ev:
        pre_events = [
            ev for ev in events
            if (ev.get("competitions") or [{}])[0].get("status", {}).get("type", {}).get("state", "") == "pre"
        ]
        if pre_events:
            pre_events.sort(key=lambda e: e.get("date", "9999"))
            chosen_ev = pre_events[0]
    if not chosen_ev:
        events.sort(key=lambda e: e.get("date", ""))
        chosen_ev = events[-1]

    ev_id = str(chosen_ev.get("id", ""))
    if not ev_id:
        return None

    ev_league_obj = chosen_ev.get("league", {})
    ev_league_slug = ev_league_obj.get("slug") or league_code
    ev_league_name = ev_league_obj.get("name") or ev_league_slug.upper()

    summary_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{ev_league_slug}/summary?event={ev_id}"
    try:
        summary_data = fetch_json(summary_url, timeout=8) or {}
    except Exception:
        summary_data = {}

    snapshot = extract_snapshot_from_summary(summary_data, fallback_event=chosen_ev)

    return {
        "sport_path": sport_path,
        "league_code": ev_league_slug,
        "league_name": ev_league_name,
        "event_id": ev_id,
        "home_team": snapshot.home_team,
        "away_team": snapshot.away_team,
        "snapshot": snapshot,
    }


NotifyCallback = Callable[[TrackedMatch, MatchSnapshot, List[str]], Awaitable[None]]


class LiveSportsTracker:
    """
    Manages active live match subscriptions across Discord channels and runs an
    on-demand background polling task that automatically stops itself when all
    tracked matches finish.
    """

    def __init__(self, poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        self.poll_interval = poll_interval
        # Keyed by (channel_id, event_id)
        self.tracked_matches: Dict[Tuple[int, str], TrackedMatch] = {}
        self._tracker_task: Optional[asyncio.Task] = None
        self._notify_callback: Optional[NotifyCallback] = None

    def set_notify_callback(self, callback: NotifyCallback) -> None:
        """Register the async callback in main.py that triggers the LLM and posts to Discord."""
        self._notify_callback = callback

    def ensure_loop_running(self) -> None:
        """Spawn the background polling task on-demand if matches are tracked and loop is not running."""
        if not self.tracked_matches:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._tracker_task is None or self._tracker_task.done():
            self._tracker_task = loop.create_task(self._run_loop())
            print(f"📡 [Live Tracker]: Started background polling loop (Interval: {self.poll_interval}s, Active matches: {len(self.tracked_matches)}).")

    async def _run_loop(self) -> None:
        """Self-stopping polling loop: runs while tracked_matches is non-empty, then exits."""
        try:
            while self.tracked_matches:
                await asyncio.sleep(self.poll_interval)
                if not self.tracked_matches:
                    break
                await self.poll_once()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"⚠️ [Live Tracker Loop Error]: {e}")
        finally:
            self._tracker_task = None
            print("🛑 [Live Tracker]: No active matches remaining. Background polling task self-stopped.")

    async def poll_once(self) -> None:
        """Poll all currently tracked matches once, fire notifications on changes, and prune finished games."""
        now = time.time()
        items = list(self.tracked_matches.items())

        for key, match in items:
            # 1. Check 4-hour safety timeout
            if now - match.created_at > SAFETY_TIMEOUT_SECONDS:
                print(f"⏱️ [Live Tracker]: Match {match.home_team} vs {match.away_team} exceeded 4-hour safety limit; auto-stopping.")
                self.tracked_matches.pop(key, None)
                continue

            # 2. Fetch fresh ESPN summary
            summary_url = (
                f"https://site.api.espn.com/apis/site/v2/sports/{match.sport_path}/"
                f"{match.league_code}/summary?event={match.event_id}"
            )
            try:
                summary_data = await asyncio.to_thread(fetch_json, summary_url, timeout=8)
                if not summary_data:
                    continue
            except Exception as e:
                print(f"  └─ ⚠️ [Live Tracker Poll Error ({match.home_team} vs {match.away_team})]: {e}")
                continue

            curr_snapshot = extract_snapshot_from_summary(summary_data)
            print(
                f"🔄 [Live Tracker Poll]: {match.home_team} {curr_snapshot.home_score} - "
                f"{curr_snapshot.away_score} {match.away_team} ({curr_snapshot.status_detail}) [{match.league_name}]"
            )
            # Carry forward all previously seen event IDs so we never re-alert old goals/cards
            merged_seen = set(match.last_snapshot.seen_event_ids) | set(curr_snapshot.seen_event_ids)

            triggers = diff_snapshots(match, curr_snapshot)
            curr_snapshot.seen_event_ids = merged_seen
            match.last_snapshot = curr_snapshot

            if triggers:
                print(f"🔔 [Live Tracker Alert ({match.home_team} vs {match.away_team})]: {len(triggers)} event(s) -> {triggers}")
                if self._notify_callback is not None:
                    try:
                        await self._notify_callback(match, curr_snapshot, triggers)
                    except Exception as e:
                        print(f"  └─ ⚠️ [Live Tracker Notification Error]: {e}")

            # 3. Self-stop tracking this match once it reaches Full-Time / Final or is postponed/aborted
            if curr_snapshot.is_finished or curr_snapshot.is_aborted:
                print(
                    f"🏁 [Live Tracker]: Match finished ({match.home_team} {curr_snapshot.home_score} - "
                    f"{curr_snapshot.away_score} {match.away_team}). Auto-removed from tracker."
                )
                self.tracked_matches.pop(key, None)

    def start_tracking(
        self,
        channel_id: int,
        query: str,
        sport: Optional[str] = None,
    ) -> str:
        """Resolve and register a match for live tracking in channel_id."""
        effective_query = query
        effective_sport = sport
        if not _clean_tracking_query(effective_query):
            last_q, last_s = get_channel_last_match_query(channel_id)
            if last_q:
                effective_query = last_q
                effective_sport = effective_sport or last_s

        info = resolve_live_or_next_event(effective_query, sport=effective_sport)
        if not info and effective_query != query:
            info = resolve_live_or_next_event(query, sport=sport)
        if not info:
            return (
                f"⚠️ Could not find an active or upcoming match for '{query}'. "
                "Please specify the team or matchup name (e.g. 'Real Madrid', 'Arsenal', 'Lakers')."
            )

        snap: MatchSnapshot = info["snapshot"]
        record_channel_last_match_query(f"{snap.home_team} vs {snap.away_team}", sport=info["sport_path"], channel_id=channel_id)

        if snap.is_finished:
            return (
                f"🏁 The match **{snap.home_team} {snap.home_score} - {snap.away_score} {snap.away_team}** "
                f"({info['league_name']}) has already finished ({snap.status_detail}), so live tracking is not needed."
            )

        key = (channel_id, info["event_id"])
        tracked = TrackedMatch(
            channel_id=channel_id,
            sport_path=info["sport_path"],
            league_code=info["league_code"],
            league_name=info["league_name"],
            event_id=info["event_id"],
            home_team=snap.home_team,
            away_team=snap.away_team,
            last_snapshot=snap,
        )
        self.tracked_matches[key] = tracked
        self.ensure_loop_running()

        extra_details = ""
        if snap.state == "in":
            status_badge = (
                f"🔴 LIVE IN PROGRESS RIGHT NOW (Current Match Clock: {snap.status_detail} — ONGOING, NOT FINISHED) — "
                f"Live Score: **{snap.home_team} {snap.home_score} - {snap.away_score} {snap.away_team}**"
            )
            if snap.notable_events:
                ev_lines = "\n".join(f"  - {desc}" for _, desc in snap.notable_events)
                extra_details += f"\n• **Goals / Scoring Plays So Far:**\n{ev_lines}"
            if snap.stats_summary:
                extra_details += f"\n• **Live Match Stats:** {snap.stats_summary}"
            llm_instruction = (
                f"\n\n=== CRITICAL INSTRUCTION FOR YOU (THE LLM) ===\n"
                f"1. Live tracking is NOW ACTIVE and RUNNING in the background for {snap.home_team} vs {snap.away_team}.\n"
                f"2. The match is CURRENTLY LIVE IN PROGRESS at {snap.status_detail} "
                f"(Current Score: {snap.home_team} {snap.home_score} - {snap.away_score} {snap.away_team}). "
                f"It is NOT over! Do NOT say the game ended at {snap.status_detail}, do NOT call {snap.home_score}-{snap.away_score} the 'final result', "
                f"and do NOT say tracking stopped or ended immediately!\n"
                f"3. Ignore any older scores or minutes from earlier messages in the conversation."
            )
        else:
            status_badge = f"🕒 Scheduled ({snap.status_detail}) — **{snap.away_team} @ {snap.home_team}**"
            llm_instruction = (
                f"\n\n=== CRITICAL INSTRUCTION FOR YOU (THE LLM) ===\n"
                f"1. Live tracking is NOW ACTIVE and RUNNING in the background for {snap.home_team} vs {snap.away_team}."
            )
        return (
            f"📡 **LIVE MATCH TRACKER ACTIVATED**\n"
            f"• **Match:** {snap.home_team} vs {snap.away_team} ({info['league_name']})\n"
            f"• **Current Status:** {status_badge}"
            f"{extra_details}\n"
            f"• **Tracking Rules:** Polling every {self.poll_interval}s for kickoff, goals/score changes, red cards, halftime/period breaks, and full-time. "
            f"The tracker will remain active while the game is in play and will automatically stop once Full-Time (FT) is reached."
            f"{llm_instruction}"
        )

    def stop_tracking(self, channel_id: int, query: str = "") -> str:
        """Stop tracking a specific match or all matches in channel_id."""
        q_clean = _clean_tracking_query(query).lower()
        channel_keys = [k for k in self.tracked_matches if k[0] == channel_id]
        if not channel_keys:
            return "ℹ️ No matches are currently being tracked in this channel."

        removed: List[str] = []
        for key in channel_keys:
            m = self.tracked_matches[key]
            if not q_clean or q_clean in ("all", "everything", "stop") or (
                q_clean in m.home_team.lower() or q_clean in m.away_team.lower()
                or any(t in m.home_team.lower() or t in m.away_team.lower() for t in q_clean.split())
            ):
                removed.append(f"**{m.home_team} vs {m.away_team}** ({m.league_name})")
                self.tracked_matches.pop(key, None)

        if not removed:
            return f"⚠️ Could not find an actively tracked match matching '{query}' in this channel."

        return f"🛑 Stopped live tracking for: {', '.join(removed)}."

    def list_tracked(self, channel_id: int) -> str:
        """List all matches currently tracked in channel_id (refreshing live display snapshots first)."""
        matches = [m for k, m in self.tracked_matches.items() if k[0] == channel_id]
        if not matches:
            return "ℹ️ No live matches are currently being tracked in this channel."

        from src.skills.sports.aggregators.espn_utils import cached_espn_get

        lines = ["📡 **ACTIVELY TRACKED MATCHES IN THIS CHANNEL:**"]
        for m in matches:
            s = m.last_snapshot
            summary_url = (
                f"https://site.api.espn.com/apis/site/v2/sports/{m.sport_path}/"
                f"{m.league_code}/summary?event={m.event_id}"
            )
            try:
                summary_data = cached_espn_get(summary_url, ttl=15)
                if summary_data and summary_data.get("header"):
                    s = extract_snapshot_from_summary(summary_data)
            except Exception:
                pass
            lines.append(
                f"• **{m.home_team} {s.home_score} - {s.away_score} {m.away_team}** "
                f"({m.league_name}) — Status: `{s.status_detail}`"
            )
        return "\n".join(lines)


LIVE_TRACKER = LiveSportsTracker()


def skill_track_live_game(
    query: str = "",
    action: Optional[str] = "start",
    sport: Optional[str] = None,
) -> str:
    """
    Start, stop, or list live background tracking for any sports match (Soccer, NBA, NHL, NFL, MLB, etc.).
    When started, the bot automatically monitors the live game in the background and posts updates
    in the Discord channel whenever the score changes, a red card occurs, halftime is reached,
    or the match finishes (and automatically stops tracking once the match is over).

    Args:
        query: The team or matchup to track (e.g. 'Real Madrid', 'AS Roma vs Real Madrid', 'Lakers', 'Arsenal').
        action: 'start' to begin tracking a match, 'stop' to stop tracking, or 'list' to view tracked games.
        sport: Optional sport or league hint (e.g. 'soccer', 'ucl', 'nba', 'nhl', 'nfl').
    """
    channel_id = get_active_channel_id()
    act = (action or "start").strip().lower()

    if act in ("stop", "untrack", "cancel", "remove", "end"):
        return LIVE_TRACKER.stop_tracking(channel_id, query=query)
    if act in ("list", "status", "active", "show"):
        return LIVE_TRACKER.list_tracked(channel_id)

    res = LIVE_TRACKER.start_tracking(channel_id, query=query, sport=sport)
    # Schedule the polling loop on the main Discord event loop if called from a worker thread
    if _MAIN_EVENT_LOOP is not None and LIVE_TRACKER.tracked_matches:
        try:
            _MAIN_EVENT_LOOP.call_soon_threadsafe(LIVE_TRACKER.ensure_loop_running)
        except Exception:
            pass
    return res


_MAIN_EVENT_LOOP: Optional[asyncio.AbstractEventLoop] = None


def bind_tracker_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Bind the main Discord asyncio event loop so worker threads can wake up the polling task."""
    global _MAIN_EVENT_LOOP
    _MAIN_EVENT_LOOP = loop
