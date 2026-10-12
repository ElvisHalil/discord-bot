import sys
import unittest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.skills.music.aggregators.beatport import BeatportAggregator


class TestBeatportAggregator(unittest.TestCase):
    """Test suite for Beatport electronic chart aggregator."""

    def test_beatport_instance(self):
        agg = BeatportAggregator()
        self.assertIsNotNone(agg)
        self.assertEqual(len(agg.CHARTS), 3)

    def test_beatport_scrape(self):
        agg = BeatportAggregator()
        tracks = agg.get_charts(genre="techno")
        self.assertIsInstance(tracks, list)
        if tracks:
            first = tracks[0]
            self.assertTrue(hasattr(first, "title"))
            self.assertTrue(hasattr(first, "artist"))
            self.assertTrue(hasattr(first, "source"))


if __name__ == "__main__":
    unittest.main()
