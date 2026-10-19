# 🤖 Local Ollama Multi-Skill Discord & Telegram AI Agent

A privacy-first, locally hosted AI research and entertainment assistant for **Discord** and **Telegram**, powered by **Ollama** (`gemma4:e4b`, `qwen3:8b`, etc.) with a modular, multi-threaded Python skill engine.

Designed specifically to give compact local LLMs (4B–8B parameters) **deterministic, real-time tool execution**, pre-verified links, timezone-aware schedules, and background event tracking without relying on paid cloud LLM APIs.

---

## ✨ Key Capabilities

| Domain | Capabilities |
| :--- | :--- |
| 🎙️ **Real-Time Discord Voice-Channel AI Co-Host** | Joins live Discord voice channels (`!join` or *"hop in the voice channel"*) as an interactive AI co-host. Features **Per-Speaker Voice Activity Detection (VAD)** (`48 kHz` stereo $\rightarrow$ `16 kHz` mono), **Zero-Extra-VRAM Speech-to-Text** via **`gemma4:e4b` native `gemma4a` USM audio encoder** (plus optional `faster-whisper`), **Wake-Word / Open-Mic / Listen-Only modes**, **Persona-Matched Neural TTS playback** (`edge-tts` / local `VOICE_TTS_URL` / pure-Python `wave_synth` fallback), **Live Sports & Flight Tracker Voice Break-Ins**, **Speaker-Attributed Call Recaps (`!vrecap`)**, and **Discord Voice Message / Audio Attachment Transcription**. |
| 📊 **On-Demand Graphing, Charts & Diagrams** | Deterministic, dark-themed high-DPI PNG rendering for **Arbitrary 2D Data Graphs** (`bar`, `horizontal_bar`, `stacked_bar`, `line`, `area`, `scatter`, `pie`, `donut`), **Financial & Multi-Ticker Market Plots** (single-ticker price + 50d/200d SMA + volume, or `%` return comparisons), and **Architecture Diagrams & Flowcharts** (`flowchart`, `sequence`, `architecture`, `state`, `mindmap` via **Kroki** with offline **Matplotlib Directed-DAG & Sequence** fallback). |
| 📈 **Global Markets, TSX/Stocks & Crypto** | Real-time global macro benchmarks (**S&P/TSX**, **S&P 500**, **Nasdaq 100**, **Dow**, **FTSE**, **DAX**, **Nikkei**, **VIX**, **Gold**, **Silver**, **WTI Crude**, **USD/CAD**, **10Y Yields**), **Risk-On / Risk-Off Regime Classification**, **Yahoo Finance v8 + TradingView Scanner fallback** for US/Canadian TSX equities & ETFs, **CoinGecko Top Movers & DeFi TVL**, **Historical Chart Summaries (`1d`–`5y`)** with annualized volatility & max drawdown, **Fundamentals & Earnings Dates**, **Side-by-Side Comparison Matrix**, and **Portfolio / Watchlist Valuation**. |
| 🎵 **Music Discovery & Audio DSP** | Multi-source chart & underground discovery (**Last.fm**, **Deezer**, **Beatport**, **FreqBlog**, **iTunes RSS / Billboard**), **Genre × Mood Matrix**, **3×3 Tempo × Intensity Spectrum Shifting** (`faster`, `slower`, `harder`, `softer`), **In-Memory DSP (`miniaudio` + `numpy`) BPM/Key/Gain Analysis**, native **`gemma4:e4b` Audio Listening**, and **👍 Song-Card Reaction Similar-Track Discovery**. |
| 🏟️ **Live Sports, CS2 Esports & Decimal Odds** | Real-time scoreboards, fixtures, and standings for **Soccer** (UCL, EPL, La Liga, Serie A, Bundesliga, Ligue 1, MLS, etc.), **NHL**, **NBA**, **NFL**, **MLB**, **UFC**, **Formula 1** (3-day session timings & driver/constructor standings), and **Counter-Strike 2** (**HLTV**, **Liquipedia**, **ESL/BLAST** live streams). Includes **Decimal Betting Odds** with implied win probabilities and **Background Live Match Tracking** (kickoff, goals, red cards, period/halftime, full-time alerts). |
| ✈️ **Flights, Airfare Calendars & Live Radar** | Live **Google Flights** itinerary scraping (Best, Cheapest, Shortest/Fastest), **Round-Trip & Flexible-Date Price Grids**, **Direct/Nonstop Route Schedules**, and **Live Commercial Flight Status & Radar Telemetry** (**Flightradar24** & **ADSB.lol** live ADS-B altitude/groundspeed/heading). Includes **Background Flight Status & Route Price Drop Alerts**. |
| 🌐 **Web Research, News & Weather** | Multi-engine web search (**DuckDuckGo** + **Brave Search** fallback), SSRF-hardened stealth article reader (**Jina Reader** + **curl_cffi** TLS impersonation), **YouTube Transcript & Metadata** analyzer, **Google News RSS** headlines (with automatic **Daily Global Market Pulse** enrichment), and **Open-Meteo** current + up to **16-day weather forecasts** (with **wttr.in** automatic fallback). |
| 💬 **Dual-Platform Discord + Telegram** | Runs simultaneously on **Discord** (with per-channel concurrency locks, live voice co-hosting, and 👍 song-card reactions) and **Telegram** (zero-dependency HTTPS long-polling bridge with fail-closed user/chat allowlist security). |

