# Coding & Architectural Harness Guidelines

Thank you for contributing! This repository uses an **AST-Enforced Coding Harness** ([`tests/test_architecture_and_boundaries.py`](tests/test_architecture_and_boundaries.py)) alongside its **Agent Runtime Harness** ([`src/orchestrator.py`](src/orchestrator.py), [`main.py`](main.py)) so that both human developers and AI coding agents can add skills rapidly without ever introducing cross-skill coupling, thread-pool deadlocks, or architectural drift.

Every pull request and commit must pass the coding harness rules below.

---

## 🏛️ Core Coding Harness Rules

### 1. Zero Lateral Cross-Skill Imports (AST-Enforced)
- Domain skill packages under `src/skills/` (`music`, `sports`, `flights`, `markets`, `graphing`, `voice`, `jobs`, `news`, `weather`, `youtube`) must **never** import from a sibling domain skill package.
- Per-turn channel context (`ACTIVE_CHANNEL_ID`, `get_active_channel_id()`, `set_active_channel_id()`, `record_channel_human_activity()`, `queue_channel_attachment()`, `pop_channel_attachments()`) and main asyncio event-loop binding (`bind_event_loop()`, `get_bound_event_loop()`) live strictly in **`src/context.py`**.
- Shared background scheduling (`JOB_ENGINE`, `UserJob`, `SystemJobProvider`, cron/interval/condition/autoplay triggers) lives in **`src/jobs/`**.
- General web research tools (`search_web`, `search_images` with multimodal vision verification, `read_website`, and `read_youtube_video` with full-timeline `0%–100%` chapter digest + `query`/timestamp zoom) live in **`src/tools/web.py`**, and system utilities (`get_current_datetime`, `evaluate_math`, `save_memory`, `recall_memories`) live in **`src/tools/system.py`**.
- Shared cross-domain vocabulary sets (`TECH_HARDWARE_WORDS`, `SPORTS_DOMAIN_WORDS`, `FLIGHTS_DOMAIN_WORDS`, `has_non_music_domain_signal()`, `has_non_sports_track_signal()`, `TOOL_ARG_ALIASES`) live strictly in **`src/utils/domain_vocab.py`**.

### 2. Standardized Skill Package Contract (`src/skills/.example/`)
Every skill package under `src/skills/` must follow the exact same standardized layout and naming conventions (use **`src/skills/.example/`** as a copy-paste skeleton when creating a new skill):
- **`__init__.py`**: Public skill exports (`skill_<domain>_<capability>` entrypoints, tracker instances, and key models).
- **`<skill_name>.py`** (e.g., `music.py`, `sports.py`, `flights.py`, `markets.py`, `graphing.py`, `voice.py`, `jobs.py`, `weather.py`, `news.py`, `youtube.py`, `example.py`): Constructor-style primary skill module defining LLM-facing `skill_<domain>_<capability>` tool entrypoint functions (`skill_music_discovery`, `skill_music_analysis`, `skill_sports_info`, `skill_sports_esports`, `skill_sports_odds`, `skill_flight_info`, `skill_markets_info`, `skill_graphing_info`, `skill_voice_info`, `skill_job_manager`, `skill_weather_info`, `skill_news_headlines`, `skill_youtube_search`). Never use generic `skill.py` filenames or scatter tool entrypoints across ad-hoc top-level files.
- **`enums.py`**: Strictly `enum.StrEnum` classes for state machines, axes, action modes, and provider/sport identifiers.
- **`models.py`**: Strictly `@dataclass(slots=True)` data classes for typed domain records (inheriting from `DictLikeMixin` in `src/utils/domain_vocab.py` if dictionary-style access `obj["key"]` is needed by legacy callers). Never mix `StrEnum`s or static lookup dictionaries into `models.py`.
- **`catalogs.py`**: Strictly static lookup tables, airport/league/genre registries, alias maps, matrices, and `frozenset` stopword collections.
- **`templates.py`**: Concise (3–4 lines max), centralized tool-return prompt builders (`=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===`).
- **`aggregators/`**: External API & web-scraping provider classes (`__init__.py`, `base.py` defining `Base<Domain>Aggregator`, optional `manager.py` defining `<Domain>AggregatorManager`, and `<provider>.py` defining `<Provider><Domain>Aggregator`).
- **Optional Standardized Modules**:
  - **`context.py`** *(for stateful/multi-turn skills)*: Per-channel state tracking (`_CHANNEL_LAST_*`), `enrich_*_tool_args()`, and `detect_missed_*_tool_call()`.
  - **`tracker.py`** *(for background-polling skills)*: Asyncio background polling service (`<Domain>TrackerService` + `<DOMAIN>_TRACKER` singleton) and `skill_<domain>_tracker` tool entrypoint.
  - **`engine/`** *(for pure domain algorithms/parsers/DSP)*: Internal domain engines (`resolver.py`, `spectrum.py`, `moods.py`, `dsp.py`, `formatting.py`, `client.py`).
  - **`playback/`** *(for media playback resolution)*: Platform-specific playback resolvers and Discord voice streaming sinks.

