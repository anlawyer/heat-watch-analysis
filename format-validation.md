Code to validate all traverse folders and files follow the correct format:

```python
"""
Validate Heat Watch data structure against standard format
Checks city folders, traverses folders, and traverse files
"""

class HeatWatchDataValidator:
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
```
