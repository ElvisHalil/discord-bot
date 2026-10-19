"""
Thread-safe Calendar Heatmap & 2D Matrix Heatmap rendering engine (`src/skills/graphing/aggregators/heatmap_engine.py`).

Provides:
  1. `render_calendar_heatmap(CalendarHeatmapSpec)`:
     Hopper / Google Flights style 7-column (`MON–SUN`) monthly fare & metric calendar heatmaps
     with rounded glassmorphic day tiles, 5-tier color coding (Deal Green -> Typical Blue -> Peak Coral),
     direct-flight badges, and a summary footer strip.
  2. `render_matrix_heatmap(ChartSpec)`:
     Universal 2D annotated grid heatmap for multi-series or category-by-category matrices
     (auto-promoting ISO `YYYY-MM-DD` date series to `render_calendar_heatmap`).
"""

from __future__ import annotations

import calendar
from datetime import date as _date, datetime
import io
import re
from typing import Dict, List, Optional, Tuple

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import matplotlib.patches as mpatches

from src.skills.graphing.aggregators.base import BaseGraphingAggregator
from src.skills.graphing.catalogs import (
    DARK_BG_COLOR,
    DARK_GRID_COLOR,
    DARK_MUTED_TEXT_COLOR,
    DARK_PANEL_COLOR,
    DARK_TEXT_COLOR,
    HEATMAP_EMPTY_TILE,
    HEATMAP_TIER_DEAL,
    HEATMAP_TIER_ELEVATED,
    HEATMAP_TIER_GOOD,
    HEATMAP_TIER_PEAK,
    HEATMAP_TIER_TYPICAL,
)
from src.skills.graphing.models import (
    CalendarDayCell,
    CalendarHeatmapSpec,
    ChartSpec,
    RenderedVisual,
)

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_WEEKDAY_HEADERS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _slugify_filename(title: str, default: str = "heatmap") -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", (title or "").strip()).strip("_").lower()
    return f"{slug[:48] or default}.png"


def _figure_to_png_bytes(fig: Figure, dpi: int = 140) -> bytes:
    canvas = FigureCanvasAgg(fig)
    buf = io.BytesIO()
    canvas.print_png(buf)
    return buf.getvalue()


def _format_cell_value(val: float, prefix: str = "$", suffix: str = "") -> str:
    if abs(val) >= 100 or val % 1 == 0:
        return f"{prefix}{int(round(val)):,}{suffix}"
    return f"{prefix}{val:,.2f}{suffix}"


