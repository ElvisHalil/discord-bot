from src.skills.music.aggregators.freqblog import FreqBlogAggregator

freqblog = FreqBlogAggregator()

def skill_audio_intelligence(
    track: str = None,
    action: str = "bpm_key",
    second_track: str = None,
    target_bpm: int = None
) -> str:
    """
    Audio analysis and DJ harmonic mixing intelligence powered by FreqBlog.
    Use ONLY when the user explicitly asks for tempo (BPM), musical key/Camelot, 
    audio transition mixing compatibility, or harmonic DJ sets.
    - action='bpm_key': Retrieves exact BPM, Camelot key, mood, and energy for a track.
    - action='transition': Scores how cleanly two tracks mix together (requires 'track' and 'second_track').
    - action='bpm_search': Finds tracks at a specific tempo (requires 'target_bpm').
    - action='harmonic_radio': Builds a continuous Camelot-compatible DJ set starting from 'track'.
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

    return f"Unknown action '{action}'."

