import sys
import unittest
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.skills.music.utils import (
    parse_song_meta,
    format_song_card,
    is_placeholder_text,
    standardize_song_reply,
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

    def test_standardize_song_reply_auto_resolve(self):
        # Even with a fake/hallucinated video ID, valid song & artist auto-resolves or retains song and artist
        fake_reply = (
            "Here is a great epic track:\n\n"
            "**Song:** Victory Song\n"
            "**Artist:** Ensiferum\n\n"
            "https://music.youtube.com/watch?v=3Z0Z0Z0Z0Z0"
        )
        res = standardize_song_reply(fake_reply)
        self.assertIn("**Song:** Victory Song", res)
        self.assertIn("**Artist:** Ensiferum", res)
        self.assertNotIn("3Z0Z0Z0Z0Z0", res)

    def test_standardize_song_reply_empty_placeholder(self):
        empty_reply = (
            "Here is your recommendation:\n\n"
            "**Song:** <song title>\n"
            "**Artist:** <artist name>\n\n"
            "<raw youtube music url>"
        )
        res = standardize_song_reply(empty_reply)
        self.assertNotIn("<song title>", res)
        self.assertIn("Here is your recommendation:", res)

    def test_standardize_song_reply_preserves_user_mention(self):
        mention_reply = (
            "<@123456789> Since you liked that track, here is another one:\n\n"
            "**Song:** Strobe\n"
            "**Artist:** deadmau5\n\n"
            "https://music.youtube.com/watch?v=tKi9Zwc53Sw"
        )
        res = standardize_song_reply(mention_reply)
        self.assertIn("<@123456789>", res)
        self.assertIn("**Song:** Strobe", res)
        self.assertIn("**Artist:** deadmau5", res)


class TestEmbeddability(unittest.TestCase):
    """Test suite ensuring YouTube links are embeddable and playable in Discord."""

    def test_verify_embeddable_invalid_id(self):
        from src.skills.youtube.client import verify_embeddable
        self.assertFalse(verify_embeddable(""))
        self.assertFalse(verify_embeddable("abc"))
        self.assertFalse(verify_embeddable("toolongvideoid12345"))

    @patch("src.skills.youtube.client.requests.get")
    def test_verify_embeddable_oembed_unauthorized(self, mock_get):
        from src.skills.youtube.client import verify_embeddable
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp
        self.assertFalse(verify_embeddable("12345678901"))

    @patch("src.skills.youtube.client.requests.get")
    def test_verify_embeddable_markers(self, mock_get):
        from src.skills.youtube.client import verify_embeddable
        oembed_resp = MagicMock()
        oembed_resp.status_code = 200

        for marker in ("Playback on other websites has been disabled", "LOGIN_REQUIRED", "UNPLAYABLE", "Video unavailable"):
            embed_resp = MagicMock()
            embed_resp.status_code = 200
            embed_resp.text = f"<html><body><div>{marker}</div></body></html>"
            mock_get.side_effect = [oembed_resp, embed_resp]
            self.assertFalse(verify_embeddable("12345678901"))

    @patch("src.skills.youtube.client.requests.get")
    def test_verify_embeddable_success(self, mock_get):
        from src.skills.youtube.client import verify_embeddable
        oembed_resp = MagicMock()
        oembed_resp.status_code = 200
        embed_resp = MagicMock()
        embed_resp.status_code = 200
        embed_resp.text = "<html><body><div>Normal playable video player</div></body></html>"
        mock_get.side_effect = [oembed_resp, embed_resp]
        self.assertTrue(verify_embeddable("12345678901"))

    @patch("src.skills.youtube.client.verify_embeddable")
    @patch("src.skills.music.playback.youtube_music.resolve_youtube_music_link")
    @patch("src.skills.music.utils.get_music_metadata")
    def test_standardize_song_reply_unembeddable_auto_resolve(self, mock_get_meta, mock_resolve, mock_verify):
        mock_verify.return_value = False
        mock_get_meta.return_value = {"song": "Unembeddable Hit", "artist": "Secret Artist"}
        mock_resolve.return_value = [{
            "id": "workingid12",
            "title": "Unembeddable Hit",
            "artist": "Secret Artist",
            "url": "https://music.youtube.com/watch?v=workingid12"
        }]

        reply = (
            "**Song:** Unembeddable Hit\n"
            "**Artist:** Secret Artist\n\n"
            "https://music.youtube.com/watch?v=badid123456"
        )
        res = standardize_song_reply(reply)
        self.assertIn("https://music.youtube.com/watch?v=workingid12", res)
        self.assertNotIn("badid123456", res)

    @patch("src.skills.music.playback.youtube_music.is_youtube_short")
    @patch("src.skills.music.playback.youtube_music.get_video_oembed")
    @patch("src.skills.music.playback.youtube_music.verify_embeddable")
    @patch("src.skills.music.playback.youtube_music.DDGS")
    def test_resolve_skips_unembeddable_candidate(self, mock_ddgs, mock_verify, mock_oembed, mock_short):
        from src.skills.music.playback.youtube_music import resolve_youtube_music_link
        mock_short.return_value = False
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = [
            {"href": "https://www.youtube.com/watch?v=unembed1111", "title": "Strobe - deadmau5"},
            {"href": "https://www.youtube.com/watch?v=embed222222", "title": "Strobe - deadmau5"}
        ]
        mock_ddgs.return_value = mock_ddgs_instance
        # First candidate fails verify_embeddable, second passes
        mock_verify.side_effect = [False, True]
        mock_oembed.return_value = {"title": "Strobe", "author": "deadmau5 - Topic"}

        results = resolve_youtube_music_link("Strobe by deadmau5", max_results=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "embed222222")


class TestDeezerExpansion(unittest.TestCase):
    """Test suite for expanded Deezer aggregator and artist search."""

    @patch("src.skills.music.aggregators.deezer.requests.get")
    def test_deezer_artist_top_tracks(self, mock_get):
        from unittest.mock import MagicMock
        from src.skills.music.aggregators.deezer import DeezerAggregator
        mock_artist_resp = MagicMock()
        mock_artist_resp.json.return_value = {
            "data": [{"id": 142381, "name": "Deadmau5"}]
        }
        mock_top_resp = MagicMock()
        mock_top_resp.json.return_value = {
            "data": [{"title": "Strobe", "album": {"title": "Random Album Title"}}]
        }
        mock_get.side_effect = [mock_artist_resp, mock_top_resp]

        deezer = DeezerAggregator()
        tracks = deezer.get_artist_top_tracks("Deadmau5", limit=5)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Strobe")
        self.assertEqual(tracks[0].artist, "Deadmau5")
        self.assertEqual(tracks[0].album, "Random Album Title")

    @patch("src.skills.music.aggregators.deezer.requests.get")
    def test_deezer_charts_with_genre(self, mock_get):
        from unittest.mock import MagicMock
        from src.skills.music.aggregators.deezer import DeezerAggregator
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"title": "Dracula", "artist": {"name": "Tame Impala"}, "album": {"title": "Dracula"}}]
        }
        mock_get.return_value = mock_resp

        deezer = DeezerAggregator()
        tracks = deezer.get_charts(genre="electronic")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Dracula")
        self.assertEqual(tracks[0].artist, "Tame Impala")

    def test_skill_top_music_artist_alias_normalization(self):
        from src.orchestrator import normalize_tool_args
        from src.skills.music.top_music import skill_top_music
        args = normalize_tool_args(
            "skill_top_music",
            skill_top_music,
            {"artist_name": "Deadmau5", "style": "techno"}
        )
        self.assertEqual(args, {"artist": "Deadmau5", "genre": "techno"})


if __name__ == "__main__":
    unittest.main()


