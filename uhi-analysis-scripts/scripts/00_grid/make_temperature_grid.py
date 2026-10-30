#!/usr/bin/env python3
"""
make_temperature_grid.py - Build the 100 m analysis grid from temperature surface footprints.

Use this when no tree canopy grid exists. The grid covers the combined extent of the
temperature rasters, snapped to 100 m cells in the rasters' projected CRS. An optional
study-area boundary crops the grid to the cells that touch it.

Output columns: id, left, top, right, bottom, row_index, col_index, geometry.
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

import pyogrio

from uhi.grid import CELL_SIZE_M, build_temperature_grid

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("temperature_grid")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the 100 m analysis grid from temperature surface rasters."
    )
    parser.add_argument(
        "--rasters",
        type=Path,
        nargs="+",
        required=True,
        help="Temperature GeoTIFFs (AM, AF, PM). All must share one projected CRS in metres.",
    )
    parser.add_argument(
        "--boundary",
        type=Path,
        default=None,
        help="Optional study-area outline. Cells that do not touch it are dropped.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("output/grid/temperature-grid.gpkg"),
        help="Output GeoPackage (default: output/grid/temperature-grid.gpkg)",
    )
    args = parser.parse_args()

    for raster in args.rasters:
        if not raster.is_file():
            logger.error("Raster not found: %s", raster)
            return 1
    if args.boundary is not None and not args.boundary.is_file():
        logger.error("Boundary not found: %s", args.boundary)
        return 1

    t0 = time.time()
    logger.info("Building %.0f m grid from %d raster(s) ...", CELL_SIZE_M, len(args.rasters))
    grid = build_temperature_grid(args.rasters, boundary=args.boundary)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(grid, args.output, driver="GPKG")

    n_rows = int(grid["row_index"].max()) + 1
    n_cols = int(grid["col_index"].max()) + 1
    print("\n" + "=" * 65)
    print("TEMPERATURE GRID COMPLETE")
    print("=" * 65)
    print(f"Cells:          {len(grid):,} (matrix {n_rows} rows x {n_cols} cols, {CELL_SIZE_M:.0f} m)")
    print(f"CRS:            {grid.crs.to_string()}")
    print(f"Extent (m):     left={grid['left'].min():.1f} right={grid['right'].max():.1f} "
          f"bottom={grid['bottom'].min():.1f} top={grid['top'].max():.1f}")
    print(f"Processing:     {time.time() - t0:.1f} s")
    print(f"Output:         {args.output.resolve()}")
    print("=" * 65 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
