import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.enums import Resampling as RioResampling
from rasterio.transform import from_origin as _rasterio_from_origin
import rasterio.errors
from rasterio.features import geometry_mask
import matplotlib.pyplot as plt
import xarray as xr
import os
import time

from scipy.ndimage import convolve
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_squared_error
from pathlib import Path

import pystac_client
import planetary_computer
import rioxarray
from rioxarray.exceptions import NoDataInBounds
from shapely.geometry import box
from scipy.signal import fftconvolve
from joblib import Parallel, delayed
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# STEP 0.5: Clean the observation data (nodata codes, implausible values)
# ---------------------------------------------------------------------------
def clean_temperature_observations(gdf: gpd.GeoDataFrame, source_col: str = "temp_c",
                                    output_col: str = "temperature_c",
                                    nodata_values: tuple = (-9999.0, -999.0, -99.0, 9999.0),
                                    valid_range: tuple = (-22.0, 131.0),
                                    verbose: bool = True) -> gpd.GeoDataFrame:
    """
    Cleans sentinel nodata codes and physically implausible values out of
    the raw temperature observation data. Values are kept in Fahrenheit
    throughout -- no unit conversion is applied. Call this immediately
    after loading your observation shapefile (or CSV), BEFORE it's used
    downstream (Steps 4-6).

    This matters a great deal for Random Forest training: a single -9999.0
    sitting next to real ~70-100 degF values creates a squared error on the
    order of (9999-85)^2 for that one point, which dominates RMSE and can
    drive R^2 to extreme negative values (an R^2 far below 0 -- your model
    would have to be predicting *worse than* simply guessing the mean --
    is a strong signal of exactly this kind of contamination, not a subtle
    normalization issue).

    Note: Random Forest does NOT require normalized/scaled input features
    or targets -- tree splits are based on value ordering, not magnitude,
    so scaling has no effect on RF performance. The fix needed here is
    removing bad sentinel values, not normalizing or converting units.

    Steps performed, in order:
        1. Replace known nodata sentinel codes with NaN, in the source
           column.
        2. Copy remaining valid values to `output_col` (default
           "temperature_c", matching what build_training_table() and
           train_rf_model() expect downstream).
        3. Flag any value outside `valid_range_f` as NaN too -- catches
           sensor glitches or undocumented sentinel codes not in your
           explicit `nodata_values` list.
        4. Drop rows with NaN in `output_col` (can't train or evaluate
           without a valid target).
        5. Print a before/after summary so you can confirm what was caught.
    """
    n_start = len(gdf)
    gdf = gdf.copy()

    if verbose:
        raw_min, raw_max = gdf[source_col].min(), gdf[source_col].max()
        print(f"Before cleaning: {n_start} rows, "
              f"{source_col} range [{raw_min:.2f}, {raw_max:.2f}] degC")

    # Step 1: sentinel nodata codes -> NaN
    gdf[source_col] = gdf[source_col].replace(list(nodata_values), np.nan)
    n_after_nodata = gdf[source_col].notna().sum()

    # Step 2: copy to output column (no unit conversion -- staying in degF)
    gdf[output_col] = gdf[source_col]

    # Step 3: implausible values -> NaN
    lo, hi = valid_range
    out_of_range = (gdf[output_col] < lo) | (gdf[output_col] > hi)
    n_out_of_range = out_of_range.sum()
    gdf.loc[out_of_range, output_col] = np.nan

    # Step 4: drop rows with no valid temperature
    gdf = gdf.dropna(subset=[output_col]).reset_index(drop=True)
    n_end = len(gdf)

    if verbose:
        print(f"  Dropped {n_start - n_after_nodata} rows matching nodata codes {nodata_values}")
        print(f"  Dropped {n_out_of_range} additional rows outside valid range {valid_range} degC")
        print(f"After cleaning: {n_end} rows ({n_start - n_end} total dropped, "
              f"{100 * (n_start - n_end) / n_start:.1f}%)")
        print(f"  {output_col} range now "
              f"[{gdf[output_col].min():.2f}, {gdf[output_col].max():.2f}] degC")

    if n_end < 0.5 * n_start:
        print(f"  WARNING: more than half your observations were dropped. "
              f"Double-check `nodata_values` and `valid_range` -- you may be "
              f"discarding legitimate data, or your source file may use a "
              f"different nodata convention than assumed here.")

    return gdf

# ---------------------------------------------------------------------------
# STEP 1: Acquire Sentinel-2 imagery
# ---------------------------------------------------------------------------

BAND_NAMES = ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]
NATIVE_RES = {  # meters -- from Table 2 of the paper
    "B02": 10, "B03": 10, "B04": 10, "B08": 10,
    "B05": 20, "B06": 20, "B07": 20, "B8A": 20, "B11": 20, "B12": 20,
}
 
# Commonly-recommended GDAL settings for reading cloud-optimized GeoTIFFs
# (COGs) efficiently over HTTP: coalesces multiple small byte-range
# requests into fewer, larger ones, and avoids GDAL trying to list a
# remote "directory" that doesn't really exist for a single blob URL.
# This reduces the number of round trips per read, which helps both
# speed and the odds of a read completing before a short-lived signed
# URL (as Planetary Computer issues) expires mid-transfer.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
os.environ.setdefault("GDAL_HTTP_VERSION", "2")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ["GDAL_HTTP_TIMEOUT"] = "60"
os.environ["GDAL_HTTP_MAX_RETRY"] = "5"
os.environ["GDAL_HTTP_RETRY_DELAY"] = "3"


_CATALOG = pystac_client.Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=planetary_computer.sign_inplace,
)
 
 
COVERAGE_CHECK_BAND = "B04"  # cheap reference band used to evaluate candidate scenes
MIN_VALID_COVERAGE = 0.92    # required fraction of non-NaN pixels within a tile's own AOI-overlap region
MIN_TILE_OVERLAP_FRACTION = 0.01  # skip tiles contributing < 1% of the AOI+buffer area
 

def _utm_epsg_for(lon: float, lat: float) -> str:
    """Returns the EPSG code for the UTM zone containing (lon, lat).
    Used to pick a metric CRS for area/overlap bookkeeping that works for
    any AOI, not just ones in zone 18N."""
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{32600 + zone}" if lat >= 0 else f"EPSG:{32700 + zone}"
 
 
def _clip_to_box(da, minx, miny, maxx, maxy, src_crs="EPSG:4326"):
    """Reprojects a box from `src_crs` into `da`'s CRS and clips to it.
    NOTE: rio.clip_box() does NOT pad the result with NaN to match the
    requested box if `da` doesn't extend that far -- it simply returns
    whatever smaller region actually overlaps. Callers that need to know
    the TRUE coverage fraction relative to the full requested box must
    account for this themselves (see _select_best_covering_item), not
    just measure np.isfinite() over the returned array's own shape."""
    box_geom = gpd.GeoSeries([box(minx, miny, maxx, maxy)], crs=src_crs).to_crs(da.rio.crs).iloc[0]
    bminx, bminy, bmaxx, bmaxy = box_geom.bounds
    return da.rio.clip_box(minx=bminx, miny=bminy, maxx=bmaxx, maxy=bmaxy)

