from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from src.skills.markets import (
    clear_channel_market_state,
    clear_markets_cache,
    detect_macro_calendar_tool_call,
    detect_missed_markets_tool_call,
    enrich_markets_tool_args,
    get_channel_market_state,
    get_daily_market_pulse,
    is_macro_calendar_query,
    skill_markets_info,
    update_channel_market_state,
)
from src.skills.markets.enums import MarketRegion, MarketView
from src.skills.markets.markets import (
    _compute_macro_regime_signals,
    _crypto_agg,
    _extract_candidate_ticker_query,
    _extract_comparison_tickers,
    _extract_timeframe,
    _macro_agg,
    _yahoo_agg,
)
from src.skills.markets.models import CryptoAssetQuote, HistoricalSummary, MacroCalendarEvent, TickerQuote
from src.utils.domain_vocab import TOOL_ARG_ALIASES
from src.utils.timezones import enrich_tool_output_with_time_context



def _make_quote(
    symbol: str,
    name: str,
    price: float,
    change_pct: float,
    currency: str = "USD",
    exchange: str = "NMS",
    **kwargs,
) -> TickerQuote:
    change = round(price * (change_pct / 100.0), 2)
    defaults = {
        "symbol": symbol,
        "name": name,
        "currency": currency,
        "exchange": exchange,
        "market_state": "REGULAR",
        "price": price,
        "change": change,
        "change_percent": change_pct,
        "day_high": round(price * 1.015, 2),
        "day_low": round(price * 0.985, 2),
        "week52_high": round(price * 1.25, 2),
        "week52_low": round(price * 0.75, 2),
        "ma50": round(price * 0.95, 2),
        "ma200": round(price * 0.88, 2),
        "volume": 15_000_000,
        "avg_volume_3m": 10_000_000,
        "market_cap": 150_000_000_000,
        "trailing_pe": 28.4,
        "forward_pe": 22.1,
        "dividend_yield": 1.45,
        "earnings_timestamp": 1762992000,
    }
    defaults.update(kwargs)
    return TickerQuote(**defaults)


