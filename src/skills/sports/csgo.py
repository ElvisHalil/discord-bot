import os
import requests
import cloudscraper
from bs4 import BeautifulSoup

def skill_csgo_esports(query_type: str = 'all', event_page: str = None, player_url: str = None) -> str:
    """
    Use this skill to fetch Counter-Strike (CS) esports information.
    query_type can be:
      - 'tournaments': Gets upcoming and ongoing S-Tier tournaments from Liquipedia.
      - 'event_info': Gets detailed info about a specific event, including CURRENT MATCHES and full TEAM ROSTERS (requires event_page).
      - 'rankings': Gets the current world team rankings from HLTV, including team rosters.
      - 'player_stats': Gets stats for a specific player (requires player_url).
      - 'live_streams': Checks if ESL is currently streaming a live match on YouTube.
      - 'all': Fetches 'tournaments', 'rankings', and 'live_streams'.
    """
    print(f"🎮 [Executing CS Esports Skill] Type: {query_type}")
    
    scraper = cloudscraper.create_scraper(browser={'browser': 'firefox', 'platform': 'windows', 'mobile': False})
    output = "=== COUNTER-STRIKE ESPORTS DATA ===\n\n"

    # 1. LIVE STREAMS
    if query_type in ['live_streams', 'all']:
        try:
            print("  └─ 🔴 Checking ESLCS YouTube for live matches...")
            api_key = os.getenv("YOUTUBE_API_KEY")
            if not api_key:
                output += "[LIVE STREAMS]\nError: YOUTUBE_API_KEY is missing.\n\n"
            else:
                channel_id = "UCPq2ETz4aAGo2Z-8JisDPIA"
                url = "https://www.googleapis.com/youtube/v3/search"
                params = {
                    'part': 'snippet',
                    'channelId': channel_id,
                    'eventType': 'live',
                    'type': 'video',
                    'key': api_key
                }
                r = requests.get(url, params=params, timeout=5)
                data = r.json()
                items = data.get('items', [])
                
                output += "[LIVE ESL MATCHES]\n"
                if not items:
                    output += "ESL is not currently broadcasting any live matches.\n"
                else:
                    for video in items:
                        title = video['snippet'].get('title', 'Unknown Title')
                        video_id = video['id'].get('videoId', '')
                        link = f"https://www.youtube.com/watch?v={video_id}"
                        output += f"- Title: {title}\n  Link: {link}\n"
                output += "\n"
        except Exception as e:
            output += f"[LIVE STREAMS]\nError fetching live streams: {e}\n\n"

    # 2. TOURNAMENTS
    if query_type in ['tournaments', 'all']:
        try:
            print("  └─ 🏆 Fetching Liquipedia Tournaments...")
            r = scraper.get('https://liquipedia.net/counterstrike/api.php?action=parse&page=S-Tier_Tournaments&format=json', timeout=8)
            soup = BeautifulSoup(r.json()['parse']['text']['*'], 'html.parser')
            
            output += "[UPCOMING & ONGOING S-TIER TOURNAMENTS]\n"
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
            output += "\n"
        except Exception as e:
            output += f"[TOURNAMENTS]\nError fetching tournaments: {e}\n\n"

    # 3. HLTV RANKINGS
    if query_type in ['rankings', 'all']:
        try:
            print("  └─ 📈 Fetching HLTV Rankings...")
            r = scraper.get('https://www.hltv.org/ranking/teams/', timeout=30)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            output += "[HLTV TOP 15 WORLD RANKINGS]\n"
            team_divs = soup.select('.ranked-team')
            if not team_divs:
                output += "Could not extract HLTV rankings (might be blocked by Cloudflare).\n"
            else:
                for i, team in enumerate(team_divs[:15]):
                    name_el = team.select_one('.name')
                    name = name_el.get_text(strip=True) if name_el else "Unknown"
                    
                    players = []
                    for a in team.select('a[href*="/player/"]'):
                        p_name = a.get_text(strip=True)
                        if p_name:
                            players.append(f"{p_name} ({a['href']})")
                    
                    players_str = " | ".join(players[:5]) if players else "No roster found"
                    output += f"{i+1}. {name} - Roster: {players_str}\n"
            output += "\n"
        except Exception as e:
            output += f"[RANKINGS]\nError fetching HLTV rankings: {e}\n\n"

    # 4. EVENT INFO
    if query_type == 'event_info':
        if not event_page:
            output += "[EVENT INFO]\nError: Please provide 'event_page' (e.g. 'ESL/Pro_League/Season_24').\n\n"
        else:
            try:
                print(f"  └─ 📖 Fetching Liquipedia Event: {event_page}...")
                r = scraper.get(f'https://liquipedia.net/counterstrike/api.php?action=parse&page={event_page}&format=json', timeout=15)
                data = r.json()
                if 'parse' in data:
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
                        
                    output += f"[EVENT INFO: {event_page}]\n{text}\n"
                    if roster_lines:
                        output += "\n--- TOURNAMENT ROSTERS ---\n" + "\n".join(roster_lines) + "\n"
                    output += "\n"
                else:
                    output += f"[EVENT INFO]\nError parsing event page. It might not exist.\n\n"
            except Exception as e:
                output += f"[EVENT INFO]\nError fetching event: {e}\n\n"

    # 5. PLAYER STATS
    if query_type == 'player_stats':
        if not player_url:
            output += "[PLAYER STATS]\nError: Please provide 'player_url' (e.g. '/player/11269/b1t').\n\n"
        else:
            try:
                print(f"  └─ 👤 Fetching Player Stats: {player_url}...")
                full_url = f"https://www.hltv.org{player_url}"
                r = scraper.get(full_url, timeout=30)
                soup = BeautifulSoup(r.text, 'html.parser')
                
                stats_texts = []
                for stat_div in soup.select('.playerProfile, .player-stat, .stat, .statistics, .summaryStatBreakdown'):
                    txt = stat_div.get_text(" ", strip=True)
                    if txt and txt not in stats_texts:
                        stats_texts.append(txt)
                
                if not stats_texts:
                    output += f"[PLAYER STATS: {player_url}]\nCould not extract stats (might be blocked by Cloudflare or incorrect URL).\n\n"
                else:
                    text = "\n".join(stats_texts)
                    if len(text) > 4000:
                        text = text[:4000]
                    output += f"[PLAYER STATS: {player_url}]\n{text}\n\n"
            except Exception as e:
                output += f"[PLAYER STATS]\nError fetching player stats: {e}\n\n"

    output += """
=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
Use this exact data to answer the user's questions about CS esports.
1. When asked about current or live event details (INCLUDING TEAM ROSTERS), FIRST call this tool with `query_type='tournaments'` to get the `event_page` IDs. THEN, call this tool again with `query_type='event_info'` and pass the corresponding `event_page` to get current matches, details, and FULL TEAM ROSTERS. Do not use generic web searches for this!
2. When asked for player stats, check the 'rankings' to get the player's `player_url`, then call this tool with `query_type='player_stats'` and pass the `player_url`.
3. If asked for the roster of a team playing in a live match, DO NOT say you cannot find it. Use `event_info` on the current tournament; it returns the exact rosters!
4. When presenting a LIVE match from YouTube:
   - Actively parse the video 'Title' to figure out who is playing (e.g. 'Team A vs. Team B').
   - Parse the title to determine what stage of the tournament it is (e.g., Playoffs, Group Stage, Grand Final, Semi-Final).
   - Present this dynamically to the user with bold styling for the teams and the stage.
   - Output the raw YouTube link on its own blank line so Discord creates a video embed!
"""
    return output
