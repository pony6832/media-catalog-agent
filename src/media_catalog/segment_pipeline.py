from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .analysis_mode import AnalysisMode
from .force_gemini import select_force_segments
from .gemini_client import GeminiSegmentRequest
from .inference import (
    Analysis,
    AnalysisError,
    LocalAnalyzer,
    assess_analysis_quality,
)
from .models import MediaRecord
from .run_state import RunStateStore, VideoSegment
from .scene_segments import SegmentRange
from .stage_runner import StagePolicy, StageRunner


class SafeStopRequested(AnalysisError):
    pass


@dataclass(frozen=True, slots=True)
class VideoAnalysisResult:
    description: str
    highlights: tuple[str, ...]
    keywords: tuple[str, ...]
    gemini_segments: int
    needs_review_segments: int
    failed_segments: int
    warning: str | None = None


class SegmentPipeline:
    def __init__(
        self,
        *,
        segmenter,
        selector,
        local_analyzer,
        gemini_client,
        store: RunStateStore,
        output_root: Path,
        stage_runner: StageRunner | None = None,
        evidence_extractor=None,
    ) -> None:
        self.segmenter = segmenter
        self.selector = selector
        self.local_analyzer = local_analyzer
        self.gemini_client = gemini_client
        self.store = store
        self.output_root = Path(output_root).resolve()
        self.stage_runner = stage_runner or StageRunner()
        self.evidence_extractor = evidence_extractor

    def analyze_video(
        self,
        record: MediaRecord,
        run_id: str,
        *,
        mode: AnalysisMode = AnalysisMode.AUTO,
    ) -> VideoAnalysisResult:
        force_gemini = mode is AnalysisMode.FORCE_GEMINI
        segments = self.store.list_segments(record.id)
        if segments:
            self.store.requeue_stale_processing(run_id)
            segments = self.store.list_segments(record.id)
        else:
            segmentation = self.stage_runner.run(
                "scene_segmentation",
                lambda _timeout: self.segmenter.segment(record.path),
                StagePolicy(1200, 1),
            )
            if not segmentation.ok or not segmentation.value:
                raise AnalysisError(
                    segmentation.error_message or "video segmentation failed"
                )
            segments = [
                VideoSegment(
                    segment_id=f"{record.id}:{index}",
                    run_id=run_id,
                    video_id=record.id,
                    segment_index=index,
                    start_seconds=item.start_seconds,
                    end_seconds=item.end_seconds,
                )
                for index, item in enumerate(segmentation.value)
            ]
            self.store.upsert_segments(run_id, record.id, tuple(segments))

        ocr_text = (
            self._video_ocr(record.path)
            if any(
                segment.status != "completed"
                or not segment.local_result_json
                for segment in segments
            )
            else ""
        )
        for segment in segments:
            if segment.status == "completed" and segment.local_result_json:
                continue
            run = self.store.get_run(run_id)
            if run is not None and run.stop_requested:
                raise SafeStopRequested("safe stop requested")
            self.store.set_current_item(
                run_id, record.id, segment.segment_id
            )
            try:
                self._analyze_segment(
                    record,
                    segment,
                    ocr_text=ocr_text,
                    allow_cloud=not force_gemini,
                )
            finally:
                self.store.set_current_item(run_id, None, None)

        persisted = self.store.list_segments(record.id)
        failed = [segment for segment in persisted if segment.status != "completed"]
        if failed:
            raise AnalysisError(f"{len(failed)} video segments are incomplete")

        warning = None
        if force_gemini:
            failed_force_segments = self._enhance_force_segments(
                record, persisted, ocr_text=ocr_text
            )
            persisted = self.store.list_segments(record.id)
            if failed_force_segments:
                warning = (
                    f"Gemini 強化失敗:{failed_force_segments} 段"
                )

        analyses = tuple(self._best_analysis(segment) for segment in persisted)
        summary = self.stage_runner.run(
            "video_summary",
            lambda _timeout: self.local_analyzer.summarize_segments(analyses),
            StagePolicy(600, 1),
        )
        if not summary.ok or summary.value is None:
            raise AnalysisError(summary.error_message or "video summary failed")
        return VideoAnalysisResult(
            description=summary.value.description,
            highlights=summary.value.highlights,
            keywords=summary.value.keywords,
            gemini_segments=sum(
                segment.cloud_result_json is not None for segment in persisted
            ),
            needs_review_segments=sum(
                segment.needs_review for segment in persisted
            ),
            failed_segments=0,
            warning=warning,
        )

    def _analyze_segment(
        self,
        record: MediaRecord,
        segment: VideoSegment,
        *,
        ocr_text: str,
        allow_cloud: bool = True,
    ) -> None:
        self.store.mark_segment_status(segment.segment_id, "processing")
        segment_range = self._segment_range(segment)
        output_dir = (
            self.output_root
            / record.id
            / f"segment-{segment.segment_index:04d}"
        )
        extraction = self.stage_runner.run(
            "frame_extraction",
            lambda _timeout: self.segmenter.extract_candidates(
                record.path, segment_range, output_dir
            ),
            StagePolicy(1200, 1),
        )
        if not extraction.ok or not extraction.value:
            self.store.mark_segment_status(
                segment.segment_id,
                "failed",
                error=extraction.error_message or "frame extraction failed",
                retry_count_increment=max(0, extraction.attempts - 1),
            )
            return
        retry_count_increment = max(0, extraction.attempts - 1)
        try:
            selected = tuple(
                self.selector.select(extraction.value, ocr_text=ocr_text)
            )
        except Exception as error:
            self.store.mark_segment_status(
                segment.segment_id,
                "failed",
                error=f"frame_selection:{type(error).__name__}",
            )
            return

        local = self.stage_runner.run(
            "local_analysis",
            lambda _timeout: self.local_analyzer.analyze_frames(
                selected, ocr_text=ocr_text
            ),
            StagePolicy(600, 1),
        )
        if not local.ok or local.value is None:
            self.store.mark_segment_status(
                segment.segment_id,
                "failed",
                error=local.error_message or "local analysis failed",
                retry_count_increment=(
                    retry_count_increment + max(0, local.attempts - 1)
                ),
            )
            return
        retry_count_increment += max(0, local.attempts - 1)

        local_analysis = local.value
        issues = assess_analysis_quality(
            local_analysis,
            ocr_text=ocr_text,
            frame_count=len(selected),
        )
        cloud_analysis = None
        error = None
        needs_review = False
        gemini_configured = (
            self.gemini_client is not None
            and getattr(self.gemini_client, "is_configured", True)
        )
        if issues and gemini_configured and allow_cloud:
            granted = self.store.consume_gemini_slot(
                record.id, frame_count=len(selected)
            )
            if granted:
                cloud = self.stage_runner.run(
                    "gemini_analysis",
                    lambda _timeout: self.gemini_client.analyze(
                        GeminiSegmentRequest(
                            frames=selected,
                            ocr_text=ocr_text,
                            local_analysis=local_analysis,
                        )
                    ),
                    StagePolicy(90, 1),
                )
                if cloud.ok and cloud.value is not None:
                    cloud_analysis = cloud.value
                else:
                    needs_review = True
                    error = f"cloud_failed:{cloud.error_type or 'unknown'}"
                retry_count_increment += max(0, cloud.attempts - 1)
            else:
                needs_review = True
                error = "cloud_quota_exhausted"
        elif issues:
            needs_review = True
            error = "force_pending" if not allow_cloud else "cloud_unavailable"

        self.store.save_segment_result(
            segment.segment_id,
            status="completed",
            selected_frames=selected,
            local_analysis=local_analysis,
            cloud_analysis=cloud_analysis,
            needs_review=needs_review,
            error=error,
            retry_count_increment=retry_count_increment,
        )

    def _enhance_force_segments(
        self,
        record: MediaRecord,
        segments: list[VideoSegment],
        *,
        ocr_text: str,
    ) -> int:
        failures = 0
        for segment in select_force_segments(segments, limit=12):
            run = self.store.get_run(segment.run_id)
            if run is not None and run.stop_requested:
                raise SafeStopRequested("safe stop requested")
            if segment.cloud_result_json is not None:
                continue
            if segment.error and segment.error.startswith(
                ("cloud_failed:", "cloud_quota_exhausted")
            ):
                failures += 1
                continue
            local_analysis = self._best_analysis(segment)
            selected = segment.selected_frames
            if not selected or not self.store.consume_gemini_slot(
                record.id, frame_count=len(selected)
            ):
                failures += 1
                self.store.save_segment_result(
                    segment.segment_id,
                    status="completed",
                    selected_frames=selected,
                    local_analysis=local_analysis,
                    cloud_analysis=None,
                    needs_review=True,
                    error="cloud_quota_exhausted",
                )
                continue
            cloud = self.stage_runner.run(
                "gemini_force_video",
                lambda _timeout: self.gemini_client.analyze(
                    GeminiSegmentRequest(
                        frames=selected,
                        ocr_text=ocr_text,
                        local_analysis=local_analysis,
                    )
                ),
                StagePolicy(90, 1),
            )
            cloud_analysis = cloud.value if cloud.ok else None
            if cloud_analysis is None:
                failures += 1
            self.store.save_segment_result(
                segment.segment_id,
                status="completed",
                selected_frames=selected,
                local_analysis=local_analysis,
                cloud_analysis=cloud_analysis,
                needs_review=cloud_analysis is None,
                error=(
                    None
                    if cloud_analysis is not None
                    else f"cloud_failed:{cloud.error_type or 'unknown'}"
                ),
                retry_count_increment=max(0, cloud.attempts - 1),
            )
        return failures

    def _video_ocr(self, source: Path) -> str:
        if self.evidence_extractor is None:
            return ""
        evidence = self.stage_runner.run(
            "video_evidence",
            lambda _timeout: self.evidence_extractor.extract(source),
            StagePolicy(1200, 1),
        )
        if not evidence.ok or evidence.value is None:
            return ""
        return "；".join(evidence.value.ocr_text)

    @staticmethod
    def _segment_range(segment: VideoSegment) -> SegmentRange:
        return SegmentRange(segment.start_seconds, segment.end_seconds)

    @staticmethod
    def _best_analysis(segment: VideoSegment) -> Analysis:
        raw = segment.cloud_result_json or segment.local_result_json
        if not raw:
            raise AnalysisError(
                f"segment {segment.segment_index} has no saved analysis"
            )
        return LocalAnalyzer._parse_analysis(raw)
