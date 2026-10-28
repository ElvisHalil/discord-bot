"""
Per-Channel Music Session State & Conversational Context Enrichment.

Manages:
- Per-channel active song context (`record_channel_played_song`, `get_channel_last_played_song`, `clear_channel_played_songs`)
- Rolling session track exclusion deque (`RECENTLY_PLAYED_TRACKS`, `record_played_track`, `is_recently_played`, `clear_recently_played_tracks`)
- Turn-0 music tool argument enrichment (`enrich_music_tool_args`) and missed shift detection (`detect_missed_music_shift_tool_call`)
"""

from collections import defaultdict, deque
import re
from typing import Dict, Optional, Tuple

from src.context import get_active_channel_id
from src.skills.music.catalogs import (
    EXPLICIT_MUSIC_CUE_WORDS as _EXPLICIT_MUSIC_CUE_WORDS,
)
from src.skills.music.models import PlayedSongRecord
from src.utils.cache import BoundedCache
from src.utils.domain_vocab import compile_word_boundary_re

# Per-channel active song context: {channel_id: PlayedSongRecord}
_CHANNEL_LAST_PLAYED_SONG: Dict[int, PlayedSongRecord] = BoundedCache(maxsize=128)

# Rolling session history of recently played/recommended tracks to prevent repeats across turns
# Scoped per channel_id so one chat's music history never affects another chat.
_CHANNEL_RECENTLY_PLAYED_TRACKS = defaultdict(lambda: deque(maxlen=80))


class _ChannelAwareDeque(deque):
    def clear(self) -> None:
        super().clear()
        _CHANNEL_RECENTLY_PLAYED_TRACKS.clear()


RECENTLY_PLAYED_TRACKS = _ChannelAwareDeque(maxlen=80)

_EXPLICIT_MUSIC_CUE_RE = compile_word_boundary_re(_EXPLICIT_MUSIC_CUE_WORDS)
_REPLY_SONG_CARD_RE = re.compile(
    r"\[Replying to song card:\s*'([^']+)'\s*by\s*'([^']+)'\]",
    re.IGNORECASE,
)


def _get_channel_id(channel_id: Optional[int] = None) -> int:
    if channel_id is not None:
        return channel_id
    return get_active_channel_id()


def _norm_track_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _clean_song_token(text: str) -> str:
    """Normalizes a song title after stripping parentheticals and dash suffixes (e.g., '- Slowed', '(Official Audio)')."""
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", "", text or "")
    s = re.sub(r"\s+[-–—|/]\s+.*$", "", s)
    return _norm_track_token(s)


def _get_music_channel_deque(channel_id: Optional[int] = None) -> deque:
    if channel_id is None:
        channel_id = get_active_channel_id()
    if not channel_id:
        return RECENTLY_PLAYED_TRACKS
    return _CHANNEL_RECENTLY_PLAYED_TRACKS[channel_id]


def clear_recently_played_tracks(channel_id: Optional[int] = None) -> None:
    if channel_id is not None:
        if channel_id == 0:
            super(_ChannelAwareDeque, RECENTLY_PLAYED_TRACKS).clear()
        else:
            _CHANNEL_RECENTLY_PLAYED_TRACKS.pop(channel_id, None)
    else:
        RECENTLY_PLAYED_TRACKS.clear()


def record_played_track(song: str, artist: str, channel_id: Optional[int] = None) -> None:
    """Records a song and artist in the channel's rolling session exclusion deque."""
    s_norm = _norm_track_token(song)
    s_clean = _clean_song_token(song)
    a_norm = _norm_track_token(artist)
    if not s_norm or s_norm in ("unknowntrack", "unknownsong", "unknown"):
        return
    target_deque = _get_music_channel_deque(channel_id)
    for tok in {s_norm, s_clean}:
        if tok:
            entry = (tok, a_norm)
            if entry in target_deque:
                target_deque.remove(entry)
            target_deque.append(entry)


def is_recently_played(song: str, artist: str = "", channel_id: Optional[int] = None) -> bool:
    """Checks whether a candidate track matches a recently played song in this channel's session."""
    target_deque = _get_music_channel_deque(channel_id)
    if not target_deque:
        return False
    s_norm = _norm_track_token(song)
    s_clean = _clean_song_token(song)
    a_norm = _norm_track_token(artist)
    if not s_norm:
        return False
    candidate_tokens = {t for t in (s_norm, s_clean) if t}
    for rec_s, rec_a in target_deque:
        if rec_s in candidate_tokens and (
            not a_norm or not rec_a or a_norm == rec_a or a_norm in rec_a or rec_a in a_norm
        ):
            return True
    return False


