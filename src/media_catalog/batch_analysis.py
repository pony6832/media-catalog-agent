from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from .analysis_mode import AnalysisMode
from .database import CatalogDatabase
from .excel_catalog import write_excel
from .force_gemini import plan_force_run
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
    mode: AnalysisMode = AnalysisMode.AUTO,
    run_id: str | None = None,
    reviewed_paths: set[str] | None = None,
) -> BatchAnalysisResult:
    database = CatalogDatabase(workspace.database_path)
    initial_records = database.list_records()
    analyzed = 0
    completed = 0
    run_state = getattr(analyzer, "run_state", None)
    segment_pipeline = getattr(analyzer, "segment_pipeline", None)
    active_run_id = run_id
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
        if active_run_id is None:
            run, _ = run_state.begin_run(
                root_path=workspace.root,
                video_count=video_count,
                image_count=image_count,
                total_bytes=total_bytes,
                mode=mode,
            )
            active_run_id = run.run_id
        run_state.clear_stop(active_run_id)

    force_eligible_ids: set[str] | None = None
    if mode is AnalysisMode.FORCE_GEMINI:
        if run_state is None or active_run_id is None:
            raise AnalysisError("force Gemini mode requires persistent run state")
        estimate, eligible_ids = plan_force_run(
            initial_records, reviewed_paths or set()
        )
        del estimate
        force_eligible_ids = set(eligible_ids)
        active_run = run_state.get_run(active_run_id)
        if active_run is None:
            raise AnalysisError("force Gemini run state is missing")
        if active_run.analysis_mode is not AnalysisMode.FORCE_GEMINI:
            raise AnalysisError("run mode does not match force Gemini mode")
        if not active_run.force_prepared:
            eligible_records = {
                record.id: record
                for record in initial_records
                if record.id in force_eligible_ids
            }
            for identity in eligible_ids:
                record = eligible_records[identity]
                if record.media_type.startswith("video/"):
                    run_state.reset_video_for_force(
                        active_run_id, record.id
                    )
            database.requeue_for_force(eligible_ids)
            run_state.mark_force_prepared(active_run_id)
            initial_records = database.list_records()
        else:
            recoverable_ids = tuple(
                record.id
                for record in initial_records
                if record.id in force_eligible_ids
                and (
                    record.status
                    in {Status.PROCESSING, Status.FAILED, Status.SKIPPED}
                    or (
                        record.status
                        in {Status.ANALYZED, Status.COMPLETED}
                        and not has_complete_analysis(record)
                    )
                )
            )
            if recoverable_ids:
                database.requeue_for_force(recoverable_ids)
                initial_records = database.list_records()

    pending = [
        record
        for record in initial_records
        if record.status is Status.PENDING
        and (
            force_eligible_ids is None or record.id in force_eligible_ids
        )
    ]
    total = len(pending)

    def failed_count(records: Iterable[MediaRecord]) -> int:
        fatal = sum(record.status is Status.FAILED for record in records)
        if mode is not AnalysisMode.FORCE_GEMINI:
            return fatal
        warnings = sum(
            record.status is Status.ANALYZED
            and bool(record.error)
            and record.error.startswith("Gemini 強化失敗")
            for record in records
        )
        return fatal + warnings

    def sync_excel() -> None:
        nonlocal excel_sync_pending
        try:
            excel_writer(database.list_records(), workspace.excel_path)
        except PermissionError:
            if run_state is None or active_run_id is None:
                raise
            excel_sync_pending = True
            run_state.set_excel_sync_pending(active_run_id, True)
        else:
            if run_state is not None and active_run_id is not None:
                excel_sync_pending = False
                run_state.set_excel_sync_pending(active_run_id, False)

    def update_run(current_media_id: str | None = None) -> None:
        if run_state is None or active_run_id is None:
            return
        records = database.list_records()
        completed_count = sum(has_complete_analysis(record) for record in records)
        current_failed_count = failed_count(records)
        run_state.update_counts(
            active_run_id,
            completed_media=completed_count,
            failed_media=current_failed_count,
            current_media_id=current_media_id,
            current_segment_id=None,
            status="running",
        )

    heartbeat = (
        HeartbeatThread(run_state, active_run_id)
        if run_state is not None and active_run_id is not None
        else nullcontext()
    )
    with heartbeat:
        for record in pending:
            if run_state is not None and active_run_id is not None:
                run = run_state.get_run(active_run_id)
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
                warning = None
                if (
                    segment_pipeline is not None
                    and active_run_id is not None
                    and record.media_type.startswith("video/")
                ):
                    result = segment_pipeline.analyze_video(
                        record, active_run_id, mode=mode
                    )
                    warning = result.warning
                elif mode is AnalysisMode.FORCE_GEMINI:
                    forced = analyzer.force_image_analyzer.analyze(record.path)
                    result = forced.analysis
                    warning = forced.warning
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
                    warning=warning,
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

    failed = failed_count(database.list_records())
    remaining = sum(
        not has_complete_analysis(record)
        for record in database.list_records()
    )
    if run_state is not None and active_run_id is not None:
        final_records = database.list_records()
        run_state.update_counts(
            active_run_id,
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
