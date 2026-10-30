As described in [this file](./data-cleaning-processing.md), many of the files and folders were inconsistent in the way they were named. Here's what I did to normalize and unify the data to match [this format](./data-format.md).

1. Separate folders into `original` and `clean` directories on Hopper. 
- `original` folders have the name they were downloaded with from OSF
    - These folders hold the .zip files that contain the original, unedited data from OSF, as well as the PDFs of the reports that were written by CAPA and the README files (if available), along with any additional data that may have been included in the download. 
- `clean` folders all follow the `Heat Watch {City Name}, {state postal code abbreviation}` format
    - These folders hold the unzipped `rasters` and `traverses` folders that contain the respective raw data files for each city. In the traverses folders, the file names AND the column names in each file were edited (if necessary) to match the unified format.

2. In a Jupyter Notebook, I wrote some code (with the help of Claude via Copilot) to ensure all files followed the same format. 

- File names update (note specific handling of outliers like Boise, San Juan, and Boston.)

```python
# Import libraries for processing data
from pathlib import Path
import re
import pandas as pd
import geopandas as gpd

from concurrent.futures import ThreadPoolExecutor, as_completed
```

```python
"""
Normalize traverse filenames across all Heat Watch city folders
to match the standard format: {time}_trav.shp (where time = am, af, pm)

Handles city-specific time period mappings for Boise, San Juan, and Boston.
"""

class TraverseFilenameNormalizer:
    """Normalize traverse filenames to standard format"""
    
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.changes_log = []
        self.errors_log = []
        
        # Standard time period mappings
        self.standard_time_mappings = {
            'morning': 'am',
            'am': 'am',
            'afternoon': 'af',
            'af': 'af',
            'evening': 'pm',
            'eve': 'pm',
            'pm': 'pm',
            'ev': 'pm'
        }
        
        # City-specific exceptions
        # For these cities: PM == afternoon (af), Eve == evening (pm)
        self.city_specific_mappings = {
            'boise': {
                'morning': 'am',
                'am': 'am',
                'pm': 'af',      # PM is afternoon
                'eve': 'pm',     # Eve is evening
                'evening': 'pm'
            },
            'san juan': {
                'morning': 'am',
                'am': 'am',
                'pm': 'af',      # PM is afternoon
                'eve': 'pm',     # Eve is evening
                'evening': 'pm'
            },
            'boston': {
                'morning': 'am',
                'am': 'am',
                'pm_revised': 'af',  # pm_revised is afternoon
                'ev': 'pm',          # ev is evening
                'evening': 'pm'
            }
        }
        
    def get_city_normalized_name(self, city_name):
        """Normalize city name for matching (lowercase, remove special chars)"""
        # Handle "Heat Watch City, STATE" format
        normalized = city_name.lower()
        
        # Extract just the city name before comma
        if ',' in normalized:
            normalized = normalized.split(',')[0]
        
        # Remove "heat watch" prefix if present
        normalized = normalized.replace('heat watch', '').strip()
        
        return normalized
    
    def normalize_time_period(self, time_str, city_name):
        """
        Convert any time period variation to standard format (am, af, pm)
        Uses city-specific mappings where applicable
        """
        time_lower = time_str.lower()
        city_normalized = self.get_city_normalized_name(city_name)
        
        # Check for city-specific mapping first
        for city_key, mapping in self.city_specific_mappings.items():
            if city_key in city_normalized:
                result = mapping.get(time_lower)
                if result:
                    # Log when using city-specific mapping
                    if time_lower in ['pm', 'eve', 'pm_revised', 'ev']:
                        self.changes_log.append({
                            'city': city_name,
                            'note': f'City-specific mapping applied: {time_lower} -> {result}'
                        })
                    return result
        
        # Fall back to standard mapping
        return self.standard_time_mappings.get(time_lower, time_str)
    
    def get_standard_extensions(self):
        """All shapefile-related extensions to rename"""
        return ['.shp', '.dbf', '.prj', '.shx', '.cpg', '.sbn', '.sbx', '.shp.xml']
    
    def should_rename(self, filename, city_name):
        """
        Determine if file needs renaming based on documented patterns
        Returns (should_rename: bool, new_base_name: str or None)
        """
        stem = filename.stem.lower()
        
        # Already in standard format
        if re.match(r'^(am|af|pm)_trav$', stem):
            return False, None
        
        # Pattern 1: trav_{time} format (Baltimore, Washington DC)
        match = re.match(r'^trav_(am|af|pm|morning|afternoon|evening)$', stem)
        if match:
            time = self.normalize_time_period(match.group(1), city_name)
            return True, f"{time}_trav"
        
        # Pattern 2: {time}_TraversePoints_{City} (Boise, Oakland-Berkeley, Sacramento, etc.)
        # Note: Boise uses PM for afternoon and Eve for evening
        match = re.match(r'^(am|afternoon|evening|morning|pm|eve)_traversepoints?_\w+$', stem)
        if match:
            time = self.normalize_time_period(match.group(1), city_name)
            return True, f"{time}_trav"
        
        # Pattern 3: {time}_Traverse_Points_{City} (San Juan)
        # Note: San Juan uses PM for afternoon and Eve for evening
        match = re.match(r'^(am|pm|eve)_traverse_points_\w+$', stem)
        if match:
            time = self.normalize_time_period(match.group(1), city_name)
            return True, f"{time}_trav"
        
        # Pattern 4: {City}_{time} format (Boston)
        # Note: Boston uses pm_revised for afternoon and ev for evening
        match = re.match(r'^boston_(am|ev|pm_revised)$', stem)
        if match:
            time = self.normalize_time_period(match.group(1), city_name)
            return True, f"{time}_trav"
        
        # Pattern 5: {city-state}_{time}_trav format (Charlotte, Laredo, Palo Alto, etc.)
        match = re.match(r'^[\w-]+_(am|af|pm)_trav$', stem)
        if match and '-' in stem:  # Has hyphenated city-state format
            time = match.group(1)
            return True, f"{time}_trav"
        
        # Pattern 6: {time}_trav_temp or {time}_trav_rh (Cincinnati, Houston, San Jose)
        match = re.match(r'^(am|af|pm)_trav_(temp|humidity?)$', stem)
        if match:
            time = match.group(1)
            suffix = match.group(2)
            # Keep temp files in main folder with standard name
            if 'temp' in suffix:
                return True, f"{time}_trav"
            # Humidity files should be moved/handled separately - flag but don't rename here
            else:
                return False, None
        
        # Pattern 7: Files with timestamps in filename (Bronx and Manhattan)
        match = re.match(r'^(am|af|pm)_trav.*$', stem)
        if match and stem != f"{match.group(1)}_trav":
            time = match.group(1)
            return True, f"{time}_trav"
        
        # Pattern 8: Los Angeles and Yonkers use 'ev' for evening
        match = re.match(r'^(am|af|ev)_trav$', stem)
        if match:
            time = self.normalize_time_period(match.group(1), city_name)
            if time != match.group(1):  # Only rename if mapping changed it
                return True, f"{time}_trav"
        
        return False, None
    
    def rename_file(self, file_path, new_base_name, dry_run=True):
        """Rename a file with all its related extensions"""
        old_base = file_path.stem
        new_name = new_base_name + file_path.suffix
        new_path = file_path.parent / new_name
        
        if new_path.exists() and new_path != file_path:
            self.errors_log.append({
                'file': str(file_path),
                'error': f'Target file already exists: {new_path.name}',
                'city': file_path.parent.parent.name
            })
            return False
        
        self.changes_log.append({
            'city': file_path.parent.parent.name,
            'folder': file_path.parent.name,
            'old_name': file_path.name,
            'new_name': new_name,
            'action': 'DRY RUN' if dry_run else 'RENAMED'
        })
        
        if not dry_run:
            file_path.rename(new_path)
            
        return True
    
    def process_traverse_folder(self, traverse_folder, city_name, dry_run=True):
        """Process all files in a traverse folder"""
        if not traverse_folder.exists():
            return
        
        # Get all shapefile base files
        shapefiles = list(traverse_folder.glob("*.shp"))
        
        for shp_file in shapefiles:
            should_rename, new_base = self.should_rename(shp_file, city_name)
            
            if should_rename and new_base:
                # Rename the .shp file
                success = self.rename_file(shp_file, new_base, dry_run)
                
                if success:
                    # Rename all related files with same base name
                    old_base = shp_file.stem
                    for ext in self.get_standard_extensions()[1:]:  # Skip .shp (already done)
                        related_file = traverse_folder / f"{old_base}{ext}"
                        if related_file.exists():
                            self.rename_file(related_file, new_base, dry_run)
    
    def process_all_cities(self, dry_run=True):
        """Process all city folders"""
        city_folders = sorted([d for d in self.base_dir.iterdir() if d.is_dir()])
        
        print(f"Found {len(city_folders)} city folders")
        print(f"Mode: {'DRY RUN' if dry_run else 'LIVE - WILL RENAME FILES'}")
        print("=" * 70)
        
        # Show city-specific mappings being used
        print("\nCity-specific time period mappings:")
        for city, mappings in self.city_specific_mappings.items():
            print(f"  {city.title()}:")
            for old, new in mappings.items():
                if old != new and old not in ['morning', 'am', 'afternoon', 'evening']:
                    print(f"    {old} -> {new}")
        print("=" * 70 + "\n")
        
        for city_folder in city_folders:
            city_name = city_folder.name
            
            # Find all traverse folders (handle variations like traverses_chw_* )
            traverse_folders = list(city_folder.glob("traverses*"))
            
            for traverse_folder in traverse_folders:
                if traverse_folder.is_dir():
                    self.process_traverse_folder(traverse_folder, city_name, dry_run)
        
        return self.generate_report()

    def generate_report(self):
        """Generate a summary report of all changes"""
        print("\n" + "=" * 70)
        print("PROCESSING COMPLETE")
        print("=" * 70)
        
        if self.changes_log:
            changes_df = pd.DataFrame(self.changes_log)
            print(f"\nTotal files to rename: {len(changes_df)}")
            print(f"\nCities affected: {changes_df['city'].nunique()}")
            
            print("\nChanges by city:")
            print(changes_df.groupby('city').size().sort_values(ascending=False))
            
            return changes_df
        else:
            print("\nNo files need renaming!")
            return pd.DataFrame()
    
    def export_report(self, output_path):
        """Export detailed report to CSV"""
        # Filter out informational log entries
        actual_changes = [log for log in self.changes_log if 'old_name' in log]
        
        if actual_changes:
            df = pd.DataFrame(actual_changes)
            df.to_csv(output_path, index=False)
            print(f"\nDetailed report saved to: {output_path}")
        
        if self.errors_log:
            errors_df = pd.DataFrame(self.errors_log)
            error_path = Path(output_path).parent / "filename_normalization_errors.csv"
            errors_df.to_csv(error_path, index=False)
            print(f"Errors saved to: {error_path}")

# ============================================================================
# USAGE
# ============================================================================

# Set your base directory
BASE_DIR = ""

# Initialize normalizer
normalizer = TraverseFilenameNormalizer(BASE_DIR)

# Step 1: DRY RUN - See what would change without actually renaming
print("=" * 70)
print("STEP 1: DRY RUN - Preview changes")
print("=" * 70)
changes_df = normalizer.process_all_cities(dry_run=True)

# Export the preview
if len(changes_df) > 0:
    normalizer.export_report("traverse_filename_changes_preview.csv")

# After running dry run, check specific cities:
if len(changes_df) > 0:
    special_cities = changes_df[
        changes_df['city'].str.contains('Boise|San Juan|Boston', case=False, regex=True)
    ]
    print("\n\nChanges for cities with special mappings:")
    print(special_cities[['city', 'old_name', 'new_name']])

# Step 2: Review the changes, then execute for real
# Uncomment the lines below after reviewing the dry run results

# print("\n" + "=" * 70)
# print("STEP 2: LIVE RUN - Actually renaming files")
# print("=" * 70)
# normalizer_live = TraverseFilenameNormalizer(BASE_DIR)
# changes_df_live = normalizer_live.process_all_cities(dry_run=False)
# normalizer_live.export_report("traverse_filename_changes_completed.csv")
```

