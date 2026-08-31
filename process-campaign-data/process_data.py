from pathlib import Path
from typing import Dict, Union

import numpy as np
import pandas as pd

from datetime import date, time

import zipfile

import geopandas as gpd
from shapely.geometry import Point

"""
Meteotracker Field Data Processing Module. From Luis Ortiz.

Converts temperature units, calculates the NOAA/NWS Heat Index using the Rothfusz
regression equation with boundary adjustments, applies automated quality control
(freeze and spike removal), and computes spatial anomalies relative to run means.
"""


def calculate_heat_index(
    t: Union[pd.Series, np.ndarray], rh: Union[pd.Series, np.ndarray]
) -> np.ndarray:
    """Calculate the Heat Index (HI) in Fahrenheit based on the NOAA/NWS equation.

    Uses the simple formula check for temperatures < 80°F and the full 9-term
    Rothfusz regression equation with low/high humidity adjustments for
    temperatures >= 80°F.

    Reference:
        https://www.wpc.ncep.noaa.gov/html/heatindex_equation.shtml

    Args:
        t: Ambient air temperature in Fahrenheit (°F).
        rh: Relative humidity in percentage (%).

    Returns:
        np.ndarray: Computed Heat Index values in Fahrenheit (°F).
    """
    t_arr = np.array(t, dtype=float)
    rh_arr = np.array(rh, dtype=float)

    # 1. Simple Heat Index formula check
    hi_simple = 0.5 * (t_arr + 61.0 + ((t_arr - 68.0) * 1.2) + (rh_arr * 0.094))
    hi_check = 0.5 * (t_arr + hi_simple)

    # Default to simple formula result
    hi = hi_check.copy()

    # Mask where full Rothfusz regression is required (hi_check >= 80 F)
    full_mask = hi_check >= 80.0

    if np.any(full_mask):
        t_f = t_arr[full_mask]
        rh_f = rh_arr[full_mask]

        # 9-term Rothfusz regression equation
        hi_rothfusz = (
            -42.379
            + 2.04901523 * t_f
            + 10.14333127 * rh_f
            - 0.22475541 * t_f * rh_f
            - 0.00683783 * (t_f**2)
            - 0.05481717 * (rh_f**2)
            + 0.00122874 * (t_f**2) * rh_f
            + 0.00085282 * t_f * (rh_f**2)
            - 0.00000199 * (t_f**2) * (rh_f**2)
        )

        # Adjustment 1: Low Humidity (RH < 13% and 80 F <= T <= 112 F)
        adj1_mask = (rh_f < 13.0) & (t_f >= 80.0) & (t_f <= 112.0)
        if np.any(adj1_mask):
            t_adj1 = t_f[adj1_mask]
            rh_adj1 = rh_f[adj1_mask]
            adj1 = ((13.0 - rh_adj1) / 4.0) * np.sqrt((17.0 - np.abs(t_adj1 - 95.0)) / 17.0)
            hi_rothfusz[adj1_mask] -= adj1

        # Adjustment 2: High Humidity (RH > 85% and 80 F <= T <= 87 F)
        adj2_mask = (rh_f > 85.0) & (t_f >= 80.0) & (t_f <= 87.0)
        if np.any(adj2_mask):
            t_adj2 = t_f[adj2_mask]
            rh_adj2 = rh_f[adj2_mask]
            adj2 = ((rh_adj2 - 85.0) / 10.0) * ((87.0 - t_adj2) / 5.0)
            hi_rothfusz[adj2_mask] += adj2

        hi[full_mask] = hi_rothfusz

    return hi


def remove_freezes(series: pd.Series, min_run: int = 10) -> pd.Series:
    """Nullify sequences of identical consecutive values (sensor freezes).

    Args:
        series: Input data pandas Series.
        min_run: Minimum consecutive repeated elements to trigger freeze masking.

    Returns:
        pd.Series: Series with freeze sequences replaced by NaN.
    """
    is_same = series == series.shift()
    run_id = (~is_same).cumsum()
    counts = run_id.value_counts()
    run_lengths = run_id.map(counts)

    masked = series.copy()
    masked[run_lengths >= min_run] = np.nan
    return masked


