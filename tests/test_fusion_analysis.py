"""
Tests for the job status tracking helpers in fusion/plugins/plugins.py.

Fully offline — no Girder connection, no Slurm cluster needed.
time.sleep is patched so tests run instantly.

Run:
    /opt/anaconda3/bin/python tests/test_fusion_analysis.py
"""

import sys
import os
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fusion.plugins.plugins import _track_fusion_jobs, _track_slurm_job

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_girder_responses(*statuses):
    """Return a list of gc.get side_effect dicts for the given status codes."""
    return [{'status': s} for s in statuses]


def _squeue_then_sacct(squeue_sequence, sacct_output):
    """
    Build a subprocess.run side_effect that returns squeue values in order,
    then returns sacct_output on the next call.
    """
    results = [MagicMock(stdout=s) for s in squeue_sequence]
    results.append(MagicMock(stdout=sacct_output))   # sacct call at the end
    return results

# ── _track_fusion_jobs tests ───────────────────────────────────────────────────

def test_fusion_job_success():
    """Job goes QUEUED → RUNNING → SUCCESS. Should poll 3 times and exit."""
    gc = MagicMock()
    gc.get.side_effect = _make_girder_responses(1, 2, 3)

    job_results = [{'job_id': 'abc123', 'file_name': 'slide1.tif'}]

    with patch('fusion.plugins.plugins.time.sleep'):
        _track_fusion_jobs(gc, job_results, 'Test Analysis', poll_interval=0)

    assert gc.get.call_count == 3
    gc.get.assert_called_with('job/abc123')
    print("  PASSED: fusion job success (3 polls)")


def test_fusion_job_failure():
    """Job goes QUEUED → RUNNING → FAILED. Should exit on status 4."""
    gc = MagicMock()
    gc.get.side_effect = _make_girder_responses(1, 2, 4)

    job_results = [{'job_id': 'def456', 'file_name': 'slide2.tif'}]

    with patch('fusion.plugins.plugins.time.sleep'):
        _track_fusion_jobs(gc, job_results, 'Test Analysis', poll_interval=0)

    assert gc.get.call_count == 3
    print("  PASSED: fusion job failure detected correctly")


def test_fusion_multiple_jobs():
    """Two jobs: first finishes fast, second takes longer."""
    gc = MagicMock()
    # Poll sequence — both jobs are queried each cycle:
    # Cycle 1: job1=RUNNING, job2=QUEUED
    # Cycle 2: job1=SUCCESS, job2=RUNNING
    # Cycle 3: (job1 already done) job2=SUCCESS
    gc.get.side_effect = [
        {'status': 2},   # cycle 1: job1 RUNNING
        {'status': 1},   # cycle 1: job2 QUEUED
        {'status': 3},   # cycle 2: job1 SUCCESS
        {'status': 2},   # cycle 2: job2 RUNNING
        {'status': 3},   # cycle 3: job2 SUCCESS
    ]

    job_results = [
        {'job_id': 'aaa', 'file_name': 'slide_A.tif'},
        {'job_id': 'bbb', 'file_name': 'slide_B.tif'},
    ]

    with patch('fusion.plugins.plugins.time.sleep'):
        _track_fusion_jobs(gc, job_results, 'Multi-file Test', poll_interval=0)

    assert gc.get.call_count == 5
    print("  PASSED: two fusion jobs tracked correctly (5 total polls)")


def test_fusion_skips_null_job_ids():
    """Jobs with job_id=None (failed submissions) should be ignored."""
    gc = MagicMock()
    gc.get.side_effect = _make_girder_responses(3)

    job_results = [
        {'job_id': None,    'file_name': 'failed_submit.tif'},
        {'job_id': 'xyz99', 'file_name': 'good_slide.tif'},
    ]

    with patch('fusion.plugins.plugins.time.sleep'):
        _track_fusion_jobs(gc, job_results, 'Test', poll_interval=0)

    # Only the valid job should be polled
    assert gc.get.call_count == 1
    gc.get.assert_called_once_with('job/xyz99')
    print("  PASSED: null job_id skipped correctly")


def test_fusion_empty_job_list():
    """No trackable jobs — should return immediately without calling gc."""
    gc = MagicMock()

    with patch('fusion.plugins.plugins.time.sleep'):
        _track_fusion_jobs(gc, [], 'Empty', poll_interval=0)

    gc.get.assert_not_called()
    print("  PASSED: empty job list returns immediately")


# ── _track_slurm_job tests ─────────────────────────────────────────────────────

def test_slurm_job_completed():
    """Job goes PENDING → RUNNING → COMPLETED."""
    side_effects = _squeue_then_sacct(
        squeue_sequence=['PD\n', 'R\n', '\n'],   # 3 squeue calls, then empty → left queue
        sacct_output='COMPLETED      \n'
    )

    with patch('subprocess.run', side_effect=side_effects), \
         patch('fusion.plugins.plugins.time.sleep'):
        _track_slurm_job('11111', 'test_job', '/logs/test_job_11111.log', poll_interval=0)

    print("  PASSED: slurm job COMPLETED path")


def test_slurm_job_failed():
    """Job goes PENDING → RUNNING → FAILED."""
    side_effects = _squeue_then_sacct(
        squeue_sequence=['PD\n', 'R\n', '\n'],
        sacct_output='FAILED      \n'
    )

    with patch('subprocess.run', side_effect=side_effects), \
         patch('fusion.plugins.plugins.time.sleep'):
        _track_slurm_job('22222', 'test_job', '/logs/test_job_22222.log', poll_interval=0)

    print("  PASSED: slurm job FAILED path")


def test_slurm_job_completing_state():
    """Job goes through CG (completing) state before leaving queue."""
    side_effects = _squeue_then_sacct(
        squeue_sequence=['PD\n', 'R\n', 'CG\n', '\n'],
        sacct_output='COMPLETED      \n'
    )

    with patch('subprocess.run', side_effect=side_effects), \
         patch('fusion.plugins.plugins.time.sleep'):
        _track_slurm_job('33333', 'test_job', '/logs/test_job_33333.log', poll_interval=0)

    print("  PASSED: slurm job CG (completing) state handled")


def test_slurm_job_timeout():
    """Job times out."""
    side_effects = _squeue_then_sacct(
        squeue_sequence=['PD\n', 'R\n', '\n'],
        sacct_output='TIMEOUT      \n'
    )

    with patch('subprocess.run', side_effect=side_effects), \
         patch('fusion.plugins.plugins.time.sleep'):
        _track_slurm_job('44444', 'test_job', '/logs/test_job_44444.log', poll_interval=0)

    print("  PASSED: slurm job TIMEOUT path")


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    ("fusion: success (queued→running→done)",      test_fusion_job_success),
    ("fusion: failure detected",                    test_fusion_job_failure),
    ("fusion: two files tracked",                   test_fusion_multiple_jobs),
    ("fusion: null job_id skipped",                 test_fusion_skips_null_job_ids),
    ("fusion: empty job list",                      test_fusion_empty_job_list),
    ("slurm:  COMPLETED path",                      test_slurm_job_completed),
    ("slurm:  FAILED path",                         test_slurm_job_failed),
    ("slurm:  CG state handled",                    test_slurm_job_completing_state),
    ("slurm:  TIMEOUT path",                        test_slurm_job_timeout),
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
