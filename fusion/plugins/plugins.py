from fusion.utilities.utility import download_to_fusion_backend
import tifffile
import numpy as np
import os
import subprocess
import re
import shutil
import time

# Session store for submitted Slurm jobs — populated by _track_slurm_job so
# check_job_status can find the log path without the user having to retype it.
_SLURM_JOBS = {}  # job_id -> {'name': str, 'log': str, 'start': float}


def _print_new_log_lines(log_path, last_pos):
    """Print any lines added to log_path since last_pos. Returns the new file position."""
    if not log_path or not os.path.exists(log_path):
        return last_pos
    try:
        with open(log_path, 'r') as f:
            f.seek(last_pos)
            new_text = f.read()
            new_pos = f.tell()
        if new_text.strip():
            for line in new_text.splitlines():
                print(f"  {line}")
        return new_pos
    except Exception:
        return last_pos


def _stream_slurm_log(job_id, job_name, log_path, poll_interval=10):
    """Stream Slurm status + live log output. Blocks until job finishes or Ctrl+C."""
    start_time = _SLURM_JOBS.get(job_id, {}).get('start', time.time())
    last_pos = 0

    print("\nStreaming log output — Ctrl+C to detach at any time")
    print("─" * 55)

    try:
        while True:
            r = subprocess.run(['squeue', '-j', job_id, '-h', '-o', '%t'],
                               capture_output=True, text=True)
            state = r.stdout.strip()
            elapsed = int(time.time() - start_time)
            elapsed_str = f"{elapsed // 60}m {elapsed % 60:02d}s"
            ts = time.strftime('%H:%M:%S')

            if state == 'PD':
                print(f"[{ts}] Status: PENDING (waiting for node)  [{elapsed_str}]")
            elif state == 'R':
                print(f"[{ts}] Status: RUNNING  [{elapsed_str}]")
            elif state == 'CG':
                print(f"[{ts}] Status: COMPLETING  [{elapsed_str}]")
            elif state:
                print(f"[{ts}] Status: {state}  [{elapsed_str}]")
            else:
                # Job left the queue — get final state from sacct
                r2 = subprocess.run(['sacct', '-j', job_id, '-n', '-o', 'State'],
                                    capture_output=True, text=True)
                output = r2.stdout.strip()
                if 'COMPLETED' in output:
                    final = 'COMPLETED ✓'
                elif 'FAILED' in output:
                    final = 'FAILED ✗'
                elif 'TIMEOUT' in output:
                    final = 'TIMEOUT ✗'
                elif 'CANCELLED' in output:
                    final = 'CANCELLED'
                else:
                    final = output.split()[0] if output else 'UNKNOWN'
                print(f"[{ts}] Status: {final}  [{elapsed_str}]")
                _print_new_log_lines(log_path, last_pos)
                print("─" * 55)
                return

            last_pos = _print_new_log_lines(log_path, last_pos)
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print(f"\nDetached. {job_name} [{job_id}] is still running on the cluster.")
        print(f"To reconnect:  check_job_status('{job_id}')")


