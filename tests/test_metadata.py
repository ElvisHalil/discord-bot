import sys
import unittest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.skills.music.utils import (
    parse_song_meta,
    format_song_card,
    is_placeholder_text,
)


class TestMusicMetadata(unittest.TestCase):
    """Test suite for song title/artist parsing and Discord card formatting."""

    def test_topic_channel_parsing(self):
        meta = parse_song_meta("Step Down", author="shftr - Topic")
        self.assertEqual(meta["song"], "Step Down")
        self.assertEqual(meta["artist"], "shftr")

    def test_title_with_official_audio_tag(self):
        meta = parse_song_meta("shftr - Step Down (Official Audio)", author="shftr")
        self.assertEqual(meta["song"], "Step Down")
        self.assertEqual(meta["artist"], "shftr")

    def test_delimiter_with_mix_tag(self):
        meta = parse_song_meta("Anyma - Running [Original Mix]", author="Afterlife")
        self.assertEqual(meta["song"], "Running")
        self.assertEqual(meta["artist"], "Anyma")

    def test_by_keyword_with_genre(self):
        meta = parse_song_meta("Apex by Bicep (Genre: Techno)")
        self.assertEqual(meta["song"], "Apex")
        self.assertEqual(meta["artist"], "Bicep")
        self.assertEqual(meta["genre"], "Techno")

    def test_topic_single_word(self):
        meta = parse_song_meta("Glue", author="Bicep - Topic")
        self.assertEqual(meta["song"], "Glue")
        self.assertEqual(meta["artist"], "Bicep")

    def test_query_fallback(self):
        meta = parse_song_meta("Believe", author="Cher", query="Believe by Cher")
        self.assertEqual(meta["song"], "Believe")
        self.assertEqual(meta["artist"], "Cher")

    def test_album_and_genre_tags(self):
        meta = parse_song_meta("Alien Superstar (Album: Renaissance, Genre: House)", author="Beyonce")
        self.assertEqual(meta["song"], "Alien Superstar")
        self.assertEqual(meta["artist"], "Beyonce")
        self.assertEqual(meta["album"], "Renaissance")
        self.assertEqual(meta["genre"], "House")

    def test_format_song_card(self):
        card = format_song_card(
            song="Strobe",
            artist="deadmau5",
            url="https://music.youtube.com/watch?v=tKi9Zwc53Sw",
            genre="Progressive House",
            album="For Lack of a Better Name",
            intro="Here is a classic track:"
        )
        self.assertIn("**Song:** Strobe", card)
        self.assertIn("**Artist:** deadmau5", card)
        self.assertIn("**Genre:** Progressive House", card)
        self.assertIn("**Album:** For Lack of a Better Name", card)
        self.assertIn("https://music.youtube.com/watch?v=tKi9Zwc53Sw", card)
        self.assertIn("Here is a classic track:", card)

    def test_is_placeholder_text(self):
        self.assertTrue(is_placeholder_text(""))
        self.assertTrue(is_placeholder_text("<song title>"))
        self.assertTrue(is_placeholder_text("<raw youtube music url>"))
        self.assertTrue(is_placeholder_text("Unknown Track"))
        self.assertTrue(is_placeholder_text("Unknown Artist"))
        self.assertFalse(is_placeholder_text("Mac Declos"))
        self.assertFalse(is_placeholder_text("Give Nothing"))


if __name__ == "__main__":
    unittest.main()
