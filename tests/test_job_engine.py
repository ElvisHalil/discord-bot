"""
Comprehensive unit and concurrency test suite for Phase 1:
The Unified Background Job, Reminder, Condition & Music Autoplay Engine (`tests/test_job_engine.py`).
"""

import asyncio
from datetime import datetime, timezone
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from src.context import (
    clear_channel_human_activity,
    get_last_channel_human_activity,
    record_channel_human_activity,
    set_active_channel_id,
    set_active_user,
)
from src.jobs import (
    ActionType,
    ConditionKind,
    JobEngine,
    MARKET_INTRADAY_MOVE_TTL_SECONDS,
    MARKET_PRICE_WATCH_TTL_SECONDS,
    MIN_ONE_SHOT_DELAY_SECONDS,
    MIN_RECURRING_INTERVAL_SECONDS,
    MULTI_HOUR_DEFAULT_MAX_RUNS,
    MULTI_HOUR_DEFAULT_TTL_SECONDS,
    SUB_HOUR_DEFAULT_MAX_RUNS,
    SUB_HOUR_DEFAULT_TTL_SECONDS,
    SystemJobProvider,
    TriggerType,
    UserJob,
    classify_action_type,
    evaluate_market_condition,
    evaluate_weather_condition,
    evaluate_web_condition,
    evaluate_youtube_condition,
    parse_duration_seconds,
    parse_job_request,
)
from src.orchestrator import (
    detect_missed_job_tool_call,
    enrich_job_tool_args,
    normalize_tool_args,
)
from src.skills.jobs import skill_job_manager
from src.skills.markets.models import TickerQuote
from src.skills.sports.models import MatchSnapshot, TrackedMatch
from src.skills.sports.tracker import SAFETY_TIMEOUT_SECONDS, SportsTrackerService
from src.utils.dispatcher import dispatch_tracker_command


