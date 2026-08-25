from __future__ import annotations

import threading
import time

import pytest

from media_catalog.inference import AnalysisError
from media_catalog.stage_runner import HeartbeatThread, StagePolicy, StageRunner


class AlwaysFails:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0
        self.timeouts: list[float] = []

    def __call__(self, timeout_seconds: float) -> None:
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        raise self.error


class FailsOnce:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, timeout_seconds: float) -> str:
        self.calls += 1
        if self.calls == 1:
            raise AnalysisError("temporary failure")
        return f"done:{timeout_seconds}"


class RecordingStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[tuple[str, int | None]] = []

    def heartbeat(self, run_id: str, worker_pid: int | None = None) -> None:
        with self._lock:
            self.calls.append((run_id, worker_pid))

    @property
    def heartbeat_count(self) -> int:
        with self._lock:
            return len(self.calls)


def test_stage_runner_retries_once_then_returns_failure() -> None:
    operation = AlwaysFails(AnalysisError("bad frame"))

    result = StageRunner().run(
        "local_analysis", operation, StagePolicy(600, 1)
    )

    assert operation.calls == 2
    assert operation.timeouts == [600, 600]
    assert result.ok is False
    assert result.attempts == 2
    assert result.error_type == "AnalysisError"
    assert result.error_message == "bad frame"


def test_stage_runner_returns_value_after_one_retry() -> None:
    operation = FailsOnce()

    result = StageRunner().run("ffmpeg", operation, StagePolicy(1200, 1))

    assert result.ok is True
    assert result.value == "done:1200"
    assert result.attempts == 2
    assert result.error_type is None


@pytest.mark.parametrize(
    "policy",
    (StagePolicy(0, 1), StagePolicy(1, -1)),
)
def test_stage_policy_rejects_non_positive_timeout_or_negative_retries(
    policy: StagePolicy,
) -> None:
    with pytest.raises(ValueError):
        StageRunner().run("invalid", lambda _: None, policy)


def test_heartbeat_updates_while_stage_waits_and_stops_after_context() -> None:
    store = RecordingStore()

    with HeartbeatThread(
        store, "run-1", worker_pid=987, interval_seconds=0.01
    ):
        time.sleep(0.045)

    count_after_exit = store.heartbeat_count
    time.sleep(0.025)
    assert count_after_exit >= 3
    assert store.heartbeat_count == count_after_exit
    assert set(store.calls) == {("run-1", 987)}
