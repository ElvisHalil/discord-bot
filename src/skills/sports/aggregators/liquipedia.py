from datetime import datetime
from bs4 import BeautifulSoup
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.utils.http import fetch_json

class LiquipediaAggregator(BaseSportsAggregator):
    """Aggregator for Counter-Strike esports tournament and roster data from Liquipedia using centralized stealth client."""

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

    def get_s_tier_tournaments(self) -> str:
        """Fetches upcoming, ongoing, and recently completed S-Tier tournaments from Liquipedia."""
        print("  └─ 🏆 [Liquipedia]: Fetching S-Tier Tournaments...")
        try:
            url = 'https://liquipedia.net/counterstrike/api.php?action=parse&page=S-Tier_Tournaments&format=json'
            data = fetch_json(url, timeout=10)
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

            output = f"[CURRENT & UPCOMING S-TIER TOURNAMENTS ({current_year})]\n"
            if upcoming_sorted:
                for item in upcoming_sorted[:5]:
                    slug = item['slug']
                    url = f"https://liquipedia.net/counterstrike/{slug}" if slug else ""
                    url_str = f" | Link: <{url}>" if url else ""
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
                    url = f"https://liquipedia.net/counterstrike/{slug}" if slug else ""
                    url_str = f" | Link: <{url}>" if url else ""
                    tool_str = f" (event_page: '{slug}')" if slug else ""
                    runner_up_str = f" (Runner-up: {item['runner_up']})" if item['runner_up'] and item['runner_up'] != 'TBD' else ""
                    prize_str = f" | Prize: {item['prize']}" if item['prize'] else ""
                    output += f"- {item['name']} ({item['date']}) | Champion: **{item['winner']}**{runner_up_str}{prize_str}{url_str}{tool_str}\n"
            else:
                output += "No recent tournament results found.\n"

            return output + "\n"
        except Exception as e:
            return f"[TOURNAMENTS]\nError fetching tournaments: {e}\n\n"

    def get_event_info(self, event_page: str) -> str:
        """Fetches detailed info about a specific event, including current matches and full team rosters."""
        if not event_page:
            return "[EVENT INFO]\nError: Please provide 'event_page' (e.g. 'ESL/Pro_League/Season_24').\n\n"

        print(f"  └─ 📖 [Liquipedia]: Fetching Event Info for '{event_page}'...")
        try:
            url = f'https://liquipedia.net/counterstrike/api.php?action=parse&page={event_page}&format=json'
            data = fetch_json(url, timeout=15)
            if 'parse' not in data:
                return f"[EVENT INFO]\nError parsing event page. It might not exist.\n\n"

            soup = BeautifulSoup(data['parse']['text']['*'], 'html.parser')
            teams_list = []
            roster_lines = []
            for card in soup.select('.team-participant-card'):
                team_name_el = card.select_one('.team-participant-card__opponent a')
                team_name = team_name_el['title'] if team_name_el else 'Unknown'
                if team_name == 'Unknown':
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

                teams_list.append(team_name)
                coach_str = f" | Coach: {coach}" if coach else ""
                roster_lines.append(f"- **{team_name}**: {', '.join(players)}{coach_str}")

            for x in soup.select('.navbox, .toc, script, style, .mw-editsection'):
                x.decompose()

            text = soup.get_text('\n', strip=True)
            if len(text) > 8000:
                text = text[:4000] + "\n...[CONTENT TRUNCATED]...\n" + text[-4000:]

            output = f"[EVENT INFO: {event_page}]\n"
            if teams_list:
                output += "=== PARTICIPATING TEAMS ===\n" + ", ".join(teams_list) + "\n\n"
            if roster_lines:
                output += "=== TOURNAMENT ROSTERS (Active Players & Coaches) ===\n" + "\n".join(roster_lines) + "\n\n"
            output += "=== SCHEDULE & EVENT DETAILS ===\n" + text + "\n"
            return output + "\n"
        except Exception as e:
            return f"[EVENT INFO]\nError fetching event: {e}\n\n"
