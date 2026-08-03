"""
Tests for the Slurm job tracking helpers in fusion/plugins/plugins.py.

Fully offline — no Girder connection, no Slurm cluster needed.
time.sleep is patched so tests run instantly.

Run:
    /opt/anaconda3/bin/python tests/test_fusion_analysis.py
"""

import sys
import os
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fusion.plugins.plugins import (
    _track_slurm_job,
    _stream_slurm_log,
    _SLURM_JOBS,
    _resolve_user_path,
    _xenium_registration_orientation,
    run_apptainer_analysis,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _squeue_then_sacct(squeue_sequence, sacct_output):
    """
    Build a subprocess.run side_effect that returns squeue values in order,
    then returns sacct_output on the next call.
    """
    results = [MagicMock(stdout=s) for s in squeue_sequence]
    results.append(MagicMock(stdout=sacct_output))   # sacct call at the end
    return results

# ── _track_slurm_job tests (info box + session store + stream/detach choice) ───

def test_slurm_track_detach():
    """Choosing detach (2) stores job info and returns without streaming."""
    with patch('builtins.input', return_value='2'):
        _track_slurm_job('11111', 'test_job', '/logs/test_job_%j.log', poll_interval=0)

    assert '11111' in _SLURM_JOBS
    assert _SLURM_JOBS['11111']['name'] == 'test_job'
    assert _SLURM_JOBS['11111']['log'] == '/logs/test_job_11111.log'
    print("  PASSED: detach path stores job info and resolves %j in log path")


def test_slurm_track_stream_choice():
    """Choosing stream (1) delegates to _stream_slurm_log."""
    with patch('builtins.input', return_value='1'), \
         patch('fusion.plugins.plugins._stream_slurm_log') as mock_stream:
        _track_slurm_job('22222', 'test_job', '/logs/test_job_%j.log', poll_interval=0)

    mock_stream.assert_called_once_with('22222', 'test_job', '/logs/test_job_22222.log', 0)
    print("  PASSED: stream choice delegates to _stream_slurm_log with resolved log path")


# ── _stream_slurm_log tests (actual status + log streaming logic) ──────────────

def test_slurm_stream_completed():
    """Job goes PENDING → RUNNING → COMPLETED."""
    side_effects = _squeue_then_sacct(
        squeue_sequence=['PD\n', 'R\n', '\n'],
        sacct_output='COMPLETED      \n'
    )

    with patch('subprocess.run', side_effect=side_effects), \
         patch('fusion.plugins.plugins.time.sleep'):
        _stream_slurm_log('33333', 'test_job', None, poll_interval=0)

    print("  PASSED: stream COMPLETED path")


def test_slurm_stream_failed():
    """Job goes PENDING → RUNNING → FAILED."""
    side_effects = _squeue_then_sacct(
        squeue_sequence=['PD\n', 'R\n', '\n'],
        sacct_output='FAILED      \n'
    )

    with patch('subprocess.run', side_effect=side_effects), \
         patch('fusion.plugins.plugins.time.sleep'):
        _stream_slurm_log('44444', 'test_job', None, poll_interval=0)

    print("  PASSED: stream FAILED path")


def test_slurm_stream_completing_state():
    """Job goes through CG (completing) before leaving the queue."""
    side_effects = _squeue_then_sacct(
        squeue_sequence=['PD\n', 'R\n', 'CG\n', '\n'],
        sacct_output='COMPLETED      \n'
    )

    with patch('subprocess.run', side_effect=side_effects), \
         patch('fusion.plugins.plugins.time.sleep'):
        _stream_slurm_log('55555', 'test_job', None, poll_interval=0)

    print("  PASSED: stream CG (completing) state handled")


def test_slurm_stream_timeout():
    """Job times out."""
    side_effects = _squeue_then_sacct(
        squeue_sequence=['PD\n', 'R\n', '\n'],
        sacct_output='TIMEOUT      \n'
    )

    with patch('subprocess.run', side_effect=side_effects), \
         patch('fusion.plugins.plugins.time.sleep'):
        _stream_slurm_log('66666', 'test_job', None, poll_interval=0)

    print("  PASSED: stream TIMEOUT path")


def test_bulk_segmentation_submits_one_job_per_image():
    """Bulk image paths reuse one model prompt and create a job per image."""
    with tempfile.TemporaryDirectory() as temp_dir:
        dataset_dir = os.path.join(temp_dir, "dataset")
        image_dir = os.path.join(dataset_dir, "ometiff-pyramids")
        os.makedirs(image_dir)
        image_paths = [
            os.path.join(image_dir, "image one.tif"),
            os.path.join(image_dir, "image-two.tif"),
        ]
        model_path = os.path.join(temp_dir, "model.pth")
        for path in image_paths + [model_path]:
            open(path, "w").close()

        submitted = [
            MagicMock(stdout="Submitted batch job 70001\n"),
            MagicMock(stdout="Submitted batch job 70002\n"),
        ]
        with patch("builtins.input", side_effect=["1", "1", model_path]) as mock_input, \
             patch("fusion.plugins.plugins._get_apptainer_cache_lines", return_value="") as mock_cache, \
             patch("fusion.plugins.plugins.subprocess.run", side_effect=submitted) as mock_run:
            results = run_apptainer_analysis(file_paths=image_paths)

        assert mock_input.call_count == 3
        mock_cache.assert_called_once_with()
        assert mock_run.call_count == 2
        assert [result["input_path"] for result in results] == image_paths
        assert results[0]["script_path"].endswith(
            "multicompartment_segmentation_image_one_submit.sh"
        )
        assert results[1]["script_path"].endswith(
            "multicompartment_segmentation_image-two_submit.sh"
        )

        with open(results[0]["script_path"]) as script:
            first_script = script.read()
        assert image_paths[0] in first_script
        assert image_paths[1] not in first_script
        assert model_path in first_script
    print("  PASSED: bulk segmentation submits one job per image")


def test_bulk_label_transfer_submits_one_job_per_counts_file():
    """Non-segmentation plugins also reuse shared inputs across primary files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        counts_paths = [
            os.path.join(temp_dir, "sample_1", "counts one.h5ad"),
            os.path.join(temp_dir, "sample_2", "counts-two.RDS"),
        ]
        reference_path = os.path.join(temp_dir, "reference.h5seurat")
        for path in counts_paths:
            os.makedirs(os.path.dirname(path))
            open(path, "w").close()
        open(reference_path, "w").close()

        submitted = [
            MagicMock(stdout="Submitted batch job 71001\n"),
            MagicMock(stdout="Submitted batch job 71002\n"),
        ]
        with patch("builtins.input", side_effect=["1", "4", reference_path]) as mock_input, \
             patch("fusion.plugins.plugins.sanitize_h5ad_obsm") as mock_sanitize, \
             patch("fusion.plugins.plugins._get_apptainer_cache_lines", return_value="") as mock_cache, \
             patch("fusion.plugins.plugins.subprocess.run", side_effect=submitted) as mock_run:
            results = run_apptainer_analysis(file_paths=counts_paths)

        assert mock_input.call_count == 3
        mock_sanitize.assert_called_once_with(counts_paths[0])
        mock_cache.assert_called_once_with()
        assert mock_run.call_count == 2
        assert [result["input_path"] for result in results] == counts_paths
        for result, counts_path in zip(results, counts_paths):
            with open(result["script_path"]) as script:
                content = script.read()
            assert counts_path in content
            assert reference_path in content
    print("  PASSED: bulk label transfer reuses the shared reference")


def _submit_result(job_id="72001"):
    return MagicMock(stdout=f"Submitted batch job {job_id}\n")


def test_xenium_registration_orientations():
    """Known IU ranges select their fixed orientation; other names use the default."""
    assert _xenium_registration_orientation("HE_IU01.tif") == (2, 1, False)
    assert _xenium_registration_orientation("HE_IU20.tif") == (2, 1, False)
    assert _xenium_registration_orientation("HE_IU90.tif") == (1, 1, False)
    assert _xenium_registration_orientation("HE_IU99.tif") == (1, 1, False)
    assert _xenium_registration_orientation("HE_IU21.tif") == (2, 1, True)
    assert _xenium_registration_orientation("sample.tif") == (2, 1, True)
    print("  PASSED: Xenium registration orientation rules")


def test_xenium_relative_input_path_resolution():
    """Xenium inputs use the same absolute/relative resolver as Visium."""
    with tempfile.TemporaryDirectory() as temp_dir:
        input_dir = os.path.join(temp_dir, "fusion_demo_notebooks", "datasets", "Xenium_Reg-FE", "input")
        os.makedirs(input_dir)
        image_path = os.path.join(input_dir, "HE_IU20.tif")
        open(image_path, "w").close()
        previous_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            resolved = _resolve_user_path(
                "fusion_demo_notebooks/datasets/Xenium_Reg-FE/input/HE_IU20.tif"
            )
        finally:
            os.chdir(previous_cwd)
        assert os.path.realpath(resolved) == os.path.realpath(image_path)
    print("  PASSED: Xenium relative input path resolution")


def test_xenium_frozen_glom_script():
    """Frozen glomerulus uses the unified Xenium output/log layout and fixed defaults."""
    with tempfile.TemporaryDirectory() as temp_dir:
        input_dir = os.path.join(temp_dir, "input")
        os.makedirs(input_dir)
        image_path = os.path.join(input_dir, "sample.svs")
        model_path = os.path.join(input_dir, "model.zip")
        for path in (image_path, model_path):
            open(path, "w").close()

        with patch("builtins.input", side_effect=["2", "1", image_path, model_path]), \
             patch("fusion.plugins.plugins._get_apptainer_cache_lines", return_value=""), \
             patch("fusion.plugins.plugins.subprocess.run", return_value=_submit_result()):
            script_path = run_apptainer_analysis()

        with open(script_path) as script:
            content = script.read()
        expected_output = os.path.join(temp_dir, "output", "sample_glomeruli.json")
        assert "sarderlab/fusion2.0_decoupled:histo_cloud" in content
        assert "SegmentWSILocal.py" in content
        assert f"--outputAnnotationFile {expected_output}" in content
        assert "--output_dir" not in content
        assert "export MPLBACKEND=Agg" in content
        assert "--patch_size 2000" in content
        assert "--simplify_contours 0.005" in content
        assert "#SBATCH --time=12:00:00" in content
        assert "#SBATCH --gpus=1" in content
    print("  PASSED: Xenium frozen glomerulus script")


def test_xenium_registration_and_feature_scripts():
    """Registration and feature extraction share an image but use distinct CLIs."""
    with tempfile.TemporaryDirectory() as temp_dir:
        input_dir = os.path.join(temp_dir, "input")
        sample_output = os.path.join(temp_dir, "output", "HE_IU95")
        os.makedirs(input_dir)
        os.makedirs(sample_output)
        paths = {
            "image": os.path.join(input_dir, "HE_IU95.tif"),
            "nuc": os.path.join(input_dir, "HE_IU95_nucleus_boundaries.csv.gz"),
            "cell": os.path.join(input_dir, "HE_IU95_cell_boundaries.csv.gz"),
        }
        for path in paths.values():
            open(path, "w").close()

        with patch("builtins.input", side_effect=[
                "2", "2", paths["image"], paths["nuc"], paths["cell"]
             ]), patch("fusion.plugins.plugins._get_apptainer_cache_lines", return_value=""), \
             patch("fusion.plugins.plugins.subprocess.run", return_value=_submit_result("72002")):
            registration_script = run_apptainer_analysis()

        with open(registration_script) as script:
            registration = script.read()
        assert "registration-feature_extraction" in registration
        assert "RegistrationLocal.py" in registration
        assert "--flip 1" in registration and "--rot 1" in registration
        assert "--exp_factor 1" in registration
        assert "--downsample_factor 4" in registration
        assert f"--output_dir {os.path.join(temp_dir, 'output')}" in registration

        with patch("builtins.input", side_effect=["2", "3", sample_output]), \
             patch("fusion.plugins.plugins._get_apptainer_cache_lines", return_value=""), \
             patch("fusion.plugins.plugins.subprocess.run", return_value=_submit_result("72003")):
            feature_script = run_apptainer_analysis()

        with open(feature_script) as script:
            feature = script.read()
        assert "registration-feature_extraction" in feature
        assert "XeniumFELocal.py" in feature
        assert f"--input_path {sample_output}" in feature
        assert "--output_dir" not in feature
    print("  PASSED: Xenium registration and feature extraction scripts")


def test_xenium_bulk_registration_prompts_for_each_boundary_pair():
    """Each image in a registration batch receives its own boundary files."""
    with tempfile.TemporaryDirectory() as temp_dir:
        input_dir = os.path.join(temp_dir, "input")
        os.makedirs(input_dir)
        images = [os.path.join(input_dir, f"HE_IU0{i}.tif") for i in (1, 2)]
        nuclei = [
            os.path.join(input_dir, f"HE_IU0{i}_nucleus_boundaries.csv.gz")
            for i in (1, 2)
        ]
        cells = [
            os.path.join(input_dir, f"HE_IU0{i}_cell_boundaries.csv.gz")
            for i in (1, 2)
        ]
        for path in images + nuclei + cells:
            open(path, "w").close()

        submissions = [_submit_result("72101"), _submit_result("72102")]
        with patch("builtins.input", side_effect=[
                "2", "2", nuclei[0], cells[0], nuclei[1], cells[1]
             ]) as mock_input, \
             patch("fusion.plugins.plugins._get_apptainer_cache_lines", return_value=""), \
             patch("fusion.plugins.plugins.subprocess.run", side_effect=submissions):
            results = run_apptainer_analysis(file_paths=images)

        assert mock_input.call_count == 6
        assert len(results) == 2
        for index, result in enumerate(results):
            with open(result["script_path"]) as script:
                content = script.read()
            assert images[index] in content
            assert nuclei[index] in content
            assert cells[index] in content
            assert nuclei[1 - index] not in content
            assert cells[1 - index] not in content
    print("  PASSED: Xenium bulk registration uses per-image boundary files")


def test_xenium_add_cell_annotation_script():
    """Cell annotation prompts for both files and emits no optional arguments."""
    with tempfile.TemporaryDirectory() as temp_dir:
        sample_output = os.path.join(temp_dir, "output", "HE_IU20")
        os.makedirs(sample_output)
        features = os.path.join(sample_output, "Xenium Cells Features.json")
        groups = os.path.join(sample_output, "cell_groups.csv")
        for path in (features, groups):
            open(path, "w").close()

        with patch("builtins.input", side_effect=["2", "4", features, groups]), \
             patch("fusion.plugins.plugins._get_apptainer_cache_lines", return_value=""), \
             patch("fusion.plugins.plugins.subprocess.run", return_value=_submit_result("72004")):
            script_path = run_apptainer_analysis()

        with open(script_path) as script:
            content = script.read()
        assert "sarderlab/fusion2.0_decoupled:add_cell_annotation" in content
        assert f"--features-json-path '{features}'" in content
        assert f"--cell-groups-path {groups}" in content
        assert "--annotation-column pred.subclass.l1" in content
        assert f"--output-dir {os.path.join(temp_dir, 'output')}" in content
        assert "custom-annots" not in content
        assert "colors-path" not in content
        assert "#SBATCH --gpus" not in content
    print("  PASSED: Xenium add-cell-annotation script")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    ("slurm:  detach stores job info",              test_slurm_track_detach),
    ("slurm:  stream choice delegates correctly",   test_slurm_track_stream_choice),
    ("slurm:  stream COMPLETED path",               test_slurm_stream_completed),
    ("slurm:  stream FAILED path",                  test_slurm_stream_failed),
    ("slurm:  stream CG state handled",             test_slurm_stream_completing_state),
    ("slurm:  stream TIMEOUT path",                 test_slurm_stream_timeout),
    ("slurm:  bulk segmentation submission",        test_bulk_segmentation_submits_one_job_per_image),
    ("slurm:  bulk label transfer submission",       test_bulk_label_transfer_submits_one_job_per_counts_file),
    ("xenium: registration orientation",              test_xenium_registration_orientations),
    ("xenium: relative input path",                    test_xenium_relative_input_path_resolution),
    ("xenium: frozen glomerulus script",               test_xenium_frozen_glom_script),
    ("xenium: registration and feature scripts",       test_xenium_registration_and_feature_scripts),
    ("xenium: bulk registration boundary pairs",       test_xenium_bulk_registration_prompts_for_each_boundary_pair),
    ("xenium: add cell annotation script",             test_xenium_add_cell_annotation_script),
]

if __name__ == '__main__':
    passed, failed = 0, 0
    for label, fn in TESTS:
        print(f"\n{label}")
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  FAILED: {e}")
            failed += 1

    print(f"\n{'='*55}")
    print(f"Results: {passed} passed, {failed} failed out of {len(TESTS)} tests")
    print("=" * 55)
