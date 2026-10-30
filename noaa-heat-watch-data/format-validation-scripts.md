Code to validate all traverse folders and files follow the correct format:

```python
# Import libraries for processing data
from pathlib import Path
import re
import pandas as pd
import geopandas as gpd
from collections import defaultdict

from concurrent.futures import ThreadPoolExecutor, as_completed
```

```python
"""
Validate Heat Watch file structure against standard format
Checks city folders, traverses folders, and traverse files
"""

class HeatWatchFolderFileValidator:
    """Validate Heat Watch data structure and file naming conventions"""
    
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.validation_results = []
        self.errors = []
        self.warnings = []
        
        # Standard patterns from data-format.md
        self.city_folder_pattern = r'^Heat Watch .+, [A-Z]{2}$'
        self.traverses_folder_pattern = r'^traverses_chw_\w+(_\d+)?$'
        self.traverse_file_pattern = r'^(am|af|pm)_trav\.(shp|dbf|prj|shx|cpg|sbn|sbx|xml|RDS|shp\.xml)$'
        
        # Valid time periods
        self.valid_times = ['am', 'af', 'pm']
        
        # Valid shapefile extensions
        self.shapefile_extensions = ['.shp', '.dbf', '.prj', '.shx', '.cpg', '.sbn', '.sbx', '.shp.xml', '.xml', '.RDS']
    
    def validate_city_folder_name(self, folder_path):
        """Validate city folder naming: 'Heat Watch {City Name}, {ST}'"""
        folder_name = folder_path.name
        
        if not re.match(self.city_folder_pattern, folder_name):
            self.errors.append({
                'type': 'city_folder_name',
                'path': str(folder_path),
                'issue': f'City folder name does not match pattern: "Heat Watch {{City}}, {{ST}}"',
                'current_name': folder_name
            })
            return False
        
        # Check state abbreviation is 2 letters
        parts = folder_name.rsplit(', ', 1)
        if len(parts) == 2:
            state = parts[1]
            if not (len(state) == 2 and state.isupper()):
                self.warnings.append({
                    'type': 'state_abbreviation',
                    'path': str(folder_path),
                    'issue': f'State abbreviation should be 2 uppercase letters: "{state}"',
                    'current_name': folder_name
                })
        
        return True
    
    def validate_traverses_folder_name(self, folder_path, city_name):
        """Validate traverses folder naming: 'traverses_chw_{state_name}_{ID?}'"""
        folder_name = folder_path.name
        
        if not re.match(self.traverses_folder_pattern, folder_name):
            self.errors.append({
                'type': 'traverses_folder_name',
                'city': city_name,
                'path': str(folder_path),
                'issue': f'Traverses folder name does not match pattern: "traverses_chw_{{state_name}}_{{ID?}}"',
                'current_name': folder_name
            })
            return False
        
        return True
    
    def validate_traverse_file_name(self, file_path, city_name):
        """Validate traverse file naming: '{time}_trav.{ext}'"""
        file_name = file_path.name
        
        # Handle .shp.xml special case
        if file_name.endswith('.shp.xml'):
            base_name = file_name[:-8]  # Remove .shp.xml
            ext = '.shp.xml'
        else:
            base_name = file_path.stem
            ext = file_path.suffix
        
        if not re.match(self.traverse_file_pattern, file_name):
            self.errors.append({
                'type': 'traverse_file_name',
                'city': city_name,
                'path': str(file_path),
                'issue': f'Traverse file name does not match pattern: "{{am|af|pm}}_trav.{{ext}}"',
                'current_name': file_name
            })
            return False
        
        # Validate time period
        time_period = base_name.split('_')[0]
        if time_period not in self.valid_times:
            self.errors.append({
                'type': 'invalid_time_period',
                'city': city_name,
                'path': str(file_path),
                'issue': f'Invalid time period: "{time_period}". Must be am, af, or pm',
                'current_name': file_name
            })
            return False
        
        # Validate extension
        if ext not in self.shapefile_extensions:
            self.warnings.append({
                'type': 'unexpected_extension',
                'city': city_name,
                'path': str(file_path),
                'issue': f'Unexpected file extension: "{ext}"',
                'current_name': file_name
            })
        
        return True
    
    def check_missing_shapefile_components(self, traverses_folder, city_name):
        """Check if all shapefile components exist for each base file"""
        shapefiles = defaultdict(list)
        
        # Group files by base name and time period
        for file_path in traverses_folder.iterdir():
            if file_path.is_file():
                file_name = file_path.name
                
                # Handle .shp.xml
                if file_name.endswith('.shp.xml'):
                    base = file_name[:-8]
                    ext = '.shp.xml'
                else:
                    base = file_path.stem
                    ext = file_path.suffix
                
                # Extract time period
                match = re.match(r'^(am|af|pm)_trav$', base)
                if match:
                    time_period = match.group(1)
                    shapefiles[time_period].append(ext)
        
        # Check for required components
        required_extensions = ['.shp', '.dbf', '.prj', '.shx']
        
        for time_period, extensions in shapefiles.items():
            missing = [ext for ext in required_extensions if ext not in extensions]
            
            if missing:
                self.warnings.append({
                    'type': 'missing_shapefile_components',
                    'city': city_name,
                    'path': str(traverses_folder),
                    'issue': f'Missing shapefile components for {time_period}_trav: {", ".join(missing)}',
                    'time_period': time_period
                })
    
    def check_missing_time_periods(self, traverses_folder, city_name):
        """Check if all three time periods (am, af, pm) are present"""
        shp_files = list(traverses_folder.glob("*.shp"))
        time_periods_found = set()
        
        for shp_file in shp_files:
            match = re.match(r'^(am|af|pm)_trav\.shp$', shp_file.name)
            if match:
                time_periods_found.add(match.group(1))
        
        missing_periods = set(self.valid_times) - time_periods_found
        
        if missing_periods:
            self.warnings.append({
                'type': 'missing_time_periods',
                'city': city_name,
                'path': str(traverses_folder),
                'issue': f'Missing time periods: {", ".join(sorted(missing_periods))}',
                'found': sorted(time_periods_found),
                'missing': sorted(missing_periods)
            })
    
    def validate_city_folder(self, city_folder):
        """Validate a single city folder and its contents"""
        city_name = city_folder.name
        
        # Validate city folder name
        self.validate_city_folder_name(city_folder)
        
        # Find traverses folders
        traverses_folders = [d for d in city_folder.iterdir() 
                            if d.is_dir() and d.name.startswith('traverses')]
        
        if not traverses_folders:
            self.errors.append({
                'type': 'missing_traverses_folder',
                'city': city_name,
                'path': str(city_folder),
                'issue': 'No traverses folder found'
            })
            return
        
        # Validate each traverses folder
        for traverses_folder in traverses_folders:
            # Validate folder name
            self.validate_traverses_folder_name(traverses_folder, city_name)
            
            # Validate files within traverses folder
            files = [f for f in traverses_folder.iterdir() if f.is_file()]
            
            if not files:
                self.warnings.append({
                    'type': 'empty_traverses_folder',
                    'city': city_name,
                    'path': str(traverses_folder),
                    'issue': 'Traverses folder is empty'
                })
                continue
            
            # Validate each file
            for file_path in files:
                self.validate_traverse_file_name(file_path, city_name)
            
            # Check for missing components and time periods
            self.check_missing_shapefile_components(traverses_folder, city_name)
            self.check_missing_time_periods(traverses_folder, city_name)
    
    def validate_all(self):
        """Validate all city folders in base directory"""
        if not self.base_dir.exists():
            print(f"Error: Base directory does not exist: {self.base_dir}")
            return None
        
        city_folders = sorted([d for d in self.base_dir.iterdir() if d.is_dir()])
        
        print(f"Found {len(city_folders)} city folders")
        print("=" * 70)
        
        for city_folder in city_folders:
            self.validate_city_folder(city_folder)
        
        return self.generate_report()
    
    def generate_report(self):
        """Generate validation report"""
        print("\n" + "=" * 70)
        print("VALIDATION REPORT")
        print("=" * 70)
        
        total_issues = len(self.errors) + len(self.warnings)
        
        if total_issues == 0:
            print("\n✓ All files and folders conform to the standard format!")
            return None
        
        print(f"\nTotal Issues Found: {total_issues}")
        print(f"  Errors: {len(self.errors)}")
        print(f"  Warnings: {len(self.warnings)}")
        
        # Errors by type
        if self.errors:
            print("\n" + "=" * 70)
            print("ERRORS (Must Fix)")
            print("=" * 70)
            
            errors_df = pd.DataFrame(self.errors)
            error_counts = errors_df['type'].value_counts()
            
            print("\nErrors by Type:")
            for error_type, count in error_counts.items():
                print(f"  {error_type}: {count}")
            
            print("\nDetailed Errors:")
            for idx, error in enumerate(self.errors[:20], 1):  # Show first 20
                print(f"\n{idx}. {error['type'].upper()}")
                print(f"   City: {error.get('city', 'N/A')}")
                print(f"   Issue: {error['issue']}")
                print(f"   Path: {error['path']}")
                if 'current_name' in error:
                    print(f"   Current Name: {error['current_name']}")
            
            if len(self.errors) > 20:
                print(f"\n... and {len(self.errors) - 20} more errors")
        
        # Warnings by type
        if self.warnings:
            print("\n" + "=" * 70)
            print("WARNINGS (Should Review)")
            print("=" * 70)
            
            warnings_df = pd.DataFrame(self.warnings)
            warning_counts = warnings_df['type'].value_counts()
            
            print("\nWarnings by Type:")
            for warning_type, count in warning_counts.items():
                print(f"  {warning_type}: {count}")
            
            print("\nDetailed Warnings (first 10):")
            for idx, warning in enumerate(self.warnings[:10], 1):
                print(f"\n{idx}. {warning['type'].upper()}")
                print(f"   City: {warning.get('city', 'N/A')}")
                print(f"   Issue: {warning['issue']}")
                if 'missing' in warning:
                    print(f"   Missing: {warning['missing']}")
            
            if len(self.warnings) > 10:
                print(f"\n... and {len(self.warnings) - 10} more warnings")
        
        # Return dataframes for further analysis
        return {
            'errors': pd.DataFrame(self.errors) if self.errors else None,
            'warnings': pd.DataFrame(self.warnings) if self.warnings else None
        }
    
    def export_report(self, output_dir):
        """Export validation report to CSV files"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        if self.errors:
            errors_df = pd.DataFrame(self.errors)
            errors_df.to_csv(output_dir / 'validation_errors.csv', index=False)
            print(f"\nErrors exported to: {output_dir / 'validation_errors.csv'}")
        
        if self.warnings:
            warnings_df = pd.DataFrame(self.warnings)
            warnings_df.to_csv(output_dir / 'validation_warnings.csv', index=False)
            print(f"Warnings exported to: {output_dir / 'validation_warnings.csv'}")
        
        # Summary report
        summary = {
            'total_errors': len(self.errors),
            'total_warnings': len(self.warnings),
            'cities_with_errors': len(set(e.get('city', 'Unknown') for e in self.errors)),
            'cities_with_warnings': len(set(w.get('city', 'Unknown') for w in self.warnings))
        }
        
        pd.DataFrame([summary]).to_csv(output_dir / 'validation_summary.csv', index=False)
        print(f"Summary exported to: {output_dir / 'validation_summary.csv'}")

# ============================================================================
# USAGE
# ============================================================================

# Set your base directory
BASE_DIR = ""

validator = HeatWatchDataValidator(BASE_DIR)
results = validator.validate_all()

# Export reports
validator.export_report("./validation_reports")
```