def remove_spikes(series: pd.Series, threshold: float, window: int = 5) -> pd.Series:
    """Nullify sudden sensor spikes deviating significantly from rolling median.

    Args:
        series: Input data pandas Series.
        threshold: Absolute difference threshold from rolling median.
        window: Window size for rolling median calculation.

    Returns:
        pd.Series: Series with detected spike outliers replaced by NaN.
    """
    rolling_median = series.rolling(window=window, center=True, min_periods=1).median()
    deviation = (series - rolling_median).abs()
    masked = series.copy()
    masked[deviation > threshold] = np.nan
    return masked


def apply_quality_control(df: pd.DataFrame) -> pd.DataFrame:
    """Apply quality control routines for temperature, humidity, and dew point.

    Filters applied:
    1. Freeze check: Nullifies consecutive static runs >= 10 points.
    2. Spike check: Nullifies deviations > 1.0°C (Temp/DP) or > 5.0% (RH) from a
       5-point rolling median.

    Args:
        df: Input DataFrame containing raw Meteotracker columns.

    Returns:
        pd.DataFrame: Cleaned DataFrame after Quality Control.
    """
    qc_cols: Dict[str, Dict[str, Union[int, float]]] = {
        'Temp[°C]': {'freeze': 10, 'spike': 1.0},
        'Hum[%]': {'freeze': 10, 'spike': 5.0},
        'DP[°C]': {'freeze': 10, 'spike': 1.0},
    }

    df_clean = df.copy()
    for col, cfg in qc_cols.items():
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce')
            orig_non_null = df_clean[col].notnull().sum()

            # Apply freeze check
            df_clean[col] = remove_freezes(df_clean[col], min_run=int(cfg['freeze']))
            after_freeze = df_clean[col].notnull().sum()

            # Apply spike check
            df_clean[col] = remove_spikes(df_clean[col], threshold=float(cfg['spike']))
            after_spike = df_clean[col].notnull().sum()

            num_freeze = orig_non_null - after_freeze
            num_spike = after_freeze - after_spike
            if num_freeze > 0 or num_spike > 0:
                print(
                    f"  QC [{col}]: Removed {num_freeze} freeze points, {num_spike} spike points."
                )

    return df_clean


def process_file(file_path: Path, output_dir: Path) -> pd.DataFrame:
    """Process a single raw Meteotracker CSV file.

    Loads CSV, converts numeric columns, applies QC, computes Fahrenheit & Heat Index
    values, and calculates spatial anomalies relative to the run mean.

    Args:
        file_path: Path to the raw CSV file.
        output_dir: Directory where the processed CSV will be written.

    Returns:
        pd.DataFrame: Processed DataFrame.
    """
    print(f"Processing: {file_path.name}")
    df = pd.read_csv(file_path)

    cols_to_convert = ['Temp[°C]', 'Hum[%]', 'Speed[km/h]', 'Radiation[]', 'DP[°C]', 'Press[mbar]']
    for col in cols_to_convert:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Apply Quality Control
    df = apply_quality_control(df)

    # 1. Temperature conversions (°C -> °F)
    df['Temp_F'] = df['Temp[°C]'] * 1.8 + 32.0
    df['DP_F'] = df['DP[°C]'] * 1.8 + 32.0

    # 2. Heat Index calculation (°F)
    df['HeatIndex_F'] = calculate_heat_index(df['Temp_F'], df['Hum[%]'])

    # 3. Compute spatial anomalies (Value - Run Mean)
    variables_to_anomaly = {
        'Temp[°C]': 'Temp_C_anomaly',
        'Temp_F': 'Temp_F_anomaly',
        'Hum[%]': 'Hum_anomaly',
        'HeatIndex_F': 'HeatIndex_F_anomaly',
        'Speed[km/h]': 'Speed_anomaly',
        'Radiation[]': 'Radiation_anomaly',
        'DP[°C]': 'DP_C_anomaly',
        'DP_F': 'DP_F_anomaly',
        'Press[mbar]': 'Press_anomaly',
    }

    for col, anomaly_col in variables_to_anomaly.items():
        if col in df.columns:
            mean_val = df[col].mean()
            df[anomaly_col] = df[col] - mean_val

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / file_path.name
    df.to_csv(out_path, index=False)
    print(f"  Saved to {out_path}\n")
    return df


