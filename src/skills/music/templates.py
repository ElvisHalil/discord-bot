"""
Shared response template for presenting a song.

Kept in one place so skill_top_music and skill_youtube_searcher give the model
identical instructions. The field layout (**Song:** / **Artist:** / bare URL)
is also what standardize_song_reply() in src/skills/music/utils.py parses, so
change both together.
"""

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
