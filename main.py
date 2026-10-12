import discord
import os
import sys
import io
import inspect

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
import re
import asyncio
import random
import requests
from dotenv import load_dotenv
from ollama import AsyncClient
import src.tools.registry as tools
from src.utils import ensure_ollama_running, chunk_message
from src.skills.music.utils import format_song_card, get_music_metadata, standardize_song_reply
from src.config import MODES

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
current_mode = os.getenv("DEFAULT_PROFILE", "research").lower()

@client.event
async def on_ready():
    print(f"✅ Bot online as: {client.user}")
    print(f"🔥 Active Mode: {current_mode.upper()} (Use '!mode chat' or '!mode research' to switch)")
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
            
        if "music.youtube.com/" in message.content or "youtube.com/watch" in message.content or "youtu.be/" in message.content:
            try:
                user = client.get_user(payload.user_id) or await client.fetch_user(payload.user_id)
            except Exception:
                return

            print(f"👍 [Reaction Caught]: {user.name} liked a song. Finding a verified similar track...")

            video_match = re.search(r'(?:music\.youtube\.com\/watch\?v=|youtube\.com\/watch\?v=|youtu\.be\/)([0-9A-Za-z_-]{11})', message.content)
            if not video_match:
                return
            video_id = video_match.group(1)

            async with channel.typing():
                try:
                    # 1. Fetch original track metadata via YouTube oEmbed
                    orig_title = "Unknown Song"
                    orig_artist = ""
                    try:
                        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                        r = await asyncio.to_thread(requests.get, oembed_url, timeout=5)
                        if r.status_code == 200:
                            data = r.json()
                            orig_title = data.get("title", "Unknown Song")
                            orig_artist = data.get("author_name", "").replace(" - Topic", "").strip()
                    except Exception as e:
                        print(f"⚠️ [Reaction oEmbed Error]: {e}")

                    # Fallback title parsing from message content
                    if orig_title == "Unknown Song":
                        for line in message.content.split("\n"):
                            m_title = re.search(r'\[(.*?)\]', line)
                            if m_title and "youtube" not in m_title.group(1).lower():
                                orig_title = m_title.group(1)
                                break

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

                    # 2. Get recommendations from Deezer / Beatport
                    similar_res = await asyncio.to_thread(tools.skill_top_music, similar_to=search_query)
                    similar_tracks = []
                    for line in similar_res.split("\n"):
                        line = line.strip()
                        if line.startswith("- "):
                            track_name = line[2:].strip()
                            if clean_title.lower() not in track_name.lower():
                                similar_tracks.append(track_name)

                    # Guaranteed fallback to Beatport if empty
                    if not similar_tracks:
                        print("⚠️ [Reaction Fallback]: No similar tracks found, pulling Beatport charts...")
                        bp_res = await asyncio.to_thread(tools.skill_top_music, genre="techno")
                        for line in bp_res.split("\n"):
                            line = line.strip()
                            if line.startswith("- "):
                                similar_tracks.append(line[2:].strip())

                    random.shuffle(similar_tracks)

                    # 3. Find verified YouTube Music link for one of the candidates
                    verified_url = None
                    chosen_track = None
                    for candidate in similar_tracks[:6]:
                        yt_output = await asyncio.to_thread(tools.skill_youtube_searcher, candidate, max_results=1, is_music=True)
                        m_url = re.search(r'(https:\/\/music\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})', yt_output)
                        if not m_url:
                            m_url = re.search(r'(https:\/\/www\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})', yt_output)
                        if m_url:
                            verified_url = m_url.group(1).replace("www.youtube.com", "music.youtube.com")
                            chosen_track = candidate
                            break

                    if not verified_url or not chosen_track:
                        await channel.send(f"⚠️ Found similar songs, but couldn't verify a working YouTube link. Please try again!")
                        return

                    # 4. Generate natural persona intro via active model
                    mode_config = MODES[current_mode]
                    orig_display = f"'{orig_title}' by '{orig_artist}'" if orig_artist else f"'{orig_title}'"
                    intro_prompt = (
                        f"User {user.display_name} liked the song {orig_display}. "
                        f"Write a single short sentence recommending the similar song '{chosen_track}'. "
                        "Do NOT include any links, URLs, or markdown links."
                    )

                    try:
                        resp = await ollama_client.chat(
                            model=mode_config["model"],
                            messages=[
                                {"role": "system", "content": mode_config["get_prompt"]("")},
                                {"role": "user", "content": intro_prompt}
                            ],
                            options=mode_config["ollama_options"]
                        )
                        intro_text = resp.message.content.strip()
                        intro_text = re.sub(r'https?:\/\/\S+', '', intro_text).strip()
                    except Exception as e:
                        print(f"⚠️ [Reaction Ollama Error]: {e}")
                        intro_text = f"Here is a song similar to **{orig_title}** by **{orig_artist}**:"

                    if not intro_text:
                        intro_text = f"Here is a song similar to **{orig_title}** by **{orig_artist}**:"

                    m_vid = re.search(r'watch\?v=([0-9A-Za-z_-]{11})', verified_url)
                    vid = m_vid.group(1) if m_vid else None
                    meta = get_music_metadata(vid, fallback_text=chosen_track)

                    final_reply = format_song_card(
                        song=meta["song"],
                        artist=meta["artist"],
                        url=verified_url,
                        genre=meta.get("genre"),
                        album=meta.get("album"),
                        intro=intro_text
                    )

                    if channel.id in chat_history:
                        chat_history[channel.id].append({
                            "role": "user",
                            "content": f"{user.display_name} reacted with 👍 to '{orig_display}'."
                        })
                        chat_history[channel.id].append({
                            "role": "assistant",
                            "content": f"Shared similar track: '{meta['song']}' by '{meta['artist']}' ({verified_url})."
                        })

                    await channel.send(final_reply)

                except Exception as e:
                    print(f"❌ [Reaction Error]: {e}")
                    await channel.send(f"⚠️ Error finding similar song: {e}")

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
            chat_history.pop(message.channel.id, None)
            tools.VISITED_URLS.clear()
            tools.URL_CACHE.clear()
            await message.channel.send(f"🔄 Switched to **{new_mode.upper()}** mode (Model: `{new_model}`). Memory and context wiped.")
        else:
            await message.channel.send(f"⚠️ Unknown mode. Available modes: {', '.join(MODES.keys())}")
        return

    if user_input.lower() == "!reset":
        chat_history.pop(message.channel.id, None)
        tools.VISITED_URLS.clear()
        tools.URL_CACHE.clear()
        await message.channel.send("🧹 Memory and context wiped for this channel.")
        return

    is_mentioned = client.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    if not is_mentioned and not is_dm:
        return

    # Set up mode config
    mode_config = MODES[current_mode]
    tools.ACTIVE_MEMORY_FILE = mode_config["memory_file"]
    supports_tools = mode_config.get("supports_tools", True)

    if message.channel.id not in chat_history:
        saved_facts = tools.recall_memories()
        chat_history[message.channel.id] = [
            {"role": "system", "content": mode_config["get_prompt"](saved_facts)}
        ]

    other_mentions = [f"{m.display_name} (ID: {m.id})" for m in message.mentions if m != client.user]
    mention_context = f" [Tagged in chat: {', '.join(other_mentions)}]" if other_mentions else ""
    
    if current_mode == "chat":
        user_msg = f"{message.author.display_name} (User ID: {message.author.id}){mention_context}: {user_input}"
    else:
        user_msg = f"User {message.author.display_name} says: {user_input}"

    chat_history[message.channel.id].append({"role": "user", "content": user_msg})

    async with message.channel.typing():
        try:
            iterations = 0
            max_iterations = mode_config["max_iterations"]
            reply = ""
            active_tools = mode_config.get("tools", {})
            tool_functions = list(active_tools.values())

            if current_mode == "research":
                tools.VISITED_URLS.clear()

            while iterations < max_iterations:
                
                # Dynamically construct chat arguments so tools are completely hidden
                # from models that can't handle them.
                chat_kwargs = {
                    "model": mode_config["model"],
                    "messages": chat_history[message.channel.id],
                    "options": mode_config["ollama_options"]
                }
                
                if supports_tools and tool_functions:
                    chat_kwargs["tools"] = tool_functions

                response = await ollama_client.chat(**chat_kwargs)

                chat_history[message.channel.id].append(response.message)

                # Process Tool Calls if any
                if getattr(response.message, "tool_calls", None):
                    print(f"⚡ [{current_mode.upper()}] Model requested {len(response.message.tool_calls)} tools simultaneously!")
                    
                    tool_names = [call.function.name for call in response.message.tool_calls]
                    has_top_music = "skill_top_music" in tool_names

                    async def run_tool(call):
                        tool_name = call.function.name
                        args = call.function.arguments or {}

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
                            print(f"  └─ 🤖 Running tool: {tool_name} with args: {args}")
                            try:
                                if not isinstance(args, dict):
                                    args = {}
                                func = active_tools[tool_name]
                                sig = inspect.signature(func)
                                filtered_args = {k: v for k, v in args.items() if k in sig.parameters}
                                tool_output = await asyncio.to_thread(func, **filtered_args)
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

                    tool_results = await asyncio.gather(*(run_tool(call) for call in response.message.tool_calls))
                    chat_history[message.channel.id].extend(tool_results)

                    iterations += 1
                    continue
                else:
                    reply = response.message.content or ""
                    
                    # Nudge if the model returned nothing after using tools
                    if not reply.strip() and iterations > 0 and current_mode == "research":
                        force_chat_kwargs = dict(chat_kwargs)
                        force_chat_kwargs["messages"] = chat_history[message.channel.id] + [{
                            "role": "user",
                            "content": "Now summarize and give your final answer based on the tool results above."
                        }]
                        force_response = await ollama_client.chat(**force_chat_kwargs)
                        reply = force_response.message.content or ""
                    break

            if iterations >= max_iterations and current_mode == "research":
                reply = "⚠️ I reached the maximum step limit and paused. Here is what I gathered so far."

            if not reply.strip():
                reply = "Don't leave me hanging—what's on your mind?" if current_mode == "chat" else "⚠️ Generated an empty response."

            # Auto-ping fix for chat mode
            if current_mode == "chat":
                for name, uid in sorted(known_users.items(), key=lambda x: len(x[0]), reverse=True):
                    pattern = re.compile(rf'@?(?:\\b{re.escape(name)}\\b)', re.IGNORECASE)
                    reply = pattern.sub(f"<@{uid}>", reply)

            # Rolling context trimming
            history_limit = 12 if current_mode == "research" else 40
            if len(chat_history[message.channel.id]) > history_limit:
                trim_size = 10 if current_mode == "research" else 35
                chat_history[message.channel.id] = [chat_history[message.channel.id][0]] + chat_history[message.channel.id][-trim_size:]

            if current_mode == "research":
                reply = reply.replace("* \\n", "* ").replace("- \\n", "- ")

            # Clean and ensure consistent song card template if a music track is returned
            reply = standardize_song_reply(reply)

            chunks = chunk_message(reply)
            for chunk in chunks:
                if chunk:
                    await message.channel.send(chunk)

        except Exception as e:
            print(f"❌ Error handling message: {e}")
            await message.channel.send(f"⚠️ Error: {e}")

if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