def record_channel_played_song(
    song: str,
    artist: str,
    genre: Optional[str] = None,
    ui_zone: Optional[str] = None,
    channel_id: Optional[int] = None,
    alt_song: Optional[str] = None,
    bpm: Optional[float] = None,
    speed_score: Optional[float] = None,
    intensity_score: Optional[float] = None,
    gain_db: Optional[float] = None,
    active_genre: Optional[str] = None,
    active_mood: Optional[str] = None,
    active_artist: Optional[str] = None,
    active_similar_to: Optional[str] = None,
    preserve_session: bool = False,
) -> None:
    """Record the most recently served song in a channel along with its genre, spectrum zone,
    acoustic metrics, and active discovery session state."""
    from src.skills.music.engine.spectrum import classify_genre_to_zone

    s_clean = (song or "").strip()
    a_clean = (artist or "").strip()
    alt_clean = (alt_song or "").strip() or None
    if not s_clean or s_clean.lower() in ("unknown track", "unknown"):
        return

    cid = _get_channel_id(channel_id)
    prev = _CHANNEL_LAST_PLAYED_SONG.get(cid)
    norm_s = _norm_track_token(s_clean)
    norm_alt = _norm_track_token(alt_clean or "")
    prev_s = _norm_track_token(prev.song if prev else "")
    prev_alt = _norm_track_token(prev.alt_song if prev and prev.alt_song else "")
    same_track = bool(
        prev
        and (norm_s in (prev_s, prev_alt) or (norm_alt and norm_alt in (prev_s, prev_alt)))
        and _norm_track_token(prev.artist) == _norm_track_token(a_clean)
    )

    eff_genre = (genre or "").strip() or (prev.genre if same_track and prev else None)
    eff_zone = ui_zone or (prev.ui_zone if same_track and prev else None)
    if not eff_zone and eff_genre:
        eff_zone = classify_genre_to_zone(eff_genre)

    has_session_update = any(
        x is not None for x in (active_genre, active_mood, active_artist, active_similar_to)
    )
    if has_session_update:
        eff_active_genre = (active_genre or "").strip() or None
        eff_active_mood = (active_mood or "").strip() or None
        eff_active_artist = (active_artist or "").strip() or None
        eff_active_sim = (active_similar_to or "").strip() or None
    elif (same_track or preserve_session) and prev:
        eff_active_genre = prev.active_genre
        eff_active_mood = prev.active_mood
        eff_active_artist = prev.active_artist
        eff_active_sim = prev.active_similar_to
    else:
        eff_active_genre = None
        eff_active_mood = None
        eff_active_artist = None
        eff_active_sim = None

    entry = PlayedSongRecord(
        song=s_clean,
        alt_song=alt_clean or (prev.alt_song if same_track and prev else None),
        artist=a_clean,
        genre=eff_genre,
        ui_zone=eff_zone,
        bpm=bpm if bpm is not None else (prev.bpm if same_track and prev else None),
        speed_score=speed_score if speed_score is not None else (prev.speed_score if same_track and prev else None),
        intensity_score=intensity_score if intensity_score is not None else (prev.intensity_score if same_track and prev else None),
        gain_db=gain_db if gain_db is not None else (prev.gain_db if same_track and prev else None),
        active_genre=eff_active_genre,
        active_mood=eff_active_mood,
        active_artist=eff_active_artist,
        active_similar_to=eff_active_sim,
    )
    _CHANNEL_LAST_PLAYED_SONG[cid] = entry


def get_channel_last_played_song(channel_id: Optional[int] = None) -> Optional[PlayedSongRecord]:
    """Retrieve the most recently served song in a specific channel."""
    cid = _get_channel_id(channel_id)
    return _CHANNEL_LAST_PLAYED_SONG.get(cid)


def clear_channel_played_songs(channel_id: Optional[int] = None) -> None:
    """Clear per-channel active song state (for one channel if channel_id is given, else all)."""
    if channel_id is not None:
        _CHANNEL_LAST_PLAYED_SONG.pop(channel_id, None)
    else:
        _CHANNEL_LAST_PLAYED_SONG.clear()


