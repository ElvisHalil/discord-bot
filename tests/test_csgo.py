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
        from src.skills.sports.aggregators.liquipedia import clear_liquipedia_cache
        clear_liquipedia_cache()
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
    def test_get_event_info_extracts_rosters_groups_and_matches(self, mock_fetch):
        mock_html = """
        <div class="team-participant-card">
          <div class="team-participant-card__opponent"><a title="Team Vitality">Vitality</a></div>
          <div class="team-participant-card__member"><span class="name"><a>ZywOo</a></span></div>
          <div class="team-participant-card__member"><span class="name"><a>apEX</a></span></div>
          <div class="team-participant-card__member"><span class="name"><a>XTQZZZ</a></span><div class="team-participant-card__member-role-right">Coach</div></div>
        </div>
        <div class="general-collapsible collapsed">
          <div>Former Participants</div>
          <div class="team-participant-card">
            <div class="team-participant-card__opponent"><a title="Virtus.pro">Virtus.pro</a></div>
            <div class="team-participant-card__member"><span class="name"><a>Jame</a></span></div>
          </div>
          <ul><li>Virtus.pro withdrew from the event and were replaced by Ninjas in Pyjamas.</li></ul>
        </div>
        <table class="grouptable">
          <tr><th>Group A</th></tr>
          <tr><td><span class="block-team"><a title="Team Falcons">Falcons</a></span></td></tr>
          <tr><td><span class="block-team"><a title="Ninjas in Pyjamas">NIP</a></span></td></tr>
        </table>
        <div class="brkts-matchlist">
          <div class="brkts-matchlist-title">Group A - Opening Matches</div>
          <div class="brkts-popup">
            <div class="match-info-header">
              <div class="match-info-header-opponent"><a title="Team Falcons">Falcons</a></div>
              <div class="match-info-header-scoreholder">
                <span class="match-info-header-scoreholder-upper">vs</span>
                <span class="match-info-header-scoreholder-lower">(Bo3)</span>
              </div>
              <div class="match-info-header-opponent"><a title="Ninjas in Pyjamas">NIP</a></div>
            </div>
            <span class="timer-object" data-timestamp="1791966600">October 14, 2026 - 10:30 CEST</span>
          </div>
        </div>
        <p>Tournament details and bracket info.</p>
        """
        mock_fetch.return_value = {"parse": {"text": {"*": mock_html}}}

        res = self.agg.get_event_info("Thunderpick/World_Championship/2026")
        self.assertIn("PARTICIPATING TEAMS", res)
        self.assertIn("TOURNAMENT ROSTERS (Active Players & Coaches)", res)
        self.assertIn("- **Team Vitality**: ZywOo, apEX | Coach: XTQZZZ", res)
        # Collapsed Former Participants (Virtus.pro) must NOT appear as an active roster bullet
        self.assertNotIn("- **Virtus.pro**:", res)
        self.assertIn("Withdrawn / Replaced Teams: Virtus.pro", res)

        # Group table extraction
        self.assertIn("GROUP STAGE SEEDING", res)
        self.assertIn("- **Group A**: Team Falcons, Ninjas in Pyjamas", res)

        # Exact match schedule & timestamp extraction
        self.assertIn("MATCH SCHEDULE & RESULTS (Exact Times)", res)
        self.assertIn("[Group A - Opening Matches]", res)
        self.assertIn("**Team Falcons** vs **Ninjas in Pyjamas** (Bo3)", res)
        self.assertIn("Oct 14, 08:30 UTC", res)
        self.assertNotIn("<t:1791966600:t>", res)


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
        # Check LLM instructions contain synthesis, exact schedule, and link guidelines
        self.assertIn("NEVER output pseudo-links", res)
        self.assertIn("DO NOT create a prominent empty section", res)
        self.assertIn("MATCH SCHEDULE & RESULTS (Exact Times)", res)

    @patch("src.skills.sports.csgo._liquipedia_aggregator.get_event_info")
    @patch("src.skills.sports.csgo._liquipedia_aggregator.get_s_tier_tournaments")
    def test_skill_csgo_esports_honors_event_page_across_query_types(self, mock_tournaments, mock_event_info):
        mock_tournaments.return_value = "[CURRENT & UPCOMING S-TIER TOURNAMENTS]\n"
        mock_event_info.return_value = "=== EVENT DETAILS: Thunderpick/World_Championship/2026 ===\n"

        res = skill_csgo_esports(query_type="tournaments", event_page="Thunderpick/World_Championship/2026")
        mock_tournaments.assert_called_once_with(include_next_event_details=False)
        mock_event_info.assert_called_once_with("Thunderpick/World_Championship/2026")
        self.assertIn("EVENT DETAILS: Thunderpick/World_Championship/2026", res)

    @patch("src.skills.sports.sports.skill_csgo_esports")
    def test_sports_info_routes_cs_match_time_queries_to_schedule(self, mock_csgo):
        from src.skills.sports.sports import skill_sports_info
        mock_csgo.return_value = "CS SCHEDULE DATA"

        res = skill_sports_info(sport="cs", query="what time are matches today?")
        mock_csgo.assert_called_once_with(query_type="tournaments", event_page=None)
        self.assertEqual(res, "CS SCHEDULE DATA")

    @patch("src.skills.sports.sports.skill_csgo_esports")
    def test_sports_info_routes_cs_player_stats_queries_to_player_stats(self, mock_csgo):
        from src.skills.sports.sports import skill_sports_info
        mock_csgo.return_value = "CS PLAYER STATS DATA"

        res = skill_sports_info(sport="cs", query="whats falcons player current stats")
        mock_csgo.assert_called_once_with(query_type="player_stats", player_url="whats falcons player current stats")
        self.assertEqual(res, "CS PLAYER STATS DATA")

    @patch("src.skills.sports.aggregators.kashrock.KashrockAggregator.is_configured", return_value=False)
    @patch("src.skills.sports.aggregators.hltv.fetch_soup")
    def test_hltv_aggregator_resolves_team_player_stats(self, mock_fetch_soup, _mock_kr):
        from bs4 import BeautifulSoup
        from src.skills.sports.aggregators.hltv import HltvAggregator, clear_hltv_cache
        clear_hltv_cache()

        rankings_html = """
        <div class="ranked-team">
          <span class="name">Falcons</span>
          <a href="/player/3741/niko">NiKo</a>
          <a href="/player/19230/m0nesy">m0NESY</a>
        </div>
        """
        niko_html = """
        <div class="playerProfile">
          <h1 class="playerNickname">NiKo</h1>
          <div class="playerRealname">Nikola Kovač</div>
          <div class="playerTeam"><span class="listRight"><a href="/team/11283/falcons">Falcons</a></span></div>
          <div class="playerAge"><span class="listRight"><span itemprop="text">29 years</span></span></div>
          <div class="tab-content">(Past 3 months • 35 maps)</div>
          <div class="player-stat">Rating 3.0 1.11</div>
          <div class="player-stat">Firepower 70 /100</div>
        </div>
        """
        m0nesy_html = """
        <div class="playerProfile">
          <h1 class="playerNickname">m0NESY</h1>
          <div class="playerRealname">Ilya Osipov</div>
          <div class="playerTeam"><span class="listRight"><a href="/team/11283/falcons">Falcons</a></span></div>
          <div class="playerAge"><span class="listRight"><span itemprop="text">21 years</span></span></div>
          <div class="tab-content">(Past 3 months • 35 maps)</div>
          <div class="player-stat">Rating 3.0 1.36</div>
          <div class="player-stat">Sniping 94 /100</div>
        </div>
        """

        def side_effect(url, timeout=30):
            if "ranking/teams" in url:
                return BeautifulSoup(rankings_html, "html.parser")
            if "3741/niko" in url:
                return BeautifulSoup(niko_html, "html.parser")
            if "19230/m0nesy" in url:
                return BeautifulSoup(m0nesy_html, "html.parser")
            return BeautifulSoup("", "html.parser")

        mock_fetch_soup.side_effect = side_effect
        agg = HltvAggregator()
        res = agg.get_player_stats("whats falcons player current stats")
        self.assertIn("[TEAM PLAYER STATS: Falcons]", res)
        self.assertIn("**NiKo** (Nikola Kovač)", res)
        self.assertIn("Rating 3.0 1.11 | Firepower 70/100", res)
        self.assertIn("**m0NESY** (Ilya Osipov)", res)
        self.assertIn("Rating 3.0 1.36 | Sniping 94/100", res)

    def test_tool_arg_aliases_for_csgo(self):
        # Model passing 'category' or 'tournament' or 'type'
        args = {"type": "tournaments", "tournament": "ESL/Pro_League", "match": "Falcons vs NIP"}
        norm = normalize_tool_args("skill_csgo_esports", skill_csgo_esports, args)
        self.assertEqual(norm.get("query_type"), "tournaments")
        self.assertEqual(norm.get("event_page"), "ESL/Pro_League")
        self.assertEqual(norm.get("matchup"), "Falcons vs NIP")

    @patch.dict("os.environ", {"YOUTUBE_API_KEY": "test_yt_key"}, clear=False)
    @patch("src.skills.sports.aggregators.kashrock.KashrockAggregator.get_live_scoreboards", return_value="")
    @patch("src.skills.sports.aggregators.esl_live.requests.get")
    def test_esl_live_aggregator_checks_esl_blast_and_thunderpick(self, mock_get, _mock_kr):
        from src.skills.sports.aggregators.esl_live import EslLiveAggregator

        def fake_get(_url, params=None, timeout=5):
            cid = (params or {}).get("channelId")
            resp = MagicMock()
            if cid == "UCDs5D1luLRP2oo3lk71MaCA":  # thunderpickesports
                resp.json.return_value = {
                    "items": [{
                        "id": {"videoId": "7Ql1O23W-hk"},
                        "snippet": {"title": "Thunderpick World Championship 2026 Finals - Day 3"},
                    }]
                }
            else:
                resp.json.return_value = {"items": []}
            return resp

        mock_get.side_effect = fake_get
        agg = EslLiveAggregator()
        out = agg.get_live_matches()

        self.assertEqual(mock_get.call_count, 3)
        checked_ids = [call.kwargs["params"]["channelId"] for call in mock_get.call_args_list]
        self.assertEqual(
            checked_ids,
            [
                "UCPq2ETz4aAGo2Z-8JisDPIA",  # ESLCS
                "UC9k--dE_UE0Faxzgb_DDkYQ",  # BLASTPremier
                "UCDs5D1luLRP2oo3lk71MaCA",  # thunderpickesports
            ],
        )
        self.assertIn("[LIVE CS2 STREAMS]", out)
        self.assertIn("Thunderpick Esports (@thunderpickesports)", out)
        self.assertIn("Thunderpick World Championship 2026 Finals - Day 3", out)
        self.assertIn("https://www.youtube.com/watch?v=7Ql1O23W-hk", out)

    @patch("src.skills.sports.sports.skill_csgo_esports")
    def test_sports_info_routes_cs_live_stream_queries_to_live_streams(self, mock_csgo):
        from src.skills.sports.sports import skill_sports_info
        mock_csgo.return_value = "[LIVE CS2 STREAMS]"

        res = skill_sports_info(sport="cs", query="are any live match streams happening on thunderpick or blast?")
        mock_csgo.assert_called_once_with(query_type="live_streams")
        self.assertEqual(res, "[LIVE CS2 STREAMS]")

    @patch("src.skills.sports.sports.skill_csgo_esports")
    def test_thunderpicks_and_blast_tournament_routing_and_group_records(self, mock_csgo):
        from bs4 import BeautifulSoup
        from src.skills.sports.sports import skill_sports_info

        mock_csgo.return_value = "CS EVENT INFO"
        skill_sports_info(query="thunderpicks table")
        mock_csgo.assert_called_with(query_type="event_info", event_page="thunderpicks table")

        mock_csgo.reset_mock()
        skill_sports_info(query="blast standings")
        mock_csgo.assert_called_with(query_type="event_info", event_page="blast standings")

        agg = LiquipediaAggregator()
        agg._known_events = [
            {"name": "PGL Masters Bucharest 2026", "slug": "PGL/2026/Masters", "location": "Bucharest"},
            {"name": "Thunderpick World Championship 2026", "slug": "Thunderpick/World_Championship/2026", "location": "Attard"},
            {"name": "BLAST Rivals Fall 2026", "slug": "BLAST/Rivals/2026/Fall", "location": "Chek Lap Kok"},
        ]
        self.assertEqual(agg.resolve_event_slug("thunderpicks table"), "Thunderpick/World_Championship/2026")
        self.assertEqual(agg.resolve_event_slug("blast standings"), "BLAST/Rivals/2026/Fall")

        group_html = """
        <table class="grouptable">
          <tr><th>Group A Week 1</th></tr>
          <tr><th>1.</th><td><span class="block-team"><a title="Team Falcons">Falcons</a></span></td><td>2-0</td><td>63-50</td><td>+13</td></tr>
          <tr><th>2.</th><td><span class="block-team"><a title="Aurora Gaming">Aurora</a></span></td><td>2-1</td><td>64-57</td><td>+7</td></tr>
        </table>
        """
        groups = agg._parse_groups_from_soup(BeautifulSoup(group_html, "html.parser"))
        self.assertEqual(groups, ["- **Group A**: Team Falcons [2-0, Rds: 63-50 (+13)], Aurora Gaming [2-1, Rds: 64-57 (+7)]"])


