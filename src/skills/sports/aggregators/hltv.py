import re
import time
from typing import Optional
from urllib.parse import quote_plus
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.catalogs import HLTV_STOPWORDS, HLTV_TEAM_ALIASES
from src.utils.cache import BoundedCache
from src.utils.http import fetch_soup
from src.utils.workers import submit_to_worker

_HLTV_CACHE: dict[str, tuple[float, object]] = BoundedCache(maxsize=128)
_HLTV_CACHE_TTL = 600  # 10 minutes


def clear_hltv_cache() -> None:
    """Clear the cached HLTV rankings and player profile responses."""
    _HLTV_CACHE.clear()


class HltvAggregator(BaseSportsAggregator):
    """Aggregator for HLTV team rankings and CS player statistics using centralized stealth client."""

    TEAM_ALIASES = HLTV_TEAM_ALIASES
    STOPWORDS = HLTV_STOPWORDS

    def _get_cached_or_fetch_soup(self, url: str, timeout: int = 30):
        now = time.time()
        if url in _HLTV_CACHE:
            ts, cached_soup = _HLTV_CACHE[url]
            if now - ts < _HLTV_CACHE_TTL:
                return cached_soup
        soup = fetch_soup(url, timeout=timeout)
        if soup is not None:
            _HLTV_CACHE[url] = (now, soup)
        return soup

    def _get_ranked_teams_structured(self) -> list[dict]:
        """Returns structured list of ranked teams: [{'rank': int, 'name': str, 'players': [(name, href), ...]}]."""
        soup = self._get_cached_or_fetch_soup('https://www.hltv.org/ranking/teams/', timeout=30)
        teams = []
        if not soup:
            return teams
        for i, team in enumerate(soup.select('.ranked-team')):
            name_el = team.select_one('.name')
            name = name_el.get_text(strip=True) if name_el else "Unknown"
            players = []
            seen_hrefs = set()
            for a in team.select('a[href*="/player/"]'):
                p_name = a.get_text(strip=True)
                href = a.get('href', '').split('#')[0]
                if p_name and href and href not in seen_hrefs:
                    seen_hrefs.add(href)
                    players.append((p_name, href))
            teams.append({
                "rank": i + 1,
                "name": name,
                "players": players[:5],
            })
        return teams

    def get_rankings(self, limit: int = 15) -> str:
        """Fetches the current world team rankings and rosters from HLTV."""
        print("  └─ 📈 [HLTV]: Fetching World Rankings...")
        try:
            teams = self._get_ranked_teams_structured()
            output = f"[HLTV TOP {limit} WORLD RANKINGS]\n"
            if not teams:
                output += "Could not extract HLTV rankings (might be blocked by Cloudflare).\n"
            else:
                for team in teams[:limit]:
                    players_str = (
                        " | ".join(f"{p_name} ({href})" for p_name, href in team["players"])
                        if team["players"]
                        else "No roster found"
                    )
                    output += f"{team['rank']}. {team['name']} - Roster: {players_str}\n"
            return output + "\n"
        except Exception as e:
            return f"[RANKINGS]\nError fetching HLTV rankings: {e}\n\n"

    def _resolve_targets(self, target: str) -> tuple[Optional[str], list[tuple[str, str]]]:
        """
        Resolves a player URL, player name, or team name into:
          (team_label_or_None, [(player_name, player_href), ...])
        """
        raw = (target or "").strip()
        if not raw:
            return None, []

        # 1. Direct /player/<id>/<slug> path or full URL
        m_direct = re.search(r'(/player/\d+/[^\s"\'<>#?]+)', raw)
        if m_direct:
            path = m_direct.group(1)
            slug = path.rstrip('/').split('/')[-1]
            return None, [(slug, path)]

        q_lower = raw.lower()
        tokens = [t for t in re.split(r'[^a-z0-9.\-_]+', q_lower) if t and t not in self.STOPWORDS]

        # 2. Check ranked teams (Top 30) for matching player names or team name
        try:
            ranked_teams = self._get_ranked_teams_structured()
        except Exception:
            ranked_teams = []

        # 2a. Check if specific player handle(s) in Top 30 are mentioned
        matched_players: list[tuple[str, str]] = []
        for team in ranked_teams:
            for p_name, p_href in team["players"]:
                p_low = p_name.lower()
                p_clean = p_low.rstrip('-')
                if p_low in tokens or p_clean in tokens or p_low == q_lower.strip():
                    if (p_name, p_href) not in matched_players:
                        matched_players.append((p_name, p_href))
        if matched_players:
            return None, matched_players[:5]

        # 2b. Check if a ranked team name is mentioned (e.g., "whats falcons player current stats")
        canonical_team = None
        for alias, canon in sorted(self.TEAM_ALIASES.items(), key=lambda x: -len(x[0])):
            if re.search(rf'\b{re.escape(alias)}\b', q_lower):
                canonical_team = canon
                break

        for team in ranked_teams:
            t_low = team["name"].lower()
            if (canonical_team and (canonical_team == t_low or canonical_team in t_low or t_low in canonical_team)) or (
                t_low in tokens or re.search(rf'\b{re.escape(t_low)}\b', q_lower)
            ):
                if team["players"]:
                    return team["name"], team["players"][:5]

        # 3. Fallback: Search HLTV (/search?query=...) for player or team outside Top 30
        search_terms = " ".join(tokens) if tokens else raw
        if canonical_team and not tokens:
            search_terms = canonical_team
        try:
            search_url = f"https://www.hltv.org/search?query={quote_plus(search_terms)}"
            s_soup = self._get_cached_or_fetch_soup(search_url, timeout=20)
            if s_soup:
                # If query is asking for a team's players or matches a known team alias, check /team/ links first
                wants_team = bool(canonical_team) or any(w in q_lower for w in ("players", "roster", "team", "lineup"))
                team_links = []
                player_links = []
                seen_t = set()
                seen_p = set()
                for a in s_soup.select('a[href]'):
                    href = a.get('href', '').split('#')[0]
                    label = a.get_text(" ", strip=True)
                    if re.match(r'^/team/\d+/', href) and href not in seen_t:
                        seen_t.add(href)
                        team_links.append((label, href))
                    elif re.match(r'^/player/\d+/', href) and href not in seen_p:
                        seen_p.add(href)
                        player_links.append((label, href))

                if wants_team and team_links:
                    # Pick best matching team link
                    chosen_team_label, chosen_team_href = team_links[0]
                    for t_label, t_href in team_links:
                        if canonical_team and canonical_team in t_label.lower():
                            chosen_team_label, chosen_team_href = t_label, t_href
                            break
                        if any(tok in t_label.lower() for tok in tokens):
                            chosen_team_label, chosen_team_href = t_label, t_href
                            break
                    t_soup = self._get_cached_or_fetch_soup(f"https://www.hltv.org{chosen_team_href}", timeout=20)
                    if t_soup:
                        roster_players = []
                        seen_rp = set()
                        for a in t_soup.select('a[href*="/player/"]'):
                            p_href = a.get('href', '').split('#')[0]
                            p_name = a.get_text(strip=True)
                            if re.match(r'^/player/\d+/', p_href) and p_name and p_href not in seen_rp:
                                seen_rp.add(p_href)
                                roster_players.append((p_name, p_href))
                        if roster_players:
                            return chosen_team_label, roster_players[:5]

                if player_links:
                    return None, [player_links[0]]
        except Exception:
            pass

        return None, []

    def _parse_player_profile_soup(self, soup, player_url: str, fallback_name: str = "", kr_stats: Optional[dict] = None) -> str:
        """Extracts structured player statistics, ratings, career KDR, and recent matches from an HLTV player profile soup."""
        if not soup:
            return f"[PLAYER STATS: {player_url}]\nCould not extract stats (page unavailable).\n"

        nick_el = soup.select_one('.playerNickname')
        real_el = soup.select_one('.playerRealname')
        team_el = soup.select_one('.playerTeam .listRight a') or soup.select_one('.playerTeam .listRight')
        age_el = soup.select_one('.playerAge .listRight span[itemprop="text"]') or soup.select_one('.playerAge .listRight')

        nick = nick_el.get_text(strip=True) if nick_el else (fallback_name or player_url.split('/')[-1])
        real_name = real_el.get_text(" ", strip=True) if real_el else ""
        team_name = team_el.get_text(" ", strip=True) if team_el else ""
        age_text = age_el.get_text(" ", strip=True).split("Born")[0].strip() if age_el else ""

        # Extract Rating 3.0 & Role Attributes (.player-stat)
        rating_str = ""
        role_percentiles = []
        for st in soup.select('.player-stat'):
            txt = re.sub(r'\s+', ' ', st.get_text(" ", strip=True)).strip()
            txt = re.sub(r'\s*/\s*100', '/100', txt)
            if not txt:
                continue
            if txt.lower().startswith("rating"):
                rating_str = txt
            elif txt not in role_percentiles:
                role_percentiles.append(txt)

        # Extract sample window (e.g. "(Past 3 months • 35 maps)")
        sample_period = "Past 3 months"
        tabs = soup.select('.playerProfile .tab-content')
        if tabs:
            tab0_txt = tabs[0].get_text(" ", strip=True)
            m_period = re.search(r'\((Past [^)]+)\)', tab0_txt)
            if m_period:
                sample_period = m_period.group(1)

        # Extract Achievements / Top 20 / Prize money from .playerInfoRow
        achievements = []
        for row in soup.select('.playerInfoRow'):
            row_txt = re.sub(r'\s+', ' ', row.get_text(" ", strip=True)).strip()
            if any(k in row_txt for k in ("Top 20", "Major winner", "Prize money")):
                row_txt = re.sub(r'\(\?\)\s*Total team winnings\s*/\s*5', '', row_txt)
                achievements.append(row_txt.strip())

        # Extract Recent Pro Match Series from the Matches tab (NOTE: intentionally ignore #tab-faceitBox casual pug stats!)
        recent_matches_line = ""
        for tab in tabs:
            t_txt = re.sub(r'\s+', ' ', tab.get_text(" ", strip=True)).strip()
            if "Latest results for" in t_txt and not recent_matches_line:
                m_res = re.findall(r'(\d{2}/\d{2}/\d{4})\s+([A-Za-z0-9 .\'-]+?)\s+(\d+)\s*:\s*(\d+)\s+([A-Za-z0-9 .\'-]+?)\s+Stats', t_txt)
                if m_res:
                    formatted_res = [f"{d}: {t1.strip()} {s1}-{s2} {t2.strip()}" for d, t1, s1, s2, t2 in m_res[:4]]
                    recent_matches_line = " | ".join(formatted_res)

        # Fallback for generic/mocked HTML in unit tests that lacks .playerNickname / .player-stat breakdown
        if not rating_str and not role_percentiles and not nick_el:
            stats_texts = []
            for stat_div in soup.select('.playerProfile, .player-stat, .stat, .statistics, .summaryStatBreakdown'):
                txt = stat_div.get_text(" ", strip=True)
                if txt and txt not in stats_texts:
                    stats_texts.append(txt)
            if not stats_texts:
                return f"[PLAYER STATS: {player_url}]\nCould not extract stats (might be blocked by Cloudflare or incorrect URL).\n\n"
            text = "\n".join(stats_texts)[:4000]
            return f"[PLAYER STATS: {player_url}]\n{text}\n\n"

        # Fetch official Pro CS2 K/D, ADR, KPR, DPR, HS%, and 30-day map gamelogs from KashRock API
        if kr_stats is None:
            kr_stats = {}
            try:
                from src.skills.sports.aggregators.kashrock import KashrockAggregator
                kr = KashrockAggregator()
                if kr.is_configured():
                    slug = (nick or player_url.rstrip('/').split('/')[-1]).lower().rstrip('-')
                    kr_stats = kr.get_player_pro_stats(slug)
            except Exception as e:
                print(f"  └─ ⚠️ [HLTV KashRock single player fallback error]: {e}")
                kr_stats = {}

        full_link = f"https://www.hltv.org{player_url}" if player_url.startswith("/") else player_url
        header_parts = [f"👤 **{nick}**"]
        if real_name:
            header_parts[0] += f" ({real_name})"
        if team_name:
            header_parts.append(f"Team: **{team_name}**")
        if age_text:
            header_parts.append(f"Age: {age_text}")
        header_parts.append(f"Profile: <{full_link}>")

        lines = [" | ".join(header_parts)]

        # 1. Recent 30-Day Pro Map Stats (KashRock)
        r30 = kr_stats.get("recent_30d")
        if r30:
            lines.append(
                f"  • **Current Pro Form (Last {r30['maps']} Maps / {r30['rounds']} Rds)**: "
                f"K/D: **{r30['kd']}** ({r30['kills']}K/{r30['deaths']}D) | "
                f"ADR: **{r30['adr']}** | KPR: **{r30['kpr']}** | DPR: **{r30['dpr']}** | HS%: **{r30['hs_pct']}%**"
            )
            if r30.get("recent_map_logs"):
                lines.append(f"  • **Recent Map Boxscores**: {' | '.join(r30['recent_map_logs'])}")

        # 2. Official Pro Career Stats (KashRock / bo3.gg)
        c_pro = kr_stats.get("career_pro")
        if c_pro:
            lines.append(
                f"  • **Pro Career Stats ({c_pro['maps']} Pro Maps)**: "
                f"K/D: **{c_pro['kd']}** ({c_pro['kd_diff']:+d}) | "
                f"ADR: **{c_pro['adr']}** | KPR: **{c_pro['kpr']}** | DPR: **{c_pro['dpr']}** | "
                f"HS%: **{c_pro['hs_pct']}%** | Opening K/Rd: {c_pro['fk_pr']} | Clutches Won: {c_pro['clutches_won']}"
            )

        # 3. HLTV Rating 3.0 & Role Percentiles
        if rating_str or role_percentiles:
            combined_hltv = " | ".join(([rating_str] if rating_str else []) + role_percentiles)
            lines.append(f"  • **HLTV Rating & Role Percentiles ({sample_period}, 0-100 scale)**: {combined_hltv}")
        else:
            lines.append("  • **HLTV Rating**: No recent 3-month HLTV rating breakdown available")

        if achievements:
            lines.append(f"  • **Achievements**: {' ; '.join(achievements)}")
        if recent_matches_line:
            lines.append(f"  • **Recent Series Results**: {recent_matches_line}")

        return "\n".join(lines) + "\n"

    def _fetch_single_player(self, name: str, player_url: str, kr_stats: Optional[dict] = None) -> str:
        full_url = f"https://www.hltv.org{player_url}" if player_url.startswith("/") else player_url
        soup = self._get_cached_or_fetch_soup(full_url, timeout=30)
        return self._parse_player_profile_soup(soup, player_url, fallback_name=name, kr_stats=kr_stats)

    def get_player_stats(self, player_url: Optional[str] = None) -> str:
        """
        Fetches statistics for a specific CS player OR all active players on a CS team from HLTV + KashRock API.
        Accepts an HLTV player path ('/player/3741/niko'), a player name ('NiKo', 'm0NESY'),
        or a team name/query ('Falcons', 'whats falcons player current stats').
        """
        if not player_url or not str(player_url).strip():
            return "[PLAYER STATS]\nError: Please provide a player name, team name, or 'player_url' (e.g. 'NiKo', 'Falcons', or '/player/3741/niko').\n\n"

        target_str = str(player_url).strip()
        print(f"  └─ 👤 [HLTV]: Fetching Player Stats for '{target_str}'...")
        try:
            team_label, targets = self._resolve_targets(target_str)
            if not targets:
                return (
                    f"[PLAYER STATS: {target_str}]\n"
                    f"Could not find HLTV player or team matching '{target_str}'.\n\n"
                )

            props_section = ""
            kr_map: dict[str, dict] = {}
            try:
                from src.skills.sports.aggregators.kashrock import KashrockAggregator
                kr = KashrockAggregator()
                if kr.is_configured():
                    props_query = team_label or (targets[0][0] if len(targets) == 1 else target_str)
                    props_section = kr.get_props_for_query(props_query)

                    slug_list = [(p_name, p_href.rstrip('/').split('/')[-1].lower().rstrip('-')) for p_name, p_href in targets]
                    futs = {p_name: submit_to_worker(kr.get_player_pro_stats, slug) for p_name, slug in slug_list}
                    for p_name, fut in futs.items():
                        kr_map[p_name] = fut.result()

                    # If any teammate has recent_30d match_slugs, backfill teammates whose /gamelogs was still warming
                    team_match_slugs: list[str] = []
                    for st in kr_map.values():
                        ms = (st.get("recent_30d") or {}).get("match_slugs") or []
                        if ms:
                            team_match_slugs = ms
                            break
                    if team_match_slugs:
                        for p_name, slug in slug_list:
                            if not kr_map.get(p_name, {}).get("recent_30d"):
                                backfilled = kr.backfill_recent_from_match_boxscores(team_match_slugs, slug)
                                if backfilled:
                                    kr_map.setdefault(p_name, {})["recent_30d"] = backfilled
            except Exception as e:
                print(f"  └─ ⚠️ [HLTV KashRock team stats fallback error]: {e}")

            if len(targets) == 1 and not team_label:
                p_name, p_href = targets[0]
                block = self._fetch_single_player(p_name, p_href, kr_stats=kr_map.get(p_name) if kr_map else None)
                if block.startswith("[PLAYER STATS:"):
                    return block + props_section
                return f"[PLAYER STATS: {p_name} ({p_href})]\n{block}\n" + props_section

            # Multiple players (e.g. full team roster like Team Falcons) -> fetch concurrently via shared worker pool!
            header = f"[TEAM PLAYER STATS: {team_label or target_str}]\n"
            futures = [
                submit_to_worker(self._fetch_single_player, p_name, p_href, kr_map.get(p_name) if kr_map else None)
                for p_name, p_href in targets
            ]
            blocks = [f.result() for f in futures]

            return header + "\n".join(blocks) + "\n" + props_section
        except Exception as e:
            return f"[PLAYER STATS]\nError fetching player stats: {e}\n\n"
