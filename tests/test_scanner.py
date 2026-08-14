from pathlib import Path

import pytest

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
    assert first.existing == 0
    assert first.supported == 2
    assert first.unsupported == 1
    assert second.discovered == 0
    assert second.existing == 2
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


def test_scan_excludes_complete_result_subtree(tmp_path: Path) -> None:
    root = tmp_path / "media"
    result_root = root / "媒體整理成果"
    result_root.mkdir(parents=True)
    (root / "original.mp4").write_bytes(b"original")
    (result_root / "backup.mp4").write_bytes(b"backup")
    database = CatalogDatabase(result_root / "catalog.sqlite")

    scan_result = scan(root, database, excluded_roots=(result_root,))

    assert scan_result.discovered == 1
    assert scan_result.existing == 0
    assert [record.path.name for record in database.list_records()] == [
        "original.mp4"
    ]


def test_scan_does_not_follow_directory_links(tmp_path: Path) -> None:
    root = tmp_path / "media"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    (external / "linked.mp4").write_bytes(b"linked")
    link = root / "linked-folder"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Directory links unavailable: {error}")
    database = CatalogDatabase(tmp_path / "catalog.sqlite")

    result = scan(root, database)

    assert result.supported == 0
    assert database.list_records() == []
