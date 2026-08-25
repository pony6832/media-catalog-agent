from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from media_catalog.analysis_mode import AnalysisMode
from media_catalog.run_state import RunStateStore, VideoSegment


def test_run_state_round_trips_progress_heartbeat_and_stop_request(
    tmp_path: Path,
) -> None:
    store = RunStateStore(tmp_path / "catalog.sqlite")
    created = store.create_run(
        "run-1",
        root_path=tmp_path / "media",
        video_count=3,
        image_count=2,
        total_bytes=4096,
    )

    assert created.total_media == 5
    store.update_counts(
        "run-1",
        completed_media=2,
        failed_media=1,
        current_media_id="video-1",
        current_segment_id="video-1:0",
    )
    store.heartbeat("run-1", worker_pid=321)
    store.request_stop("run-1")
    store.set_excel_sync_pending("run-1", True)

    reopened = RunStateStore(store.path).get_run("run-1")
    assert reopened is not None
    assert reopened.completed_media == 2
    assert reopened.failed_media == 1
    assert reopened.current_media_id == "video-1"
    assert reopened.current_segment_id == "video-1:0"
    assert reopened.worker_pid == 321
    assert reopened.last_heartbeat is not None
    assert reopened.stop_requested is True
    assert reopened.excel_sync_pending is True

    store.clear_stop("run-1")
    assert store.get_run("run-1").stop_requested is False


def test_segments_checkpoint_and_requeue_stale_processing(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path / "catalog.sqlite")
    store.create_run(
        "run-1",
        root_path=tmp_path,
        video_count=1,
        image_count=0,
        total_bytes=100,
    )
    store.upsert_segments(
        "run-1",
        "video-1",
        (
            VideoSegment("video-1:0", "run-1", "video-1", 0, 0.0, 10.0),
            VideoSegment("video-1:1", "run-1", "video-1", 1, 10.0, 20.0),
        ),
    )
    store.mark_segment_status("video-1:0", "completed")
    store.mark_segment_status("video-1:1", "processing")

    assert store.requeue_stale_processing("run-1") == 1
    segments = store.list_segments("video-1")
    assert [segment.status for segment in segments] == ["completed", "pending"]
    assert segments[1].crash_count == 1


def test_gemini_slot_stops_at_twelve_across_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite"
    store = RunStateStore(database_path)

    for _ in range(12):
        assert store.consume_gemini_slot("video-1") is True

    assert RunStateStore(database_path).consume_gemini_slot("video-1") is False


def test_gemini_slot_is_atomic_across_parallel_connections(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite"
    RunStateStore(database_path)

    def consume(_: int) -> bool:
        return RunStateStore(database_path).consume_gemini_slot("video-1")

    with ThreadPoolExecutor(max_workers=8) as executor:
        granted = list(executor.map(consume, range(24)))

    assert sum(granted) == 12


def test_run_state_tracks_and_clears_current_segment(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path / "catalog.sqlite")
    store.create_run(
        "run-1",
        root_path=tmp_path,
        video_count=1,
        image_count=0,
        total_bytes=100,
    )

    store.set_current_item("run-1", "video-1", "video-1:3")
    current = store.get_run("run-1")
    assert current is not None
    assert current.current_media_id == "video-1"
    assert current.current_segment_id == "video-1:3"

    store.set_current_item("run-1", None, None)
    cleared = store.get_run("run-1")
    assert cleared is not None
    assert cleared.current_media_id is None
    assert cleared.current_segment_id is None


def test_force_run_mode_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    store = RunStateStore(path)
    run, needs_prepare = store.begin_run(
        root_path=tmp_path,
        video_count=1,
        image_count=2,
        total_bytes=30,
        mode=AnalysisMode.FORCE_GEMINI,
    )

    reopened = RunStateStore(path).get_run(run.run_id)

    assert needs_prepare is True
    assert reopened is not None
    assert reopened.analysis_mode is AnalysisMode.FORCE_GEMINI
    assert reopened.force_generation == 1
    assert reopened.force_prepared is False


def test_incomplete_force_run_is_resumed_instead_of_recreated(
    tmp_path: Path,
) -> None:
    store = RunStateStore(tmp_path / "catalog.sqlite")
    first, _ = store.begin_run(
        root_path=tmp_path,
        video_count=1,
        image_count=0,
        total_bytes=10,
        mode=AnalysisMode.FORCE_GEMINI,
    )
    store.mark_force_prepared(first.run_id)
    resumed, needs_prepare = store.begin_run(
        root_path=tmp_path,
        video_count=1,
        image_count=0,
        total_bytes=10,
        mode=AnalysisMode.FORCE_GEMINI,
    )

    assert resumed.run_id == first.run_id
    assert resumed.force_prepared is True
    assert needs_prepare is False


def test_completed_force_run_creates_the_next_generation(tmp_path: Path) -> None:
    store = RunStateStore(tmp_path / "catalog.sqlite")
    first, _ = store.begin_run(
        root_path=tmp_path,
        video_count=1,
        image_count=0,
        total_bytes=10,
        mode=AnalysisMode.FORCE_GEMINI,
    )
    store.update_counts(
        first.run_id,
        completed_media=1,
        failed_media=0,
        current_media_id=None,
        current_segment_id=None,
        status="completed",
    )

    second, needs_prepare = store.begin_run(
        root_path=tmp_path,
        video_count=1,
        image_count=0,
        total_bytes=10,
        mode=AnalysisMode.FORCE_GEMINI,
    )

    assert second.run_id != first.run_id
    assert second.force_generation == 2
    assert needs_prepare is True
