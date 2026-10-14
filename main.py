import discord
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
import re
import asyncio
import random
import requests
from collections import defaultdict
from dotenv import load_dotenv
from ollama import AsyncClient
import src.registry as tools
from src.utils import ensure_ollama_running, chunk_message, suppress_discord_embeds
from src.skills.music.utils import format_song_card, get_music_metadata, standardize_song_reply
from src.config import MODES
from src.orchestrator import (
    normalize_tool_args,
    trim_history,
    apply_auto_ping,
    join_orphan_bullets,
    sanitize_agent_reply,
    extract_reply_context,
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


def clear_tool_caches():
    tools.VISITED_URLS.clear()
    tools.URL_CACHE.clear()
    tools.SEARCH_CACHE.clear()
    tools.YOUTUBE_CACHE.clear()
    try:
        from src.skills.youtube.client import clear_youtube_caches
        from src.skills.music.aggregators.deezer import clear_deezer_cache
        from src.skills.music.aggregators.lastfm import clear_lastfm_cache
        from src.skills.music.aggregators.charts import clear_charts_cache
        from src.skills.sports.aggregators.liquipedia import clear_liquipedia_cache
        clear_youtube_caches()
        clear_deezer_cache()
        clear_lastfm_cache()
        clear_charts_cache()
        clear_liquipedia_cache()
    except Exception:
        pass


@client.event
async def on_ready():
    print(f"✅ Bot online as: {client.user}")
    print(f"🔥 Active Mode: {current_mode.upper()} (Switch with '!mode <name>': {', '.join(MODES)})")
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

                    # 2. Get recommendations (and pre-resolved verified YouTube link) from skill_top_music
                    similar_res = await asyncio.to_thread(tools.skill_top_music, similar_to=search_query)
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
                        bp_res = await asyncio.to_thread(tools.skill_top_music, genre=None)
                        similar_res = bp_res
                        for line in bp_res.split("\n"):
                            line = line.strip()
                            if line.startswith("- "):
                                similar_tracks.append(line[2:].strip())

                    # 3. Extract pre-resolved verified YouTube Music link if already resolved inside skill_top_music
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
                            yt_output = await asyncio.to_thread(tools.skill_youtube_searcher, candidate, max_results=1, is_music=True)
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
                        asyncio.to_thread(get_music_metadata, vid, fallback_text=chosen_track)
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


async def run_agent_turn(channel_id, mode_config):
    """Run the model/tool loop for one user turn and return the final reply text."""
    is_research = mode_config.get("is_research", False)
    supports_tools = mode_config.get("supports_tools", True)
    max_iterations = mode_config["max_iterations"]
    active_tools = mode_config.get("tools", {})
    tool_functions = list(active_tools.values())
    history = chat_history[channel_id]

    if is_research:
        tools.VISITED_URLS.clear()

    base_kwargs = {
        "model": mode_config["model"],
        "options": mode_config["ollama_options"],
    }

    async def final_answer_without_tools(nudge):
        """Ask the model for a final answer with tools hidden, so it must write text."""
        response = await ollama_client.chat(
            **base_kwargs,
            messages=history + [{"role": "user", "content": nudge}],
        )
        text = response.message.content or ""
        if text.strip():
            history.append({"role": "assistant", "content": text})
        return text

    iterations = 0
    reply = ""
    while iterations < max_iterations:
        # Dynamically construct chat arguments so tools are completely hidden
        # from models that can't handle them.
        chat_kwargs = dict(base_kwargs, messages=history)
        if supports_tools and tool_functions:
            chat_kwargs["tools"] = tool_functions

        response = await ollama_client.chat(**chat_kwargs)
        history.append(response.message)

        tool_calls = getattr(response.message, "tool_calls", None)
        if not tool_calls:
            reply = response.message.content or ""
            # Nudge if the model returned nothing after using tools
            if not reply.strip() and iterations > 0 and is_research:
                reply = await final_answer_without_tools(
                    "Now summarize and give your final answer based on the tool results above."
                )
            break

        print(f"⚡ [{current_mode.upper()}] Model requested {len(tool_calls)} tools simultaneously!")
        has_top_music = any(call.function.name == "skill_top_music" for call in tool_calls)

        async def run_tool(call):
            tool_name = call.function.name

            # Guardrail: Prevent running youtube searcher in parallel with top music
            if tool_name == "skill_youtube_searcher" and has_top_music:
                print(f"  └─ ⚠️ [Orchestrator]: Blocked parallel {tool_name} call while skill_top_music is executing.")
                return {
                    "role": "tool",
                    "name": tool_name,
                    "content": (
                        "PARALLEL_EXECUTION_BLOCKED: You cannot call `skill_youtube_searcher` at the same time as `skill_top_music`. "
                        "You must first review the songs returned by `skill_top_music` in this step, select ONE specific song, "
                        "and then call `skill_youtube_searcher(query='<Song Title> by <Artist Name>', is_music=True)` in your NEXT step to get its link."
                    )
                }

            if tool_name in active_tools:
                func = active_tools[tool_name]
                args = normalize_tool_args(tool_name, func, call.function.arguments or {})
                print(f"  └─ 🤖 Running tool: {tool_name} with args: {args}")
                try:
                    tool_output = await asyncio.to_thread(func, **args)
                except Exception as e:
                    print(f"  └─ ❌ Tool {tool_name} crashed: {e}")
                    tool_output = f"Tool execution failed: {e}"
            else:
                print(f"  └─ ⚠ Unknown tool requested: {tool_name}")
                tool_output = f"Tool '{tool_name}' not available."

            return {
                "role": "tool",
                "name": tool_name,
                "content": str(tool_output)
            }

        tool_results = await asyncio.gather(*(run_tool(call) for call in tool_calls))
        history.extend(tool_results)
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
            await message.channel.send(f"🔄 Switched to **{new_mode.upper()}** mode (Model: `{new_model}`). Memory and context wiped.")
        else:
            await message.channel.send(f"⚠️ Unknown mode. Available modes: {', '.join(MODES.keys())}")
        return

    if user_input.lower() == "!reset":
        chat_history.pop(message.channel.id, None)
        clear_tool_caches()
        await message.channel.send("🧹 Memory and context wiped for this channel.")
        return

    is_mentioned = client.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    if not is_mentioned and not is_dm:
        return

    channel_id = message.channel.id

    # Serialize turns per channel: two overlapping mentions would otherwise
    # interleave their tool calls/results in the same history list.
    async with channel_locks[channel_id]:
        # Set up mode config
        mode_config = MODES[current_mode]
        is_research = mode_config.get("is_research", False)
        tools.set_memory_file(mode_config["memory_file"])

        # Rebuild the system prompt every turn so the date and saved facts stay current.
        system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
        if channel_id not in chat_history:
            chat_history[channel_id] = [system_msg]
        else:
            chat_history[channel_id][0] = system_msg

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

        chat_history[channel_id].append({"role": "user", "content": user_msg})

        async with message.channel.typing():
            try:
                reply = await run_agent_turn(channel_id, mode_config)

                if not reply.strip():
                    reply = "Don't leave me hanging—what's on your mind?" if current_mode == "chat" else "⚠️ Generated an empty response."

                # Rolling context trimming (also shortens old tool dumps)
                chat_history[channel_id] = trim_history(chat_history[channel_id], mode_config.get("history_limit", 40))

                # Auto-ping fix for chat mode
                if current_mode == "chat":
                    reply = apply_auto_ping(reply, known_users)

                if is_research:
                    reply = join_orphan_bullets(reply)

                # Suppress Discord embed cards for article/web links while preserving YouTube video embeds
                reply = suppress_discord_embeds(reply)

                # Clean and ensure consistent song card template if a music track is returned
                # (may hit the network for metadata, so run it off the event loop)
                reply = await asyncio.to_thread(standardize_song_reply, reply)

                # Scrub internal tool name leaks, meta-refusals, and hallucinated betting links
                reply = sanitize_agent_reply(reply)

                chunks = chunk_message(reply)
                for chunk in chunks:
                    if chunk:
                        await message.channel.send(chunk)

            except Exception as e:
                print(f"❌ Error handling message: {type(e).__name__}: {e}")
                await message.channel.send("⚠️ Something went wrong while handling that. Please try again!")

if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
