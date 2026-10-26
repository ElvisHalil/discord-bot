"""
User-Facing Reminder, Schedule, Conditional Trigger & Music Autoplay Skill (`src/skills/jobs/jobs.py`).
"""

import re
import time
from typing import Optional, Tuple

from src.context import (
    get_active_channel_id,
    get_active_user_id,
    get_active_user_name,
)
from src.jobs.engine import JOB_ENGINE
from src.jobs.models import TriggerType


_LIST_INTENT_RE = re.compile(
    r"^\s*(?:list|show|view|check|what\s+are)\s+(?:me\s+)?(?:all\s+)?(?:my\s+|our\s+|the\s+)?(?:active\s+|current\s+|running\s+|scheduled\s+)?(?:reminders?|jobs?|schedules?|scheduled\s+tasks?|watches|timers?|autoplay|tracked)\s*[?.!]*$"
    r"|^\s*what\s+(?:reminders?|jobs?|schedules?|scheduled\s+tasks?|watches|timers?|trackers?)\s+(?:are|do\s+(?:i|we)\s+have|is)\b"
    r"|^\s*(?:my|active|current)\s+(?:reminders?|jobs?|timers?|watches|schedules?|scheduled\s+tasks?)\s*[?.!]*$",
    re.IGNORECASE,
)

_CANCEL_INTENT_RE = re.compile(
    r"^\s*(?:cancel|stop|remove|delete|clear|unremind|end|kill|turn\s+off|shut\s+off|disable)\b"
    r"(?:\s+(?:the\s+|my\s+)?(?:reminder|job|schedule|timer|watch|autoplay|auto-play|radio|loop))?\s*(.*)$",
    re.IGNORECASE,
)

_USER_EXPLICIT_STOP_RE = re.compile(
    r"\b(?:cancel|stop|remove|delete|clear|unremind|end|kill|turn\s+off|shut\s+off|disable|pause|quit|enough|no\s+more)\b",
    re.IGNORECASE,
)

_SKIP_AUTOPLAY_RE = re.compile(
    r"^\s*(?:next|skip|next\s+(?:song|track|one)|skip\s+(?:song|track|this|it)|another\s+(?:song|track|one)|play\s+next(?:\s+song|\s+track)?)\s*[.!?]*$",
    re.IGNORECASE,
)

_SWITCH_AUTOPLAY_GENRE_RE = re.compile(
    r"\b(?:switch|change|swap)\s+(?:the\s+)?(?:(?:autoplay|auto-play|radio|music|genre|vibe|style)\s+)?(?:to|into|over\s+to)\s+([a-z0-9\s&'-]{2,40})"
    r"|\b(?:switch|change|swap)\s+(?:the\s+)?(?:autoplay|auto-play|radio|genre|vibe)\s+([a-z0-9\s&'-]{2,40})",
    re.IGNORECASE,
)

_AUTOPLAY_META_OR_NEGATION_RE = re.compile(
    r"(?:"
    r"\b(?:who|why|what|how|when|did\s+i|did\s+you)\b.*\bauto[-\s]?play\b"
    r"|\b(?:don['’]?t|do\s+not|never|no|without|didn['’]?t|did\s+not|not)\s+(?:start\s+|turn\s+on\s+|enable\s+|activate\s+|do\s+|use\s+)?auto[-\s]?play\b"
    r"|\btold\s+you\s+to\s+auto[-\s]?play\b"
    r"|\basked\s+(?:you\s+)?(?:to\s+|for\s+)?auto[-\s]?play\b"
    r")",
    re.IGNORECASE,
)

_EXPLICIT_AUTOPLAY_START_RE = re.compile(
    r"\b(?:autoplay|auto-play|auto\s+play|radio\s+mode|play\s+.*?\b(?:back[\s\-]+to[\s\-]+back|continuously|non[-\s]?stop))\b",
    re.IGNORECASE,
)


