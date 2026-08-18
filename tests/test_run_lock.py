from multiprocessing import get_context
from pathlib import Path

import pytest

from media_catalog.run_lock import AnalysisAlreadyRunningError, analysis_run_lock


def _hold_analysis_lock(lock_path: str, ready, release) -> None:
    with analysis_run_lock(Path(lock_path)):
        ready.set()
        release.wait(10)


def test_analysis_run_lock_rejects_a_second_batch_for_the_same_catalog(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "analysis.lock"

    with analysis_run_lock(lock_path):
        with pytest.raises(AnalysisAlreadyRunningError):
            with analysis_run_lock(lock_path):
                pass

    with analysis_run_lock(lock_path):
        pass


def test_analysis_run_lock_excludes_another_windows_process(
    tmp_path: Path,
) -> None:
    context = get_context("spawn")
    ready = context.Event()
    release = context.Event()
    lock_path = tmp_path / "analysis.lock"
    process = context.Process(
        target=_hold_analysis_lock,
        args=(str(lock_path), ready, release),
    )
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(AnalysisAlreadyRunningError):
            with analysis_run_lock(lock_path):
                pass
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)

    assert process.exitcode == 0
    with analysis_run_lock(lock_path):
        pass
