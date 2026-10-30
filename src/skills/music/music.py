import logging
import re
from typing import Optional

from src.context import get_active_channel_id, set_active_channel_id
from src.skills.music.aggregators.freqblog import FreqBlogAggregator
from src.skills.music.aggregators.manager import MusicAggregatorManager
from src.skills.music.context import (
    _CHANNEL_DISCOVERY_CACHE,
    _CHANNEL_DISCOVERY_GEN,
    _DISCOVERY_CACHE_LOCK,
    _get_music_channel_deque,
    _norm_track_token,
    get_channel_last_played_song,
    invalidate_channel_discovery_cache,
    is_recently_played,
    record_channel_played_song,
    record_played_track,
)
from src.skills.music.engine import dsp as audio_analyzer
from src.skills.music.engine.formatting import MUSIC_METADATA_CACHE, parse_song_meta
from src.skills.music.engine.moods import parse_mood_and_genre
from src.skills.music.engine.spectrum import (
    has_non_music_domain_context,
    parse_spectrum_shift,
    step_spectrum_zone,
)
from src.skills.music.enums import AudioAction
from src.skills.music.models import Track
from src.skills.music.playback.youtube_music import resolve_youtube_music_link
from src.skills.music.templates import build_music_llm_instructions
from src.utils.workers import submit_to_worker

logger = logging.getLogger(__name__)

manager = MusicAggregatorManager()
freqblog = FreqBlogAggregator()


def _get_or_consume_cached_discovery(
    cid: int,
    session_key: tuple,
) -> tuple[Optional[list[Track]], Optional[tuple[Track, dict]]]:
    """Retrieve a channel's cached track pool and/or background pre-resolved track for `session_key`.
    If the channel's cached `session_key` differs from the requested `session_key`, immediately
    invalidates the stale session so cross-genre/cross-artist leakage is impossible."""
    if not cid:
        return None, None
    with _DISCOVERY_CACHE_LOCK:
        entry = _CHANNEL_DISCOVERY_CACHE.get(cid)
        if not entry:
            return None, None
        if entry.get("session_key") != session_key:
            _CHANNEL_DISCOVERY_GEN[cid] += 1
            _CHANNEL_DISCOVERY_CACHE.pop(cid, None)
            return None, None

        raw_tracks = list(entry.get("tracks") or [])
        unplayed = [t for t in raw_tracks if not is_recently_played(t.title, t.artist, channel_id=cid)]
        pre = entry.pop("pre_resolved", None)
        pre_hit = None
        if isinstance(pre, dict):
            pre_cand = pre.get("cand")
            pre_res = pre.get("resolved")
            if (
                pre_cand is not None
                and isinstance(pre_res, dict)
                and pre_res.get("url")
                and not is_recently_played(pre_cand.title, pre_cand.artist, channel_id=cid)
            ):
                pre_hit = (pre_cand, pre_res)

        if pre_hit is not None:
            return (unplayed or raw_tracks), pre_hit
        if len(unplayed) >= 4:
            return unplayed, None
        return None, None


def _pre_resolve_next_candidate_for_channel(
    cid: int,
    session_key: tuple,
    expected_gen: int,
    enforce_artist_diversity: bool = True,
) -> bool:
    """Background worker that pre-resolves the next unplayed candidate for `(cid, session_key)`.
    Verifies `session_key` and `expected_gen` before and after network calls so a genre change,
    artist switch, or specific song request never gets overwritten."""
    if not cid:
        return False
    set_active_channel_id(cid)
    with _DISCOVERY_CACHE_LOCK:
        entry = _CHANNEL_DISCOVERY_CACHE.get(cid)
        if (
            not entry
            or entry.get("session_key") != session_key
            or _CHANNEL_DISCOVERY_GEN.get(cid) != expected_gen
        ):
            return False
        pool = list(entry.get("tracks") or [])

    unplayed = [t for t in pool if not is_recently_played(t.title, t.artist, channel_id=cid)]
    if not unplayed:
        return False

    sampled = MusicAggregatorManager.sample_tiered_tracks(
        unplayed,
        k=min(10, len(unplayed)),
        weights=(0.45, 0.35, 0.20),
        enforce_artist_diversity=enforce_artist_diversity,
    )
    chan_deque = _get_music_channel_deque(cid)
    last_artist = chan_deque[-1][1] if chan_deque else ""
    if enforce_artist_diversity and last_artist:
        diff_art = [c for c in sampled if _norm_track_token(c.artist) != last_artist]
        ordered = diff_art or sampled
    else:
        ordered = sampled

    for cand in ordered[:3]:
        with _DISCOVERY_CACHE_LOCK:
            entry = _CHANNEL_DISCOVERY_CACHE.get(cid)
            if (
                not entry
                or entry.get("session_key") != session_key
                or _CHANNEL_DISCOVERY_GEN.get(cid) != expected_gen
            ):
                return False
        if is_recently_played(cand.title, cand.artist, channel_id=cid):
            continue
        query = f"{cand.title} by {cand.artist}"
        try:
            results = resolve_youtube_music_link(query, max_results=1)
            if not results:
                continue
            r = results[0]
            vid = r.get("id")
            if vid and vid in MUSIC_METADATA_CACHE:
                if cand.genre:
                    MUSIC_METADATA_CACHE[vid]["genre"] = cand.genre
                if cand.album and not MUSIC_METADATA_CACHE[vid].get("album"):
                    MUSIC_METADATA_CACHE[vid]["album"] = cand.album
            with _DISCOVERY_CACHE_LOCK:
                entry = _CHANNEL_DISCOVERY_CACHE.get(cid)
                if (
                    not entry
                    or entry.get("session_key") != session_key
                    or _CHANNEL_DISCOVERY_GEN.get(cid) != expected_gen
                    or is_recently_played(cand.title, cand.artist, channel_id=cid)
                ):
                    return False
                entry["pre_resolved"] = {"cand": cand, "resolved": r}
                print(
                    f"  └─ ⚡ [Music Pre-Resolver]: Lined up next track '{r['title']}' by '{r['artist']}' "
                    f"for channel {cid} ({session_key[0]}={session_key[1]})"
                )
                return True
        except Exception as e:
            logger.debug("Background pre-resolve error for %r: %s", query, e)
            continue
    return False


