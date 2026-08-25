from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from media_catalog.run_state import AnalysisRun
from media_catalog.status_ui import StatusViewModel


def sample_run(
    *,
    heartbeat_age: int = 5,
    total: int = 100,
    completed: int = 55,
    videos: int = 72,
    images: int = 28,
    status: str = "running",
    excel_sync_pending: bool = False,
) -> AnalysisRun:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    return AnalysisRun(
        run_id="catalog-test",
        root_path=Path(r"D:\media"),
        status=status,
        video_count=videos,
        image_count=images,
        total_bytes=20_000_000_000,
        total_media=total,
        completed_media=completed,
        failed_media=0,
        current_media_id="video-027",
        current_segment_id="video-027:5",
        worker_pid=4321,
        last_heartbeat=(now - timedelta(seconds=heartbeat_age)).isoformat(),
        stop_requested=False,
        recovery_count=0,
        excel_sync_pending=excel_sync_pending,
    )


@pytest.mark.parametrize(
    ("age", "worker_alive", "color", "status_text"),
    [
        (5, True, "green", "執行中"),
        (16, True, "red", "心跳逾時"),
        (1, False, "red", "worker 未執行"),
    ],
)
def test_view_model_uses_heartbeat_for_light(
    age: int, worker_alive: bool, color: str, status_text: str
) -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)

    model = StatusViewModel.from_run(
        sample_run(heartbeat_age=age), worker_alive=worker_alive, now=now
    )

    assert model.light_color == color
    assert model.status_text == status_text


def test_view_model_formats_progress_and_media_totals() -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)

    model = StatusViewModel.from_run(
        sample_run(),
        worker_alive=True,
        now=now,
        current_media_name="訪談片段_027.mp4",
        segment_number=6,
        segment_total=14,
        gemini_used=7,
    )

    assert model.progress_text == "55 / 100"
    assert model.progress_percent == 55
    assert model.remaining_text == "45"
    assert model.video_count == 72
    assert model.image_count == 28
    assert model.total_size_text == "18.6 GB"
    assert model.current_text == "訪談片段_027.mp4｜第 6 / 14 段"
    assert model.gemini_text == "7 / 12"


def test_view_model_prioritizes_excel_waiting_over_completed_status() -> None:
    now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)

    model = StatusViewModel.from_run(
        sample_run(
            heartbeat_age=30,
            status="incomplete",
            excel_sync_pending=True,
        ),
        worker_alive=False,
        now=now,
    )

    assert model.light_color == "red"
    assert model.status_text == "等待 Excel 關閉"
