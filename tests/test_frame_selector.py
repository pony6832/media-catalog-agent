from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from media_catalog.frame_selector import FrameSelector


def _make_candidate_images(tmp_path: Path, difference: str) -> tuple[Path, ...]:
    levels = {
        "low": (96, 96, 96),
        "medium": (60, 90, 120),
        "high": (0, 128, 255),
    }[difference]
    paths = tuple(tmp_path / f"{index}.jpg" for index in range(3))
    for path, level in zip(paths, levels, strict=True):
        Image.new("L", (64, 64), level).save(path, quality=100)
    return paths


@pytest.mark.parametrize(
    ("difference", "ocr", "expected"),
    (
        ("low", "", 1),
        ("medium", "標題", 2),
        ("high", "大量文字" * 30, 3),
    ),
)
def test_selector_returns_one_to_three_distinct_frames(
    difference: str,
    ocr: str,
    expected: int,
    tmp_path: Path,
) -> None:
    candidates = _make_candidate_images(tmp_path, difference)

    selected = FrameSelector().select(candidates, ocr_text=ocr)

    assert len(selected) == expected
    assert len(set(selected)) == expected
    assert set(selected) <= set(candidates)


def test_selector_deduplicates_paths_and_uses_stable_tie_breaker(
    tmp_path: Path,
) -> None:
    candidates = _make_candidate_images(tmp_path, "low")

    selected = FrameSelector().select(
        (candidates[2], candidates[0], candidates[0], candidates[1])
    )

    assert selected == (candidates[0],)
