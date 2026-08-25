from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import nullcontext
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
from .segment_pipeline import SafeStopRequested
from .stage_runner import HeartbeatThread
from .workspace import MediaWorkspace


ProgressCallback = Callable[[int, int, MediaRecord], None]


@dataclass(frozen=True, slots=True)
class BatchAnalysisResult:
    analyzed: int
    failed: int
    skipped: int
    remaining: int
    excel_sync_pending: bool = False


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
    run_state = getattr(analyzer, "run_state", None)
    segment_pipeline = getattr(analyzer, "segment_pipeline", None)
    run_id = None
    excel_sync_pending = False
    if run_state is not None and segment_pipeline is not None:
        video_count = sum(
            record.media_type.startswith("video/") for record in initial_records
        )
        image_count = sum(
            record.media_type.startswith("image/") for record in initial_records
        )
        total_bytes = sum(
            record.path.stat().st_size
            for record in initial_records
            if record.path.is_file()
        )
        run = run_state.ensure_run(
            root_path=workspace.root,
            video_count=video_count,
            image_count=image_count,
            total_bytes=total_bytes,
        )
        run_id = run.run_id
        run_state.clear_stop(run_id)

    def sync_excel() -> None:
        nonlocal excel_sync_pending
        try:
            excel_writer(database.list_records(), workspace.excel_path)
        except PermissionError:
            if run_state is None or run_id is None:
                raise
            excel_sync_pending = True
            run_state.set_excel_sync_pending(run_id, True)
        else:
            if run_state is not None and run_id is not None:
                excel_sync_pending = False
                run_state.set_excel_sync_pending(run_id, False)

    def update_run(current_media_id: str | None = None) -> None:
        if run_state is None or run_id is None:
            return
        records = database.list_records()
        completed_count = sum(has_complete_analysis(record) for record in records)
        failed_count = sum(record.status is Status.FAILED for record in records)
        run_state.update_counts(
            run_id,
            completed_media=completed_count,
            failed_media=failed_count,
            current_media_id=current_media_id,
            current_segment_id=None,
            status="running",
        )

    heartbeat = (
        HeartbeatThread(run_state, run_id)
        if run_state is not None and run_id is not None
        else nullcontext()
    )
    with heartbeat:
        for record in pending:
            if run_state is not None and run_id is not None:
                run = run_state.get_run(run_id)
                if run is not None and run.stop_requested:
                    break
            try:
                snapshot = capture_source(record.path)
                verify_record_source(record, snapshot)
            except (OSError, SourceIntegrityError) as error:
                database.set_status(
                    record.id,
                    Status.FAILED,
                    error=sanitize_error(str(error)),
                )
                sync_excel()
                completed += 1
                update_run(record.id)
                if progress is not None:
                    current = database.get_record(record.id)
                    assert current is not None
                    progress(completed, total, current)
                continue

            database.set_status(record.id, Status.PROCESSING)
            sync_excel()
            update_run(record.id)
            try:
                if (
                    segment_pipeline is not None
                    and run_id is not None
                    and record.media_type.startswith("video/")
                ):
                    result = segment_pipeline.analyze_video(record, run_id)
                else:
                    result = analyzer.analyze(record.path)
                verify_record_source(record, snapshot)
            except SafeStopRequested:
                database.set_status(record.id, Status.PENDING)
                sync_excel()
                update_run(record.id)
                break
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
            sync_excel()
            completed += 1
            update_run(record.id)
            if progress is not None:
                current = database.get_record(record.id)
                assert current is not None
                progress(completed, total, current)

        sync_excel()

    failed = len(database.list_by_status((Status.FAILED,)))
    remaining = sum(
        not has_complete_analysis(record)
        for record in database.list_records()
    )
    if run_state is not None and run_id is not None:
        final_records = database.list_records()
        run_state.update_counts(
            run_id,
            completed_media=sum(
                has_complete_analysis(record) for record in final_records
            ),
            failed_media=failed,
            current_media_id=None,
            current_segment_id=None,
            status=(
                "completed"
                if remaining == 0 and not excel_sync_pending
                else "incomplete"
            ),
        )
    return BatchAnalysisResult(
        analyzed=analyzed,
        failed=failed,
        skipped=len(initial_records) - len(pending),
        remaining=remaining,
        excel_sync_pending=excel_sync_pending,
    )
