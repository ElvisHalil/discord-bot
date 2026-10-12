import re
import requests

# Global in-memory cache of resolved music metadata
MUSIC_METADATA_CACHE = {}

def parse_song_meta(title: str, author: str = "", query: str = "") -> dict:
    """
    Parses a raw video title, channel/author name, or query into structured metadata:
    - song: clean song/track title (always non-empty)
    - artist: clean artist name (always non-empty)
    - genre: genre name if present in title/tag, else None
    - album: album title if present in tag, else None
    """
    title = re.sub(r'https?://\S+', '', title or "").strip()
    
    # Extract embedded genre or album tags if present (e.g., "(Genre: Techno)", "(Album: Cruisin)", "(Album: X, Genre: Y)")
    genre = None
    m_genre = re.search(r'(?:\(|,\s*)Genre:\s*([^,)]+)', title, flags=re.IGNORECASE)
    if m_genre:
        genre = m_genre.group(1).strip()

    album = None
    m_album = re.search(r'(?:\(|,\s*)Album:\s*([^,)]+)', title, flags=re.IGNORECASE)
    if m_album:
        album = m_album.group(1).strip()

    title = re.sub(r'\((?:Album:[^,)]+|Genre:[^,)]+)(?:,\s*(?:Album:[^,)]+|Genre:[^,)]+))*\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\(Genre:\s*[^)]+\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\(Album:\s*[^)]+\)', '', title, flags=re.IGNORECASE).strip()

    # Clean promotional and video suffixes
    clean = re.sub(
        r'[\(\[\{].*?(?:official|video|audio|lyrics|visualizer|download|remastered|remaster|hd|4k|hq|original mix|extended mix|radio edit|free download).*?[\)\]\}]',
        '',
        title,
        flags=re.IGNORECASE
    )
    clean = re.sub(r'[\(\[\{](?:feat\.|ft\.).*?[\)\]\}]', '', clean, flags=re.IGNORECASE).strip()

    clean_author = re.sub(r'\s*-\s*Topic$', '', author or "").strip()
    artist = None
    song = None

    if " by " in clean:
        parts = clean.split(" by ", 1)
        song = parts[0].strip(" -–—\"'")
        artist = parts[1].strip(" -–—\"'")
    elif " - " in clean or " – " in clean:
        delim = " - " if " - " in clean else " – "
        parts = clean.split(delim, 1)
        artist = parts[0].strip(" -–—\"'")
        song = parts[1].strip(" -–—\"'")
    elif clean_author and clean_author.lower() not in ("youtube", "various artists", "unknown"):
        artist = clean_author
        song = clean.strip(" -–—\"'")
        if song.lower().startswith(artist.lower()):
            song = song[len(artist):].strip(" -–—\"'")
    elif query and " by " in query:
        parts = query.split(" by ", 1)
        song = parts[0].strip(" -–—\"'")
        artist = parts[1].strip(" -–—\"'")
    else:
        song = clean or title
        artist = clean_author or "Unknown Artist"

    song = re.sub(r'[\(\[\{].*?[\)\]\}]', '', song).strip(" -–—\"'")
    song = re.sub(r'[-–—|/].*$', '', song).strip(" -–—\"'")

    if not song:
        song = title or "Unknown Track"
    if not artist:
        artist = clean_author or "Unknown Artist"

    return {
        "song": song,
        "artist": artist,
        "genre": genre,
        "album": album
    }

def get_music_metadata(vid: str, fallback_text: str = "", fallback_title: str = "", fallback_author: str = "") -> dict:
    """
    Retrieves or fetches music metadata for a given YouTube video ID.
    Uses the in-memory cache first, falling back to YouTube oEmbed or text parsing.
    """
    if vid and vid in MUSIC_METADATA_CACHE:
        cached = dict(MUSIC_METADATA_CACHE[vid])
        # If fallback text has genre or album not in cache, merge them
        if fallback_text:
            parsed_fb = parse_song_meta(fallback_text)
            if parsed_fb.get("genre") and not cached.get("genre"):
                cached["genre"] = parsed_fb["genre"]
            if parsed_fb.get("album") and not cached.get("album"):
                cached["album"] = parsed_fb["album"]
        return cached

    title = fallback_title
    author = fallback_author
    
    if vid:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
            r = requests.get(oembed_url, timeout=3)
            if r.status_code == 200:
                data = r.json()
                title = data.get("title", title)
                author = data.get("author_name", author)
        except Exception:
            pass

    if not title and fallback_text:
        meta = parse_song_meta(fallback_text, author)
    else:
        meta = parse_song_meta(title, author)

    if vid and meta.get("song") != "Unknown Track":
        MUSIC_METADATA_CACHE[vid] = meta

    return meta

def format_song_card(song: str, artist: str, url: str, genre: str = None, album: str = None, intro: str = "") -> str:
    """
    Constructs the clean, consistent Markdown template for returning a song in Discord:
    [Optional Intro / Similarity Description]
    
    **Song:** <Song Title>
    **Artist:** <Artist Name>
    **Album:** <Album Title> (optional)
    **Genre:** <Genre Name> (optional)
    
    <Raw YouTube Music URL>
    """
    lines = []
    if intro and intro.strip():
        lines.append(intro.strip())
        lines.append("")

    lines.append(f"**Song:** {song.strip()}")
    lines.append(f"**Artist:** {artist.strip()}")

    if album and album.strip():
        lines.append(f"**Album:** {album.strip()}")
    if genre and genre.strip():
        lines.append(f"**Genre:** {genre.strip()}")

    lines.append("")
    lines.append(url.strip())
    return "\n".join(lines)

