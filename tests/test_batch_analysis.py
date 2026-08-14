from collections.abc import Sequence
from pathlib import Path

import pytest
from openpyxl import load_workbook

from media_catalog.batch_analysis import BatchAnalysisResult, analyze_pending
from media_catalog.database import CatalogDatabase
from media_catalog.inference import Analysis, AnalysisError
from media_catalog.models import Status
from media_catalog.scanner import scan
from media_catalog.source_guard import capture_source
from media_catalog.workspace import MediaWorkspace


class PathAwareAnalyzer:
    def __init__(self, fail_name: str | None = None) -> None:
        self.fail_name = fail_name
        self.sources: list[Path] = []

    def analyze(self, source: Path) -> Analysis:
        self.sources.append(source)
        if source.name == self.fail_name:
            raise AnalysisError("invalid image")
        return Analysis("賀卡預覽", ("紅色",), ("賀卡",))


def _workspace_with_media(
    tmp_path: Path, names: Sequence[str]
) -> MediaWorkspace:
    root = tmp_path / "media"
    root.mkdir()
    for name in names:
        (root / name).write_bytes(name.encode("utf-8"))
    workspace = MediaWorkspace.from_root(root)
    workspace.ensure_directories()
    database = CatalogDatabase(workspace.database_path)
    scan(workspace.root, database, excluded_roots=(workspace.result_root,))
    return workspace


def test_analyze_pending_continues_after_one_item_fails_and_updates_excel(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_media(tmp_path, ("bad.jpg", "good.jpg"))

    result = analyze_pending(
        workspace, PathAwareAnalyzer(fail_name="bad.jpg")
    )

    database = CatalogDatabase(workspace.database_path)
    records = {record.path.name: record for record in database.list_records()}
    assert result == BatchAnalysisResult(
        analyzed=1, failed=1, skipped=0, remaining=0
    )
    assert records["bad.jpg"].status is Status.FAILED
    assert records["good.jpg"].status is Status.ANALYZED
    workbook = load_workbook(workspace.excel_path, read_only=True)
    statuses = [
        row[0]
        for row in workbook.active.iter_rows(min_row=2, values_only=True)
    ]
    workbook.close()
    assert statuses == ["失敗", "待確認"]


def test_analyze_pending_skips_analyzed_and_failed_records_on_rerun(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_media(tmp_path, ("bad.jpg", "good.jpg"))
    first_analyzer = PathAwareAnalyzer(fail_name="bad.jpg")
    first = analyze_pending(workspace, first_analyzer)
    second_analyzer = PathAwareAnalyzer()

    second = analyze_pending(workspace, second_analyzer)

    assert first.analyzed == 1
    assert second == BatchAnalysisResult(
        analyzed=0, failed=0, skipped=2, remaining=0
    )
    assert second_analyzer.sources == []


def test_analyze_pending_preserves_source_snapshot(tmp_path: Path) -> None:
    workspace = _workspace_with_media(tmp_path, ("good.jpg",))
    source = workspace.root / "good.jpg"
    before = capture_source(source)

    analyze_pending(workspace, PathAwareAnalyzer())

    assert capture_source(source) == before


def test_excel_failure_stops_before_analyzing_the_next_item(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_media(tmp_path, ("first.jpg", "second.jpg"))
    analyzer = PathAwareAnalyzer()

    def locked_excel(*_args, **_kwargs):
        raise PermissionError("workbook is locked")

    with pytest.raises(PermissionError, match="locked"):
        analyze_pending(workspace, analyzer, excel_writer=locked_excel)

    statuses = [
        record.status
        for record in CatalogDatabase(workspace.database_path).list_records()
    ]
    assert statuses == [Status.PROCESSING, Status.PENDING]
    assert analyzer.sources == []
