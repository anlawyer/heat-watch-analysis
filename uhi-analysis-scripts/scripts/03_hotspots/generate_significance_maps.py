#!/usr/bin/env python3
"""
generate_significance_maps.py - Generate presentation-grade 16:9 Statistical Significance
and Confidence Tier maps for the NOVA Heat Watch campaign.

Produces:
1. Flagship 16:9 Single Map: Afternoon Peak Heat Statistical Significance & Confidence Tiers
   (output/figures/hotspots/map_statistical_significance_af.png)
2. 3-Panel Diurnal 16:9 Map: Diurnal Statistical Significance Tiers (AM, AF, PM)
   (output/figures/hotspots/map_statistical_significance_diurnal.png)
3. Flagship 16:9 Continuous z-score Map: Afternoon Getis-Ord Gi* Clustering Intensity
   (output/figures/hotspots/map_zscore_intensity_af.png)
"""

import sys
from pathlib import Path

def get_project_root() -> Path:
    """Locate repository root by searching upward for input_data and uhi directories."""
    p = Path(__file__).resolve()
    for parent in [p] + list(p.parents):
        if (parent / "input_data").is_dir() and (parent / "uhi").is_dir():
            return parent
    return p.parent

PROJECT_ROOT = get_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import argparse
from pathlib import Path
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.colors as mcolors
from matplotlib.colors import ListedColormap, TwoSlopeNorm
import numpy as np
from shapely.geometry import box

from uhi.hotspot_plots import read_boundary

# Design & typography tokens from GEMINI.md
SLIDE_FIGSIZE = (13.333, 7.5)
DPI = 300
FONT_FAMILY = "Figtree"
FALLBACK_FONTS = ["Figtree", "DejaVu Sans", "Arial", "sans-serif"]
plt.rcParams["font.family"] = FALLBACK_FONTS

COLOR_TEXT_DARK = "#1F2937"
COLOR_TEXT_MUTED = "#7F8C8D"
COLOR_LEGEND_BG = "#F8FAFC"
COLOR_BORDER = "#4B5563"
COLOR_DIVIDER = "#E2E8F0"

# Standard 7-tier Gi* confidence palette
COLOR_HOT_99 = "#B91C1C"  # Dark Crimson (99% confidence)
COLOR_HOT_95 = "#EF4444"  # Strong Coral Red (95% confidence)
COLOR_HOT_90 = "#FCA5A5"  # Soft Rose Red (90% confidence)
COLOR_NS = "#E5E7EB"      # Neutral Muted Gray (Not significant)
COLOR_COLD_90 = "#93C5FD" # Soft Sky Blue (90% confidence)
COLOR_COLD_95 = "#3B82F6" # Strong Primary Blue (95% confidence)
COLOR_COLD_99 = "#1D4ED8" # Deep Navy Blue (99% confidence)

TIER_PALETTE = [
    "#FFFFFF",       # 0: Outside ROI
    COLOR_COLD_99,   # 1: Cold 99% (q<=0.01, z<=-2.58)
    COLOR_COLD_95,   # 2: Cold 95% (q<=0.05, z<=-1.96)
    COLOR_COLD_90,   # 3: Cold 90% (q<=0.10, z<=-1.65)
    COLOR_NS,        # 4: Not Significant
    COLOR_HOT_90,    # 5: Hot 90% (q<=0.10, z>=1.65)
    COLOR_HOT_95,    # 6: Hot 95% (q<=0.05, z>=1.96)
    COLOR_HOT_99,    # 7: Hot 99% (q<=0.01, z>=2.58)
]


def study_area_mask(df, periods):
    """Cells inside the study area: canopy-coverage cells when that column exists,
    otherwise cells with a finite Gi* value in any of the given periods."""
    if "coverage_frac" in df.columns:
        return df["coverage_frac"].values > 0
    return np.isfinite(df[[f"gi_z_{p}" for p in periods]].to_numpy(dtype=float)).any(axis=1)


