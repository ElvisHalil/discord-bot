"""
Shared response templates and concise LLM prompt instruction builders for the Music Skill.

Kept in one place so `skill_top_music`, `skill_audio_intelligence`, and `skill_youtube_searcher`
give the model identical, token-efficient instructions. The field layout (`**Song:**` / `**Artist:**` / bare URL)
is also what `standardize_song_reply()` in `src/skills/music/utils.py` parses, so change both together.
"""

from typing import Optional

SONG_TEMPLATE = """=== SONG RESPONSE TEMPLATE (no emojis in the song card) ===
[If the user also asked a question or requested other info (e.g. sports, odds, news, facts), answer that part FIRST in full (calling any other needed tools first), then place the song card below:]
[Optional one-line music intro]

**Song:** <Song Title>
**Artist:** <Artist Name>
**Genre:** <Genre> (only if known)
**Album:** <Album> (only if known)

<bare YouTube Music URL on its own line>

Rules: always include Song and Artist. Never invent a URL. Paste the YouTube Music URL bare - no [text](url), no <>, no other markdown links for the song - so Discord embeds the player.
"""


def build_music_llm_instructions(
    shift_note: Optional[str] = None,
    unrelated_seed: Optional[str] = None,
    require_youtube_lookup: bool = False,
) -> str:
    """
    Build a concise, centralized instruction block for `skill_top_music` and `skill_audio_intelligence`.
    """
    lines = ["\n=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ==="]
    if shift_note:
        lines.append(shift_note)
    if unrelated_seed:
        lines.append(
            f"IMPORTANT: No directly related songs were found for '{unrelated_seed}'. "
            "Clearly let the user know that you couldn't find any directly related songs for that track, "
            "and suggest ONE track from above that they might like instead."
        )
    if require_youtube_lookup:
        lines.append(
            "Select ONE best matching song from above and call `skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)` "
            "to fetch its verified YouTube Music link before presenting it with the template below.\n"
        )
    else:
        lines.append(
            "CRITICAL: If `=== VERIFIED YOUTUBE MUSIC RESULT ===` is provided above, present THAT EXACT Song, Artist, "
            "and Link immediately using the template below without calling `skill_youtube_searcher` again. "
            "Only call `skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)` if no pre-resolved link is shown or if the user asked for multiple songs.\n"
        )
    lines.append(SONG_TEMPLATE)
    return "\n".join(lines)
