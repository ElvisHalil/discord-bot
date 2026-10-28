import re

from src.skills.music.context import (
    RECENTLY_PLAYED_TRACKS,
    _CHANNEL_RECENTLY_PLAYED_TRACKS,
    _clean_song_token,
    _get_music_channel_deque,
    _norm_track_token,
    clear_recently_played_tracks,
    is_recently_played,
    record_channel_played_song,
    record_played_track,
)

from src.utils.cache import BoundedCache

# Global bounded in-memory cache of resolved music metadata
MUSIC_METADATA_CACHE = BoundedCache(maxsize=512)

__all__ = [
    "MUSIC_METADATA_CACHE",
    "RECENTLY_PLAYED_TRACKS",
    "_CHANNEL_RECENTLY_PLAYED_TRACKS",
    "_clean_song_token",
    "_get_music_channel_deque",
    "_norm_track_token",
    "clear_recently_played_tracks",
    "extract_remix_qualifier",
    "format_song_card",
    "get_music_metadata",
    "is_placeholder_text",
    "is_recently_played",
    "parse_song_meta",
    "record_played_track",
    "standardize_song_reply",
]

_REMIX_GARBAGE_OR_GENERIC_RE = re.compile(
    r'\b(?:'
    r'slowed|reverb|nightcore|sped\s+up|8d|bass\s+boost(?:ed)?|karaoke|acapella|instrumental'
    r'|1\s*hour|2\s*hours|10\s*hours|full\s+album|full\s+set|live\s+set|dj\s+set|boiler\s+room'
    r'|podcast|episode|reaction|tutorial|cover|tribute|mashup|megamix|continuous\s+mix'
    r'|original\s+mix|extended\s+mix|radio\s+edit|radio\s+mix|original\s+edit|extended\s+edit'
    r'|short\s+edit|clean\s+edit|explicit|album\s+version|single\s+version'
    r'|official|music\s+video|video|audio|lyric|lyrics|visualizer|remaster(?:ed)?|free\s+download'
    r'|hd|4k|hq|live\s+at|live\s+from|session'
    r')\b',
    re.IGNORECASE,
)

_REMIX_POSITIVE_RE = re.compile(
    r'(?:'
    r'\b(?:remix|rmx|vip|rework|flip|refix|bootleg)\b'
    r'|\b(?:dub\s+mix|vip\s+dub)\b'
    r'|^(?!english\b|japanese\b|spanish\b|french\b|german\b|italian\b|portuguese\b|russian\b|hindi\b|korean\b|chinese\b)[a-z0-9&\'.\s-]{2,35}\s+dub$'
    r'|^(?!radio\b|original\b|extended\b|short\b|clean\b|explicit\b|album\b|single\b|vocal\b|instrumental\b|club\b|main\b|video\b|official\b|live\b|new\b|full\b|dj\b|\d{4}\b)[a-z0-9&\'.\s-]{2,35}\s+edit$'
    r')',
    re.IGNORECASE,
)


def _is_remix_qualifier(text: str, allow_bare_single_word: bool = True) -> bool:
    """Returns True if `text` is a genuine remix, VIP, rework, flip, dub, or artist edit qualifier."""
    s = (text or "").strip(" ()[]{}-–—\"'")
    if not s or len(s) > 65:
        return False
    # Reject strings that still contain internal ' - ' separators (e.g. 'Song Title - Sub Focus Remix')
    if " - " in s or " – " in s or " — " in s:
        return False
    if _REMIX_GARBAGE_OR_GENERIC_RE.search(s):
        return False
    if not allow_bare_single_word and len(s.split()) < 2:
        return False
    return bool(_REMIX_POSITIVE_RE.search(s))


def extract_remix_qualifier(text: str) -> str | None:
    """
    Extracts a preserved remix/VIP/edit qualifier from a song title or query if present.
    Checks parenthetical/bracketed tags first (e.g., '(Sub Focus Remix)', '[VIP]'),
    then trailing hyphenated suffixes (e.g., 'Take Me - Sub Focus Remix').
    """
    if not text:
        return None
    # Strip trailing ' by Artist' if a full query was passed
    work = re.sub(r'https?://\S+', '', text).strip()
    had_by = " by " in work
    if had_by:
        work = work.split(" by ", 1)[0].strip()

    for m in re.finditer(r'[\(\[\{]([^\)\]\}]*)[\)\]\}]', work):
        cand = m.group(1).strip(" -–—\"'")
        if _is_remix_qualifier(cand, allow_bare_single_word=True):
            return cand

    m_dash = re.search(r'\s+[-–—]\s+([^-–—|/()\[\]\{\}]+)$', work)
    if m_dash:
        cand = m_dash.group(1).strip(" -–—\"'")
        if _is_remix_qualifier(cand, allow_bare_single_word=had_by):
            return cand

    return None


