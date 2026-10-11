import re

def parse_song_meta(title, author="", query=""):
    title = re.sub(r'https?://\S+', '', title).strip()
    clean = re.sub(r'[\(\[\{].*?(?:official|video|audio|lyrics|visualizer|download|remastered|hd|4k|hq|mix).*?[\)\]\}]', '', title, flags=re.IGNORECASE)
    clean = re.sub(r'[\(\[\{](?:feat\.|ft\.).*?[\)\]\}]', '', clean, flags=re.IGNORECASE)

    genre = None
    m_genre = re.search(r'\(Genre:\s*([^)]+)\)', title, flags=re.IGNORECASE)
    if m_genre:
        genre = m_genre.group(1).strip()
        clean = re.sub(r'\(Genre:\s*[^)]+\)', '', clean, flags=re.IGNORECASE)

    clean = clean.strip()
    artist = None
    song = None

    clean_author = re.sub(r'\s*-\s*Topic$', '', author).strip()

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
    elif " by " in query:
        parts = query.split(" by ", 1)
        song = parts[0].strip(" -–—\"'")
        artist = parts[1].strip(" -–—\"'")
    else:
        song = clean or title
        artist = clean_author or "Unknown Artist"

    song = re.sub(r'[\(\[\{].*?[\)\]\}]', '', song).strip(" -–—\"'")
    song = re.sub(r'[-–—|/].*$', '', song).strip(" -–—\"'")
    return {"song": song, "artist": artist, "genre": genre}

tests = [
    ("Step Down", "shftr - Topic", ""),
    ("shftr - Step Down (Official Audio)", "shftr", ""),
    ("Anyma - Running [Original Mix]", "Afterlife", ""),
    ("Apex by Bicep (Genre: Techno)", "", ""),
    ("Glue", "Bicep - Topic", ""),
    ("Believe", "Cher", "Believe by Cher"),
]
for t, a, q in tests:
    print(f"Input: title='{t}', author='{a}', query='{q}'\n  -> {parse_song_meta(t, a, q)}\n")

