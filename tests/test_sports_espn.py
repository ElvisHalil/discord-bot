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

    def test_enrich_sports_tool_args_bayern_and_pronoun_followup(self):
        from src.orchestrator import normalize_tool_args, enrich_sports_tool_args

        # Turn 1: User asks "When does Bayern Munich play next?", LLM passes only sport='soccer', query='schedule'
        history_t1 = [{"role": "user", "content": "User Jubkill says: When does Bayern Munich play next?"}]
        raw_t1 = normalize_tool_args("skill_sports_info", skill_sports_info, {"sport": "soccer", "query": "schedule"})
        enriched_t1 = enrich_sports_tool_args(raw_t1, history_t1)
        self.assertIn("bayern munich", enriched_t1["query"].lower())

        # Turn 2: User asks "what their roster?", LLM passes only sport='soccer', query='roster'
        history_t2 = history_t1 + [
            {"role": "assistant", "content": "Bayern Munich plays RB Leipzig next."},
            {"role": "user", "content": "User Jubkill says: what their roster?"},
        ]
        raw_t2 = normalize_tool_args("skill_sports_info", skill_sports_info, {"sport": "soccer", "query": "roster"})
        enriched_t2 = enrich_sports_tool_args(raw_t2, history_t2)
        self.assertIn("bayern munich", enriched_t2["query"].lower())
        # Old intent word 'play'/'next' from Turn 1 is stripped so 'roster' wins cleanly
        self.assertNotIn("play", enriched_t2["query"].lower())

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get")
    def test_espn_search_auto_discover_and_athlete_overview(self, mock_get):
        from src.skills.sports.aggregators.espn_utils import espn_search_auto_discover, parse_athlete_overview

        def side_effect(url, params=None, ttl=300):
            if "search/v2" in url:
                return {
                    "results": [
                        {
                            "type": "player",
                            "contents": [
                                {
                                    "displayName": "Shohei Ohtani",
                                    "subtitle": "Los Angeles Dodgers",
                                    "sport": "baseball",
                                    "defaultLeagueSlug": "mlb",
                                    "uid": "s:1~l:10~a:39832",
                                }
                            ],
                        }
                    ]
                }
            return {
                "statistics": {
                    "displayName": "2026 Batting",
                    "labels": ["GP", "HR", "RBI"],
                    "splits": [{"displayName": "Regular Season", "stats": ["135", "30", "81"]}],
                },
                "rotowire": {"headline": "Ohtani is batting cleanup Wednesday."},
            }

        mock_get.side_effect = side_effect
        disc = espn_search_auto_discover("Shohei Ohtani stats")
        self.assertIsNotNone(disc)
        self.assertEqual(disc["sport"], "baseball")
        self.assertEqual(disc["league"], "mlb")
        self.assertEqual(disc["athlete_id"], "39832")

        overview = parse_athlete_overview("baseball", "mlb", "39832", "Shohei Ohtani", "Los Angeles Dodgers")
        self.assertIn("SHOHEI OHTANI (Los Angeles Dodgers)", overview)
        self.assertIn("**HR:** 30", overview)
        self.assertIn("Ohtani is batting cleanup Wednesday.", overview)

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get")
    def test_madrid_team_priority_and_all_competitions_schedule(self, mock_get):
        from src.skills.sports.aggregators.espn_utils import espn_search_auto_discover

        # 1. Verify 'Madrid' prioritizes team group ('Real Madrid') over partial surname player matches ('Sal Madrid')
        mock_get.return_value = {
            "results": [
                {
                    "type": "player",
                    "contents": [
                        {
                            "displayName": "Sal Madrid",
                            "subtitle": "Chicago Cubs",
                            "sport": "baseball",
                            "defaultLeagueSlug": "mlb",
                            "uid": "s:1~l:10~a:24519",
                        }
                    ],
                },
                {
                    "type": "team",
                    "contents": [
                        {
                            "displayName": "Real Madrid",
                            "sport": "soccer",
                            "defaultLeagueSlug": "esp.1",
                            "uid": "s:600~t:86",
                        }
                    ],
                },
            ]
        }
        disc = espn_search_auto_discover("next Madrid game")
        self.assertIsNotNone(disc)
        self.assertEqual(disc["entity_type"], "team")
        self.assertEqual(disc["display_name"], "Real Madrid")
        self.assertEqual(disc["sport"], "soccer")
        self.assertEqual(disc["team_id"], "86")

        # 2. Verify parse_team_schedule merges /soccer/all/teams/{id}/schedule?fixture=true (UEFA Champions League + La Liga)
        def sched_side_effect(url, params=None, ttl=120):
            if "/soccer/all/teams/86/schedule" in url and params and params.get("fixture") == "true":
                return {
                    "events": [
                        {
                            "id": "ucl_today",
                            "date": "2026-10-14T19:00Z",
                            "league": {"name": "UEFA Champions League", "slug": "uefa.champions"},
                            "competitions": [
                                {
                                    "status": {"type": {"state": "pre", "completed": False}},
                                    "competitors": [
                                        {"homeAway": "home", "team": {"displayName": "AS Roma"}, "score": {"displayValue": "0"}},
                                        {"homeAway": "away", "team": {"displayName": "Real Madrid"}, "score": {"displayValue": "0"}},
                                    ],
                                }
                            ],
                        },
                        {
                            "id": "laliga_sun",
                            "date": "2026-10-18T19:00Z",
                            "league": {"name": "Spanish LALIGA", "slug": "esp.1"},
                            "competitions": [
                                {
                                    "status": {"type": {"state": "pre", "completed": False}},
                                    "competitors": [
                                        {"homeAway": "home", "team": {"displayName": "Real Madrid"}, "score": {"displayValue": "0"}},
                                        {"homeAway": "away", "team": {"displayName": "Sevilla"}, "score": {"displayValue": "0"}},
                                    ],
                                }
                            ],
                        },
                    ]
                }
            return {"events": []}

        mock_get.side_effect = sched_side_effect
        sched_out = parse_team_schedule("soccer", "esp.1", "86", "Real Madrid")
        self.assertIn("REAL MADRID TEAM SCHEDULE & RESULTS (ALL COMPETITIONS)", sched_out)
        self.assertIn("**Real Madrid** @ **AS Roma** [UEFA Champions League]", sched_out)
        self.assertIn("**Sevilla** @ **Real Madrid** [Spanish LALIGA]", sched_out)
        self.assertLess(sched_out.index("AS Roma"), sched_out.index("Sevilla"))

    @patch("src.skills.sports.aggregators.odds.fetch_json")
    @patch("src.skills.sports.aggregators.espn_utils.espn_search_auto_discover")
    def test_madrid_live_odds_espn_and_odds_api(self, mock_disc, mock_fetch_json):
        from src.skills.sports.aggregators.odds import OddsAggregator
        import os

        mock_disc.return_value = {
            "entity_type": "team",
            "display_name": "Real Madrid",
            "sport": "soccer",
            "league": "esp.1",
            "team_id": "86",
        }

        def fetch_side_effect(url, timeout=8):
            if "/soccer/all/teams/86/schedule" in url:
                return {
                    "events": [
                        {
                            "id": "401915414",
                            "date": "2026-10-14T19:00Z",
                            "league": {"name": "UEFA Champions League", "slug": "uefa.champions"},
                            "competitions": [
                                {
                                    "status": {"type": {"state": "in", "detail": "14'"}},
                                    "competitors": [
                                        {"homeAway": "home", "team": {"displayName": "AS Roma"}, "score": {"displayValue": "0"}},
                                        {"homeAway": "away", "team": {"displayName": "Real Madrid"}, "score": {"displayValue": "0"}},
                                    ],
                                }
                            ],
                        }
                    ]
                }
            if "/events/401915414/competitions/401915414/odds" in url:
                return {
                    "items": [
                        {
                            "provider": {"name": "DraftKings - Live Odds"},
                            "details": "RMA +100",
                            "overUnder": 2.5,
                            "overOdds": -157.0,
                            "underOdds": 126.0,
                            "spread": 0.5,
                            "homeTeamOdds": {
                                "moneyLine": 240,
                                "current": {"pointSpread": {"american": "+0.5"}, "moneyLine": {"decimal": 3.4, "american": "+240"}},
                            },
                            "awayTeamOdds": {
                                "moneyLine": 100,
                                "current": {"pointSpread": {"american": "-0.5"}, "moneyLine": {"decimal": 2.0, "american": "+100"}},
                            },
                            "drawOdds": {"moneyLine": 275.0},
                        }
                    ]
                }
            if "api.the-odds-api.com/v4/sports/soccer_uefa_champs_league/odds" in url:
                return [
                    {
                        "sport_title": "UEFA Champions League",
                        "commence_time": "2026-10-14T19:00:00Z",
                        "home_team": "AS Roma",
                        "away_team": "Real Madrid",
                        "bookmakers": [
                            {
                                "title": "FanDuel",
                                "markets": [
                                    {
                                        "key": "h2h",
                                        "outcomes": [
                                            {"name": "AS Roma", "price": 3.4},
                                            {"name": "Real Madrid", "price": 2.05},
                                            {"name": "Draw", "price": 3.6},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            return []

        mock_fetch_json.side_effect = fetch_side_effect
        agg = OddsAggregator()
        with patch.dict(os.environ, {"THE_ODDS_API_KEY": "test_key"}):
            res = agg.get_odds("if I bet $100 on madrid game right now", sport="")

        self.assertIn("[LIVE & PRE-MATCH BETTING ODDS: AS Roma vs Real Madrid]", res)
        self.assertIn("DraftKings - Live Odds", res)
        self.assertIn("Bet Payout Simulation ($100.00 Stake)", res)
        self.assertIn("[SPORTSBOOK CONSENSUS ODDS: AS Roma vs Real Madrid]", res)
        self.assertIn("FanDuel", res)

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get")
    def test_status_does_not_trigger_roster_and_bundesliga_does_not_trigger_cs(self, mock_get):
        def side_effect(url, params=None, ttl=120):
            if "/teams" in url and "/schedule" not in url and "/roster" not in url:
                return {
                    "sports": [{
                        "leagues": [{
                            "teams": [{"team": {"id": "86", "displayName": "Real Madrid", "shortDisplayName": "Real Madrid", "abbreviation": "RMA"}}]
                        }]
                    }]
                }
            if "/schedule" in url:
                return {
                    "events": [{
                        "id": "401915414",
                        "date": "2026-10-14T19:00Z",
                        "league": {"name": "UEFA Champions League", "slug": "uefa.champions"},
                        "competitions": [{
                            "status": {"type": {"state": "in", "detail": "33'", "completed": False}},
                            "competitors": [
                                {"homeAway": "home", "team": {"displayName": "AS Roma"}, "score": {"displayValue": "1"}},
                                {"homeAway": "away", "team": {"displayName": "Real Madrid"}, "score": {"displayValue": "1"}},
                            ],
                        }],
                    }]
                }
            if "/summary" in url:
                return {
                    "header": {
                        "competitions": [{
                            "status": {"type": {"detail": "33'"}},
                            "competitors": [
                                {"homeAway": "home", "team": {"displayName": "AS Roma"}, "score": "1"},
                                {"homeAway": "away", "team": {"displayName": "Real Madrid"}, "score": "1"},
                            ],
                        }]
                    }
                }
            return {}

        mock_get.side_effect = side_effect
        out = skill_sports_info(query="whats status of current real madrid game?")
        self.assertIn("MATCH SUMMARY: AS Roma 1 - 1 Real Madrid (33')", out)
        self.assertIn("CURRENTLY PLAYING RIGHT NOW", out)
        self.assertNotIn("SQUAD ROSTER & PLAYER SEASON STATS", out)
        # Ensure live match summary is placed BEFORE the schedule block
        self.assertLess(out.index("MATCH SUMMARY"), out.index("TEAM SCHEDULE & RESULTS"))


if __name__ == "__main__":
    unittest.main()


