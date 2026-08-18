from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Status(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    ANALYZED = "analyzed"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MediaRecord:
    id: str
    path: Path
    fingerprint: str
    media_type: str
    status: Status
    description: str | None = None
    highlights: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    error: str | None = None
    markdown_path: Path | None = None
    backup_path: Path | None = None
    discovered_at: str | None = None
    updated_at: str | None = None


def has_complete_analysis(record: MediaRecord) -> bool:
    return (
        record.status in {Status.ANALYZED, Status.COMPLETED}
        and isinstance(record.description, str)
        and bool(record.description.strip())
        and bool(record.highlights)
        and all(item.strip() for item in record.highlights)
        and bool(record.keywords)
        and all(item.strip() for item in record.keywords)
    )