def check_job_status(job_id=None, mode=None):
    """
    Monitor Slurm job status.

    Default (no arguments): shows a live-refreshing squeue view for all your jobs
    (like `watch -n 2 squeue -u $USER`), with a status-code legend. Press Ctrl+C to stop.

    Pass mode='live_logs' (and optionally a job_id) to tail live log output for a
    specific job instead.

    Examples:
        check_job_status()                       # watch all your jobs
        check_job_status('12345', 'live_logs')   # stream logs for job 12345
    """
    if mode == 'live_logs':
        if job_id is None:
            job_id = input("Enter Job ID: ").strip()
        if not job_id:
            print("No Job ID entered.")
            return
        job_id = str(job_id)
        info = _SLURM_JOBS.get(job_id, {})
        job_name = info.get('name', f'job_{job_id}')
        log_path = info.get('log')
        if not log_path:
            log_path = input(
                f"Log path not found for job {job_id}.\nEnter log path (or press Enter to skip): "
            ).strip() or None
        _stream_slurm_log(job_id, job_name, log_path, poll_interval=10)
        return

    # ── Default: watch-style squeue view ────────────────────────────────────
    STATUS_LEGEND = [
        ("PD",  "PENDING    — waiting for resources or dependencies"),
        ("R",   "RUNNING    — currently executing on a node"),
        ("CG",  "COMPLETING — finishing up (almost done)"),
        ("S",   "SUSPENDED  — job has been suspended"),
        ("ST",  "STOPPED    — job has been stopped"),
        ("F",   "FAILED     — job exited with a non-zero code"),
        ("CA",  "CANCELLED  — job was cancelled by user or admin"),
        ("TO",  "TIMEOUT    — job exceeded its time limit"),
        ("NF",  "NODE_FAIL  — a compute node failed"),
    ]

    try:
        from IPython.display import clear_output
        use_clear = True
    except ImportError:
        use_clear = False

    user = os.environ.get('USER') or os.environ.get('LOGNAME') or ''
    squeue_cmd = ['squeue', '--format=%.10i %.12P %.30j %.8T %.10M %.9l %R']
    if user:
        squeue_cmd = ['squeue', '-u', user, '--format=%.10i %.12P %.30j %.8T %.10M %.9l %R']

    print("Watching job queue — Ctrl+C to stop\n")
    try:
        while True:
            r = subprocess.run(squeue_cmd, capture_output=True, text=True)
            output = r.stdout.strip()

            if use_clear:
                clear_output(wait=True)

            print(f"[{time.strftime('%H:%M:%S')}]  squeue -u $USER  (refreshing every 2s — Ctrl+C to stop)\n")
            print(output if output else "(no jobs currently in queue)")
            print()
            print("─" * 60)
            print("Status codes:")
            for code, desc in STATUS_LEGEND:
                print(f"  {code:<4}  {desc}")
            if _SLURM_JOBS:
                print()
                print("Jobs submitted this session:")
                for jid, info in _SLURM_JOBS.items():
                    print(f"  {jid}  {info['name']}  →  log: {info.get('log', 'unknown')}")
            print()
            print("To stream live logs:  check_job_status('<job_id>', 'live_logs')")

            time.sleep(2)

    except KeyboardInterrupt:
        print("\nStopped watching.")



def _track_slurm_job(job_id, job_name, log_filename, poll_interval=10):
    """
    Called immediately after sbatch submission.
    Resolves the actual log path (replaces %j with the real job ID), stores job
    info in _SLURM_JOBS, prints a summary box, then asks the user whether to
    stream live output or detach and check later.
    """
    log_path = log_filename.replace('%j', job_id)

    _SLURM_JOBS[job_id] = {
        'name':  job_name,
        'log':   log_path,
        'start': time.time(),
    }

    w = 53
    print(f"\n┌{'─' * w}┐")
    print(f"│{'  Slurm Job Submitted':<{w}}│")
    print(f"│{'':<{w}}│")
    print(f"│{'  Job ID  : ' + job_id:<{w}}│")
    print(f"│{'  Job Name: ' + job_name:<{w}}│")
    print(f"│{'  Log     : ' + log_path:<{w}}│")
    print(f"└{'─' * w}┘")

    print("\nTrack this job?")
    print("  [1] Stream live log output")
    print("  [2] Detach — check manually later")

    try:
        choice = input("Enter choice (1/2): ").strip()
    except (EOFError, KeyboardInterrupt):
        choice = '2'

    if choice == '1':
        _stream_slurm_log(job_id, job_name, log_path, poll_interval)
    else:
        print(f"\nDetached. Job {job_id} is running in the background.")
        print(f"\nTo check status later, run:")
        print(f"    check_job_status('{job_id}')")