def _mask_s2_nodata(da):
    """Converts Sentinel-2 no-data pixels (DN == 0) to NaN.
     
    Sentinel-2 L2A uses 0 as its no-data value (swath edges, detector
    gaps, outside-footprint corners of an MGRS tile). rioxarray's
    open_rasterio(masked=True) only does this conversion if the file
    carries a nodata tag; if it doesn't, those zeros stay in the data as
    if they were real reflectance. They then (a) count as "valid" in the
    coverage check, (b) win the first-valid-wins merge over real data from
    another tile, and (c) feed constant values into the focal buffers,
    producing sharp, slanted artifacts in the predicted surface.
 
    Real valid pixels are never exactly 0 (the minimum valid L2A DN is 1),
    so masking == 0 is safe. Call this right after clipping (so only the
    small AOI window is touched, not the full ~110 km tile) and before the
    coverage check / merge / reprojection.
    """
    return da.where(da != 0)

 
# ---------------------------------------------------------------------------
# Scene screening, selection and cross-scene gap-filling
# ---------------------------------------------------------------------------
# A Sentinel-2 scene's real imaged swath is slanted relative to the MGRS
# tile grid, so a tile's corner can be no-data (DN == 0) even though the
# tile nominally covers it. Instead of accepting or rejecting whole scenes,
# the functions below (1) measure each candidate's REAL valid-pixel map on
# a small common canvas, cheaply, from a COG overview; (2) pick the best
# scene per tile; and (3) add a few "fill" scenes (other dates/orbits) that
# cover whatever the best scenes miss. manual_merge() is "first valid wins",
# so listing the best scenes first and the fill scenes after them means
# fill scenes only ever supply pixels the best scenes don't have.
 
SCREEN_RES_M = 40.0            # resolution of the screening canvas
SCREEN_OVERVIEW_LEVEL = 1      # COG overview to read for screening (1 = ~4x decimated: ~40 m for a 10 m band)
SCREEN_WORKERS = 4             # candidates screened in parallel (1 = serial)
FILL_TARGET_COVERAGE = 0.995   # stop adding fill scenes once this fraction of AOI+buffer has valid data
MAX_FILL_SCENES = 3            # never add more than this many fill scenes
MIN_FILL_GAIN = 0.005          # a fill scene must add at least this fraction of the AOI+buffer
READ_RETRIES = 3               # attempts per read (each attempt re-signs the URL)
 
 
def _fresh_href(item, band_name: str) -> str:
    """Returns a freshly signed URL for one asset. Planetary Computer SAS
    tokens are short-lived and the STAC search signs them only once, so a
    long run can end up reading with an expired token. Re-signing right
    before each read (planetary_computer.sign caches tokens and renews
    expired ones) avoids that. Any old query string is stripped first so
    the old token isn't carried along."""
    base_href = item.assets[band_name].href.split("?", 1)[0]
    return planetary_computer.sign(base_href)
 
 
def _with_retries(fn, item, band_name: str, label: str, retries: int = READ_RETRIES):
    """Calls fn(fresh_signed_href), retrying on I/O errors (network
    timeouts, expired tokens) with a freshly signed URL each time.
    `fn` must do the actual pixel read inside itself (e.g. call .load()),
    because rioxarray reads lazily and errors only surface at read time."""
    for attempt in range(1, retries + 1):
        try:
            return fn(_fresh_href(item, band_name))
        except (OSError, rasterio.errors.RasterioIOError) as exc:
            if attempt == retries:
                raise
            wait = 5 * attempt
            print(f"    {label}: read failed ({type(exc).__name__}: {str(exc)[:100]}). "
                  f"Re-signing URL and retrying in {wait}s (attempt {attempt + 1}/{retries})...")
            time.sleep(wait)
 
 
def _scene_label(item) -> str:
    orbit = item.properties.get("sat:relative_orbit")
    orbit_txt = f", R{int(orbit):03d}" if orbit is not None else ""
    return (f"{item.properties.get('s2:mgrs_tile', '?')} "
            f"({item.properties['eo:cloud_cover']:.1f}% cloud, "
            f"{item.properties['datetime'][:10]}{orbit_txt})")
 
 
def _build_screen_canvas(bounds, resolution: float, crs):
    """Empty all-NaN DataArray used as the common grid for screening."""
    minx, miny, maxx, maxy = bounds
    width = int(np.ceil((maxx - minx) / resolution))
    height = int(np.ceil((maxy - miny) / resolution))
    transform = _rasterio_from_origin(minx, maxy, resolution, resolution)
    canvas = xr.DataArray(
        np.full((height, width), np.nan, dtype="float32"), dims=("y", "x"),
        coords={"y": [maxy - resolution * (i + 0.5) for i in range(height)],
                "x": [minx + resolution * (i + 0.5) for i in range(width)]},
    )
    canvas = canvas.rio.write_crs(crs)
    canvas = canvas.rio.write_transform(transform)
    return canvas.rio.write_nodata(np.nan)
 
 
def _open_overview(href: str, level: int):
    """Opens a COG at an overview level (a coarse, much smaller copy of the
    image stored inside the file), falling back to the deepest overview the
    file actually has. Reading ~4x decimated data is ~16x less data."""
    with rasterio.open(href) as src:
        n_overviews = len(src.overviews(1))
    kwargs = {"overview_level": min(level, n_overviews - 1)} if n_overviews else {}
    return rioxarray.open_rasterio(href, masked=False, **kwargs).squeeze("band", drop=True)
 
 
@dataclass(eq=False)  # eq=False -> compared by identity (fields hold numpy arrays)
class _Screen:
    item: object
    tile_ref_box: object      # tile footprint in reference_crs
    valid: np.ndarray         # bool, True where this scene has real data, on the screening canvas
    coverage: float           # valid fraction within this tile's own AOI-overlap region
 
 
def _screen_candidate(item, aoi_ref_box, reference_crs: str, canvas):
    """Measures one candidate scene's real valid-pixel map (DN == 0 is
    no-data) on the common screening canvas, reading only a coarse COG
    overview. Returns a _Screen, or None if the scene doesn't overlap the AOI."""
    def _load(href):
        da = _open_overview(href, SCREEN_OVERVIEW_LEVEL)
        tile_ref_box = gpd.GeoSeries([box(*da.rio.bounds())], crs=da.rio.crs).to_crs(reference_crs).iloc[0]
        region = tile_ref_box.intersection(aoi_ref_box)
        if region.is_empty:
            return tile_ref_box, region, None
        try:
            clipped = _clip_to_box(da, *aoi_ref_box.bounds, src_crs=reference_crs)
        except NoDataInBounds:
            return tile_ref_box, region, None
        clipped = _mask_s2_nodata(clipped).astype("float32").load()  # .load() = read happens here, inside the retry
        clipped = clipped.rio.write_nodata(np.nan)
        aligned = clipped.rio.reproject_match(canvas, resampling=RioResampling.nearest)
        return tile_ref_box, region, np.isfinite(aligned.values)
 
    tile_ref_box, region, valid = _with_retries(
        _load, item, COVERAGE_CHECK_BAND, f"screen {item.id}"
    )
    if valid is None:
        return None
    in_region = geometry_mask([region], out_shape=canvas.shape,
                              transform=canvas.rio.transform(), invert=True)
    coverage = float(valid[in_region].mean()) if in_region.any() else 0.0
    return _Screen(item=item, tile_ref_box=tile_ref_box, valid=valid, coverage=coverage)
 
 
def _pick_best_per_tile(screens: list) -> "_Screen":
    """Best scene for one tile. `screens` must be sorted by cloud cover
    ascending; a later (cloudier) scene replaces the current pick only if it
    has clearly more real coverage (> 1 percentage point more)."""
    best = None
    for s in screens:
        if best is None or s.coverage > best.coverage + 0.01:
            best = s
    return best
 
 