class TestNaturalLanguageJobParser(unittest.TestCase):
    """Verify natural language parsing across all trigger types, time formats, and phrasings."""

    def test_parse_duration_seconds_numbers_and_words(self):
        self.assertEqual(parse_duration_seconds("in 15 mins"), 900)
        self.assertEqual(parse_duration_seconds("2 hours and 30 minutes"), 9000)
        self.assertEqual(parse_duration_seconds("half an hour"), 1800)
        self.assertEqual(parse_duration_seconds("45 seconds"), 45)
        self.assertEqual(parse_duration_seconds("a couple of minutes"), 120)
        self.assertEqual(parse_duration_seconds("an hour"), 3600)

    def test_one_shot_relative_and_calendar_reminders(self):
        now_ts = 1760000000.0
        now_dt = datetime(2026, 10, 22, 14, 0, 0, tzinfo=timezone.utc)

        p1 = parse_job_request(
            "remind me in 20 minutes to check the oven",
            now_ts=now_ts,
            now_dt=now_dt,
        )
        self.assertEqual(p1["trigger_type"], TriggerType.ONCE_AT)
        self.assertEqual(p1["action_type"], ActionType.REMINDER)
        self.assertEqual(p1["next_fire_at"], now_ts + 1200)
        self.assertEqual(p1["payload_text"], "check the oven")

        p2 = parse_job_request(
            "remind me tomorrow at 9:30am to call the dentist",
            now_ts=now_ts,
            now_dt=now_dt,
        )
        self.assertEqual(p2["trigger_type"], TriggerType.ONCE_AT)
        self.assertEqual(p2["action_type"], ActionType.REMINDER)
        self.assertGreater(p2["next_fire_at"], now_ts + 12 * 3600)
        self.assertIn("call the dentist", p2["payload_text"])

        # Dynamic agent task at a scheduled time
        p3 = parse_job_request(
            "at 4:05pm check the S&P 500 and TSX market close",
            now_ts=now_ts,
            now_dt=now_dt,
        )
        self.assertEqual(p3["trigger_type"], TriggerType.ONCE_AT)
        self.assertEqual(p3["action_type"], ActionType.AGENT_TASK)

        # Task-first phrasing ("remind me to check the laundry in 45 minutes")
        p4 = parse_job_request(
            "remind me to check the laundry in 45 minutes",
            now_ts=now_ts,
            now_dt=now_dt,
        )
        self.assertEqual(p4["trigger_type"], TriggerType.ONCE_AT)
        self.assertEqual(p4["next_fire_at"], now_ts + 2700)
        self.assertEqual(p4["payload_text"], "check the laundry")

        # Task-first calendar phrasing ("remind me to call the dentist tomorrow at 9am")
        p5 = parse_job_request(
            "remind me to call the dentist tomorrow at 9am",
            now_ts=now_ts,
            now_dt=now_dt,
        )
        self.assertEqual(p5["trigger_type"], TriggerType.ONCE_AT)
        self.assertEqual(p5["payload_text"], "call the dentist")

    def test_interval_loops_sub_hour_vs_multi_hour_guardrails(self):
        now_ts = 1760000000.0
        # 1. Sub-hour interval ("play a dnb song every 5 mins") -> 10 runs / 1h cap
        p_sub = parse_job_request("play a dnb song every 5 mins", now_ts=now_ts)
        self.assertEqual(p_sub["trigger_type"], TriggerType.INTERVAL)
        self.assertEqual(p_sub["action_type"], ActionType.AGENT_TASK)
        self.assertEqual(p_sub["interval_seconds"], 300)
        self.assertEqual(p_sub["max_runs"], SUB_HOUR_DEFAULT_MAX_RUNS)
        self.assertEqual(p_sub["expires_at"], now_ts + SUB_HOUR_DEFAULT_TTL_SECONDS)

        # 2. Multi-hour interval ("play a techno song every 2 hours") -> 6 runs / 12h cap
        p_multi = parse_job_request("play a techno song every 2 hours", now_ts=now_ts)
        self.assertEqual(p_multi["trigger_type"], TriggerType.INTERVAL)
        self.assertEqual(p_multi["interval_seconds"], 7200)
        self.assertEqual(p_multi["max_runs"], MULTI_HOUR_DEFAULT_MAX_RUNS)
        self.assertEqual(p_multi["expires_at"], now_ts + MULTI_HOUR_DEFAULT_TTL_SECONDS)

        # 3. Explicit user count ("play a dnb song every 5 minutes 4 times")
        p_explicit = parse_job_request("play a dnb song every 5 minutes 4 times", now_ts=now_ts)
        self.assertEqual(p_explicit["max_runs"], 4)

        # 4. Sub-minute anti-spam guardrail ("play a song every 10 seconds", "remind me in 5 seconds")
        p_spam_loop = parse_job_request("play a dnb song every 10 seconds", now_ts=now_ts)
        self.assertEqual(p_spam_loop["interval_seconds"], MIN_RECURRING_INTERVAL_SECONDS)
        self.assertEqual(p_spam_loop["next_fire_at"], now_ts + MIN_RECURRING_INTERVAL_SECONDS)
        self.assertTrue(p_spam_loop["clamped_sub_minute"])
        self.assertEqual(p_spam_loop["requested_seconds"], 10)

        p_spam_once = parse_job_request("remind me in 5 seconds to spam", now_ts=now_ts)
        self.assertEqual(p_spam_once["next_fire_at"], now_ts + MIN_ONE_SHOT_DELAY_SECONDS)
        self.assertTrue(p_spam_once["clamped_sub_minute"])
        self.assertEqual(p_spam_once["requested_seconds"], 5)

    def test_daily_and_weekday_schedules(self):
        now_ts = 1760000000.0
        now_dt = datetime(2026, 10, 22, 10, 0, 0, tzinfo=timezone.utc)
        p_daily = parse_job_request(
            "remind me every weekday at 8:30am to check pre-market movers",
            now_ts=now_ts,
            now_dt=now_dt,
        )
        self.assertEqual(p_daily["trigger_type"], TriggerType.DAILY_SCHEDULE)
        self.assertEqual(p_daily["schedule_spec"]["hour"], 8)
        self.assertEqual(p_daily["schedule_spec"]["minute"], 30)
        self.assertEqual(p_daily["schedule_spec"]["days"], [0, 1, 2, 3, 4])

    def test_autoplay_parsing(self):
        now_ts = 1760000000.0
        p_auto = parse_job_request("autoplay liquid dnb", now_ts=now_ts)
        self.assertEqual(p_auto["trigger_type"], TriggerType.AUTOPLAY)
        self.assertEqual(p_auto["action_type"], ActionType.MUSIC_AUTOPLAY)
        self.assertIn("liquid dnb", p_auto["payload_text"].lower())
        self.assertEqual(p_auto["max_runs"], 10)

    def test_condition_parsing_markets_weather_youtube_web(self):
        now_ts = 1760000000.0

        # Market price target (14-day TTL)
        m1 = parse_job_request("alert me when BTC hits $100,000", now_ts=now_ts)
        self.assertEqual(m1["trigger_type"], TriggerType.CONDITION)
        self.assertEqual(m1["condition_spec"]["kind"], ConditionKind.MARKET_PRICE)
        self.assertEqual(m1["condition_spec"]["symbol"], "BTC-USD")
        self.assertEqual(m1["condition_spec"]["operator"], ">=")
        self.assertEqual(m1["condition_spec"]["threshold"], 100000.0)
        self.assertEqual(m1["expires_at"], now_ts + MARKET_PRICE_WATCH_TTL_SECONDS)

        # Market drop below target
        m2 = parse_job_request("remind me if NVDA drops below $120", now_ts=now_ts)
        self.assertEqual(m2["condition_spec"]["symbol"], "NVDA")
        self.assertEqual(m2["condition_spec"]["operator"], "<=")
        self.assertEqual(m2["condition_spec"]["threshold"], 120.0)

        # Market intraday % move (24h TTL)
        m3 = parse_job_request("let me know if TSLA moves 5% today", now_ts=now_ts)
        self.assertEqual(m3["condition_spec"]["symbol"], "TSLA")
        self.assertEqual(m3["condition_spec"]["operator"], "pct_move")
        self.assertEqual(m3["condition_spec"]["threshold"], 5.0)
        self.assertEqual(m3["expires_at"], now_ts + MARKET_INTRADAY_MOVE_TTL_SECONDS)

        # Weather snow & temperature threshold
        w1 = parse_job_request("alert me when it snows in Calgary", now_ts=now_ts)
        self.assertEqual(w1["condition_spec"]["kind"], ConditionKind.WEATHER)
        self.assertEqual(w1["condition_spec"]["location"], "Calgary")
        self.assertEqual(w1["condition_spec"]["condition_type"], "snow")

        w2 = parse_job_request("remind me if temperature drops below -10C in Edmonton", now_ts=now_ts)
        self.assertEqual(w2["condition_spec"]["kind"], ConditionKind.WEATHER)
        self.assertEqual(w2["condition_spec"]["condition_type"], "temp_below")
        self.assertEqual(w2["condition_spec"]["threshold_c"], -10.0)

        # YouTube channel upload
        y1 = parse_job_request("let me know when @MarquesBrownlee uploads a new video", now_ts=now_ts)
        self.assertEqual(y1["condition_spec"]["kind"], ConditionKind.YOUTUBE_UPLOAD)
        self.assertEqual(y1["condition_spec"]["channel_query"], "@MarquesBrownlee")

        # Open web condition
        web1 = parse_job_request(
            "alert me when https://example.com/tickets mentions tickets on sale",
            now_ts=now_ts,
        )
        self.assertEqual(web1["condition_spec"]["kind"], ConditionKind.WEB_CONDITION)
        self.assertEqual(web1["condition_spec"]["target_url"], "https://example.com/tickets")

        # Commodity, Index & Crypto shorthand variations ("gold reaches $3,000", "Ethereum climbs above $4k", "S&P 500 drops 2%")
        m_gold = parse_job_request("notify me when gold reaches $3,000", now_ts=now_ts)
        self.assertEqual(m_gold["condition_spec"]["symbol"], "GC=F")
        self.assertEqual(m_gold["condition_spec"]["operator"], ">=")
        self.assertEqual(m_gold["condition_spec"]["threshold"], 3000.0)

        m_eth = parse_job_request("alert me when Ethereum climbs above $4k", now_ts=now_ts)
        self.assertEqual(m_eth["condition_spec"]["symbol"], "ETH-USD")
        self.assertEqual(m_eth["condition_spec"]["operator"], ">=")
        self.assertEqual(m_eth["condition_spec"]["threshold"], 4000.0)

        m_sp = parse_job_request("ping me when S&P 500 drops 2%", now_ts=now_ts)
        self.assertEqual(m_sp["condition_spec"]["symbol"], "^GSPC")
        self.assertEqual(m_sp["condition_spec"]["operator"], "pct_down")
        self.assertEqual(m_sp["condition_spec"]["threshold"], 2.0)


