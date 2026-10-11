import os
import requests

from src.music_utils import parse_song_meta, MUSIC_METADATA_CACHE

def skill_youtube_searcher(query: str, max_results: int = 1, is_music: bool = False) -> str:
    """
    Use this skill ONLY to search for specific known videos, channels, or specific song titles on YouTube.
    IMPORTANT: If the user asks for 'top songs', 'trending music', or recommendations (even if they ask for YouTube links), DO NOT use this tool first. You MUST use the `skill_top_music` tool FIRST to discover the actual songs, and only use this YouTube tool afterward to fetch the links.
    If the user asks for a specific song or video, leave max_results as 1 (default).
    Set `is_music` to True ONLY if you are explicitly searching for a song or music track.
    """
    print(f"▶️ [Executing YouTube Search Skill]: {query} (max_results: {max_results}, is_music: {is_music})")
    
    api_key = os.getenv("YOUTUBE_API_KEY")
    data = {}
    response_status = None
    error_msg = "YOUTUBE_API_KEY not configured"

    try:
        if api_key:
            url = "https://www.googleapis.com/youtube/v3/search"
            params = {
                'part': 'snippet',
                'q': query,
                'type': 'video',
                'videoEmbeddable': 'true',
                'maxResults': max_results,
                'key': api_key
            }
            
            if is_music:
                params['videoCategoryId'] = '10'  # Music category
                
            response = requests.get(url, params=params, timeout=10)
            response_status = response.status_code
            data = response.json() if response_status == 200 else {}
            if response_status != 200:
                error_data = response.json()
                error_msg = error_data.get("error", {}).get("message", "Unknown API Error")
                print(f"⚠️ [YouTube API Error]: {error_msg}. Falling back to DuckDuckGo...")
            elif not data.get('items'):
                error_msg = "No videos found via API"
                print(f"⚠️ [YouTube Search Empty]: Falling back to DuckDuckGo...")
        else:
            print(f"⚠️ [YouTube Search]: YOUTUBE_API_KEY missing. Falling back to DuckDuckGo...")
            
        if not api_key or response_status != 200 or not data.get('items'):
            from ddgs import DDGS
            import re
            
            queries_to_try = []
            if is_music:
                queries_to_try.append(f'site:youtube.com {query} Topic')
                queries_to_try.append(f'site:music.youtube.com {query}')
            queries_to_try.append(f"site:youtube.com {query}")
            if " by " in query:
                queries_to_try.append(f"site:youtube.com {query.replace(' by ', ' ')}")
            queries_to_try.append(f"site:youtube.com {query} audio")
            
            seen_ids = set()
            candidates = []
            
            for q_try in queries_to_try:
                try:
                    res = list(DDGS().text(q_try, max_results=max_results + 4))
                    for r in res:
                        m = re.search(r'(?:v=|\/watch\?v=|\/embed\/|\/shorts\/|youtu\.be\/)([0-9A-Za-z_-]{11})(?:[?&/]|\b|$)', r.get('href', ''))
                        if m:
                            vid = m.group(1)
                            if vid not in seen_ids:
                                seen_ids.add(vid)
                                candidates.append((vid, r.get('title', '')))
                    if len(candidates) >= max_results + 3:
                        break
                except Exception:
                    continue
            
            if not candidates:
                 return f"YouTube API Error / Empty: {error_msg}. AND DuckDuckGo fallback failed. CRITICAL: Do NOT guess or invent a YouTube link! Tell the user you couldn't find it."
            
            topic_results = []
            standard_results = []
            
            for vid, title in candidates:
                try:
                    # 1. Verify embeddability (filter out videos blocked from third-party players)
                    embed_url = f"https://www.youtube.com/embed/{vid}"
                    embed_text = requests.get(embed_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3).text
                    if "Playback on other websites has been disabled" in embed_text or "UNPLAYABLE" in embed_text:
                        print(f"⚠️ [YouTube Search]: Skipping {vid} because embedding is disabled by owner.")
                        continue
                        
                    # 2. Check for official Topic channel (True Music Track) and fetch clean title
                    author = ""
                    real_title = ""
                    try:
                        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
                        o_res = requests.get(oembed_url, timeout=3)
                        if o_res.status_code == 200:
                            author = o_res.json().get("author_name", "")
                            real_title = o_res.json().get("title", "")
                    except Exception:
                        pass
                        
                    if real_title:
                        title = real_title

                    is_topic = author.endswith(" - Topic") or "Topic" in author
                    link = f"https://music.youtube.com/watch?v={vid}" if is_music else f"https://www.youtube.com/watch?v={vid}"
                    
                    if is_music:
                        meta = parse_song_meta(title, author, query=query)
                        MUSIC_METADATA_CACHE[vid] = {
                            "song": meta["song"],
                            "artist": meta["artist"],
                            "genre": meta.get("genre"),
                            "album": meta.get("album"),
                            "url": link
                        }

                    entry = (title, author, link, vid)
                    
                    if is_topic:
                        topic_results.append(entry)
                        if len(topic_results) >= max_results:
                            break
                    else:
                        standard_results.append(entry)
                except Exception:
                    continue
                    
            final_chosen = topic_results if topic_results else standard_results
            if not final_chosen and candidates:
                vid, title = candidates[0]
                link = f"https://music.youtube.com/watch?v={vid}" if is_music else f"https://www.youtube.com/watch?v={vid}"
                final_chosen = [(title, "", link, vid)]
                if is_music:
                    meta = parse_song_meta(title, "", query=query)
                    MUSIC_METADATA_CACHE[vid] = {
                        "song": meta["song"],
                        "artist": meta["artist"],
                        "genre": meta.get("genre"),
                        "album": meta.get("album"),
                        "url": link
                    }
                
            formatted_results = ""
            for i, (title, author, link, vid) in enumerate(final_chosen[:max_results]):
                if is_music and vid in MUSIC_METADATA_CACHE:
                    m = MUSIC_METADATA_CACHE[vid]
                    chan_str = f"\n   Artist: {m['artist']}"
                    formatted_results += f"{i+1}. Song: {m['song']}{chan_str}\n   Link: {link}\n\n"
                else:
                    chan_str = f"\n   Channel: {author}" if author else ""
                    formatted_results += f"{i+1}. Title: {title}{chan_str}\n   Link: {link}\n\n"
                
            instruction_text = """=== REQUIRED RESPONSE TEMPLATE FOR SONGS ===
When presenting a song to the user, you MUST format your response using this EXACT clean, text-only template (NO EMOJIS):

[Your brief conversational intro or similarity explanation, if any]

**Song:** <Song Title>
**Artist:** <Artist Name>
**Genre:** <Genre Name> (optional, include only if known)
**Album:** <Album Name> (optional, include only if known)

<Raw YouTube Music URL on its own blank line>

CRITICAL RULES:
1. BOTH '**Song:**' and '**Artist:**' MUST always be displayed.
2. Do NOT include ANY emojis in the song template lines. Keep it clean and text-only.
3. Paste ONLY the bare YouTube Music URL on a new blank line at the bottom.
4. NEVER wrap the link in markdown (like `[Title](url)`) or `<>` brackets.
5. Do NOT output ANY markdown links like `[YouTube](...)` or `[Channel](...)` or `[Title](...)`.
""" if is_music else """=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
Present the video(s) to the user.
CRITICAL INSTRUCTION FOR DISCORD EMBEDS: To ensure a full media player is embedded directly in Discord, you MUST paste the raw link on its own blank line. 
Do NOT wrap the link in markdown (like `[Title](link)`) or `<>` brackets, as that prevents Discord from creating the player embed. It MUST be a bare URL on a new line.
IMPORTANT: Do NOT output ANY markdown links like `[YouTube](...)` or `[Channel](...)` anywhere in your response. Only the raw URL.
If you searched for a specific song or video, present ONLY the best single link.
"""

            return f"""
=== DUCKDUCKGO FALLBACK RESULTS ===
{formatted_results}

{instruction_text}
"""
            
        items = data.get('items', [])
            
        formatted_results = ""
        for i, video in enumerate(items):
            title = video['snippet'].get('title', 'Unknown Title')
            channel = video['snippet'].get('channelTitle', 'Unknown Channel')
            video_id = video['id'].get('videoId', '')
            
            if is_music:
                link = f"https://music.youtube.com/watch?v={video_id}"
                meta = parse_song_meta(title, channel, query=query)
                MUSIC_METADATA_CACHE[video_id] = {
                    "song": meta["song"],
                    "artist": meta["artist"],
                    "genre": meta.get("genre"),
                    "album": meta.get("album"),
                    "url": link
                }
                chan_str = f"\n   Artist: {meta['artist']}"
                formatted_results += f"{i+1}. Song: {meta['song']}{chan_str}\n   Link: {link}\n\n"
            else:
                link = f"https://www.youtube.com/watch?v={video_id}"
                formatted_results += f"{i+1}. Title: {title}\n   Channel: {channel}\n   Link: {link}\n\n"
            
        instruction_text = """=== REQUIRED RESPONSE TEMPLATE FOR SONGS ===
When presenting a song to the user, you MUST format your response using this EXACT clean, text-only template (NO EMOJIS):

[Your brief conversational intro or similarity explanation, if any]

**Song:** <Song Title>
**Artist:** <Artist Name>
**Genre:** <Genre Name> (optional, include only if known)
**Album:** <Album Name> (optional, include only if known)

<Raw YouTube Music URL on its own blank line>

CRITICAL RULES:
1. BOTH '**Song:**' and '**Artist:**' MUST always be displayed.
2. Do NOT include ANY emojis in the song template lines. Keep it clean and text-only.
3. Paste ONLY the bare YouTube Music URL on a new blank line at the bottom.
4. NEVER wrap the link in markdown (like `[Title](url)`) or `<>` brackets.
5. Do NOT output ANY markdown links like `[YouTube](...)` or `[Channel](...)` or `[Title](...)`.
""" if is_music else """=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===
Present the video(s) to the user.
CRITICAL INSTRUCTION FOR DISCORD EMBEDS: To ensure a full media player is embedded directly in Discord, you MUST paste the raw link on its own blank line. 
Do NOT wrap the link in markdown (like `[Title](link)`) or `<>` brackets, as that prevents Discord from creating the player embed. It MUST be a bare URL on a new line.
IMPORTANT: Do NOT output ANY markdown links like `[YouTube](...)` or `[Channel](...)` anywhere in your response. Only the raw URL.
If you searched for a specific song or video, present ONLY the best single link.
"""

        return f"""
=== RAW YOUTUBE RESULTS ===
{formatted_results}

{instruction_text}
"""
    except Exception as e:
        return f"Error searching YouTube: {e}. CRITICAL: Do NOT guess or invent a YouTube link! Tell the user the search failed."