def _extract_raw_user_text(msg) -> str:
    """Strip Discord/Telegram wrapper prefixes ('User X says: ...') from a user message in history."""
    content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
    if not content:
        return ""
    text = str(content).strip()
    m = re.match(
        r"^(?:User\s+.+?\s+says(?:\s*\[.*?\])*|.+\(User ID:\s*\d+\)(?:\s*\[.*?\])*)\s*:\s*(.+)$",
        text,
        flags=re.DOTALL,
    )
    return m.group(1).strip() if m else text


def enrich_music_tool_args(args: dict, history: list) -> dict:
    """Enrich skill_music_discovery arguments with song-card reply seeds and validate 3-Tier
    music spectrum shift context so comparative words in normal conversation are never treated as shifts."""
    from src.skills.music.engine.spectrum import (
        has_non_music_domain_context,
        is_valid_music_shift_context,
        parse_spectrum_shift,
    )

    if not isinstance(args, dict) or not history:
        return args

    user_msgs = [
        m for m in history
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    ]
    if not user_msgs:
        return args

    last_msg = user_msgs[-1]
    raw_content = str(last_msg.get("content", "") if isinstance(last_msg, dict) else getattr(last_msg, "content", "") or "")
    clean_user = _extract_raw_user_text(last_msg)
    if not clean_user or clean_user.startswith("Now summarize") or clean_user.startswith("You have used"):
        return args

    enriched = dict(args)

    # Check 3-Tier Music Spectrum Shift context disambiguation
    valid_shift_ctx = is_valid_music_shift_context(raw_content, history)
    user_shift = parse_spectrum_shift(clean_user)
    arg_shift = (
        parse_spectrum_shift(str(enriched.get("shift") or ""))
        or parse_spectrum_shift(str(enriched.get("mood") or ""))
    )

    # 1. If the user is replying to a song card and didn't specify a seed song or artist, attach it
    # (unless they are explicitly switching to a specific genre without asking for similarity/shift)
    m_reply = _REPLY_SONG_CARD_RE.search(raw_content)
    if m_reply and not enriched.get("similar_to") and not enriched.get("artist"):
        wants_card_similarity = bool(
            not enriched.get("genre")
            or enriched.get("shift")
            or user_shift is not None
            or re.search(r"\b(?:similar|like\s+this|like\s+that|same\s+vibe|another\s+one)\b", clean_user, re.IGNORECASE)
        )
        if wants_card_similarity:
            enriched["similar_to"] = f"{m_reply.group(1).strip()} by {m_reply.group(2).strip()}"

    # 2. Apply or sanitize spectrum shift parameter
    if valid_shift_ctx and user_shift is not None:
        if not enriched.get("shift") and not enriched.get("genre") and not enriched.get("artist"):
            enriched["shift"] = clean_user
    elif (user_shift is not None or arg_shift is not None) and not valid_shift_ctx:
        has_explicit_music_request = bool(
            enriched.get("genre")
            or enriched.get("artist")
            or enriched.get("similar_to")
            or (enriched.get("mood") and not parse_spectrum_shift(str(enriched.get("mood") or "")))
            or _EXPLICIT_MUSIC_CUE_RE.search(clean_user)
        )
        if has_explicit_music_request:
            enriched.pop("shift", None)
        elif has_non_music_domain_context(clean_user) or not enriched.get("genre"):
            enriched["shift"] = f"NON_MUSIC_CONVERSATION: {clean_user}"

    # 3. Inherit active channel genre when user asks for a standalone mood (e.g., 'play a banger')
    # while already listening to a genre in this channel
    if (
        enriched.get("mood")
        and not enriched.get("genre")
        and not enriched.get("similar_to")
        and not enriched.get("artist")
        and not enriched.get("shift")
    ):
        active_genre = None
        last_played = get_channel_last_played_song()
        if last_played and last_played.get("genre"):
            lp_g = last_played["genre"].strip()
            if lp_g.lower() not in ("acoustic dsp", "detected", "unknown"):
                active_genre = lp_g
        if not active_genre:
            for m in reversed(history):
                role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
                if role == "assistant":
                    c_text = str(m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "") or "")
                    m_g = re.search(r"(?im)^\s*(?:\*\*)?Genre:\*{0,2}\s*(.+)$", c_text)
                    if m_g:
                        active_genre = m_g.group(1).strip()
                    break
        if active_genre:
            enriched["genre"] = active_genre

    # 4. On bare 'next' / 'skip' (or ⏭️ Next button), keep the active station/genre rather than chaining
    # `similar_to` off the song the user just skipped!
    if _BARE_NEXT_SKIP_RE.match(clean_user):
        next_args = _resolve_next_turn_music_args(history)
        if next_args is not None:
            enriched.clear()
            enriched.update(next_args)

    return enriched


