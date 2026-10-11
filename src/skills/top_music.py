import os
import json
import re
import random
import urllib.parse
import urllib.request
import requests
from bs4 import BeautifulSoup

# Dictionary of common iTunes genre IDs
GENRE_MAP = {
    "pop": 14,
    "rock": 21,
    "country": 6,
    "countrymusic": 6,
    "hiphop": 18,
    "rap": 18,
    "hiphoprap": 18,
    "randb": 15,
    "rb": 15,
    "soul": 15,
    "rbsoul": 15,
    "dance": 17,
    "electronic": 7,
    "edm": 7,
    "jazz": 11,
    "alternative": 20,
    "christian": 22,
    "gospel": 22,
    "kpop": 50,
    "latino": 12,
    "latin": 12,
    "classical": 5,
    "dubstep": 100014,
    "bass": 100015,
    "drumandbass": 1049,
    "drumnbass": 1049,
    "dnb": 1049,
    "jungle": 1049,
    "house": 1048,
    "techno": 1050,
    "trance": 1051,
    "afrohouse": 100049,
    "metal": 1153,
    "heavymetal": 1153,
    "deathmetal": 1149,
    "blackmetal": 1149,
    "hairmetal": 1151,
    "christianmetal": 1095,
    "Minimalism": 1767
}

