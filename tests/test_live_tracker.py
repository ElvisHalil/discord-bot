import asyncio
import time
import unittest
from unittest.mock import patch

from src.skills.sports.live_tracker import (
    LIVE_TRACKER,
    SAFETY_TIMEOUT_SECONDS,
    LiveSportsTracker,
    MatchSnapshot,
    TrackedMatch,
    diff_snapshots,
    extract_snapshot_from_summary,
    set_active_channel_id,
    skill_track_live_game,
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


class TestLiveSportsTracker(unittest.TestCase):
    def setUp(self):
        LIVE_TRACKER.tracked_matches.clear()
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
        tracker = LiveSportsTracker(poll_interval=1)
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

        with patch("src.skills.sports.live_tracker.fetch_json", return_value=ft_payload):
            asyncio.run(tracker.poll_once())

        # Both matches should now be automatically removed!
        self.assertEqual(len(tracker.tracked_matches), 0)
        # Match 1 should have fired its Full-Time notification before removal
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0][0], "7001")
        self.assertTrue(any("FULL-TIME" in t for t in notifications[0][3]))

    @patch("src.skills.sports.live_tracker.resolve_live_or_next_event")
    def test_skill_track_live_game_and_sports_info_delegation(self, mock_resolve):
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
        list_msg = skill_track_live_game(action="list")
        self.assertIn("AS Roma 1 - 0 Real Madrid", list_msg)

        # Stop tracking
        stop_msg = skill_track_live_game(action="stop", query="Real Madrid")
        self.assertIn("Stopped live tracking", stop_msg)
        self.assertEqual(len(LIVE_TRACKER.tracked_matches), 0)


if __name__ == "__main__":
    unittest.main()
