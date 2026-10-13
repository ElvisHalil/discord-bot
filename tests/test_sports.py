import unittest
from unittest.mock import patch, MagicMock
from src.skills.sports.aggregators.f1 import F1Aggregator
from src.skills.sports.aggregators.soccer import SoccerAggregator
from src.skills.sports.aggregators.nba import NbaAggregator
from src.skills.sports.aggregators.hockey import HockeyAggregator
from src.skills.sports.sports import skill_sports_info


class TestF1Aggregator(unittest.TestCase):
    def setUp(self):
        self.agg = F1Aggregator()

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_get_schedule(self, mock_fetch):
        # Mock scoreboard with calendar and recent event
        mock_fetch.side_effect = [
            {
                "season": {"year": 2026},
                "leagues": [{
                    "calendar": [
                        {
                            "label": "Australian Grand Prix",
                            "startDate": "2026-03-15T05:00Z",
                            "endDate": "2026-03-15T07:00Z"
                        },
                        {
                            "label": "United States Grand Prix",
                            "startDate": "2026-10-23T17:30Z",
                            "endDate": "2026-10-25T20:00Z"
                        }
                    ]
                }],
                "events": [{
                    "name": "Singapore Grand Prix",
                    "status": {"type": {"detail": "Final"}},
                    "competitions": [{
                        "type": {"abbreviation": "Race"},
                        "competitors": [
                            {"order": 1, "winner": True, "athlete": {"displayName": "Max Verstappen"}},
                            {"order": 2, "winner": False, "athlete": {"displayName": "Charles Leclerc"}}
                        ]
                    }]
                }]
            },
            # Secondary call for sessions
            {
                "events": [{
                    "competitions": [
                        {"type": {"text": "Practice 1"}, "date": "2026-10-23T17:30Z"},
                        {"type": {"text": "Qualifying"}, "date": "2026-10-24T21:00Z"},
                        {"type": {"text": "Race"}, "date": "2026-10-25T20:00Z"}
                    ]
                }]
            }
        ]

        result = self.agg.get_schedule()
        self.assertIn("FORMULA 1 2026 WORLD CHAMPIONSHIP", result)
        self.assertIn("United States Grand Prix", result)
        self.assertIn("Practice 1", result)
        self.assertIn("Max Verstappen", result)
        self.assertIn("Charles Leclerc", result)

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_get_standings(self, mock_fetch):
        mock_fetch.return_value = {
            "children": [
                {
                    "name": "Driver Standings",
                    "standings": {
                        "entries": [
                            {
                                "athlete": {"displayName": "Kimi Antonelli"},
                                "stats": [{"name": "rank", "displayValue": "1"}, {"name": "championshipPts", "displayValue": "340"}]
                            },
                            {
                                "athlete": {"displayName": "George Russell"},
                                "stats": [{"name": "rank", "displayValue": "2"}, {"name": "championshipPts", "displayValue": "238"}]
                            }
                        ]
                    }
                },
                {
                    "name": "Constructor Standings",
                    "standings": {
                        "entries": [
                            {
                                "team": {"displayName": "Mercedes"},
                                "stats": [{"name": "rank", "displayValue": "1"}, {"name": "points", "displayValue": "578"}]
                            }
                        ]
                    }
                }
            ]
        }

        result = self.agg.get_standings()
        self.assertIn("DRIVERS' WORLD CHAMPIONSHIP", result)
        self.assertIn("Kimi Antonelli - 340 pts", result)
        self.assertIn("George Russell - 238 pts", result)
        self.assertIn("Mercedes - 578 pts", result)


