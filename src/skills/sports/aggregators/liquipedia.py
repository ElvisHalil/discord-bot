import re
import time
from datetime import datetime, timezone
from typing import Optional
from bs4 import BeautifulSoup
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.skills.sports.catalogs import (
    LIQUIPEDIA_GENERIC_EVENT_QUERIES,
    LIQUIPEDIA_SLUG_STOPWORDS,
)
from src.utils.http import fetch_json

# 5-minute TTL cache for Liquipedia tournament & event lookups
_LIQUIPEDIA_CACHE: dict[str, tuple[float, object]] = {}
_LIQUIPEDIA_CACHE_TTL = 300.0


def clear_liquipedia_cache() -> None:
    """Clears in-memory Liquipedia tournament and event caches."""
    _LIQUIPEDIA_CACHE.clear()


def _cached_fetch_json(url: str, timeout: int = 15) -> dict:
    now = time.monotonic()
    if url in _LIQUIPEDIA_CACHE:
        ts, cached_data = _LIQUIPEDIA_CACHE[url]
        if now - ts < _LIQUIPEDIA_CACHE_TTL and isinstance(cached_data, dict):
            return cached_data
    data = fetch_json(url, timeout=timeout)
    if isinstance(data, dict) and "parse" in data:
        _LIQUIPEDIA_CACHE[url] = (now, data)
    return data


