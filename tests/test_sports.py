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
        from datetime import timezone
        from src.skills.sports.aggregators.f1 import get_local_now as _real_get_local_now

        self.agg = F1Aggregator()
        fixed_utc_now = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
        self._local_now_patcher = patch(
            'src.skills.sports.aggregators.f1.get_local_now',
            side_effect=lambda now=None: _real_get_local_now(now=fixed_utc_now if now is None else now),
        )
        self._local_now_patcher.start()
        self.addCleanup(self._local_now_patcher.stop)

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

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_active_race_weekend_not_marked_final_prematurely(self, mock_fetch):
        """During an active race weekend (FP1-FP3 completed, Qualifying/Race upcoming),
        get_schedule() must NOT report the Grand Prix as (Final) even if events[0].status.detail is 'Final'."""
        us_gp_event = {
            "id": "401858359",
            "name": "MSC Cruises United States Grand Prix",
            "date": "2026-10-23T17:30Z",
            "status": {"type": {"detail": "Final"}},
            "circuit": {"fullName": "Circuit of the Americas", "address": {"city": "Austin", "country": "USA"}},
            "competitions": [
                {
                    "id": "c_fp1",
                    "type": {"abbreviation": "FP1"},
                    "date": "2026-10-23T17:30Z",
                    "status": {"type": {"state": "post", "completed": True, "detail": "Final"}},
                    "competitors": [
                        {"order": 1, "athlete": {"displayName": "Charles Leclerc"}},
                        {"order": 2, "athlete": {"displayName": "Alexander Albon"}},
                        {"order": 3, "athlete": {"displayName": "Liam Lawson"}},
                    ],
                },
                {
                    "id": "c_fp3",
                    "type": {"abbreviation": "FP3"},
                    "date": "2026-10-24T17:30Z",
                    "status": {"type": {"state": "post", "completed": True, "detail": "Final"}},
                    "competitors": [
                        {"order": 1, "athlete": {"displayName": "Lewis Hamilton"}},
                        {"order": 2, "athlete": {"displayName": "Max Verstappen"}},
                        {"order": 3, "athlete": {"displayName": "Charles Leclerc"}},
                    ],
                },
                {
                    "id": "c_qual",
                    "type": {"abbreviation": "Qual"},
                    "date": "2026-10-24T21:00Z",
                    "status": {"type": {"state": "pre", "completed": False, "detail": "Sat, Oct 24 at 5:00 PM EDT"}},
                    "competitors": [],
                },
                {
                    "id": "c_race",
                    "type": {"abbreviation": "Race"},
                    "date": "2026-10-25T20:00Z",
                    "status": {"type": {"state": "pre", "completed": False, "detail": "Sun, Oct 25 at 4:00 PM EDT"}},
                    "competitors": [],
                },
            ],
        }
        mock_fetch.side_effect = [
            {
                "season": {"year": 2026},
                "leagues": [{
                    "calendar": [{
                        "label": "United States Grand Prix",
                        "startDate": "2026-10-23T17:30Z",
                        "endDate": "2026-10-25T20:00Z",
                    }]
                }],
                "events": [us_gp_event],
            },
            {"events": [us_gp_event]},
        ]
        res = self.agg.get_schedule()
        self.assertNotIn("MOST RECENT GRAND PRIX: MSC Cruises United States Grand Prix (Final)", res)
        self.assertIn("CURRENT RACE WEEKEND STATUS: MSC Cruises United States Grand Prix (Weekend In Progress", res)
        self.assertIn("Latest Completed Session (Practice 3 (FP3)): P1: Lewis Hamilton, P2: Max Verstappen, P3: Charles Leclerc", res)
        self.assertIn("Next Scheduled Session: Qualifying", res)
        self.assertIn("[✅ Completed — P1: Lewis Hamilton, P2: Max Verstappen, P3: Charles Leclerc]", res)
        self.assertIn("Qualifying:", res)
        self.assertIn("[🕒 Upcoming]", res)

    @patch.dict("os.environ", {"BOT_TIMEZONE": "America/Edmonton"})
    def test_night_session_utc_midnight_rollover_local_time_alignment(self):
        """Night sessions crossing midnight UTC (e.g. Las Vegas GP at 04:00 UTC on Saturday)
        must display the local day-of-week and time first (Friday 9:00 PM MST)."""
        from src.skills.sports.aggregators.f1 import _format_f1_session_time

        vegas_qual_str = _format_f1_session_time("2026-11-21T04:00Z")
        self.assertIn("Friday, Nov 20 at 9:00 PM MST", vegas_qual_str)
        self.assertIn("Sat Nov 21, 04:00 UTC", vegas_qual_str)

        cota_qual_str = _format_f1_session_time("2026-10-24T21:00Z")
        self.assertEqual(cota_qual_str, "Saturday, Oct 24 at 3:00 PM MDT (21:00 UTC)")

    @patch.dict("os.environ", {"BOT_TIMEZONE": "America/Edmonton"})
    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_schedule_and_tracker_session_alignment(self, mock_fetch):
        """F1Aggregator.get_schedule() and F1Aggregator.get_session_for_tracker() must produce
        identical session labels and local/UTC timestamps for Qualifying and Race."""
        us_gp_event = {
            "id": "401858359",
            "name": "MSC Cruises United States Grand Prix",
            "circuit": {"fullName": "Circuit of the Americas", "address": {"city": "Austin", "country": "USA"}},
            "competitions": [
                {
                    "id": "401858363",
                    "type": {"abbreviation": "Qual"},
                    "date": "2026-10-24T21:00Z",
                    "status": {"type": {"state": "pre", "completed": False}},
                    "competitors": [],
                },
                {
                    "id": "401858364",
                    "type": {"abbreviation": "Race"},
                    "date": "2026-10-25T20:00Z",
                    "status": {"type": {"state": "pre", "completed": False}},
                    "competitors": [],
                },
            ],
        }
        mock_fetch.side_effect = [
            {
                "season": {"year": 2026},
                "leagues": [{
                    "calendar": [{
                        "label": "United States Grand Prix",
                        "startDate": "2026-10-23T17:30Z",
                        "endDate": "2026-10-25T20:00Z",
                    }]
                }],
                "events": [us_gp_event],
            },
            {"events": [us_gp_event]},
            {"events": [us_gp_event]},
            {"events": [us_gp_event]},
        ]
        sched_text = self.agg.get_schedule()
        qual_tracker = self.agg.get_session_for_tracker(query="track F1 qualifying")
        race_tracker = self.agg.get_session_for_tracker(query="track F1 race tomorrow")

        self.assertIsNotNone(qual_tracker)
        self.assertIsNotNone(race_tracker)
        self.assertEqual(qual_tracker["away_team"], "Qualifying")
        self.assertEqual(qual_tracker["snapshot"].status_detail, "Saturday, Oct 24 at 3:00 PM MDT (21:00 UTC)")
        self.assertIn(qual_tracker["snapshot"].status_detail, sched_text)

        self.assertEqual(race_tracker["away_team"], "Grand Prix (Race)")
        self.assertEqual(race_tracker["snapshot"].status_detail, "Sunday, Oct 25 at 2:00 PM MDT (20:00 UTC)")
        self.assertIn(race_tracker["snapshot"].status_detail, sched_text)


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

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_f1_schedule_and_time_enrichment_pipeline_no_double_annotation_across_dst_and_midnight_utc(self, mock_fetch):
        """
        Verify that F1Aggregator.get_schedule() + enrich_tool_output_with_time_context():
        1. Converts Austin (MDT), Mexico City (MDT -> MST DST transition on Nov 1), and Las Vegas (midnight UTC rollover)
           to exact local Mountain Time + UTC.
        2. Computes local weekend date ranges ('2026-11-19 to 2026-11-21' for Las Vegas GP) so '• Dates:' never
           contradicts the Thursday-Saturday local session days.
        3. Never double-annotates pre-formatted (Local + UTC) session lines across either MDT or MST when passed
           through enrich_tool_output_with_time_context().
        """
        from datetime import timezone
        from src.utils.timezones import enrich_tool_output_with_time_context, get_local_now

        ref_utc = datetime(2026, 10, 24, 20, 15, tzinfo=timezone.utc)  # Sat Oct 24, 2:15 PM MDT

        austin_comps = [
            {"id": "1", "type": {"abbreviation": "FP1"}, "date": "2026-10-23T17:30Z", "status": {"type": {"state": "post", "completed": True}}, "competitors": [{"order": 1, "athlete": {"displayName": "Charles Leclerc"}}]},
            {"id": "2", "type": {"abbreviation": "Qual"}, "date": "2026-10-24T21:00Z", "status": {"type": {"state": "pre", "completed": False}}, "competitors": []},
            {"id": "3", "type": {"abbreviation": "Race"}, "date": "2026-10-25T20:00Z", "status": {"type": {"state": "pre", "completed": False}}, "competitors": []},
        ]
        mexico_comps = [
            {"id": "4", "type": {"abbreviation": "Qual"}, "date": "2026-10-31T21:00Z", "status": {"type": {"state": "pre", "completed": False}}, "competitors": []},
            {"id": "5", "type": {"abbreviation": "Race"}, "date": "2026-11-01T20:00Z", "status": {"type": {"state": "pre", "completed": False}}, "competitors": []},
        ]
        vegas_comps = [
            {"id": "6", "type": {"abbreviation": "FP1"}, "date": "2026-11-20T00:30Z", "status": {"type": {"state": "pre", "completed": False}}, "competitors": []},
            {"id": "7", "type": {"abbreviation": "FP3"}, "date": "2026-11-21T00:30Z", "status": {"type": {"state": "pre", "completed": False}}, "competitors": []},
            {"id": "8", "type": {"abbreviation": "Qual"}, "date": "2026-11-21T04:00Z", "status": {"type": {"state": "pre", "completed": False}}, "competitors": []},
            {"id": "9", "type": {"abbreviation": "Race"}, "date": "2026-11-22T04:00Z", "status": {"type": {"state": "pre", "completed": False}}, "competitors": []},
        ]

        def side_effect(url, timeout=8):
            if "dates=20261023" in url:
                return {"events": [{"name": "United States Grand Prix", "circuit": {"fullName": "Circuit of the Americas", "address": {"city": "Austin", "country": "USA"}}, "competitions": austin_comps}]}
            if "dates=20261030" in url:
                return {"events": [{"name": "Mexico City Grand Prix", "circuit": {"fullName": "Autodromo Hermanos Rodriguez", "address": {"city": "Mexico City", "country": "Mexico"}}, "competitions": mexico_comps}]}
            if "dates=20261120" in url:
                return {"events": [{"name": "Las Vegas Grand Prix", "circuit": {"fullName": "Las Vegas Street Circuit", "address": {"city": "Nevada", "country": "USA"}}, "competitions": vegas_comps}]}
            return {
                "season": {"year": 2026},
                "leagues": [{
                    "calendar": [
                        {"label": "United States Grand Prix", "startDate": "2026-10-23T20:30Z", "endDate": "2026-10-25T20:00Z"},
                        {"label": "Mexico City Grand Prix", "startDate": "2026-10-30T21:30Z", "endDate": "2026-11-01T20:00Z"},
                        {"label": "Las Vegas Grand Prix", "startDate": "2026-11-20T03:30Z", "endDate": "2026-11-22T07:00Z"},
                    ]
                }],
                "events": [{"name": "United States Grand Prix", "status": {"type": {"detail": "Final"}}, "competitions": austin_comps}],
            }

        mock_fetch.side_effect = side_effect
        with patch.dict("os.environ", {"BOT_TIMEZONE": "America/Edmonton"}, clear=False):
            real_get_local_now = get_local_now
            with patch(
                "src.skills.sports.aggregators.f1.get_local_now",
                side_effect=lambda now=None, tz_override=None: real_get_local_now(now=now or ref_utc, tz_override=tz_override),
            ):
                raw_sched = skill_sports_info("f1", "schedule")
            enriched = enrich_tool_output_with_time_context(raw_sched, now=ref_utc, tz_override="America/Edmonton")

        # 1. Reference clock header is prepended
        self.assertTrue(enriched.startswith("🕒 Current Time:") or enriched.startswith("[🕒 Current Time:"))
        # 2. Austin GP in MDT (UTC-6)
        self.assertIn("• Dates: 2026-10-23 to 2026-10-25", enriched)
        self.assertIn("• Qualifying: Saturday, Oct 24 at 3:00 PM MDT (21:00 UTC) [🕒 Upcoming]", enriched)
        self.assertIn("• Grand Prix (Race): Sunday, Oct 25 at 2:00 PM MDT (20:00 UTC) [🕒 Upcoming]", enriched)
        # 3. Mexico City GP crosses DST boundary: Saturday Oct 31 is MDT (UTC-6), Sunday Nov 01 is MST (UTC-7)
        self.assertIn("• Qualifying: Saturday, Oct 31 at 3:00 PM MDT (21:00 UTC) [🕒 Upcoming]", enriched)
        self.assertIn("• Grand Prix (Race): Sunday, Nov 01 at 1:00 PM MST (20:00 UTC) [🕒 Upcoming]", enriched)
        # 4. Las Vegas GP crosses midnight UTC: local dates are Thu Nov 19 to Sat Nov 21, with explicit UTC day labels
        self.assertIn("• Dates: 2026-11-19 to 2026-11-21", enriched)
        self.assertIn("• Practice 1 (FP1): Thursday, Nov 19 at 5:30 PM MST (Fri Nov 20, 00:30 UTC) [🕒 Upcoming]", enriched)
        self.assertIn("• Qualifying: Friday, Nov 20 at 9:00 PM MST (Sat Nov 21, 04:00 UTC) [🕒 Upcoming]", enriched)
        self.assertIn("• Grand Prix (Race): Saturday, Nov 21 at 9:00 PM MST (Sun Nov 22, 04:00 UTC) [🕒 Upcoming]", enriched)
        # 5. Zero double-annotation on any session line (no '/ 21:00 CET' or '/ 23:00 CEST' injected into session lines)
        for line in enriched.splitlines():
            if line.strip().startswith("• "):
                self.assertNotIn("CET", line)
                self.assertNotIn("CEST", line)

    @patch("src.skills.sports.aggregators.f1.fetch_json")
    def test_f1_schedule_and_tracker_alignment_for_sprint_weekend_and_evening_utc_rollover(self, mock_fetch):
        """
        Verify that on a Sprint weekend queried late in the evening local time (when UTC has already rolled over
        to the next calendar day), F1Aggregator.get_schedule() and F1Aggregator.get_session_for_tracker() produce
        identical session start times and labels for Sprint Qualifying, Sprint, Qualifying, and Race.
        """
        from datetime import timezone
        from src.skills.sports.aggregators.f1 import F1Aggregator
        from src.utils.timezones import get_local_now

        # Current time: Sunday Nov 8, 2026 at 8:30 PM MST (= Monday Nov 9, 03:30 UTC)
        ref_utc = datetime(2026, 11, 9, 3, 30, tzinfo=timezone.utc)
        sprint_comps = [
            {"id": "101", "type": {"abbreviation": "FP1"}, "date": "2026-11-06T15:30Z", "status": {"type": {"state": "post", "completed": True}}, "competitors": []},
            {"id": "102", "type": {"abbreviation": "SQ"}, "date": "2026-11-06T19:30Z", "status": {"type": {"state": "post", "completed": True}}, "competitors": []},
            {"id": "103", "type": {"abbreviation": "Sprint"}, "date": "2026-11-07T15:00Z", "status": {"type": {"state": "post", "completed": True}}, "competitors": []},
            {"id": "104", "type": {"abbreviation": "Qual"}, "date": "2026-11-07T19:00Z", "status": {"type": {"state": "post", "completed": True}}, "competitors": []},
            {"id": "105", "type": {"abbreviation": "Race"}, "date": "2026-11-08T17:00Z", "status": {"type": {"state": "pre", "completed": False}}, "competitors": []},
        ]
        sb_payload = {
            "season": {"year": 2026},
            "leagues": [{
                "calendar": [
                    {"label": "São Paulo Grand Prix", "startDate": "2026-11-06T15:30Z", "endDate": "2026-11-08T21:00Z"}
                ]
            }],
            "events": [{
                "id": "401858400",
                "name": "São Paulo Grand Prix",
                "circuit": {"fullName": "Autodromo Jose Carlos Pace", "address": {"city": "São Paulo", "country": "Brazil"}},
                "status": {"type": {"detail": "Weekend"}},
                "competitions": sprint_comps,
            }],
        }
        mock_fetch.return_value = sb_payload

        agg = F1Aggregator()
        with patch.dict("os.environ", {"BOT_TIMEZONE": "America/Edmonton"}, clear=False):
            real_get_local_now = get_local_now
            with patch(
                "src.skills.sports.aggregators.f1.get_local_now",
                side_effect=lambda now=None, tz_override=None: real_get_local_now(now=now or ref_utc, tz_override=tz_override),
            ):
                sched_out = agg.get_schedule()
                tracker_sq = agg.get_session_for_tracker(query="track sprint qualifying")
                tracker_sprint = agg.get_session_for_tracker(query="track f1 sprint")
                tracker_quali = agg.get_session_for_tracker(query="track f1 qualifying")
                tracker_race = agg.get_session_for_tracker(query="track f1 race")

        # Even though UTC is already Monday Nov 9 (03:30 UTC), local date is still Sunday Nov 8 MST, so São Paulo GP remains in UPCOMING!
        self.assertIn("1. São Paulo Grand Prix", sched_out)
        self.assertIn("• Weekend Format: Sprint Format", sched_out)
        self.assertNotIn("None of the remaining 2026 races are Sprint weekends", sched_out)

        # Verify schedule and tracker start times match identically
        self.assertEqual(tracker_sq["away_team"], "Sprint Qualifying (SQ)")
        self.assertEqual(tracker_sprint["away_team"], "Sprint")
        self.assertEqual(tracker_quali["away_team"], "Qualifying")
        self.assertEqual(tracker_quali["event_id"], "401858400:104")
        self.assertEqual(tracker_race["away_team"], "Grand Prix (Race)")
        self.assertEqual(tracker_race["snapshot"].status_detail, "Sunday, Nov 08 at 10:00 AM MST (17:00 UTC)")
        self.assertIn("• Grand Prix (Race): Sunday, Nov 08 at 10:00 AM MST (17:00 UTC)", sched_out)


if __name__ == "__main__":
    unittest.main()





