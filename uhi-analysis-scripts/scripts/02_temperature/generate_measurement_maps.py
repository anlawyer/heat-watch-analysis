#!/usr/bin/env python3
"""
generate_measurement_maps.py - Generate presentation-grade 16:9 maps of raw mobile measurement points.

Produces for each survey session (AM, AF, PM):
1. Flagship 16:9 Presentation Map (with title, subtitle statistics, vertical colorbar, Figtree font):
   - output/figures/measurements/map_raw_measurements_am.png
   - output/figures/measurements/map_raw_measurements_af.png
   - output/figures/measurements/map_raw_measurements_pm.png
2. Transparent Map-Only Version (300 DPI, no title/legend/card, transparent canvas):
   - output/figures/measurements/map_raw_measurements_am_transparent.png
   - output/figures/measurements/map_raw_measurements_af_transparent.png
   - output/figures/measurements/map_raw_measurements_pm_transparent.png
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


import sys
from pathlib import Path
import contextily as cx
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Design tokens from GEMINI.md
SLIDE_FIGSIZE = (13.333, 7.5)
DPI = 300
FONT_FAMILY = "Figtree"
FALLBACK_FONTS = ["Figtree", "DejaVu Sans", "Arial", "sans-serif"]
plt.rcParams["font.family"] = FALLBACK_FONTS

COLOR_TEXT_DARK = "#1F2937"
COLOR_TEXT_MUTED = "#7F8C8D"
COLOR_BORDER = "#475569"

PERIOD_METADATA = {
    "am": {
        "name": "Morning (AM)",
        "window": "6:00 – 7:00 AM EDT",
        "theme_color": "#2980B9",
        "filename": "combined_am.csv",
    },
    "af": {
        "name": "Afternoon Peak Heat (AF)",
        "window": "3:00 – 4:00 PM EDT",
        "theme_color": "#F39C12",
        "filename": "combined_af.csv",
    },
    "pm": {
        "name": "Evening (PM)",
        "window": "7:00 – 8:00 PM EDT",
        "theme_color": "#E74C3C",
        "filename": "combined_pm.csv",
    },
}

ESRI_GRAY_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
)




def get_contrast_ylorrd_cmap():
    """Build a high-contrast YlOrRd colormap starting at amber gold to avoid pale yellow on white."""
    base_cmap = plt.get_cmap("YlOrRd")
    colors = base_cmap(np.linspace(0.12, 1.0, 256))
    return mcolors.LinearSegmentedColormap.from_list("YlOrRd_contrast", colors)


def plot_measurement_map(period: str, input_dir: Path, boundary_path: Path, output_dir: Path):
    meta = PERIOD_METADATA[period]
    csv_path = input_dir / meta["filename"]

    print(f"\nProcessing {meta['name']} from {csv_path}...")
    df = pd.read_csv(csv_path, low_memory=False)
    temp_col = [c for c in df.columns if "Temp[" in c][0]

    # Filter valid finite points
    valid_mask = df[temp_col].notna() & df["Lat"].notna() & df["Lon"].notna()
    valid_df = df[valid_mask].copy()
    temps_c = valid_df[temp_col].values.astype(float)

    # Project coordinates to UTM Zone 18N (EPSG:32618)
    gdf = gpd.GeoDataFrame(
        valid_df,
        geometry=gpd.points_from_xy(valid_df["Lon"], valid_df["Lat"]),
        crs="EPSG:4326",
    ).to_crs(epsg=32618)

    # Load counties
    b_gdf = gpd.read_file(boundary_path)
    b_bounds = b_gdf.total_bounds
    pad = 1800
    map_xlim = (b_bounds[0] - pad, b_bounds[2] + pad)
    map_ylim = (b_bounds[1] - pad, b_bounds[3] + pad)

    # Statistics
    n_pts = len(temps_c)
    t_min = float(np.min(temps_c))
    t_max = float(np.max(temps_c))
    t_mean = float(np.mean(temps_c))
    p1 = float(np.percentile(temps_c, 1.0))
    p99 = float(np.percentile(temps_c, 99.0))

    # Colormap and Normalization
    cmap = get_contrast_ylorrd_cmap()
    norm = mcolors.Normalize(vmin=p1, vmax=p99)

    # Sort points so extreme departures from the mean appear on top
    mean_val = np.mean(temps_c)
    draw_order = np.argsort(np.abs(temps_c - mean_val))
    xs = gdf.geometry.x.values[draw_order]
    ys = gdf.geometry.y.values[draw_order]
    c_vals = temps_c[draw_order]

    # =========================================================================
    # 1. Flagship 16:9 Presentation Figure (Clean Balanced Layout, No Card)
    # =========================================================================
    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=DPI, facecolor="#FFFFFF")

    # Titles (Left-aligned at 0.08)
    fig.text(
        0.080,
        0.945,
        f"NOVA UHI Campaign — {meta['name']} Mobile Measurements",
        ha="left",
        va="top",
        fontsize=17,
        weight="bold",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_DARK,
    )
    fig.text(
        0.080,
        0.908,
        f"Traverse Survey {meta['window']} • {n_pts:,} in-situ observations • Spatial Mean: {t_mean:.1f} °C • Range: {t_min:.1f} °C to {t_max:.1f} °C",
        ha="left",
        va="top",
        fontsize=11.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    # Map Axes (Centered with generous width)
    ax = fig.add_axes([0.14, 0.08, 0.62, 0.79])
    ax.set_xlim(map_xlim)
    ax.set_ylim(map_ylim)
    ax.set_aspect("equal")
    ax.axis("off")

    # Add Esri Light Gray basemap
    try:
        cx.add_basemap(ax, crs="EPSG:32618", source=ESRI_GRAY_URL, alpha=0.92, zorder=1)
    except Exception as exc:
        print(f"Warning: Could not load basemap: {exc}")
        b_gdf.plot(ax=ax, facecolor="#F8FAFC", edgecolor="#CBD5E1", linewidth=1.2, zorder=1)

    # County boundary outlines
    b_gdf.boundary.plot(ax=ax, color=COLOR_BORDER, linewidth=1.1, linestyle="--", alpha=0.85, zorder=2)

    # Point halo underlay (dark soft outline for route clarity)
    ax.scatter(xs, ys, s=18, color="#0F172A", alpha=0.32, linewidths=0, zorder=3)
    # Colored temperature points
    sc = ax.scatter(xs, ys, c=c_vals, s=10, cmap=cmap, norm=norm, alpha=0.96, linewidths=0, zorder=4)

    # Vertical Colorbar on the right with ample clearance
    cbar_ax = fig.add_axes([0.80, 0.16, 0.022, 0.62])
    cb = fig.colorbar(sc, cax=cbar_ax, orientation="vertical", extend="both")
    cb.set_label("Air Temperature (°C)", fontsize=13, fontfamily=FONT_FAMILY, weight="bold", color=COLOR_TEXT_DARK, labelpad=14)
    cb.ax.tick_params(labelsize=10.5, colors=COLOR_TEXT_DARK, length=4, width=0.8)
    cb.outline.set_edgecolor("#9CA3AF")
    cb.outline.set_linewidth(0.8)

    # Bottom caption
    fig.text(
        0.50,
        0.030,
        f"Notes: In-situ ambient air temperature observations (°C) recorded during mobile vehicle traverses on July 16, 2026. Color stretching: 1st–99th percentiles ({p1:.1f}°C to {p99:.1f}°C). Basemap: Esri World Light Gray Canvas.",
        ha="center",
        va="bottom",
        fontsize=10.5,
        style="italic",
        fontfamily=FONT_FAMILY,
        color=COLOR_TEXT_MUTED,
    )

    out_flagship = output_dir / f"map_raw_measurements_{period}.png"
    fig.savefig(out_flagship, dpi=DPI, facecolor="#FFFFFF")
    plt.close(fig)
    print(f"Saved flagship map: {out_flagship}")

    # =========================================================================
    # 2. Transparent Map-Only Figure (Rule 8)
    # =========================================================================
    fig_tr = plt.figure(figsize=SLIDE_FIGSIZE, dpi=DPI, facecolor="none")
    ax_tr = fig_tr.add_axes([0.0, 0.0, 1.0, 1.0])
    ax_tr.set_xlim(map_xlim)
    ax_tr.set_ylim(map_ylim)
    ax_tr.set_aspect("equal")
    ax_tr.axis("off")

    # County boundary outlines
    b_gdf.boundary.plot(ax=ax_tr, color=COLOR_BORDER, linewidth=1.1, linestyle="--", alpha=0.80, zorder=1)

    # Point halo underlay
    ax_tr.scatter(xs, ys, s=18, color="#0F172A", alpha=0.32, linewidths=0, zorder=2)
    # Colored temperature points
    ax_tr.scatter(xs, ys, c=c_vals, s=10, cmap=cmap, norm=norm, alpha=0.96, linewidths=0, zorder=3)

    out_transparent = output_dir / f"map_raw_measurements_{period}_transparent.png"
    fig_tr.savefig(out_transparent, dpi=DPI, transparent=True, facecolor="none")
    plt.close(fig_tr)
    print(f"Saved transparent map: {out_transparent}")


def main():
    base_dir = PROJECT_ROOT
    input_dir = base_dir / "input_data" / "uhi-combined"
    boundary_path = base_dir / "input_data" / "county_boundaries" / "novauhi_counties.gpkg"
    output_dir = base_dir / "output" / "figures" / "measurements"
    output_dir.mkdir(parents=True, exist_ok=True)

    for period in ["am", "af", "pm"]:
        plot_measurement_map(period, input_dir, boundary_path, output_dir)

    print("\nAll measurement maps generated successfully!")


if __name__ == "__main__":
    main()
