import json
import unittest
from unittest.mock import patch

from src.context import (
    clear_channel_attachments,
    pop_channel_attachments,
    set_active_channel_id,
)
from src.orchestrator import (
    detect_missed_graphing_tool_call,
    enrich_graphing_tool_args,
    normalize_tool_args,
)
from src.skills.graphing import (
    ChartSeries,
    ChartSpec,
    ChartType,
    DiagramType,
    MarketBarSeries,
    MatplotlibChartAggregator,
    MermaidDiagramAggregator,
    clear_channel_chart_state,
    clear_graphing_cache,
    skill_graphing_info,
)
from src.skills.graphing.aggregators.mermaid_engine import normalize_and_sanitize_mermaid


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TestGraphingSkill(unittest.TestCase):
    def setUp(self):
        self.channel_id = 777888999
        set_active_channel_id(self.channel_id)
        clear_channel_attachments(self.channel_id)
        clear_channel_chart_state(self.channel_id)
        clear_graphing_cache()

    def tearDown(self):
        clear_channel_attachments(self.channel_id)
        clear_channel_chart_state(self.channel_id)
        clear_graphing_cache()

    def test_arbitrary_bar_and_pie_charts_queue_valid_png(self):
        out_bar = skill_graphing_info(
            chart_type="bar",
            title="Quarterly Revenue ($M)",
            data="Q1: 120, Q2: 148, Q3: 175, Q4: 210",
            x_label="Quarter",
            y_label="USD Millions",
        )
        self.assertIn("VISUALIZATION RENDERED & ATTACHED", out_bar)
        self.assertIn("Quarterly Revenue ($M)", out_bar)

        attachments = pop_channel_attachments(self.channel_id)
        self.assertEqual(len(attachments), 1)
        self.assertTrue(attachments[0]["filename"].endswith(".png"))
        self.assertTrue(attachments[0]["data"].startswith(PNG_MAGIC))
        self.assertGreater(len(attachments[0]["data"]), 2000)

        out_donut = skill_graphing_info(
            chart_type="donut",
            title="Cloud Market Share",
            data=json.dumps({"labels": ["AWS", "Azure", "GCP", "Other"], "values": [31, 25, 12, 32]}),
        )
        self.assertIn("VISUALIZATION RENDERED & ATTACHED", out_donut)
        donut_att = pop_channel_attachments(self.channel_id)
        self.assertEqual(len(donut_att), 1)
        self.assertTrue(donut_att[0]["data"].startswith(PNG_MAGIC))

    def test_multi_series_json_line_and_stacked_bar_charts(self):
        payload = json.dumps(
            {
                "labels": ["Jan", "Feb", "Mar", "Apr"],
                "series": [
                    {"name": "2025", "values": [40, 48, 52, 61]},
                    {"name": "2026", "values": [55, 64, 72, 88]},
                ],
            }
        )
        out_line = skill_graphing_info(
            chart_type="line",
            title="Monthly Signups YoY",
            data=payload,
            x_label="Month",
            y_label="Signups (k)",
        )
        self.assertIn("VISUALIZATION RENDERED & ATTACHED", out_line)
        atts = pop_channel_attachments(self.channel_id)
        self.assertEqual(len(atts), 1)
        self.assertTrue(atts[0]["data"].startswith(PNG_MAGIC))

        out_stacked = skill_graphing_info(
            chart_type="stacked_bar",
            title="Stacked Monthly Signups",
            data=payload,
        )
        self.assertIn("VISUALIZATION RENDERED & ATTACHED", out_stacked)
        atts_stacked = pop_channel_attachments(self.channel_id)
        self.assertEqual(len(atts_stacked), 1)
        self.assertTrue(atts_stacked[0]["data"].startswith(PNG_MAGIC))

    def test_single_and_multi_ticker_market_charts_and_followup_state(self):
        base_ts = 1714521600
        fake_nvda = MarketBarSeries(
            symbol="NVDA",
            name="NVIDIA Corp.",
            currency="USD",
            timeframe_label="6 Months",
            timestamps=[base_ts + i * 86400 for i in range(24)],
            opens=[99.0 + i * 2.5 for i in range(24)],
            highs=[102.0 + i * 2.5 for i in range(24)],
            lows=[98.0 + i * 2.5 for i in range(24)],
            closes=[100.0 + i * 2.5 for i in range(24)],
            volumes=[40_000_000.0 + i * 500_000.0 for i in range(24)],
            pct_change=57.5,
            period_high=159.5,
            period_low=98.0,
        )
        fake_amd = MarketBarSeries(
            symbol="AMD",
            name="Advanced Micro Devices",
            currency="USD",
            timeframe_label="6 Months",
            timestamps=[base_ts + i * 86400 for i in range(24)],
            opens=[149.0 + i * 1.2 for i in range(24)],
            highs=[152.0 + i * 1.2 for i in range(24)],
            lows=[148.0 + i * 1.2 for i in range(24)],
            closes=[150.0 + i * 1.2 for i in range(24)],
            volumes=[25_000_000.0 + i * 300_000.0 for i in range(24)],
            pct_change=18.4,
            period_high=179.6,
            period_low=148.0,
        )

        with patch(
            "src.skills.graphing.graphing._market_agg.fetch_bar_series",
            return_value=fake_nvda,
        ):
            out_single = skill_graphing_info(ticker="NVDA", timeframe="6mo", query="plot NVDA over 6 months")
        self.assertIn("NVIDIA Corp. (NVDA)", out_single)
        atts_single = pop_channel_attachments(self.channel_id)
        self.assertEqual(len(atts_single), 1)
        self.assertTrue(atts_single[0]["data"].startswith(PNG_MAGIC))

        # Multi-ticker comparison chart
        with patch(
            "src.skills.graphing.graphing._market_agg.fetch_bar_series",
            side_effect=[fake_nvda, fake_amd],
        ):
            out_comp = skill_graphing_info(
                ticker="NVDA vs AMD",
                timeframe="6mo",
                query="compare NVDA vs AMD chart over 6 months",
            )
        self.assertIn("Normalized Return Comparison", out_comp)
        atts_comp = pop_channel_attachments(self.channel_id)
        self.assertEqual(len(atts_comp), 1)
        self.assertTrue(atts_comp[0]["data"].startswith(PNG_MAGIC))

    def test_mermaid_shorthand_sanitization_and_offline_fallback(self):
        code = normalize_and_sanitize_mermaid(
            "User Request (Discord) -> Orchestrator [Main Loop] -> Graphing Skill (PNG)"
        )
        self.assertIn("flowchart TD", code)
        self.assertIn('["User Request (Discord)"]', code)

        # Force both mermaid.ink and kroki.io to fail so offline Matplotlib fallback executes
        with patch.object(
            MermaidDiagramAggregator,
            "_fetch_remote_mermaid_png",
            return_value=(None, ""),
        ):
            out_diag = skill_graphing_info(
                chart_type="flowchart",
                title="Bot Request Pipeline",
                diagram="User -> Discord Gateway -> Orchestrator -> Graphing Skill -> PNG Attachment",
            )
        self.assertIn("VISUALIZATION RENDERED & ATTACHED", out_diag)
        atts = pop_channel_attachments(self.channel_id)
        self.assertEqual(len(atts), 1)
        self.assertTrue(atts[0]["data"].startswith(PNG_MAGIC))

    def test_on_demand_guardrails_never_flood_normal_or_music_queries(self):
        # 1. Normal market question without chart/plot verb must NOT trigger graphing skill
        history_market = [{"role": "user", "content": "how is NVDA doing over the last 6 months?"}]
        self.assertIsNone(detect_missed_graphing_tool_call(history_market, active_channel_id=self.channel_id))

        # 2. Music Beatport/Billboard chart request must NOT trigger graphing skill
        history_music = [{"role": "user", "content": "play top techno chart from beatport"}]
        self.assertIsNone(detect_missed_graphing_tool_call(history_music, active_channel_id=self.channel_id))

        # 3. Explicit visual plot/chart request DOES trigger graphing skill
        history_plot = [{"role": "user", "content": "plot NVDA vs AAPL over 6 months"}]
        missed = detect_missed_graphing_tool_call(history_plot, active_channel_id=self.channel_id)
        self.assertIsNotNone(missed)
        self.assertEqual(missed[0], "skill_graphing_info")
        self.assertEqual(missed[1]["timeframe"], "6mo")

        # 4. Explicit diagram request DOES trigger graphing skill
        history_diag = [{"role": "user", "content": "draw a flowchart of Client -> API -> Worker -> DB"}]
        missed_diag = detect_missed_graphing_tool_call(history_diag, active_channel_id=self.channel_id)
        self.assertIsNotNone(missed_diag)
        self.assertEqual(missed_diag[0], "skill_graphing_info")
        self.assertEqual(missed_diag[1]["chart_type"], "flowchart")

        # 5. Tool arg aliases normalize cleanly
        norm = normalize_tool_args(
            "skill_graphing_info",
            skill_graphing_info,
            {"type": "bar", "mermaid": "A -> B", "heading": "Test", "period": "1y"},
        )
        self.assertEqual(
            norm,
            {"chart_type": "bar", "diagram": "A -> B", "title": "Test", "timeframe": "1y"},
        )
        enriched = enrich_graphing_tool_args({"chart_type": "line"}, history_plot, active_channel_id=self.channel_id)
        self.assertEqual(enriched["timeframe"], "6mo")
        self.assertIn("NVDA", enriched["ticker"])

    def test_flexible_timeframes_hourly_intervals_and_stale_override(self):
        from src.skills.graphing.aggregators.market_series import _resolve_timeframe_and_interval

        # 7d and 3d must never silently fall back to 1y
        r_7d, i_7d, lbl_7d, cut_7d = _resolve_timeframe_and_interval("7d")
        self.assertEqual((r_7d, i_7d, cut_7d), ("1mo", "1h", 7))
        self.assertIn("7D", lbl_7d)

        r_3d_hr, i_3d_hr, lbl_3d_hr, cut_3d_hr = _resolve_timeframe_and_interval("3d 1h")
        self.assertEqual((r_3d_hr, i_3d_hr, cut_3d_hr), ("5d", "1h", 3))
        self.assertIn("1H Bars", lbl_3d_hr)

        r_1mo_hr, i_1mo_hr, lbl_1mo_hr, _ = _resolve_timeframe_and_interval("1mo 1h")
        self.assertEqual((r_1mo_hr, i_1mo_hr), ("1mo", "1h"))
        self.assertIn("1H Bars", lbl_1mo_hr)

        # Follow-up prompt must override stale LLM timeframe="1y" and inherit last_tickers
        from src.skills.graphing.context import update_channel_chart_state
        update_channel_chart_state(chart_type="market", tickers=["BTC-USD"], timeframe="1mo", channel_id=self.channel_id)
        hist_followup = [{"role": "user", "content": "User jubkill says: last 3 days by hr"}]
        enriched = enrich_graphing_tool_args({"timeframe": "1y"}, hist_followup, active_channel_id=self.channel_id)
        self.assertEqual(enriched["ticker"], "BTC-USD")
        self.assertEqual(enriched["timeframe"], "3d 1h")

    def test_monte_carlo_simulation_and_fan_chart(self):
        from src.skills.graphing.graphing import (
            _extract_monte_carlo_horizon_and_lookback,
            _extract_monte_carlo_paths,
        )

        base_ts = 1714521600
        fake_btc = MarketBarSeries(
            symbol="BTC-USD",
            name="Bitcoin USD",
            currency="USD",
            timeframe_label="5-Year (5Y) [1D Bars]",
            timestamps=[base_ts + i * 86400 for i in range(120)],
            opens=[60000.0 + i * 180.0 for i in range(120)],
            highs=[61000.0 + i * 180.0 for i in range(120)],
            lows=[59000.0 + i * 180.0 for i in range(120)],
            closes=[60000.0 + i * 180.0 + ((-1) ** i) * 450.0 for i in range(120)],
            volumes=[25_000_000_000.0 for _ in range(120)],
            pct_change=35.0,
            period_high=82000.0,
            period_low=59000.0,
        )
        # 1. Default 5-year lookback when user only specifies forward horizon
        hist_mc = [{"role": "user", "content": "can you do Monte Carlo simulation on btc 2 years into future?"}]
        missed_mc = detect_missed_graphing_tool_call(hist_mc, active_channel_id=self.channel_id)
        self.assertIsNotNone(missed_mc)
        self.assertEqual(missed_mc[0], "skill_graphing_info")
        self.assertEqual(missed_mc[1]["chart_type"], "monte_carlo")
        self.assertEqual(missed_mc[1]["timeframe"], "2y")

        with patch.object(
            type(skill_graphing_info.__globals__["_market_agg"]),
            "fetch_bar_series",
            return_value=fake_btc,
        ) as mock_fetch:
            out_mc = skill_graphing_info(**missed_mc[1])
            mock_fetch.assert_called_once_with("BTC-USD", timeframe="5y", interval="1d")
        self.assertIn("Monte Carlo GBM Simulation", out_mc)
        self.assertIn("5-Year History (Default)", out_mc)
        self.assertIn("Terminal Price Percentiles", out_mc)
        self.assertIn("Probability Metrics", out_mc)
        atts = pop_channel_attachments(self.channel_id)
        self.assertEqual(len(atts), 1)
        self.assertTrue(atts[0]["filename"].startswith("monte_carlo_btc-usd"))
        self.assertTrue(atts[0]["data"].startswith(PNG_MAGIC))

        self.assertIn("| Basis: 5-Year History (Default)", atts[0]["caption"])

        # 2. Flexible custom lookback extraction (horizon vs lookback separation)
        self.assertEqual(
            _extract_monte_carlo_horizon_and_lookback(
                "can you do Monte Carlo simulation on btc 2 years into future based on the last 10 years?",
                "10y",
            ),
            ("2y", "10y"),
        )
        self.assertEqual(
            _extract_monte_carlo_horizon_and_lookback(
                "Monte Carlo simulation on BTC 1 year into future base off 3 years",
                "3y",
            ),
            ("1y", "3y"),
        )
        self.assertEqual(
            _extract_monte_carlo_horizon_and_lookback(
                "Monte Carlo simulation on NVDA using all-time history",
                "max",
            ),
            ("1y", "max"),
        )
        self.assertEqual(
            _extract_monte_carlo_horizon_and_lookback(
                "use past 10 years and do 5 years in future",
                "10y",
                prev_horizon="2y",
            ),
            ("5y", "10y"),
        )
        self.assertEqual(
            _extract_monte_carlo_horizon_and_lookback(
                "use past 15 years and do it again",
                "15y",
                prev_horizon="2y",
            ),
            ("2y", "15y"),
        )

        # 3. Follow-up lookback adjustment on active Monte Carlo chart preserves horizon="2y" and sets lookback="10y"
        hist_mc_followup = [{"role": "user", "content": "what if we base it on 10 years of history?"}]
        missed_followup = detect_missed_graphing_tool_call(hist_mc_followup, active_channel_id=self.channel_id)
        self.assertIsNotNone(missed_followup)
        self.assertEqual(missed_followup[1]["chart_type"], "monte_carlo")
        self.assertEqual(missed_followup[1]["ticker"], "BTC-USD")
        with patch.object(
            type(skill_graphing_info.__globals__["_market_agg"]),
            "fetch_bar_series",
            return_value=fake_btc,
        ) as mock_fetch_custom:
            out_mc_custom = skill_graphing_info(**missed_followup[1])
            mock_fetch_custom.assert_called_once_with("BTC-USD", timeframe="10y", interval="1d")
        self.assertIn("10-Year (10Y) History", out_mc_custom)
        self.assertNotIn("5-Year History (Default)", out_mc_custom)
        pop_channel_attachments(self.channel_id)

        # 4. On-demand custom path counts (default 10,000; up to 100,000)
        self.assertEqual(_extract_monte_carlo_paths("can you do Monte Carlo simulation on btc 2 years into future?"), 10000)
        self.assertEqual(_extract_monte_carlo_paths("Monte Carlo simulation on BTC with 50k paths"), 50000)
        self.assertEqual(_extract_monte_carlo_paths("Run 100,000 simulations on NVDA for 2 years"), 100000)
        self.assertEqual(_extract_monte_carlo_paths("do it again with 25k"), 25000)

    def test_dxy_charting_overlay_and_short_timeframe_intervals(self):
        from src.skills.graphing.aggregators.market_series import _resolve_timeframe_and_interval
        from src.skills.graphing.graphing import _extract_market_tickers

        # 1. Bare timeframes '1d', '5d', '1mo', '10y' must use their spec intervals ('5m', '15m', '1d', '1wk')
        self.assertEqual(_resolve_timeframe_and_interval("1d")[:2], ("1d", "5m"))
        self.assertEqual(_resolve_timeframe_and_interval("5d")[:2], ("5d", "15m"))
        self.assertEqual(_resolve_timeframe_and_interval("1mo")[:2], ("1mo", "1d"))
        self.assertEqual(_resolve_timeframe_and_interval("10y")[:2], ("10y", "1wk"))

        # 2. Natural DXY chart prompts trigger turn-0 recovery and follow-up 'graph it' recovers from prior history
        clear_channel_chart_state(self.channel_id)
        h_dxy = [{"role": "user", "content": "give last 9 years of dxy chart"}]
        m_dxy = detect_missed_graphing_tool_call(h_dxy, active_channel_id=self.channel_id)
        self.assertIsNotNone(m_dxy)
        self.assertEqual(m_dxy[1]["ticker"], "DX-Y.NYB")
        self.assertEqual(m_dxy[1]["timeframe"], "9y")

        h_followup = h_dxy + [
            {"role": "assistant", "content": "Here is the DXY summary."},
            {"role": "user", "content": "graph it"},
        ]
        m_follow = detect_missed_graphing_tool_call(h_followup, active_channel_id=self.channel_id)
        self.assertIsNotNone(m_follow)
        self.assertEqual(m_follow[1]["ticker"], "DX-Y.NYB")
        self.assertEqual(m_follow[1]["timeframe"], "9y")

        # 3. Multi-ticker overlay preserves both BTC-USD and DX-Y.NYB even if LLM only passes ticker='BTC-USD'
        h_overlay = [{"role": "user", "content": "overlay btc and dxy in a chart last 9 years of history for each"}]
        enriched_overlay = enrich_graphing_tool_args(
            {"ticker": "BTC-USD", "timeframe": "9y"},
            h_overlay,
            active_channel_id=self.channel_id,
        )
        self.assertEqual(enriched_overlay["ticker"], "BTC-USD, DX-Y.NYB")
        self.assertEqual(
            _extract_market_tickers("overlay btc and dxy in a chart last 9 years of history for each", "BTC-USD"),
            ["BTC-USD", "dxy"],
        )

        # 4. Any currency vs. BTC ('USD-BTC', 'CAD-BTC', 'CHF-BTC', 'SATS-CAD') resolves without duplicate 'BTC-USD'
        from src.skills.graphing.catalogs import extract_fx_pairs_from_text, resolve_fx_pair_symbol
        self.assertEqual(resolve_fx_pair_symbol("CAD/BTC"), "CAD-BTC")
        self.assertEqual(resolve_fx_pair_symbol("Swiss franc to Bitcoin"), "CHF-BTC")
        self.assertEqual(resolve_fx_pair_symbol("SATS/CAD"), "SATS-CAD")
        self.assertEqual(
            extract_fx_pairs_from_text("price history for $1 on how much btc that can buy"),
            ["USD-BTC"],
        )
        self.assertEqual(
            _extract_market_tickers("chart CAD/BTC over 5 years", "BTC-USD"),
            ["CAD-BTC"],
        )

        # 5. Multi-fiat BTC comparison ("BTC/USD, BTC/EUR, and BTC/CAD") preserves all 3 pairs in order
        q_btc_multi = "create and display comparison charts showing the 24hr performance for BTC/USD, BTC/EUR, and BTC/CAD"
        self.assertEqual(
            _extract_market_tickers(q_btc_multi, ""),
            ["BTC-USD", "BTC-EUR", "BTC-CAD"],
        )
        enriched_multi = enrich_graphing_tool_args(
            {"ticker": "BTC/USD"},
            [{"role": "user", "content": q_btc_multi}],
            active_channel_id=self.channel_id,
        )
        self.assertEqual(enriched_multi["ticker"], "BTC-USD, BTC-EUR, BTC-CAD")
        self.assertEqual(enriched_multi["timeframe"], "24h")
        # When sibling graphing calls are running in parallel, keep each call's single ticker distinct
        enriched_sibling = enrich_graphing_tool_args(
            {"ticker": "BTC/EUR"},
            [{"role": "user", "content": q_btc_multi}],
            active_channel_id=self.channel_id,
            has_sibling_graph_calls=True,
        )
        self.assertEqual(enriched_sibling["ticker"], "BTC-EUR")


if __name__ == "__main__":
    unittest.main()