class TestSoccerAggregator(unittest.TestCase):
    def setUp(self):
        self.agg = SoccerAggregator()

    def test_detect_league(self):
        self.assertEqual(self.agg._detect_league("premier league schedule"), "epl")
        self.assertEqual(self.agg._detect_league("who is leading in laliga?"), "laliga")
        self.assertEqual(self.agg._detect_league("arsenal match"), "epl")
        self.assertEqual(self.agg._detect_league("real madrid score"), "laliga")
        self.assertEqual(self.agg._detect_league("bayern munich"), "bundesliga")
        self.assertEqual(self.agg._detect_league("champions league scores"), "ucl")

    @patch("src.skills.sports.aggregators.soccer.fetch_json")
    def test_get_matches_with_goals(self, mock_fetch):
        mock_fetch.return_value = {
            "events": [{
                "name": "Sporting CP at Lens",
                "date": "2026-10-13T19:00Z",
                "competitions": [{
                    "status": {"type": {"detail": "HT", "state": "in", "completed": False}},
                    "venue": {"fullName": "Stade Bollaert-Delelis"},
                    "competitors": [
                        {"homeAway": "home", "score": "0", "form": "DLDWL", "team": {"id": "1", "displayName": "Lens"}},
                        {"homeAway": "away", "score": "1", "form": "DDDWW", "team": {"id": "2", "displayName": "Sporting CP"}}
                    ],
                    "details": [{
                        "type": {"text": "Goal"},
                        "clock": {"displayValue": "22'"},
                        "team": {"id": "2"},
                        "athletesInvolved": [{"displayName": "Luis Suárez"}]
                    }]
                }]
            }]
        }

        result = self.agg.get_matches("uefa.champions", "UEFA Champions League")
        self.assertIn("Lens 0 - 1 Sporting CP", result)
        self.assertIn("🔴 [LIVE]", result)
        self.assertIn("22' Luis Suárez (Sporting CP)", result)
        self.assertIn("Stade Bollaert-Delelis", result)

    @patch("src.skills.sports.aggregators.soccer.fetch_json")
    def test_get_standings(self, mock_fetch):
        mock_fetch.return_value = {
            "children": [{
                "standings": {
                    "entries": [
                        {
                            "team": {"displayName": "Manchester City"},
                            "stats": [
                                {"name": "rank", "displayValue": "1"},
                                {"name": "gamesPlayed", "displayValue": "6"},
                                {"name": "wins", "displayValue": "6"},
                                {"name": "ties", "displayValue": "0"},
                                {"name": "losses", "displayValue": "0"},
                                {"name": "pointDifferential", "displayValue": "+11"},
                                {"name": "points", "displayValue": "18"},
                            ]
                        }
                    ]
                }
            }]
        }

        result = self.agg.get_standings("eng.1", "English Premier League")
        self.assertIn("ENGLISH PREMIER LEAGUE STANDINGS", result)
        self.assertIn("Manchester City", result)
        self.assertIn("18", result)


class TestNbaAggregator(unittest.TestCase):
    def setUp(self):
        self.agg = NbaAggregator()

    @patch("src.skills.sports.aggregators.nba.fetch_json")
    def test_get_games(self, mock_fetch):
        mock_fetch.return_value = {
            "events": [{
                "competitions": [{
                    "status": {"type": {"detail": "Final", "completed": True, "state": "post"}},
                    "venue": {"fullName": "Moda Center"},
                    "competitors": [
                        {
                            "homeAway": "home",
                            "score": "119",
                            "linescores": [{"value": 34.0}, {"value": 38.0}, {"value": 28.0}, {"value": 19.0}],
                            "team": {"displayName": "Portland Trail Blazers"},
                            "records": [{"summary": "2-0"}]
                        },
                        {
                            "homeAway": "away",
                            "score": "66",
                            "linescores": [{"value": 21.0}, {"value": 12.0}, {"value": 16.0}, {"value": 17.0}],
                            "team": {"displayName": "London Lions"},
                            "records": [{"summary": "0-1"}]
                        }
                    ]
                }]
            }]
        }

        result = self.agg.get_games()
        self.assertIn("London Lions", result)
        self.assertIn("Portland Trail Blazers", result)
        self.assertIn("119", result)
        self.assertIn("Q1 | Q2 | Q3 | Q4", result)
        self.assertIn("Moda Center", result)


