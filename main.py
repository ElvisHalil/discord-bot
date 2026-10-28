import discord
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
import re
import types
import io
import asyncio
import random
import requests
from collections import defaultdict
from dotenv import load_dotenv
from ollama import AsyncClient
import src.registry as tools
from src.utils import (
    BoundedCache,
    BoundedSet,
    clear_http_caches,
    dispatch_tracker_command,
    dispatch_voice_command,
    ensure_ollama_running,
    extract_song_card_target,
    chunk_message,
    resolve_reaction_recommendation,
    suppress_discord_embeds,
    run_in_worker,
    shutdown_worker_pool,
)
from src.context import (
    bind_event_loop,
    clear_channel_attachments,
    pop_channel_attachments,
    record_channel_human_activity,
    set_active_channel_id,
    set_active_user,
)
from src.jobs import (
    JOB_ENGINE,
    ActionType,
    SystemJobProvider,
)
from src.skills.jobs import (
    ensure_turn_job_confirmation_preserved,
    has_explicit_autoplay_start_intent,
    has_phantom_schedule_claim,
    is_autoplay_meta_or_negation,
    skill_job_manager,
)
from src.skills.music.engine.formatting import (
    ensure_turn_music_card_preserved,
    format_song_card,
    get_music_metadata,
    standardize_song_reply,
)
from src.skills.sports.tracker import (
    SPORTS_TRACKER,
    skill_sports_tracker,
)
from src.skills.flights import (
    FLIGHT_TRACKER,
    skill_flight_tracker,
)
from src.skills.voice import (
    VOICE_COHOST,
    clear_voice_cache,
    skill_voice_info,
)
try:
    from src.config import MODES
except ImportError:
    import importlib.util as _importlib_util
    from pathlib import Path as _Path
    _spec = _importlib_util.spec_from_file_location("src.config", _Path(__file__).resolve().parent / "src" / "config.example.py")
    _mod = _importlib_util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    MODES = _mod.MODES
from src.orchestrator import (
    normalize_tool_args,
    enrich_sports_tool_args,
    enrich_music_tool_args,
    enrich_flight_tool_args,
    enrich_markets_tool_args,
    enrich_graphing_tool_args,
    enrich_voice_tool_args,
    enrich_job_tool_args,
    enrich_image_tool_args,
    enrich_youtube_tool_args,
    enrich_tool_output_with_time_context,
    detect_missed_sports_tool_call,
    detect_missed_music_shift_tool_call,
    detect_missed_flight_tool_call,
    detect_missed_markets_tool_call,
    detect_missed_graphing_tool_call,
    detect_missed_voice_tool_call,
    detect_missed_job_tool_call,
    detect_missed_image_tool_call,
    trim_history,
    compact_intra_turn_tool_history,
    apply_auto_ping,
    join_orphan_bullets,
    sanitize_agent_reply,
    extract_reply_context,
)
from src.utils.telegram import (
    broadcast_mode_switch,
    is_telegram_channel,
    send_telegram_message,
    send_telegram_photo,
    start_telegram_bridge,
)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    print("❌ Error: DISCORD_TOKEN is missing from .env file!")
    exit(1)

ensure_ollama_running()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
allowed_mentions = discord.AllowedMentions(
    everyone=False,
    roles=False,
    users=True,
    replied_user=True,
)
client = discord.Client(intents=intents, allowed_mentions=allowed_mentions)
ollama_client = AsyncClient()

# Global state
chat_history = {}
known_users = BoundedCache(maxsize=1024)
active_channels = set()
# One lock per channel so overlapping mentions don't interleave tool calls in a shared history.
channel_locks = defaultdict(asyncio.Lock)
# (message_id, user_id) pairs already handled by the 👍 recommender, to avoid duplicate runs.
MAX_HANDLED_REACTIONS = 2000
handled_reactions = BoundedSet(maxsize=MAX_HANDLED_REACTIONS)
# Per-channel in-flight guard so rapid double-clicks on the ⏭️ Next button don't spawn duplicate turns.
_active_next_channels = set()
# Tracks the most recent Song Card message object per channel so previous cards have their ⏭️ Next button disabled.
_last_song_card_messages = BoundedCache(maxsize=128)
# Tracks per-message button disabled state ({message_id: {"next_disabled": bool, "like_disabled": bool}})
_song_card_button_states = BoundedCache(maxsize=512)


class SongCardView(discord.ui.View):
    """Persistent Discord UI View attached to Song Card messages with ⏭️ Next and 👍 Like buttons."""

    def __init__(self, next_disabled: bool = False, like_disabled: bool = False) -> None:
        super().__init__(timeout=None)
        for child in self.children:
            cid = getattr(child, "custom_id", None)
            if next_disabled and cid == "song_card:next":
                child.disabled = True
            elif like_disabled and cid == "song_card:like":
                child.disabled = True

    @discord.ui.button(
        label="Next",
        emoji="⏭️",
        style=discord.ButtonStyle.secondary,
        custom_id="song_card:next",
    )
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await handle_song_card_button_interaction(interaction, action="next")

    @discord.ui.button(
        label="Like",
        emoji="👍",
        style=discord.ButtonStyle.secondary,
        custom_id="song_card:like",
    )
    async def like_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await handle_song_card_button_interaction(interaction, action="like")


def build_song_card_view(text: str, next_disabled: bool = False, like_disabled: bool = False):
    """Return a `SongCardView` instance if `text` is a valid Song Card, else `None`."""
    if not text:
        return None
    if extract_song_card_target(text) is not None or VOICE_COHOST.extract_song_card_track(text) is not None:
        return SongCardView(next_disabled=next_disabled, like_disabled=like_disabled)
    return None


def _get_song_card_button_state(message) -> dict:
    """Return the current `{'next_disabled': bool, 'like_disabled': bool}` state for `message`."""
    msg_id = getattr(message, "id", None)
    state = dict(_song_card_button_states.get(msg_id) or {"next_disabled": False, "like_disabled": False})
    existing_view = getattr(message, "view", None)
    if existing_view is not None and hasattr(existing_view, "children"):
        for child in existing_view.children:
            cid = getattr(child, "custom_id", None)
            if cid == "song_card:next" and getattr(child, "disabled", False):
                state["next_disabled"] = True
            elif cid == "song_card:like" and getattr(child, "disabled", False):
                state["like_disabled"] = True
    return state


async def _update_song_card_buttons(
    message,
    *,
    next_disabled: bool | None = None,
    like_disabled: bool | None = None,
) -> None:
    """Edit a Song Card message's `SongCardView`, preserving any previously disabled button state."""
    if message is None:
        return
    state = _get_song_card_button_state(message)
    if next_disabled is not None:
        state["next_disabled"] = bool(next_disabled)
    if like_disabled is not None:
        state["like_disabled"] = bool(like_disabled)
    msg_id = getattr(message, "id", None)
    if msg_id is not None:
        _song_card_button_states[msg_id] = state
    if not hasattr(message, "edit"):
        return
    try:
        await message.edit(
            view=SongCardView(
                next_disabled=state["next_disabled"],
                like_disabled=state["like_disabled"],
            )
        )
    except Exception:
        pass


async def _disable_song_card_next_button(message) -> None:
    """Edit an earlier Song Card message so its `⏭️ Next` button is disabled while preserving `👍 Like` state."""
    await _update_song_card_buttons(message, next_disabled=True)


async def _send_discord_message(channel, text: str = "", files=None):
    """Send `text` (and optional `files`) to `channel`, automatically attaching `SongCardView` if `text` is a Song Card
    and disabling the `⏭️ Next` button on the previous Song Card in that channel."""
    card_view = build_song_card_view(text)
    channel_id = getattr(channel, "id", None)
    prev_card_msg = _last_song_card_messages.get(channel_id) if (card_view is not None and channel_id is not None) else None
    kwargs = {}
    if files:
        kwargs["files"] = files
    if card_view is not None:
        kwargs["view"] = card_view
    sent_msg = None
    try:
        if text:
            sent_msg = await channel.send(text, **kwargs)
        elif files:
            sent_msg = await channel.send(**kwargs)
    except TypeError:
        kwargs.pop("view", None)
        if text:
            sent_msg = await channel.send(text, **kwargs)
        elif files:
            sent_msg = await channel.send(**kwargs)

    if card_view is not None and channel_id is not None:
        if prev_card_msg is not None and (sent_msg is None or getattr(prev_card_msg, "id", None) != getattr(sent_msg, "id", None)):
            await _disable_song_card_next_button(prev_card_msg)
        if sent_msg is not None:
            sent_msg_id = getattr(sent_msg, "id", None)
            if sent_msg_id is not None:
                _song_card_button_states[sent_msg_id] = {"next_disabled": False, "like_disabled": False}
            _last_song_card_messages[channel_id] = sent_msg
    return sent_msg

_default_profile = os.getenv("DEFAULT_PROFILE", "").lower()
if _default_profile in MODES:
    current_mode = _default_profile
else:
    current_mode = next(iter(MODES))
    if _default_profile:
        print(f"⚠️ DEFAULT_PROFILE '{_default_profile}' is not a known mode; using '{current_mode}'.")

# Only this Discord user may run !mode (it switches the model for every server
# and unloads GPU memory). Fails closed: if unset, nobody can switch modes.
try:
    BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "").strip())
except ValueError:
    BOT_OWNER_ID = None
    print("⚠️ BOT_OWNER_ID is missing or invalid in .env; '!mode' is disabled for everyone.")


def clear_tool_caches(channel_id: int = None):
    clear_channel_attachments(channel_id)
    if channel_id is None:
        _last_song_card_messages.clear()
        tools.VISITED_URLS.clear()
        tools.URL_CACHE.clear()
        tools.SEARCH_CACHE.clear()
        tools.YOUTUBE_CACHE.clear()
        clear_http_caches()
    else:
        _last_song_card_messages.pop(channel_id, None)
    try:
        from src.skills.youtube.engine.client import clear_youtube_caches
        from src.skills.music.playback.youtube_music import clear_resolved_music_cache
        from src.skills.music.aggregators.deezer import clear_deezer_cache
        from src.skills.music.aggregators.lastfm import clear_lastfm_cache
        from src.skills.music.aggregators.charts import clear_charts_cache
        from src.skills.music.context import (
            clear_channel_played_songs,
            clear_recently_played_tracks,
        )
        from src.skills.sports.tracker import clear_channel_last_match_query
        from src.skills.flights.context import clear_channel_flight_state
        from src.skills.markets.context import clear_channel_market_state
        from src.skills.graphing.context import clear_channel_chart_state
        from src.skills.sports.aggregators.liquipedia import clear_liquipedia_cache
        from src.skills.sports.aggregators.hltv import clear_hltv_cache
        from src.skills.sports.aggregators.kashrock import clear_kashrock_cache
        from src.skills.sports.aggregators.espn_utils import clear_espn_cache
        from src.skills.weather.aggregators import clear_weather_cache
        from src.skills.markets import clear_markets_cache
        from src.skills.graphing import clear_graphing_cache
        clear_channel_played_songs(channel_id)
        clear_recently_played_tracks(channel_id)
        clear_channel_last_match_query(channel_id)
        clear_channel_flight_state(channel_id)
        clear_channel_market_state(channel_id)
        clear_channel_chart_state(channel_id)
        clear_voice_cache(channel_id)
        if channel_id is None:
            clear_youtube_caches()
            clear_resolved_music_cache()
            clear_deezer_cache()
            clear_lastfm_cache()
            clear_charts_cache()
            clear_liquipedia_cache()
            clear_hltv_cache()
            clear_kashrock_cache()
            clear_espn_cache()
            clear_weather_cache()
            clear_markets_cache()
            clear_graphing_cache()
    except Exception:
        pass


