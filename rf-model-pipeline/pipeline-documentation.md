# UHI Random Forest Pipeline — Documentation

_Written by Claude to summarize the code after replicating the process from the_
_Shandas et al. (2019) paper._

This document explains `pipeline.py` step by step, tying each part back to the
methodology in:

> Shandas, V., Voelkel, J., Williams, J., & Hoffman, J. (2019). Integrating Satellite and
> Ground Measurements for Predicting Locations of Extreme Urban Heat. _Climate_, 7(1), 5.
> https://doi.org/10.3390/cli7010005

The pipeline picks up **after** volunteer-collected traverse temperature data has already
been gathered into a shapefile, and covers everything from satellite data acquisition
through producing a boundary-clipped, continuous predicted temperature map.

This version reflects substantial rework beyond the paper's original single-city, single-tile
methodology, driven by testing against a larger, multi-tile Northern Virginia campaign. Several
of these changes fix real bugs found only once the pipeline was run against real satellite
data at that larger scale — see the "Debugging log" section near the end for the most
significant of these.

---

## Pipeline overview

| Step | What it does                                                      | Paper section |
| ---- | ----------------------------------------------------------------- | ------------- |
| 0.5  | Clean observation data: sentinel nodata codes, implausible values | —             |
| 1    | Acquire Sentinel-2 imagery, mosaicking multiple tiles if needed   | §2.3          |
| 1b   | Clip bands to the study area (performance-critical)               | —             |
| 2    | Resample bands to a common 10 m grid                              | §2.4.2        |
| 3    | Build 150 focal buffer rasters (10 bands × 15 distances)          | §2.4.1        |
| 4    | Extract buffer values at observation points (bilinear)            | §2.4.2        |
| 5    | Compile the model-ready training table                            | §2.4.2        |
| 6    | Train a Random Forest with fixed hyperparameters                  | §2.4.3        |
| 7    | Predict a continuous temperature surface                          | §2.4.3        |

---

## Step 0.5: Cleaning the observation data

**Not in the paper.** Real crowdsourced datasets commonly encode missing/invalid readings
with a sentinel value (e.g. `-9999.0`) rather than a true null.

**Why this matters for Random Forest specifically:** a single `-9999.0` sitting next to
real ~70–100°F values creates a squared error on the order of `(9999-85)² ≈ 99,000,000`
for that one point — enough to dominate RMSE and drive R² to extreme negative values. An
R² far below 0 (worse than predicting the mean) is a strong diagnostic signal of exactly
this kind of contamination, not a normalization issue — and **Random Forest does not need
normalized/scaled inputs at all**, since tree splits are based on value ordering, not
magnitude.

**What the script does:** `clean_temperature_observations(gdf, source_col="temp_f",
output_col="temperature_f", nodata_values=(-9999.0, -999.0, -99.0, 9999.0),
valid_range_f=(-22.0, 131.0))`:

1. Replaces known sentinel nodata codes with NaN in the source column.
2. Copies remaining valid values to `output_col`. **Values stay in Fahrenheit throughout
   this pipeline**, matching the actual observation format (`temp_f`).
3. Flags anything outside `valid_range_f` (default −22°F to 131°F, the Fahrenheit
   equivalent of a generous −30°C to 55°C bound) as NaN too — catches sensor glitches or
   undocumented sentinel codes.
4. Drops rows with no valid temperature.
5. Prints a before/after summary, with a warning if more than half the data was dropped
   (usually means the defaults don't match your actual file's conventions).

Call this immediately after loading your shapefile, before any downstream steps.

---

## Step 1: Satellite data acquisition

**What the paper did:** Used Sentinel-2 imagery, selecting 10 of the 13 available bands —
dropping bands 1, 9, and 10 (60 m atmospheric-correction bands with limited relevance to
land use/land cover). The paper worked with a single scene per city, since its three study
areas (Richmond, Baltimore, DC) each fit within one Sentinel-2 tile.

| Band | Purpose                   | Wavelength (nm) | Native resolution |
| ---- | ------------------------- | --------------- | ----------------- |
| B02  | Blue                      | 490             | 10 m              |
| B03  | Green                     | 560             | 10 m              |
| B04  | Red                       | 665             | 10 m              |
| B05  | Vegetation red edge       | 705             | 20 m              |
| B06  | Vegetation red edge       | 740             | 20 m              |
| B07  | Vegetation red edge       | 783             | 20 m              |
| B08  | Near infrared (NIR)       | 842             | 10 m              |
| B8A  | Narrow NIR                | 865             | 20 m              |
| B11  | Shortwave infrared (SWIR) | 1610            | 20 m              |
| B12  | Shortwave infrared (SWIR) | 2190            | 20 m              |

