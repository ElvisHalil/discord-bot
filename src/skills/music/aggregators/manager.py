import logging
import random
from typing import Dict, Optional

from src.skills.music.aggregators.base import BaseMusicAggregator
from src.skills.music.aggregators.beatport import BeatportAggregator
from src.skills.music.aggregators.charts import ChartsAggregator
from src.skills.music.aggregators.deezer import DeezerAggregator
from src.skills.music.aggregators.freqblog import FreqBlogAggregator
from src.skills.music.aggregators.lastfm import LastFmAggregator
from src.skills.music.catalogs import (
    GENRE_CANONICAL_MAP,
    GENRE_MOOD_MATRIX,
    STANDALONE_MOOD_PROFILES,
)
from src.skills.music.enums import MusicProvider
from src.skills.music.models import Track
from src.skills.music.engine.formatting import (
    MUSIC_METADATA_CACHE,
    _norm_track_token,
    is_recently_played,
    parse_song_meta,
)
from src.skills.music.engine.moods import parse_mood_and_genre

logger = logging.getLogger(__name__)


class MusicAggregatorManager:
    """
    Central manager for music recommendations and chart aggregators.
    Orchestrates the fallback cascade and allows seamless registration of new music providers.
    """

    def __init__(self):
        self.aggregators: Dict[str, BaseMusicAggregator] = {
            MusicProvider.LASTFM: LastFmAggregator(),
            MusicProvider.DEEZER: DeezerAggregator(),
            MusicProvider.FREQBLOG: FreqBlogAggregator(),
            MusicProvider.BEATPORT: BeatportAggregator(),
            MusicProvider.CHARTS: ChartsAggregator(),
        }

    def register_aggregator(self, name: str, aggregator: BaseMusicAggregator):
        """Register a new music recommendation or chart provider (e.g. Last.fm, Spotify)."""
        self.aggregators[name] = aggregator

    def _filter_and_deduplicate(self, tracks: list[Track], exclude_query: str = None) -> list[Track]:
        """
        Unified exclusion and deduplication filter shared across all discovery modes:
        1. Deduplicates (title, artist) pairs across providers.
        2. Excludes the seed song when exclude_query is provided (e.g., similar_to).
        3. Excludes tracks in RECENTLY_PLAYED_TRACKS session history (falling back gracefully if all candidates would be removed).
        """
        if not tracks:
            return []

        seed_song_norm = ""
        seed_artist_norm = ""
        if exclude_query:
            meta = parse_song_meta(exclude_query)
            seed_song_norm = _norm_track_token(meta.get("song", ""))
            art_raw = meta.get("artist", "")
            if art_raw.lower() not in ("unknown artist", "unknown", ""):
                seed_artist_norm = _norm_track_token(art_raw)

        from src.skills.music.playback.youtube_music import is_garbage_music_title

        seen = set()
        deduped = []
        fresh = []

        for t in tracks:
            if is_garbage_music_title(t.title, query="", author=t.artist):
                continue

            key = (t.title.lower().strip(), t.artist.lower().strip())
            if key in seen:
                continue
            seen.add(key)

            t_song_norm = _norm_track_token(t.title)
            t_art_norm = _norm_track_token(t.artist)

            # Exclude seed song in similarity queries
            if seed_song_norm and t_song_norm:
                if t_song_norm == seed_song_norm:
                    if not seed_artist_norm or not t_art_norm or seed_artist_norm in t_art_norm or t_art_norm in seed_artist_norm:
                        continue

            deduped.append(t)
            if not is_recently_played(t.title, t.artist):
                fresh.append(t)

        return fresh if fresh else deduped

    def get_similar_tracks(self, query: str) -> list[Track]:
        """
        Executes the similarity recommendation fallback chain:
        1. Query Last.fm API (scrobble-based similarity graph if API key configured)
        2. Query Deezer API (artist graph & related tracks)
        3. Query FreqBlog API (acoustic & neural embedding fallback if FREQBLOG_API_KEY configured)
        4. Transparent genre-aware / generic chart fallback tagged as 'fallback_unrelated' if song is completely unknown
        """
        tracks = []

        # 1. Primary: Last.fm (if LASTFM_API_KEY is available)
        lastfm = self.aggregators.get("lastfm")
        if lastfm:
            tracks = self._filter_and_deduplicate(lastfm.get_similar(query), exclude_query=query)

        # 2. Secondary: Deezer
        if not tracks:
            deezer = self.aggregators.get("deezer")
            if deezer:
                tracks = self._filter_and_deduplicate(deezer.get_similar(query), exclude_query=query)

        # 3. Tertiary: FreqBlog (Acoustic neural embedding fallback, protected by local cache)
        if not tracks:
            freqblog = self.aggregators.get("freqblog")
            if freqblog:
                tracks = self._filter_and_deduplicate(freqblog.get_similar(query), exclude_query=query)

        # 4. Fallback: Genre-aware or generic top charts when the track is completely unknown
        if not tracks:
            meta = parse_song_meta(query)
            detected_genre = meta.get("genre")
            print(f"  └─ ⚠️ [Aggregator]: No related songs found for '{query}'. Falling back to {'genre ' + detected_genre if detected_genre else 'general'} charts...")
            fallback_pool = self.get_top_charts(genre=detected_genre)
            if not fallback_pool:
                beatport = self.aggregators.get("beatport")
                if beatport:
                    fallback_pool = beatport.get_charts(genre=detected_genre or "techno")

            unrelated_tracks = []
            for t in fallback_pool:
                unrelated_tracks.append(Track(
                    title=t.title,
                    artist=t.artist,
                    genre=t.genre,
                    album=t.album,
                    url=t.url,
                    source="fallback_unrelated"
                ))
            tracks = self._filter_and_deduplicate(unrelated_tracks, exclude_query=query)

        return tracks

    def get_mood_matched_tracks(self, query: str) -> tuple[list[Track], dict]:
        """
        Specifically matches the acoustic mood, energy, and emotional feel of a seed song.
        Initiates with FreqBlog's audio intelligence, falling back gracefully to similarity chain.
        """
        freqblog = self.aggregators.get("freqblog")
        detected_mood = None
        detected_energy = None
        detected_valence = None

        if freqblog and hasattr(freqblog, "match_mood"):
            tracks, mood_info = freqblog.match_mood(query)
            tracks = self._filter_and_deduplicate(tracks, exclude_query=query)
            if tracks:
                return tracks, mood_info
            # If match_mood returned no tracks (e.g. 404 or unanalyzed track), check if lookup has seed metadata
            if hasattr(freqblog, "lookup"):
                meta = parse_song_meta(query)
                lookup_data = freqblog.lookup(meta.get("song", query), meta.get("artist", ""))
                if lookup_data:
                    detected_mood = lookup_data.get("mood")
                    detected_energy = lookup_data.get("energy")
                    detected_valence = lookup_data.get("valence")

        # Fallback to general similarity if mood match yields 0
        fallback_tracks = self.get_similar_tracks(query)
        mood_meta = {
            "seed_track": query,
            "mood": detected_mood or "similar",
            "energy": detected_energy,
            "valence": detected_valence,
        }
        return fallback_tracks, mood_meta

    def _infer_seed_genre(self, similar_to: str) -> Optional[str]:
        """Infers the genre of a seed song from parsed tags, cache, or Last.fm artist tags."""
        meta = parse_song_meta(similar_to)
        if meta.get("genre"):
            return meta["genre"]

        s_norm = _norm_track_token(meta.get("song", ""))
        a_norm = _norm_track_token(meta.get("artist", ""))
        for cached in MUSIC_METADATA_CACHE.values():
            if cached.get("genre"):
                c_song = _norm_track_token(cached.get("song", ""))
                c_art = _norm_track_token(cached.get("artist", ""))
                if s_norm and c_song == s_norm and (not a_norm or c_art == a_norm):
                    return cached["genre"]

        lastfm = self.aggregators.get("lastfm")
        artist_name = meta.get("artist", "")
        if lastfm and hasattr(lastfm, "_call") and artist_name and artist_name.lower() not in ("unknown artist", "unknown"):
            try:
                res = lastfm._call("artist.gettoptags", {"artist": artist_name, "autocorrect": 1})
                tags = res.get("toptags", {}).get("tag", [])
                if isinstance(tags, dict):
                    tags = [tags]
                for t in tags[:5]:
                    t_name = (t.get("name") or "").lower().strip()
                    if t_name in GENRE_CANONICAL_MAP:
                        return GENRE_CANONICAL_MAP[t_name]
            except Exception as e:
                logger.debug("Last.fm artist.gettoptags failed for %r: %s", artist_name, e)
        return None

    def get_mood_tracks(self, mood: str, genre: str = None, similar_to: str = None) -> list[Track]:
        """
        Fetches tracks tailored to a requested mood/vibe:
        - Standalone mood (genre=None, similar_to=None): Multi-genre mix across curated vibe playlists and tags.
        - Genre + mood (e.g. 'melodic dnb', 'hard techno'): Subgenre-specific playlists and tags.
        - Song reply + mood shift (similar_to provided): Pivots to that vibe within the seed song's genre.
        """
        parsed_genre, parsed_mood = parse_mood_and_genre(genre, mood)
        canon_mood = parsed_mood or (mood or "banger").lower().strip()
        target_genre = parsed_genre

        if similar_to and not target_genre:
            target_genre = self._infer_seed_genre(similar_to)

        if target_genre:
            canon_genre = GENRE_CANONICAL_MAP.get(target_genre.lower().strip(), target_genre.lower().strip())
            profile = GENRE_MOOD_MATRIX.get((canon_genre, canon_mood)) or {
                "deezer_queries": [],
                "lastfm_tags": [f"{canon_mood} {target_genre}", target_genre],
                "beatport_query": None,
            }
            print(f"  └─ 🎭 [Mood Discovery]: Resolving genre+vibe '{canon_mood}' + '{target_genre}'...")
        else:
            profile = STANDALONE_MOOD_PROFILES.get(canon_mood) or {
                "deezer_queries": [f"{canon_mood} hits", f"{canon_mood} songs", canon_mood],
                "lastfm_tags": [canon_mood],
                "beatport_query": None,
            }
            print(f"  └─ 🎭 [Mood Discovery]: Resolving standalone multi-genre vibe '{canon_mood}'...")

        from src.utils.workers import submit_to_worker
        all_tracks = []
        deezer = self.aggregators.get("deezer")
        lastfm = self.aggregators.get("lastfm")
        beatport = self.aggregators.get("beatport")

        tasks = []
        # 1. Deezer curated vibe playlists (sample up to 4 cross-genre or subgenre queries)
        dz_queries = list(profile.get("deezer_queries", []))
        if deezer and dz_queries:
            selected_dz = random.sample(dz_queries, min(4, len(dz_queries)))
            for q in selected_dz:
                tasks.append((deezer.get_charts, q))

        # 2. Last.fm vibe/subgenre tags (sample up to 2 tags)
        lfm_tags = list(profile.get("lastfm_tags", []))
        if lastfm and lfm_tags:
            selected_tags = random.sample(lfm_tags, min(2, len(lfm_tags)))
            for tag in selected_tags:
                tasks.append((lastfm.get_charts, tag))

        # 3. Beatport via Deezer (when applicable for electronic vibes)
        bp_query = profile.get("beatport_query")
        if beatport and bp_query:
            tasks.append((beatport.get_charts, bp_query))

        if tasks:
            futures = [submit_to_worker(fn, genre=arg) for fn, arg in tasks]
            for fut in futures:
                try:
                    all_tracks.extend(fut.result() or [])
                except Exception as e:
                    logger.debug("Mood track worker failed: %s", e)

        random.shuffle(all_tracks)
        return self._filter_and_deduplicate(all_tracks, exclude_query=similar_to)

    def get_top_charts(self, genre: str = None, mood: str = None) -> list[Track]:
        """Fetches trending and charting music across Last.fm, Beatport, and global charts concurrently, or delegates to mood discovery."""
        from src.utils.workers import submit_to_worker
        parsed_genre, parsed_mood = parse_mood_and_genre(genre, mood)
        if parsed_mood:
            return self.get_mood_tracks(mood=parsed_mood, genre=parsed_genre)

        all_tracks = []
        provider_names = ("lastfm", "beatport", "charts")
        active_providers = [self.aggregators[name] for name in provider_names if self.aggregators.get(name)]

        if active_providers:
            futures = [submit_to_worker(prov.get_charts, genre=parsed_genre) for prov in active_providers]
            for fut in futures:
                try:
                    all_tracks.extend(fut.result() or [])
                except Exception as e:
                    logger.debug("Chart provider worker failed: %s", e)

        # Safety net if Last.fm API key is missing and no other provider returned tracks
        if not all_tracks and self.aggregators.get("deezer"):
            try:
                all_tracks.extend(self.aggregators["deezer"].get_charts(genre=parsed_genre) or [])
            except Exception as e:
                logger.debug("Deezer fallback chart fetch failed: %s", e)

        return self._filter_and_deduplicate(all_tracks)

    def get_artist_tracks(self, artist: str) -> list[Track]:
        """
        Fetches top tracks by a specific artist concurrently across Last.fm and Deezer.
        - Last.fm provides scrobble-ranked fan favorites and autocorrected artist resolution.
        - Deezer provides trending catalog tracks and enriches matching Last.fm tracks with Album metadata.
        """
        from src.utils.workers import submit_to_worker

        lastfm = self.aggregators.get("lastfm")
        deezer = self.aggregators.get("deezer")

        lfm_tracks: list[Track] = []
        dz_tracks: list[Track] = []

        tasks = []
        if lastfm and hasattr(lastfm, "get_artist_top_tracks"):
            tasks.append(("lastfm", lastfm.get_artist_top_tracks))
        if deezer and hasattr(deezer, "get_artist_top_tracks"):
            tasks.append(("deezer", deezer.get_artist_top_tracks))

        if len(tasks) == 1:
            name, fn = tasks[0]
            try:
                res = fn(artist) or []
                if name == "lastfm":
                    lfm_tracks = res
                else:
                    dz_tracks = res
            except Exception as e:
                logger.debug("Artist track provider %s failed for %r: %s", name, artist, e)
        elif len(tasks) >= 2:
            futs = {name: submit_to_worker(fn, artist) for name, fn in tasks}
            for name, fut in futs.items():
                try:
                    res = fut.result() or []
                    if name == "lastfm":
                        lfm_tracks = res
                    else:
                        dz_tracks = res
                except Exception as e:
                    logger.debug("Artist track worker %s failed for %r: %s", name, artist, e)

        # Map Deezer album titles by normalized track title so Last.fm tracks get Album metadata when available
        album_by_title = {}
        for dt in dz_tracks:
            if dt.album:
                norm_t = _norm_track_token(dt.title)
                if norm_t and norm_t not in album_by_title:
                    album_by_title[norm_t] = dt.album

        combined: list[Track] = []
        for lt in lfm_tracks:
            norm_t = _norm_track_token(lt.title)
            if not lt.album and norm_t in album_by_title:
                lt.album = album_by_title[norm_t]
            combined.append(lt)

        combined.extend(dz_tracks)
        return self._filter_and_deduplicate(combined)

    def resolve_track_genre_and_zone(
        self,
        song: str,
        artist: str,
        known_genre: Optional[str] = None,
        known_zone: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Resolve the genre and 3x3 spectrum `ui_zone` for a currently playing or referenced track:
        1. Use known_zone / known_genre if already determined.
        2. Check MUSIC_METADATA_CACHE for matching (song, artist).
        3. Query Last.fm track.gettoptags -> artist.gettoptags and classify the returned tags.
        """
        from src.skills.music.engine.spectrum import classify_genre_to_zone

        if known_zone:
            return known_genre, known_zone

        if known_genre:
            z = classify_genre_to_zone(known_genre)
            if z:
                return known_genre, z

        s_norm = _norm_track_token(song)
        a_norm = _norm_track_token(artist)
        for cached in MUSIC_METADATA_CACHE.values():
            c_genre = cached.get("genre")
            if c_genre:
                c_song = _norm_track_token(cached.get("song", ""))
                c_art = _norm_track_token(cached.get("artist", ""))
                if s_norm and c_song == s_norm and (not a_norm or c_art == a_norm):
                    z = classify_genre_to_zone(c_genre)
                    if z:
                        return c_genre, z

        lastfm = self.aggregators.get("lastfm")
        if lastfm and hasattr(lastfm, "_call") and artist and artist.lower() not in ("unknown artist", "unknown"):
            collected_tags: list[str] = []
            if song and song.lower() not in ("unknown track", "unknown"):
                try:
                    t_res = lastfm._call("track.gettoptags", {"track": song, "artist": artist, "autocorrect": 1})
                    t_tags = t_res.get("toptags", {}).get("tag", [])
                    if isinstance(t_tags, dict):
                        t_tags = [t_tags]
                    for tg in t_tags[:8]:
                        t_name = (tg.get("name") or "").strip()
                        if t_name:
                            collected_tags.append(t_name)
                except Exception as e:
                    logger.debug("Last.fm track.gettoptags failed for %r by %r: %s", song, artist, e)

            if not collected_tags:
                try:
                    a_res = lastfm._call("artist.gettoptags", {"artist": artist, "autocorrect": 1})
                    a_tags = a_res.get("toptags", {}).get("tag", [])
                    if isinstance(a_tags, dict):
                        a_tags = [a_tags]
                    for tg in a_tags[:8]:
                        t_name = (tg.get("name") or "").strip()
                        if t_name:
                            collected_tags.append(t_name)
                except Exception as e:
                    logger.debug("Last.fm artist.gettoptags failed for %r: %s", artist, e)

            for tg in collected_tags:
                z = classify_genre_to_zone(tg)
                if z:
                    return tg.title(), z

        # 4. Acoustic DSP fallback: analyze the 30s Deezer preview MP3 to determine true speed/intensity zone
        deezer = self.aggregators.get("deezer")
        if song and song.lower() not in ("unknown track", "unknown"):
            try:
                from src.skills.music.engine.dsp import get_track_acoustic_profile
                prof = get_track_acoustic_profile(song, artist, deezer_agg=deezer)
                if prof and prof.get("ui_zone"):
                    return known_genre or "Acoustic DSP", prof["ui_zone"]
            except Exception as e:
                logger.debug("Acoustic DSP zone fallback failed for %r by %r: %s", song, artist, e)

        return known_genre, None

    def get_spectrum_tracks(
        self,
        target_zone: dict,
        prefer_deep_cuts: bool = False,
        exclude_query: Optional[str] = None,
        d_tempo: int = 0,
        d_intensity: int = 0,
        anchor_bpm: Optional[float] = None,
        seed_genre: Optional[str] = None,
    ) -> list[Track]:
        """
        Fetch diverse tracks for a target 3x3 spectrum zone using its mainstream_tags,
        deep_cut_tags, and intersection_modifiers across Last.fm and Deezer concurrently.
        """
        from src.utils.workers import submit_to_worker

        mainstream = list(target_zone.get("mainstream_tags", []))
        deep_cuts = list(target_zone.get("deep_cut_tags", []))
        modifiers = list(target_zone.get("intersection_modifiers", []))
        intensity_lvl = target_zone.get("intensity", "mid")

        if prefer_deep_cuts:
            active_tags = random.sample(deep_cuts, min(4, len(deep_cuts))) if deep_cuts else list(mainstream[:3])
        else:
            chosen_main = random.sample(mainstream, min(3, len(mainstream)))
            chosen_deep = random.sample(deep_cuts, min(1, len(deep_cuts)))
            active_tags = chosen_main + chosen_deep

        # High-velocity escalation: when pushing 'faster' at the fast boundary or from >=152 BPM,
        # blend in genuine 170-205 BPM subgenres matching the current intensity tier.
        if d_tempo > 0 and (prefer_deep_cuts or (anchor_bpm is not None and anchor_bpm >= 152.0)):
            high_vel_map = {
                "soft": ["liquid drum and bass", "atmospheric dnb", "intelligent drum and bass", "liquid funk"],
                "mid": ["happy hardcore", "uk hardcore", "drum and bass", "eurobeat", "hyperpop"],
                "hard": ["breakcore", "neurofunk", "gabber", "frenchcore", "hardstyle"],
            }
            hv_pool = [t for t in high_vel_map.get(intensity_lvl, high_vel_map["mid"]) if t not in active_tags]
            if hv_pool:
                active_tags = active_tags[:2] + random.sample(hv_pool, min(2, len(hv_pool)))

        # Preserve genre continuity if the current seed_genre already belongs to this zone's tags
        if seed_genre:
            sg_low = seed_genre.strip().lower()
            zone_all = {t.lower() for t in (mainstream + deep_cuts)}
            if sg_low in zone_all and sg_low not in [t.lower() for t in active_tags]:
                active_tags.insert(0, sg_low)

        if not active_tags:
            active_tags = mainstream or deep_cuts or ["pop"]

        print(
            f"  └─ 🎛️ [Spectrum Shift]: Fetching zone '{target_zone.get('ui_zone')}' "
            f"(tempo={target_zone.get('tempo')}, intensity={target_zone.get('intensity')}, tags={active_tags})..."
        )

        lastfm = self.aggregators.get("lastfm")
        deezer = self.aggregators.get("deezer")

        tasks: list[tuple] = []
        if lastfm:
            for tag in active_tags[:4]:
                tasks.append((lastfm.get_charts, tag, tag.title()))
        if deezer:
            for idx, tag in enumerate(active_tags[:4]):
                if prefer_deep_cuts and idx == 0 and modifiers:
                    mod = random.choice(modifiers)
                    tasks.append((deezer.get_charts, f"{tag} {mod}", tag.title()))
                else:
                    tasks.append((deezer.get_charts, tag, tag.title()))

            if exclude_query and hasattr(deezer, "get_similar"):
                sim_label = (seed_genre or active_tags[0]).title()
                tasks.append((deezer.get_similar, exclude_query, sim_label))

        all_tracks: list[Track] = []
        if tasks:
            fut_map = {submit_to_worker(fn, arg): tag_label for fn, arg, tag_label in tasks}
            for fut, tag_label in fut_map.items():
                try:
                    res = fut.result() or []
                    for trk in res:
                        if not trk.genre:
                            trk.genre = tag_label
                        all_tracks.append(trk)
                except Exception as e:
                    logger.debug("Spectrum shift worker failed for tag %r: %s", tag_label, e)

        random.shuffle(all_tracks)
        return self._filter_and_deduplicate(all_tracks, exclude_query=exclude_query)




