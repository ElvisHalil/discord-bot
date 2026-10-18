import unittest
from datetime import datetime, timedelta
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

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_get_schedule_this_week(self, mock_fetch):
        mock_fetch.return_value = {
            "season": {"year": 2026},
            "leagues": [{"calendar": [{"label": "United States Grand Prix", "startDate": "2026-10-23T17:30Z", "endDate": "2026-10-25T20:00Z"}]}],
            "events": []
        }
        res = self.agg.get_schedule(filter_query="races this week")
        self.assertIn("F1 RACES THIS WEEK", res)

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_get_schedule_this_month(self, mock_fetch):
        mock_fetch.side_effect = [
            {
                "season": {"year": 2026},
                "leagues": [{"calendar": [{"label": "United States Grand Prix", "startDate": "2026-10-23T17:30Z", "endDate": "2026-10-25T20:00Z"}]}],
                "events": []
            },
            {"events": []}
        ]
        res = self.agg.get_schedule(filter_query="races this month")
        self.assertIn("UPCOMING GRAND PRIX THIS MONTH", res)
        self.assertIn("United States Grand Prix", res)

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_get_schedule_all_upcoming_with_fp3_and_circuits(self, mock_fetch):
        mock_fetch.side_effect = [
            {
                "season": {"year": 2026},
                "leagues": [{"calendar": [{"label": "United States Grand Prix", "startDate": "2026-10-23T17:30Z", "endDate": "2026-10-25T20:00Z"}]}],
                "events": []
            },
            {
                "events": [{
                    "circuit": {"fullName": "Circuit of the Americas", "address": {"city": "Austin", "country": "USA"}},
                    "competitions": [
                        {"type": {"abbreviation": "FP1"}, "date": "2026-10-23T17:30Z"},
                        {"type": {"abbreviation": "FP2"}, "date": "2026-10-23T21:00Z"},
                        {"type": {"abbreviation": "FP3"}, "date": "2026-10-24T17:30Z"},
                        {"type": {"abbreviation": "Qual"}, "date": "2026-10-24T21:00Z"},
                        {"type": {"abbreviation": "Race"}, "date": "2026-10-25T20:00Z"}
                    ]
                }]
            }
        ]
        res = self.agg.get_schedule()
        self.assertIn("Circuit of the Americas (Austin, USA)", res)
        self.assertIn("Practice 3 (FP3)", res)
        self.assertIn("Standard 3-Day Weekend (with FP3)", res)
        self.assertIn("None of the remaining 2026 races are Sprint weekends", res)


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

    @patch("src.skills.sports.aggregators.soccer.fetch_json")
    def test_get_schedule_range(self, mock_fetch):
        d1 = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT07:00Z")
        d2 = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT07:00Z")
        mock_fetch.side_effect = [
            {
                "leagues": [{
                    "calendar": [d1, d2]
                }]
            },
            {
                "events": [{
                    "competitions": [{
                        "status": {"type": {"detail": "Sat, Oct 17 - 11:30 AM UTC"}},
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Arsenal"}},
                            {"homeAway": "away", "team": {"displayName": "Chelsea"}}
                        ]
                    }]
                }]
            },
            {
                "events": [{
                    "competitions": [{
                        "status": {"type": {"detail": "Sun, Oct 18 - 3:30 PM UTC"}},
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Liverpool"}},
                            {"homeAway": "away", "team": {"displayName": "Man City"}}
                        ]
                    }]
                }]
            }
        ]
        res = self.agg.get_schedule_range("eng.1", "English Premier League", days_ahead=8)
        self.assertIn("ENGLISH PREMIER LEAGUE WEEKLY FIXTURES", res)
        self.assertIn("Chelsea at Arsenal", res)
        self.assertIn("Man City at Liverpool", res)

    @patch("src.skills.sports.aggregators.soccer.fetch_json")
    def test_get_past_schedule_range(self, mock_fetch):
        past_d = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%dT07:00Z")
        mock_fetch.side_effect = [
            {
                "leagues": [{
                    "calendar": [past_d]
                }]
            },
            {
                "events": [{
                    "competitions": [{
                        "status": {"type": {"detail": "FT"}},
                        "competitors": [
                            {"homeAway": "home", "score": "2", "team": {"displayName": "Arsenal"}},
                            {"homeAway": "away", "score": "1", "team": {"displayName": "Leeds United"}}
                        ],
                        "details": [{
                            "type": {"text": "Goal"},
                            "clock": {"displayValue": "55'"},
                            "team": {"id": "1"},
                            "athletesInvolved": [{"displayName": "Bukayo Saka"}]
                        }]
                    }]
                }]
            }
        ]
        res = self.agg.get_past_schedule_range("eng.1", "English Premier League", days_back=7)
        self.assertIn("ENGLISH PREMIER LEAGUE RECENT MATCH RESULTS", res)
        self.assertIn("Arsenal 2 - 1 Leeds United", res)
        self.assertIn("Bukayo Saka", res)


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

    @patch("src.skills.sports.aggregators.nba.fetch_json")
    def test_get_schedule_range(self, mock_fetch):
        future_d = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT07:00Z")
        mock_fetch.side_effect = [
            {
                "leagues": [{
                    "calendar": [future_d]
                }]
            },
            {
                "events": [{
                    "competitions": [{
                        "status": {"type": {"detail": "Wed, Oct 14 - 7:00 PM EDT"}},
                        "venue": {"fullName": "Madison Square Garden"},
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "New York Knicks"}},
                            {"homeAway": "away", "team": {"displayName": "Boston Celtics"}}
                        ]
                    }]
                }]
            }
        ]
        res = self.agg.get_schedule_range(days_ahead=7)
        self.assertIn("NBA WEEKLY SCHEDULE", res)
        self.assertIn("Boston Celtics at New York Knicks", res)
        self.assertIn("Madison Square Garden", res)

    @patch("src.skills.sports.aggregators.nba.fetch_json")
    def test_get_past_schedule_range(self, mock_fetch):
        past_d = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%dT07:00Z")
        mock_fetch.side_effect = [
            {
                "leagues": [{
                    "calendar": [past_d]
                }]
            },
            {
                "events": [{
                    "competitions": [{
                        "status": {"type": {"detail": "Final"}},
                        "competitors": [
                            {"homeAway": "home", "score": "114", "team": {"displayName": "Toronto Raptors"}},
                            {"homeAway": "away", "score": "106", "team": {"displayName": "LA Clippers"}}
                        ]
                    }]
                }]
            }
        ]
        res = self.agg.get_past_schedule_range(days_back=7)
        self.assertIn("NBA RECENT NBA RESULTS", res)
        self.assertIn("LA Clippers 106 @ Toronto Raptors 114", res)


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

    @patch("src.skills.sports.aggregators.hockey.fetch_json")
    def test_get_schedule_range(self, mock_fetch):
        future_d = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT07:00Z")
        mock_fetch.side_effect = [
            {
                "leagues": [{
                    "calendar": [future_d]
                }]
            },
            {
                "events": [{
                    "competitions": [{
                        "status": {"type": {"detail": "Wed, Oct 14 - 7:00 PM EDT"}},
                        "venue": {"fullName": "Capital One Arena"},
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "Washington Capitals"}},
                            {"homeAway": "away", "team": {"displayName": "Montreal Canadiens"}}
                        ]
                    }]
                }]
            }
        ]
        res = self.agg.get_schedule_range(days_ahead=7)
        self.assertIn("NHL WEEKLY SCHEDULE", res)
        self.assertIn("Montreal Canadiens at Washington Capitals", res)
        self.assertIn("Capital One Arena", res)

    @patch("src.skills.sports.aggregators.hockey.fetch_json")
    def test_get_past_schedule_range(self, mock_fetch):
        past_d = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%dT07:00Z")
        mock_fetch.side_effect = [
            {
                "leagues": [{
                    "calendar": [past_d]
                }]
            },
            {
                "events": [{
                    "competitions": [{
                        "status": {"type": {"detail": "Final"}},
                        "competitors": [
                            {"homeAway": "home", "score": "5", "team": {"displayName": "Boston Bruins"}},
                            {"homeAway": "away", "score": "1", "team": {"displayName": "Philadelphia Flyers"}}
                        ]
                    }]
                }]
            }
        ]
        res = self.agg.get_past_schedule_range(days_back=7)
        self.assertIn("NHL RECENT NHL RESULTS", res)
        self.assertIn("Philadelphia Flyers 1 @ Boston Bruins 5", res)


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
        mock_fetch.return_value = {
            "events": [{
                "name": "Arsenal vs Chelsea",
                "competitions": [{
                    "status": {"type": {"detail": "FT"}},
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": "Arsenal"}, "score": "2"},
                        {"homeAway": "away", "team": {"displayName": "Chelsea"}, "score": "0"}
                    ]
                }]
            }]
        }
        res = skill_sports_info("soccer", "epl matches")
        self.assertIn("ENGLISH PREMIER LEAGUE", res)
        self.assertIn("CURRENT REFERENCE DATE", res)

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
        from src.skills.sports.aggregators.hltv import clear_hltv_cache
        from src.skills.sports.aggregators.liquipedia import clear_liquipedia_cache
        clear_hltv_cache()
        clear_liquipedia_cache()
        mock_esl.return_value = "No active streams"
        mock_liqui.return_value = {"parse": {"text": {"*": "<div>S-Tier</div>"}}}
        mock_hltv.return_value = MagicMock(select=MagicMock(return_value=[]))

        res = skill_sports_info("cs", "whats happening in cs")
        self.assertIn("COUNTER-STRIKE ESPORTS DATA", res)

    @patch("src.skills.sports.aggregators.kashrock.KashrockAggregator.is_configured", return_value=False)
    @patch("src.skills.sports.aggregators.odds.fetch_soup")
    @patch("src.tools.web.search_web")
    def test_cs_odds_with_stake_simulation(self, mock_search, mock_soup, _mock_kr):
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

    @patch("src.skills.sports.aggregators.soccer.fetch_json")
    def test_sports_info_followup_without_sport_keyword(self, mock_fetch):
        cal = {"leagues": [{"calendar": ["2026-10-12T19:00Z"]}]}
        events = {
            "events": [{
                "name": "Coventry City vs Newcastle United",
                "competitions": [{
                    "status": {"type": {"detail": "FT"}},
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": "Coventry City"}, "score": "4"},
                        {"homeAway": "away", "team": {"displayName": "Newcastle United"}, "score": "1"}
                    ]
                }]
            }]
        }
        mock_fetch.side_effect = lambda url, **kw: events if "dates=" in url else cal

        # Calling with only query (sport omitted)
        res = skill_sports_info(query="what were previous matches and outcomes?")
        self.assertIn("ENGLISH PREMIER LEAGUE", res)
        self.assertIn("Coventry City", res)

        # Calling with sentence as first positional argument
        res2 = skill_sports_info("what were previous matches and outcomes?")
        self.assertIn("ENGLISH PREMIER LEAGUE", res2)
        self.assertIn("Coventry City", res2)

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_f1_standings_smart_routing(self, mock_fetch):
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
                                "athlete": {"displayName": "Sergio Perez"},
                                "stats": [{"name": "rank", "displayValue": "23"}, {"name": "championshipPts", "displayValue": "0"}]
                            }
                        ]
                    }
                },
                {
                    "name": "Constructor Standings",
                    "standings": {
                        "entries": [
                            {
                                "team": {"displayName": "Cadillac"},
                                "stats": [{"name": "rank", "displayValue": "11"}, {"name": "points", "displayValue": "0"}]
                            }
                        ]
                    }
                }
            ]
        }
        res1 = skill_sports_info(query="whats current F1 standings drivers and constructors")
        self.assertIn("FORMULA 1 CHAMPIONSHIP STANDINGS", res1)
        self.assertIn("Kimi Antonelli - 340 pts", res1)
        self.assertIn("Sergio Perez - 0 pts", res1)
        self.assertIn("Cadillac - 0 pts", res1)

        res2 = skill_sports_info(query="You are missing constructors and drivers I want them all")
        self.assertIn("FORMULA 1 CHAMPIONSHIP STANDINGS", res2)
        self.assertIn("Cadillac - 0 pts", res2)

    def test_sports_info_rejects_ai_queries(self):
        res = skill_sports_info(query="gemini 4 agron vs opus 5.5")
        self.assertIn("not a sports query", res)
        self.assertIn("search_web", res)

        res_pos = skill_sports_info("gemini 4 agron vs opus 5.5")
        self.assertIn("not a sports query", res_pos)
        self.assertIn("search_web", res_pos)

    def test_sports_info_rejects_unrelated_queries(self):
        res = skill_sports_info(query="how to bake chocolate cookies")
        self.assertIn("not recognized as a sport", res)
        self.assertIn("search_web", res)

    @patch("src.skills.sports.aggregators.hockey.fetch_json")
    @patch("src.skills.sports.aggregators.soccer.fetch_json")
    def test_multi_sport_soccer_and_hockey_routing(self, mock_soccer_fetch, mock_hockey_fetch):
        mock_soccer_fetch.return_value = {
            "events": [{
                "competitions": [{
                    "status": {"type": {"detail": "Sat 10:00 AM", "state": "pre", "completed": False}},
                    "competitors": [
                        {"homeAway": "home", "score": "0", "team": {"displayName": "Arsenal"}},
                        {"homeAway": "away", "score": "0", "team": {"displayName": "Chelsea"}},
                    ],
                }]
            }]
        }
        mock_hockey_fetch.return_value = {
            "events": [{
                "id": "401",
                "competitions": [{
                    "status": {"type": {"detail": "Tonight 7:00 PM", "state": "pre", "completed": False}},
                    "competitors": [
                        {"homeAway": "home", "score": "0", "team": {"displayName": "Toronto Maple Leafs"}},
                        {"homeAway": "away", "score": "0", "team": {"displayName": "Boston Bruins"}},
                    ],
                }]
            }]
        }
        res = skill_sports_info(query="what are the soccer and hockey games today?")
        self.assertIn("Arsenal", res)
        self.assertIn("NHL SCOREBOARD", res)
        self.assertIn("Toronto Maple Leafs", res)

    @patch("src.skills.sports.aggregators.odds.fetch_json")
    @patch("src.skills.sports.aggregators.hockey.fetch_json")
    def test_games_and_odds_combined_routing(self, mock_hockey_fetch, mock_odds_fetch):
        scoreboard_payload = {
            "events": [{
                "id": "401999",
                "competitions": [{
                    "status": {"type": {"detail": "Tonight 7:00 PM EDT", "state": "pre", "completed": False}},
                    "competitors": [
                        {"homeAway": "home", "score": "0", "team": {"displayName": "Edmonton Oilers"}},
                        {"homeAway": "away", "score": "0", "team": {"displayName": "Calgary Flames"}},
                    ],
                    "odds": [{
                        "provider": {"name": "DraftKings"},
                        "moneyline": {
                            "home": {"close": {"odds": "-160"}},
                            "away": {"close": {"odds": "+135"}},
                        },
                    }],
                }]
            }]
        }
        mock_hockey_fetch.return_value = scoreboard_payload
        mock_odds_fetch.return_value = scoreboard_payload

        res = skill_sports_info(query="hockey games today and their odds")
        self.assertIn("NHL SCOREBOARD", res)
        self.assertIn("Calgary Flames", res)
        self.assertIn("Edmonton Oilers", res)
        self.assertIn("[MATCH BETTING ODDS: Edmonton Oilers vs Calgary Flames]", res)
        self.assertIn("Best Odds Available:", res)
        self.assertIn("- **Edmonton Oilers**: 1.62 | Win probability: ~61.7%", res)

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get", return_value=None)
    @patch("src.skills.sports.sports.skill_sports_esports")
    @patch("src.skills.sports.aggregators.soccer.fetch_json")
    def test_multi_sport_cs_and_soccer_routing(self, mock_soccer_fetch, mock_csgo, _mock_espn_get):
        mock_csgo.return_value = "=== COUNTER-STRIKE ESPORTS DATA ===\n• Spirit vs Vitality [18:00 UTC]"
        mock_soccer_fetch.return_value = {
            "events": [{
                "competitions": [{
                    "status": {"type": {"detail": "Sat 10:00 AM", "state": "pre", "completed": False}},
                    "competitors": [
                        {"homeAway": "home", "score": "0", "team": {"displayName": "Arsenal"}},
                        {"homeAway": "away", "score": "0", "team": {"displayName": "Chelsea"}},
                    ],
                }]
            }]
        }
        res = skill_sports_info(query="what are the CS2 matches and Premier League games today?")
        self.assertIn("COUNTER-STRIKE ESPORTS DATA", res)
        self.assertIn("Spirit vs Vitality", res)
        self.assertIn("ENGLISH PREMIER LEAGUE", res)
        self.assertIn("Arsenal", res)

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get", return_value=None)
    @patch("src.skills.sports.sports.skill_sports_esports")
    @patch("src.skills.sports.aggregators.nba.fetch_json")
    def test_multi_sport_cs_rankings_and_nba_schedule_scoping(self, mock_nba_fetch, mock_csgo, _mock_espn_get):
        mock_csgo.return_value = "=== HLTV WORLD RANKINGS ===\n1. Team Spirit"
        mock_nba_fetch.return_value = {
            "events": [{
                "competitions": [{
                    "status": {"type": {"detail": "Tonight 10:00 PM EDT", "state": "pre", "completed": False}},
                    "competitors": [
                        {"homeAway": "home", "score": "0", "team": {"displayName": "Los Angeles Lakers"}},
                        {"homeAway": "away", "score": "0", "team": {"displayName": "Golden State Warriors"}},
                    ],
                }]
            }]
        }
        res = skill_sports_info(query="cs rankings and when do the lakers play")
        mock_csgo.assert_called_once_with(query_type="rankings")
        self.assertIn("HLTV WORLD RANKINGS", res)
        self.assertIn("Los Angeles Lakers", res)

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_f1_all_races_and_ongoing_weekend_utc_boundary(self, mock_fetch):
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        start_iso = (now_utc - timedelta(days=1)).strftime("%Y-%m-%dT14:00Z")
        end_iso = (now_utc + timedelta(hours=3)).strftime("%Y-%m-%dT21:00Z")
        mock_fetch.return_value = {
            "season": {"year": now_utc.year},
            "leagues": [{"calendar": [{"label": "United States Grand Prix", "startDate": start_iso, "endDate": end_iso}]}],
            "events": [{
                "name": "United States Grand Prix",
                "date": start_iso,
                "endDate": end_iso,
                "status": {"type": {"detail": "Sunday Race"}},
                "circuit": {"fullName": "Circuit of the Americas"},
                "competitions": [],
            }],
        }
        # Asking "what are all the F1 races" must route to schedule (not standings) and keep ongoing Sunday race in UPCOMING
        res = skill_sports_info("f1", "what are all the F1 races this season")
        self.assertIn("UPCOMING GRAND PRIX RACES", res)
        self.assertIn("United States Grand Prix", res)

    def test_espn_team_schedule_uses_bot_local_date_for_is_playing_today(self):
        from datetime import timezone
        from src.skills.sports.aggregators.espn_utils import parse_team_schedule
        from src.utils.timezones import get_local_now
        # Current time: Thu Oct 15, 2026 at 7:30 PM MDT (= Fri Oct 16, 01:30 UTC)
        # Game at Fri Oct 16, 02:00 UTC is Thu Oct 15, 8:00 PM MDT -> TODAY in America/Edmonton!
        ref_utc = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
        sched_data = {
            "events": [{
                "date": "2026-10-16T02:00Z",
                "competitions": [{
                    "date": "2026-10-16T02:00Z",
                    "status": {"type": {"state": "pre", "completed": False, "shortDetail": "Thu, Oct 15 at 10:00 PM EDT"}},
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": "Edmonton Oilers"}},
                        {"homeAway": "away", "team": {"displayName": "Calgary Flames"}},
                    ],
                }],
            }]
        }
        with patch.dict("os.environ", {"BOT_TIMEZONE": "America/Edmonton"}, clear=False):
            mdt_now = get_local_now(now=ref_utc)
            with patch("src.utils.timezones.get_local_now", return_value=mdt_now), \
                 patch("src.skills.sports.aggregators.espn_utils.cached_espn_get", return_value=sched_data):
                out = parse_team_schedule("hockey", "nhl", "22", "Edmonton Oilers")
        self.assertIn("🔥 **TODAY —", out)
        self.assertIn("Edmonton Oilers", out)


if __name__ == "__main__":
    unittest.main()





