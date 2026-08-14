from pathlib import Path

from openpyxl import load_workbook

from media_catalog.bootstrap import bootstrap_workspace
from media_catalog.cli import main
from media_catalog.database import CatalogDatabase
from media_catalog.inference import Analysis
from media_catalog.models import Status


class SuccessfulAnalyzer:
    def analyze(self, _: Path) -> Analysis:
        return Analysis("賀卡預覽", ("紅色",), ("賀卡",))


def _catalog_root_with_one_pending_photo(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    (root / "photo.jpg").write_bytes(b"photo")
    bootstrap_workspace(root)
    return root


def test_cli_start_prints_machine_readable_ready_marker(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "clip.mp4").write_bytes(b"video")

    exit_code = main(["start", str(root)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "MEDIA_CATALOG_READY" in output
    assert "added=1" in output
    assert str(root / "媒體整理成果" / "媒體清冊.xlsx") in output


def test_cli_reports_invalid_root_without_creating_fallback(
    tmp_path: Path, capsys
) -> None:
    missing = tmp_path / "missing"

    exit_code = main(["start", str(missing)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "MEDIA_CATALOG_ERROR" in captured.err
    assert not missing.exists()


def test_cli_analyze_all_prints_fixed_summary(tmp_path: Path, capsys) -> None:
    root = _catalog_root_with_one_pending_photo(tmp_path)

    exit_code = main(
        [
            "analyze-all",
            str(root),
            "--skill-root",
            str(tmp_path / "media-inventory"),
        ],
        runtime_builder=lambda **_kwargs: SuccessfulAnalyzer(),
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.startswith(
        "MEDIA_ANALYSIS_READY analyzed=1 failed=0 skipped=0 remaining=0"
    )
    assert f"catalog={root / '媒體整理成果' / '媒體清冊.xlsx'}" in captured.out


def test_cli_reports_runtime_preflight_failure(tmp_path: Path, capsys) -> None:
    root = _catalog_root_with_one_pending_photo(tmp_path)

    def unavailable(**_kwargs):
        from media_catalog.analysis_runtime import RuntimePreflightError

        raise RuntimePreflightError("找不到模型")

    exit_code = main(
        ["analyze-all", str(root), "--skill-root", str(tmp_path)],
        runtime_builder=unavailable,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "MEDIA_ANALYSIS_ERROR 找不到模型" in captured.err


def test_cli_resume_processing_requeues_only_interrupted_items(
    tmp_path: Path, capsys
) -> None:
    root = _catalog_root_with_one_pending_photo(tmp_path)
    workspace = bootstrap_workspace(root).workspace
    database = CatalogDatabase(workspace.database_path)
    record = database.list_records()[0]
    database.set_status(record.id, Status.PROCESSING)

    exit_code = main(["resume-processing", str(root)])

    assert exit_code == 0
    assert "MEDIA_ANALYSIS_RESUMED count=1" in capsys.readouterr().out
    assert database.get_record(record.id).status is Status.PENDING
    workbook = load_workbook(workspace.excel_path, read_only=True)
    assert workbook.active.cell(2, 1).value == "待處理"
    workbook.close()


def test_cli_retry_failed_requeues_failed_items(tmp_path: Path, capsys) -> None:
    root = _catalog_root_with_one_pending_photo(tmp_path)
    workspace = bootstrap_workspace(root).workspace
    database = CatalogDatabase(workspace.database_path)
    record = database.list_records()[0]
    database.set_status(record.id, Status.FAILED, error="invalid JSON")

    exit_code = main(["retry-failed", str(root)])

    assert exit_code == 0
    assert "MEDIA_ANALYSIS_RETRY_QUEUED count=1" in capsys.readouterr().out
    assert database.get_record(record.id).status is Status.PENDING


def test_cli_verify_sources_reports_success_and_mismatch(
    tmp_path: Path, capsys
) -> None:
    root = _catalog_root_with_one_pending_photo(tmp_path)

    assert main(["verify-sources", str(root)]) == 0
    first = capsys.readouterr()
    assert first.out == "MEDIA_SOURCES_VERIFIED total=1\n"
    (root / "photo.jpg").write_bytes(b"changed")

    assert main(["verify-sources", str(root)]) == 2
    second = capsys.readouterr()
    assert second.out == ""
    assert "MEDIA_ANALYSIS_ERROR" in second.err