def skill_top_music(genre: str = None, similar_to: str = None) -> str:
    """
    Fetches the current top trending/charting music in the world right now, OR finds similar songs.
    ALWAYS use this skill FIRST when a user asks for song recommendations, top hits, or what music is popular today.
    - To get top trending charts by genre, specify 'genre' (e.g., 'pop, techno').
    - To get similar songs, specify 'similar_to' with the track and artist name (e.g. 'Believe by Cher').
    """
    if similar_to:
        print(f"🎵 [Executing Top Music Skill] Similar to: {similar_to}")
        try:
            print(f"  └─ 🔍 Finding similar songs via Deezer API for: {similar_to}...")

            # Smart query cleanups
            clean_q = re.sub(r'[\(\[\{].*?[\)\]\}]', '', similar_to, flags=re.IGNORECASE)
            clean_q = re.sub(r'[-–—|/].*$', '', clean_q).strip()
            
            candidates = [similar_to.strip()]
            if clean_q and clean_q != similar_to:
                candidates.append(clean_q)
            if " by " in clean_q:
                parts = clean_q.split(" by ", 1)
                candidates.append(f"{parts[0].strip()} {parts[1].strip()}")
                candidates.append(parts[0].strip())
                candidates.append(parts[1].strip())
            elif " - " in clean_q:
                parts = clean_q.split(" - ", 1)
                candidates.append(f"{parts[1].strip()} {parts[0].strip()}")
                candidates.append(parts[1].strip())
                candidates.append(parts[0].strip())

            data = None
            for q_cand in candidates:
                if not q_cand.strip():
                    continue
                try:
                    r = requests.get(f"https://api.deezer.com/search?q={urllib.parse.quote(q_cand.strip())}&limit=1", timeout=5)
                    res = r.json()
                    if res.get("data"):
                        data = res
                        break
                except Exception:
                    continue

            if not data or not data.get("data"):
                print(f"  └─ ⚠️ Track not found on Deezer, falling back to Beatport charts...")
                return skill_top_music(genre="techno")
            
            track = data["data"][0]
            artist_id = track["artist"]["id"]
            track_title = track["title"]
            artist_name = track["artist"]["name"]
            print(f"  └─ ✅ Found track: {track_title} by {artist_name}")
            
            r_rel = requests.get(f"https://api.deezer.com/artist/{artist_id}/related?limit=20", timeout=5)
            related = r_rel.json()
            related_artists = related.get("data", [])
            
            similar_tracks = []
            if related_artists:
                if len(related_artists) > 12:
                    related_artists = random.sample(related_artists, 12)
                    
                for artist in related_artists:
                    try:
                        r_art = requests.get(f"https://api.deezer.com/artist/{artist['id']}/top?limit=5", timeout=5)
                        top = r_art.json()
                        if top.get("data"):
                            for t in top["data"]:
                                if t['title'].lower() != track_title.lower():
                                    alb = t.get('album', {}).get('title', '')
                                    alb_str = f" (Album: {alb})" if alb else ""
                                    similar_tracks.append(f"- {t['title']} by {t['artist']['name']}{alb_str}")
                    except Exception:
                        continue
            else:
                print(f"  └─ ⚠️ No related artists for {artist_name}, fetching top tracks by same artist...")
                try:
                    r_top = requests.get(f"https://api.deezer.com/artist/{artist_id}/top?limit=10", timeout=5)
                    top_data = r_top.json()
                    if top_data.get("data"):
                        for t in top_data["data"]:
                            if t['title'].lower() != track_title.lower():
                                alb = t.get('album', {}).get('title', '')
                                alb_str = f" (Album: {alb})" if alb else ""
                                similar_tracks.append(f"- {t['title']} by {t['artist']['name']}{alb_str}")
                except Exception:
                    pass

            if not similar_tracks:
                print(f"  └─ ⚠️ No related tracks found, falling back to Beatport...")
                return skill_top_music(genre="techno")
                
            # Randomize the massive pool of tracks and pick 15
            similar_tracks = list(set(similar_tracks)) # Remove duplicates
            random.shuffle(similar_tracks)
            
            output = f"=== SONGS SIMILAR TO '{track_title} by {artist_name}' ===\n\n"
            for t in similar_tracks[:15]:
                output += t + "\n"
                        
            output += """
=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
Select ONE best matching song from the list above.
CRITICAL INSTRUCTION: Call `skill_youtube_searcher` with `is_music=True` and query="<Song Title> by <Artist Name>" to fetch its verified YouTube Music link.
When presenting the song to the user, you MUST format your response using this EXACT clean, text-only template (NO EMOJIS):

[Your brief conversational intro or similarity explanation, if any]

**Song:** <Song Title>
**Artist:** <Artist Name>
**Genre:** <Genre Name> (optional, include only if known)
**Album:** <Album Name> (optional, include only if known)

<Raw YouTube Music URL on its own blank line>

CRITICAL RULES:
1. BOTH '**Song:**' and '**Artist:**' MUST always be displayed.
2. Do NOT include ANY emojis in the song template lines. Keep it clean and text-only.
3. NEVER invent or hallucinate YouTube URLs. You MUST use `skill_youtube_searcher` to get the link.
4. Paste ONLY the bare YouTube Music URL on a new blank line at the bottom.
5. NEVER wrap the link in markdown (like `[Title](url)`) or `<>` brackets.
6. Do NOT output ANY markdown links like `[YouTube](...)` or `[Channel](...)` or `[Title](...)`.
"""
            return output
        except Exception as e:
            return f"Error finding similar songs: {e}"
    
    print(f"🎵 [Executing Top Music Skill] Genre: {genre or 'All'}")
    spotify_songs = ""
    youtube_songs = ""
    radio_songs = ""
    
    # Global charts don't filter by genre well, so only fetch if no genre is specified.
    if not genre:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        try:
            print(f"  └─ 🎧 Scraping Spotify Global Daily...")
            r = requests.get('https://kworb.net/spotify/country/global_daily.html', headers=headers, timeout=5)
            soup = BeautifulSoup(r.text, 'html.parser')
            all_rows = soup.select('table.sortable tbody tr')[:50]
            if all_rows:
                sampled_rows = random.sample(all_rows, min(10, len(all_rows)))
                for tr in sampled_rows:
                    spotify_songs += f"- {tr.find('td', class_='text').get_text(strip=True)}\n"
        except Exception as e:
            spotify_songs = f"Could not load Spotify global charts: {e}\n"
            
        try:
            print(f"  └─ 📺 Scraping YouTube Trending...")
            r = requests.get('https://kworb.net/youtube/trending.html', headers=headers, timeout=5)
            soup = BeautifulSoup(r.text, 'html.parser')
            all_rows = soup.select('table tbody tr')[:50]
            if all_rows:
                sampled_rows = random.sample(all_rows, min(10, len(all_rows)))
                for tr in sampled_rows:
                    youtube_songs += f"- {tr.find('td', class_='text').get_text(strip=True)}\n"
        except Exception as e:
            youtube_songs = f"Could not load YouTube charts: {e}\n"
            
        try:
            print(f"  └─ 📻 Scraping US Radio Airplay...")
            r = requests.get('https://kworb.net/radio/', headers=headers, timeout=5)
            soup = BeautifulSoup(r.text, 'html.parser')
            all_rows = soup.select('table tbody tr')[:50]
            if all_rows:
                sampled_rows = random.sample(all_rows, min(10, len(all_rows)))
                for tr in sampled_rows:
                    radio_songs += f"- {tr.find('td', class_='text').get_text(strip=True)}\n"
        except Exception as e:
            radio_songs = f"Could not load Radio charts: {e}\n"

    output = f"=== TODAY'S TOP CHARTING MUSIC ({genre.upper() if genre else 'ALL GENRES'}) ===\n\n"
    
    if not genre:
        output += f"[SPOTIFY GLOBAL (Random 10 from Top 50)]\n{spotify_songs}\n"
        output += f"[YOUTUBE TRENDING MUSIC (Random 10 from Top 50)]\n{youtube_songs}\n"
        output += f"[US RADIO AIRPLAY (Random 10 from Top 50)]\n{radio_songs}\n"

    # BEATPORT SCRAPING (Always include since user loves Beatport)
    try:
        beatport_urls = [
            ("Top 100", "https://www.beatport.com/top-100"),
            ("Hype 100", "https://www.beatport.com/hype-100"),
            ("Best Tracks", "https://www.beatport.com/best-tracks-so-far")
        ]
        
        for name, url in beatport_urls:
            print(f"  └─ 🎧 Scraping Beatport {name}...")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            html = urllib.request.urlopen(req, timeout=10).read()
            soup = BeautifulSoup(html, 'html.parser')
            script = soup.find('script', id='__NEXT_DATA__')
            bp_songs = ""
            if script:
                data = json.loads(script.string)
                queries = data.get('props', {}).get('pageProps', {}).get('dehydratedState', {}).get('queries', [])
                for q in queries:
                    if 'data' in q.get('state', {}) and 'results' in q['state']['data']:
                        tracks = q['state']['data']['results']
                        if tracks and 'name' in tracks[0]:
                            sampled_tracks = random.sample(tracks, min(7, len(tracks)))
                            for t in sampled_tracks:
                                artist_names = ", ".join([a.get('name', '') for a in t.get('artists', [])])
                                genre_name = t.get('genre', {}).get('name', 'Unknown')
                                bp_songs += f"- {t.get('name', 'Unknown')} by {artist_names} (Genre: {genre_name})\n"
                            break
            if bp_songs:
                output += f"[BEATPORT {name.upper()} (Random 7 from 100)]\n{bp_songs}\n"
    except Exception as e:
        print(f"  └─ ⚠️ Beatport Scraping Error: {e}")
        output += f"[BEATPORT] Failed to load charts: {e}\n\n"

    # Handle single or multiple genres via iTunes
    genre_list = [genre] if genre else [None]
    if genre:
        # Split by comma or 'and'
        raw_genres = re.split(r',|\band\b', genre)
        genre_list = [g.strip() for g in raw_genres if g.strip()]
        if not genre_list:
            genre_list = [genre] # fallback

    for g_name in genre_list:
        g_id = None
        if g_name:
            clean_genre = g_name.lower().replace("-", "").replace(" ", "").replace("&", "and").replace("'", "")
            g_id = GENRE_MAP.get(clean_genre)
            
        try:
            if g_name:
                print(f"  └─ 🍎 Fetching iTunes RSS for {g_name}...")
            else:
                print(f"  └─ 🍎 Fetching iTunes RSS for all genres...")
                
            base_url = "https://itunes.apple.com/us/rss/topsongs/limit=50"
            if g_id:
                base_url += f"/genre={g_id}"
            elif g_name:
                print(f"  └─ ⚠️ Unknown genre '{g_name}', fetching overall iTunes charts but it may not be accurate.")
            base_url += "/json"
            
            response = requests.get(base_url, timeout=5)
            data = response.json()
            
            entries = data.get("feed", {}).get("entry", [])
            itunes_songs = ""
            if not entries:
                itunes_songs = "No charting music found."
            else:
                sampled_entries = random.sample(entries, min(10, len(entries)))
                for i, entry in enumerate(sampled_entries):
                    title = entry.get("im:name", {}).get("label", "Unknown Song")
                    artist = entry.get("im:artist", {}).get("label", "Unknown Artist")
                    itunes_songs += f"- {title} by {artist}\n"
            
            section_title = f"{g_name.upper()}" if g_name else "ALL GENRES"
            output += f"[APPLE MUSIC / ITUNES US - {section_title} (Random 10 from Top 50)]\n{itunes_songs}\n"
            
        except Exception as e:
            output += f"Could not load iTunes charts for {g_name or 'all'}: {e}\n\n"

    output += """
=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
You have successfully fetched today's top music. 
Present these lists to the user naturally. 
If this skill returned NO data or an unrecognizable genre, immediately use your `search_web` tool to find the top music for that specific genre online.
CRITICAL INSTRUCTION: If the user explicitly asked for "a song" or "a beat" (singular), you MUST pick EXACTLY ONE song from the list, use `skill_youtube_searcher` with `is_music=True` to get its link, and present ONLY THAT ONE SONG. Do not return 3 songs. Only return multiple if they asked for a playlist or multiple recommendations.
CRITICAL INSTRUCTION: NEVER guess or invent YouTube URLs. You MUST call `skill_youtube_searcher` with `is_music=True` to get the real, working video link for any song you present.
CRITICAL INSTRUCTION FOR DISCORD: Whenever you provide a song link, you MUST format the raw URL using the YouTube Music domain (e.g., https://music.youtube.com/watch?v=...) and paste it on its own blank line. Do NOT wrap it in markdown `[Text](link)` or `<link>`, otherwise Discord will fail to embed the music player!
"""
    return output