async def handle_live_match_update(match, snapshot, triggers):
    """Callback invoked by SPORTS_TRACKER when a tracked game has a score change, red card, halftime, or full-time."""
    is_tg = is_telegram_channel(match.channel_id)
    channel = None
    if not is_tg:
        channel = client.get_channel(match.channel_id)
        if not channel:
            try:
                channel = await client.fetch_channel(match.channel_id)
            except Exception as e:
                print(f"⚠️ [Live Tracker]: Could not fetch channel {match.channel_id}: {e}")
                return

    trigger_block = "\n".join(f"- {t}" for t in triggers)
    stats_line = f"\nLive Match Stats: {snapshot.stats_summary}" if snapshot.stats_summary else ""
    fallback_msg = (
        f"📡 **LIVE MATCH UPDATE — {match.league_name}**\n"
        f"{trigger_block}"
        + (f"\n📊 *{snapshot.stats_summary}*" if snapshot.stats_summary else "")
    )

    mode_config = MODES[current_mode]
    if current_mode == "chat":
        sys_prompt = (
            "you are a casual, hyped sports fan hanging out in discord. "
            "write 1-2 short lowercase sentences reacting to this live sports match update. "
            "include the exact score, team names, and scorer/event details. no links."
        )
    else:
        sys_prompt = (
            "You are a live sports desk commentator in Discord. "
            "Write a concise, energetic 1-3 sentence live match alert announcing the event below. "
            "Include the exact score, match clock/status, competition name, and any goal scorer/assist/card or stats details."
        )

    if getattr(match, "sport_path", "") == "f1":
        internal_user_prompt = (
            f"[INTERNAL LIVE F1 SESSION TRACKER TRIGGER]\n"
            f"Grand Prix Session: {snapshot.home_team} — {snapshot.away_team} ({match.league_name})\n"
            f"Current Leader / Order: {snapshot.home_score} | {snapshot.away_score} ({snapshot.status_detail})\n"
            f"Events Just Detected:\n{trigger_block}{stats_line}\n\n"
            "Announce this live Formula 1 session update to the Discord channel now."
        )
    else:
        internal_user_prompt = (
            f"[INTERNAL LIVE SPORTS TRACKER TRIGGER]\n"
            f"Match: {snapshot.home_team} vs {snapshot.away_team} ({match.league_name})\n"
            f"Current Score: {snapshot.home_team} {snapshot.home_score} - {snapshot.away_score} {snapshot.away_team} ({snapshot.status_detail})\n"
            f"Events Just Detected:\n{trigger_block}{stats_line}\n\n"
            "Announce this live update to the Discord channel now."
        )

    update_options = {
        **mode_config["ollama_options"],
        "num_ctx": 2048,
        "num_predict": 220,
    }

    try:
        resp = await ollama_client.chat(
            model=mode_config["model"],
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": internal_user_prompt},
            ],
            options=update_options,
        )
        llm_text = sanitize_agent_reply((resp.message.content or "").strip())
    except Exception as e:
        print(f"⚠️ [Live Tracker LLM Error]: {e}")
        llm_text = ""

    final_msg = f"📡 **LIVE UPDATE ({match.league_name})**\n{llm_text}" if llm_text else fallback_msg
    if is_tg:
        await send_telegram_message(match.channel_id, final_msg)
    else:
        await channel.send(final_msg)
    await VOICE_COHOST.announce_tracker_event(
        match.channel_id,
        llm_text or fallback_msg,
        profile_or_mode="sportscaster" if current_mode != "chat" else current_mode,
    )

    async with channel_locks[match.channel_id]:
        if match.channel_id not in chat_history:
            system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
            chat_history[match.channel_id] = [system_msg]

        chat_history[match.channel_id].append({
            "role": "user",
            "content": f"[Live Sports Tracker Event]: {trigger_block}",
        })
        chat_history[match.channel_id].append({
            "role": "assistant",
            "content": final_msg,
        })


async def handle_flight_tracker_update(channel_id: int, alert_text: str):
    """Callback invoked by FLIGHT_TRACKER when a tracked flight changes state or a watched route price drops."""
    is_tg = is_telegram_channel(channel_id)
    channel = None
    if not is_tg:
        channel = client.get_channel(channel_id)
        if not channel:
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception as e:
                print(f"⚠️ [Flight Tracker]: Could not resolve channel {channel_id}: {e}")
                return

    if is_tg:
        await send_telegram_message(channel_id, alert_text)
    else:
        await channel.send(alert_text)
    await VOICE_COHOST.announce_tracker_event(channel_id, alert_text, profile_or_mode=current_mode)

    mode_config = MODES[current_mode]
    async with channel_locks[channel_id]:
        if channel_id not in chat_history:
            system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
            chat_history[channel_id] = [system_msg]
        chat_history[channel_id].append({
            "role": "assistant",
            "content": alert_text,
        })


async def handle_job_fire(job, message_text: str):
    """Callback invoked by JOB_ENGINE when a scheduled reminder, recurring task, condition alert, or autoplay track fires."""
    channel_id = job.channel_id
    is_tg = is_telegram_channel(channel_id)
    channel = None
    if not is_tg:
        channel = client.get_channel(channel_id)
        if not channel:
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception as e:
                print(f"⚠️ [Job Engine]: Could not resolve channel {channel_id}: {e}")
                return

    final_output = message_text
    attachments = []
    # If this is a dynamic AGENT_TASK (and not an idle-brake auto-stop notice), execute it live
    if job.action_type == ActionType.AGENT_TASK and not message_text.startswith("⏸️"):
        mention_prefix = f"<@{job.creator_id}> " if job.creator_id > 0 else ""
        low_payload = (job.payload_text or "").lower()
        # Fast-path for recurring music play tasks ("play a dnb song")
        if re.search(r"\bplay\b.*\b(?:song|track|music|dnb|drum\s+and\s+bass|techno|house|jazz|rock|hip\s*hop|trance|dubstep|ambient|lofi|phonk)\b", low_payload):
            genre_q = re.sub(r"\b(?:play|send|me|a|an|some|another|song|track|music)\b", " ", job.payload_text, flags=re.IGNORECASE)
            genre_q = " ".join(genre_q.split()).strip() or "dnb"
            card_text, _ = await run_in_worker(JOB_ENGINE._autoplay_resolver, channel_id, genre_q)
            if card_text:
                final_output = f"{mention_prefix}🔁 **Scheduled Music (`{job.job_id}`):**\n\n{card_text}"
        else:
            base_mode_config = MODES.get("research") or MODES.get("research-gemma") or MODES[current_mode]
            task_tools = {
                k: v for k, v in (base_mode_config.get("tools") or {}).items()
                if k != "skill_job_manager"
            }
            mode_config = {**base_mode_config, "tools": task_tools}
            set_active_channel_id(channel_id)
            clean_task_prompt = re.sub(
                r"\s*\busing\s+`?skill_[a-z0-9_]+`?\.?\s*$",
                "",
                job.payload_text or "",
                flags=re.IGNORECASE,
            ).strip() or job.payload_text
            system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
            recent_context = [
                m for m in list(chat_history.get(channel_id) or [])[1:]
                if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) in ("user", "assistant")
            ][-4:]
            task_history = [
                system_msg,
                *recent_context,
                {
                    "role": "user",
                    "content": (
                        f"Execute scheduled task (Run #{max(1, job.run_count)}): {clean_task_prompt}. "
                        "Provide the result directly for the user now (if this is a recurring creative or brainstorming prompt, "
                        "generate a fresh, non-repetitive idea)."
                    ),
                },
            ]
            try:
                clear_channel_attachments(channel_id)
                task_reply = await run_agent_turn(channel_id, mode_config, turn_history=task_history)
                attachments = pop_channel_attachments(channel_id)
                if not task_reply.strip() and attachments:
                    captions = [str(att.get("caption") or "").strip() for att in attachments if att.get("caption")]
                    task_reply = f"📊 **{captions[0]}**" if captions else "📊 Here is your scheduled chart."
                elif task_reply.strip() and attachments:
                    for att in attachments:
                        cap = str(att.get("caption") or "").strip()
                        if att.get("filename", "").startswith("monte_carlo_") and "| Basis:" in cap and "Basis:" not in task_reply:
                            task_reply = f"📊 **{cap}**\n\n{task_reply.lstrip()}"
                            break
                task_reply = suppress_discord_embeds(task_reply)
                set_active_channel_id(channel_id)
                if "youtube.com" in task_reply.lower() or "youtu.be" in task_reply.lower():
                    task_reply = await run_in_worker(standardize_song_reply, task_reply)
                task_reply = sanitize_agent_reply(task_reply)
                clean_label = sanitize_agent_reply(clean_task_prompt)
                if task_reply.strip():
                    final_output = f"{mention_prefix}⏰ **Scheduled Task (`{job.job_id}` — {clean_label}):**\n{task_reply.strip()}"
            except Exception as e:
                print(f"⚠️ [Job Engine Agent Task Error]: {e}")

    is_music_card = VOICE_COHOST.extract_song_card_track(final_output) is not None
    if not is_tg and is_music_card and VOICE_COHOST.get_session(channel_id) is not None:
        voice_badge = await VOICE_COHOST.stream_music_from_card_async(
            channel_id,
            final_output,
            replace_current=True,
            active_mode=current_mode,
        )
        if voice_badge and voice_badge not in final_output:
            final_output = f"{final_output.rstrip()}\n{voice_badge}"

    if is_tg:
        await send_telegram_message(channel_id, final_output)
        for att in attachments:
            if att.get("data"):
                await send_telegram_photo(
                    channel_id,
                    att.get("filename", "chart.png"),
                    att["data"],
                    caption=att.get("caption", ""),
                )
    else:
        chunks = [c for c in chunk_message(final_output) if c]
        discord_files = [
            discord.File(io.BytesIO(att["data"]), filename=att["filename"])
            for att in attachments
            if att.get("data")
        ]
        if chunks:
            for idx, chunk in enumerate(chunks):
                await _send_discord_message(
                    channel,
                    chunk,
                    files=discord_files if idx == len(chunks) - 1 and discord_files else None,
                )
        elif discord_files:
            await _send_discord_message(channel, "", files=discord_files)

    if not is_music_card:
        await VOICE_COHOST.announce_tracker_event(channel_id, final_output, profile_or_mode=current_mode)

    mode_config = MODES[current_mode]
    async with channel_locks[channel_id]:
        if channel_id not in chat_history:
            system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
            chat_history[channel_id] = [system_msg]
        chat_history[channel_id].append({
            "role": "assistant",
            "content": final_output,
        })


def _list_channel_sports_matches(cid: int) -> list[str]:
    lines = []
    for (m_cid, _), m in SPORTS_TRACKER.tracked_matches.items():
        if m_cid == cid:
            snap = m.last_snapshot
            if getattr(m, "sport_path", "") == "f1":
                lines.append(
                    f"• **[F1 SESSION]** **{m.home_team} — {m.away_team}** [{snap.home_score} | {snap.away_score}] "
                    f"({m.league_name} — {snap.status_detail})"
                )
            else:
                lines.append(
                    f"• **[SPORTS]** **{m.home_team} {snap.home_score} - {snap.away_score} {m.away_team}** "
                    f"({m.league_name} — {snap.status_detail})"
                )
    return lines


def _list_channel_flights(cid: int) -> list[str]:
    lines = []
    for (f_cid, _), tf in FLIGHT_TRACKER.tracked_flights.items():
        if f_cid == cid:
            lines.append(
                f"• **[LIVE FLIGHT]** **{tf.flight_number}** ({tf.origin_code} -> {tf.dest_code}) — {tf.last_state.upper()}"
            )
    for (w_cid, _), pw in FLIGHT_TRACKER.price_watches.items():
        if w_cid == cid:
            lines.append(
                f"• **[ROUTE PRICE WATCH]** **{pw.origin_code} -> {pw.dest_code}** [{pw.date}] — Lowest: ${pw.lowest_seen_price} {pw.currency}"
            )
    return lines