class LiquipediaAggregator(BaseSportsAggregator):
    """Aggregator for Counter-Strike esports tournament, schedule, group, and roster data from Liquipedia."""

    def __init__(self):
        self._known_events: list[dict] = []

    def _parse_table_rows(self, tbl) -> list[dict]:
        """Parses rows from an S-Tier tournament table into clean dictionary records."""
        items = []
        if not tbl:
            return items
        for tr in tbl.find_all('tr'):
            cols = tr.find_all(['td', 'th'])
            if len(cols) < 5:
                continue
            if any('Tournament' in c.get_text() for c in cols[:3]):
                continue

            slug = ''
            for a in tr.find_all('a'):
                href = a.get('href', '')
                if href.startswith('/counterstrike/'):
                    cand = href.replace('/counterstrike/', '')
                    if 'Counter-Strike_2' not in cand and 'Category:' not in cand:
                        slug = cand
                        break

            name = cols[2].get_text(strip=True) if len(cols) > 2 else ''
            date = cols[3].get_text(strip=True) if len(cols) > 3 else ''
            prize = cols[4].get_text(strip=True) if len(cols) > 4 else ''
            loc = cols[5].get_text(strip=True) if len(cols) > 5 else ''
            winner = cols[7].get_text(strip=True) if len(cols) > 7 else ''
            runner_up = cols[8].get_text(strip=True) if len(cols) > 8 else ''

            if not name or winner.lower() == 'cancelled':
                continue

            items.append({
                'name': name,
                'date': date,
                'prize': prize,
                'location': loc,
                'winner': winner,
                'runner_up': runner_up,
                'slug': slug
            })
        return items

    def _find_table_for_year(self, soup: BeautifulSoup, year: int):
        """Finds the S-Tier tournament table for a specific year."""
        for h in soup.find_all(['h2', 'h3']):
            if str(year) in h.get_text():
                tbl = h.find_next('table')
                if tbl:
                    return tbl
        return None

    def resolve_event_slug(self, query_or_slug: Optional[str] = None) -> Optional[str]:
        """Resolves a natural tournament name (e.g. 'Thunderpick', 'PGL Bucharest') or empty string to a Liquipedia event slug."""
        q = (query_or_slug or "").strip()
        if q and "/" in q and " " not in q:
            return q.lstrip("/")

        if not self._known_events:
            try:
                self.get_s_tier_tournaments(include_next_event_details=False)
            except Exception:
                pass

        if not self._known_events:
            return q if q else None

        q_low = q.lower()
        if not q_low or q_low in LIQUIPEDIA_GENERIC_EVENT_QUERIES:
            return self._known_events[0].get("slug")

        # Match against known tournament names or slugs
        raw_tokens = [w for w in re.findall(r'[a-z0-9]+', q_low) if len(w) >= 3 and w not in LIQUIPEDIA_SLUG_STOPWORDS]
        q_tokens = [w[:-1] if (len(w) > 4 and w.endswith("s")) else w for w in raw_tokens]
        for ev in self._known_events:
            ev_text = f"{ev.get('name', '')} {ev.get('slug', '')} {ev.get('location', '')}".lower()
            if q_low in ev_text or (q_tokens and all(tok in ev_text for tok in q_tokens)):
                return ev.get("slug")
        for ev in self._known_events:
            ev_text = f"{ev.get('name', '')} {ev.get('slug', '')} {ev.get('location', '')}".lower()
            if q_tokens and any(tok in ev_text for tok in q_tokens):
                return ev.get("slug")

        return q if "/" in q else self._known_events[0].get("slug")

    def get_s_tier_tournaments(self, include_next_event_details: bool = True) -> str:
        """Fetches upcoming, ongoing, and recently completed S-Tier tournaments from Liquipedia."""
        print("  └─ 🏆 [Liquipedia]: Fetching S-Tier Tournaments...")
        try:
            url = 'https://liquipedia.net/counterstrike/api.php?action=parse&page=S-Tier_Tournaments&format=json'
            data = _cached_fetch_json(url, timeout=10)
            soup = BeautifulSoup(data['parse']['text']['*'], 'html.parser')

            current_year = datetime.now().year
            curr_tbl = self._find_table_for_year(soup, current_year)
            curr_items = self._parse_table_rows(curr_tbl)

            # If current year table not found, fallback to first available table
            if not curr_items:
                first_tbl = soup.find('table')
                curr_items = self._parse_table_rows(first_tbl)

            upcoming = [x for x in curr_items if x['winner'] == 'TBD' or not x['winner']]
            completed = [x for x in curr_items if x['winner'] and x['winner'] != 'TBD']

            # Liquipedia lists tournaments reverse-chronologically (Dec down to Jan).
            # Reversing upcoming brings the chronologically nearest upcoming event to index 0.
            scheduled = [u for u in reversed(upcoming) if u['date'].lower() != 'postponed']
            postponed = [u for u in reversed(upcoming) if u['date'].lower() == 'postponed']
            upcoming_sorted = scheduled + postponed

            # If near year-end and few upcoming events left, supplement with next year
            if len(upcoming_sorted) < 3:
                next_tbl = self._find_table_for_year(soup, current_year + 1)
                if next_tbl:
                    next_items = self._parse_table_rows(next_tbl)
                    next_up = [x for x in next_items if x['winner'] == 'TBD' or not x['winner']]
                    upcoming_sorted.extend(reversed(next_up))

            # If early in the year and few completed events, supplement with prior year
            if len(completed) < 3:
                prev_tbl = self._find_table_for_year(soup, current_year - 1)
                if prev_tbl:
                    prev_items = self._parse_table_rows(prev_tbl)
                    prev_comp = [x for x in prev_items if x['winner'] and x['winner'] != 'TBD']
                    completed.extend(prev_comp)

            self._known_events = [x for x in (upcoming_sorted + completed) if x.get("slug")]

            output = f"[CURRENT & UPCOMING S-TIER TOURNAMENTS ({current_year})]\n"
            if upcoming_sorted:
                for item in upcoming_sorted[:5]:
                    slug = item['slug']
                    ev_url = f"https://liquipedia.net/counterstrike/{slug}" if slug else ""
                    url_str = f" | Link: <{ev_url}>" if ev_url else ""
                    tool_str = f" (event_page: '{slug}')" if slug else ""
                    prize_str = f" | Prize: {item['prize']}" if item['prize'] else ""
                    loc_str = f" | Location: {item['location']}" if item['location'] else ""
                    output += f"- {item['name']} ({item['date']}){prize_str}{loc_str}{url_str}{tool_str}\n"
            else:
                output += "No upcoming tournaments found.\n"

            output += f"\n[RECENT TOURNAMENT CHAMPIONS ({current_year})]\n"
            if completed:
                for item in completed[:4]:
                    slug = item['slug']
                    ev_url = f"https://liquipedia.net/counterstrike/{slug}" if slug else ""
                    url_str = f" | Link: <{ev_url}>" if ev_url else ""
                    tool_str = f" (event_page: '{slug}')" if slug else ""
                    runner_up_str = f" (Runner-up: {item['runner_up']})" if item['runner_up'] and item['runner_up'] != 'TBD' else ""
                    prize_str = f" | Prize: {item['prize']}" if item['prize'] else ""
                    output += f"- {item['name']} ({item['date']}) | Champion: **{item['winner']}**{runner_up_str}{prize_str}{url_str}{tool_str}\n"
            else:
                output += "No recent tournament results found.\n"

            # Automatically enrich with the nearest ongoing/upcoming tournament's schedule, groups, and rosters
            if include_next_event_details and upcoming_sorted and upcoming_sorted[0].get("slug"):
                nearest = upcoming_sorted[0]
                nearest_slug = nearest["slug"]
                try:
                    ev_details = self.get_event_info(nearest_slug, _only_if_structured=True)
                    if ev_details:
                        output += f"\n{ev_details}"
                except Exception:
                    pass

            return output + "\n"
        except Exception as e:
            return f"[TOURNAMENTS]\nError fetching tournaments: {e}\n\n"

    @staticmethod
    def _extract_opponent_name(opp_el) -> str:
        """Extracts a clean team name from a Liquipedia opponent element."""
        if not opp_el:
            return "TBD"
        for sel in (".team-name", ".team-bracketname", "a[title]"):
            el = opp_el.select_one(sel)
            if el:
                if el.name == "a" and el.get("title"):
                    title = el["title"].strip()
                    if title and not title.startswith("Special:"):
                        return re.sub(r'\s*\(page does not exist\)\s*$', '', title, flags=re.IGNORECASE)
                txt = el.get_text(strip=True)
                if txt:
                    return txt
        txt = opp_el.get_text(" ", strip=True)
        return txt if txt else "TBD"

    @staticmethod
    def _format_match_timestamp(raw_time_str: str, epoch_str: str) -> str:
        """Formats Liquipedia's match time alongside UTC and local time."""
        clean_raw = re.sub(r'\s+', ' ', (raw_time_str or "").strip())
        if not epoch_str or not str(epoch_str).isdigit():
            return clean_raw or "Time TBD"
        try:
            from src.utils.timezones import get_local_now, _short_tz_abbr
            ts = int(epoch_str)
            utc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            local_dt = get_local_now(now=utc_dt)
            local_abbr = _short_tz_abbr(local_dt)
            utc_str = utc_dt.strftime("%b %d, %H:%M UTC")
            local_str = f"{local_dt.strftime('%I:%M %p').lstrip('0')} {local_abbr}"
            if clean_raw:
                return f"{clean_raw} ({utc_str} | {local_str})"
            return f"{utc_str} ({local_str})"
        except Exception:
            return clean_raw or "Time TBD"

    def _parse_matches_from_soup(self, soup: BeautifulSoup) -> list[str]:
        """Extracts structured match schedule & results from Liquipedia .brkts-popup and .match-info elements."""
        matches = []
        seen_keys = set()

        for popup in soup.select(".brkts-popup"):
            timer_el = popup.select_one(".timer-object")
            raw_time = timer_el.get_text(" ", strip=True) if timer_el else ""
            epoch_str = timer_el.get("data-timestamp", "") if timer_el else ""
            sort_ts = int(epoch_str) if str(epoch_str).isdigit() else 9999999999

            opps = popup.select(".match-info-header-opponent")
            team1 = self._extract_opponent_name(opps[0]) if len(opps) > 0 else "TBD"
            team2 = self._extract_opponent_name(opps[1]) if len(opps) > 1 else "TBD"

            upper_el = popup.select_one(".match-info-header-scoreholder-upper")
            lower_el = popup.select_one(".match-info-header-scoreholder-lower")
            upper_txt = re.sub(r'\s+', ' ', upper_el.get_text(" ", strip=True)) if upper_el else "vs"
            lower_txt = re.sub(r'\s+', ' ', lower_el.get_text(" ", strip=True)) if lower_el else ""

            # Determine stage / round label
            stage_parts = []
            sec_el = popup.find_previous(["h2", "h3", "h4"])
            if sec_el:
                sec_txt = re.sub(r'\[.*?\]', '', sec_el.get_text(" ", strip=True)).strip()
                if sec_txt and sec_txt.lower() not in ("results", "overview", "matches"):
                    stage_parts.append(sec_txt)

            if popup.find_parent(class_="brkts-third-place-match"):
                stage_parts.append("Third Place Match")
            else:
                ml_header = popup.find_previous(class_=["brkts-matchlist-header", "brkts-matchlist-title"])
                if ml_header and popup.find_parent(class_="brkts-matchlist"):
                    ml_txt = re.sub(r'\b(?:Show|Hide)\b', '', ml_header.get_text(" ", strip=True)).strip()
                    if ml_txt and ml_txt not in stage_parts:
                        stage_parts.append(ml_txt)

            stage_label = " - ".join(stage_parts) if stage_parts else "Match"
            dedup_key = (epoch_str or raw_time, team1.lower(), team2.lower())
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            time_display = self._format_match_timestamp(raw_time, epoch_str)
            fmt_suffix = f" {lower_txt}" if lower_txt else ""
            if upper_txt.lower() == "vs" or not upper_txt:
                matchup_str = f"**{team1}** vs **{team2}**{fmt_suffix}"
            else:
                matchup_str = f"**{team1}** {upper_txt} **{team2}**{fmt_suffix}"

            # Extract map-by-map scores from .brkts-popup-body-grid-row if present
            map_summaries: list[str] = []
            for row in popup.select(".brkts-popup-body-grid-row"):
                map_link = row.select_one("a[title]")
                if not map_link:
                    continue
                m_name = map_link.get_text(" ", strip=True)
                if not m_name:
                    continue
                main_scores = [
                    s.get_text(strip=True)
                    for s in row.select(".brkts-popup-body-detailed-scores-main-score")
                    if s.get_text(strip=True).isdigit()
                ]
                if len(main_scores) >= 2:
                    s1, s2 = int(main_scores[0]), int(main_scores[1])
                    if s1 > s2:
                        map_summaries.append(f"{m_name}: {team1} {s1}-{s2}")
                    elif s2 > s1:
                        map_summaries.append(f"{m_name}: {team2} {s2}-{s1}")
                    else:
                        map_summaries.append(f"{m_name}: {s1}-{s2}")
                else:
                    map_summaries.append(f"{m_name}: Upcoming")

            maps_suffix = f" [Maps: {' | '.join(map_summaries)}]" if map_summaries else ""
            matches.append((sort_ts, f"- [{stage_label}] {time_display}: {matchup_str}{maps_suffix}"))

        # Fallback to .match-info ticker if no .brkts-popup elements exist on the page
        if not matches:
            for mi in soup.select(".match-info"):
                timer_el = mi.select_one(".timer-object")
                raw_time = timer_el.get_text(" ", strip=True) if timer_el else ""
                epoch_str = timer_el.get("data-timestamp", "") if timer_el else ""
                sort_ts = int(epoch_str) if str(epoch_str).isdigit() else 9999999999

                stage_el = mi.select_one(".match-info-stage")
                stage_label = stage_el.get_text(" ", strip=True) if stage_el else "Match"
                rows = mi.select(".match-info-opponent-row")
                team1 = self._extract_opponent_name(rows[0]) if len(rows) > 0 else "TBD"
                team2 = self._extract_opponent_name(rows[1]) if len(rows) > 1 else "TBD"

                dedup_key = (epoch_str or raw_time, team1.lower(), team2.lower())
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                time_display = self._format_match_timestamp(raw_time, epoch_str)
                matches.append((sort_ts, f"- [{stage_label}] {time_display}: **{team1}** vs **{team2}**"))

        matches.sort(key=lambda x: x[0])
        return [m[1] for m in matches]

    def _parse_groups_from_soup(self, soup: BeautifulSoup) -> list[str]:
        """Extracts Group Stage tables and standings records (e.g. Group A, Group B) from Liquipedia."""
        group_lines = []
        for g_tbl in soup.select("table.grouptable"):
            header_el = g_tbl.select_one("th")
            if not header_el:
                continue
            raw_header = re.sub(r'\bWeek\s+\d+\b', '', header_el.get_text(" | ", strip=True).split("|")[0]).strip()
            if not raw_header:
                continue
            teams = []
            seen_names = set()
            for tr in g_tbl.select("tr"):
                team_el = tr.select_one(".block-team a[title], .team-template-text a[title]")
                if team_el and team_el.get("title"):
                    t_name = re.sub(r'\s*\(page does not exist\)\s*$', '', team_el["title"].strip(), flags=re.IGNORECASE)
                    if t_name and t_name not in seen_names:
                        seen_names.add(t_name)
                        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                        if len(tds) >= 2 and re.match(r'^\d+-\d+$', tds[1]):
                            wl = tds[1]
                            rds = f", Rds: {tds[2]}" if len(tds) >= 3 and tds[2] else ""
                            diff = f" ({tds[3]})" if len(tds) >= 4 and tds[3] else ""
                            teams.append(f"{t_name} [{wl}{rds}{diff}]")
                        else:
                            teams.append(t_name)
            if teams:
                group_lines.append(f"- **{raw_header}**: {', '.join(teams)}")
        return group_lines

    def get_event_info(self, event_page: Optional[str] = None, _only_if_structured: bool = False) -> str:
        """Fetches detailed info about a specific event, including structured match schedule, groups, and active team rosters."""
        resolved_page = self.resolve_event_slug(event_page) if not _only_if_structured else event_page
        if not resolved_page:
            return "[EVENT INFO]\nError: Please provide 'event_page' (e.g. 'Thunderpick/World_Championship/2026').\n\n"

        print(f"  └─ 📖 [Liquipedia]: Fetching Event Info for '{resolved_page}'...")
        try:
            url = f'https://liquipedia.net/counterstrike/api.php?action=parse&page={resolved_page}&format=json'
            data = _cached_fetch_json(url, timeout=15)
            if 'parse' not in data:
                return "" if _only_if_structured else f"[EVENT INFO]\nError parsing event page '{resolved_page}'. It might not exist.\n\n"

            soup = BeautifulSoup(data['parse']['text']['*'], 'html.parser')
            teams_list = []
            former_teams_list = []
            roster_lines = []

            for card in soup.select('.team-participant-card'):
                team_name_el = card.select_one('.team-participant-card__opponent a')
                team_name = team_name_el.get('title', '').strip() if team_name_el else 'Unknown'
                if not team_name or team_name == 'Unknown':
                    continue

                # Check if this card is inside a collapsed "Former Participants" block
                collapsed_parent = card.find_parent('div', class_='collapsed')
                is_former = False
                if collapsed_parent:
                    parent_txt = collapsed_parent.get_text(" ", strip=True)[:80].lower()
                    if "former" in parent_txt or "withdrawn" in parent_txt or "replaced" in parent_txt:
                        is_former = True

                if is_former:
                    if team_name not in former_teams_list:
                        former_teams_list.append(team_name)
                    continue

                players = []
                coach = None
                for member in card.select('.team-participant-card__member'):
                    name_el = member.select_one('.name a')
                    name = name_el.get_text(strip=True) if name_el else ''
                    role_el = member.select_one('.team-participant-card__member-role-right')
                    role = role_el.get_text(strip=True) if role_el else ''
                    if 'coach' in role.lower():
                        coach = name
                    elif name:
                        players.append(name)

                if team_name not in teams_list:
                    teams_list.append(team_name)
                    coach_str = f" | Coach: {coach}" if coach else ""
                    roster_lines.append(f"- **{team_name}**: {', '.join(players)}{coach_str}")

            group_lines = self._parse_groups_from_soup(soup)
            match_lines = self._parse_matches_from_soup(soup)

            if _only_if_structured and not (teams_list or group_lines or match_lines):
                return ""

            # Remove noisy or already-parsed elements before extracting concise event overview text
            for x in soup.select('.navbox, .toc, script, style, .mw-editsection, .brkts-popup, .match-info, .team-participant-card, table.grouptable'):
                x.decompose()

            text = soup.get_text('\n', strip=True)
            if len(text) > 3000:
                text = text[:2000] + "\n...[CONTENT TRUNCATED]...\n" + text[-1000:]

            output = f"[EVENT INFO: {resolved_page} (Link: <https://liquipedia.net/counterstrike/{resolved_page}>)]\n"
            if teams_list:
                output += f"=== PARTICIPATING TEAMS ({len(teams_list)} Active Teams) ===\n" + ", ".join(teams_list) + "\n"
                if former_teams_list:
                    output += f"Withdrawn / Replaced Teams: {', '.join(former_teams_list)}\n"
                output += "\n"
            if group_lines:
                output += "=== GROUP STAGE SEEDING ===\n" + "\n".join(group_lines) + "\n\n"
            if match_lines:
                output += "=== MATCH SCHEDULE & RESULTS (Exact Times) ===\n" + "\n".join(match_lines) + "\n\n"
            if roster_lines:
                output += "=== TOURNAMENT ROSTERS (Active Players & Coaches) ===\n" + "\n".join(roster_lines) + "\n\n"
            output += "=== EVENT DETAILS & FORMAT ===\n" + text + "\n"
            return output + "\n"
        except Exception as e:
            return "" if _only_if_structured else f"[EVENT INFO]\nError fetching event: {e}\n\n"

