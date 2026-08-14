import os
from pathlib import Path

import pytest

from media_catalog.database import CatalogDatabase
from media_catalog.source_guard import (
    SourceIntegrityError,
    capture_source,
    sanitize_error,
    verify_record_source,
)


ABCD_SHA256 = (
    "88d4266fd4e6338d13b845fcf289579d209c897823b9217da3e161936f031589"
)


def _record_for(database: CatalogDatabase, source: Path):
    return database.upsert_discovered(source, ABCD_SHA256, "image/jpeg")


def test_source_guard_accepts_an_unchanged_source(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"abcd")
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    record = _record_for(database, source)

    snapshot = capture_source(source)

    assert snapshot.sha256 == ABCD_SHA256
    assert verify_record_source(record, snapshot) == snapshot


def test_source_guard_detects_content_change_even_when_size_is_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"abcd")
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    record = _record_for(database, source)
    snapshot = capture_source(source)
    stat_result = source.stat()
    source.write_bytes(b"wxyz")
    os.utime(source, ns=(stat_result.st_atime_ns, snapshot.mtime_ns))

    with pytest.raises(SourceIntegrityError, match="指紋"):
        verify_record_source(record, snapshot)


def test_source_guard_reports_a_disappeared_file(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"abcd")
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    record = _record_for(database, source)
    snapshot = capture_source(source)
    source.unlink()

    with pytest.raises(SourceIntegrityError, match="找不到來源媒體"):
        verify_record_source(record, snapshot)


def test_sanitize_error_redacts_secrets_and_caps_diagnostics() -> None:
    environment = {"OPENAI_API_KEY": "test-secret-value"}
    message = "failure test-secret-value\n" + ("x" * 1200)

    sanitized = sanitize_error(message, environment)

    assert sanitized.startswith("failure [redacted] ")
    assert "test-secret-value" not in sanitized
    assert "\n" not in sanitized
    assert len(sanitized) == 1000