---

## 🏗️ Architecture Overview

Every skill under `src/skills/` follows an identical, standardized package layout (`__init__.py`, `<skill_name>.py`, `enums.py`, `models.py`, `catalogs.py`, `templates.py`, `aggregators/`, plus optional `context.py`, `tracker.py`, `engine/`, `playback/`), with a copy-paste-ready skeleton in `src/skills/.example/`. Cross-cutting concerns live in a shared core execution context (`src/context.py`), shared domain vocabularies (`src/utils/domain_vocab.py`), and a centralized background worker pool (`src/utils/workers.py`).

```mermaid
flowchart TD
    subgraph Entry["Platform Entry Layer"]
        MAIN["main.py (Discord Bot & Voice Runtime)"]
        TG["src/utils/telegram.py (Telegram Bridge)"]
        ORCH["src/orchestrator.py (Tool Routing & Sanitization)"]
        REG["src/registry.py (Tool Schemas)"]
        CFG["src/config.py / src/config.example.py"]
    end

    subgraph Core["Shared Core & Infrastructure Layer"]
        CTX["src/context.py (ACTIVE_CHANNEL_ID & Event Loop Bridge)"]
        VOCAB["src/utils/domain_vocab.py (Token Sets, Tool Aliases & DictLikeMixin)"]
        WRK["src/utils/workers.py (Shared ThreadPoolExecutor)"]
        HTTP["src/utils/http.py (curl_cffi Stealth Client + SSRF Guard)"]
        TZ["src/utils/timezones.py (Dual Local + UTC/CET Formatter)"]
    end

    subgraph Skills["Standardized Domain Skills (src/skills/)"]
        VCE["🎙️ voice\n(voice.py, enums, models, catalogs, templates,\ncontext, tracker, engine/, aggregators/, playback/)"]
        GRP["📊 graphing\n(graphing.py, enums, models, catalogs, templates,\ncontext, engine/, aggregators/)"]
        MKT["📈 markets\n(markets.py, enums, models, catalogs, templates,\ncontext, engine/, aggregators/)"]
        MUS["🎵 music\n(music.py, enums, models, catalogs, templates,\ncontext, engine/, aggregators/, playback/)"]
        SPT["🏟️ sports\n(sports.py, enums, models, catalogs, templates,\ncontext, tracker, aggregators/)"]
        FLT["✈️ flights\n(flights.py, enums, models, catalogs, templates,\ncontext, tracker, engine/, aggregators/)"]
        NWS["📰 news\n(news.py, enums, models, catalogs, templates,\naggregators/)"]
        WTH["🌤️ weather\n(weather.py, enums, models, catalogs, templates,\naggregators/)"]
        YT["📺 youtube\n(youtube.py, enums, models, catalogs, templates,\nengine/, aggregators/)"]
        EX["🧩 .example\n(example.py + Copy-Paste Skeleton)"]
    end

    MAIN --> ORCH
    TG --> ORCH
    MAIN --> CTX
    TG --> CTX
    ORCH --> REG
    REG --> Skills
    Skills --> Core
```

### 📁 Standard Skill Package Layout (`src/skills/<skill_name>/`)

