import json
import os
import tifffile
import numpy as np
import pandas as pd
import requests
from pathlib import Path
import shutil
import mimetypes

def _resolve_local_path(path, must_exist=False):
    """
    Resolve local user-entered paths safely.

    Fixes cases like:
        cwd   = /home/user/fusion_demo_notebooks
        input = fusion_demo_notebooks/datasets/data_1
        final = /home/user/fusion_demo_notebooks/datasets/data_1

    Also handles:
        blue/...   -> /blue/...
        orange/... -> /orange/...
        home/...   -> /home/...
    """
    if path is None:
        return None

    raw_path = str(path).strip().strip('"').strip("'")
    raw_path = os.path.expanduser(raw_path)

    if os.path.isabs(raw_path):
        resolved = os.path.abspath(raw_path)
        if must_exist and not os.path.exists(resolved):
            raise FileNotFoundError(f"Path not found: {resolved}")
        return resolved

    cwd = os.getcwd()
    normalized = raw_path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]

    candidates = []

    # Fix duplicated current-folder prefix.
    # Example:
    # cwd = /home/user/fusion_demo_notebooks
    # raw = fusion_demo_notebooks/datasets/...
    # candidate = /home/user/fusion_demo_notebooks/datasets/...
    if parts:
        cur = cwd
        while cur and cur != os.path.dirname(cur):
            if os.path.basename(cur) == parts[0]:
                candidates.append(os.path.abspath(os.path.join(os.path.dirname(cur), raw_path)))
            cur = os.path.dirname(cur)

        # Handle missing leading slash for known HPC roots.
        if parts[0] in {"home", "blue", "orange"}:
            candidates.append(os.path.abspath(os.path.join(os.sep, raw_path)))

    # Normal cwd-relative fallback.
    candidates.append(os.path.abspath(os.path.join(cwd, raw_path)))

    for candidate in dict.fromkeys(candidates):
        if os.path.exists(candidate):
            return candidate

    if must_exist:
        checked = "\n".join(f"  - {c}" for c in dict.fromkeys(candidates))
        raise FileNotFoundError(f"Path not found: {raw_path}\nChecked:\n{checked}")

    return candidates[0]

