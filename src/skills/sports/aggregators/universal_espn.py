"""
Universal ESPN Sports Aggregator.
Provides a generic, schema-driven ESPN aggregator + auto-discovery engine that supports
NFL, NCAAF, MLB, WNBA, NCAAB, UFC/MMA, Tennis (ATP/WTA), Golf (PGA), and any team,
player, or league discoverable via ESPN's public v2/v3 APIs.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.aggregators.espn_utils import (
    cached_espn_get,
    extract_score_str,
    resolve_espn_team,
    parse_team_schedule,
    parse_team_roster_and_stats,
    parse_league_injuries,
    parse_match_summary,
    espn_search_auto_discover,
    parse_athlete_overview,
)


class UniversalEspnAggregator(BaseSportsAggregator):
    """Generic ESPN aggregator supporting any sport, league, team, or athlete."""

    SPORT_LEAGUE_REGISTRY: Dict[str, Dict[str, Any]] = {
        "nfl": {
            "sport": "football",
            "league": "nfl",
            "label": "NFL Football",
            "icon": "🏈",
            "aliases": [
                "nfl", "national football league", "super bowl", "touchdown", "quarterback",
                "chiefs", "eagles", "cowboys", "49ers", "niners", "ravens", "bills", "lions",
                "packers", "bengals", "texans", "steelers", "jets", "giants", "patriots",
                "dolphins", "chargers", "raiders", "broncos", "seahawks", "rams", "cardinals",
                "falcons", "saints", "buccaneers", "bucs", "panthers", "bears", "vikings",
                "commanders", "browns", "colts", "jaguars", "titans",
            ],
        },
        "ncaaf": {
            "sport": "football",
            "league": "college-football",
            "label": "NCAA College Football",
            "icon": "🏈",
            "aliases": [
                "ncaaf", "college football", "cfb", "heisman", "crimson tide", "longhorns",
                "buckeyes", "wolverines", "bulldogs", "fighting irish", "sooners", "oregon ducks",
            ],
        },
        "mlb": {
            "sport": "baseball",
            "league": "mlb",
            "label": "MLB Baseball",
            "icon": "⚾",
            "aliases": [
                "mlb", "baseball", "world series", "home run", "innings",
                "yankees", "dodgers", "red sox", "blue jays", "mets", "astros", "braves",
                "phillies", "padres", "cubs", "cardinals", "orioles", "guardians", "twins",
                "mariners", "rangers", "diamondbacks", "d-backs", "brewers", "reds", "pirates",
                "royals", "tigers", "white sox", "angels", "athletics", "marlins", "nationals", "rockies",
            ],
        },
        "wnba": {
            "sport": "basketball",
            "league": "wnba",
            "label": "WNBA Basketball",
            "icon": "🏀",
            "aliases": [
                "wnba", "indiana fever", "las vegas aces", "new york liberty", "minnesota lynx",
                "connecticut sun", "seattle storm", "chicago sky", "caitlin clark", "aja wilson",
            ],
        },
        "ncaab": {
            "sport": "basketball",
            "league": "mens-college-basketball",
            "label": "NCAA Men's College Basketball",
            "icon": "🏀",
            "aliases": [
                "ncaab", "college basketball", "march madness", "final four", "duke blue devils",
                "kansas jayhawks", "kentucky wildcats", "uconn huskies", "unc tar heels",
            ],
        },
        "ufc": {
            "sport": "mma",
            "league": "ufc",
            "label": "UFC / MMA",
            "icon": "🥊",
            "aliases": [
                "ufc", "mma", "mixed martial arts", "fight night", "octagon",
                "makhachev", "pereira", "topuria", "jones", "adesanya", "chimaev",
                "holloway", "oliveira", "omalley", "o'malley", "dvalishvili", "strickland", "du plessis",
            ],
        },
        "atp": {
            "sport": "tennis",
            "league": "atp",
            "label": "ATP Tennis",
            "icon": "🎾",
            "aliases": [
                "tennis", "atp", "wimbledon", "us open tennis", "roland garros", "french open",
                "australian open", "alcaraz", "sinner", "djokovic", "zverev", "medvedev",
            ],
        },
        "wta": {
            "sport": "tennis",
            "league": "wta",
            "label": "WTA Tennis",
            "icon": "🎾",
            "aliases": ["wta", "swiatek", "sabalenka", "gauff", "rybakina"],
        },
        "pga": {
            "sport": "golf",
            "league": "pga",
            "label": "PGA Tour Golf",
            "icon": "⛳",
            "aliases": ["pga", "golf", "masters tournament", "scheffler", "mcilroy", "rahm", "schauffele"],
        },
    }

    SPORT_ICONS = {
        "football": "🏈",
        "baseball": "⚾",
        "basketball": "🏀",
        "hockey": "🏒",
        "soccer": "⚽",
        "mma": "🥊",
        "tennis": "🎾",
        "golf": "⛳",
        "racing": "🏎️",
        "rugby": "🏉",
        "cricket": "🏏",
    }

    def resolve_sport_context(self, query: str) -> Tuple[str, str, str, str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Determine (sport_path, league_code, league_label, icon, matched_team, discovered_player)
        from either keyword registry or live ESPN Search v2 auto-discovery.
        """
        q_low = (query or "").lower().strip()

        # 1. Check direct alias registry
        matched_reg = None
        for reg in self.SPORT_LEAGUE_REGISTRY.values():
            if any(alias in q_low for alias in reg["aliases"]):
                matched_reg = reg
                break

        # 2. Query ESPN Universal Search v2 to discover specific team, player, or league
        disc = espn_search_auto_discover(q_low)

        if matched_reg:
            sport_path = matched_reg["sport"]
            league_code = matched_reg["league"]
            label = matched_reg["label"]
            icon = matched_reg["icon"]
        elif disc:
            sport_path = disc["sport"]
            league_code = disc["league"] or "nfl"
            label = f"{sport_path.upper()} ({league_code.upper()})"
            for reg in self.SPORT_LEAGUE_REGISTRY.values():
                if reg["sport"] == sport_path and reg["league"] == league_code:
                    label = reg["label"]
                    break
            icon = self.SPORT_ICONS.get(sport_path, "🏟️")
        else:
            # Default fallback to NFL
            reg = self.SPORT_LEAGUE_REGISTRY["nfl"]
            sport_path, league_code, label, icon = reg["sport"], reg["league"], reg["label"], reg["icon"]

        matched_team = resolve_espn_team(sport_path, league_code, q_low)
        discovered_player = None

        if disc and disc.get("sport") == sport_path:
            if disc.get("entity_type") == "team" and disc.get("team_id") and not matched_team:
                matched_team = {"id": disc["team_id"], "displayName": disc["display_name"]}
            elif disc.get("entity_type") == "player" and disc.get("athlete_id"):
                discovered_player = disc
                if disc.get("subtitle") and not matched_team:
                    matched_team = resolve_espn_team(sport_path, league_code, disc["subtitle"])

        return sport_path, league_code, label, icon, matched_team, discovered_player

    def get_info(self, query: str = "") -> str:
        """Smart entry point for any sport/team/player query via ESPN."""
        q = (query or "").lower().strip()
        sport_path, league_code, label, icon, matched_team, discovered_player = self.resolve_sport_context(q)

        # 1. Injuries & Availability Reports
        if any(w in q for w in ("injur", "injured", "unavailable", "availability", "day-to-day", "questionable", "out", "ir")):
            return parse_league_injuries(sport_path, league_code, label, q)

        # 2. Individual Athlete Profile / Stats (if an athlete was discovered)
        if discovered_player and not any(w in q for w in ("roster", "squad", "schedule", "fixtures", "standing", "table")):
            ath_out = parse_athlete_overview(
                sport_path,
                league_code,
                discovered_player["athlete_id"],
                discovered_player["display_name"],
                discovered_player.get("subtitle", ""),
            )
            if ath_out:
                return ath_out

        # 3. Team Roster & Player Stats
        if any(w in q for w in ("roster", "squad", "player", "players", "stat", "stats", "depth")) and matched_team:
            return parse_team_roster_and_stats(
                sport_path, league_code, matched_team["id"], matched_team["displayName"], player_filter=q
            )

        # 4. Standings / Division / Conference Tables
        if any(w in q for w in ("standing", "table", "rank", "division", "conference", "seeds", "playoff")):
            return self.get_standings(sport_path, league_code, label, icon)

        # 5. Boxscore / Game Summary
        if any(w in q for w in ("boxscore", "summary", "lineup", "play-by-play")) and matched_team:
            sched_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/teams/{matched_team['id']}/schedule"
            sched_data = cached_espn_get(sched_url, ttl=300)
            if sched_data and sched_data.get("events"):
                evs = sorted(sched_data["events"], key=lambda e: e.get("date", ""))
                done = [
                    e for e in evs
                    if e.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("state") in ("in", "post")
                ]
                target_ev = done[-1] if done else evs[-1]
                sum_out = parse_match_summary(sport_path, league_code, str(target_ev.get("id", "")))
                if sum_out:
                    return sum_out

        # 6. Team Schedule + League Scoreboard
        team_sched_block = ""
        if matched_team:
            team_sched_block = parse_team_schedule(sport_path, league_code, matched_team["id"], matched_team["displayName"])

        sb_out = self.get_scoreboard(sport_path, league_code, label, icon, filter_query=q)
        if team_sched_block:
            return f"{team_sched_block}\n\n{sb_out}"
        return sb_out

    def get_scoreboard(
        self,
        sport_path: str,
        league_code: str,
        label: str,
        icon: str,
        date_param: Optional[str] = None,
        filter_query: str = "",
    ) -> str:
        """Fetch and format scoreboard events for any ESPN sport/league (team sports and MMA/individual sports)."""
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/scoreboard"
        params = {"dates": date_param} if date_param else None
        data = cached_espn_get(url, params=params, ttl=120)
        if not data:
            return f"Could not fetch scoreboard for {label}."

        events = data.get("events", [])
        now = datetime.now()
        if not events:
            return f"=== {icon} {label.upper()} SCOREBOARD ===\nNo events currently scheduled on the active {label} scoreboard."

        day_display = self.format_human_date(date_param) if date_param else self.format_human_date(now)
        out = [f"=== {icon} {label.upper()} SCOREBOARD ({day_display.upper()}) ===\n"]

        for ev in events[:15]:
            ev_name = ev.get("name", "Event")
            ev_date = ev.get("date", "")
            date_str = ev_date[:16].replace("T", " ") + " UTC" if len(ev_date) >= 16 else ""
            comps = ev.get("competitions", [])
            if not comps:
                continue

            # Combat / multi-bout event cards (e.g. UFC / MMA) where one event has many bouts
            if sport_path == "mma" or (len(comps) > 1 and comps[0].get("competitors", [{}])[0].get("athlete")):
                out.append(f"{icon} **{ev_name}** ({date_str})")
                for bout in comps[:10]:
                    fighters = bout.get("competitors", [])
                    if len(fighters) < 2:
                        continue
                    f1, f2 = fighters[0], fighters[1]
                    n1 = f1.get("athlete", {}).get("displayName", "Fighter 1")
                    n2 = f2.get("athlete", {}).get("displayName", "Fighter 2")
                    r1 = (f1.get("records") or [{}])[0].get("summary", "")
                    r2 = (f2.get("records") or [{}])[0].get("summary", "")
                    w1 = " 🏆" if f1.get("winner") else ""
                    w2 = " 🏆" if f2.get("winner") else ""
                    st_det = bout.get("status", {}).get("type", {}).get("detail", "Scheduled")
                    r1_str = f" ({r1})" if r1 else ""
                    r2_str = f" ({r2})" if r2 else ""
                    out.append(f"  • **{n1}**{r1_str}{w1} vs **{n2}**{r2_str}{w2} [{st_det}]")
                out.append("")
                continue

            comp = comps[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue

            home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            h_team = home.get("team", {}).get("displayName") or home.get("athlete", {}).get("displayName", "Home")
            a_team = away.get("team", {}).get("displayName") or away.get("athlete", {}).get("displayName", "Away")
            h_score = extract_score_str(home.get("score"))
            a_score = extract_score_str(away.get("score"))

            h_rec = (home.get("records") or [{}])[0].get("summary", "")
            a_rec = (away.get("records") or [{}])[0].get("summary", "")
            h_rec_str = f" ({h_rec})" if h_rec else ""
            a_rec_str = f" ({a_rec})" if a_rec else ""

            status_obj = comp.get("status", {}).get("type", {})
            status_desc = status_obj.get("detail") or status_obj.get("description") or "Scheduled"
            is_live = status_obj.get("state") == "in"
            is_done = status_obj.get("completed", False)
            badge = " 🔴 [LIVE]" if is_live else (" ✅ [FINAL]" if is_done else "")

            out.append(f"{icon} **{a_team}**{a_rec_str} {a_score} @ **{h_team}**{h_rec_str} {h_score}{badge}")
            out.append(f"   Status: {status_desc}" + (f" | Kickoff/Start: {date_str}" if date_str and not is_live and not is_done else ""))

            # Period/Quarter/Inning linescores
            a_lines = [str(int(l.get("value", 0))) for l in away.get("linescores", []) if l.get("value") is not None]
            h_lines = [str(int(l.get("value", 0))) for l in home.get("linescores", []) if l.get("value") is not None]
            if a_lines and h_lines:
                out.append(f"   Linescore: {a_team[:12]} [{' | '.join(a_lines)}] vs {h_team[:12]} [{' | '.join(h_lines)}]")

            # Top game leaders
            ldr_parts: List[str] = []
            for side in (away, home):
                for l_cat in side.get("leaders", [])[:3]:
                    abbr = l_cat.get("abbreviation", "")
                    top_list = l_cat.get("leaders", [])
                    if top_list:
                        top = top_list[0]
                        p_name = top.get("athlete", {}).get("shortName") or top.get("athlete", {}).get("displayName", "")
                        val = top.get("displayValue", "")
                        if p_name and val and val != "0":
                            ldr_parts.append(f"{p_name} ({val} {abbr})")
            if ldr_parts:
                out.append(f"   🌟 Leaders: {', '.join(ldr_parts[:4])}")

            venue = comp.get("venue", {}).get("fullName")
            if venue:
                out.append(f"   Venue: {venue}")
            out.append("")

        return "\n".join(out).strip()

    def get_standings(self, sport_path: str, league_code: str, label: str, icon: str) -> str:
        """Fetch and format standings for any ESPN sport/league."""
        url = f"https://site.api.espn.com/apis/v2/sports/{sport_path}/{league_code}/standings"
        data = cached_espn_get(url, ttl=600)
        if not data:
            return f"No standings data available for {label}."

        children = data.get("children", [])
        if not children and "standings" in data:
            children = [{"name": label, "standings": data["standings"]}]
        if not children:
            return f"No standings table currently available for {label}."

        out = [f"=== {icon} {label.upper()} STANDINGS ===\n"]
        for group in children[:6]:
            g_name = group.get("name", "Standings")
            entries = group.get("standings", {}).get("entries", [])
            # Some leagues nest divisions inside conferences
            if not entries and group.get("children"):
                for div in group.get("children", []):
                    d_name = div.get("name", "Division")
                    d_entries = div.get("standings", {}).get("entries", [])
                    if d_entries:
                        out.append(f"🏆 **{g_name} — {d_name}**")
                        for idx, entry in enumerate(d_entries[:10], 1):
                            t_name = entry.get("team", {}).get("displayName", "Team")
                            stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                            w = stats.get("wins", "0")
                            l = stats.get("losses", "0")
                            ties = stats.get("ties")
                            pct = stats.get("winPercent") or stats.get("points", "")
                            rec = f"{w}-{l}-{ties}" if ties and ties != "0" else f"{w}-{l}"
                            out.append(f"  {idx}. **{t_name}**: {rec}" + (f" ({pct})" if pct else ""))
                        out.append("")
                continue

            if entries:
                out.append(f"🏆 **{g_name}**")
                for idx, entry in enumerate(entries[:12], 1):
                    t_name = entry.get("team", {}).get("displayName", "Team")
                    stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", [])}
                    w = stats.get("wins", "0")
                    l = stats.get("losses", "0")
                    pct = stats.get("winPercent") or stats.get("points", "")
                    out.append(f"  {idx}. **{t_name}**: {w}-{l}" + (f" ({pct})" if pct else ""))
                out.append("")

        return "\n".join(out).strip()