_BARE_NEXT_SKIP_RE = re.compile(
    r"^\s*(?:next|skip)(?:\s+(?:song|track|one|tune|please|pls))?\s*[.?!]*$",
    re.IGNORECASE,
)


def _extract_genre_or_music_request_from_user_text(u_text: str) -> Optional[dict]:
    """
    Parse a historical user message (excluding bare 'next'/'skip') to see if it established
    an active music session (genre/mood, artist, or 👍 liked-track seed).
    """
    if not u_text or _BARE_NEXT_SKIP_RE.match(u_text):
        return None
    if u_text.startswith("!") or u_text.startswith("Now summarize") or u_text.startswith("You have used"):
        return None

    # 1. 👍 reaction recorded in chat_history: "<User> reacted with 👍 to '<Song> by <Artist>' requesting a similar track."
    m_like = re.search(
        r"reacted\s+with\s+👍\s+to\s+(.+?)\s+requesting\s+a\s+similar\s+track",
        u_text,
        re.IGNORECASE,
    )
    if m_like:
        raw_seed = m_like.group(1).strip().strip("'\"")
        raw_seed = re.sub(r"'\s+by\s+'", " by ", raw_seed, flags=re.IGNORECASE).strip("'\" ")
        if raw_seed:
            return {"similar_to": raw_seed}

    # 2. Explicit similarity request: "play songs similar to X", "something like X"
    m_sim = re.match(
        r"^\s*(?:can\s+you\s+|could\s+you\s+|please\s+)?(?:play|put\s+on|find|recommend|give\s+me)?\s*"
        r"(?:me\s+|us\s+)?(?:some\s+|something\s+|a\s+|any\s+)?(?:songs?\s+|tracks?\s+|music\s+)?"
        r"(?:similar\s+to|like|mix\s+with)\s+(.+?)\s*[.?!]*$",
        u_text,
        re.IGNORECASE,
    )
    if m_sim:
        sim_target = m_sim.group(1).strip(" .,!?'\"")
        if sim_target and sim_target.lower() not in ("this", "that", "it"):
            return {"similar_to": sim_target}

    # 3. Artist request: "play songs by <Artist>"
    m_by = _PLAY_BY_ARTIST_RE.match(u_text)
    if m_by:
        art = m_by.group(1).strip(" .,!?")
        if art:
            return {"artist": art}

    # 4. Genre or mood request: "play liquid drum and bass genre", "play liquid drum and bass", "liquid drum and bass"
    from src.skills.music.catalogs import BEATPORT_GENRE_QUERIES, GENRE_CANONICAL_MAP
    from src.skills.music.engine.moods import parse_mood_and_genre
    from src.skills.music.engine.spectrum import classify_genre_to_zone, has_non_music_domain_context

    if has_non_music_domain_context(u_text):
        return None

    cand = re.sub(
        r"^\s*(?:can\s+you\s+|could\s+you\s+|please\s+|yo\s+|hey\s+)?"
        r"(?:play|put\s+on|queue|drop|recommend|give\s+me|stream|stay\s+with|stick\s+with|stick\s+to|keep\s+playing|switch\s+to|change\s+to|back\s+to)\s+"
        r"(?:me\s+|us\s+)?(?:some\s+|a\s+|an\s+|more\s+)?",
        "",
        u_text,
        flags=re.IGNORECASE,
    ).strip()
    cand = re.sub(
        r"\s+(?:genre|music|songs?|tracks?|tunes?|vibes?|for\s+(?:me|us)|please|pls)\s*[.?!]*$",
        "",
        cand,
        flags=re.IGNORECASE,
    ).strip(" .,!?")

    if not cand or len(cand) > 45:
        return None

    cand_low = cand.lower()
    if (
        cand_low in GENRE_CANONICAL_MAP
        or cand_low in BEATPORT_GENRE_QUERIES
        or classify_genre_to_zone(cand_low) is not None
    ):
        return {"genre": cand_low}

    p_genre, p_mood = parse_mood_and_genre(cand_low, None)
    if p_mood is not None:
        if p_genre and (
            p_genre in GENRE_CANONICAL_MAP
            or p_genre in BEATPORT_GENRE_QUERIES
            or classify_genre_to_zone(p_genre) is not None
        ):
            return {"genre": cand_low}
        if not p_genre:
            return {"mood": p_mood}

    return None


