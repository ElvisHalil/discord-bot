import logging
from typing import Optional

from src.skills.music import audio_analyzer
from src.skills.music.aggregators.freqblog import FreqBlogAggregator
from src.skills.music.aggregators.manager import MusicAggregatorManager
from src.skills.music.enums import AudioAction
from src.skills.music.moods import parse_mood_and_genre
from src.skills.music.templates import build_music_llm_instructions
from src.skills.music.top_music import skill_top_music
from src.skills.music.utils import parse_song_meta

logger = logging.getLogger(__name__)

freqblog = FreqBlogAggregator()
manager = MusicAggregatorManager()


def skill_audio_intelligence(
    track: Optional[str] = None,
    action: Optional[str] = AudioAction.BPM_KEY,
    second_track: Optional[str] = None,
    target_bpm: Optional[int] = None,
) -> str:
    """
    Audio analysis and DJ harmonic mixing intelligence powered by FreqBlog.
    Use ONLY when the user explicitly asks for tempo (BPM), musical key/Camelot,
    audio transition mixing compatibility, harmonic DJ sets, or matching a song's emotional mood/vibe.
    - action='match_mood': Finds songs that acoustically and emotionally match the mood, energy, and vibe of 'track'.
    - action='bpm_key': Retrieves exact BPM, Camelot key, mood, and energy for a track.
    - action='transition': Scores how cleanly two tracks mix together (requires 'track' and 'second_track').
    - action='bpm_search': Finds tracks at a specific tempo (requires 'target_bpm').
    - action='harmonic_radio': Builds a continuous Camelot-compatible DJ set starting from 'track'.

    Args:
        track: Seed song as 'Song Title by Artist Name'.
        action: One of 'bpm_key' (default), 'match_mood', 'transition', 'bpm_search', 'harmonic_radio'.
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
            return skill_top_music(genre=parsed_g, mood=parsed_m)

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
