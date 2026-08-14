from pathlib import Path

from media_catalog.bootstrap import bootstrap_workspace


def test_bootstrap_creates_catalog_below_pasted_root(tmp_path: Path) -> None:
    root = tmp_path / "家庭照片影片"
    root.mkdir()
    source = root / "旅行.jpg"
    source.write_bytes(b"photo")

    result = bootstrap_workspace(root)

    assert result.workspace.database_path.is_file()
    assert result.workspace.excel_path.is_file()
    assert result.scan.discovered == 1
    assert result.total_records == 1
    assert source.read_bytes() == b"photo"


def test_bootstrap_is_idempotent_and_ignores_its_outputs(tmp_path: Path) -> None:
    root = tmp_path / "家庭照片影片"
    root.mkdir()
    (root / "旅行.jpg").write_bytes(b"photo")

    first = bootstrap_workspace(root)
    second = bootstrap_workspace(root)

    assert (first.scan.discovered, first.scan.existing) == (1, 0)
    assert (second.scan.discovered, second.scan.existing) == (0, 1)
    assert second.total_records == 1

