from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from media_catalog.gemini_client import GeminiError, GeminiSegmentRequest
from media_catalog.inference import Analysis, AnalysisError
from media_catalog.models import MediaRecord, Status
from media_catalog.run_state import RunStateStore, VideoSegment
from media_catalog.scene_segments import SegmentRange
from media_catalog.segment_pipeline import SegmentPipeline


GOOD = Analysis(
    "會議室內講者正在說明第三季專案進度圖表。",
    ("第三季進度圖表",),
    ("會議", "專案進度"),
)
WEAK = Analysis("一個場景。", ("畫面",), ("內容",))
STRONG = Analysis(
    "舞台上講者展示年度成果，背景螢幕可見專案標題。",
    ("年度成果簡報",),
    ("舞台", "年度成果"),
)


class FakeSegmenter:
    def __init__(self, count: int, *, forbid_segment: bool = False) -> None:
        self.count = count
        self.forbid_segment = forbid_segment
        self.segment_calls = 0

    def segment(self, _source: Path) -> tuple[SegmentRange, ...]:
        self.segment_calls += 1
        if self.forbid_segment:
            raise AssertionError("completed checkpoint should be reused")
        return tuple(
            SegmentRange(index * 10, (index + 1) * 10)
            for index in range(self.count)
        )

    def extract_candidates(
        self, _source: Path, segment: SegmentRange, output_dir: Path
    ) -> tuple[Path, ...]:
        output_dir.mkdir(parents=True, exist_ok=True)
        frames = tuple(output_dir / f"frame-{index}.jpg" for index in range(3))
        level = int(segment.start_seconds) % 255
        for index, frame in enumerate(frames):
            Image.new("L", (32, 32), min(level + index * 100, 255)).save(frame)
        return frames


class ThreeFrameSelector:
    def select(self, candidates, *, ocr_text: str = ""):
        return tuple(candidates)[:3]


class RecordingLocalAnalyzer:
    def __init__(self, result: Analysis) -> None:
        self.result = result
        self.segment_indexes: list[int] = []
        self.summary_calls = 0

    def analyze_frames(
        self,
        frames: tuple[Path, ...],
        *,
        ocr_text: str = "",
    ) -> Analysis:
        segment_name = frames[0].parent.name
        self.segment_indexes.append(int(segment_name.split("-")[-1]))
        return self.result

    def summarize_segments(self, analyses: tuple[Analysis, ...]) -> Analysis:
        self.summary_calls += 1
        assert analyses
        return STRONG


class FailsOnceLocalAnalyzer(RecordingLocalAnalyzer):
    def __init__(self) -> None:
        super().__init__(GOOD)
        self.calls = 0

    def analyze_frames(self, frames, *, ocr_text: str = "") -> Analysis:
        self.calls += 1
        if self.calls == 1:
            raise AnalysisError("temporary local failure")
        return super().analyze_frames(frames, ocr_text=ocr_text)


class RecordingGemini:
    def __init__(self) -> None:
        self.requests: list[GeminiSegmentRequest] = []

    def analyze(self, request: GeminiSegmentRequest) -> Analysis:
        self.requests.append(request)
        return STRONG


class ForbiddenGemini:
    def analyze(self, _request: GeminiSegmentRequest) -> Analysis:
        raise AssertionError("Gemini must not be called for a good local result")


class DisabledGemini(ForbiddenGemini):
    is_configured = False


class FailingGemini:
    is_configured = True

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, _request: GeminiSegmentRequest) -> Analysis:
        self.calls += 1
        raise GeminiError("provider unavailable")


def _video_record(tmp_path: Path) -> MediaRecord:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video-source")
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    return MediaRecord(
        id="video-1",
        path=source,
        fingerprint=fingerprint,
        media_type="video/mp4",
        status=Status.PENDING,
    )


def _store(tmp_path: Path) -> RunStateStore:
    store = RunStateStore(tmp_path / "catalog.sqlite")
    store.create_run(
        "run-1",
        root_path=tmp_path,
        video_count=1,
        image_count=0,
        total_bytes=12,
    )
    return store


