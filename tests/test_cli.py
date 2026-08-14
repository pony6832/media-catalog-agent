from pathlib import Path

from media_catalog.cli import main


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