async def handle_voice_utterance_turn(session, utterance, cleaned_query: str):
    """Callback invoked by VOICE_COHOST when a speaker's utterance in Discord voice chat triggers the AI co-host."""
    channel_id = session.channel_id
    mode_config = MODES[current_mode]
    is_research = mode_config.get("is_research", False)
    tools.set_memory_file(mode_config["memory_file"])
    set_active_channel_id(channel_id)

    # Fast-path for spoken voice music playback controls ("Hey bot, pause", "resume", "stop music", "skip")
    q_norm = re.sub(r"[^\w\s]", "", (cleaned_query or "").lower()).strip()
    q_norm = re.sub(r"^(?:please|can\s+you|could\s+you|yo)\s+", "", q_norm).strip()
    fast_ctrl_reply = None
    if q_norm in ("pause", "pause music", "pause the music", "pause song", "pause track"):
        fast_ctrl_reply = VOICE_COHOST.pause_music(channel_id)
    elif q_norm in ("resume", "resume music", "resume the music", "unpause", "continue music"):
        fast_ctrl_reply = VOICE_COHOST.resume_music(channel_id)
    elif q_norm in ("stop", "stop music", "stop the music", "stop playing", "stop playback"):
        fast_ctrl_reply = VOICE_COHOST.stop_music(channel_id, clear_queue=True)
    elif q_norm in ("skip", "skip song", "skip track", "next", "next song", "next track") and (
        VOICE_COHOST._music_queues.get(channel_id) or JOB_ENGINE.get_active_autoplay_job(channel_id) is not None
    ):
        fast_ctrl_reply = await dispatch_voice_command(
            text="!skip",
            channel_id=channel_id,
            voice_info_fn=skill_voice_info,
            voice_cohost_service=VOICE_COHOST,
            active_mode=current_mode,
        )
    if fast_ctrl_reply:
        channel = client.get_channel(channel_id)
        if channel is not None:
            header = f"🎙️ **Voice Co-Host (replying to {utterance.display_name} — *\"{utterance.transcript}\"*):**\n"
            asyncio.create_task(_send_discord_message(channel, header + fast_ctrl_reply))
        return None

    voice_prompt_hint = (
        f"[Spoken in Discord Voice Channel '{session.voice_channel_name}' — "
        f"your text reply will be spoken aloud automatically via TTS, so NEVER call skill_voice_info(action='speak') to answer; "
        f"only call tools if you need live data, music, or if the user explicitly asks to leave/mute/recap/switch voice. "
        f"Reply directly in 1-2 short, natural spoken sentences]"
    )
    if current_mode == "chat":
        user_msg = f"{utterance.display_name} (User ID: {utterance.user_id}) {voice_prompt_hint}: {cleaned_query}"
    else:
        user_msg = f"User {utterance.display_name} says {voice_prompt_hint}: {cleaned_query}"

    async with channel_locks[channel_id]:
        if channel_id not in chat_history:
            system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
            chat_history[channel_id] = [system_msg]
        else:
            system_msg = chat_history[channel_id][0]
        recent_tail = []
        for m in chat_history[channel_id][1:][-6:]:
            m_role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            if m_role not in ("user", "assistant"):
                continue
            m_content = str((m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or "")
            if len(m_content) > 400:
                m_content = m_content[:400].rstrip() + "..."
            recent_tail.append({"role": m_role, "content": m_content})
        turn_history = [system_msg] + recent_tail

    turn_start_idx = len(turn_history)
    turn_history.append({"role": "user", "content": user_msg})

    clear_channel_attachments(channel_id)
    reply = await run_agent_turn(
        channel_id,
        mode_config,
        turn_history=turn_history,
        fast_voice_turn=True,
    )
    if not reply or not reply.strip():
        return None

    # If a music tool ran during this voice turn and returned a verified YouTube Music URL
    # that the model omitted from its concise spoken reply, append it so standardize_song_reply
    # formats the full Song Card in the text channel while TTS speaks only the natural intro.
    if "youtube.com" not in reply.lower() and "youtu.be" not in reply.lower():
        for msg in reversed(turn_history[turn_start_idx:]):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            t_name = msg.get("name") if isinstance(msg, dict) else getattr(msg, "name", None)
            if role == "tool" and t_name in ("skill_music_discovery", "skill_youtube_search"):
                t_content = str(msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "") or "")
                m_yt = re.search(
                    r"(https:\/\/(?:music|www)\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})",
                    t_content,
                )
                if m_yt:
                    yt_url = m_yt.group(1).replace("www.youtube.com", "music.youtube.com")
                    reply = f"{reply.rstrip()}\n\n{yt_url}"
                    break

    if is_research:
        reply = join_orphan_bullets(reply)
    reply = suppress_discord_embeds(reply)
    set_active_channel_id(channel_id)
    if "youtube.com" in reply.lower() or "youtu.be" in reply.lower():
        reply = await run_in_worker(standardize_song_reply, reply)
        reply = JOB_ENGINE.sync_channel_autoplay_on_track_played(channel_id, reply, new_query_hint=cleaned_query)
    reply = sanitize_agent_reply(reply)

    if VOICE_COHOST.extract_song_card_track(reply) is not None:
        voice_badge = await VOICE_COHOST.stream_music_from_card_async(
            channel_id,
            reply,
            replace_current=True,
            active_mode=current_mode,
        )
        if voice_badge and voice_badge not in reply:
            reply = f"{reply.rstrip()}\n{voice_badge}"

    turn_delta = turn_history[turn_start_idx:]
    if len(turn_delta) == 1:
        turn_delta.append({"role": "assistant", "content": reply})
    else:
        turn_delta[-1] = {"role": "assistant", "content": reply}

    async with channel_locks[channel_id]:
        if channel_id not in chat_history:
            chat_history[channel_id] = [system_msg]
        chat_history[channel_id].extend(turn_delta)
        chat_history[channel_id] = trim_history(
            chat_history[channel_id], mode_config.get("history_limit", 40)
        )

    attachments = pop_channel_attachments(channel_id)
    channel = client.get_channel(channel_id)
    if channel is not None:
        header = f"🎙️ **Voice Co-Host (replying to {utterance.display_name} — *\"{utterance.transcript}\"*):**\n"
        chunks = [c for c in chunk_message(header + reply) if c]
        discord_files = [
            discord.File(io.BytesIO(att["data"]), filename=att["filename"])
            for att in attachments
            if att.get("data")
        ]

        async def _send_voice_text_reply():
            try:
                for idx, chunk in enumerate(chunks):
                    await _send_discord_message(
                        channel,
                        chunk,
                        files=discord_files if idx == len(chunks) - 1 and discord_files else None,
                    )
            except Exception as e:
                print(f"⚠️ [Voice Co-Host Text Reply Error]: {e}")

        asyncio.create_task(_send_voice_text_reply())

    return reply


@client.event
async def on_ready():
    print(f"✅ Bot online as: {client.user}")
    print(f"🔥 Active Mode: {current_mode.upper()} (Switch with '!mode <name>': {', '.join(MODES)})")
    loop = asyncio.get_running_loop()
    bind_event_loop(loop)
    try:
        client.add_view(SongCardView())
    except Exception:
        pass
    SPORTS_TRACKER.set_notify_callback(handle_live_match_update)
    SPORTS_TRACKER.ensure_loop_running()
    FLIGHT_TRACKER.bind_event_loop(loop)
    FLIGHT_TRACKER.set_notify_callback(handle_flight_tracker_update)
    FLIGHT_TRACKER.ensure_loop_running()
    JOB_ENGINE.bind_event_loop(loop)
    JOB_ENGINE.set_fire_callback(handle_job_fire)
    JOB_ENGINE.register_provider(
        SystemJobProvider(
            name="sports_tracker",
            interval_seconds=SPORTS_TRACKER.poll_interval,
            is_active_fn=lambda: bool(SPORTS_TRACKER.tracked_matches),
            poll_fn=SPORTS_TRACKER.poll_once,
            list_channel_fn=_list_channel_sports_matches,
            is_system_locked=False,
        )
    )
    JOB_ENGINE.register_provider(
        SystemJobProvider(
            name="flight_tracker",
            interval_seconds=FLIGHT_TRACKER.flight_poll_interval,
            is_active_fn=lambda: bool(FLIGHT_TRACKER.tracked_flights or FLIGHT_TRACKER.price_watches),
            poll_fn=lambda: asyncio.sleep(0),
            list_channel_fn=_list_channel_flights,
            is_system_locked=False,
        )
    )
    JOB_ENGINE.ensure_loop_running()
    VOICE_COHOST.set_utterance_callback(handle_voice_utterance_turn)
    if hasattr(VOICE_COHOST, "_stt") and hasattr(VOICE_COHOST._stt, "warmup_whisper_model"):
        asyncio.create_task(run_in_worker(VOICE_COHOST._stt.warmup_whisper_model))
    asyncio.create_task(start_telegram_bridge(sys.modules[__name__]))


def _resolve_author_voice_channel(author, guild, text_hint: str = ""):
    """Resolve the active Discord voice channel for `author` or `guild`."""
    author_voice_state = getattr(author, "voice", None)
    author_voice_channel = getattr(author_voice_state, "channel", None) if author_voice_state else None
    if author_voice_channel is None:
        guild_vcs = list(getattr(guild, "voice_channels", None) or [])
        if guild_vcs:
            low_in = (text_hint or "").lower()
            author_voice_channel = (
                next((vc for vc in guild_vcs if low_in and str(getattr(vc, "name", "") or "").lower() in low_in), None)
                or next((vc for vc in guild_vcs if getattr(vc, "voice_states", None) or getattr(vc, "members", None)), None)
                or guild_vcs[0]
            )
    return author_voice_channel


async def process_song_card_like(
    channel,
    message_id: int,
    message_content: str,
    user_id: int,
    user=None,
) -> bool:
    """
    Shared handler for both the 👍 emoji reaction and the 👍 Like button on a Song Card.
    Preserves identical behavior across normal chat and active Autoplay/Voice states.
    """
    card_target = extract_song_card_target(message_content)
    if not card_target:
        return False

    # Only recommend once per (message, user), even if they click the button and/or react with 👍.
    reaction_key = (message_id, user_id)
    if reaction_key in handled_reactions:
        return False
    handled_reactions.add(reaction_key)

    if user is None:
        try:
            user = client.get_user(user_id) or await client.fetch_user(user_id)
        except Exception:
            return False

    user_name = getattr(user, "name", None) or getattr(user, "display_name", None) or str(user_id)
    user_display = getattr(user, "display_name", None) or user_name
    user_tag = getattr(user, "mention", None) or f"<@{user_id}>"
    print(f"👍 [Reaction Caught]: {user_name} liked a song. Finding a verified similar track...")

    async with channel.typing():
        try:
            orig_title = card_target["orig_title"]
            orig_artist = card_target["orig_artist"]
            clean_title = card_target["clean_title"]
            orig_display = card_target["orig_display"]
            print(f"🎵 [Reaction Target]: Cleaned '{clean_title}' by '{orig_artist}'")

            rec = await resolve_reaction_recommendation(
                channel_id=channel.id,
                card_target=card_target,
                music_discovery_fn=tools.skill_music_discovery,
                youtube_search_fn=tools.skill_youtube_search,
                allow_chart_fallback=True,
                max_candidates=6,
            )
            verified_url = rec["verified_url"]
            chosen_track = rec["chosen_track"]
            vid = rec["vid"]
            is_unrelated_fallback = rec["is_unrelated_fallback"]

            if not verified_url or not chosen_track:
                await channel.send(f"{user_tag} ⚠️ Found similar songs, but couldn't verify a working YouTube link. Please try again!")
                return False

            # 4. Generate natural persona intro in parallel with metadata lookup
            mode_config = MODES[current_mode]
            if is_unrelated_fallback:
                intro_prompt = (
                    f"User {user_display} reacted with 👍 to {orig_display}, "
                    f"but no directly related songs could be found for it. "
                    f"Write a single short sentence letting them know you couldn't find any related songs for {orig_display}, "
                    f"and suggest '{chosen_track}' as a track they might like instead. "
                    "Do NOT include any links, URLs, or markdown links."
                )
                default_intro = (
                    f"Couldn't find any directly related songs for **{orig_title}**"
                    + (f" by **{orig_artist}**" if orig_artist else "")
                    + ", but here is a track you might like instead:"
                )
            else:
                intro_prompt = (
                    f"User {user_display} reacted with 👍 to the song {orig_display}. "
                    f"Write a single short sentence recommending the similar song '{chosen_track}'. "
                    "Do NOT include any links, URLs, or markdown links."
                )
                default_intro = f"Here is a song similar to **{orig_title}** by **{orig_artist}**:"

            if current_mode == "chat":
                light_sys_prompt = "you are a casual, opinionated human in discord. write 1 short, lowercase sentence with modern slang. no links, no emojis."
            else:
                light_sys_prompt = "You are a helpful music assistant in Discord. Write a single natural, concise 1-sentence recommendation intro. No links, no markdown URLs, no emojis."

            reaction_options = {
                **mode_config["ollama_options"],
                "num_ctx": 1024,
                "num_predict": 96,
            }

            async def _generate_intro():
                try:
                    resp = await ollama_client.chat(
                        model=mode_config["model"],
                        messages=[
                            {"role": "system", "content": light_sys_prompt},
                            {"role": "user", "content": intro_prompt}
                        ],
                        options=reaction_options
                    )
                    text = resp.message.content.strip()
                    return re.sub(r'https?:\/\/\S+', '', text).strip()
                except Exception as e:
                    print(f"⚠️ [Reaction Ollama Error]: {e}")
                    return default_intro

            intro_text, meta = await asyncio.gather(
                _generate_intro(),
                run_in_worker(get_music_metadata, vid, fallback_text=chosen_track)
            )

            if not intro_text:
                intro_text = default_intro

            # Prepend user @ mention so the channel knows who triggered the recommendation
            if user_tag not in intro_text:
                intro_text = f"{user_tag} {intro_text}"

            final_reply = format_song_card(
                song=meta["song"],
                artist=meta["artist"],
                url=verified_url,
                genre=meta.get("genre"),
                album=meta.get("album"),
                intro=intro_text
            )

            final_reply = JOB_ENGINE.sync_channel_autoplay_on_track_played(channel.id, final_reply)
            if VOICE_COHOST.get_session(channel.id) is not None:
                voice_badge = await VOICE_COHOST.stream_music_from_card_async(
                    channel.id,
                    final_reply,
                    replace_current=False,
                    active_mode=current_mode,
                )
                if voice_badge and voice_badge not in final_reply:
                    final_reply = f"{final_reply.rstrip()}\n{voice_badge}"
            await _send_discord_message(channel, final_reply)

            # Record it under the channel lock so it can't land between a
            # tool call and its results while a message turn is in progress.
            async with channel_locks[channel.id]:
                if channel.id not in chat_history:
                    system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
                    chat_history[channel.id] = [system_msg]

                chat_history[channel.id].append({
                    "role": "user",
                    "content": f"{user_display} reacted with 👍 to '{orig_display}' requesting a similar track."
                })
                chat_history[channel.id].append({
                    "role": "assistant",
                    "content": final_reply
                })
            return True

        except Exception as e:
            print(f"❌ [Reaction Error]: {e}")
            await channel.send(f"{user_tag} ⚠️ Something went wrong while finding a similar song. Please try again!")
            return False