def parse_song_meta(title: str, author: str = "", query: str = "") -> dict:
    """
    Parses a raw video title, channel/author name, or query into structured metadata:
    - song: clean song/track title (always non-empty), preserving named remix/VIP qualifiers
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

    # Clean promotional and video suffixes (bounded to individual bracket groups so '(Sub Focus Remix) (Official Audio)' keeps the remix)
    clean = re.sub(
        r'[\(\[\{][^\)\]\}]*?(?:official|video|audio|lyrics|visualizer|download|remastered|remaster|hd|4k|hq|original mix|extended mix|radio edit|free download)[^\)\]\}]*?[\)\]\}]',
        '',
        title,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r'[\(\[\{](?:feat\.|ft\.)[^\)\]\}]*[\)\]\}]', '', clean, flags=re.IGNORECASE).strip()

    clean_author = re.sub(r'\s*-\s*Topic$', '', author or "", flags=re.IGNORECASE).strip()
    is_topic_author = bool(re.search(r'\s*-\s*Topic$', author or "", flags=re.IGNORECASE))
    artist = None
    song = None

    has_dash = " - " in clean or " – " in clean
    dash_is_remix_only = False
    if has_dash and " by " not in clean:
        delim = " - " if " - " in clean else " – "
        p0, p1 = clean.split(delim, 1)
        if _is_remix_qualifier(p1.strip(" -–—\"'"), allow_bare_single_word=is_topic_author and p0.strip().lower() != clean_author.lower()):
            dash_is_remix_only = True

    if " by " in clean:
        parts = clean.split(" by ", 1)
        song = parts[0].strip(" -–—\"'")
        artist = parts[1].strip(" -–—\"'")
    elif has_dash and not dash_is_remix_only:
        delim = " - " if " - " in clean else " – "
        parts = clean.split(delim, 1)
        artist = parts[0].strip(" -–—\"'")
        song = parts[1].strip(" -–—\"'")
    elif not is_topic_author and query and " by " in query:
        parts = query.split(" by ", 1)
        q_song = parts[0].strip(" -–—\"'")
        artist = parts[1].strip(" -–—\"'")
        song = clean.strip(" -–—\"'")
        if song.lower().startswith(artist.lower()):
            song = song[len(artist):].strip(" -–—\"'")
        song = re.sub(r'\s+\b(?:ft|feat)\.?\s+[^(\[\{]*', '', song, flags=re.IGNORECASE).strip(" -–—\"'")
        if not song:
            song = q_song
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

    # Extract any genuine remix/VIP/edit qualifier from `song` before stripping brackets and dash suffixes
    remix_tag = None
    if song:
        for m in re.finditer(r'[\(\[\{]([^\)\]\}]*)[\)\]\}]', song):
            cand = m.group(1).strip(" -–—\"'")
            if _is_remix_qualifier(cand, allow_bare_single_word=True):
                remix_tag = cand
                break
        if not remix_tag:
            m_dash = re.search(r'\s+[-–—]\s+([^-–—|/()\[\]\{\}]+)$', song)
            if m_dash:
                cand = m_dash.group(1).strip(" -–—\"'")
                if _is_remix_qualifier(cand, allow_bare_single_word=True):
                    remix_tag = cand

    song = re.sub(r'[\(\[\{].*?[\)\]\}]', '', song).strip(" -–—\"'")
    song = re.sub(r'[-–—|/].*$', '', song).strip(" -–—\"'")

    if remix_tag and song and remix_tag.lower() not in song.lower():
        song = f"{song} ({remix_tag})"

    if not song:
        song = title or "Unknown Track"
    if not artist:
        artist = clean_author or "Unknown Artist"

    return {
        "song": song,
        "artist": artist,
        "genre": genre,
        "album": album,
    }


def get_music_metadata(vid: str, fallback_text: str = "", fallback_title: str = "", fallback_author: str = "") -> dict:
    """
    Retrieves or fetches music metadata for a given YouTube video ID.
    Uses the in-memory cache first, falling back to YouTube oEmbed or text parsing.
    """
    if vid and vid in MUSIC_METADATA_CACHE:
        cached = dict(MUSIC_METADATA_CACHE[vid])
        if cached.get("duration"):
            try:
                from src.skills.youtube.engine.client import cache_video_duration

                cache_video_duration(vid, cached["duration"])
            except Exception:
                pass
        if fallback_text:
            parsed_fb = parse_song_meta(fallback_text)
            if parsed_fb.get("genre") and not cached.get("genre"):
                cached["genre"] = parsed_fb["genre"]
            if parsed_fb.get("album") and not cached.get("album"):
                cached["album"] = parsed_fb["album"]
        return cached

    title = fallback_title
    author = fallback_author
    oembed_success = False

    if vid:
        try:
            from src.skills.youtube.engine.client import get_video_oembed
            data = get_video_oembed(vid)
            if data and data.get("title"):
                title = data.get("title", title)
                author = data.get("author", author)
                oembed_success = True
        except Exception:
            pass

    if not title and fallback_text:
        meta = parse_song_meta(fallback_text, author)
    else:
        meta = parse_song_meta(title, author)

    if vid and meta.get("song") != "Unknown Track" and oembed_success:
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

    record_played_track(song, artist)
    try:
        record_channel_played_song(song=song, artist=artist, genre=genre, preserve_session=True)
    except Exception:
        pass
    m_vid = re.search(r'(?:v=|youtu\.be\/)([0-9A-Za-z_-]{11})', url or "")
    if m_vid:
        try:
            from src.skills.music.playback.youtube_music import record_served_song_vid
            record_served_song_vid(song, artist, m_vid.group(1))
        except Exception:
            pass

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
    t = text.strip(" *-_#:`\"'").lower()
    return (
        t in (
            "", "unknown", "unknown track", "unknown artist", "none",
            "<song title>", "<artist name>", "<genre name>", "<album name>",
            "<raw youtube music url>", "<raw youtube music url on its own blank line>",
            "<youtube url>", "song title", "artist name", "genre name", "album name",
        )
        or t.startswith("<raw youtube")
        or t.startswith("<song")
        or t.startswith("<artist")
        or "<song" in t
        or "<artist" in t
        or "song title" in t
        or "artist name" in t
    )


_SONG_LINE = re.compile(r'(?im)^[^\S\n]*(?:[-*•][^\S\n]*)?(?:\*\*)?(?:Song|Track):(?:\*\*)?[^\S\n]*\S')


def standardize_song_reply(reply: str) -> str:
    """
    Scans a message reply for YouTube Music or song links.
    Guarantees that the response adheres strictly to the consistent, emoji-free song template,
    preserving any conversational context, multi-task prompt answers, or similarity descriptions
    while stripping hallucinated YouTube markdown links or broken empty templates.
    """
    has_song_line = bool(_SONG_LINE.search(reply))
    has_artist_field = bool(re.search(r'(?:\*\*|\b)Artist:', reply, re.IGNORECASE))
    has_youtube_link = bool(re.search(r'(?:youtube\.com/watch|youtu\.be/)', reply, re.IGNORECASE))
    has_song_header = has_song_line and (has_artist_field or has_youtube_link)

    song_header_m = _SONG_LINE.search(reply) if has_song_header else None
    card_region = reply[song_header_m.start():] if song_header_m else reply
    yt_match = (
        re.search(r'(https:\/\/(?:music\.|www\.)?youtube\.com\/watch\?v=([0-9A-Za-z_-]{11}))', card_region)
        or re.search(r'(https:\/\/music\.youtube\.com\/watch\?v=([0-9A-Za-z_-]{11}))', reply)
        or re.search(r'(https:\/\/(?:music\.|www\.)?youtube\.com\/watch\?v=([0-9A-Za-z_-]{11}))', reply)
    )
    card_vid = yt_match.group(2) if (has_song_header and yt_match) else None

    intro_lines = []
    for line in reply.split('\n'):
        line_stripped = line.strip()
        if not line_stripped:
            if intro_lines and intro_lines[-1] != "":
                intro_lines.append("")
            continue
        if re.match(r'^[^\S\n]*(?:[-*•][^\S\n]*)?(?:\*\*)?(?:Song|Track|Artist|Album|Genre):(?:\*\*)?\s*', line_stripped, re.IGNORECASE):
            continue
        low_l = line_stripped.lower()
        if "<raw" in low_l:
            continue
        if "youtube.com" in low_l or "youtu.be" in low_l:
            m_other_yt = re.search(r'https:\/\/www\.youtube\.com\/watch\?v=([0-9A-Za-z_-]{11})', line_stripped)
            if has_song_header and m_other_yt and card_vid and m_other_yt.group(1) != card_vid and "music.youtube.com" not in low_l:
                intro_lines.append(line_stripped)
            continue
        clean_l = re.sub(r'\[[^\]]*\]\([^\)]*(?:youtube\.com|youtu\.be)[^\)]*\)', '', line_stripped, flags=re.IGNORECASE)
        clean_l = re.sub(
            r'\[(?:YouTube|Channel|Topic|Play|Watch|Video|Track)\b[^\]]*\]\((?:[^\)]*(?:youtube\.com|youtu\.be|<raw)[^\)]*|\s*)\)',
            '',
            clean_l,
            flags=re.IGNORECASE,
        ).strip()
        if clean_l and not (clean_l.startswith("<") and not clean_l.startswith(("<@", "<http://", "<https://", "<t:"))):
            intro_lines.append(clean_l)
    while intro_lines and intro_lines[-1] == "":
        intro_lines.pop()
    intro_text = "\n".join(intro_lines).strip()

    if has_song_header:
        extracted_song = None
        extracted_artist = None
        extracted_album = None
        extracted_genre = None

        m_song = re.search(r'(?im)^[^\S\n]*(?:[-*•][^\S\n]*)?(?:\*\*)?(?:Song|Track):\*{0,2}\s*(.+)', reply)
        if m_song:
            clean_s = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_song.group(1)).strip()
            if "artist:" in clean_s.lower():
                clean_s = re.split(r'\s+(?:\*\*|\b)Artist:\*{0,2}\s*', clean_s, flags=re.IGNORECASE)[0].strip()
            clean_s = re.sub(r'^(?:\*\*|\b)(?:Song|Track):\*{0,2}\s*', '', clean_s, flags=re.IGNORECASE).strip(" *-_#:`\"'")
            if clean_s and not is_placeholder_text(clean_s):
                extracted_song = clean_s

        m_art = (
            re.search(r'(?im)^[^\S\n]*(?:[-*•][^\S\n]*)?(?:\*\*)?Artist:\*{0,2}\s*(.+)', reply)
            or re.search(r'(?:\*\*|\b)Artist:\*{0,2}\s*(.+)', reply, re.IGNORECASE)
        )
        if m_art:
            clean_a = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_art.group(1)).strip()
            clean_a = re.sub(r'https?://\S+', '', clean_a).strip()
            clean_a = re.sub(r'^(?:\*\*|\b)Artist:\*{0,2}\s*', '', clean_a, flags=re.IGNORECASE).strip(" *-_#:`\"'")
            if clean_a and not is_placeholder_text(clean_a):
                extracted_artist = clean_a

        m_alb = re.search(r'(?im)^[^\S\n]*(?:[-*•][^\S\n]*)?(?:\*\*)?Album:\*{0,2}\s*(.+)', reply)
        if m_alb:
            clean_al = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_alb.group(1)).strip()
            clean_al = re.sub(r'^(?:\*\*|\b)Album:\*{0,2}\s*', '', clean_al, flags=re.IGNORECASE).strip(" *-_#:`\"'")
            if clean_al and not is_placeholder_text(clean_al):
                extracted_album = clean_al

        m_gen = re.search(r'(?im)^[^\S\n]*(?:[-*•][^\S\n]*)?(?:\*\*)?Genre:\*{0,2}\s*(.+)', reply)
        if m_gen:
            clean_g = re.sub(r'[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]', '', m_gen.group(1)).strip()
            clean_g = re.sub(r'^(?:\*\*|\b)Genre:\*{0,2}\s*', '', clean_g, flags=re.IGNORECASE).strip(" *-_#:`\"'")
            if (
                clean_g
                and not is_placeholder_text(clean_g)
                and not clean_g.lower().startswith(("similar to", "songs similar", "tracks similar", "music similar"))
            ):
                extracted_genre = clean_g

        music_url = None
        if yt_match:
            full_url = yt_match.group(1)
            vid = yt_match.group(2)
            from src.skills.youtube.engine.client import verify_embeddable
            is_embeddable = verify_embeddable(vid)
            meta = get_music_metadata(vid)
            if meta.get("song") != "Unknown Track":
                if not extracted_song or is_placeholder_text(extracted_song):
                    extracted_song = meta.get("song")
                if not extracted_artist or is_placeholder_text(extracted_artist):
                    extracted_artist = meta.get("artist")
                if not extracted_album:
                    extracted_album = meta.get("album")
                if not extracted_genre:
                    extracted_genre = meta.get("genre")

                meta_s = _clean_song_token(meta.get("song", ""))
                meta_a = _norm_track_token(meta.get("artist", ""))
                ext_s = _clean_song_token(extracted_song or "")
                ext_a = _norm_track_token(extracted_artist or "")
                is_mismatched_vid = (
                    bool(meta_s and ext_s and meta_a and ext_a)
                    and (meta_s not in ext_s and ext_s not in meta_s)
                    and (meta_a not in ext_a and ext_a not in meta_a)
                )

                if is_mismatched_vid:
                    print(
                        f"⚠️ [standardize_song_reply]: Stale/mismatched Video ID {vid} ('{meta.get('song')}' by '{meta.get('artist')}') "
                        f"for card '{extracted_song}' by '{extracted_artist}'. Auto-resolving correct link..."
                    )
                elif is_embeddable or meta.get("is_official_fallback"):
                    music_url = full_url.replace("www.youtube.com", "music.youtube.com")
                else:
                    print(f"⚠️ [standardize_song_reply]: Video ID {vid} is not embeddable in Discord. Attempting auto-resolution for a working link...")
            else:
                print(f"⚠️ [standardize_song_reply]: Video ID {vid} is dead/hallucinated. Attempting auto-resolution...")
        else:
            print("⚠️ [standardize_song_reply]: No YouTube URL found in song template. Attempting auto-resolution...")

        if not music_url and extracted_song and extracted_artist and not is_placeholder_text(extracted_song) and not is_placeholder_text(extracted_artist):
            try:
                from src.skills.music.playback.youtube_music import resolve_youtube_music_link
                candidates = resolve_youtube_music_link(f"{extracted_song} by {extracted_artist}", max_results=1)
                if candidates:
                    music_url = candidates[0].get("url", "").replace("www.youtube.com", "music.youtube.com")
                    print(f"  └─ ✅ [standardize_song_reply]: Auto-resolved '{extracted_song} by {extracted_artist}' -> {music_url}")
            except Exception as e:
                print(f"  └─ ⚠️ [standardize_song_reply]: Auto-resolution failed: {e}")

        if music_url and extracted_song and extracted_artist and not is_placeholder_text(extracted_song) and not is_placeholder_text(extracted_artist):
            print(f"🎵 [standardize_song_reply]: Formatted verified song card for '{extracted_song}' by '{extracted_artist}' -> {music_url}")
            return format_song_card(
                song=extracted_song,
                artist=extracted_artist,
                url=music_url,
                genre=extracted_genre,
                album=extracted_album,
                intro=intro_text,
            )

        if extracted_song and extracted_artist and not is_placeholder_text(extracted_song) and not is_placeholder_text(extracted_artist):
            print(f"⚠️ [standardize_song_reply]: Could not resolve URL for '{extracted_song}' by '{extracted_artist}', presenting text card.")
            lines = []
            if intro_text:
                lines.append(intro_text)
                lines.append("")
            lines.append(f"**Song:** {extracted_song.strip()}")
            lines.append(f"**Artist:** {extracted_artist.strip()}")
            if extracted_album:
                lines.append(f"**Album:** {extracted_album.strip()}")
            if extracted_genre:
                lines.append(f"**Genre:** {extracted_genre.strip()}")
            lines.append("")
            lines.append("⚠️ Could not verify playback link on YouTube Music.")
            return "\n".join(lines).strip()

        print("⚠️ [standardize_song_reply]: Stripping broken song template with missing/placeholder metadata.")
        return intro_text if intro_text else "⚠️ Could not find a verified track on YouTube Music."

    if yt_match:
        full_url = yt_match.group(1)
        vid = yt_match.group(2)
        video_url = full_url.replace("music.youtube.com", "www.youtube.com")
        from src.skills.youtube.engine.client import get_video_oembed, search_youtube, verify_embeddable
        if not verify_embeddable(vid):
            oembed = get_video_oembed(vid)
            if oembed.get("title"):
                print(
                    f"📌 [standardize_song_reply]: Preserving valid oEmbed-verified video {vid} "
                    f"('{oembed['title']}' by '{oembed.get('author', '')}') with broadcaster-disabled inline player."
                )
            else:
                print(f"⚠️ [standardize_song_reply]: Video mode ID {vid} is dead/unavailable. Attempting auto-resolution...")
                fallback_q = intro_text
                if fallback_q:
                    try:
                        alt_videos = search_youtube(fallback_q, max_results=1)
                        if alt_videos and alt_videos[0].get("url"):
                            video_url = alt_videos[0]["url"]
                            print(f"  └─ ✅ [standardize_song_reply]: Replaced dead video with {video_url}")
                    except Exception as e:
                        print(f"  └─ ⚠️ [standardize_song_reply]: Video mode auto-resolution failed: {e}")
        if video_url != full_url:
            reply = reply.replace(full_url, video_url)

    reply = re.sub(
        r'\[(?:YouTube|Channel|Topic|Play|Watch|Video|Track)\b[^\]]*\]\((?:[^\)]*(?:youtube\.com|youtu\.be|<raw)[^\)]*|\s*)\)',
        '',
        reply,
        flags=re.IGNORECASE,
    )
    reply = re.sub(r'<Raw YouTube[^\n>]*>', '', reply, flags=re.IGNORECASE)
    return reply.strip()


def ensure_turn_music_card_preserved(
    reply: str,
    turn_messages: list,
    fast_voice_turn: bool = False,
) -> str:
    """
    Safety net for music & Autoplay turns:
    If `skill_job_manager` (Autoplay start / skip / genre switch) or `skill_music_discovery`
    returned a pre-resolved YouTube Music song card in the current turn, and the LLM's final
    reply summarized the action into a 1-sentence intro while omitting the song card / URL,
    automatically re-attach the verified song card so Track #1 is always posted immediately.
    """
    if fast_voice_turn:
        return reply
    text = (reply or "").strip()
    if re.search(r"https?://(?:music\.|www\.)?youtube\.com/watch\?v=[0-9A-Za-z_-]{11}", text):
        return reply

    for m in reversed(turn_messages or []):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role == "user":
            break
        if role != "tool":
            continue
        t_name = str((m.get("name") if isinstance(m, dict) else getattr(m, "name", "")) or "")
        t_content = str((m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or "")
        if not t_content or "youtube.com/watch?v=" not in t_content:
            continue

        # 1. Autoplay card from `skill_job_manager`
        if t_name == "skill_job_manager" and "📻 **Autoplay" in t_content:
            idx = t_content.find("📻 **Autoplay")
            card_block = t_content[idx:]
            for marker in ("\n\n[SYSTEM INSTRUCTION", "\n\n[Current Time Context", "\n[Current Time Context"):
                if marker in card_block:
                    card_block = card_block.split(marker, 1)[0]
            card_block = card_block.strip()
            if card_block and "youtube.com/watch?v=" in card_block:
                print("🛡️ [Autoplay Card Guard]: Re-attached Track #1 Autoplay song card omitted by LLM summary.")
                return f"{text}\n\n{card_block}".strip() if text else card_block

        # 2. Pre-resolved card from `skill_music_discovery`
        if t_name == "skill_music_discovery" and "=== VERIFIED YOUTUBE MUSIC RESULT" in t_content:
            idx = t_content.find("=== VERIFIED YOUTUBE MUSIC RESULT")
            block = t_content[idx:]
            m_s = re.search(r"(?im)^Song:\s*(.+)$", block)
            m_a = re.search(r"(?im)^Artist:\s*(.+)$", block)
            m_g = re.search(r"(?im)^Genre:\s*(.+)$", block)
            m_al = re.search(r"(?im)^Album:\s*(.+)$", block)
            m_u = re.search(r"(?im)^Link:\s*(https?://\S+)$", block)
            if m_s and m_a and m_u:
                print("🛡️ [Music Card Guard]: Re-attached pre-resolved song card omitted by LLM summary.")
                return format_song_card(
                    song=m_s.group(1).strip(),
                    artist=m_a.group(1).strip(),
                    url=m_u.group(1).strip(),
                    genre=m_g.group(1).strip() if m_g else None,
                    album=m_al.group(1).strip() if m_al else None,
                    intro=text,
                )

        # 3. Verified YouTube Music link from `skill_youtube_search(is_music=True)`
        if t_name == "skill_youtube_search":
            m_yt = re.search(
                r"(https?://music\.youtube\.com/watch\?v=[0-9A-Za-z_-]{11})",
                t_content,
            )
            if m_yt:
                yt_url = m_yt.group(1).strip()
                print("🛡️ [YouTube Music Link Guard]: Re-attached verified YouTube Music URL omitted by LLM summary.")
                return f"{text}\n\n{yt_url}".strip() if text else yt_url

    return reply


