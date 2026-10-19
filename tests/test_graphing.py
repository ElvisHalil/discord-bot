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


if __name__ == "__main__":
    unittest.main()
