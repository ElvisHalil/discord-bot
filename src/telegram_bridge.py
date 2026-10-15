"""
Optional, self-contained Telegram Bot Bridge.
Only loaded when ENABLE_TELEGRAM=true in .env.
Uses Telegram's HTTPS JSON Bot API directly via requests + asyncio.to_thread
with zero extra third-party dependencies and fail-closed allowlist security.
"""

import asyncio
import os
import random
import re
from typing import Any, Dict, Optional, Set, Tuple
import requests

import src.registry as tools
from src.config import MODES
from src.orchestrator import (
    apply_auto_ping,
    extract_reply_context,
    join_orphan_bullets,
    sanitize_agent_reply,
    trim_history,
)
from src.skills.music.utils import format_song_card, get_music_metadata, standardize_song_reply
from src.skills.sports.live_tracker import (
    LIVE_TRACKER,
     set_active_channel_id,
    skill_track_live_game,
)
from src.utils import chunk_message

TELEGRAM_API_BASE = "https://api.telegram.org"

# Maps internal negative channel_id -> real Telegram chat_id (so positive Telegram DM chat_ids
# never collide with positive Discord channel snowflakes in chat_history / LIVE_TRACKER).
_TG_CHANNEL_OFFSET = -10_000_000_000_000_000
_TG_CHANNEL_MAP: Dict[int, int] = {}

# Bounded cache of song cards sent on Telegram: (chat_id, message_id) -> text
# Allows 👍 reactions on Telegram to look up the song card text without a fetch_message API.
_SENT_SONG_CARDS: Dict[Tuple[int, int], str] = {}
_HANDLED_TG_REACTIONS: Set[Tuple[int, int, int]] = set()
_MAX_SONG_CARD_CACHE = 500


def is_telegram_enabled() -> bool:
    """Return True if ENABLE_TELEGRAM is enabled in environment variables."""
    return os.getenv("ENABLE_TELEGRAM", "false").strip().lower() in ("true", "1", "yes", "on")


