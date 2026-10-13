"""
Shared response template for presenting a song.

Kept in one place so skill_top_music and skill_youtube_searcher give the model
identical instructions. The field layout (**Song:** / **Artist:** / bare URL)
is also what standardize_song_reply() in src/skills/music/utils.py parses, so
change both together.
"""

SONG_TEMPLATE = """=== SONG RESPONSE TEMPLATE (no emojis) ===
[Optional one-line intro]

**Song:** <Song Title>
**Artist:** <Artist Name>
**Genre:** <Genre> (only if known)
**Album:** <Album> (only if known)

<bare YouTube Music URL on its own line>

Rules: always include Song and Artist. Never invent a URL. Paste the URL bare - no [text](url), no <>, no other markdown links - so Discord embeds the player.
"""
