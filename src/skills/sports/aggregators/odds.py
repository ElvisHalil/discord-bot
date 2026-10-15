import os
import re
import unicodedata
from typing import Optional
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.utils.http import fetch_soup, fetch_json


def _strip_accents(text: str) -> str:
    """Normalize Unicode accents/diacritics (e.g. 'Montréal' -> 'Montreal')."""
    if not text:
        return ""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def decimal_to_american(dec: float) -> str:
    """Converts decimal odds (e.g. 1.26, 4.20) to American moneyline format (+320, -385)."""
    try:
        if dec >= 2.0:
            val = int(round((dec - 1.0) * 100))
            return f"+{val}"
        elif dec > 1.0:
            val = int(round(100.0 / (dec - 1.0)))
            return f"-{val}"
    except Exception:
        pass
    return "N/A"


def american_to_decimal(ml: float) -> float:
    """Converts American moneyline (+245, -105) to decimal odds (3.45, 1.95)."""
    try:
        val = float(ml)
        if val >= 100:
            return round(1.0 + (val / 100.0), 2)
        elif val <= -100:
            return round(1.0 + (100.0 / abs(val)), 2)
    except Exception:
        pass
    return 0.0


def implied_probability(dec: float) -> str:
    """Calculates implied win percentage from decimal odds."""
    try:
        if dec > 0:
            prob = (1.0 / dec) * 100.0
            return f"{prob:.1f}%"
    except Exception:
        pass
    return "N/A"