def _store_discovery_pool_and_schedule_next(
    cid: int,
    session_key: tuple,
    tracks: list[Track],
    enforce_artist_diversity: bool = True,
) -> None:
    """Cache the active track pool for `(cid, session_key)` and schedule background pre-resolution of the next track."""
    if not cid or not tracks:
        return
    with _DISCOVERY_CACHE_LOCK:
        existing = _CHANNEL_DISCOVERY_CACHE.get(cid)
        if not existing or existing.get("session_key") != session_key:
            _CHANNEL_DISCOVERY_GEN[cid] += 1
        gen = _CHANNEL_DISCOVERY_GEN[cid]
        _CHANNEL_DISCOVERY_CACHE[cid] = {
            "session_key": session_key,
            "tracks": list(tracks),
            "pre_resolved": None,
            "generation": gen,
        }

    unplayed_rem = [t for t in tracks if not is_recently_played(t.title, t.artist, channel_id=cid)]
    if not unplayed_rem:
        return
    try:
        submit_to_worker(
            _pre_resolve_next_candidate_for_channel,
            cid,
            session_key,
            gen,
            enforce_artist_diversity,
        )
    except Exception as e:
        logger.debug("Failed to schedule background music pre-resolver: %s", e)


def _pre_resolve_top_candidate(
    candidates: list[Track],
    ui_zone: Optional[str] = None,
    d_tempo: int = 0,
    anchor_bpm: Optional[float] = None,
    active_genre: Optional[str] = None,
    active_mood: Optional[str] = None,
    active_artist: Optional[str] = None,
    active_similar_to: Optional[str] = None,
) -> str:
    """
    Pre-resolves a verified, Discord-embeddable YouTube Music link for the top candidate track
    (trying up to 4 candidates if one has Discord embedding disabled by its label).
    Eliminates an entire extra LLM tool-call round-trip on single-song discovery requests.
    """
    if not candidates:
        return ""

    # Filter out any tracks that have already been played in this session, and deprioritize the last played artist
    chan_deque = _get_music_channel_deque()
    last_artist = chan_deque[-1][1] if chan_deque else ""
    fresh_diff_artist = [
        c for c in candidates
        if not is_recently_played(c.title, c.artist) and (not last_artist or _norm_track_token(c.artist) != last_artist)
    ]
    fresh_any = [c for c in candidates if not is_recently_played(c.title, c.artist)]
    ordered_candidates = fresh_diff_artist or fresh_any or candidates

    best_fallback = None
    for cand in ordered_candidates[:4]:
        if best_fallback is not None and anchor_bpm is not None and cand.bpm is not None:
            if d_tempo > 0 and cand.bpm < anchor_bpm and cand.bpm < (best_fallback[0].bpm or 0) - 2.0:
                break
            if d_tempo < 0 and cand.bpm > anchor_bpm and cand.bpm > (best_fallback[0].bpm or 999) + 2.0:
                break

        query = f"{cand.title} by {cand.artist}"
        try:
            results = resolve_youtube_music_link(query, max_results=1)
            if results:
                r = results[0]
                if r.get("is_official_fallback"):
                    if best_fallback is None:
                        best_fallback = (cand, r)
                    continue
                if cand in candidates and candidates[0] is not cand:
                    candidates.remove(cand)
                    candidates.insert(0, cand)
                return _format_pre_resolved_entry(
                    cand,
                    r,
                    ui_zone=ui_zone,
                    active_genre=active_genre,
                    active_mood=active_mood,
                    active_artist=active_artist,
                    active_similar_to=active_similar_to,
                )
        except Exception as e:
            print(f"  └─ ⚠️ [Music Discovery Pre-Resolver]: Error resolving '{query}': {e}")
            continue

    if best_fallback is not None:
        win_c, win_r = best_fallback
        if win_c in candidates and candidates[0] is not win_c:
            candidates.remove(win_c)
            candidates.insert(0, win_c)
        return _format_pre_resolved_entry(
            win_c,
            win_r,
            ui_zone=ui_zone,
            active_genre=active_genre,
            active_mood=active_mood,
            active_artist=active_artist,
            active_similar_to=active_similar_to,
        )
    return ""


def _format_pre_resolved_entry(
    cand: Track,
    r: dict,
    ui_zone: Optional[str] = None,
    active_genre: Optional[str] = None,
    active_mood: Optional[str] = None,
    active_artist: Optional[str] = None,
    active_similar_to: Optional[str] = None,
) -> str:
    record_played_track(cand.title, cand.artist)
    record_played_track(r["title"], r["artist"])
    vid = r.get("id")
    genre = cand.genre or r.get("genre")
    album = cand.album or r.get("album")
    if vid and vid in MUSIC_METADATA_CACHE:
        if genre:
            MUSIC_METADATA_CACHE[vid]["genre"] = genre
        if album and not MUSIC_METADATA_CACHE[vid].get("album"):
            MUSIC_METADATA_CACHE[vid]["album"] = album

    record_channel_played_song(
        song=r["title"],
        artist=r["artist"],
        genre=genre,
        ui_zone=ui_zone,
        alt_song=cand.title,
        bpm=cand.bpm,
        speed_score=cand.speed_score,
        intensity_score=cand.intensity_score,
        gain_db=cand.gain,
        active_genre=active_genre,
        active_mood=active_mood,
        active_artist=active_artist,
        active_similar_to=active_similar_to,
    )

    lines = [
        "\n=== VERIFIED YOUTUBE MUSIC RESULT (PRE-RESOLVED & READY TO PRESENT) ===",
        f"Song: {r['title']}",
        f"Artist: {r['artist']}",
    ]
    if genre:
        lines.append(f"Genre: {genre}")
    if album:
        lines.append(f"Album: {album}")
    lines.append(f"Link: {r['url']}\n")
    return "\n".join(lines)