async def process_song_card_next(
    channel,
    message_content: str,
    user_id: int,
    user_display_name: str,
    author_voice_channel=None,
) -> bool:
    """
    Handler for the ⏭️ Next button on a Song Card:
    - When Autoplay is active (or tracks are queued in Voice), behaves identically to `!skip` / `!next`.
    - Outside of Autoplay, behaves identically to saying `"next"` in chat.
    """
    channel_id = channel.id
    if channel_id in _active_next_channels:
        return False
    _active_next_channels.add(channel_id)
    try:
        has_voice_queue = bool(VOICE_COHOST._music_queues.get(channel_id))
        has_autoplay = JOB_ENGINE.get_active_autoplay_job(channel_id) is not None

        # State 1: Autoplay active (or Voice queue populated) -> exact `!skip` / `!next` behavior
        if has_autoplay or has_voice_queue:
            async with channel.typing():
                voice_res = await dispatch_voice_command(
                    text="!skip",
                    channel_id=channel_id,
                    voice_info_fn=skill_voice_info,
                    voice_cohost_service=VOICE_COHOST,
                    author_voice_channel=author_voice_channel,
                    active_mode=current_mode,
                )
                if voice_res:
                    await _send_discord_message(channel, voice_res)
                    mode_config = MODES[current_mode]
                    async with channel_locks[channel_id]:
                        if channel_id not in chat_history:
                            system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
                            chat_history[channel_id] = [system_msg]
                        chat_history[channel_id].append({
                            "role": "assistant",
                            "content": voice_res,
                        })
                    return True
            return False

        # State 2: Outside of Autoplay -> exact conversational `"next"` behavior
        from src.skills.music.context import (
            get_channel_last_played_song,
            record_channel_played_song,
            record_played_track,
        )

        # If in-memory channel state was cleared (e.g. after a restart), seed from the clicked Song Card
        if get_channel_last_played_song(channel_id) is None and message_content:
            card_target = extract_song_card_target(message_content)
            if card_target:
                m_gen = re.search(r"(?im)^\s*(?:\*\*)?Genre:\*{0,2}\s*(.+)$", message_content)
                card_genre = m_gen.group(1).strip() if m_gen else None
                record_played_track(card_target["clean_title"], card_target["orig_artist"], channel_id=channel_id)
                record_channel_played_song(
                    song=card_target["clean_title"],
                    artist=card_target["orig_artist"],
                    genre=card_genre,
                    channel_id=channel_id,
                )

        mode_config = MODES[current_mode]
        is_research = mode_config.get("is_research", False)
        tools.set_memory_file(mode_config["memory_file"])
        set_active_channel_id(channel_id)

        if current_mode == "chat":
            user_msg = f"{user_display_name} (User ID: {user_id}): next"
        else:
            user_msg = f"User {user_display_name} says: next"

        async with channel_locks[channel_id]:
            system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
            if channel_id not in chat_history:
                chat_history[channel_id] = [system_msg]
                if message_content and extract_song_card_target(message_content) is not None:
                    chat_history[channel_id].append({"role": "assistant", "content": message_content})
            else:
                chat_history[channel_id][0] = system_msg
            turn_history = list(chat_history[channel_id])

        turn_history.append({"role": "user", "content": user_msg})
        turn_history = trim_history(turn_history, mode_config.get("history_limit", 40))
        turn_start_idx = len(turn_history) - 1

        async with channel.typing():
            clear_channel_attachments(channel_id)
            reply = await run_agent_turn(channel_id, mode_config, turn_history=turn_history)
            attachments = pop_channel_attachments(channel_id)

            is_empty_fallback = False
            if not reply.strip():
                is_empty_fallback = current_mode != "chat"
                reply = "Don't leave me hanging—what's on your mind?" if current_mode == "chat" else "⚠️ Generated an empty response."

            if current_mode == "chat":
                reply = apply_auto_ping(reply, known_users)
            if is_research:
                reply = join_orphan_bullets(reply)

            reply = suppress_discord_embeds(reply)
            set_active_channel_id(channel_id)
            reply = await run_in_worker(standardize_song_reply, reply)
            reply = JOB_ENGINE.sync_channel_autoplay_on_track_played(channel_id, reply, new_query_hint="next")
            reply = sanitize_agent_reply(reply)

            is_song_card_reply = VOICE_COHOST.extract_song_card_track(reply) is not None
            if is_song_card_reply:
                voice_stream_badge = await VOICE_COHOST.stream_music_from_card_async(
                    channel_id,
                    reply,
                    replace_current=True,
                    allow_auto_join=False,
                    active_mode=current_mode,
                )
                if voice_stream_badge and voice_stream_badge not in reply:
                    reply = f"{reply.rstrip()}\n{voice_stream_badge}"

            if not is_empty_fallback:
                turn_delta = turn_history[turn_start_idx:]
                if len(turn_delta) == 1:
                    turn_delta.append({"role": "assistant", "content": reply})
                else:
                    turn_delta[-1] = {"role": "assistant", "content": reply}

                async with channel_locks[channel_id]:
                    if channel_id not in chat_history:
                        chat_history[channel_id] = [system_msg]
                    chat_history[channel_id].extend(turn_delta)
                    chat_history[channel_id] = trim_history(
                        chat_history[channel_id], mode_config.get("history_limit", 40)
                    )

            chunks = [c for c in chunk_message(reply) if c]
            discord_files = [
                discord.File(io.BytesIO(att["data"]), filename=att["filename"])
                for att in attachments
                if att.get("data")
            ]
            if chunks:
                for idx, chunk in enumerate(chunks):
                    await _send_discord_message(
                        channel,
                        chunk,
                        files=discord_files if idx == len(chunks) - 1 and discord_files else None,
                    )
            elif discord_files:
                await _send_discord_message(channel, "", files=discord_files)
            return True
    except Exception as e:
        print(f"❌ [Song Card Next Error]: {e}")
        await channel.send(f"<@{user_id}> ⚠️ Something went wrong while advancing to the next song. Please try again!")
        return False
    finally:
        _active_next_channels.discard(channel_id)


async def handle_song_card_button_interaction(interaction: discord.Interaction, action: str) -> None:
    """Entry point for clicks on the Song Card `👍 Like` and `⏭️ Next` buttons."""
    user = getattr(interaction, "user", None)
    if user is None or (client.user and getattr(user, "id", None) == client.user.id):
        return

    if getattr(interaction, "guild_id", "present") is None:
        if BOT_OWNER_ID is None or user.id != BOT_OWNER_ID:
            try:
                await interaction.response.defer()
            except Exception:
                pass
            return

    try:
        if not interaction.response.is_done():
            await interaction.response.defer()
    except Exception:
        pass

    channel = getattr(interaction, "channel", None)
    channel_id = getattr(channel, "id", None) or getattr(interaction, "channel_id", None)
    if channel is None and channel_id is not None:
        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception:
                return
    if channel is None:
        return

    record_channel_human_activity(channel.id)
    user_display = getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)
    set_active_user(user.id, user_display)
    set_active_channel_id(channel.id)
    active_channels.add(channel.id)

    if getattr(user, "name", None):
        known_users[user.name.lower()] = user.id
    if getattr(user, "display_name", None):
        known_users[user.display_name.lower()] = user.id

    author_voice_channel = _resolve_author_voice_channel(user, getattr(interaction, "guild", None))
    VOICE_COHOST.register_author_voice_channel(channel.id, author_voice_channel)

    msg = getattr(interaction, "message", None)
    msg_id = getattr(msg, "id", 0) or 0
    msg_content = getattr(msg, "content", "") or ""
    active_card_msg = _last_song_card_messages.get(channel.id)
    is_older_card = (
        active_card_msg is not None
        and getattr(active_card_msg, "id", None)
        and msg_id
        and active_card_msg.id != msg_id
    )

    if action == "like":
        await _update_song_card_buttons(
            msg,
            like_disabled=True,
            next_disabled=True if is_older_card else None,
        )
        await process_song_card_like(
            channel=channel,
            message_id=msg_id,
            message_content=msg_content,
            user_id=user.id,
            user=user,
        )
    elif action == "next":
        await _update_song_card_buttons(msg, next_disabled=True)
        if is_older_card:
            return
        if msg is not None and channel.id not in _last_song_card_messages:
            _last_song_card_messages[channel.id] = msg
        await process_song_card_next(
            channel=channel,
            message_content=msg_content,
            user_id=user.id,
            user_display_name=user_display,
            author_voice_channel=author_voice_channel,
        )


@client.event
async def on_raw_reaction_add(payload):
    if payload.user_id == client.user.id:
        return
    if getattr(payload, "guild_id", "present") is None:
        if BOT_OWNER_ID is None or payload.user_id != BOT_OWNER_ID:
            return
    record_channel_human_activity(payload.channel_id)
        
    if str(payload.emoji) == '👍':
        channel = client.get_channel(payload.channel_id)
        if not channel:
            try:
                channel = await client.fetch_channel(payload.channel_id)
            except Exception:
                return
                
        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return
            
        if message.author.id != client.user.id:
            return
            
        await process_song_card_like(
            channel=channel,
            message_id=payload.message_id,
            message_content=message.content,
            user_id=payload.user_id,
        )


