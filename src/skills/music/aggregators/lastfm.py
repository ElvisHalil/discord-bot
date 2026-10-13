import os
import re
import random
import requests
from dotenv import load_dotenv
from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.models import Track
from src.skills.music.utils import parse_song_meta

load_dotenv()

class LastFmAggregator(BaseMusicAggregator):
    """
    Music data and recommendation provider utilizing the Last.fm Web Services API v2.0.
    Leverages scrobble graph intelligence for track similarity, artist similarity,
    and global/genre top charts.
    """
    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("LASTFM_API_KEY")

    def _call(self, method: str, params: dict) -> dict:
        """Helper to invoke Last.fm REST API with JSON format and error handling."""
        key = self.api_key or os.getenv("LASTFM_API_KEY")
        if not key:
            return {}
        p = {
            "method": method,
            "api_key": key,
            "format": "json",
            **params
        }
        try:
            r = requests.get(self.BASE_URL, params=p, timeout=10, headers={"User-Agent": "DiscordMusicBot/1.0"})
            if r.status_code == 200:
                return r.json()
            else:
                print(f"  └─ ⚠️ [Last.fm]: API returned status {r.status_code} for method '{method}'")
        except Exception as e:
            print(f"  └─ ⚠️ [Last.fm Error]: {e}")
        return {}

    def _parse_similar_tracks(self, data: dict, exclude_song: str = "") -> list[Track]:
        """Extracts Track models from a Last.fm similartracks response."""
        tracks = []
        raw = data.get("similartracks", {}).get("track", [])
        if isinstance(raw, dict):
            raw = [raw]
        for t in raw:
            title = t.get("name", "")
            artist_obj = t.get("artist", {})
            artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
            if not title or not artist:
                continue
            if exclude_song and exclude_song.lower() == title.lower():
                continue
            tracks.append(Track(
                title=title,
                artist=artist,
                source="lastfm_similar"
            ))
        return tracks

    def _is_relevant_match(self, query: str, expected_artist: str, cand_artist: str, cand_track: str, expected_track: str = "") -> bool:
        """
        Ensures a canonical search match is legitimately related to the query to prevent genre hijacking.
        Strictly prevents cross-role contamination where song title words match an unrelated band name.
        """
        if not cand_artist or not cand_track:
            return False

        q_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', query.lower()) if len(w) > 1}
        cand_trk_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', cand_track.lower()) if len(w) > 1}
        cand_art_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', cand_artist.lower()) if len(w) > 1}

        # Case 1: Expected artist is known
        if expected_artist:
            exp_art_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', expected_artist.lower()) if len(w) > 1}
            if exp_art_tokens:
                # Require artist token overlap
                art_overlap = exp_art_tokens.intersection(cand_art_tokens)
                if not art_overlap:
                    return False
            # If expected track is also known, require track overlap
            if expected_track:
                exp_trk_tokens = {w for w in re.findall(r'[a-zA-Z0-9]+', expected_track.lower()) if len(w) > 1}
                if exp_trk_tokens and not exp_trk_tokens.intersection(cand_trk_tokens):
                    return False
            return True

        # Case 2: Expected artist is not known (free-form query)
        if not cand_trk_tokens:
            return False

        trk_in_q = cand_trk_tokens.intersection(q_tokens)
        if not trk_in_q:
            return False

        # Precision of candidate track tokens: at least 60% of the candidate track's words must be in the query
        trk_precision = len(trk_in_q) / len(cand_trk_tokens)
        if trk_precision < 0.6:
            return False

        # Check remaining query words not covered by candidate track
        remaining_q = q_tokens - trk_in_q
        if not remaining_q:
            return True

        # If 2 or more words in query remain, they likely indicate a specific artist.
        # If candidate artist has 0 overlap with those remaining words, reject as wrong artist.
        art_in_rem = cand_art_tokens.intersection(remaining_q)
        if len(remaining_q) >= 2 and not art_in_rem:
            return False

        # Total overlap between query and (cand_track + cand_artist) must cover at least 60% of query
        total_overlap = trk_in_q.union(cand_art_tokens.intersection(q_tokens))
        return len(total_overlap) >= max(1, int(len(q_tokens) * 0.6))

    def _get_similar_artists_tracks(self, artist: str, max_artists: int = 8, tracks_per_artist: int = 3) -> list[Track]:
        """Fetches top tracks from closely related artists on Last.fm's artist similarity graph."""
        tracks = []
        artist_res = self._call("artist.getsimilar", {
            "artist": artist,
            "limit": max_artists + 4,
            "autocorrect": 1
        })
        similar_artists = artist_res.get("similarartists", {}).get("artist", [])
        if isinstance(similar_artists, dict):
            similar_artists = [similar_artists]

        for sa in similar_artists[:max_artists]:
            rel_name = sa.get("name", "")
            if rel_name:
                top_res = self._call("artist.gettoptracks", {
                    "artist": rel_name,
                    "limit": tracks_per_artist,
                    "autocorrect": 1
                })
                top_tracks = top_res.get("toptracks", {}).get("track", [])
                if isinstance(top_tracks, dict):
                    top_tracks = [top_tracks]
                for tt in top_tracks:
                    t_name = tt.get("name", "")
                    if t_name:
                        tracks.append(Track(
                            title=t_name,
                            artist=rel_name,
                            source="lastfm_similar"
                        ))
        return tracks

    def get_similar(self, query: str) -> list[Track]:
        key = self.api_key or os.getenv("LASTFM_API_KEY")
        if not key:
            print("  └─ ⚠️ [Last.fm]: LASTFM_API_KEY is not configured in .env")
            return []

        print(f"  └─ 🔍 [Last.fm]: Finding similar tracks for '{query}'...")
        tracks = []
        meta = parse_song_meta(query)
        song_title = meta.get("song", "").strip()
        artist_name = meta.get("artist", "").strip()
        if artist_name.lower() in ("unknown artist", "unknown"):
            artist_name = ""

        # Strategy 1: Direct track.getSimilar if both song and artist were cleanly parsed
        if song_title and artist_name:
            res = self._call("track.getsimilar", {
                "artist": artist_name,
                "track": song_title,
                "limit": 50,
                "autocorrect": 1
            })
            tracks = self._parse_similar_tracks(res, exclude_song=song_title)
            if tracks:
                print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via track.getSimilar for '{song_title}' by '{artist_name}'")

        # Strategy 2: If artist is known and track has no scrobbles, query the artist's similarity graph!
        # This keeps recommendations 100% in the artist's genre and scene instead of random fuzzy track matching
        if not tracks and artist_name:
            print(f"  └─ 🔍 [Last.fm]: Track-level similarity yielded 0 tracks. Querying similar artists for '{artist_name}'...")
            tracks = self._get_similar_artists_tracks(artist_name)
            if tracks:
                print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via similar artists graph for '{artist_name}'")

        # Strategy 3: Candidate artist/track pairs for multi-word queries without delimiters
        if not tracks and " by " not in query and " - " not in query:
            clean_q = re.sub(r'[\(\[\{].*?[\)\]\}]', '', query).strip()
            words = clean_q.split()
            pairs = []
            # Trailing artist candidates (Song Title Artist)
            for k in (2, 1, 3):
                if len(words) > k:
                    pairs.append((" ".join(words[-k:]), " ".join(words[:-k]), k))
            # Leading artist candidates (Artist Song Title)
            for k in (2, 1, 3):
                if len(words) > k:
                    pairs.append((" ".join(words[:k]), " ".join(words[k:]), k))

            # 3A. Try direct track similarity for candidate pairs
            for cand_art, cand_trk, k in pairs:
                res = self._call("track.getsimilar", {
                    "artist": cand_art,
                    "track": cand_trk,
                    "limit": 50,
                    "autocorrect": 1
                })
                cand_tracks = self._parse_similar_tracks(res, exclude_song=cand_trk)
                if cand_tracks:
                    tracks = cand_tracks
                    print(f"  └─ ✅ [Last.fm]: Resolved pair '{cand_trk}' by '{cand_art}' ({len(tracks)} tracks)")
                    break

            # 3B. If track similarity yielded 0, detect if one of the candidate artist splits is a verified artist
            if not tracks:
                best_art = None
                best_score = 0
                for cand_art, cand_trk, k in pairs:
                    # Ignore single common dictionary words as artist in multi-word queries
                    if k == 1 and len(words) >= 3 and cand_art.lower() in ("nothing", "still", "love", "one", "the", "you", "me", "all", "stands", "give"):
                        continue
                    info = self._call("artist.getinfo", {"artist": cand_art})
                    listeners = int(info.get("artist", {}).get("stats", {}).get("listeners", 0) or 0)
                    weight = 2.0 if k >= 2 else 1.0
                    score = listeners * weight
                    if score > best_score and listeners > 1000:
                        best_score = score
                        best_art = cand_art

                if best_art:
                    print(f"  └─ 🔍 [Last.fm]: Detected artist '{best_art}' from query. Querying similar artists graph...")
                    tracks = self._get_similar_artists_tracks(best_art)
                    if tracks:
                        print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via similar artists graph for '{best_art}'")

        # Strategy 4: Track search (with STRICT relevance validation to prevent cross-genre hijacking)
        if not tracks:
            search_res = self._call("track.search", {"track": query, "limit": 10})
            matches = search_res.get("results", {}).get("trackmatches", {}).get("track", [])
            if isinstance(matches, dict):
                matches = [matches]
            matches.sort(key=lambda x: int(x.get("listeners", 0) or 0), reverse=True)

            for m in matches[:5]:
                cand_track = m.get("name", "")
                cand_artist = m.get("artist", "")
                if cand_track and cand_artist and self._is_relevant_match(query, artist_name, cand_artist, cand_track, expected_track=song_title):
                    res = self._call("track.getsimilar", {
                        "artist": cand_artist,
                        "track": cand_track,
                        "limit": 50,
                        "autocorrect": 1
                    })
                    cand_tracks = self._parse_similar_tracks(res, exclude_song=cand_track)
                    if cand_tracks:
                        tracks = cand_tracks
                        print(f"  └─ ✅ [Last.fm]: Resolved canonical '{cand_track}' by '{cand_artist}' ({len(tracks)} similar tracks)")
                        break

        # Strategy 5: Generic artist similarity fallback if query was an artist name
        if not tracks:
            target_artist = artist_name or (query.split(" by ")[-1].strip() if " by " in query else "")
            if target_artist and target_artist.lower() not in ("unknown artist", "unknown"):
                tracks = self._get_similar_artists_tracks(target_artist)
                if tracks:
                    print(f"  └─ ✅ [Last.fm]: Found {len(tracks)} tracks via similar artists for '{target_artist}'")

        if not tracks:
            print(f"  └─ ⚠️ [Last.fm]: No similar tracks found for '{query}'")

        return tracks

    def get_charts(self, genre: str = None) -> list[Track]:
        key = self.api_key or os.getenv("LASTFM_API_KEY")
        if not key:
            return []

        tracks = []
        if genre and genre.lower() not in ("all", "general", "none"):
            tag = genre.lower().strip()
            genre_aliases = {
                "dnb": "drum and bass",
                "drumnbass": "drum and bass",
                "drum & bass": "drum and bass",
                "hip hop": "hip-hop",
                "hiphop": "hip-hop",
                "r&b": "rnb",
                "r and b": "rnb",
                "edm": "electronic",
            }
            tag = genre_aliases.get(tag, tag)
            page = random.choice([1, 2])
            print(f"  └─ 📻 [Last.fm]: Fetching top tracks (page {page}) & rotating artists for tag '{tag}'...")
            res = self._call("tag.gettoptracks", {"tag": tag, "limit": 50, "page": page})
            raw = res.get("tracks", {}).get("track", [])
            if not raw and page != 1:
                res = self._call("tag.gettoptracks", {"tag": tag, "limit": 50, "page": 1})
                raw = res.get("tracks", {}).get("track", [])
            if isinstance(raw, dict):
                raw = [raw]

            artist_seen = set()
            source_name = f"lastfm_{tag.replace(' ', '_')}"

            # Step A: Sample up to 6 diverse tagged tracks (max 1 per artist)
            if raw:
                diverse_raw = []
                for t in raw:
                    art = t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))
                    clean_art = art.lower().strip()
                    if clean_art and clean_art not in artist_seen:
                        artist_seen.add(clean_art)
                        diverse_raw.append(t)
                sampled = random.sample(diverse_raw, min(6, len(diverse_raw)))
                # Reset artist_seen to only the artists actually sampled in Step A
                artist_seen = set()
                for t in sampled:
                    title = t.get("name", "")
                    artist_obj = t.get("artist", {})
                    artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
                    if title and artist:
                        artist_seen.add(artist.lower().strip())
                        tracks.append(Track(
                            title=title,
                            artist=artist,
                            genre=genre.title(),
                            source=source_name
                        ))

            # Step B: Rotate through top genre artists via tag.gettopartists -> artist.gettoptracks
            art_res = self._call("tag.gettopartists", {"tag": tag, "limit": 30})
            raw_artists = art_res.get("topartists", {}).get("artist", [])
            if isinstance(raw_artists, dict):
                raw_artists = [raw_artists]
            if raw_artists:
                cand_artists = []
                for a in raw_artists:
                    a_name = a.get("name", "") if isinstance(a, dict) else str(a)
                    if a_name and a_name.lower().strip() not in artist_seen:
                        cand_artists.append(a_name)
                selected_artists = random.sample(cand_artists, min(4, len(cand_artists)))
                for a_name in selected_artists:
                    top_res = self._call("artist.gettoptracks", {"artist": a_name, "limit": 5, "autocorrect": 1})
                    top_trks = top_res.get("toptracks", {}).get("track", [])
                    if isinstance(top_trks, dict):
                        top_trks = [top_trks]
                    if top_trks:
                        chosen_t = random.choice(top_trks[:5])
                        t_title = chosen_t.get("name", "")
                        if t_title:
                            artist_seen.add(a_name.lower().strip())
                            tracks.append(Track(
                                title=t_title,
                                artist=a_name,
                                genre=genre.title(),
                                source=source_name
                            ))
        else:
            print("  └─ 📻 [Last.fm]: Fetching global top chart tracks...")
            res = self._call("chart.gettoptracks", {"limit": 50})
            raw = res.get("tracks", {}).get("track", [])
            if isinstance(raw, dict):
                raw = [raw]
            if raw:
                artist_seen = set()
                diverse_raw = []
                for t in raw:
                    art = t.get("artist", {}).get("name", "") if isinstance(t.get("artist"), dict) else str(t.get("artist", ""))
                    clean_art = art.lower().strip()
                    if clean_art and clean_art not in artist_seen:
                        artist_seen.add(clean_art)
                        diverse_raw.append(t)
                sampled = random.sample(diverse_raw, min(10, len(diverse_raw)))
                for t in sampled:
                    title = t.get("name", "")
                    artist_obj = t.get("artist", {})
                    artist = artist_obj.get("name", "") if isinstance(artist_obj, dict) else str(artist_obj)
                    if title and artist:
                        tracks.append(Track(
                            title=title,
                            artist=artist,
                            source="lastfm_global"
                        ))

        return tracks


