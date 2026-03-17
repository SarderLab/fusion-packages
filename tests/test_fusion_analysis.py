"""
Tests for the Slurm job tracking helpers in fusion/plugins/plugins.py.

Fully offline — no Girder connection, no Slurm cluster needed.
time.sleep is patched so tests run instantly.

Run:
    /opt/anaconda3/bin/python tests/test_fusion_analysis.py
"""

import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fusion.plugins.plugins import _track_slurm_job, _stream_slurm_log, _SLURM_JOBS

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


# ── Runner ─────────────────────────────────────────────────────────────────────

TESTS = [
    ("slurm:  detach stores job info",              test_slurm_track_detach),
    ("slurm:  stream choice delegates correctly",   test_slurm_track_stream_choice),
    ("slurm:  stream COMPLETED path",               test_slurm_stream_completed),
    ("slurm:  stream FAILED path",                  test_slurm_stream_failed),
    ("slurm:  stream CG state handled",             test_slurm_stream_completing_state),
    ("slurm:  stream TIMEOUT path",                 test_slurm_stream_timeout),
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
