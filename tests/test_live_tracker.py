import asyncio
import time
import unittest
from unittest.mock import patch

from src.skills.sports.tracker import (
    SPORTS_TRACKER,
    SAFETY_TIMEOUT_SECONDS,
    SportsTrackerService,
    MatchSnapshot,
    TrackedMatch,
    diff_snapshots,
    extract_snapshot_from_summary,
    set_active_channel_id,
    skill_sports_tracker,
)
from src.skills.sports.sports import skill_sports_info


def _make_summary_payload(
    state: str = "in",
    completed: bool = False,
    status_name: str = "STATUS_IN_PROGRESS",
    short_detail: str = "24'",
    period: int = 1,
    home_team: str = "AS Roma",
    away_team: str = "Real Madrid",
    home_score: str = "1",
    away_score: str = "0",
    key_events: list = None,
    scoring_plays: list = None,
) -> dict:
    return {
        "header": {
            "competitions": [
                {
                    "status": {
                        "period": period,
                        "type": {
                            "state": state,
                            "completed": completed,
                            "name": status_name,
                            "shortDetail": short_detail,
                        },
                    },
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {"displayName": home_team, "shortDisplayName": home_team},
                            "score": home_score,
                        },
                        {
                            "homeAway": "away",
                            "team": {"displayName": away_team, "shortDisplayName": away_team},
                            "score": away_score,
                        },
                    ],
                }
            ]
        },
        "keyEvents": key_events or [],
        "scoringPlays": scoring_plays or [],
        "boxscore": {
            "teams": [
                {
                    "team": {"shortDisplayName": home_team},
                    "statistics": [
                        {"name": "possessionPct", "displayValue": "44"},
                        {"name": "totalShots", "displayValue": "7"},
                    ],
                },
                {
                    "team": {"shortDisplayName": away_team},
                    "statistics": [
                        {"name": "possessionPct", "displayValue": "56"},
                        {"name": "totalShots", "displayValue": "14"},
                    ],
                },
            ]
        },
    }


