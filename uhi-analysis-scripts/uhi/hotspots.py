"""
uhi/hotspots.py - Spatial hot and cold spot analysis using Getis-Ord Gi* and persistence tracking.

Provides:
- Exact Getis-Ord Gi* z-scores and analytical p-values on regular grids with Queen (3x3) contiguity.
- False Discovery Rate (FDR) Benjamini-Hochberg multiple testing correction (q = 0.05).
- Diurnal persistence tracking across Morning (AM), Afternoon (AF), and Evening (PM).
- Comparative tree canopy statistics in hot vs. cold spots (only when a canopy_pct column exists).
- Sensitivity / agreement checks comparing Gi* clustering with percentile thresholds.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import logging

import numpy as np
import pandas as pd
import pyogrio
import scipy.signal
import scipy.stats

logger = logging.getLogger(__name__)

CELL_AREA_KM2: float = 0.01  # 100m x 100m = 10,000 m2 = 0.01 km2

PERIODS: Tuple[str, ...] = ("am", "af", "pm")
PERIOD_LABELS: Dict[str, str] = {
    "am": "Morning (AM)",
    "af": "Afternoon (AF)",
    "pm": "Evening (PM)",
}


def grid_positions(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, Tuple[int, int]]:
    """
    Return the 0-based (row, col) position of every cell and the shape of the full grid.

    Cells are placed by their row_index and col_index, so the order of rows in `df`
    does not matter. Grid positions with no cell in `df` are treated as missing.
    """
    rows = df["row_index"].to_numpy(dtype=int)
    cols = df["col_index"].to_numpy(dtype=int)
    rows = rows - rows.min()
    cols = cols - cols.min()
    return rows, cols, (int(rows.max()) + 1, int(cols.max()) + 1)


def to_grid_2d(values, df: pd.DataFrame, fill) -> np.ndarray:
    """Place one value per cell of `df` into a 2D array (row 0 at top); empty positions get `fill`."""
    rows, cols, shape = grid_positions(df)
    values = np.asarray(values)
    out = np.full(shape, fill, dtype=np.result_type(values, np.asarray(fill)))
    out[rows, cols] = values
    return out


def from_grid_2d(grid_2d: np.ndarray, df: pd.DataFrame) -> np.ndarray:
    """Read the value at each cell of `df` back out of a 2D grid made by `to_grid_2d`."""
    rows, cols, _ = grid_positions(df)
    return grid_2d[rows, cols]


def _canopy_pct(df: pd.DataFrame) -> pd.Series:
    """canopy_pct as a Series, or all-NaN when the grid has no canopy column."""
    if "canopy_pct" in df.columns:
        return df["canopy_pct"]
    return pd.Series(np.nan, index=df.index)


def compute_gi_star_grid(
    grid_2d: np.ndarray,
    valid_mask: Optional[np.ndarray] = None,
    kernel_size: int = 3,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute exact Getis-Ord Gi* z-scores and analytical two-tailed p-values
    on a 2D regular grid with Queen (square window) contiguity, handling NaNs.

    Parameters
    ----------
    grid_2d : np.ndarray
        2D array of values (e.g. temperature or anomaly).
    valid_mask : np.ndarray, optional
        Boolean mask of valid cells. If None, np.isfinite(grid_2d) is used.
    kernel_size : int, default=3
        Odd window size (3 corresponds to Queen-1, a 3x3 neighborhood).

    Returns
    -------
    gi_z : np.ndarray
        2D array of Gi* z-scores (NaN where invalid or undefined).
    p_values : np.ndarray
        2D array of analytical two-tailed p-values from standard normal distribution.
    """
    if valid_mask is None:
        valid_mask = np.isfinite(grid_2d)
    else:
        valid_mask = valid_mask & np.isfinite(grid_2d)

    n_valid = int(np.sum(valid_mask))
    if n_valid < 2:
        empty = np.full(grid_2d.shape, np.nan, dtype=float)
        return empty, empty

    clean_data = np.where(valid_mask, grid_2d, 0.0)
    x_bar = float(np.mean(grid_2d[valid_mask]))
    s = float(np.std(grid_2d[valid_mask], ddof=0))

    if s <= 0.0:
        empty = np.full(grid_2d.shape, np.nan, dtype=float)
        return empty, empty

    kernel = np.ones((kernel_size, kernel_size), dtype=float)
    # w_i is count of valid neighbors in the window (including focal cell)
    w_i = scipy.signal.convolve2d(valid_mask.astype(float), kernel, mode="same")
    # sum_x is sum of valid values in the window
    sum_x = scipy.signal.convolve2d(clean_data, kernel, mode="same")

    # Getis-Ord Gi* formula:
    # Denominator: s * sqrt((n * sum(w_ij^2) - (sum(w_ij))^2) / (n - 1))
    # For binary weights w_ij in {0, 1}, sum(w_ij^2) == sum(w_ij) == w_i
    variance_term = (n_valid * w_i - w_i**2) / (n_valid - 1)
    denom = s * np.sqrt(np.maximum(variance_term, 0.0))

    calc_mask = valid_mask & (denom > 0)
    gi_z = np.full(grid_2d.shape, np.nan, dtype=float)
    gi_z[calc_mask] = (sum_x[calc_mask] - w_i[calc_mask] * x_bar) / denom[calc_mask]

    # Two-tailed p-value from standard normal
    p_values = np.full(grid_2d.shape, np.nan, dtype=float)
    p_values[calc_mask] = 2.0 * (1.0 - scipy.stats.norm.cdf(np.abs(gi_z[calc_mask])))

    return gi_z, p_values


