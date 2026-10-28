import ast
from dataclasses import is_dataclass
from enum import StrEnum
import importlib
from pathlib import Path
import unittest

from src.context import (
    ACTIVE_CHANNEL_ID,
    bind_event_loop,
    get_active_channel_id,
    get_bound_event_loop,
    set_active_channel_id,
)
from src.skills.flights.catalogs import (
    AIRPORT_DIRECTORY,
    NEARBY_HUB_AIRPORTS,
    ORIGIN_INTERNATIONAL_GATEWAYS,
)
from src.skills.flights.enums import (
    CabinClass,
    FlightState,
    FlightTrackerAction,
    SortPreference,
    TripType,
)
from src.skills.flights.models import (
    AirportRecord,
    FlightSearchState,
    GatewayAirportRecord,
    NearbyHubRecord,
    TrackedFlight,
    TrackedPriceWatch,
)
from src.skills.music.catalogs import (
    COMPOUND_REGIONAL_GENRES,
    GARBAGE_MUSIC_KEYWORDS,
    GENRE_MOOD_MATRIX,
    MUSIC_TOKEN_STOPWORDS,
    SPECTRUM_CHART,
    TRUSTED_MUSIC_LABELS,
)
from src.skills.music.enums import (
    AudioAction,
    CanonicalGenre,
    IntensityLevel,
    MoodCategory,
    MusicProvider,
    SpectrumZoneId,
    TempoLevel,
)
from src.skills.music.models import (
    MoodProfile,
    PlayedSongRecord,
    RegionalGenreConfig,
    SpectrumZone,
    Track,
)
from src.skills.music.engine.spectrum import (
    clear_channel_played_songs,
    has_non_music_domain_context,
    is_valid_music_shift_context,
    parse_spectrum_shift,
    record_channel_played_song,
    step_spectrum_zone,
)
from src.skills.markets.enums import (
    AssetClass,
    HistoricalTimeframe,
    MarketRegion,
    MarketView,
)
from src.skills.markets.models import (
    BenchmarkSpec,
    CryptoAssetQuote,
    HistoricalSummary,
    TickerQuote,
)
from src.skills.graphing.enums import (
    ChartType,
    DiagramType,
)
from src.skills.graphing.models import (
    ChartChannelState,
    ChartSeries,
    ChartSpec,
    MarketBarSeries,
    RenderedVisual,
)
from src.skills.news.catalogs import NEWS_EDITIONS, NEWS_TOPICS_MAP
from src.skills.news.enums import NewsTopic
from src.skills.news.models import NewsEdition
from src.skills.sports.catalogs import (
    ESPORTS_STREAM_CHANNELS,
    SOCCER_LEAGUES,
    UNIVERSAL_SPORT_LEAGUE_REGISTRY,
)
from src.skills.sports.enums import (
    CsQueryType,
    MatchState,
    SportId,
    TrackerAction,
)
from src.skills.sports.tracker import skill_sports_tracker
from src.skills.sports.models import (
    EsportsStreamChannel,
    MatchSnapshot,
    SoccerLeagueConfig,
    TrackedMatch,
    UniversalLeagueConfig,
)
from src.skills.voice.enums import (
    SttBackend,
    TtsBackend,
    VoiceAction,
    VoiceMode,
    VoiceSessionState,
)
from src.skills.voice.models import (
    SpeakerUtterance,
    TtsAudioClip,
    VoiceProfileSpec,
    VoiceSessionRecord,
)
from src.utils.domain_vocab import (
    has_non_music_domain_signal,
    has_non_sports_track_signal,
)
from src.utils.timezones import FixedTimezoneSpec


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
SKILLS_ROOT = SRC_ROOT / "src" / "skills" if not (SRC_ROOT / "skills").exists() else SRC_ROOT / "skills"
DOMAIN_SKILLS = {"music", "sports", "flights", "news", "weather", "markets", "graphing", "voice", "jobs"}


