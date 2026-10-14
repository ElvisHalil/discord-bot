import unittest
from unittest.mock import patch
from src.skills.sports.aggregators.soccer import SoccerAggregator
from src.skills.sports.aggregators.nba import NbaAggregator
from src.skills.sports.aggregators.hockey import HockeyAggregator
from src.skills.sports.aggregators.espn_utils import (
    clear_espn_cache,
    parse_team_roster_and_stats,
    parse_league_injuries,
    parse_match_summary,
    parse_team_schedule,
)
from src.skills.sports.sports import skill_sports_info


class TestSportsEspnUpgrade(unittest.TestCase):
    def setUp(self):
        clear_espn_cache()

    def test_soccer_extract_match_details_cards_and_stats(self):
        comp = {
            "details": [
                {
                    "type": {"text": "Yellow Card"},
                    "clock": {"displayValue": "36'"},
                    "team": {"id": "357"},
                    "athletesInvolved": [{"displayName": "Ao Tanaka"}],
                    "yellowCard": True,
                },
                {
                    "type": {"text": "Goal"},
                    "clock": {"displayValue": "62'"},
                    "team": {"id": "359"},
                    "athletesInvolved": [
                        {"displayName": "Riccardo Calafiori"},
                        {"displayName": "Ben White"},
                    ],
                    "scoringPlay": True,
                },
                {
                    "type": {"text": "Red Card"},
                    "clock": {"displayValue": "88'"},
                    "team": {"id": "357"},
                    "athletesInvolved": [{"displayName": "Ethan Ampadu"}],
                    "redCard": True,
                },
            ]
        }
        home = {
            "team": {"id": "359", "displayName": "Arsenal"},
            "statistics": [
                {"name": "possessionPct", "displayValue": "58.2"},
                {"name": "totalShots", "displayValue": "13"},
                {"name": "shotsOnTarget", "displayValue": "6"},
                {"name": "wonCorners", "displayValue": "3"},
                {"name": "foulsCommitted", "displayValue": "12"},
            ],
        }
        away = {
            "team": {"id": "357", "displayName": "Leeds United"},
            "statistics": [
                {"name": "possessionPct", "displayValue": "41.8"},
                {"name": "totalShots", "displayValue": "9"},
                {"name": "shotsOnTarget", "displayValue": "4"},
                {"name": "wonCorners", "displayValue": "5"},
                {"name": "foulsCommitted", "displayValue": "14"},
            ],
        }

        goals, yc, rc, stats_line = SoccerAggregator._extract_match_details(
            comp, home, away, "Arsenal", "Leeds United"
        )
        self.assertEqual(len(goals), 1)
        self.assertIn("62' Riccardo Calafiori (Arsenal) [ast: Ben White]", goals[0])
        self.assertEqual(yc, ["36' Ao Tanaka (Leeds United)"])
        self.assertEqual(rc, ["88' Ethan Ampadu (Leeds United)"])
        self.assertIn("Possession: 58.2% - 41.8%", stats_line)
        self.assertIn("Shots (On Target): 13 (6) - 9 (4)", stats_line)
        self.assertIn("Corners: 3 - 5", stats_line)
        self.assertIn("Fouls: 12 - 14", stats_line)

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get")
    def test_parse_match_summary_lineups_subs_cards(self, mock_get):
        mock_get.return_value = {
            "header": {
                "competitions": [
                    {
                        "status": {"type": {"detail": "FT"}},
                        "competitors": [
                            {"homeAway": "home", "score": "2", "team": {"displayName": "Arsenal"}},
                            {"homeAway": "away", "score": "1", "team": {"displayName": "Leeds United"}},
                        ],
                    }
                ]
            },
            "gameInfo": {
                "venue": {"fullName": "Emirates Stadium"},
                "attendance": 60315,
                "officials": [{"displayName": "Michael Oliver"}],
            },
            "keyEvents": [
                {
                    "type": {"text": "Yellow Card"},
                    "clock": {"displayValue": "36'"},
                    "text": "Ao Tanaka (Leeds United) is shown the yellow card.",
                },
                {
                    "type": {"text": "Goal"},
                    "clock": {"displayValue": "62'"},
                    "text": "Goal! Riccardo Calafiori (Arsenal) scores.",
                },
                {
                    "type": {"text": "Substitution"},
                    "clock": {"displayValue": "63'"},
                    "text": "Substitution, Arsenal. Jurrien Timber replaces Ben White.",
                },
            ],
            "boxscore": {
                "teams": [
                    {
                        "team": {"shortDisplayName": "Arsenal"},
                        "statistics": [
                            {"name": "possessionPct", "displayValue": "58.2"},
                            {"name": "totalShots", "displayValue": "13"},
                            {"name": "shotsOnTarget", "displayValue": "6"},
                        ],
                    },
                    {
                        "team": {"shortDisplayName": "Leeds"},
                        "statistics": [
                            {"name": "possessionPct", "displayValue": "41.8"},
                            {"name": "totalShots", "displayValue": "9"},
                            {"name": "shotsOnTarget", "displayValue": "4"},
                        ],
                    },
                ]
            },
            "rosters": [
                {
                    "team": {"displayName": "Arsenal"},
                    "formation": "4-2-3-1",
                    "roster": [
                        {
                            "starter": True,
                            "subbedOut": True,
                            "jersey": "4",
                            "athlete": {"displayName": "Ben White"},
                            "position": {"abbreviation": "D"},
                            "stats": [{"abbreviation": "A", "displayValue": "1"}],
                        },
                        {
                            "starter": False,
                            "subbedIn": True,
                            "jersey": "12",
                            "athlete": {"displayName": "Jurrien Timber"},
                            "position": {"abbreviation": "D"},
                            "stats": [],
                        },
                    ],
                }
            ],
        }

        out = parse_match_summary("soccer", "eng.1", "401879268")
        self.assertIn("MATCH SUMMARY: Arsenal 2 - 1 Leeds United (FT)", out)
        self.assertIn("Emirates Stadium", out)
        self.assertIn("🟨 **36'** Ao Tanaka", out)
        self.assertIn("⚽ **62'** Goal! Riccardo Calafiori", out)
        self.assertIn("🔄 **63'** Substitution, Arsenal", out)
        self.assertIn("Possession %:** Arsenal 58.2% — 41.8% Leeds", out)
        self.assertIn("Formation: **4-2-3-1**", out)
        self.assertIn("**Ben White** (D) [1A] 🔻", out)
        self.assertIn("🔺 #12 **Jurrien Timber** (D)", out)

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get")
    def test_parse_team_roster_and_stats(self, mock_get):
        mock_get.return_value = {
            "athletes": [
                {
                    "displayName": "Bukayo Saka",
                    "jersey": "7",
                    "age": 25,
                    "position": {"displayName": "Forward", "abbreviation": "F"},
                    "injuries": [],
                    "statistics": {
                        "splits": {
                            "categories": [
                                {
                                    "name": "general",
                                    "stats": [
                                        {"name": "appearances", "displayValue": "6"},
                                        {"name": "yellowCards", "displayValue": "1"},
                                        {"name": "redCards", "displayValue": "0"},
                                    ],
                                },
                                {
                                    "name": "offensive",
                                    "stats": [
                                        {"name": "totalGoals", "displayValue": "3"},
                                        {"name": "goalAssists", "displayValue": "2"},
                                        {"name": "totalShots", "displayValue": "17"},
                                        {"name": "shotsOnTarget", "displayValue": "10"},
                                    ],
                                },
                            ]
                        }
                    },
                },
                {
                    "displayName": "Gabriel Jesus",
                    "jersey": "9",
                    "age": 29,
                    "position": {"displayName": "Forward", "abbreviation": "F"},
                    "injuries": [{"status": "Out", "shortComment": "Knee injury"}],
                    "statistics": {"splits": {"categories": []}},
                },
            ]
        }

        out = parse_team_roster_and_stats("soccer", "eng.1", "359", "Arsenal", player_filter="saka")
        self.assertIn("ARSENAL SQUAD ROSTER & PLAYER SEASON STATS", out)
        self.assertIn("Bukayo Saka", out)
        self.assertIn("**3G, 2A** in 6 apps", out)
        self.assertIn("🚑 **Gabriel Jesus** (F): Out - Knee injury", out)

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get")
    def test_parse_league_injuries_nba(self, mock_get):
        def side_effect(url, params=None, ttl=300):
            if url.endswith("/teams"):
                return {
                    "sports": [
                        {
                            "leagues": [
                                {
                                    "teams": [
                                        {
                                            "team": {
                                                "id": "13",
                                                "displayName": "Los Angeles Lakers",
                                                "shortDisplayName": "Lakers",
                                                "name": "Lakers",
                                                "location": "Los Angeles",
                                                "abbreviation": "LAL",
                                            }
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            return {
                "injuries": [
                    {
                        "displayName": "Los Angeles Lakers",
                        "injuries": [
                            {
                                "status": "Day-To-Day",
                                "shortComment": "Questionable for Friday.",
                                "athlete": {"displayName": "Collin Sexton", "position": {"abbreviation": "G"}},
                                "details": {"type": "Lower Leg", "returnDate": "2026-10-16"},
                            }
                        ],
                    }
                ]
            }

        mock_get.side_effect = side_effect
        out = parse_league_injuries("basketball", "nba", "NBA", "lakers injuries")
        self.assertIn("NBA INJURY REPORT", out)
        self.assertIn("Los Angeles Lakers", out)
        self.assertIn("**Collin Sexton** (G) — **Day-To-Day** (Lower Leg)", out)

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get")
    def test_parse_team_schedule_chronological_and_fixtures(self, mock_get):
        def side_effect(url, params=None, ttl=300):
            if params and params.get("fixture") == "true":
                return {
                    "events": [
                        {
                            "id": "3",
                            "date": "2026-10-18T15:30Z",
                            "competitions": [
                                {
                                    "status": {"type": {"state": "pre", "completed": False}},
                                    "competitors": [
                                        {"homeAway": "home", "team": {"displayName": "Nottingham Forest"}, "score": {"displayValue": "0"}},
                                        {"homeAway": "away", "team": {"displayName": "Arsenal"}, "score": {"displayValue": "0"}},
                                    ],
                                }
                            ],
                        }
                    ]
                }
            return {
                "events": [
                    {
                        "id": "2",
                        "date": "2026-10-10T11:30Z",
                        "competitions": [
                            {
                                "status": {"type": {"state": "post", "completed": True, "shortDetail": "FT"}},
                                "competitors": [
                                    {"homeAway": "home", "team": {"displayName": "Arsenal"}, "score": {"displayValue": "2"}},
                                    {"homeAway": "away", "team": {"displayName": "Leeds United"}, "score": {"displayValue": "1"}},
                                ],
                            }
                        ],
                    },
                    {
                        "id": "1",
                        "date": "2026-08-21T19:00Z",
                        "competitions": [
                            {
                                "status": {"type": {"state": "post", "completed": True, "shortDetail": "FT"}},
                                "competitors": [
                                    {"homeAway": "home", "team": {"displayName": "Arsenal"}, "score": {"displayValue": "3"}},
                                    {"homeAway": "away", "team": {"displayName": "Coventry City"}, "score": {"displayValue": "0"}},
                                ],
                            }
                        ],
                    },
                ]
            }

        mock_get.side_effect = side_effect
        out = parse_team_schedule("soccer", "eng.1", "359", "Arsenal")
        self.assertIn("ARSENAL TEAM SCHEDULE & RESULTS", out)
        # Verify Coventry (Aug 21) appears before Leeds (Oct 10)
        self.assertLess(out.index("Coventry City"), out.index("Leeds United"))
        self.assertIn("Upcoming Fixtures", out)
        self.assertIn("**Arsenal** @ **Nottingham Forest**", out)

    def test_nba_and_nhl_leader_extraction(self):
        nba_comp = {
            "leaders": [
                {"name": "points", "abbreviation": "Pts", "leaders": [{"athlete": {"shortName": "P. Banchero"}, "displayValue": "24"}]},
                {"name": "rebounds", "abbreviation": "Reb", "leaders": [{"athlete": {"shortName": "P. Banchero"}, "displayValue": "8"}]},
                {"name": "assists", "abbreviation": "Ast", "leaders": [{"athlete": {"shortName": "F. Wagner"}, "displayValue": "8"}]},
            ]
        }
        self.assertEqual(
            NbaAggregator._extract_team_leaders(nba_comp),
            "P. Banchero (24 Pts), P. Banchero (8 Reb), F. Wagner (8 Ast)",
        )

        nhl_comp = {
            "leaders": [
                {"name": "goals", "abbreviation": "G", "leaders": [{"athlete": {"shortName": "A. DeBrincat"}, "displayValue": "2"}]},
                {"name": "assists", "abbreviation": "A", "leaders": [{"athlete": {"shortName": "A. DeBrincat"}, "displayValue": "1"}]},
                {"name": "points", "abbreviation": "PTS", "leaders": [{"athlete": {"shortName": "A. DeBrincat"}, "displayValue": "3"}]},
            ]
        }
        self.assertEqual(
            HockeyAggregator._extract_team_leaders(nhl_comp),
            "A. DeBrincat (2 G), A. DeBrincat (1 A), A. DeBrincat (3 PTS)",
        )

    @patch("src.skills.sports.sports._soccer_aggregator.get_info")
    def test_sports_router_preserves_sport_and_query_context(self, mock_soccer_info):
        mock_soccer_info.return_value = "MOCK_UCL_STANDINGS"
        res = skill_sports_info(sport="ucl", query="standings")
        mock_soccer_info.assert_called_once_with("ucl standings")
        self.assertIn("MOCK_UCL_STANDINGS", res)


if __name__ == "__main__":
    unittest.main()