def fdr_correction(p_values: np.ndarray, q: float = 0.05) -> np.ndarray:
    """
    Apply Benjamini-Hochberg False Discovery Rate (FDR) adjustment to p-values.

    Parameters
    ----------
    p_values : np.ndarray
        Array of p-values (can contain NaNs).
    q : float, default=0.05
        False discovery rate significance threshold.

    Returns
    -------
    fdr_adj : np.ndarray
        Array of FDR-adjusted p-values (q-values), matching the input shape.
    """
    fdr_adj = np.full(p_values.shape, np.nan, dtype=float)
    valid = np.isfinite(p_values)
    if np.sum(valid) == 0:
        return fdr_adj

    p_valid = p_values[valid]
    adj_valid = scipy.stats.false_discovery_control(p_valid, method="bh")
    fdr_adj[valid] = adj_valid
    return fdr_adj


def analyze_hotspots(
    df: pd.DataFrame,
    fdr_threshold: float = 0.05,
    percentile_threshold: float = 10.0,
) -> pd.DataFrame:
    """
    Perform full spatial hot/cold spot detection, persistence analysis,
    and percentile sensitivity checks on a temperature grid.

    Parameters
    ----------
    df : pd.DataFrame
        Grid with row_index, col_index, and temp_am, temp_af, temp_pm columns.
        canopy_pct is optional and only used by the canopy summaries.
    fdr_threshold : float, default=0.05
        FDR q-value threshold for statistical significance (95% confidence).
    percentile_threshold : float, default=10.0
        Top/bottom percentile for sensitivity comparison (default 10% = 90th / 10th percentiles).

    Returns
    -------
    pd.DataFrame
        Enriched copy of df with Gi* z-scores, p-values, spot classifications,
        persistence categories, and percentile markers.
    """
    res = df.copy()

    for p in PERIODS:
        t_col = f"temp_{p}"
        if t_col not in res.columns:
            logger.info("Column %s not in dataframe; this period is skipped.", t_col)
            continue

        raw_vals = res[t_col].to_numpy(dtype=float)
        # Place cells on the grid by row/col index; positions without a cell are NaN
        grid_2d = to_grid_2d(raw_vals, res, fill=np.nan)
        valid_mask = np.isfinite(grid_2d)

        gi_z, p_vals = compute_gi_star_grid(grid_2d, valid_mask=valid_mask)
        fdr_q = fdr_correction(p_vals, q=fdr_threshold)

        z_flat = from_grid_2d(gi_z, res)
        q_flat = from_grid_2d(fdr_q, res)

        is_hot = (q_flat < fdr_threshold) & (z_flat > 0.0)
        is_cold = (q_flat < fdr_threshold) & (z_flat < 0.0)
        is_valid = np.isfinite(raw_vals)

        spot_cat = np.full(len(res), "No Data", dtype=object)
        spot_cat[is_valid] = "Not Significant"
        spot_cat[is_hot] = "Hot Spot"
        spot_cat[is_cold] = "Cold Spot"

        res[f"gi_z_{p}"] = np.round(z_flat, 3)
        res[f"fdr_q_{p}"] = np.round(q_flat, 4)
        res[f"spot_{p}"] = spot_cat
        res[f"is_hot_{p}"] = is_hot
        res[f"is_cold_{p}"] = is_cold

        # Percentile definition sensitivity check (top 10% and bottom 10%)
        valid_temps = raw_vals[is_valid]
        p_high = np.percentile(valid_temps, 100.0 - percentile_threshold)
        p_low = np.percentile(valid_temps, percentile_threshold)
        pct_hot = is_valid & (raw_vals >= p_high)
        pct_cold = is_valid & (raw_vals <= p_low)

        pct_cat = np.full(len(res), "No Data", dtype=object)
        pct_cat[is_valid] = "Neutral"
        pct_cat[pct_hot] = "Top 10%"
        pct_cat[pct_cold] = "Bottom 10%"
        res[f"pct_class_{p}"] = pct_cat
        res[f"pct_hot_{p}"] = pct_hot
        res[f"pct_cold_{p}"] = pct_cold

    # Persistence compares all three periods, so it is only defined when AM, AF and PM are present
    if any(f"temp_{p}" not in res.columns for p in PERIODS):
        logger.info("AM, AF and PM are not all present; skipping persistence.")
        return res

    hot_cols = [f"is_hot_{p}" for p in PERIODS if f"is_hot_{p}" in res.columns]
    cold_cols = [f"is_cold_{p}" for p in PERIODS if f"is_cold_{p}" in res.columns]

    n_hot = np.sum([res[c].astype(int) for c in hot_cols], axis=0)
    n_cold = np.sum([res[c].astype(int) for c in cold_cols], axis=0)

    res["n_hot_periods"] = n_hot
    res["n_cold_periods"] = n_cold

    hot_all3 = (n_hot == len(PERIODS))
    cold_all3 = (n_cold == len(PERIODS))
    mixed = (n_hot >= 1) & (n_cold >= 1)

    res["hot_all3"] = hot_all3
    res["cold_all3"] = cold_all3

    # Detailed persistence category
    # Hierarchical priority:
    # 1. Hot All 3 Times
    # 2. Cold All 3 Times
    # 3. Hot 2 Times
    # 4. Cold 2 Times
    # 5. Hot 1 Time
    # 6. Cold 1 Time
    # 7. Mixed Shift
    # 8. Not Significant
    # 9. No Data
    persistence_cat = np.full(len(res), "Not Significant", dtype=object)
    
    # Cells missing any period data
    has_any_temp = np.isfinite(res["temp_af"].values)
    persistence_cat[~has_any_temp] = "No Data"

    persistence_cat[n_hot == 1] = "Hot Spot (1 Time)"
    persistence_cat[n_cold == 1] = "Cold Spot (1 Time)"
    persistence_cat[n_hot == 2] = "Hot Spot (2 Times)"
    persistence_cat[n_cold == 2] = "Cold Spot (2 Times)"
    persistence_cat[mixed] = "Mixed Shift"
    persistence_cat[hot_all3] = "Hot Spot (All 3 Times)"
    persistence_cat[cold_all3] = "Cold Spot (All 3 Times)"

    res["persistence"] = persistence_cat

    # Primary 3-class persistent summary for high-level comparisons
    headline_class = np.full(len(res), "Other / Transitional", dtype=object)
    headline_class[hot_all3] = "Hot Spot (All 3 Times)"
    headline_class[cold_all3] = "Cold Spot (All 3 Times)"
    res["headline_spot"] = headline_class

    return res