class TestSportsTrackerService(unittest.TestCase):
    def setUp(self):
        SPORTS_TRACKER.tracked_matches.clear()
        set_active_channel_id(12345)

    def test_safety_timeout_is_4_hours(self):
        self.assertEqual(SAFETY_TIMEOUT_SECONDS, 4 * 3600)

    def test_diff_snapshots_soccer_lifecycle(self):
        # 1. Pre-match snapshot
        pre_snap = extract_snapshot_from_summary(
            _make_summary_payload(
                state="pre",
                status_name="STATUS_SCHEDULED",
                short_detail="19:00 UTC",
                home_score="0",
                away_score="0",
            )
        )
        tracked = TrackedMatch(
            channel_id=12345,
            sport_path="soccer",
            league_code="uefa.champions",
            league_name="UEFA Champions League",
            event_id="7001",
            home_team="AS Roma",
            away_team="Real Madrid",
            last_snapshot=pre_snap,
        )

        # 2. Kickoff (pre -> in)
        kickoff_snap = extract_snapshot_from_summary(
            _make_summary_payload(
                state="in",
                status_name="STATUS_IN_PROGRESS",
                short_detail="1'",
                home_score="0",
                away_score="0",
            )
        )
        triggers = diff_snapshots(tracked, kickoff_snap)
        self.assertEqual(len(triggers), 1)
        self.assertIn("KICKOFF / GAME START", triggers[0])
        tracked.last_snapshot = kickoff_snap

        # 3. Goal scored (0-0 -> 1-0 with keyEvent)
        goal_snap = extract_snapshot_from_summary(
            _make_summary_payload(
                state="in",
                status_name="STATUS_IN_PROGRESS",
                short_detail="18'",
                home_score="1",
                away_score="0",
                key_events=[
                    {
                        "id": "ev_101",
                        "type": {"text": "Goal"},
                        "clock": {"displayValue": "18'"},
                        "text": "Paulo Dybala (AS Roma) left footed shot",
                        "scoringPlay": True,
                    }
                ],
            )
        )
        triggers = diff_snapshots(tracked, goal_snap)
        self.assertEqual(len(triggers), 1)
        self.assertIn("GOAL!", triggers[0])
        self.assertIn("Paulo Dybala", triggers[0])
        goal_snap.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = goal_snap

        # 4. Next poll at 25' with no new goals should NOT re-trigger
        quiet_snap = extract_snapshot_from_summary(
            _make_summary_payload(
                state="in",
                status_name="STATUS_IN_PROGRESS",
                short_detail="25'",
                home_score="1",
                away_score="0",
                key_events=[
                    {
                        "id": "ev_101",
                        "type": {"text": "Goal"},
                        "clock": {"displayValue": "18'"},
                        "text": "Paulo Dybala (AS Roma) left footed shot",
                        "scoringPlay": True,
                    }
                ],
            )
        )
        triggers = diff_snapshots(tracked, quiet_snap)
        self.assertEqual(triggers, [])
        quiet_snap.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = quiet_snap

        # 5. Halftime reached
        ht_snap = extract_snapshot_from_summary(
            _make_summary_payload(
                state="in",
                status_name="STATUS_HALFTIME",
                short_detail="HT",
                home_score="1",
                away_score="0",
            )
        )
        triggers = diff_snapshots(tracked, ht_snap)
        self.assertEqual(len(triggers), 1)
        self.assertIn("HALFTIME REACHED", triggers[0])
        self.assertIn("Possession: AS Roma 44% - 56% Real Madrid", triggers[0])
        ht_snap.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = ht_snap

        # 6. Second Half resumption
        h2_snap = extract_snapshot_from_summary(
            _make_summary_payload(
                state="in",
                status_name="STATUS_SECOND_HALF",
                short_detail="46'",
                period=2,
                home_score="1",
                away_score="0",
            )
        )
        triggers = diff_snapshots(tracked, h2_snap)
        self.assertEqual(len(triggers), 1)
        self.assertIn("SECOND HALF UNDERWAY", triggers[0])
        h2_snap.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = h2_snap

        # 7. Red card + Equalizer goal
        eq_snap = extract_snapshot_from_summary(
            _make_summary_payload(
                state="in",
                status_name="STATUS_SECOND_HALF",
                short_detail="67'",
                period=2,
                home_score="1",
                away_score="1",
                key_events=[
                    {
                        "id": "ev_101",
                        "type": {"text": "Goal"},
                        "clock": {"displayValue": "18'"},
                        "text": "Paulo Dybala (AS Roma)",
                        "scoringPlay": True,
                    },
                    {
                        "id": "ev_202",
                        "type": {"text": "Red Card"},
                        "clock": {"displayValue": "65'"},
                        "text": "Gianluca Mancini (AS Roma) receives a red card",
                        "scoringPlay": False,
                    },
                    {
                        "id": "ev_203",
                        "type": {"text": "Goal"},
                        "clock": {"displayValue": "67'"},
                        "text": "Kylian Mbappé (Real Madrid) right footed shot",
                        "scoringPlay": True,
                    },
                ],
            )
        )
        triggers = diff_snapshots(tracked, eq_snap)
        self.assertEqual(len(triggers), 2)
        self.assertTrue(any("RED CARD!" in t for t in triggers))
        self.assertTrue(any("Kylian Mbappé" in t for t in triggers))
        eq_snap.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = eq_snap

        # 8. Full-time / Final whistle
        ft_snap = extract_snapshot_from_summary(
            _make_summary_payload(
                state="post",
                completed=True,
                status_name="STATUS_FULL_TIME",
                short_detail="FT",
                period=2,
                home_score="1",
                away_score="1",
            )
        )
        triggers = diff_snapshots(tracked, ft_snap)
        self.assertEqual(len(triggers), 1)
        self.assertIn("FULL-TIME / FINAL WHISTLE", triggers[0])
        self.assertTrue(ft_snap.is_finished)

    def test_basketball_swing_throttling(self):
        snap0 = MatchSnapshot(
            state="in",
            status_name="STATUS_IN_PROGRESS",
            status_detail="Q1 08:00",
            period=1,
            home_team="Lakers",
            away_team="Celtics",
            home_score="10",
            away_score="10",
        )
        tracked = TrackedMatch(
            channel_id=12345,
            sport_path="basketball",
            league_code="nba",
            league_name="NBA",
            event_id="9001",
            home_team="Lakers",
            away_team="Celtics",
            last_snapshot=snap0,
        )
        # Routine 2-point basket in Q1 -> should be throttled (no spam)
        snap1 = MatchSnapshot(
            state="in",
            status_name="STATUS_IN_PROGRESS",
            status_detail="Q1 07:40",
            period=1,
            home_team="Lakers",
            away_team="Celtics",
            home_score="12",
            away_score="10",
        )
        self.assertEqual(diff_snapshots(tracked, snap1), [])
        tracked.last_snapshot = snap1

        # 12-point run in Q1 -> exceeds 10pt swing threshold, triggers update
        snap2 = MatchSnapshot(
            state="in",
            status_name="STATUS_IN_PROGRESS",
            status_detail="Q1 03:10",
            period=1,
            home_team="Lakers",
            away_team="Celtics",
            home_score="22",
            away_score="10",
        )
        triggers = diff_snapshots(tracked, snap2)
        self.assertEqual(len(triggers), 1)
        self.assertIn("SCORE UPDATE", triggers[0])

    def test_poll_once_self_stops_on_full_time_and_timeout(self):
        tracker = SportsTrackerService(poll_interval=1)
        notifications = []

        async def fake_notify(match, snapshot, triggers):
            notifications.append((match.event_id, snapshot.home_score, snapshot.away_score, triggers))

        tracker.set_notify_callback(fake_notify)

        # Match 1: Live match that reaches Full-Time on next poll
        snap_live = extract_snapshot_from_summary(
            _make_summary_payload(state="in", short_detail="89'", home_score="1", away_score="2")
        )
        tracker.tracked_matches[(12345, "7001")] = TrackedMatch(
            channel_id=12345,
            sport_path="soccer",
            league_code="uefa.champions",
            league_name="UEFA Champions League",
            event_id="7001",
            home_team="AS Roma",
            away_team="Real Madrid",
            last_snapshot=snap_live,
        )

        # Match 2: Expired match (> 4 hours old)
        tracker.tracked_matches[(12345, "7002")] = TrackedMatch(
            channel_id=12345,
            sport_path="soccer",
            league_code="eng.1",
            league_name="English Premier League",
            event_id="7002",
            home_team="Arsenal",
            away_team="Chelsea",
            last_snapshot=snap_live,
            created_at=time.time() - (SAFETY_TIMEOUT_SECONDS + 60),
        )

        ft_payload = _make_summary_payload(
            state="post",
            completed=True,
            status_name="STATUS_FULL_TIME",
            short_detail="FT",
            home_score="1",
            away_score="2",
        )

        with patch("src.skills.sports.tracker.fetch_json", return_value=ft_payload):
            asyncio.run(tracker.poll_once())

        # Both matches should now be automatically removed!
        self.assertEqual(len(tracker.tracked_matches), 0)
        # Match 1 should have fired its Full-Time notification before removal
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0][0], "7001")
        self.assertTrue(any("FULL-TIME" in t for t in notifications[0][3]))

    @patch("src.skills.sports.tracker.resolve_live_or_next_event")
    def test_skill_sports_tracker_and_sports_info_delegation(self, mock_resolve):
        mock_resolve.return_value = {
            "sport_path": "soccer",
            "league_code": "uefa.champions",
            "league_name": "UEFA Champions League",
            "event_id": "4018001",
            "home_team": "AS Roma",
            "away_team": "Real Madrid",
            "snapshot": extract_snapshot_from_summary(
                _make_summary_payload(state="in", short_detail="30'", home_score="1", away_score="0")
            ),
        }

        # Start tracking via skill_sports_info fallback delegation
        start_msg = skill_sports_info("soccer", "track the Real Madrid game")
        self.assertIn("LIVE MATCH TRACKER ACTIVATED", start_msg)
        self.assertIn("AS Roma vs Real Madrid", start_msg)

        # List tracked games
        list_msg = skill_sports_tracker(action="list")
        self.assertIn("AS Roma 1 - 0 Real Madrid", list_msg)

        # Stop tracking
        stop_msg = skill_sports_tracker(action="stop", query="Real Madrid")
        self.assertIn("Stopped live tracking", stop_msg)
        self.assertEqual(len(SPORTS_TRACKER.tracked_matches), 0)

    @patch("src.skills.sports.tracker.resolve_live_or_next_event")
    def test_track_this_game_uses_channel_last_match_context(self, mock_resolve):
        from src.skills.sports.tracker import (
            set_active_channel_id,
            record_channel_last_match_query,
            _clean_tracking_query,
        )

        # Ensure demonstrative phrases strip cleanly to empty string
        self.assertEqual(_clean_tracking_query("track this game"), "")
        self.assertEqual(_clean_tracking_query("track the game"), "")
        self.assertEqual(_clean_tracking_query("track that match"), "")

        set_active_channel_id(99999)
        record_channel_last_match_query("AS Roma vs Real Madrid", sport="soccer", channel_id=99999)

        mock_resolve.return_value = {
            "sport_path": "soccer",
            "league_code": "uefa.champions",
            "league_name": "UEFA Champions League",
            "event_id": "4018999",
            "home_team": "AS Roma",
            "away_team": "Real Madrid",
            "snapshot": extract_snapshot_from_summary(
                _make_summary_payload(state="in", short_detail="55'", home_score="2", away_score="1")
            ),
        }

        out = skill_sports_tracker(query="this game", action="start")
        mock_resolve.assert_called_with("AS Roma vs Real Madrid", sport="soccer")
        self.assertIn("LIVE MATCH TRACKER ACTIVATED", out)
        self.assertIn("ONGOING, NOT FINISHED", out)
        self.assertIn("Do NOT say the game ended at 55'", out)
        SPORTS_TRACKER.stop_tracking(99999)

    @patch("src.skills.sports.aggregators.espn_utils.cached_espn_get")
    def test_parse_match_summary_marks_live_in_progress(self, mock_get):
        from src.skills.sports.aggregators.espn_utils import parse_match_summary
        from src.skills.sports.tracker import set_active_channel_id, get_channel_last_match_query

        set_active_channel_id(88888)
        mock_get.return_value = _make_summary_payload(
            state="in",
            completed=False,
            status_name="STATUS_SECOND_HALF",
            short_detail="74'",
            home_score="2",
            away_score="2",
        )
        summary_text = parse_match_summary("soccer", "uefa.champions", "4018999")
        self.assertIn("🔴 LIVE MATCH IN PROGRESS (ONGOING — NOT FINISHED): AS Roma 2 - 2 Real Madrid (Live Clock: 74')", summary_text)
        self.assertIn("The game is NOT over", summary_text)
        self.assertEqual(get_channel_last_match_query(88888), ("AS Roma vs Real Madrid", "soccer"))

    def test_nhl_plays_goal_extraction_and_poll_once_signature(self):
        nhl_summary_initial = {
            "header": {
                "competitions": [
                    {
                        "status": {
                            "period": 1,
                            "type": {
                                "state": "in",
                                "completed": False,
                                "name": "STATUS_IN_PROGRESS",
                                "shortDetail": "12:00 - 1st",
                            },
                        },
                        "competitors": [
                            {"homeAway": "home", "team": {"id": "23", "displayName": "Washington Capitals"}, "score": "1"},
                            {"homeAway": "away", "team": {"id": "10", "displayName": "Montreal Canadiens"}, "score": "1"},
                        ],
                    }
                ]
            },
            "plays": [
                {
                    "id": "p1",
                    "type": {"text": "Goal"},
                    "text": "Josh Anderson Goal (3) Snap Shot, assists: Alex Newhook (2)",
                    "awayScore": 1,
                    "homeScore": 0,
                    "period": {"number": 1, "displayValue": "1st"},
                    "clock": {"displayValue": "4:29"},
                    "scoringPlay": True,
                    "team": {"id": "10"},
                    "strength": {"text": "Even Strength"},
                },
                {
                    "id": "p2",
                    "type": {"text": "Goal"},
                    "text": "Jordan Kyrou Goal (2) Snap Shot, assists: Cole Hutson (3)",
                    "awayScore": 1,
                    "homeScore": 1,
                    "period": {"number": 1, "displayValue": "1st"},
                    "clock": {"displayValue": "7:56"},
                    "scoringPlay": True,
                    "team": {"id": "23"},
                    "strength": {"text": "Even Strength"},
                },
            ],
            "boxscore": {
                "teams": [
                    {"team": {"shortDisplayName": "Canadiens"}, "statistics": [{"name": "shotsTotal", "displayValue": "8"}, {"name": "hits", "displayValue": "10"}]},
                    {"team": {"shortDisplayName": "Capitals"}, "statistics": [{"name": "shotsTotal", "displayValue": "9"}, {"name": "hits", "displayValue": "8"}]},
                ]
            },
        }

        snap1 = extract_snapshot_from_summary(nhl_summary_initial)
        self.assertEqual(len(snap1.notable_events), 2)
        self.assertIn("Shots on Goal: Canadiens 8 - 9 Capitals", snap1.stats_summary)

        # Simulate a new Capitals Power Play goal scored on the next poll
        nhl_summary_goal = dict(nhl_summary_initial)
        nhl_summary_goal["header"] = {
            "competitions": [
                {
                    "status": {
                        "period": 1,
                        "type": {
                            "state": "in",
                            "completed": False,
                            "name": "STATUS_IN_PROGRESS",
                            "shortDetail": "19:34 - 1st",
                        },
                    },
                    "competitors": [
                        {"homeAway": "home", "team": {"id": "23", "displayName": "Washington Capitals"}, "score": "2"},
                        {"homeAway": "away", "team": {"id": "10", "displayName": "Montreal Canadiens"}, "score": "1"},
                    ],
                }
            ]
        }
        nhl_summary_goal["plays"] = list(nhl_summary_initial["plays"]) + [
            {
                "id": "p3",
                "type": {"text": "Goal"},
                "text": "Ivan Miroshnichenko Goal (1) Wrist Shot, assists: Ilya Protas (3)",
                "awayScore": 1,
                "homeScore": 2,
                "period": {"number": 1, "displayValue": "1st"},
                "clock": {"displayValue": "19:34"},
                "scoringPlay": True,
                "team": {"id": "23"},
                "strength": {"text": "Power Play"},
            }
        ]

        tracker = SportsTrackerService(poll_interval=1)
        notifications = []

        async def fake_notify(match, snapshot, triggers):
            notifications.append((match.event_id, snapshot.home_score, snapshot.away_score, triggers))

        tracker.set_notify_callback(fake_notify)
        tracker.tracked_matches[(55555, "401892504")] = TrackedMatch(
            channel_id=55555,
            sport_path="hockey",
            league_code="nhl",
            league_name="NHL",
            event_id="401892504",
            home_team="Washington Capitals",
            away_team="Montreal Canadiens",
            last_snapshot=snap1,
        )

        # Ensure poll_once passes timeout as a keyword argument (not headers=8)
        def strict_fetch_json(url, headers=None, timeout=10.0, **kwargs):
            if headers is not None and not isinstance(headers, dict):
                raise TypeError("headers must be dict or None")
            return nhl_summary_goal

        with patch("src.skills.sports.tracker.fetch_json", side_effect=strict_fetch_json):
            asyncio.run(tracker.poll_once())

        self.assertEqual(len(notifications), 1)
        _, h_score, a_score, trigs = notifications[0]
        self.assertEqual(h_score, "2")
        self.assertEqual(a_score, "1")
        self.assertTrue(any("Ivan Miroshnichenko Goal" in t and "[Power Play]" in t for t in trigs))

    def test_cs2_bo5_key_milestones_and_tracker_lifecycle(self):
        from src.skills.sports.models import CsLiveSeries, CsMapResult
        from src.skills.sports.tracker import (
            extract_snapshot_from_cs_series,
            resolve_live_or_next_event,
        )

        # 1. Start during Map 2 (Ancient) in a BO5 series (BetBoom 1 - 0 Aurora)
        series_map2_live = CsLiveSeries(
            match_id="115219",
            slug="aurora-vs-betboom",
            tournament="Thunderpick World Championship 2026",
            tier="A",
            stage="Playoffs",
            bo_type=5,
            status="current",
            team1="BetBoom Team",
            team2="Aurora Gaming",
            team1_score=1,
            team2_score=0,
            active_map_name="Ancient",
            active_map_number=2,
            maps=[
                CsMapResult(map_number=1, map_name="Dust2", status="finished", winner="BetBoom Team", loser="Aurora Gaming", winner_score=13, loser_score=6, team1_rounds=13, team2_rounds=6),
                CsMapResult(map_number=2, map_name="Ancient", status="current", team1_rounds=10, team2_rounds=11),
                CsMapResult(map_number=3, map_name="Nuke", status="upcoming"),
                CsMapResult(map_number=4, map_name="Mirage", status="upcoming"),
                CsMapResult(map_number=5, map_name="Cache", status="upcoming"),
            ],
        )

        with patch(
            "src.skills.sports.aggregators.kashrock.KashrockAggregator.get_live_and_upcoming_series",
            return_value=([series_map2_live], []),
        ):
            info = resolve_live_or_next_event("track Aurora vs BetBoom", sport="cs2")
            self.assertIsNotNone(info)
            self.assertEqual(info["sport_path"], "esports")
            self.assertEqual(info["league_code"], "cs2")
            self.assertEqual(info["event_id"], "115219")

        snap_initial = info["snapshot"]
        tracked = TrackedMatch(
            channel_id=12345,
            sport_path="esports",
            league_code="cs2",
            league_name=info["league_name"],
            event_id="115219",
            home_team="BetBoom Team",
            away_team="Aurora Gaming",
            last_snapshot=snap_initial,
        )

        # 2. Map 2 finishes in OT (19-16 Aurora) and Map 3 (Nuke) starts at 0-0 -> Fires Map 2 Final + Map 3 Started
        series_map3_start = CsLiveSeries(
            match_id="115219",
            slug="aurora-vs-betboom",
            tournament="Thunderpick World Championship 2026",
            bo_type=5,
            status="current",
            team1="BetBoom Team",
            team2="Aurora Gaming",
            team1_score=1,
            team2_score=1,
            active_map_name="Nuke",
            active_map_number=3,
            maps=[
                CsMapResult(map_number=1, map_name="Dust2", status="finished", winner="BetBoom Team", loser="Aurora Gaming", winner_score=13, loser_score=6),
                CsMapResult(map_number=2, map_name="Ancient", status="finished", winner="Aurora Gaming", loser="BetBoom Team", winner_score=19, loser_score=16),
                CsMapResult(map_number=3, map_name="Nuke", status="current", team1_rounds=1, team2_rounds=0),
                CsMapResult(map_number=4, map_name="Mirage", status="upcoming"),
                CsMapResult(map_number=5, map_name="Cache", status="upcoming"),
            ],
        )
        snap_m3_start = extract_snapshot_from_cs_series(series_map3_start)
        triggers = diff_snapshots(tracked, snap_m3_start)
        self.assertEqual(len(triggers), 2)
        self.assertTrue(any("MAP 2 FINAL (Ancient)" in t and "Next Map: Nuke" in t for t in triggers))
        self.assertTrue(any("MAP 3 STARTED (Nuke)" in t for t in triggers))
        snap_m3_start.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = snap_m3_start

        # 3. Routine round 3 (2-1) -> Should NOT trigger spam (Key Milestones only!)
        series_map3_r3 = CsLiveSeries(
            match_id="115219",
            slug="aurora-vs-betboom",
            tournament="Thunderpick World Championship 2026",
            bo_type=5,
            status="current",
            team1="BetBoom Team",
            team2="Aurora Gaming",
            team1_score=1,
            team2_score=1,
            active_map_name="Nuke",
            active_map_number=3,
            maps=[
                CsMapResult(map_number=1, map_name="Dust2", status="finished", winner="BetBoom Team", loser="Aurora Gaming", winner_score=13, loser_score=6),
                CsMapResult(map_number=2, map_name="Ancient", status="finished", winner="Aurora Gaming", loser="BetBoom Team", winner_score=19, loser_score=16),
                CsMapResult(map_number=3, map_name="Nuke", status="current", team1_rounds=2, team2_rounds=1),
            ],
        )
        snap_r3 = extract_snapshot_from_cs_series(series_map3_r3)
        self.assertEqual(diff_snapshots(tracked, snap_r3), [])
        snap_r3.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = snap_r3

        # 4. 5-Round Checkpoint at Round 5 (3-2) -> Triggers checkpoint update
        series_map3_r5 = CsLiveSeries(
            match_id="115219",
            slug="aurora-vs-betboom",
            tournament="Thunderpick World Championship 2026",
            bo_type=5,
            status="current",
            team1="BetBoom Team",
            team2="Aurora Gaming",
            team1_score=1,
            team2_score=1,
            active_map_name="Nuke",
            active_map_number=3,
            maps=[
                CsMapResult(map_number=1, map_name="Dust2", status="finished", winner="BetBoom Team", loser="Aurora Gaming", winner_score=13, loser_score=6),
                CsMapResult(map_number=2, map_name="Ancient", status="finished", winner="Aurora Gaming", loser="BetBoom Team", winner_score=19, loser_score=16),
                CsMapResult(map_number=3, map_name="Nuke", status="current", team1_rounds=3, team2_rounds=2),
            ],
        )
        snap_r5 = extract_snapshot_from_cs_series(series_map3_r5)
        triggers_r5 = diff_snapshots(tracked, snap_r5)
        self.assertEqual(len(triggers_r5), 1)
        self.assertIn("MAP 3 ROUND 5 (Nuke)", triggers_r5[0])
        snap_r5.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = snap_r5

        # 5. MR12 Halftime at Round 12 (7-5) -> Triggers Map Halftime
        series_map3_ht = CsLiveSeries(
            match_id="115219",
            slug="aurora-vs-betboom",
            tournament="Thunderpick World Championship 2026",
            bo_type=5,
            status="current",
            team1="BetBoom Team",
            team2="Aurora Gaming",
            team1_score=1,
            team2_score=1,
            active_map_name="Nuke",
            active_map_number=3,
            maps=[
                CsMapResult(map_number=1, map_name="Dust2", status="finished", winner="BetBoom Team", loser="Aurora Gaming", winner_score=13, loser_score=6),
                CsMapResult(map_number=2, map_name="Ancient", status="finished", winner="Aurora Gaming", loser="BetBoom Team", winner_score=19, loser_score=16),
                CsMapResult(map_number=3, map_name="Nuke", status="current", team1_rounds=7, team2_rounds=5),
            ],
        )
        snap_ht = extract_snapshot_from_cs_series(series_map3_ht)
        triggers_ht = diff_snapshots(tracked, snap_ht)
        self.assertEqual(len(triggers_ht), 1)
        self.assertIn("MAP 3 HALFTIME (Nuke)", triggers_ht[0])
        snap_ht.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = snap_ht

        # 6. Series finishes (Aurora wins 3-1) via SportsTrackerService.poll_once -> Fires Full-Time and self-stops!
        series_final = CsLiveSeries(
            match_id="115219",
            slug="aurora-vs-betboom",
            tournament="Thunderpick World Championship 2026",
            bo_type=5,
            status="finished",
            team1="BetBoom Team",
            team2="Aurora Gaming",
            team1_score=1,
            team2_score=3,
            maps=[
                CsMapResult(map_number=1, map_name="Dust2", status="finished", winner="BetBoom Team", loser="Aurora Gaming", winner_score=13, loser_score=6),
                CsMapResult(map_number=2, map_name="Ancient", status="finished", winner="Aurora Gaming", loser="BetBoom Team", winner_score=19, loser_score=16),
                CsMapResult(map_number=3, map_name="Nuke", status="finished", winner="Aurora Gaming", loser="BetBoom Team", winner_score=13, loser_score=10),
                CsMapResult(map_number=4, map_name="Mirage", status="finished", winner="Aurora Gaming", loser="BetBoom Team", winner_score=13, loser_score=8),
            ],
        )

        tracker = SportsTrackerService(poll_interval=1)
        cs_notifications = []

        async def fake_cs_notify(match, snapshot, trigs):
            cs_notifications.append((match.event_id, snapshot.home_score, snapshot.away_score, trigs))

        tracker.set_notify_callback(fake_cs_notify)
        tracker.tracked_matches[(12345, "115219")] = tracked

        with patch(
            "src.skills.sports.aggregators.kashrock.KashrockAggregator.get_series_for_tracker",
            return_value=series_final,
        ):
            asyncio.run(tracker.poll_once())

        # Match should be auto-removed after series completion
        self.assertEqual(len(tracker.tracked_matches), 0)
        self.assertEqual(len(cs_notifications), 1)
        self.assertEqual(cs_notifications[0][1], "1")
        self.assertEqual(cs_notifications[0][2], "3")
        self.assertTrue(any("FULL-TIME / FINAL WHISTLE" in t for t in cs_notifications[0][3]))

    def test_cs2_bo3_sweep_and_decider_handling(self):
        from src.skills.sports.models import CsLiveSeries, CsMapResult
        from src.skills.sports.tracker import extract_snapshot_from_cs_series

        # 1. Standard BO3 series live on Map 2 (Vitality leads 1-0 over FaZe)
        bo3_live = CsLiveSeries(
            match_id="2001",
            slug="vitality-vs-faze",
            tournament="BLAST Premier",
            bo_type=3,
            status="current",
            team1="Team Vitality",
            team2="FaZe Clan",
            team1_score=1,
            team2_score=0,
            active_map_name="Mirage",
            active_map_number=2,
            maps=[
                CsMapResult(map_number=1, map_name="Inferno", status="finished", winner="Team Vitality", loser="FaZe Clan", winner_score=13, loser_score=9),
                CsMapResult(map_number=2, map_name="Mirage", status="current", team1_rounds=11, team2_rounds=8),
                CsMapResult(map_number=3, map_name="Anubis", status="upcoming"),
            ],
        )
        snap_live = extract_snapshot_from_cs_series(bo3_live)
        self.assertFalse(snap_live.is_finished)
        self.assertIn("BO3 • Map 2 (Mirage: Team Vitality 11-8 FaZe Clan)", snap_live.status_detail)
        self.assertIn("Map 3 (Anubis): Upcoming", snap_live.stats_summary)

        tracked = TrackedMatch(
            channel_id=12345,
            sport_path="esports",
            league_code="cs2",
            league_name="BLAST Premier (CS2 BO3)",
            event_id="2001",
            home_team="Team Vitality",
            away_team="FaZe Clan",
            last_snapshot=snap_live,
        )

        # 2. Vitality wins Map 2 for a 2-0 BO3 sweep -> Series is immediately finished (2 == maps_to_win for BO3),
        #    and unplayed Map 3 (Anubis) is NOT listed as "Next Map" or "Upcoming" in Final Stats!
        bo3_sweep = CsLiveSeries(
            match_id="2001",
            slug="vitality-vs-faze",
            tournament="BLAST Premier",
            bo_type=3,
            status="current",  # even before BO3.gg flips status to 'finished', 2-0 in BO3 completes it
            team1="Team Vitality",
            team2="FaZe Clan",
            team1_score=2,
            team2_score=0,
            maps=[
                CsMapResult(map_number=1, map_name="Inferno", status="finished", winner="Team Vitality", loser="FaZe Clan", winner_score=13, loser_score=9),
                CsMapResult(map_number=2, map_name="Mirage", status="finished", winner="Team Vitality", loser="FaZe Clan", winner_score=13, loser_score=8),
                CsMapResult(map_number=3, map_name="Anubis", status="upcoming"),
            ],
        )
        snap_sweep = extract_snapshot_from_cs_series(bo3_sweep)
        self.assertTrue(snap_sweep.is_finished)
        self.assertEqual(snap_sweep.status_detail, "Final (BO3)")
        self.assertNotIn("Anubis", snap_sweep.stats_summary)

        triggers = diff_snapshots(tracked, snap_sweep)
        self.assertTrue(any("MAP 2 FINAL (Mirage)" in t and "Next Map" not in t for t in triggers))
        self.assertTrue(any("FULL-TIME / FINAL WHISTLE (Final (BO3))" in t for t in triggers))

    @patch.dict("os.environ", {"BOT_TIMEZONE": "America/Edmonton"})
    def test_f1_live_session_tracking_lifecycle_and_openf1_telemetry(self):
        """Verify end-to-end F1 Qualifying & Race session tracking across pre-session, OpenF1 Green Light,
        Red Flag, Safety Car, Steward Penalties, P1 Provisional Pole lead changes, and Final Chequered Flag."""
        from src.skills.sports.aggregators.f1 import F1Aggregator

        agg = F1Aggregator()

        espn_scoreboard_pre = {
            "events": [{
                "id": "401858359",
                "name": "MSC Cruises United States Grand Prix",
                "circuit": {"fullName": "Circuit of the Americas"},
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
            }]
        }

        # 1. Start tracking F1 Qualifying while 'pre'
        with patch("src.skills.sports.aggregators.f1.fetch_json", return_value=espn_scoreboard_pre), \
             patch.object(F1Aggregator, "_fetch_openf1_live_data", return_value={}):
            start_msg = SPORTS_TRACKER.start_tracking(12345, query="track the F1 qualifying session", sport="f1")

        self.assertIn("LIVE F1 SESSION TRACKER ACTIVATED", start_msg)
        self.assertIn("MSC Cruises United States Grand Prix — Qualifying", start_msg)
        self.assertIn("Saturday, Oct 24 at 3:00 PM MDT (21:00 UTC)", start_msg)
        self.assertIn((12345, "401858359:401858363"), SPORTS_TRACKER.tracked_matches)

        tracked = SPORTS_TRACKER.tracked_matches[(12345, "401858359:401858363")]
        self.assertEqual(tracked.sport_path, "f1")
        self.assertEqual(tracked.last_snapshot.state, "pre")

        # 2. Session goes LIVE ('in') with OpenF1 Green Light + initial running order (P1: Max Verstappen, P2: Lando Norris)
        espn_scoreboard_live = {
            "events": [{
                "id": "401858359",
                "name": "MSC Cruises United States Grand Prix",
                "circuit": {"fullName": "Circuit of the Americas"},
                "competitions": [
                    {
                        "id": "401858363",
                        "type": {"abbreviation": "Qual"},
                        "date": "2026-10-24T21:00Z",
                        "status": {"type": {"state": "in", "completed": False, "name": "STATUS_IN_PROGRESS"}},
                        "competitors": [],
                    }
                ],
            }]
        }

        def fake_openf1_fetch_step1(url, timeout=4):
            if "/sessions?" in url:
                return [{"session_key": 9901, "session_name": "Qualifying", "date_start": "2026-10-24T21:00:00+00:00"}]
            if "/race_control?" in url:
                return [
                    {"date": "2026-10-24T21:00:00+00:00", "category": "Other", "flag": "GREEN", "message": "GREEN LIGHT - PIT EXIT OPEN"},
                ]
            if "/drivers?" in url:
                return [
                    {"driver_number": 1, "full_name": "Max VERSTAPPEN"},
                    {"driver_number": 4, "full_name": "Lando NORRIS"},
                    {"driver_number": 16, "full_name": "Charles LECLERC"},
                ]
            if "/position?" in url:
                return [
                    {"driver_number": 1, "position": 1},
                    {"driver_number": 4, "position": 2},
                    {"driver_number": 16, "position": 3},
                ]
            return []

        with patch("src.skills.sports.aggregators.f1.fetch_json", side_effect=lambda u, timeout=8: espn_scoreboard_live if "espn.com" in u else fake_openf1_fetch_step1(u, timeout)):
            info_step1 = agg.get_session_for_tracker(event_id="401858359:401858363")

        snap1 = info_step1["snapshot"]
        self.assertEqual(snap1.state, "in")
        self.assertEqual(snap1.home_score, "P1: Max Verstappen")
        self.assertEqual(snap1.away_score, "P2: Lando Norris")
        self.assertIn("Q1 • 🟢 Track Clear", snap1.status_detail)

        trigs1 = diff_snapshots(tracked, snap1)
        self.assertTrue(any("F1 SESSION LIVE" in t for t in trigs1))
        self.assertTrue(any("GREEN LIGHT - PIT EXIT OPEN" in t for t in trigs1))
        self.assertTrue(any("P1 LEADER UPDATE" in t and "P1: Max Verstappen" in t for t in trigs1))
        snap1.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = snap1

        # 3. Mid-session: Lando Norris takes Provisional Pole (P1) + Red Flag + Steward Penalty
        def fake_openf1_fetch_step2(url, timeout=4):
            if "/sessions?" in url:
                return [{"session_key": 9901, "session_name": "Qualifying", "date_start": "2026-10-24T21:00:00+00:00"}]
            if "/race_control?" in url:
                return [
                    {"date": "2026-10-24T21:00:00+00:00", "category": "Other", "flag": "GREEN", "message": "GREEN LIGHT - PIT EXIT OPEN"},
                    {"date": "2026-10-24T21:14:22+00:00", "category": "Flag", "flag": "RED", "message": "RED FLAG"},
                    {"date": "2026-10-24T21:15:10+00:00", "category": "Other", "flag": None, "message": "FIA STEWARDS: 3 PLACE GRID PENALTY FOR CAR 11"},
                ]
            if "/drivers?" in url:
                return [
                    {"driver_number": 1, "full_name": "Max VERSTAPPEN"},
                    {"driver_number": 4, "full_name": "Lando NORRIS"},
                    {"driver_number": 16, "full_name": "Charles LECLERC"},
                ]
            if "/position?" in url:
                return [
                    {"driver_number": 4, "position": 1},
                    {"driver_number": 1, "position": 2},
                    {"driver_number": 16, "position": 3},
                ]
            return []

        with patch("src.skills.sports.aggregators.f1.fetch_json", side_effect=lambda u, timeout=8: espn_scoreboard_live if "espn.com" in u else fake_openf1_fetch_step2(u, timeout)):
            info_step2 = agg.get_session_for_tracker(event_id="401858359:401858363")

        snap2 = info_step2["snapshot"]
        trigs2 = diff_snapshots(tracked, snap2)
        # Must NOT re-trigger the earlier GREEN LIGHT event
        self.assertFalse(any("GREEN LIGHT" in t for t in trigs2))
        self.assertTrue(any("RED FLAG" in t for t in trigs2))
        self.assertTrue(any("FIA STEWARDS" in t and "PENALTY" in t for t in trigs2))
        self.assertTrue(any("P1 LEADER UPDATE" in t and "P1: Lando Norris" in t and "was P1: Max Verstappen" in t for t in trigs2))
        snap2.seen_event_ids |= tracked.last_snapshot.seen_event_ids
        tracked.last_snapshot = snap2

        # 4. Session finishes ('post') via poll_once() -> fires Final classification alert and auto-removes from tracker
        espn_scoreboard_post = {
            "events": [{
                "id": "401858359",
                "name": "MSC Cruises United States Grand Prix",
                "circuit": {"fullName": "Circuit of the Americas"},
                "competitions": [
                    {
                        "id": "401858363",
                        "type": {"abbreviation": "Qual"},
                        "date": "2026-10-24T21:00Z",
                        "status": {"type": {"state": "post", "completed": True, "name": "STATUS_FINAL"}},
                        "competitors": [
                            {"order": 1, "athlete": {"displayName": "Lando Norris"}},
                            {"order": 2, "athlete": {"displayName": "Max Verstappen"}},
                            {"order": 3, "athlete": {"displayName": "Charles Leclerc"}},
                        ],
                    }
                ],
            }]
        }

        f1_notifications = []

        async def fake_f1_notify(m, s, trigs):
            f1_notifications.append((m.event_id, s.home_score, s.away_score, trigs))

        SPORTS_TRACKER.set_notify_callback(fake_f1_notify)
        with patch("src.skills.sports.aggregators.f1.fetch_json", return_value=espn_scoreboard_post):
            asyncio.run(SPORTS_TRACKER.poll_once())

        self.assertEqual(len(SPORTS_TRACKER.tracked_matches), 0)
        self.assertEqual(len(f1_notifications), 1)
        self.assertEqual(f1_notifications[0][1], "P1: Lando Norris")
        self.assertTrue(any("F1 SESSION FINISHED" in t for t in f1_notifications[0][3]))

    def test_f1_tracker_start_list_stop_and_missed_tool_call(self):
        """Verify list_tracked, stop_tracking('f1'), and detect_missed_sports_tool_call for F1 sessions."""
        from src.skills.sports.context import detect_missed_sports_tool_call

        # 1. Turn-0 missed tool call detection sets sport='f1'
        missed = detect_missed_sports_tool_call(
            [{"role": "user", "content": "track the F1 qualifying session"}],
            active_channel_id=12345,
        )
        self.assertIsNotNone(missed)
        self.assertEqual(missed[0], "skill_sports_tracker")
        self.assertEqual(missed[1].get("sport"), "f1")

        # 2. List & Stop F1 session
        espn_sb = {
            "events": [{
                "id": "401858359",
                "name": "MSC Cruises United States Grand Prix",
                "circuit": {"fullName": "Circuit of the Americas"},
                "competitions": [
                    {
                        "id": "401858364",
                        "type": {"abbreviation": "Race"},
                        "date": "2026-10-25T20:00Z",
                        "status": {"type": {"state": "pre", "completed": False}},
                        "competitors": [],
                    }
                ],
            }]
        }
        with patch("src.skills.sports.aggregators.f1.fetch_json", return_value=espn_sb):
            res_start = skill_sports_tracker(query="track the F1 race tomorrow", action="start", sport="f1")
            self.assertIn("Grand Prix (Race)", res_start)

            res_list = skill_sports_tracker(action="list")
            self.assertIn("MSC Cruises United States Grand Prix — Grand Prix (Race)", res_list)

            res_stop = skill_sports_tracker(query="f1", action="stop")
            self.assertIn("Stopped live tracking", res_stop)
            self.assertIn("MSC Cruises United States Grand Prix — Grand Prix (Race)", res_stop)

    def test_f1_tracker_safety_car_vsc_and_next_weekend_calendar_rollover(self):
        """
        Verify:
        1. When events[0] is completely finished (all sessions state='post'), get_session_for_tracker()
           automatically rolls over to the next upcoming Grand Prix weekend in the calendar.
        2. OpenF1 Safety Car / Virtual Safety Car and Chequered Flag messages produce high-signal alerts.
        """
        from src.skills.sports.aggregators.f1 import F1Aggregator

        agg = F1Aggregator()
        finished_austin_sb = {
            "leagues": [{
                "calendar": [
                    {"label": "United States Grand Prix", "startDate": "2020-10-23T17:30Z", "endDate": "2020-10-25T20:00Z"},
                    {"label": "Mexico City Grand Prix", "startDate": "2099-10-30T18:30Z", "endDate": "2099-11-01T20:00Z"},
                ]
            }],
            "events": [{
                "id": "401858359",
                "name": "United States Grand Prix",
                "competitions": [
                    {"id": "1", "type": {"abbreviation": "Race"}, "date": "2020-10-25T20:00Z", "status": {"type": {"state": "post", "completed": True}}}
                ],
            }],
        }
        upcoming_mexico_sb = {
            "events": [{
                "id": "401858999",
                "name": "Mexico City Grand Prix",
                "circuit": {"fullName": "Autodromo Hermanos Rodriguez"},
                "competitions": [
                    {"id": "901", "type": {"abbreviation": "Qual"}, "date": "2099-10-31T21:00Z", "status": {"type": {"state": "in", "completed": False}}},
                ],
            }]
        }

        def fake_fetch(url, timeout=8):
            if "dates=20991030" in url:
                return upcoming_mexico_sb
            if "/sessions?" in url:
                return [{"session_key": 9999, "session_name": "Qualifying", "date_start": "2099-10-31T21:00:00+00:00"}]
            if "/race_control?" in url:
                return [
                    {"date": "2099-10-31T21:00:00+00:00", "category": "Other", "flag": "GREEN", "message": "GREEN LIGHT - PIT EXIT OPEN"},
                    {"date": "2099-10-31T21:05:00+00:00", "category": "SafetyCar", "flag": None, "message": "VIRTUAL SAFETY CAR DEPLOYED"},
                    {"date": "2099-10-31T21:18:00+00:00", "category": "Flag", "flag": "CHEQUERED", "message": "CHEQUERED FLAG"},
                ]
            if "/drivers?" in url or "/position?" in url:
                return []
            return finished_austin_sb

        with patch("src.skills.sports.aggregators.f1.fetch_json", side_effect=fake_fetch):
            info = agg.get_session_for_tracker(query="track f1 qualifying")

        self.assertIsNotNone(info)
        self.assertEqual(info["home_team"], "Mexico City Grand Prix")
        self.assertEqual(info["away_team"], "Qualifying")
        snap = info["snapshot"]
        self.assertEqual(snap.state, "in")
        self.assertIn("Between Q1 & Q2", snap.status_detail)
        ev_texts = [desc for _, desc in snap.notable_events]
        self.assertTrue(any("VIRTUAL SAFETY CAR DEPLOYED" in t for t in ev_texts))
        self.assertTrue(any("CHEQUERED FLAG (End of Q1)" in t for t in ev_texts))


if __name__ == "__main__":
    unittest.main()