def classify_confidence_tiers(df, period: str):
    """Classify cells into 7 standard Getis-Ord Gi* confidence tiers."""
    z = df[f"gi_z_{period}"].values
    q = df[f"fdr_q_{period}"].values
    inside = study_area_mask(df, [period])
    valid = np.isfinite(z) & inside

    cat = np.zeros(len(df), dtype=int)
    cat[inside] = 4  # Default Not Significant

    hot_90 = valid & (z >= 1.645) & (q <= 0.10)
    hot_95 = valid & (z >= 1.960) & (q <= 0.05)
    hot_99 = valid & (z >= 2.576) & (q <= 0.01)

    cat[hot_90] = 5
    cat[hot_95] = 6
    cat[hot_99] = 7

    cold_90 = valid & (z <= -1.645) & (q <= 0.10)
    cold_95 = valid & (z <= -1.960) & (q <= 0.05)
    cold_99 = valid & (z <= -2.576) & (q <= 0.01)

    cat[cold_90] = 3
    cat[cold_95] = 2
    cat[cold_99] = 1

    return cat


def plot_single_significance_map(df, b_gdf, period="af", output_path=None):
    """Create flagship single map with right-hand key card."""
    nrows = int(df["row_index"].max() - df["row_index"].min() + 1)
    ncols = int(df["col_index"].max() - df["col_index"].min() + 1)

    xmin = float(df["left"].min())
    xmax = float(df["right"].max())
    ymin = float(df["bottom"].min())
    ymax = float(df["top"].max())
    extent = [xmin, xmax, ymin, ymax]

    cat_1d = classify_confidence_tiers(df, period)
    cat_grid = np.zeros((nrows, ncols), dtype=int)
    cat_grid[df["row_index"].values, df["col_index"].values] = cat_1d

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=DPI, facecolor="#FFFFFF")

    period_title = "Afternoon Peak Heat" if period == "af" else ("Morning" if period == "am" else "Evening")
    fig.text(
        0.055,
        0.950,
        f"Statistical Significance of Hot & Cold Spots ({period_title})",
        ha="left",
        va="top",
        fontsize=18,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )
    fig.text(
        0.055,
        0.912,
        "Getis-Ord Gi* spatial clustering confidence tiers with Benjamini–Hochberg False Discovery Rate (FDR) control",
        ha="left",
        va="top",
        fontsize=11.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    ax = fig.add_axes([0.055, 0.06, 0.58, 0.81])
    cmap = ListedColormap(TIER_PALETTE)

    ax.imshow(
        cat_grid,
        extent=extent,
        cmap=cmap,
        vmin=0,
        vmax=7,
        origin="upper",
        interpolation="nearest",
        zorder=2,
    )

    if b_gdf is not None:
        b_gdf.boundary.plot(ax=ax, color=COLOR_BORDER, linewidth=0.85, zorder=3)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    card_ax = fig.add_axes([0.655, 0.06, 0.305, 0.81])
    card_ax.set_facecolor(COLOR_LEGEND_BG)
    for spine in card_ax.spines.values():
        spine.set_visible(False)
    card_ax.set_xticks([])
    card_ax.set_yticks([])
    card_ax.set_xlim(0, 1)
    card_ax.set_ylim(0, 1)

    rect = patches.FancyBboxPatch(
        (0.0, 0.0),
        1.0,
        1.0,
        boxstyle="round,pad=0.0,rounding_size=0.03",
        facecolor=COLOR_LEGEND_BG,
        edgecolor="none",
        transform=card_ax.transAxes,
        clip_on=False,
        zorder=-1,
    )
    card_ax.add_patch(rect)

    card_ax.text(
        0.07,
        0.955,
        "STATISTICAL CONFIDENCE KEY",
        fontsize=13.0,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
        va="top",
    )
    card_ax.text(
        0.07,
        0.925,
        "Significance of local spatial clustering relative to region",
        fontsize=9.0,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
        va="top",
    )

    inside = study_area_mask(df, [period])
    cat_inside = cat_1d[inside]
    n_inside = len(cat_inside)

    def get_tier_stat(val):
        c = int(np.sum(cat_inside == val))
        return c * 0.01, c / n_inside * 100.0

    km_h99, pct_h99 = get_tier_stat(7)
    km_h95, pct_h95 = get_tier_stat(6)
    km_h90, pct_h90 = get_tier_stat(5)
    km_ns, pct_ns = get_tier_stat(4)
    km_c90, pct_c90 = get_tier_stat(3)
    km_c95, pct_c95 = get_tier_stat(2)
    km_c99, pct_c99 = get_tier_stat(1)

    def draw_swatch_row(y_center, color, title, subtitle):
        sw_w, sw_h = 0.065, 0.036
        sw_x, sw_y = 0.07, y_center - sw_h / 2.0
        p = patches.FancyBboxPatch(
            (sw_x, sw_y),
            sw_w,
            sw_h,
            boxstyle="round,pad=0.004,rounding_size=0.008",
            facecolor=color,
            edgecolor="#9CA3AF",
            linewidth=0.8,
            transform=card_ax.transAxes,
            zorder=3,
        )
        card_ax.add_patch(p)
        card_ax.text(
            sw_x + sw_w + 0.045,
            y_center + 0.012,
            title,
            fontsize=10.0,
            weight="bold",
            fontfamily=FONT_FAMILY,
            color=COLOR_TEXT_DARK,
            va="center",
        )
        card_ax.text(
            sw_x + sw_w + 0.045,
            y_center - 0.012,
            subtitle,
            fontsize=8.5,
            fontfamily=FONT_FAMILY,
            color="#4B5563",
            va="center",
        )

    card_ax.text(
        0.07,
        0.880,
        "STATISTICALLY SIGNIFICANT HOT SPOTS",
        fontsize=10.0,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_HOT_99,
        va="top",
    )
    draw_swatch_row(0.825, COLOR_HOT_99, "Hot Spot — 99% Confidence", f"q ≤ 0.01, z ≥ +2.58 • {km_h99:.0f} km² ({pct_h99:.1f}%)")
    draw_swatch_row(0.745, COLOR_HOT_95, "Hot Spot — 95% Confidence", f"q ≤ 0.05, z ≥ +1.96 • {km_h95:.0f} km² ({pct_h95:.1f}%)")
    draw_swatch_row(0.665, COLOR_HOT_90, "Hot Spot — 90% Confidence", f"q ≤ 0.10, z ≥ +1.65 • {km_h90:.0f} km² ({pct_h90:.1f}%)")

    card_ax.text(
        0.07,
        0.595,
        "SPATIAL RANDOMNESS / NEAR AVERAGE",
        fontsize=10.0,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color="#4B5563",
        va="top",
    )
    draw_swatch_row(0.540, COLOR_NS, "Not Statistically Significant", f"q > 0.10 or |z| < 1.65 • {km_ns:.0f} km² ({pct_ns:.1f}%)")

    card_ax.text(
        0.07,
        0.470,
        "STATISTICALLY SIGNIFICANT COLD SPOTS",
        fontsize=10.0,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_COLD_99,
        va="top",
    )
    draw_swatch_row(0.415, COLOR_COLD_90, "Cold Spot — 90% Confidence", f"q ≤ 0.10, z ≤ -1.65 • {km_c90:.0f} km² ({pct_c90:.1f}%)")
    draw_swatch_row(0.335, COLOR_COLD_95, "Cold Spot — 95% Confidence", f"q ≤ 0.05, z ≤ -1.96 • {km_c95:.0f} km² ({pct_c95:.1f}%)")
    draw_swatch_row(0.255, COLOR_COLD_99, "Cold Spot — 99% Confidence", f"q ≤ 0.01, z ≤ -2.58 • {km_c99:.0f} km² ({pct_c99:.1f}%)")

    card_ax.plot([0.07, 0.93], [0.190, 0.190], color=COLOR_DIVIDER, linewidth=1.0, transform=card_ax.transAxes)

    card_ax.text(
        0.07,
        0.170,
        "FDR MULTIPLE TESTING CONTROL",
        fontsize=10.0,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
        va="top",
    )
    n_tested = int(np.isfinite(df[f"gi_z_{period}"].to_numpy(dtype=float)).sum())
    method_text = (
        "• Benjamini–Hochberg FDR adjusts p-values across all\n"
        f"  {n_tested:,} cells, capping false discovery rate at 5%.\n"
        "• Neighborhood: 3×3 Queen contiguity (300m window).\n"
        "• Prevents random background fluctuations from being\n"
        "  falsely classified as meaningful heat clusters."
    )
    card_ax.text(
        0.07,
        0.140,
        method_text,
        fontsize=8.8,
        fontfamily=FONT_FAMILY,
        color="#4B5563",
        va="top",
        linespacing=1.35,
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=DPI, facecolor="#FFFFFF")
        plt.close(fig)
        print(f"Saved: {out}")

    return fig, ax


def plot_diurnal_significance_panel(df, b_gdf, output_path=None):
    """Create 3-panel diurnal statistical significance map (AM, AF, PM) with spacious breathing room."""
    nrows = int(df["row_index"].max() - df["row_index"].min() + 1)
    ncols = int(df["col_index"].max() - df["col_index"].min() + 1)

    xmin = float(df["left"].min())
    xmax = float(df["right"].max())
    ymin = float(df["bottom"].min())
    ymax = float(df["top"].max())
    extent = [xmin, xmax, ymin, ymax]

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=DPI, facecolor="#FFFFFF")

    # Main Title & Subtitle
    fig.text(
        0.055,
        0.950,
        "Statistical Significance of Hot and Cold Spots Across Collection Times",
        ha="left",
        va="top",
        fontsize=18,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )
    fig.text(
        0.055,
        0.912,
        "Morning, Afternoon, and Evening Getis-Ord Gi* spatial clustering confidence tiers (FDR-controlled)",
        ha="left",
        va="top",
        fontsize=11.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    # Bespoke Top Legend Bar placed with ample margin
    legend_ax = fig.add_axes([0.14, 0.810, 0.72, 0.050])
    legend_ax.set_facecolor(COLOR_LEGEND_BG)
    for s in legend_ax.spines.values():
        s.set_visible(False)
    legend_ax.set_xticks([])
    legend_ax.set_yticks([])
    legend_ax.set_xlim(0, 1)
    legend_ax.set_ylim(0, 1)

    leg_patch = patches.FancyBboxPatch(
        (0.0, 0.0), 1.0, 1.0,
        boxstyle="round,pad=0.0,rounding_size=0.15",
        facecolor=COLOR_LEGEND_BG, edgecolor="none",
        transform=legend_ax.transAxes, clip_on=False, zorder=-1
    )
    legend_ax.add_patch(leg_patch)

    legend_items = [
        (0.07, COLOR_HOT_99, "Hot (99%)"),
        (0.21, COLOR_HOT_95, "Hot (95%)"),
        (0.35, COLOR_HOT_90, "Hot (90%)"),
        (0.49, COLOR_NS, "Not Sig."),
        (0.63, COLOR_COLD_90, "Cold (90%)"),
        (0.77, COLOR_COLD_95, "Cold (95%)"),
        (0.91, COLOR_COLD_99, "Cold (99%)"),
    ]
    for x_c, col, lbl in legend_items:
        sw_w, sw_h = 0.022, 0.40
        p = patches.FancyBboxPatch(
            (x_c - sw_w - 0.005, 0.5 - sw_h / 2.0), sw_w, sw_h,
            boxstyle="round,pad=0.001,rounding_size=0.08",
            facecolor=col, edgecolor="#9CA3AF", linewidth=0.6,
            transform=legend_ax.transAxes, zorder=2
        )
        legend_ax.add_patch(p)
        legend_ax.text(x_c + 0.005, 0.5, lbl, fontsize=9.2, weight="bold", color=COLOR_TEXT_DARK, va="center", ha="left")

    cmap = ListedColormap(TIER_PALETTE)
    periods = [("am", "Morning (AM)"), ("af", "Afternoon (AF)"), ("pm", "Evening (PM)")]

    # 3 Panels positioned side-by-side with lower height to avoid clipping header
    panel_lefts = [0.055, 0.370, 0.685]
    panel_width = 0.260
    panel_bottom = 0.05
    panel_height = 0.63

    inside = study_area_mask(df, [p_key for p_key, _ in periods])

    for i, (p_key, p_label) in enumerate(periods):
        ax = fig.add_axes([panel_lefts[i], panel_bottom, panel_width, panel_height])

        cat_1d = classify_confidence_tiers(df, p_key)
        cat_grid = np.zeros((nrows, ncols), dtype=int)
        cat_grid[df["row_index"].values, df["col_index"].values] = cat_1d

        ax.imshow(
            cat_grid,
            extent=extent,
            cmap=cmap,
            vmin=0,
            vmax=7,
            origin="upper",
            interpolation="nearest",
            zorder=2,
        )

        if b_gdf is not None:
            b_gdf.boundary.plot(ax=ax, color=COLOR_BORDER, linewidth=0.75, zorder=3)

        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

        cat_roi = cat_1d[inside]
        hot_total = np.sum((cat_roi >= 5) & (cat_roi <= 7))
        cold_total = np.sum((cat_roi >= 1) & (cat_roi <= 3))
        n_roi = len(cat_roi)

        ax.set_title(
            f"{p_label}\nHot: {hot_total/n_roi*100:.1f}% ({hot_total*0.01:.0f} km²) • Cold: {cold_total/n_roi*100:.1f}% ({cold_total*0.01:.0f} km²)",
            fontsize=11.0,
            weight="bold",
            color=COLOR_TEXT_DARK,
            fontfamily=FONT_FAMILY,
            pad=10,
            linespacing=1.35,
        )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=DPI, facecolor="#FFFFFF")
        plt.close(fig)
        print(f"Saved: {out}")

    return fig


