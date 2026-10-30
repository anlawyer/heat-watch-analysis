#!/usr/bin/env python3
"""
make_hotspot_analysis.py - Master driver for spatial hot and cold spot analysis
of a temperature grid (Northern Virginia by default).

Executes:
1. Getis-Ord Gi* hotspot detection for AM, AF, and PM on 100m grid with FDR control.
2. Diurnal persistence analysis identifying multi-period heat zones and cool refuges.
3. Canopy cover comparison between persistent hot spots and cold spots (only when the grid has canopy_pct).
4. Export of enriched GeoPackage and summary CSVs.
5. Generation of presentation-ready 16:9 figures adhering to GEMINI.md design standards.
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
import logging
import time
from pathlib import Path

# Ensure uhi is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

import geopandas as gpd
from shapely.geometry import box

from uhi.hotspots import (
    PERIODS,
    analyze_hotspots,
    summarize_hotspots_by_period,
    summarize_persistence,
    summarize_canopy_comparison,
    compare_methods_sensitivity,
)
from uhi.hotspot_plots import (
    read_boundary,
    plot_map_spots_by_period,
    plot_map_persistence,
    plot_barchart_spot_area_by_period,
    plot_barchart_persistence,
    plot_boxplot_canopy_hot_vs_cold,
    plot_barchart_canopy_by_spot,
    plot_barchart_method_agreement,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("hotspot_pipeline")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Getis-Ord Gi* hot and cold spots, persistence, and figures for a temperature grid."
    )
    parser.add_argument(
        "--grid",
        type=Path,
        default=Path("output/canopy/grid-canopy.gpkg"),
        help="Grid GeoPackage with temp_am, temp_af, temp_pm (default: output/canopy/grid-canopy.gpkg)",
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        default=Path("input_data/county_boundaries/novauhi_counties.gpkg"),
        help="Study-area outline in any CRS. Cells outside it are excluded from maps and card statistics.",
    )
    parser.add_argument(
        "--no-boundary",
        action="store_true",
        help="Run without a study-area outline (every grid cell is used)",
    )
    parser.add_argument(
        "--region-name",
        default="Northern Virginia",
        help="Region name used in figure text (default: Northern Virginia)",
    )
    parser.add_argument(
        "--variable",
        default="Air Temperature",
        help="Variable name used in the figure titles, e.g. 'Heat Index' (default: Air Temperature)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/hotspots"),
        help="Directory for the enriched GeoPackage and summary CSVs (default: output/hotspots)",
    )
    parser.add_argument(
        "--figures-dir",
        type=Path,
        default=Path("output/figures/hotspots"),
        help="Directory for figures (default: output/figures/hotspots)",
    )
    args = parser.parse_args()

    t_start = time.time()
    logger.info("Starting %s hot & cold spot analysis pipeline...", args.region_name)

    grid_path = args.grid
    out_data_dir = args.output_dir
    out_fig_dir = args.figures_dir

    if not grid_path.is_file():
        logger.error("Grid dataset not found: %s", grid_path)
        return 1

    # 1. Read input dataset
    logger.info("Loading grid from %s...", grid_path)
    # We read with geometry so we can save back enriched GeoPackage
    gdf = gpd.read_file(grid_path)
    has_canopy = "canopy_pct" in gdf.columns
    logger.info(
        "Loaded %d grid cells (CRS: %s). Canopy column %s.",
        len(gdf),
        gdf.crs,
        "found" if has_canopy else "not found; canopy outputs skipped",
    )

    # Persistence, canopy comparison and the three-panel figures need AM, AF and PM together
    all_periods = all(f"temp_{p}" in gdf.columns for p in PERIODS)
    if not all_periods:
        present = [p.upper() for p in PERIODS if f"temp_{p}" in gdf.columns]
        logger.info(
            "Found %s only: persistence and figures that compare all three periods are skipped.",
            ", ".join(present) or "no periods",
        )

    boundary_path = None if args.no_boundary else args.boundary
    if boundary_path is not None:
        if not boundary_path.is_file():
            logger.error("Boundary not found: %s (use --no-boundary to run without one)", boundary_path)
            return 1
        boundary = read_boundary(boundary_path, gdf.crs)
        if not boundary.intersects(box(*gdf.total_bounds)).any():
            logger.error(
                "Boundary %s does not overlap the grid. Pass the boundary for this study area or use --no-boundary.",
                boundary_path,
            )
            return 1

    out_data_dir.mkdir(parents=True, exist_ok=True)
    out_fig_dir.mkdir(parents=True, exist_ok=True)

    # 2. Run Hot Spot Analysis
    logger.info("Running Getis-Ord Gi* clustering, FDR control, and persistence...")
    enriched = analyze_hotspots(gdf, fdr_threshold=0.05, percentile_threshold=10.0)

    # 3. Generate summary tables
    logger.info("Computing summary tables...")
    period_summary = summarize_hotspots_by_period(enriched)
    sensitivity_summary = compare_methods_sensitivity(enriched)
    if all_periods:
        persistence_summary = summarize_persistence(enriched)
    if all_periods and has_canopy:
        canopy_res = summarize_canopy_comparison(enriched)
        canopy_summary = canopy_res["stats_table"]

    # Export CSVs
    period_summary.to_csv(out_data_dir / "summary_by_period.csv", index=False)
    if all_periods:
        persistence_summary.to_csv(out_data_dir / "persistence_counts.csv", index=False)
    if all_periods and has_canopy:
        canopy_summary.to_csv(out_data_dir / "canopy_hot_vs_cold.csv", index=False)
    sensitivity_summary.to_csv(out_data_dir / "method_agreement.csv", index=False)
    logger.info("Exported summary CSVs to %s", out_data_dir)

    # 4. Save enriched GeoPackage
    out_gpkg = out_data_dir / "hotspots.gpkg"
    logger.info("Saving enriched GeoPackage to %s...", out_gpkg)
    export_gdf = enriched.copy()
    # Convert bool columns to int for wide GeoPackage compatibility
    for col in export_gdf.columns:
        if export_gdf[col].dtype == bool:
            export_gdf[col] = export_gdf[col].astype(int)

    export_gdf.to_file(out_gpkg, driver="GPKG")
    logger.info("GeoPackage written successfully.")

    # 5. Generate Figures
    logger.info("Generating 16:9 presentation figures...")

    if all_periods:
        # Figure 1: 3-panel map of hot/cold spots per period
        fig1_path = out_fig_dir / "map_spots_by_period.png"
        plot_map_spots_by_period(
            enriched, boundary_path=boundary_path, output_path=fig1_path, region_name=args.region_name, variable=args.variable
        )

        # Figure 2: Diurnal persistence map
        fig2_path = out_fig_dir / "map_persistence.png"
        plot_map_persistence(
            enriched, boundary_path=boundary_path, output_path=fig2_path, region_name=args.region_name, variable=args.variable
        )

        # Figure 3: Grouped bar chart of spot area by period
        fig3_path = out_fig_dir / "barchart_spot_area_by_period.png"
        plot_barchart_spot_area_by_period(period_summary, output_path=fig3_path)

        # Figure 4: Persistence tier bar chart
        fig4_path = out_fig_dir / "barchart_persistence.png"
        plot_barchart_persistence(persistence_summary, output_path=fig4_path)

        # Figure 7: Sensitivity check (Gi* vs Percentile agreement)
        fig7_path = out_fig_dir / "barchart_method_agreement.png"
        plot_barchart_method_agreement(sensitivity_summary, output_path=fig7_path)

    if all_periods and has_canopy:
        # Figure 5: Boxplot of canopy cover: Hot All-3 vs Cold All-3
        fig5_path = out_fig_dir / "boxplot_canopy_hot_vs_cold.png"
        plot_boxplot_canopy_hot_vs_cold(enriched, output_path=fig5_path)

        # Figure 6: Grouped bar chart of canopy by spot class per period
        fig6_path = out_fig_dir / "barchart_canopy_by_spot.png"
        plot_barchart_canopy_by_spot(period_summary, output_path=fig6_path)

    # 6. Print summary report to stdout
    print("\n" + "=" * 78)
    print(f"{args.region_name.upper()} URBAN HEAT ISLAND — HOT & COLD SPOT ANALYSIS SUMMARY")
    print("=" * 78)
    period_cols = ["period_label", "spot_class", "area_pct", "area_km2", "mean_temp_c"]
    persistence_cols = ["persistence_class", "area_pct", "area_km2"]
    if has_canopy:
        period_cols.append("mean_canopy_pct")
        persistence_cols += ["mean_canopy_pct", "median_canopy_pct"]
    print("\n1. DIURNAL HOT & COLD SPOTS BY COLLECTION TIME:")
    print(period_summary[period_cols].to_string(index=False))

    if all_periods:
        print("\n2. DIURNAL PERSISTENCE ACROSS ALL 3 PERIODS:")
        print(persistence_summary[persistence_cols].to_string(index=False))

    if all_periods and has_canopy:
        print("\n3. CANOPY COMPARISON (PERSISTENT HOT VS. COLD SPOTS):")
        print(canopy_summary[["group", "area_km2", "mean_canopy_pct", "median_canopy_pct", "iqr_canopy_pct"]].to_string(index=False))
        print(f"\nMann-Whitney U Test: p = {canopy_res['p_value']:.2e}, Rank-Biserial Effect Size = {canopy_res['rank_biserial_effect_size']:.3f}")
        print(f"Canopy Difference: Cold refuges have +{canopy_res['canopy_difference_median']:.1f}% higher median canopy cover than Hot spots.")

    print("\n4. SENSITIVITY CHECK (PERCENTILE EXTREMES CONFIRMED BY GI* CLUSTERING):")
    print(sensitivity_summary[["period_label", "feature", "share_of_percentile_confirmed_by_gi", "jaccard_index"]].to_string(index=False))

    t_elapsed = time.time() - t_start
    print(f"\nAll operations completed successfully in {t_elapsed:.1f} seconds.")
    print("=" * 78 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
