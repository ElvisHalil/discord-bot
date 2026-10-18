import discord
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
import re
import asyncio
import random
import requests
from collections import defaultdict
from dotenv import load_dotenv
from ollama import AsyncClient
import src.registry as tools
from src.utils import (
    ensure_ollama_running,
    chunk_message,
    suppress_discord_embeds,
    run_in_worker,
    shutdown_worker_pool,
)
from src.context import (
    bind_event_loop,
    set_active_channel_id,
)
from src.skills.music.engine.formatting import format_song_card, get_music_metadata, standardize_song_reply
from src.skills.sports.tracker import (
    SPORTS_TRACKER,
    skill_sports_tracker,
)
from src.skills.flights import (
    FLIGHT_TRACKER,
    skill_flight_tracker,
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
    enrich_tool_output_with_time_context,
    detect_missed_sports_tool_call,
    detect_missed_music_shift_tool_call,
    detect_missed_flight_tool_call,
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
client = discord.Client(intents=intents)
ollama_client = AsyncClient()

# Global state
chat_history = {}
known_users = {}
active_channels = set()
# One lock per channel so overlapping mentions don't interleave tool calls in a shared history.
channel_locks = defaultdict(asyncio.Lock)
# (message_id, user_id) pairs already handled by the 👍 recommender, to avoid duplicate runs.
handled_reactions = set()
MAX_HANDLED_REACTIONS = 2000

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
    if channel_id is None:
        tools.VISITED_URLS.clear()
        tools.URL_CACHE.clear()
        tools.SEARCH_CACHE.clear()
        tools.YOUTUBE_CACHE.clear()
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
        from src.skills.sports.aggregators.liquipedia import clear_liquipedia_cache
        from src.skills.sports.aggregators.hltv import clear_hltv_cache
        from src.skills.sports.aggregators.kashrock import clear_kashrock_cache
        from src.skills.sports.aggregators.espn_utils import clear_espn_cache
        from src.skills.weather.aggregators import clear_weather_cache
        clear_channel_played_songs(channel_id)
        clear_recently_played_tracks(channel_id)
        clear_channel_last_match_query(channel_id)
        clear_channel_flight_state(channel_id)
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
    elif current_mode == "chappie":
        sys_prompt = (
            "Act as Chappie, the childlike AI raised by South African gangsters. "
            "Speak in the third person in 1-2 short sentences announcing this live sports match update "
            "with the exact score and player/event details. No links."
        )
    else:
        sys_prompt = (
            "You are a live sports desk commentator in Discord. "
            "Write a concise, energetic 1-3 sentence live match alert announcing the event below. "
            "Include the exact score, match clock/status, competition name, and any goal scorer/assist/card or stats details."
        )

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

    mode_config = MODES[current_mode]
    async with channel_locks[channel_id]:
        if channel_id not in chat_history:
            system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
            chat_history[channel_id] = [system_msg]
        chat_history[channel_id].append({
            "role": "assistant",
            "content": alert_text,
        })


@client.event
async def on_ready():
    print(f"✅ Bot online as: {client.user}")
    print(f"🔥 Active Mode: {current_mode.upper()} (Switch with '!mode <name>': {', '.join(MODES)})")
    loop = asyncio.get_running_loop()
    bind_event_loop(loop)
    SPORTS_TRACKER.set_notify_callback(handle_live_match_update)
    SPORTS_TRACKER.ensure_loop_running()
    FLIGHT_TRACKER.bind_event_loop(loop)
    FLIGHT_TRACKER.set_notify_callback(handle_flight_tracker_update)
    FLIGHT_TRACKER.ensure_loop_running()
    asyncio.create_task(start_telegram_bridge(sys.modules[__name__]))


@client.event
async def on_raw_reaction_add(payload):
    if payload.user_id == client.user.id:
        return
        
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
            
        m_card_song = re.search(r'(?im)^\s*(?:\*\*)?(?:Song|Track):\*{0,2}\s*(.+)', message.content)
        m_card_art = re.search(r'(?im)^\s*(?:\*\*)?Artist:\*{0,2}\s*(.+)', message.content)

        if m_card_song and m_card_art and "music.youtube.com/" in message.content:
            # Only recommend once per (message, user), even if they remove and re-add the reaction.
            reaction_key = (payload.message_id, payload.user_id)
            if reaction_key in handled_reactions:
                return
            if len(handled_reactions) >= MAX_HANDLED_REACTIONS:
                handled_reactions.clear()
            handled_reactions.add(reaction_key)

            try:
                user = client.get_user(payload.user_id) or await client.fetch_user(payload.user_id)
            except Exception:
                return

            print(f"👍 [Reaction Caught]: {user.name} liked a song. Finding a verified similar track...")

            video_match = re.search(r'music\.youtube\.com\/watch\?v=([0-9A-Za-z_-]{11})', message.content)
            if not video_match:
                return

            async with channel.typing():
                try:
                    # 1. Extract track metadata directly from the verified song card
                    orig_title = m_card_song.group(1).strip()
                    orig_artist = m_card_art.group(1).strip()

                    # Smart clean YouTube title and artist
                    clean_title = re.sub(r'[\(\[\{].*?(?:official|video|audio|lyrics|visualizer|download|remastered|hd|4k|hq|mix).*?[\)\]\}]', '', orig_title, flags=re.IGNORECASE)
                    clean_title = re.sub(r'[\(\[\{](?:feat\.|ft\.).*?[\)\]\}]', '', clean_title, flags=re.IGNORECASE)
                    if " - " in clean_title:
                        parts = clean_title.split(" - ", 1)
                        orig_artist = parts[0].strip()
                        clean_title = parts[1].strip()
                    clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', clean_title).strip()
                    clean_title = re.sub(r'[-–—|/].*$', '', clean_title).strip()
                    if not clean_title:
                        clean_title = orig_title

                    search_query = f"{clean_title} by {orig_artist}".strip() if orig_artist else clean_title
                    print(f"🎵 [Reaction Target]: Cleaned '{clean_title}' by '{orig_artist}'")

                    # 2. Get recommendations (and pre-resolved verified YouTube link) from skill_music_discovery
                    set_active_channel_id(channel.id)
                    similar_res = await run_in_worker(tools.skill_music_discovery, similar_to=search_query)
                    is_unrelated_fallback = "NO DIRECT SIMILAR SONGS FOUND" in similar_res
                    similar_tracks = []
                    for line in similar_res.split("\n"):
                        line = line.strip()
                        if line.startswith("- "):
                            track_name = line[2:].strip()
                            if clean_title.lower() not in track_name.lower():
                                similar_tracks.append(track_name)

                    # Guaranteed fallback to general charts if empty
                    if not similar_tracks:
                        is_unrelated_fallback = True
                        print("⚠️ [Reaction Fallback]: No similar tracks found, pulling general charts...")
                        bp_res = await run_in_worker(tools.skill_music_discovery, genre=None)
                        similar_res = bp_res
                        for line in bp_res.split("\n"):
                            line = line.strip()
                            if line.startswith("- "):
                                similar_tracks.append(line[2:].strip())

                    # 3. Extract pre-resolved verified YouTube Music link if already resolved inside skill_music_discovery
                    verified_url = None
                    chosen_track = None
                    if "=== VERIFIED YOUTUBE MUSIC RESULT" in similar_res:
                        pre_block = similar_res.split("=== VERIFIED YOUTUBE MUSIC RESULT", 1)[1]
                        m_pre_song = re.search(r'(?m)^Song:\s*(.+)$', pre_block)
                        m_pre_art = re.search(r'(?m)^Artist:\s*(.+)$', pre_block)
                        m_pre_url = re.search(r'(?m)^Link:\s*(https:\/\/(?:music|www)\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})', pre_block)
                        if m_pre_url and m_pre_song and m_pre_art:
                            cand_song = m_pre_song.group(1).strip()
                            cand_art = m_pre_art.group(1).strip()
                            if clean_title.lower() not in cand_song.lower():
                                verified_url = m_pre_url.group(1).replace("www.youtube.com", "music.youtube.com")
                                chosen_track = f"{cand_song} by {cand_art}"

                    # Fallback candidate resolution loop if not pre-resolved
                    if not verified_url or not chosen_track:
                        random.shuffle(similar_tracks)
                        for candidate in similar_tracks[:6]:
                            yt_output = await run_in_worker(tools.skill_youtube_search, candidate, max_results=1, is_music=True)
                            m_url = re.search(r'(https:\/\/music\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})', yt_output)
                            if not m_url:
                                m_url = re.search(r'(https:\/\/www\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})', yt_output)
                            if m_url:
                                verified_url = m_url.group(1).replace("www.youtube.com", "music.youtube.com")
                                chosen_track = candidate
                                break

                    user_tag = user.mention if user else f"<@{payload.user_id}>"

                    if not verified_url or not chosen_track:
                        await channel.send(f"{user_tag} ⚠️ Found similar songs, but couldn't verify a working YouTube link. Please try again!")
                        return

                    # 4. Generate natural persona intro in parallel with metadata lookup
                    mode_config = MODES[current_mode]
                    orig_display = f"'{orig_title}' by '{orig_artist}'" if orig_artist else f"'{orig_title}'"
                    if is_unrelated_fallback:
                        intro_prompt = (
                            f"User {user.display_name} reacted with 👍 to {orig_display}, "
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
                            f"User {user.display_name} reacted with 👍 to the song {orig_display}. "
                            f"Write a single short sentence recommending the similar song '{chosen_track}'. "
                            "Do NOT include any links, URLs, or markdown links."
                        )
                        default_intro = f"Here is a song similar to **{orig_title}** by **{orig_artist}**:"

                    if current_mode == "chat":
                        light_sys_prompt = "you are a casual, opinionated human in discord. write 1 short, lowercase sentence with modern slang. no links, no emojis."
                    elif current_mode == "chappie":
                        light_sys_prompt = "Act as Chappie, the childlike AI raised by South African gangsters. Speak in the third person in 1 short sentence. No links."
                    else:
                        light_sys_prompt = "You are a helpful music assistant in Discord. Write a single natural, concise 1-sentence recommendation intro. No links, no markdown URLs, no emojis."

                    reaction_options = {
                        **mode_config["ollama_options"],
                        "num_ctx": 1024,
                        "num_predict": 96,
                    }

                    m_vid = re.search(r'watch\?v=([0-9A-Za-z_-]{11})', verified_url)
                    vid = m_vid.group(1) if m_vid else None

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

                    await channel.send(final_reply)

                    # Record it under the channel lock so it can't land between a
                    # tool call and its results while a message turn is in progress.
                    async with channel_locks[channel.id]:
                        if channel.id not in chat_history:
                            system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
                            chat_history[channel.id] = [system_msg]

                        chat_history[channel.id].append({
                            "role": "user",
                            "content": f"{user.display_name} reacted with 👍 to '{orig_display}' requesting a similar track."
                        })
                        chat_history[channel.id].append({
                            "role": "assistant",
                            "content": final_reply
                        })

                except Exception as e:
                    print(f"❌ [Reaction Error]: {e}")
                    user_tag = user.mention if 'user' in locals() and user else f"<@{payload.user_id}>"
                    await channel.send(f"{user_tag} ⚠️ Something went wrong while finding a similar song. Please try again!")


async def run_agent_turn(channel_id, mode_config, turn_history=None):
    """Run the model/tool loop for one user turn and return the final reply text."""
    is_research = mode_config.get("is_research", False)
    supports_tools = mode_config.get("supports_tools", True)
    max_iterations = mode_config["max_iterations"]
    active_tools = mode_config.get("tools", {})
    tool_functions = list(active_tools.values())
    history = turn_history if turn_history is not None else chat_history[channel_id]
    set_active_channel_id(channel_id)

    if is_research:
        tools.reset_turn_visited_urls()
        if turn_history is None:
            tools.VISITED_URLS.clear()

    base_kwargs = {
        "model": mode_config["model"],
        "options": mode_config["ollama_options"],
    }

    async def final_answer_without_tools(nudge):
        """Ask the model for a final answer with tools hidden and thinking disabled so all tokens go to visible text."""
        while history:
            last_msg = history[-1]
            last_role = last_msg.get("role") if isinstance(last_msg, dict) else getattr(last_msg, "role", None)
            last_content = (last_msg.get("content") if isinstance(last_msg, dict) else getattr(last_msg, "content", "")) or ""
            last_tools = last_msg.get("tool_calls") if isinstance(last_msg, dict) else getattr(last_msg, "tool_calls", None)
            if last_role == "assistant" and not str(last_content).strip() and not last_tools:
                history.pop()
            else:
                break

        compact_intra_turn_tool_history(history)
        fallback_messages = history + [{"role": "user", "content": nudge}]
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
        "skill_weather_info",
        "skill_news_headlines",
        "skill_music_analysis",
        "evaluate_math",
        "save_memory",
    }
    hide_tools_next_step = False
    iterations = 0
    reply = ""
    while iterations < max_iterations:
        if iterations >= 2:
            compact_intra_turn_tool_history(history)

        # Dynamically construct chat arguments so tools are completely hidden
        # from models that can't handle them (or after self-contained terminal skills have returned).
        chat_kwargs = dict(base_kwargs, messages=history)
        if supports_tools and tool_functions and not hide_tools_next_step:
            chat_kwargs["tools"] = tool_functions

        response = await ollama_client.chat(**chat_kwargs)
        history.append(response.message)

        tool_calls = getattr(response.message, "tool_calls", None)
        if not tool_calls:
            if iterations == 0 and supports_tools:
                missed = (
                    detect_missed_sports_tool_call(history, active_channel_id=channel_id)
                    or detect_missed_music_shift_tool_call(history)
                    or detect_missed_flight_tool_call(history, active_channel_id=channel_id)
                )
                if missed and missed[0] in active_tools:
                    forced_tool_name, forced_raw_args = missed
                    func = active_tools[forced_tool_name]
                    forced_args = normalize_tool_args(forced_tool_name, func, forced_raw_args)
                    history.pop()  # Discard stale non-tool reply from iteration 0
                    if forced_tool_name in ("skill_flight_info", "skill_flight_tracker"):
                        forced_args = enrich_flight_tool_args(forced_args, history)
                    set_active_channel_id(channel_id)
                    print(f"⚡ [{current_mode.upper()}] Intercepted missed tool on turn 0 -> forcing {forced_tool_name}({forced_args})")
                    try:
                        tool_output = await run_in_worker(func, **forced_args)
                    except Exception as e:
                        print(f"  └─ ❌ Forced tool {forced_tool_name} crashed: {e}")
                        tool_output = f"Tool execution failed: {e}"
                    if SPORTS_TRACKER.tracked_matches:
                        SPORTS_TRACKER.ensure_loop_running()
                    if FLIGHT_TRACKER.tracked_flights or FLIGHT_TRACKER.price_watches:
                        FLIGHT_TRACKER.ensure_loop_running()
                    history.append({
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"function": {"name": forced_tool_name, "arguments": forced_args}}],
                    })
                    history.append({
                        "role": "tool",
                        "name": forced_tool_name,
                        "content": enrich_tool_output_with_time_context(str(tool_output)),
                    })
                    iterations += 1
                    continue

            reply = response.message.content or ""
            # Nudge with think=False if the model returned empty content (e.g. exhausted num_predict inside <think>)
            if not reply.strip() and (iterations > 0 or is_research or getattr(response.message, "thinking", None)):
                thinking_len = len(getattr(response.message, "thinking", "") or "")
                print(
                    f"⚠️ [{current_mode.upper()}] Empty content on step {iterations} "
                    f"(thinking_chars={thinking_len}); forcing direct final answer with think=False."
                )
                nudge_msg = (
                    "Now summarize and give your final answer based on the tool results above."
                    if iterations > 0
                    else "Give your direct, concise final answer to the user's message now."
                )
                reply = await final_answer_without_tools(nudge_msg)
            break

        print(f"⚡ [{current_mode.upper()}] Model requested {len(tool_calls)} tools simultaneously!")
        has_top_music = any(call.function.name == "skill_music_discovery" for call in tool_calls)
        per_tool_char_budget = 10000 if len(tool_calls) == 1 else max(4000, 18000 // len(tool_calls))

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
                set_active_channel_id(channel_id)
                print(f"  └─ 🤖 Running tool: {tool_name} with args: {args}")
                try:
                    tool_output = await run_in_worker(func, **args)
                except Exception as e:
                    print(f"  └─ ❌ Tool {tool_name} crashed: {e}")
                    tool_output = f"Tool execution failed: {e}"
            else:
                print(f"  └─ ⚠ Unknown tool requested: {tool_name}")
                tool_output = f"Tool '{tool_name}' not available."

            enriched_content = enrich_tool_output_with_time_context(str(tool_output))
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
        history.extend(tool_results)
        called_names = {c.function.name for c in tool_calls}
        if (
            called_names.issubset(_TERMINAL_SKILLS)
            and len(tool_calls) >= 2
            and all("PARALLEL_EXECUTION_BLOCKED" not in r["content"] and not r["content"].startswith("⚠️") for r in tool_results)
        ):
            hide_tools_next_step = True
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

    return reply


@client.event
async def on_message(message):
    global current_mode
    if message.author == client.user:
        return

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

    # Direct Flight & Price Tracker Commands (!trackflight <flight/route>, !untrackflight [flight/route], !trackedflights)
    if user_input.lower().startswith("!trackflight "):
        active_channels.add(message.channel.id)
        target_q = user_input[13:].strip()
        set_active_channel_id(message.channel.id)
        res = await run_in_worker(skill_flight_tracker, action="track", query=target_q)
        FLIGHT_TRACKER.ensure_loop_running()
        await message.channel.send(res)
        return

    if user_input.lower().startswith("!untrackflight"):
        active_channels.add(message.channel.id)
        target_q = user_input[14:].strip()
        set_active_channel_id(message.channel.id)
        res = await run_in_worker(skill_flight_tracker, action="untrack", query=target_q)
        await message.channel.send(res)
        return

    if user_input.lower() in ("!trackedflights", "!trackflight"):
        active_channels.add(message.channel.id)
        set_active_channel_id(message.channel.id)
        res = await run_in_worker(skill_flight_tracker, action="list")
        await message.channel.send(res)
        return

    # Direct Live Sports Tracker Commands (!track <team>, !untrack [team], !tracked)
    if user_input.lower().startswith("!track "):
        active_channels.add(message.channel.id)
        target_q = user_input[7:].strip()
        set_active_channel_id(message.channel.id)
        res = await run_in_worker(skill_sports_tracker, action="start", query=target_q)
        SPORTS_TRACKER.ensure_loop_running()
        await message.channel.send(res)
        return

    if user_input.lower().startswith("!untrack"):
        active_channels.add(message.channel.id)
        target_q = user_input[8:].strip()
        set_active_channel_id(message.channel.id)
        res = await run_in_worker(skill_sports_tracker, action="stop", query=target_q)
        await message.channel.send(res)
        return

    if user_input.lower() in ("!tracked", "!track"):
        active_channels.add(message.channel.id)
        set_active_channel_id(message.channel.id)
        res = await run_in_worker(skill_sports_tracker, action="list")
        await message.channel.send(res)
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

    turn_start_idx = len(turn_history)
    turn_history.append({"role": "user", "content": user_msg})

    # Phase 2: Lock-free turn execution and reply post-processing
    async with message.channel.typing():
        try:
            reply = await run_agent_turn(channel_id, mode_config, turn_history=turn_history)

            if not reply.strip():
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

            # Scrub internal tool name leaks, meta-refusals, and hallucinated betting links
            reply = sanitize_agent_reply(reply)

            # Phase 3: Atomically commit this turn's completed message block (<1ms)
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

            chunks = chunk_message(reply)
            for chunk in chunks:
                if chunk:
                    await message.channel.send(chunk)

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
        shutdown_worker_pool()
