"""
uhi/grid.py - Build the analysis grid from temperature surface footprints.

The grid is the unit of analysis: square cells of CELL_SIZE_M metres in the projected
CRS of the temperature surfaces. Row 0 is the top row and column 0 is the left column.
"""

import logging
import math
from pathlib import Path
from typing import Optional, Sequence, Union

import geopandas as gpd
import numpy as np
import rasterio
import shapely

logger = logging.getLogger(__name__)

CELL_SIZE_M: float = 100.0


def build_temperature_grid(
    raster_paths: Sequence[Union[str, Path]],
    boundary: Optional[Union[str, Path, gpd.GeoDataFrame]] = None,
) -> gpd.GeoDataFrame:
    """
    Build a grid of CELL_SIZE_M cells covering the combined footprint of the rasters.

    Parameters
    ----------
    raster_paths : sequence of str or Path
        Temperature surfaces. All must share one projected CRS measured in metres.
    boundary : str, Path or GeoDataFrame, optional
        Study-area outline. Cells that do not touch it are dropped, and the grid is
        cropped to the remaining cells so that row_index and col_index start at 0.

    Returns
    -------
    gpd.GeoDataFrame
        Columns: id, left, top, right, bottom, row_index, col_index, geometry.
    """
    if not raster_paths:
        raise ValueError("At least one temperature raster is required")

    crs = None
    bounds = []
    for path in raster_paths:
        with rasterio.open(path) as src:
            if src.crs is None:
                raise ValueError(f"Raster has no CRS: {path}")
            if crs is None:
                crs = src.crs
            elif src.crs != crs:
                raise ValueError(f"CRS mismatch: {path} is {src.crs}, expected {crs}")
            bounds.append(src.bounds)

            pixel = max(abs(src.res[0]), abs(src.res[1]))
            if pixel > CELL_SIZE_M:
                logger.warning(
                    "%s has %.0f m pixels, coarser than the %.0f m grid",
                    Path(path).name,
                    pixel,
                    CELL_SIZE_M,
                )

    if not crs.is_projected or crs.linear_units not in ("metre", "meter"):
        raise ValueError(f"Grid needs a projected CRS in metres, got {crs}")

    left = min(b.left for b in bounds)
    right = max(b.right for b in bounds)
    bottom = min(b.bottom for b in bounds)
    top = max(b.top for b in bounds)

    # Snap outward to whole cells, anchored at the top-left corner
    x0 = math.floor(round(left / CELL_SIZE_M, 6)) * CELL_SIZE_M
    y0 = math.ceil(round(top / CELL_SIZE_M, 6)) * CELL_SIZE_M
    ncols = math.ceil(round((right - x0) / CELL_SIZE_M, 6))
    nrows = math.ceil(round((y0 - bottom) / CELL_SIZE_M, 6))

    rows, cols = np.meshgrid(np.arange(nrows), np.arange(ncols), indexing="ij")
    rows = rows.ravel()
    cols = cols.ravel()

    lefts = x0 + cols * CELL_SIZE_M
    tops = y0 - rows * CELL_SIZE_M
    rights = lefts + CELL_SIZE_M
    bottoms = tops - CELL_SIZE_M

    grid = gpd.GeoDataFrame(
        {
            "id": np.arange(1, rows.size + 1),
            "left": lefts,
            "top": tops,
            "right": rights,
            "bottom": bottoms,
            "row_index": rows,
            "col_index": cols,
        },
        geometry=shapely.box(lefts, bottoms, rights, tops),
        crs=crs,
    )

    if boundary is not None:
        if not isinstance(boundary, gpd.GeoDataFrame):
            boundary = gpd.read_file(boundary)
        outline = boundary.to_crs(crs).union_all()
        keep = grid.geometry.intersects(outline).to_numpy()
        if not keep.any():
            raise ValueError("The boundary does not overlap the temperature surfaces")

        grid = grid.loc[keep].copy()
        grid["row_index"] = grid["row_index"] - grid["row_index"].min()
        grid["col_index"] = grid["col_index"] - grid["col_index"].min()
        grid["id"] = np.arange(1, len(grid) + 1)
        grid = grid.reset_index(drop=True)

    return grid
