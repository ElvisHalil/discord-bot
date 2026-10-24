"""
Soccer (Association Football) Sports Aggregator.
Fetches real-time match scores, goal scorers, yellow/red cards, possession & match stats,
starting lineups, substitutions, team schedules, player season stats, league leaders,
injuries, and league standings across major soccer competitions via ESPN's public v2 API.
"""

from datetime import datetime, timedelta, timezone
import re
from typing import Optional, Dict, Any, List, Tuple
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.aggregators.espn_utils import (
    cached_espn_get,
    cached_espn_get_many,
    parallel_fetch_json,
    resolve_espn_team,
    match_team_in_query,
    parse_team_schedule,
    parse_team_roster_and_stats,
    parse_league_injuries,
    parse_match_summary,
    espn_search_auto_discover,
    parse_athlete_overview,
)
from src.skills.sports.catalogs import (
    CLUB_LEAGUE_HINTS as _CLUB_LEAGUE_HINTS,
    SOCCER_LEAGUES as _SOCCER_LEAGUES,
    SOCCER_MULTI_LEAGUE_KEYS as _SOCCER_MULTI_LEAGUE_KEYS,
)
from src.utils.http import fetch_json
from src.utils.timezones import get_local_now


class SoccerAggregator(BaseSportsAggregator):
    """Aggregator for Soccer matches, live scoreboards, cards, stats, lineups, rosters, and standings."""

    LEAGUES = _SOCCER_LEAGUES
    CLUB_LEAGUE_HINTS = _CLUB_LEAGUE_HINTS
    SCOREBOARD_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
    STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports/soccer"
    MULTI_LEAGUE_KEYS = _SOCCER_MULTI_LEAGUE_KEYS

    def get_info(self, query: str = "") -> str:
        """Smart entry point for soccer queries: standings, lineups/summaries, injuries, player stats, rosters, and schedules."""
        q = (query or "").lower().strip()
        explicit_league_or_club = self._has_explicit_league_or_club(q)
        league_key = self._detect_league(q)
        league_info = self.LEAGUES[league_key]
        league_code = league_info["code"]
        league_name = league_info["name"]

        matched_team = resolve_espn_team("soccer", league_code, q)
        discovered_player = None

        # Auto-discover any worldwide soccer club or player via ESPN Search v2 if not found in default league
        if not matched_team:
            disc = espn_search_auto_discover(q, sport_hint="soccer")
            if disc and disc.get("sport") == "soccer":
                disc_league = disc.get("league") or league_code
                league_code = disc_league
                for l_info in self.LEAGUES.values():
                    if l_info["code"] == disc_league:
                        league_name = l_info["name"]
                        break
                else:
                    league_name = f"Soccer ({disc_league.upper()})"

                if disc.get("entity_type") == "team" and disc.get("team_id"):
                    matched_team = {"id": disc["team_id"], "displayName": disc["display_name"]}
                elif disc.get("entity_type") == "player" and disc.get("athlete_id"):
                    discovered_player = disc
                    if disc.get("subtitle"):
                        matched_team = resolve_espn_team("soccer", league_code, disc["subtitle"])

        # 1. Deep Match Summary / Starting Lineups / Substitutions / Boxscore
        if re.search(r'\b(?:lineups?|line-ups?|starting\s*xi|starting\s*11|formations?|subs?|substitutions?|boxscores?|match\s*stats?|match\s*summary|possession|corners|fouls)\b', q):
            summary_out = self.get_match_summary(league_code, league_name, q, matched_team=matched_team)
            if summary_out:
                return summary_out

        # 2. Injuries / Suspensions / Availability
        if re.search(r'\b(?:injur(?:y|ies|ed)|unavailable|availability|fitness|suspended|suspensions?)\b', q):
            return parse_league_injuries("soccer", league_code, league_name, q)

        # 3. Squad Roster, Player Season Stats, Top Scorers, Assists, or Cards
        # Note: Do NOT match bare substring 'stat' (which false-positives on 'status' / 'state'!).
        is_live_or_schedule_query = bool(
            re.search(
                r'\b(?:status|current|currently|live|playing|right\s*now|score|scores|scored|goal|goals|going|minute|half|halftime|winning|losing|next|upcoming|when|schedule|fixtures?|vs\.?)\b',
                q,
            )
        )
        is_explicit_roster = bool(re.search(r'\b(?:rosters?|squads?)\b', q))

        if discovered_player and not is_explicit_roster and not is_live_or_schedule_query and not any(w in q for w in ("standing", "table")):
            ath_out = parse_athlete_overview(
                "soccer",
                league_code,
                discovered_player["athlete_id"],
                discovered_player["display_name"],
                discovered_player.get("subtitle", ""),
            )
            if ath_out:
                return ath_out

        if is_explicit_roster or (
            not is_live_or_schedule_query
            and re.search(r'\b(?:players?|stats|statistics|scorers?|golden\s*boot|assists?|yellow\s*cards?|red\s*cards?|cards|appearances)\b', q)
        ):
            if matched_team:
                roster_out = parse_team_roster_and_stats(
                    "soccer", league_code, matched_team["id"], matched_team["displayName"], player_filter=q
                )
                if any(w in q for w in ("card", "yellow", "red", "last match", "last game", "recent")):
                    match_sum = self.get_match_summary(league_code, league_name, q, matched_team=matched_team)
                    if match_sum:
                        return f"{roster_out}\n\n{match_sum}"
                return roster_out
            return self.get_league_leaders(league_code, league_name, filter_query=q)

        # 4. League Standings / Table
        if any(w in q for w in ("standing", "table", "rank", "points")):
            return self.get_standings(league_code, league_name)

        # 5. Temporal Scope & History detection
        now = get_local_now().replace(tzinfo=None)
        is_past = any(w in q for w in (
            "past", "recent", "played already", "already played", "have been played", "already",
            "last week", "previous week", "go back", "prior", "completed", "results",
            "last game", "last match", "previous match", "earlier", "history"
        )) or (any(w in q for w in ("played", "result", "results", "back")) and not any(w in q for w in ("upcoming", "next", "future", "schedule", "tomorrow")))

        has_explicit_day = any(w in q for w in ("today", "tomorrow", "yesterday", "tonight"))
        is_both_today_tomorrow = ("today" in q or "tonight" in q) and "tomorrow" in q
        is_month = "month" in q and not is_past and not has_explicit_day
        is_week = (
            any(w in q for w in ("week", "upcoming", "next", "fixtures", "schedule", "soon", "weekend", "saturday", "sunday", "few days", "coming up"))
            and not is_past
            and (not has_explicit_day or "week" in q or "weekend" in q)
        )

        if matched_team:
            team_sched_block = parse_team_schedule("soccer", league_code, matched_team["id"], matched_team["displayName"])
            if team_sched_block:
                is_live_now = "🔴 **LIVE" in team_sched_block
                if is_live_now or "🔥 **TODAY" in team_sched_block or is_past or is_live_or_schedule_query:
                    match_sum = self.get_match_summary(league_code, league_name, q, matched_team=matched_team)
                    if match_sum:
                        # Put live match summary FIRST when a game is currently live or user asks for current status
                        if is_live_now or any(w in q for w in ("status", "current", "currently", "live", "playing", "score", "scored", "goal", "goals", "going", "minute", "winning", "losing", "half")):
                            return f"{match_sum}\n\n{team_sched_block}"
                        return f"{team_sched_block}\n\n{match_sum}"
                return team_sched_block

        # When no specific league or club was mentioned (e.g., "soccer matches this week"),
        # fetch across all major competitions in parallel!
        if not explicit_league_or_club and not discovered_player:
            if is_past and "yesterday" not in q:
                return self.get_multi_league_past_schedule_range(days_back=7, scope_label="RECENT MATCH RESULTS")
            elif is_month:
                return self.get_multi_league_schedule_range(days_ahead=30, scope_label="MONTHLY FIXTURES")
            elif is_week:
                return self.get_multi_league_schedule_range(days_ahead=8, scope_label="WEEKLY FIXTURES")
            elif is_both_today_tomorrow:
                tom_param = (now + timedelta(days=1)).strftime("%Y%m%d")
                today_out = self.get_multi_league_matches(date_param=None)
                tom_out = self.get_multi_league_matches(date_param=tom_param)
                return f"{today_out}\n\n{tom_out}"
            elif "yesterday" in q:
                date_param = (now - timedelta(days=1)).strftime("%Y%m%d")
                return self.get_multi_league_matches(date_param=date_param)
            elif "tomorrow" in q:
                date_param = (now + timedelta(days=1)).strftime("%Y%m%d")
                return self.get_multi_league_matches(date_param=date_param)
            else:
                return self.get_multi_league_matches(date_param=None)

        effective_filter = matched_team["displayName"] if matched_team else q
        if is_past and "yesterday" not in q:
            res = self.get_past_schedule_range(league_code, league_name, days_back=7, scope_label="RECENT MATCH RESULTS", filter_query=effective_filter)
        elif is_month:
            res = self.get_schedule_range(league_code, league_name, days_ahead=30, scope_label="MONTHLY FIXTURES", filter_query=effective_filter)
        elif is_week:
            res = self.get_schedule_range(league_code, league_name, days_ahead=8, scope_label="WEEKLY FIXTURES", filter_query=effective_filter)
        elif is_both_today_tomorrow:
            tom_param = (now + timedelta(days=1)).strftime("%Y%m%d")
            today_out = self.get_matches(league_code, league_name, date_param=None, filter_query=effective_filter)
            tom_out = self.get_matches(league_code, league_name, date_param=tom_param, filter_query=effective_filter)
            res = f"{today_out}\n\n{tom_out}"
        elif "yesterday" in q:
            date_param = (now - timedelta(days=1)).strftime("%Y%m%d")
            res = self.get_matches(league_code, league_name, date_param=date_param, filter_query=effective_filter)
        elif "tomorrow" in q:
            date_param = (now + timedelta(days=1)).strftime("%Y%m%d")
            res = self.get_matches(league_code, league_name, date_param=date_param, filter_query=effective_filter)
        else:
            res = self.get_matches(league_code, league_name, date_param=None, filter_query=effective_filter)

        return res

    def _has_explicit_league_or_club(self, query: str) -> bool:
        """Return True if the query explicitly mentions a league alias or a known club."""
        q = (query or "").lower()
        for info in self.LEAGUES.values():
            for alias in info["aliases"]:
                if re.search(rf'\b{re.escape(alias)}\b', q):
                    return True
        for club in self.CLUB_LEAGUE_HINTS:
            if re.search(rf'\b{re.escape(club)}\b', q):
                return True
        return False

    def _detect_league(self, query: str) -> str:
        """Maps query keywords or team names to the most relevant soccer league key."""
        q = query.lower()
        for key, info in self.LEAGUES.items():
            for alias in info["aliases"]:
                if alias in q:
                    return key
        for club, league_key in self.CLUB_LEAGUE_HINTS.items():
            if club in q:
                return league_key
        return "epl"

    @staticmethod
    def _extract_match_details(
        comp: Dict[str, Any],
        home: Dict[str, Any],
        away: Dict[str, Any],
        home_name: str,
        away_name: str,
    ) -> Tuple[List[str], List[str], List[str], str]:
        """Extract Goals, Yellow Cards, Red Cards, and Team Match Stats from an ESPN competition object."""
        details = comp.get("details", [])
        goals: List[str] = []
        yellow_cards: List[str] = []
        red_cards: List[str] = []

        home_id = home.get("team", {}).get("id")
        for det in details:
            item_type = det.get("type", {}).get("text", "")
            clock_str = det.get("clock", {}).get("displayValue", "")
            athletes = det.get("athletesInvolved", [])
            player_name = athletes[0].get("displayName") if athletes else "Player"
            team_id = det.get("team", {}).get("id")
            ev_team = home_name if team_id == home_id else away_name

            if det.get("scoringPlay") or "Goal" in item_type or "Penalty - Scored" in item_type:
                pen = " (PEN)" if det.get("penaltyKick") else ""
                og = " (OG)" if det.get("ownGoal") else ""
                assist_str = ""
                if len(athletes) > 1 and athletes[1].get("displayName"):
                    assist_str = f" [ast: {athletes[1].get('displayName')}]"
                goals.append(f"{clock_str} {player_name} ({ev_team}){pen}{og}{assist_str}".strip())
            elif det.get("redCard") or "Red" in item_type or "Second Yellow" in item_type:
                red_cards.append(f"{clock_str} {player_name} ({ev_team})".strip())
            elif det.get("yellowCard") or "Yellow" in item_type:
                yellow_cards.append(f"{clock_str} {player_name} ({ev_team})".strip())

        # Extract inline team match stats from competitors[i]["statistics"]
        h_stats = {s.get("name"): s.get("displayValue") for s in home.get("statistics", []) if s.get("name")}
        a_stats = {s.get("name"): s.get("displayValue") for s in away.get("statistics", []) if s.get("name")}

        stat_parts: List[str] = []
        if "possessionPct" in h_stats or "possessionPct" in a_stats:
            hp = h_stats.get("possessionPct", "0")
            ap = a_stats.get("possessionPct", "0")
            stat_parts.append(f"Possession: {hp}% - {ap}%")
        if "totalShots" in h_stats or "totalShots" in a_stats:
            hs = h_stats.get("totalShots", "0")
            as_ = a_stats.get("totalShots", "0")
            hsot = h_stats.get("shotsOnTarget", "0")
            asot = a_stats.get("shotsOnTarget", "0")
            stat_parts.append(f"Shots (On Target): {hs} ({hsot}) - {as_} ({asot})")
        if "wonCorners" in h_stats or "wonCorners" in a_stats:
            stat_parts.append(f"Corners: {h_stats.get('wonCorners', '0')} - {a_stats.get('wonCorners', '0')}")
        if "foulsCommitted" in h_stats or "foulsCommitted" in a_stats:
            stat_parts.append(f"Fouls: {h_stats.get('foulsCommitted', '0')} - {a_stats.get('foulsCommitted', '0')}")

        stats_line = " | ".join(stat_parts)
        return goals, yellow_cards, red_cards, stats_line

    @classmethod
    def _event_matches_team_filter(cls, home: Dict[str, Any], away: Dict[str, Any], filter_query: str) -> bool:
        """Check if a match involves a team specifically queried in filter_query."""
        if not filter_query:
            return True
        # Only filter if filter_query actually mentions a known club or specific team token
        q_low = filter_query.lower()
        has_club_hint = any(club in q_low for club in cls.CLUB_LEAGUE_HINTS)
        if not has_club_hint:
            return True

        for side in (home, away):
            t = side.get("team", {})
            if match_team_in_query(
                t.get("displayName", ""),
                t.get("shortDisplayName", ""),
                t.get("abbreviation", ""),
                q_low,
            ):
                return True
        return False

    def get_league_leaders(self, league_code: str, league_name: str, filter_query: str = "") -> str:
        """Fetch top goalscorers and assist leaders for a soccer league from ESPN /statistics."""
        url = f"{self.SCOREBOARD_BASE}/{league_code}/statistics"
        data = cached_espn_get(url, ttl=300)
        if not data or not data.get("stats"):
            return f"No player statistical leaders currently available for {league_name}."

        out = [f"=== 🏆 {league_name.upper()} PLAYER STATISTICAL LEADERS ==="]
        q_low = (filter_query or "").lower()

        for cat in data.get("stats", []):
            cat_title = cat.get("displayName") or cat.get("name", "Leaders")
            leaders = cat.get("leaders", [])
            if not leaders:
                continue

            out.append(f"\n⚽ **Top {cat_title}:**")
            shown = 0
            for idx, entry in enumerate(leaders, 1):
                ath = entry.get("athlete", {})
                p_name = ath.get("displayName", "Player")
                t_name = ath.get("team", {}).get("displayName", "Club")
                stats_map = {s.get("name"): s.get("displayValue") for s in ath.get("statistics", []) if s.get("name")}
                apps = stats_map.get("appearances", "?")
                goals = stats_map.get("totalGoals", str(int(entry.get("value", 0))))
                assists = stats_map.get("goalAssists", "0")

                if shown < 12 or (q_low and p_name.lower() in q_low):
                    out.append(
                        f"  {idx}. **{p_name}** ({t_name}) — **{goals} Goals, {assists} Assists** ({apps} matches)"
                    )
                    shown += 1

        return "\n".join(out)

    def get_match_summary(
        self,
        league_code: str,
        league_name: str,
        query: str = "",
        matched_team: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Locate the most relevant match event_id and return its full ESPN /summary (lineups, subs, cards, stats)."""
        event_id = None
        summary_league = league_code

        # 1. If a specific team was matched, check its schedule across all competitions for the latest completed, live, or today's match
        if matched_team:
            sched_url = f"{self.SCOREBOARD_BASE}/{league_code}/teams/{matched_team['id']}/schedule"
            all_url = f"{self.SCOREBOARD_BASE}/all/teams/{matched_team['id']}/schedule"
            cur_year = datetime.now(timezone.utc).year
            events: List[Dict[str, Any]] = []
            seen_ids = set()
            payloads = cached_espn_get_many(
                [
                    (sched_url, None),
                    (all_url, {"season": str(cur_year)}),
                    (all_url, {"fixture": "true"}),
                ],
                ttl=20,
            )
            for s_data in payloads:
                if s_data and s_data.get("events"):
                    for ev in s_data["events"]:
                        eid = str(ev.get("id", ""))
                        if eid and eid not in seen_ids:
                            seen_ids.add(eid)
                            events.append(ev)

            if events:
                events.sort(key=lambda e: e.get("date", ""))
                today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                live_or_today = [
                    ev for ev in events
                    if ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("state") == "in"
                    or ev.get("date", "").startswith(today_utc)
                ]
                live_or_done = [
                    ev for ev in events
                    if ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("state") in ("in", "post")
                    or ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed")
                ]
                chosen = live_or_today[-1] if live_or_today else (live_or_done[-1] if live_or_done else events[-1])
                event_id = str(chosen.get("id", ""))
                ev_slug = chosen.get("league", {}).get("slug")
                if ev_slug:
                    summary_league = ev_slug

        # 2. Otherwise inspect current/recent league scoreboard
        if not event_id:
            sb_url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard"
            sb_data = cached_espn_get(sb_url, ttl=120) or fetch_json(sb_url, timeout=8)
            events = sb_data.get("events", []) if sb_data else []
            if events:
                event_id = str(events[0].get("id", ""))

        if not event_id:
            return ""
        return parse_match_summary("soccer", summary_league, event_id)

    def get_past_schedule_range(self, league_code: str, league_name: str, days_back: int = 7, scope_label: str = "RECENT MATCH RESULTS", filter_query: str = "") -> str:
        """Fetches completed matches and results from recent matchdays with goals, yellow/red cards, and team stats."""
        print(f"  └─ ⚽ [Soccer]: Fetching {scope_label} for {league_name} (Code: {league_code}, Past {days_back} days)...")
        now = get_local_now().replace(tzinfo=None)
        start_date = now - timedelta(days=days_back)

        try:
            base_url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard"
            base_data = fetch_json(base_url, timeout=8)
            leagues = base_data.get("leagues", [])
            calendar = leagues[0].get("calendar", []) if leagues else []

            past_dates = []
            for item in calendar:
                if isinstance(item, str):
                    try:
                        clean_dt = datetime.fromisoformat(item.replace("Z", "+00:00")).replace(tzinfo=None)
                        if clean_dt.date() < now.date():
                            d_str = clean_dt.strftime("%Y%m%d")
                            if d_str not in past_dates:
                                past_dates.append(d_str)
                    except Exception:
                        pass

            if not past_dates:
                # For UEFA competitions where calendar contains stage dicts, check base events and recent Tue/Wed/Thu
                for ev in (base_data or {}).get("events", []):
                    try:
                        ev_dt = datetime.fromisoformat(ev.get("date", "").replace("Z", "+00:00")).replace(tzinfo=None)
                        if ev_dt.date() <= now.date():
                            d_str = ev_dt.strftime("%Y%m%d")
                            if d_str not in past_dates:
                                past_dates.append(d_str)
                    except Exception:
                        pass
                if not past_dates and "uefa" in league_code:
                    for d in range(days_back, 0, -1):
                        cand = now - timedelta(days=d)
                        if cand.weekday() in (1, 2, 3):
                            past_dates.append(cand.strftime("%Y%m%d"))

            if not past_dates:
                return f"=== {league_name.upper()} {scope_label} ===\nNo recent completed matches found in the current season calendar."

            recent_dates = [d for d in past_dates if datetime.strptime(d, "%Y%m%d").date() >= start_date.date()]
            recent_dates = recent_dates[-3:] if recent_dates else past_dates[-3:]

            output_lines = [
                f"=== {league_name.upper()} {scope_label} ===\n",
                f"📅 Current Date: {self.format_human_date(now)}\n"
            ]

            day_urls = [f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard?dates={pdate}" for pdate in recent_dates]
            day_payloads = parallel_fetch_json(fetch_json, day_urls, timeout=6, ttl=30)

            total_matches = 0
            for pdate, day_data in zip(recent_dates, day_payloads):
                day_title = self.format_human_date(pdate)
                events = (day_data or {}).get("events", [])
                if not events:
                    continue

                day_match_lines: List[str] = []
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                    if not self._event_matches_team_filter(home, away, filter_query):
                        continue

                    home_name = home.get("team", {}).get("displayName", "Home")
                    away_name = away.get("team", {}).get("displayName", "Away")
                    home_score = home.get("score", "0")
                    away_score = away.get("score", "0")
                    status_desc = comp.get("status", {}).get("type", {}).get("detail") or "FT"

                    goals, yellow_cards, red_cards, stats_line = self._extract_match_details(
                        comp, home, away, home_name, away_name
                    )

                    venue = comp.get("venue", {}).get("fullName")
                    venue_str = f" | Venue: {venue}" if venue else ""

                    day_match_lines.append(f"  • {home_name} {home_score} - {away_score} {away_name} [{status_desc}{venue_str}]")
                    if goals:
                        day_match_lines.append(f"    🎯 Goals: {', '.join(goals)}")
                    if yellow_cards:
                        day_match_lines.append(f"    🟨 Yellow Cards: {', '.join(yellow_cards)}")
                    if red_cards:
                        day_match_lines.append(f"    🟥 Red Cards: {', '.join(red_cards)}")
                    if stats_line:
                        day_match_lines.append(f"    📊 Stats: {stats_line}")
                    total_matches += 1

                if day_match_lines:
                    output_lines.append(f"📅 --- {day_title.upper()} ---")
                    output_lines.extend(day_match_lines)
                    output_lines.append("")

            if total_matches == 0:
                output_lines.append(f"No completed match records found for recent matchdays in {league_name}.")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching recent results for {league_name}: {e}"

    def get_schedule_range(self, league_code: str, league_name: str, days_ahead: int = 8, scope_label: str = "WEEKLY FIXTURES", filter_query: str = "") -> str:
        """Fetches upcoming fixtures across multiple matchdays within the specified time window (week or month)."""
        print(f"  └─ ⚽ [Soccer]: Fetching {scope_label} for {league_name} (Code: {league_code}, Next {days_ahead} days)...")
        now = get_local_now().replace(tzinfo=None)
        end_date = now + timedelta(days=days_ahead)

        try:
            base_url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard"
            base_data = fetch_json(base_url, timeout=8)
            leagues = base_data.get("leagues", [])
            calendar = leagues[0].get("calendar", []) if leagues else []

            upcoming_dates = []
            for item in calendar:
                if isinstance(item, str):
                    try:
                        clean_dt = datetime.fromisoformat(item.replace("Z", "+00:00")).replace(tzinfo=None)
                        if now.date() <= clean_dt.date() <= end_date.date():
                            d_str = clean_dt.strftime("%Y%m%d")
                            if d_str not in upcoming_dates:
                                upcoming_dates.append(d_str)
                    except Exception:
                        pass

            if not upcoming_dates:
                for ev in (base_data or {}).get("events", []):
                    try:
                        ev_dt = datetime.fromisoformat(ev.get("date", "").replace("Z", "+00:00")).replace(tzinfo=None)
                        if now.date() <= ev_dt.date() <= end_date.date():
                            d_str = ev_dt.strftime("%Y%m%d")
                            if d_str not in upcoming_dates:
                                upcoming_dates.append(d_str)
                    except Exception:
                        pass

            if not upcoming_dates:
                target_weekdays = (1, 2, 3) if "uefa" in league_code else (5, 6)
                for d in range(0, min(days_ahead + 1, 8)):
                    cand = now + timedelta(days=d)
                    if cand.weekday() in target_weekdays:
                        upcoming_dates.append(cand.strftime("%Y%m%d"))

            if not upcoming_dates:
                return f"=== {league_name.upper()} {scope_label} ===\nNo upcoming matches scheduled in the next {days_ahead} days."

            output_lines = [f"=== {league_name.upper()} {scope_label} ===\n"]

            target_dates = upcoming_dates[:5]
            day_urls = [f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard?dates={udate}" for udate in target_dates]
            day_payloads = parallel_fetch_json(fetch_json, day_urls, timeout=6, ttl=30)

            total_matches = 0
            for udate, day_data in zip(target_dates, day_payloads):
                day_title = self.format_human_date(udate)
                events = (day_data or {}).get("events", [])
                if not events:
                    continue

                day_match_lines: List[str] = []
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                    if not self._event_matches_team_filter(home, away, filter_query):
                        continue

                    home_name = home.get("team", {}).get("displayName", "Home")
                    away_name = away.get("team", {}).get("displayName", "Away")
                    status_obj = comp.get("status", {})
                    status_desc = status_obj.get("type", {}).get("detail") or "Scheduled"

                    venue = comp.get("venue", {}).get("fullName")
                    m_date = ev.get("date", "")
                    time_str = m_date[:16].replace("T", " ") + " UTC" if len(m_date) >= 16 else ""

                    details_list = []
                    if time_str:
                        details_list.append(f"Kickoff: {time_str}")
                    if venue:
                        details_list.append(f"Venue: {venue}")
                    details_str = f" ({' | '.join(details_list)})" if details_list else ""

                    day_match_lines.append(f"  • {away_name} at {home_name}{details_str} [{status_desc}]")
                    total_matches += 1

                if day_match_lines:
                    output_lines.append(f"📅 --- {day_title.upper()} ({len(day_match_lines)} Matches) ---")
                    output_lines.extend(day_match_lines)
                    output_lines.append("")

            if total_matches == 0:
                return f"=== {league_name.upper()} {scope_label} ===\nNo upcoming matches scheduled in the next {days_ahead} days."

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching {scope_label} for {league_name}: {e}"

    def get_matches(self, league_code: str, league_name: str, date_param: Optional[str] = None, filter_query: str = "") -> str:
        """Fetches live matches, recent results, and upcoming fixtures for a league with goals, cards, and team stats."""
        print(f"  └─ ⚽ [Soccer]: Fetching matches for {league_name} (Code: {league_code}, Date: {date_param or 'Current'})...")
        url = f"{self.SCOREBOARD_BASE}/{league_code}/scoreboard"
        if date_param:
            url += f"?dates={date_param}"

        try:
            data = fetch_json(url, timeout=8)
            events = data.get("events", [])

            now = get_local_now().replace(tzinfo=None)
            all_completed = events and all(ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("completed", False) for ev in events)
            if (not events or all_completed) and not date_param:
                upcoming_preview = self.get_schedule_range(league_code, league_name, days_ahead=7, scope_label="UPCOMING MATCHES THIS WEEK", filter_query=filter_query)
                if "No upcoming matches" not in upcoming_preview and "No fixtures found" not in upcoming_preview:
                    if not events:
                        return upcoming_preview

            if not events:
                day_display = self.format_human_date(date_param) if date_param else self.format_human_date(now)
                upcoming_preview = self.get_schedule_range(league_code, league_name, days_ahead=7, scope_label="NEXT UPCOMING FIXTURES", filter_query=filter_query)
                return (
                    f"=== {league_name.upper()} — NO MATCHES SCHEDULED ===\n"
                    f"📅 Date Checked: {day_display} (No matches scheduled for this date).\n\n"
                    f"ℹ️ There are no {league_name} matches scheduled for {day_display}.\n\n"
                    f"🔜 {upcoming_preview}"
                )

            day_display = self.format_human_date(date_param) if date_param else self.format_human_date(now)
            output_lines = [f"=== {league_name.upper()} MATCHES — {day_display.upper()} ===\n"]

            from src.skills.sports.aggregators.odds import format_inline_scoreboard_odds

            for ev in events:
                comps = ev.get("competitions", [])
                if not comps:
                    continue
                comp = comps[0]

                competitors = comp.get("competitors", [])
                home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                if not self._event_matches_team_filter(home, away, filter_query):
                    continue

                home_name = home.get("team", {}).get("displayName", "Home")
                away_name = away.get("team", {}).get("displayName", "Away")
                home_score = home.get("score", "0")
                away_score = away.get("score", "0")

                status_obj = comp.get("status", {})
                status_desc = status_obj.get("type", {}).get("detail") or status_obj.get("type", {}).get("description") or "Scheduled"
                is_completed = status_obj.get("type", {}).get("completed", False)
                is_live = status_obj.get("type", {}).get("state") == "in"

                live_marker = " 🔴 [LIVE]" if is_live else (" ✅ [FT]" if is_completed else "")
                output_lines.append(f"⚽ {home_name} {home_score} - {away_score} {away_name}{live_marker}")
                output_lines.append(f"   Status: {status_desc}")

                if not is_completed:
                    inline_odds = format_inline_scoreboard_odds(comp, home_name, away_name)
                    if inline_odds:
                        output_lines.append(inline_odds)

                venue_name = comp.get("venue", {}).get("fullName")
                match_date = ev.get("date", "")
                date_display = match_date[:16].replace("T", " ") + " UTC" if match_date else ""
                meta_parts = []
                if date_display and not is_live:
                    meta_parts.append(f"Kickoff: {date_display}")
                if venue_name:
                    meta_parts.append(f"Venue: {venue_name}")
                if meta_parts:
                    output_lines.append(f"   {' | '.join(meta_parts)}")

                goals, yellow_cards, red_cards, stats_line = self._extract_match_details(
                    comp, home, away, home_name, away_name
                )

                if goals:
                    output_lines.append(f"   🎯 Goals: {', '.join(goals)}")
                if yellow_cards:
                    output_lines.append(f"   🟨 Yellow Cards: {', '.join(yellow_cards)}")
                if red_cards:
                    output_lines.append(f"   🟥 Red Cards: {', '.join(red_cards)}")
                if stats_line:
                    output_lines.append(f"   📊 Stats: {stats_line}")

                home_form = home.get("form")
                away_form = away.get("form")
                if home_form and away_form:
                    output_lines.append(f"   Form: {home_name} [{home_form}] vs {away_name} [{away_form}]")

                output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching soccer matches for {league_name}: {e}"

    def get_standings(self, league_code: str, league_name: str) -> str:
        """Fetches the current league table with points, wins, draws, losses, and goal difference."""
        print(f"  └─ ⚽ [Soccer]: Fetching standings for {league_name} (Code: {league_code})...")
        url = f"{self.STANDINGS_BASE}/{league_code}/standings"
        try:
            data = fetch_json(url, timeout=8)
            children = data.get("children", [])
            entries = []
            if children:
                entries = children[0].get("standings", {}).get("entries", [])
            elif "standings" in data:
                entries = data["standings"].get("entries", [])

            if not entries:
                return f"No standings data currently available for {league_name}."

            output_lines = [f"=== {league_name.upper()} STANDINGS ===\n"]
            output_lines.append("Pos | Club                      | MP | W  | D  | L  | GD  | PTS")
            output_lines.append("-" * 62)

            for entry in entries[:15]:
                team_name = entry.get("team", {}).get("displayName", "Team")
                stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                rank = stats.get("rank") or "?"
                mp = stats.get("gamesPlayed", "0")
                w = stats.get("wins", "0")
                d = stats.get("ties", "0")
                l = stats.get("losses", "0")
                gd = stats.get("pointDifferential", "0")
                pts = stats.get("points", "0")

                output_lines.append(f"{rank:>3} | {team_name:<25} | {mp:>2} | {w:>2} | {d:>2} | {l:>2} | {gd:>3} | {pts:>3}")

            output_lines.append("")
            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching standings for {league_name}: {e}"

    def get_multi_league_schedule_range(self, days_ahead: int = 8, scope_label: str = "WEEKLY FIXTURES") -> str:
        """Fetches upcoming fixtures across all major soccer competitions in parallel (2 flat HTTP waves)."""
        print(f"  └─ ⚽ [Soccer]: Fetching {scope_label} across all major competitions (Next {days_ahead} days)...")
        now = get_local_now().replace(tzinfo=None)
        end_date = now + timedelta(days=days_ahead)

        try:
            # Wave 1: Fetch base calendar scoreboards for all major competitions concurrently
            league_items = [(k, self.LEAGUES[k]["code"], self.LEAGUES[k]["name"]) for k in self.MULTI_LEAGUE_KEYS]
            base_urls = [f"{self.SCOREBOARD_BASE}/{code}/scoreboard" for _, code, _ in league_items]
            base_payloads = parallel_fetch_json(fetch_json, base_urls, timeout=6, ttl=60)

            day_requests: List[Tuple[str, str, str, str, str]] = []  # (key, code, name, udate, url)
            for (k, code, name), base_data in zip(league_items, base_payloads):
                leagues = (base_data or {}).get("leagues", [])
                calendar = leagues[0].get("calendar", []) if leagues else []
                upcoming_dates: List[str] = []
                for item in calendar:
                    if isinstance(item, str):
                        try:
                            clean_dt = datetime.fromisoformat(item.replace("Z", "+00:00")).replace(tzinfo=None)
                            if now.date() <= clean_dt.date() <= end_date.date():
                                d_str = clean_dt.strftime("%Y%m%d")
                                if d_str not in upcoming_dates:
                                    upcoming_dates.append(d_str)
                        except Exception:
                            pass

                if not upcoming_dates:
                    for ev in (base_data or {}).get("events", []):
                        try:
                            ev_dt = datetime.fromisoformat(ev.get("date", "").replace("Z", "+00:00")).replace(tzinfo=None)
                            if now.date() <= ev_dt.date() <= end_date.date():
                                d_str = ev_dt.strftime("%Y%m%d")
                                if d_str not in upcoming_dates:
                                    upcoming_dates.append(d_str)
                        except Exception:
                            pass
                    if "uefa" in code:
                        for d in range(0, min(days_ahead + 1, 8)):
                            cand = now + timedelta(days=d)
                            if cand.weekday() in (1, 2, 3):
                                d_str = cand.strftime("%Y%m%d")
                                if d_str not in upcoming_dates:
                                    upcoming_dates.append(d_str)

                for udate in sorted(upcoming_dates)[:4]:
                    day_requests.append((k, code, name, udate, f"{self.SCOREBOARD_BASE}/{code}/scoreboard?dates={udate}"))

            if not day_requests:
                return f"=== SOCCER {scope_label} (ALL MAJOR COMPETITIONS) ===\nNo upcoming matches scheduled in the next {days_ahead} days."

            # Wave 2: Fetch all matchday scoreboards across all leagues concurrently
            day_urls = [req[4] for req in day_requests]
            day_payloads = parallel_fetch_json(fetch_json, day_urls, timeout=6, ttl=30)

            by_league: Dict[str, List[str]] = {k: [] for k, _, _ in league_items}
            total_matches = 0

            for (k, code, name, udate, _), day_data in zip(day_requests, day_payloads):
                events = (day_data or {}).get("events", [])
                if not events:
                    continue
                day_title = self.format_human_date(udate)
                day_match_lines: List[str] = []
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                    home_name = home.get("team", {}).get("displayName", "Home")
                    away_name = away.get("team", {}).get("displayName", "Away")
                    status_obj = comp.get("status", {})
                    state = status_obj.get("type", {}).get("state", "")
                    is_completed = status_obj.get("type", {}).get("completed", False)
                    status_desc = status_obj.get("type", {}).get("detail") or "Scheduled"

                    m_date = ev.get("date", "")
                    time_str = m_date[:16].replace("T", " ") + " UTC" if len(m_date) >= 16 else ""
                    venue = comp.get("venue", {}).get("fullName")

                    if state == "in" or is_completed:
                        h_score = home.get("score", "0")
                        a_score = away.get("score", "0")
                        badge = " 🔴 [LIVE]" if state == "in" else " ✅ [FT]"
                        day_match_lines.append(f"  • {home_name} {h_score} - {a_score} {away_name}{badge} [{status_desc}]")
                    else:
                        details_list = []
                        if time_str:
                            details_list.append(f"Kickoff: {time_str}")
                        if venue:
                            details_list.append(f"Venue: {venue}")
                        details_str = f" ({' | '.join(details_list)})" if details_list else ""
                        day_match_lines.append(f"  • {away_name} at {home_name}{details_str} [{status_desc}]")
                    total_matches += 1

                if day_match_lines:
                    by_league[k].append(f"📅 {day_title} ({len(day_match_lines)} Matches):")
                    by_league[k].extend(day_match_lines)

            if total_matches == 0:
                return f"=== SOCCER {scope_label} (ALL MAJOR COMPETITIONS) ===\nNo upcoming matches scheduled in the next {days_ahead} days."

            output_lines = [f"=== SOCCER {scope_label} (ALL MAJOR COMPETITIONS) ===\n"]
            for k, code, name in league_items:
                if by_league[k]:
                    output_lines.append(f"🏆 === {name.upper()} ===")
                    output_lines.extend(by_league[k])
                    output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching multi-league soccer {scope_label}: {e}"

    def get_multi_league_past_schedule_range(self, days_back: int = 7, scope_label: str = "RECENT MATCH RESULTS") -> str:
        """Fetches recent completed match results across all major soccer competitions in parallel."""
        print(f"  └─ ⚽ [Soccer]: Fetching {scope_label} across all major competitions (Past {days_back} days)...")
        now = get_local_now().replace(tzinfo=None)
        start_date = now - timedelta(days=days_back)

        try:
            league_items = [(k, self.LEAGUES[k]["code"], self.LEAGUES[k]["name"]) for k in self.MULTI_LEAGUE_KEYS]
            base_urls = [f"{self.SCOREBOARD_BASE}/{code}/scoreboard" for _, code, _ in league_items]
            base_payloads = parallel_fetch_json(fetch_json, base_urls, timeout=6, ttl=60)

            day_requests: List[Tuple[str, str, str, str, str]] = []
            for (k, code, name), base_data in zip(league_items, base_payloads):
                leagues = (base_data or {}).get("leagues", [])
                calendar = leagues[0].get("calendar", []) if leagues else []
                past_dates: List[str] = []
                for item in calendar:
                    if isinstance(item, str):
                        try:
                            clean_dt = datetime.fromisoformat(item.replace("Z", "+00:00")).replace(tzinfo=None)
                            if clean_dt.date() < now.date():
                                d_str = clean_dt.strftime("%Y%m%d")
                                if d_str not in past_dates:
                                    past_dates.append(d_str)
                        except Exception:
                            pass

                if not past_dates:
                    for ev in (base_data or {}).get("events", []):
                        try:
                            ev_dt = datetime.fromisoformat(ev.get("date", "").replace("Z", "+00:00")).replace(tzinfo=None)
                            if ev_dt.date() <= now.date():
                                d_str = ev_dt.strftime("%Y%m%d")
                                if d_str not in past_dates:
                                    past_dates.append(d_str)
                        except Exception:
                            pass
                    if "uefa" in code:
                        for d in range(days_back, -1, -1):
                            cand = now - timedelta(days=d)
                            if cand.weekday() in (1, 2, 3):
                                d_str = cand.strftime("%Y%m%d")
                                if d_str not in past_dates:
                                    past_dates.append(d_str)

                recent_dates = [d for d in sorted(past_dates) if datetime.strptime(d, "%Y%m%d").date() >= start_date.date()]
                recent_dates = recent_dates[-2:] if recent_dates else sorted(past_dates)[-2:]
                for pdate in recent_dates:
                    day_requests.append((k, code, name, pdate, f"{self.SCOREBOARD_BASE}/{code}/scoreboard?dates={pdate}"))

            if not day_requests:
                return f"=== SOCCER {scope_label} (ALL MAJOR COMPETITIONS) ===\nNo recent completed matches found."

            day_urls = [req[4] for req in day_requests]
            day_payloads = parallel_fetch_json(fetch_json, day_urls, timeout=6, ttl=30)

            by_league: Dict[str, List[str]] = {k: [] for k, _, _ in league_items}
            total_matches = 0

            for (k, code, name, pdate, _), day_data in zip(day_requests, day_payloads):
                events = (day_data or {}).get("events", [])
                if not events:
                    continue
                day_title = self.format_human_date(pdate)
                day_match_lines: List[str] = []
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                    home_name = home.get("team", {}).get("displayName", "Home")
                    away_name = away.get("team", {}).get("displayName", "Away")
                    home_score = home.get("score", "0")
                    away_score = away.get("score", "0")
                    status_desc = comp.get("status", {}).get("type", {}).get("detail") or "FT"

                    goals, _, red_cards, _ = self._extract_match_details(comp, home, away, home_name, away_name)
                    day_match_lines.append(f"  • {home_name} {home_score} - {away_score} {away_name} [{status_desc}]")
                    if goals:
                        day_match_lines.append(f"    🎯 Goals: {', '.join(goals)}")
                    if red_cards:
                        day_match_lines.append(f"    🟥 Red Cards: {', '.join(red_cards)}")
                    total_matches += 1

                if day_match_lines:
                    by_league[k].append(f"📅 {day_title}:")
                    by_league[k].extend(day_match_lines)

            if total_matches == 0:
                return f"=== SOCCER {scope_label} (ALL MAJOR COMPETITIONS) ===\nNo recent completed matches found."

            output_lines = [f"=== SOCCER {scope_label} (ALL MAJOR COMPETITIONS) ===\n"]
            for k, code, name in league_items:
                if by_league[k]:
                    output_lines.append(f"🏆 === {name.upper()} ===")
                    output_lines.extend(by_league[k])
                    output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching multi-league recent results: {e}"

    def get_multi_league_matches(self, date_param: Optional[str] = None) -> str:
        """Fetches live, completed, and scheduled matches for a specific date across all major soccer competitions in parallel."""
        now = get_local_now().replace(tzinfo=None)
        target_date = date_param or now.strftime("%Y%m%d")
        day_display = self.format_human_date(target_date)
        print(f"  └─ ⚽ [Soccer]: Fetching matches across all major competitions for {day_display}...")

        try:
            league_items = [(k, self.LEAGUES[k]["code"], self.LEAGUES[k]["name"]) for k in self.MULTI_LEAGUE_KEYS]
            urls = [f"{self.SCOREBOARD_BASE}/{code}/scoreboard?dates={target_date}" for _, code, _ in league_items]
            payloads = parallel_fetch_json(fetch_json, urls, timeout=6, ttl=20)

            output_lines = [f"=== SOCCER MATCHES — {day_display.upper()} (ALL MAJOR COMPETITIONS) ===\n"]
            total_matches = 0
            any_live_or_upcoming_today = False

            for (k, code, name), data in zip(league_items, payloads):
                events = (data or {}).get("events", [])
                if not events:
                    continue
                league_lines: List[str] = [f"🏆 === {name.upper()} ==="]
                for ev in events:
                    comps = ev.get("competitions", [])
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors", [])
                    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0] if competitors else {})
                    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else {})

                    home_name = home.get("team", {}).get("displayName", "Home")
                    away_name = away.get("team", {}).get("displayName", "Away")
                    home_score = home.get("score", "0")
                    away_score = away.get("score", "0")

                    status_obj = comp.get("status", {})
                    status_desc = status_obj.get("type", {}).get("detail") or "Scheduled"
                    is_completed = status_obj.get("type", {}).get("completed", False)
                    is_live = status_obj.get("type", {}).get("state") == "in"
                    if not is_completed:
                        any_live_or_upcoming_today = True

                    m_date = ev.get("date", "")
                    kickoff_str = f" | Kickoff: {m_date[:16].replace('T', ' ')} UTC" if (len(m_date) >= 16 and not is_live and not is_completed) else ""
                    live_marker = " 🔴 [LIVE]" if is_live else (" ✅ [FT]" if is_completed else "")
                    league_lines.append(f"  ⚽ {home_name} {home_score} - {away_score} {away_name}{live_marker} ({status_desc}{kickoff_str})")

                    goals, _, red_cards, stats_line = self._extract_match_details(comp, home, away, home_name, away_name)
                    if goals:
                        league_lines.append(f"     🎯 Goals: {', '.join(goals)}")
                    if red_cards:
                        league_lines.append(f"     🟥 Red Cards: {', '.join(red_cards)}")
                    if stats_line and (is_live or is_completed):
                        league_lines.append(f"     📊 Stats: {stats_line}")
                    total_matches += 1

                if len(league_lines) > 1:
                    output_lines.extend(league_lines)
                    output_lines.append("")

            if total_matches == 0:
                if not date_param:
                    return self.get_multi_league_schedule_range(days_ahead=7, scope_label="UPCOMING MATCHES THIS WEEK")
                return f"=== SOCCER MATCHES — {day_display.upper()} ===\nNo major competition matches scheduled for {day_display}."

            if not date_param and not any_live_or_upcoming_today:
                upcoming_week = self.get_multi_league_schedule_range(days_ahead=7, scope_label="UPCOMING MATCHES THIS WEEK")
                if "No upcoming matches" not in upcoming_week:
                    output_lines.append(upcoming_week)

            return "\n".join(output_lines)

        except Exception as e:
            return f"⚠️ Error fetching multi-league soccer matches: {e}"