- Temperature columns update (note specific handling of outliers like San Juan, DC, and Baltimore.)

```python
"""
Rename non-standard temperature column names (t_f, T_F) to the
required standard column name 'temp_f' across all traverse shapefiles.

Follows the same dry_run / changes_log / errors_log pattern as
TraverseFilenameNormalizer in this file.
"""

class TempColumnRenamer:
    """Rename t_f / T_F column to temp_f in all traverse shapefiles."""

    # Columns that must be renamed → target name (per data-format.md)
    RENAME_MAP = {
        't_f': 'temp_f',
        'T_F': 'temp_f',
    }

    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.changes_log = []
        self.errors_log = []

    def _extract_city_name(self, city_dir_name: str) -> str:
        return city_dir_name.replace('Heat Watch ', '')

    def _columns_to_rename(self, columns: list[str]) -> dict[str, str]:
        """Return {old_name: new_name} for any columns that need renaming."""
        return {col: self.RENAME_MAP[col] for col in columns if col in self.RENAME_MAP}

    # Per-file processing
    def rename_columns_in_shapefile(
        self, shp_path: Path, city_name: str, dry_run: bool = True
    ) -> dict:
        """
        Rename non-standard temp columns in a single shapefile.

        In dry_run mode the file is never written; the record still shows
        what *would* change so you can audit before committing.
        """
        try:
            gdf = gpd.read_file(shp_path)
        except Exception as exc:
            record = {
                'status': 'error',
                'city': city_name,
                'filename': shp_path.name,
                'path': str(shp_path),
                'issue': f'Could not open shapefile: {exc}',
            }
            self.errors_log.append(record)
            return record

        renames = self._columns_to_rename(list(gdf.columns))

        if not renames:
            return {
                'status': 'skipped',
                'city': city_name,
                'filename': shp_path.name,
                'path': str(shp_path),
                'renames': {},
            }

        record = {
            'status': 'dry_run' if dry_run else 'renamed',
            'city': city_name,
            'filename': shp_path.name,
            'path': str(shp_path),
            'renames': renames,
        }

        if not dry_run:
            try:
                gdf = gdf.rename(columns=renames)
                # to_file rewrites .shp/.dbf/.shx/.prj/.cpg atomically
                gdf.to_file(shp_path, driver='ESRI Shapefile')
                self.changes_log.append(record)
            except Exception as exc:
                record['status'] = 'error'
                record['issue'] = f'Failed to write shapefile: {exc}'
                self.errors_log.append(record)
        else:
            self.changes_log.append(record)

        return record

    # City / dataset traversal
    def process_city(self, city_dir: Path, dry_run: bool = True):
        """Process all traverse shapefiles within a single city folder."""
        city_name = self._extract_city_name(city_dir.name)
        traverse_folders = list(city_dir.glob('traverses*')) or [city_dir]

        processed = set()
        for folder in traverse_folders:
            for shp_path in sorted(folder.glob('*trav.shp')):
                if str(shp_path) in processed:
                    continue
                processed.add(str(shp_path))
                self.rename_columns_in_shapefile(shp_path, city_name, dry_run=dry_run)

    def process_all(self, dry_run: bool = True) -> dict | None:
        """
        Rename temp columns across every city folder.

        Parameters
        ----------
        dry_run : bool
            If True (default), log what would change without writing any files.
            Set to False only after reviewing the dry-run report.
        """
        if not self.base_dir.exists():
            print(f'Error: Base directory does not exist: {self.base_dir}')
            return None

        mode_label = '(DRY RUN — no files will be written)' if dry_run else '(LIVE — files will be overwritten)'
        print(f'\nProcessing all cities {mode_label}')
        print('=' * 70)

        city_dirs = sorted([d for d in self.base_dir.iterdir() if d.is_dir()])
        print(f'Found {len(city_dirs)} city folders')

        for city_dir in city_dirs:
            self.process_city(city_dir, dry_run=dry_run)

        return self.generate_report(dry_run=dry_run)

    # Reporting
    def generate_report(self, dry_run: bool = True) -> dict | None:
        """Print a structured report and return DataFrames."""
        mode_label = 'DRY RUN' if dry_run else 'COMPLETED'

        print(f'\n{"=" * 70}')
        print(f'TEMP COLUMN RENAME REPORT — {mode_label}')
        print('=' * 70)

        renames = [r for r in self.changes_log if r['renames']]
        skipped = [r for r in self.changes_log if not r['renames']]

        print(f'\nFiles requiring rename : {len(renames)}')
        print(f'Files already correct  : {len(skipped)}')
        print(f'Errors                 : {len(self.errors_log)}')

        if renames:
            print(f'\n{"=" * 70}')
            action = 'Would rename' if dry_run else 'Renamed'
            print(f'{action} columns in the following files:')
            print('=' * 70)

            renames_df = pd.DataFrame(renames)

            # Column-name frequency summary
            all_old_names = []
            for r in renames:
                all_old_names.extend(r['renames'].keys())
            freq = pd.Series(all_old_names).value_counts()
            print('\nOld column name frequencies:')
            for col_name, count in freq.items():
                print(f"  '{col_name}' → 'temp_f' : {count} file(s)")

            print(f'\nDetailed file list (first 20):')
            for idx, r in enumerate(renames[:20], 1):
                print(f"\n{idx}. {r['city']} / {r['filename']}")
                for old, new in r['renames'].items():
                    print(f"     '{old}' → '{new}'")

            if len(renames) > 20:
                print(f'\n... and {len(renames) - 20} more files')

        if self.errors_log:
            print(f'\n{"=" * 70}')
            print('ERRORS')
            print('=' * 70)
            for err in self.errors_log:
                print(f"  {err['city']} / {err['filename']}: {err['issue']}")

        return {
            'renames': pd.DataFrame(renames) if renames else None,
            'skipped': pd.DataFrame(skipped) if skipped else None,
            'errors': pd.DataFrame(self.errors_log) if self.errors_log else None,
        }

    def export_report(self, output_dir: str):
        """Export rename logs to CSVs."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        renames = [r for r in self.changes_log if r['renames']]
        if renames:
            path = output_dir / 'temp_column_renames.csv'
            pd.DataFrame(renames).to_csv(path, index=False)
            print(f'Renames log exported to: {path}')

        if self.errors_log:
            path = output_dir / 'temp_column_rename_errors.csv'
            pd.DataFrame(self.errors_log).to_csv(path, index=False)
            print(f'Errors exported to: {path}')

        summary = {
            'files_to_rename': len(renames),
            'files_skipped': len([r for r in self.changes_log if not r['renames']]),
            'errors': len(self.errors_log),
        }
        path = output_dir / 'temp_column_rename_summary.csv'
        pd.DataFrame([summary]).to_csv(path, index=False)
        print(f'Summary exported to: {path}')

"""
Helper functions for temperature column conversions.
"""

def celsius_to_fahrenheit(celsius):
    """Convert Celsius to Fahrenheit"""
    return (celsius * 9/5) + 32

def rename_t_to_tc(shapefile_path, city_name):
    """
    Rename 'T' column to 'T_C' in traverse files
    """
    try:
        # Read shapefile
        gdf = gpd.read_file(shapefile_path)
        
        # Check if 'T' column exists
        if 'T' not in gdf.columns:
            return {
                'status': 'skipped',
                'file': str(shapefile_path),
                'city': city_name,
                'message': 'No T column found'
            }
        
        # Check if T_C already exists
        if 'T_C' in gdf.columns:
            return {
                'status': 'skipped',
                'file': str(shapefile_path),
                'city': city_name,
                'message': 'T_C column already exists'
            }
        
        # Rename column
        gdf = gdf.rename(columns={'T': 'T_C'})
        
        # Save back to shapefile (overwrites original)
        gdf.to_file(shapefile_path)
        
        logger.info(f"✓ Renamed T → T_C in {city_name}/{shapefile_path.name}")
        
        return {
            'status': 'success',
            'file': str(shapefile_path),
            'city': city_name,
            'operation': 'rename_t_to_tc',
            'rows_processed': len(gdf)
        }
    
    except Exception as e:
        logger.error(f"Error processing {shapefile_path}: {e}")
        return {
            'status': 'error',
            'file': str(shapefile_path),
            'city': city_name,
            'error': str(e)
        }

def add_fahrenheit_column(shapefile_path, city_name):
    """
    Add T_F column (Fahrenheit) based on T column (Celsius)
    for Baltimore and Washington DC files
    """
    try:
        # Read shapefile
        gdf = gpd.read_file(shapefile_path)
        
        # Check if 'T' column exists
        if 'T' not in gdf.columns:
            return {
                'status': 'skipped',
                'file': str(shapefile_path),
                'message': 'No T column found'
            }
        
        # Check if T_F already exists
        if 'T_F' in gdf.columns:
            return {
                'status': 'skipped',
                'file': str(shapefile_path),
                'message': 'T_F column already exists'
            }
        
        # Add T_F column by converting T (Celsius) to Fahrenheit
        gdf['T_F'] = gdf['T'].apply(celsius_to_fahrenheit)
        
        # Save back to shapefile (overwrites original)
        gdf.to_file(shapefile_path)
        
        logger.info(f"✓ Added T_F column to {shapefile_path.name}")
        
        return {
            'status': 'success',
            'file': str(shapefile_path),
            'operation': 'add_fahrenheit',
            'city': city_name,
            'rows_processed': len(gdf)
        }
    
    except Exception as e:
        logger.error(f"Error processing {shapefile_path}: {e}")
        return {
            'status': 'error',
            'file': str(shapefile_path),
            'error': str(e)
        }

def process_cities(data_root, cities_to_process, max_workers=4):
    """
    Process traverse files for specified cities and rename T → T_C
    
    Parameters:
    - data_root: Root directory containing city folders
    - cities_to_process: List of city name patterns to match (e.g., ['San Juan', 'Baltimore', 'Washington'])
    - max_workers: Number of concurrent operations
    """
    data_root = Path(data_root)
    
    tasks = []
    
    # Find city directories matching the patterns
    for city_pattern in cities_to_process:
        city_dirs = list(data_root.glob(f"*{city_pattern}*"))
        
        logger.info(f"Found {len(city_dirs)} directories matching '{city_pattern}'")
        
        for city_dir in city_dirs:
            if not city_dir.is_dir():
                continue
            
            city_name = city_dir.name
            
            # Find traverse folders
            traverse_folders = list(city_dir.glob('traverses*'))
            if not traverse_folders:
                traverse_folders = [city_dir]
            
            for traverse_folder in traverse_folders:
                # Find all shapefiles
                shapefiles = list(traverse_folder.glob('**/*.shp'))
                
                for shp in shapefiles:
                    # Only process traverse files
                    if 'trav' in shp.stem.lower():
                        tasks.append((city_name, shp))
    
    logger.info(f"\nTotal traverse files to process: {len(tasks)}")
    
    if not tasks:
        logger.warning("No traverse files found!")
        return []
    
    results = []
    
    # Process all files concurrently
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        
        for city_name, shapefile_path in tasks:
            future = executor.submit(rename_t_to_tc, shapefile_path, city_name)
            futures[future] = (city_name, shapefile_path)
        
        completed = 0
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1
            
            city_name, shapefile_path = futures[future]
            status_symbol = "✓" if result['status'] == 'success' else ("⊘" if result['status'] == 'skipped' else "✗")
            
            print(f"[{completed}/{len(tasks)}] {status_symbol} {city_name}/{shapefile_path.name}")
    
    return results

# ============================================================================
# USAGE
# ============================================================================

# Set your base directory
BASE_DIR = ""

# ── Step 1: Dry run — preview every rename, write nothing ──────────────
renamer = TempColumnRenamer(BASE_DIR)
report = renamer.process_all(dry_run=True)

# Optionally export the preview to CSV for review
renamer.export_report('./rename_preview')

# ── Step 2: Apply renames only after reviewing the dry-run report ──────
# renamer_live = TempColumnRenamer(BASE_DIR)
# renamer_live.process_all(dry_run=False)
# renamer_live.export_report('./rename_results')
```