def is_autoplay_meta_or_negation(raw_text: str) -> bool:
    """Return True if the user message is questioning, complaining about, or negating Autoplay (e.g. 'who told you to autoplay?')."""
    user_text, _ = _extract_user_and_reply_card(raw_text or "")
    return bool(user_text and _AUTOPLAY_META_OR_NEGATION_RE.search(user_text))


def has_explicit_autoplay_start_intent(raw_text: str) -> bool:
    """Return True ONLY when the user explicitly requests starting/running Autoplay or continuous back-to-back playback."""
    user_text, _ = _extract_user_and_reply_card(raw_text or "")
    if not user_text:
        return False
    if _AUTOPLAY_META_OR_NEGATION_RE.search(user_text) or _USER_EXPLICIT_STOP_RE.search(user_text):
        return False
    return bool(_EXPLICIT_AUTOPLAY_START_RE.search(user_text))

_SPEAKER_PREFIX_RE = re.compile(
    r"^(?:User\s+.+?\s+says(?:\s*\[.*?\])*|.+\(User ID:\s*\d+\)(?:\s*\[.*?\])*|\[Replying to[^\]]*\])\s*:\s*(.+)$",
    flags=re.DOTALL | re.IGNORECASE,
)

_REPLY_SONG_CARD_RE = re.compile(
    r"\[Replying to song card:\s*'([^']+)'\s*by\s*'([^']+)'\]",
    re.IGNORECASE,
)


def _extract_user_and_reply_card(raw_content: str) -> Tuple[str, Optional[Tuple[str, str]]]:
    """Strip speaker/reply prefixes cleanly and extract any replied-to song card ('Song', 'Artist') or replied-to YouTube URL."""
    text = (raw_content or "").strip()
    m_reply = _REPLY_SONG_CARD_RE.search(text)
    reply_seed = (m_reply.group(1).strip(), m_reply.group(2).strip()) if m_reply else None
    m_pref = _SPEAKER_PREFIX_RE.match(text)
    if m_pref:
        user_text = m_pref.group(1).strip()
    elif ": " in text:
        user_text = text.split(": ", 1)[1].strip()
    else:
        user_text = text
    user_text = re.sub(r"\[Replying to[^\]]*\]", " ", user_text, flags=re.IGNORECASE).strip()
    user_text = " ".join(user_text.split())
    if "http" not in user_text.lower() and ("youtube.com/watch" in text.lower() or "youtu.be/" in text.lower()):
        m_yt = re.search(r"https?://(?:music\.|www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[0-9A-Za-z_-]{11}\S*", text)
        if m_yt:
            yt_clean = m_yt.group(0).rstrip("\"'\\]")
            user_text = f"{user_text} {yt_clean}".strip()
    return user_text, reply_seed

_AUTOPLAY_DIRECT_INSTRUCTION = (
    "\n\n[SYSTEM INSTRUCTION: You MUST include the exact `📻 **Autoplay (...)` header and the full "
    "`**Song:** / **Artist:** / https://music.youtube.com/watch?v=...` card above in your reply so Track #1 "
    "plays immediately. Do NOT omit the song card or URL, and do NOT call skill_music_discovery, skill_youtube_search, or read_youtube_video.]"
)