def _greedy_gap_fill(remaining: list, union: np.ndarray,
                     target: float = FILL_TARGET_COVERAGE,
                     max_scenes: int = MAX_FILL_SCENES,
                     min_gain: float = MIN_FILL_GAIN):
    """Greedily adds the scene that fills the most still-empty canvas pixels,
    until `target` coverage is reached, `max_scenes` are used, or no
    remaining scene adds at least `min_gain` of the canvas.
    Returns (list of (screen, gain_fraction), final_union)."""
    union = union.copy()
    remaining = list(remaining)
    chosen = []
    while remaining and len(chosen) < max_scenes and union.mean() < target:
        gains = [(np.count_nonzero(s.valid & ~union) / union.size, s) for s in remaining]
        gain, best = max(gains, key=lambda g: g[0])
        if gain < min_gain:
            break
        chosen.append((best, gain))
        union |= best.valid
        remaining = [s for s in remaining if s is not best]
    return chosen, union
 
 
def _select_scenes_with_gap_fill(candidates_by_tile: dict, aoi_ref_box, reference_crs: str):
    """For each kept tile: screens every candidate scene (cheaply, in
    parallel), picks the best one, then adds fill scenes (any tile, any
    date in the search window) that cover what the best scenes miss.
 
    Returns (primary_screens {tile_id: _Screen}, fill_screens [(_Screen, gain)],
             valid_coverage_fraction of the AOI+buffer after gap-fill)."""
    canvas = _build_screen_canvas(aoi_ref_box.bounds, SCREEN_RES_M, reference_crs)
    jobs = [(tile_id, item) for tile_id, cands in candidates_by_tile.items() for item in cands]
    print(f"Screening {len(jobs)} candidate scene(s) from a coarse COG overview "
          f"(~{SCREEN_RES_M:.0f} m) -- far less data than reading full resolution...")
 
    def _run(job):
        tile_id, item = job
        return tile_id, _screen_candidate(item, aoi_ref_box, reference_crs, canvas)
 
    if SCREEN_WORKERS > 1:
        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as pool:
            outputs = list(pool.map(_run, jobs))
    else:
        outputs = [_run(j) for j in jobs]
 
    screens_by_tile = {}
    for tile_id, screen in outputs:          # jobs were built in cloud-ascending order per tile
        if screen is None:
            print(f"    tile {tile_id}: a candidate does not actually overlap the AOI -- skipping")
            continue
        screens_by_tile.setdefault(tile_id, []).append(screen)
        print(f"    tile {tile_id}: {_scene_label(screen.item)} "
              f"-> {screen.coverage*100:.1f}% valid within its own AOI-overlap region")
 
    primaries = {}
    for tile_id in candidates_by_tile:
        if not screens_by_tile.get(tile_id):
            raise ValueError(
                f"No candidate for tile {tile_id} actually overlaps the AOI. "
                f"This shouldn't happen after pre-filtering -- investigate the "
                f"tile grouping logic."
            )
        primaries[tile_id] = _pick_best_per_tile(screens_by_tile[tile_id])
        p = primaries[tile_id]
        flag = "" if p.coverage >= MIN_VALID_COVERAGE else \
            f"  <-- below {MIN_VALID_COVERAGE*100:.0f}%; will try to fill the gap from other scenes"
        print(f"  tile {tile_id}: best scene {_scene_label(p.item)} "
              f"({p.coverage*100:.1f}% valid){flag}")
 
    union = np.zeros(canvas.shape, dtype=bool)
    for p in primaries.values():
        union |= p.valid
    primary_cov = float(union.mean())
    print(f"Best scenes together have real data over {primary_cov*100:.1f}% of the AOI+buffer.")
 
    chosen_ids = {id(p) for p in primaries.values()}
    remaining = [s for ss in screens_by_tile.values() for s in ss if id(s) not in chosen_ids]
    fills, union = _greedy_gap_fill(remaining, union)
    for s, gain in fills:
        print(f"  gap-fill: adding {_scene_label(s.item)} -> +{gain*100:.1f}% of the AOI+buffer")
    final_cov = float(union.mean())
    if fills:
        print(f"After gap-fill: real data over {final_cov*100:.1f}% of the AOI+buffer "
              f"(fill pixels come from a different date/orbit than the best scenes).")
    if final_cov < FILL_TARGET_COVERAGE:
        print(f"  WARNING: {(1 - final_cov)*100:.1f}% of the AOI+buffer has no valid data in ANY "
              f"candidate scene. It will be NaN in the output. Try widening date_window_days "
              f"or raising cloud_thresh to bring in more scenes.")
    return primaries, fills, final_cov


