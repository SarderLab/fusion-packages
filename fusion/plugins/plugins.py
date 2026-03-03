from fusion.utilities.utility import download_to_fusion_backend
import tifffile
import numpy as np
import os
import subprocess
import re
import shutil

def check_job_status():
    """
    Asks the user for a Job ID and checks its status.
    """
    # Get Job ID from user
    job_id = input("Enter Job ID to check: ").strip()
    
    if not job_id:
        print("No Job ID entered.")
        return

    print(f"\nChecking status for Job ID: {job_id}...")
    print("-" * 30)

    # --- STEP 1: Check if job is currently active (squeue) ---
    try:
        result = subprocess.run(
            ['squeue', '-j', job_id, '-h', '-o', '%t'], 
            capture_output=True, 
            text=True
        )
        
        state_code = result.stdout.strip()

        if state_code:
            if state_code == 'R':
                print(f"Status: \033[92mRUNNING\033[0m") # Green text
                print("The job is currently processing.")
                return
            elif state_code == 'PD':
                print(f"Status: \033[93mIN QUEUE (PENDING)\033[0m") # Yellow text
                print("Waiting for resources to become available.")
                return
            elif state_code == 'CG':
                print(f"Status: \033[94mCOMPLETING\033[0m") # Blue text
                print("The job is finishing up.")
                return
            else:
                print(f"Status: ACTIVE (State: {state_code})")
                return
                
    except Exception:
        pass

    # --- STEP 2: If not in squeue, check history (sacct) ---
    try:
        result = subprocess.run(
            ['sacct', '-j', job_id, '-n', '-o', 'State'], 
            capture_output=True, 
            text=True
        )
        
        output = result.stdout.strip()
        
        if output:
            if 'COMPLETED' in output:
                print(f"Status: \033[92mSUCCESSFUL\033[0m")
            elif 'FAILED' in output:
                print(f"Status: \033[91mFAILED\033[0m") 
                print("The job terminated with an error.")
            elif 'TIMEOUT' in output:
                print(f"Status: \033[91mFAILED (TIMEOUT)\033[0m")
                print("The job ran out of time.")
            elif 'CANCELLED' in output:
                print(f"Status: \033[91mCANCELLED\033[0m")
                print("The job was stopped manually.")
            else:
                clean_state = output.split()[0]
                print(f"Status: FINISHED ({clean_state})")
        else:
            print("Status: UNKNOWN")
            print("Job ID not found.")
            
    except Exception:
        print("Error! Unable to retrieve job status.")


def get_hive_workspace_root():
    """
    Parses the current working directory to find the Hive workspace root.
    Logic: Looks for '/user-workspaces/', takes the next two segments (username/id),
    and ignores everything after that.
    """
    current_path = os.getcwd()
    parts = current_path.split(os.sep)
    
    # Look for the 'user-workspaces' segment
    if 'user-workspaces' in parts:
        try:
            # Find where 'user-workspaces' is located
            idx = parts.index('user-workspaces')
            
            # We need 'user-workspaces' + username + workspace_id
            # This requires at least 3 segments starting from 'user-workspaces'
            if len(parts) >= idx + 3:
                # Construct the path up to the workspace_id
                # parts[:idx+3] includes everything up to and including the ID
                workspace_root = os.sep.join(parts[:idx+3])
                return workspace_root
        except Exception as e:
            print(f"Error parsing path: {e}")
            
    # Fallback if structure isn't found
    print(f"Warning: 'user-workspaces' structure not found in {current_path}.")
    return current_path

def run_apptainer_analysis():
    """
    Interactive function to generate and submit analysis tasks using Apptainer containers via Slurm.
    """
    # Container image mapping with their specific parameters
    container_configs = {
        "multicompartment_segmentation": {
            "image": "dsrithad/fusion1_decoupled:multicompartment-segmentation-notebook",
            "script": "/opt/MultiC/test_decoupled.py",
            "params": ["input_file", "modelfile", "output_dir"]
        },
        "spatial_aggregation": {
            "image": "dsrithad/spatial-aggregation-notebook:v1",
            "script": "/data/run_aggregation.py",
            "params": []  
        }
    }

    # Display available analysis options
    print("Available Analysis Tasks:")
    print("-" * 30)
    for i, (key, config) in enumerate(container_configs.items(), 1):
        print(f"{i}. {key.replace('_', ' ').title()}")

    # Get user selection for analysis type
    while True:
        try:
            choice = int(input(f"\nSelect analysis task (1-{len(container_configs)}): "))
            if 1 <= choice <= len(container_configs):
                analysis_type = list(container_configs.keys())[choice - 1]
                config = container_configs[analysis_type]
                break
            else:
                print(f"Please enter a number between 1 and {len(container_configs)}")
        except ValueError:
            print("Please enter a valid number")

    print(f"\nSelected: {analysis_type.replace('_', ' ').title()}")
    print(f"Container: {config['image']}")

    # Get parameters specific to this container
    user_params = {}
    if config['params']:
        print(f"\nRequired parameters for {analysis_type.replace('_', ' ').title()}:")
        print("-" * 40)
        for param in config['params']:
            while True:
                if param == 'output_dir':
                    value = input(f"Enter {param} (output directory path): ").strip()
                else:
                    value = input(f"Enter {param} path: ").strip()
                
                if value:
                    user_params[param] = value
                    break
                else:
                    print(f"{param} is required. Please enter a value.")
    else:
        print(f"\nNo additional parameters required for {analysis_type.replace('_', ' ').title()}")

    # Ask for Job Name
    job_name_input = input("\nEnter job name (default: analysis_job): ").strip()
    job_name = job_name_input if job_name_input else "analysis_job"

    # Hardcoded Resources
    time_limit = "01:00:00"
    mem_limit = "8gb"
    
    # DETERMINE WORKSPACE ROOT AUTOMATICALLY
    workspace_path = get_hive_workspace_root()
    mount_point = "/data"
    
    print(f"\nDetected Workspace Root: {workspace_path}")
    print(f"Mounting: {workspace_path} -> {mount_point}")

    apptainer_cmd_parts = [
        "apptainer exec",
        f"-B {workspace_path}:{mount_point}",
        f"docker://{config['image']}",
        f"python {config['script']}"
    ]

    # Add parameters to the apptainer command
    for param_name, param_value in user_params.items():
        if param_value is not None:
            if param_name == 'output_dir':
                apptainer_cmd_parts.append(f"--{param_name} {mount_point}/{param_value}")
            elif param_name in ['input_file', 'modelfile']:
                apptainer_cmd_parts.append(f"--{param_name} {mount_point}/{param_value}")
            else:
                apptainer_cmd_parts.append(f"--{param_name} {param_value}")
        else:
            apptainer_cmd_parts.append(f"--{param_name}")
            
    apptainer_command = " \\\n  ".join(apptainer_cmd_parts)

    # --- DETERMINE FILE PATHS FOR SCRIPT AND LOGS ---
    # Default to current directory if no input file is present
    target_dir = os.getcwd()
    
    # If input_file exists, we want to save the script in that directory
    if 'input_file' in user_params:
        # User input path is relative to the workspace root
        relative_input_path = user_params['input_file']
        # Construct the absolute path on the host system
        clean_relative_path = relative_input_path.lstrip(os.sep).lstrip('.')
        full_input_path = os.path.join(workspace_path, clean_relative_path)
        
        # Get the directory containing the input file
        target_dir = os.path.dirname(full_input_path)
        
        # Check if this directory actually exists on the host
        if not os.path.exists(target_dir):
            print(f"Warning: Calculated directory {target_dir} does not exist. Saving to current dir.")
            target_dir = os.getcwd()

    script_filename = os.path.join(target_dir, f"{job_name}_submit.sh")
    log_filename = os.path.join(target_dir, f"{job_name}_%j.log")

    # create a submission script content
    slurm_script_content = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --output={log_filename}