def download_folder_zip_from_fusion_backend(gc, folder_id, output_dir="."):
    output_dir = _resolve_local_path(output_dir, must_exist=False)
    os.makedirs(output_dir, exist_ok=True)
    
    # Get the folder info so we can name the zip file correctly
    folder_info = gc.get(f"/folder/{folder_id}")
    folder_name = folder_info["name"]
    zip_filename = os.path.join(output_dir, f"{folder_name}.zip")
    
    # Construct the full URL using the Girder client's base URL
    url = f"{gc.urlBase}/folder/{folder_id}/download"
    
    # Pass the Girder token in the headers for authentication
    headers = {"Girder-Token": gc.token}
    
    print(f"Downloading folder '{folder_name}' as a zip archive...")
    
    # Make the raw streaming request to the endpoint
    response = requests.get(url, headers=headers, stream=True)
    response.raise_for_status()
    
    # Write the zip file to your local machine in chunks
    with open(zip_filename, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                
    print(f"Successfully downloaded: {zip_filename}")
    return zip_filename


def download_from_fusion_to_workspace(gc, resource_id, resource_type="file", output_dir="."):
    output_dir = _resolve_local_path(output_dir, must_exist=False)

    if resource_type == "file":
        info = gc.get(f"/file/{resource_id}")
        file_name = info["name"]

        # Use the file name as the folder name
        folder_name = os.path.splitext(file_name)[0]
        folder_path = os.path.join(output_dir, "datasets", folder_name, "image")
        os.makedirs(folder_path, exist_ok=True)

        # Set the final output path inside the new folder
        out_path = os.path.join(folder_path, file_name)

        print(f"Downloading file: {file_name} ({info.get('size', 'unknown')} bytes) to {out_path}...")
        gc.downloadFile(resource_id, path=out_path)

    elif resource_type == "item":
        os.makedirs(output_dir, exist_ok=True)
        info = gc.get(f"/item/{resource_id}")
        item_name = info["name"]

        print(f"Downloading item: {item_name} to {output_dir}/...")
        gc.downloadItem(resource_id, output_dir)
        out_path = os.path.join(output_dir, item_name)

    elif resource_type == "folder":
        info = gc.get(f"/folder/{resource_id}")
        folder_name = info["name"]

        out_path = os.path.join(output_dir, folder_name)
        os.makedirs(out_path, exist_ok=True)

        print(f"Downloading folder: {folder_name} to {out_path}/...")

        # Download all items directly inside this folder
        for item in gc.listItem(resource_id):
            download_from_fusion_to_workspace(
                gc,
                resource_id=item["_id"],
                resource_type="item",
                output_dir=out_path
            )

        # Recursively download all subfolders
        for subfolder in gc.listFolder(resource_id):
            download_from_fusion_to_workspace(
                gc,
                resource_id=subfolder["_id"],
                resource_type="folder",
                output_dir=out_path
            )

    else:
        raise ValueError("resource_type must be either 'file', 'item', or 'folder'")

    print("Done.")
    return out_path


def fetch_data_info_from_HuBMAP(hubmap_id, all=False, histology=False, visium=False):
    """
    Fetch data files from HubMAP for a given dataset.
    
    Args:
        hubmap_id (str): The HubMAP ID to fetch data for.
        all (bool): If True, fetch all available files. Default behavior if no filter specified.
        histology (bool): If True, fetch only .ome.tiff or .ome.tif files.
        visium (bool): If True, fetch only .h5ad files.
    
    Returns:
        dict: Contains hubmap_id, dataset_type, and a list of datasets with their
              uuid and filtered rel_paths for files.
    """
    search_api = "https://search.api.hubmapconsortium.org/v3/search"
    
    # Get initial dataset
    ds_payload = {
        "query": {
            "bool": {
                "must": [
                    {"match": {"hubmap_id": hubmap_id}}
                ]
            }
        },
        "_source": ["dataset_type", "descendants", "hubmap_id", "uuid", "files"],
        "size": 1
    }
    
    r = requests.post(search_api, json=ds_payload)
    response_data = r.json()
    
    if not response_data['hits']['hits']:
        return {"error": "No dataset found for the given hubmap_id"}
    
    source = response_data['hits']['hits'][0]['_source']
    dataset_type = source.get('dataset_type')
    descendants = source.get('descendants', [])
    result = {
        "hubmap_id": hubmap_id,
        "dataset_type": dataset_type,
        "datasets": []
    }
    
    # Default to 'all' if no specific filter is provided
    if not all and not histology and not visium:
        all = True
    
    # Mapping of dataset types to their target descendant types
    target_mapping = {
        "Histology": ["Histology [Kaggle-1 Glomerulus Segmentation]","Histology [Image Pyramid]","Histology [Kaggle-1 Segmentation]"],
        "Visium (no probes)": ["Visium (no probes) [Salmon + Scanpy]", "Visium (no probes)"]
    }
    
    # Find all matching descendants
    target_descendants = []
    if dataset_type in target_mapping:
        mapped_value = target_mapping[dataset_type]
        for desc in descendants:
            desc_type = desc.get('dataset_type')
            if desc_type in mapped_value:
                target_descendants.append({
                    'uuid': desc.get('uuid'),
                    'dataset_type': desc_type
                })
    # Process each descendant separately
    if target_descendants:
        for target_desc in target_descendants:
            target_uuid = target_desc['uuid']
            target_type = target_desc['dataset_type']
            
            # Fetch descendant dataset details
            desc_payload = {
                "query": {
                    "bool": {
                        "must": [
                            {"match": {"uuid": target_uuid}}
                        ]
                    }
                },
                "_source": ["files", "uuid", "dataset_type"],
                "size": 1
            }
            
            desc_r = requests.post(search_api, json=desc_payload)
            desc_data = desc_r.json()
            
            if desc_data['hits']['hits']:
                desc_source = desc_data['hits']['hits'][0]['_source']
                files = desc_source.get('files', [])
                
                # Filter files based on parameters
                filtered_files = []
                
                if all:
                    # Fetch all files
                    filtered_files = [file.get('rel_path') for file in files if file.get('rel_path')]
                else:
                    for file in files:
                        rel_path = file.get('rel_path')
                        if not rel_path:
                            continue
                        
                        # Check for histology files
                        if histology and (rel_path.endswith('.ome.tiff') or rel_path.endswith('.ome.tif')):
                            filtered_files.append(rel_path)
                        # Check for visium files
                        if visium and rel_path.endswith('.h5ad'):
                            filtered_files.append(rel_path)
                
                result['datasets'].append({
                    'dataset_type': target_type,
                    'uuid': target_uuid,
                    'rel_paths': filtered_files,
                    'file_count': len(filtered_files)
                })
    else:
        result['available_descendants'] = [
            {
                'dataset_type': desc.get('dataset_type'),
                'uuid': desc.get('uuid')
            }
            for desc in descendants
        ]
    
    return result

def HuBMAP_to_workspace_download(hubmap_id, all=False, histology=False, visium=False, temp_download=False, Optimize_WSI=True, delete_originals=True):
    """
    Download files from HubMAP to the local workspace.
    
    Args:
        hubmap_id (str): The HubMAP ID to download data for.
        all (bool): If True, download all available files.
        histology (bool): If True, download only .ome.tiff or .ome.tif files.
        visium (bool): If True, download only .h5ad files.
        temp_download (bool): If True, download to a temporary folder.
        Optimize_WSI (bool): If True, convert ome.tif/ome.tiff files to single frame.
    
    Returns:
        dict: Download statistics and results.
    """
    # Fetch data from HubMAP
    print(f"Fetching data for HubMAP ID: {hubmap_id}")
    fetch_result = fetch_data_info_from_HuBMAP(hubmap_id, all=all, histology=histology, visium=visium)

    if temp_download:
        delete_originals = True
    
    if 'error' in fetch_result:
        print(f"Error: {fetch_result['error']}")
        return fetch_result
    
    if 'available_descendants' in fetch_result and 'datasets' not in fetch_result:
        print("No processed data available for this dataset.")
        return fetch_result
    
    # Determine base folder name
    folder_suffix = "_temp" if temp_download else ""
    base_folder = f"datasets/{hubmap_id}{folder_suffix}"

    if not temp_download:
        print(f"Download location: {base_folder}/")
        print("-" * 80)
    
    all_stats = {
        "total_files": 0,
        "downloaded": 0,
        "failed": 0,
        "skipped": 0,
        "total_size": 0,
        "downloaded_size": 0,
        "skipped_size": 0,
        "failed_size": 0,
        "optimized_wsi": 0
    }
    
    ome_files_to_optimize = []
    
    # Download files for each dataset
    for dataset in fetch_result.get('datasets', []):
        uuid = dataset['uuid']
        rel_paths = dataset['rel_paths']
        dataset_type = dataset['dataset_type']

        if not temp_download:
            #print(f"\nProcessing dataset: {dataset_type}")
            #print(f"UUID: {uuid}")
            print(f"Files to download to workspace: {dataset['file_count']}")
            print("-" * 80)
        
        base_path = Path(base_folder)
        
        for rel_path in rel_paths:
            # Create full local path
            local_file_path = base_path / rel_path
            
            # Skip if file already exists
            if local_file_path.exists():
                if not temp_download:
                    print(f"Skipping (already exists): {rel_path}")
                all_stats['skipped'] += 1
                all_stats['total_files'] += 1
                continue
            
            # Create parent directories
            local_file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Construct download URL
            url = f"https://assets.hubmapconsortium.org/{uuid}/{rel_path}"
            
            try:
                if not temp_download:
                    print(f"Downloading to workspace: {rel_path}")
                response = requests.get(url, stream=True)
                response.raise_for_status()
                
                # Write file in chunks
                with open(local_file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                all_stats['downloaded'] += 1
                all_stats['total_files'] += 1
                if not temp_download:
                    print(f"Successfully downloaded to workspace.")
                
                # Track ome.tif/ome.tiff files for optimization
                if Optimize_WSI and (rel_path.endswith('.ome.tiff') or rel_path.endswith('.ome.tif')):
                    ome_files_to_optimize.append(str(local_file_path))
                
            except requests.exceptions.RequestException as e:
                print(f"Failed to download to workspace: {e}")
                all_stats['failed'] += 1
                all_stats['total_files'] += 1

    # Optimize WSI files if requested
    if Optimize_WSI and ome_files_to_optimize:
        print("\n" + "=" * 80)
        print("OPTIMIZING WSI FILES")
        print("=" * 80)
        for ome_file in ome_files_to_optimize:
            try:
                print(f"Converting to single frame: {ome_file}")
                optimize_workspace_wsi(source=ome_file, delete_originals=delete_originals)
                all_stats['optimized_wsi'] += 1
                print(f"Successfully optimized")
            except Exception as e:
                print(f"Failed to optimize: {e}")
    
    # Print summary
    if not temp_download:
        print("\n" + "=" * 80)
        print("DOWNLOAD SUMMARY")
        print("=" * 80)
        print(f"Total files:            {all_stats['total_files']}")
        print(f"Successfully downloaded: {all_stats['downloaded']}")
        print(f"Optimised images: {all_stats['optimized_wsi']}")
        print("=" * 80)
    
    return {
        "hubmap_id": hubmap_id,
        "download_location": base_folder,
        "stats": all_stats,
        "datasets": fetch_result.get('datasets', [])
    }

def convert_ome_tiff_to_rgb_compressed(input_path):
    
    input_path = Path(input_path)

    output_filename = input_path.name.replace('.ome.tif', '_rgb_compressed.tif')
    output_path = input_path.with_name(output_filename)

    #print(f"Converting '{input_path.name}'...")
    
    with tifffile.TiffFile(input_path) as tif:
        if len(tif.pages) < 3:
            raise ValueError(f"Input file '{input_path}' has fewer than 3 pages.")
        
        r = tif.pages[0].asarray()
        g = tif.pages[1].asarray()
        b = tif.pages[2].asarray()
        rgb = np.stack([r, g, b], axis=-1)

    # If rgb is greater than 2 gigabytes use bigtiff
    use_bigtiff = rgb.nbytes > 2 * (1024**3)

    #print(f"Writing to '{output_path.name}' (BigTIFF: {use_bigtiff})")
    
    tifffile.imwrite(
        output_path,  
        rgb,
        photometric='rgb',
        compression='zlib',
        bigtiff=use_bigtiff
    )
    print(f"\nSuccess! Converted file saved at: {output_path}")

    return output_path

def optimize_workspace_wsi(source, delete_originals=False):
    """
    Optimize image files by converting multi-frame images to RGB compressed format.
    
    Args:
        source (str): Can be:
                     - HubMAP ID (looks in datasets/{hubmap_id})
                     - File path (single image file)
                     - Folder path (processes all images in folder)
    
    Returns:
        dict: Optimization results and statistics.
    """
    source_path = Path(_resolve_local_path(source, must_exist=False))
    
    results = {
        "source": source,
        "total_files": 0,
        "optimized": 0,
        "skipped": 0,
        "failed": 0,
        "deleted": 0,
        "files_without_rgb": [],
        "optimized_files": [],
        "failed_files": [],
        "deleted_files":[]
    }
    
    image_files = []
    files_to_delete = []
    
    # Determine source type and collect files
    if source_path.exists() and source_path.is_file():
        # Source is a file path
        #print(f"Processing single file: {source_path}")
        image_files = [source_path]
        
    elif source_path.exists() and source_path.is_dir():
        # Source is a folder path
        #print(f"Processing folder: {source_path}")
        # Find all image files (common extensions)
        image_extensions = ['*.tif', '*.tiff', '*.ome.tif', '*.ome.tiff']
        for ext in image_extensions:
            image_files.extend(source_path.glob(f"**/{ext}"))
        
        # Remove duplicates
        image_files = list(set(image_files))
        
        if not image_files:
            print(f"No image files found in '{source_path}' to optimise.")
            return {"error": "No image files found", "path": str(source_path)}
            
    else:
        # Assume it's a hubmap_id
        base_path = Path(_resolve_local_path(Path("datasets") / str(source), must_exist=False))
        
        if not base_path.exists():
            print(f"Error: Directory 'datasets/{source}' does not exist and '{source}' is not a valid file/folder path.")
            print("Please provide a valid HubMAP ID, file path, or folder path.")
            return {"error": "Invalid source", "path": str(base_path)}
        
        #print(f"Processing HubMAP ID: {source}")
        # Find all ome.tif/ome.tiff files
        image_files = list(base_path.glob("**/*.ome.tif")) + list(base_path.glob("**/*.ome.tiff"))
        
        if not image_files:
            print(f"No OME-TIFF files found in 'datasets/{source}'.")
            return {"error": "No OME-TIFF files found", "path": str(base_path)}
        
        results["hubmap_id"] = source
    
    results["total_files"] = len(image_files)
    print(f"Found {len(image_files)} image file(s) to process.")
    print("=" * 80)
    
    for image_file in image_files:
        print(f"\n optimising: {image_file.name}")
        
        # Check if file has RGB frames (at least 3 pages)
        try:
            with tifffile.TiffFile(image_file) as tif:
                num_pages = len(tif.pages)
                
                if num_pages < 3:
                    print(f"Skipping: File does not have RGB frames (only {num_pages} page(s))")
                    results['skipped'] += 1
                    results['files_without_rgb'].append(str(image_file))
                    continue
            
            # Convert to optimized format
            try:
                output_path = convert_ome_tiff_to_rgb_compressed(image_file)
                results['optimized'] += 1
                results['optimized_files'].append(str(output_path))
                files_to_delete.append(image_file)
                print(f"Successfully optimized")
            except Exception as e:
                print(f"Failed to optimize: {e}")
                results['failed'] += 1
                results['failed_files'].append(str(image_file))
                
        except Exception as e:
            print(f"Failed to read file: {e}")
            results['failed'] += 1
            results['failed_files'].append(str(image_file))

    #delete original files
    if delete_originals and files_to_delete:
        #print("\n" + "=" * 80)
        #print("DELETING ORIGINAL FILES")
        #print("=" * 80)
        
        for file_to_delete in files_to_delete:
            try:
                os.remove(file_to_delete)
                #print(f"Deleted: {file_to_delete.name}")
                results['deleted'] += 1
                results['deleted_files'].append(str(file_to_delete))
            except Exception as e:
                print(f"Failed to delete {file_to_delete.name}: {e}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("OPTIMIZATION SUMMARY")
    print("=" * 80)
    print(f"Total image files:           {results['total_files']}")
    print(f"Successfully optimized:      {results['optimized']}")
    print(f"Skipped (no RGB frames):     {results['skipped']}")
    print(f"Failed:                      {results['failed']}")
    #if delete_originals:
     #   print(f"Original files deleted:      {results['deleted']}")
    print("=" * 80)
    
    if results['files_without_rgb']:
        print("\nFiles without RGB frames:")
        for file in results['files_without_rgb']:
            print(f"  - {Path(file).name}")
    
    return results

def create_or_get_folder_from_fusion_backend(gc, user_name, hubmap_id=None, file_name=None, folder_name=None):
    """
    Create or get an Athena folder for uploading files.
    
    Args:
        gc: Girder client instance.
        user_name (str): Athena username.
        hubmap_id (str): HubMAP ID (folder will be named after this).
        file_name (str): Single file name (folder will be upload_{file_name}).
        folder_name (str): Custom folder name for multiple files.
    
    Returns:
        str: folder_id to use for uploads.
    """
    # Search for user
    print(f"Searching for username: {user_name}")
    users = gc.get('/user', parameters={'text': user_name})
    
    if not users:
        raise ValueError(f"No user found matching the provided search criteria")
    
    user_id = users[0]['_id']
    user_info = users[0]
    print(f"Using user: {user_info.get('login', 'N/A')} (ID: {user_id})")
    
    # Determine folder name
    if hubmap_id:
        target_folder_name = hubmap_id
    elif file_name:
        target_folder_name = f"upload_{file_name.split('.')[0]}"
    elif folder_name:
        target_folder_name = folder_name
    else:
        # Ask user for folder name
        target_folder_name = input("Please enter a folder name: ").strip()
        if not target_folder_name:
            raise ValueError("Folder name cannot be empty")
    
    # Check if folder already exists
    existing_folders = gc.get('/folder', parameters={
        'parentType': 'user',
        'parentId': user_id
    })
    
    existing_folder = None
    for folder in existing_folders:
        if folder.get('name') == target_folder_name:
            existing_folder = folder
            break
    
    if existing_folder:
        folder_id = existing_folder['_id']
        print(f"\nFolder '{target_folder_name}' already exists (ID: {folder_id})")
        
        # Get contents of the folder
        items = gc.get('/item', parameters={'folderId': folder_id})
        
        if items:
            print(f"\nCurrent contents ({len(items)} item(s)):")
            for idx, item in enumerate(items[:10], 1):  # Show first 10 items
                print(f"  {idx}. {item.get('name')}")
            if len(items) > 10:
                print(f"  ... and {len(items) - 10} more items")
        else:
            print("\nFolder is currently empty.")
        
        # Ask user what to do
        print("\nWhat would you like to do?")
        print("1. Delete existing folder and create new one")
        print("2. Add to existing folder")
        print("3. Create a new folder with a different name")
        print("4. Use the existing folder")
        print("5. Exit (cancel operation)")
        
        choice = input("Enter your choice (1/2/3/4/5): ").strip()
        
        if choice == '1':
            # Delete existing folder
            print(f"Deleting folder '{target_folder_name}'...")
            gc.delete(f'/folder/{folder_id}')
            print("Folder deleted.")
            
            # Create new folder
            print(f"Creating new folder: {target_folder_name}")
            folder = gc.post('/folder', parameters={
                'parentType': 'user',
                'parentId': user_id,
                'name': target_folder_name,
                'public': True
            })
            folder_id = folder['_id']
            print(f"Folder created successfully!")
            print(f"  Name: {target_folder_name}")
            print(f"  ID: {folder_id}")
            
        elif choice == '2':
            # Use existing folder
            print(f"Using existing folder: {target_folder_name} (ID: {folder_id})")
            
        elif choice == '3':
            # Create new folder with custom name
            while True:
                new_folder_name = input("Enter new folder name: ").strip()
                if not new_folder_name:
                    print("Folder name cannot be empty. Please try again.")
                    continue
                
                # Check if this name already exists
                existing_folders = gc.get('/folder', parameters={
                    'parentType': 'user',
                    'parentId': user_id
                })
                
                name_exists = False
                for folder in existing_folders:
                    if folder.get('name') == new_folder_name:
                        name_exists = True
                        break
                
                if name_exists:
                    print(f"Folder '{new_folder_name}' already exists. Please enter a different name.")
                    continue
                else:
                    # Name is unique, create the folder
                    break
            
            print(f"Creating folder: {new_folder_name}")
            folder = gc.post('/folder', parameters={
                'parentType': 'user',
                'parentId': user_id,
                'name': new_folder_name,
                'public': True
            })
            folder_id = folder['_id']
            print(f"Folder created successfully!")
            print(f"  Name: {new_folder_name}")
            print(f"  ID: {folder_id}")
            
        elif choice == '4':
            # Use existing folder
            print(f"Using existing folder: {target_folder_name} (ID: {folder_id})")
            return folder_id, False
            
        elif choice == '5':
            # Exit - cancel the operation
            print("Operation cancelled by user.")
            return None, False
            
        else:
            raise ValueError("Invalid choice. Please enter 1, 2, or 3")
    
    else:
        # Folder doesn't exist, create new one
        print(f"Creating folder: {target_folder_name}")
        folder = gc.post('/folder', parameters={
            'parentType': 'user',
            'parentId': user_id,
            'name': target_folder_name,
            'public': True
        })
        folder_id = folder['_id']
        print(f"Folder created successfully!")
        print(f"  Name: {target_folder_name}")
        print(f"  ID: {folder_id}")
    
    return folder_id, True

def upload_to_fusion_backend_helper(gc, file_source, folder_id):
    """
    Upload a file to Athena from local path.
    
    Args:
        gc: Girder client instance.
        file_source (str): Local file path to upload.
        folder_id (str): Athena folder ID to upload to.
    
    Returns:
        tuple: (item_id, folder_id) for the uploaded file.
    """
    
    if not os.path.exists(file_source):
        raise FileNotFoundError(f"File not found: {file_source}")
    
    filename = os.path.basename(file_source)
    file_size = os.path.getsize(file_source)
    
    # Check if the file exists
    item_id = file_exists(gc, folder_id, filename)
    if item_id:
        print(f"File '{filename}' : id {item_id} already exists in folder. Skipping upload.")
        return item_id, folder_id
    try:
        # Initialize upload
        r = gc.post('/file', parameters={
            'parentType': 'folder',
            'parentId': folder_id,
            'name': filename,
            'size': file_size
        })
        upload_id = r['_id']
        print(f"Upload ID: {upload_id}")
        
        # Upload in chunks
        chunk_size = 64 * 1024 * 1024  
        offset = 0
        with open(file_source, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                print(f"Uploading ({offset/file_size*100:.1f}%)")
                r1 = gc.post('/file/chunk', parameters={
                    'uploadId': upload_id,
                    'offset': offset
                }, data=chunk)
                offset += len(chunk)
        
        item_id = r1["itemId"]
        print(f"Uploading (100%)")
        print("Upload completed successfully!")
        print("item_id, folder_id :", item_id, folder_id)
        return item_id, folder_id
    except Exception as e:
        print(f"Error during upload: {e}")
        raise

def file_exists(gc, folder_id, filename):
    """Check if file already exists in folder."""
    items = gc.get('/item', parameters={'folderId': folder_id})
    for item in items:
        if item.get('name') == filename:
            return item.get('_id')
    return None

def upload_to_fusion_backend(gc, hubmap_id=None, user=None, file_path=None, file_paths=None, is_visium=True, is_xenium=False,
                       dir_path=None, all=False, histology=False, visium=False, temp_download=False, is_analysis_result=False,
                       folder_id=None, fusion_dsa_instance_url='https://fusionpub.rc.ufl.edu//api/v1'):
    """
    Upload the files to Athena from HubMAP or local files.

    Args:
        gc: Girder client instance.
        hubmap_id (str): HubMAP ID to download from HubMAP.
        user (str): Athena username for creating/finding folders.
        file_path (str): Single local file path to upload.
        file_paths (list): List of local file paths to upload.
        dir_path (str): Local directory path; all files in the directory will be uploaded.
        all (bool): If True, download all files from HubMAP.
        histology (bool): If True, download only OME-TIFF files from HubMAP.
        visium (bool): If True, download only h5ad files from HubMAP.
        temp_download (bool): If True, download to temp folder and clean up after upload.
        folder_id (str): Existing Athena folder ID to upload to.
        fusion_dsa_instance_url (str): Athena API base URL.

    Returns:
        dict: Upload results with item IDs and statistics.
    """
    results = {
        "uploaded_files": [],
        "failed_files": [],
        "total_uploaded": 0,
        "total_failed": 0,
        "tif_item_id" : None
    }
    
    tif_item_id = None
    if hubmap_id:
        temp_download = True
    if file_path:
            file_path = _resolve_local_path(file_path, must_exist=True)
    
    if file_paths:
            file_paths = [
                _resolve_local_path(path, must_exist=True)
                for path in file_paths
            ]
    
    if dir_path:
            dir_path = _resolve_local_path(dir_path, must_exist=True)
        
    temp_folder = None
    should_upload = True
    
    # Create or get folder if not provided
    if not folder_id:
        if hubmap_id:
            folder_id, should_upload = create_or_get_folder_from_fusion_backend(gc, user, hubmap_id=hubmap_id)
        elif file_path:
            folder_id, should_upload = create_or_get_folder_from_fusion_backend(gc, user, file_name=os.path.basename(file_path))
        elif file_paths:
            folder_id, should_upload = create_or_get_folder_from_fusion_backend(gc, user)
        elif dir_path:
            folder_id, should_upload = create_or_get_folder_from_fusion_backend(gc, user, folder_name=os.path.basename(os.path.normpath(dir_path)))
        else:
            print("Error: Must provide either hubmap_id, file_path, file_paths, or dir_path")
            return {"error": "No input provided"}
    else:
        print(f"Using folder_id: {folder_id}")
    
    # Check if user cancelled
    if folder_id is None:
        print("Upload cancelled by user.")
        return {"error": "Operation cancelled, no folder_id available."}
    
    # If user chose to use existing folder without uploading, return early
    if not should_upload:
        existing_tif_item_id = None

        if is_analysis_result:
            items = gc.get('/item', parameters={'folderId': folder_id})
    
            for item in items:
                item_details = gc.get(f'/item/{item["_id"]}')
                if 'largeImage' in item_details:
                    existing_tif_item_id = item['_id']
                    break
                
        return {
            "folder_id": folder_id,
            "uploaded_files": [],
            "failed_files": [],
            "total_uploaded": 0,
            "total_failed": 0,
            "tif_item_id": existing_tif_item_id
        }
    
    # 1: Upload from HubMAP ID
    if hubmap_id:
        print(f"\nProcessing HubMAP ID: {hubmap_id}")
        
        # Download to local workspace first
        download_result = HuBMAP_to_workspace_download(
            hubmap_id=hubmap_id,
            all=all,
            histology=histology,
            visium=visium,
            temp_download=temp_download,
            Optimize_WSI=True,
            delete_originals=True
        )
        
        if 'error' in download_result:
            print(f"Error downloading from HubMAP: {download_result['error']}")
            return download_result
        
        # Get the local folder path
        local_folder = Path.cwd() / download_result['download_location']
        temp_folder = local_folder if temp_download else None
        
        # Get all downloaded files
        files_to_upload = []
        for root,dirs,files in os.walk(local_folder):
            for file in files: 
                files_to_upload.append(os.path.join(root, file))
        
        #print(f"\n{'='*80}")
        #print(f"UPLOADING TO ATHENA")
        #print(f"{'='*80}")

       
        # Upload each file
        for file_source in files_to_upload:
            try:
                item_id, folder_id_used = upload_to_fusion_backend_helper(
                    gc=gc,
                    file_source=file_source,
                    folder_id=folder_id
                )
                results['uploaded_files'].append({
                    'file': os.path.basename(file_source),
                    'item_id': item_id,
                    'folder_id': folder_id_used
                })
                results['total_uploaded'] += 1
            except Exception as e:
                print(f"Failed to upload {file_source} to Fusion backend: {e}")
                results['failed_files'].append({
                    'file': os.path.basename(file_source),
                    'error': str(e)
                })
                results['total_failed'] += 1
    
    # 2: Upload single file path
    elif file_path:
        print(f"\n{'='*80}")
        #print(f"UPLOADING TO ATHENA")
        print(f"{'='*80}")
        print(f"Uploading single file: {file_path}")
        
        try:
            item_id, folder_id_used = upload_to_fusion_backend_helper(
                gc=gc,
                file_source=file_path,
                folder_id=folder_id
            )
            results['uploaded_files'].append({
                'file': os.path.basename(file_path),
                'item_id': item_id,
                'folder_id': folder_id_used
            })
            results['total_uploaded'] += 1
        except Exception as e:
            print(f"Failed to upload {file_path} to Fusion backend: {e}")
            results['failed_files'].append({
                'file': os.path.basename(file_path),
                'error': str(e)
            })
            results['total_failed'] += 1
    
    # 3: Upload list of file paths
    elif file_paths:
        print(f"\n{'='*80}")
        #print(f"UPLOADING TO ATHENA")
        print(f"{'='*80}")
        print(f"Uploading {len(file_paths)} file(s)")
        
        for file_source in file_paths:
            try:
                item_id, folder_id_used = upload_to_fusion_backend_helper(
                    gc=gc,
                    file_source=file_source,
                    folder_id=folder_id
                )
                results['uploaded_files'].append({
                    'file': os.path.basename(file_source),
                    'item_id': item_id,
                    'folder_id': folder_id_used
                })
                results['total_uploaded'] += 1
            except Exception as e:
                print(f"Failed to upload {file_source} to Fusion backend: {e}")
                results['failed_files'].append({
                    'file': os.path.basename(file_source),
                    'error': str(e)
                })
                results['total_failed'] += 1
    
    # 4: Upload all files from a local directory
    elif dir_path:
        if not os.path.isdir(dir_path):
            print(f"Error: '{dir_path}' is not a valid directory")
            return {"error": f"Invalid directory: {dir_path}"}
        

        if is_analysis_result and is_visium:
            # ----------------------------------------------------------------
            # Step 1: Find and upload the .tif file
            # ----------------------------------------------------------------
            image_folder = None
            for candidate in ["ometiff-pyramids", "image", "images"]:
                candidate_path = os.path.join(dir_path, candidate)
                if os.path.isdir(candidate_path):
                    image_folder = candidate_path
                    break

            if image_folder is None:
                print("Error: No image folder (ometiff-pyramids/image/images) found in directory.")
                return {"error": "No image folder found in directory."}

            if image_folder is None:
                print("Error: No image folder (ometiff-pyramids/image/images) found in directory.")
                return {"error": "No image folder found in directory."}

            tif_files = [f for f in os.listdir(image_folder) if f.lower().endswith((".tif", ".tiff", ".svs", ".ndpi"))]
            if not tif_files:
                print("Error: image folder should have one .tif/.tiff/.svs file")
                return {"error": "image folder should have one .tif/.tiff/.svs file"}

            if len(tif_files) == 1:
                tif_to_upload = os.path.join(image_folder, tif_files[0])
            else:
                rgb_compressed = [f for f in tif_files if f.endswith("_rgb_compressed.tif")]
                if not rgb_compressed:
                    print("Error: image folder should have one .tif file")
                    return {"error": "image folder should have one .tif file"}
                tif_to_upload = os.path.join(image_folder, rgb_compressed[0])

            print(f"\nUploading TIF: {tif_to_upload}")
            try:
                tif_item_id, folder_id_used = upload_to_fusion_backend_helper(
                    gc=gc,
                    file_source=tif_to_upload,
                    folder_id=folder_id
                )
                results['uploaded_files'].append({
                    'file': os.path.basename(tif_to_upload),
                    'item_id': tif_item_id,
                    'folder_id': folder_id_used
                })
                results['total_uploaded'] += 1
            except Exception as e:
                print(f"Failed to upload TIF {tif_to_upload}: {e}")
                results['failed_files'].append({
                    'file': os.path.basename(tif_to_upload),
                    'error': str(e)
                })
                results['total_failed'] += 1
                return {"error": f"TIF upload failed, cannot proceed: {e}"}

            # ----------------------------------------------------------------
            # Step 2: Upload all .h5ad files in the root of dir_path
            # ----------------------------------------------------------------
            h5ad_files = [
                os.path.join(dir_path, f)
                for f in os.listdir(dir_path)
                if f.endswith(".h5ad") and os.path.isfile(os.path.join(dir_path, f))
            ]
            print(f"\nUploading {len(h5ad_files)} .h5ad file(s)")
            for h5ad_file in h5ad_files:
                try:
                    item_id, folder_id_used = upload_to_fusion_backend_helper(
                        gc=gc,
                        file_source=h5ad_file,
                        folder_id=folder_id
                    )
                    results['uploaded_files'].append({
                        'file': os.path.basename(h5ad_file),
                        'item_id': item_id,
                        'folder_id': folder_id_used
                    })
                    results['total_uploaded'] += 1
                except Exception as e:
                    print(f"Failed to upload {h5ad_file}: {e}")
                    results['failed_files'].append({
                        'file': os.path.basename(h5ad_file),
                        'error': str(e)
                    })
                    results['total_failed'] += 1

            # ----------------------------------------------------------------
            # Step 3: Upload Segmented_FTU .json files as annotation
            # ----------------------------------------------------------------
            segmented_ftu_path = os.path.join(dir_path, "Segmented_FTU")
            if os.path.isdir(segmented_ftu_path):
                seg_json_files = [
                    os.path.join(segmented_ftu_path, f)
                    for f in os.listdir(segmented_ftu_path)
                    if f.endswith(".json")
                ]
                print(f"\nUploading {len(seg_json_files)} Segmented_FTU annotation(s)")
                for json_file in seg_json_files:
                    try:
                        with open(json_file, "r") as f:
                            annotation_content = json.load(f)

                        gc.post(
                            f"/annotation/item/{tif_item_id}",
                            json=annotation_content
                        )
                        
                        results['uploaded_files'].append({
                            'file': os.path.basename(json_file),
                            'item_id': tif_item_id,
                            'folder_id': folder_id
                        })
                        results['total_uploaded'] += 1
                    except Exception as e:
                        print(f"Failed to upload annotation {json_file}: {e}")
                        results['failed_files'].append({
                            'file': os.path.basename(json_file),
                            'error': str(e)
                        })
                        results['total_failed'] += 1

            # ----------------------------------------------------------------
            # Step 4: Upload Spots.json from files/ as annotation
            # ----------------------------------------------------------------
            files_folder_path = os.path.join(dir_path, "files" if os.path.isdir(os.path.join(dir_path, "files")) else "Files")
            if os.path.isdir(files_folder_path):
                spots_json = os.path.join(files_folder_path, "Spots.json")
                if os.path.isfile(spots_json):
                    print(f"\nUploading Spots.json as annotation")
                    try:
                        with open(spots_json, "r") as f:
                            annotation_content = json.load(f)

                        gc.post(
                            f"/annotation/item/{tif_item_id}",
                            json=annotation_content
                        )
                        
                        results['uploaded_files'].append({
                            'file': 'Spots.json',
                            'item_id': tif_item_id,
                            'folder_id': folder_id
                        })
                        results['total_uploaded'] += 1
                    except Exception as e:
                        print(f"Failed to upload Spots.json: {e}")
                        results['failed_files'].append({
                            'file': 'Spots.json',
                            'error': str(e)
                        })
                        results['total_failed'] += 1

            # ----------------------------------------------------------------
            # Step 5: Upload Aggregated_FTU .json files as annotations
            # ----------------------------------------------------------------
            aggregated_ftu_path = os.path.join(dir_path, "Aggregated_FTU")
            if os.path.isdir(aggregated_ftu_path):
                agg_json_files = [
                    os.path.join(aggregated_ftu_path, f)
                    for f in os.listdir(aggregated_ftu_path)
                    if f.endswith(".json")
                ]
                print(f"\nUploading {len(agg_json_files)} Aggregated_FTU annotation(s)")
                for json_file in agg_json_files:
                    try:
                        with open(json_file, "r") as f:
                            annotation_content = json.load(f)

                        gc.post(
                            f"/annotation/item/{tif_item_id}",
                            json=annotation_content
                        )
                        
                        results['uploaded_files'].append({
                            'file': os.path.basename(json_file),
                            'item_id': tif_item_id,
                            'folder_id': folder_id
                        })
                        results['total_uploaded'] += 1
                    except Exception as e:
                        print(f"Failed to upload annotation {json_file}: {e}")
                        results['failed_files'].append({
                            'file': os.path.basename(json_file),
                            'error': str(e)
                        })
                        results['total_failed'] += 1
                        
            # ----------------------------------------------------------------
            # Step 6: Upload metadata.json to item metadata
            # ----------------------------------------------------------------
            if os.path.isdir(files_folder_path):
                metadata_json = os.path.join(files_folder_path, "metadata.json")
                if os.path.isfile(metadata_json):
                    print(f"\nUploading metadata.json to item metadata")
                    try:
                        with open(metadata_json, "r") as f:
                            metadata_content = json.load(f)
                        gc.put(f"/item/{tif_item_id}/metadata", json=metadata_content)
                        results['uploaded_files'].append({
                            'file': 'metadata.json',
                            'item_id': tif_item_id,
                            'folder_id': folder_id
                        })
                        results['total_uploaded'] += 1
                    except Exception as e:
                        print(f"Failed to upload metadata.json: {e}")
                        results['failed_files'].append({
                            'file': 'metadata.json',
                            'error': str(e)
                        })
                        results['total_failed'] += 1

            # ----------------------------------------------------------------
            # Step 7: Upload remaining files in files/ folder via /file API
            # ----------------------------------------------------------------
            if os.path.isdir(files_folder_path):
                excluded_files = {"Spots.json", "metadata.json"}
                remaining_files = [
                    os.path.join(files_folder_path, f)
                    for f in os.listdir(files_folder_path)
                    if f not in excluded_files and os.path.isfile(os.path.join(files_folder_path, f))
                ]
                print(f"\nUploading {len(remaining_files)} remaining file(s) from files/ folder")
                for file_path in remaining_files:
                    file_name = os.path.basename(file_path)
                    file_size = os.path.getsize(file_path)
                    mime_type, _ = mimetypes.guess_type(file_path)
                    mime_type = mime_type or "application/octet-stream"
                    try:
                        # Initialize the upload
                        upload_response = gc.post(
                            "/file",
                            parameters={
                                "parentType": "item",
                                "parentId": tif_item_id,
                                "name": file_name,
                                "size": file_size,
                                "mimeType": mime_type
                            }
                        )
                        upload_id = upload_response["_id"]

                        # Send the file content
                        with open(file_path, "rb") as f:
                            gc.post(
                                f"/file/chunk",
                                parameters={"uploadId": upload_id, "offset": 0},
                                data=f.read()
                            )
                        results['uploaded_files'].append({
                            'file': file_name,
                            'item_id': tif_item_id,
                            'folder_id': folder_id
                        })
                        results['total_uploaded'] += 1
                    except Exception as e:
                        print(f"Failed to upload {file_name}: {e}")
                        results['failed_files'].append({
                            'file': file_name,
                            'error': str(e)
                        })
                        results['total_failed'] += 1

        else:

            files_to_upload = [
                os.path.join(root, f)
                for root, _, files in os.walk(dir_path)
                for f in files
            ]
            print(f"\nUploading {len(files_to_upload)} file(s) from directory: {dir_path}")

            for file_source in files_to_upload:
                try:
                    item_id, folder_id_used = upload_to_fusion_backend_helper(
                        gc=gc,
                        file_source=file_source,
                        folder_id=folder_id
                    )
                    results['uploaded_files'].append({
                        'file': os.path.basename(file_source),
                        'item_id': item_id,
                        'folder_id': folder_id_used
                    })
                    results['total_uploaded'] += 1
                except Exception as e:
                    print(f"Failed to upload {file_source} to Fusion backend: {e}")
                    results['failed_files'].append({
                        'file': os.path.basename(file_source),
                        'error': str(e)
                    })
                    results['total_failed'] += 1

    # Clean up temporary folder if needed
    if temp_folder and temp_folder.exists():
        #print(f"\n{'='*80}")
        #print(f"CLEANING UP TEMPORARY FILES")
        #print(f"{'='*80}")
        try:
            shutil.rmtree(temp_folder)
            #print(f"Removed temporary folder: {temp_folder}")
        except Exception as e:
            print(f"Failed to remove temporary folder: {e}")
    '''
    # Print summary
    print(f"\n{'='*80}")
    print("ATHENA UPLOAD SUMMARY")
    print(f"{'='*80}")
    print(f"Total uploaded:  {results['total_uploaded']}")
    print(f"Total failed:    {results['total_failed']}")
    print(f"{'='*80}")'''
    
    if tif_item_id:
        results["tif_item_id"] = tif_item_id
    
    return results

def download_reference_files_from_fusion(gc):
    assets = {
        "References": [
            "6989338b7d7fb0fd9933755c",
            "697baf5f13bbccd3003a6435",
        ]
    }
    for output_dir, file_ids in assets.items():
        output_dir = _resolve_local_path(output_dir, must_exist=False)
        print(f"\nDownloading {output_dir} to {output_dir}/")
        os.makedirs(output_dir, exist_ok=True)
        for file_id in file_ids:
            file_info = gc.get(f"/file/{file_id}")
            out_path = os.path.join(output_dir, file_info["name"])
            print(f"  - {file_info['name']} ({file_info['size']} bytes)...")
            gc.downloadFile(file_id, path=out_path)
        print(f"  Done.")

def download_model_files_from_fusion(gc):
    assets = {
        "Models": [
            "6967ef12413ffaf54798bc91",
            "6967ee7b413ffaf54798bc8e",
        ]
    }
    for output_dir, file_ids in assets.items():
        output_dir = _resolve_local_path(output_dir, must_exist=False)
        print(f"\nDownloading {output_dir} to {output_dir}/")
        os.makedirs(output_dir, exist_ok=True)
        for file_id in file_ids:
            file_info = gc.get(f"/file/{file_id}")
            out_path = os.path.join(output_dir, file_info["name"])
            print(f"  - {file_info['name']} ({file_info['size']} bytes)...")
            gc.downloadFile(file_id, path=out_path)
        print(f"  Done.")

def download_file_from_fusion(gc, file_id, output_dir):
    output_dir = _resolve_local_path(output_dir, must_exist=False)
    os.makedirs(output_dir, exist_ok=True)
    file_info = gc.get(f"/file/{file_id}")
    out_path = os.path.join(output_dir, file_info["name"])
    print(f"Downloading {file_info['name']} ({file_info['size']} bytes)...")
    gc.downloadFile(file_id, path=out_path)
    print(f"Saved to {out_path}")

KEY_COLS = 'data.contrast nuclei,data.contrast eosinophilic,data.condition,item.name,item.id,annotation.id,annotationelement.id'
#----------------------------------------------------------------------------------------------------------------------------------------------------------------
# Fetch data from Athena
def get_patient_id(gc, path):
    r = gc.get("resource/lookup", parameters={'path': path})
    return r['meta']['Patient']

def plot_json_to_df(json_obj):
    sorted_cols = sorted(json_obj['columns'], key=lambda x: x['index'])
    col_names = [col['title'] for col in sorted_cols]
    return pd.DataFrame(json_obj['data'], columns=col_names)

def get_available_annotations(gc, path):
    r = gc.get("resource/lookup", parameters={'path': path})
    uuid = r['_id']
    annotations = gc.get(f"annotation", parameters={'itemId': uuid})
    print(f"Found {len(annotations)} annotation(s) for item: {r['name']}")
    print("-" * 80)
    annotation_dict = {}
    for i, ann in enumerate(annotations, 1):
        annotation_name = ann.get('annotation', {}).get('name', 'Unnamed')
        annotation_id = ann['_id']
        element_count = ann.get('_elementCount', 0)
        print(f"{i}. Name: '{annotation_name}' | ID: {annotation_id} | Elements: {element_count}")
        annotation_dict[annotation_name] = annotation_id
    print("-" * 80)
    return annotation_dict

def get_available_columns(gc, path, annotation_name):
    r = gc.get("resource/lookup", parameters={'path': path})
    uuid = r['_id']
    annotations = gc.get("annotation", parameters={'itemId': uuid})
    matching_annotations = [
        ann
        for ann in annotations
        if ann.get('annotation', {}).get('name') == annotation_name
    ]

    columns = list(matching_annotations[0].keys()) if matching_annotations else []
    print("-" * 80)
    print(f"Available columns for annotation '{annotation_name}' ({len(columns)}):")
    for i, col in enumerate(columns, 1):
        print(f"{i}. {col}")
    print("-" * 80)
    return columns

def get_annotation_data(gc, path, annotation_name, columns=KEY_COLS):
    
    r = gc.get("resource/lookup", parameters={'path': path})
    uuid = r['_id']
    annotations = gc.get(f"annotation", parameters={'itemId': uuid})
    annotation_ids = [
        ann['_id']
        for ann in annotations
        if ann.get('annotation', {}).get('name') == annotation_name
    ]
    print(f"Found {len(annotation_ids)} annotation(s) with name '{annotation_name}': {annotation_ids}")
    if annotation_ids:
        # Use the list of IDs in your plot data request
        data = gc.post(f"annotation/item/{uuid}/plot/data", parameters={
            'adjacentItems': 'true',
            'keys': ','.join(columns),
            'annotations': json.dumps(annotation_ids)
        })
        df = plot_json_to_df(data)
    else:
        print(f"No annotations found with name: {annotation_name}")
        df = None
    
    return df
