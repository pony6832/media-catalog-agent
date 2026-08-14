from pathlib import Path

from media_catalog.database import CatalogDatabase
from media_catalog.scanner import scan


def test_scan_recurses_ignores_unsupported_and_does_not_duplicate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "media"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "photo.jpg").write_bytes(b"photo")
    (root / "clip.mp4").write_bytes(b"video")
    (root / "notes.txt").write_text("ignore", encoding="utf-8")
    database = CatalogDatabase(tmp_path / "catalog.sqlite")

    first = scan(root, database)
    second = scan(root, database)

    assert first.discovered == 2
    assert first.supported == 2
    assert first.unsupported == 1
    assert second.discovered == 0
    assert {record.path.name for record in database.list_records()} == {
        "photo.jpg",
        "clip.mp4",
    }


def test_scan_rejects_a_missing_root(tmp_path: Path) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")

    try:
        scan(tmp_path / "missing", database)
    except FileNotFoundError as error:
        assert error.filename == str((tmp_path / "missing").resolve())
    else:
        raise AssertionError("scan should reject a missing root")