def get_telegram_owner_id() -> Optional[int]:
    """Return the numeric TELEGRAM_OWNER_ID from .env if configured, else None."""
    raw = os.getenv("TELEGRAM_OWNER_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_allowed_ids() -> Set[int]:
    """
    Parse TELEGRAM_OWNER_ID and TELEGRAM_ALLOWED_IDS from .env into a set of integers.
    Supports both positive user IDs and negative (-100...) group chat IDs.
    """
    allowed: Set[int] = set()
    owner_id = get_telegram_owner_id()
    if owner_id is not None:
        allowed.add(owner_id)

    raw_list = os.getenv("TELEGRAM_ALLOWED_IDS", "")
    for token in re.split(r"[,;\s]+", raw_list.strip()):
        if not token:
            continue
        try:
            allowed.add(int(token))
        except ValueError:
            pass
    return allowed


def is_authorized_telegram_event(user_id: Optional[int], chat_id: Optional[int]) -> bool:
    """
    Fail-closed authorization check:
    Returns True ONLY if user_id or chat_id is explicitly listed in TELEGRAM_OWNER_ID or TELEGRAM_ALLOWED_IDS.
    If neither env var is set, returns False for everyone.
    """
    allowed = parse_allowed_ids()
    if not allowed:
        return False
    if user_id is not None and int(user_id) in allowed:
        return True
    if chat_id is not None and int(chat_id) in allowed:
        return True
    return False


def to_tg_channel_id(chat_id: int) -> int:
    """Convert a Telegram chat_id into a collision-free negative internal channel_id."""
    cid = int(chat_id)
    internal_id = (cid + _TG_CHANNEL_OFFSET) if cid < 0 else (-cid + _TG_CHANNEL_OFFSET)
    _TG_CHANNEL_MAP[internal_id] = cid
    return internal_id


def from_tg_channel_id(channel_id: int) -> int:
    """Convert an internal negative channel_id back to the real Telegram chat_id."""
    cid = int(channel_id)
    if cid in _TG_CHANNEL_MAP:
        return _TG_CHANNEL_MAP[cid]
    return cid


def is_telegram_channel(channel_id: int) -> bool:
    """Return True if channel_id belongs to a Telegram chat."""
    try:
        cid = int(channel_id)
        return cid in _TG_CHANNEL_MAP or cid <= _TG_CHANNEL_OFFSET
    except (ValueError, TypeError):
        return False


def clean_for_telegram(text: str) -> str:
    """
    Unwrap Discord's `<https://...>` angle-bracket link masks so Telegram doesn't
    display literal `<` and `>` characters around URLs.
    """
    if not text:
        return ""
    # 1. [Label](<https://...>) -> [Label](https://...)
    cleaned = re.sub(r"\]\(<(https?://[^>\s]+)>\)", r"](\1)", text)
    # 2. Standalone <https://...> -> https://...
    cleaned = re.sub(r"<(https?://[^>\s]+)>", r"\1", cleaned)
    return cleaned.strip()


def _has_youtube_link(text: str) -> bool:
    return bool(re.search(r"https?://(?:www\.|music\.)?(?:youtube\.com/watch\?v=|youtu\.be/)", text or "", re.IGNORECASE))


def _normalize_command(text: str, bot_username: str = "") -> str:
    """Normalize Telegram `/cmd@BotName arg` into `!cmd arg` so command handling is unified."""
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return stripped
    parts = stripped.split(None, 1)
    cmd_part = parts[0][1:]  # strip leading '/'
    if "@" in cmd_part:
        cmd_base, target_bot = cmd_part.split("@", 1)
        if bot_username and target_bot.lower() != bot_username.lower():
            return stripped
        cmd_part = cmd_base
    cmd_low = cmd_part.lower()
    if cmd_low in ("reset", "mode", "track", "untrack", "tracked"):
        rest = f" {parts[1]}" if len(parts) > 1 else ""
        return f"!{cmd_low}{rest}"
    return stripped


def _tg_api_post(token: str, method: str, payload: Dict[str, Any], timeout: int = 35) -> Optional[Dict[str, Any]]:
    """Synchronous helper to call Telegram Bot HTTP API."""
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        return resp.json()
    except Exception as e:
        print(f"⚠️ [Telegram API Error ({method})]: {e}")
        return None


async def send_telegram_message(channel_or_chat_id: int, text: str, token: Optional[str] = None) -> Optional[int]:
    """Send a message to a Telegram chat, chunking safely and preserving YouTube previews."""
    bot_token = token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token or not text:
        return None

    chat_id = from_tg_channel_id(channel_or_chat_id)
    cleaned = clean_for_telegram(text)
    chunks = chunk_message(cleaned, max_length=3800)
    last_msg_id: Optional[int] = None

    for chunk in chunks:
        if not chunk:
            continue
        show_preview = _has_youtube_link(chunk)
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "link_preview_options": {"is_disabled": not show_preview},
        }
        res = await asyncio.to_thread(_tg_api_post, bot_token, "sendMessage", payload, 15)
        if res and res.get("ok") and isinstance(res.get("result"), dict):
            last_msg_id = res["result"].get("message_id")
            if "music.youtube.com/watch?v=" in chunk and last_msg_id is not None:
                if len(_SENT_SONG_CARDS) >= _MAX_SONG_CARD_CACHE:
                    _SENT_SONG_CARDS.clear()
                _SENT_SONG_CARDS[(chat_id, int(last_msg_id))] = chunk

    return last_msg_id