class TestConditionEvaluators(unittest.TestCase):
    """Test deterministic and hash-gated condition evaluators."""

    def test_market_condition_evaluator_price_and_pct(self):
        fake_quote = TickerQuote(
            symbol="BTC-USD",
            name="Bitcoin USD",
            exchange="CCC",
            currency="USD",
            market_state="REGULAR",
            price=101250.0,
            change=4200.0,
            change_percent=4.33,
        )
        with patch("src.skills.markets.aggregators.YahooFinanceAggregator.fetch_quotes", return_value={"BTC-USD": fake_quote}):
            hit, msg = evaluate_market_condition({"symbol": "BTC-USD", "operator": ">=", "threshold": 100000})
            self.assertTrue(hit)
            self.assertIn("101,250.00", msg)

            miss, _ = evaluate_market_condition({"symbol": "BTC-USD", "operator": "<=", "threshold": 95000})
            self.assertFalse(miss)

            pct_hit, pct_msg = evaluate_market_condition({"symbol": "BTC-USD", "operator": "pct_move", "threshold": 4.0})
            self.assertTrue(pct_hit)
            self.assertIn("+4.33%", pct_msg)

    def test_weather_condition_evaluator(self):
        sample_weather = "Current in Calgary: -12.5°C, Light Snow and windy."
        with patch("src.skills.weather.aggregators.WeatherAggregatorManager.get_weather", return_value=sample_weather):
            hit_snow, msg_snow = evaluate_weather_condition({"location": "Calgary", "condition_type": "snow"})
            self.assertTrue(hit_snow)
            self.assertIn("Snow", msg_snow)

            hit_temp, msg_temp = evaluate_weather_condition(
                {"location": "Calgary", "condition_type": "temp_below", "threshold_c": -10.0}
            )
            self.assertTrue(hit_temp)
            self.assertIn("-12.5°C", msg_temp)

    def test_youtube_condition_evaluator_baseline_then_new_upload(self):
        xml_v1 = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
          <title>MKBHD</title>
          <entry>
            <yt:videoId>vid_old_111</yt:videoId>
            <title>Old Review Video</title>
          </entry>
        </feed>"""
        xml_v2 = b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
          <title>MKBHD</title>
          <entry>
            <yt:videoId>vid_new_222</yt:videoId>
            <title>Brand New Smartphone Review</title>
          </entry>
        </feed>"""
        spec = {"channel_query": "@MKBHD", "channel_id": "UCBJycsmduvYEL83R_U4JriQ", "last_seen_video_id": None}

        resp1 = MagicMock(status_code=200, content=xml_v1)
        resp2 = MagicMock(status_code=200, content=xml_v2)

        with patch("src.jobs.evaluators.requests.get", side_effect=[resp1, resp1, resp2]):
            # Poll 1: establishes baseline `vid_old_111`, does not alert
            trig1, _ = evaluate_youtube_condition(spec)
            self.assertFalse(trig1)
            self.assertEqual(spec["last_seen_video_id"], "vid_old_111")

            # Poll 2: still `vid_old_111` -> no alert
            trig2, _ = evaluate_youtube_condition(spec)
            self.assertFalse(trig2)

            # Poll 3: new video `vid_new_222` published -> alerts!
            trig3, msg3 = evaluate_youtube_condition(spec)
            self.assertTrue(trig3)
            self.assertIn("Brand New Smartphone Review", msg3)
            self.assertIn("https://www.youtube.com/watch?v=vid_new_222", msg3)

    def test_web_condition_hash_gate_skips_unchanged_content(self):
        spec = {
            "target_url": "https://example.com/status",
            "condition_text": "tickets are now on sale",
            "last_content_hash": None,
        }
        checker_calls = []

        def fake_checker(cond: str, text: str) -> bool:
            checker_calls.append((cond, text))
            return "on sale now" in text.lower()

        with patch("src.tools.web.read_website", side_effect=[
            "Page v1: Coming soon",
            "Page v1: Coming soon",
            "Page v2: Tickets are ON SALE NOW!",
        ]):
            # Poll 1: page fetched, hash saved, checker called once -> False
            t1, _ = evaluate_web_condition(spec, llm_check_fn=fake_checker)
            self.assertFalse(t1)
            self.assertEqual(len(checker_calls), 1)

            # Poll 2: identical page text -> SHA-256 hash gate exits in 0ms without calling checker!
            t2, _ = evaluate_web_condition(spec, llm_check_fn=fake_checker)
            self.assertFalse(t2)
            self.assertEqual(len(checker_calls), 1)

            # Poll 3: page changed -> checker runs and triggers!
            t3, msg3 = evaluate_web_condition(spec, llm_check_fn=fake_checker)
            self.assertTrue(t3)
            self.assertEqual(len(checker_calls), 2)
            self.assertIn("CONDITION TRIGGERED", msg3)


