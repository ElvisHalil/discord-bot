"""
Thread-safe Matplotlib dark-mode rendering engine (`src/skills/graphing/aggregators/matplotlib_engine.py`).

Uses Matplotlib's object-oriented `Figure` + `FigureCanvasAgg` directly (bypassing global `pyplot` state)
so concurrent worker threads in `SUB_WORKER_POOL` can render charts in parallel with zero race conditions.
"""

from __future__ import annotations

from datetime import datetime, timezone
import io
import math
import re
from typing import List, Optional, Tuple

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker

from src.skills.graphing.aggregators.base import BaseGraphingAggregator
from src.skills.graphing.catalogs import (
    BEARISH_RED,
    BULLISH_GREEN,
    DARK_BG_COLOR,
    DARK_GRID_COLOR,
    DARK_MUTED_TEXT_COLOR,
    DARK_PANEL_COLOR,
    DARK_TEXT_COLOR,
    DARK_THEME_PALETTE,
)
from src.skills.graphing.enums import ChartType
from src.skills.graphing.models import ChartSpec, MarketBarSeries, MonteCarloResult, RenderedVisual


def _slugify_filename(title: str, default: str = "chart") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", (title or "").strip()).strip("_").lower()
    return f"{slug[:48] or default}.png"


def _fmt_price_val(val: float) -> str:
    av = abs(val)
    if 0 < av < 0.01:
        return f"{val:,.8f}".rstrip("0").rstrip(".")
    if 0 < av < 1.0:
        return f"{val:,.4f}"
    return f"{val:,.2f}"


def _style_dark_axes(ax, title: str = "", subtitle: str = "", x_label: str = "", y_label: str = "") -> None:
    ax.set_facecolor(DARK_PANEL_COLOR)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("bottom", "left"):
        ax.spines[spine].set_color(DARK_GRID_COLOR)
        ax.spines[spine].set_linewidth(0.9)
    ax.tick_params(colors=DARK_TEXT_COLOR, labelsize=9.5)
    ax.grid(True, linestyle="--", linewidth=0.6, color=DARK_GRID_COLOR, alpha=0.65)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(
            title,
            color=DARK_TEXT_COLOR,
            fontsize=13,
            fontweight="bold",
            pad=18 if subtitle else 12,
            loc="left",
        )
    if subtitle:
        ax.text(
            0.0,
            1.02,
            subtitle,
            transform=ax.transAxes,
            color=DARK_MUTED_TEXT_COLOR,
            fontsize=9.5,
            va="bottom",
            ha="left",
        )
    if x_label:
        ax.set_xlabel(x_label, color=DARK_MUTED_TEXT_COLOR, fontsize=10, labelpad=8)
    if y_label:
        ax.set_ylabel(y_label, color=DARK_MUTED_TEXT_COLOR, fontsize=10, labelpad=8)


def _figure_to_png_bytes(fig: Figure, dpi: int = 140) -> bytes:
    canvas = FigureCanvasAgg(fig)
    buf = io.BytesIO()
    canvas.print_png(buf)
    return buf.getvalue()