**Why this pipeline needed to go further:** your Northern Virginia campaign's AOI is large
enough to span **multiple Sentinel-2 tiles** — Sentinel-2 imagery is distributed as
individual ~110 km × 110 km tiles aligned to the MGRS 100 km grid, and any AOI wider than
that (or one that simply straddles a tile boundary at any size) needs more than one scene
to cover it fully. The pipeline handles this with several coordinated pieces:

### Tile search and selection (`search_and_select_tiles`)

This is the shared core used by both `get_sentinel2_bands()` (to fetch data) and
`plot_tile_coverage()` (to visualize the selection) — kept in one function so the two are
always guaranteed to agree with each other, rather than each running its own independent
search that might return slightly different results.

1. **Searches across a date window** (`date_window_days`, default ±10 days), not just the
   exact target date — adjacent tiles aren't always imaged on the same day.
2. **Groups results by MGRS tile ID** (`s2:mgrs_tile`), keeping every candidate scene per
   tile rather than immediately picking one.
3. **Pre-filters tiles by real geometric overlap** with the AOI+buffer, in a properly
   projected metric CRS (the AOI's own UTM zone, auto-detected via `_utm_epsg_for`). Any
   tile contributing less than `MIN_TILE_OVERLAP_FRACTION` (default 1%) of the AOI+buffer
   area is dropped before any further processing — this is what keeps tile counts sane
   when an AOI happens to sit near a grid corner where many tiles technically graze it by
   a negligible sliver.
4. **Selects the best-covering scene per tile** based on _actual valid-pixel coverage_,
   not `eo:cloud_cover` (see `_select_best_covering_item`).

### Why coverage-based selection, not cloud cover (`_select_best_covering_item`)

`eo:cloud_cover` is a **whole-tile average** — it says nothing about whether a scene's
actual imaged swath (Sentinel-2's real footprint is a rotated parallelogram, not the full
square tile) covers the specific corner of the tile your AOI overlaps. A scene with
excellent overall cloud statistics can still have a swath-edge gap running right through
your AOI, while a "worse" scene by cloud % might fully cover it.

For each candidate, this function:

1. Intersects the tile's own footprint with the AOI (in the shared metric reference CRS)
   to get the region this tile is actually responsible for.
2. Clips to _that_ region specifically (not the whole AOI) and measures the valid-pixel
   fraction within it.
3. Tries candidates in ascending cloud-cover order, but takes the first one reaching
   `MIN_VALID_COVERAGE` (default 98%) regardless of its cloud statistic.
4. If no candidate reaches the threshold, uses the best available with an explicit
   warning — telling you to widen `date_window_days` rather than silently returning a
   gappy result.

**A note on measuring coverage correctly:** `da.rio.clip_box()` does not pad a result
with NaN when the source doesn't extend as far as requested — it simply returns whatever
smaller region actually overlaps. Measuring `np.isfinite()` over that returned array's
own shape (rather than against the full requested extent) can silently report "100%
valid" for a tile that only touches 5% of your AOI. `_clip_to_box()` and
`_select_best_covering_item()` account for this explicitly by measuring against known
geometric extents, not the shape of whatever came back.

### Cross-UTM-zone handling

Northern Virginia (and any AOI near a UTM zone boundary — zone 17N/18N sits at -78°
longitude) can be covered by tiles from two different native CRSs. `open_and_clip()`
(inside `get_sentinel2_bands()`) reprojects each tile to a single `reference_crs`
**immediately** after clipping, not deferred until a later merge step — this means every
array returned is always directly comparable, and any bounds you print at any point are
meaningful without needing to check CRS first. `nodata=np.nan` is passed explicitly to
`.rio.reproject()`, since without it, areas outside a reprojected array's original extent
can silently fill with an unexpected default (e.g. 0) instead of NaN.

### Performance: clip before merge, not after

Each Sentinel-2 tile is enormous at full resolution (~10,980 × 10,980 pixels per 10 m
band, several hundred MB per band per tile). Merging needs actual pixel data, which forces
a read — if that read isn't restricted to the AOI first, fetching 10 bands across
multiple full tiles can mean many GB over the network, risking a mid-transfer failure
(a timeout, or Planetary Computer's short-lived signed URLs expiring before the transfer
completes). Every tile is clipped to the AOI (padded by `clip_buffer_m`, default 1500 m)
immediately after opening — `clip_box()` only needs coordinate/transform metadata, not a
full pixel read, to compute the window.

The module also sets standard GDAL HTTP settings for efficient COG reads (range
coalescing, avoiding unnecessary directory listings) right at import time, since these
reduce round trips per read and help both speed and the odds of finishing before a signed
URL expires.

### `plot_tile_coverage()` — visual diagnostic

Run this **before** `get_sentinel2_bands()` to sanity-check tile selection without
triggering any large data transfer — it only reads tile footprint metadata and one cheap
reference band (`B04`) per candidate, the same lightweight check used internally.

```python
result = _search_and_select_tiles(bbox, target_date, cloud_thresh, date_window_days, clip_buffer_m)
plot_tile_coverage(bbox, target_date, precomputed_selection=result)
band_arrays = get_sentinel2_bands(bbox, target_date, precomputed_selection=result)
```

The plot shows: your AOI (solid outline), AOI+buffer (dashed outline), each **selected**
tile's footprint in its own color and labeled with its MGRS ID, and any **filtered-out**
tiles in gray with their overlap percentage — so you can see at a glance both what was
used and what was excluded, and why.

**Always pass `precomputed_selection` when you've already called `plot_tile_coverage()`.**
Without it, `plot_tile_coverage()` and `get_sentinel2_bands()` each run their own
independent STAC search — there's no guarantee both return identical tiles/scenes, so a
plot showing full coverage doesn't strictly guarantee the actual fetch matches it.
Computing the selection once and passing it to both closes that gap completely.

### Merging tiles: `manual_merge()`, not `rioxarray.merge.merge_arrays()`

**This is the single most significant fix in this version of the pipeline.** Extensive
debugging (see the dedicated section near the end of this document) found that
`merge_arrays()` was silently dropping a large fraction of genuinely valid data when
combining certain real multi-tile Sentinel-2 inputs — geometric analysis showed the six
tiles covering a real AOI should combine to ~93.6% valid coverage, but `merge_arrays()`
was producing only ~47%, with no root cause identified despite many synthetic
reproduction attempts matching the real data's exact bounds, resolutions, CRS mix, and
lazy/eager loading state.

Rather than depend on a library function exhibiting unexplained behavior, `get_sentinel2_bands()`
now uses `manual_merge()` — a from-scratch reimplementation using only two independently
verifiable operations:

1. **`reproject_match()`** to align every tile onto one exact shared target grid (built by
   `build_reference_grid()`, an empty NaN canvas at the AOI+buffer extent and the band's
   native resolution).
2. **Plain boolean-masked numpy assignment** — `output[mask] = vals[mask]` wherever the
   canvas is still empty — to composite them, with no black-box compositing logic.

This was verified against the real reported bounds/resolutions from an actual 6-tile
Northern Virginia fetch (4 native tiles + 2 cross-UTM-zone reprojected tiles): the manual
merge produced results matching the geometric prediction exactly, where `merge_arrays()`
had not.

### Debugging tools built into `get_sentinel2_bands()`

`verbose_tile_diagnostics=True` prints, for every band: each tile's shape, bounds,
resolution, dtype, declared nodata, and valid-pixel fraction **before** merging, plus an
**incremental merge trace** (adding tiles one at a time, flagging with `<-- DROP` any
addition that _decreases_ valid coverage rather than increasing it) and the final merged
result's stats. Off by default since it forces early materialization of every tile's
pixel data — turn it on when a merged band looks wrong despite tile selection reporting
full coverage, to localize exactly which tile or merge step is responsible.

```python
band_arrays = get_sentinel2_bands(
    bbox, target_date, precomputed_selection=result, verbose_tile_diagnostics=True
)
```

---

## Step 1b: Clip bands to the study area (performance-critical)

**Not in the paper.** Planetary Computer serves each Sentinel-2 band as the full ~110 km
MGRS tile regardless of how small your search `bbox` was — the bbox only determines which
scenes are returned, not their extent. `clip_bands_to_aoi(band_arrays, bbox, buffer_m)`
clips each band down to the AOI + `buffer_m` (default 1000 m, matching the largest focal
buffer distance) padding.

**Call this immediately after Step 1, before resampling.** The padding must be at least as
large as your largest focal buffer distance — buffers near a clipped raster's edge need
real data within their radius to average correctly.

---

## Step 2: Resampling to a common 10 m grid (in-memory)

**What the paper did:** "All 20 m² band derivatives were resampled to 10 m² in order to
assess all variables on a pixel-by-pixel basis" (§2.4.2).

**What the script does:** `resample_band_to_10m()` calls `.rio.reproject()` with
`Resampling.bilinear` directly on in-memory `rioxarray` DataArrays — no intermediate
GeoTIFF files. Bands already at 10 m are passed through unchanged.
`resample_all_bands_to_10m()` applies this across the full band dict.
`dataarray_to_numpy()` and `profile_from_dataarray()` bridge from the `rioxarray`/`xarray`
world into the plain numpy-array-plus-profile format Step 3 expects; `dataarray_to_numpy()`
squeezes out any leftover singleton `band` dimension, which `rioxarray.open_rasterio()`
and `.rio.reproject()` can otherwise leave behind and which causes a
`scipy.ndimage.convolve` shape-mismatch error in Step 3 if not handled.

---

## Step 3: Focal buffer rasters (the core spatial feature engineering step)

**What the paper did:** Transforms each of the 10 spectral bands into a **focal buffer** —
a raster where each pixel is the average value of that band within a specified real-world
distance, at 15 distances (50–1000 m) per band, giving 150 total predictor rasters. This
operationalizes Tobler's First Law of Geography and follows Land Use Regression practice.

**What the script does:** `circular_kernel()` builds the averaging kernel (circular, since
the paper's methods don't specify square vs. circular and "focal buffer" is a GIS term
generally implying radial distance). `compute_focal_buffer()` computes one buffer at one
distance, with two major performance optimizations beyond the paper's approach:

- **FFT-based convolution** (`scipy.signal.fftconvolve`) for large-radius buffers
  (kernel radius > 15 px), switching automatically via `use_fft="auto"` — direct
  convolution costs `O(pixels × kernel_area)`, which becomes extremely slow at 1000 m
  distances; FFT convolution costs `O(pixels × log(pixels))` instead, independent of
  distance.
- **Skips the redundant nodata convolution** when the input has no NaN at all (the common
  case once bands are AOI-clipped with no internal nodata), halving the work.

`build_all_focal_buffers()` parallelizes across all 150 (band, distance) tasks via
`joblib.Parallel` (`n_jobs=-1`, `backend="loky"`), giving roughly a 5–7× speedup on an
8-core machine.

**Numerical caveat:** FFT convolution zero-pads beyond an array's true edge, while direct
convolution replicates edge values instead — these disagree near a raster's actual edge.
This is why Step 1b's AOI padding matters: as long as `buffer_m` is at least as large as
your largest focal distance, your real observation points never fall in that
edge-affected zone.

---

## Step 4: Extracting values at observation points

**What the paper did:** Used bilinear interpolation to assign focal buffer values to each
traverse observation, recalculating a value from the four nearest pixel centers weighted
by the point's exact position — necessary since GPS readings don't align to pixel centers
and multiple observations can fall in the same pixel.

**What the script does:** `extract_values_bilinear(raster_path, points_gdf)` implements
this directly: converts each point's coordinates to fractional row/column via the
raster's affine transform, then computes a weighted average of the four surrounding pixel
values.

---

## Step 5: Compiling the training table

**What the paper did:** Built one table per city/time-period with 151 variables
(temperature + 150 focal buffers) and one row per observation.

**What the script does:** `build_training_table(points_gdf, focal_raster_paths,
temp_col="temperature_f")`:

- **Parallelizes extraction** across all 150 rasters via `joblib.Parallel`, since each
  raster's point extraction is independent.
- **Builds the DataFrame with a single `pd.concat()`** rather than inserting columns one
  at a time in a loop — repeated `table[col] = ...` assignment fragments pandas' internal
  block manager, triggering a `PerformanceWarning` and making each subsequent insertion
  progressively slower. Collecting all columns first and concatenating once avoids this
  entirely.

If replicating the paper's original per-city, per-time-period model structure, run this
separately for each subset.

---

## Step 6: Train/test split and Random Forest training

**What the paper did:** Random 70/30 split per table, trained RF with no reported
hyperparameter tuning, evaluated with R² and RMSE. Described as "cross validation," though
it's really a single holdout split, not k-fold.

**What the script does:** `train_rf_model(table, target_col="temperature_f", test_size=0.30)`
replicates this directly — 500 trees, `max_features="sqrt"`, `oob_score=True` as an
additional internal check. Prints R², RMSE (in °F), and OOB score. **Use this to match the
paper's methodology for direct comparison.**

---

## Step 7: Predicting a continuous temperature surface

**What the paper did:** Applied the trained model to the 150 focal buffer rasters to
predict a continuous temperature surface across the full study area extent.

**What the script does:** `predict_temperature_surface(rf_model, focal_raster_paths,
feature_order, out_path, reference_profile)`: Stacks the 150 focal buffer rasters and
predicts in chunks of 500,000 pixels to manage memory.

---

## Debugging log: the `merge_arrays()` data-loss bug

Documented here because it's a real, non-obvious failure mode worth understanding if you
extend this pipeline further, and because the investigation methodology (systematically
ruling out hypotheses with matched synthetic reproductions) may be useful for your own
debugging going forward.

**Symptom:** predicted temperature maps for a large, multi-tile AOI showed data only in
one corner, with the rest NaN — despite tile selection reporting 100% AOI coverage.

**Investigation, in order:**

1. **Ruled out selection bugs.** `plot_tile_coverage()` confirmed 6 tiles genuinely
   covering the AOI (100% combined coverage reported).
2. **Ruled out UTM zone mismatch as a merge-time issue.** Two of the six tiles were
   natively in zone 17N (confirmed by reverse-geocoding their anomalous coordinates to
   longitude −77.5° to −77.4° — the AOI's actual western edge). Reprojecting these
   immediately (not deferred to the merge step) fixed the _displayed bounds_ confusion,
   but not the underlying data loss.
3. **Ruled out hidden zero-nodata contamination.** Hypothesized that real Sentinel-2 COGs
   might declare no nodata value at all (confirmed: `declared_nodata=None` for 4 of 6
   real tiles), leaving the standard Sentinel-2 fill value (0) undetected by
   `masked=True`. Direct inspection of real pixel values found **0.0% exact zeros**
   across all tiles — this theory was wrong.
4. **Ruled out lazy vs. eager array state.** Tested real file-backed `rioxarray` arrays,
   both lazy and explicitly `.load()`-ed before merging — both merged correctly in
   isolation.
5. **Quantified the real severity.** Computed the true geometric union of all 6 tiles'
   footprints against the final canvas: **93.6%** expected coverage from geometry alone,
   regardless of internal validity. The actual merged result was **47.4%** — meaning
   roughly half of everything that should have survived was being silently dropped.
6. **Found no synthetic reproduction, despite exact-geometry matching.** Built arrays
   with the real reported bounds, shapes, and resolutions (both hand-constructed and real
   file-backed GeoTIFFs) — every clean, correctly-constructed test merged to 100% (or the
   fully-expected value), never reproducing the drop.
7. **Replaced rather than continued chasing.** Since the failure was real (severe,
   reproducible on the actual pipeline run) but not explainable through any synthetic
   test, `manual_merge()` was written as a from-scratch alternative using only two
   well-understood, independently-verified primitives (`reproject_match` +
   boolean-masked numpy assignment). Tested against the real reported bounds: produced
   exactly the geometrically-expected 87.4%/100% results where `merge_arrays()` had not.

**Takeaway for future debugging:** when a library function's behavior can't be
reproduced with careful synthetic tests matching the real failure's exact parameters,
that's itself informative — it suggests the safest path is bypassing the function with a
transparent reimplementation rather than continuing to guess at its internals.

---

## Key differences from the original paper

1. **Multi-tile mosaicking with coverage-based selection**, entirely absent from the
   paper (its three cities each fit in one tile). This includes cross-UTM-zone handling,
   which the paper never needed to address.
2. **Satellite acquisition and resampling stay in-memory** via `rioxarray`, rather than
   round-tripping through intermediate GeoTIFFs — the paper predates `rioxarray`'s
   widespread adoption and doesn't describe its acquisition tooling in this detail.
3. **Kernel shape** for focal buffers (circular vs. square) is not specified in the paper;
   this pipeline defaults to circular.
4. **Performance optimizations in Step 3** (AOI clipping, FFT convolution, parallelization,
   float32) are engineering additions with no discussion in the 2019 paper, which reports
   no runtime information. Numerically equivalent to direct convolution in a properly
   padded raster's interior.
5. **RF hyperparameters** in Step 6 (500 trees, `sqrt` max features) are reasonable
   defaults, not a replication of a specific paper configuration the paper never reports.
6. **`manual_merge()` replacing `rioxarray.merge.merge_arrays()`** — an engineering fix
   with no bearing on the paper's methodology, needed only because of the multi-tile
   mosaicking this pipeline adds beyond the paper's scope.
