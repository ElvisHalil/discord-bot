from datetime import datetime, timezone
import os
import re
import time
from typing import Optional
import requests
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.models import CsLiveSeries, CsMapResult
from src.utils.http import fetch_json

_KASHROCK_CACHE: dict[str, tuple[float, object]] = {}
_KASHROCK_CACHE_TTL = 300  # 5 minutes
_KASHROCK_SESSION: Optional[requests.Session] = None

BO3_MATCHES_URL = (
    "https://api.bo3.gg/api/v1/matches"
    "?page[offset]=0&page[limit]={limit}&sort=start_date"
    "&filter[matches.status][in]={status_filter}"
    "&filter[matches.discipline_id][eq]=1"
    "&with=teams,tournament,games,streams,stage"
)


def clear_kashrock_cache() -> None:
    """Clear the in-memory KashRock and BO3.gg API response cache."""
    _KASHROCK_CACHE.clear()


def _get_session() -> requests.Session:
    global _KASHROCK_SESSION
    if _KASHROCK_SESSION is None:
        _KASHROCK_SESSION = requests.Session()
    return _KASHROCK_SESSION


def _clean_map_name(raw_map: Optional[str]) -> str:
    """Normalize a CS2 map code like 'de_dust2' or 'de_ancient' into 'Dust2' / 'Ancient'."""
    name = str(raw_map or "").strip()
    if not name:
        return "TBD"
    if name.lower().startswith("de_"):
        name = name[3:]
    if name.lower() == "dust2":
        return "Dust2"
    return name.capitalize()


def _format_iso_start_time(iso_str: str) -> str:
    """Format an ISO-8601 UTC start timestamp into both UTC and local user time."""
    if not iso_str:
        return "Time TBD"
    try:
        from src.utils.timezones import get_local_now, _short_tz_abbr
        cleaned = str(iso_str).replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(cleaned)
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        else:
            dt_utc = dt_utc.astimezone(timezone.utc)
        local_dt = get_local_now(now=dt_utc)
        local_abbr = _short_tz_abbr(local_dt)
        utc_part = dt_utc.strftime("%b %d, %H:%M UTC")
        local_part = f"{local_dt.strftime('%I:%M %p').lstrip('0')} {local_abbr}"
        return f"{utc_part} ({local_part})"
    except Exception:
        return str(iso_str)


def _team_match_key(name: str) -> str:
    """Normalize team name for cross-provider matching (e.g. 'BetBoom Team' -> 'betboom')."""
    low = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    tokens = [t for t in low.split() if t not in ("team", "esports", "gaming", "clan", "club")]
    return " ".join(tokens) if tokens else low