def is_placeholder_text(text: str) -> bool:
    """Detects if text contains empty strings or LLM template placeholders."""
    if not text:
        return True
    t = text.strip().lower()
    return t in (
        "", "unknown", "unknown track", "unknown artist", "none",
        "<song title>", "<artist name>", "<genre name>", "<album name>",
        "<raw youtube music url>", "<raw youtube music url on its own blank line>",
        "<youtube url>"
    ) or t.startswith("<raw youtube") or t.startswith("<song") or t.startswith("<artist")

def standardize_song_reply(reply: str) -> str:
    """
    Scans a message reply for YouTube Music or song links.
    Guarantees that the response adheres strictly to the consistent, emoji-free song template,
    preserving any conversational context or similarity descriptions while stripping
    hallucinated markdown links or broken empty templates.
    """
    has_song_header = bool(re.search(r'(?:\*\*|\b)(?:Song|Track):\s*', reply, re.IGNORECASE))

    # Helper: Extract conversational intro lines preceding any song headers or raw urls
    intro_lines = []
    for line in reply.split('\n'):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        if re.search(r'(?:\*\*|\b)(?:Song|Track|Artist|Album|Genre):\s*', line_stripped, re.IGNORECASE):
            break
        if "youtube.com" in line_stripped.lower() or "<raw" in line_stripped.lower() or line_stripped.startswith("<http"):
            break
        clean_l = re.sub(r'\[.*?\]\(.*?\)', '', line_stripped).strip()
        if clean_l and not clean_l.startswith("<"):
            intro_lines.append(clean_l)
    intro_text = "\n".join(intro_lines).strip()

    yt_match = re.search(r'(https:\/\/(?:music\.|www\.)?youtube\.com\/watch\?v=([0-9A-Za-z_-]{11}))', reply)

    # If message was formatted with a song template:
    if has_song_header:
        # Case A: Song template present but NO valid YouTube video link (e.g. placeholder or missing URL)
        if not yt_match:
            print("⚠️ [standardize_song_reply]: Stripping broken song template with missing/placeholder URL.")
            return intro_text if intro_text else "⚠️ Could not find a verified track on YouTube Music."

        full_url = yt_match.group(1)
        vid = yt_match.group(2)
        music_url = full_url.replace("www.youtube.com", "music.youtube.com")

        meta = get_music_metadata(vid)
        # Defense: If video is NOT in verified cache AND oEmbed returned Unknown Track (HTTP 404 on YouTube), reject dead link
        if meta.get("song") == "Unknown Track":
            print(f"⚠️ [standardize_song_reply]: Stripping broken song template for dead/hallucinated video ID {vid}.")
            return intro_text if intro_text else "⚠️ Could not find a verified track on YouTube Music."

        extracted_song = meta.get("song")
        extracted_artist = meta.get("artist")
        extracted_album = meta.get("album")
        extracted_genre = meta.get("genre")

        m_song = re.search(r'(?:\*\*|\b)(?:Song|Track):\s*(.+)', reply, re.IGNORECASE)
        if m_song:
            clean_s = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_song.group(1)).strip()
            # If multiple fields are inline (e.g. "Song: X Artist: Y")
            if "artist:" in clean_s.lower():
                clean_s = re.split(r'\s+(?:\*\*|\b)Artist:\s*', clean_s, flags=re.IGNORECASE)[0].strip()
            if clean_s and not is_placeholder_text(clean_s):
                extracted_song = clean_s

        m_art = re.search(r'(?:\*\*|\b)Artist:\s*(.+)', reply, re.IGNORECASE)
        if m_art:
            clean_a = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_art.group(1)).strip()
            # If URL is inline (e.g. "Artist: Y https://...")
            clean_a = re.sub(r'https?://\S+', '', clean_a).strip()
            if clean_a and not is_placeholder_text(clean_a):
                extracted_artist = clean_a

        m_alb = re.search(r'(?:\*\*|\b)Album:\s*(.+)', reply, re.IGNORECASE)
        if m_alb:
            clean_al = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_alb.group(1)).strip()
            if clean_al and not is_placeholder_text(clean_al):
                extracted_album = clean_al

        m_gen = re.search(r'(?:\*\*|\b)Genre:\s*(.+)', reply, re.IGNORECASE)
        if m_gen:
            clean_g = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_gen.group(1)).strip()
            if clean_g and not is_placeholder_text(clean_g):
                extracted_genre = clean_g

        # Case B: If song title or artist is empty, placeholder, or Unknown, strip broken template
        if is_placeholder_text(extracted_song) or is_placeholder_text(extracted_artist):
            print(f"⚠️ [standardize_song_reply]: Stripping broken song template for unverified track '{extracted_song}' by '{extracted_artist}' (ID: {vid}).")
            return intro_text if intro_text else "⚠️ Could not find a verified track on YouTube Music."

        print(f"🎵 [standardize_song_reply]: Formatted verified song card for '{extracted_song}' by '{extracted_artist}' -> {music_url}")
        return format_song_card(
            song=extracted_song,
            artist=extracted_artist,
            url=music_url,
            genre=extracted_genre,
            album=extracted_album,
            intro=intro_text
        )

    # Video mode (no **Song:** header):
    if yt_match:
        full_url = yt_match.group(1)
        video_url = full_url.replace("music.youtube.com", "www.youtube.com")
        if video_url != full_url:
            reply = reply.replace(full_url, video_url)

    # Strip hallucinated markdown links and accidental raw placeholders
    reply = re.sub(r'\[(?:YouTube|Channel|Topic|Play|Watch|Video|Track)[^\]]*\]\([^\)]*\)', '', reply, flags=re.IGNORECASE)
    reply = re.sub(r'<Raw YouTube[^\n>]*>', '', reply, flags=re.IGNORECASE)
    return reply.strip()
