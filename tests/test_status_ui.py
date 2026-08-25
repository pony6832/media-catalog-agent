from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from media_catalog.run_state import AnalysisRun
from media_catalog.analysis_mode import AnalysisMode
from media_catalog.bootstrap import bootstrap_workspace
from media_catalog.force_gemini import ForceGeminiEstimate
from media_catalog.supervisor import SupervisorSnapshot
import media_catalog.status_ui as status_ui
from media_catalog.status_ui import (
    StatusApplication,
    StatusViewModel,
    format_force_confirmation,
    validate_force_environment,
)
from media_catalog.workspace import MediaWorkspace


class RecordingSupervisor:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []
        self.analysis_calls: list[tuple[Path, Path]] = []
        self.is_busy = False

    def start_catalog(self, root: Path, skill_root: Path) -> int:
        self.calls.append((root, skill_root))
        return 1

    def start(
        self,
        root: Path,
        skill_root: Path,
        *,
        mode: AnalysisMode = AnalysisMode.AUTO,
    ) -> int:
        self.analysis_calls.append((root, skill_root))
        return 2


class RecordingRoot:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []

    def after(self, delay: int, callback) -> None:
        self.after_calls.append((delay, callback))


def sample_run(
    *,
    heartbeat_age: int = 5,
    total: int = 100,
    completed: int = 55,
    videos: int = 72,
    images: int = 28,
    status: str = "running",
    excel_sync_pending: bool = False,
    failed_media: int = 0,
    analysis_mode: AnalysisMode = AnalysisMode.AUTO,
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
        failed_media=failed_media,
        current_media_id="video-027",
        current_segment_id="video-027:5",
        worker_pid=4321,
        last_heartbeat=(now - timedelta(seconds=heartbeat_age)).isoformat(),
        stop_requested=False,
        recovery_count=0,
        excel_sync_pending=excel_sync_pending,
        analysis_mode=analysis_mode,
    )


def test_idle_view_model_has_no_selected_root_or_progress() -> None:
    model = StatusViewModel.idle()

    assert model.light_color == "red"
    assert model.status_text == "尚未選擇資料夾"
    assert model.root_text == "尚未選擇"
    assert model.progress_text == "0 / 0"
    assert model.remaining_text == "0"


def test_cancel_selection_does_not_create_results_or_start_process(
    tmp_path: Path,
) -> None:
    supervisor = RecordingSupervisor()

    workspace, error = status_ui.begin_selected_root(
        "", supervisor, tmp_path / "skill"
    )

    assert workspace is None
    assert error == ""
    assert supervisor.calls == []
    assert list(tmp_path.iterdir()) == []


def test_valid_selection_starts_catalog_without_creating_results_in_ui(
    tmp_path: Path,
) -> None:
    root = tmp_path / "中文 & media"
    root.mkdir()
    supervisor = RecordingSupervisor()

    workspace, error = status_ui.begin_selected_root(
        str(root), supervisor, tmp_path / "skill"
    )

    assert error == ""
    assert workspace is not None
    assert supervisor.calls == [
        (root.resolve(), (tmp_path / "skill").resolve())
    ]
    assert not (root / "媒體整理成果").exists()


def test_invalid_selection_returns_actionable_error_without_starting(
    tmp_path: Path,
) -> None:
    supervisor = RecordingSupervisor()

    workspace, error = status_ui.begin_selected_root(
        str(tmp_path / "missing"), supervisor, tmp_path / "skill"
    )

    assert workspace is None
    assert "找不到資料夾" in error
    assert supervisor.calls == []


@pytest.mark.parametrize(
    (
        "has_workspace",
        "has_outputs",
        "busy",
        "select",
        "start",
        "open_outputs",
    ),
    [
        (False, False, False, True, False, False),
        (True, False, True, False, False, False),
        (True, True, True, False, False, True),
        (True, True, False, True, True, True),
    ],
)
def test_control_state_prevents_root_switch_while_busy(
    has_workspace: bool,
    has_outputs: bool,
    busy: bool,
    select: bool,
    start: bool,
    open_outputs: bool,
) -> None:
    state = status_ui.ControlState.from_context(
        has_workspace=has_workspace,
        has_outputs=has_outputs,
        busy=busy,
    )

    assert state.select_enabled is select
    assert state.start_enabled is start
    assert state.open_outputs_enabled is open_outputs


