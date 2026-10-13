import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

from src.skills.sports.aggregators.liquipedia import LiquipediaAggregator
from src.skills.sports.csgo import skill_csgo_esports
from src.orchestrator import normalize_tool_args


MOCK_LIQUIPEDIA_HTML = """
<h2><span class="mw-headline" id="2027">2027</span></h2>
<table class="wikitable">
  <tr><th>G</th><th>Tier</th><th>Tournament</th><th>Date</th><th>Prize Pool</th><th>Location</th><th>P#</th><th>Winner</th><th>Runner-up</th></tr>
  <tr><td></td><td></td><td><a href="/counterstrike/Future_Major_2027">Future Major 2027</a></td><td>Nov 2027</td><td>$1,250,000</td><td>Shanghai</td><td>32</td><td>TBD</td><td>TBD</td></tr>
</table>

<h2><span class="mw-headline" id="2026">2026</span></h2>
<table class="wikitable">
  <tr><th>G</th><th>Tier</th><th>Tournament</th><th>Date</th><th>Prize Pool</th><th>Location</th><th>P#</th><th>Winner</th><th>Runner-up</th></tr>
  <tr><td></td><td></td><td><a href="/counterstrike/Late_Major_2026">Late Major 2026</a></td><td>Nov 25 – Dec 13, 2026</td><td>$1,250,000</td><td>Singapore</td><td>32</td><td>TBD</td><td>TBD</td></tr>
  <tr><td></td><td></td><td><a href="/counterstrike/Cancelled_Cup">Cancelled Cup 2026</a></td><td>Nov 16–22, 2026</td><td>$600,000</td><td>Dubai</td><td>-</td><td>Cancelled</td><td></td></tr>
  <tr><td></td><td></td><td><a href="/counterstrike/Next_Championship_2026">Next Championship 2026</a></td><td>Oct 14–18, 2026</td><td>$1,000,000</td><td>Malta</td><td>8</td><td>TBD</td><td>TBD</td></tr>
  <tr><td></td><td></td><td><a href="/counterstrike/Recent_League_2026">Recent League 2026</a></td><td>Oct 03–11, 2026</td><td>$1,000,000</td><td>Katowice</td><td>16</td><td>Team Vitality</td><td>MOUZ</td></tr>
  <tr><td></td><td></td><td><a href="/counterstrike/Past_Open_2026">Past Open 2026</a></td><td>Sep 01–06, 2026</td><td>$500,000</td><td>Copenhagen</td><td>16</td><td>Team Spirit</td><td>FaZe Clan</td></tr>
</table>

<h2><span class="mw-headline" id="2025">2025</span></h2>
<table class="wikitable">
  <tr><th>G</th><th>Tier</th><th>Tournament</th><th>Date</th><th>Prize Pool</th><th>Location</th><th>P#</th><th>Winner</th><th>Runner-up</th></tr>
  <tr><td></td><td></td><td><a href="/counterstrike/Past_Major_2025">Past Major 2025</a></td><td>Nov 2025</td><td>$1,250,000</td><td>Budapest</td><td>32</td><td>FaZe Clan</td><td>NAVI</td></tr>
</table>
"""


class TestLiquipediaAggregator(unittest.TestCase):

    def setUp(self):
        self.agg = LiquipediaAggregator()

    @patch("src.skills.sports.aggregators.liquipedia.fetch_json")
    def test_get_s_tier_tournaments_targets_current_year(self, mock_fetch):
        mock_fetch.return_value = {
            "parse": {"text": {"*": MOCK_LIQUIPEDIA_HTML}}
        }

        output = self.agg.get_s_tier_tournaments()
        curr_year = datetime.now().year

        self.assertIn(f"CURRENT & UPCOMING S-TIER TOURNAMENTS ({curr_year})", output)
        self.assertIn(f"RECENT TOURNAMENT CHAMPIONS ({curr_year})", output)

        # Cancelled events must be filtered
        self.assertNotIn("Cancelled Cup", output)

        # Next Championship should appear before Late Major because upcoming is ordered chronologically
        next_pos = output.find("Next Championship 2026")
        late_pos = output.find("Late Major 2026")
        self.assertTrue(next_pos != -1 and late_pos != -1)
        self.assertLess(next_pos, late_pos, "Chronologically closer upcoming event should appear first")

        # Champions should include Recent League and Past Open
        self.assertIn("Recent League 2026", output)
        self.assertIn("Team Vitality", output)
        self.assertIn("MOUZ", output)

        # URLs should be valid HTTPS links and tool id should be clearly noted
        self.assertIn("<https://liquipedia.net/counterstrike/Next_Championship_2026>", output)
        self.assertIn("(event_page: 'Next_Championship_2026')", output)

        # Should NOT contain broken pseudo-links like [event_page: ...]
        self.assertNotIn("[event_page:", output)

    @patch("src.skills.sports.aggregators.liquipedia.fetch_json")
    def test_get_event_info_extracts_rosters(self, mock_fetch):
        mock_html = """
        <div class="team-participant-card">
          <div class="team-participant-card__opponent"><a title="Team Vitality">Vitality</a></div>
          <div class="team-participant-card__member"><span class="name"><a>ZywOo</a></span></div>
          <div class="team-participant-card__member"><span class="name"><a>apEX</a></span></div>
        </div>
        <p>Tournament details and bracket info.</p>
        """
        mock_fetch.return_value = {"parse": {"text": {"*": mock_html}}}

        res = self.agg.get_event_info("ESL/Pro_League/Season_24")
        self.assertIn("TOURNAMENT ROSTERS", res)
        self.assertIn("Team Vitality: ZywOo, apEX", res)


class TestCsgoSkill(unittest.TestCase):

    @patch("src.skills.sports.csgo._esl_aggregator.get_live_matches")
    @patch("src.skills.sports.csgo._liquipedia_aggregator.get_s_tier_tournaments")
    @patch("src.skills.sports.csgo._hltv_aggregator.get_rankings")
    def test_skill_csgo_esports_all(self, mock_hltv, mock_liquipedia, mock_esl):
        mock_esl.return_value = "[LIVE ESL MATCHES]\nNo matches.\n"
        mock_liquipedia.return_value = "[CURRENT & UPCOMING S-TIER TOURNAMENTS (2026)]\n- Tourney\n"
        mock_hltv.return_value = "[HLTV TOP 7 WORLD RANKINGS]\n1. Spirit\n"

        res = skill_csgo_esports(query_type="all")

        self.assertIn("COUNTER-STRIKE ESPORTS DATA", res)
        self.assertIn("No matches.", res)
        self.assertIn("Tourney", res)
        self.assertIn("1. Spirit", res)
        # Check LLM instructions contain synthesis and link guidelines
        self.assertIn("NEVER output pseudo-links", res)
        self.assertIn("DO NOT create a prominent empty section", res)

    def test_tool_arg_aliases_for_csgo(self):
        # Model passing 'category' or 'tournament' or 'type'
        args = {"type": "tournaments", "tournament": "ESL/Pro_League"}
        norm = normalize_tool_args("skill_csgo_esports", skill_csgo_esports, args)
        self.assertEqual(norm.get("query_type"), "tournaments")
        self.assertEqual(norm.get("event_page"), "ESL/Pro_League")


if __name__ == "__main__":
    unittest.main()