Every skill package under `src/skills/<skill_name>/` follows this exact structure, with the primary module named `<skill_name>.py` to match its folder name (see `src/skills/.example/` for a copy-paste-ready skeleton):

```text
src/skills/<skill_name>/
├── __init__.py             # Public exports (skill_<skill_name>_* entrypoints & optional tracker)
├── <skill_name>.py         # Constructor-style main module with LLM tool entrypoint(s) (skill_<skill_name>_*)
├── enums.py                # Strictly enum.StrEnum classes (modes, states, providers)
├── models.py               # Strictly @dataclass(slots=True) domain models
├── catalogs.py             # Strictly static registries, lookup dicts & frozenset stopwords
├── templates.py            # Concise LLM formatting instructions (3–4 lines max)
├── context.py              # [Optional] Per-channel state, arg enrichment & turn-0 recovery
├── tracker.py              # [Optional] Asyncio background polling service (<Domain>TrackerService)
├── engine/                 # [Optional] Pure domain algorithms, parsers, or DSP helpers
├── playback/               # [Optional] Audio/media streaming & Discord voice sinks
└── aggregators/            # External API & scraping providers
    ├── __init__.py
    ├── base.py             # Abstract base class (Base<Domain>Aggregator)
    └── example_provider.py # Concrete data fetcher using src/utils/http.py & workers.py
```

#### 🧩 Adding a New Skill in 5 Steps
1. **Copy the skeleton** to `src/skills/<your_skill>/` and rename `example.py` to `<your_skill>.py`:
   ```bash
   cp -r src/skills/.example src/skills/<your_skill>
   mv src/skills/<your_skill>/example.py src/skills/<your_skill>/<your_skill>.py
   ```
2. **Define your types & static data**:
   - Put `StrEnum`s in `enums.py`, `@dataclass(slots=True)` models in `models.py`, static dicts/`frozenset` stopwords in `catalogs.py`, and concise LLM rules in `templates.py`.
3. **Implement your data provider** in `aggregators/` inheriting from `Base<Domain>Aggregator` in `aggregators/base.py`, using `fetch_json` / `fetch_text` from `src.utils.http` and `map_in_workers` / `submit_to_worker` from `src.utils.workers`.
4. **Implement your tool entrypoint** (`def skill_<your_skill>_info(...) -> str:`) in `<your_skill>.py` and export it in `src/skills/<your_skill>/__init__.py` and `src/skills/__init__.py`.
5. **Register the tool** in `src/registry.py` (`RESEARCH_TOOLS` / `CHAT_TOOLS`) and add parameter synonyms to `TOOL_ARG_ALIASES` in `src/utils/domain_vocab.py`.

#### 💡 Why Code-First Skill Packages Instead of `SKILL.md`?
Many coding and OS agents (like Claude Code, Cursor, or OpenClaw) use `SKILL.md` files — Markdown instruction manuals that a large cloud model reads at runtime to figure out what shell commands or scripts to run across multiple turns.

This bot intentionally uses **compiled Python skill packages + `templates.py`** instead:
- **Built for Fast Local Models (`4B–8B`)**: Rather than asking a small local model to read a Markdown guide and improvise CLI commands across 3–4 turns, each skill exposes a strict, typed Python function (`<skill_name>.py`) and appends concise formatting rules (`templates.py`) directly to the tool output in a **single turn**.
- **Deterministic Speed & Concurrency**: Heavy lifting — like 24-thread worker fan-out, API fallback chains, and in-memory MP3/PCM signal processing — runs in deterministic, unit-tested Python rather than prompt instructions.

---

## 🚀 Getting Started

