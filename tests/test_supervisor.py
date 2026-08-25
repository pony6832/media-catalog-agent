from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from media_catalog.bootstrap import bootstrap_workspace
from media_catalog.run_state import RunStateStore, VideoSegment
from media_catalog.supervisor import WorkerSupervisor


@dataclass
class FakeProcess:
    returncode: int | None = None
    terminate_calls: int = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15


class ProcessFactory:
    def __init__(self, processes: list[FakeProcess]) -> None:
        self.processes = processes
        self.arguments: list[list[str]] = []

    def __call__(self, arguments: list[str]) -> FakeProcess:
        self.arguments.append(arguments)
        return self.processes[len(self.arguments) - 1]


def prepared_root(tmp_path: Path) -> tuple[Path, Path, RunStateStore, str]:
    root = tmp_path / "media"
    root.mkdir()
    (root / "sample.jpg").write_bytes(b"image")
    workspace = bootstrap_workspace(root).workspace
    store = RunStateStore(workspace.database_path, excel_path=workspace.excel_path)
    run = store.ensure_run(
        root_path=root, video_count=0, image_count=1, total_bytes=5
    )
    return root, workspace.database_path, store, run.run_id


def test_supervisor_launches_headless_worker_with_argument_array(
    tmp_path: Path,
) -> None:
    root, _, store, _ = prepared_root(tmp_path)
    process = FakeProcess()
    factory = ProcessFactory([process])
    supervisor = WorkerSupervisor(
        python_executable=Path(r"C:\runtime\python.exe"),
        process_factory=factory,
        store_factory=lambda _workspace: store,
    )

    pid = supervisor.start(root, Path(r"C:\skill"))

    assert pid == 1
    assert factory.arguments == [[
        r"C:\runtime\python.exe",
        "-m",
        "media_catalog.cli",
        "analyze-all",
        str(root.resolve()),
        "--skill-root",
        str(Path(r"C:\skill").resolve()),
    ]]


def test_supervisor_restarts_dead_worker_only_once(tmp_path: Path) -> None:
    root, _, store, _ = prepared_root(tmp_path)
    first = FakeProcess(returncode=1)
    second = FakeProcess(returncode=1)
    factory = ProcessFactory([first, second])
    supervisor = WorkerSupervisor(
        python_executable=Path(r"C:\runtime\python.exe"),
        process_factory=factory,
        store_factory=lambda _workspace: store,
    )

    supervisor.start(root, Path(r"C:\skill"))
    restarting = supervisor.poll()
    stopped = supervisor.poll()

    assert len(factory.arguments) == 2
    assert restarting.status == "restarting"
    assert stopped.status == "error"
    assert stopped.worker_alive is False


def test_second_worker_crash_marks_repeated_segment_failed(
    tmp_path: Path,
) -> None:
    root, _, store, run_id = prepared_root(tmp_path)
    segment = VideoSegment(
        segment_id="video-1:0",
        run_id=run_id,
        video_id="video-1",
        segment_index=0,
        start_seconds=0,
        end_seconds=10,
        status="processing",
    )
    store.upsert_segments(run_id, "video-1", (segment,))
    factory = ProcessFactory([
        FakeProcess(returncode=1),
        FakeProcess(returncode=1),
    ])
    supervisor = WorkerSupervisor(
        python_executable=Path(r"C:\runtime\python.exe"),
        process_factory=factory,
        store_factory=lambda _workspace: store,
    )

    supervisor.start(root, Path(r"C:\skill"))
    supervisor.poll()
    store.mark_segment_status("video-1:0", "processing")
    stopped = supervisor.poll()

    persisted = store.list_segments("video-1")[0]
    assert stopped.status == "error"
    assert persisted.status == "failed"
    assert persisted.crash_count == 2
    assert persisted.error == "worker_crashed_repeatedly"


def test_safe_stop_sets_request_without_terminating_worker(
    tmp_path: Path,
) -> None:
    root, _, store, run_id = prepared_root(tmp_path)
    process = FakeProcess()
    supervisor = WorkerSupervisor(
        python_executable=Path(r"C:\runtime\python.exe"),
        process_factory=ProcessFactory([process]),
        store_factory=lambda _workspace: store,
    )
    supervisor.start(root, Path(r"C:\skill"))

    supervisor.request_safe_stop()

    run = store.get_run(run_id)
    assert run is not None and run.stop_requested is True
    assert process.terminate_calls == 0


def test_worker_gets_startup_grace_before_missing_heartbeat_is_stale(
    tmp_path: Path,
) -> None:
    root, _, store, run_id = prepared_root(tmp_path)
    process = FakeProcess()
    clock = iter((100.0, 110.0))
    supervisor = WorkerSupervisor(
        python_executable=Path(r"C:\runtime\python.exe"),
        process_factory=ProcessFactory([process]),
        store_factory=lambda _workspace: store,
        monotonic=lambda: next(clock),
        heartbeat_is_fresh=lambda _run: False,
        startup_grace_seconds=30,
    )

    supervisor.start(root, Path(r"C:\skill"))
    snapshot = supervisor.poll()

    run = store.get_run(run_id)
    assert snapshot.status == "starting"
    assert run is not None and run.stop_requested is False


def test_stale_worker_gets_grace_period_before_exact_handle_is_terminated(
    tmp_path: Path,
) -> None:
    root, _, store, run_id = prepared_root(tmp_path)
    first = FakeProcess()
    second = FakeProcess()
    factory = ProcessFactory([first, second])
    clock = iter((100.0, 105.0, 111.0))
    supervisor = WorkerSupervisor(
        python_executable=Path(r"C:\runtime\python.exe"),
        process_factory=factory,
        store_factory=lambda _workspace: store,
        monotonic=lambda: next(clock),
        heartbeat_is_fresh=lambda _run: False,
        startup_grace_seconds=0,
    )
    supervisor.start(root, Path(r"C:\skill"))

    waiting = supervisor.poll()
    still_waiting = supervisor.poll()
    restarted = supervisor.poll()

    assert waiting.status == "stopping_stale_worker"
    assert still_waiting.status == "stopping_stale_worker"
    assert first.terminate_calls == 1
    assert restarted.status == "restarting"
    assert len(factory.arguments) == 2
    run = store.get_run(run_id)
    assert run is not None and run.recovery_count == 1
