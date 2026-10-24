import sys
import unittest
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.skills.music.engine.formatting import (
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

    def test_remix_and_vip_preservation(self):
        # Parenthetical remix in 'by' query
        m1 = parse_song_meta("Take Me (Sub Focus Remix) by Wilkinson")
        self.assertEqual(m1["song"], "Take Me (Sub Focus Remix)")
        self.assertEqual(m1["artist"], "Wilkinson")

        # Bracketed VIP alongside Official Audio tag
        m2 = parse_song_meta("Chase & Status - Baddadan [VIP] (Official Audio)", author="Chase & Status")
        self.assertEqual(m2["song"], "Baddadan (VIP)")
        self.assertEqual(m2["artist"], "Chase & Status")

        # Hyphenated remix on Topic channel
        m3 = parse_song_meta("Take Me - Sub Focus Remix", author="Wilkinson - Topic")
        self.assertEqual(m3["song"], "Take Me (Sub Focus Remix)")
        self.assertEqual(m3["artist"], "Wilkinson")

        # Artist edit & Dub mix preserved, while Radio Edit and Extended Mix are stripped
        m4 = parse_song_meta("Desire (Dimension Edit) by Sub Focus")
        self.assertEqual(m4["song"], "Desire (Dimension Edit)")
        self.assertEqual(m4["artist"], "Sub Focus")

        m5 = parse_song_meta("Strobe (Radio Edit) [Extended Mix]", author="deadmau5 - Topic")
        self.assertEqual(m5["song"], "Strobe")
        self.assertEqual(m5["artist"], "deadmau5")

    def test_score_music_candidate_enforces_remix_and_vip(self):
        from src.skills.music.playback.youtube_music import score_music_candidate

        q_remix = "Take Me (Sub Focus Remix) by Wilkinson"
        # Un-remixed original must be rejected when a remix was requested
        valid_orig, _, _ = score_music_candidate(q_remix, "Take Me", "Wilkinson - Topic")
        self.assertFalse(valid_orig)

        # Different remixer must be rejected
        valid_other, _, _ = score_music_candidate(q_remix, "Take Me (Calibre Remix)", "Wilkinson - Topic")
        self.assertFalse(valid_other)

        # Matching remix on original artist's Topic channel -> score 3
        valid_topic, score_topic, _ = score_music_candidate(q_remix, "Take Me (Sub Focus Remix)", "Wilkinson - Topic")
        self.assertTrue(valid_topic)
        self.assertEqual(score_topic, 3)

        # Matching remix on remixer's Topic channel -> score 3
        valid_rmx_topic, score_rmx_topic, _ = score_music_candidate(q_remix, "Take Me (Sub Focus Remix)", "Sub Focus - Topic")
        self.assertTrue(valid_rmx_topic)
        self.assertEqual(score_rmx_topic, 3)

        # VIP enforcement
        q_vip = "Baddadan (VIP) by Chase & Status"
        valid_non_vip, _, _ = score_music_candidate(q_vip, "Baddadan", "Chase & Status - Topic")
        self.assertFalse(valid_non_vip)
        valid_vip, score_vip, _ = score_music_candidate(q_vip, "Baddadan (VIP)", "Chase & Status - Topic")
        self.assertTrue(valid_vip)
        self.assertEqual(score_vip, 3)

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

    @patch("src.skills.music.playback.youtube_music.resolve_youtube_music_link")
    @patch("src.skills.youtube.engine.client.verify_embeddable", return_value=False)
    def test_standardize_song_reply_auto_resolve(self, _mock_verify, mock_resolve):
        mock_resolve.return_value = [{
            "id": "ueT3qH1GC5E",
            "title": "Victory Song",
            "artist": "Ensiferum",
            "url": "https://music.youtube.com/watch?v=ueT3qH1GC5E",
        }]
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

    @patch("src.skills.youtube.engine.client.verify_embeddable", return_value=True)
    def test_standardize_song_reply_preserves_user_mention(self, _mock_verify):
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

    def setUp(self):
        from src.skills.youtube.engine.client import clear_youtube_caches
        from src.skills.music.playback.youtube_music import clear_resolved_music_cache
        clear_youtube_caches()
        clear_resolved_music_cache()

    def tearDown(self):
        from src.skills.youtube.engine.client import clear_youtube_caches
        from src.skills.music.playback.youtube_music import clear_resolved_music_cache
        clear_youtube_caches()
        clear_resolved_music_cache()

    def test_verify_embeddable_invalid_id(self):
        from src.skills.youtube.engine.client import verify_embeddable
        self.assertFalse(verify_embeddable(""))
        self.assertFalse(verify_embeddable("abc"))
        self.assertFalse(verify_embeddable("toolongvideoid12345"))

    @patch("src.skills.youtube.engine.client._HTTP_SESSION.get")
    def test_verify_embeddable_oembed_unauthorized(self, mock_get):
        from src.skills.youtube.engine.client import verify_embeddable
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp
        self.assertFalse(verify_embeddable("12345678901"))

    @patch("src.skills.youtube.engine.client._HTTP_SESSION.get")
    def test_verify_embeddable_markers(self, mock_get):
        from src.skills.youtube.engine.client import verify_embeddable, clear_youtube_caches
        oembed_resp = MagicMock()
        oembed_resp.status_code = 200

        for marker in (
            "Playback on other websites has been disabled",
            "blocked it from display on this website or application",
            '"playableInEmbed":false',
            "LOGIN_REQUIRED",
            "UNPLAYABLE",
            "Video unavailable",
        ):
            clear_youtube_caches()
            mock_get.reset_mock()
            embed_resp = MagicMock()
            embed_resp.status_code = 200
            embed_resp.text = f"<html><body><div>{marker}</div></body></html>"
            mock_get.side_effect = lambda url, **kw: oembed_resp if "oembed" in url else embed_resp
            self.assertFalse(verify_embeddable("12345678901"))
            self.assertTrue(
                any(call.kwargs.get("headers", {}).get("Referer") == "https://discord.com/" for call in mock_get.call_args_list)
            )

    @patch("src.skills.youtube.engine.client._HTTP_SESSION.get")
    def test_verify_embeddable_success(self, mock_get):
        from src.skills.youtube.engine.client import verify_embeddable, get_video_oembed
        oembed_resp = MagicMock()
        oembed_resp.status_code = 200
        oembed_resp.json.return_value = {"title": "Strobe", "author_name": "deadmau5 - Topic"}
        embed_resp = MagicMock()
        embed_resp.status_code = 200
        embed_resp.text = "<html><body><div>Normal playable video player</div></body></html>"
        mock_get.side_effect = lambda url, **kw: oembed_resp if "oembed" in url else embed_resp
        self.assertTrue(verify_embeddable("12345678901"))
        self.assertEqual(mock_get.call_count, 2)

        # Subsequent get_video_oembed and verify_embeddable calls for the same ID hit memory cache (0 network calls)
        cached_oembed = get_video_oembed("12345678901")
        self.assertEqual(cached_oembed, {"title": "Strobe", "author": "deadmau5 - Topic"})
        self.assertTrue(verify_embeddable("12345678901"))
        self.assertEqual(mock_get.call_count, 2)

    @patch("src.skills.youtube.engine.client.verify_embeddable")
    @patch("src.skills.music.playback.youtube_music.resolve_youtube_music_link")
    @patch("src.skills.music.engine.formatting.get_music_metadata")
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

    @patch("src.skills.music.playback.youtube_music._fetch_direct_youtube_video_ids", return_value=[])
    @patch("src.skills.music.playback.youtube_music.is_youtube_short")
    @patch("src.skills.music.playback.youtube_music.get_video_oembed")
    @patch("src.skills.music.playback.youtube_music.verify_embeddable")
    @patch("src.skills.music.playback.youtube_music.DDGS")
    def test_resolve_skips_unembeddable_candidate(self, mock_ddgs, mock_verify, mock_oembed, mock_short, _mock_direct):
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

    def setUp(self):
        from src.skills.music.aggregators.deezer import clear_deezer_cache
        clear_deezer_cache()

    def tearDown(self):
        from src.skills.music.aggregators.deezer import clear_deezer_cache
        clear_deezer_cache()

    @patch("src.skills.music.aggregators.deezer._HTTP_SESSION.get")
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

    @patch("src.skills.music.aggregators.deezer._HTTP_SESSION.get")
    def test_deezer_global_charts(self, mock_get):
        from unittest.mock import MagicMock
        from src.skills.music.aggregators.deezer import DeezerAggregator
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"title": "Dracula", "artist": {"name": "Tame Impala"}, "album": {"title": "Dracula"}}]
        }
        mock_get.return_value = mock_resp

        deezer = DeezerAggregator()
        tracks = deezer.get_charts(genre=None)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Dracula")
        self.assertEqual(tracks[0].artist, "Tame Impala")

    def test_skill_music_discovery_artist_alias_normalization(self):
        from src.orchestrator import normalize_tool_args
        from src.skills.music.music import skill_music_discovery
        args = normalize_tool_args(
            "skill_music_discovery",
            skill_music_discovery,
            {"artist_name": "Deadmau5", "style": "techno"}
        )
        self.assertEqual(args, {"artist": "Deadmau5", "genre": "techno"})