def plot_continuous_zscore_map(df, b_gdf, period="af", output_path=None):
    """Create continuous Getis-Ord Gi* z-score clustering intensity map with diverging scale."""
    nrows = int(df["row_index"].max() - df["row_index"].min() + 1)
    ncols = int(df["col_index"].max() - df["col_index"].min() + 1)

    xmin = float(df["left"].min())
    xmax = float(df["right"].max())
    ymin = float(df["bottom"].min())
    ymax = float(df["top"].max())
    extent = [xmin, xmax, ymin, ymax]

    inside = study_area_mask(df, [period])
    z_raw = df[f"gi_z_{period}"].values

    z_grid = np.full((nrows, ncols), np.nan, dtype=np.float32)
    z_grid[df.loc[inside, "row_index"].values, df.loc[inside, "col_index"].values] = z_raw[inside]

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=DPI, facecolor="#FFFFFF")

    period_title = "Afternoon Peak Heat" if period == "af" else ("Morning" if period == "am" else "Evening")
    fig.text(
        0.080,
        0.945,
        f"Getis-Ord Gi* Spatial Clustering Intensity ({period_title})",
        ha="left",
        va="top",
        fontsize=18,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )
    fig.text(
        0.080,
        0.908,
        "Continuous standard normal z-scores measuring local spatial clustering relative to regional average",
        ha="left",
        va="top",
        fontsize=11.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    ax = fig.add_axes([0.08, 0.07, 0.66, 0.79])

    # Colormap: Diverging Cool-to-Warm (RdBu_r)
    # Symmetrical limits at +/- 8.0 for clear gradient visualization
    z_lim = 8.0
    norm = TwoSlopeNorm(vmin=-z_lim, vcenter=0.0, vmax=z_lim)
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad(color="#FFFFFF", alpha=0.0)

    im = ax.imshow(
        z_grid,
        extent=extent,
        cmap=cmap,
        norm=norm,
        origin="upper",
        interpolation="bilinear",
        zorder=2,
    )

    if b_gdf is not None:
        b_gdf.boundary.plot(ax=ax, color=COLOR_BORDER, linewidth=0.85, zorder=3)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Vertical colorbar on the right
    cbar_ax = fig.add_axes([0.77, 0.16, 0.022, 0.62])
    cb = fig.colorbar(im, cax=cbar_ax, orientation="vertical", extend="both")
    cb.set_ticks([-8, -6, -4, -1.96, 0, 1.96, 4, 6, 8])
    cb.set_ticklabels([
        "-8 (Extreme Cool)",
        "-6",
        "-4",
        "-1.96 (p = 0.05)",
        "0 (Expected)",
        "+1.96 (p = 0.05)",
        "+4",
        "+6",
        "+8 (Extreme Heat)",
    ])
    cb.ax.tick_params(labelsize=10.0, colors=COLOR_TEXT_DARK, length=4, width=0.8, pad=6)
    cb.outline.set_edgecolor("#9CA3AF")
    cb.outline.set_linewidth(0.8)

    # Subtle horizontal dashed lines across the colorbar at statistical significance thresholds (+/- 1.96)
    cbar_ax.axhline(1.96, color="#4B5563", linestyle="--", linewidth=0.9, zorder=10)
    cbar_ax.axhline(-1.96, color="#4B5563", linestyle="--", linewidth=0.9, zorder=10)

    cb.set_label(
        "Getis-Ord Gi* z-score",
        fontsize=13,
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
        labelpad=14,
    )

    # Bottom caption
    fig.text(
        0.50,
        0.030,
        "Notes: Values with |z| ≥ 1.96 indicate statistically significant local clustering at p ≤ 0.05 (FDR-controlled). Red = Heat Clusters, Blue = Cool Refuges.",
        ha="center",
        va="bottom",
        fontsize=10.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=DPI, facecolor="#FFFFFF")
        plt.close(fig)
        print(f"Saved: {out}")

    return fig, ax


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Statistical significance and z-score maps from make_hotspot_analysis.py output."
    )
    parser.add_argument(
        "--hotspots",
        type=Path,
        default=Path("output/hotspots/hotspots.gpkg"),
        help="Enriched GeoPackage from make_hotspot_analysis.py (default: output/hotspots/hotspots.gpkg)",
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        default=Path("input_data/county_boundaries/novauhi_counties.gpkg"),
        help="Study-area outline in any CRS, drawn on the maps and used to mask them",
    )
    parser.add_argument(
        "--no-boundary",
        action="store_true",
        help="Draw the maps without a study-area outline",
    )
    parser.add_argument(
        "--period",
        choices=["am", "af", "pm"],
        default="af",
        help="Period for the single-period maps (default: af)",
    )
    parser.add_argument(
        "--figures-dir",
        type=Path,
        default=Path("output/figures/hotspots"),
        help="Directory for figures (default: output/figures/hotspots)",
    )
    args = parser.parse_args()

    if not args.hotspots.is_file():
        print(f"Error: hotspots file not found: {args.hotspots}")
        sys.exit(1)

    # Read with geometry so the grid CRS is known for reprojecting the boundary
    df = gpd.read_file(args.hotspots)
    b_gdf = None
    if not args.no_boundary:
        if not args.boundary.is_file():
            print(f"Error: boundary not found: {args.boundary} (use --no-boundary to run without one)")
            sys.exit(1)
        b_gdf = read_boundary(args.boundary, df.crs)
        if not b_gdf.intersects(box(*df.total_bounds)).any():
            print(f"Error: boundary {args.boundary} does not overlap the grid. Pass the boundary for this study area or use --no-boundary.")
            sys.exit(1)

    figures_dir = args.figures_dir

    # 1. Flagship confidence tier map: Afternoon Peak Heat
    out1 = figures_dir / f"map_statistical_significance_{args.period}.png"
    plot_single_significance_map(df, b_gdf, period=args.period, output_path=out1)

    # 2. 3-Panel diurnal map: Statistical significance across AM, AF, PM (needs all three periods)
    if all(f"gi_z_{p}" in df.columns for p in ("am", "af", "pm")):
        out2 = figures_dir / "map_statistical_significance_diurnal.png"
        plot_diurnal_significance_panel(df, b_gdf, output_path=out2)
    else:
        print("Skipping the diurnal panel: AM, AF and PM are not all present.")

    # 3. Continuous z-score clustering intensity map
    out3 = figures_dir / f"map_zscore_intensity_{args.period}.png"
    plot_continuous_zscore_map(df, b_gdf, period=args.period, output_path=out3)
