from fusion.utilities.utility import upload_to_fusion_backend, download_model_files_from_fusion, download_reference_files_from_fusion, HuBMAP_to_workspace_download
import tifffile
import numpy as np
import os
import subprocess
import re
import shutil
import time
import anndata
import shlex
import pandas as pd
import socket

# Session store for submitted Slurm jobs
_SLURM_JOBS = {}   # job_id -> {'name': str, 'log': str, 'start': float}

# Session store for submitted Fusion backend (Girder) jobs
_FUSION_JOBS = {}  # job_id -> {'name': str, 'gc': girder_client, 'file_name': str, 'start': float}

_GIRDER_STATUS = {
    0: 'INACTIVE',
    1: 'QUEUED',
    2: 'RUNNING',
    3: 'SUCCESS ✓',
    4: 'ERROR ✗',
    5: 'CANCELLED',
}


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
    
def _is_hipergator():
    """Check if running on HiPerGator."""
    hostname = socket.gethostname()
    return "ufhpc" in hostname

def _stream_slurm_log(job_id, job_name, log_path, poll_interval=10):
    """Stream live log output. Blocks until job finishes or Ctrl+C."""
    last_pos = 0
    print("─" * 55)

    try:
        while True:
            r = subprocess.run(['squeue', '-j', job_id, '-h', '-o', '%t'],
                               capture_output=True, text=True)
            state = r.stdout.strip()

            if not state:
                # Job left the queue — print any remaining log lines then show final state
                last_pos = _print_new_log_lines(log_path, last_pos)
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
                print("─" * 55)
                print(f"Job {job_id} finished — {final}")
                return

            last_pos = _print_new_log_lines(log_path, last_pos)
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print(f"\n{job_name} [{job_id}] is still running on the cluster.")
        print(f"To reconnect:  check_job_status('{job_id}', 'live_logs')")
        print(f"To cancel:     !scancel {job_id}")

def _stream_girder_log(job_id, job_name, gc, poll_interval=10):
    """Stream live log output from a Girder job. Blocks until job finishes or Ctrl+C."""
    last_count = 0
    print("─" * 55)

    try:
        while True:
            job = gc.get(f'job/{job_id}')
            status = job.get('status', 0)
            log_entries = job.get('log', [])

            for entry in log_entries[last_count:]:
                print(entry, end='')
            last_count = len(log_entries)

            if status in (3, 4, 5):  # terminal states: SUCCESS, ERROR, CANCELLED
                print("─" * 55)
                print(f"Job {job_id} finished — {_GIRDER_STATUS.get(status, str(status))}")
                return

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print(f"\n{job_name} [{job_id}] is still running on JupyterHub.")
        print(f"To reconnect:  check_job_status('{job_id}', 'live_logs')")

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

        # Detect job type: Girder if found in _FUSION_JOBS, otherwise Slurm
        if job_id in _FUSION_JOBS:
            info = _FUSION_JOBS[job_id]
            _stream_girder_log(job_id, info['name'], info['gc'], poll_interval=10)
        else:
            info = _SLURM_JOBS.get(job_id, {})
            job_name = info.get('name', f'job_{job_id}')
            log_path = info.get('log')
            if not log_path:
                log_path = input(
                    f"Log path not found for job {job_id}.\nEnter log path (or press Enter to skip): "
                ).strip() or None
            _stream_slurm_log(job_id, job_name, log_path, poll_interval=10)
        return

    # ── Default: watch-style view (Slurm + Fusion backend) ──────────────────
    try:
        from IPython.display import clear_output
        use_clear = True
    except ImportError:
        use_clear = False

    user = os.environ.get('USER') or os.environ.get('LOGNAME') or ''
    # Fetch JOBID, PARTITION (for WORKSPACES filtering only), NAME, STATE, TIME
    squeue_cmd = ['squeue', '-h', '--format=%i|%P|%j|%T|%M']
    if user:
        squeue_cmd = ['squeue', '-u', user, '-h', '--format=%i|%P|%j|%T|%M']

    try:
        while True:
            if mode=='live' and use_clear:
                clear_output(wait=True)

            if mode=='live':
                print(f"[{time.strftime('%H:%M:%S')}]  refreshing every 2s  |  To cancel: !scancel <job_id>\n")

            # ── Slurm section ────────────────────────────────────────────────
            print(f"{'─' * 70}")
            print("SLURM JOBS")
            print(f"{'─' * 70}")
            r = subprocess.run(squeue_cmd, capture_output=True, text=True)
            rows = [
                line.split('|') for line in r.stdout.strip().splitlines()
                if line and not line.split('|')[1].upper().startswith('WORKSPACES')
            ]
            if rows:
                print(f"  {'JOBID':<12}  {'NAME':<35}  {'STATE':<12}  TIME")
                print(f"  {'─'*12}  {'─'*35}  {'─'*12}  {'─'*10}")
                for parts in rows:
                    if len(parts) >= 5:
                        print(f"  {parts[0]:<12}  {parts[2]:<35}  {parts[3]:<12}  {parts[4]}")
            else:
                print("  (no analysis jobs in queue)")

            # ── Fusion backend section ───────────────────────────────────────
            if _FUSION_JOBS:
                print(f"\n{'─' * 70}")
                print("FUSION BACKEND JOBS  (JupyterHub / Girder)")
                print(f"{'─' * 70}")
                print(f"  {'JOB ID':<28}  {'NAME':<35}  STATUS")
                print(f"  {'─'*28}  {'─'*35}  {'─'*12}")
                for jid, info in _FUSION_JOBS.items():
                    try:
                        job = info['gc'].get(f'job/{jid}')
                        status_label = _GIRDER_STATUS.get(job.get('status', 0), '?')
                    except Exception:
                        status_label = 'UNKNOWN'
                    print(f"  {jid:<28}  {info['name']:<35}  {status_label}")

            print()
            print("To stream live logs:  check_job_status('<job_id>', 'live_logs')")

            if mode != 'live':
                break

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

    print(f"\n{'─' * 55}")
    print(f"Slurm Job Submitted")
    print(f"{'─' * 55}")
    print(f"  Job ID  : {job_id}")
    print(f"  Job Name: {job_name}")
    print(f"  Log     : {log_path}")
    print(f"{'─' * 55}")

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

