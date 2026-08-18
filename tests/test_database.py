import sqlite3
from pathlib import Path

from media_catalog.database import CatalogDatabase
from media_catalog.models import Status


def test_upsert_discovered_is_idempotent_and_preserves_status(tmp_path: Path) -> None:
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"video")
    database = CatalogDatabase(tmp_path / "catalog.sqlite")

    first = database.upsert_discovered(media_path, "abc", "video/mp4")
    database.set_status(first.id, Status.FAILED, error="model unavailable")
    second = database.upsert_discovered(media_path, "abc", "video/mp4")

    assert second.id == first.id
    assert second.status is Status.FAILED
    assert second.error == "model unavailable"


def test_list_and_get_return_persisted_records(tmp_path: Path) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    photo_path = tmp_path / "photo.jpg"
    photo_path.write_bytes(b"photo")

    created = database.upsert_discovered(photo_path, "def", "image/jpeg")

    assert database.get_record(created.id) == created
    assert database.list_records() == [created]


def test_save_analysis_marks_record_analyzed_for_human_review(
    tmp_path: Path,
) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    media = tmp_path / "photo.jpg"
    media.write_bytes(b"photo")
    record = database.upsert_discovered(media, "fingerprint", "image/jpeg")

    saved = database.save_analysis(
        record.id,
        description="紅色賀卡與金色馬。",
        highlights=("紅底", "金色馬"),
        keywords=("賀卡", "馬年"),
    )

    assert saved.status is Status.ANALYZED
    assert database.list_by_status((Status.ANALYZED,)) == [saved]


def test_requeue_processing_only_resets_interrupted_records(
    tmp_path: Path,
) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    processing_path = tmp_path / "processing.jpg"
    failed_path = tmp_path / "failed.jpg"
    processing_path.write_bytes(b"one")
    failed_path.write_bytes(b"two")
    processing = database.upsert_discovered(
        processing_path, "one", "image/jpeg"
    )
    failed = database.upsert_discovered(failed_path, "two", "image/jpeg")
    database.set_status(processing.id, Status.PROCESSING)
    database.set_status(failed.id, Status.FAILED, error="bad image")

    assert database.requeue_processing() == 1
    assert database.get_record(processing.id).status is Status.PENDING
    assert database.get_record(failed.id).status is Status.FAILED


def test_requeue_failed_only_resets_failed_records(tmp_path: Path) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    failed_path = tmp_path / "failed.jpg"
    analyzed_path = tmp_path / "analyzed.jpg"
    failed_path.write_bytes(b"failed")
    analyzed_path.write_bytes(b"analyzed")
    failed = database.upsert_discovered(failed_path, "failed", "image/jpeg")
    analyzed = database.upsert_discovered(
        analyzed_path, "analyzed", "image/jpeg"
    )
    database.set_status(failed.id, Status.FAILED, error="bad output")
    database.save_analysis(
        analyzed.id,
        description="done",
        highlights=("done",),
        keywords=("done",),
    )

    assert database.requeue_failed() == 1
    assert database.get_record(failed.id).status is Status.PENDING
    assert database.get_record(failed.id).error is None
    assert database.get_record(analyzed.id).status is Status.ANALYZED


def test_requeue_incomplete_analysis_repairs_legacy_blank_rows(
    tmp_path: Path,
) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    blank_path = tmp_path / "blank.jpg"
    complete_path = tmp_path / "complete.jpg"
    blank_path.write_bytes(b"blank")
    complete_path.write_bytes(b"complete")
    blank = database.upsert_discovered(blank_path, "blank", "image/jpeg")
    complete = database.upsert_discovered(
        complete_path, "complete", "image/jpeg"
    )
    database.save_analysis(
        complete.id,
        description="完整描述",
        highlights=("重點",),
        keywords=("關鍵字",),
    )
    with sqlite3.connect(database.db_path) as connection:
        connection.execute(
            """
            UPDATE media_records
            SET status = ?, description = '', highlights_json = '[]',
                keywords_json = '[]'
            WHERE id = ?
            """,
            (Status.ANALYZED.value, blank.id),
        )

    assert database.requeue_incomplete_analysis() == 1
    assert database.get_record(blank.id).status is Status.PENDING
    assert database.get_record(complete.id).status is Status.ANALYZED


def test_requeue_incomplete_analysis_recovers_legacy_skipped_rows(
    tmp_path: Path,
) -> None:
    database = CatalogDatabase(tmp_path / "catalog.sqlite")
    media_path = tmp_path / "skipped.jpg"
    media_path.write_bytes(b"skipped")
    skipped = database.upsert_discovered(
        media_path, "skipped", "image/jpeg"
    )
    database.set_status(skipped.id, Status.SKIPPED)

    assert database.requeue_incomplete_analysis() == 1
    assert database.get_record(skipped.id).status is Status.PENDING