def test_start_retries_catalog_when_selected_workspace_has_no_outputs(
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    supervisor = RecordingSupervisor()
    app = object.__new__(status_ui.StatusApplication)
    app.workspace = MediaWorkspace.from_root(media_root)
    app.media_root = media_root.resolve()
    app.skill_root = (tmp_path / "skill").resolve()
    app.supervisor = supervisor
    app.root = RecordingRoot()

    app._start()

    assert supervisor.calls == [(media_root.resolve(), app.skill_root)]
    assert supervisor.analysis_calls == []


def test_cataloging_snapshot_renders_before_a_database_run_exists(
    tmp_path: Path,
) -> None:
    app = object.__new__(status_ui.StatusApplication)
    app.media_root = (tmp_path / "media").resolve()
    snapshot = SupervisorSnapshot("cataloging", True, None)

    model = app._view_model(snapshot)

    assert model.status_text == "正在建立／更新清冊"
    assert model.root_text == str(app.media_root)
    assert model.progress_text == "0 / 0"


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


def test_force_environment_requires_key_and_exact_model() -> None:
    assert "API Key" in validate_force_environment({})
    assert validate_force_environment(
        {"GEMINI_API_KEY": "configured"}
    ) is None
    assert "gemini-3.7-flash" in validate_force_environment(
        {
            "GEMINI_API_KEY": "configured",
            "GEMINI_MODEL": "gemini-other",
        }
    )
    assert (
        validate_force_environment(
            {
                "GEMINI_API_KEY": "configured",
                "GEMINI_MODEL": "gemini-3.7-flash",
            }
        )
        is None
    )


def test_force_confirmation_shows_normal_and_retry_limits() -> None:
    text = format_force_confirmation(ForceGeminiEstimate(2, 3, 4, 27, 54))

    assert "影片：2" in text
    assert "照片：3" in text
    assert "已審核：4" in text
    assert "正常強化請求上限：27" in text
    assert "含重試的最壞上限：54" in text
    assert "完整影片" in text


def test_force_view_model_reports_nonfatal_fallback_count() -> None:
    model = StatusViewModel.from_run(
        sample_run(
            failed_media=2,
            analysis_mode=AnalysisMode.FORCE_GEMINI,
        ),
        worker_alive=True,
        supervisor_status="running",
        now=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
    )

    assert model.status_text == "Gemini 強制強化中"
    assert model.failure_text == "失敗／降級：2"


def _force_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[status_ui.StatusApplication, Mock, Mock]:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "photo.jpg").write_bytes(b"photo")
    workspace = bootstrap_workspace(media_root).workspace
    supervisor = Mock()
    supervisor.is_busy = False
    messagebox = Mock()
    app = StatusApplication.__new__(StatusApplication)
    app.media_root = media_root.resolve()
    app.workspace = workspace
    app.skill_root = tmp_path.resolve()
    app.supervisor = supervisor
    app.messagebox = messagebox
    monkeypatch.setenv("GEMINI_API_KEY", "configured-for-test")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")
    return app, supervisor, messagebox


def test_force_button_confirms_then_starts_force_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, supervisor, messagebox = _force_app(tmp_path, monkeypatch)
    messagebox.askokcancel.return_value = True

    app._start_force_gemini()

    assert (
        supervisor.start.call_args.kwargs["mode"]
        is AnalysisMode.FORCE_GEMINI
    )
    assert (
        "正常強化請求上限"
        in messagebox.askokcancel.call_args.args[1]
    )


def test_cancelled_force_confirmation_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, supervisor, messagebox = _force_app(tmp_path, monkeypatch)
    messagebox.askokcancel.return_value = False

    app._start_force_gemini()

    supervisor.start.assert_not_called()


def test_force_button_blocks_when_reviewed_workbook_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, supervisor, messagebox = _force_app(tmp_path, monkeypatch)
    app.workspace.excel_path.write_bytes(b"not-an-excel-workbook")

    app._start_force_gemini()

    supervisor.start.assert_not_called()
    assert messagebox.showerror.called


def test_catalog_ready_view_shows_inventory_counts_and_size(
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "photo.jpg").write_bytes(b"photo")
    (media_root / "clip.mp4").write_bytes(b"video-data")
    workspace = bootstrap_workspace(media_root).workspace
    app = object.__new__(status_ui.StatusApplication)
    app.media_root = media_root.resolve()
    app.workspace = workspace
    snapshot = SupervisorSnapshot("catalog_ready", False, None)

    model = app._view_model(snapshot)

    assert model.status_text == "清冊就緒，請選擇分析模式"
    assert model.image_count == 1
    assert model.video_count == 1
    assert model.total_size_text == "15 B"
