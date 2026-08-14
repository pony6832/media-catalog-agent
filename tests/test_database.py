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
