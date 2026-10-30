#!/usr/bin/env python3
"""
make_uhi_maps.py - Main driver for generating presentation-ready 16:9 UHI maps.

Loads the urban heat island rasters from input_data/maps, computes spatial statistics
and anomalies, and produces individual and panel comparison figures in 16:9 format.
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
from pathlib import Path

# Ensure uhi package is importable from current working directory
sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for server/script runs
import matplotlib.pyplot as plt

import uhi
from uhi.io import load_uhi_rasters, load_uhi_raster, UHIRaster, PERIOD_ORDER
from uhi.plotting import (
    plot_single_map,
    plot_panel,
    compute_shared_anomaly_limit,
    DEFAULT_DPI,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("uhi_generator")


def print_summary_table(
    rasters: list[UHIRaster], shared_lim: float, region_name: str, variable: str, units: str
) -> None:
    """Print an ASCII summary table of raster dimensions and temperature stats."""
    labels = [f"Min ({units})", f"P2 ({units})", f"Mean ({units})", f"P98 ({units})", f"Max ({units})"]
    widths = [8, 8, 9, 8, 8]
    header = f"{'Period':<8} | {'Filename':<38} | {'Dims (R×C)':<12} | " + " | ".join(
        f"{label:<{width}}" for label, width in zip(labels, widths)
    )
    sep = "-" * len(header)
    print("\n" + sep)
    print(f"{region_name} {variable} — Raster Summary Statistics")
    print(sep)
    print(header)
    print(sep)
    for r in rasters:
        s = r.summary()
        fname = r.source_path.name if r.source_path else "unknown"
        dims = f"{r.data.shape[0]}×{r.data.shape[1]}"
        print(
            f"{r.period.upper():<8} | {fname:<38} | {dims:<12} | "
            f"{s['min']:<8.2f} | {s['p2']:<8.2f} | {s['mean']:<9.2f} | {s['p98']:<8.2f} | {s['max']:<8.2f}"
        )
    print(sep)
    print(f"Shared symmetric anomaly scale limit: ±{shared_lim:.1f} {units}")
    print(sep + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate 16:9 presentation maps for an urban heat island campaign."
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=Path("input_data/maps"),
        help="Directory containing the input GeoTIFF rasters (default: input_data/maps)",
    )
    parser.add_argument(
        "--boundary",
        "-b",
        type=Path,
        default=Path("input_data/county_boundaries/novauhi_counties.gpkg"),
        help="Region of interest boundary file to clip to (default: input_data/county_boundaries/novauhi_counties.gpkg)",
    )
    parser.add_argument(
        "--region-name",
        default="Northern Virginia",
        help="Region name used in the figure titles (default: Northern Virginia)",
    )
    parser.add_argument(
        "--variable",
        default="Air Temperature",
        help="Variable name used in the figure titles, e.g. 'Heat Index' (default: Air Temperature)",
    )
    parser.add_argument(
        "--units",
        default="°C",
        help="Units shown with the values and on the colorbars (default: °C)",
    )
    parser.add_argument(
        "--no-clip",
        action="store_true",
        help="Do not clip rasters to the region of interest boundary",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("output/figures"),
        help="Directory to save generated figures (default: output/figures)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"Resolution for generated figures (default: {DEFAULT_DPI})",
    )
    parser.add_argument(
        "--no-basemap",
        action="store_true",
        help="Disable CartoDB Positron basemap tiles (offline mode)",
    )

    args = parser.parse_args()

    boundary = None if args.no_clip else args.boundary
    if boundary and not boundary.is_file():
        logger.warning("Boundary file %s not found. Proceeding without boundary clipping.", boundary)
        boundary = None

    clipped_defaults = [
        Path("morning_clipped.tif"),
        Path("afternoon_clipped.tif"),
        Path("evening_clipped.tif"),
    ]
    # If using default input path and pre-clipped rasters exist in root, use them directly
    if args.input == Path("input_data/maps") and all(f.is_file() for f in clipped_defaults):
        logger.info("Using pre-clipped region-of-interest rasters (morning/afternoon/evening_clipped.tif)...")
        rasters = [load_uhi_raster(f, boundary=boundary) for f in clipped_defaults]
        rasters.sort(key=lambda r: PERIOD_ORDER.index(r.period) if r.period in PERIOD_ORDER else 99)
    else:
        logger.info("Loading rasters from %s (boundary clipping: %s)...", args.input, bool(boundary))
        try:
            rasters = load_uhi_rasters(args.input, boundary=boundary)
        except Exception as exc:
            logger.error("Failed to load rasters: %s", exc)
            return 1

    shared_anomaly_limit = compute_shared_anomaly_limit(rasters)
    print_summary_table(rasters, shared_anomaly_limit, args.region_name, args.variable, args.units)

    add_basemap = not args.no_basemap
    temp_dir = args.output / "temperature"
    anom_dir = args.output / "anomaly"
    panel_dir = args.output / "panels"

    generated_files: list[Path] = []

    # 1. Generate individual temperature maps (3 figures)
    logger.info("Generating individual temperature maps (colormap: YlOrRd)...")
    for r in rasters:
        out_file = temp_dir / f"uhi_temperature_{r.period}.png"
        fig, _ = plot_single_map(
            raster=r,
            kind="temperature",
            add_basemap=add_basemap,
            output_path=out_file,
            dpi=args.dpi,
            region_name=args.region_name,
            variable=args.variable,
            units=args.units,
        )
        plt.close(fig)
        generated_files.append(out_file)

    # 2. Generate individual anomaly maps (3 figures)
    logger.info("Generating individual anomaly maps (colormap: Spectral_r)...")
    for r in rasters:
        out_file = anom_dir / f"uhi_anomaly_{r.period}.png"
        fig, _ = plot_single_map(
            raster=r,
            kind="anomaly",
            anomaly_limit=shared_anomaly_limit,
            add_basemap=add_basemap,
            output_path=out_file,
            dpi=args.dpi,
            region_name=args.region_name,
            variable=args.variable,
            units=args.units,
        )
        plt.close(fig)
        generated_files.append(out_file)

    # 3. Generate 3-panel comparison figures (2 figures). These need AM, AF and PM together.
    if len(rasters) == 3:
        logger.info("Generating 3-panel temperature progression figure...")
        temp_panel_file = panel_dir / "uhi_temperature_panel.png"
        fig, _ = plot_panel(
            rasters=rasters,
            kind="temperature",
            add_basemap=add_basemap,
            output_path=temp_panel_file,
            dpi=args.dpi,
            region_name=args.region_name,
            variable=args.variable,
            units=args.units,
        )
        plt.close(fig)
        generated_files.append(temp_panel_file)

        logger.info("Generating 3-panel anomaly progression figure...")
        anom_panel_file = panel_dir / "uhi_anomaly_panel.png"
        fig, _ = plot_panel(
            rasters=rasters,
            kind="anomaly",
            anomaly_limit=shared_anomaly_limit,
            add_basemap=add_basemap,
            output_path=anom_panel_file,
            dpi=args.dpi,
            region_name=args.region_name,
            variable=args.variable,
            units=args.units,
        )
        plt.close(fig)
        generated_files.append(anom_panel_file)

    else:
        logger.info("Skipping 3-panel figures: %d surface(s) found, 3 are needed (AM, AF, PM).", len(rasters))

    print("\n" + "=" * 60)
    print(f"SUCCESS: Generated {len(generated_files)} presentation-ready figure(s) in 16:9 format:")
    print("=" * 60)
    for p in generated_files:
        size_mb = p.stat().st_size / (1024 * 1024)
        print(f"  • {p.as_posix():<52} ({size_mb:.2f} MB)")
    print("=" * 60 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

