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
        with patch("builtins.input", side_effect=["1", model_path]) as mock_input, \
             patch("fusion.plugins.plugins._get_apptainer_cache_lines", return_value="") as mock_cache, \
             patch("fusion.plugins.plugins.subprocess.run", side_effect=submitted) as mock_run:
            results = run_apptainer_analysis(file_paths=image_paths)

        assert mock_input.call_count == 2
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
        with patch("builtins.input", side_effect=["4", reference_path]) as mock_input, \
             patch("fusion.plugins.plugins.sanitize_h5ad_obsm") as mock_sanitize, \
             patch("fusion.plugins.plugins._get_apptainer_cache_lines", return_value="") as mock_cache, \
             patch("fusion.plugins.plugins.subprocess.run", side_effect=submitted) as mock_run:
            results = run_apptainer_analysis(file_paths=counts_paths)

        assert mock_input.call_count == 2
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