def _resolve_next_turn_music_args(history: list, channel_id: Optional[int] = None) -> Optional[dict]:
    """
    Resolve the active station/genre arguments when the user says 'next'/'skip' or clicks ⏭️ Next:
    1. If Autoplay is active, keep the Autoplay job's seed/genre.
    2. Otherwise, inspect previous user turns in `history` for the most recent explicit genre/mood/artist/👍 request.
    3. Otherwise, use `last_played.genre` or the most recent Song Card's `Genre:` line in `history`.
    4. Never chain `similar_to` off a song that the user just skipped with 'next' when a genre is active.
    """
    from src.skills.music.aggregators.deezer import clean_playlist_genre_label

    cid = _get_channel_id(channel_id)
    try:
        from src.jobs.engine import JOB_ENGINE

        active_ap = JOB_ENGINE.get_active_autoplay_job(cid)
        if active_ap:
            g = (
                active_ap.payload_text.strip()
                if active_ap.payload_text and active_ap.payload_text.lower() != "top tracks"
                else None
            )
            if g:
                m_sim = re.match(
                    r"^(?:similar\s+to|similar|songs?\s+like|tracks?\s+like|music\s+like|like|mix\s+with)\s+(.+)$",
                    g,
                    flags=re.IGNORECASE,
                )
                if m_sim:
                    return {"similar_to": m_sim.group(1).strip()}
                if " by " in g.lower():
                    return {"similar_to": g}
                return {"genre": g}
            return {}
    except Exception:
        pass

    last_song = get_channel_last_played_song(cid)
    if last_song:
        if last_song.active_genre or last_song.active_mood:
            res_active: dict = {}
            if last_song.active_genre:
                res_active["genre"] = clean_playlist_genre_label(last_song.active_genre)
            if last_song.active_mood:
                res_active["mood"] = last_song.active_mood
            return res_active
        if last_song.active_artist:
            return {"artist": last_song.active_artist}
        if last_song.active_similar_to:
            return {"similar_to": last_song.active_similar_to}

    # Fallback when in-memory session state was not populated via skill_music_discovery:
    # walk backwards through prior user messages in history (excluding the current 'next'/'skip' message)
    if history and len(history) > 1:
        for m in reversed(history[:-1]):
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            if role == "user":
                u_text = _extract_raw_user_text(m)
                req = _extract_genre_or_music_request_from_user_text(u_text)
                if req is not None:
                    return req

    if last_song and last_song.genre:
        lp_g = last_song.genre.strip()
        if lp_g and not lp_g.lower().startswith("similar to") and lp_g.lower() not in ("unknown", "detected", "acoustic dsp"):
            return {"genre": clean_playlist_genre_label(lp_g)}

    if history:
        for m in reversed(history):
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            if role == "assistant":
                c_text = str(m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "") or "")
                m_g = re.search(r"(?im)^\s*(?:\*\*)?Genre:\*{0,2}\s*(.+)$", c_text)
                if m_g:
                    return {"genre": clean_playlist_genre_label(m_g.group(1).strip())}
                if "**Song:**" in c_text:
                    break

    if last_song and last_song.song and last_song.artist:
        return {"similar_to": f"{last_song.song} by {last_song.artist}"}
    if last_song:
        return {}
    return None


_GENERIC_PLAY_SONG_RE = re.compile(
    r"^\s*(?:can\s+you\s+|could\s+you\s+|please\s+|yo\s+|hey\s+)?(?:play|put\s+on|queue|drop|recommend|give\s+me)\s+(?:me\s+|us\s+)?"
    r"(?:(?:a|some|any|another)\s+)?(?:([a-z0-9\s&-]{2,30}?)\s+)?(?:song|track|music|tune|banger)s?(?:\s+for\s+(?:me|us))?\s*[.?!]*$",
    re.IGNORECASE,
)
_PLAY_BY_ARTIST_RE = re.compile(
    r"^\s*(?:can\s+you\s+|could\s+you\s+|please\s+)?(?:play|put\s+on|recommend)\s+(?:me\s+)?"
    r"(?:a\s+|some\s+)?(?:song|track|music)s?\s+by\s+([A-Za-z0-9\s.&'-]{2,40}?)\s*[.?!]*$",
    re.IGNORECASE,
)
_PLAY_SPECIFIC_BY_ARTIST_RE = re.compile(
    r"^\s*(?:can\s+you\s+|could\s+you\s+|please\s+)?(?:play|put\s+on)\s+(?:the\s+song\s+)?"
    r"([A-Za-z0-9\s'&-]{2,45}\s+by\s+[A-Za-z0-9\s.&'-]{2,40}?)\s*[.?!]*$",
    re.IGNORECASE,
)
_GENERIC_QUALIFIER_STOPWORDS = frozenset({
    "a", "an", "some", "any", "another", "good", "great", "nice", "cool", "random", "new", "me", "us", "the", "your", "favorite",
})


