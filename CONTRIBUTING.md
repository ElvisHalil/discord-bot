# Contributing & Architectural Guidelines

Thank you for contributing! To keep the codebase modular, fast, and maintainable across all skills, every pull request must adhere to the following architectural rules (enforced automatically by `tests/test_architecture_and_boundaries.py`).

---

## 🏛️ Core Architectural Rules

### 1. Zero Lateral Cross-Skill Imports
- Domain skill packages under `src/skills/` (`music`, `sports`, `flights`, `news`, `weather`) must **never** import from a sibling domain skill package.
- Per-turn channel context (`ACTIVE_CHANNEL_ID`, `get_active_channel_id()`, `set_active_channel_id()`) and main asyncio event-loop binding (`bind_event_loop()`, `get_bound_event_loop()`) live strictly in **`src/context.py`**.
- Shared cross-domain vocabulary sets (`TECH_HARDWARE_WORDS`, `SPORTS_DOMAIN_WORDS`, `FLIGHTS_DOMAIN_WORDS`, `has_non_music_domain_signal()`, `has_non_sports_track_signal()`, `TOOL_ARG_ALIASES`) live strictly in **`src/utils/domain_vocab.py`**.

### 2. Standardized Domain Separation: `enums.py`, `models.py`, `catalogs.py`, `templates.py`
Every domain skill must separate its declarations into dedicated, single-responsibility modules rather than mixing them or scattering magic dicts/sets across aggregators:
- **`enums.py`**: Strictly `enum.StrEnum` classes for state machines, axes, action modes, and provider/sport identifiers.
- **`models.py`**: Strictly `@dataclass(slots=True)` data classes for typed domain records (inheriting from `DictLikeMixin` in `src/utils/domain_vocab.py` if dictionary-style access `obj["key"]` is needed by legacy callers). Never mix `StrEnum`s or static lookup dictionaries into `models.py`.
- **`catalogs.py`**: Strictly static lookup tables, airport/league/genre registries, alias maps, matrices, and `frozenset` stopword collections.
- **`templates.py`**: Concise (3–4 lines max), centralized tool-return prompt builders (`=== SKILL INSTRUCTIONS FOR YOU (THE LLM) ===`).

### 3. Shared Background Worker Pool Discipline
- Never instantiate ad-hoc `ThreadPoolExecutor` pools inside skills or aggregators.
- Use `submit_to_worker`, `map_in_workers`, or `run_in_worker` from **`src/utils/workers.py`**, which automatically propagate `contextvars` (including `ACTIVE_CHANNEL_ID`) to worker threads.

### 4. Zero Test-Detection Hacks in Production Code
- Production code under `src/` must **never** inspect `unittest.mock` attributes (`assert_called`, `_mock_name`, `MagicMock`).
- Unit tests under `tests/` must explicitly `@patch` external network/API boundaries (e.g., `fetch_json`, `fetch_soup`, `fetch_flights_html`, `resolve_youtube_music_link`) and clear module caches in `setUp()`.

### 5. No Silent `except Exception: pass` Blocks
- Catch narrow exception types (`(ValueError, KeyError, TypeError)`) or log unexpected exceptions via `logging.getLogger(__name__).debug(...)` so bugs are never silently swallowed.

---

## 🧪 Verification Before Submitting

Always run the full test suite before committing:

```bash
python -m unittest discover -s tests -p "test_*.py"
```
