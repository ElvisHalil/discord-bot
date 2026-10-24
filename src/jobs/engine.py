"""
Unified Background Job, Reminder, Condition & Autoplay Engine (`src/jobs/engine.py`).

Manages:
1. User-created jobs (`is_system=False`):
   - One-shot time reminders & tasks (`TriggerType.ONCE_AT`)
   - Recurring clock schedules (`TriggerType.DAILY_SCHEDULE`)
   - Fixed-interval loops (`TriggerType.INTERVAL`) with 3-Layer Auto-Stop Guardrails
   - Conditional triggers (`TriggerType.CONDITION`: Markets, Weather, YouTube RSS, Web/News)
   - Song-length Music Autoplay queues (`TriggerType.AUTOPLAY`)
2. Registered background providers (`SPORTS_TRACKER`, `FLIGHT_TRACKER`, and Phase 2 `DAILY_DIGEST`).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
import re
import threading
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.context import (
    get_bound_event_loop,
    get_last_channel_human_activity,
    set_active_channel_id,
)
from src.jobs.evaluators import evaluate_job_condition
from src.jobs.models import (
    AUTOPLAY_DEFAULT_TRACK_SECONDS,
    AUTOPLAY_MAX_TRACK_SECONDS,
    AUTOPLAY_MIN_TRACK_SECONDS,
    ActionType,
    ConditionKind,
    MAX_ACTIVE_USER_JOBS_PER_CHANNEL,
    MIN_RECURRING_INTERVAL_SECONDS,
    SystemJobProvider,
    TriggerType,
    UserJob,
)
from src.jobs.parser import (
    _format_interval_short,
    classify_action_type,
    compute_next_schedule_timestamp,
    parse_job_request,
)
from src.utils.timezones import get_local_now
from src.utils.workers import run_in_worker

DEFAULT_JOBS_FILE = os.path.join("data", "jobs.json")

JobFireCallback = Callable[[UserJob, str], Awaitable[None]]


def resolve_autoplay_track_sync(channel_id: int, query: str) -> Tuple[str, int]:
    """
    Resolve the next unplayed song card and its duration in seconds (clamped to 90s..480s)
    for an active Music Autoplay job in `channel_id`.
    """
    set_active_channel_id(channel_id)
    from src.skills.music.aggregators.deezer import DeezerAggregator
    from src.skills.music.music import skill_music_discovery
    from src.skills.music.engine.formatting import format_song_card, get_music_metadata, parse_song_meta
    from src.skills.youtube.engine.client import get_video_duration_seconds
    from src.skills.youtube import skill_youtube_search

    disc_res = skill_music_discovery(genre=query)
    if "NO DIRECT" in disc_res or "Could not find" in disc_res:
        disc_res = skill_music_discovery(similar_to=query)

    verified_url: Optional[str] = None
    song_title: str = ""
    artist_name: str = ""
    genre_label: Optional[str] = query.title() if query else None
    album_title: Optional[str] = None

    if "=== VERIFIED YOUTUBE MUSIC RESULT" in disc_res:
        pre_block = disc_res.split("=== VERIFIED YOUTUBE MUSIC RESULT", 1)[1]
        m_song = re.search(r"(?m)^Song:\s*(.+)$", pre_block)
        m_art = re.search(r"(?m)^Artist:\s*(.+)$", pre_block)
        m_alb = re.search(r"(?m)^Album:\s*(.+)$", pre_block)
        m_gen = re.search(r"(?m)^Genre:\s*(.+)$", pre_block)
        m_url = re.search(
            r"(?m)^Link:\s*(https:\/\/(?:music|www)\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})",
            pre_block,
        )
        if m_url and m_song and m_art:
            verified_url = m_url.group(1).replace("www.youtube.com", "music.youtube.com")
            song_title = m_song.group(1).strip()
            artist_name = m_art.group(1).strip()
            if m_alb:
                album_title = m_alb.group(1).strip()
            if m_gen:
                genre_label = m_gen.group(1).strip()

    if not verified_url:
        candidates: List[str] = []
        for line in disc_res.splitlines():
            s = line.strip()
            if s.startswith("- "):
                candidates.append(s[2:].strip())
        if not candidates:
            candidates = [query]
        for cand in candidates[:5]:
            yt_out = skill_youtube_search(cand, max_results=1, is_music=True)
            m_u = re.search(r"(https:\/\/(?:music|www)\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})", yt_out)
            if m_u:
                verified_url = m_u.group(1).replace("www.youtube.com", "music.youtube.com")
                parsed = parse_song_meta(cand)
                song_title = parsed.get("song") or cand
                artist_name = parsed.get("artist") or "Unknown Artist"
                album_title = parsed.get("album")
                genre_label = parsed.get("genre") or genre_label
                break

    if not verified_url:
        return ("", AUTOPLAY_DEFAULT_TRACK_SECONDS)

    m_vid = re.search(r"watch\?v=([0-9A-Za-z_-]{11})", verified_url)
    vid = m_vid.group(1) if m_vid else ""
    if vid and (not song_title or song_title == query):
        meta = get_music_metadata(vid, fallback_text=f"{song_title} by {artist_name}".strip())
        song_title = meta.get("song") or song_title
        artist_name = meta.get("artist") or artist_name
        album_title = album_title or meta.get("album")
        genre_label = genre_label or meta.get("genre")

    # Resolve track duration in seconds via Deezer or YouTube embed cache
    duration_sec = get_video_duration_seconds(vid) or 0
    if duration_sec <= 0 and song_title:
        try:
            dz_detail = DeezerAggregator().resolve_track_details(song_title, artist_name)
            if dz_detail and dz_detail.get("duration"):
                duration_sec = int(dz_detail["duration"])
                if not album_title and dz_detail.get("album"):
                    album_title = str(dz_detail["album"])
        except Exception:
            pass

    if duration_sec <= 0:
        duration_sec = AUTOPLAY_DEFAULT_TRACK_SECONDS
    clamped_duration = max(AUTOPLAY_MIN_TRACK_SECONDS, min(AUTOPLAY_MAX_TRACK_SECONDS, duration_sec))
    mins, secs = divmod(clamped_duration, 60)

    card = format_song_card(
        song=song_title or "Unknown Track",
        artist=artist_name or "Unknown Artist",
        url=verified_url,
        genre=genre_label,
        album=album_title,
        intro=f"📻 **Autoplay ({query})** — Next track in `{mins}:{secs:02d}`:",
    )
    return (card, clamped_duration)


class JobEngine:
    """
    Central background scheduler managing user reminders, recurring schedules,
    conditional watches, music autoplay queues, and registered domain trackers.
    """

    def __init__(self, jobs_file: str = DEFAULT_JOBS_FILE) -> None:
        self.jobs_file = jobs_file
        self.jobs: Dict[str, UserJob] = {}
        self.providers: Dict[str, SystemJobProvider] = {}
        self._lock = threading.RLock()
        self._seq_counter: int = 100
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None
        self._wake_event: Optional[asyncio.Event] = None
        self._fire_callback: Optional[JobFireCallback] = None
        self._autoplay_resolver: Callable[[int, str], Tuple[str, int]] = resolve_autoplay_track_sync
        self._llm_condition_checker: Optional[Callable[[str, str], bool]] = None
        self._load_jobs()

    def bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._event_loop = loop

    def set_fire_callback(self, callback: JobFireCallback) -> None:
        """Register the async callback in `main.py` that delivers fired reminders/tasks/autoplay to Discord/Telegram."""
        self._fire_callback = callback

    def set_autoplay_resolver(self, resolver_fn: Callable[[int, str], Tuple[str, int]]) -> None:
        """Override the synchronous `(card_text, duration_seconds)` autoplay resolver (useful in tests)."""
        self._autoplay_resolver = resolver_fn

    def set_llm_condition_checker(self, checker_fn: Optional[Callable[[str, str], bool]]) -> None:
        """Register an optional boolean checker `(condition_text, content) -> bool` for open web conditions."""
        self._llm_condition_checker = checker_fn

    def register_provider(self, provider: SystemJobProvider) -> None:
        """Register a domain tracker (`SPORTS_TRACKER`, `FLIGHT_TRACKER`, or future `DAILY_DIGEST`)."""
        with self._lock:
            self.providers[provider.name] = provider
        self.ensure_loop_running()

    def _next_job_id(self) -> str:
        with self._lock:
            self._seq_counter += 1
            while f"J-{self._seq_counter}" in self.jobs:
                self._seq_counter += 1
            return f"J-{self._seq_counter}"

    def _load_jobs(self) -> None:
        if not self.jobs_file or not os.path.exists(self.jobs_file):
            return
        try:
            with open(self.jobs_file, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
            if not isinstance(raw_list, list):
                return
            now_ts = time.time()
            with self._lock:
                for item in raw_list:
                    if not isinstance(item, dict) or not item.get("job_id"):
                        continue
                    job = UserJob.from_dict(item)
                    m_num = re.match(r"^J-(\d+)$", job.job_id)
                    if m_num:
                        self._seq_counter = max(self._seq_counter, int(m_num.group(1)))
                    # Drop expired jobs on startup
                    if job.expires_at is not None and now_ts >= job.expires_at:
                        continue
                    if job.trigger_type == TriggerType.ONCE_AT and job.next_fire_at <= now_ts:
                        # If overdue by more than 24h, drop; otherwise mark for immediate delayed catch-up fire
                        if (now_ts - job.next_fire_at) > 86400:
                            continue
                    elif job.trigger_type == TriggerType.DAILY_SCHEDULE and job.next_fire_at <= now_ts:
                        spec = job.schedule_spec or {}
                        job.next_fire_at = compute_next_schedule_timestamp(
                            int(spec.get("hour", 9)),
                            int(spec.get("minute", 0)),
                            days_of_week=spec.get("days"),
                        )
                    # Upgrade persisted jobs whose payload is a skill/tool execution task (e.g. charting, market brief)
                    if (
                        job.trigger_type in (TriggerType.ONCE_AT, TriggerType.INTERVAL, TriggerType.DAILY_SCHEDULE)
                        and job.action_type == ActionType.REMINDER
                        and classify_action_type(job.payload_text) == ActionType.AGENT_TASK
                    ):
                        job.action_type = ActionType.AGENT_TASK
                    self.jobs[job.job_id] = job
        except Exception as e:
            print(f"⚠️ [Job Engine]: Could not load jobs from {self.jobs_file}: {e}")

    def _save_jobs(self) -> None:
        if not self.jobs_file:
            return
        try:
            parent = os.path.dirname(self.jobs_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with self._lock:
                payload = [j.to_dict() for j in self.jobs.values()]
            tmp_file = f"{self.jobs_file}.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_file, self.jobs_file)
        except Exception as e:
            print(f"⚠️ [Job Engine]: Could not save jobs to {self.jobs_file}: {e}")

    def has_active_work(self) -> bool:
        with self._lock:
            if any(not j.paused for j in self.jobs.values()):
                return True
            for p in self.providers.values():
                try:
                    if p.is_active_fn():
                        return True
                except Exception:
                    continue
        return False

    def wake(self) -> None:
        """Wake the background scheduler loop immediately when a new job or tracker is added."""
        if self._wake_event is not None:
            try:
                self._wake_event.set()
            except Exception:
                pass
        self.ensure_loop_running()

    def ensure_loop_running(self) -> None:
        if not self.has_active_work():
            return
        try:
            loop = asyncio.get_running_loop()
            self._event_loop = loop
            if self._task is None or self._task.done():
                self._wake_event = asyncio.Event()
                self._task = loop.create_task(self._run_loop())
        except RuntimeError:
            target_loop = self._event_loop or get_bound_event_loop()
            if target_loop and target_loop.is_running():
                def _start() -> None:
                    if self._task is None or self._task.done():
                        self._wake_event = asyncio.Event()
                        self._task = target_loop.create_task(self._run_loop())
                target_loop.call_soon_threadsafe(_start)

    def shutdown(self) -> None:
        """Cancel the background scheduler loop cleanly on exit."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        try:
            while self.has_active_work():
                now = time.time()
                next_wake = await self.tick_once(now=now)
                if not self.has_active_work():
                    break
                sleep_for = max(0.5, min(15.0, next_wake - time.time()))
                if self._wake_event is None:
                    self._wake_event = asyncio.Event()
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=sleep_for)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"⚠️ [Job Engine Loop Error]: {e}")
        finally:
            self._task = None

    async def tick_once(self, now: Optional[float] = None) -> float:
        """
        Execute one scheduler cycle across all due `UserJob`s and active `SystemJobProvider`s.
        Returns the recommended UTC epoch timestamp for the next wake-up.
        """
        eff_now = float(now if now is not None else time.time())
        earliest_next = eff_now + 15.0
        dirty = False

        # 1. Run any due SystemJobProviders (e.g. SPORTS_TRACKER, FLIGHT_TRACKER)
        with self._lock:
            active_providers = list(self.providers.values())

        for prov in active_providers:
            try:
                if not prov.is_active_fn():
                    continue
                due_at = prov.last_run_at + prov.interval_seconds
                if eff_now >= due_at:
                    prov.last_run_at = eff_now
                    await prov.poll_fn()
                    earliest_next = min(earliest_next, eff_now + prov.interval_seconds)
                else:
                    earliest_next = min(earliest_next, due_at)
            except Exception as e:
                print(f"⚠️ [Job Engine Provider '{prov.name}' Error]: {e}")

        # 2. Evaluate all UserJobs
        with self._lock:
            job_items = list(self.jobs.items())

        for job_id, job in job_items:
            if job.paused:
                continue

            # Guardrail Layer 1 & 2: Check TTL expiration (`expires_at`) and `max_runs`
            if job.expires_at is not None and eff_now >= job.expires_at:
                with self._lock:
                    self.jobs.pop(job_id, None)
                dirty = True
                exp_msg = (
                    f"⏸️ **Auto-Stopped Job `{job.job_id}` ({job.description})**: "
                    "Reached maximum time window / expiration limit."
                )
                if self._fire_callback is not None:
                    try:
                        await self._fire_callback(job, exp_msg)
                    except Exception as e:
                        print(f"⚠️ [Job Engine Expiration Callback Error]: {e}")
                continue

            if job.max_runs is not None and job.run_count >= job.max_runs:
                with self._lock:
                    self.jobs.pop(job_id, None)
                dirty = True
                continue

            # Guardrail Layer 3: Check Quiet-Channel Idle Brake on recurring interval & autoplay jobs
            if job.trigger_type in (TriggerType.INTERVAL, TriggerType.AUTOPLAY) and job.last_fired_at is not None:
                last_human = get_last_channel_human_activity(job.channel_id)
                if last_human > job.last_fired_at:
                    if job.unacknowledged_fires != 0:
                        job.unacknowledged_fires = 0
                        dirty = True

            if eff_now < job.next_fire_at:
                earliest_next = min(earliest_next, job.next_fire_at)
                continue

            # If Quiet-Channel Idle Brake threshold reached before firing again, auto-stop now!
            if (
                job.trigger_type in (TriggerType.INTERVAL, TriggerType.AUTOPLAY)
                and job.max_unacknowledged_fires > 0
                and job.unacknowledged_fires >= job.max_unacknowledged_fires
            ):
                with self._lock:
                    self.jobs.pop(job_id, None)
                dirty = True
                stop_msg = (
                    f"⏸️ **Auto-Stopped Job `{job.job_id}` ({job.description})**: "
                    f"No channel activity detected for the last {job.unacknowledged_fires} runs."
                )
                if self._fire_callback is not None:
                    try:
                        await self._fire_callback(job, stop_msg)
                    except Exception as e:
                        print(f"⚠️ [Job Engine Idle-Brake Callback Error]: {e}")
                continue

            # Execute or evaluate the due job
            should_remove, next_ts = await self._execute_due_job(job, eff_now)
            dirty = True
            if should_remove:
                with self._lock:
                    self.jobs.pop(job_id, None)
            else:
                job.next_fire_at = next_ts
                earliest_next = min(earliest_next, next_ts)

        if dirty:
            self._save_jobs()

        return earliest_next

    async def _execute_due_job(self, job: UserJob, now_ts: float) -> Tuple[bool, float]:
        """
        Run a single due `UserJob`. Returns `(should_remove_job, next_fire_at)`.
        """
        mention_prefix = f"<@{job.creator_id}> " if job.creator_id > 0 else ""

        # Case A: Conditional Trigger ("when X happens")
        if job.trigger_type == TriggerType.CONDITION:
            spec = job.condition_spec or {}
            triggered, alert_text = await run_in_worker(
                evaluate_job_condition,
                spec,
                self._llm_condition_checker,
            )
            if not triggered:
                poll_iv = max(MIN_RECURRING_INTERVAL_SECONDS, int(job.interval_seconds or 60))
                return (False, now_ts + poll_iv)

            job.run_count += 1
            job.last_fired_at = now_ts
            msg = f"{mention_prefix}{alert_text}" if alert_text else f"{mention_prefix}🔔 **Condition Met:** {job.description}"
            if self._fire_callback is not None:
                try:
                    await self._fire_callback(job, msg)
                except Exception as e:
                    print(f"⚠️ [Job Engine Condition Callback Error]: {e}")

            max_r = job.max_runs if job.max_runs is not None else 1
            if job.run_count >= max_r:
                return (True, now_ts)
            return (False, now_ts + max(60, int(job.interval_seconds or 60)))

        # Case B: Song-Length Music Autoplay (`TriggerType.AUTOPLAY`)
        if job.trigger_type == TriggerType.AUTOPLAY or job.action_type == ActionType.MUSIC_AUTOPLAY:
            auto_spec = job.autoplay_spec or {}
            queue_list: List[str] = list(auto_spec.get("queue") or [])
            if queue_list:
                next_query = queue_list.pop(0)
                auto_spec["queue"] = queue_list
                queue_only = bool(auto_spec.get("queue_only", False))
            else:
                next_query = str(auto_spec.get("query") or job.payload_text or "dnb")
                queue_only = False

            card_text, track_duration = await run_in_worker(
                self._autoplay_resolver,
                job.channel_id,
                next_query,
            )
            job.run_count += 1
            job.unacknowledged_fires += 1
            job.last_fired_at = now_ts
            auto_spec["last_duration"] = track_duration
            job.interval_seconds = track_duration

            if card_text and self._fire_callback is not None:
                try:
                    await self._fire_callback(job, card_text)
                except Exception as e:
                    print(f"⚠️ [Job Engine Autoplay Callback Error]: {e}")

            if (queue_only and not queue_list) or (job.max_runs is not None and job.run_count >= job.max_runs):
                return (True, now_ts)
            if job.expires_at is not None and (now_ts + track_duration) > job.expires_at and job.run_count >= 1:
                # Allow next tick only if before expires_at
                if now_ts >= job.expires_at:
                    return (True, now_ts)
            return (False, now_ts + track_duration)

        # Case C: One-Shot, Interval, or Daily Schedule Reminder / Agent Task
        is_delayed_once = (
            job.trigger_type == TriggerType.ONCE_AT
            and (now_ts - job.next_fire_at) > 90
        )
        delayed_note = " *(Delayed — bot was offline at scheduled time)*" if is_delayed_once else ""

        job.run_count += 1
        job.unacknowledged_fires += 1
        job.last_fired_at = now_ts

        if job.action_type == ActionType.AGENT_TASK:
            # Signal `main.py` callback with `[AGENT_TASK]` prefix or let callback check `job.action_type`
            out_msg = f"{mention_prefix}⏰ **Scheduled Task (`{job.job_id}`):** {job.payload_text}{delayed_note}"
        else:
            out_msg = f"{mention_prefix}⏰ **Reminder (`{job.job_id}`):** {job.payload_text}{delayed_note}"

        if self._fire_callback is not None:
            try:
                await self._fire_callback(job, out_msg)
            except Exception as e:
                print(f"⚠️ [Job Engine Fire Callback Error]: {e}")

        if job.trigger_type == TriggerType.ONCE_AT:
            return (True, now_ts)

        if job.max_runs is not None and job.run_count >= job.max_runs:
            return (True, now_ts)

        if job.trigger_type == TriggerType.INTERVAL:
            iv = max(MIN_RECURRING_INTERVAL_SECONDS, int(job.interval_seconds or 60))
            return (False, now_ts + iv)

        if job.trigger_type == TriggerType.DAILY_SCHEDULE:
            spec = job.schedule_spec or {}
            next_ts = compute_next_schedule_timestamp(
                int(spec.get("hour", 9)),
                int(spec.get("minute", 0)),
                days_of_week=spec.get("days"),
            )
            return (False, next_ts)

        return (True, now_ts)

    def get_active_autoplay_job(self, channel_id: int, platform: Optional[str] = None) -> Optional[UserJob]:
        """Return the single active Music Autoplay job in `channel_id`, if any."""
        with self._lock:
            for j in self.jobs.values():
                if (
                    j.channel_id == channel_id
                    and not j.is_system
                    and not j.paused
                    and (j.trigger_type == TriggerType.AUTOPLAY or j.action_type == ActionType.MUSIC_AUTOPLAY)
                ):
                    return j
        return None

    @staticmethod
    def _clean_autoplay_query(raw_query: str, fallback_query: str = "dnb") -> str:
        """Clean boilerplate skip/autoplay/genre-switch words from a query; return `fallback_query` if only boilerplate words remain."""
        cleaned = re.sub(
            r"\b(?:please|can\s+(?:you|we)|could\s+(?:you|we)|let'?s|lets|start|begin|turn\s+on|enable|"
            r"autoplay|auto-play|auto\s+play|radio(?:\s+mode)?|back[\s\-]+to[\s\-]+back|continuously|"
            r"play|put\s+on|queue\s+up|queue|some|music|songs?|tracks?|tunes?|for\s+(?:me|us)|in\s+this\s+channel|"
            r"next|skip|another|one|switch|change|swap|shift|move|the|genre|vibe|style|mode|over|into|to|instead|now|pls|"
            r"harder|softer|faster|slower|more\s+intense|more\s+chill|heavier|lighter)\b",
            " ",
            raw_query or "",
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(?:for\s+|in\s+|after\s+)?(?:\d+|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen|twenty)\s+"
            r"(?:times|runs|tracks|songs|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        res = " ".join(cleaned.split()).strip(" -–—,.!?")
        return res or fallback_query

    def create_user_job(
        self,
        channel_id: int,
        query: str,
        when: Optional[str] = None,
        task: Optional[str] = None,
        creator_id: int = 0,
        creator_name: str = "",
        now_ts: Optional[float] = None,
        now_dt: Optional[datetime] = None,
        defer_first_autoplay_tick: bool = False,
    ) -> Tuple[Optional[UserJob], str]:
        """
        Parse and register a new user-controllable job in `channel_id`.
        Enforces at most 1 active `TriggerType.AUTOPLAY` job per channel and prevents
        accidental conversion of active Autoplay into a one-shot text reminder (`ONCE_AT`).
        Returns `(created_job_or_None, formatted_confirmation_string)`.
        """
        eff_now = float(now_ts if now_ts is not None else time.time())
        existing_autoplay = self.get_active_autoplay_job(channel_id)
        parsed = parse_job_request(query=query, when=when, task=task, now_ts=eff_now, now_dt=now_dt)

        # If Autoplay is already active in this channel and the LLM called `skill_job_manager(action='create')`
        # for a skip/next/music/genre request (or tried to manually schedule a 'next track' timer),
        # keep it locked onto the active Autoplay job instead of creating a one-shot text reminder!
        combined_req = " ".join(p for p in (query, when, task) if p).strip().lower()
        if existing_autoplay is not None and parsed["trigger_type"] != TriggerType.AUTOPLAY:
            is_explicit_personal_reminder = bool(
                re.search(r"\b(?:remind\s+me|set\s+a\s+reminder|alert\s+me|notify\s+me|ping\s+me)\b", combined_req)
                and not re.search(
                    r"\b(?:autoplay|auto-play|next|skip|song|track|music|genre|vibe|queue|dnb|drum\s+and\s+bass|jungle|techno|house)\b",
                    combined_req,
                )
            )
            is_music_or_skip = bool(
                re.search(
                    r"\b(?:next|skip|another|song|track|music|autoplay|auto-play|radio|queue|play|genre|vibe|"
                    r"faster|slower|harder|softer|dnb|drum\s+and\s+bass|jungle|techno|house|trance|dubstep|ambient|lofi|lo-fi|phonk|synthwave|hip\s*hop|rock|metal|jazz|pop)\b",
                    combined_req,
                )
            )
            if parsed["trigger_type"] != TriggerType.CONDITION and not is_explicit_personal_reminder and is_music_or_skip:
                new_vibe = self._clean_autoplay_query(query or task or "", fallback_query=existing_autoplay.payload_text)
                parsed = {
                    "trigger_type": TriggerType.AUTOPLAY,
                    "action_type": ActionType.MUSIC_AUTOPLAY,
                    "description": f"Music Autoplay ({new_vibe})",
                    "payload_text": new_vibe,
                    "next_fire_at": eff_now,
                    "interval_seconds": existing_autoplay.interval_seconds or AUTOPLAY_DEFAULT_TRACK_SECONDS,
                    "autoplay_spec": {
                        "query": new_vibe,
                        "queue": [],
                        "last_duration": existing_autoplay.interval_seconds or AUTOPLAY_DEFAULT_TRACK_SECONDS,
                    },
                    "max_runs": existing_autoplay.max_runs or 10,
                    "expires_at": existing_autoplay.expires_at or (eff_now + 2700),
                }

        # Single-Autoplay-Per-Channel: update existing Autoplay job in-place if one is already running
        if parsed["trigger_type"] == TriggerType.AUTOPLAY and existing_autoplay is not None:
            initial_next_fire = (
                eff_now + AUTOPLAY_DEFAULT_TRACK_SECONDS
                if defer_first_autoplay_tick
                else float(parsed["next_fire_at"])
            )
            with self._lock:
                # Remove any stray duplicate autoplay jobs in this channel
                for jid, j in list(self.jobs.items()):
                    if (
                        j.channel_id == channel_id
                        and jid != existing_autoplay.job_id
                        and (j.trigger_type == TriggerType.AUTOPLAY or j.action_type == ActionType.MUSIC_AUTOPLAY)
                    ):
                        self.jobs.pop(jid, None)

                existing_autoplay.trigger_type = TriggerType.AUTOPLAY
                existing_autoplay.action_type = ActionType.MUSIC_AUTOPLAY
                existing_autoplay.description = parsed["description"]
                existing_autoplay.payload_text = parsed["payload_text"]
                existing_autoplay.next_fire_at = initial_next_fire
                existing_autoplay.interval_seconds = parsed.get("interval_seconds") or AUTOPLAY_DEFAULT_TRACK_SECONDS
                existing_autoplay.autoplay_spec = parsed.get("autoplay_spec") or {
                    "query": parsed["payload_text"],
                    "queue": [],
                    "last_duration": AUTOPLAY_DEFAULT_TRACK_SECONDS,
                }
                existing_autoplay.max_runs = parsed.get("max_runs") or existing_autoplay.max_runs or 10
                existing_autoplay.expires_at = parsed.get("expires_at") or (eff_now + 2700)
                existing_autoplay.run_count = 0
                existing_autoplay.unacknowledged_fires = 0

            self._save_jobs()
            self.wake()
            return (existing_autoplay, self.format_job_created(existing_autoplay, now_ts=eff_now))

        with self._lock:
            channel_jobs = [j for j in self.jobs.values() if j.channel_id == channel_id and not j.is_system]
            if len(channel_jobs) >= MAX_ACTIVE_USER_JOBS_PER_CHANNEL:
                return (
                    None,
                    f"⚠️ This channel already has **{ len(channel_jobs) }** active jobs/reminders (maximum is {MAX_ACTIVE_USER_JOBS_PER_CHANNEL}). "
                    "Use `!jobs` to view them or `!canceljob <ID>` to clear old ones first.",
                )

        initial_next_fire = (
            eff_now + AUTOPLAY_DEFAULT_TRACK_SECONDS
            if (defer_first_autoplay_tick and parsed["trigger_type"] == TriggerType.AUTOPLAY)
            else float(parsed["next_fire_at"])
        )
        job_id = self._next_job_id()
        job = UserJob(
            job_id=job_id,
            channel_id=channel_id,
            creator_id=creator_id,
            creator_name=creator_name,
            trigger_type=parsed["trigger_type"],
            action_type=parsed["action_type"],
            description=parsed["description"],
            payload_text=parsed["payload_text"],
            created_at=eff_now,
            next_fire_at=initial_next_fire,
            interval_seconds=parsed.get("interval_seconds"),
            schedule_spec=parsed.get("schedule_spec"),
            condition_spec=parsed.get("condition_spec"),
            autoplay_spec=parsed.get("autoplay_spec"),
            max_runs=parsed.get("max_runs"),
            expires_at=parsed.get("expires_at"),
            is_system=False,
        )

        # Prime baseline video ID for YouTube upload watches if possible
        with self._lock:
            self.jobs[job_id] = job
        self._save_jobs()
        self.wake()

        clamped_sub_minute = bool(parsed.get("clamped_sub_minute", False))
        return (job, self.format_job_created(job, now_ts=eff_now, clamped_sub_minute=clamped_sub_minute))

    def skip_or_update_autoplay(
        self,
        channel_id: int,
        new_query: Optional[str] = None,
        now_ts: Optional[float] = None,
    ) -> Tuple[Optional[UserJob], str]:
        """
        Skip to the next track (or switch genre/vibe) on the channel's active Autoplay job,
        resetting the countdown timer to the newly resolved song's duration without losing
        `TriggerType.AUTOPLAY` state.
        """
        job = self.get_active_autoplay_job(channel_id)
        if job is None:
            return (None, "ℹ️ No active Music Autoplay in this channel.")

        eff_now = float(now_ts if now_ts is not None else time.time())
        with self._lock:
            if new_query and new_query.strip():
                cleaned_q = self._clean_autoplay_query(new_query, fallback_query=job.payload_text)
                job.payload_text = cleaned_q
                job.description = f"Music Autoplay ({cleaned_q})"
                if job.autoplay_spec is None:
                    job.autoplay_spec = {"query": cleaned_q, "queue": [], "last_duration": AUTOPLAY_DEFAULT_TRACK_SECONDS}
                else:
                    job.autoplay_spec["query"] = cleaned_q
            # Push `next_fire_at` forward before resolving so the background loop cannot race
            job.next_fire_at = eff_now + AUTOPLAY_DEFAULT_TRACK_SECONDS

        card_text, track_dur = self._autoplay_resolver(channel_id, job.payload_text)
        finish_ts = float(now_ts if now_ts is not None else time.time())
        with self._lock:
            job.trigger_type = TriggerType.AUTOPLAY
            job.action_type = ActionType.MUSIC_AUTOPLAY
            job.run_count += 1
            job.last_fired_at = finish_ts
            job.interval_seconds = track_dur
            if job.autoplay_spec is not None:
                job.autoplay_spec["last_duration"] = track_dur
            job.next_fire_at = finish_ts + track_dur
            job.unacknowledged_fires = 0

        self._save_jobs()
        self.wake()
        return (job, card_text)

    def sync_channel_autoplay_on_track_played(
        self,
        channel_id: int,
        reply_text: str,
        new_query_hint: Optional[str] = None,
        now_ts: Optional[float] = None,
    ) -> str:
        """
        When Autoplay is active in `channel_id`, any song played in the channel (via 'next',
        genre/song request, 'harder'/'faster' shift, or 👍 reaction) updates the active Autoplay
        vibe and resets the countdown timer to the newly played song's duration.
        """
        if not reply_text:
            return reply_text
        job = self.get_active_autoplay_job(channel_id)
        if job is None:
            return reply_text

        # Check if `reply_text` contains a verified song card with a YouTube watch link
        if not re.search(r"(?im)^[^\S\n]*(?:\*\*)?(?:Song|Track):", reply_text):
            return reply_text
        m_vid = re.search(r"(?:v=|youtu\.be/)([0-9A-Za-z_-]{11})", reply_text)
        if not m_vid:
            return reply_text

        vid = m_vid.group(1)
        raw_duration: Optional[int] = None
        try:
            from src.skills.youtube.engine.client import get_video_duration_seconds
            raw_duration = get_video_duration_seconds(vid)
        except Exception:
            pass
        if not raw_duration:
            try:
                from src.skills.music.engine.formatting import MUSIC_METADATA_CACHE
                cached_meta = MUSIC_METADATA_CACHE.get(vid)
                if isinstance(cached_meta, dict) and cached_meta.get("duration"):
                    raw_duration = int(cached_meta["duration"])
            except Exception:
                pass
        if not raw_duration:
            try:
                from src.skills.music.aggregators.deezer import DeezerAggregator
                m_song = re.search(r"(?im)^[^\S\n]*(?:\*\*)?(?:Song|Track):(?:\*\*)?\s*(.+)$", reply_text)
                m_art = re.search(r"(?im)^[^\S\n]*(?:\*\*)?Artist:(?:\*\*)?\s*(.+)$", reply_text)
                if m_song:
                    s_title = re.sub(r"\*+", "", m_song.group(1)).strip()
                    a_name = re.sub(r"\*+", "", m_art.group(1)).strip() if m_art else ""
                    dz_detail = DeezerAggregator().resolve_track_details(s_title, a_name)
                    if dz_detail and dz_detail.get("duration"):
                        raw_duration = int(dz_detail["duration"])
            except Exception:
                pass

        if raw_duration and raw_duration > 0:
            clamped_duration = max(AUTOPLAY_MIN_TRACK_SECONDS, min(AUTOPLAY_MAX_TRACK_SECONDS, int(raw_duration)))
        else:
            clamped_duration = int(job.interval_seconds or AUTOPLAY_DEFAULT_TRACK_SECONDS)

        already_tagged_autoplay = (
            "MUSIC AUTOPLAY STARTED" in reply_text
            or "📻 **Autoplay" in reply_text
        )

        # Extract updated genre/vibe from the user's request or played song card when Autoplay wasn't already updated this turn
        new_vibe: Optional[str] = None
        if not already_tagged_autoplay:
            hint_str = (new_query_hint or "").strip()
            is_shift_request = bool(
                re.search(
                    r"\b(?:harder|softer|faster|slower|more\s+intense|more\s+chill|heavier|lighter|step\s+up|step\s+down)\b",
                    hint_str,
                    re.IGNORECASE,
                )
            )
            cleaned_hint = self._clean_autoplay_query(hint_str, fallback_query="") if hint_str else ""
            is_explicit_skip_only = bool(hint_str and not cleaned_hint and not is_shift_request)
            current_vibe_generic = not job.payload_text or job.payload_text.strip().lower() in ("top tracks", "music", "unknown", "")

            if not is_explicit_skip_only or current_vibe_generic:
                umbrella_tags = {"unknown", "music", "none", "electronic", "dance", "detected", "acoustic dsp", ""}
                if (
                    cleaned_hint
                    and cleaned_hint.lower() not in ("unknown", "music", "song", "track")
                    and " by " not in cleaned_hint.lower()
                    and len(cleaned_hint.split()) <= 4
                ):
                    new_vibe = cleaned_hint

                if not new_vibe:
                    m_gen = re.search(r"(?im)^[^\S\n]*\*\*Genre:\*\*\s*(.+)$", reply_text)
                    if m_gen:
                        cand_gen = m_gen.group(1).strip()
                        if cand_gen.lower() not in umbrella_tags or current_vibe_generic:
                            if cand_gen.lower() not in ("unknown", "music", "none", ""):
                                new_vibe = cand_gen

                if not new_vibe:
                    try:
                        from src.skills.music.context import get_channel_last_played_song
                        last_rec = get_channel_last_played_song(channel_id)
                        if last_rec:
                            rec_genre = str(getattr(last_rec, "genre", "") or "").strip()
                            if rec_genre.lower() not in umbrella_tags or current_vibe_generic:
                                if rec_genre.lower() not in ("unknown", "music", "none", ""):
                                    new_vibe = rec_genre
                    except Exception:
                        pass

        eff_now = float(now_ts if now_ts is not None else time.time())
        with self._lock:
            job.trigger_type = TriggerType.AUTOPLAY
            job.action_type = ActionType.MUSIC_AUTOPLAY
            if new_vibe:
                job.payload_text = new_vibe
                job.description = f"Music Autoplay ({new_vibe})"
                if job.autoplay_spec is None:
                    job.autoplay_spec = {"query": new_vibe, "queue": [], "last_duration": clamped_duration}
                else:
                    job.autoplay_spec["query"] = new_vibe
            if not already_tagged_autoplay:
                job.run_count += 1
            job.last_fired_at = eff_now
            job.interval_seconds = clamped_duration
            if job.autoplay_spec is not None:
                job.autoplay_spec["last_duration"] = clamped_duration
            job.next_fire_at = eff_now + clamped_duration
            job.unacknowledged_fires = 0

        self._save_jobs()
        self.wake()

        if not already_tagged_autoplay:
            mins, secs = divmod(clamped_duration, 60)
            banner = f"📻 **Autoplay (`{job.job_id}` — {job.payload_text})** — Next track in `{mins}:{secs:02d}`:"
            return f"{banner}\n\n{reply_text.lstrip()}"
        return reply_text

    def format_job_created(
        self,
        job: UserJob,
        now_ts: Optional[float] = None,
        clamped_sub_minute: bool = False,
    ) -> str:
        eff_now = float(now_ts if now_ts is not None else time.time())
        delta_s = max(0, int(round(job.next_fire_at - eff_now)))
        clamp_note = (
            "\n• 🛡️ **Anti-Spam Guardrail**: Sub-minute timings (`< 1m`) are clamped to **1m** minimum to prevent channel spam."
            if clamped_sub_minute
            else ""
        )

        if job.trigger_type == TriggerType.AUTOPLAY:
            ttl_mins = max(1, int(round(((job.expires_at or (eff_now + 2700)) - eff_now) / 60)))
            return (
                f"📻 **MUSIC AUTOPLAY STARTED (`{job.job_id}`)**\n"
                f"• **Vibe / Queue**: {job.payload_text}\n"
                f"• **Pacing**: Dynamic per-song duration (`90s`–`480s` based on each track's length)\n"
                f"• **Auto-Stop Guardrail**: Stops automatically after **{job.max_runs} tracks** (~{ttl_mins}m), "
                f"after **{job.max_unacknowledged_fires} tracks** in a quiet channel, or when you type `!stopjob {job.job_id}`."
            )

        if job.trigger_type == TriggerType.INTERVAL:
            iv_str = _format_interval_short(int(job.interval_seconds or 60))
            ttl_str = _format_interval_short(max(60, int(round((job.expires_at or (eff_now + 3600)) - eff_now))))
            mode_label = "Live AI Task" if job.action_type == ActionType.AGENT_TASK else "Recurring Reminder"
            return (
                f"🔁 **RECURRING JOB SCHEDULED (`{job.job_id}`)**\n"
                f"• **Task ({mode_label})**: {job.payload_text}\n"
                f"• **Interval**: Every **{iv_str}** (First run in `{_format_interval_short(delta_s)}`)\n"
                f"• **Auto-Stop Guardrail**: Stops after **{job.max_runs} runs** / **{ttl_str}**, "
                f"or after **{job.max_unacknowledged_fires} runs** with no channel activity. Cancel anytime with `!canceljob {job.job_id}`.{clamp_note}"
            )

        if job.trigger_type == TriggerType.DAILY_SCHEDULE:
            spec = job.schedule_spec or {}
            hr, mn = int(spec.get("hour", 9)), int(spec.get("minute", 0))
            days = spec.get("days")
            days_label = "Every day" if not days else f"Days {days}"
            return (
                f"🗓️ **SCHEDULED ROUTINE CREATED (`{job.job_id}`)**\n"
                f"• **Task**: {job.payload_text}\n"
                f"• **Schedule**: {days_label} at **{hr:02d}:{mn:02d}** local time (Next run in `{_format_interval_short(delta_s)}`)\n"
                f"• **Manage**: View with `!jobs` or cancel with `!canceljob {job.job_id}`."
            )

        if job.trigger_type == TriggerType.CONDITION:
            iv_str = _format_interval_short(int(job.interval_seconds or 60))
            ttl_days = max(1, int(round(((job.expires_at or (eff_now + 86400)) - eff_now) / 3600)))
            ttl_label = f"{ttl_days // 24}d" if ttl_days >= 48 else f"{ttl_days}h"
            return (
                f"🎯 **CONDITION WATCH CREATED (`{job.job_id}`)**\n"
                f"• **Trigger**: **{job.description}**\n"
                f"• **Check Frequency**: Every **{iv_str}** (Auto-completes once triggered | Expires after **{ttl_label}** if unmet)\n"
                f"• **Manage**: View with `!jobs` or cancel with `!canceljob {job.job_id}`."
            )

        # Default: ONCE_AT
        when_str = _format_interval_short(delta_s) if delta_s > 0 else "immediately"
        mode_label = "Scheduled Task" if job.action_type == ActionType.AGENT_TASK else "Reminder"
        return (
            f"⏰ **{mode_label.upper()} SET (`{job.job_id}`)**\n"
            f"• **What**: {job.payload_text}\n"
            f"• **When**: Fires in **{when_str}**\n"
            f"• **Cancel**: Type `!canceljob {job.job_id}` anytime.{clamp_note}"
        )

    def cancel_job(self, channel_id: int, query_or_id: str = "") -> str:
        """Cancel a specific user job by ID (`J-101`), keyword (`dnb`, `btc`), or `'all'` in `channel_id`."""
        q = (query_or_id or "").strip()
        q_low = q.lower()

        with self._lock:
            # Check if the user tried to target a system-locked job
            for j in self.jobs.values():
                if j.is_system and q and (q.upper() == j.job_id.upper() or q_low in j.description.lower()):
                    return f"🔒 Job `{j.job_id}` ({j.description}) is a background system job and cannot be modified from chat."

            channel_user_jobs = [j for j in self.jobs.values() if j.channel_id == channel_id and not j.is_system]

            if q_low in ("all", "*", "everything", "clear"):
                removed_count = len(channel_user_jobs)
                for j in channel_user_jobs:
                    self.jobs.pop(j.job_id, None)
                if removed_count:
                    self._save_jobs()
                return f"🧹 Cleared **{removed_count}** active job(s)/reminder(s) in this channel."

            # If query is empty or asks to stop autoplay specifically
            if not q or q_low in ("autoplay", "auto-play", "radio", "music"):
                autoplay_jobs = [j for j in channel_user_jobs if j.trigger_type == TriggerType.AUTOPLAY or "song" in j.payload_text.lower() or "play" in j.payload_text.lower()]
                target_list = autoplay_jobs if (q_low in ("autoplay", "auto-play", "radio", "music") and autoplay_jobs) else (channel_user_jobs[-1:] if not q else [])
                if target_list:
                    removed_labels = []
                    for j in target_list:
                        self.jobs.pop(j.job_id, None)
                        removed_labels.append(f"`{j.job_id}` ({j.description})")
                    self._save_jobs()
                    return "🛑 Stopped: " + ", ".join(removed_labels)

            # Match by exact Job ID (e.g. "J-101" or "101")
            norm_id = q.upper() if q.upper().startswith("J-") else f"J-{q}"
            for j in channel_user_jobs:
                if j.job_id.upper() == q.upper() or j.job_id.upper() == norm_id:
                    self.jobs.pop(j.job_id, None)
                    self._save_jobs()
                    return f"🛑 Cancelled job **`{j.job_id}`** ({j.description})."

            # Match by keyword in description or payload
            matched = [
                j
                for j in channel_user_jobs
                if q_low in j.description.lower() or q_low in j.payload_text.lower()
            ]
            if matched:
                removed_labels = []
                for j in matched:
                    self.jobs.pop(j.job_id, None)
                    removed_labels.append(f"`{j.job_id}` ({j.description})")
                self._save_jobs()
                return "🛑 Cancelled: " + ", ".join(removed_labels)

        return f"ℹ️ No active reminder or job matching `{query_or_id}` was found in this channel."

    def list_channel_jobs(self, channel_id: int, now_ts: Optional[float] = None) -> str:
        """Return a unified overview of all active reminders, schedules, condition watches, autoplay, and sports/flight trackers in `channel_id`."""
        eff_now = float(now_ts if now_ts is not None else time.time())
        with self._lock:
            user_jobs = [
                j
                for j in self.jobs.values()
                if j.channel_id == channel_id and not j.is_system and not j.paused
            ]
            providers = list(self.providers.values())

        provider_lines: List[str] = []
        for prov in providers:
            if prov.is_system_locked or prov.list_channel_fn is None:
                continue
            try:
                lines = prov.list_channel_fn(channel_id)
                if lines:
                    provider_lines.extend(lines)
            except Exception:
                continue

        if not user_jobs and not provider_lines:
            return "ℹ️ There are currently no active reminders, condition watches, autoplay queues, or live trackers in this channel."

        out = ["=== ACTIVE JOBS, REMINDERS & TRACKERS IN THIS CHANNEL ==="]
        if user_jobs:
            out.append("\n⏰ **Reminders, Schedules, Watches & Autoplay**:")
            for j in user_jobs:
                rem_s = max(0, int(round(j.next_fire_at - eff_now)))
                next_str = _format_interval_short(rem_s)
                runs_str = f" | Runs: {j.run_count}/{j.max_runs}" if j.max_runs else (f" | Runs: {j.run_count}" if j.run_count else "")
                out.append(
                    f"• **`{j.job_id}`** [{j.trigger_type.upper()}] — **{j.description}** "
                    f"(Next check/fire in `{next_str}`{runs_str})"
                )

        if provider_lines:
            out.append("\n📡 **Active Domain Trackers (Sports & Flights)**:")
            out.extend(provider_lines)

        return "\n".join(out)


JOB_ENGINE = JobEngine()