class TestMusicDiversityExpansion(unittest.TestCase):
    """Tests for session exclusion, multi-playlist pooling, multi-region/nationality charts, Beatport subgenres, and Last.fm rotation."""

    def setUp(self):
        from src.skills.music.engine.formatting import RECENTLY_PLAYED_TRACKS
        RECENTLY_PLAYED_TRACKS.clear()

    def tearDown(self):
        from src.skills.music.engine.formatting import RECENTLY_PLAYED_TRACKS
        RECENTLY_PLAYED_TRACKS.clear()

    def test_recently_played_exclusion_in_manager(self):
        from src.skills.music.engine.formatting import record_played_track, is_recently_played
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.models import Track

        record_played_track("Tarantula", "Pendulum")
        self.assertTrue(is_recently_played("Tarantula", "Pendulum"))
        self.assertFalse(is_recently_played("Solar System", "Sub Focus"))

        mgr = MusicAggregatorManager()
        candidates = [
            Track(title="Tarantula", artist="Pendulum", source="lastfm"),
            Track(title="Solar System", artist="Sub Focus", source="deezer"),
            Track(title="Baddadan", artist="Chase & Status", source="itunes"),
        ]
        filtered = mgr._filter_and_deduplicate(candidates)
        self.assertEqual(len(filtered), 2)
        self.assertEqual([t.title for t in filtered], ["Solar System", "Baddadan"])

    def test_unknown_song_similarity_fallback_marks_unrelated(self):
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.models import Track
        from src.skills.music.music import skill_music_discovery

        mgr = MusicAggregatorManager()
        for name in ("lastfm", "deezer", "freqblog"):
            mgr.aggregators[name].get_similar = MagicMock(return_value=[])
        mgr.get_top_charts = MagicMock(return_value=[
            Track(title="Baddadan", artist="Chase & Status", genre="Drum & Bass", source="deezer")
        ])

        tracks = mgr.get_similar_tracks("Completely Unknown Obscure Track 99999 by Nobody")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].source, "fallback_unrelated")

        with (
            patch("src.skills.music.music.manager", mgr),
            patch("src.skills.music.music._pre_resolve_top_candidate", return_value=""),
        ):
            out = skill_music_discovery(similar_to="Completely Unknown Obscure Track 99999 by Nobody")
            self.assertIn("NO DIRECT SIMILAR SONGS FOUND", out)
            self.assertIn("couldn't find any directly related songs", out)

    def test_charts_nationality_modifier_and_song_title_safeguard(self):
        from src.skills.music.aggregators.charts import resolve_genre_and_regions

        # Regional genre queries
        gid_de, regs_de = resolve_genre_and_regions("german rap")
        self.assertEqual(gid_de, 18)
        self.assertEqual(regs_de, ["de"])

        gid_dr, regs_dr = resolve_genre_and_regions("deutschrap")
        self.assertEqual(gid_dr, 18)
        self.assertEqual(regs_dr, ["de"])

        gid_uk, regs_uk = resolve_genre_and_regions("uk drill")
        self.assertEqual(gid_uk, 18)
        self.assertEqual(regs_uk, ["gb"])

        gid_fr, regs_fr = resolve_genre_and_regions("french house")
        self.assertEqual(gid_fr, 1048)
        self.assertEqual(regs_fr, ["fr"])

        # General genre multi-region capitals
        gid_dnb, regs_dnb = resolve_genre_and_regions("dnb")
        self.assertEqual(gid_dnb, 1049)
        self.assertEqual(regs_dnb, ["gb", "us", "de"])

        # Safeguard: Specific song titles containing nationality words must NOT resolve as genres
        self.assertEqual(resolve_genre_and_regions("englishman in new york"), (None, []))
        self.assertEqual(resolve_genre_and_regions("american idiot"), (None, []))
        self.assertEqual(resolve_genre_and_regions("german shepherd"), (None, []))

    @patch("src.skills.music.aggregators.deezer._HTTP_SESSION.get")
    def test_deezer_multi_playlist_pooling(self, mock_get):
        from src.skills.music.aggregators.deezer import DeezerAggregator, clear_deezer_cache
        clear_deezer_cache()

        mock_search_resp = MagicMock()
        mock_search_resp.json.return_value = {
            "data": [
                {"id": 101, "title": "Hospitality DnB", "nb_tracks": 50},
                {"id": 102, "title": "UKF Drum & Bass", "nb_tracks": 60},
            ]
        }
        mock_pl1_resp = MagicMock()
        mock_pl1_resp.json.return_value = {
            "data": [{"title": "Solar System", "artist": {"name": "Sub Focus"}, "album": {"title": "Portals"}}]
        }
        mock_pl2_resp = MagicMock()
        mock_pl2_resp.json.return_value = {
            "data": [{"title": "Afterglow", "artist": {"name": "Wilkinson"}, "album": {"title": "Lazers Not Included"}}]
        }
        mock_get.side_effect = [mock_search_resp, mock_pl1_resp, mock_pl2_resp]

        deezer = DeezerAggregator()
        tracks = deezer.get_charts(genre="dnb")
        artists = {t.artist for t in tracks}
        self.assertEqual(artists, {"Sub Focus", "Wilkinson"})
        clear_deezer_cache()

    @patch("src.skills.music.aggregators.deezer.DeezerAggregator.get_charts")
    def test_beatport_techno_queries_beatport_top_100_techno_on_deezer(self, mock_deezer_charts):
        from src.skills.music.aggregators.beatport import BeatportAggregator
        from src.skills.music.models import Track

        mock_deezer_charts.return_value = [
            Track(title="Consciousness", artist="Anyma", genre="Techno", source="deezer_beatport_top_100_techno")
        ]

        bp = BeatportAggregator()
        tracks = bp.get_charts(genre="techno")
        mock_deezer_charts.assert_called_once_with(genre="Beatport Top 100 Techno")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Consciousness")
        self.assertEqual(tracks[0].genre, "Techno")

    def test_lastfm_combined_tracks_and_artists_rotation(self):
        from src.skills.music.aggregators.lastfm import LastFmAggregator

        lfm = LastFmAggregator(api_key="test_key")

        def fake_call(method, params):
            if method == "tag.gettoptracks":
                return {
                    "tracks": {
                        "track": [
                            {"name": "Solar System", "artist": {"name": "Sub Focus"}},
                            {"name": "Afterglow", "artist": {"name": "Wilkinson"}},
                            {"name": "LALALALA", "artist": {"name": "Stray Kids"}},  # Off-genre tag spam, should be filtered out
                        ]
                    }
                }
            if method == "tag.gettopartists":
                return {
                    "topartists": {
                        "artist": [
                            {"name": "Sub Focus"},  # Already in Step A, should be skipped in Step B
                            {"name": "Wilkinson"},  # Verified genre artist in Step A
                            {"name": "Noisia"},
                            {"name": "Netsky"},
                        ]
                    }
                }
            if method == "artist.gettoptracks":
                art = params.get("artist")
                return {
                    "toptracks": {
                        "track": [{"name": f"{art} Hit", "artist": {"name": art}}]
                    }
                }
            return {}

        lfm._call = MagicMock(side_effect=fake_call)
        tracks = lfm.get_charts(genre="dnb")
        artists = {t.artist for t in tracks}
        self.assertEqual(artists, {"Sub Focus", "Wilkinson", "Noisia", "Netsky"})
        self.assertNotIn("Stray Kids", artists)

    def test_get_top_charts_skips_deezer_when_lastfm_succeeds(self):
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.models import Track

        mgr = MusicAggregatorManager()
        mgr.aggregators["lastfm"].get_charts = MagicMock(return_value=[
            Track(title="Murder In My Mind", artist="Kordhell", genre="Phonk", source="lastfm_phonk")
        ])
        mgr.aggregators["beatport"].get_charts = MagicMock(return_value=[])
        mgr.aggregators["charts"].get_charts = MagicMock(return_value=[])
        mgr.aggregators["deezer"].get_charts = MagicMock(return_value=[
            Track(title="Off-Genre Playlist Track", artist="Random User", genre="Phonk", source="deezer_phonk")
        ])

        tracks = mgr.get_top_charts(genre="phonk")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].artist, "Kordhell")
        mgr.aggregators["deezer"].get_charts.assert_not_called()


