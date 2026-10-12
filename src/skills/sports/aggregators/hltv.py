import cloudscraper
from bs4 import BeautifulSoup
from src.skills.sports.aggregators.base import BaseSportsAggregator

class HltvAggregator(BaseSportsAggregator):
    """Aggregator for HLTV team rankings and CS player statistics."""

    def __init__(self):
        self.scraper = cloudscraper.create_scraper(browser={'browser': 'firefox', 'platform': 'windows', 'mobile': False})

    def get_rankings(self, limit: int = 15) -> str:
        """Fetches the current world team rankings and rosters from HLTV."""
        print("  └─ 📈 [HLTV]: Fetching World Rankings...")
        try:
            r = self.scraper.get('https://www.hltv.org/ranking/teams/', timeout=30)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            output = f"[HLTV TOP {limit} WORLD RANKINGS]\n"
            team_divs = soup.select('.ranked-team')
            if not team_divs:
                output += "Could not extract HLTV rankings (might be blocked by Cloudflare).\n"
            else:
                for i, team in enumerate(team_divs[:limit]):
                    name_el = team.select_one('.name')
                    name = name_el.get_text(strip=True) if name_el else "Unknown"
                    
                    players = []
                    for a in team.select('a[href*="/player/"]'):
                        p_name = a.get_text(strip=True)
                        if p_name:
                            players.append(f"{p_name} ({a['href']})")
                    
                    players_str = " | ".join(players[:5]) if players else "No roster found"
                    output += f"{i+1}. {name} - Roster: {players_str}\n"
            return output + "\n"
        except Exception as e:
            return f"[RANKINGS]\nError fetching HLTV rankings: {e}\n\n"

    def get_player_stats(self, player_url: str) -> str:
        """Fetches statistics for a specific player from HLTV."""
        if not player_url:
            return "[PLAYER STATS]\nError: Please provide 'player_url' (e.g. '/player/11269/b1t').\n\n"

        print(f"  └─ 👤 [HLTV]: Fetching Player Stats for '{player_url}'...")
        try:
            full_url = f"https://www.hltv.org{player_url}"
            r = self.scraper.get(full_url, timeout=30)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            stats_texts = []
            for stat_div in soup.select('.playerProfile, .player-stat, .stat, .statistics, .summaryStatBreakdown'):
                txt = stat_div.get_text(" ", strip=True)
                if txt and txt not in stats_texts:
                    stats_texts.append(txt)
            
            if not stats_texts:
                return f"[PLAYER STATS: {player_url}]\nCould not extract stats (might be blocked by Cloudflare or incorrect URL).\n\n"
            
            text = "\n".join(stats_texts)
            if len(text) > 4000:
                text = text[:4000]
            return f"[PLAYER STATS: {player_url}]\n{text}\n\n"
        except Exception as e:
            return f"[PLAYER STATS]\nError fetching player stats: {e}\n\n"
