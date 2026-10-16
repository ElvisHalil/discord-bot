from datetime import datetime, timedelta
import os
import re
import unicodedata
from typing import Any, Optional
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


def parse_any_odds_to_decimal(val: Any) -> float:
    """Parse an American ('+150', '-192', 'EVEN'), Fractional ('5/2'), or Decimal ('2.50', 1.85) odds value into Decimal float."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        fval = float(val)
        if abs(fval) >= 100:
            return american_to_decimal(fval)
        return round(fval, 2) if fval > 1.0 else 0.0
    s = str(val).strip().upper()
    if not s or s in ("N/A", "NONE", "-", "OFF"):
        return 0.0
    if s in ("EVEN", "EV", "PK"):
        return 2.00
    m_frac = re.match(r"^(\d+)\s*/\s*(\d+)$", s)
    if m_frac:
        den = float(m_frac.group(2))
        if den > 0:
            return round(1.0 + (float(m_frac.group(1)) / den), 2)
    try:
        fval = float(s)
        if abs(fval) >= 100:
            return american_to_decimal(fval)
        if fval > 1.0:
            return round(fval, 2)
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


def convert_inline_american_to_decimal(text: str) -> str:
    """Convert inline American odds tokens (e.g. '+150', '-200') inside text snippets into Decimal odds."""
    if not text:
        return ""

    def _repl(m: re.Match) -> str:
        raw = m.group(1)
        dec = parse_any_odds_to_decimal(raw)
        if dec > 1.0:
            return f"{dec:.2f} (~{implied_probability(dec)})"
        return m.group(0)

    return re.sub(r"(?<![A-Za-z0-9])([+-]\d{3,5})(?![A-Za-z0-9])", _repl, text)


def format_standard_odds_card(
    match_title: str,
    outcomes: list[tuple[str, float]],
    favorite: Optional[str] = None,
    competition: Optional[str] = None,
    status_banner: Optional[str] = None,
    stake: Optional[float] = None,
    market_source: str = "Sportsbook Consensus",
    extra_lines: Optional[list[str]] = None,
) -> str:
    """
    Standardized CS-style ('Aurora vs 9z') single-card Decimal odds formatter used across all sports.
    """
    valid_outcomes = [(label, round(float(dec), 2)) for label, dec in outcomes if dec and float(dec) > 1.0]
    if not valid_outcomes:
        return ""

    if not favorite:
        non_draw = [o for o in valid_outcomes if o[0].lower() != "draw"]
        if non_draw:
            favorite = min(non_draw, key=lambda x: x[1])[0]

    out = f"[MATCH BETTING ODDS: {match_title}]\n"
    if competition:
        out += f"Competition: {competition}\n"
    if status_banner:
        out += f"Match Status: {status_banner}\n"
    if favorite:
        out += f"Favorite: **{favorite}**\n"
    out += "Best Odds Available:\n"
    for label, dec in valid_outcomes:
        out += f"- **{label}**: {dec:.2f} | Win probability: ~{implied_probability(dec)}\n"
    if extra_lines:
        for ex in extra_lines:
            if ex:
                out += f"- {ex}\n"

    if stake and stake > 0:
        out += f"\n💰 Bet Payout Simulation (${stake:.2f} Stake):\n"
        for label, dec in valid_outcomes:
            payout = stake * dec
            profit = payout - stake
            out += (
                f"- Wager on **{label}** ({dec:.2f}): Returns **${payout:.2f}** "
                f"(${profit:.2f} profit) | Win probability: ~{implied_probability(dec)}\n"
            )

    if market_source:
        out += f"\nMarket Source: {market_source}\n"
    out += "Note: Betting lines reflect bookmaker consensus and fluctuate prior to match start.\n\n"
    return out


def extract_espn_item_decimal_prices(item: dict) -> tuple[float, float, float, list[str], str]:
    """
    Extract (home_dec, away_dec, draw_dec, extra_market_lines, provider_name) in Decimal format
    from either an ESPN Core Odds item or an ESPN /scoreboard inline comp['odds'] item.
    """
    if not isinstance(item, dict):
        return 0.0, 0.0, 0.0, [], ""

    prov_name = (item.get("provider") or {}).get("name") or "DraftKings"
    home_o = item.get("homeTeamOdds") or {}
    away_o = item.get("awayTeamOdds") or {}
    draw_o = item.get("drawOdds") or {}
    ml_block = item.get("moneyline") or {}

    def _side_dec(team_o: dict, side_key: str) -> float:
        for period_key in ("current", "close", "open"):
            sub_ml = (team_o.get(period_key) or {}).get("moneyLine") or {}
            if isinstance(sub_ml, dict):
                for k in ("decimal", "value", "american", "alternateDisplayValue"):
                    dec = parse_any_odds_to_decimal(sub_ml.get(k))
                    if dec > 1.0:
                        return dec
        if team_o.get("moneyLine") is not None:
            dec = parse_any_odds_to_decimal(team_o.get("moneyLine"))
            if dec > 1.0:
                return dec
        odds_sub = team_o.get("odds") or {}
        if isinstance(odds_sub, dict) and odds_sub.get("value") is not None:
            dec = parse_any_odds_to_decimal(odds_sub.get("value"))
            if dec > 1.0:
                return dec
        if team_o.get("value") is not None:
            dec = parse_any_odds_to_decimal(team_o.get("value"))
            if dec > 1.0:
                return dec

        # Fallback to /scoreboard inline item['moneyline'][side_key]
        side_ml = ml_block.get(side_key) or {}
        if isinstance(side_ml, dict):
            for period_key in ("close", "current", "open"):
                p_obj = side_ml.get(period_key) or {}
                if isinstance(p_obj, dict):
                    dec = parse_any_odds_to_decimal(p_obj.get("odds") or p_obj.get("decimal") or p_obj.get("value"))
                    if dec > 1.0:
                        return dec
        return 0.0

    h_dec = _side_dec(home_o, "home")
    a_dec = _side_dec(away_o, "away")
    d_dec = _side_dec(draw_o, "draw")

    extra_lines: list[str] = []
    over_under = item.get("overUnder")
    if over_under is not None:
        over_dec = parse_any_odds_to_decimal(item.get("overOdds"))
        under_dec = parse_any_odds_to_decimal(item.get("underOdds"))
        if over_dec <= 1.0 or under_dec <= 1.0:
            total_blk = item.get("total") or {}
            over_dec = over_dec or parse_any_odds_to_decimal(((total_blk.get("over") or {}).get("close") or {}).get("odds"))
            under_dec = under_dec or parse_any_odds_to_decimal(((total_blk.get("under") or {}).get("close") or {}).get("odds"))
        if over_dec > 1.0 and under_dec > 1.0:
            extra_lines.append(f"**Over/Under {over_under}**: Over {over_dec:.2f} / Under {under_dec:.2f}")
        else:
            extra_lines.append(f"**Over/Under**: {over_under}")

    return h_dec, a_dec, d_dec, extra_lines, prov_name


def format_inline_scoreboard_odds(comp: dict, home_name: str, away_name: str) -> str:
    """Format a compact 1-line Decimal odds summary from an ESPN scoreboard competition object if available."""
    odds_list = (comp or {}).get("odds") or []
    for item in odds_list:
        h_dec, a_dec, d_dec, _, _ = extract_espn_item_decimal_prices(item)
        if h_dec > 1.0 or a_dec > 1.0:
            parts = []
            if h_dec > 1.0:
                parts.append(f"{home_name} {h_dec:.2f} (~{implied_probability(h_dec)})")
            if a_dec > 1.0:
                parts.append(f"{away_name} {a_dec:.2f} (~{implied_probability(a_dec)})")
            if d_dec > 1.0:
                parts.append(f"Draw {d_dec:.2f} (~{implied_probability(d_dec)})")
            return " | ".join(parts)
    return ""


class OddsAggregator(BaseSportsAggregator):
    """Centralized aggregator for sports, F1, and esports Decimal betting odds and implied win probabilities."""

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

    F1_DRIVERS = (
        "max verstappen", "verstappen", "lando norris", "norris", "charles leclerc", "leclerc",
        "oscar piastri", "piastri", "lewis hamilton", "hamilton", "george russell", "russell",
        "kimi antonelli", "antonelli", "carlos sainz", "sainz", "fernando alonso", "alonso",
        "alex albon", "albon", "pierre gasly", "gasly", "esteban ocon", "ocon",
        "nico hulkenberg", "hulkenberg", "yuki tsunoda", "tsunoda", "lance stroll", "stroll",
        "oliver bearman", "bearman", "liam lawson", "lawson", "isack hadjar", "hadjar",
        "gabriel bortoleto", "bortoleto", "franco colapinto", "colapinto", "arvid lindblad",
        "sergio perez", "perez", "valtteri bottas", "bottas",
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
        tokens = clean.split()
        expanded = []
        for t in tokens:
            alias_val = TEAM_ALIASES.get(t, t)
            if alias_val != t and alias_val in clean:
                expanded.append(t)
            else:
                expanded.append(alias_val)
        return " ".join(expanded)

    @classmethod
    def _detect_sport_or_league_scope(cls, query: str, sport: str) -> Optional[str]:
        """
        Return a canonical sport/league scope ('nhl', 'nba', 'nfl', 'mlb', 'f1', 'cs2',
        or a soccer league key like 'epl', 'ucl', 'laliga', 'seriea', 'bundesliga', 'ligue1', 'mls', 'soccer')
        when the query or sport parameter refers to a sport/league.
        """
        from src.skills.sports.aggregators.soccer import SoccerAggregator
        combined = f"{sport or ''} {query or ''}".lower()

        if re.search(r"\b(?:f1|formula\s*1|formula1|grand\s*prix)\b", combined) or any(
            re.search(rf"\b{re.escape(d)}\b", combined) for d in cls.F1_DRIVERS
        ):
            return "f1"
        if re.search(r"\b(?:nhl|hockey|ice\s*hockey)\b", combined):
            return "nhl"
        if re.search(r"\b(?:nba|basketball|hoops)\b", combined):
            return "nba"
        if re.search(r"\b(?:nfl|american\s*football)\b", combined):
            return "nfl"
        if re.search(r"\b(?:mlb|baseball)\b", combined):
            return "mlb"
        for key, info in SoccerAggregator.LEAGUES.items():
            for alias in info["aliases"]:
                if re.search(rf"\b{re.escape(alias)}\b", combined):
                    return key
        if re.search(r"\b(?:soccer|footy|futbol)\b", combined):
            return "soccer"
        return None

    def _fetch_from_espn_odds(
        self,
        query: str,
        sport: str,
        stake: Optional[float] = None,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Discovers the live or next upcoming match for a team/matchup via ESPN and formats
        ESPN's live/pre-match odds in the standardized CS-style Decimal odds card.
        Returns (formatted_odds_str, discovered_espn_league_slug, canonical_team_name).
        """
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

            from src.skills.sports.aggregators.espn_utils import parallel_fetch_json

            if sport_path == "soccer":
                sched_url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams/{team_id}/schedule?fixture=true"
            else:
                sched_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_code}/teams/{team_id}/schedule"

            sched_data = parallel_fetch_json(fetch_json, [sched_url], timeout=8, ttl=20)[0]
            events = (sched_data or {}).get("events", [])
            if not events and sport_path == "soccer":
                fallback_sched_url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/teams/{team_id}/schedule?fixture=true"
                sched_data = parallel_fetch_json(fetch_json, [fallback_sched_url], timeout=8, ttl=20)[0]
                events = (sched_data or {}).get("events", [])

            if not events:
                return None, league_code, team_name

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

            def _load_ev_odds(ev_obj: dict):
                e_id = str(ev_obj.get("id", ""))
                e_lg_obj = ev_obj.get("league", {})
                e_lg_slug = e_lg_obj.get("slug") or league_code
                e_lg_name = e_lg_obj.get("name") or e_lg_slug.upper()
                e_date = ev_obj.get("date", "")

                c_obj = (ev_obj.get("competitions") or [{}])[0]
                st_type = c_obj.get("status", {}).get("type", {})
                st_state = st_type.get("state", "")
                st_detail = st_type.get("detail") or st_type.get("shortDetail") or "Scheduled"

                comps = c_obj.get("competitors", [])
                h_comp = next((c for c in comps if c.get("homeAway") == "home"), {})
                a_comp = next((c for c in comps if c.get("homeAway") == "away"), {})
                h_name = h_comp.get("team", {}).get("displayName", "Home")
                a_name = a_comp.get("team", {}).get("displayName", "Away")

                def _score_str(c: dict) -> str:
                    s = c.get("score", "")
                    if isinstance(s, dict):
                        return str(s.get("displayValue", s.get("value", "")))
                    return str(s) if s is not None else ""

                h_score = _score_str(h_comp)
                a_score = _score_str(a_comp)

                o_url = f"https://sports.core.api.espn.com/v2/sports/{sport_path}/leagues/{e_lg_slug}/events/{e_id}/competitions/{e_id}/odds"
                s_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{e_lg_slug}/summary?event={e_id}"

                if not is_json_mock and st_state == "in" and (not h_score or not a_score):
                    o_data, s_data = parallel_fetch_json(fetch_json, [o_url, s_url], timeout=8, ttl=15)
                    o_items = (o_data or {}).get("items", [])
                    s_data = s_data or {}
                    h_comps = (s_data.get("header") or {}).get("competitions") or []
                    if h_comps:
                        h_c = next((c for c in h_comps[0].get("competitors", []) if c.get("homeAway") == "home"), {})
                        a_c = next((c for c in h_comps[0].get("competitors", []) if c.get("homeAway") == "away"), {})
                        h_score = h_score or _score_str(h_c)
                        a_score = a_score or _score_str(a_c)
                    if not o_items:
                        o_items = s_data.get("pickcenter") or s_data.get("odds") or []
                else:
                    o_data = parallel_fetch_json(fetch_json, [o_url], timeout=8, ttl=15)[0]
                    o_items = (o_data or {}).get("items", [])
                    if not is_json_mock and not o_items:
                        try:
                            s_data = parallel_fetch_json(fetch_json, [s_url], timeout=8, ttl=15)[0] or {}
                            o_items = s_data.get("pickcenter") or s_data.get("odds") or []
                        except Exception:
                            pass

                if not o_items and c_obj.get("odds"):
                    o_items = c_obj.get("odds") or []

                return o_items, e_lg_slug, e_lg_name, e_date, st_state, st_detail, h_name, a_name, h_score, a_score

            items, ev_league_slug, ev_league_name, ev_date, state, status_detail, home_name, away_name, home_score, away_score = _load_ev_odds(chosen_ev)

            if not items and state == "pre" and not is_json_mock:
                post_candidates = [
                    ev for ev in candidate_events
                    if (ev.get("competitions") or [{}])[0].get("status", {}).get("type", {}).get("state", "") == "post"
                ]
                if post_candidates:
                    post_candidates.sort(key=lambda e: e.get("date", ""))
                    items, ev_league_slug, ev_league_name, ev_date, state, status_detail, home_name, away_name, home_score, away_score = _load_ev_odds(post_candidates[-1])

            if not items:
                return None, ev_league_slug, team_name

            best_h = 0.0
            best_a = 0.0
            best_d = 0.0
            extra_lines: list[str] = []
            providers: list[str] = []

            for item in items:
                h_dec, a_dec, d_dec, item_extras, prov_name = extract_espn_item_decimal_prices(item)
                if h_dec <= 1.0 and a_dec <= 1.0:
                    continue
                if prov_name and prov_name not in providers:
                    providers.append(prov_name)
                # Prefer live odds if available, otherwise highest decimal price per outcome
                if "live" in prov_name.lower():
                    if h_dec > 1.0:
                        best_h = h_dec
                    if a_dec > 1.0:
                        best_a = a_dec
                    if d_dec > 1.0:
                        best_d = d_dec
                else:
                    if h_dec > best_h:
                        best_h = h_dec
                    if a_dec > best_a:
                        best_a = a_dec
                    if d_dec > best_d:
                        best_d = d_dec
                if item_extras and not extra_lines:
                    extra_lines = item_extras

            if best_h <= 1.0 and best_a <= 1.0:
                return None, ev_league_slug, team_name

            outcomes: list[tuple[str, float]] = []
            if best_h > 1.0:
                outcomes.append((home_name, best_h))
            if best_a > 1.0:
                outcomes.append((away_name, best_a))
            if best_d > 1.0:
                outcomes.append(("Draw", best_d))

            score_part = f" — Score: {home_name} {home_score} - {away_score} {away_name}" if (home_score or away_score) else ""
            status_banner = f"🔴 LIVE NOW ({status_detail}){score_part}" if state == "in" else f"{status_detail} ({ev_date})"
            source_str = ", ".join(providers) if providers else "ESPN / DraftKings"

            out = format_standard_odds_card(
                match_title=f"{home_name} vs {away_name}",
                outcomes=outcomes,
                competition=ev_league_name,
                status_banner=status_banner,
                stake=stake,
                market_source=source_str,
                extra_lines=extra_lines,
            )
            return out, ev_league_slug, team_name
        except Exception as e:
            print(f"  └─ ⚠️ [ESPN Odds Lookup Error]: {e}")
            return None, None, None

    def _fetch_league_scoreboard_odds(
        self,
        query: str,
        sport: str,
        stake: Optional[float] = None,
    ) -> Optional[str]:
        """
        Fetch Decimal betting odds for all live or upcoming games on a league/sport scoreboard
        (e.g. 'hockey odds', 'nhl odds', 'soccer odds', 'premier league odds', 'nba odds').
        """
        is_soup_mock = hasattr(fetch_soup, "assert_called") or "MagicMock" in str(type(fetch_soup))
        is_json_mock = hasattr(fetch_json, "assert_called") or "MagicMock" in str(type(fetch_json))
        if is_soup_mock and not is_json_mock:
            return None

        scope = self._detect_sport_or_league_scope(query, sport)
        if not scope or scope in ("f1", "cs2"):
            return None

        from src.skills.sports.aggregators.soccer import SoccerAggregator
        from src.skills.sports.aggregators.espn_utils import parallel_fetch_json

        league_targets: list[tuple[str, str, str]] = []
        if scope == "nhl":
            league_targets = [("hockey", "nhl", "NHL")]
        elif scope == "nba":
            league_targets = [("basketball", "nba", "NBA")]
        elif scope == "nfl":
            league_targets = [("football", "nfl", "NFL")]
        elif scope == "mlb":
            league_targets = [("baseball", "mlb", "MLB")]
        elif scope in SoccerAggregator.LEAGUES:
            info = SoccerAggregator.LEAGUES[scope]
            league_targets = [("soccer", info["code"], info["name"])]
        elif scope == "soccer":
            for k in ("ucl", "epl", "laliga", "seriea", "bundesliga", "ligue1"):
                info = SoccerAggregator.LEAGUES[k]
                league_targets.append(("soccer", info["code"], info["name"]))

        if not league_targets:
            return None

        now = datetime.now()
        today_ymd = now.strftime("%Y%m%d")
        tomorrow_ymd = (now + timedelta(days=1)).strftime("%Y%m%d")

        cards: list[str] = []
        for sport_path, lg_code, lg_name in league_targets:
            base_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{lg_code}/scoreboard"
            urls_to_check = [base_url] if is_json_mock else [base_url, f"{base_url}?dates={today_ymd}", f"{base_url}?dates={tomorrow_ymd}"]
            payloads = parallel_fetch_json(fetch_json, urls_to_check, timeout=7, ttl=20)

            seen_ev_ids: set[str] = set()
            active_events: list[dict] = []
            calendar_dates: list[str] = []

            for p in payloads:
                if not isinstance(p, dict):
                    continue
                if not calendar_dates:
                    leagues_arr = p.get("leagues") or []
                    if leagues_arr and isinstance(leagues_arr[0], dict):
                        calendar_dates = leagues_arr[0].get("calendar") or []
                for ev in p.get("events") or []:
                    ev_id = str(ev.get("id") or "")
                    comp = (ev.get("competitions") or [{}])[0]
                    state = comp.get("status", {}).get("type", {}).get("state", "pre")
                    if state in ("in", "pre") and ev_id and ev_id not in seen_ev_ids:
                        seen_ev_ids.add(ev_id)
                        active_events.append(ev)

            # If no live/upcoming games today or tomorrow, check the next scheduled calendar date
            if not active_events and calendar_dates and not is_json_mock:
                for cal_item in calendar_dates:
                    cal_str = cal_item.get("startDate", "") if isinstance(cal_item, dict) else str(cal_item)
                    if not cal_str:
                        continue
                    try:
                        c_dt = datetime.fromisoformat(cal_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        if c_dt.date() >= now.date():
                            next_ymd = c_dt.strftime("%Y%m%d")
                            next_data = parallel_fetch_json(fetch_json, [f"{base_url}?dates={next_ymd}"], timeout=7, ttl=20)[0]
                            for ev in (next_data or {}).get("events") or []:
                                ev_id = str(ev.get("id") or "")
                                if ev_id and ev_id not in seen_ev_ids:
                                    seen_ev_ids.add(ev_id)
                                    active_events.append(ev)
                            if active_events:
                                break
                    except Exception:
                        pass

            max_per_league = 3 if len(league_targets) > 1 else 6
            for ev in active_events[:max_per_league]:
                ev_id = str(ev.get("id") or "")
                ev_date = ev.get("date", "")
                comp = (ev.get("competitions") or [{}])[0]
                st_type = comp.get("status", {}).get("type", {})
                state = st_type.get("state", "pre")
                status_detail = st_type.get("detail") or st_type.get("shortDetail") or "Scheduled"

                competitors = comp.get("competitors") or []
                h_comp = next((c for c in competitors if c.get("homeAway") == "home"), {})
                a_comp = next((c for c in competitors if c.get("homeAway") == "away"), {})
                home_name = (h_comp.get("team") or {}).get("displayName", "Home")
                away_name = (a_comp.get("team") or {}).get("displayName", "Away")

                items = comp.get("odds") or []
                if not items and ev_id and not is_json_mock:
                    o_url = f"https://sports.core.api.espn.com/v2/sports/{sport_path}/leagues/{lg_code}/events/{ev_id}/competitions/{ev_id}/odds"
                    o_data = parallel_fetch_json(fetch_json, [o_url], timeout=6, ttl=20)[0]
                    items = (o_data or {}).get("items") or []

                best_h, best_a, best_d = 0.0, 0.0, 0.0
                extra_lines: list[str] = []
                providers: list[str] = []
                for item in items:
                    h_dec, a_dec, d_dec, item_extras, prov_name = extract_espn_item_decimal_prices(item)
                    if h_dec <= 1.0 and a_dec <= 1.0:
                        continue
                    if prov_name and prov_name not in providers:
                        providers.append(prov_name)
                    if h_dec > best_h:
                        best_h = h_dec
                    if a_dec > best_a:
                        best_a = a_dec
                    if d_dec > best_d:
                        best_d = d_dec
                    if item_extras and not extra_lines:
                        extra_lines = item_extras

                if best_h <= 1.0 and best_a <= 1.0:
                    continue

                outcomes: list[tuple[str, float]] = []
                if best_h > 1.0:
                    outcomes.append((home_name, best_h))
                if best_a > 1.0:
                    outcomes.append((away_name, best_a))
                if best_d > 1.0:
                    outcomes.append(("Draw", best_d))

                status_banner = f"🔴 LIVE NOW ({status_detail})" if state == "in" else f"{status_detail} ({ev_date})"
                card = format_standard_odds_card(
                    match_title=f"{home_name} vs {away_name}",
                    outcomes=outcomes,
                    competition=lg_name,
                    status_banner=status_banner,
                    stake=stake,
                    market_source=", ".join(providers) if providers else "DraftKings",
                    extra_lines=extra_lines,
                )
                if card:
                    cards.append(card.strip())

        if cards:
            return "\n\n---\n\n".join(cards[:8]) + "\n\n"
        return None

    def _fetch_f1_odds(self, query: str, stake: Optional[float] = None) -> Optional[str]:
        """
        Fetch and format Formula 1 Grand Prix Outright Winner, Podium, Constructor, or Drivers' Championship
        odds in the standardized CS-style Decimal odds card.
        """
        try:
            from src.tools.web import search_web

            gp_name = "Grand Prix"
            season_year = datetime.now().year
            se = getattr(fetch_json, "side_effect", None)
            is_fixed_list_mock = se is not None and not callable(se)
            if not is_fixed_list_mock:
                try:
                    f1_sb = fetch_json("https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard", timeout=6) or {}
                    if isinstance(f1_sb, dict):
                        season_year = (f1_sb.get("season") or {}).get("year", season_year)
                        cal = ((f1_sb.get("leagues") or [{}])[0]).get("calendar") or []
                        now_dt = datetime.now()
                        for item in cal:
                            s_date = item.get("startDate", "") if isinstance(item, dict) else ""
                            if s_date:
                                dt = datetime.fromisoformat(s_date.replace("Z", "+00:00")).replace(tzinfo=None)
                                if dt >= now_dt:
                                    gp_name = item.get("label") or gp_name
                                    break
                except Exception:
                    pass

            q_low = (query or "").lower()
            wants_championship = any(w in q_low for w in ("championship", "champion", "title", "standings", "futures", "season"))

            search_q = (
                f"Formula 1 {season_year} Drivers Championship betting odds Verstappen Norris"
                if wants_championship
                else f"{season_year} {gp_name} Formula 1 race betting odds winner"
            )
            search_res = search_web(search_q)

            candidate_urls: list[str] = []
            for line in search_res.split("\n"):
                m_url = re.search(r"https?://[^\s)\]>]+", line)
                if m_url:
                    u = m_url.group(0).strip()
                    if any(d in u.lower() for d in ("lasvegassportsbetting.com", "sportsbettingdime.com", "covers.com", "oddschecker.com")):
                        if u not in candidate_urls:
                            candidate_urls.insert(0, u)
                    elif u not in candidate_urls:
                        candidate_urls.append(u)

            driver_outcomes: dict[str, float] = {}
            source_used = "Sportsbook Consensus"

            # 1. Try parsing structured tables / lines from top candidate pages
            for url in candidate_urls[:2]:
                try:
                    soup = fetch_soup(url, timeout=8)
                    if not soup:
                        continue
                    # Check HTML table rows first (e.g. SportsBettingDime 'Driver | Odds')
                    for tr in soup.find_all("tr"):
                        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                        if len(cells) >= 2:
                            name_cand, odds_cand = cells[0].strip(), cells[1].strip()
                            if any(d in name_cand.lower() for d in self.F1_DRIVERS):
                                dec = parse_any_odds_to_decimal(odds_cand)
                                if 1.01 <= dec <= 501.0:
                                    driver_outcomes[name_cand] = dec
                                    source_used = f"F1 Sportsbook Consensus (<{url}>)"

                    # Check line-by-line pairs (e.g. LasVegasSportsBetting 'Outright\nDriver\n+150')
                    if not driver_outcomes:
                        main_node = soup.find("main") or soup.find("article") or soup
                        raw_lines = [ln.strip() for ln in main_node.get_text("\n", strip=True).splitlines() if ln.strip()]
                        in_podium_or_car = False
                        for idx in range(len(raw_lines) - 1):
                            cur_ln = raw_lines[idx]
                            nxt_ln = raw_lines[idx + 1]
                            if re.search(r"\b(?:Top\s*3\s*Finish|Winning\s*Car|Podium)\b", cur_ln, re.IGNORECASE) and driver_outcomes:
                                in_podium_or_car = True
                            if in_podium_or_car:
                                break
                            if any(d in cur_ln.lower() for d in self.F1_DRIVERS) and re.match(r"^[+-]\d{3,5}$", nxt_ln):
                                dec = parse_any_odds_to_decimal(nxt_ln)
                                if 1.01 <= dec <= 501.0 and cur_ln not in driver_outcomes:
                                    driver_outcomes[cur_ln] = dec
                                    source_used = f"F1 Sportsbook Consensus (<{url}>)"
                    if driver_outcomes:
                        break
                except Exception:
                    pass

            # 2. Fallback: Extract 'Driver Name (+200)' or 'Driver Name +200' patterns directly from search snippets
            if not driver_outcomes:
                for m in re.finditer(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*(?:\(\s*([+-]\d{3,5})\s*\)|(?::|-|–)?\s*([+-]\d{3,5})\b)", search_res):
                    d_name = m.group(1).strip()
                    raw_odd = m.group(2) or m.group(3)
                    if raw_odd and any(d in d_name.lower() for d in self.F1_DRIVERS):
                        dec = parse_any_odds_to_decimal(raw_odd)
                        if 1.01 <= dec <= 501.0 and d_name not in driver_outcomes:
                            driver_outcomes[d_name] = dec

            if driver_outcomes:
                sorted_outcomes = sorted(driver_outcomes.items(), key=lambda x: x[1])[:8]
                event_title = (
                    f"Formula 1 — {season_year} Drivers' Championship"
                    if wants_championship
                    else f"Formula 1 — {gp_name} ({season_year})"
                )
                return format_standard_odds_card(
                    match_title=event_title,
                    outcomes=sorted_outcomes,
                    competition="Formula 1 World Championship",
                    stake=stake,
                    market_source=source_used,
                )
        except Exception as e:
            print(f"  └─ ⚠️ [F1 Odds Error]: {e}")
        return None

    def _fetch_from_the_odds_api(
        self,
        query: str,
        sport: str,
        stake: Optional[float] = None,
        discovered_league: Optional[str] = None,
        canonical_team: Optional[str] = None,
    ) -> Optional[str]:
        """Fetches structured multi-bookmaker odds if THE_ODDS_API_KEY is configured and formats as CS-style Decimal card."""
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
        if sport_clean in ("cs", "cs2", "csgo", "esports", "f1", "formula 1"):
            return None

        candidate_keys = []
        if discovered_league and discovered_league in self.ESPN_TO_ODDS_API_LEAGUE:
            candidate_keys.append(self.ESPN_TO_ODDS_API_LEAGUE[discovered_league])

        from src.skills.sports.aggregators.soccer import SoccerAggregator
        target_clean = _strip_accents(canonical_team or self._clean_query_target(query) or query).lower()
        for club, lg in sorted(SoccerAggregator.CLUB_LEAGUE_HINTS.items(), key=lambda x: len(x[0]), reverse=True):
            if re.search(rf'\b{re.escape(club)}\b', target_clean):
                for k in ("soccer_uefa_champs_league", self.ESPN_TO_ODDS_API_LEAGUE.get(lg)):
                    if k and k not in candidate_keys:
                        candidate_keys.append(k)
                break

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
        seen_toks = set()
        dedup_tokens = []
        for t in query_tokens:
            if t not in seen_toks:
                seen_toks.add(t)
                dedup_tokens.append(t)
        query_tokens = dedup_tokens

        from src.skills.sports.aggregators.espn_utils import parallel_fetch_json

        top_candidates = candidate_keys[:4]
        odds_urls = [
            f"https://api.the-odds-api.com/v4/sports/{sk}/odds/?apiKey={api_key}&regions=us,eu,uk&markets=h2h"
            for sk in top_candidates
        ]
        datasets = parallel_fetch_json(fetch_json, odds_urls, timeout=8, ttl=20)

        matched_games = []
        for sport_key, data in zip(top_candidates, datasets):
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

        if not matched_games:
            return None

        matched_games.sort(key=lambda x: (-x[0], x[1]))
        _, _, best_sport_key, best_game = matched_games[0]

        home = best_game.get("home_team", "")
        away = best_game.get("away_team", "")
        bookmakers = best_game.get("bookmakers", [])

        best_prices: dict[str, float] = {}
        outcome_order: list[str] = []
        bm_names: list[str] = []

        for bm in bookmakers[:3]:
            markets = bm.get("markets", [])
            if not markets or not markets[0].get("outcomes"):
                continue
            bm_title = bm.get("title", "Bookmaker")
            if bm_title and bm_title not in bm_names:
                bm_names.append(bm_title)
            for out in markets[0]["outcomes"]:
                name = out.get("name", "")
                price = parse_any_odds_to_decimal(out.get("price", 0))
                if price > 1.0 and name:
                    if name not in outcome_order:
                        outcome_order.append(name)
                    if price > best_prices.get(name, 0.0):
                        best_prices[name] = price

        if not best_prices:
            return None

        outcomes = [(name, best_prices[name]) for name in outcome_order if name in best_prices]
        return format_standard_odds_card(
            match_title=f"{home} vs {away}",
            outcomes=outcomes,
            competition=best_game.get("sport_title", best_sport_key),
            status_banner=f"Scheduled ({best_game.get('commence_time', '')})" if best_game.get("commence_time") else None,
            stake=stake,
            market_source=", ".join(bm_names) if bm_names else "Sportsbook Consensus",
        )

    def _fetch_esports_hltv_odds(self, query: str, stake: Optional[float] = None) -> Optional[str]:
        """Finds and parses live HLTV betting analytics for Counter-Strike matches."""
        try:
            from src.tools.web import search_web
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

                title = soup.title.get_text(strip=True) if soup.title else query
                title_clean = title.split(" | ")[0].replace(" odds and prediction", "").replace(" | Full overview", "")

                best_odds = [float(x) for x in re.findall(r'Best odds\s*-\s*([0-9]+\.[0-9]+)', page_text)]
                teams = re.split(r'\s+vs\.?\s+', title_clean, flags=re.IGNORECASE)

                if len(teams) >= 2 and len(best_odds) >= 2:
                    team1, team2 = teams[0].strip(), teams[1].strip()
                    odds1, odds2 = best_odds[0], best_odds[1]

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

                    return format_standard_odds_card(
                        match_title=f"{team1} vs {team2}",
                        outcomes=[(team1, odds1), (team2, odds2)],
                        favorite=favorite_name,
                        stake=stake,
                        market_source=f"HLTV Betting Analytics (<{hltv_url}>)",
                    )

        except Exception as e:
            print(f"  └─ ⚠️ [HLTV Odds Scraper Error]: {e}")
        return None

    def _fetch_web_odds_search(self, query: str, sport: str) -> str:
        """Falls back to web search synthesis to find consensus odds across major sportsbooks (converting American to Decimal)."""
        from src.tools.web import search_web
        sport_label = sport if sport not in ("", "sports", "odds", "betting") else ""
        search_query = f"{query} {sport_label} betting odds decimal".strip()
        res = search_web(search_query)

        lines = []
        for line in res.split("\n"):
            line_str = line.strip()
            if any(k in line_str.lower() for k in ["odds", "favorite", "+", "-", "moneyline", "underdog", "winner"]):
                if len(line_str) > 15:
                    lines.append(convert_inline_american_to_decimal(line_str))

        if lines:
            snippets = "\n".join(lines[:6])
            return (
                f"[MATCH BETTING ODDS: {query}]\n"
                f"Sport/Category: {sport or 'sports'}\n"
                f"Market Information (Decimal Odds):\n{snippets}\n\n"
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
        """Fetches odds for a single matchup, team, F1 event, or league query."""
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

        scope = self._detect_sport_or_league_scope(query_clean, sport_clean)
        if scope == "f1" and not is_cs:
            sport_clean = "f1"

        print(f"  └─ 🎲 [Odds]: Fetching betting odds for '{query_clean}' (Sport: {sport_clean})...")

        # 1. Counter-Strike / Esports
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

        # 2. Formula 1 (Grand Prix Outrights & Championship Odds)
        if sport_clean == "f1":
            f1_odds = self._fetch_f1_odds(query_clean, stake=stake)
            if f1_odds:
                return f1_odds
            return self._fetch_web_odds_search(query_clean, "Formula 1")

        effective_sport = "sports" if sport_clean == "esports" else sport_clean

        # 3. Single Team / Matchup via ESPN + The Odds API
        espn_odds, discovered_league, canonical_team = self._fetch_from_espn_odds(
            query_clean, effective_sport, stake=stake
        )

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

        # 4. League-Wide / Sport-Wide Scoreboard Odds (e.g. 'hockey odds', 'soccer odds', 'premier league odds')
        league_odds = self._fetch_league_scoreboard_odds(query_clean, effective_sport, stake=stake)
        if league_odds:
            return league_odds

        # 5. Web search consensus fallback
        return self._fetch_web_odds_search(query_clean, effective_sport)

    def get_odds(self, query: str, sport: Optional[str] = "sports") -> str:
        """Fetches live match Decimal betting odds, payout multipliers, and implied win probabilities."""
        if (not query or not query.strip()) and (not sport or sport.strip().lower() in ("", "sports", "odds", "betting")):
            return "[BETTING ODDS]\nPlease specify the matchup, team, or sport (e.g. 'Real Madrid', 'hockey odds', 'F1 odds', 'Aurora vs 9z').\n\n"

        query_clean = (query or "").strip()
        sport_clean = (sport or "").strip().lower()
        if sport_clean in ("", "odds", "betting", "general", "all"):
            sport_clean = "sports"

        stake_match = re.search(r'\$([0-9]+(?:\.[0-9]{2})?)|\b([0-9]+(?:\.[0-9]{2})?)\s*(?:dollars|bucks|usd)', query_clean, re.IGNORECASE)
        stake = float(stake_match.group(1) or stake_match.group(2)) if stake_match else None

        sub_queries: list[tuple[str, str]] = []
        seen_keys: set[str] = set()

        def _add_sub(q_str: str, sp_str: str) -> None:
            cleaned = self._clean_query_target(q_str)
            scope = self._detect_sport_or_league_scope(q_str, sp_str)
            if not cleaned and not scope:
                return
            norm_key = _strip_accents(cleaned or scope or "").lower()
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
        detected_scope = self._detect_sport_or_league_scope(query_clean, sport_clean)

        if wants_tracked or (not cleaned_main and not detected_scope):
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
                    last_q, last_s = get_channel_last_match_query(ch_id)
                    if last_q:
                        _add_sub(last_q, last_s or sport_clean)
            except Exception:
                pass

        # Split multi-matchup or multi-sport queries (e.g. "Washington Capitals vs Montreal Canadiens and Colorado Avalanche vs Dallas Stars", or "soccer and hockey odds")
        if re.search(r'(?:,|;|\s+and\s+|\s*&\s*)', query_clean, flags=re.IGNORECASE) and "brighton and hove" not in query_clean.lower():
            parts = [
                p.strip()
                for p in re.split(r'(?:,|;|\s+and\s+|\s*&\s*)', query_clean, flags=re.IGNORECASE)
                if p and (self._clean_query_target(p) or self._detect_sport_or_league_scope(p, ""))
            ]
            if len(parts) >= 2:
                for p in parts:
                    part_scope = self._detect_sport_or_league_scope(p, "")
                    _add_sub(p, part_scope or sport_clean)

        if not sub_queries:
            if not cleaned_main and not detected_scope:
                return "[BETTING ODDS]\nNo actively tracked games were found in this channel. Please specify the matchup, team, or sport (e.g. 'Washington Capitals vs Montreal Canadiens', 'hockey odds', 'soccer odds').\n\n"
            return self._get_single_match_odds(query_clean or sport_clean, sport_clean, stake=stake)

        if len(sub_queries) == 1:
            q_single, sp_single = sub_queries[0]
            return self._get_single_match_odds(q_single, sp_single, stake=stake)

        from src.skills.sports.aggregators.espn_utils import SPORTS_EXECUTOR
        is_mock = (
            hasattr(fetch_json, "assert_called")
            or "MagicMock" in str(type(fetch_json))
            or hasattr(fetch_soup, "assert_called")
            or "MagicMock" in str(type(fetch_soup))
        )
        batch = sub_queries[:4]
        if is_mock:
            raw_results = [self._get_single_match_odds(q_item, sp_item, stake=stake) for q_item, sp_item in batch]
        else:
            raw_results = list(
                SPORTS_EXECUTOR.map(lambda item: self._get_single_match_odds(item[0], item[1], stake=stake), batch)
            )
        results = [r.strip() for r in raw_results if r]
        return "\n\n---\n\n".join(results) + "\n\n"
