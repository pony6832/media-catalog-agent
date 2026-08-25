from pathlib import Path

from media_catalog.force_gemini import (
    ForceGeminiEstimate,
    ForceImageAnalyzer,
    ForceImageResult,
    plan_force_run,
)
from media_catalog.inference import Analysis
from media_catalog.stage_runner import StageRunner
from media_catalog.models import MediaRecord, Status


def _record(identity: str, path: Path, media_type: str) -> MediaRecord:
    return MediaRecord(
        id=identity,
        path=path,
        fingerprint=f"fingerprint-{identity}",
        media_type=media_type,
        status=Status.ANALYZED,
        description="舊描述",
        highlights=("舊重點",),
        keywords=("舊關鍵字",),
    )


def test_force_plan_excludes_reviewed_and_reports_both_limits(
    tmp_path: Path,
) -> None:
    records = (
        _record("photo", tmp_path / "a.jpg", "image/jpeg"),
        _record("video", tmp_path / "b.mp4", "video/mp4"),
        _record("reviewed", tmp_path / "c.jpg", "image/jpeg"),
    )

    estimate, eligible = plan_force_run(
        records, {str((tmp_path / "c.jpg").resolve())}
    )

    assert eligible == ("photo", "video")
    assert estimate == ForceGeminiEstimate(
        video_count=1,
        image_count=1,
        reviewed_count=1,
        normal_request_limit=13,
        retry_attempt_limit=26,
    )


def test_force_plan_matches_reviewed_paths_case_insensitively(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Photo.JPG"

    estimate, eligible = plan_force_run(
        (_record("photo", source, "image/jpeg"),),
        {str(source.resolve()).upper()},
    )

    assert eligible == ()
    assert estimate.reviewed_count == 1
    assert estimate.normal_request_limit == 0


class _LocalAnalyzer:
    def __init__(self, result: Analysis) -> None:
        self.result = result
        self.calls: list[Path] = []

    def analyze(self, source: Path) -> Analysis:
        self.calls.append(source)
        return self.result


class _ImagePreparer:
    def __init__(self, preview: Path) -> None:
        self.preview = preview
        self.calls: list[Path] = []

    def prepare(self, source: Path) -> Path:
        self.calls.append(source)
        return self.preview


class _GeminiClient:
    def __init__(self, result: Analysis | Exception) -> None:
        self.result = result
        self.requests = []

    def analyze(self, request):
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_force_image_uses_gemini_result_after_local_analysis(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    preview = tmp_path / "preview.jpg"
    local = Analysis("本機描述", ("本機重點",), ("本機",))
    cloud = Analysis("Gemini 描述", ("雲端重點",), ("Gemini",))
    local_analyzer = _LocalAnalyzer(local)
    image_preparer = _ImagePreparer(preview)
    gemini = _GeminiClient(cloud)

    result = ForceImageAnalyzer(
        local_analyzer=local_analyzer,
        image_preparer=image_preparer,
        gemini_client=gemini,
        stage_runner=StageRunner(),
    ).analyze(source)

    assert result == ForceImageResult(cloud, warning=None, gemini_used=True)
    assert local_analyzer.calls == [source]
    assert image_preparer.calls == [source]
    assert len(gemini.requests) == 1
    assert gemini.requests[0].frames == (preview,)
    assert gemini.requests[0].local_analysis == local


def test_force_image_retries_then_keeps_local_result_with_warning(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    local = Analysis("可用的本機描述", ("本機重點",), ("本機",))
    gemini = _GeminiClient(RuntimeError("temporary provider failure"))

    result = ForceImageAnalyzer(
        local_analyzer=_LocalAnalyzer(local),
        image_preparer=_ImagePreparer(tmp_path / "preview.jpg"),
        gemini_client=gemini,
        stage_runner=StageRunner(),
    ).analyze(source)

    assert result.analysis == local
    assert result.gemini_used is False
    assert result.warning == "Gemini 強化失敗:RuntimeError"
    assert len(gemini.requests) == 2
