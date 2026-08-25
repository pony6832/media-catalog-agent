from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence
from urllib.parse import urlparse


Runner = Callable[..., subprocess.CompletedProcess[str]]
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
SEMANTIC_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


class AnalysisError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Analysis:
    description: str
    highlights: tuple[str, ...]
    keywords: tuple[str, ...]


_GENERIC_KEYWORDS = {"內容", "畫面", "場景", "影像", "人物"}
_CONFLICTING_VISUALS = (
    ("白天", "夜晚"),
    ("室內", "室外"),
    ("單人", "多人"),
)


def assess_analysis_quality(
    analysis: Analysis,
    *,
    ocr_text: str,
    frame_count: int,
    frame_summaries: Sequence[str] = (),
) -> tuple[str, ...]:
    issues: list[str] = []
    description = "".join(analysis.description.split())
    if len(description) < 12:
        issues.append("generic_description")

    normalized_highlights = [
        "".join(item.split()).casefold() for item in analysis.highlights
    ]
    if len(set(normalized_highlights)) < len(normalized_highlights):
        issues.append("duplicate_highlights")

    normalized_keywords = {
        "".join(item.split()).casefold() for item in analysis.keywords
    }
    if normalized_keywords and normalized_keywords <= {
        item.casefold() for item in _GENERIC_KEYWORDS
    }:
        issues.append("generic_keywords")

    compact_ocr = "".join(ocr_text.split())
    if len(compact_ocr) >= 80:
        main_tokens = _main_ocr_tokens(ocr_text)
        normalized_description = description.casefold()
        if main_tokens and not any(
            token in normalized_description for token in main_tokens
        ):
            issues.append("ocr_not_reflected")

    if frame_count >= 3 and len(frame_summaries) >= 2:
        combined = " ".join(frame_summaries).casefold()
        if any(
            left.casefold() in combined and right.casefold() in combined
            for left, right in _CONFLICTING_VISUALS
        ):
            issues.append("conflicting_frames")
    return tuple(issues)


def _main_ocr_tokens(ocr_text: str) -> tuple[str, ...]:
    counts: Counter[str] = Counter()
    chunks = re.findall(
        r"[A-Za-z0-9]+|[\u3400-\u9fff]+", ocr_text.casefold()
    )
    for chunk in chunks:
        if re.fullmatch(r"[\u3400-\u9fff]+", chunk):
            if len(chunk) == 1:
                counts[chunk] += 1
            else:
                counts.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
        elif len(chunk) >= 2:
            counts[chunk] += 1
    return tuple(token for token, _count in counts.most_common(3))


@dataclass(frozen=True, slots=True)
class VideoEvidence:
    frames: tuple[Path, ...]
    metadata: dict[str, object]
    ocr_text: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class VideoExtractor(Protocol):
    def extract(self, source: str | Path) -> VideoEvidence:
        raise NotImplementedError


class ImagePreparer(Protocol):
    def prepare(self, source: Path) -> Path:
        raise NotImplementedError