def detect_missed_music_shift_tool_call(history: list) -> Optional[Tuple[str, dict]]:
    """Detect when a model skips calling skill_music_discovery / skill_youtube_search on a valid
    3-Tier music spectrum shift or an explicit 'play a song' / 'play <genre> music' request."""
    from src.skills.music.engine.spectrum import (
        has_non_music_domain_context,
        is_valid_music_shift_context,
    )

    if not history:
        return None

    user_msgs = [
        m for m in history
        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
    ]
    if not user_msgs:
        return None

    last_msg = user_msgs[-1]
    raw_content = str(last_msg.get("content", "") if isinstance(last_msg, dict) else getattr(last_msg, "content", "") or "")
    clean_user = _extract_raw_user_text(last_msg)
    if not clean_user or clean_user.startswith("!") or clean_user.startswith("Now summarize") or clean_user.startswith("You have used"):
        return None

    if is_valid_music_shift_context(raw_content, history):
        args = enrich_music_tool_args({"shift": clean_user}, history)
        return ("skill_music_discovery", args)

    if has_non_music_domain_context(clean_user):
        return None

    # Strip compound voice-channel join/target phrases (e.g., "join voice and play dnb", "play house music in vc")
    music_clause = re.sub(
        r"^\s*(?:please\s+|yo\s+|hey\s+)?(?:join|connect\s+to|hop\s+in(?:to)?|come\s+in(?:to)?|enter)\s+"
        r"(?:the\s+|our\s+|my\s+)?(?:voice(?:\s+channel|\s+chat|\s+call)?|vc)\s+(?:and|then|,)\s+",
        "",
        clean_user,
        flags=re.IGNORECASE,
    ).strip()
    music_clause = re.sub(
        r"\s+(?:in|into|on)\s+(?:the\s+|our\s+|my\s+)?(?:voice(?:\s+channel|\s+chat|\s+call)?|vc)\s*[.?!]*$",
        "",
        music_clause,
        flags=re.IGNORECASE,
    ).strip()
    music_clause = re.sub(r"^\s*stream\s+", "play ", music_clause, flags=re.IGNORECASE)

    m_by = _PLAY_BY_ARTIST_RE.match(music_clause)
    if m_by:
        artist = m_by.group(1).strip(" .,!?")
        if artist:
            return ("skill_music_discovery", {"artist": artist})

    m_spec = _PLAY_SPECIFIC_BY_ARTIST_RE.match(music_clause)
    if m_spec:
        query = m_spec.group(1).strip(" .,!?")
        if query:
            return ("skill_youtube_search", {"query": query, "max_results": 1, "is_music": True})

    m_gen = _GENERIC_PLAY_SONG_RE.match(music_clause)
    if m_gen:
        qualifier = (m_gen.group(1) or "").strip(" .,!?").lower()
        words = [w for w in qualifier.split() if w not in _GENERIC_QUALIFIER_STOPWORDS]
        clean_genre = " ".join(words).strip()
        if clean_genre:
            return ("skill_music_discovery", {"genre": clean_genre})
        return ("skill_music_discovery", {})

    # Also match "play <genre>" without trailing "music/song" (e.g. "play dnb", "join voice and play drum and bass")
    m_bare_genre = re.match(
        r"^\s*(?:can\s+you\s+|could\s+you\s+|please\s+)?(?:play|put\s+on|drop)\s+(?:some\s+)?([a-z0-9\s&-]{2,30}?)\s*[.?!]*$",
        music_clause,
        re.IGNORECASE,
    )
    if m_bare_genre:
        from src.skills.music.engine.spectrum import classify_genre_to_zone

        cand_g = m_bare_genre.group(1).strip(" .,!?").lower()
        if classify_genre_to_zone(cand_g) is not None:
            return ("skill_music_discovery", {"genre": cand_g})

    if _BARE_NEXT_SKIP_RE.match(clean_user):
        next_args = _resolve_next_turn_music_args(history)
        if next_args is not None:
            return ("skill_music_discovery", next_args)

    return None

