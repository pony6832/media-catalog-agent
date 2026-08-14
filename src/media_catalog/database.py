from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from .models import MediaRecord, Status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CatalogDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_records (
                    id TEXT PRIMARY KEY,
                    normalized_path TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    description TEXT,
                    highlights_json TEXT NOT NULL DEFAULT '[]',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    markdown_path TEXT,
                    backup_path TEXT,
                    discovered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(normalized_path, fingerprint)
                )
                """
            )

    def upsert_discovered(
        self, path: Path, fingerprint: str, media_type: str
    ) -> MediaRecord:
        normalized_path = str(Path(path).resolve())
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM media_records
                WHERE normalized_path = ? AND fingerprint = ?
                """,
                (normalized_path, fingerprint),
            ).fetchone()
            if row is None:
                record_id = str(uuid.uuid4())
                timestamp = _now()
                connection.execute(
                    """
                    INSERT INTO media_records (
                        id, normalized_path, fingerprint, media_type, status,
                        discovered_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        normalized_path,
                        fingerprint,
                        media_type,
                        Status.PENDING.value,
                        timestamp,
                        timestamp,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM media_records WHERE id = ?", (record_id,)
                ).fetchone()
        return self._to_record(row)

    def get_record(self, record_id: str) -> MediaRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM media_records WHERE id = ?", (record_id,)
            ).fetchone()
        return self._to_record(row) if row is not None else None

    def list_records(self) -> list[MediaRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM media_records ORDER BY discovered_at, id"
            ).fetchall()
        return [self._to_record(row) for row in rows]

    def list_by_status(self, statuses: Sequence[Status]) -> list[MediaRecord]:
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM media_records WHERE status IN ({placeholders}) "
                "ORDER BY discovered_at, id",
                tuple(status.value for status in statuses),
            ).fetchall()
        return [self._to_record(row) for row in rows]

    def requeue_processing(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE media_records
                SET status = ?, error = NULL, updated_at = ?
                WHERE status = ?
                """,
                (
                    Status.PENDING.value,
                    _now(),
                    Status.PROCESSING.value,
                ),
            )
        return cursor.rowcount

    def requeue_failed(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE media_records
                SET status = ?, error = NULL, updated_at = ?
                WHERE status = ?
                """,
                (
                    Status.PENDING.value,
                    _now(),
                    Status.FAILED.value,
                ),
            )
        return cursor.rowcount

    def set_status(
        self, record_id: str, status: Status, *, error: str | None = None
    ) -> MediaRecord:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE media_records
                SET status = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status.value, error, _now(), record_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown media record: {record_id}")
        record = self.get_record(record_id)
        assert record is not None
        return record

    def save_analysis(
        self,
        record_id: str,
        *,
        description: str,
        highlights: tuple[str, ...],
        keywords: tuple[str, ...],
    ) -> MediaRecord:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE media_records
                SET status = ?, description = ?, highlights_json = ?,
                    keywords_json = ?, error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    Status.ANALYZED.value,
                    description,
                    json.dumps(highlights, ensure_ascii=False),
                    json.dumps(keywords, ensure_ascii=False),
                    _now(),
                    record_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown media record: {record_id}")
        record = self.get_record(record_id)
        assert record is not None
        return record

    @staticmethod
    def _to_record(row: sqlite3.Row) -> MediaRecord:
        return MediaRecord(
            id=row["id"],
            path=Path(row["normalized_path"]),
            fingerprint=row["fingerprint"],
            media_type=row["media_type"],
            status=Status(row["status"]),
            description=row["description"],
            highlights=tuple(json.loads(row["highlights_json"])),
            keywords=tuple(json.loads(row["keywords_json"])),
            error=row["error"],
            markdown_path=(Path(row["markdown_path"]) if row["markdown_path"] else None),
            backup_path=(Path(row["backup_path"]) if row["backup_path"] else None),
            discovered_at=row["discovered_at"],
            updated_at=row["updated_at"],
        )