def _search_and_select_tiles(bbox: list, target_date: str, cloud_thresh: int = 20,
                              date_window_days: int = 10, clip_buffer_m: float = 1500.0) -> dict:
    """
    Shared logic for finding, filtering, and selecting the best-covering
    Sentinel-2 tile(s) for an AOI. Used by both get_sentinel2_bands() (to
    actually fetch band data) and plot_tile_coverage() (to visualize the
    selection without fetching any full-resolution band data) -- keeping
    this in one place guarantees the plot always shows exactly what
    get_sentinel2_bands() would actually do, not a separate approximation
    of it.
 
    Returns a dict with:
        reference_crs        -- metric CRS used for all area/overlap math
        aoi_box               -- the AOI itself (no buffer), in reference_crs
        aoi_ref_box            -- AOI + clip_buffer_m, in reference_crs
        all_tile_footprints    -- {tile_id: (footprint_box, overlap_fraction, kept: bool)}
                                   for EVERY candidate tile found, including
                                   ones filtered out as negligible slivers
        selected_items         -- list of chosen STAC items (one per kept tile)
        selected_footprints    -- {tile_id: footprint_box} for kept tiles only
        covered_geom            -- union of all selected tiles' AOI-overlap regions
        total_coverage_fraction -- covered_geom.area / aoi_ref_box.area
    """
    center_date = datetime.strptime(target_date, "%Y-%m-%d")
    start = (center_date - timedelta(days=date_window_days)).strftime("%Y-%m-%d")
    end = (center_date + timedelta(days=date_window_days)).strftime("%Y-%m-%d")
 
    search = _CATALOG.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lt": cloud_thresh}},
    )
    items = list(search.items())
    if not items:
        raise ValueError(
            f"No Sentinel-2 scenes found for bbox={bbox} within "
            f"{date_window_days} days of {target_date} with cloud cover "
            f"< {cloud_thresh}%. Try widening date_window_days or relaxing "
            f"cloud_thresh."
        )
 
    candidates_by_tile = {}
    for item in items:
        tile_id = item.properties.get("s2:mgrs_tile", item.id)
        candidates_by_tile.setdefault(tile_id, []).append(item)
    for tile_id in candidates_by_tile:
        candidates_by_tile[tile_id].sort(key=lambda it: it.properties["eo:cloud_cover"])
 
    center_lon, center_lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    reference_crs = _utm_epsg_for(center_lon, center_lat)
    aoi_box = gpd.GeoSeries([box(*bbox)], crs="EPSG:4326").to_crs(reference_crs).iloc[0]
    aoi_bounds = aoi_box.bounds
    aoi_ref_box = box(
        aoi_bounds[0] - clip_buffer_m, aoi_bounds[1] - clip_buffer_m,
        aoi_bounds[2] + clip_buffer_m, aoi_bounds[3] + clip_buffer_m,
    )
 
    print(f"Found {len(candidates_by_tile)} candidate Sentinel-2 tile(s) intersecting the search bbox: "
          f"{list(candidates_by_tile.keys())}")
    print(f"Checking each tile's real overlap with the AOI+buffer (reference CRS {reference_crs})...")
 
    all_tile_footprints = {}
    filtered_candidates_by_tile = {}
    for tile_id, candidates in candidates_by_tile.items():
        probe = _with_retries(
            lambda href: rioxarray.open_rasterio(href, masked=False),
            candidates[0], COVERAGE_CHECK_BAND, f"probe {tile_id}",
        )
        tile_ref_box = gpd.GeoSeries([box(*probe.rio.bounds())], crs=probe.rio.crs).to_crs(reference_crs).iloc[0]
        overlap_fraction = tile_ref_box.intersection(aoi_ref_box).area / aoi_ref_box.area
        print(f"  tile {tile_id}: overlaps {overlap_fraction*100:.2f}% of the AOI+buffer area")
 
        kept = overlap_fraction >= MIN_TILE_OVERLAP_FRACTION
        all_tile_footprints[tile_id] = (tile_ref_box, overlap_fraction, kept)
        if not kept:
            print(f"    Skipping -- below the {MIN_TILE_OVERLAP_FRACTION*100:.1f}% threshold "
                  f"(negligible corner sliver, likely not worth the processing cost).")
            continue
        filtered_candidates_by_tile[tile_id] = candidates
 
    if not filtered_candidates_by_tile:
        raise ValueError("No tiles with meaningful overlap found for this AOI after filtering.")
 
    print(f"Proceeding with {len(filtered_candidates_by_tile)} tile(s) after filtering: "
          f"{list(filtered_candidates_by_tile.keys())}")
    print("Selecting the best-covering scene per tile (checked against each tile's own "
          "AOI-overlap region, not just whole-tile cloud %)...")
 
    primaries, fills, valid_coverage_fraction = _select_scenes_with_gap_fill(
        filtered_candidates_by_tile, aoi_ref_box, reference_crs
    )
    # Order matters: manual_merge() is "first valid pixel wins", so the best
    # scene of every tile goes first and the gap-fill scenes come after them
    # (they only ever supply pixels the best scenes don't have).
    selected_items = [p.item for p in primaries.values()] + [s_.item for s_, _ in fills]
    selected_footprints = {tile_id: p.tile_ref_box for tile_id, p in primaries.items()}
    covered_geom = None
    for tile_id, p in primaries.items():
        piece = p.tile_ref_box.intersection(aoi_ref_box)
        covered_geom = piece if covered_geom is None else covered_geom.union(piece)
 
    total_coverage_fraction = covered_geom.area / aoi_ref_box.area if aoi_ref_box.area > 0 else 0.0
    tile_summary = ", ".join(_scene_label(p.item) for p in primaries.values())
    print(f"Final selection ({len(primaries)} best scene(s) + {len(fills)} gap-fill scene(s)): "
          f"{tile_summary}" + "".join(f" + fill {_scene_label(s_.item)}" for s_, _ in fills))
    print(f"Combined selected tiles cover {total_coverage_fraction*100:.1f}% of the requested AOI+buffer area.")
    if total_coverage_fraction < 0.999:
        missing_bounds = aoi_ref_box.difference(covered_geom).bounds
        print(f"  WARNING: {(1 - total_coverage_fraction)*100:.1f}% of the AOI+buffer is not covered "
              f"by ANY selected tile -- a genuine data gap, not fixable by better tile selection. "
              f"Missing region bounds (in {reference_crs}): {missing_bounds}. "
              f"This will show up as NaN in the final output; consider whether your AOI extends "
              f"beyond available Sentinel-2 coverage, or try a different date range.")
 
    return {
        "reference_crs": reference_crs,
        "aoi_box": aoi_box,
        "aoi_ref_box": aoi_ref_box,
        "all_tile_footprints": all_tile_footprints,
        "selected_items": selected_items,
        "selected_footprints": selected_footprints,
        "covered_geom": covered_geom,
        "total_coverage_fraction": total_coverage_fraction,
        "fill_items": [s_.item for s_, _ in fills],
        "valid_coverage_fraction": valid_coverage_fraction,
    }

def plot_tile_coverage(bbox: list, target_date: str, cloud_thresh: int = 20,
                        date_window_days: int = 10, clip_buffer_m: float = 1500.0, ax=None,
                        precomputed_selection: dict = None):
    """
    Visualizes Sentinel-2 tile coverage against your AOI: which tiles were
    found, which were filtered out as negligible slivers, which were
    selected, and how much of the AOI+buffer each one actually covers.
 
    Run this BEFORE calling get_sentinel2_bands() -- it only reads tile
    footprint metadata and one cheap reference band per candidate (the
    same coverage check get_sentinel2_bands() does internally), so it's
    fast and won't trigger any large data transfer. It's the fastest way
    to sanity-check tile selection without waiting on a full band fetch.
 
    IMPORTANT -- for the plot to be guaranteed consistent with what
    get_sentinel2_bands() actually fetches, compute the selection ONCE
    and pass it to both:
 
        result = search_and_select_tiles(bbox, target_date, ...)
        plot_tile_coverage(bbox, target_date, precomputed_selection=result)
        band_arrays = get_sentinel2_bands(bbox, target_date, precomputed_selection=result)
 
    If you instead call plot_tile_coverage() and get_sentinel2_bands()
    separately without `precomputed_selection`, each one runs its own
    independent STAC search -- there's no guarantee both searches return
    identical tiles/scenes, so a plot showing full coverage doesn't
    strictly guarantee the actual fetch will match it. Passing the same
    precomputed result to both eliminates that gap entirely.
 
    Returns (fig, ax, result) -- `result` is the same diagnostic dict
    _earch_and_select_tiles() returns, in case you want to inspect the
    numbers directly rather than just look at the plot.
    """
 
    result = precomputed_selection or _search_and_select_tiles(
        bbox, target_date, cloud_thresh, date_window_days, clip_buffer_m
    )
 
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 10))
    else:
        fig = ax.figure
 
    def plot_polygon(geom, **kwargs):
        x, y = geom.exterior.xy
        ax.fill(x, y, **kwargs)
 
    # AOI itself (no buffer) -- solid black outline, unfilled
    plot_polygon(result["aoi_box"], facecolor="none", edgecolor="black",
                 linewidth=2, label="AOI", zorder=5)
    # AOI + buffer -- dashed outline, unfilled
    plot_polygon(result["aoi_ref_box"], facecolor="none", edgecolor="black",
                 linewidth=1, linestyle="--", label="AOI + buffer", zorder=4)
 
    colors = plt.cm.tab10.colors
    for i, (tile_id, (footprint, overlap_fraction, kept)) in enumerate(result["all_tile_footprints"].items()):
        if not kept:
            plot_polygon(footprint, facecolor="lightgray", edgecolor="gray",
                         alpha=0.4, linestyle=":", linewidth=1, zorder=1)
            ax.annotate(f"{tile_id}\n(filtered, {overlap_fraction*100:.1f}%)",
                        (footprint.centroid.x, footprint.centroid.y),
                        ha="center", fontsize=8, color="gray", zorder=2)
        else:
            color = colors[i % len(colors)]
            selected_footprint = result["selected_footprints"][tile_id]
            plot_polygon(selected_footprint, facecolor=color, edgecolor=color,
                         alpha=0.35, linewidth=1.5, zorder=3)
            ax.annotate(tile_id, (selected_footprint.centroid.x, selected_footprint.centroid.y),
                        ha="center", fontsize=10, fontweight="bold", color=color, zorder=4)
 
    ax.set_aspect("equal")
    ax.set_xlabel(f"Easting ({result['reference_crs']})")
    ax.set_ylabel("Northing")
    ax.set_title(f"Sentinel-2 tile coverage vs. AOI\n"
                 f"Combined coverage: {result['total_coverage_fraction']*100:.1f}%")
    ax.legend(loc="upper right", fontsize=8)
 
    minx, miny, maxx, maxy = result["aoi_ref_box"].bounds
    margin = max(maxx - minx, maxy - miny) * 0.15
    ax.set_xlim(minx - margin, maxx + margin)
    ax.set_ylim(miny - margin, maxy + margin)
 
    return fig, ax, result