class MatplotlibChartAggregator(BaseGraphingAggregator):
    """Renders universal 2D data charts, single/multi-ticker financial charts, and fallback flowcharts."""

    name = "matplotlib"

    def render_data_chart(self, spec: ChartSpec) -> RenderedVisual:
        """Render a line, area, bar, horizontal_bar, stacked_bar, scatter, pie, or donut chart to PNG bytes."""
        ctype = (spec.chart_type or ChartType.BAR).lower().strip()
        fig = Figure(figsize=(10.0, 5.8), dpi=140, facecolor=DARK_BG_COLOR)
        ax = fig.add_subplot(111)

        summary_lines: List[str] = []
        series_list = spec.series or []
        labels = list(spec.labels or [])

        if ctype in (ChartType.PIE, ChartType.DONUT):
            ax.set_facecolor(DARK_BG_COLOR)
            s0 = series_list[0] if series_list else None
            vals = [max(0.0, float(v)) for v in (s0.values if s0 else [])]
            if not labels or len(labels) < len(vals):
                labels = labels + [f"Item {i + 1}" for i in range(len(labels), len(vals))]
            colors = [DARK_THEME_PALETTE[i % len(DARK_THEME_PALETTE)] for i in range(len(vals))]
            wedge_props = {"edgecolor": DARK_BG_COLOR, "linewidth": 1.8}
            if ctype == ChartType.DONUT:
                wedge_props["width"] = 0.45
            wedges, texts, autotexts = ax.pie(
                vals,
                labels=labels[: len(vals)],
                colors=colors,
                autopct=lambda pct: f"{pct:.1f}%" if pct >= 3.0 else "",
                startangle=140,
                pctdistance=0.76 if ctype == ChartType.DONUT else 0.65,
                wedgeprops=wedge_props,
                textprops={"color": DARK_TEXT_COLOR, "fontsize": 10},
            )
            for at in autotexts:
                at.set_color(DARK_BG_COLOR if ctype == ChartType.PIE else DARK_TEXT_COLOR)
                at.set_fontweight("bold")
                at.set_fontsize(9)
            ax.set_title(spec.title or "Distribution", color=DARK_TEXT_COLOR, fontsize=13.5, fontweight="bold", pad=14)
            total = sum(vals) or 1.0
            top_items = sorted(zip(labels[: len(vals)], vals), key=lambda x: x[1], reverse=True)[:4]
            summary_lines.append(
                "**Top Segments:** "
                + ", ".join(f"{lbl} ({val:,.2f} | {(val / total) * 100:.1f}%)" for lbl, val in top_items)
            )
        else:
            _style_dark_axes(
                ax,
                title=spec.title or "Data Visualization",
                subtitle=spec.subtitle,
                x_label=spec.x_label,
                y_label=spec.y_label,
            )
            max_len = max((len(s.values) for s in series_list), default=0)
            if not labels and max_len > 0:
                labels = [str(i + 1) for i in range(max_len)]
            elif len(labels) < max_len:
                labels = labels + [str(i + 1) for i in range(len(labels), max_len)]

            x_idx = list(range(max_len))
            n_series = max(1, len(series_list))

            if ctype == ChartType.HORIZONTAL_BAR:
                bar_h = 0.72 / n_series
                for idx, s in enumerate(series_list):
                    color = s.color or DARK_THEME_PALETTE[idx % len(DARK_THEME_PALETTE)]
                    offsets = [y + (idx - (n_series - 1) / 2.0) * bar_h for y in range(len(s.values))]
                    bars = ax.barh(offsets, s.values, height=bar_h * 0.92, color=color, label=s.name, alpha=0.92)
                    if len(s.values) <= 12:
                        for b in bars:
                            w = b.get_width()
                            ax.text(
                                w,
                                b.get_y() + b.get_height() / 2.0,
                                f" {spec.value_prefix}{w:,.2f}{spec.value_suffix}",
                                va="center",
                                ha="left",
                                color=DARK_TEXT_COLOR,
                                fontsize=8.5,
                            )
                ax.set_yticks(range(len(labels)))
                ax.set_yticklabels(labels)
                ax.invert_yaxis()
            elif ctype in (ChartType.BAR, ChartType.STACKED_BAR):
                bar_w = 0.72 if ctype == ChartType.STACKED_BAR else (0.76 / n_series)
                bottoms = [0.0] * max_len
                for idx, s in enumerate(series_list):
                    color = s.color or DARK_THEME_PALETTE[idx % len(DARK_THEME_PALETTE)]
                    vals = list(s.values) + [0.0] * (max_len - len(s.values))
                    if ctype == ChartType.STACKED_BAR:
                        ax.bar(x_idx, vals, width=bar_w, bottom=bottoms, color=color, label=s.name, alpha=0.92)
                        bottoms = [b + v for b, v in zip(bottoms, vals)]
                    else:
                        offsets = [x + (idx - (n_series - 1) / 2.0) * bar_w for x in x_idx]
                        bars = ax.bar(offsets, vals, width=bar_w * 0.92, color=color, label=s.name, alpha=0.92)
                        if max_len <= 10 and n_series <= 2:
                            for b in bars:
                                h = b.get_height()
                                ax.annotate(
                                    f"{spec.value_prefix}{h:,.2f}{spec.value_suffix}".rstrip("0").rstrip(".")
                                    if h % 1 == 0
                                    else f"{spec.value_prefix}{h:,.2f}{spec.value_suffix}",
                                    xy=(b.get_x() + b.get_width() / 2.0, h),
                                    xytext=(0, 4),
                                    textcoords="offset points",
                                    ha="center",
                                    va="bottom",
                                    color=DARK_TEXT_COLOR,
                                    fontsize=8.5,
                                )
                ax.set_xticks(x_idx)
                ax.set_xticklabels(labels[:max_len], rotation=25 if max_len > 7 else 0, ha="right" if max_len > 7 else "center")
            elif ctype == ChartType.SCATTER:
                for idx, s in enumerate(series_list):
                    color = s.color or DARK_THEME_PALETTE[idx % len(DARK_THEME_PALETTE)]
                    xs = s.x_values if (s.x_values and len(s.x_values) == len(s.values)) else list(range(len(s.values)))
                    ax.scatter(xs, s.values, color=color, label=s.name, s=58, alpha=0.9, edgecolors=DARK_BG_COLOR, linewidth=0.8)
                if labels and not any(s.x_values for s in series_list):
                    ax.set_xticks(x_idx)
                    ax.set_xticklabels(labels[:max_len], rotation=25 if max_len > 7 else 0)
            else:
                # LINE or AREA
                for idx, s in enumerate(series_list):
                    color = s.color or DARK_THEME_PALETTE[idx % len(DARK_THEME_PALETTE)]
                    xs = list(range(len(s.values)))
                    marker = "o" if len(s.values) <= 24 else None
                    ax.plot(xs, s.values, color=color, label=s.name, linewidth=2.3, marker=marker, markersize=4.5)
                    if ctype == ChartType.AREA or len(series_list) == 1:
                        ax.fill_between(xs, s.values, min(0.0, min(s.values)) if min(s.values) < 0 else min(s.values) * 0.96, color=color, alpha=0.16)
                if max_len > 0:
                    step = max(1, math.ceil(max_len / 10))
                    tick_positions = list(range(0, max_len, step))
                    if (max_len - 1) not in tick_positions:
                        tick_positions.append(max_len - 1)
                    ax.set_xticks(tick_positions)
                    ax.set_xticklabels([labels[i] for i in tick_positions], rotation=20 if len(tick_positions) > 6 else 0)

            if len(series_list) > 1 or (series_list and series_list[0].name and series_list[0].name != "Series 1"):
                leg = ax.legend(facecolor=DARK_PANEL_COLOR, edgecolor=DARK_GRID_COLOR, fontsize=9.5, loc="best")
                for txt in leg.get_texts():
                    txt.set_color(DARK_TEXT_COLOR)

            for s in series_list[:4]:
                if s.values:
                    summary_lines.append(
                        f"**{s.name}:** Start `{s.values[0]:,.2f}` → End `{s.values[-1]:,.2f}` "
                        f"(Min `{min(s.values):,.2f}`, Max `{max(s.values):,.2f}`)"
                    )

        fig.tight_layout(pad=1.4)
        png_bytes = _figure_to_png_bytes(fig)
        return RenderedVisual(
            filename=_slugify_filename(spec.title, default=f"{ctype}_chart"),
            png_bytes=png_bytes,
            title=spec.title or "Data Visualization",
            summary_lines=summary_lines,
            renderer_used="matplotlib",
        )

    def render_single_market_chart(self, bar_series: MarketBarSeries) -> RenderedVisual:
        """Render a dark-mode price + moving-averages (+ volume when available) chart for a single market ticker."""
        closes = bar_series.closes
        timestamps = bar_series.timestamps
        volumes = bar_series.volumes
        n = len(closes)
        has_volume = any(v > 0 for v in volumes)

        fig = Figure(figsize=(10.5, 6.4 if has_volume else 5.8), dpi=140, facecolor=DARK_BG_COLOR)
        if has_volume:
            gs = fig.add_gridspec(2, 1, height_ratios=[3.2, 1.0], hspace=0.08)
            ax_price = fig.add_subplot(gs[0])
            ax_vol = fig.add_subplot(gs[1], sharex=ax_price)
        else:
            ax_price = fig.add_subplot(111)
            ax_vol = None

        is_up = closes[-1] >= closes[0] if n >= 2 else True
        main_color = BULLISH_GREEN if is_up else BEARISH_RED
        sign = "+" if bar_series.pct_change > 0 else ""

        start_dt = datetime.fromtimestamp(timestamps[0], tz=timezone.utc).strftime("%b %d, %Y") if timestamps else ""
        end_dt = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc).strftime("%b %d, %Y") if timestamps else ""
        title_str = f"{bar_series.name} ({bar_series.symbol}) — {bar_series.timeframe_label}"
        subtitle_str = (
            f"Latest: {_fmt_price_val(closes[-1])} {bar_series.currency} ({sign}{bar_series.pct_change:.2f}%)  |  "
            f"Window: {start_dt} → {end_dt}  |  High: {_fmt_price_val(bar_series.period_high)} / Low: {_fmt_price_val(bar_series.period_low)}"
        )

        _style_dark_axes(ax_price, title=title_str, subtitle=subtitle_str, y_label=f"Price ({bar_series.currency})")
        if ax_vol is not None:
            _style_dark_axes(ax_vol, y_label="Volume")
            ax_price.tick_params(labelbottom=False)

        xs = list(range(n))
        ax_price.plot(xs, closes, color=main_color, linewidth=2.1, label=f"{bar_series.symbol} Close")
        y_floor = min(closes) * 0.985 if min(closes) > 0 else min(closes)
        ax_price.fill_between(xs, closes, y_floor, color=main_color, alpha=0.15)

        # Overlay 50-period and 200-period moving averages when enough daily bars are present
        if n >= 50:
            ma50_vals = [sum(closes[max(0, i - 49) : i + 1]) / min(50, i + 1) for i in range(n)]
            ax_price.plot(xs, ma50_vals, color="#58A6FF", linewidth=1.35, linestyle="--", alpha=0.88, label="50-Period MA")
        if n >= 200:
            ma200_vals = [sum(closes[max(0, i - 199) : i + 1]) / min(200, i + 1) for i in range(n)]
            ax_price.plot(xs, ma200_vals, color="#D2A8FF", linewidth=1.35, linestyle="-.", alpha=0.88, label="200-Period MA")

        # Annotate period high and low
        if n >= 3:
            hi_idx = max(range(n), key=lambda i: closes[i])
            lo_idx = min(range(n), key=lambda i: closes[i])
            ax_price.scatter([hi_idx], [closes[hi_idx]], color=BULLISH_GREEN, s=36, zorder=5)
            ax_price.scatter([lo_idx], [closes[lo_idx]], color=BEARISH_RED, s=36, zorder=5)

        leg = ax_price.legend(facecolor=DARK_PANEL_COLOR, edgecolor=DARK_GRID_COLOR, fontsize=9, loc="upper left")
        for txt in leg.get_texts():
            txt.set_color(DARK_TEXT_COLOR)

        if ax_vol is not None:
            # Volume bars
            vol_colors = [
                BULLISH_GREEN if (i == 0 or closes[i] >= closes[i - 1]) else BEARISH_RED
                for i in range(n)
            ]
            ax_vol.bar(xs, volumes, color=vol_colors, width=0.75, alpha=0.7)
            ax_vol.yaxis.set_major_formatter(
                mticker.FuncFormatter(
                    lambda v, _: f"{v / 1e6:.1f}M" if v >= 1e6 else (f"{v / 1e3:.0f}K" if v >= 1e3 else f"{v:.0f}")
                )
            )

        # Date tick labels
        step = max(1, math.ceil(n / 8))
        tick_idxs = list(range(0, n, step))
        if (n - 1) not in tick_idxs:
            tick_idxs.append(n - 1)
        span_days = (timestamps[-1] - timestamps[0]) / 86400.0 if n >= 2 else 30
        fmt_str = "%H:%M" if span_days <= 2 else ("%b %d" if span_days <= 370 else "%b '%y")
        tick_labels = [datetime.fromtimestamp(timestamps[i], tz=timezone.utc).strftime(fmt_str) for i in tick_idxs]
        target_ax = ax_vol if ax_vol is not None else ax_price
        target_ax.set_xticks(tick_idxs)
        target_ax.set_xticklabels(tick_labels, rotation=0)
        if ax_vol is None:
            fig.tight_layout(pad=1.3)

        png_bytes = _figure_to_png_bytes(fig)
        summary_lines = [
            f"**Asset:** {bar_series.name} (`{bar_series.symbol}`) — {bar_series.timeframe_label}",
            f"**Price Trajectory:** {_fmt_price_val(closes[0])} {bar_series.currency} → **{_fmt_price_val(closes[-1])} {bar_series.currency}** (**{sign}{bar_series.pct_change:.2f}%**)",
            f"**Period Range:** Low **{_fmt_price_val(bar_series.period_low)}** — High **{_fmt_price_val(bar_series.period_high)} {bar_series.currency}** ({n} bars plotted)",
        ]
        return RenderedVisual(
            filename=_slugify_filename(f"{bar_series.symbol}_{bar_series.timeframe_label}", default="market_chart"),
            png_bytes=png_bytes,
            title=title_str,
            summary_lines=summary_lines,
            renderer_used="matplotlib_market",
        )

    def render_multi_market_comparison(self, series_list: List[MarketBarSeries]) -> RenderedVisual:
        """Render a normalized cumulative % return comparison chart for 2 to 5 market tickers."""
        fig = Figure(figsize=(10.5, 6.0), dpi=140, facecolor=DARK_BG_COLOR)
        ax = fig.add_subplot(111)

        tf_label = series_list[0].timeframe_label if series_list else "1-Year (1Y)"
        sym_names = " vs. ".join(s.symbol for s in series_list)
        title_str = f"Normalized Return Comparison: {sym_names} ({tf_label})"

        ranked = sorted(series_list, key=lambda s: s.pct_change, reverse=True)
        longest_s = max(series_list, key=lambda s: len(s.timestamps))

        # Check if 2-asset comparison has a massive return-amplitude disparity (>=4x, e.g. BTC-USD vs DXY)
        curves: List[Tuple[MarketBarSeries, List[float], List[float], float]] = []
        for s in ranked:
            if not s.closes or s.closes[0] <= 0:
                continue
            base = s.closes[0]
            pct_curve = [((c - base) / base) * 100.0 for c in s.closes]
            n_pts = len(pct_curve)
            xs = [i * (100.0 / max(1, n_pts - 1)) for i in range(n_pts)]
            span = max(pct_curve) - min(pct_curve) if pct_curve else 0.0
            curves.append((s, xs, pct_curve, span))

        use_dual_axis = False
        if len(curves) == 2:
            hi_span = max(curves[0][3], curves[1][3])
            lo_span = max(0.5, min(curves[0][3], curves[1][3]))
            if hi_span / lo_span >= 4.0:
                use_dual_axis = True

        if use_dual_axis:
            s_left, xs_left, pct_left, _ = curves[0]
            s_right, xs_right, pct_right, _ = curves[1]
            c_left = DARK_THEME_PALETTE[0]
            c_right = DARK_THEME_PALETTE[1]
            _style_dark_axes(
                ax,
                title=title_str,
                subtitle="Dual-axis cumulative % return (scaled per asset to reveal macro co-movement)",
                y_label=f"{s_left.symbol} Return (%)",
            )
            ax2 = ax.twinx()
            ax2.set_facecolor("none")
            for spine in ax2.spines.values():
                spine.set_color(DARK_GRID_COLOR)
            ax2.set_ylabel(f"{s_right.symbol} Return (%)", color=c_right, fontsize=10, fontweight="bold")
            ax.yaxis.label.set_color(c_left)
            ax.tick_params(axis="y", colors=c_left, labelsize=9.5)
            ax2.tick_params(axis="y", colors=c_right, labelsize=9.5)
            ax2.grid(False)

            ax.axhline(0.0, color=DARK_MUTED_TEXT_COLOR, linestyle=":", linewidth=1.0, alpha=0.6)
            sign_l = "+" if s_left.pct_change > 0 else ""
            sign_r = "+" if s_right.pct_change > 0 else ""
            l1 = ax.plot(xs_left, pct_left, color=c_left, linewidth=2.3, label=f"{s_left.symbol} ({sign_l}{s_left.pct_change:.2f}%) [Left]")
            l2 = ax2.plot(xs_right, pct_right, color=c_right, linewidth=2.3, label=f"{s_right.symbol} ({sign_r}{s_right.pct_change:.2f}%) [Right]")
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{'+' if v > 0 else ''}{v:.1f}%"))
            ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{'+' if v > 0 else ''}{v:.1f}%"))
            lines_all = l1 + l2
            labels_all = [ln.get_label() for ln in lines_all]
            leg = ax.legend(lines_all, labels_all, facecolor=DARK_PANEL_COLOR, edgecolor=DARK_GRID_COLOR, fontsize=10, loc="upper left")
            for txt in leg.get_texts():
                txt.set_color(DARK_TEXT_COLOR)
        else:
            _style_dark_axes(
                ax,
                title=title_str,
                subtitle="Cumulative percentage return indexed to 0.0% at period start",
                y_label="Cumulative Return (%)",
            )
            ax.axhline(0.0, color=DARK_MUTED_TEXT_COLOR, linestyle=":", linewidth=1.1, alpha=0.8)
            for idx, (s, xs, pct_curve, _) in enumerate(curves):
                color = DARK_THEME_PALETTE[idx % len(DARK_THEME_PALETTE)]
                sign = "+" if s.pct_change > 0 else ""
                ax.plot(
                    xs,
                    pct_curve,
                    color=color,
                    linewidth=2.3,
                    label=f"{s.symbol} ({sign}{s.pct_change:.2f}%)",
                )
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{'+' if v > 0 else ''}{v:.1f}%"))
            leg = ax.legend(facecolor=DARK_PANEL_COLOR, edgecolor=DARK_GRID_COLOR, fontsize=10, loc="upper left")
            for txt in leg.get_texts():
                txt.set_color(DARK_TEXT_COLOR)

        if longest_s.timestamps:
            n_ts = len(longest_s.timestamps)
            span_days = (longest_s.timestamps[-1] - longest_s.timestamps[0]) / 86400.0 if n_ts >= 2 else 30
            fmt_str = "%b %d" if span_days <= 370 else "%b '%y"
            tick_positions = [0, 20, 40, 60, 80, 100]
            tick_labels = [
                datetime.fromtimestamp(
                    longest_s.timestamps[min(n_ts - 1, int(round((p / 100.0) * (n_ts - 1))))],
                    tz=timezone.utc,
                ).strftime(fmt_str)
                for p in tick_positions
            ]
            ax.set_xticks(tick_positions)
            ax.set_xticklabels(tick_labels)

        fig.tight_layout(pad=1.4)
        png_bytes = _figure_to_png_bytes(fig)
        summary_lines = [
            f"**Comparison Window:** {tf_label}",
            "**Ranked Cumulative Returns:** "
            + " | ".join(f"**{s.symbol}**: {'+' if s.pct_change > 0 else ''}{s.pct_change:.2f}%" for s in ranked),
        ]
        if len(ranked) >= 2:
            lead = ranked[0].pct_change - ranked[1].pct_change
            summary_lines.append(
                f"**Outperformer:** `{ranked[0].symbol}` led `{ranked[1].symbol}` by **+{lead:.2f}%**"
            )
        return RenderedVisual(
            filename=_slugify_filename(f"compare_{'_vs_'.join(s.symbol for s in series_list)}", default="comparison_chart"),
            png_bytes=png_bytes,
            title=title_str,
            summary_lines=summary_lines,
            renderer_used="matplotlib_comparison",
        )

    def render_monte_carlo_chart(self, mc: MonteCarloResult) -> RenderedVisual:
        """
        Render a dual-panel dark-mode Geometric Brownian Motion (GBM) Monte Carlo fan chart
        (trailing history + 60 stochastic paths + P5-P95 & P25-P75 confidence cones + right-hand
        terminal price distribution histogram).
        """
        fig = Figure(figsize=(11.6, 6.6), dpi=140, facecolor=DARK_BG_COLOR)
        gs = fig.add_gridspec(1, 2, width_ratios=[3.6, 1.0], wspace=0.06)
        ax_fan = fig.add_subplot(gs[0])
        ax_hist = fig.add_subplot(gs[1], sharey=ax_fan)

        n_hist = len(mc.history_closes)
        n_fut = len(mc.p50_path)
        hist_xs = list(range(n_hist))
        fut_xs = [max(0, n_hist - 1) + i for i in range(n_fut)]

        p5_end = mc.p5_path[-1]
        p25_end = mc.p25_path[-1]
        p50_end = mc.p50_path[-1]
        p75_end = mc.p75_path[-1]
        p95_end = mc.p95_path[-1]
        mean_end = mc.mean_path[-1]

        title_str = f"Monte Carlo GBM Simulation: {mc.name} ({mc.symbol}) — {mc.horizon_label} | Basis: {mc.lookback_label}"
        drift_sign = "+" if mc.annualized_drift_pct > 0 else ""
        subtitle_str = (
            f"{mc.num_simulations:,} Paths  |  Calibrated on {mc.lookback_label} ({mc.lookback_days:,} daily bars)  |  "
            f"Spot: {_fmt_price_val(mc.spot_price)} {mc.currency}  |  Vol (σ): {mc.annualized_volatility_pct:.1f}%/yr  |  "
            f"Drift: {drift_sign}{mc.annualized_drift_pct:.1f}%/yr  |  P(Profit): {mc.prob_profit_pct:.1f}%"
        )
        _style_dark_axes(
            ax_fan,
            title=title_str,
            subtitle=subtitle_str,
            y_label=f"Price ({mc.currency})",
        )
        _style_dark_axes(ax_hist, x_label="Terminal Density")
        ax_hist.tick_params(labelleft=False)

        # 1. Plot trailing historical price
        if n_hist >= 2:
            ax_fan.plot(
                hist_xs,
                mc.history_closes,
                color=DARK_TEXT_COLOR,
                linewidth=2.0,
                label=f"Recent Close (Calibrated: {mc.lookback_label})",
                zorder=4,
            )
            ax_fan.axvline(
                n_hist - 1,
                color=DARK_MUTED_TEXT_COLOR,
                linestyle="--",
                linewidth=1.1,
                alpha=0.85,
                zorder=3,
            )

        # 2. Plot sample stochastic trajectories
        for path in mc.sample_paths[:55]:
            ax_fan.plot(fut_xs, path, color="#58A6FF", linewidth=0.55, alpha=0.08, zorder=1)

        # 3. Shaded confidence cones (P5-P95 and P25-P75)
        ax_fan.fill_between(
            fut_xs,
            mc.p5_path,
            mc.p95_path,
            color="#58A6FF",
            alpha=0.13,
            label="90% Confidence Cone (P5–P95)",
            zorder=2,
        )
        ax_fan.fill_between(
            fut_xs,
            mc.p25_path,
            mc.p75_path,
            color="#39C5CF",
            alpha=0.24,
            label="50% Interquartile Band (P25–P75)",
            zorder=2,
        )

        # 4. Percentile trajectories
        ax_fan.plot(
            fut_xs,
            mc.p95_path,
            color=BULLISH_GREEN,
            linewidth=1.8,
            linestyle="--",
            label=f"95th Pct Bull ({_fmt_price_val(p95_end)})",
            zorder=5,
        )
        ax_fan.plot(
            fut_xs,
            mc.p75_path,
            color="#39C5CF",
            linewidth=1.5,
            linestyle="-.",
            label=f"75th Pct ({_fmt_price_val(p75_end)})",
            zorder=5,
        )
        ax_fan.plot(
            fut_xs,
            mc.p50_path,
            color="#58A6FF",
            linewidth=2.5,
            label=f"50th Pct Median ({_fmt_price_val(p50_end)})",
            zorder=6,
        )
        ax_fan.plot(
            fut_xs,
            mc.p25_path,
            color="#E3B341",
            linewidth=1.5,
            linestyle="-.",
            label=f"25th Pct ({_fmt_price_val(p25_end)})",
            zorder=5,
        )
        ax_fan.plot(
            fut_xs,
            mc.p5_path,
            color=BEARISH_RED,
            linewidth=1.8,
            linestyle="--",
            label=f"5th Pct Bear ({_fmt_price_val(p5_end)})",
            zorder=5,
        )
        ax_fan.axhline(mc.spot_price, color=DARK_MUTED_TEXT_COLOR, linestyle=":", linewidth=0.9, alpha=0.7)

        # Cap Y-axis upper bound slightly above P95 max so extreme single-path outliers don't squash the fan
        y_min = min(min(mc.p5_path), min(mc.history_closes) if mc.history_closes else mc.spot_price) * 0.90
        y_max = max(max(mc.p95_path), max(mc.history_closes) if mc.history_closes else mc.spot_price) * 1.08
        if y_max > y_min > 0:
            ax_fan.set_ylim(y_min, y_max)

        # 5. Terminal distribution histogram on the right panel
        clipped_term = [min(y_max, max(y_min, v)) for v in mc.terminal_prices if y_min <= v <= y_max * 1.05]
        if clipped_term:
            ax_hist.hist(
                clipped_term,
                bins=32,
                orientation="horizontal",
                color="#58A6FF",
                 alpha=0.45,
                edgecolor=DARK_PANEL_COLOR,
            )
        for lvl, col, lbl in (
            (p95_end, BULLISH_GREEN, f"P95: {_fmt_price_val(p95_end)}"),
            (p75_end, "#39C5CF", f"P75: {_fmt_price_val(p75_end)}"),
            (p50_end, "#58A6FF", f"P50: {_fmt_price_val(p50_end)}"),
            (p25_end, "#E3B341", f"P25: {_fmt_price_val(p25_end)}"),
            (p5_end, BEARISH_RED, f"P5: {_fmt_price_val(p5_end)}"),
        ):
            ax_hist.axhline(lvl, color=col, linestyle="--", linewidth=1.2, alpha=0.9)
            ax_hist.text(
                0.96,
                lvl,
                lbl,
                transform=ax_hist.get_yaxis_transform(),
                color=col,
                fontsize=8.0,
                fontweight="bold",
                ha="right",
                va="bottom",
                bbox={"boxstyle": "round,pad=0.15", "facecolor": DARK_BG_COLOR, "edgecolor": "none", "alpha": 0.8},
            )

        # Date tick labels across combined historical + future timeline
        all_ts = (mc.history_timestamps[:-1] if n_hist >= 1 else []) + mc.future_timestamps
        total_pts = len(all_ts)
        if total_pts >= 2:
            step = max(1, math.ceil(total_pts / 8))
            tick_idxs = list(range(0, total_pts, step))
            if (total_pts - 1) not in tick_idxs:
                tick_idxs.append(total_pts - 1)
            span_days = (all_ts[-1] - all_ts[0]) / 86400.0
            fmt_str = "%b %d" if span_days <= 180 else "%b '%y"
            tick_labels = [datetime.fromtimestamp(all_ts[i], tz=timezone.utc).strftime(fmt_str) for i in tick_idxs]
            ax_fan.set_xticks(tick_idxs)
            ax_fan.set_xticklabels(tick_labels)

        leg = ax_fan.legend(facecolor=DARK_PANEL_COLOR, edgecolor=DARK_GRID_COLOR, fontsize=8.5, loc="upper left")
        for txt in leg.get_texts():
            txt.set_color(DARK_TEXT_COLOR)

        fig.subplots_adjust(left=0.08, right=0.97, top=0.89, bottom=0.11)
        png_bytes = _figure_to_png_bytes(fig)

        def _pct_vs_spot(val: float) -> str:
            chg = ((val - mc.spot_price) / mc.spot_price) * 100.0 if mc.spot_price > 0 else 0.0
            return f"{'+' if chg > 0 else ''}{chg:.1f}%"

        summary_lines = [
            f"**Asset & Forecast Horizon:** {mc.name} (`{mc.symbol}`) — {mc.horizon_label} ({mc.num_simulations:,} GBM simulated paths)",
            f"**Historical Calibration Basis:** **{mc.lookback_label}** ({mc.lookback_days:,} daily bars used to calibrate volatility & drift; mention clearly in your reply that this simulation is based on **{mc.lookback_label}**)",
            f"**Calibrated Parameters:** Spot **{_fmt_price_val(mc.spot_price)} {mc.currency}** | Annualized Volatility (σ): **{mc.annualized_volatility_pct:.2f}%** | Annualized Drift: **{drift_sign}{mc.annualized_drift_pct:.2f}%**",
            f"**Terminal Price Percentiles:** Bear P5: **{_fmt_price_val(p5_end)} {mc.currency}** ({_pct_vs_spot(p5_end)}) | P25: **{_fmt_price_val(p25_end)}** ({_pct_vs_spot(p25_end)}) | **Median P50: {_fmt_price_val(p50_end)} {mc.currency} ({_pct_vs_spot(p50_end)})** | Mean: **{_fmt_price_val(mean_end)}** ({_pct_vs_spot(mean_end)}) | P75: **{_fmt_price_val(p75_end)}** ({_pct_vs_spot(p75_end)}) | Bull P95: **{_fmt_price_val(p95_end)} {mc.currency}** ({_pct_vs_spot(p95_end)})",
            f"**Probability Metrics:** P(Price > Spot): **{mc.prob_profit_pct:.1f}%** | P(≥ 2x Spot): **{mc.prob_double_pct:.1f}%** | P(Drawdown ≥ 20%): **{mc.prob_drawdown_20_pct:.1f}%**",
        ]
        return RenderedVisual(
            filename=_slugify_filename(f"monte_carlo_{mc.symbol}_{mc.horizon_days}d_{mc.lookback_label}", default="monte_carlo_chart"),
            png_bytes=png_bytes,
            title=title_str,
            summary_lines=summary_lines,
            renderer_used="matplotlib_monte_carlo",
        )

    def render_flowchart_fallback(
        self,
        title: str,
        nodes: List[Tuple[str, str]],
        edges: List[Tuple[str, str, Optional[str]]],
        direction: str = "TD",
    ) -> bytes:
        """Render a clean dark-mode directed node-and-arrow flowchart locally via Matplotlib when offline."""
        is_horiz = direction.upper() in ("LR", "RL")
        n_nodes = max(1, len(nodes))
        fig_w = max(8.5, min(14.0, n_nodes * 2.1)) if is_horiz else 9.0
        fig_h = 5.2 if is_horiz else max(5.5, min(13.0, n_nodes * 1.45))
        fig = Figure(figsize=(fig_w, fig_h), dpi=140, facecolor=DARK_BG_COLOR)
        ax = fig.add_subplot(111)
        ax.set_facecolor(DARK_BG_COLOR)
        ax.axis("off")

        if title:
            ax.set_title(title, color=DARK_TEXT_COLOR, fontsize=13.5, fontweight="bold", pad=14)

        coords: dict[str, Tuple[float, float]] = {}
        for idx, (node_id, _) in enumerate(nodes):
            t = (idx + 0.5) / n_nodes
            if is_horiz:
                coords[node_id] = (t, 0.5 + (0.14 * ((idx % 2) * 2 - 1) if n_nodes > 4 else 0.0))
            else:
                coords[node_id] = (0.5 + (0.18 * ((idx % 2) * 2 - 1) if n_nodes > 5 else 0.0), 1.0 - t)

        # Draw edges
        for src, dst, edge_lbl in edges:
            if src in coords and dst in coords:
                x0, y0 = coords[src]
                x1, y1 = coords[dst]
                arrow = mpatches.FancyArrowPatch(
                    (x0, y0),
                    (x1, y1),
                    arrowstyle="-|>,head_length=8,head_width=5",
                    connectionstyle="arc3,rad=0.06",
                    color="#58A6FF",
                    linewidth=1.8,
                    zorder=2,
                )
                ax.add_patch(arrow)
                if edge_lbl:
                    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                    ax.text(
                        mx,
                        my + 0.03,
                        edge_lbl,
                        color=DARK_MUTED_TEXT_COLOR,
                        fontsize=8.5,
                        ha="center",
                        va="center",
                        bbox={"boxstyle": "round,pad=0.2", "facecolor": DARK_BG_COLOR, "edgecolor": "none"},
                        zorder=4,
                    )

        # Draw node boxes
        for idx, (node_id, label) in enumerate(nodes):
            x, y = coords[node_id]
            border_color = DARK_THEME_PALETTE[idx % len(DARK_THEME_PALETTE)]
            ax.text(
                x,
                y,
                label,
                color=DARK_TEXT_COLOR,
                fontsize=10,
                fontweight="bold",
                ha="center",
                va="center",
                bbox={
                    "boxstyle": "round,pad=0.65,rounding_size=0.2",
                    "facecolor": DARK_PANEL_COLOR,
                    "edgecolor": border_color,
                    "linewidth": 1.8,
                },
                zorder=5,
            )

        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.0)
        fig.tight_layout(pad=1.2)
        return _figure_to_png_bytes(fig)