### 3. Shared 3-Tier Background Worker Pool Discipline (AST-Enforced)
- Never instantiate ad-hoc `ThreadPoolExecutor` pools inside skills, tools, or aggregators.
- Use `run_in_worker`, `submit_to_worker`, or `map_in_workers` from **`src/utils/workers.py`**, which route work across a deadlock-free 3-tier executor DAG (`WORKER_POOL` → `SUB_WORKER_POOL` → `LEAF_WORKER_POOL` → inline fallback at depth >= 3) while propagating isolated copies of `contextvars` (including `ACTIVE_CHANNEL_ID` and `ACTIVE_MEMORY_FILE`).

### 4. Security, SSRF & Memory Hygiene Guardrails
- **SSRF & Redirect Protection**: Route external HTTP fetches through **`src/utils/http.py`** (`fetch_url`, `fetch_json`, `fetch_text`, `fetch_soup`) or validate hosts with `_is_private_or_local_host()` on every redirect hop so loopback, RFC1918 private networks, link-local, and cloud metadata IPs are always blocked.
- **Secret Redaction**: Never log or cache raw API keys or bot tokens. Use `SecretRedactingFilter` and `redact_secrets()` from `src/utils/http.py` so URL query secrets (`apiKey`, `api_key`, `key`, `token`, `access_token`) and Telegram `/bot<id>:<secret>/` paths are automatically scrubbed.
- **Bounded Caches**: Always use `BoundedCache` or `BoundedSet` from `src/utils/cache.py` for module-level caches, and strip transient binary payloads (such as raw `_mp3_bytes` or image buffers) before storing entries.
- **Discord Mention Safety**: Keep `discord.AllowedMentions(everyone=False, roles=False, users=True)` enforced on the Discord client and pass outbound LLM text through `sanitize_agent_reply()`.

### 5. Zero Test-Detection Hacks in Production Code (AST-Enforced)
- Production code under `src/` must **never** inspect `unittest.mock` attributes (`assert_called`, `_mock_name`, `MagicMock`, `side_effect`).
- Unit tests under `tests/` must explicitly `@patch` external network/API boundaries (e.g., `fetch_json`, `fetch_soup`, `fetch_flights_html`, `resolve_youtube_music_link`) and clear module caches in `setUp()`.

### 6. No Silent `except Exception: pass` Blocks
- Catch narrow exception types (`(ValueError, KeyError, TypeError)`) or log unexpected exceptions via `logging.getLogger(__name__).debug(...)` so bugs are never silently swallowed.

---

## 🧪 Pre-Commit Harness Verification

Always run the full test suite before committing and verify 100% of tests pass:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Or on Windows PowerShell:

```powershell
python -m unittest @(Get-ChildItem tests\test_*.py | ForEach-Object { "tests/$($_.Name)" })
```