def skill_job_manager(
    action: Optional[str] = "create",
    query: Optional[str] = None,
    when: Optional[str] = None,
    task: Optional[str] = None,
    job_id: Optional[str] = None,
) -> str:
    """
    Create, list, or cancel reminders, recurring schedules/intervals ('every 5 mins', 'every day at 8am'),
    conditional triggers ('remind me when BTC hits $100k', 'alert me when it snows in Calgary',
    'let me know when @Channel uploads a video'), or song-length music autoplay ('autoplay dnb').

    Args:
        action: One of 'create' (default), 'autoplay', 'list' / 'status', 'cancel' / 'stop', or 'clear'.
        query: Full natural-language request (e.g. 'remind me in 20 mins to check the oven',
               'play a dnb song every 5 mins', 'alert me when NVDA drops below $120', 'autoplay liquid dnb').
        when: Optional time, interval, or trigger condition (e.g. 'in 20 mins', 'tomorrow at 9am', 'every 2 hours', 'when BTC > 100000').
        task: Optional task or reminder message (e.g. 'check the oven', 'play a dnb song').
        job_id: Optional job ID (e.g. 'J-101') when cancelling a specific job.
    """
    act = (action or "create").strip().lower()
    q_str = (query or "").strip()
    channel_id = get_active_channel_id()
    user_id = get_active_user_id()
    user_name = get_active_user_name()

    print(
        f"⏰ [Executing Job Manager Skill] Action: {act}, Query: '{q_str}', "
        f"When: '{when}', Task: '{task}', JobID: '{job_id}'"
    )

    if act in ("list", "status", "show", "active") or (
        not q_str and not when and not task and not job_id and act == "create"
    ):
        return JOB_ENGINE.list_channel_jobs(channel_id)

    if act in ("clear", "reset", "cancel_all", "stop_all"):
        return JOB_ENGINE.cancel_job(channel_id, "all")

    if act in ("cancel", "stop", "remove", "delete", "unremind"):
        target = (job_id or q_str or task or when or "").strip()
        return JOB_ENGINE.cancel_job(channel_id, target)

    # Detect natural-language list/cancel intents inside `query`
    combined = " ".join(p for p in (q_str, when, task) if p).strip()
    if not when and not task and not job_id and _LIST_INTENT_RE.search(q_str):
        return JOB_ENGINE.list_channel_jobs(channel_id)

    m_cancel = _CANCEL_INTENT_RE.match(combined)
    if m_cancel and not when:
        target = (job_id or m_cancel.group(1) or "").strip()
        return JOB_ENGINE.cancel_job(channel_id, target or "autoplay")

    existing_autoplay = JOB_ENGINE.get_active_autoplay_job(channel_id)
    if act in ("skip", "next") or (existing_autoplay is not None and _SKIP_AUTOPLAY_RE.match(combined)):
        if existing_autoplay is not None:
            _, card_text = JOB_ENGINE.skip_or_update_autoplay(channel_id, new_query=q_str or task)
            return f"{card_text}{_AUTOPLAY_DIRECT_INSTRUCTION}"

    if existing_autoplay is not None and _SWITCH_AUTOPLAY_GENRE_RE.search(combined) and not when:
        act = "autoplay"

    if act == "autoplay" and "autoplay" not in combined.lower():
        q_str = f"autoplay {q_str or task or 'dnb'}".strip()

    job, confirm_msg = JOB_ENGINE.create_user_job(
        channel_id=channel_id,
        query=q_str,
        when=when,
        task=task,
        creator_id=user_id,
        creator_name=user_name,
        defer_first_autoplay_tick=True,
    )
    if job is None:
        return confirm_msg

    # For Song-Length Music Autoplay, immediately serve Track 1 and schedule Track 2 at `now + track1_duration`
    if job.trigger_type == TriggerType.AUTOPLAY:
        try:
            card_text, track_dur = JOB_ENGINE._autoplay_resolver(channel_id, job.payload_text)
            if card_text:
                now_ts = time.time()
                with JOB_ENGINE._lock:
                    job.run_count = 1
                    job.last_fired_at = now_ts
                    job.interval_seconds = track_dur
                    if job.autoplay_spec is not None:
                        job.autoplay_spec["last_duration"] = track_dur
                    job.next_fire_at = now_ts + track_dur
                JOB_ENGINE._save_jobs()
                JOB_ENGINE.wake()
                return f"{confirm_msg}\n\n{card_text}{_AUTOPLAY_DIRECT_INSTRUCTION}"
        except Exception as e:
            print(f"⚠️ [Job Manager Autoplay Initial Track Error]: {e}")

    return confirm_msg