def _get_jupyter_slurm_resources(default_time="08:00:00", default_mem="96gb"):
    """
    Read the time limit and memory of the current JupyterHub Slurm job and
    return them so the analysis job can use the same allocation.

    JupyterHub (via batchspawner) sets SLURM_JOB_ID in the kernel environment.
    Falls back to the defaults if the env var is missing or squeue fails.
    """
    job_id = os.environ.get('SLURM_JOB_ID', '').strip()
    if not job_id:
        print(f"Note: SLURM_JOB_ID not found — using defaults ({default_time}, {default_mem})")
        return default_time, default_mem

    try:
        r = subprocess.run(
            ['squeue', '-j', job_id, '-h', '--format=%l %m'],
            capture_output=True, text=True, timeout=10
        )
        parts = r.stdout.strip().split()
        if len(parts) >= 2:
            time_limit = parts[0]           # e.g. "8:00:00"
            raw_mem   = parts[1].upper()    # e.g. "98304M" or "96G"

            # Normalise memory to GB for --mem sbatch directive
            if raw_mem.endswith('G'):
                mem_limit = f"{raw_mem[:-1]}gb"
            elif raw_mem.endswith('M'):
                mem_gb = int(raw_mem[:-1]) // 1024
                mem_limit = f"{mem_gb}gb"
            else:
                mem_limit = f"{raw_mem}mb"  # leave as-is if unknown unit

            print(f"Matched JupyterHub session resources: time={time_limit}, mem={mem_limit}")
            return time_limit, mem_limit
    except Exception:
        pass

    print(f"Note: Could not query Slurm job {job_id} — using defaults ({default_time}, {default_mem})")
    return default_time, default_mem

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
    # Container image mapping with their specific parameters.
    # output_dir is auto-derived from the primary input path — do not add it to params.
    # path_params: params that get the /data/ mount prefix in the container command.
    # fixed_params: hardcoded values appended to the command without prompting.
    # primary_input: the prompted param used to derive the dataset root folder.
    # input_depth: how many os.path.dirname() calls from primary_input reach the dataset root.
    # output_subdir: subfolder under dataset root used as output_dir.
    # annotations_subdir: if set, also auto-derives --annotations_dir for that subfolder.
    container_configs = {
        "multicompartment_segmentation": {
            "image": "dsrithad/fusion1_decoupled:multicompartment-segmentation-notebook",
            "script": "/opt/MultiC/multic/cli/MultiC/MultiCLocal.py",
            "params": ["input_file", "modelfile"],
            "path_params": {"input_file", "modelfile", "output_dir"},
            "output_subdir": "Segmented_FTU",
            "primary_input": "input_file",
            "input_depth": 2
        },
        "frozen_glom_segmentation": {
            "image": "dsrithad/fusion1_decoupled:frozenglom-segmentation-notebook",
            "script": "/opt/GlomSegmentation/FrozenGlomSegmentation/cli/GlomSeg/GlomSegLocal.py",
            "params": ["input_image", "model_file"],
            "path_params": {"input_image", "model_file", "output_dir"},
            "output_subdir": "Segmented_FTU",
            "primary_input": "input_image",
            "input_depth": 2
        },
        "feature_extraction": {
            "image": "dsrithad/fusion1_decoupled:feature-extraction-notebook",
            "script": "/opt/FExtract/fextract/cli/PathomicsFE/PathomicsFELocal.py",
            "params": ["input_image"],
            "path_params": {"input_image", "annotations_dir", "output_dir"},
            "output_subdir": "Files",
            "annotations_subdir": "Segmented_FTU",
            "primary_input": "input_image",
            "input_depth": 2
        },
        "label_transfer": {
            "image": "dsrithad/fusion1_decoupled:10x-visium-analysis-notebook",
            "script": "/opt/Visium_Analysis/Visium_Analysis/cli/LabelTransferLocal/LabelTransferLocal.py",
            "params": ["counts_file", "reference"],
            "path_params": {"counts_file", "reference", "output_dir"},
            "fixed_params": {
                "organ": "KPMP Atlas Kidney"
            },
            "output_subdir": "Files",
            "primary_input": "counts_file",
            "input_depth": 1
        },
        "spot_annotation": {
            "image": "dsrithad/fusion1_decoupled:10x-visium-analysis-notebook",
            "script": "/opt/Visium_Analysis/Visium_Analysis/cli/SpotAnnotationLocal/SpotAnnotationLocal.py",
            "params": ["input_file", "cell_reference_file"],
            "path_params": {"input_file", "cell_reference_file", "output_dir"},
            "fixed_params": {},
            "output_subdir": "Files",
            "primary_input": "input_file",
            "input_depth": 2
        },
        "spatial_aggregation": {
            "image": "dsrithad/fusion1_decoupled:spatial-aggregation-notebook",
            "script": "/opt/Spatial-Omics-Plugins/SpatialAggregation/cli/Aggregate/AggregateLocal.py",
            "params": ["base_annotation", "agg_annotations"],
            "path_params": {"base_annotation", "agg_annotations", "output_dir"},
            "output_subdir": "Aggregated_FTU",
            "primary_input": "base_annotation",
            "input_depth": 1
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
                if param == 'agg_annotations':
                    value = input(f"Enter {param} paths (space-separated): ").strip()
                else:
                    value = input(f"Enter {param} path: ").strip()
                if value:
                    user_params[param] = value
                    break
                else:
                    print(f"{param} is required. Please enter a value.")
    else:
        print(f"\nNo additional parameters required for {analysis_type.replace('_', ' ').title()}")

    # Auto-derive output_dir (and annotations_dir if needed) from the primary input path.
    # The primary input is something like: fusion_demo_notebooks/datasets/HBM355.CWFF.355/ometiff-pyramids/file.tif
    # Going up input_depth levels reaches the dataset root folder.
    primary = config.get('primary_input')
    if primary and primary in user_params:
        rel_input = user_params[primary].lstrip(os.sep).lstrip('.')
        input_depth = config.get('input_depth', 2)

        # Validate that the path has enough components for the configured depth.
        # e.g. depth=2 requires at least "a/b/file" (2 separators).
        path_parts = [p for p in rel_input.replace('\\', '/').split('/') if p]
        if len(path_parts) <= input_depth:
            print(f"\nWarning: '{rel_input}' is too shallow for this analysis.")
            print(f"  Expected a path at least {input_depth + 1} levels deep, e.g.:")
            if input_depth == 2:
                print(f"  fusion_demo_notebooks/datasets/HBM355.CWFF.355/ometiff-pyramids/file.tif")
            else:
                print(f"  fusion_demo_notebooks/datasets/HBM355.CWFF.355/expr.h5ad")
            print("Please re-run and enter the correct path.\n")
            return None

        dataset_root = rel_input
        for _ in range(input_depth):
            dataset_root = os.path.dirname(dataset_root)
        user_params['output_dir'] = f"{dataset_root}/{config['output_subdir']}"
        print(f"\nAuto-derived output directory: /data/{user_params['output_dir']}")
        if 'annotations_subdir' in config:
            user_params['annotations_dir'] = f"{dataset_root}/{config['annotations_subdir']}"
            print(f"Auto-derived annotations directory: /data/{user_params['annotations_dir']}")

    # Job name is derived from the analysis type key (already snake_case)
    job_name = analysis_type

    # Match resources to the current JupyterHub Slurm session
    time_limit, mem_limit = _get_jupyter_slurm_resources()
    
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

    # Add user-provided and auto-derived parameters to the apptainer command.
    # path_params are prefixed with the container mount point (/data).
    path_params = config.get('path_params', set())
    for param_name, param_value in user_params.items():
        if param_value is not None:
            if param_name in path_params:
                apptainer_cmd_parts.append(f"--{param_name} {mount_point}/{param_value}")
            else:
                apptainer_cmd_parts.append(f"--{param_name} {param_value}")
        else:
            apptainer_cmd_parts.append(f"--{param_name}")

    # Add fixed (hardcoded) params — values are already fully-specified container paths.
    for param_name, param_value in config.get('fixed_params', {}).items():
        apptainer_cmd_parts.append(f"--{param_name} {param_value}")
            
    apptainer_command = " \\\n  ".join(apptainer_cmd_parts)

    # --- DETERMINE FILE PATHS FOR SCRIPT AND LOGS ---
    # Logs and submission script go into a "logs" folder under the dataset root.
    # Fall back to cwd if the dataset root can't be resolved.
    target_dir = os.getcwd()

    primary = config.get('primary_input', 'input_file')
    if primary in user_params:
        relative_input_path = user_params[primary]
        clean_relative_path = relative_input_path.lstrip(os.sep).lstrip('.')

        # Walk up input_depth levels to reach the dataset root
        dataset_rel = clean_relative_path
        for _ in range(config.get('input_depth', 2)):
            dataset_rel = os.path.dirname(dataset_rel)

        logs_dir = os.path.join(workspace_path, dataset_rel, 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        target_dir = logs_dir

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
        
        match = re.search(r"Submitted batch job (\d+)", output)
        if match:
            job_id = match.group(1)
            log_path = log_filename.replace('%j', job_id)

            _SLURM_JOBS[job_id] = {
                'name':  job_name,
                'log':   log_path,
                'start': time.time(),
            }

            w = 53
            print(f"\n┌{'─' * w}┐")
            print(f"│{'  Slurm Job Submitted':<{w}}│")
            print(f"│{'':<{w}}│")
            print(f"│{'  Job ID  : ' + job_id:<{w}}│")
            print(f"│{'  Job Name: ' + job_name:<{w}}│")
            print(f"│{'  Log     : ' + log_path:<{w}}│")
            print(f"└{'─' * w}┘")
            print(f"\nTo watch all your jobs:")
            print(f"    check_job_status()")
            print(f"To stream live log output:")
            print(f"    check_job_status('{job_id}', 'live_logs')")
            
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