def get_sentinel2_bands(bbox: list, target_date: str, cloud_thresh: int = 20,
                         date_window_days: int = 10, clip_buffer_m: float = 1500.0,
                         precomputed_selection: dict = None,
                         verbose_tile_diagnostics: bool = False) -> dict:
    """
    Searches Planetary Computer for Sentinel-2 L2A scenes covering `bbox`
    near `target_date`, and opens each of the 10 target bands as a
    rioxarray DataArray -- MOSAICKING MULTIPLE TILES TOGETHER when the AOI
    spans more than one Sentinel-2 tile.
 
    WHY MOSAICKING MATTERS: Sentinel-2 imagery is distributed as
    individual ~110km x 110km tiles aligned to the MGRS 100km grid. A
    single scene only covers one such tile. Any AOI wider than ~100km in
    either dimension -- or one that simply happens to straddle a tile
    boundary, which can happen at much smaller AOI sizes depending on
    where the boundary falls -- will NOT be fully covered by a single
    scene. Requesting just the single least-cloudy scene silently returns
    a raster truncated at the tile edge, with no error.
 
    WHY THIS FUNCTION CLIPS BEFORE MERGING (performance-critical): each
    Sentinel-2 tile is enormous at full resolution (roughly 10,980 x
    10,980 pixels per 10m band, several hundred MB per band per tile).
    merge_arrays() needs actual pixel data, which forces a read -- if that
    read isn't restricted to the AOI first, opening 10 bands across
    multiple full tiles can mean many GB pulled over the network, taking
    many minutes and risking a mid-transfer failure (a network timeout,
    or Planetary Computer's short-lived signed URLs expiring before the
    transfer completes -- a likely cause of an opaque rasterio read
    error after a long hang). Each tile is clipped to the AOI (padded by
    clip_buffer_m) via rio.clip_box() IMMEDIATELY after opening and
    BEFORE merge_arrays() is called. rioxarray's clip_box() only needs
    the raster's coordinate/transform metadata (already available once a
    file is opened) to compute the clip window -- it does not itself
    trigger a full-resolution pixel read, so this keeps every subsequent
    read small regardless of how large the source tile is.
 
    APPROACH: search across a date window (not just the exact target
    date), group results by MGRS tile ID, and for each tile select the
    candidate scene with the best ACTUAL VALID-PIXEL COVERAGE within the
    AOI (see _select_scenes_with_gap_fill) -- not simply the lowest
    whole-tile cloud cover, since a scene's real imaged swath may not
    cover the specific part of the tile your AOI overlaps even when its
    overall cloud statistic looks good. Each selected tile is then
    clipped to the AOI and merged into one continuous DataArray per band.
 
    date_window_days: how many days before/after target_date to search.
    Widen this if some tiles in your AOI don't have a low-cloud scene
    within the default 10-day window.
 
    clip_buffer_m: padding (in meters) applied when clipping each tile to
    the AOI before merging. Should be at least as large as the buffer_m
    you'll later pass to clip_bands_to_aoi() (which itself should be at
    least your largest focal buffer distance) -- this is a coarse,
    network-efficiency clip, not the final precise one; clip_bands_to_aoi()
    still runs afterward to produce the exact final extent.
 
    precomputed_selection: pass the dict returned by _search_and_select_tiles()
    (or by plot_tile_coverage(), which returns it as its third value) to
    reuse an already-computed tile selection instead of searching again.
    STRONGLY RECOMMENDED whenever you've already called plot_tile_coverage()
    to sanity-check coverage -- calling both functions independently
    triggers two separate STAC searches with no guarantee they return
    identical results, so a plot showing full coverage would not
    strictly guarantee the actual fetch matches it. Passing the same
    precomputed result to both closes that gap completely:
 
        result = _search_and_select_tiles(bbox, target_date, cloud_thresh, date_window_days, clip_buffer_m)
        plot_tile_coverage(bbox, target_date, precomputed_selection=result)
        band_arrays = get_sentinel2_bands(bbox, target_date, precomputed_selection=result)
 
    verbose_tile_diagnostics: if True, prints per-tile shape, bounds,
    resolution, dtype, and valid-pixel fraction for every band, both
    BEFORE and immediately AFTER the merge call. Use this when a merged
    band looks wrong (e.g. only partially populated) despite tile
    selection reporting full AOI coverage -- it tells you whether the
    problem is in what each individual tile contributes (something wrong
    upstream of the merge) or specifically in how merge_arrays combines
    them (the merge call itself). Off by default since it forces early
    materialization of every tile's pixel data for the printed valid-
    fraction stats, which adds some overhead -- not something you want
    running by default in production use.
    """
    result = precomputed_selection or _search_and_select_tiles(
        bbox, target_date, cloud_thresh, date_window_days, clip_buffer_m
    )
    selected_items = result["selected_items"]
    reference_crs = result["reference_crs"]
    aoi_ref_box = result["aoi_ref_box"]
 
    def build_reference_grid(bounds, resolution, crs):
        """
        Builds an empty (all-NaN) canvas DataArray at the exact target
        extent and resolution, used as the alignment target for
        _manual_merge() below.
        """
        minx, miny, maxx, maxy = bounds
        width = int(np.ceil((maxx - minx) / resolution))
        height = int(np.ceil((maxy - miny) / resolution))
        transform = _rasterio_from_origin(minx, maxy, resolution, resolution)
        canvas = np.full((height, width), np.nan, dtype="float32")
        da = xr.DataArray(
            canvas, dims=("y", "x"),
            coords={"y": [maxy - resolution * (i + 0.5) for i in range(height)],
                    "x": [minx + resolution * (i + 0.5) for i in range(width)]},
        )
        da = da.rio.write_crs(crs)
        da = da.rio.write_transform(transform)
        da = da.rio.write_nodata(np.nan)
        return da
 
    def manual_merge(tile_arrays, reference_grid):
        """
        Explicit, manually-verified alternative to rioxarray's
        merge_arrays(). Reprojects/resamples each tile onto the EXACT
        same target grid as `reference_grid` via reproject_match(), then
        fills the output using simple "only write where still empty"
        logic on plain numpy arrays.
 
        WHY THIS EXISTS: extensive debugging (synthetic reproductions
        matching real tile geometry exactly, real file-backed lazy and
        eager arrays, nodata-attribute checks, direct pixel-value
        inspection) found merge_arrays() silently dropping a large
        fraction of genuinely valid data for certain real multi-tile
        Sentinel-2 inputs, with no root cause identified in any
        synthetic reproduction attempt. Rather than depend on a library
        function exhibiting unexplained behavior, this reimplements the
        same "first valid value wins" semantics using only well-tested,
        independently-verifiable operations (reproject_match for
        alignment, then plain boolean-masked numpy assignment for
        compositing).
        """
        output = np.full(reference_grid.shape, np.nan, dtype="float32")
        for da in tile_arrays:
            aligned = da.rio.reproject_match(reference_grid)
            vals = aligned.values
            mask = np.isnan(output) & ~np.isnan(vals)
            output[mask] = vals[mask]
        return reference_grid.copy(data=output)
 
    def open_and_clip(item, tile_label: str, band_name: str):
        """Opens one band from one tile, clips it to the AOI (+ buffer),
        and reprojects it to `reference_crs` IMMEDIATELY -- not deferred
        until the merge step. Northern Virginia (and any AOI near -78
        longitude) can straddle the UTM zone 17N/18N boundary, so tiles
        covering the same AOI may legitimately come from different native
        CRSs. Reprojecting late (only inside the merge loop, keyed off
        whichever tile happened to be first) makes every intermediate
        array's bounds hard to compare/debug and adds a failure point
        right before the merge call. Reprojecting here means every
        returned array is always already in reference_crs -- bounds
        printed at any point downstream are directly comparable, and the
        merge step itself no longer needs any CRS branching at all.
 
        nodata=np.nan is passed explicitly to reproject() rather than
        relying on the array's nodata being auto-detected -- this is a
        known GDAL/rasterio gotcha: without an explicit nodata value,
        areas outside the source array's original extent can be filled
        with an unexpected default (e.g. 0) instead of NaN after
        reprojection, which would let merge_arrays mistake "no real data
        here" for a valid measurement.
        """
        print(f"  Opening {band_name} for tile {tile_label}...")
 
        def _load(href):
            da = rioxarray.open_rasterio(href, masked=True).squeeze("band", drop=True)
            clipped = _clip_to_box(
                da,
                aoi_ref_box.bounds[0], aoi_ref_box.bounds[1], aoi_ref_box.bounds[2], aoi_ref_box.bounds[3],
                src_crs=reference_crs,
            )
            # zeros are no-data, not valid pixels; .load() makes the network
            # read happen here, inside the retry, with a freshly signed URL
            return _mask_s2_nodata(clipped).load()
 
        clipped = _with_retries(_load, item, band_name, f"{band_name} {tile_label}")
        if clipped.rio.crs != reference_crs:
            print(f"    Reprojecting tile {tile_label} from {clipped.rio.crs} to {reference_crs}...")
            clipped = clipped.rio.reproject(reference_crs, nodata=np.nan)
        return clipped
 
    band_arrays = {}
    for b in BAND_NAMES:
        # tile_label includes the date so a gap-fill scene from the same tile
        # as a best scene can be told apart in the diagnostics below
        tile_ids = [f"{item.properties.get('s2:mgrs_tile', item.id)}@{item.properties['datetime'][:10]}"
                    for item in selected_items]
        tile_arrays = [open_and_clip(item, label, b)
                       for item, label in zip(selected_items, tile_ids)]
 
        if verbose_tile_diagnostics:
            print(f"  --- Per-tile diagnostics for {b}, BEFORE merge ---")
            for tile_id, da in zip(tile_ids, tile_arrays):
                valid_frac = float(np.isfinite(da.values).mean()) if da.size else 0.0
                print(f"    {tile_id}: shape={da.shape}, "
                      f"bounds={tuple(round(x, 1) for x in da.rio.bounds())}, "
                      f"resolution={tuple(round(r, 3) for r in da.rio.resolution())}, "
                      f"dtype={da.dtype}, declared_nodata={da.rio.nodata}, "
                      f"valid={valid_frac*100:.1f}%")
 
            if len(tile_arrays) > 1:
                # Incremental merge: add one tile at a time and track valid %
                # after each addition. This localizes EXACTLY which tile's
                # inclusion causes a drop in valid coverage.
                print(f"  --- Incremental merge trace for {b} (using manual_merge) ---")
                band_res = abs(tile_arrays[0].rio.resolution()[0])
                prev_valid = float(np.isfinite(tile_arrays[0].values).mean())
                print(f"    after {tile_ids[0]} alone: valid={prev_valid*100:.1f}%")
                for n in range(2, len(tile_arrays) + 1):
                    running = tile_arrays[:n]
                    running_bounds = [da.rio.bounds() for da in running]
                    union_minx = min(b[0] for b in running_bounds)
                    union_miny = min(b[1] for b in running_bounds)
                    union_maxx = max(b[2] for b in running_bounds)
                    union_maxy = max(b[3] for b in running_bounds)
                    ref_grid = build_reference_grid(
                        (union_minx, union_miny, union_maxx, union_maxy), band_res, reference_crs
                    )
                    m = manual_merge(running, ref_grid)
                    v = float(np.isfinite(m.values).mean())
                    delta = (v - prev_valid) * 100
                    flag = " <-- DROP" if delta < -1 else ""
                    print(f"    after adding {tile_ids[n-1]}: shape={m.shape}, valid={v*100:.1f}% "
                          f"(change: {delta:+.1f}pp){flag}")
                    prev_valid = v
 
        # Every array is already in reference_crs (handled inside
        # open_and_clip). Using manual_merge() instead of rioxarray's
        # merge_arrays() -- see manual_merge()'s docstring for why:
        # merge_arrays() was found to silently drop a large fraction of
        # genuinely valid data for real multi-tile inputs, with no root
        # cause identified despite extensive debugging. manual_merge()
        # reimplements the same semantics using only independently
        # verifiable operations.
        if len(tile_arrays) == 1:
            band_arrays[b] = tile_arrays[0]
        else:
            band_res = abs(tile_arrays[0].rio.resolution()[0])
            reference_grid = build_reference_grid(aoi_ref_box.bounds, band_res, reference_crs)
            band_arrays[b] = manual_merge(tile_arrays, reference_grid)
 
        if verbose_tile_diagnostics:
            merged = band_arrays[b]
            merged_valid = float(np.isfinite(merged.values).mean()) if merged.size else 0.0
            print(f"    FINAL MERGED: shape={merged.shape}, "
                  f"bounds={tuple(round(x, 1) for x in merged.rio.bounds())}, "
                  f"valid={merged_valid*100:.1f}%")
 
        print(f"  {b} done ({len(tile_arrays)} tile(s) merged).")
 
    return band_arrays

# ---------------------------------------------------------------------------
# STEP 1b: Clip and resample all bands to a common 10 m grid (in-memory)
# ---------------------------------------------------------------------------

def clip_bands_to_aoi(band_arrays: dict, bbox: list, buffer_m: float = 1000.0) -> dict:
    """
    Clips each band DataArray down to the study area, padded by `buffer_m`
    on all sides (default: the largest focal buffer distance, 1000 m) so
    focal buffers computed later still have valid data at the AOI's edges.
 
    Planetary Computer serves Sentinel-2 bands as full ~110 km MGRS tiles 
    (10,000+pixels per side at 10 m) regardless of how small your search 
    `bbox` was -- the bbox only filters *which* scenes are returned, it doesn't
    crop them. Without this clip, every focal-buffer convolution in Step 3
    runs over roughly 100x more pixels than a typical city needs.
 
    `bbox` must be the same [west, south, east, north] (EPSG:4326) passed
    to get_sentinel2_bands(). Call this immediately after Step 1, before
    resampling.
    """
    aoi = box(*bbox)
    clipped = {}
    for name, da in band_arrays.items():
        aoi_projected = gpd.GeoSeries([aoi], crs="EPSG:4326").to_crs(da.rio.crs).iloc[0]
        minx, miny, maxx, maxy = aoi_projected.bounds
        clipped[name] = da.rio.clip_box(
            minx=minx - buffer_m, miny=miny - buffer_m,
            maxx=maxx + buffer_m, maxy=maxy + buffer_m,
        )
    sample_shape = next(iter(clipped.values())).shape
    print(f"Clipped bands to AOI + {buffer_m}m buffer. New shape: {sample_shape} "
          f"(compare to full-tile shape of ~10,980 x 10,980 at 10m)")
    return clipped


# ---------------------------------------------------------------------------
# STEP 2: Clip and resample all bands to a common 10 m grid (in-memory)
# ---------------------------------------------------------------------------


def resample_band_to_10m(da, target_res: float = 10.0):
    """
    Resamples a single rioxarray DataArray band to `target_res` using
    bilinear resampling, matching the paper's approach of putting all bands
    on a pixel-by-pixel comparable grid before buffer creation.

    Works entirely in memory -- no intermediate GeoTIFF is written. If the
    band is already at the target resolution, it's returned unchanged.
    """
    current_res = abs(da.rio.resolution()[0])
    if np.isclose(current_res, target_res):
        return da
    return da.rio.reproject(
        da.rio.crs,
        resolution=target_res,
        resampling=RioResampling.bilinear,
    )

def resample_all_bands_to_10m(band_arrays: dict, target_res: float = 10.0) -> dict:
    """
    Applies resample_band_to_10m() to every band in the dict, returning a
    new dict of DataArrays all sharing the same 10 m grid. Bands already at
    10 m (B02, B03, B04, B08) are passed through unchanged; 20 m bands are
    upsampled.
    """
    resampled = {name: resample_band_to_10m(da, target_res) for name, da in band_arrays.items()}
    shapes = {name: da.shape for name, da in resampled.items()}
    print(f"Resampled all bands to {target_res} m grid. Shapes: {shapes}")
    return resampled

def dataarray_to_numpy(da) -> np.ndarray:
    """
    Converts a resampled rioxarray DataArray to a plain 2D numpy array,
    which is what build_all_focal_buffers() (Step 3) expects. This is the
    one point where data is pulled out of the lazy/xarray representation.

    Squeezes out any leftover singleton 'band' dimension -- rioxarray keeps
    a band axis (size 1 for a single-band file) through most operations,
    including .rio.reproject(), and scipy.ndimage.convolve requires the
    kernel and input array to have the same number of dimensions, so a
    stray (1, H, W) shape here will cause a shape-mismatch error in Step 3.
    """
    arr = np.squeeze(da.values).astype(np.float64)
    if arr.ndim != 2:
        raise ValueError(
            f"Expected a 2D array after squeezing, got shape {arr.shape} "
            f"from original shape {da.shape}. This DataArray may have "
            f"multiple non-singleton bands, or an unexpected extra "
            f"dimension (e.g. time)."
        )
    return arr

def profile_from_dataarray(da) -> dict:
    """
    Builds a rasterio-style profile dict (crs, transform, dimensions, etc.)
    from a resampled rioxarray DataArray. Needed because Step 3 writes
    focal buffer rasters to disk with rasterio, so it still needs a profile
    even though Steps 1-2 no longer touch rasterio directly.
    """
    return {
        "driver": "GTiff",
        "height": da.shape[-2],
        "width": da.shape[-1],
        "count": 1,
        "dtype": "float32",
        "crs": da.rio.crs,
        "transform": da.rio.transform(),
        "nodata": np.nan,
    }

# ---------------------------------------------------------------------------
# STEP 3: Build focal buffer rasters
# ---------------------------------------------------------------------------
# Compute a moving-window average for each of the 10 bands at
# each of 15 distances (from Voelkel & Shandas 2017):
FOCAL_DISTANCES_M = [50, 100, 150, 200, 250, 300, 350, 400, 450,
                      500, 600, 700, 800, 900, 1000]


def circular_kernel(radius_px: int) -> np.ndarray:
    """Builds a circular (disk) averaging kernel, radius in pixels."""
    y, x = np.ogrid[-radius_px:radius_px + 1, -radius_px:radius_px + 1]
    mask = x**2 + y**2 <= radius_px**2
    kernel = mask.astype(np.float32)
    return kernel / kernel.sum()

FFT_RADIUS_THRESHOLD_PX = 15  # kernels larger than ~31x31 px benefit from FFT convolution

def compute_focal_buffer(band_array: np.ndarray, pixel_size_m: float,
                          distance_m: float, nodata_mask: np.ndarray = None,
                          use_fft: str = "auto") -> np.ndarray:
    """
    Computes the focal (circular moving-window) mean of a band at a given
    real-world distance. This is the raster transformation described in
    Section 2.4.1 of the paper -- each output pixel equals the average of
    all input pixels within `distance_m` of it.

    PERFORMANCE: direct convolution (scipy.ndimage.convolve) costs
    O(pixels x kernel_area) -- at large distances (e.g. 1000 m at 10 m
    resolution, radius=100px, kernel=201x201=~40,000 taps) this becomes
    extremely slow. FFT-based convolution (scipy.signal.fftconvolve) costs
    O(pixels x log(pixels)) instead, and is dramatically faster once the
    kernel is more than ~30x30 px. `use_fft="auto"` switches automatically
    based on kernel radius; pass True/False to force one method.

    Also skips the second (nodata-mask) convolution entirely when the
    input array has no NaNs -- this is the common case once bands have
    been clipped to the AOI (see clip_bands_to_aoi), and halves the work.
    """
    radius_px = max(1, round(distance_m / pixel_size_m))
    kernel = circular_kernel(radius_px).astype(np.float32)

    arr = np.squeeze(band_array).astype(np.float32)
    if arr.ndim != 2:
        raise ValueError(
            f"compute_focal_buffer expects a 2D band array, got shape "
            f"{band_array.shape} (squeezed to {arr.shape}). Check that "
            f"dataarray_to_numpy() was called on a band with no leftover "
            f"singleton dimensions (e.g. a 'band' axis of size 1)."
        )
    if nodata_mask is not None:
        arr = np.where(nodata_mask, np.nan, arr)

    if use_fft == "auto":
        use_fft = radius_px > FFT_RADIUS_THRESHOLD_PX

    if use_fft:
        conv = lambda a, k: fftconvolve(a, k, mode="same").astype(np.float32)
    else:
        conv = lambda a, k: convolve(a, k, mode="nearest").astype(np.float32)

    has_nodata = np.isnan(arr).any()
    arr_filled = np.nan_to_num(arr, nan=0.0)

    if not has_nodata:
        # Fast path: every pixel is valid, so the buffer mean is just the
        # convolution -- no need for a second convolution to count valid
        # pixels per window. This is the common case after AOI clipping.
        return conv(arr_filled, kernel)

    # valid = (~np.isnan(arr)).astype(np.float32)
    # numerator = conv(arr_filled, kernel)
    # denominator = conv(valid, kernel)
    # denominator[denominator == 0] = np.nan

    # return (numerator / denominator).astype(np.float32)

    kernel64 = kernel.astype(np.float64)
    if use_fft:
        conv64 = lambda a, k: fftconvolve(a, k, mode="same")
    else:
        conv64 = lambda a, k: convolve(a, k, mode="nearest")

    valid = (~np.isnan(arr)).astype(np.float64)
    numerator = conv64(arr_filled.astype(np.float64), kernel64)
    denominator = conv64(valid, kernel64)

    min_weight = kernel64[kernel64 > 0].min()
    no_data = denominator < 0.5 * min_weight
    out = np.full(arr.shape, np.nan, dtype=np.float32)
    ok = ~no_data
    out[ok] = (numerator[ok] / denominator[ok]).astype(np.float32)
    return out

def _build_one_focal_buffer(band_name: str, arr: np.ndarray, dist: float,
                             pixel_size_m: float, out_dir: str, profile: dict,
                             use_fft: str) -> str:
    """Worker function for a single (band, distance) pair -- used by
    build_all_focal_buffers()'s parallel execution. Must be a top-level
    function (not a nested closure) so joblib/multiprocessing can pickle
    it and send it to worker processes."""
    focal = compute_focal_buffer(arr, pixel_size_m, dist, use_fft=use_fft)
    out_path = f"{out_dir}/focal_{band_name}_{dist}m.tif"
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(focal.astype(np.float32), 1)
    return out_path

def build_all_focal_buffers(band_arrays: dict, pixel_size_m: float,
                             out_dir: str, profile: dict,
                             n_jobs: int = -1, use_fft: str = "auto") -> list:
    """
    Loops over all 10 bands x 15 distances = 150 focal buffer rasters,
    writing each to disk and returning the list of file paths.

    n_jobs: number of parallel worker processes. -1 uses all available
    CPU cores (joblib's default). Each (band, distance) pair is
    independent, so this parallelizes cleanly -- on an 8-core machine,
    expect roughly a 5-7x speedup over the serial loop (not a full 8x,
    due to process-startup and array-pickling overhead).

    use_fft: passed through to compute_focal_buffer() for each raster --
    "auto" (default) uses FFT convolution for large-radius buffers and
    direct convolution for small ones.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    tasks = [
        (band_name, arr, dist)
        for band_name, arr in band_arrays.items()
        for dist in FOCAL_DISTANCES_M
    ]

    out_paths = Parallel(n_jobs=n_jobs, backend="loky")(
        delayed(_build_one_focal_buffer)(
            band_name, arr, dist, pixel_size_m, out_dir, profile, use_fft
        )
        for band_name, arr, dist in tasks
    )

    print(f"Built {len(out_paths)} focal buffer rasters "
          f"({len(band_arrays)} bands x {len(FOCAL_DISTANCES_M)} distances) "
          f"using n_jobs={n_jobs}")
    return out_paths


# ---------------------------------------------------------------------------
# STEP 4: Extract focal buffer values at observation points (bilinear)
# ---------------------------------------------------------------------------

def extract_values_bilinear(raster_path: str, points_gdf: gpd.GeoDataFrame) -> np.ndarray:
    """
    Extracts raster values at point locations using bilinear interpolation,
    matching the paper's handling of multiple observations falling within
    the same pixel (Section 2.4.2).
    """
    with rasterio.open(raster_path) as src:
        arr = src.read(1)
        transform = src.transform
        values = np.full(len(points_gdf), np.nan, dtype=np.float64)

        for i, geom in enumerate(points_gdf.geometry):
            x, y = geom.x, geom.y
            # Convert to fractional row/col
            col_f, row_f = ~transform * (x, y)
            col0, row0 = int(np.floor(col_f)), int(np.floor(row_f))
            dx, dy = col_f - col0, row_f - row0

            if (0 <= row0 < arr.shape[0] - 1) and (0 <= col0 < arr.shape[1] - 1):
                v00 = arr[row0, col0]
                v10 = arr[row0, col0 + 1]
                v01 = arr[row0 + 1, col0]
                v11 = arr[row0 + 1, col0 + 1]
                values[i] = (
                    v00 * (1 - dx) * (1 - dy)
                    + v10 * dx * (1 - dy)
                    + v01 * (1 - dx) * dy
                    + v11 * dx * dy
                )
    return values

def build_training_table(points_gdf: gpd.GeoDataFrame, focal_raster_paths: list,
                          temp_col: str = "temperature_c", n_jobs: int = -1) -> pd.DataFrame:
    """
    Compiles the full training table: 1 row per observation, 1 column per
    focal buffer predictor (150 columns) + 1 temperature column, matching
    the paper's 151-variable table (Section 2.4.2).

    Builds all predictor columns first (in parallel across rasters, since
    each extraction is independent) and assembles the DataFrame with a
    single pd.concat() call, rather than inserting columns one at a time
    into an existing DataFrame. Repeated `table[col] = ...` assignment in a
    loop causes the DataFrame's internal block manager to fragment,
    triggering pandas' PerformanceWarning and making each subsequent
    insertion progressively slower -- building the full set of columns
    first and concatenating once avoids this entirely.
    """
    def _extract_one(path):
        col_name = Path(path).stem.replace("focal_", "")  # e.g. "B2_50m"
        return col_name, extract_values_bilinear(path, points_gdf)

    results = Parallel(n_jobs=n_jobs, backend="loky")(
        delayed(_extract_one)(path) for path in focal_raster_paths
    )

    predictor_df = pd.DataFrame({col_name: values for col_name, values in results})
    table = pd.concat(
        [pd.Series(points_gdf[temp_col].values, name=temp_col), predictor_df],
        axis=1,
    )

    table = table.dropna()  # drop rows with missing predictor values
    return table

# ---------------------------------------------------------------------------
# STEP 5-6: Train/test split and Random Forest training
# ---------------------------------------------------------------------------
def train_rf_model(table: pd.DataFrame, target_col: str = "temperature_c",
                    test_size: float = 0.30, random_state: int = 42):
    """
    Splits data 70/30 (matching the paper) and trains a Random Forest
    regressor. Returns the fitted model and evaluation metrics.
    """
    X = table.drop(columns=[target_col])
    y = table[target_col]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state
    )

    # sklearn's RandomForestRegressor defaults are a reasonable starting
    # point; n_estimators=500 and default max_features give results
    # comparable to the paper's reported R^2 values in practice.
    rf = RandomForestRegressor(
        n_estimators=500,
        max_features="sqrt",
        n_jobs=-1,
        random_state=random_state,
        oob_score=True,  # gives an internal validation estimate too
    )
    rf.fit(X_train, y_train)

    y_pred = rf.predict(X_test)
    r2 = r2_score(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))

    print(f"Test R^2:   {r2:.4f}")
    print(f"Test RMSE:  {rmse:.4f} degrees F")
    print(f"OOB score:  {rf.oob_score_:.4f}")

    return rf, {"r2": r2, "rmse": rmse, "X_test": X_test, "y_test": y_test, "y_pred": y_pred}

# ---------------------------------------------------------------------------
# STEP 7: Predict a continuous surface across the full study area
# ---------------------------------------------------------------------------
def predict_temperature_surface(rf_model, focal_raster_paths: list,
                                 feature_order: list, out_path: str,
                                 reference_profile: dict):
    """
    Applies the trained RF model pixel-by-pixel to produce a continuous
    predicted temperature surface (Section 2.4.3, final step).

    `feature_order` must match the column order the model was trained on.

    """
    stack = []
    for path in focal_raster_paths:
        with rasterio.open(path) as src:
            stack.append(src.read(1))
    stack = np.stack(stack, axis=0)
    inside_boundary = None
    out_profile = reference_profile.copy()

    n_bands, rows, cols = stack.shape
    flat = stack.reshape(n_bands, rows * cols).T  # shape: (rows*cols, n_bands)

    valid_mask = ~np.isnan(flat).any(axis=1)
    if inside_boundary is not None:
        valid_mask = valid_mask & inside_boundary.reshape(-1)

    predictions = np.full(rows * cols, np.nan, dtype=np.float32)

    # Predict only on valid pixels (non-nodata AND, if given, inside the
    # boundary), in chunks to manage memory
    chunk_size = 500_000
    valid_idx = np.where(valid_mask)[0]
    for start in range(0, len(valid_idx), chunk_size):
        idx_chunk = valid_idx[start:start + chunk_size]
        X_chunk = pd.DataFrame(flat[idx_chunk], columns=feature_order)
        predictions[idx_chunk] = rf_model.predict(X_chunk)

    prediction_raster = predictions.reshape(rows, cols)

    out_profile.update(dtype="float32", count=1, nodata=np.nan)
    with rasterio.open(out_path, "w", **out_profile) as dst:
        dst.write(prediction_raster.astype(np.float32), 1)

    print(f"Predicted temperature surface written to {out_path} ")
    return prediction_raster
