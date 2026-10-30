"""
uhi/hotspot_plots.py - Visualizations for hot and cold spot analysis.

Follows strict presentation design standards:
- 16:9 widescreen presentation layout (figsize=(13.333, 7.5), dpi=300).
- Pure white background (#FFFFFF).
- Typography: Figtree font family across all titles, axes, annotations, and legends.
- Color standards:
  - Morning (AM): #2980B9
  - Afternoon (AF): #F39C12
  - Evening (PM): #E74C3C
  - Hot Spot: #E74C3C (Red)
  - Cold Spot: #2980B9 (Blue)
  - Not Significant: #E5E7EB (Light Neutral Gray)
  - Background/Baseline: #BDC3C7
  - Legend card fill: #ECF0F1 (with rounded corners)
  - Primary text: #1F2937
  - Muted text: #7F8C8D
- Tufte-style de-cluttering (frameless axes, no outer spines, edgeless bars, color-matched annotations).
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import logging

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
import pandas as pd
import scipy.stats
from matplotlib.colors import ListedColormap

from uhi.hotspots import PERIODS, PERIOD_LABELS, CELL_AREA_KM2, from_grid_2d, to_grid_2d

logger = logging.getLogger(__name__)

SLIDE_FIGSIZE: Tuple[float, float] = (13.333, 7.5)
DEFAULT_DPI: int = 300
FONT_FAMILY: str = "Figtree"

# Project Color Palette
COLOR_AM: str = "#2980B9"
COLOR_AF: str = "#F39C12"
COLOR_PM: str = "#E74C3C"

COLOR_HOT: str = "#E74C3C"         # Red for Hot Spot
COLOR_HOT_LIGHT: str = "#FADBD8"
COLOR_HOT_MID: str = "#F1948A"

COLOR_COLD: str = "#2980B9"        # Blue for Cold Spot
COLOR_COLD_LIGHT: str = "#D4E6F1"
COLOR_COLD_MID: str = "#85C1E9"

COLOR_NS: str = "#E5E7EB"          # Neutral light gray for Not Significant
COLOR_BORDER: str = "#9CA3AF"      # Subtle boundary outline
COLOR_TEXT_DARK: str = "#1F2937"
COLOR_TEXT_MUTED: str = "#7F8C8D"
COLOR_BASELINE: str = "#BDC3C7"
COLOR_LEGEND_BG: str = "#ECF0F1"


def configure_matplotlib_fonts() -> None:
    """Set Figtree as the primary font family with clean sans-serif fallbacks."""
    plt.rcParams["font.family"] = [FONT_FAMILY, "DejaVu Sans", "Arial", "sans-serif"]


def read_boundary(
    boundary_path: Optional[Union[str, Path]],
    grid_crs=None,
) -> Optional[gpd.GeoDataFrame]:
    """
    Read a study-area outline, reprojected to the grid's CRS so that its coordinates
    match the grid. Returns None if no path is given or the file does not exist.
    """
    if not boundary_path or not Path(boundary_path).is_file():
        return None
    boundary = gpd.read_file(boundary_path)
    if grid_crs is not None and boundary.crs != grid_crs:
        boundary = boundary.to_crs(grid_crs)
    return boundary


def plot_map_spots_by_period(
    df: pd.DataFrame,
    boundary_path: Optional[Union[str, Path]] = None,
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = DEFAULT_DPI,
    region_name: str = "the study area",
    variable: str = "Air Temperature",
) -> Tuple[plt.Figure, List[plt.Axes]]:
    """
    Generate a 3-panel widescreen map comparing Hot Spots, Cold Spots,
    and Not Significant areas across Morning (AM), Afternoon (AF), and Evening (PM).
    """
    configure_matplotlib_fonts()

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=dpi, facecolor="#FFFFFF")

    # Layout for 3 side-by-side maps:
    panel_w = 0.275
    panel_gap = 0.040
    left_start = 0.055
    bottom = 0.06
    height = 0.70

    # Top title
    fig.text(
        0.055,
        0.950,
        f"{variable} Hot and Cool Spots by Time of Day",
        ha="left",
        va="top",
        fontsize=16,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )

    # Subtitle with simplified language
    fig.text(
        0.055,
        0.915,
        f"Morning, Afternoon, and Evening heat clusters and cool zones across {region_name}",
        ha="left",
        va="top",
        fontsize=11.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    # Global legend centered horizontally below subtitle with dedicated clearance
    legend_handles = [
        patches.Patch(facecolor=COLOR_HOT, edgecolor="none", label="Hot Spot (Warmer than surrounding area)"),
        patches.Patch(facecolor=COLOR_COLD, edgecolor="none", label="Cool Spot (Cooler than surrounding area)"),
        patches.Patch(facecolor=COLOR_NS, edgecolor="none", label="Typical / Near Average"),
    ]
    leg = fig.legend(
        handles=legend_handles,
        ncols=3,
        loc="upper center",
        bbox_to_anchor=(0.50, 0.865),
        frameon=True,
        facecolor=COLOR_LEGEND_BG,
        edgecolor="none",
        handlelength=1.0,
        handleheight=1.0,
        borderpad=0.5,
        columnspacing=2.0,
        handletextpad=0.5,
        prop={"family": FONT_FAMILY, "weight": "bold", "size": 10.5},
    )
    leg.get_frame().set_boxstyle("round,pad=0.45,rounding_size=0.3")

    # Load boundaries and derive inside-ROI mask if available
    boundary_gdf = None
    inside_mask = None
    if boundary_path and Path(boundary_path).is_file():
        try:
            boundary_gdf = read_boundary(boundary_path, getattr(df, "crs", None))
            union_poly = boundary_gdf.union_all()
            centroids = gpd.GeoSeries(
                gpd.points_from_xy((df["left"] + df["right"]) / 2, (df["bottom"] + df["top"]) / 2),
                crs=boundary_gdf.crs,
            )
            inside_mask = to_grid_2d(centroids.within(union_poly).values, df, fill=False)
        except Exception as e:
            logger.warning("Could not read boundary file: %s", e)

    # Grid extent from coordinates in df
    xmin = df["left"].min()
    xmax = df["right"].max()
    ymin = df["bottom"].min()
    ymax = df["top"].max()
    extent = [xmin, xmax, ymin, ymax]

    # Discrete colormap: 0=No Data / Outside ROI (white), 1=Not Significant (light gray), 2=Cold Spot (blue), 3=Hot Spot (red)
    cmap = ListedColormap(["#FFFFFF", COLOR_NS, COLOR_COLD, COLOR_HOT])

    axes: List[plt.Axes] = []

    for i, p in enumerate(PERIODS):
        ax_left = left_start + i * (panel_w + panel_gap)
        ax = fig.add_axes([ax_left, bottom, panel_w, height])
        axes.append(ax)

        # Build 2D categorical raster
        # 0: No data / NaN / Outside ROI, 1: NS, 2: Cold, 3: Hot
        spot_vals = to_grid_2d(df[f"spot_{p}"].values, df, fill="No Data")
        grid_cat = np.zeros(spot_vals.shape, dtype=int)
        grid_cat[spot_vals == "Not Significant"] = 1
        grid_cat[spot_vals == "Cold Spot"] = 2
        grid_cat[spot_vals == "Hot Spot"] = 3

        # Mask cells outside the region of interest
        if inside_mask is not None:
            grid_cat[~inside_mask] = 0
            spot_vals_eval = spot_vals[inside_mask]
        else:
            spot_vals_eval = spot_vals

        # Count stats for panel subtitle (within ROI)
        n_hot = int(np.sum(spot_vals_eval == "Hot Spot"))
        n_cold = int(np.sum(spot_vals_eval == "Cold Spot"))
        n_valid = int(np.sum(spot_vals_eval != "No Data"))
        pct_hot = (n_hot / n_valid * 100.0) if n_valid > 0 else 0.0
        pct_cold = (n_cold / n_valid * 100.0) if n_valid > 0 else 0.0

        # Render raster
        ax.imshow(
            grid_cat,
            extent=extent,
            cmap=cmap,
            vmin=0,
            vmax=3,
            origin="upper",
            interpolation="nearest",
            zorder=2,
        )

        # Plot boundary overlay
        if boundary_gdf is not None:
            boundary_gdf.boundary.plot(ax=ax, color=COLOR_BORDER, linewidth=0.75, zorder=3)

        # Panel Title with Period and Stats
        ax.set_title(
            f"{PERIOD_LABELS[p]}\nHot: {pct_hot:.1f}% ({n_hot * CELL_AREA_KM2:.0f} km²)  •  Cool: {pct_cold:.1f}% ({n_cold * CELL_AREA_KM2:.0f} km²)",
            fontsize=12,
            weight="bold",
            color=COLOR_TEXT_DARK,
            fontfamily=FONT_FAMILY,
            pad=8,
        )

        # De-clutter: frameless axes, no ticks
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_axis_off()
        for spine in ax.spines.values():
            spine.set_visible(False)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, facecolor="#FFFFFF")
        plt.close(fig)
        logger.info("Saved: %s", out)

    return fig, axes


def plot_map_persistence(
    df: pd.DataFrame,
    boundary_path: Optional[Union[str, Path]] = None,
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = DEFAULT_DPI,
    region_name: str = "the study area",
    variable: str = "Air Temperature",
) -> Tuple[plt.Figure, plt.Axes]:
    """
    Generate a 16:9 widescreen map of diurnal persistence, highlighting locations
    that are persistent hot spots (hot at all 3 times) and persistent cold spots.
    """
    configure_matplotlib_fonts()

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=dpi, facecolor="#FFFFFF")

    # Main Title & Subtitle with simplified language
    fig.text(
        0.055,
        0.950,
        f"{variable} Hot and Cool Spots",
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
        f"Areas across {region_name} that consistently stay warmer or cooler than surrounding communities throughout the day",
        ha="left",
        va="top",
        fontsize=11.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    # Map area positioned with dedicated margins
    ax = fig.add_axes([0.055, 0.06, 0.58, 0.81])

    # Categories and colors:
    # 0: Background / Outside ROI -> #FFFFFF
    # 1: Not Significant -> #E5E7EB
    # 2: Mixed Shift -> #CBD5E1
    # 3: Cold Spot (1 Time) -> COLOR_COLD_LIGHT (#D4E6F1)
    # 4: Cold Spot (2 Times) -> COLOR_COLD_MID (#85C1E9)
    # 5: Cold Spot (All 3 Times) -> COLOR_COLD (#2980B9)
    # 6: Hot Spot (1 Time) -> COLOR_HOT_LIGHT (#FADBD8)
    # 7: Hot Spot (2 Times) -> COLOR_HOT_MID (#F1948A)
    # 8: Hot Spot (All 3 Times) -> COLOR_HOT (#E74C3C)
    palette = [
        "#FFFFFF",          # 0
        COLOR_NS,           # 1
        "#CBD5E1",          # 2
        COLOR_COLD_LIGHT,   # 3
        COLOR_COLD_MID,     # 4
        COLOR_COLD,         # 5
        COLOR_HOT_LIGHT,    # 6
        COLOR_HOT_MID,      # 7
        COLOR_HOT,          # 8
    ]
    cmap = ListedColormap(palette)

    pers_vals = to_grid_2d(df["persistence"].values, df, fill="No Data")
    grid_cat = np.zeros(pers_vals.shape, dtype=int)

    grid_cat[pers_vals == "Not Significant"] = 1
    grid_cat[pers_vals == "Mixed Shift"] = 2
    grid_cat[pers_vals == "Cold Spot (1 Time)"] = 3
    grid_cat[pers_vals == "Cold Spot (2 Times)"] = 4
    grid_cat[pers_vals == "Cold Spot (All 3 Times)"] = 5
    grid_cat[pers_vals == "Hot Spot (1 Time)"] = 6
    grid_cat[pers_vals == "Hot Spot (2 Times)"] = 7
    grid_cat[pers_vals == "Hot Spot (All 3 Times)"] = 8

    # Load boundary outline and compute inside-ROI mask
    inside_mask = None
    b_gdf = None
    if boundary_path and Path(boundary_path).is_file():
        try:
            b_gdf = read_boundary(boundary_path, getattr(df, "crs", None))
            union_poly = b_gdf.union_all()
            centroids = gpd.GeoSeries(
                gpd.points_from_xy((df["left"] + df["right"]) / 2, (df["bottom"] + df["top"]) / 2),
                crs=b_gdf.crs,
            )
            inside_mask = to_grid_2d(centroids.within(union_poly).values, df, fill=False)
        except Exception as e:
            logger.warning("Could not read boundary file: %s", e)

    # Mask cells outside the region of interest
    if inside_mask is not None:
        grid_cat[~inside_mask] = 0

    xmin = df["left"].min()
    xmax = df["right"].max()
    ymin = df["bottom"].min()
    ymax = df["top"].max()
    extent = [xmin, xmax, ymin, ymax]

    ax.imshow(
        grid_cat,
        extent=extent,
        cmap=cmap,
        vmin=0,
        vmax=8,
        origin="upper",
        interpolation="nearest",
        zorder=2,
    )

    # Boundary outline
    if b_gdf is not None:
        b_gdf.boundary.plot(ax=ax, color=COLOR_BORDER, linewidth=0.8, zorder=3)

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_axis_off()
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Compute key stats for card (within ROI)
    if inside_mask is not None:
        inside_1d = from_grid_2d(inside_mask, df)
        df_roi = df[inside_1d]
        pers_vals_roi = pers_vals[inside_mask]
    else:
        df_roi = df
        pers_vals_roi = pers_vals

    total_valid = int(np.sum(df_roi["persistence"] != "No Data"))
    n_hot_all = int(np.sum(df_roi["hot_all3"]))
    n_hot_2 = int(np.sum(pers_vals_roi == "Hot Spot (2 Times)"))
    n_hot_1 = int(np.sum(pers_vals_roi == "Hot Spot (1 Time)"))
    n_cold_all = int(np.sum(df_roi["cold_all3"]))
    n_cold_2 = int(np.sum(pers_vals_roi == "Cold Spot (2 Times)"))
    n_cold_1 = int(np.sum(pers_vals_roi == "Cold Spot (1 Time)"))

    # Right side: Color Key Card
    card_ax = fig.add_axes([0.655, 0.06, 0.305, 0.81])
    card_ax.set_facecolor(COLOR_LEGEND_BG)
    for spine in card_ax.spines.values():
        spine.set_visible(False)
    card_ax.set_xticks([])
    card_ax.set_yticks([])
    card_ax.set_xlim(0, 1)
    card_ax.set_ylim(0, 1)

    # Card rounded border
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

    # Card Title
    card_ax.text(
        0.07,
        0.955,
        "COLOR KEY",
        fontsize=13.5,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
        va="top",
    )
    # card_ax.text(
    #     0.07,
    #     0.925,
    #     "Darker shades -> heat/cool persists across the day",
    #     fontsize=9.5,
    #     style="italic",
    #     fontfamily=FONT_FAMILY,
    #     color=COLOR_TEXT_MUTED,
    #     va="top",
    # )

    def draw_swatch_entry(ax_target, y_center, color, title, subtitle, border_color="#9CA3AF"):
        swatch_w = 0.070
        swatch_h = 0.036
        swatch_x = 0.07
        swatch_y = y_center - swatch_h / 2.0

        patch = patches.FancyBboxPatch(
            (swatch_x, swatch_y),
            swatch_w,
            swatch_h,
            boxstyle="round,pad=0.004,rounding_size=0.008",
            facecolor=color,
            edgecolor=border_color,
            linewidth=0.8,
            transform=ax_target.transAxes,
            zorder=3,
        )
        ax_target.add_patch(patch)

        text_x = swatch_x + swatch_w + 0.045
        ax_target.text(
            text_x,
            y_center + 0.012,
            title,
            fontsize=10.5,
            weight="bold",
            fontfamily=FONT_FAMILY,
            color=COLOR_TEXT_DARK,
            va="center",
            ha="left",
        )
        ax_target.text(
            text_x,
            y_center - 0.012,
            subtitle,
            fontsize=9.0,
            fontfamily=FONT_FAMILY,
            color="#4B5563",
            va="center",
            ha="left",
        )

    # Section 1: Warmer Areas (Hot Spots)
    card_ax.text(
        0.07,
        0.875,
        "WARMER AREAS (HOT SPOTS)",
        fontsize=10.5,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_HOT,
        va="top",
    )
    draw_swatch_entry(
        card_ax,
        y_center=0.825,
        color=COLOR_HOT,
        title="Hot all day",
        subtitle=f"Hot in morning, afternoon & evening • {n_hot_all * CELL_AREA_KM2:.0f} km² ({n_hot_all/total_valid*100:.0f}%)",
        border_color=COLOR_HOT,
    )
    draw_swatch_entry(
        card_ax,
        y_center=0.755,
        color=COLOR_HOT_MID,
        title="Hot most of the day",
        subtitle=f"Hot during 2 of 3 periods • {n_hot_2 * CELL_AREA_KM2:.0f} km² ({n_hot_2/total_valid*100:.0f}%)",
        border_color="#E59866",
    )
    draw_swatch_entry(
        card_ax,
        y_center=0.685,
        color=COLOR_HOT_LIGHT,
        title="Hot part of the day",
        subtitle=f"Hot during 1 period • {n_hot_1 * CELL_AREA_KM2:.0f} km² ({n_hot_1/total_valid*100:.0f}%)",
        border_color="#F5B7B1",
    )

    # Section 2: Average Temperatures
    card_ax.text(
        0.07,
        0.625,
        "AVERAGE TEMPERATURES",
        fontsize=10.5,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color="#4B5563",
        va="top",
    )
    draw_swatch_entry(
        card_ax,
        y_center=0.575,
        color=COLOR_NS,
        title="Typical / Moderate",
        subtitle="Near regional average throughout the day",
        border_color="#BDC3C7",
    )

    # Section 3: Cooler Areas (Cold Spots)
    card_ax.text(
        0.07,
        0.515,
        "COOLER AREAS (COOL SPOTS)",
        fontsize=10.5,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_COLD,
        va="top",
    )
    draw_swatch_entry(
        card_ax,
        y_center=0.465,
        color=COLOR_COLD_LIGHT,
        title="Cool part of the day",
        subtitle=f"Cooler during 1 period • {n_cold_1 * CELL_AREA_KM2:.0f} km² ({n_cold_1/total_valid*100:.0f}%)",
        border_color="#AED6F1",
    )
    draw_swatch_entry(
        card_ax,
        y_center=0.395,
        color=COLOR_COLD_MID,
        title="Cool most of the day",
        subtitle=f"Cooler during 2 of 3 periods • {n_cold_2 * CELL_AREA_KM2:.0f} km² ({n_cold_2/total_valid*100:.0f}%)",
        border_color="#5DADE2",
    )
    draw_swatch_entry(
        card_ax,
        y_center=0.325,
        color=COLOR_COLD,
        title="Cool all day",
        subtitle=f"Cool in morning, afternoon & evening • {n_cold_all * CELL_AREA_KM2:.0f} km² ({n_cold_all/total_valid*100:.0f}%)",
        border_color=COLOR_COLD,
    )

    # Horizontal separator
    card_ax.plot([0.07, 0.93], [0.260, 0.260], color="#CBD5E1", linewidth=1.0)

    # Community takeaway box
    card_ax.text(
        0.07,
        0.235,
        "COMMUNITY TAKEAWAY",
        fontsize=11.0,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
        va="top",
    )
    # Takeaway figures are computed from this grid; canopy lines appear only when canopy is present
    takeaway_lines = [
        f"• About {n_hot_all / total_valid * 100:.0f}% of {region_name} stays in an all-day",
        "  heat cluster without diurnal cooling relief.",
    ]
    if "canopy_pct" in df.columns:
        hot_canopy_median = df.loc[df["hot_all3"].astype(bool), "canopy_pct"].median()
        cold_canopy_median = df.loc[df["cold_all3"].astype(bool), "canopy_pct"].median()
        if np.isfinite(hot_canopy_median) and np.isfinite(cold_canopy_median) and hot_canopy_median > 0:
            takeaway_lines += [
                f"• Cool refuges feature {cold_canopy_median / hot_canopy_median:.0f}× denser tree canopy cover",
                f"  ({cold_canopy_median:.0f}% median canopy vs. {hot_canopy_median:.0f}% in persistent hot spots).",
            ]
    takeaway_text = "\n".join(takeaway_lines)
    card_ax.text(
        0.07,
        0.195,
        takeaway_text,
        fontsize=9.5,
        style="normal",
        fontfamily=FONT_FAMILY,
        color="#4B5563",
        va="top",
        linespacing=1.35,
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, facecolor="#FFFFFF")
        plt.close(fig)
        logger.info("Saved: %s", out)

    return fig, ax


def plot_barchart_spot_area_by_period(
    summary_df: pd.DataFrame,
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = DEFAULT_DPI,
) -> Tuple[plt.Figure, plt.Axes]:
    """
    Grouped bar chart comparing the percentage of area that is hot vs. cold
    across Morning (AM), Afternoon (AF), and Evening (PM).
    """
    configure_matplotlib_fonts()

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=dpi, facecolor="#FFFFFF")
    ax = fig.add_axes([0.08, 0.16, 0.88, 0.62])

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, labelleft=False, bottom=False)

    # Reference zero line
    ax.axhline(0, color=COLOR_BASELINE, linestyle="-", linewidth=1.2, zorder=1)

    # Categories: Morning, Afternoon, Evening
    periods = ["am", "af", "pm"]
    labels = ["Morning\n(AM)", "Afternoon\n(AF)", "Evening\n(PM)"]
    indices = np.arange(len(periods))
    bar_width = 0.32

    hot_pcts = []
    cold_pcts = []
    hot_areas = []
    cold_areas = []

    for p in periods:
        h_row = summary_df[(summary_df["period"] == p) & (summary_df["spot_class"] == "Hot Spot")]
        c_row = summary_df[(summary_df["period"] == p) & (summary_df["spot_class"] == "Cold Spot")]

        h_val = float(h_row["area_pct"].values[0]) if len(h_row) > 0 else 0.0
        c_val = float(c_row["area_pct"].values[0]) if len(c_row) > 0 else 0.0
        h_km = float(h_row["area_km2"].values[0]) if len(h_row) > 0 else 0.0
        c_km = float(c_row["area_km2"].values[0]) if len(c_row) > 0 else 0.0

        hot_pcts.append(h_val)
        cold_pcts.append(c_val)
        hot_areas.append(h_km)
        cold_areas.append(c_km)

    # Draw Hot Bars (left) and Cold Bars (right)
    pos_hot = indices - bar_width / 2.0 - 0.02
    pos_cold = indices + bar_width / 2.0 + 0.02

    bars_hot = ax.bar(
        pos_hot,
        hot_pcts,
        bar_width,
        color=COLOR_HOT,
        edgecolor="none",
        linewidth=0,
        zorder=3,
        label="Hot Spot Cluster (High Temp)",
    )
    bars_cold = ax.bar(
        pos_cold,
        cold_pcts,
        bar_width,
        color=COLOR_COLD,
        edgecolor="none",
        linewidth=0,
        zorder=3,
        label="Cold Spot Cluster (Low Temp)",
    )

    # Direct value annotations on bars
    for bar, pct, km in zip(bars_hot, hot_pcts, hot_areas):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            pct + 0.8,
            f"{pct:.1f}%\n({km:.0f} km²)",
            ha="center",
            va="bottom",
            fontsize=11,
            weight="bold",
            color=COLOR_HOT,
            fontfamily=FONT_FAMILY,
        )

    for bar, pct, km in zip(bars_cold, cold_pcts, cold_areas):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            pct + 0.8,
            f"{pct:.1f}%\n({km:.0f} km²)",
            ha="center",
            va="bottom",
            fontsize=11,
            weight="bold",
            color=COLOR_COLD,
            fontfamily=FONT_FAMILY,
        )

    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(0, max(10.0, max(hot_pcts + cold_pcts + [0.0]) * 1.3))

    ax.set_xticks(indices)
    ax.tick_params(axis="x", pad=16)
    ax.set_xticklabels(labels, fontsize=12.5, weight="bold", color=COLOR_TEXT_DARK, fontfamily=FONT_FAMILY)

    # Title at top left
    fig.text(
        0.08,
        0.94,
        "Regional Land Area Classified as Hot vs. Cool Spots by Collection Time",
        ha="left",
        va="top",
        fontsize=16,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )

    # Legend centered at top
    legend_handles = [
        patches.Patch(facecolor=COLOR_HOT, edgecolor="none", label="Hot Spot (Gi* z > +1.96, FDR q < 0.05)"),
        patches.Patch(facecolor=COLOR_COLD, edgecolor="none", label="Cold Spot (Gi* z < -1.96, FDR q < 0.05)"),
    ]
    leg = ax.legend(
        handles=legend_handles,
        ncols=2,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.98),
        frameon=True,
        facecolor=COLOR_LEGEND_BG,
        edgecolor="none",
        handlelength=1.0,
        handleheight=1.0,
        borderpad=0.5,
        columnspacing=2.5,
        handletextpad=0.6,
        prop={"family": FONT_FAMILY, "weight": "bold", "size": 11},
    )
    leg.get_frame().set_boxstyle("round,pad=0.5,rounding_size=0.3")

    # Bottom caption
    fig.text(
        0.50,
        0.035,
        "Percentages calculated relative to valid modeled grid cells per collection period • Error bounds controlled at 5% False Discovery Rate (FDR)",
        ha="center",
        va="center",
        fontsize=10.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, facecolor="#FFFFFF")
        plt.close(fig)
        logger.info("Saved: %s", out)

    return fig, ax


def plot_boxplot_canopy_hot_vs_cold(
    df: pd.DataFrame,
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = DEFAULT_DPI,
) -> Tuple[plt.Figure, plt.Axes]:
    """
    Distribution boxplot comparing tree canopy cover (%) between:
    1. Persistent Hot Spots (All 3 Times)
    2. Persistent Cold Spots (All 3 Times)
    3. Not Significant Areas
    4. Regional Study Area Baseline
    """
    configure_matplotlib_fonts()

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=dpi, facecolor="#FFFFFF")
    ax = fig.add_axes([0.10, 0.16, 0.84, 0.62])

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, labelleft=False, bottom=False)

    canopy_df = df.dropna(subset=["canopy_pct"]).copy()
    hot_canopy = canopy_df[canopy_df["hot_all3"].astype(bool)]["canopy_pct"].values
    cold_canopy = canopy_df[canopy_df["cold_all3"].astype(bool)]["canopy_pct"].values
    ns_canopy = canopy_df[canopy_df["persistence"] == "Not Significant"]["canopy_pct"].values
    all_canopy = canopy_df["canopy_pct"].values

    data_groups = [hot_canopy, cold_canopy, ns_canopy, all_canopy]
    group_labels = [
        "Hot Spot\n(All 3 Times)",
        "Cool Spot\n(All 3 Times)",
        "Not Significant\n(Intermittent)",
        "Regional Study Area\n(Overall Baseline)",
    ]
    group_colors = [COLOR_HOT, COLOR_COLD, "#9CA3AF", "#4B5563"]

    positions = np.arange(len(data_groups))

    # Reference 50% line
    ax.axhline(50, color=COLOR_BASELINE, linestyle=":", linewidth=1.2, alpha=0.7, zorder=1)

    bp = ax.boxplot(
        data_groups,
        positions=positions,
        widths=0.45,
        patch_artist=True,
        showfliers=False,
        showmeans=True,
        meanline=True,
        medianprops=dict(color="#FFFFFF", linewidth=2.5),
        meanprops=dict(color="#111827", linewidth=2.0, linestyle="--"),
        whiskerprops=dict(linewidth=1.5, color="#4B5563"),
        capprops=dict(linewidth=1.5, color="#4B5563"),
        zorder=3,
    )

    for patch, col in zip(bp["boxes"], group_colors):
        patch.set_facecolor(col)
        patch.set_edgecolor("none")
        patch.set_alpha(0.88)

    # Direct value annotations for median and mean above/below each box
    for pos, vals, col in zip(positions, data_groups, group_colors):
        med = float(np.median(vals))
        mean_v = float(np.mean(vals))
        q75 = float(np.percentile(vals, 75))

        ax.text(
            pos,
            q75 + 4.5,
            f"Median: {med:.1f}%\nMean: {mean_v:.1f}%",
            ha="center",
            va="bottom",
            fontsize=11.5,
            weight="bold",
            color=col,
            fontfamily=FONT_FAMILY,
        )

    ax.set_xlim(-0.6, len(data_groups) - 0.4)
    ax.set_ylim(-5, 138)

    ax.set_xticks(positions)
    ax.tick_params(axis="x", pad=16)
    ax.set_xticklabels(group_labels, fontsize=12, weight="bold", color=COLOR_TEXT_DARK, fontfamily=FONT_FAMILY)

    # Title at top left
    fig.text(
        0.08,
        0.94,
        "Tree Canopy Cover: Persistent Hot Spots vs. Cold Spots",
        ha="left",
        va="top",
        fontsize=16,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )

    # Subtitle with Mann-Whitney U test finding
    u_stat, p_val = scipy.stats.mannwhitneyu(hot_canopy, cold_canopy, alternative="two-sided")
    hot_median = float(np.median(hot_canopy))
    cold_median = float(np.median(cold_canopy))
    p_text = "p < 0.001" if p_val < 0.001 else f"p = {p_val:.3f}"
    divergence = "Significant divergence" if p_val < 0.05 else "No significant divergence"
    ratio_text = f"{cold_median / hot_median:.1f}× greater" if hot_median > 0 else "greater"
    fig.text(
        0.08,
        0.90,
        f"{divergence}: Persistent cold refuges have {ratio_text} median tree canopy ({cold_median:.1f}% vs {hot_median:.1f}%, Mann-Whitney U {p_text})",
        ha="left",
        va="top",
        fontsize=11,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    # Legend centered at top
    legend_handles = [
        patches.Patch(facecolor="#4B5563", edgecolor="none", label="Box: Interquartile Range (Q1–Q3)"),
        matplotlib.lines.Line2D([0], [0], color="#FFFFFF", lw=2.5, label="Solid White Line: Median"),
        matplotlib.lines.Line2D([0], [0], color="#111827", lw=2.0, ls="--", label="Dashed Black Line: Mean"),
    ]
    leg = ax.legend(
        handles=legend_handles,
        ncols=3,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.98),
        frameon=True,
        facecolor=COLOR_LEGEND_BG,
        edgecolor="none",
        handlelength=1.4,
        handleheight=0.9,
        borderpad=0.5,
        columnspacing=2.0,
        handletextpad=0.5,
        prop={"family": FONT_FAMILY, "weight": "bold", "size": 11},
    )
    leg.get_frame().set_boxstyle("round,pad=0.5,rounding_size=0.3")

    # Bottom caption
    fig.text(
        0.50,
        0.035,
        f"Box bounds represent 25th to 75th percentiles (IQR) • Whiskers show 1.5 × IQR • Cells without canopy data are excluded (n = {len(canopy_df):,} cells)",
        ha="center",
        va="center",
        fontsize=10.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, facecolor="#FFFFFF")
        plt.close(fig)
        logger.info("Saved: %s", out)

    return fig, ax


def plot_barchart_canopy_by_spot(
    summary_df: pd.DataFrame,
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = DEFAULT_DPI,
) -> Tuple[plt.Figure, plt.Axes]:
    """
    Grouped bar chart comparing Mean Tree Canopy Cover (%) across Hot Spots,
    Cold Spots, and Not Significant areas for each collection time.
    """
    configure_matplotlib_fonts()

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=dpi, facecolor="#FFFFFF")
    ax = fig.add_axes([0.08, 0.16, 0.88, 0.62])

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, labelleft=False, bottom=False)

    ax.axhline(0, color=COLOR_BASELINE, linestyle="-", linewidth=1.2, zorder=1)

    periods = ["am", "af", "pm"]
    labels = ["Morning\n(AM)", "Afternoon\n(AF)", "Evening\n(PM)"]
    indices = np.arange(len(periods))
    bar_width = 0.24

    hot_means = []
    cold_means = []
    ns_means = []

    for p in periods:
        h_row = summary_df[(summary_df["period"] == p) & (summary_df["spot_class"] == "Hot Spot")]
        c_row = summary_df[(summary_df["period"] == p) & (summary_df["spot_class"] == "Cold Spot")]
        ns_row = summary_df[(summary_df["period"] == p) & (summary_df["spot_class"] == "Not Significant")]

        hot_means.append(float(h_row["mean_canopy_pct"].values[0]) if len(h_row) > 0 else 0.0)
        cold_means.append(float(c_row["mean_canopy_pct"].values[0]) if len(c_row) > 0 else 0.0)
        ns_means.append(float(ns_row["mean_canopy_pct"].values[0]) if len(ns_row) > 0 else 0.0)

    pos_hot = indices - bar_width - 0.03
    pos_ns = indices
    pos_cold = indices + bar_width + 0.03

    bars_hot = ax.bar(pos_hot, hot_means, bar_width, color=COLOR_HOT, edgecolor="none", linewidth=0, zorder=3)
    bars_ns = ax.bar(pos_ns, ns_means, bar_width, color="#9CA3AF", edgecolor="none", linewidth=0, zorder=3)
    bars_cold = ax.bar(pos_cold, cold_means, bar_width, color=COLOR_COLD, edgecolor="none", linewidth=0, zorder=3)

    # Direct labels
    for bar, m in zip(bars_hot, hot_means):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            m + 1.2,
            f"{m:.1f}%",
            ha="center",
            va="bottom",
            fontsize=11.5,
            weight="bold",
            color=COLOR_HOT,
            fontfamily=FONT_FAMILY,
        )

    for bar, m in zip(bars_ns, ns_means):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            m + 1.2,
            f"{m:.1f}%",
            ha="center",
            va="bottom",
            fontsize=11.5,
            weight="bold",
            color="#4B5563",
            fontfamily=FONT_FAMILY,
        )

    for bar, m in zip(bars_cold, cold_means):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            m + 1.2,
            f"{m:.1f}%",
            ha="center",
            va="bottom",
            fontsize=11.5,
            weight="bold",
            color=COLOR_COLD,
            fontfamily=FONT_FAMILY,
        )

    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(0, 110)

    ax.set_xticks(indices)
    ax.tick_params(axis="x", pad=16)
    ax.set_xticklabels(labels, fontsize=12.5, weight="bold", color=COLOR_TEXT_DARK, fontfamily=FONT_FAMILY)

    # Title at top left
    fig.text(
        0.08,
        0.94,
        "Mean Tree Canopy Cover (%) Across Spatial Hot and Cold Spots",
        ha="left",
        va="top",
        fontsize=16,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )

    # Legend centered at top
    legend_handles = [
        patches.Patch(facecolor=COLOR_HOT, edgecolor="none", label="Hot Spot Cluster (High Temp)"),
        patches.Patch(facecolor="#9CA3AF", edgecolor="none", label="Not Significant"),
        patches.Patch(facecolor=COLOR_COLD, edgecolor="none", label="Cold Spot Cluster (Low Temp)"),
    ]
    leg = ax.legend(
        handles=legend_handles,
        ncols=3,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.98),
        frameon=True,
        facecolor=COLOR_LEGEND_BG,
        edgecolor="none",
        handlelength=1.0,
        handleheight=1.0,
        borderpad=0.5,
        columnspacing=2.2,
        handletextpad=0.6,
        prop={"family": FONT_FAMILY, "weight": "bold", "size": 11},
    )
    leg.get_frame().set_boxstyle("round,pad=0.5,rounding_size=0.3")

    fig.text(
        0.50,
        0.035,
        "Tree canopy cover (%) computed within 100m grid cells",
        ha="center",
        va="center",
        fontsize=10.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, facecolor="#FFFFFF")
        plt.close(fig)
        logger.info("Saved: %s", out)

    return fig, ax


def plot_barchart_method_agreement(
    sens_df: pd.DataFrame,
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = DEFAULT_DPI,
    variable: str = "Air Temperature",
) -> Tuple[plt.Figure, plt.Axes]:
    """
    Sensitivity comparison figure showing how Getis-Ord Gi* spatial clustering
    compares with simple top/bottom 10% percentile thresholding.
    """
    configure_matplotlib_fonts()

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=dpi, facecolor="#FFFFFF")
    ax = fig.add_axes([0.08, 0.16, 0.88, 0.62])

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, labelleft=False, bottom=False)

    ax.axhline(0, color=COLOR_BASELINE, linestyle="-", linewidth=1.2, zorder=1)

    periods = ["am", "af", "pm"]
    labels = ["Morning\n(AM)", "Afternoon\n(AF)", "Evening\n(PM)"]
    indices = np.arange(len(periods))
    bar_width = 0.32

    hot_shares = []
    cold_shares = []

    for p in periods:
        h_row = sens_df[(sens_df["period"] == p) & (sens_df["feature"] == "Hot Spot")]
        c_row = sens_df[(sens_df["period"] == p) & (sens_df["feature"] == "Cold Spot")]

        hot_shares.append(float(h_row["share_of_percentile_confirmed_by_gi"].values[0]))
        cold_shares.append(float(c_row["share_of_percentile_confirmed_by_gi"].values[0]))

    pos_hot = indices - bar_width / 2.0 - 0.02
    pos_cold = indices + bar_width / 2.0 + 0.02

    bars_hot = ax.bar(pos_hot, hot_shares, bar_width, color=COLOR_HOT, edgecolor="none", linewidth=0, zorder=3)
    bars_cold = ax.bar(pos_cold, cold_shares, bar_width, color=COLOR_COLD, edgecolor="none", linewidth=0, zorder=3)

    for bar, val in zip(bars_hot, hot_shares):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            val + 1.2,
            f"{val:.1f}%",
            ha="center",
            va="bottom",
            fontsize=12,
            weight="bold",
            color=COLOR_HOT,
            fontfamily=FONT_FAMILY,
        )

    for bar, val in zip(bars_cold, cold_shares):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            val + 1.2,
            f"{val:.1f}%",
            ha="center",
            va="bottom",
            fontsize=12,
            weight="bold",
            color=COLOR_COLD,
            fontfamily=FONT_FAMILY,
        )

    ax.set_xlim(-0.6, 2.6)
    ax.set_ylim(0, 140)

    ax.set_xticks(indices)
    ax.tick_params(axis="x", pad=16)
    ax.set_xticklabels(labels, fontsize=12.5, weight="bold", color=COLOR_TEXT_DARK, fontfamily=FONT_FAMILY)

    # Title at top left
    fig.text(
        0.08,
        0.94,
        "Method Sensitivity: Percentile Extremes Confirmed by Spatial Clustering",
        ha="left",
        va="top",
        fontsize=16,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )
    fig.text(
        0.08,
        0.90,
        "Share of top 10% hottest and bottom 10% coolest cells statistically validated as significant spatial clusters by Getis-Ord Gi*",
        ha="left",
        va="top",
        fontsize=11,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    legend_handles = [
        patches.Patch(facecolor=COLOR_HOT, edgecolor="none", label="Top 10% Hot Confirmed by Gi*"),
        patches.Patch(facecolor=COLOR_COLD, edgecolor="none", label="Bottom 10% Cold Confirmed by Gi*"),
    ]
    leg = ax.legend(
        handles=legend_handles,
        ncols=2,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.98),
        frameon=True,
        facecolor=COLOR_LEGEND_BG,
        edgecolor="none",
        handlelength=1.0,
        handleheight=1.0,
        borderpad=0.5,
        columnspacing=2.5,
        handletextpad=0.6,
        prop={"family": FONT_FAMILY, "weight": "bold", "size": 11},
    )
    leg.get_frame().set_boxstyle("round,pad=0.5,rounding_size=0.3")

    fig.text(
        0.50,
        0.035,
        f"Spatial clustering confirms {min(hot_shares + cold_shares):.0f}–{max(hot_shares + cold_shares):.0f}% of raw {variable.lower()} extremes, while eliminating isolated spurious single-cell anomalies",
        ha="center",
        va="center",
        fontsize=10.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, facecolor="#FFFFFF")
        plt.close(fig)
        logger.info("Saved: %s", out)

    return fig, ax


def plot_barchart_persistence(
    pers_df: pd.DataFrame,
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = DEFAULT_DPI,
) -> Tuple[plt.Figure, plt.Axes]:
    """
    Bar chart showing total land area (km² and %) by diurnal persistence tier,
    contrasting persistent all-day hot spots vs persistent cold refuges.
    """
    configure_matplotlib_fonts()

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=dpi, facecolor="#FFFFFF")
    ax = fig.add_axes([0.08, 0.18, 0.88, 0.60])

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, labelleft=False, bottom=False)

    ax.axhline(0, color=COLOR_BASELINE, linestyle="-", linewidth=1.2, zorder=1)

    # Ordered tiers: Hot All 3, Hot 2, Hot 1, Mixed, Cold 1, Cold 2, Cold All 3
    tiers = [
        ("Hot Spot (All 3 Times)", "Hot All 3\n(All-Day)", COLOR_HOT),
        ("Hot Spot (2 Times)", "Hot 2 Times\n(Intermittent)", COLOR_HOT_MID),
        ("Hot Spot (1 Time)", "Hot 1 Time\n(Transient)", COLOR_HOT_LIGHT),
        ("Mixed Shift", "Mixed\nShift", "#CBD5E1"),
        ("Cold Spot (1 Time)", "Cool 1 Time\n(Transient)", COLOR_COLD_LIGHT),
        ("Cold Spot (2 Times)", "Cool 2 Times\n(Intermittent)", COLOR_COLD_MID),
        ("Cold Spot (All 3 Times)", "Cool All 3\n(Refuge)", COLOR_COLD),
    ]

    tier_names = [t[0] for t in tiers]
    labels = [t[1] for t in tiers]
    colors = [t[2] for t in tiers]

    areas_km = []
    pcts = []
    for name in tier_names:
        row = pers_df[pers_df["persistence_class"] == name]
        if len(row) > 0:
            areas_km.append(float(row["area_km2"].values[0]))
            pcts.append(float(row["area_pct"].values[0]))
        else:
            areas_km.append(0.0)
            pcts.append(0.0)

    indices = np.arange(len(tiers))
    bar_width = 0.55

    bars = ax.bar(
        indices,
        areas_km,
        bar_width,
        color=colors,
        edgecolor="none",
        linewidth=0,
        zorder=3,
    )

    # Direct labels above bars
    for bar, km, pct, col in zip(bars, areas_km, pcts, colors):
        label_color = col if col not in (COLOR_HOT_LIGHT, COLOR_COLD_LIGHT, "#CBD5E1") else COLOR_TEXT_DARK
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            km + 4.0,
            f"{km:.0f} km²\n({pct:.1f}%)",
            ha="center",
            va="bottom",
            fontsize=11,
            weight="bold",
            color=label_color,
            fontfamily=FONT_FAMILY,
        )

    ax.set_xlim(-0.6, len(tiers) - 0.4)
    ax.set_ylim(0, max(areas_km) * 1.25)

    ax.set_xticks(indices)
    ax.tick_params(axis="x", pad=16)
    ax.set_xticklabels(labels, fontsize=11.5, weight="bold", color=COLOR_TEXT_DARK, fontfamily=FONT_FAMILY)

    # Title at top left
    fig.text(
        0.08,
        0.94,
        "Diurnal Heat Persistence: Total Area by Persistence Tier",
        ha="left",
        va="top",
        fontsize=16,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )
    fig.text(
        0.08,
        0.90,
        "Comparison of land area remaining statistically hot or cold across 1, 2, or all 3 collection times (Getis-Ord Gi* FDR q < 0.05)",
        ha="left",
        va="top",
        fontsize=11,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    # Bottom caption: totals come from the summary table, so they match the bars above
    total_km2 = float(pers_df["cell_count"].sum()) * CELL_AREA_KM2
    hot_all_km2 = float(pers_df.loc[pers_df["persistence_class"] == "Hot Spot (All 3 Times)", "area_km2"].sum())
    hot_all_pct = hot_all_km2 / total_km2 * 100.0 if total_km2 > 0 else 0.0
    fig.text(
        0.50,
        0.035,
        f"Total modeled area = {total_km2:,.1f} km² • {hot_all_km2:.1f} km² ({hot_all_pct:.1f}%) of the modeled area remains an extreme hot spot continuously from morning through evening",
        ha="center",
        va="center",
        fontsize=10.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, facecolor="#FFFFFF")
        plt.close(fig)
        logger.info("Saved: %s", out)

    return fig, ax