def _get_jupyter_slurm_resources(analysis_type=None):
    """Return fixed Slurm resource limits for analysis jobs."""
    if analysis_type=='label_transfer':
        return "03:00:00", "64GB", 4, 0, None
    if analysis_type in ('frozen_glom_segmentation', 'multicompartment_segmentation'):
        return "03:00:00", "16gb", 2, 1, 'hpg-turin'
    return "03:00:00", "16gb", 2, 0, None

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

def _resolve_user_path(path, must_exist=True):
    """
    Resolve a user-entered path to a real absolute path.

    Fixes:
    cwd   = /home/user/fusion_demo_notebooks
    input = fusion_demo_notebooks/datasets/data_1
    final = /home/user/fusion_demo_notebooks/datasets/data_1

    Also keeps /blue, /orange, and /home paths real.
    """
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

    if parts:
        cur = cwd
        while cur and cur != os.path.dirname(cur):
            if os.path.basename(cur) == parts[0]:
                candidates.append(os.path.abspath(os.path.join(os.path.dirname(cur), raw_path)))
            cur = os.path.dirname(cur)
    
        if parts[0] in {"home", "blue", "orange"}:
            candidates.append(os.path.abspath(os.path.join(os.sep, raw_path)))
    
    candidates.append(os.path.abspath(os.path.join(cwd, raw_path)))

    for candidate in dict.fromkeys(candidates):
        if os.path.exists(candidate):
            return candidate

    if must_exist:
        checked = "\n".join(f"  - {c}" for c in dict.fromkeys(candidates))
        raise FileNotFoundError(f"Path not found: {raw_path}\nChecked:\n{checked}")

    return candidates[0]


def _get_bind_root(path):
    abs_path = os.path.abspath(path)
    parts = abs_path.strip(os.sep).split(os.sep)
    if not parts or not parts[0]:
        return os.sep
    return os.path.join(os.sep, parts[0])


def _quote_path(path):
    return shlex.quote(str(path))