_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:what|why|how|who|where|when\s+(?:is|was|did|does|do|will)|did|was|were|is|are|do|does)\b",
    re.IGNORECASE,
)

_NOUN_SCHEDULE_RE = re.compile(
    r"\b(?:game|match|flight|season|playoff|tournament|tv|broadcast|nhl|nba|nfl|mlb|f1|ufc|epl|ucl|bus|train|transit|class|exam)\s+schedules?\b"
    r"|\bschedule\s+(?:for|of)\s+(?:the\s+)?(?:nhl|nba|nfl|mlb|f1|ufc|epl|ucl|oiler|flames|leafs|canucks|jets|senators|habs|canadiens|game|match|season|playoffs?|tournament|flight)\b",
    re.IGNORECASE,
)


def detect_missed_job_tool_call(
    history: list,
    active_channel_id: Optional[int] = None,
) -> Optional[Tuple[str, dict]]:
    """
    Turn-0 safety net: detect when a user explicitly asks to set a reminder, recurring interval loop,
    market/weather/youtube condition watch, or music autoplay, or asks to list/cancel active jobs,
    and the model failed to invoke `skill_job_manager`.
    """
    if not history:
        return None
    last_msg = history[-1]
    role = last_msg.get("role") if isinstance(last_msg, dict) else getattr(last_msg, "role", None)
    if role != "user":
        for m in reversed(history):
            r = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            if r == "user":
                last_msg = m
                break
        else:
            return None

    raw_content = str((last_msg.get("content") if isinstance(last_msg, dict) else getattr(last_msg, "content", "")) or "")
    # Never trigger job creation recursively inside a background scheduled task execution turn!
    if raw_content.strip().startswith("Execute scheduled task:"):
        return None
    user_text, reply_seed = _extract_user_and_reply_card(raw_content)

    if not user_text:
        return None
    low = user_text.lower()

    # Do not hijack explicit sports or flight tracking requests
    if re.search(r"\b(?:track|untrack)\b.*\b(?:flight|game|match|series|vs|v\.)\b", low):
        return None
    if re.search(r"\b(?:flights?|airfare|airlines?)\b", low) and not re.search(r"\b(?:remind\s+me\s+in|in\s+\d+\s*(?:m|min|mins|minute|minutes|h|hr|hrs|hour|hours))\b", low):
        return None

    # 1. List reminders / jobs
    if _LIST_INTENT_RE.search(low):
        return ("skill_job_manager", {"action": "list", "query": user_text})

    # 2. Cancel / stop reminder, job, or autoplay
    if re.search(
        r"\b(?:cancel|stop|remove|delete|clear|unremind|turn\s+off|kill|disable)\s+(?:the\s+|my\s+|all\s+)?(?:reminders?|jobs?|schedules?|scheduled\s+(?:tasks?|jobs?)|timers?|watches|autoplay|auto-play|radio|J-\d+)\b",
        low,
        re.IGNORECASE,
    ):
        return ("skill_job_manager", {"action": "cancel", "query": user_text})

    # 3. Music Autoplay ("autoplay dnb", "start techno radio mode") or Skip/Next/Genre-Switch while Autoplay is active
    if _AUTOPLAY_META_OR_NEGATION_RE.search(user_text):
        return None
    if has_explicit_autoplay_start_intent(user_text):
        auto_q = user_text
        if reply_seed and "http" not in low:
            auto_q = f"autoplay similar to {reply_seed[0]} by {reply_seed[1]}"
        return ("skill_job_manager", {"action": "autoplay", "query": auto_q})
    if active_channel_id is not None and JOB_ENGINE.get_active_autoplay_job(active_channel_id) is not None:
        if _SKIP_AUTOPLAY_RE.match(low):
            return ("skill_job_manager", {"action": "skip", "query": user_text})
        if _SWITCH_AUTOPLAY_GENRE_RE.search(low) and not _USER_EXPLICIT_STOP_RE.search(low):
            return ("skill_job_manager", {"action": "autoplay", "query": user_text})

    # 4. Conditional trigger ("alert/notify/remind/let me know/tell/ping/message me when/if BTC hits...", "when it snows in..., alert me")
    if re.search(
        r"\b(?:alert\s+(?:me|us)|notify\s+(?:me|us)|let\s+(?:me|us)\s+know|ping\s+(?:me|us)|remind\s+(?:me|us)|tell\s+(?:me|us)|message\s+(?:me|us)|dm\s+(?:me|us))\s+(?:when|whenever|if|as\s+soon\s+as|once)\b"
        r"|^\s*(?:please\s+)?(?:when|whenever|if|as\s+soon\s+as|once)\s+.+?\b(?:alert\s+(?:me|us)|notify\s+(?:me|us)|let\s+(?:me|us)\s+know|ping\s+(?:me|us)|remind\s+(?:me|us)|tell\s+(?:me|us)|message\s+(?:me|us)|send\s+(?:me|us))\b",
        low,
    ):
        return ("skill_job_manager", {"action": "create", "query": user_text})

    # 5. Explicit reminder, timer, alarm, schedule, relative timer ("in 10m..."), or recurring interval/clock schedule
    is_info_question = bool(_QUESTION_PREFIX_RE.search(low) and not re.search(r"\b(?:remind\s+me|set\s+an?\s+(?:reminder|timer|alarm))\b", low))
    if not is_info_question and not _NOUN_SCHEDULE_RE.search(low):
        if re.search(
            r"\b(?:remind\s+(?:me|us|the\s+channel|<@!?\d+>|\@\S+)|set\s+(?:up\s+)?an?\s+(?:reminder|timer|alarm)|create\s+an?\s+(?:reminder|timer|alarm|job|scheduled\s+task))\b"
            r"|\bschedule\s+(?:an?\s+|my\s+|this\s+|that\s+)?(?:reminder|timer|alarm|job|task|chart|report|alert|watch|check|update|summary|digest|recap|message|ping|daily|weekly|hourly|recurring)\b"
            r"|^\s*(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+)?schedule\s+.+?\b(?:at|in|every|for|tomorrow|tonight|daily)\b"
            r"|\bevery\s+(?:(?:\d+(?:\.\d+)?|a|an|one|two|couple(?:\s+of)?|three|few|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty-five|half\s+an?)\s+)?(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|mornings?|afternoons?|evenings?|nights?|weekdays?|weekends?|mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?)\b"
            r"|\b(?:daily|every\s+day|tomorrow|tonight)\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?\b"
            r"|^\s*(?:please\s+|can\s+you\s+|could\s+you\s+)?in\s+(?:\d+(?:\.\d+)?|a|an|one|two|couple(?:\s+of)?|three|few|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty-five|half\s+an?)\s+(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\s+(?:remind|tell|ping|alert|notify|message|dm|send|post|check|play|run|create|plot|graph|chart|give|show)\b"
            r"|\b(?:remind|tell\s+me|ping\s+me|alert\s+me|notify\s+me|message\s+me|dm\s+me|send\s+me)\b.+\bin\s+(?:\d+(?:\.\d+)?|a|an|one|two|couple(?:\s+of)?|three|few|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty-five|half\s+an?)\s+(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
            low,
        ):
            return ("skill_job_manager", {"action": "create", "query": user_text})

    return None


