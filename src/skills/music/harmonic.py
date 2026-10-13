from typing import Optional
from src.skills.music.aggregators.freqblog import FreqBlogAggregator
from src.skills.music.aggregators.manager import MusicAggregatorManager

freqblog = FreqBlogAggregator()
manager = MusicAggregatorManager()

def skill_audio_intelligence(
    track: Optional[str] = None,
    action: Optional[str] = "bpm_key",
    second_track: Optional[str] = None,
    target_bpm: Optional[int] = None
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

    # 1. BPM and Key analysis
    if action == "bpm_key":
        data = freqblog.get_audio_features(track)
        if not data or not data.get("bpm"):
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
    elif action == "transition":
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
    elif action == "bpm_search":
        bpm_val = target_bpm or 128
        tracks = freqblog.find_by_bpm(bpm_val, tolerance=3, limit=10)
        if not tracks:
            return f"No tracks found matching {bpm_val} BPM."
        output = f"=== TRACKS MATCHING {bpm_val} BPM (±3) ===\n\n"
        for t in tracks:
            output += t.display_line() + "\n"
        return output

    # 4. Harmonic Radio / DJ Set
    elif action == "harmonic_radio":
        tracks = freqblog.get_harmonic_radio(track, count=10)
        if not tracks:
            return f"Could not generate harmonic DJ set for '{track}'."
        output = f"=== HARMONIC DJ SET (Starting from '{track}') ===\n\n"
        for i, t in enumerate(tracks, 1):
            output += f"{i}. {t.title} by {t.artist}\n"
        return output

    # 5. Match Mood / Emotional Vibe
    elif action == "match_mood":
        from src.skills.music.moods import parse_mood_and_genre
        from src.skills.music.top_music import skill_top_music
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

        mood_str = f" ({mood.title()})" if mood and mood.lower() != "similar" else ""
        output = f"=== TRACKS MATCHING MOOD OF '{seed_display}'{mood_str} ===\n"
        if energy is not None or valence is not None:
            output += "Audio Profile:"
            if energy is not None:
                output += f" Energy: {energy:.2f}/1.00"
            if valence is not None:
                output += f" | Valence: {valence:.2f}/1.00"
            output += "\n\n"
        else:
            output += "\n"

        # Deduplicate
        seen = set()
        unique = []
        for t in tracks:
            k = (t.title.lower(), t.artist.lower())
            if k not in seen:
                seen.add(k)
                unique.append(t)

        for t in unique[:15]:
            output += t.display_line() + "\n"

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

    return f"Unknown action '{action}'."

