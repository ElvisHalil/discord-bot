import os
import time
from typing import Optional
import requests
from src.skills.sports.aggregators.base import BaseSportsAggregator

_KASHROCK_CACHE: dict[str, tuple[float, object]] = {}
_KASHROCK_CACHE_TTL = 300  # 5 minutes
_KASHROCK_SESSION: Optional[requests.Session] = None


def clear_kashrock_cache() -> None:
    """Clear the in-memory KashRock API response cache."""
    _KASHROCK_CACHE.clear()


def _get_session() -> requests.Session:
    global _KASHROCK_SESSION
    if _KASHROCK_SESSION is None:
        _KASHROCK_SESSION = requests.Session()
    return _KASHROCK_SESSION


class KashrockAggregator(BaseSportsAggregator):
    """
    Client for the KashRock v6 Esports API (CS2 pro player stats, map gamelogs,
    live player props across 25+ books/DFS, and live in-game boxscores).
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

    def get_live_scoreboards(self) -> str:
        """
        Fetches currently active CS2 live matches and map boxscores from /v6/esports/cs2/live/games.
        """
        if not self.is_configured():
            return ""

        live_resp = self._get_json("/live/games", timeout=10, ttl=60)
        games = (live_resp or {}).get("games") or []
        if not isinstance(games, list) or not games:
            return ""

        lines = ["=== LIVE IN-GAME CS2 SCOREBOARDS (KashRock API) ==="]
        for g in games[:4]:
            game_id = g.get("game_id") or g.get("kr_match_id")
            league = g.get("league") or "CS2 Match"
            t1 = g.get("blue_code") or "Team 1"
            t2 = g.get("red_code") or "Team 2"
            map_num = g.get("number") or 1

            score_detail = f"Map #{map_num} in progress"
            if game_id:
                bs = self._get_json(f"/live/{game_id}/boxscore", timeout=8, ttl=60)
                if bs and isinstance(bs.get("maps"), list) and bs["maps"]:
                    latest_map = bs["maps"][-1]
                    m_name = str(latest_map.get("map_name") or "").replace("de_", "").capitalize()
                    b_score = latest_map.get("blue_score", 0)
                    r_score = latest_map.get("red_score", 0)
                    m_status = latest_map.get("status", "live")
                    score_detail = f"{m_name}: {t1} {b_score} - {r_score} {t2} ({m_status})"

            lines.append(f"- **{t1}** vs **{t2}** ({league}) — {score_detail}")
        return "\n".join(lines) + "\n\n"
