from pathlib import Path

from media_catalog.force_gemini import ForceGeminiEstimate, plan_force_run
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
