"""
Test file to individually run all 6 Fusion backend analyses on the same image.

Files are fetched directly from Girder — no local upload needed.
Set the Girder IDs in the CONFIG section below, then run:
    python tests/test_fusion_analysis.py
"""

import girder_client
from getpass import getpass
import json
import os

TOKEN_CACHE = os.path.join(os.path.dirname(__file__), '.girder_token')

# ── CONFIG — set your Girder IDs here ─────────────────────────────────────────

FUSION_API_URL = 'https://fusionpub.rc.ufl.edu/api/v1'

# Internal Docker URL used by plugins running on the server to call back to Girder.
# This bypasses the nginx proxy and avoids 414 URI Too Long errors on large metadata PUTs.
GIRDER_INTERNAL_URL = 'http://girder:8080/api/v1/'

# Girder item ID of the test WSI — used for all 6 jobs
TEST_ITEM_ID = '69a754e63bf95fef535604cb'

# Job 4 (Label Transfer) — Girder file ID of the counts file
COUNTS_FILE_ID = '69a754c93bf95fef535604c6'

# Job 6 (FTU Spot Aggregation)
BASE_ANNOTATION = 'Spots'
AGG_ANNOTATION  = 'arteries/arterioles, tubules'   # comma-separated

# ── Job configs (mirrors run_analysis_tasks_fusion_backend) ───────────────────

JOB_CONFIGS = {
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

# ── Shared helpers ─────────────────────────────────────────────────────────────

def connect():
    """
    Authenticate to the Fusion Girder backend and return gc.
    Caches the token in .girder_token so subsequent runs skip the password prompt.
    Delete .girder_token to force re-login.
    """
    gc = girder_client.GirderClient(apiUrl=FUSION_API_URL)

    # Try cached token first
    if os.path.exists(TOKEN_CACHE):
        token = json.loads(open(TOKEN_CACHE).read())['token']
        gc.token = token
        try:
            me = gc.get('user/me')
            if me:
                print(f"Reusing session for: {me['firstName']} {me['lastName']}\n")
                return gc
        except Exception:
            print("Cached token expired, re-authenticating...")

    username = input("Fusion username: ").strip()
    password = getpass("Fusion password: ")
    user_info = gc.authenticate(username, password)
    json.dump({'token': gc.token}, open(TOKEN_CACHE, 'w'))
    print(f"Logged in as: {user_info['firstName']} {user_info['lastName']}\n")
    return gc


def get_wsi_item(gc, item_id):
    """
    Fetch a single WSI item by Girder item ID.
    Mirrors the largeImage lookup in run_analysis_tasks_fusion_backend.
    """
    details = gc.get(f'item/{item_id}')
    if 'largeImage' not in details:
        raise RuntimeError(f"Item {item_id} does not have a largeImage (not a WSI).")
    print(f"Using WSI: {details['name']} (item_id={item_id})")
    return [{
        'name': details['name'],
        'item_id': item_id,
        'file_id': details['largeImage'].get('fileId')
    }]


def submit_jobs(gc, job_config, wsi_items, extra_params=None):
    """
    Submit jobs for each WSI and return a list of result dicts.
    Mirrors the job submission logic in run_analysis_tasks_fusion_backend.

    extra_params: flat dict applied to every job,
                  or a callable(wsi) -> dict for per-file params.
    """
    response = gc.get('slicer_cli_web/docker_image')
    run_endpoint = response
    for key in job_config['path']:
        if key not in run_endpoint:
            raise KeyError(
                f"Path key '{key}' not found. Available keys: {list(run_endpoint.keys())}"
            )
        run_endpoint = run_endpoint[key]
    run_endpoint = run_endpoint['run']

    results = []
    for wsi in wsi_items:
        print(f"  Submitting for: {wsi['name']}")
        try:
            params = {
                job_config['input_param']: wsi['file_id'],
                'girderApiUrl': GIRDER_INTERNAL_URL,
                'girderToken': gc.token
            }
            params.update(job_config['params'])
            if callable(extra_params):
                params.update(extra_params(wsi))
            elif extra_params:
                params.update(extra_params)

            r = gc.post(run_endpoint, parameters=params)
            print(f"    job_id: {r['_id']}")
            results.append({
                'job_id': r['_id'],
                'file_name': wsi['name'],
                'item_id': wsi['item_id'],
                'status': 'submitted'
            })
        except Exception as e:
            print(f"    FAILED: {e}")
            results.append({
                'job_id': None,
                'file_name': wsi['name'],
                'item_id': wsi['item_id'],
                'status': 'failed',
                'error': str(e)
            })
    return results


def _run(label, gc, job_config, extra_params=None):
    print(f"\n{'='*60}\n{label}\n{'='*60}")
    wsi_items = get_wsi_item(gc, TEST_ITEM_ID)
    results = submit_jobs(gc, job_config, wsi_items, extra_params)
    failed = [r for r in results if r['status'] == 'failed']
    print(f"Done — {len(results) - len(failed)}/{len(results)} submitted, item_id={TEST_ITEM_ID}")
    return {'job_type': label, 'item_id': TEST_ITEM_ID, 'jobs': results}

# ── Individual test functions ──────────────────────────────────────────────────

def test_multi_compartment_segmentation(gc):
    return _run("1. Multi-Compartment Segmentation", gc, JOB_CONFIGS['1'])


def test_frozen_glomerulus_segmentation(gc):
    return _run("2. Frozen Glomerulus Segmentation", gc, JOB_CONFIGS['2'])


def test_feature_extraction(gc):
    return _run("3. Feature Extraction", gc, JOB_CONFIGS['3'])


def test_label_transfer(gc):
    """Requires COUNTS_FILE_ID to be set in CONFIG."""
    return _run(
        "4. Label Transfer (10X Visium - step 1)", gc, JOB_CONFIGS['4'],
        extra_params={'counts_file': COUNTS_FILE_ID}
    )


def test_spot_annotation(gc):
    """Run AFTER test_label_transfer — uses the integrated RDS uploaded to the item."""
    return _run("5. Spot Annotation (10X Visium - step 2)", gc, JOB_CONFIGS['5'])


def test_ftu_spot_aggregation(gc):
    """Requires BASE_ANNOTATION and AGG_ANNOTATION to be set in CONFIG."""
    return _run(
        "6. FTU Spot Aggregation", gc, JOB_CONFIGS['6'],
        extra_params={
            'base_annotation': BASE_ANNOTATION,
            'agg_annotation': AGG_ANNOTATION
        }
    )


# ── Job menu ───────────────────────────────────────────────────────────────────

MENU = {
    '1': ('Multi-Compartment Segmentation',       test_multi_compartment_segmentation),
    '2': ('Frozen Glomerulus Segmentation',        test_frozen_glomerulus_segmentation),
    '3': ('Feature Extraction',                    test_feature_extraction),
    '4': ('Label Transfer (10X Visium - step 1)',  test_label_transfer),
    '5': ('Spot Annotation (10X Visium - step 2)', test_spot_annotation),
    '6': ('FTU Spot Aggregation',                  test_ftu_spot_aggregation),
}

if __name__ == '__main__':
    gc = connect()

    print("Select the analysis to run:")
    for key, (name, _) in MENU.items():
        print(f"  {key}. {name}")
    print("  all. Run all sequentially")

    choice = input("\nEnter choice: ").strip().lower()

    if choice == 'all':
        for _, fn in MENU.values():
            fn(gc)
    elif choice in MENU:
        MENU[choice][1](gc)
    else:
        print(f"Invalid choice '{choice}'. Enter 1–6 or 'all'.")