class KashrockAggregator(BaseSportsAggregator):
    """
    Client for the KashRock v6 Esports API and BO3.gg public JSON API (CS2 pro player stats,
    map gamelogs, live player props across 25+ books/DFS, BO1/BO3/BO5 live series scoreboards,
    and upcoming CS2 match schedules).
    """

    BASE_URL = "https://kashrock.up.railway.app/v6/esports/cs2"

    def _api_key(self) -> Optional[str]:
        return (os.getenv("KASHROCK_API_KEY") or os.getenv("KASHROCK_KEY") or "").strip() or None

    def is_configured(self) -> bool:
        return bool(self._api_key())

    def _get_json(self, path: str, timeout: int = 12, ttl: int = _KASHROCK_CACHE_TTL) -> Optional[dict]:
        api_key = self._api_key()
        if not api_key:
            return None

        url = f"{self.BASE_URL}{path}" if path.startswith("/") else f"{self.BASE_URL}/{path}"
        now = time.time()
        if url in _KASHROCK_CACHE:
            ts, cached = _KASHROCK_CACHE[url]
            if now - ts < ttl:
                return cached  # type: ignore[return-value]

        try:
            resp = _get_session().get(
                url,
                headers={"X-API-Key": api_key, "Accept": "application/json"},
                timeout=timeout,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if isinstance(data, dict):
                _KASHROCK_CACHE[url] = (now, data)
                return data
        except Exception:
            pass
        return None

    def fetch_bo3_matches(
        self,
        status_filter: str = "current,upcoming",
        limit: int = 20,
        ttl: int = 30,
    ) -> list[dict]:
        """Fetch live and upcoming CS2 series from the public keyless BO3.gg JSON API."""
        url = BO3_MATCHES_URL.format(limit=limit, status_filter=status_filter)
        now = time.time()
        if url in _KASHROCK_CACHE:
            ts, cached = _KASHROCK_CACHE[url]
            if now - ts < ttl and isinstance(cached, list):
                return cached  # type: ignore[return-value]

        try:
            data = fetch_json(url, timeout=8)
            results = (data or {}).get("results") if isinstance(data, dict) else None
            if isinstance(results, list):
                _KASHROCK_CACHE[url] = (now, results)
                return results
        except Exception:
            pass
        return []

    def get_player_pro_stats(self, player_slug: str) -> dict:
        """
        Fetches official pro CS2 stats (K/D, ADR, KPR, DPR, HS%, Opening Kills, Clutches)
        and recent 30-day map gamelogs for a player slug (e.g. 'm0nesy', 'niko', 'kyousuke').
        """
        slug = (player_slug or "").strip().lower()
        if not slug or not self.is_configured():
            return {}

        result: dict = {}

        # 1. Career / Pro aggregate stats (/players/{slug}/stats)
        stats_resp = self._get_json(f"/players/{slug}/stats", timeout=10, ttl=600)
        if stats_resp and isinstance(stats_resp.get("data"), dict):
            d = stats_resp["data"]
            games_count = d.get("games_count")
            if games_count and int(games_count) > 0:
                clutches_total = sum(
                    int(d.get(f"clutches_vs_{i}", 0) or 0) for i in range(1, 6)
                )
                multikills_3plus = sum(
                    int(d.get(f"multikills_vs_{i}", 0) or 0) for i in range(3, 6)
                )
                result["career_pro"] = {
                    "maps": int(games_count),
                    "rounds": int(d.get("rounds_count") or 0),
                    "round_winrate_pct": round(float(d.get("rounds_winrate") or 0) * 100, 1),
                    "kd": round(float(d.get("avg_kd_rate") or 0), 2),
                    "kd_diff": int(d.get("kd_diff_sum") or 0),
                    "adr": round(float(d.get("avg_damage") or 0), 1),
                    "kpr": round(float(d.get("avg_kills") or 0), 2),
                    "dpr": round(float(d.get("avg_deaths") or 0), 2),
                    "apr": round(float(d.get("avg_assists") or 0), 2),
                    "hs_pct": round(float(d.get("avg_headshot_kills_accuracy") or 0) * 100, 1),
                    "fk_pr": round(float(d.get("avg_first_kills") or 0), 2),
                    "fd_pr": round(float(d.get("avg_first_death") or 0), 2),
                    "clutches_won": clutches_total,
                    "multikills_3k_plus": multikills_3plus,
                    "bo3_rating": round(float(d.get("avg_player_rating") or 0), 2),
                }

        # 2. Recent 30-day pro map gamelogs (/players/{slug}/gamelogs?limit=30)
        gl_resp = self._get_json(f"/players/{slug}/gamelogs?limit=30", timeout=10, ttl=300)
        gamelogs = (gl_resp or {}).get("gamelogs") or []
        if isinstance(gamelogs, list) and gamelogs:
            maps_cnt = len(gamelogs)
            kills = sum(int(m.get("kills") or 0) for m in gamelogs)
            deaths = sum(int(m.get("deaths") or 0) for m in gamelogs)
            hs = sum(int(m.get("headshots") or 0) for m in gamelogs)
            rounds = sum(int(m.get("rounds_played") or 0) for m in gamelogs) or 1
            weighted_adr = sum(
                float(m.get("adr") or 0) * int(m.get("rounds_played") or 0) for m in gamelogs
            ) / rounds
            recent_maps = []
            for m in gamelogs[:3]:
                map_name = m.get("map_name") or "Map"
                opp = m.get("opponent") or "Opponent"
                mk = m.get("kills", 0)
                md = m.get("deaths", 0)
                madr = round(float(m.get("adr") or 0), 1)
                recent_maps.append(f"{map_name} vs {opp} ({mk}K/{md}D, {madr} ADR)")

            result["recent_30d"] = {
                "maps": maps_cnt,
                "rounds": rounds,
                "kd": round(kills / max(1, deaths), 2),
                "kills": kills,
                "deaths": deaths,
                "adr": round(weighted_adr, 1),
                "kpr": round(kills / rounds, 2),
                "dpr": round(deaths / rounds, 2),
                "hs_pct": round((hs / max(1, kills)) * 100, 1),
                "recent_map_logs": recent_maps,
                "match_slugs": list(dict.fromkeys(m.get("match_slug") for m in gamelogs if m.get("match_slug"))),
            }

        return result

    def backfill_recent_from_match_boxscores(self, match_slugs: list[str], player_slug: str) -> Optional[dict]:
        """
        When a player's /gamelogs endpoint returns {'available': False, 'reason': 'warming'},
        reconstruct their exact recent 30-day map stats from their team's recent match boxscores.
        """
        slug = (player_slug or "").strip().lower()
        if not slug or not match_slugs or not self.is_configured():
            return None

        player_maps = []
        for m_slug in match_slugs[:5]:
            bs = self._get_json(f"/matches/{m_slug}/boxscore", timeout=10, ttl=600)
            if not bs or not isinstance(bs.get("maps"), list):
                continue
            t1_name = (bs.get("team1") or {}).get("name") or ""
            t2_name = (bs.get("team2") or {}).get("name") or ""
            for mp in bs["maps"]:
                map_name = mp.get("map_name") or "Map"
                rounds_played = int(mp.get("rounds_played") or 0)
                for p in mp.get("players") or []:
                    p_slug = str(p.get("player_slug") or p.get("nickname") or "").lower()
                    if p_slug == slug:
                        p_team = str(p.get("team") or "")
                        opp = t2_name if p_team == t1_name else (t1_name if p_team == t2_name else "Opponent")
                        player_maps.append({
                            "map_name": map_name,
                            "opponent": opp,
                            "kills": int(p.get("kills") or 0),
                            "deaths": int(p.get("deaths") or 0),
                            "headshots": int(p.get("headshots") or 0),
                            "adr": float(p.get("adr") or 0),
                            "rounds_played": int(p.get("rounds_played") or rounds_played or 20),
                        })

        if not player_maps:
            return None

        maps_cnt = len(player_maps)
        kills = sum(m["kills"] for m in player_maps)
        deaths = sum(m["deaths"] for m in player_maps)
        hs = sum(m["headshots"] for m in player_maps)
        rounds = sum(m["rounds_played"] for m in player_maps) or 1
        weighted_adr = sum(m["adr"] * m["rounds_played"] for m in player_maps) / rounds
        recent_maps = [
            f"{m['map_name']} vs {m['opponent']} ({m['kills']}K/{m['deaths']}D, {round(m['adr'], 1)} ADR)"
            for m in player_maps[:3]
        ]
        return {
            "maps": maps_cnt,
            "rounds": rounds,
            "kd": round(kills / max(1, deaths), 2),
            "kills": kills,
            "deaths": deaths,
            "adr": round(weighted_adr, 1),
            "kpr": round(kills / rounds, 2),
            "dpr": round(deaths / rounds, 2),
            "hs_pct": round((hs / max(1, kills)) * 100, 1),
            "recent_map_logs": recent_maps,
        }

    def get_props_for_query(self, query: str) -> str:
        """
        Fetches live CS2 player props from /v6/esports/cs2/props matching a team, matchup, or player.
        """
        if not self.is_configured() or not query:
            return ""

        q_lower = query.lower().strip()
        board = self._get_json("/props", timeout=12, ttl=300)
        props = (board or {}).get("props") or []
        if not isinstance(props, list) or not props:
            return ""

        stop = {
            "vs", "versus", "odds", "bet", "betting", "props", "prop", "lines", "line",
            "cs", "cs2", "csgo", "match", "today", "tomorrow", "what", "are", "the", "on", "for", "team",
        }
        tokens = [t.rstrip("s") if len(t) > 4 else t for t in q_lower.replace("'", " ").split() if len(t) >= 2 and t not in stop]
        if not tokens:
            return ""

        primary_matched = []
        secondary_matched = []
        for p in props:
            p_name = str(p.get("player_name") or "").lower()
            p_slug = str(p.get("player_slug") or "").lower()
            team = str(p.get("team") or "").lower()
            opp = str(p.get("opponent") or "").lower()
            if any(tok in p_name or tok in p_slug or tok in team for tok in tokens):
                primary_matched.append(p)
            elif any(tok in opp for tok in tokens):
                secondary_matched.append(p)

        matched = primary_matched if primary_matched else secondary_matched
        if not matched:
            return ""

        # Group by (player_name, stat_type) and pick the primary consensus line (most books / both OVER & UNDER)
        by_player_stat: dict[tuple[str, str], dict[str, dict]] = {}
        for p in matched:
            p_name = str(p.get("player_name") or p.get("player_slug") or "Player")
            team = str(p.get("team") or "").strip()
            opp = str(p.get("opponent") or "").strip()
            matchup_str = f"{team} vs {opp}" if (team and opp) else (team or opp)
            stat_raw = str(p.get("stat_type") or "").replace("CS2_", "").replace("_", " ").title()
            line_str = str(p.get("line"))
            direction = str(p.get("direction") or "").lower()
            odds = p.get("odds")
            book = str(p.get("book_name") or p.get("sportsbook_name") or "Book")

            ps_key = (p_name, stat_raw)
            by_player_stat.setdefault(ps_key, {})
            if line_str not in by_player_stat[ps_key]:
                by_player_stat[ps_key][line_str] = {
                    "matchup": matchup_str,
                    "books": set(),
                    "directions": set(),
                    "odds": odds,
                }
            entry = by_player_stat[ps_key][line_str]
            entry["books"].add(book)
            if direction:
                entry["directions"].add(direction.upper())
            if odds is not None and entry["odds"] is None:
                entry["odds"] = odds

        out_lines = ["=== LIVE CS2 PLAYER PROPS (KashRock API) ==="]
        for (p_name, stat_label), lines_dict in list(by_player_stat.items())[:12]:
            # Pick best line: prefer lines with both OVER and UNDER, then most sportsbooks
            best_line_str, info = max(
                lines_dict.items(),
                key=lambda kv: (len(kv[1]["directions"]), len(kv[1]["books"])),
            )
            dirs = "/".join(sorted(info["directions"])) or "O/U"
            odds_str = f" (Odds: {info['odds']})" if info["odds"] is not None else ""
            books_str = ", ".join(sorted(info["books"])[:3])
            out_lines.append(
                f"- **{p_name}** ({info['matchup']}): {stat_label} **{dirs} {best_line_str}**{odds_str} [{books_str}]"
            )
        return "\n".join(out_lines) + "\n\n"

    @staticmethod
    def _parse_bo3_series(item: dict) -> CsLiveSeries:
        """Convert a BO3.gg match JSON object into a normalized CsLiveSeries."""
        t1_obj = item.get("team1") or {}
        t2_obj = item.get("team2") or {}
        t1_name = str(t1_obj.get("name") or "Team 1")
        t2_name = str(t2_obj.get("name") or "Team 2")
        t1_id = t1_obj.get("id")
        t2_id = t2_obj.get("id")
        t1_rank = int(t1_obj.get("rank") or 0)
        t2_rank = int(t2_obj.get("rank") or 0)

        tourn_obj = item.get("tournament") or {}
        tourn_name = str(tourn_obj.get("name") or "CS2 Match")
        tier = str(item.get("tier") or tourn_obj.get("tier") or "").upper()
        prize = int(tourn_obj.get("prize") or 0)
        stage_obj = item.get("stage") or {}
        stage_title = str(stage_obj.get("title") or "")

        bo_type = int(item.get("bo_type") or 3)
        status = str(item.get("status") or "current")
        t1_score = int(item.get("team1_score") or 0)
        t2_score = int(item.get("team2_score") or 0)

        raw_games = sorted(
            item.get("games") or [],
            key=lambda g: int(g.get("number") or 99),
        )
        maps: list[CsMapResult] = []
        active_map_name = ""
        active_map_num = 0
        for g in raw_games:
            m_num = int(g.get("number") or (len(maps) + 1))
            m_name = _clean_map_name(g.get("map_name"))
            m_status = str(g.get("status") or "upcoming")
            w_score = int(g.get("winner_clan_score") or 0)
            l_score = int(g.get("loser_clan_score") or 0)
            w_clan = str(g.get("winner_clan_name") or "")
            l_clan = str(g.get("loser_clan_name") or "")
            w_team_id = ((g.get("winner_team_clan") or {}).get("team") or {}).get("id")

            winner_name = ""
            loser_name = ""
            t1_rds = 0
            t2_rds = 0
            if m_status == "finished" and (w_score or l_score):
                if w_team_id and t1_id and int(w_team_id) == int(t1_id):
                    winner_name, loser_name = t1_name, t2_name
                    t1_rds, t2_rds = w_score, l_score
                elif w_team_id and t2_id and int(w_team_id) == int(t2_id):
                    winner_name, loser_name = t2_name, t1_name
                    t1_rds, t2_rds = l_score, w_score
                else:
                    winner_name = w_clan or t1_name
                    loser_name = l_clan or t2_name
                    if _team_match_key(winner_name) == _team_match_key(t1_name):
                        t1_rds, t2_rds = w_score, l_score
                    else:
                        t1_rds, t2_rds = l_score, w_score

            if m_status == "current" and not active_map_name:
                active_map_name = m_name
                active_map_num = m_num

            maps.append(
                CsMapResult(
                    map_number=m_num,
                    map_name=m_name,
                    status=m_status,
                    winner=winner_name,
                    loser=loser_name,
                    winner_score=w_score,
                    loser_score=l_score,
                    team1_rounds=t1_rds,
                    team2_rounds=t2_rds,
                )
            )

        # Infer missing winner on a just-finished map if series score accounts for it
        finished_maps = [mp for mp in maps if mp.status == "finished"]
        missing_winner_maps = [mp for mp in finished_maps if not mp.winner]
        if len(missing_winner_maps) == 1 and len(finished_maps) == (t1_score + t2_score):
            known_t1 = sum(1 for mp in finished_maps if mp.winner and _team_match_key(mp.winner) == _team_match_key(t1_name))
            known_t2 = sum(1 for mp in finished_maps if mp.winner and _team_match_key(mp.winner) == _team_match_key(t2_name))
            target_mp = missing_winner_maps[0]
            if t1_score == known_t1 + 1 and t2_score == known_t2:
                target_mp.winner, target_mp.loser = t1_name, t2_name
            elif t2_score == known_t2 + 1 and t1_score == known_t1:
                target_mp.winner, target_mp.loser = t2_name, t1_name

        raw_streams = sorted(
            item.get("streams") or [],
            key=lambda s: (not bool(s.get("official")), -(int(s.get("viewers_number") or 0))),
        )
        streams: list[str] = []
        seen_urls: set[str] = set()
        for s in raw_streams:
            s_url = str(s.get("raw_url") or s.get("stream_url") or s.get("url") or "").strip()
            s_lang = str(s.get("language") or "en").upper()
            if s_url and s_url not in seen_urls:
                seen_urls.add(s_url)
                streams.append(f"<{s_url}> ({s_lang})")
            if len(streams) >= 3:
                break

        odds_summary = ""
        bet_upd = item.get("bet_updates") or {}
        b_t1 = bet_upd.get("team_1") or {}
        b_t2 = bet_upd.get("team_2") or {}
        if b_t1.get("coeff") and b_t2.get("coeff"):
            odds_summary = f"{t1_name} {b_t1['coeff']} vs {t2_name} {b_t2['coeff']}"

        return CsLiveSeries(
            match_id=str(item.get("id") or item.get("slug") or ""),
            slug=str(item.get("slug") or ""),
            tournament=tourn_name,
            tier=tier,
            stage=stage_title,
            prize_pool=prize,
            bo_type=bo_type,
            status=status,
            start_date=str(item.get("start_date") or ""),
            team1=t1_name,
            team2=t2_name,
            team1_score=t1_score,
            team2_score=t2_score,
            team1_rank=t1_rank,
            team2_rank=t2_rank,
            maps=maps,
            active_map_name=active_map_name,
            active_map_number=active_map_num,
            streams=streams,
            odds_summary=odds_summary,
        )

    @staticmethod
    def _parse_kr_live_match(m: dict) -> CsLiveSeries:
        """Convert a KashRock /matches?status=live item into a normalized CsLiveSeries."""
        t1_name = str(m.get("team1") or m.get("blue_code") or "Team 1")
        t2_name = str(m.get("team2") or m.get("red_code") or "Team 2")
        kr_id = str(m.get("kr_match_id") or m.get("match_id") or m.get("game_id") or "")
        slug = str(m.get("slug") or kr_id)
        tourn = str(m.get("competition") or m.get("league") or "CS2 Match")
        tier = str(m.get("competition_tier") or m.get("tier") or "").upper()
        prize = int(m.get("competition_prize") or 0)
        bo_type = int(m.get("bo_type") or 3)
        t1_score = int(m.get("team1_score") or 0)
        t2_score = int(m.get("team2_score") or 0)
        t1_rank = int(m.get("team1_rank") or 0)
        t2_rank = int(m.get("team2_rank") or 0)

        maps: list[CsMapResult] = []
        active_map_name = ""
        active_map_num = 0
        for mp in sorted(m.get("maps") or [], key=lambda x: int(x.get("map_number") or 99)):
            m_num = int(mp.get("map_number") or (len(maps) + 1))
            m_name = _clean_map_name(mp.get("map_name") or mp.get("map_code"))
            m_status = str(mp.get("status") or "upcoming")
            w_name = str(mp.get("winner") or "")
            l_name = str(mp.get("loser") or "")
            w_score = int(mp.get("winner_score") or 0)
            l_score = int(mp.get("loser_score") or 0)
            t1_rds = 0
            t2_rds = 0
            if m_status == "finished" and (w_score or l_score):
                if _team_match_key(w_name) == _team_match_key(t1_name):
                    t1_rds, t2_rds = w_score, l_score
                else:
                    t1_rds, t2_rds = l_score, w_score
            if m_status == "current" and not active_map_name:
                active_map_name = m_name
                active_map_num = m_num

            maps.append(
                CsMapResult(
                    map_number=m_num,
                    map_name=m_name,
                    status=m_status,
                    winner=w_name,
                    loser=l_name,
                    winner_score=w_score,
                    loser_score=l_score,
                    team1_rounds=t1_rds,
                    team2_rounds=t2_rds,
                )
            )

        return CsLiveSeries(
            match_id=kr_id or slug,
            slug=slug,
            tournament=tourn,
            tier=tier,
            stage=str(m.get("stage_name") or ""),
            prize_pool=prize,
            bo_type=bo_type,
            status="current",
            start_date=str(m.get("start_date") or m.get("start_time") or ""),
            team1=t1_name,
            team2=t2_name,
            team1_score=t1_score,
            team2_score=t2_score,
            team1_rank=t1_rank,
            team2_rank=t2_rank,
            kr_game_id=kr_id,
            maps=maps,
            active_map_name=active_map_name,
            active_map_number=active_map_num,
        )

    def _enrich_with_live_boxscore(self, series: CsLiveSeries) -> None:
        """Fetch live in-map round score & top player K/D/ADR from KashRock /live/{id}/boxscore (45s cache)."""
        if not self.is_configured() or not series.kr_game_id:
            return
        bs = self._get_json(f"/live/{series.kr_game_id}/boxscore", timeout=8, ttl=45)
        if not bs or not isinstance(bs.get("maps"), list) or not bs["maps"]:
            return

        latest_map = bs["maps"][-1]
        m_name = _clean_map_name(latest_map.get("map_name"))
        b_score = int(latest_map.get("blue_score") or 0)
        r_score = int(latest_map.get("red_score") or 0)
        m_status = str(latest_map.get("status") or "live")
        if m_name and m_name != "TBD":
            series.active_map_name = m_name
        if not series.active_map_number:
            series.active_map_number = int(latest_map.get("map_number") or 1)
        series.live_round_score = f"{m_name}: {series.team1} {b_score} - {r_score} {series.team2} ({m_status})"

        # Update active map rounds inside series.maps if present
        for mp in series.maps:
            if mp.map_name.lower() == m_name.lower() or mp.map_number == series.active_map_number:
                mp.team1_rounds = b_score
                mp.team2_rounds = r_score
                break

        players = latest_map.get("players") or []
        if isinstance(players, list) and players:
            sorted_p = sorted(
                players,
                key=lambda p: (int(p.get("kills") or 0), float(p.get("adr") or 0.0)),
                reverse=True,
            )
            top_list: list[str] = []
            for p in sorted_p[:4]:
                nick = str(p.get("nickname") or p.get("player_slug") or "Player")
                k = int(p.get("kills") or 0)
                d = int(p.get("deaths") or 0)
                adr = float(p.get("adr") or 0.0)
                adv = p.get("advanced_stats") or {}
                rt = adv.get("rating") or p.get("rating")
                rt_str = f", {float(rt):.2f} Rtg" if rt is not None else ""
                adr_str = f", {adr:.1f} ADR" if adr > 0 else ""
                top_list.append(f"{nick} ({k}K/{d}D{adr_str}{rt_str})")
            series.top_players = top_list

    def get_live_and_upcoming_series(
        self,
        match_query: Optional[str] = None,
        include_live_boxscore: bool = True,
    ) -> tuple[list[CsLiveSeries], list[CsLiveSeries]]:
        """
        Return `(live_series, upcoming_series)` combining BO3.gg public JSON API (primary fast spine)
        with KashRock `/matches?status=live` and `/live/{id}/boxscore` (quota-guarded to max 2 calls).
        """
        bo3_items = self.fetch_bo3_matches(status_filter="current,upcoming", limit=25, ttl=30)
        live_by_pair: dict[frozenset[str], CsLiveSeries] = {}
        live_list: list[CsLiveSeries] = []
        upcoming_list: list[CsLiveSeries] = []

        for item in bo3_items:
            parsed = self._parse_bo3_series(item)
            pair_key = frozenset({_team_match_key(parsed.team1), _team_match_key(parsed.team2)})
            if parsed.status == "current":
                live_by_pair[pair_key] = parsed
                live_list.append(parsed)
            else:
                upcoming_list.append(parsed)

        # Enrich or backfill from KashRock /matches?status=live (1 cached call, 45s TTL)
        if self.is_configured():
            kr_live_resp = self._get_json("/matches?status=live", timeout=8, ttl=45)
            kr_matches = (kr_live_resp or {}).get("matches") or []
            if isinstance(kr_matches, list):
                for km in kr_matches:
                    kr_series = self._parse_kr_live_match(km)
                    pair_key = frozenset({_team_match_key(kr_series.team1), _team_match_key(kr_series.team2)})
                    existing = live_by_pair.get(pair_key)
                    if existing:
                        existing.kr_game_id = kr_series.kr_game_id or existing.kr_game_id
                        if kr_series.prize_pool and not existing.prize_pool:
                            existing.prize_pool = kr_series.prize_pool
                        if kr_series.team1_rank and not existing.team1_rank:
                            existing.team1_rank = kr_series.team1_rank
                        if kr_series.team2_rank and not existing.team2_rank:
                            existing.team2_rank = kr_series.team2_rank
                        if kr_series.maps:
                            if not existing.maps:
                                existing.maps = kr_series.maps
                            else:
                                kr_by_num = {m.map_number: m for m in kr_series.maps}
                                for emp in existing.maps:
                                    kmp = kr_by_num.get(emp.map_number)
                                    if kmp:
                                        if not emp.winner and kmp.winner:
                                            emp.winner = kmp.winner
                                            emp.loser = kmp.loser
                                        if not emp.winner_score and kmp.winner_score:
                                            emp.winner_score = kmp.winner_score
                                            emp.loser_score = kmp.loser_score
                    else:
                        live_by_pair[pair_key] = kr_series
                        live_list.append(kr_series)

            # Fallback to /live/games if neither BO3.gg nor /matches?status=live returned live series
            if not live_list:
                lg_resp = self._get_json("/live/games", timeout=8, ttl=45)
                lg_games = (lg_resp or {}).get("games") or []
                for g in lg_games:
                    kr_s = self._parse_kr_live_match(g)
                    if not kr_s.active_map_number and g.get("number"):
                        kr_s.active_map_number = int(g["number"])
                    live_list.append(kr_s)

        # Sort live matches: query matches first, then S/A-tier tournaments first
        q_tokens = [
            t for t in re.findall(r"[a-z0-9]+", (match_query or "").lower())
            if len(t) >= 2 and t not in ("vs", "cs", "cs2", "csgo", "live", "match", "game", "score", "the", "final", "finals")
        ]

        def _series_priority(s: CsLiveSeries) -> tuple[int, int]:
            haystack = f"{s.team1} {s.team2} {s.tournament} {s.stage} {s.slug}".lower()
            q_hit = 1 if (q_tokens and any(tok in haystack for tok in q_tokens)) else 0
            tier_rank = {"S": 4, "A": 3, "B": 2, "C": 1}.get((s.tier or "").upper(), 0)
            return (-q_hit, -tier_rank)

        live_list.sort(key=_series_priority)

        # Quota guard: only query /live/{id}/boxscore for at most 2 top live matches
        if include_live_boxscore and self.is_configured():
            boxscore_calls = 0
            for s in live_list:
                if not s.kr_game_id:
                    continue
                if boxscore_calls >= 2:
                    break
                self._enrich_with_live_boxscore(s)
                boxscore_calls += 1

        return live_list, upcoming_list

    @staticmethod
    def _format_series_block(s: CsLiveSeries) -> str:
        """Render a single CsLiveSeries into a rich multi-line markdown block."""
        t1_rk = f" (#{s.team1_rank})" if s.team1_rank else ""
        t2_rk = f" (#{s.team2_rank})" if s.team2_rank else ""
        tier_tag = f"{s.tier}-Tier" if s.tier else ""
        prize_tag = f"${s.prize_pool:,}" if s.prize_pool else ""
        meta_parts = [p for p in (tier_tag, prize_tag, s.stage if s.stage and s.stage != s.tournament else "") if p]
        meta_str = f" [{' | '.join(meta_parts)}]" if meta_parts else ""

        next_up_map: Optional[CsMapResult] = None
        if not s.active_map_name and s.maps and (s.team1_score or s.team2_score):
            for mp in s.maps:
                if mp.status == "upcoming":
                    next_up_map = mp
                    break

        active_badge = ""
        if s.live_round_score:
            active_badge = f" — 🔴 LIVE ({s.live_round_score})"
        elif s.active_map_name:
            active_badge = f" — 🔴 LIVE on Map {s.active_map_number}: **{s.active_map_name}**"
        elif next_up_map:
            active_badge = f" — 🔴 LIVE (Between Maps — Next Map: Map {next_up_map.map_number} **{next_up_map.map_name}**)"
        elif s.active_map_number:
            active_badge = f" — Map #{s.active_map_number} in progress"

        lines = [
            f"- **{s.team1}**{t1_rk} **{s.team1_score} - {s.team2_score}** **{s.team2}**{t2_rk} "
            f"(Best of {s.bo_type} | {s.tournament}{meta_str}){active_badge}"
        ]

        if s.maps:
            map_chunks: list[str] = []
            for mp in s.maps:
                if mp.status == "finished":
                    if mp.winner_score or mp.loser_score:
                        ot_tag = " (OT)" if (mp.winner_score + mp.loser_score) > 24 else ""
                        map_chunks.append(
                            f"Map {mp.map_number} ({mp.map_name}): ✅ **{mp.winner} {mp.winner_score}-{mp.loser_score} {mp.loser}**{ot_tag}"
                        )
                    elif mp.winner:
                        map_chunks.append(
                            f"Map {mp.map_number} ({mp.map_name}): ✅ **{mp.winner} Won**"
                        )
                    else:
                        map_chunks.append(f"Map {mp.map_number} ({mp.map_name}): ✅ **Completed**")
                elif mp.status == "current":
                    if mp.team1_rounds or mp.team2_rounds:
                        map_chunks.append(
                            f"Map {mp.map_number} ({mp.map_name}): 🔴 **LIVE ({s.team1} {mp.team1_rounds}-{mp.team2_rounds} {s.team2})**"
                        )
                    else:
                        map_chunks.append(f"Map {mp.map_number} ({mp.map_name}): 🔴 **LIVE (In Progress)**")
                elif next_up_map and mp.map_number == next_up_map.map_number:
                    map_chunks.append(f"Map {mp.map_number} ({mp.map_name}): ⏭️ **Up Next / Starting Now**")
                else:
                    map_chunks.append(f"Map {mp.map_number} ({mp.map_name}): ⏳ Upcoming")
            lines.append("  • **Series Maps:** " + " | ".join(map_chunks))

        if s.top_players:
            lines.append("  • **Live Map Leaders:** " + " | ".join(s.top_players))
        if s.streams:
            lines.append("  • **Official Streams:** " + " , ".join(s.streams))

        return "\n".join(lines)

    def get_series_for_tracker(
        self,
        event_id: str,
        home_team: str = "",
        away_team: str = "",
    ) -> Optional[CsLiveSeries]:
        """
        Fetch a specific CS2 series for the live background tracker.
        Checks active/upcoming series first (with live boxscore enrichment), and falls back
        to recently finished BO3.gg matches if the series just concluded.
        """
        query_str = f"{home_team} {away_team}".strip()
        live_list, upcoming_list = self.get_live_and_upcoming_series(
            match_query=query_str or None,
            include_live_boxscore=True,
        )
        target_pair = frozenset(
            k for k in (_team_match_key(home_team), _team_match_key(away_team)) if k
        )
        for s in live_list + upcoming_list:
            if event_id and (str(s.match_id) == str(event_id) or str(s.kr_game_id) == str(event_id) or s.slug == event_id):
                return s
            if len(target_pair) == 2:
                s_pair = frozenset({_team_match_key(s.team1), _team_match_key(s.team2)})
                if s_pair == target_pair:
                    return s

        # Check recently finished matches in case the series just ended between polls
        finished_items = self.fetch_bo3_matches(status_filter="finished", limit=15, ttl=25)
        for item in finished_items:
            parsed = self._parse_bo3_series(item)
            if event_id and (str(parsed.match_id) == str(event_id) or parsed.slug == event_id):
                return parsed
            if len(target_pair) == 2:
                p_pair = frozenset({_team_match_key(parsed.team1), _team_match_key(parsed.team2)})
                if p_pair == target_pair:
                    return parsed
        return None

    def get_live_scoreboards(self, match_query: Optional[str] = None) -> str:
        """
        Fetches currently active CS2 live series (with BO1/BO3/BO5 map breakdowns, completed map scores,
        active map status, and live player boxscores) plus upcoming scheduled matches today.
        """
        live_list, upcoming_list = self.get_live_and_upcoming_series(
            match_query=match_query,
            include_live_boxscore=True,
        )
        if not live_list and not upcoming_list:
            return ""

        top_series = live_list[0] if live_list else upcoming_list[0]
        try:
            from src.skills.sports.aggregators.espn_utils import record_channel_last_match_query
            record_channel_last_match_query(f"{top_series.team1} vs {top_series.team2}", sport="esports")
        except Exception:
            pass

        out_sections: list[str] = []
        if live_list:
            lines = ["=== LIVE IN-GAME CS2 SCOREBOARDS (BO3.gg & KashRock API) ==="]
            for s in live_list[:5]:
                lines.append(self._format_series_block(s))
            out_sections.append("\n".join(lines))

        if upcoming_list:
            up_lines = ["=== UPCOMING CS2 MATCH SCHEDULE (BO3.gg) ==="]
            for u in upcoming_list[:5]:
                t1_rk = f" (#{u.team1_rank})" if u.team1_rank else ""
                t2_rk = f" (#{u.team2_rank})" if u.team2_rank else ""
                start_str = _format_iso_start_time(u.start_date)
                up_lines.append(
                    f"- [{start_str}] **{u.team1}**{t1_rk} vs **{u.team2}**{t2_rk} "
                    f"(Best of {u.bo_type} — {u.tournament})"
                )
            out_sections.append("\n".join(up_lines))

        return "\n\n".join(out_sections) + "\n\n"