class TestHockeyAggregator(unittest.TestCase):
    def setUp(self):
        self.agg = HockeyAggregator()

    @patch("src.skills.sports.aggregators.hockey.fetch_json")
    def test_get_games(self, mock_fetch):
        mock_fetch.return_value = {
            "events": [{
                "competitions": [{
                    "status": {"type": {"detail": "Final", "completed": True, "state": "post"}},
                    "venue": {"fullName": "Little Caesars Arena"},
                    "competitors": [
                        {
                            "homeAway": "home",
                            "score": "3",
                            "linescores": [{"value": 1.0}, {"value": 1.0}, {"value": 1.0}],
                            "team": {"displayName": "Detroit Red Wings"},
                            "records": [{"summary": "2-3-1"}]
                        },
                        {
                            "homeAway": "away",
                            "score": "2",
                            "linescores": [{"value": 1.0}, {"value": 0.0}, {"value": 1.0}],
                            "team": {"displayName": "New Jersey Devils"},
                            "records": [{"summary": "3-2-0"}]
                        }
                    ]
                }]
            }]
        }

        result = self.agg.get_games()
        self.assertIn("New Jersey Devils", result)
        self.assertIn("Detroit Red Wings", result)
        self.assertIn("P1 | P2 | P3", result)
        self.assertIn("Little Caesars Arena", result)


class TestSkillSportsInfo(unittest.TestCase):
    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_f1_routing(self, mock_fetch):
        mock_fetch.return_value = {
            "season": {"year": 2026},
            "leagues": [{"calendar": []}],
            "events": []
        }
        res = skill_sports_info("f1", "schedule")
        self.assertIn("FORMULA 1 2026 WORLD CHAMPIONSHIP", res)

    @patch("src.skills.sports.aggregators.soccer.fetch_json")
    def test_soccer_routing(self, mock_fetch):
        mock_fetch.return_value = {"events": []}
        res = skill_sports_info("soccer", "epl matches")
        self.assertIn("ENGLISH PREMIER LEAGUE MATCHES", res)

    @patch("src.skills.sports.aggregators.nba.fetch_json")
    def test_nba_routing(self, mock_fetch):
        mock_fetch.return_value = {"events": []}
        res = skill_sports_info("nba", "today games")
        self.assertIn("NBA SCOREBOARD", res)

    @patch("src.skills.sports.aggregators.hockey.fetch_json")
    def test_hockey_routing(self, mock_fetch):
        mock_fetch.return_value = {"events": []}
        res = skill_sports_info("nhl", "hockey scores")
        self.assertIn("NHL SCOREBOARD", res)

    @patch("src.skills.sports.aggregators.liquipedia.fetch_json")
    @patch("src.skills.sports.aggregators.hltv.fetch_soup")
    @patch("src.skills.sports.aggregators.esl_live.EslLiveAggregator.get_live_matches")
    def test_cs_routing_via_sports_info(self, mock_esl, mock_hltv, mock_liqui):
        mock_esl.return_value = "No active streams"
        mock_liqui.return_value = {"parse": {"text": {"*": "<div>S-Tier</div>"}}}
        mock_hltv.return_value = MagicMock(select=MagicMock(return_value=[]))

        res = skill_sports_info("cs", "whats happening in cs")
        self.assertIn("COUNTER-STRIKE ESPORTS DATA", res)

    @patch("src.skills.sports.aggregators.odds.fetch_soup")
    @patch("src.tools.web.search_web")
    def test_cs_odds_with_stake_simulation(self, mock_search, mock_soup):
        mock_search.return_value = "https://www.hltv.org/betting/analytics/123/falcons-vs-ninjas-in-pyjamas-thunderpick-world-championship-2026"
        mock_page = MagicMock()
        mock_page.title.get_text.return_value = "Falcons vs Ninjas in Pyjamas odds and prediction | HLTV.org"
        mock_page.get_text.return_value = "Best odds - 1.26 Best odds - 4.20 Falcons is the bookmaker favorite"
        mock_soup.return_value = mock_page

        from src.skills.sports.aggregators.odds import OddsAggregator
        agg = OddsAggregator()
        res = agg.get_odds("if I bet $200 on falcons tomorrow will I win and how much?", sport="sports")
        self.assertIn("MATCH BETTING ODDS: Falcons vs Ninjas in Pyjamas", res)
        self.assertIn("Bet Payout Simulation ($200.00 Stake)", res)
        self.assertIn("Returns **$252.00**", res)
        self.assertIn("Returns **$840.00**", res)


if __name__ == "__main__":
    unittest.main()

