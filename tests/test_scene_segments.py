from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from media_catalog.scene_segments import (
    SceneSegmenter,
    SegmentRange,
    build_ranges,
)


def test_scene_boundaries_are_split_and_tail_is_not_too_short() -> None:
    ranges = build_ranges(
        duration=607,
        scene_times=(10, 310),
        max_seconds=300,
        min_tail=2,
    )

    assert ranges[0] == SegmentRange(0, 10)
    assert all(item.duration <= 300 for item in ranges)
    assert ranges[-1].duration >= 2
    assert ranges[-1].end_seconds == 607


def test_static_video_is_forced_into_five_minute_segments() -> None:
    assert build_ranges(720, (), 300, 2) == (
        SegmentRange(0, 300),
        SegmentRange(300, 600),
        SegmentRange(600, 720),
    )


def test_one_second_tail_is_rebalanced_without_exceeding_maximum() -> None:
    ranges = build_ranges(601, (), 300, 2)

    assert ranges[-1].duration == 2
    assert all(item.duration <= 300 for item in ranges)
    assert ranges[0].start_seconds == 0
    assert ranges[-1].end_seconds == 601


def test_segmenter_parses_scene_times_and_extracts_bounded_candidates(
    tmp_path: Path,
) -> None:
    source = tmp_path / "long clip.mp4"
    source.write_bytes(b"original-video")
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    calls: list[list[str]] = []

    def runner(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments[0] == "ffprobe.exe":
            return subprocess.CompletedProcess(
                arguments, 0, stdout="607.0\n", stderr=""
            )
        if "-f" in arguments and "null" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout="",
                stderr="showinfo pts_time:10.0\nshowinfo pts_time:310.0\n",
            )
        output = Path(arguments[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1600, 900), "navy").save(output)
        return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

    segmenter = SceneSegmenter(
        ffmpeg_executable="ffmpeg.exe",
        ffprobe_executable="ffprobe.exe",
        runner=runner,
    )
    ranges = segmenter.segment(source)
    candidates = segmenter.extract_candidates(
        source, ranges[1], tmp_path / "frames"
    )

    assert ranges[0] == SegmentRange(0, 10)
    assert len(candidates) == 3
    assert [path.name for path in candidates] == [
        "frame-020.jpg",
        "frame-050.jpg",
        "frame-080.jpg",
    ]
    assert all(max(Image.open(path).size) <= 1280 for path in candidates)
    seek_times = [
        float(call[call.index("-ss") + 1])
        for call in calls
        if "-ss" in call
    ]
    assert seek_times == [70.0, 160.0, 250.0]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg integration tools are unavailable",
)
def test_real_ffmpeg_scene_pipeline_preserves_synthetic_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic-scenes.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=2:r=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x240:d=2:r=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=2:r=10",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
            "-map",
            "[v]",
            "-y",
            str(source),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    segmenter = SceneSegmenter(timeout_seconds=30)
    ranges = segmenter.segment(source)
    frames = segmenter.extract_candidates(
        source, ranges[0], tmp_path / "frames"
    )

    assert len(ranges) >= 2
    assert ranges[0].end_seconds == pytest.approx(4.0, abs=0.1)
    assert len(frames) == 3
    assert all(max(Image.open(path).size) <= 1280 for path in frames)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash
