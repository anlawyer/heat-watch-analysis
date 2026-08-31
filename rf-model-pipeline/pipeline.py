import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.enums import Resampling as RioResampling
from rasterio.transform import from_origin as _rasterio_from_origin
import matplotlib.pyplot as plt
import xarray as xr
import os

from scipy.ndimage import convolve
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score, mean_squared_error
from pathlib import Path

import pystac_client
import planetary_computer
import rioxarray
from shapely.geometry import box
from scipy.signal import fftconvolve
from joblib import Parallel, delayed
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# STEP 0.5: Clean the observation data (nodata codes, implausible values)
# ---------------------------------------------------------------------------
def clean_temperature_observations(gdf: gpd.GeoDataFrame, source_col: str = "temp_f",
                                    output_col: str = "temperature_f",
                                    nodata_values: tuple = (-9999.0, -999.0, -99.0, 9999.0),
                                    valid_range_f: tuple = (-22.0, 131.0),
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
           "temperature_f", matching what build_training_table() and
           train_rf_model() expect downstream).
        3. Flag any value outside `valid_range_f` as NaN too -- catches
           sensor glitches or undocumented sentinel codes not in your
           explicit `nodata_values` list.
        4. Drop rows with NaN in `output_col` (can't train or evaluate
           without a valid target).
        5. Print a before/after summary so you can confirm what was caught.

    `valid_range_f` defaults to (-22, 131) degF -- the Fahrenheit
    equivalent of a generous (-30, 55) degC bound for ground-level air
    temperature during a summer heat traverse campaign in the continental
    US. Tighten this if you have city- or season-specific expectations
    (e.g. Shandas et al. 2019's traverses only ran on days above the 90th
    percentile of historic highs, so a narrower lower bound would be
    reasonable for a similarly-designed campaign).
    """
    n_start = len(gdf)
    gdf = gdf.copy()

    if verbose:
        raw_min, raw_max = gdf[source_col].min(), gdf[source_col].max()
        print(f"Before cleaning: {n_start} rows, "
              f"{source_col} range [{raw_min:.2f}, {raw_max:.2f}] degF")

    # Step 1: sentinel nodata codes -> NaN
    gdf[source_col] = gdf[source_col].replace(list(nodata_values), np.nan)
    n_after_nodata = gdf[source_col].notna().sum()

    # Step 2: copy to output column (no unit conversion -- staying in degF)
    gdf[output_col] = gdf[source_col]

    # Step 3: implausible values -> NaN
    lo, hi = valid_range_f
    out_of_range = (gdf[output_col] < lo) | (gdf[output_col] > hi)
    n_out_of_range = out_of_range.sum()
    gdf.loc[out_of_range, output_col] = np.nan

    # Step 4: drop rows with no valid temperature
    gdf = gdf.dropna(subset=[output_col]).reset_index(drop=True)
    n_end = len(gdf)

    if verbose:
        print(f"  Dropped {n_start - n_after_nodata} rows matching nodata codes {nodata_values}")
        print(f"  Dropped {n_out_of_range} additional rows outside valid range {valid_range_f} degF")
        print(f"After cleaning: {n_end} rows ({n_start - n_end} total dropped, "
              f"{100 * (n_start - n_end) / n_start:.1f}%)")
        print(f"  {output_col} range now "
              f"[{gdf[output_col].min():.2f}, {gdf[output_col].max():.2f}] degF")

    if n_end < 0.5 * n_start:
        print(f"  WARNING: more than half your observations were dropped. "
              f"Double-check `nodata_values` and `valid_range_f` -- you may be "
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
 
_CATALOG = pystac_client.Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=planetary_computer.sign_inplace,
)
 
 
COVERAGE_CHECK_BAND = "B04"  # cheap reference band used to evaluate candidate scenes
MIN_VALID_COVERAGE = 0.98    # required fraction of non-NaN pixels within a tile's own AOI-overlap region
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
 
 
def _select_best_covering_item(candidates: list, aoi_ref_box, reference_crs: str, tile_id: str):
    """
    Picks the candidate scene for one MGRS tile based on ACTUAL VALID
    PIXEL COVERAGE within the AOI -- not `eo:cloud_cover` (a whole-tile
    average) and not naive np.isfinite(clipped).mean() (which silently
    measures against whatever smaller region clip_box happened to
    return, NOT the full requested AOI -- a tile covering just 5% of the
    AOI corner can report a misleading 100% by that naive metric).
 
    Correct approach: for each candidate, intersect the TILE'S OWN
    geographic bounds with the AOI (in a shared metric reference CRS) to
    get the region this tile is actually responsible for. Clip to that
    intersection specifically, then measure valid-pixel fraction within
    it. A tile that only overlaps a small sliver of the AOI will
    correctly show a small (but still potentially 100%-valid) region --
    that's fine, since other tiles cover the rest; what this catches is a
    tile whose swath has GAPS within the portion it's actually supposed
    to cover.
 
    Returns (best_item, best_coverage, tile_ref_box) -- tile_ref_box is
    this tile's own footprint in `reference_crs`, used by the caller to
    track combined coverage across all selected tiles.
    """
    best_item, best_coverage, best_tile_ref_box = None, -1.0, None
 
    for item in candidates:
        href = item.assets[COVERAGE_CHECK_BAND].href
        da = rioxarray.open_rasterio(href, masked=True).squeeze("band", drop=True)
 
        tile_ref_box = gpd.GeoSeries([box(*da.rio.bounds())], crs=da.rio.crs).to_crs(reference_crs).iloc[0]
        relevant_region = tile_ref_box.intersection(aoi_ref_box)
        if relevant_region.is_empty:
            print(f"    tile {tile_id}: candidate {item.id} does not actually overlap the AOI -- skipping")
            continue
 
        rminx, rminy, rmaxx, rmaxy = relevant_region.bounds
        clipped = _clip_to_box(da, rminx, rminy, rmaxx, rmaxy, src_crs=reference_crs)
        coverage = float(np.isfinite(clipped.values).mean()) if clipped.size else 0.0
        print(f"    tile {tile_id}: candidate {item.id} "
              f"({item.properties['eo:cloud_cover']:.1f}% cloud, {item.properties['datetime'][:10]}) "
              f"-> {coverage*100:.1f}% valid within its own AOI-overlap region")
 
        if coverage > best_coverage:
            best_item, best_coverage, best_tile_ref_box = item, coverage, tile_ref_box
        if coverage >= MIN_VALID_COVERAGE:
            return best_item, best_coverage, best_tile_ref_box
 
    if best_item is None:
        raise ValueError(
            f"No candidate for tile {tile_id} actually overlaps the AOI. "
            f"This shouldn't happen after pre-filtering -- investigate the "
            f"tile grouping logic."
        )
 
    print(f"    WARNING: no candidate for tile {tile_id} reached "
          f"{MIN_VALID_COVERAGE*100:.0f}% coverage within its own AOI-overlap region. "
          f"Using the best available ({best_item.id}, {best_coverage*100:.1f}%) -- "
          f"expect some NaN gap in the final output for this tile's portion of the AOI. "
          f"Try widening date_window_days to search for a better-covering scene.")
    return best_item, best_coverage, best_tile_ref_box
 
 
def search_and_select_tiles(bbox: list, target_date: str, cloud_thresh: int = 20,
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
        probe = rioxarray.open_rasterio(candidates[0].assets[COVERAGE_CHECK_BAND].href, masked=False)
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
 
    selected_items = []
    selected_footprints = {}
    covered_geom = None
    for tile_id, candidates in filtered_candidates_by_tile.items():
        item, coverage, tile_ref_box = _select_best_covering_item(candidates, aoi_ref_box, reference_crs, tile_id)
        selected_items.append(item)
        selected_footprints[tile_id] = tile_ref_box
        piece = tile_ref_box.intersection(aoi_ref_box)
        covered_geom = piece if covered_geom is None else covered_geom.union(piece)
 
    total_coverage_fraction = covered_geom.area / aoi_ref_box.area if aoi_ref_box.area > 0 else 0.0
    tile_summary = ", ".join(
        f"{it.properties.get('s2:mgrs_tile', '?')} "
        f"({it.properties['eo:cloud_cover']:.1f}% cloud, {it.properties['datetime'][:10]})"
        for it in selected_items
    )
    print(f"Final selection ({len(selected_items)} tile(s)): {tile_summary}")
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
 
    result = precomputed_selection or search_and_select_tiles(
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
    AOI (see _select_best_covering_item) -- not simply the lowest
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
 
    precomputed_selection: pass the dict returned by search_and_select_tiles()
    (or by plot_tile_coverage(), which returns it as its third value) to
    reuse an already-computed tile selection instead of searching again.
    STRONGLY RECOMMENDED whenever you've already called plot_tile_coverage()
    to sanity-check coverage -- calling both functions independently
    triggers two separate STAC searches with no guarantee they return
    identical results, so a plot showing full coverage would not
    strictly guarantee the actual fetch matches it. Passing the same
    precomputed result to both closes that gap completely:
 
        result = search_and_select_tiles(bbox, target_date, cloud_thresh, date_window_days, clip_buffer_m)
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
    result = precomputed_selection or search_and_select_tiles(
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
 
    def open_and_clip(href: str, tile_label: str, band_name: str):
        """Opens one band from one tile, clips it to the AOI (+ buffer),
        and reprojects it to `reference_crs` IMMEDIATELY -- not deferred
        until the merge step. Reprojecting late (only inside the merge loop, keyed off
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
        da = rioxarray.open_rasterio(href, masked=True).squeeze("band", drop=True)
        clipped = _clip_to_box(
            da,
            aoi_ref_box.bounds[0], aoi_ref_box.bounds[1], aoi_ref_box.bounds[2], aoi_ref_box.bounds[3],
            src_crs=reference_crs,
        )
        if clipped.rio.crs != reference_crs:
            print(f"    Reprojecting tile {tile_label} from {clipped.rio.crs} to {reference_crs}...")
            clipped = clipped.rio.reproject(reference_crs, nodata=np.nan)
        return clipped
 
    band_arrays = {}
    for b in BAND_NAMES:
        tile_arrays = [
            open_and_clip(item.assets[b].href, item.properties.get("s2:mgrs_tile", item.id), b)
            for item in selected_items
        ]
        tile_ids = [item.properties.get("s2:mgrs_tile", item.id) for item in selected_items]
 
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

    valid = (~np.isnan(arr)).astype(np.float32)
    numerator = conv(arr_filled, kernel)
    denominator = conv(valid, kernel)
    denominator[denominator == 0] = np.nan

    return (numerator / denominator).astype(np.float32)

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
                          temp_col: str = "temperature_f", n_jobs: int = -1) -> pd.DataFrame:
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
        [pd.Series(points_gdf[temp_col].values, name="temperature_f"), predictor_df],
        axis=1,
    )

    table = table.dropna()  # drop rows with missing predictor values
    return table

# ---------------------------------------------------------------------------
# STEP 5-6: Train/test split and Random Forest training
# ---------------------------------------------------------------------------
def train_rf_model(table: pd.DataFrame, target_col: str = "temperature_f",
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