def enrich_job_tool_args(
    args: dict,
    history: list,
    active_channel_id: Optional[int] = None,
) -> dict:
    """
    Ensure `skill_job_manager` receives the full user request in `query` if omitted by the LLM,
    and prevent the LLM from mistakenly cancelling an active Autoplay session or dropping the
    `autoplay` action when the user asks to switch genres.
    """
    enriched = dict(args or {})
    user_text = ""
    reply_seed: Optional[Tuple[str, str]] = None
    for m in reversed(history or []):
        r = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if r == "user":
            raw_c = str((m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or "")
            user_text, reply_seed = _extract_user_and_reply_card(raw_c)
            break

    act = str(enriched.get("action") or "create").strip().lower()
    if not enriched.get("query") and user_text:
        if act not in ("cancel", "stop", "remove", "delete", "unremind", "clear", "reset", "cancel_all", "stop_all") or (
            not enriched.get("when") and not enriched.get("task") and not enriched.get("job_id")
        ):
            enriched["query"] = user_text

    if not user_text:
        return enriched

    cid = active_channel_id if active_channel_id is not None else get_active_channel_id()
    existing_autoplay = JOB_ENGINE.get_active_autoplay_job(cid) if cid is not None else None
    low_user = user_text.lower()
    user_wants_stop = bool(_USER_EXPLICIT_STOP_RE.search(low_user))

    # Guardrail 1: If Autoplay is active and the LLM called `action='cancel'` on a genre-switch prompt
    # (thinking it should cancel the old genre job first), convert it in-place to an Autoplay genre switch!
    if (
        existing_autoplay is not None
        and act in ("cancel", "stop", "remove", "delete", "unremind")
        and not user_wants_stop
        and (
            _SWITCH_AUTOPLAY_GENRE_RE.search(low_user)
            or re.search(
                r"\b(?:autoplay|auto-play|auto\s+play|radio|play|switch|change|swap|genre|vibe|"
                r"techno|dnb|drum\s+and\s+bass|jungle|house|trance|dubstep|ambient|lofi|lo-fi|phonk|synthwave|hip\s*hop|rap|rock|metal|jazz|pop)\b",
                low_user,
            )
        )
    ):
        print(
            f"🛡️ [Job Enricher]: Intercepted mistaken cancel of active Autoplay '{existing_autoplay.job_id}' "
            f"on genre-switch prompt '{user_text}' -> converting to action='autoplay'."
        )
        enriched["action"] = "autoplay"
        enriched["query"] = user_text
        enriched.pop("job_id", None)
        return enriched

    # Guardrail 2: If the user asked for autoplay or to switch autoplay genre, ensure `action='autoplay'`
    # even if the LLM passed `action='create'` and stripped the word 'autoplay' from `query`.
    if act == "create" and not user_wants_stop:
        if has_explicit_autoplay_start_intent(user_text):
            enriched["action"] = "autoplay"
        elif existing_autoplay is not None and _SWITCH_AUTOPLAY_GENRE_RE.search(low_user):
            enriched["action"] = "autoplay"
            if not enriched.get("query"):
                enriched["query"] = user_text

    # Guardrail 3: When starting or updating Autoplay from a replied-to song card or YouTube link,
    # ensure `query` preserves the similarity seed or link if the LLM omitted it.
    if str(enriched.get("action") or "").strip().lower() == "autoplay":
        cur_q = str(enriched.get("query") or "").strip()
        if reply_seed and "http" not in low_user and " by " not in cur_q.lower():
            enriched["query"] = f"autoplay similar to {reply_seed[0]} by {reply_seed[1]}"
        elif ("youtube.com/watch" in low_user or "youtu.be/" in low_user) and "http" not in cur_q.lower() and " by " not in cur_q.lower():
            enriched["query"] = user_text

    return enriched


_JOB_CONFIRMATION_BLOCK_RE = re.compile(
    r"((?:⏰|📅|🗓️|🔁|🎯|👁️|📻)\s*\*\*[A-Z0-9\s/_-]+\(`(J-\d+)`\)\*\*(?:\n•[^\n]+)*)",
    re.IGNORECASE,
)

_PHANTOM_SCHEDULE_CLAIM_RE = re.compile(
    r"(?:"
    r"\b(?:i['’]?ve|i\s+have)\s+(?:scheduled|set(?:\s+up)?\s+an?\s+(?:reminder|timer|alarm|watch|alert|schedule|recurring\s+task|daily\s+task))\b"
    r"|\b(?:reminder|timer|alarm|scheduled\s+task|condition\s+watch)\s+(?:has\s+been\s+|is\s+(?:now\s+)?)?(?:set|created|scheduled|activated)\b"
    r"|\b(?:i['’]?ll|i\s+will)\s+(?:remind|alert|notify|ping)\s+(?:you|the\s+channel)\s+(?:in\s+\d+|at\s+\d+|tomorrow|tonight|every\s+|when\s+|whenever\s+|once\s+|as\s+soon\s+as\s+)\b"
    r"|\b(?:i['’]?ve|i\s+have)\s+set\s+(?:a|your)\s+(?:reminder|timer|alarm|schedule|watch)\s+(?:for|to)\b"
    r")",
    re.IGNORECASE,
)


def has_phantom_schedule_claim(reply: str, user_text: str) -> bool:
    """
    Return True if the assistant's `reply` claims that a reminder, scheduled task, timer, or condition watch
    was just registered, yet no `J-XXX` job ID appears anywhere in `reply`.
    """
    if not reply or not user_text:
        return False
    if re.search(r"\bJ-\d+\b", reply):
        return False
    clean_u, _ = _extract_user_and_reply_card(user_text)
    low_u = clean_u.lower()
    if clean_u.startswith("Execute scheduled task:"):
        return False
    if _LIST_INTENT_RE.search(low_u) or _USER_EXPLICIT_STOP_RE.search(low_u) or _AUTOPLAY_META_OR_NEGATION_RE.search(clean_u):
        return False
    return bool(_PHANTOM_SCHEDULE_CLAIM_RE.search(reply))


def ensure_turn_job_confirmation_preserved(
    reply: str,
    history: list,
    fast_voice_turn: bool = False,
) -> str:
    """
    Deterministic post-turn guard: if `skill_job_manager` executed during the current turn and
    created a job (`J-XXX`) or returned a `⚠️` error, ensure the `J-XXX` confirmation block
    (or `⚠️` error) is always preserved in the final reply even if the LLM paraphrased it away.
    """
    if fast_voice_turn or not history:
        return reply

    last_user_idx = -1
    for idx in range(len(history) - 1, -1, -1):
        r = history[idx].get("role") if isinstance(history[idx], dict) else getattr(history[idx], "role", None)
        if r == "user":
            last_user_idx = idx
            break
    if last_user_idx < 0:
        return reply

    confirm_blocks = []
    warning_lines = []
    for msg in history[last_user_idx + 1 :]:
        r = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        name = msg.get("name") if isinstance(msg, dict) else getattr(msg, "name", None)
        if r != "tool" or name != "skill_job_manager":
            continue
        raw_out = str((msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")) or "")
        clean_out = raw_out.split("\n\n[CURRENT REFERENCE TIME:")[0].split("\n\n[SYSTEM INSTRUCTION")[0].strip()
        for m_conf in _JOB_CONFIRMATION_BLOCK_RE.finditer(clean_out):
            block_text = m_conf.group(1).strip()
            job_id = m_conf.group(2).strip()
            confirm_blocks.append((job_id, block_text))
        if clean_out.startswith("⚠️"):
            first_warn = clean_out.splitlines()[0].strip()
            if first_warn:
                warning_lines.append(first_warn)

    out = (reply or "").strip()
    for job_id, block_text in reversed(confirm_blocks):
        if not re.search(rf"\b{re.escape(job_id)}\b", out, re.IGNORECASE):
            out = f"{block_text}\n\n{out}".strip() if out else block_text

    for warn in reversed(warning_lines):
        if "⚠️" not in out and warn not in out:
            out = f"{warn}\n\n{out}".strip() if out else warn

    return out


