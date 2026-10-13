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
            "nfl": "americanfootball_nfl",
            "football": "americanfootball_nfl",
            "american football": "americanfootball_nfl",
            "ncaaf": "americanfootball_ncaaf",
            "college football": "americanfootball_ncaaf",
            "cfl": "americanfootball_cfl",
            "nba": "basketball_nba",
            "basketball": "basketball_nba",
            "wnba": "basketball_wnba",
            "euroleague": "basketball_euroleague",
            "mlb": "baseball_mlb",
            "baseball": "baseball_mlb",
            "nhl": "icehockey_nhl",
            "hockey": "icehockey_nhl",
            "epl": "soccer_epl",
            "premier league": "soccer_epl",
            "soccer": "soccer_epl",
            "champions league": "soccer_uefa_champs_league",
            "ucl": "soccer_uefa_champs_league",
            "la liga": "soccer_spain_la_liga",
            "laliga": "soccer_spain_la_liga",
            "bundesliga": "soccer_germany_bundesliga",
            "serie a": "soccer_italy_serie_a",
            "seriea": "soccer_italy_serie_a",
            "ligue 1": "soccer_france_ligue_one",
            "mls": "soccer_usa_mls",
            "mma": "mma_mixed_martial_arts",
            "ufc": "mma_mixed_martial_arts",
            "boxing": "boxing_boxing",
        }

        # Resolve candidate sport keys to check
        sport_clean = sport.lower().strip()
        if sport_clean in ("cs", "cs2", "csgo", "esports"):
            return None

        candidate_keys = []
        if sport_clean in sport_map:
            candidate_keys.append(sport_map[sport_clean])
        else:
            # Check if any sport keyword is inside query or sport string
            for k, v in sport_map.items():
                if k in sport_clean or k in query.lower():
                    if v not in candidate_keys:
                        candidate_keys.append(v)
            if not candidate_keys:
                candidate_keys = ["americanfootball_nfl", "soccer_epl", "basketball_nba", "baseball_mlb", "icehockey_nhl"]

        q_lower = query.lower()
        ignore_words = {"vs", "versus", "the", "and", "odds", "match", "game", "moneyline", "spread", "who", "will", "win"}
        query_tokens = [w for w in re.split(r'[\s\-_/]+', q_lower) if len(w) >= 3 and w not in ignore_words]

        for sport_key in candidate_keys:
            url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={api_key}&regions=us,eu,uk&markets=h2h"
            try:
                data = fetch_json(url, timeout=10)
                if not isinstance(data, list) or not data:
                    continue

                for game in data:
                    home = game.get("home_team", "")
                    away = game.get("away_team", "")
                    home_l, away_l = home.lower(), away.lower()

                    matched = False
                    if home_l in q_lower or away_l in q_lower:
                        matched = True
                    elif query_tokens and any(t in home_l or t in away_l for t in query_tokens):
                        matched = True

                    if matched and game.get("bookmakers"):
                        bm = game["bookmakers"][0]
                        markets = bm.get("markets", [])
                        if markets and markets[0].get("outcomes"):
                            outcomes = markets[0]["outcomes"]
                            lines = []
                            favorite = None
                            min_price = 999.0
                            for out in outcomes:
                                name = out.get("name", "")
                                price = float(out.get("price", 0))
                                if 1.0 < price < min_price:
                                    min_price = price
                                    favorite = name
                                lines.append(f"- **{name}**: {price:.2f} (American: {decimal_to_american(price)}) | Implied prob: {implied_probability(price)}")

                            fav_str = f"Favorite: **{favorite}**\n" if favorite and favorite.lower() != "draw" else ""
                            return (
                                f"[LIVE BETTING ODDS: {home} vs {away}]\n"
                                f"Sport / League: {game.get('sport_title', sport_key)}\n"
                                f"Scheduled Start: {game.get('commence_time', '')}\n"
                                f"{fav_str}"
                                f"Bookmaker Consensus ({bm.get('title', 'Market')}):\n"
                                + "\n".join(lines) + "\n\n"
                                "Note: Odds fluctuate leading up to match start.\n\n"
                            )
            except Exception as e:
                # Silently ignore 404s on inactive candidate leagues
                if "404" not in str(e):
                    print(f"  └─ ⚠️ [The Odds API ({sport_key})]: {e}")
        return None

    CS_TEAMS = (
        "falcons", "team falcons", "nip", "ninjas in pyjamas", "spirit", "team spirit",
        "vitality", "team vitality", "mouz", "mousesports", "furia", "faze", "g2",
        "navi", "natus vincere", "astralis", "complexity", "mongolz", "heroic",
        "eternal fire", "virtus.pro", "9z", "aurora", "betboom", "parivision",
        "thunderpick", "blast", "pgl", "iem", "esl", "cs", "cs2", "csgo"
    )

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

        # Extract potential stake from query (e.g. $200, 200 dollars)
        stake_match = re.search(r'\$([0-9]+(?:\.[0-9]{2})?)|\b([0-9]+(?:\.[0-9]{2})?)\s*(?:dollars|bucks|usd)', query_clean, re.IGNORECASE)
        stake = float(stake_match.group(1) or stake_match.group(2)) if stake_match else None

        # Detect Counter-Strike / Esports teams and context
        q_lower = query_clean.lower()
        is_cs = (
            sport_clean in ("cs", "cs2", "csgo", "esports") or
            any(t in q_lower for t in self.CS_TEAMS) or
            any(t in sport_clean for t in self.CS_TEAMS)
        )
        # Avoid misclassifying explicit NFL requests
        if is_cs and not any(k in q_lower for k in ("nfl", "atlanta falcons", "american football")):
            sport_clean = "cs2"

        print(f"  └─ 🎲 [Odds]: Fetching betting odds for '{query_clean}' (Sport: {sport_clean})...")

        # 1. For Counter-Strike/Esports, scrape HLTV betting analytics first
        if sport_clean in ["cs", "cs2", "csgo", "esports"]:
            hltv_odds = self._fetch_esports_hltv_odds(query_clean, stake=stake)
            if hltv_odds:
                return hltv_odds

        # 2. Check The Odds API if key is present
        api_odds = self._fetch_from_the_odds_api(query_clean, sport_clean)
        if api_odds:
            return api_odds

        # 3. Web search consensus
        return self._fetch_web_odds_search(query_clean, sport_clean)

