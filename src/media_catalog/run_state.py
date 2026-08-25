from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .schema_migration import ensure_a_plus_schema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    run_id: str
    root_path: Path
    status: str
    video_count: int
    image_count: int
    total_bytes: int
    total_media: int
    completed_media: int
    failed_media: int
    current_media_id: str | None
    current_segment_id: str | None
    worker_pid: int | None
    last_heartbeat: str | None
    stop_requested: bool
    recovery_count: int
    excel_sync_pending: bool


@dataclass(frozen=True, slots=True)
class VideoSegment:
    segment_id: str
    run_id: str
    video_id: str
    segment_index: int
    start_seconds: float
    end_seconds: float
    status: str = "pending"
    selected_frames: tuple[Path, ...] = ()
    local_result_json: str | None = None
    cloud_result_json: str | None = None
    needs_review: bool = False
    retry_count: int = 0
    crash_count: int = 0
    error: str | None = None
    last_heartbeat: str | None = None


class RunStateStore:
    def __init__(self, path: Path, *, excel_path: Path | None = None) -> None:
        self.path = Path(path).resolve()
        ensure_a_plus_schema(self.path, excel_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def create_run(
        self,
        run_id: str,
        *,
        root_path: Path,
        video_count: int,
        image_count: int,
        total_bytes: int,
        status: str = "pending",
    ) -> AnalysisRun:
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_runs (
                    run_id, root_path, status, video_count, image_count,
                    total_bytes, total_media, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(Path(root_path).resolve()),
                    status,
                    video_count,
                    image_count,
                    total_bytes,
                    video_count + image_count,
                    timestamp,
                    timestamp,
                ),
            )
        result = self.get_run(run_id)
        assert result is not None
        return result

    @staticmethod
    def run_id_for_root(root_path: Path) -> str:
        normalized = str(Path(root_path).resolve()).casefold().encode("utf-8")
        return f"catalog-{hashlib.sha256(normalized).hexdigest()[:20]}"

    def ensure_run(
        self,
        *,
        root_path: Path,
        video_count: int,
        image_count: int,
        total_bytes: int,
    ) -> AnalysisRun:
        run_id = self.run_id_for_root(root_path)
        existing = self.get_run(run_id)
        if existing is not None:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE analysis_runs
                    SET video_count = ?, image_count = ?, total_bytes = ?,
                        total_media = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        video_count,
                        image_count,
                        total_bytes,
                        video_count + image_count,
                        _now(),
                        run_id,
                    ),
                )
            refreshed = self.get_run(run_id)
            assert refreshed is not None
            return refreshed
        return self.create_run(
            run_id,
            root_path=root_path,
            video_count=video_count,
            image_count=image_count,
            total_bytes=total_bytes,
        )

    def get_run(self, run_id: str) -> AnalysisRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._to_run(row) if row is not None else None

    def update_counts(
        self,
        run_id: str,
        *,
        completed_media: int,
        failed_media: int,
        current_media_id: str | None,
        current_segment_id: str | None,
        status: str = "running",
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_runs
                SET status = ?, completed_media = ?, failed_media = ?,
                    current_media_id = ?, current_segment_id = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    completed_media,
                    failed_media,
                    current_media_id,
                    current_segment_id,
                    _now(),
                    run_id,
                ),
            )
        self._require_updated(cursor.rowcount, run_id)

    def heartbeat(
        self, run_id: str, worker_pid: int | None = None
    ) -> None:
        timestamp = _now()
        pid = os.getpid() if worker_pid is None else worker_pid
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_runs
                SET worker_pid = ?, last_heartbeat = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (pid, timestamp, timestamp, run_id),
            )
        self._require_updated(cursor.rowcount, run_id)

    def set_current_item(
        self,
        run_id: str,
        media_id: str | None,
        segment_id: str | None,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_runs
                SET current_media_id = ?, current_segment_id = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (media_id, segment_id, _now(), run_id),
            )
        self._require_updated(cursor.rowcount, run_id)

    def request_stop(self, run_id: str) -> None:
        self._set_run_flag(run_id, "stop_requested", True)

    def clear_stop(self, run_id: str) -> None:
        self._set_run_flag(run_id, "stop_requested", False)

    def set_excel_sync_pending(self, run_id: str, pending: bool) -> None:
        self._set_run_flag(run_id, "excel_sync_pending", pending)

    def _set_run_flag(self, run_id: str, column: str, enabled: bool) -> None:
        if column not in {"stop_requested", "excel_sync_pending"}:
            raise ValueError(f"Unsupported run flag: {column}")
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE analysis_runs SET {column} = ?, updated_at = ? "
                "WHERE run_id = ?",
                (int(enabled), _now(), run_id),
            )
        self._require_updated(cursor.rowcount, run_id)

    def upsert_segments(
        self,
        run_id: str,
        video_id: str,
        segments: tuple[VideoSegment, ...],
    ) -> None:
        timestamp = _now()
        rows = []
        for segment in segments:
            if segment.run_id != run_id or segment.video_id != video_id:
                raise ValueError("Segment identity does not match its batch")
            rows.append(
                (
                    segment.segment_id,
                    run_id,
                    video_id,
                    segment.segment_index,
                    segment.start_seconds,
                    segment.end_seconds,
                    segment.status,
                    json.dumps(
                        [str(path) for path in segment.selected_frames],
                        ensure_ascii=False,
                    ),
                    segment.local_result_json,
                    segment.cloud_result_json,
                    int(segment.needs_review),
                    segment.retry_count,
                    segment.crash_count,
                    segment.error,
                    segment.last_heartbeat,
                    timestamp,
                    timestamp,
                )
            )
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO video_segments (
                    segment_id, run_id, video_id, segment_index,
                    start_seconds, end_seconds, status, selected_frames_json,
                    local_result_json, cloud_result_json, needs_review,
                    retry_count, crash_count, error, last_heartbeat,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(segment_id) DO UPDATE SET
                    status = excluded.status,
                    selected_frames_json = excluded.selected_frames_json,
                    local_result_json = excluded.local_result_json,
                    cloud_result_json = excluded.cloud_result_json,
                    needs_review = excluded.needs_review,
                    retry_count = excluded.retry_count,
                    crash_count = excluded.crash_count,
                    error = excluded.error,
                    last_heartbeat = excluded.last_heartbeat,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

    def list_segments(self, video_id: str) -> list[VideoSegment]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM video_segments WHERE video_id = ? "
                "ORDER BY segment_index",
                (video_id,),
            ).fetchall()
        return [self._to_segment(row) for row in rows]

    def mark_segment_status(
        self,
        segment_id: str,
        status: str,
        *,
        error: str | None = None,
        retry_count_increment: int = 0,
    ) -> None:
        if retry_count_increment < 0:
            raise ValueError("retry_count_increment must not be negative")
        timestamp = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE video_segments
                SET status = ?, error = ?,
                    retry_count = retry_count + ?,
                    last_heartbeat = ?, updated_at = ?
                WHERE segment_id = ?
                """,
                (
                    status,
                    error,
                    retry_count_increment,
                    timestamp,
                    timestamp,
                    segment_id,
                ),
            )
        self._require_updated(cursor.rowcount, segment_id)

    def save_segment_result(
        self,
        segment_id: str,
        *,
        status: str,
        selected_frames: tuple[Path, ...],
        local_analysis,
        cloud_analysis,
        needs_review: bool,
        error: str | None = None,
        retry_count_increment: int = 0,
    ) -> None:
        if retry_count_increment < 0:
            raise ValueError("retry_count_increment must not be negative")
        timestamp = _now()
        local_json = self._analysis_json(local_analysis)
        cloud_json = self._analysis_json(cloud_analysis)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE video_segments
                SET status = ?, selected_frames_json = ?,
                    local_result_json = ?, cloud_result_json = ?,
                    needs_review = ?, error = ?,
                    retry_count = retry_count + ?, last_heartbeat = ?,
                    updated_at = ?
                WHERE segment_id = ?
                """,
                (
                    status,
                    json.dumps(
                        [str(Path(path).resolve()) for path in selected_frames],
                        ensure_ascii=False,
                    ),
                    local_json,
                    cloud_json,
                    int(needs_review),
                    error,
                    retry_count_increment,
                    timestamp,
                    timestamp,
                    segment_id,
                ),
            )
        self._require_updated(cursor.rowcount, segment_id)

    def requeue_stale_processing(self, run_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE video_segments
                SET status = 'pending', crash_count = crash_count + 1,
                    error = NULL, updated_at = ?
                WHERE run_id = ? AND status = 'processing'
                """,
                (_now(), run_id),
            )
        return cursor.rowcount

    def consume_gemini_slot(
        self,
        video_id: str,
        *,
        frame_count: int = 1,
        max_segments: int = 12,
        max_frames: int = 36,
    ) -> bool:
        if frame_count < 1:
            raise ValueError("frame_count must be positive")
        timestamp = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO gemini_usage (
                    video_id, segment_count, frame_count, updated_at
                ) VALUES (?, 0, 0, ?)
                """,
                (video_id, timestamp),
            )
            row = connection.execute(
                "SELECT segment_count, frame_count FROM gemini_usage "
                "WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            if (
                row["segment_count"] >= max_segments
                or row["frame_count"] + frame_count > max_frames
            ):
                return False
            connection.execute(
                """
                UPDATE gemini_usage
                SET segment_count = segment_count + 1,
                    frame_count = frame_count + ?, updated_at = ?
                WHERE video_id = ?
                """,
                (frame_count, timestamp, video_id),
            )
        return True

    @staticmethod
    def _analysis_json(analysis) -> str | None:
        if analysis is None:
            return None
        return json.dumps(
            {
                "description": analysis.description,
                "highlights": analysis.highlights,
                "keywords": analysis.keywords,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _require_updated(rowcount: int, identity: str) -> None:
        if rowcount != 1:
            raise KeyError(f"Unknown run state item: {identity}")

    @staticmethod
    def _to_run(row: sqlite3.Row) -> AnalysisRun:
        return AnalysisRun(
            run_id=row["run_id"],
            root_path=Path(row["root_path"]),
            status=row["status"],
            video_count=row["video_count"],
            image_count=row["image_count"],
            total_bytes=row["total_bytes"],
            total_media=row["total_media"],
            completed_media=row["completed_media"],
            failed_media=row["failed_media"],
            current_media_id=row["current_media_id"],
            current_segment_id=row["current_segment_id"],
            worker_pid=row["worker_pid"],
            last_heartbeat=row["last_heartbeat"],
            stop_requested=bool(row["stop_requested"]),
            recovery_count=row["recovery_count"],
            excel_sync_pending=bool(row["excel_sync_pending"]),
        )
    @staticmethod
    def _to_segment(row: sqlite3.Row) -> VideoSegment:
        return VideoSegment(
            segment_id=row["segment_id"],
            run_id=row["run_id"],
            video_id=row["video_id"],
            segment_index=row["segment_index"],
            start_seconds=row["start_seconds"],
            end_seconds=row["end_seconds"],
            status=row["status"],
            selected_frames=tuple(
                Path(item) for item in json.loads(row["selected_frames_json"])
            ),
            local_result_json=row["local_result_json"],
            cloud_result_json=row["cloud_result_json"],
            needs_review=bool(row["needs_review"]),
            retry_count=row["retry_count"],
            crash_count=row["crash_count"],
            error=row["error"],
            last_heartbeat=row["last_heartbeat"],
        )