def skill_music_discovery(
    genre: Optional[str] = None,
    similar_to: Optional[str] = None,
    match_mood: Optional[bool] = False,
    artist: Optional[str] = None,
    mood: Optional[str] = None,
    shift: Optional[str] = None,
) -> str:
    """
    Discover and recommend music: top charting tracks by genre, mood/vibe tracks ('banger', 'melodic', 'hard', 'chill', 'sad', 'love'), 1-notch tempo/intensity shifts ('harder', 'softer', 'faster', 'slower'), similar songs, or top tracks by an artist.
    For one specific song by title (e.g. 'play Strobe by Deadmau5'), call `skill_youtube_search(query='<Song> by <Artist>', is_music=True)` instead.

    Args:
        genre: Genre or style for top charts (e.g. 'pop', 'techno', 'dnb', 'phonk', 'german rap'). Leave empty for all genres.
        similar_to: Seed song for similarity or spectrum shift, as 'Song Title by Artist Name'.
        match_mood: Set true to match the emotional mood/energy of `similar_to` rather than just its style.
        artist: Artist name to fetch their top tracks (e.g. 'Deadmau5', 'Daft Punk').
        mood: Mood or vibe keyword (e.g. 'banger', 'melodic', 'hard', 'dark', 'chill', 'sad', 'love', 'workout').
        shift: Relative 1-notch tempo/intensity shift from the current song: 'harder', 'softer', 'faster', or 'slower'.
    """
    # Resolve any YouTube / YouTube Music URL passed in similar_to, genre, or artist into 'Song by Artist'
    for _arg_val in (similar_to, genre, artist):
        if _arg_val and ("youtube.com/watch" in _arg_val or "youtu.be/" in _arg_val):
            _m_yt = re.search(
                r"https?://(?:music\.|www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([0-9A-Za-z_-]{11})",
                _arg_val,
            )
            if _m_yt:
                from src.skills.music.engine.formatting import get_music_metadata

                _meta = get_music_metadata(_m_yt.group(1))
                _s = str(_meta.get("song") or "").strip()
                _a = str(_meta.get("artist") or "").strip()
                if _s and _s.lower() not in ("unknown", "unknown track", "youtube music"):
                    similar_to = f"{_s} by {_a}" if (_a and _a.lower() not in ("unknown", "unknown artist")) else _s
                    if genre and ("youtube.com/watch" in genre or "youtu.be/" in genre):
                        genre = None
                    if artist and ("youtube.com/watch" in artist or "youtu.be/" in artist):
                        artist = None
                    break

    # If `genre` was populated with a similarity phrase (e.g. 'similar to Titan by Papa Pedro'), move it to `similar_to`
    if genre and not similar_to:
        _m_sim_g = re.match(
            r"^(?:similar\s+to|similar|songs?\s+like|tracks?\s+like|music\s+like|like|mix\s+with)\s+(.+)$",
            genre.strip(),
            flags=re.IGNORECASE,
        )
        if _m_sim_g:
            similar_to = _m_sim_g.group(1).strip()
            genre = None

    if similar_to:
        similar_to = re.sub(
            r"^(?:similar\s+to|similar|songs?\s+like|tracks?\s+like|music\s+like|like|mix\s+with)\s+",
            "",
            similar_to.strip(),
            flags=re.IGNORECASE,
        ).strip()

    shift_raw = (shift or "").strip()
    if not shift_raw and not artist:
        if mood and parse_spectrum_shift(mood) is not None:
            shift_raw = mood.strip()
        elif genre and not mood and parse_spectrum_shift(genre) is not None:
            g_check, m_check = parse_mood_and_genre(genre, None)
            if not m_check and g_check == genre.lower().strip():
                shift_raw = genre.strip()

    if shift_raw:
        if (
            shift_raw.startswith("NON_MUSIC_CONVERSATION:")
            or has_non_music_domain_context(shift_raw)
            or (similar_to and has_non_music_domain_context(similar_to))
        ):
            clean_shift_err = shift_raw.replace("NON_MUSIC_CONVERSATION:", "").strip()
            return (
                f"⚠️ '{clean_shift_err}' appears to be a non-music comparison or domain question. "
                "Do NOT use `skill_music_discovery` for non-music questions; answer the user's question directly or use `search_web` / `skill_sports_info`."
            )

        parsed_shift = parse_spectrum_shift(shift_raw)
        if parsed_shift is not None:
            d_tempo, d_intensity, shift_label = parsed_shift
            seed_song = ""
            seed_artist = ""
            seed_genre = None
            known_zone = None
            matched_last_played = False

            last_played = get_channel_last_played_song()
            if similar_to:
                s_meta = parse_song_meta(similar_to)
                seed_song = s_meta.get("song", "")
                seed_artist = s_meta.get("artist", "")
                seed_genre = s_meta.get("genre")
                if last_played:
                    lp_song = _norm_track_token(last_played.get("song") or "")
                    lp_alt = _norm_track_token(last_played.get("alt_song") or "")
                    lp_artist = _norm_track_token(last_played.get("artist") or "")
                    norm_seed_s = _norm_track_token(seed_song)
                    norm_seed_a = _norm_track_token(seed_artist)
                    if (
                        (norm_seed_s and (norm_seed_s in (lp_song, lp_alt) or lp_song in norm_seed_s or (lp_alt and lp_alt in norm_seed_s)))
                        or (norm_seed_a and lp_artist and (norm_seed_a in lp_artist or lp_artist in norm_seed_a))
                    ):
                        matched_last_played = True
                        seed_genre = seed_genre or last_played.get("genre")
                        known_zone = last_played.get("ui_zone")
            elif last_played:
                matched_last_played = True
                seed_song = last_played.get("song") or ""
                seed_artist = last_played.get("artist") or ""
                seed_genre = last_played.get("genre")
                known_zone = last_played.get("ui_zone")

            resolved_genre, start_zone = manager.resolve_track_genre_and_zone(
                song=seed_song,
                artist=seed_artist,
                known_genre=seed_genre,
                known_zone=known_zone,
            )
            step_info = step_spectrum_zone(start_zone, d_tempo, d_intensity)
            target_zone = step_info["target_zone"]
            target_zone_name = step_info["target_zone_name"]
            at_boundary = step_info["at_boundary"]
            used_default = step_info["used_default_start"]

            print(
                f"🎛️ [Executing Music Spectrum Shift] Direction: {shift_label} | "
                f"Seed: '{seed_song} by {seed_artist}' (Genre: {resolved_genre}, Start Zone: {start_zone or 'mid_mid (default)'}) "
                f"-> Target Zone: {target_zone_name} (Boundary Deep-Cuts: {at_boundary})"
            )

            anchor_profile = None
            try:
                if seed_song:
                    anchor_profile = audio_analyzer.get_track_acoustic_profile(seed_song, seed_artist)
                    if not anchor_profile and matched_last_played and last_played and last_played.get("alt_song"):
                        anchor_profile = audio_analyzer.get_track_acoustic_profile(last_played["alt_song"], seed_artist)
                if matched_last_played and last_played and last_played.get("bpm"):
                    if anchor_profile is None:
                        anchor_profile = {
                            "bpm": float(last_played["bpm"]),
                            "speed_score": float(last_played.get("speed_score") or 5.5),
                            "intensity_score": float(last_played.get("intensity_score") or 5.5),
                            "gain_db": float(last_played.get("gain_db") or -9.0),
                        }
                    else:
                        anchor_profile["bpm"] = float(last_played["bpm"])
                        if last_played.get("speed_score") is not None:
                            anchor_profile["speed_score"] = float(last_played["speed_score"])
                        if last_played.get("intensity_score") is not None:
                            anchor_profile["intensity_score"] = float(last_played["intensity_score"])
                if anchor_profile is not None:
                    anchor_profile["genre"] = resolved_genre or seed_genre
            except Exception as e:
                print(f"  └─ ⚠️ [Spectrum Anchor Profile]: {e}")

            exclude_q = f"{seed_song} by {seed_artist}" if seed_song and seed_artist else None
            try:
                all_tracks = manager.get_spectrum_tracks(
                    target_zone=target_zone,
                    prefer_deep_cuts=at_boundary,
                    exclude_query=exclude_q,
                    d_tempo=d_tempo,
                    d_intensity=d_intensity,
                    anchor_bpm=(anchor_profile or {}).get("bpm"),
                    seed_genre=resolved_genre,
                )
            except TypeError:
                all_tracks = manager.get_spectrum_tracks(
                    target_zone=target_zone,
                    prefer_deep_cuts=at_boundary,
                    exclude_query=exclude_q,
                )
            if not all_tracks:
                return f"No tracks found for spectrum zone '{target_zone_name}'."

            candidate_pool = MusicAggregatorManager.sample_tiered_tracks(
                all_tracks,
                k=20,
                weights=(0.45, 0.35, 0.20),
                enforce_artist_diversity=True,
            )

            try:
                candidate_pool = audio_analyzer.rank_candidates_by_acoustic_shift(
                    candidate_pool,
                    d_tempo=d_tempo,
                    d_intensity=d_intensity,
                    anchor_profile=anchor_profile,
                    max_analyze=14,
                )
            except Exception as e:
                print(f"  └─ ⚠️ [Spectrum Acoustic Re-Ranker]: {e}")

            top_sample = candidate_pool[:15]
            target_tags = target_zone.get("deep_cut_tags", []) if at_boundary else target_zone.get("mainstream_tags", [])
            shift_active_genre = (top_sample[0].genre if top_sample and top_sample[0].genre else None) or (target_tags[0] if target_tags else resolved_genre) or ""
            invalidate_channel_discovery_cache(get_active_channel_id())
            pre_resolved = _pre_resolve_top_candidate(
                top_sample,
                ui_zone=target_zone_name,
                d_tempo=d_tempo,
                anchor_bpm=(anchor_profile or {}).get("bpm"),
                active_genre=shift_active_genre,
                active_mood="",
                active_artist="",
                active_similar_to="",
            )

            styles_preview = ", ".join(
                (target_zone.get("deep_cut_tags", []) if at_boundary else target_zone.get("mainstream_tags", []))[:4]
            )
            acoustic_line = ""
            if top_sample and top_sample[0].bpm and top_sample[0].intensity_score is not None:
                win = top_sample[0]
                if anchor_profile and anchor_profile.get("bpm"):
                    acoustic_line = (
                        f"• Acoustic Verification: ~{int(round(anchor_profile['bpm']))} BPM, "
                        f"Intensity {anchor_profile['intensity_score']:.1f}/10 ({anchor_profile.get('gain_db', -9.0):+.1f} dB) "
                        f"➔ ~{int(round(win.bpm))} BPM, Intensity {win.intensity_score:.1f}/10 ({(win.gain or -9.0):+.1f} dB)\n"
                    )
                else:
                    acoustic_line = (
                        f"• Acoustic Verification: ~{int(round(win.bpm))} BPM | "
                        f"Speed {win.speed_score:.1f}/10 | Intensity {win.intensity_score:.1f}/10 ({(win.gain or -9.0):+.1f} dB)\n"
                    )

            if seed_song and not used_default:
                output = (
                    f"=== 🎛️ MUSIC SPECTRUM SHIFT: {shift_label.upper()} ===\n"
                    f"• Previous Track: {seed_song} by {seed_artist} "
                    f"(Genre: {resolved_genre or 'Detected'} | Zone: {step_info['start_zone']})\n"
                    f"• New Target Zone: {target_zone_name} "
                    f"(Tempo: {target_zone['tempo'].upper()}, Intensity: {target_zone['intensity'].upper()} | Styles: {styles_preview})\n"
                    f"{acoustic_line}\n"
                )
            else:
                output = (
                    f"=== 🎛️ MUSIC SPECTRUM SHIFT: {shift_label.upper()} (DEFAULT CENTER START) ===\n"
                    f"• Starting Zone: mid_mid (Center Default — no active song or identifiable genre)\n"
                    f"• New Target Zone: {target_zone_name} "
                    f"(Tempo: {target_zone['tempo'].upper()}, Intensity: {target_zone['intensity'].upper()} | Styles: {styles_preview})\n"
                    f"{acoustic_line}\n"
                )

            if pre_resolved:
                output += pre_resolved + "\n"
            elif top_sample:
                record_channel_played_song(
                    song=top_sample[0].title,
                    artist=top_sample[0].artist,
                    genre=top_sample[0].genre,
                    ui_zone=target_zone_name,
                    bpm=top_sample[0].bpm,
                    speed_score=top_sample[0].speed_score,
                    intensity_score=top_sample[0].intensity_score,
                    gain_db=top_sample[0].gain,
                    active_genre=shift_active_genre,
                    active_mood="",
                    active_artist="",
                    active_similar_to="",
                )

            for t in top_sample:
                output += t.display_line() + "\n"

            output += build_music_llm_instructions(
                shift_note=(
                    f"Briefly mention how you adjusted the vibe 1 notch "
                    f"({shift_label} -> {target_zone['tempo']} tempo, {target_zone['intensity']} intensity: {styles_preview})."
                )
            )
            return output

    if artist:
        print(f"🎵 [Executing Music Discovery Skill] Artist: {artist}")
        try:
            cid = get_active_channel_id()
            session_key = ("artist", _norm_track_token(artist))
            cached_tracks, pre_hit = _get_or_consume_cached_discovery(cid, session_key)
            tracks = cached_tracks if cached_tracks is not None else manager.get_artist_tracks(artist)
            if not tracks:
                return f"No tracks found for artist '{artist}'."

            top_sample = MusicAggregatorManager.sample_tiered_tracks(
                tracks,
                k=15,
                weights=(0.45, 0.35, 0.20),
                enforce_artist_diversity=False,
            )
            output = f"=== TOP TRACKS BY '{artist.title()}' ===\n\n"
            if pre_hit is not None:
                pre_cand, pre_r = pre_hit
                if pre_cand in top_sample and top_sample[0] is not pre_cand:
                    top_sample.remove(pre_cand)
                    top_sample.insert(0, pre_cand)
                elif pre_cand not in top_sample:
                    top_sample.insert(0, pre_cand)
                pre_resolved = _format_pre_resolved_entry(
                    pre_cand,
                    pre_r,
                    active_genre="",
                    active_mood="",
                    active_artist=artist.strip(),
                    active_similar_to="",
                )
            else:
                pre_resolved = _pre_resolve_top_candidate(
                    top_sample,
                    active_genre="",
                    active_mood="",
                    active_artist=artist.strip(),
                    active_similar_to="",
                )
            if pre_resolved:
                output += pre_resolved + "\n"
            elif top_sample:
                record_channel_played_song(
                    song=top_sample[0].title,
                    artist=top_sample[0].artist,
                    genre=top_sample[0].genre,
                    active_genre="",
                    active_mood="",
                    active_artist=artist.strip(),
                    active_similar_to="",
                )
            _store_discovery_pool_and_schedule_next(
                cid,
                session_key,
                tracks,
                enforce_artist_diversity=False,
            )

            for t in top_sample:
                output += t.display_line() + "\n"

            output += build_music_llm_instructions()
            return output
        except Exception as e:
            return f"Error finding tracks for artist '{artist}': {e}"

    if genre and re.match(
        r"^\s*(?:next|skip|another)(?:\s+(?:song|track|one|tune|please|pls))?\s*[.?!]*$",
        genre.strip(),
        re.IGNORECASE,
    ):
        genre = None

    if not genre and not similar_to and not artist and not mood and not shift_raw:
        try:
            from src.jobs.engine import JOB_ENGINE

            active_ap = JOB_ENGINE.get_active_autoplay_job(get_active_channel_id())
            if active_ap and active_ap.payload_text and active_ap.payload_text.lower() != "top tracks":
                ap_payload = active_ap.payload_text.strip()
                m_ap_sim = re.match(
                    r"^(?:similar\s+to|similar|songs?\s+like|tracks?\s+like|music\s+like|like|mix\s+with)\s+(.+)$",
                    ap_payload,
                    flags=re.IGNORECASE,
                )
                if m_ap_sim:
                    similar_to = m_ap_sim.group(1).strip()
                elif " by " in ap_payload.lower():
                    similar_to = ap_payload
                else:
                    genre = ap_payload
        except Exception:
            pass

    if genre and not similar_to and not artist and not mood and not shift_raw:
        m_spec_song = re.match(r"^(?:play\s+)?(?:the\s+song\s+)?(.+?\s+by\s+.+?)$", genre.strip(), re.IGNORECASE)
        if m_spec_song and not re.match(r"^(?:play\s+)?(?:a\s+|some\s+)?(?:songs?|tracks?|music)\s+by\s+", genre.strip(), re.IGNORECASE):
            from src.skills.youtube.youtube import skill_youtube_search
            spec_q = m_spec_song.group(1).strip()
            print(f"🔄 [Music Discovery Skill]: Redirecting specific song query '{genre}' -> skill_youtube_search(query='{spec_q}', is_music=True).")
            return skill_youtube_search(query=spec_q, max_results=1, is_music=True)

    raw_g = (genre or "").strip()
    raw_m = (mood or "").strip()
    if raw_g and raw_m and raw_m.lower() not in raw_g.lower():
        combined_g = f"{raw_m} {raw_g}".strip()
    else:
        combined_g = raw_g or raw_m
    parsed_genre = None if not combined_g or combined_g.lower() in ("all", "global", "general", "none") else combined_g
    parsed_mood = None

    if similar_to:
        print(f"🎵 [Executing Music Discovery Skill] Similar to: {similar_to} (match_mood={match_mood}, mood={parsed_mood})")
        try:
            from src.skills.music.aggregators.deezer import clean_playlist_genre_label

            cid = get_active_channel_id()
            sim_session_key = (
                "similar",
                _norm_track_token(similar_to),
                bool(match_mood),
                _norm_track_token(parsed_genre or ""),
            )
            cached_sim_tracks, sim_pre_hit = _get_or_consume_cached_discovery(cid, sim_session_key)

            last_played = get_channel_last_played_song()
            s_meta = parse_song_meta(similar_to)
            s_song = s_meta.get("song", "")
            s_artist = s_meta.get("artist", "")
            seed_matches_last_played = False
            if last_played:
                lp_s = _norm_track_token(last_played.get("song") or "")
                lp_alt = _norm_track_token(last_played.get("alt_song") or "")
                norm_s = _norm_track_token(s_song)
                if norm_s and (norm_s in (lp_s, lp_alt) or lp_s in norm_s):
                    seed_matches_last_played = True

            prev_active_genre = last_played.active_genre if (last_played and seed_matches_last_played) else None

            mood_tag = None
            if cached_sim_tracks is not None:
                tracks = cached_sim_tracks
                if parsed_genre:
                    mood_tag = parsed_genre.title()
            elif match_mood:
                tracks, mood_info = manager.get_mood_matched_tracks(similar_to)
                if mood_info.get("mood"):
                    mood_tag = mood_info["mood"].title()
            elif parsed_genre:
                sim_tracks = manager.get_similar_tracks(similar_to)
                genre_tracks = manager.get_top_charts(genre=parsed_genre)
                tracks = (sim_tracks or []) + (genre_tracks or [])
                mood_tag = parsed_genre.title()
            else:
                tracks = manager.get_similar_tracks(similar_to)

            if not tracks:
                return f"No similar songs found for '{similar_to}'."

            top_sample = MusicAggregatorManager.sample_tiered_tracks(
                tracks,
                k=15,
                weights=(0.50, 0.35, 0.15),
                enforce_artist_diversity=True,
            )
            is_unrelated_fallback = all(t.source == "fallback_unrelated" for t in top_sample)

            if not is_unrelated_fallback and top_sample and sim_pre_hit is None:
                try:
                    sim_anchor = None
                    inferred_sim_genre = prev_active_genre or s_meta.get("genre")
                    if last_played and seed_matches_last_played:
                        if not inferred_sim_genre and last_played.get("genre"):
                            inferred_sim_genre = last_played.get("genre")
                        if last_played.get("bpm"):
                            sim_anchor = {
                                "bpm": float(last_played["bpm"]),
                                "speed_score": float(last_played.get("speed_score") or 5.5),
                                "intensity_score": float(last_played.get("intensity_score") or 5.5),
                            }
                    if not inferred_sim_genre:
                        inferred_sim_genre = manager._infer_seed_genre(similar_to)
                    if inferred_sim_genre:
                        clean_sim_g = clean_playlist_genre_label(inferred_sim_genre)
                        for t in top_sample:
                            if not t.genre or (prev_active_genre and t.genre.lower() in ("dnb", "drum and bass", "drum & bass", "electronic", "dance")):
                                t.genre = clean_sim_g.title() if clean_sim_g == clean_sim_g.lower() else clean_sim_g
                    top_sample = audio_analyzer.rank_candidates_by_acoustic_similarity(
                        top_sample,
                        seed_song=s_song,
                        seed_artist=s_artist,
                        anchor_profile=sim_anchor,
                        max_analyze=6,
                    )
                except Exception as e:
                    logger.debug("Acoustic similarity verification skipped for %r: %s", similar_to, e)

            if is_unrelated_fallback:
                header = f"=== NO DIRECT SIMILAR SONGS FOUND FOR '{similar_to}' — FALLBACK SUGGESTIONS ===\n\n"
            elif mood_tag:
                header = f"=== SONGS MATCHING MOOD OF '{similar_to}' ({mood_tag}) ===\n\n"
            else:
                header = f"=== SONGS SIMILAR TO '{similar_to}' ===\n\n"

            output = header
            # When the user explicitly likes (👍) or requests tracks similar to a song,
            # anchor subsequent 'next' presses to that liked seed's YouTube Music Radio queue!
            if sim_pre_hit is not None:
                pre_cand, pre_r = sim_pre_hit
                if pre_cand in top_sample and top_sample[0] is not pre_cand:
                    top_sample.remove(pre_cand)
                    top_sample.insert(0, pre_cand)
                elif pre_cand not in top_sample:
                    top_sample.insert(0, pre_cand)
                pre_resolved = _format_pre_resolved_entry(
                    pre_cand,
                    pre_r,
                    active_genre="",
                    active_mood="",
                    active_artist="",
                    active_similar_to=similar_to,
                )
            else:
                pre_resolved = _pre_resolve_top_candidate(
                    top_sample,
                    active_genre="",
                    active_mood="",
                    active_artist="",
                    active_similar_to=similar_to,
                )
            if pre_resolved:
                output += pre_resolved + "\n"
            elif top_sample:
                record_channel_played_song(
                    song=top_sample[0].title,
                    artist=top_sample[0].artist,
                    genre=top_sample[0].genre,
                    active_genre="",
                    active_mood="",
                    active_artist="",
                    active_similar_to=similar_to,
                )
            if not is_unrelated_fallback:
                _store_discovery_pool_and_schedule_next(
                    cid,
                    sim_session_key,
                    tracks,
                    enforce_artist_diversity=True,
                )

            for t in top_sample:
                output += t.display_line() + "\n"

            output += build_music_llm_instructions(
                unrelated_seed=similar_to if is_unrelated_fallback else None
            )
            return output
        except Exception as e:
            return f"Error finding similar songs: {e}"

    print(f"🎵 [Executing Music Discovery Skill] Genre: {parsed_genre or 'All'}, Mood: {parsed_mood or 'None'}")
    try:
        from src.skills.music.aggregators.deezer import clean_playlist_genre_label

        cid = get_active_channel_id()
        raw_req_genre = clean_playlist_genre_label((genre or parsed_genre or "").strip())
        session_genre = raw_req_genre or (parsed_genre or "")
        session_mood = (mood or "").strip() if mood else (parsed_mood or "" if not session_genre else "")
        genre_session_key = (
            "genre",
            _norm_track_token(session_genre or "all"),
            _norm_track_token(session_mood or ""),
        )
        cached_genre_tracks, genre_pre_hit = _get_or_consume_cached_discovery(cid, genre_session_key)
        all_tracks = cached_genre_tracks if cached_genre_tracks is not None else manager.get_top_charts(genre=parsed_genre)
        is_artist_fallback = bool(
            parsed_genre
            and all_tracks
            and all((t.source or "").endswith("_artist") for t in all_tracks)
        )
        if is_artist_fallback:
            output = f"=== TOP TRACKS BY '{parsed_genre.title()}' ===\n\n"
            featured_sample = MusicAggregatorManager.sample_tiered_tracks(
                all_tracks,
                k=15,
                weights=(0.45, 0.35, 0.20),
                enforce_artist_diversity=False,
            )
            if genre_pre_hit is not None:
                pre_cand, pre_r = genre_pre_hit
                if pre_cand in featured_sample and featured_sample[0] is not pre_cand:
                    featured_sample.remove(pre_cand)
                    featured_sample.insert(0, pre_cand)
                elif pre_cand not in featured_sample:
                    featured_sample.insert(0, pre_cand)
                pre_resolved = _format_pre_resolved_entry(
                    pre_cand,
                    pre_r,
                    active_genre="",
                    active_mood="",
                    active_artist=parsed_genre.strip(),
                    active_similar_to="",
                )
            else:
                pre_resolved = _pre_resolve_top_candidate(
                    featured_sample,
                    active_genre="",
                    active_mood="",
                    active_artist=parsed_genre.strip(),
                    active_similar_to="",
                )
            if pre_resolved:
                output += pre_resolved + "\n"
            elif featured_sample:
                record_channel_played_song(
                    song=featured_sample[0].title,
                    artist=featured_sample[0].artist,
                    genre=featured_sample[0].genre,
                    active_genre="",
                    active_mood="",
                    active_artist=parsed_genre.strip(),
                    active_similar_to="",
                )
            _store_discovery_pool_and_schedule_next(
                cid,
                ("artist", _norm_track_token(parsed_genre)),
                all_tracks,
                enforce_artist_diversity=False,
            )
            for t in featured_sample:
                output += t.display_line() + "\n"
            output += "\n" + build_music_llm_instructions()
            return output

        output = f"=== TODAY'S TOP CHARTING MUSIC ({parsed_genre.upper() if parsed_genre else 'ALL GENRES'}) ===\n\n"
        if all_tracks:
            display_genre_label = session_genre.title() if session_genre == session_genre.lower() else session_genre

            featured_sample = MusicAggregatorManager.sample_tiered_tracks(
                all_tracks,
                k=15,
                weights=(0.45, 0.35, 0.20),
                enforce_artist_diversity=True,
            )
            if display_genre_label:
                for t in featured_sample:
                    if not t.genre or t.genre.strip().lower() in ("unknown", "music", "electronic", "dance", "dnb", "drum and bass", "drum & bass"):
                        t.genre = display_genre_label
                for t in all_tracks:
                    if not t.genre or t.genre.strip().lower() in ("unknown", "music", "electronic", "dance", "dnb", "drum and bass", "drum & bass"):
                        t.genre = display_genre_label

            if genre_pre_hit is not None:
                pre_cand, pre_r = genre_pre_hit
                if display_genre_label and (
                    not pre_cand.genre or pre_cand.genre.strip().lower() in ("unknown", "music", "electronic", "dance", "dnb", "drum and bass", "drum & bass")
                ):
                    pre_cand.genre = display_genre_label
                if pre_cand in featured_sample and featured_sample[0] is not pre_cand:
                    featured_sample.remove(pre_cand)
                    featured_sample.insert(0, pre_cand)
                elif pre_cand not in featured_sample:
                    featured_sample.insert(0, pre_cand)
                pre_resolved = _format_pre_resolved_entry(
                    pre_cand,
                    pre_r,
                    active_genre=session_genre,
                    active_mood=session_mood,
                    active_artist="",
                    active_similar_to="",
                )
            else:
                pre_resolved = _pre_resolve_top_candidate(
                    featured_sample,
                    active_genre=session_genre,
                    active_mood=session_mood,
                    active_artist="",
                    active_similar_to="",
                )
            if pre_resolved:
                output += pre_resolved + "\n"
            elif featured_sample:
                record_channel_played_song(
                    song=featured_sample[0].title,
                    artist=featured_sample[0].artist,
                    genre=featured_sample[0].genre,
                    active_genre=session_genre,
                    active_mood=session_mood,
                    active_artist="",
                    active_similar_to="",
                )
            _store_discovery_pool_and_schedule_next(
                cid,
                genre_session_key,
                all_tracks,
                enforce_artist_diversity=True,
            )

            output += f"[FEATURED DIVERSE SELECTION ({len(featured_sample)} Different Artists)]\n"
            for t in featured_sample:
                output += t.display_line() + "\n"
            output += "\n"
        else:
            output += "No charting music found.\n"

        output += build_music_llm_instructions()
        return output
    except Exception as e:
        return f"Error fetching top music: {e}"