async def _handle_telegram_reaction(
    reaction_obj: Dict[str, Any],
    main_mod: Any,
    bot_token: str,
) -> None:
    """Handle 👍 reactions on bot song cards in Telegram to recommend a similar verified song."""
    chat = reaction_obj.get("chat") or {}
    user = reaction_obj.get("user") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    message_id = reaction_obj.get("message_id")

    if chat_id is None or user_id is None or message_id is None:
        return
    if not is_authorized_telegram_event(user_id, chat_id):
        return

    new_reactions = reaction_obj.get("new_reaction") or []
    has_thumbs_up = any(r.get("emoji") == "👍" for r in new_reactions if isinstance(r, dict))
    if not has_thumbs_up:
        return

    card_text = _SENT_SONG_CARDS.get((int(chat_id), int(message_id)))
    if not card_text:
        return

    reaction_key = (int(chat_id), int(message_id), int(user_id))
    if reaction_key in _HANDLED_TG_REACTIONS:
        return
    if len(_HANDLED_TG_REACTIONS) >= 2000:
        _HANDLED_TG_REACTIONS.clear()
    _HANDLED_TG_REACTIONS.add(reaction_key)

    m_card_song = re.search(r"(?im)^\s*(?:\*\*)?(?:Song|Track):\*{0,2}\s*(.+)", card_text)
    m_card_art = re.search(r"(?im)^\s*(?:\*\*)?Artist:\*{0,2}\s*(.+)", card_text)
    if not (m_card_song and m_card_art and "music.youtube.com/" in card_text):
        return

    orig_title = m_card_song.group(1).strip()
    orig_artist = m_card_art.group(1).strip()
    user_display = user.get("first_name") or user.get("username") or "User"
    user_tag = f"@{user['username']}" if user.get("username") else user_display
    internal_cid = to_tg_channel_id(int(chat_id))

    await asyncio.to_thread(_tg_api_post, bot_token, "sendChatAction", {"chat_id": chat_id, "action": "typing"}, 10)

    clean_title = re.sub(r"[\(\[\{].*?(?:official|video|audio|lyrics|visualizer|download|remastered|hd|4k|hq|mix).*?[\)\]\}]", "", orig_title, flags=re.IGNORECASE)
    clean_title = re.sub(r"[\(\[\{](?:feat\.|ft\.).*?[\)\]\}]", "", clean_title, flags=re.IGNORECASE)
    if " - " in clean_title:
        parts = clean_title.split(" - ", 1)
        orig_artist = parts[0].strip()
        clean_title = parts[1].strip()
    clean_title = re.sub(r"[\(\[\{].*?[\)\]\}]", "", clean_title).strip()
    clean_title = re.sub(r"[-–—|/].*$", "", clean_title).strip() or orig_title
    search_query = f"{clean_title} by {orig_artist}".strip() if orig_artist else clean_title

    set_active_channel_id(internal_cid)
    similar_res = await asyncio.to_thread(tools.skill_top_music, similar_to=search_query)
    verified_url = None
    chosen_track = None
    if "=== VERIFIED YOUTUBE MUSIC RESULT" in similar_res:
        pre_block = similar_res.split("=== VERIFIED YOUTUBE MUSIC RESULT", 1)[1]
        m_pre_song = re.search(r"(?m)^Song:\s*(.+)$", pre_block)
        m_pre_art = re.search(r"(?m)^Artist:\s*(.+)$", pre_block)
        m_pre_url = re.search(r"(?m)^Link:\s*(https:\/\/(?:music|www)\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})", pre_block)
        if m_pre_url and m_pre_song and m_pre_art:
            verified_url = m_pre_url.group(1).replace("www.youtube.com", "music.youtube.com")
            chosen_track = f"{m_pre_song.group(1).strip()} by {m_pre_art.group(1).strip()}"

    if not verified_url or not chosen_track:
        similar_tracks = [
            line[2:].strip()
            for line in similar_res.split("\n")
            if line.strip().startswith("- ") and clean_title.lower() not in line.lower()
        ]
        random.shuffle(similar_tracks)
        for candidate in similar_tracks[:4]:
            yt_output = await asyncio.to_thread(tools.skill_youtube_searcher, candidate, max_results=1, is_music=True)
            m_url = re.search(r"(https:\/\/(?:music|www)\.youtube\.com\/watch\?v=[0-9A-Za-z_-]{11})", yt_output)
            if m_url:
                verified_url = m_url.group(1).replace("www.youtube.com", "music.youtube.com")
                chosen_track = candidate
                break

    if not verified_url or not chosen_track:
        return

    m_vid = re.search(r"watch\?v=([0-9A-Za-z_-]{11})", verified_url)
    vid = m_vid.group(1) if m_vid else None
    meta = await asyncio.to_thread(get_music_metadata, vid, fallback_text=chosen_track)
    intro_text = f"{user_tag} Here is a song similar to **{orig_title}** by **{orig_artist}**:"
    final_reply = format_song_card(
        song=meta["song"],
        artist=meta["artist"],
        url=verified_url,
        genre=meta.get("genre"),
        album=meta.get("album"),
        intro=intro_text,
    )
    await send_telegram_message(internal_cid, final_reply, token=bot_token)