#SBATCH --time={time_limit}
#SBATCH --mem={mem_limit}
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1

echo "Starting job on $(hostname)"
module load apptainer 2>/dev/null || echo "Apptainer module load skipped or failed"

{apptainer_command}
"""
    
    print("\n" + "="*60)
    print(f"GENERATING SUBMISSION SCRIPT: {script_filename}")
    print(f"LOG FILES WILL BE SAVED TO: {log_filename}")
    print("="*60)
    
    # Write the script to file
    try:
        with open(script_filename, 'w') as f:
            f.write(slurm_script_content)
    except IOError as e:
        print(f"Error writing script to {target_dir}. Falling back to current directory.")
        target_dir = os.getcwd()
        script_filename = os.path.join(target_dir, f"{job_name}_submit.sh")
        with open(script_filename, 'w') as f:
            f.write(slurm_script_content)

    print(f"Script saved. Submitting via sbatch...")
    
    # Submit the job
    try:
        result = subprocess.run(['sbatch', script_filename], check=True, capture_output=True, text=True)
        output = result.stdout.strip()
        print(f"Success! {output}")
        
        # Parse Job ID to give user the status command
        match = re.search(r"Submitted batch job (\d+)", output)
        if match:
            job_id = match.group(1)
            print("\n" + "-"*40)
            print("CHECK JOB STATUS:")
            print("-" * 40)
            print(f"To check the status of this specific job, run:")
            print(f"\033[1m  !squeue -j {job_id} \033[0m")
            print("-" * 40)
            
    except subprocess.CalledProcessError as e:
        print(f"Error submitting job: {e.stderr}")
    except FileNotFoundError:
        print("Error: 'sbatch' command not found. Are you on a cluster login node?")

    return script_filename

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
    print("4. Label Transfer (10X Visium - step 1)")
    print("5. Spot Annotation (10X Visium - step 2)")  
    print("6. FTU Spot Aggregation") 
    print(f"{'='*80}")
    
    job_choice = input("Enter your choice (1/2/3/4/5/6): ").strip()
    
    # Define job configurations
    job_configs = {
    '1': {
        'name': 'Multi-Compartment Segmentation',
        'path': ["sarderlab/fusion_v1","MultiCompartmentSegmentation", "MultiC"],
        'input_param': 'input_file',
        'params': {
            'modelfile': '6967ee7b413ffaf54798bc8e'
        }
    },
    '2': {
        'name': 'Frozen Glomerulus Segmentation',
        'path': ["sarderlab/fusion_v1", "FrozenGlomSegmentation", "GlomSeg"],
        'input_param': 'input_image',
        'params': {
            'model_file': '6967ef12413ffaf54798bc91',
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
        'path': ["sarderlab/fusion_v1", "PathomicFeatureExtraction", "PathomicsFE"],
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
            'cell_reference_file': '69892c0b7d7fb0fd9933751f'
        }
    },
    '6': {
        'name': 'FTU Spot Aggregation',
        'path': ["sarderlab/fusion_v1", "FTUSpotAggregation", "Aggregate"],
        'input_param': 'input_image',
        'params': {}
    }
   }
    if job_choice not in job_configs:
        print("Invalid choice. Please enter 1,2,3,4,5 or 6.")
        return {"error": "Invalid job selection"}
    
    selected_job = job_configs[job_choice]
    
    # Upload to Athena using the utility function
    #print("Uploading file(s) to Fusion backend...")
    upload_result = download_to_fusion_backend(
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
                    'girderApiUrl': 'http://girder:8080/api/v1/',
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