def sanitize_h5ad_obsm(h5ad_path):
    
    """
    Loads an h5ad file and removes any non-numeric columns from the 'DeepScence'
    obsm DataFrame entry. Overwrites the file in place if changes were made.
    Returns True if the file was modified, False otherwise.
    """
    adata = anndata.read_h5ad(h5ad_path)

    if 'DeepScence' not in adata.obsm:
        #print(f"  No 'DeepScence' entry found in obsm — no changes needed.")
        return False

    entry = adata.obsm['DeepScence']

    if not isinstance(entry, pd.DataFrame):
        #print(f"  'DeepScence' is not a DataFrame — no changes needed.")
        return False

    string_cols = [
        col for col in entry.columns
        if not pd.api.types.is_numeric_dtype(entry[col])
    ]

    if not string_cols:
        #print(f"  'DeepScence' has no non-numeric columns — no changes needed.")
        return False

    #print(f"  Dropping non-numeric column(s) from 'DeepScence': {string_cols}")
    adata.obsm['DeepScence'] = entry.drop(columns=string_cols)
    adata.write_h5ad(h5ad_path)
    #print(f"  Overwrote: {h5ad_path}")
    return True

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
            "image": "sarderlab/fusion1.0_decoupled:multi_compartment_segmentation",
            "script": "/opt/MultiC/multic/cli/MultiC/MultiCLocal.py",
            "params": ["input_file", "modelfile"],
            "path_params": {"input_file", "modelfile", "output_dir"},
            "output_subdir": "Segmented_FTU",
            "primary_input": "input_file",
            "input_depth": 2
        },
        "frozen_glom_segmentation": {
            "image": "sarderlab/fusion1.0_decoupled:frozen_glom_segmentation",
            "script": "/opt/GlomSegmentation/FrozenGlomSegmentation/cli/GlomSeg/GlomSegLocal.py",
            "params": ["input_image", "model_file"],
            "path_params": {"input_image", "model_file", "output_dir"},
            "output_subdir": "Segmented_FTU",
            "primary_input": "input_image",
            "input_depth": 2
        },
        "feature_extraction": {
            "image": "sarderlab/fusion1.0_decoupled:pathomic_feature_extraction",
            "script": "/opt/FExtract/fextract/cli/PathomicsFE/PathomicsFELocal.py",
            "params": ["input_image"],
            "path_params": {"input_image", "annotations_dir", "output_dir"},
            "output_subdir": "Files",
            "annotations_subdir": "Segmented_FTU",
            "primary_input": "input_image",
            "input_depth": 2
        },
        "label_transfer": {
            "image": "sarderlab/fusion1.0_decoupled:10x_visium_analysis",
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
            "image": "sarderlab/fusion1.0_decoupled:10x_visium_analysis",
            "script": "/opt/Visium_Analysis/Visium_Analysis/cli/SpotAnnotationLocal/SpotAnnotationLocal.py",
            "params": ["input_file", "cell_reference_file"],
            "path_params": {"input_file", "cell_reference_file", "output_dir"},
            "fixed_params": {},
            "output_subdir": "Files",
            "primary_input": "input_file",
            "input_depth": 2,
        },
        "spatial_aggregation": {
            "image": "sarderlab/fusion1.0_decoupled:ftu_spot_aggregation",
            "script": "/opt/Spatial-Omics-Plugins/SpatialAggregation/cli/Aggregate/AggregateLocal.py",
            "params": ["base_annotation"],
            "path_params": {"base_annotation", "agg_annotations", "output_dir"},
            "output_subdir": "Aggregated_FTU",
            "primary_input": "base_annotation",
            "input_depth": 2
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

    workspace_path = get_hive_workspace_root()
    mount_point = "/data"

    # Get parameters specific to this container
    user_params = {}
    if config['params']:
        print(f"\nRequired parameters for {analysis_type.replace('_', ' ').title()}:")
        print("-" * 40)
        for param in config['params']:
            while True:
                value = input(f"Enter {param} path: ").strip()
                if value:
                    try:
                        user_params[param] = _resolve_user_path(value, must_exist=True)
                        break
                    except FileNotFoundError as e:
                        print(f"\nError: {e}\n")
                else:
                    print(f"{param} is required. Please enter a value.")
    else:
        print(f"\nNo additional parameters required for {analysis_type.replace('_', ' ').title()}")
    
    if analysis_type == "label_transfer" and "counts_file" in user_params:
        h5ad_abs = user_params["counts_file"]
        #print("\nChecking h5ad file for non-numeric 'DeepScence' obsm columns...")
        #print("─" * 55)
        try:
            sanitize_h5ad_obsm(h5ad_abs)
        except Exception as e:
            print(f"  Warning: Could not process {h5ad_abs}: {e}")
        #print("─" * 55)

    # Auto-derive output_dir (and annotations_dir if needed) from the primary input path.
    # The primary input is something like: fusion_demo_notebooks/datasets/HBM355.CWFF.355/ometiff-pyramids/file.tif
    # Going up input_depth levels reaches the dataset root folder.
    primary = config.get('primary_input')
    if primary and primary in user_params:
        input_abs = user_params[primary]
        input_depth = config.get('input_depth', 2)
        
        path_parts = [p for p in input_abs.replace('\\', '/').split('/') if p]
        if len(path_parts) <= input_depth:
            print(f"\nWarning: '{input_abs}' is too shallow for this analysis.")
            print(f"  Expected a path at least {input_depth + 1} levels deep, e.g.:")
            if input_depth == 2:
                print(f"  fusion_demo_notebooks/datasets/HBM355.CWFF.355/ometiff-pyramids/file.tif")
            else:
                print(f"  fusion_demo_notebooks/datasets/HBM355.CWFF.355/expr.h5ad")
            print("Please re-run and enter the correct path.\n")
            return None
        
        dataset_root = input_abs
        for _ in range(input_depth):
            dataset_root = os.path.dirname(dataset_root)
        
        user_params['output_dir'] = os.path.join(dataset_root, config['output_subdir'])
        
        if 'annotations_subdir' in config:
            user_params['annotations_dir'] = os.path.join(dataset_root, config['annotations_subdir'])
        if analysis_type == "spot_annotation":
            import glob as _glob
            files_dir = os.path.join(dataset_root, "Files")
            matches = _glob.glob(os.path.join(files_dir, "*_integrated.rds"))
            if len(matches) == 0:
                print(f"\nError: No *_integrated.rds file found in {files_dir}")
                print("Please run Label Transfer first to generate the integrated RDS.\n")
                return None
            if len(matches) > 1:
                print(f"\nError: Multiple *_integrated.rds files found in {files_dir}:")
                for m in matches:
                    print(f"  {m}")
                print("Please ensure only one integrated RDS exists.\n")
                return None
            rds_path = matches[0]
            user_params['input_file'] = rds_path
            print(f"Found integrated RDS: {rds_path}")

        if analysis_type == "spatial_aggregation":
            segmented_ftu_dir = os.path.join(dataset_root, "Segmented_FTU")
            supported_exts = {'.json', '.geojson', '.xml', '.csv', '.parquet'}
            if os.path.isdir(segmented_ftu_dir):
                ann_files = sorted([
                    f for f in os.listdir(segmented_ftu_dir)
                    if os.path.splitext(f)[1].lower() in supported_exts
                ])
            else:
                ann_files = []

            if not ann_files:
                print(f"\nNo annotation files found in Segmented FTU at: {segmented_ftu_dir}")
                print("Please ensure Segmented FTU annotations exist before running aggregation.")
                return None

            print(f"\nAvailable annotations in Segmented FTU:")
            print("-" * 40)
            for i, fname in enumerate(ann_files, 1):
                print(f"  {i}. {fname}")

            while True:
                sel = input("\nEnter annotation numbers to aggregate (comma-separated, e.g. 1,3): ").strip()
                try:
                    indices = [int(x.strip()) for x in sel.split(',')]
                    selected_paths = []
                    valid = True
                    for idx in indices:
                        if 1 <= idx <= len(ann_files):
                            ann_path = os.path.join(dataset_root, "Segmented_FTU", ann_files[idx - 1])
                            selected_paths.append(ann_path)
                        else:
                            print(f"  {idx} is out of range (1-{len(ann_files)}). Please try again.")
                            valid = False
                            break
                    if valid and selected_paths:
                        user_params['agg_annotations'] = selected_paths
                        print(f"Selected {len(selected_paths)} annotation(s): {[ann_files[i-1] for i in indices]}")
                        break
                except ValueError:
                    print("Please enter numbers separated by commas.")

    # Job name is derived from the analysis type key (already snake_case)
    job_name = analysis_type

    # Match resources to the current JupyterHub Slurm session
    time_limit, mem_limit, cpus, gpus, partition = _get_jupyter_slurm_resources(analysis_type)
    
    print("\nApptainer Cache Directory:")
    print("-" * 40)
    print("  [1] Default (home directory)")
    print("  [2] Custom path")
    
    cache_choice = input("Enter choice (1/2): ").strip()

    cache_lines = ""
    if cache_choice == '2':
        while True:
            cache_path = input("Enter cache directory path: ").strip()
            if cache_path:
                try:
                    os.makedirs(cache_path, exist_ok=True)
                    tmp_path = os.path.join(cache_path, 'tmp')
                    os.makedirs(tmp_path, exist_ok=True)
                    cache_lines = f"export APPTAINER_CACHEDIR={_quote_path(cache_path)}\nexport APPTAINER_TMPDIR={_quote_path(tmp_path)}"
                    print(f"Cache will be stored at: {cache_path}")
                    break
                except Exception as e:
                    print(f"Could not create directory: {e}\nPlease enter a valid path.")
            else:
                print("Path cannot be empty.")
    
    path_params = config.get('path_params', set())
    bind_roots = set()
    
    for param_name, param_value in user_params.items():
        if param_name in path_params and param_value is not None:
            values = param_value if isinstance(param_value, list) else [param_value]
            for value in values:
                bind_roots.add(_get_bind_root(value))
    
    bind_args = " ".join(
        f"-B {_quote_path(root)}:{_quote_path(root)}"
        for root in sorted(bind_roots)
    )
    
    if analysis_type in (
    "frozen_glom_segmentation",
    "multicompartment_segmentation"):
        NV='--nv'
    else: 
        NV=""
    
    apptainer_cmd_parts = [
        f"apptainer exec {NV} {bind_args}",
        f"docker://{config['image']}",
        f"python {config['script']}"
    ]

    # Add user-provided and auto-derived parameters to the apptainer command.
    # Paths are already absolute host paths, and the same roots are bound into the container.
    skip_script_params = config.get('skip_script_params', set())
    
    for param_name, param_value in user_params.items():
        if param_name in skip_script_params:
            continue
    
        if param_value is not None:
            if isinstance(param_value, list):
                paths_str = " ".join(_quote_path(p) for p in param_value)
                apptainer_cmd_parts.append(f"--{param_name} {paths_str}")
            else:
                apptainer_cmd_parts.append(f"--{param_name} {_quote_path(param_value)}")
        else:
            apptainer_cmd_parts.append(f"--{param_name}")
    
        # Add fixed (hardcoded) params — quote values that contain spaces.
        for param_name, param_value in config.get('fixed_params', {}).items():
            quoted = _quote_path(param_value)
            apptainer_cmd_parts.append(f"--{param_name} {quoted}")
            
    apptainer_command = " \\\n  ".join(apptainer_cmd_parts)

    # --- DETERMINE FILE PATHS FOR SCRIPT AND LOGS ---
    # Logs and submission script go into a "logs" folder under the dataset root.
    # Fall back to cwd if the dataset root can't be resolved.
    target_dir = os.getcwd()

    primary = config.get('primary_input', 'input_file')
    if primary in user_params:
        dataset_rel = dataset_root
        logs_dir = os.path.join(dataset_root, 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        target_dir = logs_dir

    script_filename = os.path.join(target_dir, f"{job_name}_submit.sh")
    log_filename = os.path.join(target_dir, f"{job_name}_%j.log")

    # For label_transfer, clean up the expr_components folder created as a side effect.
    # For spot_annotation, rename the output *_annotations.json to Spots.json.
    cleanup_cmd = ""
    pre_cmd = ""
    if analysis_type == "label_transfer" and primary in user_params:
        abs_dataset_root = dataset_rel
        cleanup_cmd = f"\nrm -rf {_quote_path(os.path.join(abs_dataset_root, 'expr_components'))}"
    elif analysis_type == "spot_annotation" and primary in user_params:
        abs_files_dir = os.path.join(dataset_rel, "Files")
        cleanup_cmd = f"\nmv {abs_files_dir}/*_annotations.json {abs_files_dir}/Spots.json"
    elif analysis_type == "spatial_aggregation" and 'agg_annotations' in user_params:
        import json as _json
        abs_output_dir = user_params['output_dir']
        mkdir_lines = []
        for ann_path in user_params['agg_annotations']:
            abs_ann_path = ann_path
            try:
                with open(abs_ann_path) as _f:
                    ann_data = _json.load(_f)
                ann_name = (ann_data.get('annotation', {}).get('name', '')
                            or ann_data.get('name', ''))
                if '/' in ann_name:
                    subdir = os.path.join(abs_output_dir, os.path.dirname(ann_name))
                    mkdir_lines.append(f"mkdir -p {_quote_path(subdir)}")
            except Exception:
                pass
        if mkdir_lines:
            pre_cmd = "\n" + "\n".join(mkdir_lines)
        # After container runs, flatten any subdirs in Aggregated_FTU back to the top level
        # e.g. arteries/arterioles_aggregated.json -> arteries_arterioles_aggregated.json
        cleanup_cmd = f"""
find {abs_output_dir} -mindepth 2 -type f | while IFS= read -r filepath; do
    rel="${{filepath#{abs_output_dir}/}}"
    newname="${{rel//\\//_}}"
    mv "$filepath" "{abs_output_dir}/$newname"
done
find {abs_output_dir} -mindepth 1 -type d -empty -delete"""

    gpu_lines = ""
    if gpus > 0 and partition and _is_hipergator():
        gpu_lines = f"#SBATCH --partition={partition}\n#SBATCH --gpus={gpus}"
    
    # create a submission script content
    slurm_script_content = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --output={log_filename}
#SBATCH --time={time_limit}
#SBATCH --mem={mem_limit}
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={cpus}
{gpu_lines}
{cache_lines}

echo "Starting job on $(hostname)"
module load apptainer 2>/dev/null || echo "Apptainer module load skipped or failed"
{pre_cmd}
{apptainer_command}{cleanup_cmd}
"""
    
    # Write the script to file
    try:
        with open(script_filename, 'w') as f:
            f.write(slurm_script_content)
    except IOError:
        target_dir = os.getcwd()
        script_filename = os.path.join(target_dir, f"{job_name}_submit.sh")
        with open(script_filename, 'w') as f:
            f.write(slurm_script_content)
    
    # Submit the job
    try:
        result = subprocess.run(['sbatch', script_filename], check=True, capture_output=True, text=True)
        output = result.stdout.strip()
        
        match = re.search(r"Submitted batch job (\d+)", output)
        if match:
            job_id = match.group(1)
            log_path = log_filename.replace('%j', job_id)

            _SLURM_JOBS[job_id] = {
                'name':  job_name,
                'log':   log_path,
                'start': time.time(),
            }

            print(f"\nJob submitted  |  ID: {job_id}  |  Name: {job_name}")
            print(f"Log: {log_path}")
            print(f"\nTo watch all your jobs:      check_job_status()")
            print(f"To stream live log output:   check_job_status('{job_id}', 'live_logs')")
            print(f"To cancel:                   !scancel {job_id}")
            
    except subprocess.CalledProcessError as e:
        print(f"Error submitting job: {e.stderr}")
    except FileNotFoundError:
        print("Error: 'sbatch' command not found. Are you on a cluster login node?")

    return script_filename

def run_analysis_tasks_fusion_backend(gc, user_name, hubmap_id=None, file_path=None, file_paths=None, dir_path=None):
    """
    Run multi-compartment segmentation on image(s) from HubMAP or local file(s).

    Args:
        gc: Girder client instance.
        user_name (str): Athena username.
        hubmap_id (str): HubMAP ID to process.
        file_path (str): Local file path to process.
        file_paths (list): List of local file paths to process.
        dir_path (str): Local directory path; all files in the directory will be uploaded and processed.

    Returns:
        dict: Job information including job_ids and file details.
    """
    if sum([bool(hubmap_id), bool(file_path), bool(file_paths), bool(dir_path)]) > 1:
        raise ValueError("Please provide only one of: hubmap_id, file_path, file_paths, or dir_path.")

    if not hubmap_id and not file_path and not file_paths and not dir_path:
        raise ValueError("Please provide either hubmap_id, file_path, file_paths, or dir_path.")

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
        'path': ["sarderlab/fusion1.0","MultiCompartmentSegmentation", "MultiC"],
        'input_param': 'input_file',
        'params': {
            'modelfile': '6967ee7b413ffaf54798bc8e'
        }
    },
    '2': {
        'name': 'Frozen Glomerulus Segmentation',
        'path': ["sarderlab/fusion1.0", "FrozenGlomSegmentation", "GlomSeg"],
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
        'path': ["sarderlab/fusion1.0", "PathomicFeatureExtraction", "PathomicsFE"],
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
        'path': ["sarderlab/fusion1.0", "10X_VisiumAnalysis", "LabelTransfer"],
        'input_param': 'input_image',
        'params': {
            'organ': 'KPMP Atlas Kidney',
            'reference': '697baf5f13bbccd3003a6435'
        }
    },
    '5': {
        'name': 'Spot Annotation (10X Visium - step 2)',
        'path': ["sarderlab/fusion1.0", "10X_VisiumAnalysis", "SpotAnnotation"],
        'input_param': 'input_file',
        'params': {
            'cell_reference_file': '69892c0b7d7fb0fd9933751f'
        }
    },
    '6': {
        'name': 'FTU Spot Aggregation',
        'path': ["sarderlab/fusion1.0", "FTUSpotAggregation", "Aggregate"],
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
    upload_result = upload_to_fusion_backend(
        gc=gc,
        hubmap_id=hubmap_id,
        user=user_name,
        file_path=file_path,
        file_paths=file_paths,
        dir_path=dir_path,
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
                submitted_id = r['_id']
                print(f"Job submitted. job_id={submitted_id}")

                _FUSION_JOBS[submitted_id] = {
                    'name':      f"{selected_job['name']} — {wsi['name']}",
                    'gc':        gc,
                    'file_name': wsi['name'],
                    'start':     time.time(),
                }

                job_results.append({
                    'job_id': submitted_id,
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

        successful = [j for j in job_results if j.get('job_id')]
        if successful:
            print(f"\nTo watch all your jobs:")
            print(f"    check_job_status()")
            print(f"To stream live log output:")
            print(f"    check_job_status('{successful[0]['job_id']}', 'live_logs')")

        return response_summary
        
    except Exception as e:
        print(f"Error submitting job: {e}")
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# HOW TO RUN AN ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
# Use run_analysis() to launch any analysis task. Choose a backend:
#
#   'notebook'  — runs the analysis via Slurm on the cluster using Apptainer
#                 containers. Fully interactive: prompts for analysis type and
#                 file paths, then submits a Slurm job.
#
#                 run_analysis('notebook')
#
#   'fusion'    — runs the analysis on the Fusion / JupyterHub backend via
#                 Girder. Requires a Girder client (gc) and your username.
#                 Optionally pass a HubMAP ID or local file path to skip prompts.
#
#                 run_analysis('fusion', gc=gc, user_name='your_username')
#                 run_analysis('fusion', gc=gc, user_name='your_username', hubmap_id='HBM355.CWFF.355')
#
# After submission, use check_job_status() to monitor your jobs:
#
#   check_job_status()                        # live-refreshing queue view
#   check_job_status('job_id', 'live_logs')   # stream log output for a job
# ─────────────────────────────────────────────────────────────────────────────


def run_analysis(backend, gc=None, user_name=None, hubmap_id=None, file_path=None, file_paths=None, dir_path=None):
    """
    Unified entry point for running analysis tasks.

    Args:
        backend (str): 'notebook' to run via Slurm/Apptainer, 'fusion' to run via the Fusion backend.
        gc: Girder client instance (optional for 'notebook', required for 'fusion').
        user_name (str): Athena username (required when backend='fusion').
        hubmap_id (str): HubMAP ID to process. For 'notebook', auto-downloads the dataset to the workspace. For 'fusion', uploads it to the Fusion backend.
        file_path (str): Local file path to process (optional, fusion only).
        file_paths (list): List of local file paths to process (optional, fusion only).
        dir_path (str): Local directory path; all files in the directory will be uploaded and processed (optional, fusion only).
    """
    print("Running from:", __file__)
    
    if backend == 'notebook':
        if gc is not None:
            download_reference_files_from_fusion(gc)
            download_model_files_from_fusion(gc)
        if hubmap_id is not None:
            HuBMAP_to_workspace_download(hubmap_id)
        
        return run_apptainer_analysis()
    
    elif backend == 'fusion':
        if gc is None or user_name is None:
            raise ValueError("backend='fusion' requires both 'gc' and 'user_name' arguments.")
        return run_analysis_tasks_fusion_backend(
            gc=gc,
            user_name=user_name,
            hubmap_id=hubmap_id,
            file_path=file_path,
            file_paths=file_paths,
            dir_path=dir_path,
        )
    else:
        raise ValueError(f"Unknown backend '{backend}'. Choose 'notebook' or 'fusion'.")