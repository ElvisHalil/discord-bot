from bs4 import BeautifulSoup
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.utils.http import fetch_json

class LiquipediaAggregator(BaseSportsAggregator):
    """Aggregator for Counter-Strike esports tournament and roster data from Liquipedia using centralized stealth client."""

    def get_s_tier_tournaments(self) -> str:
        """Fetches upcoming and ongoing S-Tier tournaments from Liquipedia."""
        print("  └─ 🏆 [Liquipedia]: Fetching S-Tier Tournaments...")
        try:
            url = 'https://liquipedia.net/counterstrike/api.php?action=parse&page=S-Tier_Tournaments&format=json'
            data = fetch_json(url, timeout=10)
            soup = BeautifulSoup(data['parse']['text']['*'], 'html.parser')
            
            output = "[UPCOMING & ONGOING S-TIER TOURNAMENTS]\n"
            rows = soup.find_all('tr')
            
            count = 0
            for tr in rows:
                cols = tr.find_all(['td', 'th'])
                if len(cols) >= 3 and count < 10:
                    row_text = " | ".join([c.get_text(strip=True) for c in cols])
                    if "Prize Pool" not in row_text and len(row_text) > 10:
                        link = ''
                        for c in cols:
                            a_tag = c.find('a')
                            if a_tag and 'href' in a_tag.attrs and '/counterstrike/' in a_tag['href']:
                                candidate = a_tag['href'].replace('/counterstrike/', '')
                                if 'Counter-Strike_2' not in candidate and 'Category:' not in candidate:
                                    link = candidate
                                    break
                        event_info_str = f" [event_page: {link}]" if link else ""
                        output += f"- {row_text}{event_info_str}\n"
                        count += 1
            if count == 0:
                output += "No tournament data could be parsed.\n"
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
            roster_lines = []
            for card in soup.select('.team-participant-card'):
                team_name_el = card.select_one('.team-participant-card__opponent a')
                team_name = team_name_el['title'] if team_name_el else 'Unknown'
                players = [p.get_text(strip=True) for p in card.select('.team-participant-card__member .name a')]
                if players:
                    roster_lines.append(f"{team_name}: {', '.join(players)}")
            
            for x in soup.select('.navbox, .toc, script, style, .mw-editsection'):
                x.decompose()
            
            text = soup.get_text('\n', strip=True)
            if len(text) > 8000:
                text = text[:4000] + "\n...[CONTENT TRUNCATED]...\n" + text[-4000:]
                
            output = f"[EVENT INFO: {event_page}]\n{text}\n"
            if roster_lines:
                output += "\n--- TOURNAMENT ROSTERS ---\n" + "\n".join(roster_lines) + "\n"
            return output + "\n"
        except Exception as e:
            return f"[EVENT INFO]\nError fetching event: {e}\n\n"
