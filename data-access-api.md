Here are the steps I followed to access and download the Heat Watch data via [OSF's API](https://developer.osf.io). 

1. Create an account on [OSF](https://accounts.osf.io/login) and create a personal access token.

2. Authenticate via API with PAT in a Jupyter Notebook

```python
# Import libraries for processing data
import requests
import json
import os
import pandas as pd
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor, as_completed
from email.message import EmailMessage
from urllib.parse import urlparse
import zipfile

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
```

```python
bearer_token = "your-token" 

headers = {
    "Authorization": f"Bearer {bearer_token}",
}

response = requests.get("https://api.osf.io/v2/users/me/", headers=headers)
if response.status_code == 200:
    print("Success")
else:
    print(f"Error: {response.json()}")
```

3. Call the parent project endpoint to retrieve list of sub-projects, and save each sub-project's files link to a csv 

```python
# Set up empty dataframe to store links for additional calls later on
init = {'id': [], 'files_link': [], 'title': []}
df = pd.DataFrame(init)

# Call parent project node for array of 10 sub-projects at a time, add `?page={n}` to url for subsequent pages
response = requests.get("https://api.osf.io/v2/users/9neka/nodes")

if response.status_code == 200:
    res = response.json()
    for proj in res['data']:
        curr_id = proj['id']
        # Call sub-project files endpoint 
        curr_res = requests.get(f"https://api.osf.io/v2/nodes/{curr_id}/files/")
        curr_data = curr_res.json()
        # Grab link to endpoint where project files are stored 
        link = curr_data['data'][0]['relationships']['files']['links']['related']['href']
        # Save id, file link and title in dataframe (created above)
        df.loc[df.shape[0]] = [curr_id, link, proj['attributes']['title']]
else:
    print(f"Error: {response.json()}")

# Save df as csv for later parsing
csv_file_path = 'file_links.csv'
df.to_csv(csv_file_path, index=False)
```

Note: I had to manually add the files links to the csv for cities that were nested within sub-project folders (Heat Watch Campaigns (2019), Mystic River (2021), Oklahoma City, Heat Watch VFIC, UHI Assessments (2017, 2018)). 

Resulting csv file: 

```csv
id, files_link, title
vwsh9, https://api.osf.io/v2/nodes/vwsh9/files/osfstorage/, Heat Watch Little Rock
apsx5, https://api.osf.io/v2/nodes/apsx5/files/osfstorage/, Heat Watch Palo Alto CA
zqa86, https://api.osf.io/v2/nodes/zqa86/files/osfstorage/, Heat Watch Laredo TX
86ume, https://api.osf.io/v2/nodes/86ume/files/osfstorage/, Heat Watch Charlotte NC
...
```

4. To get the actual data, I asked Claude via Copilot to write me a script with the following instructions: First, call the endpoint in the files_link column. Then, make a call to download each of the files in the response from the first call to my virtual machine. These files need to be saved to a new folder named with the corresponding "title" string from the df.

Here's the script I ended up running, mainly written by Claude with a few modifications:

```python
def extract_filename_from_header(content_disposition):
    """Extract filename from Content-Disposition header"""
    msg = EmailMessage()
    msg['Content-Disposition'] = content_disposition
    return msg.get_filename() or 'downloaded_file'

def download_file(file_url, save_path):
    """Download a single file and save it"""
    try:
        response = requests.get(file_url, timeout=30)
        response.raise_for_status()

        # Extract filename from Content-Disposition header if available
        if 'content-disposition' in response.headers:
            filename = extract_filename_from_header(response.headers['content-disposition'])
        else:
            # Fallback: extract from URL
            filename = urlparse(file_url).path.split('/')[-1] or 'file'

        filepath = save_path / filename

        if response.status_code == 200:
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192): # Stream in chunks
                    f.write(chunk)
            print(f"File successfully downloaded to {filepath}")
        else:
            print(f"Failed to download file. Status code: {response.status_code}")
            print(f"Response: {response.text}")

        return {'status': 'success', 'filename': filename, 'url': file_url}
    except Exception as e:
        return {'status': 'error', 'filename': None, 'url': file_url, 'error': str(e)}

def fetch_file_list(api_url):
    """Fetch the list of files from the API endpoint"""
    try:
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching {api_url}: {e}")
        return None

def process_row(row, base_output_dir, max_workers=5):
    """Process a single row: fetch files list and download files"""
    row_id = row['id']
    files_link = row['files_link']
    title = row['title']

    # Create folder for this title
    folder_path = Path(base_output_dir) / title
    folder_path.mkdir(parents=True, exist_ok=True)
    
    # Fetch file list from API
    file_list = fetch_file_list(files_link)
    if not file_list:
        return {'id': row_id, 'status': 'failed', 'message': 'Could not fetch file list'}

    # Extract download URLs from response
    download_urls = []
    for item in file_list.get('data', []):
        try:
            print(f"download link is: {item['links']['download']}")
            download_urls.append(item['links']['download'])
        except KeyError:
            print("KeyError caught: no download link")
    
    # Download files concurrently
    downloaded_files = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(download_file, url, folder_path): url for url in download_urls}
        for future in as_completed(futures):
            result = future.result()
            downloaded_files.append(result)
    
    successful_downloads = sum(1 for f in downloaded_files if f['status'] == 'success')
    
    return {
        'id':  row_id,
        'title': title,
        'status': 'completed',
        'files_requested': len(download_urls),
        'files_downloaded': successful_downloads,
        'folder': str(folder_path)
    }

def process_dataframe(df, base_output_dir='./data', max_workers_per_row=5, max_concurrent_rows=3):
    """
    Process entire dataframe with concurrent execution
    
    Parameters:
    - df: pandas DataFrame with columns 'id', 'files_link', 'title'
    - base_output_dir: directory to save files
    - max_workers_per_row: threads for downloading files per row
    - max_concurrent_rows: concurrent row processing
    """
    
    results = []
    
    # Create base output directory
    Path(base_output_dir).mkdir(parents=True, exist_ok=True)
    
    # Process rows concurrently
    with ThreadPoolExecutor(max_workers=max_concurrent_rows) as executor:
        futures = {
            executor.submit(process_row, row, base_output_dir, max_workers_per_row): idx 
            for idx, (_, row) in enumerate(df.iterrows())
        }
        
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
                print(f"Processed:  {result['title']} - {result['files_downloaded']} files downloaded")
            except Exception as e:
                print(f"Error processing row:  {e}")
    
    return pd.DataFrame(results)
```

5. The data was now available on my VM, with the raw data files contained in zip files. I asked Claude to write another script that I could use to unzip all these nested files.

Here's what I ended up running, again mainly written by Claude with a few modifications:

```python
def validate_zip_file(zip_path):
    """Validate that the zip file is not corrupted"""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            bad_file = zip_ref.testzip()
            if bad_file:
                return False, f"Corrupted file in archive: {bad_file}"
        return True, "Valid"
    except zipfile.BadZipFile:
        return False, "Not a valid zip file"
    except Exception as e:
        return False, str(e)

def unzip_file_advanced(zip_path, verify=True):
    """
    Unzip a single file with validation and error handling
    """
    try:
        zip_path = Path(zip_path)
        parent_folder = zip_path.parent
        output_folder = parent_folder / zip_path.stem
        
        # Validate zip before extraction
        if verify:
            is_valid, message = validate_zip_file(zip_path)
            if not is_valid:
                return {
                    'status': 'error',
                    'zip_file': str(zip_path),
                    'error': f"Validation failed: {message}"
                }
        
        # Create output folder
        output_folder.mkdir(parents=True, exist_ok=True)
        
        # Extract with progress
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            members = zip_ref.namelist()
            zip_ref.extractall(output_folder)
        
        # Verify extraction
        extracted_items = list(output_folder.rglob('*'))
        
        logger.info(f"Successfully extracted {len(extracted_items)} items from {zip_path.name}")
        
        return {
            'status': 'success',
            'zip_file': str(zip_path),
            'output_folder': str(output_folder),
            'files_extracted': len(extracted_items),
            'archive_members': len(members)
        }
    
    except Exception as e:
        logger.error(f"Error extracting {zip_path}:  {e}")
        return {
            'status': 'error',
            'zip_file': str(zip_path),
            'error': str(e)
        }

def unzip_all_folders_advanced(base_path, max_workers=8, verify=True):
    """
    Find and unzip all .zip files with validation
    """
    base_path = Path(base_path)
    
    # Find all .zip files
    zip_files = sorted(list(base_path.rglob('*.zip')))
    
    logger.info(f"Found {len(zip_files)} zip files")
    
    if not zip_files:
        logger.warning("No zip files found!")
        return []
    
    results = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(unzip_file_advanced, zip_path, verify): zip_path 
            for zip_path in zip_files
        }
        
        completed = 0
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            completed += 1
            
            status_symbol = "✓" if result['status'] == 'success' else "✗"
            zip_name = Path(result['zip_file']).name
            print(f"[{completed}/{len(zip_files)}] {status_symbol} {zip_name}")
    
    return results
```

6. I moved the folders with the data to another space with larger storage on GMU's computing cluster, and then continued with the data formatting/cleaning steps as outlined in [this file](./data-cleaning-processing.md).
