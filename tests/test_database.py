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