class TestMarketsSkill(unittest.TestCase):
    def setUp(self):
        clear_markets_cache()

    def tearDown(self):
        clear_markets_cache()

    def test_canadian_dual_listed_company_defaults_to_cad_tsx(self):
        """Canadian companies (Shopify, RBC, TD, Enbridge) must resolve to TSX (.TO) CAD primary + US secondary."""
        with patch("src.skills.markets.aggregators.yahoo._get_session_and_crumb", side_effect=RuntimeError("offline")):
            primary, dual, _ = _yahoo_agg.resolve_ticker_and_news("Shopify", prefer_canada=True)
            self.assertEqual(primary, "SHOP.TO")
            self.assertEqual(dual, "SHOP")

            primary_ry, dual_ry, _ = _yahoo_agg.resolve_ticker_and_news("royal bank", prefer_canada=True)
            self.assertEqual(primary_ry, "RY.TO")
            self.assertEqual(dual_ry, "RY")

            primary_enb, dual_enb, _ = _yahoo_agg.resolve_ticker_and_news("Enbridge", prefer_canada=True)
            self.assertEqual(primary_enb, "ENB.TO")
            self.assertEqual(dual_enb, "ENB")

    def test_single_ticker_snapshot_canadian_dual_listed_renders_cad_and_usd(self):
        """Verify skill_markets_info renders SHOP.TO (CAD) as primary and SHOP (USD) alongside it with technicals & news."""
        shop_to = _make_quote(
            "SHOP.TO",
            "Shopify Inc.",
            price=148.50,
            change_pct=3.45,
            currency="CAD",
            exchange="TOR",
        )
        shop_us = _make_quote(
            "SHOP",
            "Shopify Inc.",
            price=107.60,
            change_pct=3.41,
            currency="USD",
            exchange="NYQ",
        )
        fake_news = ["**Shopify GMV Surges Past Expectations in Q3** — *BNN Bloomberg* (https://example.com/shopify-q3)"]
        fake_hist = HistoricalSummary(
            symbol="SHOP.TO",
            currency="CAD",
            timeframe_label="6-Month (6M)",
            start_date="Apr 18, 2026",
            end_date="Oct 18, 2026",
            start_price=112.00,
            end_price=148.50,
            abs_change=36.50,
            pct_change=32.59,
            period_high=155.20,
            period_high_date="Sep 24, 2026",
            period_low=108.40,
            period_low_date="May 02, 2026",
            max_drawdown_pct=-9.45,
            annualized_volatility_pct=34.2,
            horizon_returns={"1W": 4.2, "1M": 9.8, "3M": 18.5, "6M": 32.59, "YTD": 27.4, "1Y": 54.1, "5Y": 88.0},
            checkpoints=["Apr 18: **112.00**", "Jul 18: **131.40**", "Oct 18: **148.50**"],
        )

        with (
            patch(
                "src.skills.markets.markets._yahoo_agg.resolve_ticker_and_news",
                return_value=("SHOP.TO", "SHOP", fake_news),
            ),
            patch(
                "src.skills.markets.markets._yahoo_agg.fetch_quotes",
                return_value={"SHOP.TO": shop_to, "SHOP": shop_us},
            ),
            patch(
                "src.skills.markets.markets._yahoo_agg.fetch_historical_summary",
                return_value=fake_hist,
            ),
        ):
            output = skill_markets_info(ticker="Shopify", timeframe="6m")

        self.assertIn("TICKER INTELLIGENCE SNAPSHOT: Shopify Inc. (SHOP.TO)", output)
        self.assertIn("148.50 CAD", output)
        self.assertIn("Dual-Listed Quote (`SHOP` — NYQ):", output)
        self.assertIn("107.60 USD", output)
        self.assertIn("Multi-Horizon Returns:", output)
        self.assertIn("**6M:** +32.59%", output)
        self.assertIn("HISTORICAL PERFORMANCE & TREND: 6-Month (6M)", output)
        self.assertIn("112.00 CAD → 148.50 CAD", output)
        self.assertIn("Max Drawdown:", output)
        self.assertIn("Chronological Price Trajectory:", output)
        self.assertIn("52-Week Range:", output)
        self.assertIn("Moving Averages & Trend:", output)
        self.assertIn("Bullish (Price > 50d & 200d MA)", output)
        self.assertIn("1.50x", output)
        self.assertIn("Shopify GMV Surges Past Expectations in Q3", output)

    def test_macro_regime_signals_detects_safe_haven_and_regional_divergence(self):
        """Verify _compute_macro_regime_signals flags VIX spikes, safe-haven rotation, and TSX outperformance."""
        quotes = {
            "^GSPC": _make_quote("^GSPC", "S&P 500", 5750.0, -1.45),
            "^IXIC": _make_quote("^IXIC", "Nasdaq Composite", 18100.0, -1.95),
            "^GSPTSE": _make_quote("^GSPTSE", "S&P/TSX Composite", 24600.0, 0.35, currency="CAD"),
            "^VIX": _make_quote("^VIX", "Cboe Volatility Index", 27.4, 14.2),
            "^TNX": _make_quote("^TNX", "US 10Y Treasury Yield", 4.42, 2.3),
            "GC=F": _make_quote("GC=F", "Gold Futures", 2745.0, 1.35),
            "SI=F": _make_quote("SI=F", "Silver Futures", 33.90, 2.10),
            "CL=F": _make_quote("CL=F", "WTI Crude Oil", 76.80, 3.10),
            "CADUSD=X": _make_quote("CADUSD=X", "CAD/USD", 0.7245, 0.12),
        }
        crypto = [
            CryptoAssetQuote(symbol="BTC", name="Bitcoin", price_usd=68500.0, price_cad=94500.0, change_24h_pct=2.4)
        ]
        signals = _compute_macro_regime_signals(quotes, crypto)
        joined = "\n".join(signals)
        self.assertIn("Risk-Off / Elevated Hedging", joined)
        self.assertIn("Regional Divergence", joined)
        self.assertIn("Safe-Haven Bid", joined)
        self.assertIn("WTI Crude Oil", joined)
        self.assertIn("Bitcoin (BTC)", joined)

    def test_global_overview_and_daily_market_pulse(self):
        """Verify overview and get_daily_market_pulse return structured global indices, commodities, movers, and crypto."""
        fake_quotes = {
            "^GSPTSE": _make_quote("^GSPTSE", "S&P/TSX Composite", 24800.0, 0.62, currency="CAD"),
            "CADUSD=X": _make_quote("CADUSD=X", "CAD/USD", 0.7245, 0.15),
            "USDCAD=X": _make_quote("USDCAD=X", "USD/CAD", 1.3802, -0.15, currency="CAD"),
            "^GSPC": _make_quote("^GSPC", "S&P 500", 5860.0, 0.45),
            "^IXIC": _make_quote("^IXIC", "Nasdaq Composite", 18520.0, 0.78),
            "^DJI": _make_quote("^DJI", "Dow Jones", 43250.0, 0.25),
            "^STOXX50E": _make_quote("^STOXX50E", "Euro Stoxx 50", 4985.0, 0.31, currency="EUR"),
            "^FTSE": _make_quote("^FTSE", "FTSE 100", 8350.0, 0.19, currency="GBP"),
            "^GDAXI": _make_quote("^GDAXI", "DAX", 19620.0, 0.44, currency="EUR"),
            "^N225": _make_quote("^N225", "Nikkei 225", 38950.0, -0.22, currency="JPY"),
            "^HSI": _make_quote("^HSI", "Hang Seng", 20800.0, 1.15, currency="HKD"),
            "GC=F": _make_quote("GC=F", "Gold Futures", 2730.0, 0.85),
            "SI=F": _make_quote("SI=F", "Silver Futures", 33.80, 1.90),
            "CL=F": _make_quote("CL=F", "WTI Crude Oil", 70.40, -1.10),
            "BZ=F": _make_quote("BZ=F", "Brent Crude Oil", 74.20, -0.95),
            "^VIX": _make_quote("^VIX", "VIX", 18.1, -3.5),
            "^TNX": _make_quote("^TNX", "10Y Yield", 4.08, -0.5),
        }
        fake_crypto = [
            CryptoAssetQuote(
                symbol="BTC",
                name="Bitcoin",
                price_usd=68450.0,
                price_cad=94475.0,
                change_24h_pct=2.35,
                market_cap_usd=1_350_000_000_000,
                volume_24h_usd=34_000_000_000,
            )
        ]
        fake_tsx_movers = {
            "gainers": [_make_quote("SHOP.TO", "Shopify Inc.", 148.50, 3.45, currency="CAD")],
            "losers": [_make_quote("BCE.TO", "BCE Inc.", 44.10, -1.80, currency="CAD")],
        }
        fake_us_movers = {
            "gainers": [_make_quote("NVDA", "NVIDIA Corp.", 138.20, 4.10)],
            "losers": [_make_quote("INTC", "Intel Corp.", 22.15, -2.50)],
            "actives": [_make_quote("NVDA", "NVIDIA Corp.", 138.20, 4.10)],
        }

        with (
            patch("src.skills.markets.markets._yahoo_agg.fetch_quotes", return_value=fake_quotes),
            patch("src.skills.markets.markets._yahoo_agg.fetch_tsx_movers", return_value=fake_tsx_movers),
            patch("src.skills.markets.markets._yahoo_agg.fetch_us_movers", return_value=fake_us_movers),
            patch(
                "src.skills.markets.markets._yahoo_agg.fetch_macro_market_headlines",
                return_value=["**Global Stocks Climb as Yields Ease** — *Reuters*"],
            ),
            patch("src.skills.markets.markets._crypto_agg.fetch_top_coins", return_value=fake_crypto),
        ):
            overview = skill_markets_info(query="how are global markets doing today", view="overview")
            self.assertIn("GLOBAL MARKETS, COMMODITIES & CRYPTO INTELLIGENCE", overview)
            self.assertIn("S&P/TSX Composite", overview)
            self.assertIn("S&P 500", overview)
            self.assertIn("Gold", overview)
            self.assertIn("Bitcoin (`BTC`)", overview)
            self.assertIn("S&P/TSX 60 Top Gainers", overview)
            self.assertIn("US Market Top Gainers", overview)

            pulse = get_daily_market_pulse()
            self.assertIn("DAILY GLOBAL MARKET PULSE & MACRO SIGNALS", pulse)
            self.assertIn("S&P/TSX Composite", pulse)
            self.assertIn("S&P 500", pulse)
            self.assertIn("Gold", pulse)
            self.assertIn("Bitcoin (BTC)", pulse)

    def test_candidate_ticker_extraction_and_tool_arg_aliases(self):
        """Verify _extract_candidate_ticker_query distinguishes broad market queries from single-ticker lookups."""
        self.assertIsNone(_extract_candidate_ticker_query("how are global markets doing today", ""))
        self.assertIsNone(_extract_candidate_ticker_query("what stocks are going up and down today", ""))
        self.assertEqual(_extract_candidate_ticker_query("how is shopify stock doing today", ""), "shopify")
        self.assertEqual(_extract_candidate_ticker_query("", "NVDA"), "NVDA")

        aliases = TOOL_ARG_ALIASES["skill_markets_info"]
        self.assertEqual(aliases["symbol"], "ticker")
        self.assertEqual(aliases["country"], "region")
        self.assertEqual(aliases["mode"], "view")
        self.assertEqual(aliases["period"], "timeframe")

    def test_timeframe_extraction_and_historical_summary_computation(self):
        """Verify _extract_timeframe and fetch_historical_summary compute returns, drawdowns, and checkpoints."""
        self.assertEqual(_extract_timeframe("how has gold performed over the last 6 months", ""), "6mo")
        self.assertEqual(_extract_timeframe("Shopify 5 year history", ""), "5y")
        self.assertEqual(_extract_timeframe("TSX YTD performance", ""), "ytd")
        self.assertEqual(_extract_timeframe("how did NVDA do in 2024", ""), "2024")
        self.assertEqual(_extract_timeframe("", "3m"), "3mo")

        # Build synthetic 400-day daily series
        base_ts = 1750000000 - (400 * 86400)
        synthetic_bars = []
        for d in range(400):
            ts = base_ts + d * 86400
            price = 100.0 + (d * 0.25)  # climbs from 100.00 to 199.75
            synthetic_bars.append((ts, price - 0.5, price + 1.0, price - 1.0, price))

        with patch.object(_yahoo_agg, "_fetch_chart_bars", return_value=("CAD", synthetic_bars)):
            hist = _yahoo_agg.fetch_historical_summary("SHOP.TO", timeframe="6mo")

        self.assertIsNotNone(hist)
        self.assertEqual(hist.symbol, "SHOP.TO")
        self.assertEqual(hist.currency, "CAD")
        self.assertEqual(hist.timeframe_label, "6-Month (6M)")
        self.assertGreater(hist.pct_change, 20.0)
        self.assertIn("1W", hist.horizon_returns)
        self.assertIn("1M", hist.horizon_returns)
        self.assertIn("6M", hist.horizon_returns)
        self.assertIn("1Y", hist.horizon_returns)
        self.assertEqual(len(hist.checkpoints), 6)

    def test_multi_ticker_comparison_extraction_and_rendering(self):
        """Verify multi-ticker comparisons ('Compare Shopify vs NVDA over 5 years') rank winners and render side-by-side."""
        self.assertEqual(
            _extract_comparison_tickers("Compare Shopify vs NVDA over 5 years", ""),
            ["shopify", "nvda"],
        )
        self.assertEqual(
            _extract_comparison_tickers("", "SHOP.TO, NVDA, AAPL"),
            ["shop.to", "nvda", "aapl"],
        )
        # Ensure broad non-comparison queries with 'and' do not falsely hijack overview into comparison mode
        self.assertEqual(
            _extract_comparison_tickers("how are bitcoin and ethereum and crypto markets doing today", ""),
            [],
        )

        shop_q = _make_quote("SHOP.TO", "Shopify Inc.", 148.50, 2.10, currency="CAD", exchange="TOR", beta=1.85, target_mean_price=168.0)
        nvda_q = _make_quote("NVDA", "NVIDIA Corp.", 138.20, 4.10, currency="USD", exchange="NMS", beta=1.65, target_mean_price=160.0)
        shop_hist = HistoricalSummary(
            symbol="SHOP.TO",
            currency="CAD",
            timeframe_label="5-Year (5Y)",
            start_date="Oct 18, 2021",
            end_date="Oct 18, 2026",
            start_price=80.0,
            end_price=148.50,
            abs_change=68.50,
            pct_change=85.62,
            cagr_pct=13.17,
            period_high=175.0,
            period_high_date="Nov 19, 2021",
            period_low=35.0,
            period_low_date="Oct 14, 2022",
            max_drawdown_pct=-62.4,
            annualized_volatility_pct=46.1,
            horizon_returns={"1Y": 54.1, "5Y": 85.62},
        )
        nvda_hist = HistoricalSummary(
            symbol="NVDA",
            currency="USD",
            timeframe_label="5-Year (5Y)",
            start_date="Oct 18, 2021",
            end_date="Oct 18, 2026",
            start_price=22.0,
            end_price=138.20,
            abs_change=116.20,
            pct_change=528.18,
            cagr_pct=44.42,
            total_return_pct=530.10,
            total_return_cagr_pct=44.50,
            period_high=142.0,
            period_high_date="Oct 15, 2026",
            period_low=11.2,
            period_low_date="Oct 14, 2022",
            max_drawdown_pct=-66.1,
            annualized_volatility_pct=51.3,
            horizon_returns={"1Y": 168.4, "5Y": 528.18},
        )

        def fake_bundle(target, prefer_ca, tf, prefer_crypto=False):
            if "shop" in target.lower():
                return (shop_q, None, shop_hist, ["Shopify expands enterprise merchants"])
            return (nvda_q, None, nvda_hist, ["NVIDIA Blackwell demand surges"])

        with patch("src.skills.markets.markets._fetch_single_asset_bundle", side_effect=fake_bundle):
            out = skill_markets_info(query="Compare Shopify vs NVDA over 5 years")

        self.assertIn("MULTI-ASSET HEAD-TO-HEAD COMPARISON", out)
        self.assertIn("**Relative Outperformer (5-Year (5Y)):** **NVIDIA Corp. (`NVDA`)** at **+528.18%**", out)
        self.assertIn("outperforming **Shopify Inc. (`SHOP.TO`)** (+85.62%) by **+442.56%**", out)
        self.assertIn("**Total Return (w/ Divs):** +530.10%", out)

    def test_custom_date_range_single_date_and_intraday_timeframes(self):
        """Verify intraday ('1d'), single historical date ('on Oct 15, 2025'), custom range, and standalone year extraction."""
        self.assertEqual(_extract_timeframe("AAPL intraday chart", ""), "1d")
        self.assertEqual(_extract_timeframe("what did AAPL close at on Oct 15, 2025", ""), "date:2025-10-15")
        self.assertEqual(
            _extract_timeframe("NVDA performance from March 2025 to October 2025", ""),
            "range:2025-03-01:2025-10-31",
        )
        self.assertEqual(_extract_timeframe("NVDA 2024 return", ""), "2024")
        self.assertEqual(
            _extract_candidate_ticker_query("Palantir five years", "", has_timeframe=True),
            "palantir",
        )
        self.assertEqual(
            _extract_candidate_ticker_query("CrowdStrike five years", "", has_timeframe=True),
            "CrowdStrike",
        )

        # Build synthetic daily series across 2025
        start_2025 = int(datetime(2024, 12, 20, tzinfo=timezone.utc).timestamp())
        bars = []
        for d in range(340):
            ts = start_2025 + d * 86400
            p = 100.0 + d * 0.5
            bars.append((ts, p - 1.0, p + 2.0, p - 2.0, p))

        with patch.object(_yahoo_agg, "_fetch_chart_bars", return_value=("USD", bars)):
            # 1. Single-date lookup
            hist_date = _yahoo_agg.fetch_historical_summary("AAPL", timeframe="date:2025-06-15")
            self.assertIsNotNone(hist_date)
            self.assertIsNotNone(hist_date.exact_date_bar)
            self.assertEqual(hist_date.exact_date_bar["requested_date"], "2025-06-15")

            # 2. Custom range lookup
            hist_range = _yahoo_agg.fetch_historical_summary("AAPL", timeframe="range:2025-03-01:2025-08-01")
            self.assertIsNotNone(hist_range)
            self.assertIn("Custom Window (2025-03-01 → 2025-08-01)", hist_range.timeframe_label)

            # 3. Calendar year with prior-year Dec 31 baseline
            hist_yr = _yahoo_agg.fetch_historical_summary("AAPL", timeframe="2025")
            self.assertEqual(hist_yr.timeframe_label, "Calendar Year 2025")
            self.assertIn("2024", hist_yr.start_date)

            # 4. Pre-IPO / empty calendar year graceful fallback
            hist_empty_yr = _yahoo_agg.fetch_historical_summary("AAPL", timeframe="2019")
            self.assertIn("Available History (No data in 2019)", hist_empty_yr.timeframe_label)

    def test_channel_context_followup_and_missed_tool_detector(self):
        """Verify per-channel market state resolves follow-up timeframe/comparison queries and turn-0 missed tool calls."""
        clear_channel_market_state()
        update_channel_market_state(tickers=["SHOP.TO"], timeframe="1y", view="ticker")

        enriched = enrich_markets_tool_args({"query": "what about over 5 years?"}, [
            {"role": "user", "content": "what about over 5 years?"}
        ])
        self.assertEqual(enriched.get("query"), "what about over 5 years?")
        self.assertEqual(enriched.get("ticker"), "SHOP.TO")

        # Follow-up comparison when LLM passed only the new ticker
        enriched_cmp = enrich_markets_tool_args({"query": "compare it with NVDA", "ticker": "NVDA"}, [
            {"role": "user", "content": "compare it with NVDA"}
        ])
        self.assertEqual(enriched_cmp.get("ticker"), "SHOP.TO, NVDA")
        self.assertEqual(enriched_cmp.get("view"), "compare")

        # Switching to a new asset in the same channel must NOT inherit the previous ticker
        enriched_new_asset = enrich_markets_tool_args(
            {"query": "how has gold performed over the last 6 months?"},
            [{"role": "user", "content": "how has gold performed over the last 6 months?"}],
        )
        self.assertIsNone(enriched_new_asset.get("ticker"))

        # Follow-up missed tool call detection when channel has active market state
        missed_followup = detect_missed_markets_tool_call([
            {"role": "user", "content": "how did it perform in 2024?"}
        ])
        self.assertIsNotNone(missed_followup)
        self.assertEqual(missed_followup[0], "skill_markets_info")
        self.assertEqual(missed_followup[1].get("ticker"), "SHOP.TO")

        # Cashtag missed tool call detection
        missed_cashtag = detect_missed_markets_tool_call([
            {"role": "user", "content": "check $NVDA and $AMD today"}
        ])
        self.assertIsNotNone(missed_cashtag)
        self.assertEqual(missed_cashtag[0], "skill_markets_info")
        self.assertEqual(missed_cashtag[1].get("ticker"), "NVDA, AMD")

        # Broad market query containing 'economy' or 'business' must NOT be blocked by flight cabin words
        missed_econ = detect_missed_markets_tool_call([
            {"role": "user", "content": "how is the stock market and economy doing today?"}
        ])
        self.assertIsNotNone(missed_econ)
        self.assertEqual(missed_econ[0], "skill_markets_info")

        # Ensure sports/flight queries are never intercepted by detect_missed_markets_tool_call
        self.assertIsNone(detect_missed_markets_tool_call([
            {"role": "user", "content": "track the Arsenal match live"}
        ]))
        self.assertIsNone(detect_missed_markets_tool_call([
            {"role": "user", "content": "cheapest flights from YYC to LHR"}
        ]))

    def test_region_and_view_filtering_in_skill_markets_info(self):
        """Verify `view='commodities'` and `region='europe'` focus the output sections."""
        fake_quotes = {
            "^STOXX50E": _make_quote("^STOXX50E", "Euro Stoxx 50", 4985.0, 0.31, currency="EUR"),
            "^GSPTSE": _make_quote("^GSPTSE", "S&P/TSX Composite", 24800.0, 0.62, currency="CAD"),
            "USDCAD=X": _make_quote("USDCAD=X", "USD/CAD", 1.3802, -0.15, currency="CAD"),
            "GC=F": _make_quote("GC=F", "Gold Futures", 2730.0, 0.85),
        }
        with (
            patch("src.skills.markets.markets._yahoo_agg.fetch_quotes", return_value=fake_quotes),
            patch("src.skills.markets.markets._yahoo_agg.fetch_us_movers", return_value={"gainers": [], "losers": [], "actives": []}),
            patch("src.skills.markets.markets._yahoo_agg.fetch_macro_market_headlines", return_value=[]),
            patch("src.skills.markets.markets._crypto_agg.fetch_top_coins", return_value=[]),
        ):
            comm_out = skill_markets_info(query="gold and commodities", view="commodities")
            self.assertIn("PRECIOUS METALS & COMMODITIES", comm_out)
            self.assertIn("CAD Spot Proxy:", comm_out)
            self.assertNotIn("EUROPEAN MARKETS", comm_out)
            self.assertNotIn("TODAY'S MARKET MOVERS", comm_out)

            eu_out = skill_markets_info(query="european markets", region="europe", view="overview")
            self.assertIn("EUROPEAN MARKETS", eu_out)
            self.assertNotIn("ASIAN & PACIFIC MARKETS", eu_out)

    def test_yahoo_v7_to_v8_chart_fallback_preserves_all_symbols_and_technicals(self):
        """When Yahoo v7/finance/quote fails (e.g. 429/500), v8/finance/chart fallback must recover all symbols and reconstruct MAs/volume."""
        class _DummyResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _DummySession:
            def get(self, url: str, timeout: int = 5):
                if "/v7/finance/quote" in url:
                    return _DummyResp(429, {})
                if "/v8/finance/chart/" in url:
                    # Extract symbol from URL path
                    sym = url.split("/v8/finance/chart/")[1].split("?")[0].replace("%5E", "^").replace("%3D", "=")
                    closes = [100.0 + i * 0.5 for i in range(220)]  # ends at 209.5, prev_close = 209.0
                    vols = [1_000_000 + (i * 1000) for i in range(220)]
                    return _DummyResp(
                        200,
                        {
                            "chart": {
                                "result": [
                                    {
                                        "meta": {
                                            "symbol": sym,
                                            "longName": f"{sym} Asset",
                                            "fullExchangeName": "TOR" if sym.endswith(".TO") else "NMS",
                                            "currency": "CAD" if sym.endswith(".TO") else "USD",
                                            "regularMarketPrice": 210.0,
                                            "regularMarketDayLow": 208.0,
                                            "regularMarketDayHigh": 211.5,
                                            "fiftyTwoWeekLow": 95.0,
                                            "fiftyTwoWeekHigh": 215.0,
                                            "regularMarketVolume": 1_500_000,
                                        },
                                        "indicators": {
                                            "quote": [{"close": closes, "volume": vols}]
                                        },
                                    }
                                ]
                            }
                        },
                    )
                return _DummyResp(404, {})

        symbols = [f"SYM{i}" for i in range(12)] + ["SHOP.TO"]
        with patch(
            "src.skills.markets.aggregators.yahoo._get_session_and_crumb",
            return_value=(_DummySession(), "testcrumb"),
        ):
            fetched = _yahoo_agg.fetch_quotes(symbols)

        # Verify zero symbols were dropped (even >8 symbols) and all technicals were reconstructed
        self.assertEqual(len(fetched), 13)
        shop_q = fetched["SHOP.TO"]
        self.assertEqual(shop_q.currency, "CAD")
        self.assertEqual(shop_q.price, 210.0)
        # 1-day change vs closes[-2] (209.0) -> +1.0 (+0.48%)
        self.assertAlmostEqual(shop_q.change, 1.0, places=2)
        self.assertIsNotNone(shop_q.ma50)
        self.assertIsNotNone(shop_q.ma200)
        self.assertIsNotNone(shop_q.avg_volume_3m)
        self.assertEqual(shop_q.week52_low, 95.0)
        self.assertEqual(shop_q.week52_high, 215.0)

    def test_yahoo_crumb_401_auto_refresh_recovery(self):
        """When Yahoo v7 returns 401/403 due to an expired crumb, fetch_quotes refreshes the crumb and succeeds on retry."""
        calls = []

        class _DummyResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class _DummySession:
            def get(self, url: str, timeout: int = 6):
                calls.append(url)
                if len(calls) == 1:
                    return _DummyResp(401, {})
                return _DummyResp(
                    200,
                    {
                        "quoteResponse": {
                            "result": [
                                {
                                    "symbol": "GC=F",
                                    "longName": "Gold Futures",
                                    "currency": "USD",
                                    "regularMarketPrice": 4195.0,
                                    "regularMarketChange": 15.0,
                                    "regularMarketChangePercent": 0.36,
                                }
                            ]
                        }
                    },
                )

        sess = _DummySession()
        with patch(
            "src.skills.markets.aggregators.yahoo._get_session_and_crumb",
            side_effect=[(sess, "stale_crumb"), (sess, "fresh_crumb")],
        ):
            res = _yahoo_agg.fetch_quotes(["GC=F"])

        self.assertEqual(len(calls), 2)
        self.assertIn("GC=F", res)
        self.assertEqual(res["GC=F"].price, 4195.0)

    def test_coingecko_rate_limit_fallback_to_yahoo_preserves_crypto_and_movers(self):
        """When CoinGecko returns 429 or times out, CryptoAggregator falls back to Yahoo Finance with full USD/CAD & movers."""
        class _DummyResp429:
            status_code = 429

        fake_yf_quotes = {
            "BTC-USD": _make_quote("BTC-USD", "Bitcoin", 82500.0, 1.85),
            "ETH-USD": _make_quote("ETH-USD", "Ethereum", 2420.0, 0.95),
            "SOL-USD": _make_quote("SOL-USD", "Solana", 105.0, 3.40),
            "XRP-USD": _make_quote("XRP-USD", "XRP", 1.31, -1.20),
            "DOGE-USD": _make_quote("DOGE-USD", "Dogecoin", 0.085, -2.80),
            "ZEC-USD": _make_quote("ZEC-USD", "Zcash", 1165.0, 4.50),
        }

        with (
            patch("src.skills.markets.aggregators.crypto.creq.get", return_value=_DummyResp429()),
            patch("src.skills.markets.aggregators.yahoo.YahooFinanceAggregator.fetch_quotes", return_value=fake_yf_quotes),
        ):
            coins = _crypto_agg.fetch_top_coins(usdcad_rate=1.40)

        self.assertGreaterEqual(len(coins), 6)
        btc = next(c for c in coins if c.symbol == "BTC")
        self.assertEqual(btc.price_usd, 82500.0)
        self.assertEqual(btc.price_cad, round(82500.0 * 1.40, 2))
        self.assertIsNotNone(btc.market_cap_usd)
        self.assertIsNotNone(btc.volume_24h_usd)

        movers = _crypto_agg.get_crypto_movers(coins, count=2)
        self.assertEqual(movers["gainers"][0].symbol, "ZEC")
        self.assertEqual(movers["losers"][0].symbol, "DOGE")

    def test_crypto_qualifier_extraction_and_sub_cent_snapshot(self):
        """Verify queries like 'conflux crypto', 'no crypto rxd', and 'raditan (RDX)' resolve to crypto pairs with sub-cent precision and CAD dual quote."""
        self.assertEqual(_extract_candidate_ticker_query("conflux crypto", ""), "conflux")
        self.assertEqual(_extract_candidate_ticker_query("no crypto rxd", ""), "rxd")
        self.assertEqual(_extract_candidate_ticker_query("no it's a crpyro currency raditan (RDX)", ""), "rdx")

        rxd_usd = TickerQuote(
            symbol="RXD-USD",
            name="Radiant USD",
            exchange="CCC",
            currency="USD",
            market_state="REGULAR",
            price=0.00002968,
            change=0.00000071,
            change_percent=2.45,
            day_low=0.00002850,
            day_high=0.00003020,
            week52_low=0.00001800,
            week52_high=0.00009500,
            ma50=0.00002710,
            ma200=0.00003420,
            volume=185000,
            avg_volume_3m=140000,
        )
        usdcad = _make_quote("USDCAD=X", "USD/CAD", 1.40, 0.10)

        with (
            patch(
                "src.skills.markets.markets._yahoo_agg.resolve_ticker_and_news",
                return_value=("RXD-USD", "RXD-CAD", ["Radiant network hash rate update [CoinDesk] (<https://example.com/rxd>)"]),
            ) as mock_resolve,
            patch(
                "src.skills.markets.markets._yahoo_agg.fetch_quotes",
                return_value={"RXD-USD": rxd_usd, "USDCAD=X": usdcad},
            ),
            patch(
                "src.skills.markets.markets._yahoo_agg.fetch_historical_summary",
                return_value=None,
            ),
        ):
            out = skill_markets_info(query="no crypto rxd")

        mock_resolve.assert_called_once_with("rxd", prefer_canada=True, prefer_crypto=True)
        self.assertIn("Radiant USD (RXD-USD)", out)
        self.assertIn("0.00002968 USD", out)
        self.assertIn("Dual-Listed Quote (`RXD-CAD`", out)
        self.assertIn("0.00004155 CAD", out)
        self.assertIn("50-Day MA: **0.00002710**", out)

    def test_macro_calendar_intent_and_followup_routing(self):
        """Verify central bank rate decision prompts and follow-ups route cleanly to skill_markets_info(view='calendar')."""
        self.assertTrue(is_macro_calendar_query("did bank of Canada raise rates today?"))
        self.assertTrue(is_macro_calendar_query("what time is federal reserve rate decision"))
        self.assertTrue(is_macro_calendar_query("when is the next FOMC meeting?"))
        self.assertTrue(is_macro_calendar_query("did the ECB cut interest rates?"))
        self.assertTrue(is_macro_calendar_query("when is the next US CPI report?"))
        # Negative cases: casual speech, sports, flights, graphing
        self.assertFalse(is_macro_calendar_query("I am fed up with this weather"))
        self.assertFalse(is_macro_calendar_query("when is the next Oilers game?"))
        self.assertFalse(is_macro_calendar_query("flights from YYC to LHR"))

        # Turn-0 missed tool call detection on direct prompts
        res_boc = detect_missed_markets_tool_call(
            [{"role": "user", "content": "did bank of Canada raise rates today?"}],
            active_channel_id=777,
        )
        self.assertEqual(res_boc, ("skill_markets_info", {"query": "did bank of Canada raise rates today?", "view": "calendar"}))

        res_fed = detect_missed_markets_tool_call(
            [{"role": "user", "content": "what time is federal reserve rate decision"}],
            active_channel_id=777,
        )
        self.assertEqual(res_fed, ("skill_markets_info", {"query": "what time is federal reserve rate decision", "view": "calendar"}))

        # Follow-up prompts in an active central bank conversation ("is it today??", "my local time what time", "convert it to your local time")
        followup_hist = [
            {"role": "user", "content": "what time is federal reserve rate decision"},
            {"role": "assistant", "content": "The Federal Reserve FOMC interest rate decision is released at 2:00 PM Eastern Time."},
            {"role": "user", "content": "is it today??"},
        ]
        res_today = detect_macro_calendar_tool_call(followup_hist, active_channel_id=777)
        self.assertIsNotNone(res_today)
        self.assertEqual(res_today[0], "skill_markets_info")
        self.assertEqual(res_today[1]["view"], "calendar")
        self.assertIn("Federal Reserve FOMC", res_today[1]["query"])

        local_time_hist = followup_hist + [
            {"role": "assistant", "content": "The FOMC meeting is today at 2:00 PM Eastern Time."},
            {"role": "user", "content": "my local time what time"},
        ]
        res_local = detect_macro_calendar_tool_call(local_time_hist, active_channel_id=777)
        self.assertIsNotNone(res_local)
        self.assertEqual(res_local[0], "skill_markets_info")
        self.assertEqual(res_local[1]["view"], "calendar")

        convert_hist = local_time_hist + [
            {"role": "assistant", "content": "Here is the current time."},
            {"role": "user", "content": "convert it to your local time the meeting time today"},
        ]
        res_conv = detect_macro_calendar_tool_call(convert_hist, active_channel_id=777)
        self.assertIsNotNone(res_conv)
        self.assertEqual(res_conv[0], "skill_markets_info")
        self.assertEqual(res_conv[1]["view"], "calendar")

        # enrich_markets_tool_args automatically sets view='calendar' on macro queries
        enriched = enrich_markets_tool_args(
            {"query": "what time is federal reserve rate decision"},
            [{"role": "user", "content": "what time is federal reserve rate decision"}],
            active_channel_id=777,
        )
        self.assertEqual(enriched["view"], "calendar")

    def test_macro_calendar_aggregator_and_skill_execution(self):
        """Verify MacroCalendarAggregator merges TradingView + ForexFactory + Official Fed/BoC schedules with Local MDT time first."""
        ref_now = datetime(2026, 10, 28, 15, 36, tzinfo=timezone.utc)  # 9:36 AM MDT on Wed Oct 28, 2026
        tv_mock = [
            MacroCalendarEvent(
                title="BoC Interest Rate Decision",
                country="CA",
                timestamp_utc=datetime(2026, 10, 28, 13, 45, tzinfo=timezone.utc),  # 7:45 AM MDT / 9:45 AM EDT
                importance=1,
                actual="2.50%",
                forecast="2.50%",
                previous="2.25%",
                unit="%",
                category="rate_decision",
                source="TradingView",
            ),
            MacroCalendarEvent(
                title="Fed Interest Rate Decision",
                country="US",
                timestamp_utc=datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc),  # 12:00 PM MDT / 2:00 PM EDT
                importance=1,
                actual=None,
                forecast="4.00%",
                previous="4.00%",
                unit="%",
                category="rate_decision",
                source="TradingView",
            ),
            MacroCalendarEvent(
                title="Fed Press Conference",
                country="US",
                timestamp_utc=datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc),  # 12:30 PM MDT / 2:30 PM EDT
                importance=1,
                category="central_bank",
                source="TradingView",
            ),
        ]
        boc_hist_mock = [
            {"date": "October 28, 2026", "target": "2.50%", "change": "+0.25%"},
            {"date": "September 2, 2026", "target": "2.25%", "change": "---"},
        ]
        with (
            patch.object(_macro_agg, "fetch_tradingview_events", return_value=tv_mock),
            patch.object(_macro_agg, "fetch_forexfactory_week_events", return_value=[]),
            patch.object(
                _macro_agg,
                "fetch_official_boc_data",
                return_value=(boc_hist_mock, _macro_agg.fetch_official_boc_data(2026)[1]),
            ),
            patch.object(_macro_agg, "fetch_official_fomc_schedule", wraps=_macro_agg.fetch_official_fomc_schedule),
        ):
            report = _macro_agg.format_macro_calendar_report(
                query="what time is federal reserve rate decision",
                now=ref_now,
                tz_override="America/Edmonton",
                live_rate_quotes={"^TNX": _make_quote("^TNX", "10Y Yield", 4.28, 1.2)},
            )

        self.assertIn("TODAY'S SCHEDULED DECISIONS & RELEASES (Wednesday, Oct 28, 2026)", report)
        self.assertIn("CENTRAL BANK RATE DECISION DAY TODAY", report)
        # Verify Bank of Canada shows 7:45 AM MDT (Local), +25 bps rate hike, and RELEASED TODAY
        self.assertIn("BoC Interest Rate Decision (Canada)", report)
        self.assertIn("TODAY, Wed Oct 28, 2026 at 7:45 AM MDT (Local)", report)
        self.assertIn("9:45 AM EDT / 13:45 UTC", report)
        self.assertIn("Actual: 2.50%", report)
        self.assertIn("+25 bps rate hike", report)
        self.assertIn("✅ RELEASED TODAY", report)
        # Verify Federal Reserve shows 12:00 PM MDT (Local), 2:00 PM EDT / 18:00 UTC, and UPCOMING TODAY
        self.assertIn("Fed Interest Rate Decision (United States)", report)
        self.assertIn("TODAY, Wed Oct 28, 2026 at 12:00 PM MDT (Local)", report)
        self.assertIn("2:00 PM EDT / 18:00 UTC", report)
        self.assertIn("⏳ UPCOMING TODAY", report)
        self.assertIn("TODAY, Wed Oct 28, 2026 at 12:30 PM MDT (Local)", report)

        # Verify end-to-end skill_markets_info routing and channel state update
        with (
            patch.object(_macro_agg, "format_macro_calendar_report", return_value=report) as mock_fmt,
            patch.object(_yahoo_agg, "fetch_quotes", return_value={}),
            patch.object(_yahoo_agg, "fetch_macro_market_headlines", return_value=["Fed set to announce October rate decision [Reuters]"]),
        ):
            skill_out = skill_markets_info(query="what time is federal reserve rate decision")

        mock_fmt.assert_called_once()
        self.assertIn("TODAY, Wed Oct 28, 2026 at 12:00 PM MDT (Local)", skill_out)
        self.assertIn("ALWAYS Lead with Local Time (`MDT`/`MST`) First", skill_out)
        st = get_channel_market_state()
        self.assertIsNotNone(st)
        self.assertEqual(st.last_view, MarketView.CALENDAR)

    def test_spelled_out_eastern_time_and_dotted_ampm_timezone_enrichment(self):
        """Verify '2:00 PM Eastern Time', '2:00 p.m. ET', and '2 p.m. Eastern Daylight Time' are inline-converted to Local MDT."""
        ref_now = datetime(2026, 10, 28, 15, 37, tzinfo=timezone.utc)  # 9:37 AM MDT on Wed Oct 28, 2026
        raw_snippet = (
            "=== SEARCH RESULTS FOR 'Federal Reserve rate decision' ===\n"
            "1. Decisions are generally released around 2:00 PM Eastern Time following the FOMC meeting.\n"
            "2. Chair Powell's press conference begins at 2:30 p.m. ET.\n"
            "3. Bank of Canada announced its rate hike at 9:45 a.m. Eastern Daylight Time.\n"
            "4. Live coverage starts at 2 p.m. Eastern Time."
        )
        enriched = enrich_tool_output_with_time_context(
            raw_snippet,
            now=ref_now,
            tz_override="America/Edmonton",
        )
        self.assertIn("🕒 Current Time:", enriched)
        self.assertIn("2:00 PM Eastern Time (Today 12:00 PM MDT", enriched)
        self.assertIn("2:30 p.m. ET (Today 12:30 PM MDT", enriched)
        self.assertIn("9:45 a.m. Eastern Daylight Time (Today 7:45 AM MDT", enriched)
        self.assertIn("2 p.m. Eastern Time (Today 12:00 PM MDT", enriched)

    def test_federal_reserve_youtube_live_feed_does_not_route_to_macro_calendar(self):
        """Asking to 'post the federal reserve live Youtube feed' must NOT trigger Central Bank Rate
        Decisions (`skill_markets_info(view='calendar')`) and must route to `skill_youtube_search`."""
        from src.skills.youtube import (
            clean_youtube_video_search_query,
            detect_missed_youtube_tool_call,
        )
        from src.skills.youtube.aggregators.ddg import (
            has_video_query_relevance,
            is_trusted_or_requested_video_channel,
        )

        prompt = "post the federal reserve live Youtube feed"
        history = [{"role": "user", "content": f"User jubkill says: {prompt}"}]

        # 1. Must not be classified as a macro calendar query even if view='calendar' was proposed
        self.assertFalse(is_macro_calendar_query(prompt))
        self.assertFalse(is_macro_calendar_query(prompt, view="calendar"))
        self.assertIsNone(detect_macro_calendar_tool_call(history))
        self.assertIsNone(detect_missed_markets_tool_call(history))

        # 2. Must route to skill_youtube_search(query='federal reserve live', is_music=False)
        self.assertEqual(clean_youtube_video_search_query(prompt), "federal reserve live")
        yt_call = detect_missed_youtube_tool_call(history)
        self.assertEqual(
            yt_call,
            ("skill_youtube_search", {"query": "federal reserve live", "is_music": False}),
        )

        # 3. Official Federal Reserve YouTube channel upload ('FOMC Press Conference, October 28, 2026')
        # must pass video relevance and official channel checks even when 'live' is not in the video title
        self.assertTrue(
            is_trusted_or_requested_video_channel("federal reserve live", "Federal Reserve")
        )
        self.assertTrue(
            has_video_query_relevance(
                "federal reserve live",
                "FOMC Press Conference, October 28, 2026",
                "Federal Reserve",
            )
        )


if __name__ == "__main__":
    unittest.main()