async def handle_telegram_update(
    update: Dict[str, Any],
    main_mod: Any,
    bot_token: str,
    bot_username: str = "",
    bot_id: Optional[int] = None,
) -> None:
    """Process a single Telegram update dictionary (message or message_reaction)."""
    if "message_reaction" in update:
        await _handle_telegram_reaction(update["message_reaction"], main_mod, bot_token)
        return

    msg = update.get("message")
    if not isinstance(msg, dict):
        return

    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    if sender.get("is_bot"):
        return

    chat_id = chat.get("id")
    user_id = sender.get("id")
    chat_type = chat.get("type", "private")
    raw_text = (msg.get("text") or msg.get("caption") or "").strip()
    if chat_id is None or user_id is None or not raw_text:
        return

    # 1. Fail-Closed Security Guard
    if not is_authorized_telegram_event(user_id, chat_id):
        uname = sender.get("username") or sender.get("first_name") or "Unknown"
        print(
            f"🔒 [Telegram Blocked]: Ignored message from {uname} "
            f"(user_id={user_id}, chat_id={chat_id}). "
            f"Add ID to TELEGRAM_OWNER_ID or TELEGRAM_ALLOWED_IDS in .env to authorize."
        )
        return

    internal_cid = to_tg_channel_id(int(chat_id))
    user_display = sender.get("first_name") or sender.get("username") or f"User_{user_id}"
    normalized_input = _normalize_command(raw_text, bot_username=bot_username)

    # Strip @bot_username mention from input text
    is_mentioned = False
    if bot_username and re.search(rf"@{re.escape(bot_username)}\b", normalized_input, re.IGNORECASE):
        is_mentioned = True
        normalized_input = re.sub(rf"@{re.escape(bot_username)}\b", "", normalized_input, flags=re.IGNORECASE).strip()

    reply_to = msg.get("reply_to_message") or {}
    reply_from = reply_to.get("from") or {}
    is_reply_to_bot = bool(
        (bot_id is not None and reply_from.get("id") == bot_id)
        or (bot_username and (reply_from.get("username") or "").lower() == bot_username.lower())
    )

    # 2. Handle Direct Commands (!mode, !reset, !track, !untrack, !tracked)
    low_input = normalized_input.lower()
    if low_input in ("/start", "!start"):
        await send_telegram_message(
            internal_cid,
            f"👋 Hey {user_display}! Bot is online in **{main_mod.current_mode.upper()}** mode.",
            token=bot_token,
        )
        return

    if low_input.startswith("!mode"):
        tg_owner = get_telegram_owner_id()
        if tg_owner is None or int(user_id) != tg_owner:
            print(f"🔒 [Telegram !mode denied]: {user_display} ({user_id})")
            await send_telegram_message(internal_cid, "🔒 Only the bot owner can switch modes.", token=bot_token)
            return
        new_mode = normalized_input.split(" ")[-1].lower()
        if new_mode in MODES:
            previous_model = MODES[main_mod.current_mode]["model"]
            new_model = MODES[new_mode]["model"]
            if previous_model != new_model:
                await send_telegram_message(
                    internal_cid,
                    f"🧹 Unloading `{previous_model}` from GPU memory to make room...",
                    token=bot_token,
                )
                try:
                    await main_mod.ollama_client.generate(model=previous_model, keep_alive=0)
                except Exception:
                    pass
            main_mod.current_mode = new_mode
            main_mod.chat_history.clear()
            if hasattr(main_mod, "reset_channel_memory"):
                main_mod.reset_channel_memory(internal_cid)
            await send_telegram_message(
                internal_cid,
                f"🔄 Switched to **{new_mode.upper()}** mode (Model: `{new_model}`). Memory and context wiped.",
                token=bot_token,
            )
        else:
            await send_telegram_message(
                internal_cid,
                f"⚠️ Unknown mode. Available modes: {', '.join(MODES.keys())}",
                token=bot_token,
            )
        return

    if low_input == "!reset":
        main_mod.chat_history.pop(internal_cid, None)
        if hasattr(main_mod, "reset_channel_memory"):
            main_mod.reset_channel_memory(internal_cid)
        await send_telegram_message(internal_cid, "🧹 Memory and context wiped for this chat.", token=bot_token)
        return

    if low_input.startswith("!track "):
        target_q = normalized_input[7:].strip()
        set_active_channel_id(internal_cid)
        res = await asyncio.to_thread(skill_track_live_game, action="start", query=target_q)
        LIVE_TRACKER.ensure_loop_running()
        await send_telegram_message(internal_cid, res, token=bot_token)
        return

    if low_input.startswith("!untrack"):
        target_q = normalized_input[8:].strip()
        set_active_channel_id(internal_cid)
        res = await asyncio.to_thread(skill_track_live_game, action="stop", query=target_q)
        await send_telegram_message(internal_cid, res, token=bot_token)
        return

    if low_input in ("!tracked", "!track"):
        set_active_channel_id(internal_cid)
        res = await asyncio.to_thread(skill_track_live_game, action="list")
        await send_telegram_message(internal_cid, res, token=bot_token)
        return

    # 3. In group chats, only respond when @mentioned or replied to
    is_private = chat_type == "private"
    if not is_private and not is_mentioned and not is_reply_to_bot:
        return
    if not normalized_input:
        return

    # 4. Run standard conversation turn reusing main.run_agent_turn
    async with main_mod.channel_locks[internal_cid]:
        mode_config = MODES[main_mod.current_mode]
        is_research = mode_config.get("is_research", False)
        tools.set_memory_file(mode_config["memory_file"])

        system_msg = {"role": "system", "content": mode_config["get_prompt"](tools.recall_memories())}
        if internal_cid not in main_mod.chat_history:
            main_mod.chat_history[internal_cid] = [system_msg]
        else:
            main_mod.chat_history[internal_cid][0] = system_msg

        ref_context = ""
        ref_text = reply_to.get("text") or reply_to.get("caption")
        if ref_text:
            ref_context = extract_reply_context(ref_text)

        if main_mod.current_mode == "chat":
            user_msg = f"{user_display} (User ID: {user_id}){ref_context}: {normalized_input}"
        else:
            user_msg = f"User {user_display} says{ref_context}: {normalized_input}"

        main_mod.chat_history[internal_cid].append({"role": "user", "content": user_msg})

        await asyncio.to_thread(
            _tg_api_post,
            bot_token,
            "sendChatAction",
            {"chat_id": chat_id, "action": "typing"},
            10,
        )

        try:
            reply = await main_mod.run_agent_turn(internal_cid, mode_config)
            if not reply.strip():
                reply = (
                    "Don't leave me hanging—what's on your mind?"
                    if main_mod.current_mode == "chat"
                    else "⚠️ Generated an empty response."
                )

            main_mod.chat_history[internal_cid] = trim_history(
                main_mod.chat_history[internal_cid],
                mode_config.get("history_limit", 40),
            )

            if main_mod.current_mode == "chat":
                reply = apply_auto_ping(reply, main_mod.known_users)

            if is_research:
                reply = join_orphan_bullets(reply)

            set_active_channel_id(internal_cid)
            reply = await asyncio.to_thread(standardize_song_reply, reply)
            reply = sanitize_agent_reply(reply)

            await send_telegram_message(internal_cid, reply, token=bot_token)
        except Exception as e:
            print(f"❌ [Telegram Error]: {type(e).__name__}: {e}")
            await send_telegram_message(
                internal_cid,
                "⚠️ Something went wrong while handling that. Please try again!",
                token=bot_token,
            )