### 1. Prerequisites
- **Python 3.11+** (tested up to Python 3.14)
- **[Ollama](https://ollama.com/)** running locally (`ollama serve`)
  - Pull your preferred model(s), e.g.:
    ```bash
    ollama pull gemma4:e4b
    ollama pull qwen3:8b
    ```

### 2. Installation
Clone the repository and install dependencies into a virtual environment:

```bash
git clone <your-repo-url>
cd discord-bot
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Configuration (`.env` and `src/config.py`)
1. Copy the environment variable template and add your **Discord Bot Token** (and optional API keys):
   ```bash
   cp .env.example .env
   ```
2. Copy the public bot persona/model configuration template to `src/config.py` (which is git-ignored so your custom prompts and local model tags stay private):
   ```bash
   cp src/config.example.py src/config.py
   ```
   *(If `src/config.py` is omitted, the bot automatically loads the default `research` and `chat` profiles from `src/config.example.py`.)*

### 4. Run the Bot

#### Option A: Run Natively
```bash
python main.py
```

#### Option B: Run in a Hardened, Self-Contained Docker Stack (`ollama` + `discord-bot`)
For complete filesystem and network isolation from the host machine (pre-configured with `libopus0` and `ffmpeg` for Discord Voice Co-Hosting):
```bash
docker compose up -d --build
```
- **Dedicated GPU `ollama` Container**: Runs `ollama/ollama` inside a sibling container on a private internal bridge network (`http://ollama:11434`) with GPU passthrough (`capabilities: [gpu]`) and an isolated `ollama_models` volume. Missing models in `MODES` are pulled automatically on first boot (`OLLAMA_AUTO_PULL=true`).
- **Zero Host Bind-Mounts**: No host folders are mounted into either container (`ollama_models`, `bot_data`, and `bot_workspace` use isolated Docker-managed named volumes).
- **Read-Only Root Filesystem**: `/`, `/usr`, and `/app` in `discord-bot` are mounted read-only; the bot runs as an unprivileged user (`1000:1000`) with `cap_drop: [ALL]` and `no-new-privileges:true`.

---

## 🎙️ Discord Voice-Channel AI Co-Host Guide

The bot can join any Discord voice channel and participate as a real-time, voice-activated AI co-host while retaining full access to all 10 domain skills.

### How It Works
1. **Join a Voice Channel**: Hop into any Discord voice channel and type **`!join`** (or **`!join conversational`**), or simply ask in chat: *"@Bot hop into the voice channel"*.
2. **Per-Speaker Listening & Native `gemma4:e4b` Speech-to-Text**:
   - The bot demultiplexes incoming `48 kHz` stereo voice streams **per speaker** (so it knows whether *Elvis*, *Alex*, or *Sam* is talking), detects speech boundaries using RMS dBFS Voice Activity Detection (`0.45s` silence gap), resamples to `16 kHz` mono WAV in memory, and transcribes speech via **`gemma4:e4b`'s native `gemma4a` USM audio encoder** (zero extra VRAM required) or local `faster-whisper`.
3. **Choose Your Listening Mode (`!vmode <mode>`)**:
   - **`wake_word` *(Default)***: Transcribes all utterances into the rolling call log for context, but **only speaks back** when someone says the wake word (`"hey bot"`).
   - **`conversational` *(Open Mic)***: Replies to every spoken question or statement ($\ge 2$ words) without requiring a wake word.
   - **`listen_only` *(Passive Scribe)***: Silently logs the conversation in the background so you can generate a speaker-attributed call recap (`!vrecap`) at any time.
4. **Dual Text + Persona-Matched Neural TTS Replies**:
   - When triggered in voice chat, the bot executes any needed tools (sports scores, stock quotes, flights, web search, music), posts the full formatted answer with links in the text channel, and speaks a concise, natural spoken summary out loud into the Discord voice channel using a persona-matched Neural voice (`Research Co-Host`, `Casual Hangout Co-Host`, or `Live Sportscaster Voice`).
5. **Live Sports & Flight Tracker Voice Break-Ins**:
   - While connected to a voice channel (and unmuted), any live goal, score change, red card, halftime, final whistle (`!track`), or flight status alert (`!trackflight`) is automatically announced out loud in the voice call in real time.
6. **Discord Voice Messages & Audio Attachments**:
   - Uploading or recording a Discord voice message (`.ogg`, `.wav`, `.mp3`, `.m4a`, `.flac`) while mentioning the bot automatically transcribes the audio and answers your spoken prompt.

---

## 🎮 Commands & Natural Language Prompts

### Bot Commands & Interactions
| Discord | Telegram | Description |
| :--- | :--- | :--- |
| `!mode <profile>` | `/mode <profile>` | Switch active AI model/persona profile (e.g., `research`, `chat`; restricted to `BOT_OWNER_ID`). |
| `!reset` | `/reset` | Wipe conversation history, tool caches, and active voice session for the current channel. |
| `!join [mode]` | — | Join your current Discord voice channel (`!join`, `!join conversational`, `!join listen_only`). |
| `!leave` / `!disconnect` | — | Disconnect the AI co-host from the active Discord voice channel. |
| `!speak <text>` | — | Synthesize `<text>` via Neural TTS and speak it out loud in the active voice channel. |
| `!vmode <mode>` | — | Switch voice listening mode (`wake_word`, `conversational`, `listen_only`). |
| `!vrecap` | — | Post a chronological, speaker-attributed transcript recap of the voice call. |
| `!vstatus` | — | View active voice channel connection, listening mode, TTS voice profile, and utterance stats. |
| `!vmute` / `!vunmute` | — | Mute or unmute spoken TTS audio output while keeping voice transcription active. |
| `!track <team/match>` | `/track <team/match>` | Start live background match tracking (`!untrack` to stop, `!tracked` to list active games). |
| `!trackflight <flight/route>` | `/trackflight <flight/route>` | Start live flight status or route price watch (`!untrackflight` to stop, `!trackedflights` to list). |
| React with 👍 on a song card | React with 👍 on a song card | Automatically finds and posts a fresh, acoustically similar song to the reacted track. |

### Natural Language Examples
- **Discord Voice Co-Host (`skill_voice_info`)**:
  - *"Hop into the voice channel in conversational mode"*
  - *(Spoken in voice chat)*: *"Hey bot, what is Shopify stock trading at right now?"*
  - *(Spoken in voice chat)*: *"Hey bot, when do the Oilers play next and what are the odds?"*
  - *"Give me a recap of what everyone talked about in the voice call"*
  - *"Announce in the voice channel that we're starting the match in 5 minutes"*
- **On-Demand Graphing, Charts & Diagrams (`skill_graphing_info`)**:
  - *"Graph a bar chart of GPU VRAM: RTX 4090: 24, RTX 4080: 16, RTX 4070 Ti: 12"*
  - *"Plot NVDA vs AMD vs SHOP.TO over the last 6 months"*
  - *"Draw a flowchart of how an OAuth2 login request is authenticated"*
  - *"Create a sequence diagram for a Discord message triggering a tool call in Ollama"*
- **Global Markets, Stocks/TSX & Crypto (`skill_markets_info`)**:
  - *"How are the Canadian and US markets doing today?"*
  - *"Compare NVDA vs AMD vs SHOP.TO over the last 6 months"*
  - *"Show me the 1-year performance, volatility, and max drawdown for Bitcoin and Gold"*
  - *"Value my portfolio: 25 AAPL, 40 SHOP.TO, 0.5 BTC"*
  - *"What are the fundamentals and next earnings date for Microsoft?"*
- **Music Discovery & Spectrum Shifts (`skill_music_discovery`)**:
  - *"Play me a dark techno banger"*
  - *"Give me something a little faster and harder"* (or reply directly to a song card with *"slower"* / *"softer"*)
  - *"Find tracks that harmonically mix with Strobe by Deadmau5"*
- **Sports, CS2 Esports & Live Match Tracking (`skill_sports_info` / `skill_sports_tracker`)**:
  - *"What are the Champions League and NHL games today and their odds?"*
  - *"When is the next F1 race and what are the session times?"*
  - *"What's happening in CS2 right now and what are Vitality's player stats?"*
  - *"Track the Real Madrid game"* / *"Which games are we tracking right now?"* / *"Stop tracking the Real Madrid game"*
- **Flights, Airfare Calendars & Live Radar Tracking (`skill_flight_info` / `skill_flight_tracker`)**:
  - *"Find the cheapest round-trip flights from New York to Tokyo in November for 2 weeks"*
  - *"When are the direct flights from JFK to LHR?"*
  - *"Where is flight BA117 right now?"*
  - *"Track flight BA117"* / *"Track the price for JFK to LHR on Nov 21"* / *"Stop tracking flight BA117"*

---

## 🧪 Running the Test Suite

The repository includes **290+ unit, concurrency, combinatorial, and architectural boundary tests** (executing completely offline in ~6 seconds):

```bash
python -m unittest discover -s tests -p "test_*.py"
```

To run the architectural & module boundary guardrails specifically:

```bash
python -m unittest tests/test_architecture_and_boundaries.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for code quality standards and skill authoring guidelines.
