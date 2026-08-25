from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


LATEST_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class MigrationResult:
    migrated: bool
    version: int
    backup_dir: Path | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _current_version(database_path: Path) -> int | None:
    if not database_path.is_file():
        return None
    with sqlite3.connect(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_version'"
        ).fetchone()
        if exists is None:
            return None
        row = connection.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _next_backup_dir(database_path: Path, timestamp: str) -> Path:
    backup_root = database_path.parent / "APlus備份"
    candidate = backup_root / timestamp
    suffix = 1
    while candidate.exists():
        candidate = backup_root / f"{timestamp}-{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _backup_catalog(
    database_path: Path,
    excel_path: Path | None,
    timestamp: str,
) -> Path:
    backup_dir = _next_backup_dir(database_path, timestamp)
    backup_database = backup_dir / database_path.name
    try:
        with sqlite3.connect(database_path) as source:
            with sqlite3.connect(backup_database) as destination:
                source.backup(destination)
        with sqlite3.connect(backup_database) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise sqlite3.DatabaseError("SQLite backup integrity check failed")
        if backup_database.stat().st_size == 0:
            raise OSError("SQLite backup is empty")

        if excel_path is not None and excel_path.is_file():
            backup_excel = backup_dir / excel_path.name
            shutil.copy2(excel_path, backup_excel)
            if backup_excel.stat().st_size == 0:
                raise OSError("Excel backup is empty")
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise
    return backup_dir


def _apply_schema(database_path: Path, applied_at: str) -> None:
    with sqlite3.connect(database_path, timeout=30) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_runs (
                run_id TEXT PRIMARY KEY,
                root_path TEXT NOT NULL,
                status TEXT NOT NULL,
                video_count INTEGER NOT NULL DEFAULT 0,
                image_count INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                total_media INTEGER NOT NULL DEFAULT 0,
                completed_media INTEGER NOT NULL DEFAULT 0,
                failed_media INTEGER NOT NULL DEFAULT 0,
                current_media_id TEXT,
                current_segment_id TEXT,
                worker_pid INTEGER,
                last_heartbeat TEXT,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                recovery_count INTEGER NOT NULL DEFAULT 0,
                excel_sync_pending INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_segments (
                segment_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                segment_index INTEGER NOT NULL,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                selected_frames_json TEXT NOT NULL DEFAULT '[]',
                local_result_json TEXT,
                cloud_result_json TEXT,
                needs_review INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                crash_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                last_heartbeat TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(video_id, segment_index),
                FOREIGN KEY(run_id) REFERENCES analysis_runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS gemini_usage (
                video_id TEXT PRIMARY KEY,
                segment_count INTEGER NOT NULL DEFAULT 0,
                frame_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) "
            "VALUES (?, ?)",
            (LATEST_SCHEMA_VERSION, applied_at),
        )


def ensure_a_plus_schema(
    database_path: Path,
    excel_path: Path | None = None,
    *,
    now: Callable[[], datetime] = _utc_now,
) -> MigrationResult:
    database_path = Path(database_path).resolve()
    excel_path = Path(excel_path).resolve() if excel_path is not None else None
    database_path.parent.mkdir(parents=True, exist_ok=True)
    version = _current_version(database_path)
    if version is not None and version >= LATEST_SCHEMA_VERSION:
        return MigrationResult(False, version, None)

    current_time = now()
    backup_dir = None
    if database_path.is_file() and database_path.stat().st_size > 0:
        backup_dir = _backup_catalog(
            database_path,
            excel_path,
            current_time.strftime("%Y%m%d-%H%M%S"),
        )
    _apply_schema(database_path, current_time.astimezone(timezone.utc).isoformat())
    return MigrationResult(True, LATEST_SCHEMA_VERSION, backup_dir)