Code to validate temperature column names:

```python
class TraverseTempColumnValidator:
    """
    Validate temperature column names in traverse shapefiles across all Heat Watch cities.

    Per data-format.md, every traverse shapefile MUST contain a 'temp_f' column.
    Other temperature column variants (e.g. 'T_C', 'T_F', 'Temprtr') are flagged as
    warnings so they can be reviewed and renamed during normalization.
    """

    # The single required temperature column name per data-format.md
    REQUIRED_TEMP_COLUMN = 'temp_f'

    # Recognized non-standard temperature column names (trigger warnings, not errors)
    KNOWN_TEMP_VARIANTS = ['Temprtr', 't_f', 'T', 'T_F', 'T_C', 't_c', 'temp_c']

    # Substrings used to detect any temperature-like column not in the above sets
    TEMP_INDICATORS = ['temp', 'hi_f']

    def __init__(self, data_root):
        self.data_root = Path(data_root)
        self.errors = []
        self.warnings = []
        # Full inventory of all files scanned (for export/reporting)
        self._file_records = []

    def _extract_city_name(self, city_dir_name: str) -> str:
        """Clean up city name from directory"""
        # Remove 'Heat Watch ' prefix if present
        return city_dir_name.replace('Heat Watch ', '')

    def _detect_temp_columns(self, columns: list[str]) -> dict:
        """
        Categorise all columns in a shapefile by temperature role.

        Returns a dict with keys:
          - 'has_required'  : bool  — True if 'temp_f' is present
          - 'known_variants': list  — recognised non-standard temp columns
          - 'unknown_temp'  : list  — columns that look like temperature but are unrecognised
          - 'all_temp_cols' : list  — union of the above three groups
        """
        has_required = self.REQUIRED_TEMP_COLUMN in columns
        known_variants = [c for c in columns if c in self.KNOWN_TEMP_VARIANTS]
        all_temp_cols = ([self.REQUIRED_TEMP_COLUMN] if has_required else []) + known_variants

        # Heuristic scan for anything else that looks like temperature
        unknown_temp = [
            c for c in columns
            if c not in all_temp_cols
            and any(ind.lower() in c.lower() for ind in self.TEMP_INDICATORS)
        ]
        all_temp_cols += unknown_temp

        return {
            'has_required': has_required,
            'known_variants': known_variants,
            'unknown_temp': unknown_temp,
            'all_temp_cols': all_temp_cols,
        }

    # Per-file validation
    def validate_shapefile(self, shapefile_path: Path, city_name: str) -> dict | None:
        """
        Validate a single traverse shapefile's temperature columns.

        Returns a record dict (used for reporting), or None on load failure.
        """
        try:
            gdf = gpd.read_file(shapefile_path)
        except Exception as exc:
            self.errors.append({
                'type': 'shapefile_load_error',
                'city': city_name,
                'path': str(shapefile_path),
                'filename': shapefile_path.name,
                'issue': f'Could not open shapefile: {exc}',
            })
            return None

        columns = list(gdf.columns)
        temp_info = self._detect_temp_columns(columns)

        record = {
            'city': city_name,
            'filename': shapefile_path.name,
            'path': str(shapefile_path),
            'all_columns': columns,
            'has_required_temp_f': temp_info['has_required'],
            'known_variant_cols': temp_info['known_variants'],
            'unknown_temp_cols': temp_info['unknown_temp'],
            'all_temp_cols': temp_info['all_temp_cols'],
            'status': 'ok',
        }

        # ERROR: missing required 'temp_f'
        if not temp_info['has_required']:
            record['status'] = 'error'
            self.errors.append({
                'type': 'missing_required_temp_column',
                'city': city_name,
                'path': str(shapefile_path),
                'filename': shapefile_path.name,
                'issue': (
                    f"Required column '{self.REQUIRED_TEMP_COLUMN}' not found. "
                    f"Temperature-like columns present: {temp_info['all_temp_cols'] or 'none'}"
                ),
            })

        # WARNING: known non-standard variants present
        if temp_info['known_variants']:
            record['status'] = record['status'] if record['status'] == 'error' else 'warning'
            self.warnings.append({
                'type': 'non_standard_temp_column',
                'city': city_name,
                'path': str(shapefile_path),
                'filename': shapefile_path.name,
                'issue': (
                    f"Non-standard (but recognised) temperature columns found: "
                    f"{temp_info['known_variants']}. "
                    f"These should be renamed to '{self.REQUIRED_TEMP_COLUMN}' or 'T_C' "
                    f"during normalisation."
                ),
                'columns': temp_info['known_variants'],
            })

        # WARNING: unrecognised temperature-like columns
        if temp_info['unknown_temp']:
            record['status'] = record['status'] if record['status'] == 'error' else 'warning'
            self.warnings.append({
                'type': 'unrecognised_temp_column',
                'city': city_name,
                'path': str(shapefile_path),
                'filename': shapefile_path.name,
                'issue': (
                    f"Unrecognised temperature-like columns found: {temp_info['unknown_temp']}. "
                    f"Manually verify whether these are temperature columns."
                ),
                'columns': temp_info['unknown_temp'],
            })

        return record

    # City-level traversal
    def validate_city(self, city_dir: Path):
        """Validate all traverse shapefiles within a single city folder."""
        city_name = self._extract_city_name(city_dir.name)

        traverse_folders = list(city_dir.glob('traverses*'))
        if not traverse_folders:
            # Fallback: look directly in the city folder
            traverse_folders = [city_dir]

        processed = set()
        for folder in traverse_folders:
            for shp_path in sorted(folder.glob('*trav.shp')):
                if str(shp_path) in processed:
                    continue
                processed.add(str(shp_path))

                record = self.validate_shapefile(shp_path, city_name)
                if record:
                    self._file_records.append(record)

    # Full dataset validation
    def validate_all(self) -> dict | None:
        """Validate temperature columns in every city folder under data_root."""
        if not self.data_root.exists():
            print(f"Error: Base directory does not exist: {self.data_root}")
            return None

        city_dirs = sorted([d for d in self.data_root.iterdir() if d.is_dir()])
        print(f"Found {len(city_dirs)} city folders")
        print("=" * 70)

        for city_dir in city_dirs:
            self.validate_city(city_dir)

        return self.generate_report()

    # Reporting
    def generate_report(self) -> dict | None:
        """Print a structured validation report and return DataFrames."""
        print("\n" + "=" * 70)
        print("TEMPERATURE COLUMN VALIDATION REPORT")
        print("=" * 70)

        total_files = len(self._file_records)
        total_issues = len(self.errors) + len(self.warnings)

        print(f"\nTotal traverse shapefiles scanned: {total_files}")

        if total_issues == 0:
            print("\n✓ All traverse shapefiles contain the required 'temp_f' column!")
            return None

        print(f"\nTotal Issues Found: {total_issues}")
        print(f"  Errors   (must fix) : {len(self.errors)}")
        print(f"  Warnings (review)   : {len(self.warnings)}")

        if self.errors:
            print("\n" + "=" * 70)
            print("ERRORS (Must Fix)")
            print("=" * 70)

            errors_df = pd.DataFrame(self.errors)
            print("\nErrors by Type:")
            for error_type, count in errors_df['type'].value_counts().items():
                print(f"  {error_type}: {count}")

            print("\nDetailed Errors:")
            for idx, err in enumerate(self.errors[:20], 1):
                print(f"\n{idx}. {err['type'].upper()}")
                print(f"   City    : {err.get('city', 'N/A')}")
                print(f"   File    : {err.get('filename', 'N/A')}")
                print(f"   Issue   : {err['issue']}")
                print(f"   Path    : {err['path']}")

            if len(self.errors) > 20:
                print(f"\n... and {len(self.errors) - 20} more errors")

        if self.warnings:
            print("\n" + "=" * 70)
            print("WARNINGS (Should Review)")
            print("=" * 70)

            warnings_df = pd.DataFrame(self.warnings)
            print("\nWarnings by Type:")
            for warn_type, count in warnings_df['type'].value_counts().items():
                print(f"  {warn_type}: {count}")

            # Column-name frequency table — useful for deciding what to normalise
            all_variant_cols = []
            for w in self.warnings:
                all_variant_cols.extend(w.get('columns', []))
            if all_variant_cols:
                variant_series = pd.Series(all_variant_cols).value_counts()
                print("\nNon-standard Temperature Column Name Frequencies:")
                for col_name, count in variant_series.items():
                    print(f"  '{col_name}': {count} file(s)")

            print("\nDetailed Warnings (first 10):")
            for idx, warn in enumerate(self.warnings[:10], 1):
                print(f"\n{idx}. {warn['type'].upper()}")
                print(f"   City    : {warn.get('city', 'N/A')}")
                print(f"   File    : {warn.get('filename', 'N/A')}")
                print(f"   Issue   : {warn['issue']}")

            if len(self.warnings) > 10:
                print(f"\n... and {len(self.warnings) - 10} more warnings")

        print("\n" + "=" * 70)
        print("CONFORMANCE SUMMARY BY CITY")
        print("=" * 70)
        records_df = pd.DataFrame(self._file_records)
        city_summary = (
            records_df.groupby('city')['status']
            .value_counts()
            .unstack(fill_value=0)
            .reindex(columns=['ok', 'warning', 'error'], fill_value=0)
        )
        print(city_summary.to_string())

        return {
            'errors': pd.DataFrame(self.errors) if self.errors else None,
            'warnings': pd.DataFrame(self.warnings) if self.warnings else None,
            'file_records': records_df,
        }

    def export_report(self, output_dir: str):
        """Export validation report CSVs to output_dir."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if self.errors:
            errors_path = output_dir / 'temp_column_errors.csv'
            pd.DataFrame(self.errors).to_csv(errors_path, index=False)
            print(f"\nErrors exported to: {errors_path}")

        if self.warnings:
            warnings_path = output_dir / 'temp_column_warnings.csv'
            pd.DataFrame(self.warnings).to_csv(warnings_path, index=False)
            print(f"Warnings exported to: {warnings_path}")

        if self._file_records:
            records_df = pd.DataFrame(self._file_records)

            # Full per-file inventory
            inventory_path = output_dir / 'temp_column_inventory.csv'
            records_df.to_csv(inventory_path, index=False)
            print(f"Full file inventory exported to: {inventory_path}")

            # Summary
            summary = {
                'total_files_scanned': len(records_df),
                'files_ok': int((records_df['status'] == 'ok').sum()),
                'files_with_warnings': int((records_df['status'] == 'warning').sum()),
                'files_with_errors': int((records_df['status'] == 'error').sum()),
                'total_errors': len(self.errors),
                'total_warnings': len(self.warnings),
                'cities_with_errors': len(
                    set(e.get('city', 'Unknown') for e in self.errors)
                ),
                'cities_with_warnings': len(
                    set(w.get('city', 'Unknown') for w in self.warnings)
                ),
            }
            summary_path = output_dir / 'temp_column_summary.csv'
            pd.DataFrame([summary]).to_csv(summary_path, index=False)
            print(f"Summary exported to: {summary_path}")

# ============================================================================
# USAGE
# ============================================================================

# Set your base directory
BASE_DIR = ""

validator = TraverseTempColumnValidator(BASE_DIR)
results = validator.validate_all()

# Optionally export all CSVs
validator.export_report('./validation_output')
```
