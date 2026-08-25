from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .gemini_client import GeminiClient, GeminiSegmentRequest
from .inference import Analysis, ImagePreparer, LocalAnalyzer
from .models import MediaRecord
from .run_state import VideoSegment
from .stage_runner import StagePolicy, StageRunner


@dataclass(frozen=True, slots=True)
class ForceImageResult:
    analysis: Analysis
    warning: str | None
    gemini_used: bool


class ForceImageAnalyzer:
    def __init__(
        self,
        *,
        local_analyzer: LocalAnalyzer,
        image_preparer: ImagePreparer,
        gemini_client: GeminiClient,
        stage_runner: StageRunner,
    ) -> None:
        self.local_analyzer = local_analyzer
        self.image_preparer = image_preparer
        self.gemini_client = gemini_client
        self.stage_runner = stage_runner

    def analyze(self, source: Path) -> ForceImageResult:
        local = self.local_analyzer.analyze(source)
        preview = self.image_preparer.prepare(source)
        request = GeminiSegmentRequest(
            frames=(preview,),
            ocr_text="",
            local_analysis=local,
        )
        cloud = self.stage_runner.run(
            "gemini_force_image",
            lambda _timeout: self.gemini_client.analyze(request),
            StagePolicy(timeout_seconds=90, retries=1),
        )
        if cloud.ok and cloud.value is not None:
            return ForceImageResult(
                analysis=cloud.value,
                warning=None,
                gemini_used=True,
            )
        error_type = cloud.error_type or "UnknownError"
        return ForceImageResult(
            analysis=local,
            warning=f"Gemini 強化失敗:{error_type}",
            gemini_used=False,
        )


def _spread_segments(
    segments: tuple[VideoSegment, ...], limit: int
) -> tuple[VideoSegment, ...]:
    if limit <= 0 or not segments:
        return ()
    if len(segments) <= limit:
        return segments
    if limit == 1:
        return (segments[len(segments) // 2],)
    indexes = tuple(
        round(index * (len(segments) - 1) / (limit - 1))
        for index in range(limit)
    )
    return tuple(segments[index] for index in indexes)


def select_force_segments(
    segments: Iterable[VideoSegment], *, limit: int = 12
) -> tuple[VideoSegment, ...]:
    if limit < 1:
        raise ValueError("force Gemini segment limit must be positive")
    ordered = tuple(sorted(segments, key=lambda item: item.segment_index))
    issues = tuple(
        item for item in ordered if item.needs_review or item.error is not None
    )
    if len(issues) >= limit:
        return tuple(
            sorted(
                _spread_segments(issues, limit),
                key=lambda item: item.segment_index,
            )
        )
    issue_ids = {item.segment_id for item in issues}
    remaining = tuple(
        item for item in ordered if item.segment_id not in issue_ids
    )
    selected = issues + _spread_segments(remaining, limit - len(issues))
    return tuple(sorted(selected, key=lambda item: item.segment_index))


@dataclass(frozen=True, slots=True)
class ForceGeminiEstimate:
    video_count: int
    image_count: int
    reviewed_count: int
    normal_request_limit: int
    retry_attempt_limit: int


def _normalized_path(value: str | Path) -> str:
    return str(Path(value).resolve()).casefold()


def plan_force_run(
    records: Iterable[MediaRecord], reviewed_paths: set[str]
) -> tuple[ForceGeminiEstimate, tuple[str, ...]]:
    reviewed = {_normalized_path(item) for item in reviewed_paths}
    catalog = tuple(records)
    supported = tuple(
        item
        for item in catalog
        if item.media_type.startswith(("image/", "video/"))
    )
    eligible = tuple(
        item
        for item in supported
        if _normalized_path(item.path) not in reviewed
    )
    video_count = sum(
        item.media_type.startswith("video/") for item in eligible
    )
    image_count = sum(
        item.media_type.startswith("image/") for item in eligible
    )
    reviewed_count = sum(
        _normalized_path(item.path) in reviewed for item in supported
    )
    normal_limit = image_count + video_count * 12
    return (
        ForceGeminiEstimate(
            video_count=video_count,
            image_count=image_count,
            reviewed_count=reviewed_count,
            normal_request_limit=normal_limit,
            retry_attempt_limit=normal_limit * 2,
        ),
        tuple(item.id for item in eligible),
    )