"""
Combining Campgain CSV Files Module. Co-authored with Claude.

"""

TIME_COLUMN = "Time"
EXPECTED_DATE = date() # campaign date -- rows on any other date are excluded

# (period_name, start_time_inclusive, end_time_inclusive)
TIME_WINDOWS = [
    ("am", time(5, 0, 0), time(9, 0, 0)),
    ("af", time(14, 0, 0), time(16, 0, 0)),
    ("pm", time(18, 0, 0), time(21, 0, 0)),
]


def load_all_csvs(folder: Path) -> pd.DataFrame:
    """
    Reads every .csv file in `folder`, parses the Time column as
    timezone-aware datetimes, tags each row with its source filename (for
    traceability/debugging), and concatenates everything into one
    DataFrame. Files with different column sets are handled gracefully --
    the combined result has the union of all columns, with NaN filled in
    wherever a given file didn't have that column.
    """
    csv_paths = sorted(folder.glob("*.csv"))
    if not csv_paths:
        raise FileNotFoundError(f"No .csv files found in {folder}")

    print(f"Found {len(csv_paths)} CSV files in {folder}")

    frames = []
    failed = []
    for path in csv_paths:
        try:
            df = pd.read_csv(path)
            if TIME_COLUMN not in df.columns:
                print(f"  SKIPPING {path.name}: no '{TIME_COLUMN}' column found "
                      f"(columns present: {list(df.columns)})")
                failed.append(path.name)
                continue
            df["source_file"] = path.name
            frames.append(df)
        except Exception as e:
            print(f"  FAILED to read {path.name}: {e}")
            failed.append(path.name)

    if failed:
        print(f"\n{len(failed)} file(s) could not be read/used: {failed}\n")

    combined = pd.concat(frames, axis=0, ignore_index=True, sort=False)
    print(f"Loaded {len(combined)} total rows from {len(frames)} files "
          f"(before time parsing/filtering)")
    return combined