def test_good_local_segments_never_call_gemini(tmp_path: Path) -> None:
    local = RecordingLocalAnalyzer(GOOD)
    pipeline = SegmentPipeline(
        segmenter=FakeSegmenter(2),
        selector=ThreeFrameSelector(),
        local_analyzer=local,
        gemini_client=ForbiddenGemini(),
        store=_store(tmp_path),
        output_root=tmp_path / "segments",
    )

    result = pipeline.analyze_video(_video_record(tmp_path), "run-1")

    assert result.gemini_segments == 0
    assert result.needs_review_segments == 0
    assert local.segment_indexes == [0, 1]


def test_only_twelve_segments_can_use_gemini_across_resume(
    tmp_path: Path,
) -> None:
    gemini = RecordingGemini()
    pipeline = SegmentPipeline(
        segmenter=FakeSegmenter(13),
        selector=ThreeFrameSelector(),
        local_analyzer=RecordingLocalAnalyzer(WEAK),
        gemini_client=gemini,
        store=_store(tmp_path),
        output_root=tmp_path / "segments",
    )

    result = pipeline.analyze_video(_video_record(tmp_path), "run-1")

    assert result.gemini_segments == 12
    assert result.needs_review_segments == 1
    assert sum(len(request.frames) for request in gemini.requests) == 36
    assert (
        RunStateStore(tmp_path / "catalog.sqlite").consume_gemini_slot(
            "video-1"
        )
        is False
    )


def test_resume_skips_completed_segments_and_requeues_processing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    segments = tuple(
        VideoSegment(
            f"video-1:{index}",
            "run-1",
            "video-1",
            index,
            index * 10,
            (index + 1) * 10,
        )
        for index in range(5)
    )
    store.upsert_segments("run-1", "video-1", segments)
    for index in (0, 1):
        store.save_segment_result(
            f"video-1:{index}",
            status="completed",
            selected_frames=(),
            local_analysis=GOOD,
            cloud_analysis=None,
            needs_review=False,
        )
    store.mark_segment_status("video-1:2", "processing")
    local = RecordingLocalAnalyzer(GOOD)
    pipeline = SegmentPipeline(
        segmenter=FakeSegmenter(5, forbid_segment=True),
        selector=ThreeFrameSelector(),
        local_analyzer=local,
        gemini_client=ForbiddenGemini(),
        store=store,
        output_root=tmp_path / "segments",
    )

    pipeline.analyze_video(_video_record(tmp_path), "run-1")

    assert local.segment_indexes == [2, 3, 4]
    assert all(
        segment.status == "completed"
        for segment in store.list_segments("video-1")
    )
    assert store.list_segments("video-1")[2].crash_count == 1


def test_successful_retry_is_persisted_on_segment(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pipeline = SegmentPipeline(
        segmenter=FakeSegmenter(1),
        selector=ThreeFrameSelector(),
        local_analyzer=FailsOnceLocalAnalyzer(),
        gemini_client=ForbiddenGemini(),
        store=store,
        output_root=tmp_path / "segments",
    )

    pipeline.analyze_video(_video_record(tmp_path), "run-1")

    segment = store.list_segments("video-1")[0]
    assert segment.status == "completed"
    assert segment.retry_count == 1
    run = store.get_run("run-1")
    assert run is not None and run.current_segment_id is None


def test_unconfigured_gemini_does_not_consume_persisted_quota(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    pipeline = SegmentPipeline(
        segmenter=FakeSegmenter(1),
        selector=ThreeFrameSelector(),
        local_analyzer=RecordingLocalAnalyzer(WEAK),
        gemini_client=DisabledGemini(),
        store=store,
        output_root=tmp_path / "segments",
    )

    result = pipeline.analyze_video(_video_record(tmp_path), "run-1")

    assert result.gemini_segments == 0
    assert result.needs_review_segments == 1
    assert store.consume_gemini_slot("video-1", frame_count=3) is True


def test_cloud_failure_keeps_local_checkpoint_and_marks_review(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    gemini = FailingGemini()
    pipeline = SegmentPipeline(
        segmenter=FakeSegmenter(1),
        selector=ThreeFrameSelector(),
        local_analyzer=RecordingLocalAnalyzer(WEAK),
        gemini_client=gemini,
        store=store,
        output_root=tmp_path / "segments",
    )

    result = pipeline.analyze_video(_video_record(tmp_path), "run-1")

    segment = store.list_segments("video-1")[0]
    assert result.needs_review_segments == 1
    assert segment.status == "completed"
    assert segment.local_result_json is not None
    assert segment.cloud_result_json is None
    assert segment.error == "cloud_failed:GeminiError"
    assert gemini.calls == 2