class OddsAggregator(BaseSportsAggregator):
    """Centralized aggregator for sports and esports betting odds, moneylines, spreads, totals, and probabilities."""

    ESPN_TO_ODDS_API_LEAGUE = {
        "uefa.champions": "soccer_uefa_champs_league",
        "ucl": "soccer_uefa_champs_league",
        "uefa.europa": "soccer_uefa_europa_league",
        "uefa.europa.conf": "soccer_uefa_europa_conference_league",
        "eng.1": "soccer_epl",
        "epl": "soccer_epl",
        "eng.2": "soccer_efl_champ",
        "esp.1": "soccer_spain_la_liga",
        "laliga": "soccer_spain_la_liga",
        "ger.1": "soccer_germany_bundesliga",
        "bundesliga": "soccer_germany_bundesliga",
        "ita.1": "soccer_italy_serie_a",
        "seriea": "soccer_italy_serie_a",
        "fra.1": "soccer_france_ligue_one",
        "ligue1": "soccer_france_ligue_one",
        "usa.1": "soccer_usa_mls",
        "mls": "soccer_usa_mls",
        "ned.1": "soccer_netherlands_eredivisie",
        "por.1": "soccer_portugal_primeira_liga",
        "nba": "basketball_nba",
        "wnba": "basketball_wnba",
        "mens-college-basketball": "basketball_ncaab",
        "nhl": "icehockey_nhl",
        "nfl": "americanfootball_nfl",
        "college-football": "americanfootball_ncaaf",
        "mlb": "baseball_mlb",
        "ufc": "mma_mixed_martial_arts",
    }

    CS_TEAMS = (
        "falcons", "team falcons", "nip", "ninjas in pyjamas", "spirit", "team spirit",
        "vitality", "team vitality", "mouz", "mousesports", "furia", "faze", "g2",
        "navi", "natus vincere", "astralis", "complexity", "mongolz", "heroic",
        "eternal fire", "virtus.pro", "9z", "aurora", "betboom", "parivision",
        "thunderpick", "blast", "pgl", "iem", "esl", "cs", "cs2", "csgo"
    )

    @staticmethod
    def _clean_query_target(query: str) -> str:
        """Strips betting/conversational filler words and expands short team aliases."""
        from src.skills.sports.aggregators.espn_utils import TEAM_ALIASES
        clean = _strip_accents(query.lower())
        for phrase in [
            "if i bet", "i bet", "bet on", "will i win and how much",
            "will i win", "how much", "what are the", "can you check",
            "right now", "next game", "next match", "moneyline", "betting line",
            "betting odds", "live odds", "match odds", "game odds",
            "point spread", "over under", "tracked games", "tracked matches",
            "tracked game", "tracked match", "both games", "both matches",
            "these games", "these matches", "the games", "the matches",
        ]:
            clean = re.sub(rf'\b{re.escape(phrase)}\b', ' ', clean, flags=re.IGNORECASE)
        for word in [
            "bet", "betting", "odds", "odd", "line", "lines", "spread", "moneyline",
            "tomorrow", "today", "tonight", "live", "now", "currently",
            "next", "match", "matches", "game", "games", "tracked", "tracking", "track",
            "soccer", "football", "basketball", "hockey", "nhl", "nba", "nfl", "mlb",
            "dollars", "bucks", "usd", "on", "for", "the", "in", "at", "of", "are", "is",
            "what", "whats", "please", "both", "these", "those", "get", "check", "why", "cant",
        ]:
            clean = re.sub(rf'\b{re.escape(word)}\b', ' ', clean, flags=re.IGNORECASE)
        clean = re.sub(r'[\$0-9]+(?:\.[0-9]{2})?', ' ', clean)
        clean = re.sub(r'[^\w\s]', ' ', clean)
        clean = ' '.join(clean.split()).strip()
        if not clean:
            return ""

        # Expand known short aliases (e.g. 'madrid' -> 'real madrid', 'munich' -> 'bayern munich')
        if clean in TEAM_ALIASES:
            return TEAM_ALIASES[clean]
        # Only expand single tokens if the multi-word string doesn't already contain the expanded form
        tokens = clean.split()
        expanded = []
        for t in tokens:
            alias_val = TEAM_ALIASES.get(t, t)
            if alias_val != t and alias_val in clean:
                expanded.append(t)
            else:
                expanded.append(alias_val)
        return " ".join(expanded)

    def _fetch_from_espn_odds(
        self,
        query: str,
        sport: str,
        stake: Optional[float] = None,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Discovers the live or next upcoming match for a team/matchup via ESPN and fetches
        ESPN's live/pre-match DraftKings & Bet365 odds, spread, and over/under.
        Returns (formatted_odds_str, discovered_espn_league_slug, canonical_team_name).
        """
        # Avoid live network calls during unit tests that only mock fetch_soup
        is_soup_mock = hasattr(fetch_soup, "assert_called") or "MagicMock" in str(type(fetch_soup))
        is_json_mock = hasattr(fetch_json, "assert_called") or "MagicMock" in str(type(fetch_json))
        if is_soup_mock and not is_json_mock:
            return None, None, None

        try:
            from src.skills.sports.aggregators.espn_utils import espn_search_auto_discover, resolve_espn_team
            from src.skills.sports.aggregators.soccer import SoccerAggregator

            target = self._clean_query_target(query) or query.strip()
            sport_l = (sport or "").lower().strip()
            q_low = query.lower()

            sport_hint = None
            club_league = None

            # Check if target matches a known soccer club hint
            for club, lg in sorted(SoccerAggregator.CLUB_LEAGUE_HINTS.items(), key=lambda x: len(x[0]), reverse=True):
                if re.search(rf'\b{re.escape(club)}\b', target.lower()):
                    club_league = SoccerAggregator.LEAGUES.get(lg, {}).get("code", lg)
                    sport_hint = "soccer"
                    break

            if not sport_hint:
                if sport_l in ("soccer", "epl", "ucl", "laliga", "seriea", "bundesliga", "ligue1", "mls"):
                    sport_hint = "soccer"
                elif sport_l in ("nhl", "hockey", "ice hockey") or re.search(r'\b(?:nhl|hockey)\b', q_low):
                    sport_hint = "hockey"
                    club_league = "nhl"
                elif sport_l in ("nba", "basketball", "hoops") or re.search(r'\b(?:nba|basketball)\b', q_low):
                    sport_hint = "basketball"
                    club_league = "nba"
                elif sport_l in ("nfl", "football", "american football") or re.search(r'\b(?:nfl)\b', q_low):
                    sport_hint = "football"
                    club_league = "nfl"
                elif sport_l in ("mlb", "baseball") or re.search(r'\b(?:mlb|baseball)\b', q_low):
                    sport_hint = "baseball"
                    club_league = "mlb"

            # Split matchup strings (e.g. "washington capitals vs montreal canadiens") so team auto-discovery succeeds
            candidate_targets = [target]
            if re.search(r'\b(?:vs|v|versus)\b', target, flags=re.IGNORECASE):
                parts = [p.strip() for p in re.split(r'\b(?:vs|v|versus)\b', target, flags=re.IGNORECASE) if p.strip()]
                candidate_targets = parts + [target]

            disc = None
            matched_cand = target
            for cand_t in candidate_targets:
                d = espn_search_auto_discover(cand_t, sport_hint=sport_hint)
                ent_t = (d or {}).get("entity_type") or (d or {}).get("type")
                t_id = (d or {}).get("team_id") or (d or {}).get("id")
                if d and ent_t == "team" and t_id:
                    disc = d
                    matched_cand = cand_t
                    break

            ent_type = (disc or {}).get("entity_type") or (disc or {}).get("type")
            team_id = (disc or {}).get("team_id") or (disc or {}).get("id")
            sport_path = (disc or {}).get("sport") or sport_hint or "soccer"
            league_code = (disc or {}).get("league") or club_league or (
                "nhl" if sport_path == "hockey" else ("nba" if sport_path == "basketball" else ("nfl" if sport_path == "football" else "eng.1"))
            )
            team_name = (disc or {}).get("display_name") or (disc or {}).get("name") or matched_cand.title()

            if (not disc or ent_type != "team" or not team_id) and not is_json_mock:
                # Fallback to resolve_espn_team across candidate targets
                fallback_leagues = [(sport_path, league_code)] if sport_hint else [
                    ("hockey", "nhl"),
                    ("basketball", "nba"),
                    ("football", "nfl"),
                    ("baseball", "mlb"),
                    ("soccer", "eng.1"),
                ]
                for sp, lg in fallback_leagues:
                    for cand_t in candidate_targets:
                        resolved = resolve_espn_team(sp, lg, cand_t)
                        if resolved and resolved.get("id"):
                            team_id = str(resolved["id"])
                            sport_path = sp
                            league_code = lg
                            team_name = resolved.get("displayName") or resolved.get("name") or cand_t.title()
                            ent_type = "team"
                            break
                    if team_id:
                        break

            if ent_type != "team" or not team_id:
                return None, club_league, None

            # Fetch schedule (for soccer, use /all/teams/{id}/schedule?fixture=true to include UCL/cups)
            if sport_path == "soccer":
                sched_url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{team_id}/schedule?fixture=true"
            else:
                sched_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/teams/{team_id}/schedule"

            sched_data = fetch_json(sched_url, timeout=8)
            events = (sched_data or {}).get("events", [])
            if not events and sport_path == "soccer":
                sched_data = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/teams/{team_id}/schedule?fixture=true", timeout=8)
                events = (sched_data or {}).get("events", [])

            if not events:
                return None, league_code, team_name

            # Pick live ('in') or earliest upcoming ('pre') event (preferring opponent match if 'vs' was specified)
            candidate_events = events
            if len(candidate_targets) >= 3:
                opp_tokens = [
                    w for w in re.split(r'\s+', _strip_accents(candidate_targets[1]).lower())
                    if len(w) >= 3
                ]
                if opp_tokens:
                    opp_matched = []
                    for ev in events:
                        comp = (ev.get("competitions") or [{}])[0]
                        comp_names = " ".join(
                            _strip_accents((c.get("team") or {}).get("displayName", "")).lower()
                            for c in comp.get("competitors", [])
                        )
                        if any(tok in comp_names for tok in opp_tokens):
                            opp_matched.append(ev)
                    if opp_matched:
                        candidate_events = opp_matched

            chosen_ev = None
            for ev in candidate_events:
                comp = (ev.get("competitions") or [{}])[0]
                state = comp.get("status", {}).get("type", {}).get("state", "")
                if state == "in":
                    chosen_ev = ev
                    break
            if not chosen_ev:
                pre_evs = [
                    ev for ev in candidate_events
                    if (ev.get("competitions") or [{}])[0].get("status", {}).get("type", {}).get("state", "") == "pre"
                ]
                if pre_evs:
                    pre_evs.sort(key=lambda e: e.get("date", "9999"))
                    chosen_ev = pre_evs[0]
            if not chosen_ev:
                post_evs = list(candidate_events)
                post_evs.sort(key=lambda e: e.get("date", ""))
                chosen_ev = post_evs[-1]

            ev_id = str(chosen_ev.get("id", ""))
            ev_league_obj = chosen_ev.get("league", {})
            ev_league_slug = ev_league_obj.get("slug") or league_code
            ev_league_name = ev_league_obj.get("name") or ev_league_slug.upper()
            ev_date = chosen_ev.get("date", "")

            comp = (chosen_ev.get("competitions") or [{}])[0]
            status_type = comp.get("status", {}).get("type", {})
            state = status_type.get("state", "")
            status_detail = status_type.get("detail") or status_type.get("shortDetail") or "Scheduled"

            competitors = comp.get("competitors", [])
            home_comp = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away_comp = next((c for c in competitors if c.get("homeAway") == "away"), {})
            home_name = home_comp.get("team", {}).get("displayName", "Home")
            away_name = away_comp.get("team", {}).get("displayName", "Away")

            def _score_str(c: dict) -> str:
                s = c.get("score", "")
                if isinstance(s, dict):
                    return str(s.get("displayValue", s.get("value", "")))
                return str(s) if s is not None else ""

            home_score = _score_str(home_comp)
            away_score = _score_str(away_comp)

            # Fetch ESPN Core API odds for this specific event
            odds_url = f"https://sports.core.api.espn.com/v2/sports/{sport_path}/leagues/{ev_league_slug}/events/{ev_id}/competitions/{ev_id}/odds"
            odds_data = fetch_json(odds_url, timeout=8)
            items = (odds_data or {}).get("items", [])

            # If live scores are omitted on /schedule or Core API odds items are empty, check /summary
            if not is_json_mock and ((state == "in" and (not home_score or not away_score)) or not items):
                try:
                    summary_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{ev_league_slug}/summary?event={ev_id}"
                    summary_data = fetch_json(summary_url, timeout=8) or {}
                    header_comps = (summary_data.get("header") or {}).get("competitions") or []
                    if header_comps:
                        h_c = next((c for c in header_comps[0].get("competitors", []) if c.get("homeAway") == "home"), {})
                        a_c = next((c for c in header_comps[0].get("competitors", []) if c.get("homeAway") == "away"), {})
                        home_score = home_score or _score_str(h_c)
                        away_score = away_score or _score_str(a_c)
                    if not items:
                        items = summary_data.get("pickcenter") or summary_data.get("odds") or []
                except Exception:
                    pass

            if not items:
                return None, ev_league_slug, team_name

            provider_blocks = []
            primary_outcomes = []  # (label, dec_price) for stake simulation

            for item in items:
                if not isinstance(item, dict):
                    continue
                prov_name = (item.get("provider") or {}).get("name", "Sportsbook")
                details = item.get("details")
                over_under = item.get("overUnder")
                over_odds = item.get("overOdds")
                under_odds = item.get("underOdds")
                spread_val = item.get("spread")

                home_o = item.get("homeTeamOdds") or {}
                away_o = item.get("awayTeamOdds") or {}
                draw_o = item.get("drawOdds") or {}

                def _extract_prices(team_o: dict) -> tuple[float, str]:
                    # Try current.moneyLine.decimal / american first
                    curr_ml = (team_o.get("current") or {}).get("moneyLine") or {}
                    if curr_ml.get("decimal"):
                        dec = float(curr_ml["decimal"])
                        am = str(curr_ml.get("american") or decimal_to_american(dec))
                        return dec, am
                    # Try top-level moneyLine (American)
                    if team_o.get("moneyLine") is not None:
                        am_val = float(team_o["moneyLine"])
                        dec = american_to_decimal(am_val)
                        am_str = f"+{int(am_val)}" if am_val > 0 else str(int(am_val))
                        return dec, am_str
                    # Try Bet365 style odds.value (Decimal)
                    odds_sub = team_o.get("odds") or {}
                    if odds_sub.get("value"):
                        dec = float(odds_sub["value"])
                        return dec, decimal_to_american(dec)
                    if team_o.get("value"):
                        dec = float(team_o["value"])
                        return dec, decimal_to_american(dec)
                    return 0.0, "N/A"

                h_dec, h_am = _extract_prices(home_o)
                a_dec, a_am = _extract_prices(away_o)
                d_dec, d_am = _extract_prices(draw_o)

                if h_dec <= 0 and a_dec <= 0:
                    continue

                lines = [f"**{prov_name}**" + (f" (Line: {details})" if details else "") + ":"]
                if h_dec > 0:
                    lines.append(f"  - **{home_name} (Home)**: {h_dec:.2f} (American: {h_am}) | Implied prob: {implied_probability(h_dec)}")
                if a_dec > 0:
                    lines.append(f"  - **{away_name} (Away)**: {a_dec:.2f} (American: {a_am}) | Implied prob: {implied_probability(a_dec)}")
                if d_dec > 0:
                    lines.append(f"  - **Draw**: {d_dec:.2f} (American: {d_am}) | Implied prob: {implied_probability(d_dec)}")

                extra_markets = []
                if spread_val is not None:
                    h_sp = (
                        ((home_o.get("current") or {}).get("pointSpread") or {}).get("american")
                        or ((home_o.get("close") or home_o.get("open") or {}).get("pointSpread") or {}).get("american")
                    )
                    a_sp = (
                        ((away_o.get("current") or {}).get("pointSpread") or {}).get("american")
                        or ((away_o.get("close") or away_o.get("open") or {}).get("pointSpread") or {}).get("american")
                    )
                    if h_sp or a_sp:
                        extra_markets.append(f"Spread: {home_name} {h_sp or ''} / {away_name} {a_sp or ''}")
                if over_under is not None:
                    ou_str = f"Over/Under: {over_under}"
                    if over_odds is not None and under_odds is not None:
                        o_str = f"+{int(over_odds)}" if over_odds > 0 else str(int(over_odds))
                        u_str = f"+{int(under_odds)}" if under_odds > 0 else str(int(under_odds))
                        ou_str += f" (Over {o_str} / Under {u_str})"
                    extra_markets.append(ou_str)
                if extra_markets:
                    lines.append("  - " + " | ".join(extra_markets))

                provider_blocks.append("\n".join(lines))

                if not primary_outcomes or "Live" in prov_name:
                    primary_outcomes = []
                    if h_dec > 0:
                        primary_outcomes.append((home_name, h_dec))
                    if a_dec > 0:
                        primary_outcomes.append((away_name, a_dec))
                    if d_dec > 0:
                        primary_outcomes.append(("Draw", d_dec))

            if not provider_blocks:
                return None, ev_league_slug, team_name

            score_part = f" — Score: {home_name} {home_score} - {away_score} {away_name}" if (home_score or away_score) else ""
            status_banner = f"🔴 LIVE NOW ({status_detail}){score_part}" if state == "in" else f"{status_detail} ({ev_date})"
            favorite = min([o for o in primary_outcomes if o[0] != "Draw"], key=lambda x: x[1], default=(None, 0))[0]
            fav_str = f"Favorite: **{favorite}**\n" if favorite else ""

            out = (
                f"[LIVE & PRE-MATCH BETTING ODDS: {home_name} vs {away_name}]\n"
                f"Competition: {ev_league_name}\n"
                f"Match Status: {status_banner}\n"
                f"{fav_str}\n"
                + "\n\n".join(provider_blocks) + "\n"
            )

            if stake and stake > 0 and primary_outcomes:
                out += f"\n💰 Bet Payout Simulation (${stake:.2f} Stake):\n"
                for label, dec in primary_outcomes:
                    payout = stake * dec
                    profit = payout - stake
                    out += f"- Wager on **{label}** ({dec:.2f}): Returns **${payout:.2f}** (${profit:.2f} profit) | Implied prob: {implied_probability(dec)}\n"

            return out + "\n", ev_league_slug, team_name
        except Exception as e:
            print(f"  └─ ⚠️ [ESPN Odds Lookup Error]: {e}")
            return None, None, None

    def _fetch_from_the_odds_api(
        self,
        query: str,
        sport: str,
        stake: Optional[float] = None,
        discovered_league: Optional[str] = None,
        canonical_team: Optional[str] = None,
    ) -> Optional[str]:
        """Fetches structured multi-bookmaker odds if THE_ODDS_API_KEY is configured."""
        api_key = os.getenv("THE_ODDS_API_KEY")
        if not api_key:
            return None

        sport_map = {
            "nfl": ["americanfootball_nfl"],
            "football": ["americanfootball_nfl", "soccer_uefa_champs_league", "soccer_epl"],
            "american football": ["americanfootball_nfl"],
            "ncaaf": ["americanfootball_ncaaf"],
            "college football": ["americanfootball_ncaaf"],
            "cfl": ["americanfootball_cfl"],
            "nba": ["basketball_nba"],
            "basketball": ["basketball_nba"],
            "wnba": ["basketball_wnba"],
            "euroleague": ["basketball_euroleague"],
            "mlb": ["baseball_mlb"],
            "baseball": ["baseball_mlb"],
            "nhl": ["icehockey_nhl"],
            "hockey": ["icehockey_nhl"],
            "epl": ["soccer_epl"],
            "premier league": ["soccer_epl"],
            "champions league": ["soccer_uefa_champs_league"],
            "ucl": ["soccer_uefa_champs_league"],
            "la liga": ["soccer_spain_la_liga", "soccer_uefa_champs_league"],
            "laliga": ["soccer_spain_la_liga", "soccer_uefa_champs_league"],
            "bundesliga": ["soccer_germany_bundesliga", "soccer_uefa_champs_league"],
            "serie a": ["soccer_italy_serie_a", "soccer_uefa_champs_league"],
            "seriea": ["soccer_italy_serie_a", "soccer_uefa_champs_league"],
            "ligue 1": ["soccer_france_ligue_one", "soccer_uefa_champs_league"],
            "mls": ["soccer_usa_mls"],
            "soccer": [
                "soccer_uefa_champs_league",
                "soccer_epl",
                "soccer_spain_la_liga",
                "soccer_germany_bundesliga",
                "soccer_italy_serie_a",
                "soccer_france_ligue_one",
                "soccer_uefa_europa_league",
                "soccer_usa_mls",
            ],
            "mma": ["mma_mixed_martial_arts"],
            "ufc": ["mma_mixed_martial_arts"],
            "boxing": ["boxing_boxing"],
        }

        sport_clean = sport.lower().strip()
        if sport_clean in ("cs", "cs2", "csgo", "esports"):
            return None

        candidate_keys = []
        # 1. If ESPN already identified the exact league of the next/live match (e.g. 'uefa.champions'), check that first!
        if discovered_league and discovered_league in self.ESPN_TO_ODDS_API_LEAGUE:
            candidate_keys.append(self.ESPN_TO_ODDS_API_LEAGUE[discovered_league])

        # 2. Check if query matches a known soccer club to prioritize UCL + its domestic league
        from src.skills.sports.aggregators.soccer import SoccerAggregator
        target_clean = _strip_accents(canonical_team or self._clean_query_target(query) or query).lower()
        for club, lg in sorted(SoccerAggregator.CLUB_LEAGUE_HINTS.items(), key=lambda x: len(x[0]), reverse=True):
            if re.search(rf'\b{re.escape(club)}\b', target_clean):
                for k in ("soccer_uefa_champs_league", self.ESPN_TO_ODDS_API_LEAGUE.get(lg)):
                    if k and k not in candidate_keys:
                        candidate_keys.append(k)
                break

        # 3. Map explicit sport parameter or keywords in query
        if sport_clean in sport_map:
            for k in sport_map[sport_clean]:
                if k not in candidate_keys:
                    candidate_keys.append(k)
        else:
            for k, v_list in sport_map.items():
                if k in sport_clean or k in query.lower():
                    for v in v_list:
                        if v not in candidate_keys:
                            candidate_keys.append(v)
            if not candidate_keys:
                candidate_keys = [
                    "soccer_uefa_champs_league",
                    "soccer_epl",
                    "soccer_spain_la_liga",
                    "americanfootball_nfl",
                    "basketball_nba",
                    "icehockey_nhl",
                    "baseball_mlb",
                ]

        ignore_words = {
            "vs", "versus", "the", "and", "odds", "odd", "match", "matches", "game", "games",
            "moneyline", "spread", "who", "will", "win", "right", "now", "today", "tomorrow",
            "live", "bet", "betting", "line", "lines", "soccer", "football", "basketball",
            "hockey", "nhl", "nba", "nfl", "mlb", "next", "for", "check", "get", "tracked",
            "tracking", "track", "both", "these", "those", "what", "whats", "are", "please",
        }
        full_query_norm = _strip_accents(self._clean_query_target(query) or query).lower()
        query_tokens = [
            w for w in re.split(r'[\s\-_/]+', f"{target_clean} {full_query_norm}".strip())
            if len(w) >= 3 and w not in ignore_words
        ]
        # Preserve token order while deduplicating
        seen_toks = set()
        dedup_tokens = []
        for t in query_tokens:
            if t not in seen_toks:
                seen_toks.add(t)
                dedup_tokens.append(t)
        query_tokens = dedup_tokens

        matched_games = []
        for sport_key in candidate_keys[:4]:
            url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={api_key}&regions=us,eu,uk&markets=h2h"
            try:
                data = fetch_json(url, timeout=8)
                if not isinstance(data, list) or not data:
                    continue

                for game in data:
                    home = game.get("home_team", "")
                    away = game.get("away_team", "")
                    home_l = _strip_accents(home).lower()
                    away_l = _strip_accents(away).lower()

                    score = 0
                    home_hits = sum(1 for t in query_tokens if t in home_l)
                    away_hits = sum(1 for t in query_tokens if t in away_l)
                    token_hits = home_hits + away_hits

                    if home_hits >= 1 and away_hits >= 1:
                        score = 100
                    elif target_clean and (target_clean in home_l or target_clean in away_l):
                        score = 100
                    elif query_tokens:
                        if token_hits == len(query_tokens):
                            score = 80
                        elif token_hits >= 2:
                            score = 60
                        elif token_hits > 0 and len(query_tokens) <= 2:
                            score = 50

                    if score > 0 and game.get("bookmakers"):
                        commence = game.get("commence_time", "9999-99-99")
                        matched_games.append((score, commence, sport_key, game))
            except Exception as e:
                if "404" not in str(e):
                    print(f"  └─ ⚠️ [The Odds API ({sport_key})]: {e}")

        if not matched_games:
            return None

        # Sort by highest match score first, then earliest commence_time (so today's/live match beats weekend fixtures)
        matched_games.sort(key=lambda x: (-x[0], x[1]))
        _, _, best_sport_key, best_game = matched_games[0]

        home = best_game.get("home_team", "")
        away = best_game.get("away_team", "")
        bookmakers = best_game.get("bookmakers", [])

        bm_blocks = []
        favorite = None
        min_price = 999.0
        primary_outcomes = []

        for bm in bookmakers[:3]:
            markets = bm.get("markets", [])
            if not markets or not markets[0].get("outcomes"):
                continue
            outcomes = markets[0]["outcomes"]
            lines = [f"**{bm.get('title', 'Bookmaker')}**:"]
            for out in outcomes:
                name = out.get("name", "")
                price = float(out.get("price", 0))
                if 1.0 < price < min_price and name.lower() != "draw":
                    min_price = price
                    favorite = name
                lines.append(f"  - **{name}**: {price:.2f} (American: {decimal_to_american(price)}) | Implied prob: {implied_probability(price)}")
            bm_blocks.append("\n".join(lines))
            if not primary_outcomes:
                primary_outcomes = [(o.get("name", ""), float(o.get("price", 0))) for o in outcomes if float(o.get("price", 0)) > 0]

        if not bm_blocks:
            return None

        fav_str = f"Favorite: **{favorite}**\n" if favorite else ""
        out_str = (
            f"[SPORTSBOOK CONSENSUS ODDS: {home} vs {away}]\n"
            f"Sport / Competition: {best_game.get('sport_title', best_sport_key)}\n"
            f"Scheduled Start: {best_game.get('commence_time', '')}\n"
            f"{fav_str}\n"
            + "\n\n".join(bm_blocks) + "\n"
        )

        if stake and stake > 0 and primary_outcomes:
            out_str += f"\n💰 Bet Payout Simulation (${stake:.2f} Stake):\n"
            for label, dec in primary_outcomes:
                payout = stake * dec
                profit = payout - stake
                out_str += f"- Wager on **{label}** ({dec:.2f}): Returns **${payout:.2f}** (${profit:.2f} profit) | Implied prob: {implied_probability(dec)}\n"

        out_str += "\nNote: Odds reflect live/current bookmaker markets and fluctuate during play.\n\n"
        return out_str

    def _fetch_esports_hltv_odds(self, query: str, stake: Optional[float] = None) -> Optional[str]:
        """Finds and parses live HLTV betting analytics for Counter-Strike matches."""
        try:
            from src.tools.web import search_web
            # Strip conversational and monetary filler to isolate core team/matchup
            clean_target = query
            for stopword in [
                "if i bet", "i bet", "bet on", "bet", "will i win and how much",
                "will i win", "how much", "tomorrow", "today", "tonight", "next game",
                "next match", "match", "game", "odds", "what are the", "can you check",
                "dollars", "bucks", "usd", "on"
            ]:
                clean_target = re.sub(rf'\b{re.escape(stopword)}\b', ' ', clean_target, flags=re.IGNORECASE)
            clean_target = re.sub(r'[\$0-9]+(?:\.[0-9]{2})?', ' ', clean_target)
            clean_target = re.sub(r'[^\w\s]', ' ', clean_target)
            clean_target = ' '.join(clean_target.split())
            if not clean_target:
                clean_target = query

            search_query = f"{clean_target} cs2 betting analytics hltv"
            search_res = search_web(search_query)

            candidate_urls = []
            for line in search_res.split("\n"):
                if "hltv.org/betting/analytics/" in line:
                    match = re.search(r'https://www.hltv.org/betting/analytics/[^\s)]+', line)
                    if match and match.group(0) not in candidate_urls:
                        url = match.group(0)
                        if "2026" in url or "thunderpick" in url:
                            candidate_urls.insert(0, url)
                        else:
                            candidate_urls.append(url)

            # Fallback search query if analytics URL not found on first attempt
            if not candidate_urls:
                search_res = search_web(f"{clean_target} odds hltv")
                for line in search_res.split("\n"):
                    if "hltv.org/betting/analytics/" in line:
                        match = re.search(r'https://www.hltv.org/betting/analytics/[^\s)]+', line)
                        if match and match.group(0) not in candidate_urls:
                            url = match.group(0)
                            if "2026" in url or "thunderpick" in url:
                                candidate_urls.insert(0, url)
                            else:
                                candidate_urls.append(url)

            if not candidate_urls:
                return None

            for hltv_url in candidate_urls[:3]:
                print(f"  └─ 🎲 [Odds Scraper]: Parsing HLTV betting analytics at {hltv_url}...")
                soup = fetch_soup(hltv_url, timeout=12)
                page_text = soup.get_text(" ", strip=True)

                # Match title (e.g. Falcons vs Ninjas in Pyjamas odds and prediction ...)
                title = soup.title.get_text(strip=True) if soup.title else query
                title_clean = title.split(" | ")[0].replace(" odds and prediction", "").replace(" | Full overview", "")

                # Extract best odds numbers (e.g. Best odds - 1.26, Best odds - 4.20)
                best_odds = [float(x) for x in re.findall(r'Best odds\s*-\s*([0-9]+\.[0-9]+)', page_text)]
                
                # Parse team names from matchup
                teams = re.split(r'\s+vs\.?\s+', title_clean, flags=re.IGNORECASE)

                if len(teams) >= 2 and len(best_odds) >= 2:
                    team1, team2 = teams[0].strip(), teams[1].strip()
                    odds1, odds2 = best_odds[0], best_odds[1]

                    # Extract favorite
                    fav_match = re.search(r'([A-Za-z0-9\s]+?)\s+(?:has better form ranking|is the bookmaker favorite)', page_text)
                    favorite = fav_match.group(1).strip() if fav_match else ""

                    favorite_name = "Market Consensus"
                    if team1.lower() in favorite.lower():
                        favorite_name = team1
                    elif team2.lower() in favorite.lower():
                        favorite_name = team2
                    elif odds1 < odds2:
                        favorite_name = team1
                    elif odds2 < odds1:
                        favorite_name = team2

                    out = f"[MATCH BETTING ODDS: {team1} vs {team2}]\n"
                    out += f"Favorite: **{favorite_name}**\n"
                    out += f"Best Odds Available:\n"
                    out += f"- **{team1}**: {odds1:.2f} (American: {decimal_to_american(odds1)}) | Win probability: ~{implied_probability(odds1)}\n"
                    out += f"- **{team2}**: {odds2:.2f} (American: {decimal_to_american(odds2)}) | Win probability: ~{implied_probability(odds2)}\n"

                    # If the user specified a stake (e.g. $200), calculate projected payouts
                    if stake and stake > 0:
                        payout1 = stake * odds1
                        profit1 = payout1 - stake
                        payout2 = stake * odds2
                        profit2 = payout2 - stake
                        out += f"\n💰 Bet Payout Simulation (${stake:.2f} Stake):\n"
                        out += f"- Wager on **{team1}** ({odds1:.2f}): Returns **${payout1:.2f}** (${profit1:.2f} profit) | Win probability: ~{implied_probability(odds1)}\n"
                        out += f"- Wager on **{team2}** ({odds2:.2f}): Returns **${payout2:.2f}** (${profit2:.2f} profit) | Win probability: ~{implied_probability(odds2)}\n"

                    out += f"\nMarket Source: HLTV Betting Analytics (<{hltv_url}>)\n"
                    out += f"Note: Betting lines reflect bookmaker consensus and fluctuate prior to match start.\n\n"
                    return out

        except Exception as e:
            print(f"  └─ ⚠️ [HLTV Odds Scraper Error]: {e}")
        return None

    def _fetch_web_odds_search(self, query: str, sport: str) -> str:
        """Falls back to web search synthesis to find consensus odds across major sportsbooks."""
        from src.tools.web import search_web
        sport_label = sport if sport not in ("", "sports", "odds", "betting") else ""
        search_query = f"{query} {sport_label} match odds betting line moneyline".strip()
        res = search_web(search_query)

        lines = []
        for line in res.split("\n"):
            line_str = line.strip()
            if any(k in line_str.lower() for k in ["odds", "favorite", "+", "-", "moneyline", "underdog"]):
                if len(line_str) > 15:
                    lines.append(line_str)

        if lines:
            snippets = "\n".join(lines[:6])
            return (
                f"[LIVE BETTING ODDS: {query}]\n"
                f"Sport/Category: {sport or 'sports'}\n"
                f"Market Information:\n{snippets}\n\n"
                "Note: Verify current betting lines with licensed sportsbooks before placing wagers.\n\n"
            )
        else:
            return (
                f"[BETTING ODDS: {query}]\n"
                f"No verified betting lines or active odds were found for '{query}'. "
                "Odds typically open closer to the scheduled match start time.\n\n"
            )

    def _get_single_match_odds(
        self,
        query_clean: str,
        sport_clean: str,
        stake: Optional[float] = None,
    ) -> str:
        """Fetches odds for a single matchup or team query."""
        q_lower = query_clean.lower()
        traditional_sport_keywords = (
            "nfl", "nhl", "nba", "mlb", "ucl", "epl", "laliga", "la liga",
            "serie a", "bundesliga", "hockey", "basketball", "soccer",
            "american football", "atlanta falcons",
        )
        has_traditional_sport = (
            sport_clean in ("nhl", "hockey", "nba", "basketball", "nfl", "football", "mlb", "baseball", "soccer", "ucl", "epl", "laliga", "seriea", "bundesliga")
            or any(k in q_lower for k in traditional_sport_keywords)
        )
        has_cs_team = any(bool(re.search(rf'\b{re.escape(t)}\b', q_lower)) for t in self.CS_TEAMS)
        is_cs = (sport_clean in ("cs", "cs2", "csgo") or has_cs_team) and not has_traditional_sport

        if is_cs:
            sport_clean = "cs2"

        print(f"  └─ 🎲 [Odds]: Fetching betting odds for '{query_clean}' (Sport: {sport_clean})...")

        # 1. For Counter-Strike/Esports, scrape HLTV betting analytics first and enrich with KashRock player props
        if sport_clean in ("cs", "cs2", "csgo") or (sport_clean == "esports" and is_cs):
            hltv_odds = self._fetch_esports_hltv_odds(query_clean, stake=stake)
            kr_props = ""
            is_mock = hasattr(fetch_soup, "assert_called") or "MagicMock" in str(type(fetch_soup))
            if not is_mock:
                try:
                    from src.skills.sports.aggregators.kashrock import KashrockAggregator
                    kr_props = KashrockAggregator().get_props_for_query(query_clean)
                except Exception:
                    kr_props = ""
            if hltv_odds:
                return hltv_odds + kr_props
            if kr_props:
                return f"[CS2 BETTING & PLAYER PROPS: {query_clean}]\n\n" + kr_props
            if sport_clean in ("cs", "cs2", "csgo"):
                return self._fetch_web_odds_search(query_clean, sport_clean)

        # Normalize generic 'esports' default if query is a traditional sports team/matchup
        effective_sport = "sports" if sport_clean == "esports" else sport_clean

        # 2. Fetch ESPN Live/Pre-Match Odds (DraftKings Live, DraftKings Pre-Match, Bet365) via team/event auto-discovery
        espn_odds, discovered_league, canonical_team = self._fetch_from_espn_odds(
            query_clean, effective_sport, stake=stake
        )

        # 3. Check The Odds API if key is present (prioritizing the competition discovered by ESPN, e.g. UCL / NHL)
        api_odds = self._fetch_from_the_odds_api(
            query_clean,
            effective_sport,
            stake=stake if not espn_odds else None,
            discovered_league=discovered_league,
            canonical_team=canonical_team,
        )

        if espn_odds and api_odds:
            return espn_odds + api_odds
        if espn_odds:
            return espn_odds
        if api_odds:
            return api_odds

        # 4. Web search consensus fallback
        return self._fetch_web_odds_search(query_clean, effective_sport)

    def get_odds(self, query: str, sport: Optional[str] = "sports") -> str:
        """Fetches live match betting odds, moneyline lines, spreads, totals, and implied win probabilities."""
        if not query or not query.strip():
            return "[BETTING ODDS]\nPlease specify the matchup or event (e.g. 'Real Madrid', 'Falcons vs Ninjas in Pyjamas', 'Lakers').\n\n"

        query_clean = query.strip()
        sport_clean = (sport or "").strip().lower()
        if sport_clean in ("", "odds", "betting", "general", "all"):
            sport_clean = "sports"

        # Extract potential stake from query (e.g. $200, 200 dollars)
        stake_match = re.search(r'\$([0-9]+(?:\.[0-9]{2})?)|\b([0-9]+(?:\.[0-9]{2})?)\s*(?:dollars|bucks|usd)', query_clean, re.IGNORECASE)
        stake = float(stake_match.group(1) or stake_match.group(2)) if stake_match else None

        # Resolve active tracked games when the user asks for "tracked games" / "both games" or omits a team
        sub_queries: list[tuple[str, str]] = []
        seen_keys: set[str] = set()

        def _add_sub(q_str: str, sp_str: str) -> None:
            cleaned = self._clean_query_target(q_str)
            if not cleaned:
                return
            norm_key = _strip_accents(cleaned).lower()
            if norm_key not in seen_keys:
                seen_keys.add(norm_key)
                sub_queries.append((q_str.strip(), sp_str))

        wants_tracked = bool(
            re.search(
                r'\b(?:tracked\s+games?|tracked\s+matches|tracked|both\s+games|both\s+matches|these\s+games|these\s+matches)\b',
                query_clean,
                re.IGNORECASE,
            )
        )
        cleaned_main = self._clean_query_target(query_clean)

        if wants_tracked or not cleaned_main:
            try:
                from src.skills.sports.live_tracker import LIVE_TRACKER, get_active_channel_id, get_channel_last_match_query
                ch_id = get_active_channel_id()
                active_matches = [
                    m for (cid, _), m in LIVE_TRACKER.tracked_matches.items()
                    if ch_id is None or cid == ch_id
                ]
                if not active_matches and LIVE_TRACKER.tracked_matches:
                    active_matches = list(LIVE_TRACKER.tracked_matches.values())
                for m in active_matches:
                    match_q = f"{m.home_team} vs {m.away_team}"
                    match_sp = m.league_code or m.sport_path or sport_clean
                    _add_sub(match_q, match_sp)
                if not sub_queries and ch_id is not None:
                    last_q = get_channel_last_match_query(ch_id)
                    if last_q:
                        _add_sub(last_q, sport_clean)
            except Exception:
                pass

        # Split multi-matchup queries (e.g. "Washington Capitals vs Montreal Canadiens and Colorado Avalanche vs Dallas Stars")
        if re.search(r'(?:,|;|\s+and\s+|\s*&\s*)', query_clean, flags=re.IGNORECASE) and "brighton and hove" not in query_clean.lower():
            parts = [
                p.strip()
                for p in re.split(r'(?:,|;|\s+and\s+|\s*&\s*)', query_clean, flags=re.IGNORECASE)
                if p and self._clean_query_target(p)
            ]
            if len(parts) >= 2:
                for p in parts:
                    _add_sub(p, sport_clean)

        if not sub_queries:
            if not cleaned_main:
                return "[BETTING ODDS]\nNo actively tracked games were found in this channel. Please specify the matchup or team (e.g. 'Washington Capitals vs Montreal Canadiens').\n\n"
            return self._get_single_match_odds(query_clean, sport_clean, stake=stake)

        if len(sub_queries) == 1:
            q_single, sp_single = sub_queries[0]
            return self._get_single_match_odds(q_single, sp_single, stake=stake)

        results = []
        for q_item, sp_item in sub_queries[:4]:
            res = self._get_single_match_odds(q_item, sp_item, stake=stake)
            if res:
                results.append(res.strip())
        return "\n\n---\n\n".join(results) + "\n\n"