def parse_time_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    Parses the Time column into timezone-aware datetimes. Rows with an
    unparseable Time value become NaT and are reported, not silently
    dropped without explanation.
    """
    df = df.copy()
    parsed = pd.to_datetime(df[TIME_COLUMN], errors="coerce", utc=False)
    n_unparseable = parsed.isna().sum() - df[TIME_COLUMN].isna().sum()
    if n_unparseable > 0:
        print(f"  WARNING: {n_unparseable} rows had a Time value that could not "
              f"be parsed and will be excluded from all outputs.")
    df["_parsed_time"] = parsed
    return df


def assign_time_period(df: pd.DataFrame) -> pd.DataFrame:
    """
    Assigns each row to 'am', 'af', 'pm', or None, based on TWO checks
    against _parsed_time:
        1. Date must match EXPECTED_DATE (2026-07-16) -- catches stray
           readings from a different day (device clock errors, test data
           left in a file, etc.) that would otherwise silently pass
           through just because their time-of-day looks valid.
        2. Time-of-day must fall within one of the three defined windows.

    Also records WHY a row was excluded (when it was), in a new
    "_exclusion_reason" column, so the excluded-rows report distinguishes
    "wrong date" from "right date but outside all time windows" from
    "time value couldn't be parsed at all" -- these usually need different
    follow-up (a wrong date might mean a real data problem worth
    investigating; a gap-window exclusion is probably just expected).
    """
    df = df.copy()

    def classify(parsed_time):
        if pd.isna(parsed_time):
            return pd.Series({"_time_period": None, "_exclusion_reason": "unparseable_time"})
        if parsed_time.date() != EXPECTED_DATE:
            return pd.Series({
                "_time_period": None,
                "_exclusion_reason": f"wrong_date ({parsed_time.date()})",
            })
        t = parsed_time.time()
        for period_name, start, end in TIME_WINDOWS:
            if start <= t <= end:
                return pd.Series({"_time_period": period_name, "_exclusion_reason": None})
        return pd.Series({"_time_period": None, "_exclusion_reason": "outside_time_window"})

    result = df["_parsed_time"].apply(classify)
    df["_time_period"] = result["_time_period"]
    df["_exclusion_reason"] = result["_exclusion_reason"]
    return df


def combine_and_split(folder: str, out_dir: str = "."):
    folder = Path(folder)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    combined = load_all_csvs(folder)
    combined = parse_time_column(combined)
    combined = assign_time_period(combined)

    print()
    counts = combined["_time_period"].value_counts(dropna=False)
    for period_name, _, _ in TIME_WINDOWS:
        print(f"  {period_name}: {counts.get(period_name, 0)} rows")

    n_excluded = combined["_time_period"].isna().sum()
    print(f"  excluded (wrong date / outside time windows / unparseable): {n_excluded} rows")
    if n_excluded > 0:
        # Break down exclusions by reason, collapsing distinct wrong-date
        # values (e.g. "wrong_date (2026-07-15)") into one "wrong_date"
        # bucket for the summary, while keeping the specific date in the
        # actual excluded CSV for full detail.
        reason_summary = (
            combined.loc[combined["_time_period"].isna(), "_exclusion_reason"]
            .str.replace(r"^wrong_date.*", "wrong_date", regex=True)
            .value_counts()
        )
        for reason, count in reason_summary.items():
            print(f"    - {reason}: {count} rows")

    for period_name, _, _ in TIME_WINDOWS:
        subset = combined[combined["_time_period"] == period_name].copy()
        subset = subset.drop(columns=["_parsed_time", "_time_period", "_exclusion_reason"])
        out_path = out_dir / f"combined_{period_name}.csv"
        subset.to_csv(out_path, index=False)
        print(f"Wrote {len(subset)} rows -> {out_path}")

    if n_excluded > 0:
        excluded = combined[combined["_time_period"].isna()].copy()
        excluded = excluded.drop(columns=["_parsed_time", "_time_period"])
        excluded = excluded.rename(columns={"_exclusion_reason": "exclusion_reason"})
        excluded_out_path = out_dir / "excluded_rows.csv"
        excluded.to_csv(excluded_out_path, index=False)
        print(f"Wrote {n_excluded} excluded rows -> {excluded_out_path} "
              f"(includes an 'exclusion_reason' column -- review 'wrong_date' "
              f"rows especially closely, since those may indicate a real "
              f"data problem rather than an expected schedule gap)")


"""
Convert Combined CSV Files to Shapefiles Module. Co-authored with Claude.

