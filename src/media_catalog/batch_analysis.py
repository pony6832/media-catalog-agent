from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .database import CatalogDatabase
from .excel_catalog import write_excel
from .inference import AnalysisError
from .models import MediaRecord, Status, has_complete_analysis
from .processor import Analyzer
from .source_guard import (
    SourceIntegrityError,
    capture_source,
    sanitize_error,
    verify_record_source,
)
from .workspace import MediaWorkspace


ProgressCallback = Callable[[int, int, MediaRecord], None]


@dataclass(frozen=True, slots=True)
class BatchAnalysisResult:
    analyzed: int
    failed: int
    skipped: int
    remaining: int


def analyze_pending(
    workspace: MediaWorkspace,
    analyzer: Analyzer,
    *,
    excel_writer: Callable[[Iterable[MediaRecord], Path], Path] = write_excel,
    progress: ProgressCallback | None = None,
) -> BatchAnalysisResult:
    database = CatalogDatabase(workspace.database_path)
    initial_records = database.list_records()
    pending = [
        record for record in initial_records if record.status is Status.PENDING
    ]
    analyzed = 0
    completed = 0
    total = len(pending)

    for record in pending:
        try:
            snapshot = capture_source(record.path)
            verify_record_source(record, snapshot)
        except (OSError, SourceIntegrityError) as error:
            database.set_status(
                record.id,
                Status.FAILED,
                error=sanitize_error(str(error)),
            )
            excel_writer(database.list_records(), workspace.excel_path)
            completed += 1
            if progress is not None:
                current = database.get_record(record.id)
                assert current is not None
                progress(completed, total, current)
            continue

        database.set_status(record.id, Status.PROCESSING)
        excel_writer(database.list_records(), workspace.excel_path)
        try:
            result = analyzer.analyze(record.path)
            verify_record_source(record, snapshot)
        except (AnalysisError, OSError, SourceIntegrityError) as error:
            database.set_status(
                record.id,
                Status.FAILED,
                error=sanitize_error(str(error)),
            )
        else:
            database.save_analysis(
                record.id,
                description=result.description,
                highlights=result.highlights,
                keywords=result.keywords,
            )
            analyzed += 1
        excel_writer(database.list_records(), workspace.excel_path)
        completed += 1
        if progress is not None:
            current = database.get_record(record.id)
            assert current is not None
            progress(completed, total, current)

    failed = len(database.list_by_status((Status.FAILED,)))
    remaining = sum(
        not has_complete_analysis(record)
        for record in database.list_records()
    )
    return BatchAnalysisResult(
        analyzed=analyzed,
        failed=failed,
        skipped=len(initial_records) - len(pending),
        remaining=remaining,
    )