async def run_agent_turn(channel_id, mode_config, turn_history=None, fast_voice_turn: bool = False):
    """Run the model/tool loop for one user turn and return the final reply text."""
    is_research = mode_config.get("is_research", False)
    supports_tools = mode_config.get("supports_tools", True)
    max_iterations = min(mode_config["max_iterations"], 3) if fast_voice_turn else mode_config["max_iterations"]
    active_tools = mode_config.get("tools", {})
    tool_functions = list(active_tools.values())
    history = turn_history if turn_history is not None else chat_history[channel_id]
    set_active_channel_id(channel_id)

    # Pre-resolve any YouTube Music / music-prompt YouTube link in the latest user message so the LLM
    # immediately sees the exact Song & Artist title and never wastes a step calling search_web / read_youtube_video
    # to guess what an opaque 11-char video ID is.
    for _idx in range(len(history) - 1, -1, -1):
        _m = history[_idx]
        _r = _m.get("role") if isinstance(_m, dict) else getattr(_m, "role", None)
        if _r == "user":
            _c = str((_m.get("content") if isinstance(_m, dict) else getattr(_m, "content", "")) or "")
            if (
                ("youtube.com/watch" in _c.lower() or "youtu.be/" in _c.lower())
                and "[Replying to song card:" not in _c
                and (
                    "music.youtube.com/watch" in _c.lower()
                    or re.search(
                        r"\b(?:autoplay|auto-play|auto\s+play|similar|like\s+this|like\s+that|same\s+vibe|song|track|music|radio|play)\b",
                        _c,
                        re.IGNORECASE,
                    )
                )
            ):
                _m_yt = re.search(
                    r"https?://(?:music\.|www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([0-9A-Za-z_-]{11})",
                    _c,
                )
                if _m_yt:
                    try:
                        _meta = await run_in_worker(get_music_metadata, _m_yt.group(1))
                        _s = str(_meta.get("song") or "").strip()
                        _a = str(_meta.get("artist") or "").strip()
                        if _s and _s.lower() not in ("unknown", "unknown track", "youtube music"):
                            _card_tag = (
                                f"[Replying to song card: '{_s}' by '{_a}']"
                                if (_a and _a.lower() not in ("unknown", "unknown artist"))
                                else f"[Replying to song card: '{_s}' by 'Unknown']"
                            )
                            if isinstance(_m, dict):
                                _m["content"] = f"{_c} {_card_tag}"
                            else:
                                setattr(_m, "content", f"{_c} {_card_tag}")
                    except Exception:
                        pass
            break

    if is_research:
        tools.reset_turn_visited_urls()
        if turn_history is None:
            tools.VISITED_URLS.clear()

    turn_options = dict(mode_config["ollama_options"])
    if fast_voice_turn:
        # Keep num_ctx identical to mode_config so Ollama reuses its warm system+tools
        # KV cache in 0ms and cap spoken output tokens for ultra-low-latency TTS replies.
        turn_options["num_predict"] = min(int(turn_options.get("num_predict", 1024)), 140)

    def _format_tool_output_for_turn(raw_output: str) -> str:
        text_out = str(raw_output or "")
        if not fast_voice_turn:
            return enrich_tool_output_with_time_context(text_out)
        # Strip verbose Markdown-report system instructions on spoken voice turns so the LLM
        # replies in 1-2 short spoken sentences instead of generating a 320-token wall of text.
        stripped = text_out.split("\n\n[SYSTEM INSTRUCTION")[0].strip()
        if len(stripped) > 2500:
            stripped = stripped[:2500].rsplit("\n", 1)[0].strip() + "\n...(truncated for spoken brevity)"
        voice_rule = (
            "\n\n[VOICE CO-HOST SPOKEN MODE: The user asked this live in a Discord voice channel. "
            "Summarize the top takeaway in 1-2 short, natural spoken sentences (under 45 words). "
            "Do NOT output Markdown headers, bullet lists, tables, or URLs.]"
        )
        return enrich_tool_output_with_time_context(stripped + voice_rule)

    base_kwargs = {
        "model": mode_config["model"],
        "options": turn_options,
    }

    async def final_answer_without_tools(nudge):
        """Ask the model for a final answer with tools hidden and thinking disabled so all tokens go to visible text."""
        while history:
            last_msg = history[-1]
            last_role = last_msg.get("role") if isinstance(last_msg, dict) else getattr(last_msg, "role", None)
            last_tools = last_msg.get("tool_calls") if isinstance(last_msg, dict) else getattr(last_msg, "tool_calls", None)
            if last_role == "assistant" and not last_tools:
                history.pop()
            else:
                break

        compact_intra_turn_tool_history(history)
        # Find the start of the current user turn so we ONLY include tool results from this turn,
        # never stale tool results from earlier turns in the channel history!
        last_user_idx = 0
        for idx in range(len(history) - 1, -1, -1):
            r = history[idx].get("role") if isinstance(history[idx], dict) else getattr(history[idx], "role", None)
            if r == "user":
                last_user_idx = idx
                break

        # Convert tool_calls / role="tool" messages into plain text context so Gemma 4's template
        # never primes the model to emit raw <|tool_call> tags while `tools` is omitted.
        clean_messages = []
        for idx, m in enumerate(history):
            m_role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            m_content = str((m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or "")
            if m_role == "tool":
                if idx < last_user_idx:
                    continue
                t_name = (m.get("name") if isinstance(m, dict) else getattr(m, "name", None)) or "tool"
                clean_messages.append({"role": "user", "content": f"[Tool result from {t_name}]:\n{m_content}"})
            elif m_role == "assistant":
                if m_content.strip() and m_content.strip() != "⚠️ Generated an empty response.":
                    clean_messages.append({"role": "assistant", "content": m_content})
            else:
                clean_messages.append({"role": m_role or "user", "content": m_content})

        fallback_messages = clean_messages + [{"role": "user", "content": nudge}]
        try:
            response = await ollama_client.chat(
                **base_kwargs,
                messages=fallback_messages,
                think=False,
            )
        except TypeError:
            response = await ollama_client.chat(
                **base_kwargs,
                messages=fallback_messages,
            )
        text = response.message.content or ""
        if text.strip():
            history.append({"role": "assistant", "content": text})
        return text

    _TERMINAL_SKILLS = {
        "skill_sports_info",
        "skill_sports_esports",
        "skill_sports_odds",
        "skill_sports_tracker",
        "skill_flight_info",
        "skill_flight_tracker",
        "skill_markets_info",
        "skill_graphing_info",
        "skill_voice_info",
        "skill_job_manager",
        "skill_weather_info",
        "skill_news_headlines",
        "skill_music_analysis",
        "search_images",
        "evaluate_math",
        "save_memory",
    }
    hide_tools_next_step = False
    fast_image_caption_next_step = False
    iterations = 0
    reply = ""
    while iterations < max_iterations:
        if iterations >= 2:
            compact_intra_turn_tool_history(history)

        # Dynamically construct chat arguments so tools are completely hidden
        # from models that can't handle them (or after self-contained terminal skills have returned).
        # On fast_voice_turn, keep `tools` in chat_kwargs so Ollama's 6,200-token system+tools
        # KV cache prefix stays 100% warm across Step 0 -> Step 1 and across consecutive spoken turns.
        chat_kwargs = dict(base_kwargs, messages=history)
        if supports_tools and tool_functions and (fast_voice_turn or not hide_tools_next_step):
            chat_kwargs["tools"] = tool_functions

        fast_image_step0 = (
            iterations == 0
            and supports_tools
            and "search_images" in active_tools
            and detect_missed_image_tool_call(history, active_channel_id=channel_id) is not None
        )
        if fast_voice_turn or fast_image_caption_next_step or fast_image_step0:
            try:
                response = await ollama_client.chat(**chat_kwargs, think=False)
            except TypeError:
                response = await ollama_client.chat(**chat_kwargs)
        else:
            response = await ollama_client.chat(**chat_kwargs)
        history.append(response.message)

        tool_calls = getattr(response.message, "tool_calls", None)
        if fast_voice_turn and hide_tools_next_step and tool_calls:
            if (response.message.content or "").strip():
                tool_calls = None
            else:
                reply = await final_answer_without_tools(
                    "Give a 1-2 sentence spoken summary of the result above with no Markdown or URLs."
                )
                break
        if not tool_calls and iterations == 0 and supports_tools and tool_functions and not hide_tools_next_step:
            step0_reply = response.message.content or ""
            step0_done = str(getattr(response, "done_reason", "") or "").lower()
            step0_thinking = len(getattr(response.message, "thinking", "") or "")
            if (not step0_reply.strip() or step0_done == "length") and step0_thinking > 0:
                print(
                    f"⚠️ [{current_mode.upper()}] Step 0 thinking truncated before tool/reply emission "
                    f"(done_reason={step0_done or 'n/a'}, thinking_chars={step0_thinking}); retrying step 0 with think=False and tools enabled."
                )
                history.pop()
                try:
                    response = await ollama_client.chat(**chat_kwargs, think=False)
                except TypeError:
                    response = await ollama_client.chat(**chat_kwargs)
                history.append(response.message)
                tool_calls = getattr(response.message, "tool_calls", None)

        if not tool_calls:
            if iterations == 0 and supports_tools:
                missed = (
                    detect_missed_job_tool_call(history, active_channel_id=channel_id)
                    or detect_missed_sports_tool_call(history, active_channel_id=channel_id)
                    or detect_missed_music_shift_tool_call(history)
                    or detect_missed_flight_tool_call(history, active_channel_id=channel_id)
                    or detect_missed_graphing_tool_call(history, active_channel_id=channel_id)
                    or detect_missed_markets_tool_call(history, active_channel_id=channel_id)
                    or detect_missed_image_tool_call(history, active_channel_id=channel_id)
                    or detect_missed_voice_tool_call(history, active_channel_id=channel_id)
                )
                if missed and missed[0] in active_tools:
                    forced_tool_name, forced_raw_args = missed
                    func = active_tools[forced_tool_name]
                    forced_args = normalize_tool_args(forced_tool_name, func, forced_raw_args)
                    history.pop()  # Discard stale non-tool reply from iteration 0
                    if forced_tool_name in ("skill_flight_info", "skill_flight_tracker"):
                        forced_args = enrich_flight_tool_args(forced_args, history)
                    elif forced_tool_name == "skill_job_manager":
                        forced_args = enrich_job_tool_args(forced_args, history, active_channel_id=channel_id)
                    elif forced_tool_name == "skill_markets_info":
                        forced_args = enrich_markets_tool_args(forced_args, history, active_channel_id=channel_id)
                    elif forced_tool_name == "skill_graphing_info":
                        forced_args = enrich_graphing_tool_args(forced_args, history, active_channel_id=channel_id)
                    elif forced_tool_name == "search_images":
                        forced_args = enrich_image_tool_args(forced_args, history, active_channel_id=channel_id)
                    elif forced_tool_name == "skill_voice_info":
                        forced_args = enrich_voice_tool_args(forced_args, history, active_channel_id=channel_id)
                        if (
                            not forced_args.get("voice_profile")
                            and forced_args.get("action") == "join"
                            and VOICE_COHOST.get_session(channel_id) is None
                        ):
                            forced_args = dict(forced_args)
                            forced_args["voice_profile"] = current_mode
                    set_active_channel_id(channel_id)
                    print(f"⚡ [{current_mode.upper()}] Intercepted missed tool on turn 0 -> forcing {forced_tool_name}({forced_args})")
                    try:
                        tool_output = await run_in_worker(func, **forced_args)
                        if forced_tool_name == "skill_voice_info" and VOICE_COHOST.get_session(channel_id) is not None:
                            vc_conn = await VOICE_COHOST.ensure_discord_voice_connected(channel_id)
                            if vc_conn is None and VOICE_COHOST.get_session(channel_id) is None:
                                tool_output = VOICE_COHOST.format_session_status(channel_id)
                    except Exception as e:
                        print(f"  └─ ❌ Forced tool {forced_tool_name} crashed: {e}")
                        tool_output = f"Tool execution failed: {e}"
                    if SPORTS_TRACKER.tracked_matches:
                        SPORTS_TRACKER.ensure_loop_running()
                    if FLIGHT_TRACKER.tracked_flights or FLIGHT_TRACKER.price_watches:
                        FLIGHT_TRACKER.ensure_loop_running()
                    if JOB_ENGINE.has_active_work():
                        JOB_ENGINE.ensure_loop_running()
                    history.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"function": {"name": forced_tool_name, "arguments": forced_args}}],
                    })
                    history.append({
                        "role": "tool",
                        "name": forced_tool_name,
                        "content": _format_tool_output_for_turn(str(tool_output)),
                    })
                    if forced_tool_name == "search_images":
                        hide_tools_next_step = True
                        fast_image_caption_next_step = True
                    elif forced_tool_name == "skill_job_manager":
                        hide_tools_next_step = True
                    elif fast_voice_turn and (
                        forced_tool_name in _TERMINAL_SKILLS
                        or "youtube.com/watch?v=" in str(tool_output)
                    ):
                        hide_tools_next_step = True
                    iterations += 1
                    continue

            reply = response.message.content or ""
            thinking_len = len(getattr(response.message, "thinking", "") or "")
            done_reason = str(getattr(response, "done_reason", "") or "").lower()
            has_complete_yt_link = bool(
                re.search(r"https?:\/\/(?:www\.|music\.)?youtube\.com\/watch\?v=[0-9A-Za-z_-]{11}", reply)
            )
            if fast_voice_turn:
                is_truncated = not reply.strip()
            else:
                is_truncated = (
                    not reply.strip()
                    or done_reason == "length"
                    or (thinking_len > 400 and len(reply.strip()) < 90 and not has_complete_yt_link)
                    or bool(re.search(r"\[[^\]]*\]\(<?https?://[^\s)>]*$", reply.strip()))
                )
            # Nudge with think=False if the model returned empty or mid-generation truncated content
            if is_truncated and (iterations > 0 or is_research or thinking_len > 0):
                print(
                    f"⚠️ [{current_mode.upper()}] Truncated/empty content on step {iterations} "
                    f"(done_reason={done_reason or 'n/a'}, content_chars={len(reply)}, thinking_chars={thinking_len}); "
                    f"forcing complete final answer with think=False."
                )
                turn_used_news = any(
                    (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "tool"
                    and (m.get("name") if isinstance(m, dict) else getattr(m, "name", None)) == "skill_news_headlines"
                    for m in history[-6:]
                )
                if fast_voice_turn:
                    nudge_msg = "Give a 1-2 sentence spoken summary of the result above with no Markdown or URLs."
                elif iterations > 0 and turn_used_news:
                    nudge_msg = (
                        "Now summarize and give your complete final answer based on the tool results above. "
                        "Cover all sections from the tool results (including the Daily Market & Macro Pulse if present) with complete URLs."
                    )
                elif iterations > 0:
                    nudge_msg = (
                        "Now give your complete, direct final answer based only on the latest tool result above, "
                        "preserving any verified URLs."
                    )
                else:
                    nudge_msg = "Give your direct, concise final answer to the user's message now."
                reply = await final_answer_without_tools(nudge_msg)
            break

        # Guardrail for spoken voice turns: if the model called skill_voice_info(action='speak', query=...)
        # to answer a spoken question, return that spoken text directly in 1 step instead of playing TTS
        # inside the tool and triggering a second "I said hello..." summary step.
        if fast_voice_turn and len(tool_calls) == 1 and tool_calls[0].function.name == "skill_voice_info":
            raw_v_args = dict(tool_calls[0].function.arguments or {})
            v_act = str(raw_v_args.get("action") or "").strip().lower()
            v_query = str(raw_v_args.get("query") or "").strip()
            v_prof = str(raw_v_args.get("voice_profile") or "").strip()
            if v_act in ("speak", "say", "talk", "announce") and v_query:
                if v_prof:
                    VOICE_COHOST.set_voice_profile(channel_id, v_prof)
                history.pop()
                reply = v_query
                history.append({"role": "assistant", "content": reply})
                break

        if (
            iterations == 0
            and supports_tools
            and "skill_graphing_info" in active_tools
            and not any(c.function.name == "skill_graphing_info" for c in tool_calls)
            and any(c.function.name == "skill_markets_info" for c in tool_calls)
        ):
            missed_graph = detect_missed_graphing_tool_call(history, active_channel_id=channel_id)
            if missed_graph and missed_graph[0] == "skill_graphing_info":
                print(f"⚡ [{current_mode.upper()}] Co-executing missed skill_graphing_info alongside skill_markets_info: {missed_graph[1]}")
                tool_calls = list(tool_calls) + [
                    types.SimpleNamespace(
                        function=types.SimpleNamespace(
                            name="skill_graphing_info",
                            arguments=missed_graph[1],
                        )
                    )
                ]

        if (
            iterations == 0
            and supports_tools
            and "search_images" in active_tools
            and not any(c.function.name in ("search_images", "skill_graphing_info") for c in tool_calls)
            and any(c.function.name in ("search_web", "read_website") for c in tool_calls)
        ):
            missed_img = detect_missed_image_tool_call(history, active_channel_id=channel_id)
            if missed_img and missed_img[0] == "search_images":
                print(f"⚡ [{current_mode.upper()}] Co-executing missed search_images alongside search_web: {missed_img[1]}")
                tool_calls = list(tool_calls) + [
                    types.SimpleNamespace(
                        function=types.SimpleNamespace(
                            name="search_images",
                            arguments=missed_img[1],
                        )
                    )
                ]

        # Compound Voice + Music/Autoplay co-execution: if the user asked to "join voice and play dnb"
        # and the model only emitted skill_voice_info(action='join') on step 0, co-execute the music/autoplay tool!
        if (
            iterations == 0
            and supports_tools
            and any(c.function.name == "skill_voice_info" for c in tool_calls)
            and not any(c.function.name in ("skill_music_discovery", "skill_youtube_search", "skill_job_manager") for c in tool_calls)
        ):
            missed_voice_music = (
                detect_missed_job_tool_call(history, active_channel_id=channel_id)
                or detect_missed_music_shift_tool_call(history)
            )
            if missed_voice_music and missed_voice_music[0] in active_tools:
                print(
                    f"⚡ [{current_mode.upper()}] Co-executing compound {missed_voice_music[0]} alongside skill_voice_info: {missed_voice_music[1]}"
                )
                tool_calls = list(tool_calls) + [
                    types.SimpleNamespace(
                        function=types.SimpleNamespace(
                            name=missed_voice_music[0],
                            arguments=missed_voice_music[1],
                        )
                    )
                ]

        # Compound Domain/System Tool + Scheduled Job co-execution: when the user asks to set a reminder,
        # recurring interval, daily schedule, or conditional watch (e.g. "check BTC every 5m", "remind me in
        # 20m to check the oven", "create a chart for BTC/USD now and every morning at 9:01 AM") and the LLM
        # called a domain or system tool (`save_memory`, `get_current_datetime`, `skill_markets_info`,
        # `skill_weather_info`, `skill_graphing_info`, etc.) without calling `skill_job_manager`, co-execute
        # (or replace `save_memory` with) `skill_job_manager` so the job is always registered in JOB_ENGINE.
        if iterations == 0 and supports_tools:
            if (
                not any(c.function.name in ("skill_job_manager", "skill_sports_tracker", "skill_flight_tracker") for c in tool_calls)
                and "skill_job_manager" in active_tools
            ):
                missed_job = detect_missed_job_tool_call(history, active_channel_id=channel_id)
                if missed_job and missed_job[0] == "skill_job_manager" and (missed_job[1] or {}).get("action") != "autoplay":
                    last_u_raw = next(
                        (
                            str((m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or "")
                            for m in reversed(history)
                            if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
                        ),
                        "",
                    )
                    if any(c.function.name == "save_memory" for c in tool_calls) and not re.search(
                        r"\b(?:remember|memorize|save\s+to\s+memory)\b", last_u_raw, re.IGNORECASE
                    ):
                        tool_calls = [c for c in tool_calls if c.function.name != "save_memory"]
                    print(f"⚡ [{current_mode.upper()}] Co-executing missed skill_job_manager alongside {[c.function.name for c in tool_calls]}: {missed_job[1]}")
                    tool_calls = list(tool_calls) + [
                        types.SimpleNamespace(
                            function=types.SimpleNamespace(
                                name="skill_job_manager",
                                arguments=missed_job[1],
                            )
                        )
                    ]
            elif (
                any(c.function.name == "skill_job_manager" for c in tool_calls)
                and not any(c.function.name == "skill_graphing_info" for c in tool_calls)
                and "skill_graphing_info" in active_tools
            ):
                last_u_raw = next(
                    (
                        str((m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or "")
                        for m in reversed(history)
                        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
                    ),
                    "",
                )
                if re.search(r"\b(?:now|right\s+now|today|this\s+(?:morning|afternoon|evening))\s+and\s+(?:every|daily)\b", last_u_raw, re.IGNORECASE):
                    missed_graph = detect_missed_graphing_tool_call(history, active_channel_id=channel_id)
                    if missed_graph and missed_graph[0] == "skill_graphing_info":
                        print(f"⚡ [{current_mode.upper()}] Co-executing compound skill_graphing_info alongside skill_job_manager: {missed_graph[1]}")
                        tool_calls = list(tool_calls) + [
                            types.SimpleNamespace(
                                function=types.SimpleNamespace(
                                    name="skill_graphing_info",
                                    arguments=missed_graph[1],
                                )
                            )
                        ]

        # Single-image-per-turn guard: prevent duplicate parallel search_images calls in the same step
        if sum(1 for c in tool_calls if c.function.name == "search_images") > 1:
            seen_img_call = False
            deduped_calls = []
            for c in tool_calls:
                if c.function.name == "search_images":
                    if seen_img_call:
                        continue
                    seen_img_call = True
                deduped_calls.append(c)
            tool_calls = deduped_calls

        # Autoplay Start Interception: when the user explicitly asks to start/run Autoplay (e.g. "start autoplay
        # similar to this <youtube link>" or "autoplay similar to Strobe") and the LLM called any other tool
        # (e.g. search_web, read_youtube_video, skill_music_discovery, skill_youtube_search) instead of
        # skill_job_manager, convert it to skill_job_manager(action='autoplay') so the Autoplay job is registered
        # in JOB_ENGINE with a live countdown timer.
        if (
            supports_tools
            and "skill_job_manager" in active_tools
            and not any(c.function.name == "skill_job_manager" for c in tool_calls)
            and tool_calls
        ):
            last_u_raw = next(
                (
                    str((m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or "")
                    for m in reversed(history)
                    if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
                ),
                "",
            )
            if not last_u_raw.strip().startswith("Execute scheduled task:"):
                missed_auto = detect_missed_job_tool_call(history, active_channel_id=channel_id)
                if missed_auto and missed_auto[0] == "skill_job_manager" and (missed_auto[1] or {}).get("action") == "autoplay":
                    music_call = next(
                        (
                            c for c in tool_calls
                            if c.function.name in ("skill_music_discovery", "skill_youtube_search", "read_youtube_video")
                        ),
                        tool_calls[0],
                    )
                    m_args = dict((music_call.function.arguments if music_call else None) or {})
                    base_auto_q = str((missed_auto[1] or {}).get("query") or "").strip()
                    low_auto_q = base_auto_q.lower()
                    if "youtube.com/watch" not in low_auto_q and "youtu.be/" not in low_auto_q and " by " not in low_auto_q:
                        if m_args.get("url") and ("youtube.com/watch" in str(m_args.get("url")).lower() or "youtu.be/" in str(m_args.get("url")).lower()):
                            base_auto_q = f"autoplay similar to {m_args['url']}"
                        elif m_args.get("similar_to"):
                            base_auto_q = f"autoplay similar to {m_args['similar_to']}"
                        elif m_args.get("genre"):
                            base_auto_q = f"autoplay {m_args['genre']}"
                        elif m_args.get("artist"):
                            base_auto_q = f"autoplay similar to {m_args['artist']}"
                    auto_args = enrich_job_tool_args(
                        {"action": "autoplay", "query": base_auto_q},
                        history,
                        active_channel_id=channel_id,
                    )
                    print(
                        f"⚡ [{current_mode.upper()}] Intercepted {music_call.function.name if music_call else 'tool'} "
                        f"on explicit Autoplay prompt -> redirecting to skill_job_manager: {auto_args}"
                    )
                    tool_calls = [
                        c for c in tool_calls
                        if c.function.name == "skill_voice_info"
                    ] + [
                        types.SimpleNamespace(
                            function=types.SimpleNamespace(
                                name="skill_job_manager",
                                arguments=auto_args,
                            )
                        )
                    ]
                elif (
                    "skill_music_discovery" in active_tools
                    and any(c.function.name in ("read_youtube_video", "search_web", "read_website") for c in tool_calls)
                    and not any(c.function.name == "skill_music_discovery" for c in tool_calls)
                    and (
                        "music.youtube.com/watch" in last_u_raw.lower()
                        or (
                            ("youtube.com/watch" in last_u_raw.lower() or "youtu.be/" in last_u_raw.lower())
                            and re.search(r"\b(?:similar\s+to|like\s+this|like\s+that|same\s+vibe|songs?\s+like|tracks?\s+like)\b", last_u_raw, re.IGNORECASE)
                        )
                    )
                    and not re.search(r"\b(?:summarize|summary|transcript|lyrics|what\s+do\s+they\s+say|about)\b", last_u_raw, re.IGNORECASE)
                ):
                    yt_call = next((c for c in tool_calls if c.function.name in ("read_youtube_video", "search_web", "read_website")), None)
                    yt_url = str(((yt_call.function.arguments if yt_call else None) or {}).get("url") or "").strip() or last_u_raw
                    print(
                        f"⚡ [{current_mode.upper()}] Intercepted {yt_call.function.name if yt_call else 'tool'} on music similarity/YouTube Music prompt "
                        f"-> redirecting to skill_music_discovery(similar_to='{yt_url}')"
                    )
                    tool_calls = [c for c in tool_calls if c.function.name not in ("read_youtube_video", "search_web", "read_website")] + [
                        types.SimpleNamespace(
                            function=types.SimpleNamespace(
                                name="skill_music_discovery",
                                arguments={"similar_to": yt_url},
                            )
                        )
                    ]
                elif (
                    "read_youtube_video" in active_tools
                    and any(c.function.name in ("skill_youtube_search", "read_website", "search_web") for c in tool_calls)
                    and not any(c.function.name == "read_youtube_video" for c in tool_calls)
                    and ("youtube.com/watch" in last_u_raw.lower() or "youtu.be/" in last_u_raw.lower() or "youtube.com/live/" in last_u_raw.lower() or "youtube.com/shorts/" in last_u_raw.lower())
                    and "music.youtube.com/watch" not in last_u_raw.lower()
                    and not re.search(r"\b(?:similar\s+to|like\s+this|like\s+that|same\s+vibe|songs?\s+like|tracks?\s+like|play\s+something)\b", last_u_raw, re.IGNORECASE)
                ):
                    m_yt_link = re.search(
                        r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s<>\"'`)]+|live/[^\s<>\"'`)]+|shorts/[^\s<>\"'`)]+)|youtu\.be/[^\s<>\"'`)]+)",
                        last_u_raw,
                        re.IGNORECASE,
                    )
                    if m_yt_link:
                        yt_direct_url = m_yt_link.group(0).rstrip(".,;)>\"'")
                        print(
                            f"⚡ [{current_mode.upper()}] Intercepted {tool_calls[0].function.name} on direct YouTube video link "
                            f"-> redirecting to read_youtube_video(url='{yt_direct_url}')"
                        )
                        tool_calls = [
                            c for c in tool_calls
                            if c.function.name not in ("skill_youtube_search", "read_website", "search_web")
                        ] + [
                            types.SimpleNamespace(
                                function=types.SimpleNamespace(
                                    name="read_youtube_video",
                                    arguments={"url": yt_direct_url},
                                )
                            )
                        ]

        # Unrequested Autoplay Guardrail: when Autoplay is NOT active in the channel and the user did NOT
        # explicitly ask to start Autoplay (e.g. user simply said "play dnb", "play liquid dnb", or asked
        # "who told you to autoplay?"), prevent a hallucinated skill_job_manager(action='autoplay') call
        # from starting continuous Autoplay; redirect plain music requests to skill_music_discovery instead.
        if (
            supports_tools
            and any(c.function.name == "skill_job_manager" for c in tool_calls)
            and JOB_ENGINE.get_active_autoplay_job(channel_id) is None
        ):
            last_u_raw = next(
                (
                    str((m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or "")
                    for m in reversed(history)
                    if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
                ),
                "",
            )
            if not last_u_raw.strip().startswith("Execute scheduled task:") and not has_explicit_autoplay_start_intent(last_u_raw):
                legit_job_intent = detect_missed_job_tool_call(history, active_channel_id=channel_id)
                rewritten_calls = []
                for c in tool_calls:
                    if c.function.name != "skill_job_manager":
                        rewritten_calls.append(c)
                        continue
                    c_args = dict(c.function.arguments or {})
                    c_act = str(c_args.get("action") or "create").strip().lower()
                    c_query = str(c_args.get("query") or c_args.get("task") or "").strip()
                    c_when = str(c_args.get("when") or "").strip()
                    has_schedule_or_reminder_signal = bool(
                        re.search(
                            r"\b(?:remind|reminder|timer|alarm|schedule|alert|notify|ping|when|whenever|if|once|every|daily|tomorrow|tonight|in\s+\d+|at\s+\d+)\b",
                            f"{c_query} {last_u_raw}",
                            re.IGNORECASE,
                        )
                    )
                    is_unrequested_autoplay = (
                        c_act in ("autoplay", "skip", "next")
                        or "autoplay" in c_query.lower()
                        or (c_act == "create" and not c_when and legit_job_intent is None and not has_schedule_or_reminder_signal)
                    )
                    if not is_unrequested_autoplay:
                        rewritten_calls.append(c)
                        continue
                    if is_autoplay_meta_or_negation(last_u_raw):
                        print(
                            f"🛡️ [{current_mode.upper()}] Suppressed hallucinated skill_job_manager({c_args}) "
                            f"on autoplay meta-question/negation: '{last_u_raw}'"
                        )
                        continue
                    if any(other.function.name in ("skill_music_discovery", "skill_youtube_search") for other in tool_calls if other is not c):
                        print(
                            f"🛡️ [{current_mode.upper()}] Dropped unrequested skill_job_manager({c_args}) "
                            f"alongside existing music tool on single-song prompt."
                        )
                        continue
                    music_fallback = detect_missed_music_shift_tool_call(history)
                    if music_fallback and music_fallback[0] in active_tools:
                        fb_name, fb_args = music_fallback
                    elif "skill_music_discovery" in active_tools:
                        clean_g = re.sub(
                            r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+)?(?:auto[-\s]?play|play|put\s+on|drop|queue)\s+(?:some\s+|a\s+|an\s+)?",
                            "",
                            c_query,
                            flags=re.IGNORECASE,
                        ).strip()
                        clean_g = re.sub(r"\s+(?:music|song|songs|track|tracks)\s*$", "", clean_g, flags=re.IGNORECASE).strip()
                        fb_name = "skill_music_discovery"
                        if re.match(r"^(?:similar\s+to|like)\s+", clean_g, re.IGNORECASE) or " by " in clean_g.lower():
                            sim_seed = re.sub(r"^(?:similar\s+to|like)\s+", "", clean_g, flags=re.IGNORECASE).strip()
                            fb_args = {"similar_to": sim_seed}
                        else:
                            fb_args = {"genre": clean_g or "dnb"}
                    else:
                        continue
                    print(
                        f"⚡ [{current_mode.upper()}] Intercepted unrequested skill_job_manager({c_args}) "
                        f"on single-song prompt -> redirecting to {fb_name}({fb_args})"
                    )
                    rewritten_calls.append(
                        types.SimpleNamespace(
                            function=types.SimpleNamespace(
                                name=fb_name,
                                arguments=fb_args,
                            )
                        )
                    )
                tool_calls = rewritten_calls
                if not tool_calls:
                    reply = await final_answer_without_tools(
                        "Give your direct, concise final answer to the user's message now."
                    )
                    break

        # Single-Autoplay-Card guard: prevent running skill_job_manager and skill_music_discovery/skill_youtube_search
        # in the same step when starting Autoplay or when Autoplay is already active in the channel.
        if (
            any(c.function.name == "skill_job_manager" for c in tool_calls)
            and any(c.function.name in ("skill_music_discovery", "skill_youtube_search", "read_youtube_video") for c in tool_calls)
        ):
            active_auto = JOB_ENGINE.get_active_autoplay_job(channel_id)
            if active_auto is not None:
                # Autoplay is already active: let skill_music_discovery/skill_youtube_search play the requested track/shift/genre,
                # and sync_channel_autoplay_on_track_played will automatically reset and sync the Autoplay timer!
                last_u_text = next(
                    (
                        str((m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or "").lower()
                        for m in reversed(history)
                        if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
                    ),
                    "",
                )
                user_explicitly_cancelling = bool(
                    re.search(r"\b(?:cancel|stop|remove|delete|clear|end|kill|turn\s+off|shut\s+off|disable)\b", last_u_text)
                )
                tool_calls = [
                    c for c in tool_calls
                    if not (
                        c.function.name == "skill_job_manager"
                        and (
                            not user_explicitly_cancelling
                            or str((c.function.arguments or {}).get("action", "create")).lower() not in ("cancel", "stop", "clear", "list", "status")
                        )
                    )
                ]
            else:
                # Starting Autoplay: skill_job_manager(action='autoplay') already resolves Track 1, so drop duplicate music tool calls.
                has_autoplay_job_call = any(
                    c.function.name == "skill_job_manager"
                    and (
                        str((c.function.arguments or {}).get("action", "")).lower() in ("autoplay", "skip", "next")
                        or "autoplay" in str((c.function.arguments or {}).get("query", "")).lower()
                    )
                    for c in tool_calls
                )
                if has_autoplay_job_call:
                    tool_calls = [c for c in tool_calls if c.function.name not in ("skill_music_discovery", "skill_youtube_search", "read_youtube_video")]

        print(f"⚡ [{current_mode.upper()}] Model requested {len(tool_calls)} tools simultaneously!")
        has_top_music = any(call.function.name == "skill_music_discovery" for call in tool_calls)
        has_explicit_markets = any(call.function.name == "skill_markets_info" for call in tool_calls)
        has_sibling_graph_calls = sum(1 for call in tool_calls if call.function.name == "skill_graphing_info") > 1
        per_tool_char_budget = 12000 if len(tool_calls) == 1 else max(5000, 20000 // len(tool_calls))

        async def run_tool(call):
            tool_name = call.function.name
            raw_args = call.function.arguments or {}
            func = active_tools.get(tool_name)
            norm_args = normalize_tool_args(tool_name, func, raw_args) if func else (raw_args if isinstance(raw_args, dict) else {})

            # Guardrail: Prevent running youtube searcher (in music mode) in parallel with music discovery
            if tool_name == "skill_youtube_search" and has_top_music and bool(norm_args.get("is_music")):
                print(f"  └─ ⚠️ [Orchestrator]: Blocked parallel {tool_name}(is_music=True) call while skill_music_discovery is executing.")
                return {
                    "role": "tool",
                    "name": tool_name,
                    "content": (
                        "PARALLEL_EXECUTION_BLOCKED: You cannot call `skill_youtube_search(is_music=True)` at the same time as `skill_music_discovery`. "
                        "You must first review the songs returned by `skill_music_discovery` in this step, select ONE specific song, "
                        "and then call `skill_youtube_search(query='<Song Title> by <Artist Name>', is_music=True)` in your NEXT step to get its link."
                    )
                }

            if tool_name in active_tools:
                args = norm_args
                if tool_name in ("skill_sports_info", "skill_sports_tracker", "skill_sports_odds"):
                    sibling_tools = [c.function.name for c in tool_calls if c is not call]
                    args = enrich_sports_tool_args(args, history, sibling_tools=sibling_tools)
                elif tool_name == "skill_music_discovery":
                    args = enrich_music_tool_args(args, history)
                elif tool_name in ("skill_flight_info", "skill_flight_tracker"):
                    args = enrich_flight_tool_args(args, history)
                elif tool_name == "skill_markets_info":
                    args = enrich_markets_tool_args(args, history, active_channel_id=channel_id)
                elif tool_name == "skill_graphing_info":
                    args = enrich_graphing_tool_args(
                        args,
                        history,
                        active_channel_id=channel_id,
                        has_sibling_graph_calls=has_sibling_graph_calls,
                    )
                elif tool_name == "skill_job_manager":
                    args = enrich_job_tool_args(args, history, active_channel_id=channel_id)
                elif tool_name == "search_images":
                    args = enrich_image_tool_args(args, history, active_channel_id=channel_id)
                elif tool_name == "read_youtube_video":
                    args = enrich_youtube_tool_args(args, history)
                elif tool_name == "skill_voice_info":
                    args = enrich_voice_tool_args(args, history, active_channel_id=channel_id)
                    if (
                        not args.get("voice_profile")
                        and args.get("action") == "join"
                        and VOICE_COHOST.get_session(channel_id) is None
                    ):
                        args = dict(args)
                        args["voice_profile"] = current_mode
                set_active_channel_id(channel_id)
                print(f"  └─ 🤖 Running tool: {tool_name} with args: {args}")
                try:
                    topic_arg = str(args.get("topic") or "").strip().lower()
                    user_mentions_markets = bool(
                        re.search(r"\b(markets?|stocks?|indices|s&p|nasdaq|dow|tsx)\b", str(history[-2].get("content", "") if len(history) >= 2 and isinstance(history[-2], dict) else ""), re.IGNORECASE)
                    )
                    if (
                        tool_name == "skill_news_headlines"
                        and (not fast_voice_turn or user_mentions_markets)
                        and not has_explicit_markets
                        and topic_arg not in ("entertainment", "sports", "science", "health")
                    ):
                        from src.skills.markets import get_daily_market_pulse
                        news_res, pulse_res = await asyncio.gather(
                            run_in_worker(func, **args),
                            run_in_worker(get_daily_market_pulse),
                        )
                        if pulse_res:
                            tool_output = f"{pulse_res}\n{news_res}" if user_mentions_markets else f"{news_res}\n{pulse_res}"
                        else:
                            tool_output = news_res
                    else:
                        tool_output = await run_in_worker(func, **args)
                        if tool_name == "skill_voice_info" and VOICE_COHOST.get_session(channel_id) is not None:
                            vc_conn = await VOICE_COHOST.ensure_discord_voice_connected(channel_id)
                            if vc_conn is None and VOICE_COHOST.get_session(channel_id) is None:
                                tool_output = VOICE_COHOST.format_session_status(channel_id)
                except Exception as e:
                    print(f"  └─ ❌ Tool {tool_name} crashed: {e}")
                    tool_output = f"Tool execution failed: {e}"
            else:
                print(f"  └─ ⚠ Unknown tool requested: {tool_name}")
                tool_output = f"Tool '{tool_name}' not available."

            enriched_content = _format_tool_output_for_turn(str(tool_output))
            if len(tool_calls) > 1 and len(enriched_content) > per_tool_char_budget:
                from src.utils.timezones import _sanitize_and_bound_tool_output
                enriched_content = _sanitize_and_bound_tool_output(enriched_content, max_chars=per_tool_char_budget)

            return {
                "role": "tool",
                "name": tool_name,
                "content": enriched_content,
            }

        tool_results = await asyncio.gather(*(run_tool(call) for call in tool_calls))
        if SPORTS_TRACKER.tracked_matches:
            SPORTS_TRACKER.ensure_loop_running()
        if FLIGHT_TRACKER.tracked_flights or FLIGHT_TRACKER.price_watches:
            FLIGHT_TRACKER.ensure_loop_running()
        if JOB_ENGINE.has_active_work():
            JOB_ENGINE.ensure_loop_running()
        history.extend(tool_results)
        called_names = {c.function.name for c in tool_calls}
        latest_user_text = next(
            (
                str(m.get("content") if isinstance(m, dict) else getattr(m, "content", "") or "")
                for m in reversed(history)
                if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "user"
            ),
            "",
        )
        latest_user_clean = re.sub(r"\[Spoken in Discord Voice Channel[^\]]*\]", "", latest_user_text)
        has_compound_media_intent = bool(re.search(r"\b(play|song|track|music|video)\b", latest_user_clean, re.IGNORECASE))
        has_verified_music_link = any(
            r["name"] in ("skill_music_discovery", "skill_youtube_search", "skill_job_manager") and "youtube.com/watch?v=" in r["content"]
            for r in tool_results
        )
        if (
            (
                called_names.issubset(_TERMINAL_SKILLS)
                and (
                    len(tool_calls) >= 2
                    or (called_names.issubset({"skill_news_headlines", "skill_markets_info", "skill_graphing_info", "skill_voice_info", "search_images"}) and not has_compound_media_intent)
                    or called_names == {"skill_job_manager"}
                    or (fast_voice_turn and not has_compound_media_intent)
                )
            )
            or has_verified_music_link
        ) and all("PARALLEL_EXECUTION_BLOCKED" not in r["content"] and not r["content"].startswith("⚠️") for r in tool_results):
            hide_tools_next_step = True
            if "search_images" in called_names:
                fast_image_caption_next_step = True
        iterations += 1

    if iterations >= max_iterations and not reply.strip():
        # Step budget exhausted while still calling tools: make the model answer with what it has.
        print(f"⚠️ [{current_mode.upper()}] Reached max iterations ({max_iterations}); forcing a final answer.")
        try:
            reply = await final_answer_without_tools(
                "You have used all available tool steps. Do not call any more tools. "
                "Give your best final answer now using only the tool results above."
            )
        except Exception as e:
            print(f"⚠️ Final-answer call failed: {e}")
            reply = ""
        if not reply.strip() and is_research:
            reply = "⚠️ I reached the maximum step limit before I could finish. Please try a narrower question."

    # Post-turn Phantom-Schedule Auto-Recovery & Job ID Confirmation Guard:
    # 1. If `skill_job_manager` did NOT run during this turn, yet the user asked to schedule a job OR the LLM's
    #    reply claims it scheduled a reminder/task/timer/watch without a `J-XXX` ID, execute `skill_job_manager`
    #    now (or append a clear warning if unavailable) so the bot never claims a job is scheduled when `!jobs` is empty.
    # 2. Whenever `skill_job_manager` ran in this turn, ensure the `J-XXX` confirmation block (or `⚠️` warning)
    #    is preserved in `reply` even if the LLM paraphrased the tool output.
    if not fast_voice_turn and supports_tools and history:
        last_u_idx = -1
        last_u_text = ""
        for idx in range(len(history) - 1, -1, -1):
            r = history[idx].get("role") if isinstance(history[idx], dict) else getattr(history[idx], "role", None)
            if r == "user":
                last_u_idx = idx
                last_u_text = str(
                    (history[idx].get("content") if isinstance(history[idx], dict) else getattr(history[idx], "content", "")) or ""
                )
                break
        if last_u_idx >= 0 and not last_u_text.strip().startswith("Execute scheduled task:"):
            turn_tools_used = {
                (m.get("name") if isinstance(m, dict) else getattr(m, "name", None))
                for m in history[last_u_idx + 1 :]
                if (m.get("role") if isinstance(m, dict) else getattr(m, "role", None)) == "tool"
            }
            if not (turn_tools_used & {"skill_job_manager", "skill_flight_tracker", "skill_sports_tracker"}):
                missed_post = detect_missed_job_tool_call(history, active_channel_id=channel_id)
                needs_recovery = (
                    (missed_post and missed_post[0] == "skill_job_manager" and (missed_post[1] or {}).get("action") == "create")
                    or has_phantom_schedule_claim(reply, last_u_text)
                )
                if needs_recovery:
                    if "skill_job_manager" in active_tools:
                        job_func = active_tools["skill_job_manager"]
                        base_job_args = (missed_post[1] if missed_post else {"action": "create"}) or {"action": "create"}
                        rec_args = enrich_job_tool_args(
                            normalize_tool_args("skill_job_manager", job_func, base_job_args),
                            history,
                            active_channel_id=channel_id,
                        )
                        set_active_channel_id(channel_id)
                        print(
                            f"🛡️ [{current_mode.upper()}] Post-turn phantom schedule recovery -> executing skill_job_manager({rec_args})"
                        )
                        try:
                            rec_out = await run_in_worker(job_func, **rec_args)
                        except Exception as e:
                            rec_out = f"⚠️ Tool execution failed: {e}"
                        if JOB_ENGINE.has_active_work():
                            JOB_ENGINE.ensure_loop_running()
                        history.append(
                            {
                                "role": "tool",
                                "name": "skill_job_manager",
                                "content": _format_tool_output_for_turn(str(rec_out)),
                            }
                        )
                    else:
                        warn_unavail = (
                            "⚠️ **Scheduling Failed:** No background job was registered in `!jobs` because "
                            "`skill_job_manager` is not enabled in the current mode's tool configuration (`src/config.py`)."
                        )
                        reply = f"{warn_unavail}\n\n{reply.strip()}".strip() if reply.strip() else warn_unavail

    reply = ensure_turn_job_confirmation_preserved(reply, history, fast_voice_turn=fast_voice_turn)
    reply = ensure_turn_music_card_preserved(reply, history, fast_voice_turn=fast_voice_turn)
    return reply


@client.event
async def on_message(message):
    global current_mode
    if message.author == client.user:
        return

    if isinstance(message.channel, (discord.DMChannel, discord.GroupChannel)):
        if BOT_OWNER_ID is None or message.author.id != BOT_OWNER_ID:
            return

    record_channel_human_activity(message.channel.id)
    set_active_user(message.author.id, message.author.display_name)

    # Cache speaker details for auto-ping
    known_users[message.author.name.lower()] = message.author.id
    known_users[message.author.display_name.lower()] = message.author.id

    for m in message.mentions:
        if m != client.user:
            known_users[m.name.lower()] = m.id
            known_users[m.display_name.lower()] = m.id

    user_input = message.content.replace(f'<@{client.user.id}>', '').strip()
    
    # Mode Switching Logic
    if user_input.lower().startswith("!mode"):
        active_channels.add(message.channel.id)
        if BOT_OWNER_ID is None or message.author.id != BOT_OWNER_ID:
            print(f"🔒 [!mode denied]: {message.author} ({message.author.id})")
            await message.channel.send("🔒 Only the bot owner can switch modes.")
            return
        new_mode = user_input.split(" ")[-1].lower()
        if new_mode in MODES:
            previous_model = MODES[current_mode]["model"]
            new_model = MODES[new_mode]["model"]

            if previous_model != new_model:
                await message.channel.send(f"🧹 Unloading `{previous_model}` from GPU memory to make room...")
                try:
                    # Unload the previous model explicitly by setting keep_alive=0
                    await ollama_client.generate(model=previous_model, keep_alive=0)
                except Exception as e:
                    print(f"Note: Could not explicitly unload model: {e}")

            current_mode = new_mode
            # The mode is global, so every channel's history was built with the old
            # system prompt/persona. Wipe them all, not just this channel.
            chat_history.clear()
            clear_tool_caches()
            status_msg = f"🔄 Switched to **{new_mode.upper()}** mode (Model: `{new_model}`). Memory and context wiped."
            await message.channel.send(status_msg)
            await broadcast_mode_switch(
                sys.modules[__name__],
                status_msg,
                origin_channel_id=message.channel.id,
            )
        else:
            await message.channel.send(f"⚠️ Unknown mode. Available modes: {', '.join(MODES.keys())}")
        return

    if user_input.lower() == "!reset":
        active_channels.add(message.channel.id)
        chat_history.pop(message.channel.id, None)
        clear_tool_caches(message.channel.id)
        await message.channel.send("🧹 Memory and context wiped for this channel.")
        return

    # Register author's voice channel early so both tracker commands (!autoplay) and voice commands (!join) can use it
    author_voice_channel = _resolve_author_voice_channel(message.author, getattr(message, "guild", None), user_input)
    VOICE_COHOST.register_author_voice_channel(message.channel.id, author_voice_channel)

    # Direct Flight & Live Sports Tracker Commands (!trackflight, !untrackflight, !trackedflights, !track, !untrack, !tracked, !autoplay, !stopautoplay)
    tracker_res = await dispatch_tracker_command(
        text=user_input,
        channel_id=message.channel.id,
        flight_tracker_fn=skill_flight_tracker,
        flight_tracker_service=FLIGHT_TRACKER,
        sports_tracker_fn=skill_sports_tracker,
        sports_tracker_service=SPORTS_TRACKER,
    )
    if tracker_res is not None:
        active_channels.add(message.channel.id)
        if user_input.strip().lower().startswith("!stopautoplay") and VOICE_COHOST.is_music_playing(message.channel.id):
            VOICE_COHOST.stop_music(message.channel.id, clear_queue=False)
        elif VOICE_COHOST.extract_song_card_track(tracker_res) is not None:
            voice_badge = await VOICE_COHOST.stream_music_from_card_async(
                message.channel.id,
                tracker_res,
                replace_current=True,
                allow_auto_join=VOICE_COHOST.has_voice_music_intent(user_input),
                active_mode=current_mode,
            )
            if voice_badge and voice_badge not in tracker_res:
                tracker_res = f"{tracker_res.rstrip()}\n{voice_badge}"
        await _send_discord_message(message.channel, tracker_res)
        return

    # Direct Voice Co-Host & Music Playback Commands (!join, !leave, !speak, !vmode, !vrecap, !vstatus, !vmute, !vunmute, !pause, !resume, !skip, !stop, !queue)
    voice_res = await dispatch_voice_command(
        text=user_input,
        channel_id=message.channel.id,
        voice_info_fn=skill_voice_info,
        voice_cohost_service=VOICE_COHOST,
        author_voice_channel=author_voice_channel,
        active_mode=current_mode,
    )
    if voice_res is not None:
        active_channels.add(message.channel.id)
        await _send_discord_message(message.channel, voice_res)
        return

    is_mentioned = client.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    if not is_mentioned and not is_dm:
        return

    channel_id = message.channel.id
    active_channels.add(channel_id)

    mode_config = MODES[current_mode]
    is_research = mode_config.get("is_research", False)
    tools.set_memory_file(mode_config["memory_file"])
    set_active_channel_id(channel_id)

    # Transcribe Discord voice messages / audio attachments (.ogg, .wav, .mp3, .m4a) via gemma4a / faster-whisper
    attachments = getattr(message, "attachments", None) or []
    for att in attachments:
        att_name = str(getattr(att, "filename", "") or "").lower()
        att_ctype = str(getattr(att, "content_type", "") or "").lower()
        if att_ctype.startswith("audio/") or att_name.endswith((".ogg", ".wav", ".mp3", ".m4a", ".flac")):
            try:
                audio_bytes = await att.read()
                transcript = await run_in_worker(
                    VOICE_COHOST.transcribe_attachment_bytes,
                    audio_bytes,
                    mode_config["model"],
                )
                if transcript:
                    user_input = f"{user_input} [Spoken Voice Message: {transcript}]".strip()
            except Exception as e:
                print(f"⚠️ [Voice Attachment Transcription Error]: {e}")

    other_mentions = [f"{m.display_name} (ID: {m.id})" for m in message.mentions if m != client.user]
    mention_context = f" [Tagged in chat: {', '.join(other_mentions)}]" if other_mentions else ""

    ref_context = ""
    if message.reference and message.reference.message_id:
        try:
            ref_msg = message.reference.resolved
            if not isinstance(ref_msg, discord.Message):
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
            if ref_msg:
                ref_context = extract_reply_context(ref_msg.content)
        except Exception as e:
            print(f"⚠️ [Reference Resolution Error]: {e}")

    if current_mode == "chat":
        user_msg = f"{message.author.display_name} (User ID: {message.author.id}){mention_context}{ref_context}: {user_input}"
    else:
        user_msg = f"User {message.author.display_name} says{mention_context}{ref_context}: {user_input}"

    # Phase 1: Take a fast (<1ms) isolated snapshot of chat_history and release the channel lock
    # so slow worker tool fetches never block other prompts, 👍 reactions, or live tracker alerts.
    async with channel_locks[channel_id]:
        system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
        if channel_id not in chat_history:
            chat_history[channel_id] = [system_msg]
        else:
            chat_history[channel_id][0] = system_msg
        turn_history = list(chat_history[channel_id])

    turn_history.append({"role": "user", "content": user_msg})
    turn_history = trim_history(turn_history, mode_config.get("history_limit", 40))
    turn_start_idx = len(turn_history) - 1

    # Phase 2: Lock-free turn execution and reply post-processing
    async with message.channel.typing():
        try:
            clear_channel_attachments(channel_id)
            reply = await run_agent_turn(channel_id, mode_config, turn_history=turn_history)
            attachments = pop_channel_attachments(channel_id)

            if not reply.strip() and attachments:
                captions = [str(att.get("caption") or "").strip() for att in attachments if att.get("caption")]
                reply = f"📊 **{captions[0]}**" if captions else "📊 Here is your rendered chart."
            elif reply.strip() and attachments:
                for att in attachments:
                    cap = str(att.get("caption") or "").strip()
                    if att.get("filename", "").startswith("monte_carlo_") and "| Basis:" in cap and "Basis:" not in reply:
                        reply = f"📊 **{cap}**\n\n{reply.lstrip()}"
                        break

            is_empty_fallback = False
            if not reply.strip():
                is_empty_fallback = current_mode != "chat"
                reply = "Don't leave me hanging—what's on your mind?" if current_mode == "chat" else "⚠️ Generated an empty response."

            # Auto-ping fix for chat mode
            if current_mode == "chat":
                reply = apply_auto_ping(reply, known_users)

            if is_research:
                reply = join_orphan_bullets(reply)

            # Suppress Discord embed cards for article/web links while preserving YouTube video embeds
            reply = suppress_discord_embeds(reply)

            # Clean and ensure consistent song card template if a music track is returned
            # (may hit the network for metadata, so run it off the event loop)
            set_active_channel_id(channel_id)
            reply = await run_in_worker(standardize_song_reply, reply)
            reply = JOB_ENGINE.sync_channel_autoplay_on_track_played(channel_id, reply, new_query_hint=user_input)

            # Scrub internal tool name leaks, meta-refusals, and hallucinated betting links
            reply = sanitize_agent_reply(reply)

            # If the reply contains a Song Card and the bot is in a Discord voice channel (or the user asked
            # to join/stream in voice in this prompt), stream the song ad-free via yt-dlp + FFmpegPCMAudio!
            is_song_card_reply = VOICE_COHOST.extract_song_card_track(reply) is not None
            if is_song_card_reply:
                voice_stream_badge = await VOICE_COHOST.stream_music_from_card_async(
                    channel_id,
                    reply,
                    replace_current=True,
                    allow_auto_join=VOICE_COHOST.has_voice_music_intent(user_input),
                    active_mode=current_mode,
                )
                if voice_stream_badge and voice_stream_badge not in reply:
                    reply = f"{reply.rstrip()}\n{voice_stream_badge}"

            # Phase 3: Atomically commit this turn's completed message block (<1ms)
            if not is_empty_fallback:
                turn_delta = turn_history[turn_start_idx:]
                if len(turn_delta) == 1:
                    turn_delta.append({"role": "assistant", "content": reply})
                else:
                    turn_delta[-1] = {"role": "assistant", "content": reply}

                async with channel_locks[channel_id]:
                    if channel_id not in chat_history:
                        chat_history[channel_id] = [system_msg]
                    chat_history[channel_id].extend(turn_delta)
                    chat_history[channel_id] = trim_history(
                        chat_history[channel_id], mode_config.get("history_limit", 40)
                    )

            chunks = [c for c in chunk_message(reply) if c]
            discord_files = [
                discord.File(io.BytesIO(att["data"]), filename=att["filename"])
                for att in attachments
                if att.get("data")
            ]
            if chunks:
                for idx, chunk in enumerate(chunks):
                    await _send_discord_message(
                        message.channel,
                        chunk,
                        files=discord_files if idx == len(chunks) - 1 and discord_files else None,
                    )
            elif discord_files:
                await _send_discord_message(message.channel, "", files=discord_files)

            # If an active unmuted voice co-host session is connected for this channel, speak non-music replies aloud
            # (never run TTS over an active ad-free music stream or read a song card URL aloud!)
            active_voice_sess = VOICE_COHOST.get_session(channel_id)
            if (
                active_voice_sess
                and not active_voice_sess.is_muted
                and not is_song_card_reply
                and not VOICE_COHOST.is_music_playing(channel_id)
            ):
                await VOICE_COHOST.speak_async(channel_id, reply, profile_or_mode=current_mode)

        except Exception as e:
            print(f"❌ Error handling message: {type(e).__name__}: {e}")
            await message.channel.send("⚠️ Something went wrong while handling that. Please try again!")

if __name__ == "__main__":
    try:
        client.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            SPORTS_TRACKER.shutdown()
        except Exception:
            pass
        try:
            FLIGHT_TRACKER.shutdown()
        except Exception:
            pass
        try:
            JOB_ENGINE.shutdown()
        except Exception:
            pass
        try:
            VOICE_COHOST.shutdown()
        except Exception:
            pass
        shutdown_worker_pool()