class TestMoodVibeDiscovery(unittest.TestCase):
    """Tests for mood/vibe parsing, standalone multi-genre vibe profiles, and genre+mood matrix."""

    def setUp(self):
        from src.skills.music.engine.formatting import RECENTLY_PLAYED_TRACKS
        RECENTLY_PLAYED_TRACKS.clear()

    def tearDown(self):
        from src.skills.music.engine.formatting import RECENTLY_PLAYED_TRACKS
        RECENTLY_PLAYED_TRACKS.clear()

    def test_parse_mood_and_genre(self):
        from src.skills.music.engine.moods import parse_mood_and_genre

        # Standalone vibes passed via genre or mood
        self.assertEqual(parse_mood_and_genre(mood="banger"), (None, "banger"))
        self.assertEqual(parse_mood_and_genre(genre="banger"), (None, "banger"))
        self.assertEqual(parse_mood_and_genre(genre="something melodic"), (None, "melodic"))
        self.assertEqual(parse_mood_and_genre(genre="play me something hard"), (None, "hard"))
        self.assertEqual(parse_mood_and_genre(genre="chill song"), (None, "chill"))
        self.assertEqual(parse_mood_and_genre(genre="sad song"), (None, "sad"))
        self.assertEqual(parse_mood_and_genre(genre="love song"), (None, "love"))
        self.assertEqual(parse_mood_and_genre(mood="romantic"), (None, "love"))

        # Combined genre + vibe
        self.assertEqual(parse_mood_and_genre(genre="melodic dnb"), ("dnb", "melodic"))
        self.assertEqual(parse_mood_and_genre(genre="hard techno"), ("techno", "hard"))
        self.assertEqual(parse_mood_and_genre(genre="dnb banger"), ("dnb", "banger"))
        self.assertEqual(parse_mood_and_genre(genre="r&b love song"), ("r&b", "love"))
        self.assertEqual(parse_mood_and_genre(genre="dnb", mood="melodic"), ("dnb", "melodic"))

        # Pure genres untouched
        self.assertEqual(parse_mood_and_genre(genre="dnb"), ("dnb", None))
        self.assertEqual(parse_mood_and_genre(genre="german rap"), ("german rap", None))
        self.assertEqual(parse_mood_and_genre(genre="techno"), ("techno", None))

    def test_standalone_and_combined_mood_tracks_in_manager(self):
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.models import Track

        mgr = MusicAggregatorManager()
        mgr.aggregators["deezer"].get_charts = MagicMock(
            side_effect=lambda genre=None: [Track(title=f"Hit ({genre})", artist=f"Artist ({genre})", source="deezer")]
        )
        mgr.aggregators["lastfm"].get_charts = MagicMock(
            side_effect=lambda genre=None: [Track(title=f"Tag ({genre})", artist=f"TagArtist ({genre})", source="lastfm")]
        )
        mgr.aggregators["beatport"].get_charts = MagicMock(
            side_effect=lambda genre=None: [Track(title=f"BP ({genre})", artist=f"BPArtist ({genre})", source="beatport")]
        )

        # Standalone 'banger' pools across 4 Deezer queries + 2 Last.fm tags + Beatport Hype 100
        banger_tracks = mgr.get_mood_tracks(mood="banger")
        self.assertEqual(mgr.aggregators["deezer"].get_charts.call_count, 4)
        self.assertEqual(mgr.aggregators["lastfm"].get_charts.call_count, 2)
        mgr.aggregators["beatport"].get_charts.assert_called_once_with(genre="Beatport Hype 100")
        self.assertEqual(len(banger_tracks), 7)

        # Combined 'melodic' + 'dnb' queries Liquid / Melodic DnB
        mgr.aggregators["deezer"].get_charts.reset_mock()
        melodic_dnb = mgr.get_mood_tracks(mood="melodic", genre="dnb")
        dz_called_genres = {call.kwargs.get("genre") for call in mgr.aggregators["deezer"].get_charts.call_args_list}
        self.assertEqual(dz_called_genres, {"Liquid Drum & Bass", "Melodic Drum and Bass"})
        self.assertTrue(len(melodic_dnb) >= 3)

    def test_skill_music_discovery_mood_routing_and_alias(self):
        from src.orchestrator import normalize_tool_args
        from src.skills.music.music import skill_music_discovery
        from src.skills.music.models import Track

        args = normalize_tool_args("skill_music_discovery", skill_music_discovery, {"vibe": "love"})
        self.assertEqual(args, {"mood": "love"})

        fake_mgr = MagicMock()
        fake_mgr.get_mood_tracks.return_value = [
            Track(title="Adorn", artist="Miguel", genre="R&B", source="deezer_love")
        ]
        with (
            patch("src.skills.music.music.manager", fake_mgr),
            patch("src.skills.music.music._pre_resolve_top_candidate", return_value=""),
        ):
            out = skill_music_discovery(mood="love")
            fake_mgr.get_mood_tracks.assert_called_once_with(mood="love", genre=None)
            self.assertIn("TOP VIBE / MOOD SELECTION: LOVE (MULTI-GENRE MIX)", out)
            self.assertIn("Adorn by Miguel", out)

    @patch("src.skills.music.music.resolve_youtube_music_link")
    def test_skill_music_discovery_pre_resolves_and_skips_unembeddable(self, mock_resolve):
        from src.skills.music.music import skill_music_discovery
        from src.skills.music.models import Track

        fake_mgr = MagicMock()
        fake_mgr.get_similar_tracks.return_value = [
            Track(title="Blocked Song", artist="Artist A", genre="House", source="lastfm"),
            Track(title="Playable Song", artist="Artist B", genre="House", source="deezer"),
        ]

        def fake_yt(query, max_results=1):
            if "Blocked Song" in query:
                return []
            return [{
                "id": "playable123",
                "title": "Playable Song",
                "artist": "Artist B",
                "url": "https://music.youtube.com/watch?v=playable123",
            }]

        mock_resolve.side_effect = fake_yt
        with patch("src.skills.music.music.manager", fake_mgr):
            out = skill_music_discovery(similar_to="Strobe by Deadmau5")
            self.assertIn("=== VERIFIED YOUTUBE MUSIC RESULT (PRE-RESOLVED & READY TO PRESENT) ===", out)
            self.assertIn("Song: Playable Song", out)
            self.assertIn("Artist: Artist B", out)
            self.assertIn("Link: https://music.youtube.com/watch?v=playable123", out)

    @patch("src.skills.youtube.engine.client._HTTP_SESSION.head")
    @patch("src.skills.youtube.engine.client._HTTP_SESSION.get")
    def test_topic_channel_skips_short_head_request(self, mock_get, mock_head):
        from src.skills.youtube.engine.client import verify_embeddable, is_youtube_short, clear_youtube_caches
        clear_youtube_caches()

        oembed_resp = MagicMock()
        oembed_resp.status_code = 200
        oembed_resp.json.return_value = {"title": "Solar System", "author_name": "Sub Focus - Topic"}
        embed_resp = MagicMock()
        embed_resp.status_code = 200
        embed_resp.text = "<html><body>Playable</body></html>"
        mock_get.side_effect = lambda url, **kw: oembed_resp if "oembed" in url else embed_resp

        self.assertTrue(verify_embeddable("topicvid123"))
        # Because author_name has ' - Topic', is_youtube_short should return False in 0ms without calling requests.head
        self.assertFalse(is_youtube_short("topicvid123"))
        mock_head.assert_not_called()

    def test_lastfm_artist_top_tracks_and_manager_album_enrichment(self):
        from src.skills.music.aggregators.lastfm import LastFmAggregator
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.models import Track

        lfm = LastFmAggregator(api_key="test_key")

        def fake_call(method, params):
            if method == "artist.gettoptracks":
                return {
                    "toptracks": {
                        "track": [
                            {"name": "Strobe", "artist": {"name": "deadmau5"}},
                            {"name": "Ghosts 'n' Stuff", "artist": {"name": "deadmau5"}},
                        ]
                    }
                }
            return {}

        lfm._call = MagicMock(side_effect=fake_call)
        lfm_tracks = lfm.get_artist_top_tracks("deadmau5")
        self.assertEqual(len(lfm_tracks), 2)
        self.assertEqual(lfm_tracks[0].title, "Strobe")
        self.assertEqual(lfm_tracks[0].artist, "deadmau5")

        # Verify MusicAggregatorManager.get_artist_tracks merges Last.fm + Deezer and enriches Album metadata
        mgr = MusicAggregatorManager()
        mgr.aggregators["lastfm"].get_artist_top_tracks = MagicMock(return_value=[
            Track(title="Strobe", artist="deadmau5", source="lastfm_artist"),
            Track(title="I Remember", artist="deadmau5", source="lastfm_artist"),
        ])
        mgr.aggregators["deezer"].get_artist_top_tracks = MagicMock(return_value=[
            Track(title="Strobe", artist="deadmau5", album="For Lack of a Better Name", source="deezer_artist"),
            Track(title="Monophobia", artist="deadmau5", album="mau5ville: Level 1", source="deezer_artist"),
        ])

        combined = mgr.get_artist_tracks("deadmau5")
        self.assertEqual([t.title for t in combined], ["Strobe", "I Remember", "Monophobia"])
        # Strobe from Last.fm is enriched with Deezer's album metadata
        self.assertEqual(combined[0].album, "For Lack of a Better Name")
        self.assertEqual(combined[0].source, "lastfm_artist")

    def test_garbage_keywords_and_set_filtering(self):
        from src.skills.music.playback.youtube_music import is_garbage_music_title
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.models import Track

        # Rejects slowed, reverb, tracklist, and DJ/live/full sets
        self.assertTrue(is_garbage_music_title("PASSO BEM SOLTO - Slowed", ""))
        self.assertTrue(is_garbage_music_title("One Chance (Slowed + Reverb)", ""))
        self.assertTrue(is_garbage_music_title("Phonk Mix 2026 (w/ Tracklist)", ""))
        self.assertTrue(is_garbage_music_title("Charlotte de Witte - Full Set", ""))
        self.assertTrue(is_garbage_music_title("Sub Focus (DJ Set)", ""))
        self.assertTrue(is_garbage_music_title("Amelie Lens [Live Set]", ""))
        self.assertTrue(is_garbage_music_title("Hard Techno Set 2026", ""))
        self.assertTrue(is_garbage_music_title("Close Eyes", "", author="Phonk Slowed & Reverb"))

        # Preserves legitimate song titles containing "set"
        self.assertFalse(is_garbage_music_title("Set Fire to the Rain", "Set Fire to the Rain by Adele"))
        self.assertFalse(is_garbage_music_title("Set Me Free", "Set Me Free by Avicii"))
        self.assertFalse(is_garbage_music_title("Sunset Lover", "Sunset Lover by Petit Biscuit"))

        # Manager filters out slowed/reverb/set/tracklist tracks upstream
        mgr = MusicAggregatorManager()
        raw_tracks = [
            Track(title="PASSO BEM SOLTO - Slowed", artist="ATLXS", source="lastfm"),
            Track(title="Techno Mix (Tracklist)", artist="Various", source="deezer"),
            Track(title="Live Set @ Tomorrowland", artist="Anyma", source="deezer"),
            Track(title="Murder In My Mind", artist="Kordhell", source="lastfm"),
        ]
        filtered = mgr._filter_and_deduplicate(raw_tracks)
        self.assertEqual([t.title for t in filtered], ["Murder In My Mind"])

    def test_score_music_candidate_rejects_fake_and_wrong_topic_songs(self):
        from src.skills.music.playback.youtube_music import score_music_candidate

        # 1. Wrong song on the right artist's Topic channel must NEVER match
        valid_gin, score_gin, _ = score_music_candidate(
            "Lay Low by Snoop Dogg", "Gin and Juice", "Snoop Dogg - Topic"
        )
        self.assertFalse(valid_gin)
        self.assertEqual(score_gin, 0)

        valid_gin_bare, _, _ = score_music_candidate(
            "Snoop Dogg Lay Low", "Gin and Juice", "Snoop Dogg - Topic"
        )
        self.assertFalse(valid_gin_bare)

        # 2. Fake re-upload with clickbait year on unverified mix channel ('RASPY MIX') must be rejected
        valid_fake, score_fake, _ = score_music_candidate(
            "Lay Low by Snoop Dogg",
            "Snoop Dogg - Lay Low (Official Music 2026)",
            "RASPY MIX",
        )
        self.assertFalse(valid_fake)
        self.assertEqual(score_fake, 0)

        valid_fake_yr, _, _ = score_music_candidate(
            "Lay Low by Snoop Dogg",
            "Snoop Dogg - Lay Low (Official 2026)",
            "Random Channel",
        )
        self.assertFalse(valid_fake_yr)

        # 3. Genuine Topic, VEVO/Artist, and legitimate standard/label uploads continue to pass
        valid_topic, score_topic, _ = score_music_candidate(
            "Lay Low by Snoop Dogg", "Lay Low", "Snoop Dogg - Topic"
        )
        self.assertTrue(valid_topic)
        self.assertEqual(score_topic, 3)

        valid_vevo, score_vevo, _ = score_music_candidate(
            "Lay Low by Snoop Dogg",
            "Snoop Dogg - Lay Low (Official Music Video)",
            "SnoopDoggVEVO",
        )
        self.assertTrue(valid_vevo)
        self.assertEqual(score_vevo, 2)

        valid_label, score_label, _ = score_music_candidate(
            "Running by Anyma",
            "Anyma - Running [Original Mix]",
            "Afterlife",
        )
        self.assertTrue(valid_label)
        self.assertEqual(score_label, 1)

    @patch("src.skills.music.playback.youtube_music._fetch_direct_youtube_video_ids", return_value=[])
    @patch("src.skills.music.playback.youtube_music.is_youtube_short")
    @patch("src.skills.music.playback.youtube_music.get_video_oembed")
    @patch("src.skills.music.playback.youtube_music.verify_embeddable")
    @patch("src.skills.music.playback.youtube_music.DDGS")
    def test_retry_exclusion_and_official_fallback_hierarchy(self, mock_ddgs, mock_verify, mock_oembed, mock_short, _mock_direct):
        from src.skills.youtube.engine.client import clear_youtube_caches, _OEMBED_CACHE
        from src.skills.music.playback.youtube_music import (
            clear_resolved_music_cache,
            get_blocked_official_info,
            resolve_youtube_music_link,
        )
        from src.skills.youtube.youtube import skill_youtube_search

        clear_youtube_caches()
        clear_resolved_music_cache()
        mock_short.return_value = False

        # Part A: Official 'Lay Low by Snoop Dogg' upload is embed-blocked while fake upload is rejected ->
        # Tier 2 Official Fallback returns the genuine Snoop Dogg - Topic link (mG9UXeDvsTA)
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = [
            {"href": "https://www.youtube.com/watch?v=mG9UXeDvsTA", "title": "Lay Low - Snoop Dogg - Topic"},
            {"href": "https://www.youtube.com/watch?v=-2f1baeNfLE", "title": "Snoop Dogg - Lay Low (Official Music 2026)"},
        ]
        mock_ddgs.return_value = mock_ddgs_instance

        _OEMBED_CACHE["mG9UXeDvsTA"] = {"title": "Lay Low", "author": "Snoop Dogg - Topic"}
        _OEMBED_CACHE["-2f1baeNfLE"] = {"title": "Snoop Dogg - Lay Low (Official Music 2026)", "author": "RASPY MIX"}

        def fake_verify(vid):
            return vid != "mG9UXeDvsTA"

        mock_verify.side_effect = fake_verify
        mock_oembed.side_effect = lambda vid: _OEMBED_CACHE.get(vid, {"title": "", "author": ""})

        out_fallback = skill_youtube_search("Lay Low by Snoop Dogg", max_results=1, is_music=True)
        self.assertIn("https://music.youtube.com/watch?v=mG9UXeDvsTA", out_fallback)
        self.assertNotIn("-2f1baeNfLE", out_fallback)
        self.assertIsNotNone(get_blocked_official_info("Lay Low by Snoop Dogg"))

        # Verify standardize_song_reply preserves the official fallback URL even though verify_embeddable is False
        with patch("src.skills.youtube.engine.client.verify_embeddable", return_value=False):
            card = standardize_song_reply(
                "**Song:** Lay Low\n"
                "**Artist:** Snoop Dogg\n\n"
                "https://music.youtube.com/watch?v=mG9UXeDvsTA"
            )
            self.assertIn("https://music.youtube.com/watch?v=mG9UXeDvsTA", card)

        # Part B: 'Drop It Like It's Hot by Snoop Dogg' -> official Snoop Dogg - Topic (embed-blocked)
        # MUST beat unverified standard re-upload channel ('CuentasXXX Online', embeddable=True)
        clear_youtube_caches()
        clear_resolved_music_cache()
        mock_ddgs_instance.text.return_value = [
            {"href": "https://www.youtube.com/watch?v=T6bjORaYktk", "title": "Drop It Like It's Hot"},
            {"href": "https://www.youtube.com/watch?v=VYOoqeldaUE", "title": "Snoop Dogg   Drop It Like It's Hot ft  Pharrell Williams"},
        ]
        _OEMBED_CACHE["T6bjORaYktk"] = {"title": "Drop It Like It's Hot", "author": "Snoop Dogg - Topic"}
        _OEMBED_CACHE["VYOoqeldaUE"] = {
            "title": "Snoop Dogg   Drop It Like It's Hot ft  Pharrell Williams",
            "author": "CuentasXXX Online",
        }
        mock_verify.side_effect = lambda vid: vid == "VYOoqeldaUE"
        res_drop = resolve_youtube_music_link("Drop It Like It's Hot by Snoop Dogg", max_results=1)
        self.assertEqual(len(res_drop), 1)
        self.assertEqual(res_drop[0]["id"], "T6bjORaYktk")
        self.assertEqual(res_drop[0]["artist"], "Snoop Dogg")
        self.assertTrue(res_drop[0]["is_official_fallback"])

        # Part C: Specific song requests via skill_youtube_search do NOT exclude previously served links,
        # while explicit exclude_ids on resolve_youtube_music_link still skips excluded IDs when requested.
        clear_youtube_caches()
        clear_resolved_music_cache()
        mock_ddgs_instance.text.return_value = [
            {"href": "https://www.youtube.com/watch?v=firstvid111", "title": "Strobe - deadmau5"},
            {"href": "https://www.youtube.com/watch?v=secondvid22", "title": "Strobe (Radio Edit) - deadmau5"},
        ]
        mock_verify.side_effect = None
        mock_verify.return_value = True
        mock_oembed.side_effect = lambda vid: {
            "firstvid111": {"title": "Strobe", "author": "deadmau5 - Topic"},
            "secondvid22": {"title": "Strobe (Radio Edit)", "author": "deadmau5 - Topic"},
        }[vid]

        first_res = skill_youtube_search("Strobe by deadmau5", max_results=1, is_music=True)
        self.assertIn("firstvid111", first_res)

        # Second call for the same specific song reuses the official firstvid111 link rather than blocking it
        second_res = skill_youtube_search("Strobe by deadmau5", max_results=1, is_music=True)
        self.assertIn("firstvid111", second_res)

        # Explicit exclude_ids parameter on resolve_youtube_music_link still skips firstvid111 when needed
        alt_res = resolve_youtube_music_link("Strobe by deadmau5", max_results=1, exclude_ids={"firstvid111"})
        self.assertEqual(len(alt_res), 1)
        self.assertEqual(alt_res[0]["id"], "secondvid22")

    def test_zero_undefined_names_across_entire_codebase(self):
        """Static AST scope analyzer ensuring no module or function in src/, main.py, or tests/ references an undefined name."""
        import ast
        import builtins
        import pathlib

        builtin_names = set(dir(builtins)) | {
            "__file__", "__name__", "__doc__", "__package__", "__spec__", "__annotations__"
        }

        def collect_scope_defs(node):
            defs = set()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                    defs.add(arg.arg)
                if node.args.vararg:
                    defs.add(node.args.vararg.arg)
                if node.args.kwarg:
                    defs.add(node.args.kwarg.arg)
            body = getattr(node, "body", [])
            if not isinstance(body, list):
                body = [body]
            stack = list(body)
            while stack:
                cur = stack.pop()
                if isinstance(cur, (ast.Import, ast.ImportFrom)):
                    for alias in cur.names:
                        defs.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defs.add(cur.name)
                    continue
                elif isinstance(cur, (ast.Lambda, ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                    continue
                elif isinstance(cur, ast.Name) and isinstance(cur.ctx, (ast.Store, ast.Param)):
                    defs.add(cur.id)
                elif isinstance(cur, ast.ExceptHandler) and cur.name:
                    defs.add(cur.name)
                elif isinstance(cur, ast.NamedExpr) and isinstance(cur.target, ast.Name):
                    defs.add(cur.target.id)
                for child in ast.iter_child_nodes(cur):
                    stack.append(child)
            return defs

        undefined_findings = []

        def check_node(node, scopes, filepath):
            cur_scopes = list(scopes)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                cur_scopes.append(collect_scope_defs(node))
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                comp_defs = set()
                for gen in node.generators:
                    for t in ast.walk(gen.target):
                        if isinstance(t, ast.Name):
                            comp_defs.add(t.id)
                    for sub in ast.walk(gen):
                        if isinstance(sub, ast.NamedExpr) and isinstance(sub.target, ast.Name):
                            comp_defs.add(sub.target.id)
                cur_scopes.append(comp_defs)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if not any(node.id in s for s in cur_scopes):
                    undefined_findings.append(f"{filepath}:{node.lineno} -> undefined name '{node.id}'")
            for child in ast.iter_child_nodes(node):
                check_node(child, cur_scopes, filepath)

        root = pathlib.Path(__file__).resolve().parent.parent
        py_files = list((root / "src").rglob("*.py")) + [root / "main.py"] + list((root / "tests").rglob("*.py"))
        for p in py_files:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            check_node(tree, [builtin_names], str(p.relative_to(root)))

        self.assertEqual(undefined_findings, [], f"Undefined names detected:\n" + "\n".join(undefined_findings))


class TestThreeShelfTieredDiscoveryAndGatekeeper(unittest.TestCase):
    """Tests for the 3-Shelf (45/35/20) Crate-Digging Tiered Sampler and Cross-Source Genre/Similarity Gatekeeper."""

    def setUp(self):
        from src.skills.music.engine.formatting import RECENTLY_PLAYED_TRACKS
        from src.skills.music.aggregators.lastfm import clear_lastfm_cache
        from src.skills.music.aggregators.deezer import clear_deezer_cache
        RECENTLY_PLAYED_TRACKS.clear()
        clear_lastfm_cache()
        clear_deezer_cache()

    def tearDown(self):
        from src.skills.music.engine.formatting import RECENTLY_PLAYED_TRACKS
        from src.skills.music.aggregators.lastfm import clear_lastfm_cache
        from src.skills.music.aggregators.deezer import clear_deezer_cache
        RECENTLY_PLAYED_TRACKS.clear()
        clear_lastfm_cache()
        clear_deezer_cache()

    def test_three_shelf_crate_digging_distribution_and_zero_decay(self):
        from collections import Counter
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.models import Track

        pool = (
            [Track(title=f"Core {i}", artist=f"CoreArt {i}", tier=1, verified_genre=True) for i in range(10)]
            + [Track(title=f"Mid {i}", artist=f"MidArt {i}", tier=2, verified_genre=True) for i in range(10)]
            + [Track(title=f"Deep {i}", artist=f"DeepArt {i}", tier=3, verified_genre=True) for i in range(10)]
        )

        slot0_tiers = Counter()
        for _ in range(400):
            sampled = MusicAggregatorManager.sample_tiered_tracks(pool, k=15, weights=(0.45, 0.35, 0.20))
            self.assertEqual(len(sampled), 15)
            slot0_tiers[sampled[0].tier] += 1

        # All 3 shelves participate on Slot #0 following the ~45% / 35% / 20% distribution
        self.assertGreater(slot0_tiers[1], 120)
        self.assertGreater(slot0_tiers[2], 90)
        self.assertGreater(slot0_tiers[3], 40)
        self.assertGreater(slot0_tiers[1], slot0_tiers[3])

    def test_genre_gatekeeper_filters_unverified_itunes_and_deezer_crossovers(self):
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.models import Track

        mgr = MusicAggregatorManager()
        mgr.aggregators["lastfm"].get_verified_genre_artists = MagicMock(
            return_value={"sub focus", "wilkinson", "dimension", "culture shock", "metrik", "kanine", "hybrid minds"}
        )

        raw_tracks = [
            Track(title="Solar System", artist="Sub Focus", genre="Dnb", source="lastfm_dnb", tier=1, verified_genre=True),
            Track(title="Afterglow", artist="Wilkinson", genre="Dnb", source="lastfm_dnb", tier=1, verified_genre=True),
            Track(title="Offender", artist="Dimension", genre="Dnb", source="lastfm_dnb", tier=2, verified_genre=True),
            Track(title="Deconstruct", artist="Culture Shock", genre="Dnb", source="lastfm_dnb", tier=2, verified_genre=True),
            Track(title="Hackers", artist="Metrik", genre="Dnb", source="lastfm_dnb", tier=2, verified_genre=True),
            Track(title="Want You", artist="Kanine", genre="Dnb", source="beatport_fallback_deezer", tier=1, verified_genre=True),
            # Verified genre artist appearing on iTunes -> kept and marked verified_genre=True
            Track(title="Touch", artist="Hybrid Minds", genre="Dnb", source="itunes_dnb", tier=3, verified_genre=False),
            # Unverified commercial pop crossover on iTunes / random user playlist -> dropped by gatekeeper
            Track(title="Random Pop Radio Song", artist="Mainstream Pop Star", genre="Pop", source="itunes_dnb", tier=1, verified_genre=False),
            Track(title="Random User Playlist Song", artist="Unknown Indie Band", genre="Rock", source="deezer_dnb", tier=1, verified_genre=False),
        ]

        filtered = mgr._apply_genre_gatekeeper(raw_tracks, genre="dnb")
        filtered_artists = {t.artist for t in filtered}
        self.assertIn("Hybrid Minds", filtered_artists)
        self.assertNotIn("Mainstream Pop Star", filtered_artists)
        self.assertNotIn("Unknown Indie Band", filtered_artists)
        self.assertTrue(all(t.verified_genre for t in filtered))

    def test_lastfm_similar_tracks_relevance_floor_and_three_shelves(self):
        from src.skills.music.aggregators.lastfm import LastFmAggregator

        lfm = LastFmAggregator(api_key="test_key")
        raw_sim = []
        # 25 tracks with decaying similarity match scores + 1 album intro that should be skipped
        raw_sim.append({"name": "Album Intro (Skit)", "artist": {"name": "Artist 0"}, "match": "0.99"})
        for idx in range(30):
            match_val = max(0.02, 0.95 - idx * 0.04)
            raw_sim.append({
                "name": f"Similar Song {idx}",
                "artist": {"name": f"Artist {idx}"},
                "match": f"{match_val:.2f}",
            })

        parsed = lfm._parse_similar_tracks({"similartracks": {"track": raw_sim}})
        # Drops the Album Intro skit and drops the weak tail (< 0.15 / > 22 tracks)
        self.assertTrue(all("Intro" not in t.title for t in parsed))
        self.assertLessEqual(len(parsed), 22)
        self.assertTrue(all(t.relevance_score >= 0.15 for t in parsed))
        tiers_present = {t.tier for t in parsed}
        self.assertEqual(tiers_present, {1, 2, 3})

    @patch("src.skills.music.aggregators.deezer._HTTP_SESSION.get")
    def test_deezer_similar_validates_title_and_artist_match(self, mock_get):
        from src.skills.music.aggregators.deezer import DeezerAggregator

        # Deezer search returns an unrelated artist first, followed by the actual matching artist
        search_resp = MagicMock()
        search_resp.json.return_value = {
            "data": [
                {"id": 999, "title": "Solar System Meditation", "artist": {"id": 888, "name": "Yoga Guru"}},
                {"id": 101, "title": "Solar System", "artist": {"id": 42, "name": "Sub Focus"}},
            ]
        }
        rel_resp = MagicMock()
        rel_resp.json.return_value = {
            "data": [{"id": 55, "name": "Dimension"}]
        }
        top_resp = MagicMock()
        top_resp.json.return_value = {
            "data": [{"id": 777, "title": "Offender", "artist": {"name": "Dimension"}, "album": {"title": "Organ"}}]
        }
        mock_get.side_effect = [search_resp, rel_resp, top_resp]

        dz = DeezerAggregator()
        tracks = dz.get_similar("Solar System by Sub Focus")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].title, "Offender")
        self.assertEqual(tracks[0].artist, "Dimension")
        self.assertEqual(tracks[0].tier, 1)

    def test_multi_source_consensus_boosts_to_shelf_1(self):
        from src.skills.music.aggregators.manager import MusicAggregatorManager
        from src.skills.music.models import Track

        mgr = MusicAggregatorManager()
        candidates = [
            Track(title="Offender", artist="Dimension", source="lastfm_dnb", tier=2, relevance_score=0.80, verified_genre=True),
            Track(title="Offender", artist="Dimension", album="Organ", deezer_id=12345, source="deezer_dnb", tier=2, relevance_score=0.78),
        ]
        deduped = mgr._filter_and_deduplicate(candidates)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].tier, 1)
        self.assertEqual(deduped[0].album, "Organ")
        self.assertEqual(deduped[0].deezer_id, 12345)
        self.assertTrue(deduped[0].verified_genre)
        self.assertGreater(deduped[0].relevance_score, 0.90)


if __name__ == "__main__":
    unittest.main()


