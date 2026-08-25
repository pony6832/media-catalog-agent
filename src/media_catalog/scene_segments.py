from __future__ import annotations

import math
import os
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


Runner = Callable[..., subprocess.CompletedProcess[str]]
SCENE_THRESHOLD = 0.35
MAX_SEGMENT_SECONDS = 300.0
MIN_TAIL_SECONDS = 2.0
_PTS_TIME = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


class SceneSegmentationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SegmentRange:
    start_seconds: float
    end_seconds: float

    @property
    def duration(self) -> float:
        return self.end_seconds - self.start_seconds


def build_ranges(
    duration: float,
    scene_times: Iterable[float],
    max_seconds: float = MAX_SEGMENT_SECONDS,
    min_tail: float = MIN_TAIL_SECONDS,
) -> tuple[SegmentRange, ...]:
    duration = float(duration)
    max_seconds = float(max_seconds)
    min_tail = float(min_tail)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be a positive finite number")
    if not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("max_seconds must be a positive finite number")
    if not math.isfinite(min_tail) or min_tail < 0 or min_tail > max_seconds:
        raise ValueError("min_tail must be between zero and max_seconds")

    boundaries = sorted(
        {
            float(item)
            for item in scene_times
            if math.isfinite(float(item)) and 0 < float(item) < duration
        }
    )
    points = (0.0, *boundaries, duration)
    ranges: list[SegmentRange] = []
    for raw_start, raw_end in zip(points, points[1:]):
        start = raw_start
        while raw_end - start > max_seconds:
            end = start + max_seconds
            ranges.append(SegmentRange(start, end))
            start = end
        if raw_end > start:
            ranges.append(SegmentRange(start, raw_end))

    if len(ranges) > 1 and ranges[-1].duration < min_tail:
        previous = ranges[-2]
        tail = ranges[-1]
        if tail.end_seconds - previous.start_seconds <= max_seconds:
            ranges[-2:] = [
                SegmentRange(previous.start_seconds, tail.end_seconds)
            ]
        else:
            adjusted_boundary = tail.end_seconds - min_tail
            ranges[-2] = SegmentRange(
                previous.start_seconds, adjusted_boundary
            )
            ranges[-1] = SegmentRange(adjusted_boundary, tail.end_seconds)
    return tuple(ranges)


class SceneSegmenter:
    def __init__(
        self,
        *,
        ffmpeg_executable: str = "ffmpeg",
        ffprobe_executable: str = "ffprobe",
        runner: Runner = subprocess.run,
        timeout_seconds: float = 1200,
    ) -> None:
        self.ffmpeg_executable = ffmpeg_executable
        self.ffprobe_executable = ffprobe_executable
        self.runner = runner
        self.timeout_seconds = timeout_seconds

    def segment(self, source: Path) -> tuple[SegmentRange, ...]:
        media_path = self._source_file(source)
        probe = self._run(
            [
                self.ffprobe_executable,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ]
        )
        if probe.returncode != 0:
            raise SceneSegmentationError(
                f"ffprobe failed with exit code {probe.returncode}"
            )
        try:
            duration = float(probe.stdout.strip())
        except ValueError as error:
            raise SceneSegmentationError(
                "ffprobe returned an invalid duration"
            ) from error

        scene_probe = self._run(
            [
                self.ffmpeg_executable,
                "-hide_banner",
                "-i",
                str(media_path),
                "-vf",
                f"select=gt(scene\\,{SCENE_THRESHOLD}),showinfo",
                "-an",
                "-f",
                "null",
                "NUL" if os.name == "nt" else "/dev/null",
            ]
        )
        if scene_probe.returncode != 0:
            raise SceneSegmentationError(
                f"ffmpeg scene probe failed with exit code "
                f"{scene_probe.returncode}"
            )
        raw_log = f"{scene_probe.stdout}\n{scene_probe.stderr}"
        scene_times = tuple(
            float(match.group(1)) for match in _PTS_TIME.finditer(raw_log)
        )
        return build_ranges(duration, scene_times)

    def extract_candidates(
        self,
        source: Path,
        segment: SegmentRange,
        output_dir: Path,
    ) -> tuple[Path, ...]:
        media_path = self._source_file(source)
        if segment.duration <= 0:
            raise ValueError("segment duration must be positive")
        destination = Path(output_dir).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        candidates: list[Path] = []
        for percentage in (20, 50, 80):
            timestamp = segment.start_seconds + (
                segment.duration * percentage / 100
            )
            output = destination / f"frame-{percentage:03d}.jpg"
            result = self._run(
                [
                    self.ffmpeg_executable,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{timestamp:.6f}",
                    "-i",
                    str(media_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=1280:1280:force_original_aspect_ratio=decrease",
                    "-q:v",
                    "2",
                    "-y",
                    str(output),
                ]
            )
            if result.returncode != 0:
                raise SceneSegmentationError(
                    f"ffmpeg frame extraction failed with exit code "
                    f"{result.returncode}"
                )
            if not output.is_file():
                raise SceneSegmentationError(
                    "ffmpeg frame extraction produced no image"
                )
            self._normalize_preview(output)
            candidates.append(output)
        return tuple(candidates)

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                arguments,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SceneSegmentationError(
                f"media tool failed: {type(error).__name__}"
            ) from error

    @staticmethod
    def _source_file(source: Path) -> Path:
        media_path = Path(source).resolve()
        if not media_path.is_file():
            raise FileNotFoundError(media_path)
        return media_path

    @staticmethod
    def _normalize_preview(path: Path) -> None:
        try:
            with Image.open(path) as original:
                image = original.convert("RGB")
                image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            image.save(path, format="JPEG", quality=88, optimize=True)
        except OSError as error:
            raise SceneSegmentationError(
                "extracted frame is not a readable image"
            ) from error
