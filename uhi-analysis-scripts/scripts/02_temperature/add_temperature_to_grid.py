#!/usr/bin/env python3
"""
add_temperature_to_grid.py - Compute and attach temperature zonal means to the grid.

Computes zonal mean temperatures (°C) for Morning (temp_am), Afternoon (temp_af),
and Evening (temp_pm) from the temperature rasters and saves them into the grid
GeoPackage (default: output/canopy/grid-canopy.gpkg; use --grid for another grid).
The input rasters must be in °C.
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
import sys
import time
from pathlib import Path

# Ensure uhi package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pyogrio

import uhi
from uhi.zonal import compute_temperature_zonal_means

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("temp_zonal")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute zonal mean temperatures (°C) per grid polygon and save to GeoPackage."
    )
    parser.add_argument(
        "--grid",
        type=Path,
        default=Path("output/canopy/grid-canopy.gpkg"),
        help="Path to grid GeoPackage (default: output/canopy/grid-canopy.gpkg)",
    )
    parser.add_argument(
        "--maps",
        type=Path,
        default=Path("input_data/maps"),
        help="Directory containing the temperature rasters (default: input_data/maps)",
    )
    parser.add_argument(
        "--am",
        type=Path,
        default=None,
        help="Morning raster (°C). Naming any of --am/--af/--pm computes only the periods named",
    )
    parser.add_argument("--af", type=Path, default=None, help="Afternoon raster in °C (see --am)")
    parser.add_argument("--pm", type=Path, default=None, help="Evening raster in °C (see --am)")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output path (default: updates --grid in place)",
    )

    args = parser.parse_args()
    output_path = args.output if args.output is not None else args.grid

    if not args.grid.is_file():
        logger.error("Grid file not found: %s", args.grid)
        return 1

    # Naming any period (--am/--af/--pm) computes only the periods given
    rasters = {p: path for p, path in (("am", args.am), ("af", args.af), ("pm", args.pm)) if path is not None}
    rasters = rasters or None

    t0 = time.time()
    logger.info("Reading grid from %s ...", args.grid)
    # Keep the grid's own layer name, so the update replaces that layer rather than adding a second one
    layer = pyogrio.list_layers(args.grid)[0][0]
    grid_gdf = pyogrio.read_dataframe(args.grid, layer=layer)
    logger.info("Loaded %d features. Columns: %s", len(grid_gdf), list(grid_gdf.columns))

    logger.info("Computing zonal mean temperatures (°C)...")
    try:
        updated_gdf = compute_temperature_zonal_means(grid_gdf, maps_dir=args.maps, rasters=rasters)
    except (ValueError, FileNotFoundError, NotADirectoryError) as exc:
        logger.error("%s", exc)
        return 1

    periods = [p for p in ["am", "af", "pm"] if f"temp_{p}" in updated_gdf.columns]
    logger.info("Computing temperature spatial anomalies (T - spatial_mean) for %s...", ", ".join(p.upper() for p in periods))
    for period in periods:
        t_col = f"temp_{period}"
        a_col = f"anom_{period}"
        valid_mask = updated_gdf[t_col].notna()
        mean_val = updated_gdf.loc[valid_mask, t_col].mean()
        updated_gdf[a_col] = np.where(
            valid_mask,
            np.round(updated_gdf[t_col] - mean_val, 2),
            np.nan,
        )
        logger.info("Computed %s (mean baseline = %.2f °C)", a_col, mean_val)

    logger.info("Writing updated grid with temperature & anomaly attributes to %s ...", output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(updated_gdf, output_path, driver="GPKG", layer=layer)

    total_time = time.time() - t0

    print("\n" + "=" * 70)
    print("TEMPERATURE ZONAL MEAN COMPUTATION COMPLETE")
    print("=" * 70)
    print(f"Total processing time: {total_time:.2f} s")
    print(f"Output GeoPackage:     {output_path.resolve()}\n")

    header = f"{'Attribute':<12} | {'Valid Cells':<12} | {'Min (°C)':<9} | {'Mean (°C)':<10} | {'Max (°C)':<9} | {'Std (°C)':<8}"
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    for col in [f"temp_{p}" for p in periods]:
        vals = updated_gdf[col].dropna()
        if len(vals) > 0:
            print(
                f"{col:<12} | {len(vals):<12,} | {vals.min():<9.2f} | {vals.mean():<10.2f} | {vals.max():<9.2f} | {vals.std():<8.2f}"
            )
        else:
            print(f"{col:<12} | {'0':<12} | {'N/A':<9} | {'N/A':<10} | {'N/A':<9} | {'N/A':<8}")
    print(sep)

    # Cross-analysis with tree canopy cover if present
    if "canopy_pct" in updated_gdf.columns and periods:
        print("\n--- Correlation with Tree Canopy Coverage ---")
        valid_both = updated_gdf.dropna(subset=["canopy_pct"] + [f"temp_{p}" for p in periods])
        print(f"Cells with both canopy & temperature: {len(valid_both):,}")
        labels = {"am": "Morning Temp (AM)", "af": "Afternoon Temp (AF)", "pm": "Evening Temp (PM)"}
        for p in periods:
            r = np.corrcoef(valid_both["canopy_pct"], valid_both[f"temp_{p}"])[0, 1]
            note = "" if p == "am" else " (strong cooling effect!)"
            print(f"  • Canopy % vs {labels[p]}: r = {r:+.3f}{note}")
    print("=" * 70 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