class TestOdds(unittest.TestCase):

    @patch("src.skills.sports.aggregators.kashrock.KashrockAggregator.is_configured", return_value=False)
    @patch("src.skills.sports.aggregators.odds.fetch_soup")
    def test_odds_aggregator_hltv_parsing(self, mock_soup, _mock_kr):
        from src.skills.sports.aggregators.odds import OddsAggregator, decimal_to_american, implied_probability
        from bs4 import BeautifulSoup

        self.assertEqual(decimal_to_american(1.26), "-385")
        self.assertEqual(decimal_to_american(4.20), "+320")
        self.assertEqual(implied_probability(1.26), "79.4%")
        self.assertEqual(implied_probability(4.20), "23.8%")

        html = """
        <html>
        <head><title>Falcons vs Ninjas in Pyjamas odds and prediction | Full overview</title></head>
        <body>
          <p>Best odds - 1.26</p>
          <p>Best odds - 4.20</p>
          <p>Falcons is the bookmaker favorite</p>
        </body>
        </html>
        """
        mock_soup.return_value = BeautifulSoup(html, "html.parser")

        with patch("src.tools.web.search_web") as mock_search:
            mock_search.return_value = "https://www.hltv.org/betting/analytics/123/falcons-vs-ninjas-in-pyjamas"
            agg = OddsAggregator()
            res = agg.get_odds("Falcons vs Ninjas in Pyjamas", sport="cs2")

            self.assertIn("MATCH BETTING ODDS: Falcons vs Ninjas in Pyjamas", res)
            self.assertIn("Favorite: **Falcons**", res)
            self.assertIn("- **Falcons**: 1.26 | Win probability: ~79.4%", res)
            self.assertIn("- **Ninjas in Pyjamas**: 4.20 | Win probability: ~23.8%", res)

    def test_tool_arg_aliases_for_odds(self):
        from src.skills.sports.odds import skill_betting_odds
        args = {"matchup": "Falcons vs NIP", "category": "cs2"}
        norm = normalize_tool_args("skill_betting_odds", skill_betting_odds, args)
        self.assertEqual(norm.get("query"), "Falcons vs NIP")
        self.assertEqual(norm.get("sport"), "cs2")


if __name__ == "__main__":
    unittest.main()