class TestArchitectureAndBoundaries(unittest.TestCase):
    """Architectural guardrails verifying module boundaries, production code hygiene, and domain isolation."""

    def test_no_lateral_imports_between_domain_skills(self):
        """No domain skill (music, sports, flights, news, weather) may import from another sibling domain skill."""
        violations: list[str] = []

        for py_file in SKILLS_ROOT.rglob("*.py"):
            rel_parts = py_file.relative_to(SKILLS_ROOT).parts
            if not rel_parts:
                continue
            owner_skill = rel_parts[0]
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

            for node in ast.walk(tree):
                imported_modules: list[str] = []
                if isinstance(node, ast.Import):
                    imported_modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.append(node.module)

                for mod in imported_modules:
                    if not mod.startswith("src.skills."):
                        continue
                    target_parts = mod.split(".")
                    if len(target_parts) < 3:
                        continue
                    target_skill = target_parts[2]
                    if owner_skill in DOMAIN_SKILLS and target_skill in DOMAIN_SKILLS and target_skill != owner_skill:
                        violations.append(
                            f"{py_file.relative_to(REPO_ROOT)}:{getattr(node, 'lineno', '?')} imports '{mod}'"
                        )
                    elif owner_skill == "youtube" and py_file.name == "client.py" and target_skill in DOMAIN_SKILLS:
                        violations.append(
                            f"{py_file.relative_to(REPO_ROOT)}:{getattr(node, 'lineno', '?')} (shared youtube client) imports domain skill '{mod}'"
                        )

        self.assertEqual(
            violations,
            [],
            "Found forbidden cross-skill imports:\n" + "\n".join(violations),
        )

    def test_zero_mock_inspection_in_production_src(self):
        """Production code in src/ must never inspect unittest.mock attributes."""
        forbidden_tokens = ("assert_called", "_mock_name", "_is_manager_mocked", "MagicMock", "side_effect")
        violations: list[str] = []

        for py_file in SRC_ROOT.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                for token in forbidden_tokens:
                    if token in line:
                        violations.append(
                            f"{py_file.relative_to(REPO_ROOT)}:{lineno} contains forbidden token '{token}': {line.strip()}"
                        )

        self.assertEqual(
            violations,
            [],
            "Found forbidden mock-inspection tokens in src/:\n" + "\n".join(violations),
        )

    def test_centralized_worker_pool_discipline(self):
        """Only src/utils/workers.py should instantiate ThreadPoolExecutor or call .submit/.map on raw executors inside src/."""
        violations: list[str] = []
        workers_file = (SRC_ROOT / "utils" / "workers.py").resolve()

        for py_file in SRC_ROOT.rglob("*.py"):
            if py_file.resolve() == workers_file:
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                        if (
                            isinstance(node.func.value, ast.Name)
                            and node.func.value.id in ("WORKER_POOL", "SUB_WORKER_POOL", "LEAF_WORKER_POOL", "SPORTS_EXECUTOR", "_PROBE_EXECUTOR")
                            and func_name in ("submit", "map")
                        ):
                            violations.append(
                                f"{py_file.relative_to(REPO_ROOT)}:{getattr(node, 'lineno', '?')} calls {node.func.value.id}.{func_name}() directly instead of submit_to_worker/map_in_workers"
                            )
                    if func_name == "ThreadPoolExecutor":
                        violations.append(
                            f"{py_file.relative_to(REPO_ROOT)}:{getattr(node, 'lineno', '?')} instantiates ThreadPoolExecutor directly"
                        )

        self.assertEqual(
            violations,
            [],
            "Found ad-hoc executor usage outside src/utils/workers.py:\n" + "\n".join(violations),
        )

    def test_strict_separation_of_enums_models_and_catalogs(self):
        """Verify each domain skill strictly separates StrEnums (enums.py), dataclasses (models.py), and static tables (catalogs.py)."""
        for skill in ("music", "sports", "flights", "news", "weather", "youtube", "markets", "graphing", "voice", "jobs"):
            enums_mod = importlib.import_module(f"src.skills.{skill}.enums")
            models_mod = importlib.import_module(f"src.skills.{skill}.models")
            catalogs_mod = importlib.import_module(f"src.skills.{skill}.catalogs")

            # 1. enums.py: Every defined class must be a StrEnum; no dataclasses or dict/set catalogs allowed
            enum_classes = [
                obj for name, obj in vars(enums_mod).items()
                if isinstance(obj, type) and obj.__module__ == enums_mod.__name__
            ]
            self.assertTrue(enum_classes, f"src.skills.{skill}.enums must define at least one StrEnum")
            for cls in enum_classes:
                self.assertTrue(issubclass(cls, StrEnum), f"{cls.__name__} in {skill}.enums must be a StrEnum")
                self.assertFalse(is_dataclass(cls), f"{cls.__name__} in {skill}.enums must not be a dataclass")

            # 2. models.py: Every defined class must be a @dataclass(slots=True); no StrEnums or dict/set catalogs allowed
            model_classes = [
                obj for name, obj in vars(models_mod).items()
                if isinstance(obj, type) and obj.__module__ == models_mod.__name__
            ]
            self.assertTrue(model_classes, f"src.skills.{skill}.models must define at least one dataclass")
            for cls in model_classes:
                self.assertTrue(is_dataclass(cls), f"{cls.__name__} in {skill}.models must be a @dataclass")
                self.assertTrue(hasattr(cls, "__slots__"), f"{cls.__name__} in {skill}.models must declare slots=True")
                self.assertFalse(issubclass(cls, StrEnum), f"{cls.__name__} in {skill}.models must not be a StrEnum")

            for name, val in vars(models_mod).items():
                if not name.startswith("_"):
                    self.assertFalse(
                        isinstance(val, (dict, frozenset, set, list)),
                        f"Static collection '{name}' found in {skill}.models; move it to {skill}.catalogs",
                    )

            # 3. catalogs.py: Must not define new classes
            catalog_classes = [
                obj for name, obj in vars(catalogs_mod).items()
                if isinstance(obj, type) and obj.__module__ == catalogs_mod.__name__
            ]
            self.assertEqual(
                catalog_classes,
                [],
                f"src.skills.{skill}.catalogs must only hold static registries/stopwords, not class definitions: {catalog_classes}",
            )

    def test_strenum_and_slotted_dataclass_models(self):
        """Verify all refactored enums inherit from StrEnum and domain/catalog records use @dataclass(slots=True)."""
        enum_types = (
            TempoLevel,
            IntensityLevel,
            SpectrumZoneId,
            MoodCategory,
            CanonicalGenre,
            MusicProvider,
            AudioAction,
            MatchState,
            TrackerAction,
            SportId,
            CsQueryType,
            FlightState,
            FlightTrackerAction,
            CabinClass,
            TripType,
            SortPreference,
            NewsTopic,
            MarketView,
            MarketRegion,
            AssetClass,
            HistoricalTimeframe,
            ChartType,
            DiagramType,
            VoiceAction,
            VoiceMode,
            VoiceSessionState,
            SttBackend,
            TtsBackend,
        )
        for enum_cls in enum_types:
            self.assertTrue(issubclass(enum_cls, StrEnum), f"{enum_cls.__name__} must inherit from StrEnum")

        dataclass_types = (
            SpectrumZone,
            PlayedSongRecord,
            RegionalGenreConfig,
            MoodProfile,
            Track,
            MatchSnapshot,
            TrackedMatch,
            SoccerLeagueConfig,
            UniversalLeagueConfig,
            EsportsStreamChannel,
            AirportRecord,
            NearbyHubRecord,
            GatewayAirportRecord,
            FlightSearchState,
            TrackedFlight,
            TrackedPriceWatch,
            NewsEdition,
            FixedTimezoneSpec,
            BenchmarkSpec,
            TickerQuote,
            CryptoAssetQuote,
            HistoricalSummary,
            ChartSeries,
            ChartSpec,
            MarketBarSeries,
            RenderedVisual,
            ChartChannelState,
            VoiceProfileSpec,
            SpeakerUtterance,
            TtsAudioClip,
            VoiceSessionRecord,
        )
        for dc_cls in dataclass_types:
            self.assertTrue(is_dataclass(dc_cls), f"{dc_cls.__name__} must be a @dataclass")
            self.assertTrue(hasattr(dc_cls, "__slots__"), f"{dc_cls.__name__} must declare slots=True")

        # Verify catalog entries are instances of their typed dataclasses and support both attribute & tuple access
        yyc = AIRPORT_DIRECTORY["yyc"]
        self.assertIsInstance(yyc, AirportRecord)
        self.assertEqual(yyc.iata, "YYC")
        self.assertEqual(yyc[0], "YYC")
        self.assertEqual(yyc, ("YYC", "Calgary International Airport", "Calgary", "Canada"))

        hub = NEARBY_HUB_AIRPORTS["LJU"]
        self.assertIsInstance(hub, NearbyHubRecord)
        self.assertEqual((hub.iata, hub.city), ("ZAG", "Zagreb"))

        gw = ORIGIN_INTERNATIONAL_GATEWAYS["YVR"][0]
        self.assertIsInstance(gw, GatewayAirportRecord)
        self.assertEqual(gw.iata, "FRA")

        epl = SOCCER_LEAGUES["epl"]
        self.assertIsInstance(epl, SoccerLeagueConfig)
        self.assertEqual(epl.code, "eng.1")
        self.assertEqual(epl["code"], "eng.1")

        nfl = UNIVERSAL_SPORT_LEAGUE_REGISTRY["nfl"]
        self.assertIsInstance(nfl, UniversalLeagueConfig)
        self.assertEqual(nfl.sport, "football")

        ch = ESPORTS_STREAM_CHANNELS[0]
        self.assertIsInstance(ch, EsportsStreamChannel)
        self.assertEqual(ch.handle, "ESLCS")

        dr = COMPOUND_REGIONAL_GENRES["deutschrap"]
        self.assertIsInstance(dr, RegionalGenreConfig)
        self.assertEqual((dr.region, dr.genre_id), ("de", 18))

        self.assertTrue(len(SPECTRUM_CHART) == 9)
        self.assertTrue(len(GENRE_MOOD_MATRIX) > 0)
        self.assertTrue(len(GARBAGE_MUSIC_KEYWORDS) > 0)
        self.assertTrue(len(TRUSTED_MUSIC_LABELS) > 0)
        self.assertTrue(len(MUSIC_TOKEN_STOPWORDS) > 0)
        self.assertEqual(NEWS_EDITIONS["canada"], ("en-CA", "CA"))
        self.assertEqual(NEWS_TOPICS_MAP["tech"], NewsTopic.TECHNOLOGY)

    def test_shared_context_module_contract(self):
        """Verify src/context.py manages ACTIVE_CHANNEL_ID ContextVar and event loop binding."""
        prev = get_active_channel_id()
        set_active_channel_id(987654321)
        try:
            self.assertEqual(get_active_channel_id(), 987654321)
            self.assertEqual(ACTIVE_CHANNEL_ID.get(), 987654321)
        finally:
            set_active_channel_id(prev)

        bind_event_loop(None)
        self.assertIsNone(get_bound_event_loop())

    def test_cross_skill_collision_immunity_for_flight_queries(self):
        """Flight queries with comparative/tracking words must never trigger music spectrum shifts or sports game tracking."""
        channel_id = 445566
        record_channel_played_song(
            song="Harder, Better, Faster, Stronger",
            artist="Daft Punk",
            genre="electronic",
            ui_zone=SpectrumZoneId.MID_MID,
            channel_id=channel_id,
        )
        try:
            flight_queries = [
                "find a faster flight to Zagreb",
                "is there a faster route to London with 1 stop?",
                "give me a softer layover at Heathrow",
                "why is my flight AC850 delayed?",
                "track flight BA117 arrival",
                "nonstop flight from JFK to LHR in business class",
                "shorter layover on Air Canada",
            ]
            for fq in flight_queries:
                self.assertTrue(has_non_music_domain_signal(fq), f"Expected non-music domain signal for: {fq}")
                self.assertTrue(has_non_music_domain_context(fq), f"Expected non-music domain context for: {fq}")
                self.assertTrue(has_non_sports_track_signal(fq), f"Expected non-sports track signal for: {fq}")
                self.assertIsNone(
                    parse_spectrum_shift(fq),
                    f"Flight query '{fq}' must not parse as a music spectrum shift",
                )
                self.assertFalse(
                    is_valid_music_shift_context(fq, history=[]),
                    f"Flight query '{fq}' must not validate as a music shift context",
                )

            # Verify sports live tracker rejects flight tracking and points to skill_flight_tracker
            tracker_resp = skill_sports_tracker(action="start", query="track flight AC850")
            self.assertIn("skill_flight_tracker", tracker_resp)

            # Verify genuine music shift still works as expected
            music_query = "play something a little faster and harder"
            self.assertFalse(has_non_music_domain_context(music_query))
            shift_parsed = parse_spectrum_shift(music_query)
            self.assertEqual(shift_parsed[:2], (1, 1))
            self.assertTrue(is_valid_music_shift_context(music_query, history=[]))
            stepped = step_spectrum_zone(SpectrumZoneId.MID_MID, 1, 1)
            self.assertEqual(stepped["target_zone_name"], SpectrumZoneId.FAST_HARD)
        finally:
            clear_channel_played_songs(channel_id)

    def test_public_config_example_loads_cleanly_as_blank_slate(self):
        """Verify src/config.example.py is valid Python, exposes 2 clean MODES ('research' and 'chat'), and demonstrates distinct models/prompts."""
        import importlib.util

        cfg_example_path = SRC_ROOT / "config.example.py"
        self.assertTrue(cfg_example_path.exists(), "src/config.example.py must exist")
        source = cfg_example_path.read_text(encoding="utf-8")
        # Ensure blank-slate example is super concise and does not leak private prompts
        self.assertLessEqual(len(source.splitlines()), 70)

        spec = importlib.util.spec_from_file_location("src.config_example_test", cfg_example_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        self.assertIn("MODES", dir(mod))
        self.assertEqual(set(mod.MODES.keys()), {"research", "chat"})
        self.assertNotEqual(mod.MODES["research"]["model"], mod.MODES["chat"]["model"])
        for required_mode in ("research", "chat"):
            prompt_text = mod.MODES[required_mode]["get_prompt"]("User likes hockey.")
            self.assertIn("User likes hockey.", prompt_text)

    def test_standardized_skill_package_layout_and_example_skeleton(self):
        """Every skill package under src/skills/ (including .example) must strictly follow the constructor-style <skill_name>.py file structure."""
        import importlib.util
        import sys

        common_required_files = {
            "__init__.py",
            "enums.py",
            "models.py",
            "catalogs.py",
            "templates.py",
        }
        allowed_subdirs = {"aggregators", "engine", "playback"}

        skill_dirs = [
            d for d in SKILLS_ROOT.iterdir()
            if d.is_dir() and d.name != "__pycache__"
        ]
        skill_names = {d.name for d in skill_dirs}
        self.assertEqual(
            skill_names,
            {".example", "flights", "graphing", "jobs", "markets", "music", "news", "sports", "voice", "weather", "youtube"},
        )

        expected_base_aggregators = {
            ".example": "BaseExampleAggregator",
            "flights": "BaseFlightAggregator",
            "graphing": "BaseGraphingAggregator",
            "jobs": "BaseJobsAggregator",
            "markets": "BaseMarketAggregator",
            "music": "BaseMusicAggregator",
            "news": "BaseNewsAggregator",
            "sports": "BaseSportsAggregator",
            "voice": "BaseVoiceAggregator",
            "weather": "BaseWeatherAggregator",
            "youtube": "BaseYouTubeAggregator",
        }

        for sdir in skill_dirs:
            constructor_py = f"{sdir.name.lstrip('.')}.py"
            required_files = common_required_files | {constructor_py}
            allowed_top_py_files = required_files | {"context.py", "tracker.py"}

            present_py = {f.name for f in sdir.glob("*.py")}
            self.assertNotIn(
                "skill.py",
                present_py,
                f"Skill package '{sdir.name}' must use constructor-style '{constructor_py}' instead of 'skill.py'",
            )
            missing = required_files - present_py
            self.assertFalse(
                missing,
                f"Skill package '{sdir.name}' is missing required standardized files: {sorted(missing)}",
            )
            unexpected = present_py - allowed_top_py_files
            self.assertFalse(
                unexpected,
                f"Skill package '{sdir.name}' contains non-standard top-level .py files: {sorted(unexpected)}",
            )

            agg_dir = sdir / "aggregators"
            self.assertTrue(
                agg_dir.is_dir() and (agg_dir / "__init__.py").exists() and (agg_dir / "base.py").exists(),
                f"Skill package '{sdir.name}' must have an 'aggregators/' subpackage with __init__.py and base.py",
            )
            base_source = (agg_dir / "base.py").read_text(encoding="utf-8")
            expected_base_cls = expected_base_aggregators[sdir.name]
            self.assertIn(
                f"class {expected_base_cls}",
                base_source,
                f"{sdir.name}/aggregators/base.py must define {expected_base_cls}",
            )

            subdirs = {
                sub.name for sub in sdir.iterdir()
                if sub.is_dir() and sub.name != "__pycache__"
            }
            unexpected_subdirs = subdirs - allowed_subdirs
            self.assertFalse(
                unexpected_subdirs,
                f"Skill package '{sdir.name}' contains non-standard subdirectories: {sorted(unexpected_subdirs)}",
            )
            for sub in subdirs:
                self.assertTrue(
                    (sdir / sub / "__init__.py").exists(),
                    f"Subpackage '{sdir.name}/{sub}' must contain __init__.py",
                )

        # Verify src/skills/.example has context.py, tracker.py and executes cleanly
        example_dir = SKILLS_ROOT / ".example"
        self.assertFalse((example_dir / "README.md").exists(), "Do not keep a separate README.md inside src/skills/.example")
        self.assertTrue((example_dir / "context.py").exists())
        self.assertTrue((example_dir / "tracker.py").exists())

        pkg_name = "src.skills._example_skeleton_test"
        spec = importlib.util.spec_from_file_location(
            pkg_name,
            example_dir / "__init__.py",
            submodule_search_locations=[str(example_dir)],
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[pkg_name] = mod
        try:
            spec.loader.exec_module(mod)
            out_info = mod.skill_example_info(query="test item", action="lookup")
            self.assertIn("EXAMPLE SKILL DATA", out_info)
            self.assertIn("test item", out_info)
            out_track = mod.skill_example_tracker(action="list")
            self.assertIn("active", out_track.lower())
        finally:
            for k in list(sys.modules):
                if k == pkg_name or k.startswith(f"{pkg_name}."):
                    sys.modules.pop(k, None)

    def test_zero_legacy_skill_and_class_aliases_in_codebase(self):
        """Ensure zero stale legacy skill_* function names, deprecated class/singleton names, or .skill paths remain in src/, main.py, or tests/."""
        legacy_identifiers = (
            "skill_top_music",
            "skill_audio_intelligence",
            "skill_weather_reporter",
            "skill_top_headlines",
            "get_top_headlines",
            "skill_youtube_searcher",
            "skill_csgo_esports",
            "skill_betting_odds",
            "skill_track_live_game",
            "skill_track_flight",
            "skill_flights_info",
            "skill_charts_info",
            "skill_example_lookup",
            "skill_track_example",
            "BaseYouTubeSearchAggregator",
            "BaseChartAggregator",
            "YouTubeSearchManager",
            "LiveSportsTracker",
            "LIVE_TRACKER",
            "FlightTrackerManager",
            ".skill import",
            ".skill.",
        )
        this_file = Path(__file__).resolve()
        tests_root = REPO_ROOT / "tests"
        violations: list[str] = []
        files_to_scan = (
            list(SRC_ROOT.rglob("*.py"))
            + [REPO_ROOT / "main.py"]
            + [f for f in tests_root.rglob("*.py") if f.resolve() != this_file]
        )
        for py_file in files_to_scan:
            if not py_file.exists():
                continue
            source = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                for legacy_id in legacy_identifiers:
                    if legacy_id in line:
                        violations.append(
                            f"{py_file.relative_to(REPO_ROOT)}:{lineno} contains stale identifier '{legacy_id}'"
                        )

        self.assertEqual(
            violations,
            [],
            "Found stale legacy identifiers in codebase:\n" + "\n".join(violations),
        )


class TestHighSeverityCodeReviewFixes(unittest.TestCase):
    """Regression and security tests covering High-Severity Code Review findings H-1 through H-6."""

    def test_h1_nested_worker_fanout_never_deadlocks_sub_worker_pool(self):
        """H-1: Saturating SUB_WORKER_POOL (24 threads) with 30 concurrent parent tasks that each
        spawn nested child and grandchild tasks via submit_to_worker / map_in_workers must never deadlock."""
        import threading
        from src.utils.workers import map_in_workers, submit_to_worker

        barrier = threading.Event()

        def _depth3_grandchild(x: int) -> tuple[int, str]:
            return (x * 2, threading.current_thread().name)

        def _depth2_child(x: int) -> tuple[int, str, str]:
            fut = submit_to_worker(_depth3_grandchild, x)
            val, gc_thread = fut.result(timeout=5)
            return (val, threading.current_thread().name, gc_thread)

        def _depth1_parent(idx: int) -> list[tuple[int, str, str]]:
            # Wait briefly so all 24 BotSubWorker threads are simultaneously occupied by parents
            barrier.wait(timeout=0.15)
            return map_in_workers(_depth2_child, [idx, idx + 1, idx + 2], timeout=5)

        parent_futs = [submit_to_worker(_depth1_parent, i) for i in range(30)]
        barrier.set()
        results = [f.result(timeout=10) for f in parent_futs]
        self.assertEqual(len(results), 30)
        for idx, res_list in enumerate(results):
            self.assertEqual([r[0] for r in res_list], [idx * 2, (idx + 1) * 2, (idx + 2) * 2])

    def test_h2_api_secrets_redacted_from_logs_exceptions_and_cache_keys(self):
        """H-2: Telegram bot tokens and API keys in URLs/exceptions must be scrubbed by redact_secrets,
        SecretRedactingFilter, and parallel_fetch_json cache keys."""
        import logging
        from src.skills.sports.aggregators.espn_utils import _CACHE, clear_espn_cache, parallel_fetch_json
        from src.utils.http import SecretRedactingFilter, redact_secrets

        clear_espn_cache()
        tg_err = (
            "HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: "
            "https://api.telegram.org/bot987654321:AAH_SuperSecretBotToken_XYZ123456/getUpdates"
        )
        redacted_tg = redact_secrets(tg_err)
        self.assertNotIn("AAH_SuperSecretBotToken_XYZ123456", redacted_tg)
        self.assertIn("<REDACTED_TOKEN>", redacted_tg)

        odds_url = "https://api.the-odds-api.com/v4/sports/mma/odds/?apiKey=SUPER_SECRET_ODDS_KEY_999&regions=us"
        lfm_url = "https://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&api_key=SECRET_LASTFM_KEY_123&format=json"
        yt_url = "https://www.googleapis.com/youtube/v3/search?part=snippet&key=SECRET_YOUTUBE_KEY_456"
        for raw_u, secret_val in (
            (odds_url, "SUPER_SECRET_ODDS_KEY_999"),
            (lfm_url, "SECRET_LASTFM_KEY_123"),
            (yt_url, "SECRET_YOUTUBE_KEY_456"),
        ):
            scrubbed = redact_secrets(raw_u)
            self.assertNotIn(secret_val, scrubbed)
            self.assertIn("<REDACTED>", scrubbed)

        # Verify SecretRedactingFilter scrubs LogRecord messages and args
        filt = SecretRedactingFilter()
        rec = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname=__file__,
            lineno=1,
            msg="Fetch failed for %s: %s",
            args=(odds_url, RuntimeError(tg_err)),
            exc_info=None,
        )
        filt.filter(rec)
        formatted = rec.getMessage()
        self.assertNotIn("SUPER_SECRET_ODDS_KEY_999", formatted)
        self.assertNotIn("AAH_SuperSecretBotToken_XYZ123456", formatted)

        # Verify parallel_fetch_json never stores raw apiKey in _CACHE keys
        parallel_fetch_json(lambda u, timeout=6.0: [{"id": "1"}], [odds_url], ttl=60)
        for k in _CACHE.keys():
            self.assertNotIn("SUPER_SECRET_ODDS_KEY_999", k)
        clear_espn_cache()

    def test_h3_discord_allowed_mentions_and_mass_ping_sanitization(self):
        """H-3: discord.Client must disable @everyone and role mentions, and sanitize_agent_reply must neutralize mass pings."""
        import main as bot_main
        from src.orchestrator import sanitize_agent_reply

        am = bot_main.client.allowed_mentions
        self.assertIsNotNone(am)
        self.assertFalse(am.everyone)
        self.assertFalse(am.roles)
        self.assertTrue(am.users)

        raw_reply = "Hey @everyone and @here and <@&123456789>, check this out <@555666777>!"
        cleaned = sanitize_agent_reply(raw_reply)
        self.assertNotIn("@everyone", cleaned)
        self.assertNotIn("@here", cleaned)
        self.assertNotIn("<@&123456789>", cleaned)
        # Direct user mention from apply_auto_ping must remain intact
        self.assertIn("<@555666777>", cleaned)

    def test_h4_audio_preview_download_blocks_ssrf_and_enforces_size_cap(self):
        """H-4: _download_safe_audio_preview must reject private/loopback/metadata URLs, non-audio Content-Types, and oversized streams."""
        from unittest.mock import MagicMock, patch
        from src.skills.music.engine.dsp import _download_safe_audio_preview

        # 1. Private/loopback/metadata URLs blocked before network call
        with patch("src.skills.music.engine.dsp.requests.get") as mock_get:
            self.assertEqual(_download_safe_audio_preview("http://127.0.0.1:8080/audio.mp3"), b"")
            self.assertEqual(_download_safe_audio_preview("http://169.254.169.254/latest/meta-data/"), b"")
            self.assertEqual(_download_safe_audio_preview("file:///etc/passwd"), b"")
            mock_get.assert_not_called()

        # 2. Redirect to private IP blocked
        redir_resp = MagicMock(status_code=302, headers={"Location": "http://127.0.0.1/internal.mp3"})
        with (
            patch("src.skills.music.engine.dsp._is_private_or_local_host", side_effect=lambda h: h == "127.0.0.1"),
            patch("src.skills.music.engine.dsp.requests.get", return_value=redir_resp),
        ):
            self.assertEqual(_download_safe_audio_preview("https://cdnt-preview.dzcdn.net/stream/test.mp3"), b"")

        # 3. Non-audio Content-Type rejected
        html_resp = MagicMock(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b"<html>not audio</html>",
        )
        with (
            patch("src.skills.music.engine.dsp._is_private_or_local_host", return_value=False),
            patch("src.skills.music.engine.dsp.requests.get", return_value=html_resp),
        ):
            self.assertEqual(_download_safe_audio_preview("https://cdnt-preview.dzcdn.net/stream/test.mp3"), b"")

        # 4. Oversized stream (> max_bytes) aborted immediately
        big_resp = MagicMock(
            status_code=200,
            headers={"Content-Type": "audio/mpeg"},
        )
        big_resp.iter_content.return_value = [b"x" * 600, b"y" * 600]
        with (
            patch("src.skills.music.engine.dsp._is_private_or_local_host", return_value=False),
            patch("src.skills.music.engine.dsp.requests.get", return_value=big_resp),
        ):
            self.assertEqual(
                _download_safe_audio_preview("https://cdnt-preview.dzcdn.net/stream/test.mp3", max_bytes=1000),
                b"",
            )

    def test_h5_audio_feature_cache_is_bounded_and_never_retains_raw_mp3_bytes(self):
        """H-5: AUDIO_FEATURE_CACHE must be a BoundedCache and must strip _mp3_bytes before caching."""
        from unittest.mock import MagicMock, patch
        from src.skills.music.engine.dsp import (
            AUDIO_FEATURE_CACHE,
            clear_audio_feature_cache,
            get_track_acoustic_profile,
        )
        from src.utils.cache import BoundedCache

        self.assertIsInstance(AUDIO_FEATURE_CACHE, BoundedCache)
        clear_audio_feature_cache()
        fake_dz = MagicMock()
        fake_dz.resolve_track_details.return_value = {
            "id": 123,
            "title": "Strobe",
            "artist": "Deadmau5",
            "album": "For Lack of a Better Name",
            "preview": "https://cdnt-preview.dzcdn.net/stream/strobe.mp3",
            "bpm": 128.0,
            "gain": -7.5,
        }
        with (
            patch("src.skills.music.engine.dsp._download_safe_audio_preview", return_value=b"fake-mp3-bytes"),
            patch(
                "src.skills.music.engine.dsp.analyze_mp3_bytes",
                return_value={"bpm": 128.0, "speed_score": 6.5, "intensity_score": 6.0, "gain_db": -7.5, "ui_zone": "mid_mid"},
            ),
        ):
            prof = get_track_acoustic_profile("Strobe", "Deadmau5", deezer_agg=fake_dz)
            self.assertIsNotNone(prof)
            self.assertEqual(prof.get("_mp3_bytes"), b"fake-mp3-bytes")
            # Inspect cached entry in AUDIO_FEATURE_CACHE -> must NOT store raw _mp3_bytes
            self.assertEqual(len(AUDIO_FEATURE_CACHE), 1)
            for cached_entry in AUDIO_FEATURE_CACHE.values():
                self.assertNotIn("_mp3_bytes", cached_entry)
        clear_audio_feature_cache()

    def test_h6_sports_tracker_poll_once_isolates_malformed_match_payload(self):
        """H-6: If extract_snapshot_from_summary or diff_snapshots raises on one match in poll_once,
        other tracked matches must continue polling and firing alerts."""
        import asyncio
        from unittest.mock import patch
        from src.skills.sports.enums import MatchState
        from src.skills.sports.models import MatchSnapshot, TrackedMatch
        from src.skills.sports.tracker import SportsTrackerService

        tracker = SportsTrackerService(poll_interval=15)
        snap_bad = MatchSnapshot(
            state=MatchState.IN,
            status_detail="1st 10:00",
            home_team="Broken FC",
            away_team="Glitch United",
            home_score="0",
            away_score="0",
        )
        snap_good = MatchSnapshot(
            state=MatchState.IN,
            status_detail="1st 20:00",
            home_team="Arsenal",
            away_team="Chelsea",
            home_score="0",
            away_score="0",
        )
        tracker.tracked_matches[(100, "bad_1")] = TrackedMatch(
            channel_id=100,
            sport_path="soccer",
            league_code="eng.1",
            league_name="EPL",
            event_id="bad_1",
            home_team="Broken FC",
            away_team="Glitch United",
            last_snapshot=snap_bad,
        )
        tracker.tracked_matches[(100, "good_2")] = TrackedMatch(
            channel_id=100,
            sport_path="soccer",
            league_code="eng.1",
            league_name="EPL",
            event_id="good_2",
            home_team="Arsenal",
            away_team="Chelsea",
            last_snapshot=snap_good,
        )

        fired_alerts = []

        async def _on_notify(match, snap, triggers):
            fired_alerts.append((match.event_id, snap.home_score, triggers))

        tracker.set_notify_callback(_on_notify)

        def _fake_extract(payload):
            if payload.get("corrupt"):
                raise ValueError("Corrupted ESPN summary structure")
            return MatchSnapshot(
                state=MatchState.IN,
                status_detail="35'",
                home_team="Arsenal",
                away_team="Chelsea",
                home_score="1",
                away_score="0",
            )

        def _fake_fetch(url, timeout=8):
            if "bad_1" in url:
                return {"corrupt": True}
            return {"ok": True}

        with (
            patch("src.skills.sports.tracker.fetch_json", side_effect=_fake_fetch),
            patch("src.skills.sports.tracker.extract_snapshot_from_summary", side_effect=_fake_extract),
        ):
            asyncio.run(tracker.poll_once())

        # Match 2 must still fire its alert despite Match 1 raising inside extract_snapshot_from_summary
        self.assertEqual(len(fired_alerts), 1)
        self.assertEqual(fired_alerts[0][0], "good_2")
        self.assertEqual(fired_alerts[0][1], "1")
        self.assertEqual(len(tracker.tracked_matches), 2)

    def test_disabled_skills_env_filter_in_registry(self):
        """DISABLED_SKILLS should cleanly exclude specified domain skill packs or individual tools from toolsets."""
        from src.registry import (
            _ALL_CHAT_TOOLS,
            _ALL_RESEARCH_TOOLS,
            filter_toolset,
            get_disabled_tool_names,
        )

        self.assertEqual(get_disabled_tool_names(""), set())
        disabled = get_disabled_tool_names("flights, sports, music")
        self.assertEqual(
            disabled,
            {
                "skill_flight_info",
                "skill_flight_tracker",
                "skill_sports_esports",
                "skill_sports_info",
                "skill_sports_odds",
                "skill_sports_tracker",
                "skill_music_discovery",
                "skill_music_analysis",
            },
        )
        filtered_res = filter_toolset(_ALL_RESEARCH_TOOLS, "flights, sports")
        self.assertNotIn("skill_flight_info", filtered_res)
        self.assertNotIn("skill_flight_tracker", filtered_res)
        self.assertNotIn("skill_sports_info", filtered_res)
        self.assertIn("skill_markets_info", filtered_res)
        self.assertIn("skill_graphing_info", filtered_res)

        filtered_chat = filter_toolset(_ALL_CHAT_TOOLS, "voice")
        self.assertNotIn("skill_voice_info", filtered_chat)
        self.assertIn("skill_job_manager", filtered_chat)


if __name__ == "__main__":
    unittest.main()

