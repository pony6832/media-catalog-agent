from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from media_catalog.database import CatalogDatabase
from media_catalog.schema_migration import LATEST_SCHEMA_VERSION, ensure_a_plus_schema


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _sqlite_integrity(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute("PRAGMA integrity_check").fetchone()[0]


def _make_legacy_catalog(tmp_path: Path) -> tuple[Path, Path]:
    result_root = tmp_path / "媒體整理成果"
    database_path = result_root / "catalog.sqlite"
    excel_path = result_root / "媒體清冊.xlsx"
    database = CatalogDatabase(database_path)
    database.upsert_discovered(tmp_path / "photo.jpg", "abc123", "image")
    excel_path.write_bytes(b"legacy-excel")
    return database_path, excel_path


def test_migration_backs_up_database_and_excel_before_schema_change(
    tmp_path: Path,
) -> None:
    database_path, excel_path = _make_legacy_catalog(tmp_path)
    before_excel = _sha256(excel_path)
    fixed = datetime(2026, 8, 25, 14, 30, tzinfo=timezone.utc)

    result = ensure_a_plus_schema(
        database_path, excel_path, now=lambda: fixed
    )

    assert result.migrated is True
    assert result.backup_dir is not None
    assert result.backup_dir.name == "20260825-143000"
    assert _sha256(result.backup_dir / "媒體清冊.xlsx") == before_excel
    assert _sqlite_integrity(result.backup_dir / "catalog.sqlite") == "ok"
    with sqlite3.connect(result.backup_dir / "catalog.sqlite") as backup:
        legacy_row = backup.execute(
            "SELECT fingerprint, media_type FROM media_records"
        ).fetchone()
    assert legacy_row == ("abc123", "image")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"schema_version", "analysis_runs", "video_segments"} <= tables
    with sqlite3.connect(database_path) as connection:
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(analysis_runs)")
        }
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert {"analysis_mode", "force_generation", "force_prepared"} <= run_columns
    assert version == LATEST_SCHEMA_VERSION == 2


def test_migration_is_idempotent(tmp_path: Path) -> None:
    database_path, excel_path = _make_legacy_catalog(tmp_path)

    first = ensure_a_plus_schema(database_path, excel_path)
    second = ensure_a_plus_schema(database_path, excel_path)

    assert first.migrated is True
    assert second.migrated is False
    assert second.backup_dir is None
    backup_root = database_path.parent / "APlus備份"
    assert len([path for path in backup_root.iterdir() if path.is_dir()]) == 1


def test_catalog_database_exposes_opt_in_a_plus_migration(
    tmp_path: Path,
) -> None:
    database_path, excel_path = _make_legacy_catalog(tmp_path)
    database = CatalogDatabase(database_path)

    result = database.prepare_a_plus_schema(excel_path)

    assert result.migrated is True
    assert result.backup_dir is not None
