from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .models import MediaRecord


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
