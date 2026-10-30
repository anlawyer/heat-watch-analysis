"""
uhi/zonal.py - Zonal statistics computation for grid polygons.

Computes exact zonal mean temperatures for grid polygons using
high-performance raster reprojection with area-weighted average downsampling.
Input surfaces and output values are in degrees Celsius.
"""

import logging
from pathlib import Path
from typing import List, Mapping, Optional, Tuple, Union

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from uhi.canopy import GridSpec

logger = logging.getLogger(__name__)

TEMPERATURE_PERIODS: List[Tuple[str, str, str]] = [
    ("am", "temp_am", "predicted_temperature_surface_am-whole.tif"),
    ("af", "temp_af", "predicted_temperature_surface_af-whole.tif"),
    ("pm", "temp_pm", "predicted_temperature_surface_pm-whole.tif"),
]

# Plausible air temperature range in °C. Surfaces outside it almost always have the wrong
# units (e.g. Kelvin or Fahrenheit values), so they are rejected rather than averaged.
PLAUSIBLE_RANGE_C: Tuple[float, float] = (-20.0, 60.0)


def compute_temperature_zonal_means(
    grid_gdf: gpd.GeoDataFrame,
    maps_dir: Union[str, Path] = "input_data/maps",
    spec: Optional[GridSpec] = None,
    rasters: Optional[Mapping[str, Union[str, Path]]] = None,
) -> gpd.GeoDataFrame:
    """
    Compute the zonal mean temperature (°C) for each grid cell across morning (AM),
    afternoon (AF), and evening (PM) surfaces.

    Parameters
    ----------
    grid_gdf : gpd.GeoDataFrame
        Grid GeoDataFrame with row_index and col_index attributes.
    maps_dir : str or Path
        Directory containing the predicted temperature surface GeoTIFFs.
        Used only when `rasters` is None.
    spec : GridSpec, optional
        Precomputed GridSpec. If None, derived automatically from grid_gdf.
    rasters : mapping, optional
        Explicit GeoTIFF path for each period, keyed "am", "af", "pm". When given,
        only these periods are computed and maps_dir is not used.

    Returns
    -------
    gpd.GeoDataFrame
        Updated GeoDataFrame with a 'temp_<period>' column (°C) for each period computed.

    Raises
    ------
    ValueError
        If a surface has values outside PLAUSIBLE_RANGE_C, which usually means it is not in °C.
    """
    known = {period for period, _, _ in TEMPERATURE_PERIODS}
    if rasters is None:
        periods = TEMPERATURE_PERIODS
        maps_path = Path(maps_dir)
        if not maps_path.is_dir():
            raise NotADirectoryError(f"Maps directory not found: {maps_path}")
    else:
        unknown = set(rasters) - known
        if unknown or not rasters:
            raise ValueError(f"rasters must use keys from {sorted(known)}, got {sorted(rasters)}")
        periods = [t for t in TEMPERATURE_PERIODS if t[0] in rasters]

    if spec is None:
        spec = GridSpec.from_grid(grid_gdf)

    dst_transform = from_origin(spec.x0, spec.y0, spec.cell_size, spec.cell_size)
    dst_crs = f"EPSG:{spec.crs_epsg}"

    r_idx = grid_gdf["row_index"].values.astype(int)
    c_idx = grid_gdf["col_index"].values.astype(int)

    out_gdf = grid_gdf.copy()

    for period, col_name, filename in periods:
        if rasters is not None:
            tif_path = Path(rasters[period])
            if not tif_path.is_file():
                raise FileNotFoundError(f"Temperature raster for '{period}' not found: {tif_path}")
        else:
            tif_path = maps_path / filename
            if not tif_path.is_file():
                # Try finding any file matching period in filename
                candidates = list(maps_path.glob(f"*{period}*.tif"))
                if candidates:
                    tif_path = candidates[0]
                else:
                    raise FileNotFoundError(f"Temperature raster for '{period}' not found in {maps_path}")

        logger.info("Computing zonal mean temperature for %s from %s ...", period.upper(), tif_path.name)

        with rasterio.open(tif_path) as src:
            if src.crs is None:
                raise ValueError(f"Temperature raster has no CRS: {tif_path}")

            # Read values with nodata as NaN, so nodata never enters the averages
            source = src.read(1).astype(np.float32)
            if src.nodata is not None:
                source[source == src.nodata] = np.nan
            source[~np.isfinite(source)] = np.nan

            # Downsample to the grid cell size using average resampling
            dst_grid = np.full((spec.nrows, spec.ncols), np.nan, dtype=np.float32)

            reproject(
                source=source,
                destination=dst_grid,
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=np.nan,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                dst_nodata=np.nan,
                resampling=Resampling.average,
            )

        finite = dst_grid[np.isfinite(dst_grid)]
        low, high = PLAUSIBLE_RANGE_C
        if finite.size and (finite.min() < low or finite.max() > high):
            raise ValueError(
                f"{tif_path.name}: values span {finite.min():.1f} to {finite.max():.1f} °C, outside "
                f"the plausible {low:.0f} to {high:.0f} °C. The surface must be in °C."
            )

        # Extract values for each grid cell via indices
        cell_temps = dst_grid[r_idx, c_idx]
        out_gdf[col_name] = np.where(np.isfinite(cell_temps), np.round(cell_temps, 2), np.nan)

        valid_count = np.isfinite(cell_temps).sum()
        logger.info(
            "Completed %s: %d / %d cells valid (mean=%.2f °C)",
            col_name,
            valid_count,
            len(out_gdf),
            np.nanmean(cell_temps),
        )

    return out_gdf