def _percentile(sorted_vals: List[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = max(0.0, min(1.0, q)) * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(len(sorted_vals) - 1, lo + 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _pick_tier_colors(
    val: float,
    p20: float,
    p45: float,
    p70: float,
    p88: float,
    higher_is_better: bool = False,
) -> Tuple[str, str, str]:
    """Return `(fill_hex, border_hex, value_text_hex)` for a cell value."""
    if not higher_is_better:
        if val <= p20:
            return HEATMAP_TIER_DEAL
        if val <= p45:
            return HEATMAP_TIER_GOOD
        if val <= p70:
            return HEATMAP_TIER_TYPICAL
        if val <= p88:
            return HEATMAP_TIER_ELEVATED
        return HEATMAP_TIER_PEAK
    else:
        if val >= p88:
            return HEATMAP_TIER_DEAL
        if val >= p70:
            return HEATMAP_TIER_GOOD
        if val >= p45:
            return HEATMAP_TIER_TYPICAL
        if val >= p20:
            return HEATMAP_TIER_ELEVATED
        return HEATMAP_TIER_PEAK


class HeatmapAggregator(BaseGraphingAggregator):
    """Renders 7-column monthly Calendar Heatmaps (flight fare calendars) and 2D Matrix Heatmaps."""

    name = "matplotlib_heatmap"

    def render_calendar_heatmap(self, spec: CalendarHeatmapSpec) -> RenderedVisual:
        """Render a 1- or 2-month 7-column Mon-Sun calendar heatmap card into PNG bytes."""
        parsed_cells: Dict[_date, CalendarDayCell] = {}
        for cell in spec.cells or []:
            m = _ISO_DATE_RE.match((cell.date_str or "").strip())
            if not m:
                continue
            try:
                dt = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                # Keep the lowest value if duplicate dates exist (unless higher_is_better)
                if dt in parsed_cells:
                    prev = parsed_cells[dt]
                    if (not spec.higher_is_better and cell.value < prev.value) or (
                        spec.higher_is_better and cell.value > prev.value
                    ):
                        parsed_cells[dt] = cell
                    elif cell.is_direct and not prev.is_direct:
                        prev.is_direct = True
                else:
                    parsed_cells[dt] = cell
            except ValueError:
                continue

        if not parsed_cells:
            # Fallback empty placeholder card if no valid ISO dates were supplied
            fig = Figure(figsize=(9.5, 4.5), dpi=140, facecolor=DARK_BG_COLOR)
            ax = fig.add_subplot(111)
            ax.set_facecolor(DARK_BG_COLOR)
            ax.axis("off")
            ax.text(
                0.5,
                0.5,
                f"{spec.title or 'Calendar Heatmap'}\nNo dated values available to render.",
                color=DARK_TEXT_COLOR,
                fontsize=12,
                ha="center",
                va="center",
            )
            return RenderedVisual(
                filename=_slugify_filename(spec.title, default="calendar_heatmap"),
                png_bytes=_figure_to_png_bytes(fig),
                title=spec.title or "Calendar Heatmap",
                summary_lines=[],
                renderer_used="matplotlib_heatmap",
            )

        sorted_dates = sorted(parsed_cells.keys())
        all_vals = sorted(c.value for c in parsed_cells.values())
        min_val = all_vals[0]
        max_val = all_vals[-1]
        med_val = _percentile(all_vals, 0.50)

        p20 = spec.low_threshold if spec.low_threshold is not None else _percentile(all_vals, 0.20)
        p45 = _percentile(all_vals, 0.45)
        p70 = _percentile(all_vals, 0.72)
        p88 = spec.high_threshold if spec.high_threshold is not None else _percentile(all_vals, 0.88)

        # Mark best cell(s) if none were explicitly flagged
        target_best_val = max_val if spec.higher_is_better else min_val
        if not any(c.is_best for c in parsed_cells.values()):
            for c in parsed_cells.values():
                if abs(c.value - target_best_val) < 1e-6:
                    c.is_best = True

        # Determine distinct (year, month) buckets (render up to 2 months side-by-side)
        months_order: List[Tuple[int, int]] = []
        for dt in sorted_dates:
            ym = (dt.year, dt.month)
            if ym not in months_order:
                months_order.append(ym)
        months_to_render = months_order[:2]
        n_months = len(months_to_render)

        cal = calendar.Calendar(firstweekday=0)  # Monday = 0
        month_weeks = [cal.monthdayscalendar(y, m) for y, m in months_to_render]
        max_weeks = max(len(w) for w in month_weeks)

        fig_w = 10.6 if n_months == 1 else 14.4
        fig_h = max(6.2, 2.3 + max_weeks * 0.92)
        fig = Figure(figsize=(fig_w, fig_h), dpi=140, facecolor=DARK_BG_COLOR)

        # Top header & legend area
        fig.text(
            0.035,
            0.945,
            spec.title or "Calendar Heatmap",
            color=DARK_TEXT_COLOR,
            fontsize=14.5,
            fontweight="bold",
            ha="left",
            va="top",
        )
        if spec.subtitle:
            fig.text(
                0.035,
                0.902,
                spec.subtitle,
                color=DARK_MUTED_TEXT_COLOR,
                fontsize=9.8,
                ha="left",
                va="top",
            )

        # Legend pills on top-right
        has_direct = any(c.is_direct for c in parsed_cells.values())
        legend_items = [
            ("Lowest / Deal", HEATMAP_TIER_DEAL[1]),
            ("Below Avg", HEATMAP_TIER_GOOD[1]),
            ("Typical", HEATMAP_TIER_TYPICAL[1]),
            ("Elevated", HEATMAP_TIER_ELEVATED[1]),
            ("Peak / High", HEATMAP_TIER_PEAK[1]),
        ]
        if has_direct:
            legend_items.append(("Direct Flight", "#39C5CF"))

        leg_patches = [
            mpatches.Patch(facecolor=col, edgecolor="none", label=lbl)
            for lbl, col in legend_items
        ]
        fig.legend(
            handles=leg_patches,
            loc="upper right",
            bbox_to_anchor=(0.975, 0.955),
            ncol=len(leg_patches),
            frameon=True,
            facecolor=DARK_PANEL_COLOR,
            edgecolor=DARK_GRID_COLOR,
            fontsize=8.3,
            labelcolor=DARK_TEXT_COLOR,
            handlelength=1.0,
            handleheight=0.9,
            columnspacing=1.0,
        )

        gs = fig.add_gridspec(
            1,
            n_months,
            left=0.035,
            right=0.965,
            top=0.83,
            bottom=0.11,
            wspace=0.06,
        )

        for m_idx, (year, month) in enumerate(months_to_render):
            ax = fig.add_subplot(gs[0, m_idx])
            ax.set_facecolor(DARK_BG_COLOR)
            weeks = month_weeks[m_idx]
            n_rows = len(weeks)

            ax.set_xlim(0, 7)
            ax.set_ylim(0, n_rows + 0.65)
            ax.axis("off")

            month_title = f"{calendar.month_name[month].upper()} {year}"
            ax.text(
                3.5,
                n_rows + 0.42,
                month_title,
                color=DARK_TEXT_COLOR,
                fontsize=11.5,
                fontweight="bold",
                ha="center",
                va="center",
            )

            # Weekday column headers
            for col_idx, wd_name in enumerate(_WEEKDAY_HEADERS):
                ax.text(
                    col_idx + 0.5,
                    n_rows + 0.08,
                    wd_name,
                    color=DARK_MUTED_TEXT_COLOR,
                    fontsize=8.8,
                    fontweight="bold",
                    ha="center",
                    va="center",
                )

            # Draw day tiles
            pad = 0.055
            tile_w = 1.0 - 2 * pad
            tile_h = 1.0 - 2 * pad

            for row_idx, week in enumerate(weeks):
                y_bottom = (n_rows - 1 - row_idx) + pad
                for col_idx, day_num in enumerate(week):
                    if day_num == 0:
                        continue
                    x_left = col_idx + pad
                    dt_obj = _date(year, month, day_num)
                    cell = parsed_cells.get(dt_obj)

                    if cell is not None:
                        fill_c, border_c, val_text_c = _pick_tier_colors(
                            cell.value,
                            p20=p20,
                            p45=p45,
                            p70=p70,
                            p88=p88,
                            higher_is_better=spec.higher_is_better,
                        )
                        lw = 2.1 if cell.is_best else 1.05
                        if cell.is_best:
                            border_c = "#3FB950"
                            fill_c = "#14462B"

                        box = mpatches.FancyBboxPatch(
                            (x_left, y_bottom),
                            tile_w,
                            tile_h,
                            boxstyle="round,pad=0.015,rounding_size=0.08",
                            facecolor=fill_c,
                            edgecolor=border_c,
                            linewidth=lw,
                        )
                        ax.add_patch(box)

                        # Top-left: Day of Month
                        ax.text(
                            x_left + 0.08,
                            y_bottom + tile_h - 0.10,
                            str(day_num),
                            color=DARK_TEXT_COLOR if cell.is_best else DARK_MUTED_TEXT_COLOR,
                            fontsize=8.2,
                            fontweight="bold",
                            ha="left",
                            va="top",
                        )

                        # Top-right badge: BEST or DIRECT
                        if cell.is_best:
                            badge_txt = "BEST · DIRECT" if (cell.is_direct and n_months == 1) else "BEST"
                            ax.text(
                                x_left + tile_w - 0.07,
                                y_bottom + tile_h - 0.10,
                                badge_txt,
                                color="#7EE787",
                                fontsize=6.6 if len(badge_txt) > 6 else 7.0,
                                fontweight="bold",
                                ha="right",
                                va="top",
                            )
                        elif cell.is_direct:
                            ax.text(
                                x_left + tile_w - 0.07,
                                y_bottom + tile_h - 0.10,
                                "DIRECT",
                                color="#39C5CF",
                                fontsize=6.8,
                                fontweight="bold",
                                ha="right",
                                va="top",
                            )

                        # Center: Formatted Price / Value
                        val_str = cell.label or _format_cell_value(
                            cell.value,
                            prefix=spec.value_prefix,
                            suffix=spec.value_suffix,
                        )
                        center_y = y_bottom + tile_h * (0.48 if cell.sublabel else 0.43)
                        ax.text(
                            x_left + tile_w / 2.0,
                            center_y,
                            val_str,
                            color=val_text_c,
                            fontsize=10.2 if n_months == 1 else 9.0,
                            fontweight="bold",
                            ha="center",
                            va="center",
                        )

                        # Bottom sublabel (e.g. return date or airline)
                        if cell.sublabel:
                            ax.text(
                                x_left + tile_w / 2.0,
                                y_bottom + 0.11,
                                cell.sublabel[:14],
                                color=DARK_MUTED_TEXT_COLOR,
                                fontsize=6.8 if n_months == 1 else 6.2,
                                ha="center",
                                va="bottom",
                            )
                    else:
                        # Unscanned or past date in the month
                        e_fill, e_border, e_txt = HEATMAP_EMPTY_TILE
                        box = mpatches.FancyBboxPatch(
                            (x_left, y_bottom),
                            tile_w,
                            tile_h,
                            boxstyle="round,pad=0.015,rounding_size=0.08",
                            facecolor=e_fill,
                            edgecolor=e_border,
                            linewidth=0.75,
                        )
                        ax.add_patch(box)
                        ax.text(
                            x_left + 0.08,
                            y_bottom + tile_h - 0.10,
                            str(day_num),
                            color=e_txt,
                            fontsize=8.0,
                            ha="left",
                            va="top",
                        )
                        ax.text(
                            x_left + tile_w / 2.0,
                            y_bottom + tile_h * 0.42,
                            "—",
                            color=e_txt,
                            fontsize=9.0,
                            ha="center",
                            va="center",
                        )

        # Build summary lines & footer strip
        best_dates = [dt for dt, c in sorted(parsed_cells.items()) if c.is_best]
        best_dt = best_dates[0] if best_dates else sorted_dates[0]
        best_cell = parsed_cells[best_dt]
        peak_dt = max(parsed_cells.keys(), key=lambda d: parsed_cells[d].value)
        peak_cell = parsed_cells[peak_dt]

        ccy_suffix = f" {spec.currency}" if spec.currency else ""
        best_val_fmt = _format_cell_value(best_cell.value, spec.value_prefix, spec.value_suffix) + ccy_suffix
        med_val_fmt = _format_cell_value(med_val, spec.value_prefix, spec.value_suffix) + ccy_suffix
        peak_val_fmt = _format_cell_value(peak_cell.value, spec.value_prefix, spec.value_suffix) + ccy_suffix

        summary_lines = [
            f"**Lowest / Best Date:** `{best_dt.strftime('%a, %b %d, %Y')}` at **{best_val_fmt}**",
            f"**Window Spread ({len(parsed_cells)} days):** Median **{med_val_fmt}** | Peak `{peak_dt.strftime('%a, %b %d')}` (**{peak_val_fmt}**)",
        ]
        if spec.footer_notes:
            for note in spec.footer_notes[:2]:
                summary_lines.append(note)

        footer_parts = [
            f"Best Deal: {best_dt.strftime('%a %b %d')} ({best_val_fmt})",
            f"Median: {med_val_fmt}",
            f"Peak: {peak_dt.strftime('%a %b %d')} ({peak_val_fmt})",
        ]
        if spec.footer_notes:
            clean_note = re.sub(r"[*`]", "", spec.footer_notes[0]).strip()
            if clean_note:
                footer_parts.append(clean_note)

        fig.text(
            0.5,
            0.035,
            "   •   ".join(footer_parts),
            color=DARK_MUTED_TEXT_COLOR,
            fontsize=8.8,
            ha="center",
            va="center",
        )

        png_bytes = _figure_to_png_bytes(fig)
        return RenderedVisual(
            filename=_slugify_filename(spec.title, default="calendar_heatmap"),
            png_bytes=png_bytes,
            title=spec.title or "Calendar Heatmap",
            summary_lines=summary_lines,
            renderer_used="matplotlib_heatmap",
        )

    def render_matrix_heatmap(self, spec: ChartSpec) -> RenderedVisual:
        """
        Render a 2D annotated heatmap from a `ChartSpec`.
        Automatically upgrades ISO `YYYY-MM-DD` date labels into a 7-column `render_calendar_heatmap`.
        """
        labels = list(spec.labels or [])
        series_list = list(spec.series or [])

        # Auto-detect ISO date labels -> render as a 7-column Calendar Heatmap!
        if labels and len(series_list) == 1 and all(_ISO_DATE_RE.match(lbl.strip()) for lbl in labels if lbl.strip()):
            vals = series_list[0].values
            cells = [
                CalendarDayCell(date_str=lbl.strip(), value=float(v))
                for lbl, v in zip(labels, vals)
            ]
            cal_spec = CalendarHeatmapSpec(
                title=spec.title or "Calendar Heatmap",
                subtitle=spec.subtitle,
                cells=cells,
                value_prefix=spec.value_prefix or "",
                value_suffix=spec.value_suffix or "",
            )
            return self.render_calendar_heatmap(cal_spec)

        n_rows = max(1, len(series_list))
        n_cols = max((len(s.values) for s in series_list), default=0)
        if not labels and n_cols > 0:
            labels = [f"Col {i + 1}" for i in range(n_cols)]
        elif len(labels) < n_cols:
            labels = labels + [f"Col {i + 1}" for i in range(len(labels), n_cols)]

        all_vals = sorted(float(v) for s in series_list for v in s.values)
        p20 = _percentile(all_vals, 0.20)
        p45 = _percentile(all_vals, 0.45)
        p70 = _percentile(all_vals, 0.72)
        p88 = _percentile(all_vals, 0.88)

        fig_w = max(8.5, min(14.0, 2.4 + n_cols * 1.05))
        fig_h = max(4.6, min(10.0, 2.2 + n_rows * 0.85))
        fig = Figure(figsize=(fig_w, fig_h), dpi=140, facecolor=DARK_BG_COLOR)
        ax = fig.add_subplot(111)
        ax.set_facecolor(DARK_BG_COLOR)

        ax.set_xlim(0, max(1, n_cols))
        ax.set_ylim(0, max(1, n_rows) + 0.65)
        ax.axis("off")

        ax.text(
            0.0,
            n_rows + 0.45,
            spec.title or "Heatmap Matrix",
            color=DARK_TEXT_COLOR,
            fontsize=13.5,
            fontweight="bold",
            ha="left",
            va="center",
        )

        for col_idx, col_lbl in enumerate(labels[:n_cols]):
            ax.text(
                col_idx + 0.5,
                n_rows + 0.08,
                str(col_lbl)[:14],
                color=DARK_MUTED_TEXT_COLOR,
                fontsize=9.0,
                fontweight="bold",
                ha="center",
                va="center",
            )

        pad = 0.06
        tile_w = 1.0 - 2 * pad
        tile_h = 1.0 - 2 * pad

        for r_idx, s in enumerate(series_list):
            y_bottom = (n_rows - 1 - r_idx) + pad
            if n_rows > 1 or (s.name and s.name not in ("Series 1", "Values")):
                ax.text(
                    -0.08,
                    y_bottom + tile_h / 2.0,
                    s.name[:18],
                    color=DARK_TEXT_COLOR,
                    fontsize=9.2,
                    fontweight="bold",
                    ha="right",
                    va="center",
                )
            for c_idx in range(n_cols):
                x_left = c_idx + pad
                if c_idx < len(s.values):
                    val = float(s.values[c_idx])
                    fill_c, border_c, txt_c = _pick_tier_colors(val, p20, p45, p70, p88)
                    box = mpatches.FancyBboxPatch(
                        (x_left, y_bottom),
                        tile_w,
                        tile_h,
                        boxstyle="round,pad=0.015,rounding_size=0.07",
                        facecolor=fill_c,
                        edgecolor=border_c,
                        linewidth=1.1,
                    )
                    ax.add_patch(box)
                    val_txt = _format_cell_value(val, prefix=spec.value_prefix, suffix=spec.value_suffix)
                    ax.text(
                        x_left + tile_w / 2.0,
                        y_bottom + tile_h / 2.0,
                        val_txt,
                        color=txt_c,
                        fontsize=9.5,
                        fontweight="bold",
                        ha="center",
                        va="center",
                    )

        summary_lines: List[str] = []
        if all_vals:
            summary_lines.append(
                f"**Matrix Range ({len(all_vals)} cells):** Min `{all_vals[0]:,.2f}` | "
                f"Median `{_percentile(all_vals, 0.5):,.2f}` | Max `{all_vals[-1]:,.2f}`"
            )

        fig.tight_layout(pad=1.4)
        return RenderedVisual(
            filename=_slugify_filename(spec.title, default="heatmap"),
            png_bytes=_figure_to_png_bytes(fig),
            title=spec.title or "Heatmap Matrix",
            summary_lines=summary_lines,
            renderer_used="matplotlib_heatmap",
        )

