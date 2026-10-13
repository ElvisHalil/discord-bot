import os
import re
from typing import Optional
from src.skills.sports.aggregators.base import BaseSportsAggregator
from src.utils.http import fetch_soup, fetch_json


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
    """Centralized aggregator for sports and esports betting odds, moneylines, and probabilities."""

    def _fetch_from_the_odds_api(self, query: str, sport: str) -> Optional[str]:
        """Fetches structured odds if THE_ODDS_API_KEY is configured."""
        api_key = os.getenv("THE_ODDS_API_KEY")
        if not api_key:
            return None

        # Map common sport names to The Odds API sport keys
        sport_map = {
            "cs": "esports_cs2",
            "cs2": "esports_cs2",
            "csgo": "esports_cs2",
            "f1": "motorsport_f1",
            "formula1": "motorsport_f1",
            "nfl": "americanfootball_nfl",
            "nba": "basketball_nba",
            "epl": "soccer_epl",
            "soccer": "soccer_epl",
        }
        sport_key = sport_map.get(sport.lower(), "upcoming")
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={api_key}&regions=us,eu&markets=h2h"

        try:
            data = fetch_json(url, timeout=10)
            if not isinstance(data, list) or not data:
                return None

            q_lower = query.lower()
            for game in data:
                home = game.get("home_team", "")
                away = game.get("away_team", "")
                if (home.lower() in q_lower or away.lower() in q_lower) and game.get("bookmakers"):
                    bm = game["bookmakers"][0]
                    markets = bm.get("markets", [])
                    if markets and markets[0].get("outcomes"):
                        outcomes = markets[0]["outcomes"]
                        lines = []
                        for out in outcomes:
                            name = out.get("name", "")
                            price = float(out.get("price", 0))
                            lines.append(f"- **{name}**: {price:.2f} (American: {decimal_to_american(price)}) | Implied prob: {implied_probability(price)}")
                        return (
                            f"[LIVE BETTING ODDS: {home} vs {away}]\n"
                            f"Sport/League: {game.get('sport_title', sport)}\n"
                            f"Commence Time: {game.get('commence_time', '')}\n"
                            f"Bookmaker: {bm.get('title', 'Consensus')}\n"
                            + "\n".join(lines) + "\n\n"
                        )
        except Exception as e:
            print(f"  └─ ⚠️ [The Odds API]: {e}")
        return None

    def _fetch_esports_hltv_odds(self, query: str) -> Optional[str]:
        """Finds and parses live HLTV betting analytics for Counter-Strike matches."""
        try:
            from src.tools.web import search_web
            search_query = f"{query} odds hltv"
            search_res = search_web(search_query)

            hltv_url = None
            for line in search_res.split("\n"):
                if "hltv.org/betting/analytics/" in line:
                    match = re.search(r'https://www.hltv.org/betting/analytics/[^\s)]+', line)
                    if match:
                        hltv_url = match.group(0)
                        break

            if not hltv_url:
                return None

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

                # Match favorite against parsed team names
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
                out += f"Market Source: HLTV Betting Analytics (<{hltv_url}>)\n"
                out += f"Note: Betting lines reflect bookmaker consensus and fluctuate prior to match start.\n\n"
                return out

        except Exception as e:
            print(f"  └─ ⚠️ [HLTV Odds Scraper Error]: {e}")
        return None

    def _fetch_web_odds_search(self, query: str, sport: str) -> str:
        """Falls back to web search synthesis to find consensus odds across major sportsbooks."""
        from src.tools.web import search_web
        search_query = f"{query} {sport} match odds betting line moneyline"
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
                f"Sport/Category: {sport}\n"
                f"Market Information:\n{snippets}\n\n"
                "Note: Verify current betting lines with licensed sportsbooks before placing wagers.\n\n"
            )
        else:
            return (
                f"[BETTING ODDS: {query}]\n"
                f"No verified betting lines or active odds were found for '{query}'. "
                "Odds typically open closer to the scheduled match start time.\n\n"
            )

    def get_odds(self, query: str, sport: Optional[str] = "esports") -> str:
        """Fetches live match betting odds, moneyline lines, and implied win probabilities."""
        if not query or not query.strip():
            return "[BETTING ODDS]\nPlease specify the matchup or event (e.g. 'Falcons vs Ninjas in Pyjamas', 'Verstappen F1').\n\n"

        query_clean = query.strip()
        sport_clean = (sport or "esports").strip().lower()
        print(f"  └─ 🎲 [Odds]: Fetching betting odds for '{query_clean}' (Sport: {sport_clean})...")

        # 1. Check The Odds API if key is present
        api_odds = self._fetch_from_the_odds_api(query_clean, sport_clean)
        if api_odds:
            return api_odds

        # 2. For Counter-Strike/Esports, scrape HLTV betting analytics
        if sport_clean in ["cs", "cs2", "csgo", "esports"]:
            hltv_odds = self._fetch_esports_hltv_odds(query_clean)
            if hltv_odds:
                return hltv_odds

        # 3. Web search consensus
        return self._fetch_web_odds_search(query_clean, sport_clean)
