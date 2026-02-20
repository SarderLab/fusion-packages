from fusion.utilities.utility import download_to_fusion_backend
import tifffile
import numpy as np
    
def run_analysis_tasks_fusion_backend(gc, user_name, hubmap_id=None, file_path=None, file_paths=None):
    """
    Run multi-compartment segmentation on image(s) from HubMAP or local file(s).
    
    Args:
        gc: Girder client instance.
        user_name (str): Athena username.
        hubmap_id (str): HubMAP ID to process.
        file_path (str): Local file path to process.
        file_paths (list): List of local file paths to process.
    
    Returns:
        dict: Job information including job_ids and file details.
    """
    if sum([bool(hubmap_id), bool(file_path), bool(file_paths)]) > 1:
        raise ValueError("Please provide only one of: hubmap_id, file_path, or file_paths.")
    
    if not hubmap_id and not file_path and not file_paths:
        raise ValueError("Please provide either hubmap_id, file_path, or file_paths.")

    # Prompt user to select job type
    print(f"\n{'='*80}")
    print("Select the segmentation analysis to run:")
    print("1. Multi-Compartment Segmentation")
    print("2. Frozen Glomerulus Segmentation")
    print("3. Feature Extraction")
    print(f"{'='*80}")
    
    job_choice = input("Enter your choice (1/2/3): ").strip()
    
    # Define job configurations
    job_configs = {
        '1': {
            'name': 'Multi-Compartment Segmentation',
            'path': ["sarderlab/fusion1", "Multic", "MultiCompartmentSegment"],
            'input_param': 'input_file',
            'params': {
                'modelfile': '6967ee7b413ffaf54798bc8e'
            }
        },
        '2': {
            'name': 'Frozen Glomerulus Segmentation',
            'path': ["tatkeanish/fusion_v1", "frozen_glom_segmentation", "GlomSeg"],
            'input_param': 'input_file',
            'params': {
                'modelfile': '6967ef12413ffaf54798bc91',
                'num_classes': 2,
                'threshold': 0.5,
                'batch_size': 8,
                'region_size': 512,
                'step_size': 256,
                'num_workers': 4
            }
        },
        '3': {
        'name': 'Feature Extraction',
        'path': ["sarderlab/fusion1", "PathomicFeatureExtraction", "PathomicsFE"], 
        'input_param': 'input_image',
        'params': {
                'type': 'Feature_Pipeline',  
                'threshold_nuclei': 200,
                'minsize_nuclei': 20,
                'threshold_PAS': 50,
                'minsize_PAS': 20,
                'threshold_LS': 0,
                'minsize_LS': 0,
                'ignoreAnns': '',
                'rename': True,
                'replace_annotations': True,
                'returnXlsx': False
        }
    },
        '4': {
        'name': 'Label Transfer (10X Visium - step 1)',
        'path': ["sarderlab/fusion_v1", "10X_VisiumAnalysis", "LabelTransfer"],
        'input_param': 'input_image',
        'params': {
            'organ': 'KPMP Atlas Kidney', 
            'reference': '697baf5f13bbccd3003a6435'
        }
    },
        '5': {
        'name': 'Spot Annotation (10X Visium - step 2)',
        'path': ["sarderlab/fusion_v1", "10X_VisiumAnalysis", "SpotAnnotation"],
        'input_param': 'input_file',
        'params': {
            'cell_reference_file': '69892c0b7d7fb0fd9933751f', 
            'gene_list_file': '',
            'scale_factors': '',
            'spot_coords': ''
    }
    },
        '6': {
        'name': 'STU Spot Aggregation',
        'path': ["sarderlab/fusion_v1", "FTUSpotAggregation", "Aggregate"], 
        'input_param': 'input_image',
        'params': { 
        }
    }
    }
    if job_choice not in job_configs:
        print("Invalid choice. Please enter 1,2 or 3.")
        return {"error": "Invalid job selection"}
    
    selected_job = job_configs[job_choice]
    
    # Upload to Athena using the utility function
    #print("Uploading file(s) to Fusion backend...")
    upload_result = run_analysis_tasks_fusion_backend(
        gc=gc,
        hubmap_id=hubmap_id,
        user=user_name,
        file_path=file_path,
        file_paths=file_paths,
        temp_download=True  # Clean up after upload
    )
    
    if 'error' in upload_result:
        print(f"Error uploading to Fusion backend: {upload_result['error']}")
        return upload_result
    
    if upload_result['total_uploaded'] == 0:
        #print("No files were uploaded.")
        folder_id = upload_result['folder_id']
        if upload_result['total_failed'] > 0:
            return {"error": "Upload failed"}
    else:
        # Get folder_id from first uploaded file
        folder_id = upload_result['uploaded_files'][0]['folder_id']
    
    # Get all items in the folder
    print(f"\nFetching files from folder (ID: {folder_id})...")
    items = gc.get('/item', parameters={'folderId': folder_id})
    
    # Filter for WSI files (items with largeImage)
    wsi_items = []
    for item in items:
        # Check if item has largeImage metadata
        item_details = gc.get(f'item/{item["_id"]}')
        if 'largeImage' in item_details:
            wsi_items.append({
                'name': item['name'],
                'item_id': item['_id'],
                'file_id': item_details['largeImage'].get('fileId')
            })
    
    if not wsi_items:
        print("No WSI files found in the folder.")
        return {"error": "No WSI files found"}
    
    # Display WSI files to user
    print(f"\n{'='*80}")
    print(f"Found {len(wsi_items)} WSI file(s) in folder:")
    print(f"{'='*80}")
    for idx, wsi in enumerate(wsi_items, 1):
        print(f"{idx}. {wsi['name']}")
    
    # Prompt user to select files
    print(f"\n{'='*80}")
    print("Select file(s) to run analysis on:")
    print("  - Enter number(s) separated by commas (e.g., 1,3,5)")
    print("  - Enter 'all' to process all files")
    print(f"{'='*80}")
    
    user_input = input("Your selection: ").strip().lower()
    
    selected_items = []
    if user_input == 'all':
        selected_items = wsi_items
    else:
        try:
            indices = [int(x.strip()) for x in user_input.split(',')]
            for idx in indices:
                if 1 <= idx <= len(wsi_items):
                    selected_items.append(wsi_items[idx - 1])
                else:
                    print(f"Warning: Index {idx} is out of range. Skipping.")
        except ValueError:
            print("Invalid input. Please enter numbers separated by commas or 'all'.")
            return {"error": "Invalid selection"}
    
    if not selected_items:
        print("No valid files selected.")
        return {"error": "No files selected"}
    
    print(f"\n{'='*80}")
    print(f"Running {selected_job['name']} on {len(selected_items)} file(s)...")
    print(f"{'='*80}")
    
    # Run segmentation job on each selected file
    job_results = []
    
    try:
        # Get docker images list to find the run endpoint
        response = gc.get('slicer_cli_web/docker_image')

        # Navigate to the run endpoint
        run_endpoint = response
        for key in selected_job['path']:
            run_endpoint = run_endpoint[key]
        run_endpoint = run_endpoint["run"]
        #print(f"run end point: {run_endpoint}")
        
        for wsi in selected_items:
            print(f"\nSubmitting job for: {wsi['name']}")
            try:
                params = {
                    selected_job['input_param']: wsi['file_id'],
                    'girderApiUrl': 'https://fusionpub.rc.ufl.edu/api/v1', 
                    'girderToken': gc.token  
                }
                # Load defaults from config FIRST
                params.update(selected_job['params'])

                # Handle Option 4 (Label Transfer) Overrides
                if job_choice == '4':
                    counts_id = input(f"Enter Girder ID for counts_file corresponding to {wsi['name']}: ")
                    params['counts_file'] = counts_id

                # Handle Option 6 (STU Spot Aggregation) Overrides
                elif job_choice == '6':
                    print(f"--- Settings for {wsi['name']} ---")
                    base_ann = input("Enter 'base_annotation' layer name (e.g., Spots): ")
                    agg_ann = input("Enter 'agg_annotation' layer names (comma-separated, e.g., glomeruli,tubules): ")
                    
                    params['base_annotation'] = base_ann
                    params['agg_annotation'] = agg_ann
                
                r = gc.post(run_endpoint, parameters=params)
                print(f"Job submitted. job_id={r['_id']}")
                
                job_results.append({
                    'job_id': r['_id'],
                    'file_name': wsi['name'],
                    'file_id': wsi['file_id'],
                    'item_id': wsi['item_id'],
                    'plugin_name': r['_original_name'],
                    'raw': r
                })
            except Exception as e:
                print(f"Failed to submit job: {e}")
                job_results.append({
                    'job_id': None,
                    'job_type': selected_job['name'],
                    'file_name': wsi['name'],
                    'file_id': wsi['file_id'],
                    'item_id': wsi['item_id'],
                    'status': 'failed',
                    'error': str(e)
                })
                
        # Create response summary
        failed_jobs = [j for j in job_results if j.get('status') == 'failed']
        
        response_summary = {
            'job_type': selected_job['name'],
            'total_jobs': len(job_results),
            'failed_jobs': len(failed_jobs),
            'folder_id': folder_id,
            'jobs': job_results
        }
        
        print(f"\n{'='*80}")
        print("SEGMENTATION JOBS SUMMARY")
        print(f"{'='*80}")
        print(f"Job Type: {selected_job['name']}")
        print(f"Total jobs submitted: {len(job_results)}")
        for job in job_results:
            print(f"  - {job['file_name']}: job_id={job['job_id']}")
        print(f"{'='*80}")
        
        return response_summary
        
    except Exception as e:
        print(f"Error submitting job: {e}")
        return {"error": str(e)}