class FallbackVideoExtractor:
    def __init__(
        self,
        primary: VideoExtractor | None,
        fallback: VideoExtractor | None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def extract(self, source: str | Path) -> VideoEvidence:
        failures: list[str] = []
        for name, extractor in (
            ("watch", self.primary),
            ("mcp", self.fallback),
        ):
            if extractor is None:
                failures.append(f"{name} unavailable")
                continue
            try:
                return extractor.extract(source)
            except AnalysisError as error:
                failures.append(f"{name}: {error}")
        raise AnalysisError("; ".join(failures))


def _local_media_path(source: str | Path) -> Path:
    raw = str(source)
    if urlparse(raw).scheme.lower() in {"http", "https"}:
        raise ValueError("Only local media files are allowed")
    path = Path(raw).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _output_directory(output_root: Path, source: Path, backend: str) -> Path:
    key = hashlib.sha256(str(source).casefold().encode("utf-8")).hexdigest()[:16]
    destination = Path(output_root).resolve() / f"{backend}-{key}"
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _offline_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in (
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "TWELVELABS_API_KEY",
        "MCP_WRITE_SIDECARS",
    ):
        environment.pop(key, None)
    environment.update(
        {
            "npm_config_offline": "true",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYTHONUTF8": "1",
        }
    )
    return environment


class WatchVideoExtractor:
    """Invoke watch-skill once; watch is the interface to claude-video."""

    def __init__(
        self,
        *,
        script_path: Path,
        output_root: Path,
        python_executable: str = sys.executable,
        runner: Runner = subprocess.run,
        timeout: float = 300,
    ) -> None:
        self.script_path = Path(script_path).resolve()
        self.output_root = Path(output_root)
        self.python_executable = python_executable
        self.runner = runner
        self.timeout = timeout

    def extract(self, source: str | Path) -> VideoEvidence:
        media_path = _local_media_path(source)
        if not self.script_path.is_file():
            raise FileNotFoundError(self.script_path)
        output_directory = _output_directory(
            self.output_root, media_path, "watch"
        )
        arguments = [
            self.python_executable,
            str(self.script_path),
            str(media_path),
            "--detail",
            "efficient",
            "--max-frames",
            "12",
            "--no-whisper",
            "--out-dir",
            str(output_directory),
        ]
        self._run(arguments)
        frames = tuple(sorted(output_directory.rglob("*.jpg")))
        if not frames:
            raise AnalysisError("watch-skill produced no representative frames")
        return VideoEvidence(frames=frames, metadata={"backend": "watch"})

    def _run(self, arguments: list[str]) -> None:
        try:
            result = self.runner(
                arguments,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
                env=_offline_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AnalysisError(f"watch-skill failed: {error}") from error
        if result.returncode != 0:
            raise AnalysisError(
                f"watch-skill failed with exit code {result.returncode}: "
                f"{result.stderr.strip()}"
            )


class McpVideoExtractor:
    def __init__(
        self,
        *,
        executable_path: Path,
        package_json_path: Path,
        expected_version: str,
        output_root: Path,
        runner: Runner = subprocess.run,
        timeout: float = 300,
    ) -> None:
        if not SEMANTIC_VERSION.fullmatch(expected_version):
            raise ValueError("mcp-video-analyzer requires a pinned semantic version")
        self.executable_path = Path(executable_path).resolve()
        self.package_json_path = Path(package_json_path).resolve()
        if not self.executable_path.is_file():
            raise FileNotFoundError(self.executable_path)
        if not self.package_json_path.is_file():
            raise FileNotFoundError(self.package_json_path)
        try:
            package = json.loads(self.package_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Invalid mcp-video-analyzer package.json") from error
        if package.get("name") != "mcp-video-analyzer":
            raise ValueError("Configured package is not mcp-video-analyzer")
        actual_version = package.get("version")
        if actual_version != expected_version:
            raise ValueError(
                "Installed mcp-video-analyzer version "
                f"{actual_version!r} does not match pinned version {expected_version!r}"
            )
        self.expected_version = expected_version
        self.output_root = Path(output_root)
        self.runner = runner
        self.timeout = timeout

    def extract(self, source: str | Path) -> VideoEvidence:
        media_path = _local_media_path(source)
        output_directory = _output_directory(
            self.output_root, media_path, "mcp-video-analyzer"
        )
        arguments = [
            str(self.executable_path),
            "analyze",
            str(media_path),
            "--detail",
            "standard",
            "--max-frames",
            "12",
            "--fields",
            "metadata,frames,ocrResults",
            "--out",
            str(output_directory),
        ]
        try:
            result = self.runner(
                arguments,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
                env=_offline_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AnalysisError(f"mcp-video-analyzer failed: {error}") from error
        if result.returncode != 0:
            raise AnalysisError(
                f"mcp-video-analyzer failed with exit code {result.returncode}: "
                f"{result.stderr.strip()}"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise AnalysisError(
                "mcp-video-analyzer did not return valid JSON"
            ) from error
        frames = tuple(
            Path(item["filePath"]).resolve()
            for item in payload.get("frames", [])
            if isinstance(item, dict) and item.get("filePath")
        )
        ocr_text = tuple(
            item["text"].strip()
            for item in payload.get("ocrResults", [])
            if isinstance(item, dict)
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        )
        warnings = tuple(str(item) for item in payload.get("warnings", []))
        if not frames:
            raise AnalysisError("mcp-video-analyzer produced no representative frames")
        metadata = payload.get("metadata", {})
        return VideoEvidence(
            frames=frames,
            metadata=metadata if isinstance(metadata, dict) else {},
            ocr_text=ocr_text,
            warnings=warnings,
        )


class FfmpegImagePreparer:
    def __init__(
        self,
        *,
        output_root: Path,
        ffmpeg_executable: str = "ffmpeg",
        runner: Runner = subprocess.run,
        timeout: float = 60,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.ffmpeg_executable = ffmpeg_executable
        self.runner = runner
        self.timeout = timeout

    def prepare(self, source: Path) -> Path:
        media_path = _local_media_path(source)
        stat_result = media_path.stat()
        key_source = (
            f"{str(media_path).casefold()}|{stat_result.st_size}|"
            f"{stat_result.st_mtime_ns}"
        )
        key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()[:20]
        self.output_root.mkdir(parents=True, exist_ok=True)
        preview = self.output_root / f"{key}.jpg"
        if preview.is_file() and preview.stat().st_size > 0:
            return preview

        arguments = [
            self.ffmpeg_executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(media_path),
            "-vf",
            "scale=1024:1024:force_original_aspect_ratio=decrease",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(preview),
        ]
        try:
            result = self.runner(
                arguments,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
                env=_offline_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AnalysisError(f"ffmpeg preview failed: {error}") from error
        if result.returncode != 0:
            raise AnalysisError(
                f"ffmpeg preview failed with exit code {result.returncode}: "
                f"{result.stderr.strip()}"
            )
        if not preview.is_file() or preview.stat().st_size == 0:
            raise AnalysisError("ffmpeg preview produced no image")
        return preview


def _representative_paths(paths: list[Path], limit: int = 3) -> list[Path]:
    if len(paths) <= limit:
        return paths
    indexes = (0, len(paths) // 2, len(paths) - 1)
    return [paths[index] for index in indexes]


class LocalAnalyzer:
    def __init__(
        self,
        *,
        model: str,
        video_extractor: VideoExtractor | None = None,
        image_preparer: ImagePreparer | None = None,
        ollama_executable: str = "ollama",
        runner: Runner = subprocess.run,
        timeout: float = 300,
    ) -> None:
        self.model = model
        self.video_extractor = video_extractor
        self.image_preparer = image_preparer
        self.ollama_executable = ollama_executable
        self.runner = runner
        self.timeout = timeout

    def analyze(self, source: Path) -> Analysis:
        media_path = _local_media_path(source)
        visual_paths = [media_path]
        ocr_text = ""
        if media_path.suffix.casefold() in VIDEO_EXTENSIONS:
            if self.video_extractor is None:
                raise AnalysisError("A local video extractor is required for video")
            evidence = self.video_extractor.extract(media_path)
            visual_paths = _representative_paths(list(evidence.frames))
            ocr_text = "；".join(evidence.ocr_text)

        return self._analyze_visuals(visual_paths, ocr_text=ocr_text)

    def analyze_frames(
        self,
        frames: Sequence[Path],
        *,
        ocr_text: str = "",
    ) -> Analysis:
        if not 1 <= len(frames) <= 3:
            raise AnalysisError("Local segment analysis requires one to three frames")
        visual_paths = [_local_media_path(path) for path in frames]
        return self._analyze_visuals(visual_paths, ocr_text=ocr_text)

    def summarize_segments(
        self, analyses: Sequence[Analysis]
    ) -> Analysis:
        if not analyses:
            raise AnalysisError("At least one segment analysis is required")
        segment_payload = json.dumps(
            [
                {
                    "segment": index + 1,
                    "description": analysis.description,
                    "highlights": analysis.highlights,
                    "keywords": analysis.keywords,
                }
                for index, analysis in enumerate(analyses)
            ],
            ensure_ascii=False,
        )
        prompt = (
            "請只根據以下片段文字分析，彙整整支影片。只輸出單一 JSON 物件，"
            "欄位固定為 description、highlights、keywords，使用繁體中文且不得"
            f"空白。不得加入片段中沒有的內容。片段資料：{segment_payload}"
        )
        return self._invoke((), prompt)

    def _analyze_visuals(
        self, visual_paths: Sequence[Path], *, ocr_text: str
    ) -> Analysis:
        prepared_paths = list(visual_paths)

        if self.image_preparer is not None:
            prepared_paths = [
                self.image_preparer.prepare(path) for path in prepared_paths
            ]

        prompt = (
            "請只輸出單一 JSON 物件，並根據提供的本機影像填寫內容。"
            "description 必須是非空白繁體中文字串；highlights 與 keywords "
            "必須是繁體中文字串陣列。即使畫面簡單也要提供具體描述與至少一個"
            "關鍵字。不要加入 Markdown 或額外欄位。"
        )
        if ocr_text:
            prompt += " 已擷取 OCR 文字：" + ocr_text
        return self._invoke(prepared_paths, prompt)

    def _invoke(
        self, visual_paths: Sequence[Path], prompt: str
    ) -> Analysis:
        arguments = [
            self.ollama_executable,
            "run",
            self.model,
            "--format",
            "json",
            "--hidethinking",
            "--nowordwrap",
            *(str(path) for path in visual_paths),
            prompt,
        ]
        try:
            result = self.runner(
                arguments,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
                env=_offline_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AnalysisError(f"Ollama analysis failed: {error}") from error
        if result.returncode != 0:
            raise AnalysisError(
                f"Ollama failed with exit code {result.returncode}: "
                f"{result.stderr.strip()}"
            )
        return self._parse_analysis(result.stdout)

    @staticmethod
    def _parse_analysis(raw_output: str) -> Analysis:
        try:
            payload = json.loads(raw_output)
        except json.JSONDecodeError as error:
            raise AnalysisError("Ollama did not return valid JSON") from error
        description = payload.get("description") if isinstance(payload, dict) else None
        highlights = payload.get("highlights") if isinstance(payload, dict) else None
        keywords = payload.get("keywords") if isinstance(payload, dict) else None
        cleaned_highlights = (
            tuple(item.strip() for item in highlights if item.strip())
            if isinstance(highlights, list)
            and all(isinstance(item, str) for item in highlights)
            else ()
        )
        cleaned_keywords = (
            tuple(item.strip() for item in keywords if item.strip())
            if isinstance(keywords, list)
            and all(isinstance(item, str) for item in keywords)
            else ()
        )
        if (
            not isinstance(description, str)
            or not description.strip()
            or not isinstance(highlights, list)
            or not all(isinstance(item, str) for item in highlights)
            or not cleaned_highlights
            or not isinstance(keywords, list)
            or not all(isinstance(item, str) for item in keywords)
            or not cleaned_keywords
        ):
            raise AnalysisError("Ollama JSON does not match the analysis schema")
        return Analysis(
            description=description.strip(),
            highlights=cleaned_highlights,
            keywords=cleaned_keywords,
        )