_POLLING_ACTIVE = False


async def start_telegram_bridge(main_mod: Any) -> None:
    """
    Entrypoint spawned as a background task from main.py's on_ready() when ENABLE_TELEGRAM=true.
    Validates TELEGRAM_BOT_TOKEN and runs the long-polling loop.
    """
    global _POLLING_ACTIVE
    if _POLLING_ACTIVE or not is_telegram_enabled():
        return

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token or bot_token == "your_telegram_bot_token_here":
        print("⚠️ [Telegram]: ENABLE_TELEGRAM is true, but TELEGRAM_BOT_TOKEN is missing in .env! Continuing with Discord only.")
        return

    allowed = parse_allowed_ids()
    if not allowed:
        print(
            "⚠️ [Telegram]: No TELEGRAM_OWNER_ID or TELEGRAM_ALLOWED_IDS set in .env! "
            "Fail-closed security is active: all incoming Telegram messages will be blocked and logged with their IDs."
        )

    me_res = await asyncio.to_thread(_tg_api_post, bot_token, "getMe", {}, 15)
    if not me_res or not me_res.get("ok"):
        print(f"❌ [Telegram]: Invalid TELEGRAM_BOT_TOKEN or API unreachable: {me_res}. Continuing with Discord only.")
        return

    bot_info = me_res.get("result") or {}
    bot_username = bot_info.get("username", "")
    bot_id = bot_info.get("id")
    print(f"✅ Telegram Bridge online as: @{bot_username} (Allowed IDs: {sorted(allowed) if allowed else 'NONE - Fail-Closed'})")

    _POLLING_ACTIVE = True
    offset = 0
    try:
        while True:
            try:
                poll_payload = {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message", "message_reaction"],
                }
                data = await asyncio.to_thread(_tg_api_post, bot_token, "getUpdates", poll_payload, 35)
                if not data or not data.get("ok"):
                    await asyncio.sleep(5)
                    continue

                for upd in data.get("result") or []:
                    upd_id = upd.get("update_id", 0)
                    if upd_id >= offset:
                        offset = upd_id + 1
                    asyncio.create_task(
                        handle_telegram_update(
                            upd,
                            main_mod=main_mod,
                            bot_token=bot_token,
                            bot_username=bot_username,
                            bot_id=bot_id,
                        )
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ [Telegram Polling Error]: {e}")
                await asyncio.sleep(5)
    finally:
        _POLLING_ACTIVE = False