def skill_music_analysis(
    track: Optional[str] = None,
    action: Optional[str] = AudioAction.BPM_KEY,
    second_track: Optional[str] = None,
    target_bpm: Optional[int] = None,
) -> str:
    """
    Audio analysis and DJ harmonic mixing via FreqBlog. Use ONLY when asked for BPM, musical key/Camelot, transition compatibility, harmonic DJ sets, or matching a song's acoustic mood.

    Args:
        track: Seed song as 'Song Title by Artist Name'.
        action: 'bpm_key' (default: BPM/Camelot/energy), 'match_mood' (acoustic mood match), 'transition' (mix score for track + second_track), 'bpm_search' (tracks at target_bpm), or 'harmonic_radio' (Camelot DJ set).
        second_track: Second song for action='transition', as 'Song Title by Artist Name'.
        target_bpm: Tempo to search for with action='bpm_search'.
    """
    if not track and not target_bpm:
        return "Please specify a track name or target BPM."

    act = (action or AudioAction.BPM_KEY).strip().lower()

    # 1. BPM and Key analysis
    if act == AudioAction.BPM_KEY:
        data = freqblog.get_audio_features(track)
        if not data or not data.get("bpm"):
            meta = parse_song_meta(track)
            prof = audio_analyzer.get_track_acoustic_profile(meta.get("song", track), meta.get("artist", ""))
            if prof and prof.get("bpm"):
                gemma_vibe = audio_analyzer.listen_with_gemma_audio(
                    mp3_bytes=prof.get("_mp3_bytes"),
                    preview_url=prof.get("preview_url"),
                    song=prof.get("title", track),
                    artist=prof.get("artist", ""),
                )
                output = f"=== AUDIO INTELLIGENCE FOR '{prof.get('title', track)}' ===\n"
                output += f"Artist: {prof.get('artist', 'Unknown')}\n"
                output += f"BPM: ~{prof['bpm']} (Half-time/Double-time available)\n"
                output += f"Speed Rating: {prof['speed_score']:.1f} / 10.0 (Onset Rate: {prof['onset_rate']:.2f}/s)\n"
                output += (
                    f"Acoustic Intensity: {prof['intensity_score']:.1f} / 10.0 "
                    f"(Master Gain: {prof['gain_db']:+.1f} dB | Spectral Brightness: {int(prof['spectral_centroid_hz'])} Hz)\n"
                )
                output += f"Spectrum Zone: {prof['ui_zone']}\n"
                if gemma_vibe:
                    if gemma_vibe.get("instruments"):
                        output += f"Instruments: {gemma_vibe['instruments']}\n"
                    if gemma_vibe.get("vocals"):
                        output += f"Vocals: {gemma_vibe['vocals']}\n"
                    if gemma_vibe.get("vibe"):
                        output += f"Mood / Vibe: {gemma_vibe['vibe']}\n"
                return output
            return f"Audio features for '{track}' could not be resolved in the catalog."
        bpm = data.get("bpm")
        key = data.get("key")
        camelot = data.get("camelot")
        energy = data.get("energy")
        mood = data.get("mood")
        genre = data.get("genre")

        output = f"=== AUDIO INTELLIGENCE FOR '{data.get('track_name', track)}' ===\n"
        output += f"Artist: {data.get('artist_name', 'Unknown')}\n"
        output += f"BPM: {bpm} (Half-time/Double-time available)\n"
        output += f"Key: {key} (Camelot: {camelot})\n"
        if energy is not None:
            output += f"Energy: {energy:.2f} / 1.00\n"
        if mood:
            output += f"Mood: {mood.title()}\n"
        if genre:
            output += f"Genre: {genre.title()}\n"
        return output

    # 2. Transition Scoring (Can I mix A into B?)
    elif act == AudioAction.TRANSITION:
        if not second_track:
            return "Please provide both 'track' and 'second_track' to score transition."
        res = freqblog.score_transition(track, second_track)
        if not res or res.get("error"):
            return f"Transition score unavailable: {res.get('error', 'Could not resolve tracks.')}"
        score = res.get("score", 0)
        reason = res.get("reason", "No transition details provided.")
        components = res.get("components", {})
        output = f"=== DJ MIX TRANSITION: '{track}' ➔ '{second_track}' ===\n"
        output += f"Mix Compatibility Score: {score}/100\n"
        output += f"Harmonic Alignment: {components.get('harmonic', 'N/A')}/100\n"
        output += f"Tempo Match: {components.get('tempo', 'N/A')}/100\n"
        output += f"Energy Smoothness: {components.get('energy', 'N/A')}/100\n"
        output += f"Analysis: {reason}\n"
        return output

    # 3. Find by BPM
    elif act == AudioAction.BPM_SEARCH:
        bpm_val = target_bpm or 128
        tracks = freqblog.find_by_bpm(bpm_val, tolerance=3, limit=10)
        if not tracks:
            return f"No tracks found matching {bpm_val} BPM."
        output = f"=== TRACKS MATCHING {bpm_val} BPM (±3) ===\n\n"
        for t in tracks:
            output += t.display_line() + "\n"
        return output

    # 4. Harmonic Radio / DJ Set
    elif act == AudioAction.HARMONIC_RADIO:
        tracks = freqblog.get_harmonic_radio(track, count=10)
        if not tracks:
            return f"Could not generate harmonic DJ set for '{track}'."
        output = f"=== HARMONIC DJ SET (Starting from '{track}') ===\n\n"
        for i, t in enumerate(tracks, 1):
            output += f"{i}. {t.title} by {t.artist}\n"
        return output

    # 5. Match Mood / Emotional Vibe
    elif act == AudioAction.MATCH_MOOD:
        parsed_g, parsed_m = parse_mood_and_genre(track)
        if parsed_m and " by " not in (track or "").lower() and " - " not in (track or ""):
            return skill_music_discovery(genre=parsed_g, mood=parsed_m)

        tracks, mood_info = manager.get_mood_matched_tracks(track)
        if not tracks:
            return f"Could not find any songs matching the mood or vibe of '{track}'."
        mood = mood_info.get("mood", "")
        energy = mood_info.get("energy")
        valence = mood_info.get("valence")
        seed_display = mood_info.get("seed_track", track)

        seen = set()
        unique = []
        for t in tracks:
            k = (t.title.lower(), t.artist.lower())
            if k not in seen:
                seen.add(k)
                unique.append(t)

        dsp_profile_line = ""
        if energy is None:
            try:
                meta = parse_song_meta(track)
                seed_prof = audio_analyzer.get_track_acoustic_profile(meta.get("song", track), meta.get("artist", ""))
                if seed_prof:
                    gemma_vibe = audio_analyzer.listen_with_gemma_audio(
                        mp3_bytes=seed_prof.get("_mp3_bytes"),
                        preview_url=seed_prof.get("preview_url"),
                        song=seed_prof.get("title", track),
                        artist=seed_prof.get("artist", ""),
                    )
                    if gemma_vibe and gemma_vibe.get("vibe"):
                        mood = gemma_vibe["vibe"]
                    dsp_profile_line = (
                        f"Audio Profile: ~{int(round(seed_prof['bpm']))} BPM | "
                        f"Speed: {seed_prof['speed_score']:.1f}/10 | "
                        f"Intensity: {seed_prof['intensity_score']:.1f}/10 ({seed_prof['gain_db']:+.1f} dB)"
                    )
                    if gemma_vibe and gemma_vibe.get("instruments"):
                        dsp_profile_line += f" | Instruments: {gemma_vibe['instruments']}"
                    dsp_profile_line += "\n\n"
                    unique = audio_analyzer.rank_candidates_by_acoustic_shift(
                        unique,
                        d_tempo=0,
                        d_intensity=0,
                        anchor_profile=seed_prof,
                        max_analyze=6,
                    )
            except Exception as e:
                logger.debug("Match mood DSP enrichment failed for %r: %s", track, e)

        mood_str = f" ({mood.title()})" if mood and mood.lower() != "similar" else ""
        output = f"=== TRACKS MATCHING MOOD OF '{seed_display}'{mood_str} ===\n"
        if energy is not None or valence is not None:
            output += "Audio Profile:"
            if energy is not None:
                output += f" Energy: {energy:.2f}/1.00"
            if valence is not None:
                output += f" | Valence: {valence:.2f}/1.00"
            output += "\n\n"
        elif dsp_profile_line:
            output += dsp_profile_line
        else:
            output += "\n"

        for t in unique[:15]:
            output += t.display_line() + "\n"

        output += build_music_llm_instructions(require_youtube_lookup=True)
        return output

    return f"Unknown action '{action}'."