def summarize_hotspots_by_period(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate summary statistics of hot and cold spots for each collection time.
    """
    rows = []
    for p in PERIODS:
        spot_col = f"spot_{p}"
        t_col = f"temp_{p}"
        if spot_col not in df.columns:
            continue

        valid = df[df[spot_col] != "No Data"]
        n_valid = len(valid)

        for cat in ["Hot Spot", "Cold Spot", "Not Significant"]:
            subset = valid[valid[spot_col] == cat]
            count = len(subset)
            pct_area = (count / n_valid * 100.0) if n_valid > 0 else 0.0
            area_km2 = count * CELL_AREA_KM2

            mean_t = subset[t_col].mean() if count > 0 else np.nan
            std_t = subset[t_col].std() if count > 0 else np.nan

            canopy_valid = _canopy_pct(subset).dropna()
            mean_canopy = canopy_valid.mean() if len(canopy_valid) > 0 else np.nan
            median_canopy = canopy_valid.median() if len(canopy_valid) > 0 else np.nan

            rows.append({
                "period": p,
                "period_label": PERIOD_LABELS[p],
                "spot_class": cat,
                "cell_count": count,
                "area_km2": np.round(area_km2, 2),
                "area_pct": np.round(pct_area, 2),
                "mean_temp_c": np.round(mean_t, 2),
                "std_temp_c": np.round(std_t, 2),
                "mean_canopy_pct": np.round(mean_canopy, 2),
                "median_canopy_pct": np.round(median_canopy, 2),
            })

    return pd.DataFrame(rows)


def summarize_persistence(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate area and distribution summary for diurnal persistence classes.
    """
    categories = [
        "Hot Spot (All 3 Times)",
        "Hot Spot (2 Times)",
        "Hot Spot (1 Time)",
        "Cold Spot (All 3 Times)",
        "Cold Spot (2 Times)",
        "Cold Spot (1 Time)",
        "Mixed Shift",
        "Not Significant",
    ]

    total_cells = len(df)
    rows = []
    for cat in categories:
        sub = df[df["persistence"] == cat]
        cnt = len(sub)
        area_km2 = cnt * CELL_AREA_KM2
        pct = (cnt / total_cells * 100.0)

        canopy_sub = _canopy_pct(sub).dropna()
        rows.append({
            "persistence_class": cat,
            "cell_count": cnt,
            "area_km2": np.round(area_km2, 2),
            "area_pct": np.round(pct, 2),
            "mean_canopy_pct": np.round(canopy_sub.mean(), 2) if len(canopy_sub) > 0 else np.nan,
            "median_canopy_pct": np.round(canopy_sub.median(), 2) if len(canopy_sub) > 0 else np.nan,
            "canopy_q25": np.round(canopy_sub.quantile(0.25), 2) if len(canopy_sub) > 0 else np.nan,
            "canopy_q75": np.round(canopy_sub.quantile(0.75), 2) if len(canopy_sub) > 0 else np.nan,
        })

    return pd.DataFrame(rows)


def summarize_canopy_comparison(df: pd.DataFrame) -> Dict[str, Union[pd.DataFrame, float]]:
    """
    Perform comparative tree canopy statistics between Hot All-3, Cold All-3,
    and Not Significant / Study Area baseline, including Mann-Whitney U test.
    """
    canopy_df = df.assign(canopy_pct=_canopy_pct(df)).dropna(subset=["canopy_pct"]).copy()

    hot_canopy = canopy_df[canopy_df["hot_all3"]]["canopy_pct"].values
    cold_canopy = canopy_df[canopy_df["cold_all3"]]["canopy_pct"].values
    ns_canopy = canopy_df[canopy_df["persistence"] == "Not Significant"]["canopy_pct"].values
    all_canopy = canopy_df["canopy_pct"].values

    groups = {
        "Hot Spot (All 3 Times)": hot_canopy,
        "Cold Spot (All 3 Times)": cold_canopy,
        "Not Significant": ns_canopy,
        "Regional Study Area": all_canopy,
    }

    stats_rows = []
    for name, vals in groups.items():
        n = len(vals)
        mean_val = float(np.mean(vals)) if n > 0 else np.nan
        std_val = float(np.std(vals, ddof=1)) if n > 1 else np.nan
        sem_val = std_val / np.sqrt(n) if n > 1 else np.nan
        ci95 = 1.96 * sem_val if n > 1 else np.nan

        stats_rows.append({
            "group": name,
            "n_cells": n,
            "area_km2": np.round(n * CELL_AREA_KM2, 2),
            "mean_canopy_pct": np.round(mean_val, 2),
            "ci95": np.round(ci95, 2),
            "std_canopy_pct": np.round(std_val, 2),
            "median_canopy_pct": np.round(float(np.median(vals)), 2) if n > 0 else np.nan,
            "iqr_canopy_pct": np.round(float(np.percentile(vals, 75) - np.percentile(vals, 25)), 2) if n > 0 else np.nan,
            "q25": np.round(float(np.percentile(vals, 25)), 2) if n > 0 else np.nan,
            "q75": np.round(float(np.percentile(vals, 75)), 2) if n > 0 else np.nan,
        })

    # Statistical test: Mann-Whitney U between Hot All-3 and Cold All-3
    n1, n2 = len(hot_canopy), len(cold_canopy)
    if n1 > 0 and n2 > 0:
        u_stat, p_val = scipy.stats.mannwhitneyu(hot_canopy, cold_canopy, alternative="two-sided")
        r_biserial = 1.0 - (2.0 * u_stat) / (n1 * n2)
        diff_mean = float(np.mean(cold_canopy) - np.mean(hot_canopy))
        diff_median = float(np.median(cold_canopy) - np.median(hot_canopy))
    else:
        u_stat, p_val, r_biserial = np.nan, np.nan, np.nan
        diff_mean, diff_median = np.nan, np.nan

    return {
        "stats_table": pd.DataFrame(stats_rows),
        "mann_whitney_u": float(u_stat),
        "p_value": float(p_val),
        "rank_biserial_effect_size": float(r_biserial),
        "canopy_difference_mean": diff_mean,
        "canopy_difference_median": diff_median,
    }


def compare_methods_sensitivity(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compare Getis-Ord Gi* spatial hot/cold spots with simple top/bottom 10%
    percentile thresholding to evaluate sensitivity and spatial clustering effect.
    """
    rows = []
    for p in [p for p in PERIODS if f"is_hot_{p}" in df.columns]:
        hot_gi = df[f"is_hot_{p}"].values
        cold_gi = df[f"is_cold_{p}"].values
        hot_pct = df[f"pct_hot_{p}"].values
        cold_pct = df[f"pct_cold_{p}"].values

        # Hot spot comparison
        hot_both = hot_gi & hot_pct
        hot_either = hot_gi | hot_pct
        hot_jaccard = (np.sum(hot_both) / np.sum(hot_either)) if np.sum(hot_either) > 0 else 0.0
        pct_hot_in_gi = (np.sum(hot_both) / np.sum(hot_pct) * 100.0) if np.sum(hot_pct) > 0 else 0.0

        # Cold spot comparison
        cold_both = cold_gi & cold_pct
        cold_either = cold_gi | cold_pct
        cold_jaccard = (np.sum(cold_both) / np.sum(cold_either)) if np.sum(cold_either) > 0 else 0.0
        pct_cold_in_gi = (np.sum(cold_both) / np.sum(cold_pct) * 100.0) if np.sum(cold_pct) > 0 else 0.0

        rows.append({
            "period": p,
            "period_label": PERIOD_LABELS[p],
            "feature": "Hot Spot",
            "gi_star_count": int(np.sum(hot_gi)),
            "percentile_count": int(np.sum(hot_pct)),
            "overlap_count": int(np.sum(hot_both)),
            "jaccard_index": np.round(hot_jaccard, 3),
            "share_of_percentile_confirmed_by_gi": np.round(pct_hot_in_gi, 1),
        })
        rows.append({
            "period": p,
            "period_label": PERIOD_LABELS[p],
            "feature": "Cold Spot",
            "gi_star_count": int(np.sum(cold_gi)),
            "percentile_count": int(np.sum(cold_pct)),
            "overlap_count": int(np.sum(cold_both)),
            "jaccard_index": np.round(cold_jaccard, 3),
            "share_of_percentile_confirmed_by_gi": np.round(pct_cold_in_gi, 1),
        })

    return pd.DataFrame(rows)