"""

LAT_COL = "Lat"
LON_COL = "Lon"
CRS = "EPSG:4326"  # WGS84 -- standard for raw GPS lat/lon degrees


def load_csv_as_dataframe(csv_path: Path) -> pd.DataFrame:
    """
    Reads the CSV and drops a leftover unnamed index column, if present
    (some exports include a bare row-number column with an empty header,
    e.g. from an earlier pd.to_csv() call that didn't use index=False).
    """
    df = pd.read_csv(csv_path)
    first_col = df.columns[0]
    if first_col == "" or first_col.startswith("Unnamed:"):
        df = df.drop(columns=[first_col])
    return df


def sanitize_fieldnames(columns: list) -> dict:
    """
    Builds a mapping from original column names to safe shapefile field
    names: ASCII alphanumeric + underscore only, max 10 characters, and
    guaranteed unique even after truncation (two long column names that
    happen to share their first 10 characters would otherwise silently
    collide and overwrite each other in the .dbf).
    """
    mapping = {}
    used_names = set()

    for col in columns:
        # Keep only ASCII letters, digits, and underscores; everything
        # else (degree signs, brackets, parens, spaces, slashes) is
        # dropped rather than replaced, to keep names compact within the
        # 10-char limit.
        cleaned = "".join(ch for ch in col if ch.isascii() and (ch.isalnum() or ch == "_"))
        if not cleaned:
            cleaned = "field"
        candidate = cleaned[:10]

        if candidate not in used_names:
            mapping[col] = candidate
            used_names.add(candidate)
            continue

        # Collision after truncation -- append a numeric suffix, trimming
        # the base further as needed to stay within 10 characters.
        for suffix in range(1, 100):
            suffix_str = f"_{suffix}"
            trimmed = cleaned[: 10 - len(suffix_str)]
            candidate = f"{trimmed}{suffix_str}"
            if candidate not in used_names:
                break
        mapping[col] = candidate
        used_names.add(candidate)

    return mapping


def csv_to_shapefile(csv_path: Path, out_dir: Path):
    """
    Converts one CSV to a point shapefile (zipped), plus a field-name
    mapping CSV. Rows with missing/invalid Lat or Lon are dropped and
    reported -- a shapefile has no way to represent a "point with no
    location", so these can't be included as geometry.
    """
    df = load_csv_as_dataframe(csv_path)

    if LAT_COL not in df.columns or LON_COL not in df.columns:
        raise ValueError(
            f"{csv_path.name}: expected '{LAT_COL}' and '{LON_COL}' columns, "
            f"found columns: {list(df.columns)}"
        )

    n_start = len(df)
    df = df.dropna(subset=[LAT_COL, LON_COL]).copy()
    n_dropped = n_start - len(df)
    if n_dropped > 0:
        print(f"  Dropped {n_dropped} row(s) with missing Lat/Lon (can't be mapped)")

    geometry = [Point(lon, lat) for lon, lat in zip(df[LON_COL], df[LAT_COL])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=CRS)

    # Ensure the Time column (and any other datetime-like text) is stored
    # as a plain string -- shapefile date fields don't reliably preserve
    # timezone-aware ISO 8601 timestamps like "2026-07-16T11:58:09-04:00",
    # so keeping it as text avoids silent truncation or timezone loss.
    if "Time" in gdf.columns:
        gdf["Time"] = gdf["Time"].astype(str)

    field_mapping = sanitize_fieldnames([c for c in gdf.columns if c != "geometry"])
    gdf = gdf.rename(columns=field_mapping)

    stem = csv_path.stem
    shp_dir = out_dir / stem
    shp_dir.mkdir(parents=True, exist_ok=True)
    shp_path = shp_dir / f"{stem}.shp"

    gdf.to_file(shp_path, driver="ESRI Shapefile")

    mapping_path = out_dir / f"{stem}_field_mapping.csv"
    pd.DataFrame(
        [{"original_column": k, "shapefile_field": v} for k, v in field_mapping.items()]
    ).to_csv(mapping_path, index=False)

    zip_path = out_dir / f"{stem}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for component in shp_dir.glob(f"{stem}.*"):
            zf.write(component, arcname=component.name)

    print(f"  {len(gdf)} points -> {zip_path}")
    print(f"  Field name mapping -> {mapping_path}")

    return zip_path, mapping_path


def convert_all(csv_paths: list, out_dir: str = "shapefiles"):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for csv_path in csv_paths:
        csv_path = Path(csv_path)
        print(f"Converting {csv_path.name}...")
        try:
            csv_to_shapefile(csv_path, out_dir)
        except Exception as e:
            print(f"  FAILED: {e}")
        print()