class TestJobEngineLifecycleAndGuardrails(unittest.TestCase):
    """Test JobEngine execution, 3-layer auto-stop guardrails, autoplay song duration scheduling, and persistence."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.jobs_path = os.path.join(self.temp_dir.name, "jobs.json")
        clear_channel_human_activity()

    def tearDown(self):
        self.temp_dir.cleanup()
        clear_channel_human_activity()

    def test_one_shot_reminder_fires_and_removes_itself(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        fired_events = []

        async def on_fire(job, text):
            fired_events.append((job.job_id, text))

        engine.set_fire_callback(on_fire)
        t0 = 1760000000.0
        job, _ = engine.create_user_job(
            channel_id=555,
            query="remind me in 10 minutes to take out the trash",
            creator_id=42,
            creator_name="Halil",
            now_ts=t0,
        )
        self.assertIsNotNone(job)
        self.assertIn(job.job_id, engine.jobs)

        # Before due time -> does not fire
        asyncio.run(engine.tick_once(now=t0 + 300))
        self.assertEqual(len(fired_events), 0)
        self.assertIn(job.job_id, engine.jobs)

        # At due time (+600s) -> fires and removes itself
        asyncio.run(engine.tick_once(now=t0 + 601))
        self.assertEqual(len(fired_events), 1)
        self.assertIn("<@42>", fired_events[0][1])
        self.assertIn("take out the trash", fired_events[0][1])
        self.assertNotIn(job.job_id, engine.jobs)

    def test_quiet_channel_idle_brake_and_human_activity_reset(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        fired_messages = []

        async def on_fire(job, text):
            fired_messages.append(text)

        engine.set_fire_callback(on_fire)
        t0 = 1760000000.0
        job, _ = engine.create_user_job(
            channel_id=777,
            query="play a dnb song every 5 minutes",
            creator_id=99,
            now_ts=t0,
        )
        self.assertEqual(job.max_unacknowledged_fires, 5)

        # Fire 3 times with no human activity -> unacknowledged_fires reaches 3
        for step in range(1, 4):
            asyncio.run(engine.tick_once(now=t0 + step * 300))
        self.assertEqual(job.unacknowledged_fires, 3)

        # Now a human speaks or reacts with 👍 in channel 777 at t0 + 950!
        record_channel_human_activity(777, timestamp=t0 + 950)

        # Next tick resets `unacknowledged_fires` back to 0 (and then increments to 1 on fire #4)
        asyncio.run(engine.tick_once(now=t0 + 4 * 300))
        self.assertEqual(job.unacknowledged_fires, 1)

        # Now 4 more unacknowledged fires (bringing unacknowledged_fires to 5) with no human messages
        for step in range(5, 9):
            asyncio.run(engine.tick_once(now=t0 + step * 300))
        self.assertEqual(job.unacknowledged_fires, 5)
        self.assertIn(job.job_id, engine.jobs)

        # On the next tick, the Quiet-Channel Idle Brake stops the job instead of firing a 6th unacked track!
        asyncio.run(engine.tick_once(now=t0 + 9 * 300))
        self.assertNotIn(job.job_id, engine.jobs)
        self.assertTrue(any("Auto-Stopped Job" in m for m in fired_messages))

    def test_song_length_autoplay_uses_each_track_duration(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        delivered = []

        async def on_fire(job, text):
            delivered.append(text)

        # Track 1 = 195s (3m15s), Track 2 = 264s (4m24s)
        durations = [(
            "**Song:** Afterglow\n**Artist:** Wilkinson\n\nhttps://music.youtube.com/watch?v=11111111111",
            195,
        ), (
            "**Song:** Sub Focus - Solar System\n**Artist:** Sub Focus\n\nhttps://music.youtube.com/watch?v=22222222222",
            264,
        )]
        call_idx = {"i": 0}

        def fake_autoplay_resolver(cid: int, q: str):
            res = durations[call_idx["i"] % len(durations)]
            call_idx["i"] += 1
            return res

        engine.set_fire_callback(on_fire)
        engine.set_autoplay_resolver(fake_autoplay_resolver)

        t0 = 1760000000.0
        job, _ = engine.create_user_job(
            channel_id=888,
            query="autoplay dnb 2 times",
            now_ts=t0,
        )
        self.assertEqual(job.trigger_type, TriggerType.AUTOPLAY)
        self.assertEqual(job.max_runs, 2)

        # Tick 1 at t0: plays Track 1 (195s) and schedules Track 2 at t0 + 195
        asyncio.run(engine.tick_once(now=t0))
        self.assertEqual(len(delivered), 1)
        self.assertIn("Afterglow", delivered[0])
        self.assertEqual(job.next_fire_at, t0 + 195)
        self.assertIn(job.job_id, engine.jobs)

        # Tick at t0 + 100s: Track 1 is still playing! Does not fire early.
        asyncio.run(engine.tick_once(now=t0 + 100))
        self.assertEqual(len(delivered), 1)

        # Tick at t0 + 195s: Track 1 finished! Plays Track 2 and hits max_runs=2 -> auto-removes!
        asyncio.run(engine.tick_once(now=t0 + 195))
        self.assertEqual(len(delivered), 2)
        self.assertIn("Solar System", delivered[1])
        self.assertNotIn(job.job_id, engine.jobs)

    def test_persistence_and_startup_missed_window_recovery(self):
        t0 = time.time()
        engine1 = JobEngine(jobs_file=self.jobs_path)
        # Job 1: Overdue by 5 minutes (< 24h) -> should survive reload and fire with Delayed note
        j_due = UserJob(
            job_id="J-101",
            channel_id=100,
            creator_id=7,
            trigger_type=TriggerType.ONCE_AT,
            action_type=ActionType.REMINDER,
            description="Call supplier",
            payload_text="Call supplier",
            created_at=t0 - 1800,
            next_fire_at=t0 - 300,
            max_runs=1,
        )
        # Job 2: Overdue by 30 hours (> 24h) -> should be pruned on reload
        j_stale = UserJob(
            job_id="J-102",
            channel_id=100,
            creator_id=7,
            trigger_type=TriggerType.ONCE_AT,
            action_type=ActionType.REMINDER,
            description="Very old reminder",
            payload_text="Very old reminder",
            created_at=t0 - 40 * 3600,
            next_fire_at=t0 - 30 * 3600,
            max_runs=1,
        )
        engine1.jobs["J-101"] = j_due
        engine1.jobs["J-102"] = j_stale
        engine1._save_jobs()

        # Reload into a fresh JobEngine instance
        engine2 = JobEngine(jobs_file=self.jobs_path)
        self.assertIn("J-101", engine2.jobs)
        self.assertNotIn("J-102", engine2.jobs)

        fired = []

        async def on_fire(job, text):
            fired.append(text)

        engine2.set_fire_callback(on_fire)
        asyncio.run(engine2.tick_once(now=t0))
        self.assertEqual(len(fired), 1)
        self.assertIn("Delayed", fired[0])
        self.assertNotIn("J-101", engine2.jobs)

    def test_multi_job_concurrency_and_system_vs_user_isolation(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        fired = []

        async def on_fire(job, text):
            fired.append((job.channel_id, job.job_id, text))

        engine.set_fire_callback(on_fire)
        t0 = 1760000000.0

        # Add a locked background system job (simulating Phase 2 Daily Digest)
        sys_job = UserJob(
            job_id="J-SYS1",
            channel_id=111,
            trigger_type=TriggerType.DAILY_SCHEDULE,
            action_type=ActionType.AGENT_TASK,
            description="Morning Daily Digest",
            payload_text="morning_digest",
            next_fire_at=t0 + 3600,
            schedule_spec={"hour": 7, "minute": 30},
            is_system=True,
        )
        engine.jobs["J-SYS1"] = sys_job

        # User cannot cancel the system job from chat
        res_deny = engine.cancel_job(111, "J-SYS1")
        self.assertIn("background system job", res_deny)
        self.assertIn("J-SYS1", engine.jobs)

        # User creates 2 reminders in different channels and 1 market condition watch
        j1, _ = engine.create_user_job(channel_id=111, query="remind me in 5 mins to stretch", creator_id=10, now_ts=t0)
        j2, _ = engine.create_user_job(channel_id=222, query="remind me in 5 mins to check email", creator_id=20, now_ts=t0)
        j3, _ = engine.create_user_job(channel_id=111, query="alert me when NVDA hits $150", creator_id=10, now_ts=t0)

        # Register a mock sports provider
        sports_Polled = {"count": 0}

        async def mock_sports_poll():
            sports_Polled["count"] += 1

        engine.register_provider(
            SystemJobProvider(
                name="sports_tracker",
                interval_seconds=60,
                is_active_fn=lambda: True,
                poll_fn=mock_sports_poll,
                list_channel_fn=lambda cid: ["• **[SPORTS]** **Arsenal 2 - 1 Chelsea** (EPL — 72')"] if cid == 111 else [],
            )
        )

        # Unified listing in channel 111 shows user jobs AND sports tracker, but hides locked system job
        listing_111 = engine.list_channel_jobs(111, now_ts=t0)
        self.assertIn(j1.job_id, listing_111)
        self.assertIn(j3.job_id, listing_111)
        self.assertIn("Arsenal 2 - 1 Chelsea", listing_111)
        self.assertNotIn("J-SYS1", listing_111)

        # Tick at t0 + 301s with NVDA at $155 -> fires j1, j2, j3, and polls sports_tracker!
        fake_nvda = TickerQuote(
            symbol="NVDA",
            name="NVIDIA Corp",
            exchange="NMS",
            currency="USD",
            market_state="REGULAR",
            price=155.0,
            change=6.0,
            change_percent=4.02,
        )
        with patch("src.skills.markets.aggregators.YahooFinanceAggregator.fetch_quotes", return_value={"NVDA": fake_nvda}):
            asyncio.run(engine.tick_once(now=t0 + 301))

        self.assertEqual(sports_Polled["count"], 1)
        fired_ids = {item[1] for item in fired}
        self.assertEqual(fired_ids, {j1.job_id, j2.job_id, j3.job_id})
        # All three one-shot/condition jobs auto-removed after firing, while J-SYS1 remains!
        self.assertNotIn(j1.job_id, engine.jobs)
        self.assertNotIn(j2.job_id, engine.jobs)
        self.assertNotIn(j3.job_id, engine.jobs)
        self.assertIn("J-SYS1", engine.jobs)

    def test_cancellation_by_keyword_autoplay_all_and_channel_cap(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        t0 = 1760000000.0

        # Add a locked system job in channel 111 and a user job in channel 222
        engine.jobs["J-SYS9"] = UserJob(
            job_id="J-SYS9",
            channel_id=111,
            trigger_type=TriggerType.DAILY_SCHEDULE,
            action_type=ActionType.AGENT_TASK,
            description="System Digest",
            payload_text="digest",
            next_fire_at=t0 + 3600,
            is_system=True,
        )
        j_other, _ = engine.create_user_job(channel_id=222, query="remind me in 30m to call Bob", now_ts=t0)

        # Create 3 jobs in channel 111
        j_oven, _ = engine.create_user_job(channel_id=111, query="remind me in 15m to check the oven", now_ts=t0)
        j_auto, _ = engine.create_user_job(channel_id=111, query="autoplay liquid dnb", now_ts=t0)
        j_tea, _ = engine.create_user_job(channel_id=111, query="remind me in 20m to steep tea", now_ts=t0)

        # 1. Cancel by keyword "oven"
        msg_kw = engine.cancel_job(111, "oven")
        self.assertIn(j_oven.job_id, msg_kw)
        self.assertNotIn(j_oven.job_id, engine.jobs)

        # 2. Cancel by "autoplay"
        msg_auto = engine.cancel_job(111, "autoplay")
        self.assertIn(j_auto.job_id, msg_auto)
        self.assertNotIn(j_auto.job_id, engine.jobs)

        # 3. Cancel "all" in channel 111 -> removes j_tea, keeps J-SYS9 (system) and j_other (channel 222)!
        msg_all = engine.cancel_job(111, "all")
        self.assertIn("Cleared **1**", msg_all)
        self.assertNotIn(j_tea.job_id, engine.jobs)
        self.assertIn("J-SYS9", engine.jobs)
        self.assertIn(j_other.job_id, engine.jobs)

        # 4. Per-channel cap (15 jobs max per channel)
        for idx in range(15):
            j_created, _ = engine.create_user_job(
                channel_id=333,
                query=f"remind me in {idx + 5} minutes task {idx}",
                now_ts=t0,
            )
            self.assertIsNotNone(j_created)

        j_over_cap, cap_msg = engine.create_user_job(
            channel_id=333,
            query="remind me in 60 minutes task 16",
            now_ts=t0,
        )
        self.assertIsNone(j_over_cap)
        self.assertIn("already has **15** active jobs", cap_msg)

    def test_ttl_expiration_auto_stops_job_and_notifies_channel(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        fired = []

        async def on_fire(job, text):
            fired.append(text)

        engine.set_fire_callback(on_fire)
        t0 = 1760000000.0
        job, _ = engine.create_user_job(
            channel_id=444,
            query="let me know if TSLA moves 5% today",
            now_ts=t0,
        )
        self.assertEqual(job.expires_at, t0 + MARKET_INTRADAY_MOVE_TTL_SECONDS)

        # Advance past the 24-hour TTL without the condition ever hitting
        asyncio.run(engine.tick_once(now=t0 + MARKET_INTRADAY_MOVE_TTL_SECONDS + 10))
        self.assertNotIn(job.job_id, engine.jobs)
        self.assertEqual(len(fired), 1)
        self.assertIn("Auto-Stopped Job", fired[0])

    def test_sub_minute_anti_spam_guardrail_in_engine_and_confirmation(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        t0 = 1760000000.0

        # Recurring sub-minute request ("every 10 seconds") is clamped to 60s (1m) with guardrail notice
        j_loop, msg_loop = engine.create_user_job(
            channel_id=666,
            query="play a dnb song every 10 seconds",
            now_ts=t0,
        )
        self.assertEqual(j_loop.interval_seconds, 60)
        self.assertEqual(j_loop.next_fire_at, t0 + 60)
        self.assertIn("Anti-Spam Guardrail", msg_loop)
        self.assertIn("1m", msg_loop)

        # One-shot sub-minute request ("in 5 seconds") is clamped to 60s (1m) with guardrail notice
        j_once, msg_once = engine.create_user_job(
            channel_id=666,
            query="remind me in 5 seconds to test spam",
            now_ts=t0,
        )
        self.assertEqual(j_once.next_fire_at, t0 + 60)
        self.assertIn("Anti-Spam Guardrail", msg_once)


class TestSportsTrackerMidGameCutoffFix(unittest.TestCase):
    """Verify the fix for the Sports Tracker 4-hour mid-game cutoff bug."""

    def test_kickoff_resets_timer_and_active_in_play_extends_window(self):
        tracker = SportsTrackerService(poll_interval=25)
        now = time.time()

        # Match tracked 3.5 hours before kickoff (so `created_at` is 3.5h ago while still in 'pre' state)
        snap_pre = MatchSnapshot(
            state="pre",
            completed=False,
            status_name="STATUS_SCHEDULED",
            status_detail="7:00 PM EDT",
            home_team="Edmonton Oilers",
            away_team="Calgary Flames",
            home_score="0",
            away_score="0",
        )
        match = TrackedMatch(
            channel_id=999,
            sport_path="hockey",
            league_code="nhl",
            league_name="NHL",
            event_id="401999",
            home_team="Edmonton Oilers",
            away_team="Calgary Flames",
            last_snapshot=snap_pre,
            created_at=now - (3.5 * 3600),
        )
        tracker.tracked_matches[(999, "401999")] = match

        # Poll 1: Kickoff happens ('pre' -> 'in')! `created_at` must reset to `now`.
        live_p1_payload = {
            "header": {
                "competitions": [{
                    "status": {"type": {"state": "in", "completed": False, "name": "STATUS_IN_PROGRESS", "shortDetail": "1st 19:00"}, "period": 1},
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": "Edmonton Oilers"}, "score": "0"},
                        {"homeAway": "away", "team": {"displayName": "Calgary Flames"}, "score": "0"},
                    ],
                }]
            }
        }
        with patch("src.skills.sports.tracker.fetch_json", return_value=live_p1_payload):
            asyncio.run(tracker.poll_once())

        self.assertIn((999, "401999"), tracker.tracked_matches)
        self.assertGreaterEqual(match.created_at, now - 5)

        # Simulate game reaching 3h 55m into overtime ('in' state with clock advancing)
        match.created_at = time.time() - (SAFETY_TIMEOUT_SECONDS - 120)
        ot_payload = {
            "header": {
                "competitions": [{
                    "status": {"type": {"state": "in", "completed": False, "name": "STATUS_IN_PROGRESS", "shortDetail": "2OT 08:14"}, "period": 5},
                    "competitors": [
                        {"homeAway": "home", "team": {"displayName": "Edmonton Oilers"}, "score": "3"},
                        {"homeAway": "away", "team": {"displayName": "Calgary Flames"}, "score": "3"},
                    ],
                }]
            }
        }
        with patch("src.skills.sports.tracker.fetch_json", return_value=ot_payload):
            asyncio.run(tracker.poll_once())

        # Because the match is actively 'in' progress and advancing, its window is extended so it won't die at 4h!
        self.assertIn((999, "401999"), tracker.tracked_matches)
        self.assertGreater(SAFETY_TIMEOUT_SECONDS - (time.time() - match.created_at), 1800)


class TestOrchestratorAndDispatcherJobIntegration(unittest.TestCase):
    """Verify missed-tool detection, argument normalization, and bang commands for jobs/reminders/autoplay."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.jobs_path = os.path.join(self.tmp_dir.name, "jobs.json")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_detect_missed_job_tool_call(self):
        cases = [
            ("User Halil says: remind me in 15 minutes to check the oven", "create"),
            ("User Halil says: play a dnb song every 5 minutes", "create"),
            ("User Halil says: alert me when BTC hits $100,000", "create"),
            ("User Halil says: autoplay liquid dnb", "autoplay"),
            ("User Halil says: what reminders are active?", "list"),
            ("User Halil says: stop autoplay", "cancel"),
        ]
        for msg, expected_action in cases:
            res = detect_missed_job_tool_call([{"role": "user", "content": msg}])
            self.assertIsNotNone(res, f"Failed to detect job intent for: {msg}")
            self.assertEqual(res[0], "skill_job_manager")
            self.assertEqual(res[1]["action"], expected_action)

        # Sports tracking should NOT be hijacked by detect_missed_job_tool_call
        sports_msg = [{"role": "user", "content": "User Halil says: track the Arsenal vs Chelsea match"}]
        self.assertIsNone(detect_missed_job_tool_call(sports_msg))

    def test_normalize_and_enrich_job_args(self):
        norm = normalize_tool_args(
            "skill_job_manager",
            skill_job_manager,
            {"command": "create", "time": "in 10m", "message": "check tea", "id": "J-105"},
        )
        self.assertEqual(norm["action"], "create")
        self.assertEqual(norm["when"], "in 10m")
        self.assertEqual(norm["task"], "check tea")
        self.assertEqual(norm["job_id"], "J-105")

        enriched = enrich_job_tool_args(
            {"action": "create"},
            [{"role": "user", "content": "Halil: remind me in 5m to stretch"}],
        )
        self.assertEqual(enriched["query"], "remind me in 5m to stretch")

    def test_dispatch_bang_commands(self):
        calls = []

        def fake_job_fn(**kwargs):
            calls.append(kwargs)
            return f"OK:{kwargs.get('action')}"

        res1 = asyncio.run(
            dispatch_tracker_command(
                text="!remind in 10m check oven",
                channel_id=321,
                flight_tracker_fn=lambda **k: "",
                flight_tracker_service=MagicMock(),
                sports_tracker_fn=lambda **k: "",
                sports_tracker_service=MagicMock(),
                job_manager_fn=fake_job_fn,
            )
        )
        self.assertEqual(res1, "OK:create")
        self.assertEqual(calls[-1]["query"], "in 10m check oven")

        res2 = asyncio.run(
            dispatch_tracker_command(
                text="!jobs",
                channel_id=321,
                flight_tracker_fn=lambda **k: "",
                flight_tracker_service=MagicMock(),
                sports_tracker_fn=lambda **k: "",
                sports_tracker_service=MagicMock(),
                job_manager_fn=fake_job_fn,
            )
        )
        self.assertEqual(res2, "OK:list")

        res3 = asyncio.run(
            dispatch_tracker_command(
                text="!autoplay techno",
                channel_id=321,
                flight_tracker_fn=lambda **k: "",
                flight_tracker_service=MagicMock(),
                sports_tracker_fn=lambda **k: "",
                sports_tracker_service=MagicMock(),
                job_manager_fn=fake_job_fn,
            )
        )
        self.assertEqual(res3, "OK:autoplay")

        res4 = asyncio.run(
            dispatch_tracker_command(
                text="!stopautoplay",
                channel_id=321,
                flight_tracker_fn=lambda **k: "",
                flight_tracker_service=MagicMock(),
                sports_tracker_fn=lambda **k: "",
                sports_tracker_service=MagicMock(),
                job_manager_fn=fake_job_fn,
            )
        )
        self.assertEqual(res4, "OK:cancel")

    def test_autoplay_defer_first_tick_prevents_double_card_startup_race(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        delivered = []

        async def on_fire(job, text):
            delivered.append(text)

        engine.set_fire_callback(on_fire)
        engine.set_autoplay_resolver(lambda cid, q: ("Card A", 200))

        t0 = 1760000000.0
        job, _ = engine.create_user_job(
            channel_id=777,
            query="autoplay techno",
            now_ts=t0,
            defer_first_autoplay_tick=True,
        )
        self.assertGreater(job.next_fire_at, t0)
        # Background tick during the Track 1 HTTP resolution window must NOT fire a second card!
        asyncio.run(engine.tick_once(now=t0 + 1.0))
        self.assertEqual(len(delivered), 0)

    def test_single_autoplay_per_channel_updates_in_place_on_genre_switch(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        t0 = 1760000000.0
        job1, msg1 = engine.create_user_job(
            channel_id=777,
            query="autoplay house",
            now_ts=t0,
            defer_first_autoplay_tick=True,
        )
        self.assertIn("MUSIC AUTOPLAY STARTED", msg1)
        self.assertEqual(job1.payload_text, "house")

        # Switching genre in the same channel updates job1 in-place instead of spawning J-102
        job2, msg2 = engine.create_user_job(
            channel_id=777,
            query="autoplay techno",
            now_ts=t0 + 30.0,
            defer_first_autoplay_tick=True,
        )
        self.assertIs(job1, job2)
        self.assertEqual(job2.job_id, job1.job_id)
        self.assertEqual(job2.payload_text, "techno")
        self.assertIn("techno", msg2)
        self.assertEqual(len(engine.jobs), 1)

    def test_autoplay_skip_and_channel_music_takeover_never_creates_text_reminder(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        delivered = []

        async def on_fire(job, text):
            delivered.append((job.trigger_type, job.action_type, text))

        tracks = [
            ("**Song:** Track 1\n**Artist:** Artist 1\n**Genre:** Techno\nhttps://music.youtube.com/watch?v=11111111111", 180),
            ("**Song:** Track 2\n**Artist:** Artist 2\n**Genre:** Techno\nhttps://music.youtube.com/watch?v=22222222222", 210),
            ("**Song:** Track 3\n**Artist:** Artist 3\n**Genre:** Hard Techno\nhttps://music.youtube.com/watch?v=33333333333", 195),
        ]
        idx = {"i": 1}

        def fake_resolver(cid: int, q: str):
            res = tracks[idx["i"] % len(tracks)]
            idx["i"] += 1
            return res

        engine.set_fire_callback(on_fire)
        engine.set_autoplay_resolver(fake_resolver)

        t0 = 1760000000.0
        job, _ = engine.create_user_job(
            channel_id=999,
            query="autoplay techno",
            now_ts=t0,
            defer_first_autoplay_tick=True,
        )
        # Simulate Track 1 resolved at startup (180s)
        job.run_count = 1
        job.next_fire_at = t0 + 180.0

        # 1. User types 'next' at t0 + 40s -> skip_or_update_autoplay resolves Track 2 (210s)
        skipped_job, skip_msg = engine.skip_or_update_autoplay(
            channel_id=999,
            now_ts=t0 + 40.0,
        )
        self.assertIs(skipped_job, job)
        self.assertIn("Track 2", skip_msg)
        self.assertEqual(job.trigger_type, TriggerType.AUTOPLAY)
        self.assertEqual(job.action_type, ActionType.MUSIC_AUTOPLAY)
        self.assertEqual(job.next_fire_at, t0 + 40.0 + 210.0)

        # 2. Even if LLM hallucinates create_user_job("in 3m 30s next track") during active Autoplay,
        # it MUST NOT create a ONCE_AT REMINDER job!
        guard_job, _ = engine.create_user_job(
            channel_id=999,
            query="in 3m 30s next track",
            now_ts=t0 + 41.0,
            defer_first_autoplay_tick=True,
        )
        self.assertIs(guard_job, job)
        self.assertEqual(len(engine.jobs), 1)
        self.assertEqual(job.trigger_type, TriggerType.AUTOPLAY)
        self.assertEqual(job.action_type, ActionType.MUSIC_AUTOPLAY)

        # 3. Channel Takeover: User plays a song / shifts 'harder' / reacts 👍 at t0 + 100s (duration 240s)
        # sync_channel_autoplay_on_track_played resets the Autoplay timer to t0 + 100 + 240s and updates vibe
        with patch("src.skills.youtube.engine.client.get_video_duration_seconds", return_value=240):
            synced_reply = engine.sync_channel_autoplay_on_track_played(
                channel_id=999,
                reply_text="**Song:** Custom Song\n**Artist:** Custom Artist\n**Genre:** Hard Techno\nhttps://music.youtube.com/watch?v=custom12345",
                new_query_hint="hard techno",
                now_ts=t0 + 100.0,
            )
        self.assertIn("📻 **Autoplay", synced_reply)
        self.assertIn("Next track in `4:00`", synced_reply)
        self.assertEqual(job.payload_text, "hard techno")
        self.assertEqual(job.next_fire_at, t0 + 100.0 + 240.0)

        # 4. When the custom song finishes at t0 + 340s, the next background tick plays Track 3 (NOT a text reminder!)
        asyncio.run(engine.tick_once(now=t0 + 340.0))
        self.assertEqual(len(delivered), 1)
        trig_type, act_type, fired_text = delivered[0]
        self.assertEqual(trig_type, TriggerType.AUTOPLAY)
        self.assertEqual(act_type, ActionType.MUSIC_AUTOPLAY)
        self.assertIn("Track 3", fired_text)
        self.assertNotIn("⏰ **Reminder**", fired_text)

    def test_skill_job_manager_end_to_end_autoplay_skip_and_personal_reminder_coexistence(self):
        from src.context import set_active_channel_id
        from src.jobs.engine import JOB_ENGINE

        set_active_channel_id(555)
        with patch.object(JOB_ENGINE, "jobs", {}), patch.object(JOB_ENGINE, "_save_jobs"), patch.object(
            JOB_ENGINE,
            "_autoplay_resolver",
            side_effect=[
                ("📻 **Autoplay (liquid dnb)** — Next track in `3:15`:\n**Song:** Track A\n**Artist:** Artist A\nhttps://music.youtube.com/watch?v=aaaaaaaaaaa", 195),
                ("📻 **Autoplay (liquid dnb)** — Next track in `3:40`:\n**Song:** Track B\n**Artist:** Artist B\nhttps://music.youtube.com/watch?v=bbbbbbbbbbb", 220),
                ("📻 **Autoplay (neurofunk)** — Next track in `4:05`:\n**Song:** Track C\n**Artist:** Artist C\nhttps://music.youtube.com/watch?v=ccccccccccc", 245),
            ],
        ):
            # 1. Start autoplay via skill_job_manager
            out_start = skill_job_manager(action="autoplay", query="liquid dnb")
            self.assertIn("MUSIC AUTOPLAY STARTED", out_start)
            self.assertIn("Track A", out_start)
            ap_job = JOB_ENGINE.get_active_autoplay_job(555)
            self.assertIsNotNone(ap_job)
            self.assertEqual(ap_job.interval_seconds, 195)

            # 2. Explicit skip via action='skip'
            out_skip = skill_job_manager(action="skip")
            self.assertIn("Track B", out_skip)
            self.assertEqual(ap_job.interval_seconds, 220)
            self.assertEqual(ap_job.trigger_type, TriggerType.AUTOPLAY)

            # 3. Hallucinated 'create' with 'next' + genre switch -> intercepted as skip_or_update_autoplay
            out_next = skill_job_manager(action="create", when="in 3m 40s", task="next song neurofunk")
            self.assertIn("Track C", out_next)
            self.assertEqual(ap_job.payload_text, "neurofunk")
            self.assertEqual(ap_job.interval_seconds, 245)
            self.assertEqual(len(JOB_ENGINE.jobs), 1)

            # 4. Personal non-music reminder ('remind me in 15m to check the pizza') coexists without overwriting Autoplay
            out_rem = skill_job_manager(action="create", query="remind me in 15m to check the pizza")
            self.assertIn("REMINDER SET", out_rem)
            self.assertEqual(len(JOB_ENGINE.jobs), 2)
            self.assertIs(JOB_ENGINE.get_active_autoplay_job(555), ap_job)

    def test_autoplay_channel_isolation_and_non_music_reply_passthrough(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        t0 = 1760000000.0
        j_ch1, _ = engine.create_user_job(channel_id=101, query="autoplay techno", now_ts=t0, defer_first_autoplay_tick=True)
        j_ch2, _ = engine.create_user_job(channel_id=202, query="autoplay lofi", now_ts=t0, defer_first_autoplay_tick=True)
        j_ch1.next_fire_at = t0 + 180.0
        j_ch2.next_fire_at = t0 + 200.0

        # Non-music reply in channel 101 must NOT touch the Autoplay timer or add a banner
        weather_reply = "🌤️ Current weather in Calgary: 14°C and clear."
        synced_weather = engine.sync_channel_autoplay_on_track_played(
            channel_id=101,
            reply_text=weather_reply,
            now_ts=t0 + 50.0,
        )
        self.assertEqual(synced_weather, weather_reply)
        self.assertEqual(j_ch1.next_fire_at, t0 + 180.0)

        # Playing a track (or 👍 reaction / spectrum shift) in channel 101 updates ONLY channel 101, leaving channel 202 untouched
        with patch("src.skills.youtube.engine.client.get_video_duration_seconds", return_value=260):
            engine.sync_channel_autoplay_on_track_played(
                channel_id=101,
                reply_text="**Song:** Acid Rain\n**Artist:** Lorn\n**Genre:** Industrial Techno\nhttps://music.youtube.com/watch?v=12345678901",
                now_ts=t0 + 60.0,
            )
        self.assertEqual(j_ch1.payload_text, "Industrial Techno")
        self.assertEqual(j_ch1.next_fire_at, t0 + 60.0 + 260.0)
        self.assertEqual(j_ch2.payload_text, "lofi")
        self.assertEqual(j_ch2.next_fire_at, t0 + 200.0)

    def test_detect_missed_music_shift_tool_call_on_bare_next_or_skip_during_autoplay(self):
        from src.context import set_active_channel_id
        from src.jobs.engine import JOB_ENGINE
        from src.skills.music.context import detect_missed_music_shift_tool_call

        set_active_channel_id(606)
        fake_ap = UserJob(
            job_id="J-101",
            channel_id=606,
            trigger_type=TriggerType.AUTOPLAY,
            action_type=ActionType.MUSIC_AUTOPLAY,
            description="Music Autoplay (synthwave)",
            payload_text="synthwave",
            next_fire_at=time.time() + 180,
        )
        with patch.object(JOB_ENGINE, "get_active_autoplay_job", return_value=fake_ap):
            for phrase in ("next", "skip", "next song", "skip track"):
                res = detect_missed_music_shift_tool_call([{"role": "user", "content": f"User Halil says: {phrase}"}])
                self.assertEqual(res, ("skill_music_discovery", {"genre": "synthwave"}))

    def test_dm_lockdown_allows_only_bot_owner(self):
        import discord
        from unittest.mock import AsyncMock
        import main as bot_main

        dm_channel = MagicMock(spec=discord.DMChannel)
        dm_channel.id = 88888
        dm_channel.send = AsyncMock()

        stranger_msg = MagicMock()
        stranger_msg.author = MagicMock(id=999999, name="stranger", display_name="Stranger")
        stranger_msg.channel = dm_channel
        stranger_msg.content = "!reset"
        stranger_msg.mentions = []

        owner_msg = MagicMock()
        owner_msg.author = MagicMock(id=123456, name="halil", display_name="Halil")
        owner_msg.channel = dm_channel
        owner_msg.content = "!reset"
        owner_msg.mentions = []

        with patch.object(bot_main, "BOT_OWNER_ID", 123456), patch.object(
            bot_main.client._connection, "user", MagicMock(id=11111)
        ):
            # 1. Stranger DM is completely ignored (even for !reset)
            asyncio.run(bot_main.on_message(stranger_msg))
            dm_channel.send.assert_not_awaited()

            # 2. Bot owner DM is allowed through
            asyncio.run(bot_main.on_message(owner_msg))
            dm_channel.send.assert_awaited_once()

    def test_autoplay_genre_switch_intercepts_mistaken_cancel_and_updates_in_place(self):
        from src.context import set_active_channel_id
        from src.jobs.engine import JOB_ENGINE
        from src.skills.jobs.jobs import enrich_job_tool_args

        set_active_channel_id(707)
        with patch.object(JOB_ENGINE, "jobs", {}), patch.object(JOB_ENGINE, "_save_jobs"), patch.object(
            JOB_ENGINE,
            "_autoplay_resolver",
            side_effect=[
                ("📻 **Autoplay (acid techno)** — Next track in `3:30`:\n**Song:** Doppelt\n**Artist:** Serge Geyzel\nhttps://music.youtube.com/watch?v=M9rie5p4smw", 210),
                ("📻 **Autoplay (driving techno)** — Next track in `4:10`:\n**Song:** No Glue Needed\n**Artist:** Blastculture\nhttps://music.youtube.com/watch?v=mC_7olaOie8", 250),
            ],
        ):
            # 1. Start autoplay on 'acid techno'
            out1 = skill_job_manager(action="autoplay", query="acid techno")
            self.assertIn("Doppelt", out1)
            ap_job = JOB_ENGINE.get_active_autoplay_job(707)
            self.assertIsNotNone(ap_job)
            self.assertEqual(ap_job.payload_text, "acid techno")

            # 2. User asks to switch to 'driving techno', and LLM mistakenly calls action='cancel', job_id=ap_job.job_id
            history = [{"role": "user", "content": "User Halil says: switch genre to driving techno"}]
            enriched = enrich_job_tool_args(
                {"action": "cancel", "job_id": ap_job.job_id},
                history,
                active_channel_id=707,
            )
            self.assertEqual(enriched.get("action"), "autoplay")
            self.assertNotIn("job_id", enriched)

            out2 = skill_job_manager(**enriched)
            self.assertIn("No Glue Needed", out2)
            self.assertEqual(ap_job.payload_text, "driving techno")
            self.assertEqual(ap_job.interval_seconds, 250)
            self.assertIs(JOB_ENGINE.get_active_autoplay_job(707), ap_job)

    def test_timeless_genre_query_in_create_user_job_starts_autoplay_instead_of_10m_reminder(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        t0 = 1760000000.0
        # Even when no existing autoplay is running, calling create_user_job(query='driving techno')
        # with no time/duration must create an AUTOPLAY job, NOT a 10-minute ONCE_AT text reminder!
        job, msg = engine.create_user_job(
            channel_id=808,
            query="driving techno",
            now_ts=t0,
            defer_first_autoplay_tick=True,
        )
        self.assertIsNotNone(job)
        self.assertEqual(job.trigger_type, TriggerType.AUTOPLAY)
        self.assertEqual(job.action_type, ActionType.MUSIC_AUTOPLAY)
        self.assertEqual(job.payload_text, "driving techno")
        self.assertIn("MUSIC AUTOPLAY STARTED", msg)

    def test_sync_channel_autoplay_preserves_specific_genre_on_next_and_umbrella_tags(self):
        engine = JobEngine(jobs_file=self.jobs_path)
        t0 = 1760000000.0
        job, _ = engine.create_user_job(
            channel_id=909,
            query="autoplay acid techno",
            now_ts=t0,
            defer_first_autoplay_tick=True,
        )
        with patch("src.skills.youtube.engine.client.get_video_duration_seconds", return_value=215):
            # Saying 'next' when the song card has broad iTunes tag 'Electronic' must NOT overwrite 'acid techno'
            engine.sync_channel_autoplay_on_track_played(
                channel_id=909,
                reply_text="**Song:** Flower Warfare\n**Artist:** Remco Beekwilder\n**Genre:** Electronic\nhttps://music.youtube.com/watch?v=t2hzjZJmc5Y",
                new_query_hint="next",
                now_ts=t0 + 50.0,
            )
        self.assertEqual(job.payload_text, "acid techno")
        self.assertEqual(job.next_fire_at, t0 + 50.0 + 215.0)

    def test_ensure_turn_music_card_preserved_reattaches_omitted_autoplay_card(self):
        from src.skills.music.engine.formatting import ensure_turn_music_card_preserved

        tool_out = (
            "✅ **Scheduled Job `J-105`:** Music Autoplay (dnb)\n\n"
            "📻 **Autoplay (dnb)** — Next track in `3:33`:\n\n"
            "**Song:** Fall 2 Fast\n"
            "**Artist:** Particle\n"
            "**Genre:** Dnb\n\n"
            "https://music.youtube.com/watch?v=bPJKn9EK_vI\n\n"
            "[SYSTEM INSTRUCTION: You MUST include the exact `📻 **Autoplay (...)` header...]"
        )
        turn_history = [
            {"role": "user", "content": "User Jubkill says: autoplay drum and bass"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "skill_job_manager", "arguments": {"action": "autoplay", "query": "dnb"}}}],
            },
            {"role": "tool", "name": "skill_job_manager", "content": tool_out},
        ]
        # LLM writes a conversational intro and forgets to copy the Track #1 song card
        llm_reply = "🎧 Music Autoplay has been started with a Drum & Bass vibe! Enjoying some heavy beats now."
        preserved = ensure_turn_music_card_preserved(llm_reply, turn_history)
        self.assertIn("🎧 Music Autoplay has been started with a Drum & Bass vibe!", preserved)
        self.assertIn("📻 **Autoplay (dnb)** — Next track in `3:33`:", preserved)
        self.assertIn("**Song:** Fall 2 Fast", preserved)
        self.assertIn("**Artist:** Particle", preserved)
        self.assertIn("https://music.youtube.com/watch?v=bPJKn9EK_vI", preserved)
        self.assertNotIn("[SYSTEM INSTRUCTION", preserved)


if __name__ == "__main__":
    unittest.main()
