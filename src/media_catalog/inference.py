from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
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


@dataclass(frozen=True, slots=True)
class VideoEvidence:
    frames: tuple[Path, ...]
    metadata: dict[str, object]
    ocr_text: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class VideoExtractor(Protocol):
    def extract(self, source: str | Path) -> VideoEvidence:
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


class LocalAnalyzer:
    def __init__(
        self,
        *,
        model: str,
        video_extractor: VideoExtractor | None = None,
        ollama_executable: str = "ollama",
        runner: Runner = subprocess.run,
        timeout: float = 300,
    ) -> None:
        self.model = model
        self.video_extractor = video_extractor
        self.ollama_executable = ollama_executable
        self.runner = runner
        self.timeout = timeout

    def analyze(self, source: Path) -> Analysis:
        media_path = _local_media_path(source)
        visual_paths = [media_path]
        ocr_text: tuple[str, ...] = ()
        if media_path.suffix.casefold() in VIDEO_EXTENSIONS:
            if self.video_extractor is None:
                raise AnalysisError("A local video extractor is required for video")
            evidence = self.video_extractor.extract(media_path)
            visual_paths = list(evidence.frames)
            ocr_text = evidence.ocr_text

        prompt = (
            "請只輸出單一 JSON 物件，並根據提供的本機影像填寫內容。"
            "description 必須是非空白繁體中文字串；highlights 與 keywords "
            "必須是繁體中文字串陣列。即使畫面簡單也要提供具體描述與至少一個"
            "關鍵字。不要加入 Markdown 或額外欄位。"
        )
        if ocr_text:
            prompt += " 已擷取 OCR 文字：" + "；".join(ocr_text)
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
        if (
            not isinstance(description, str)
            or not description.strip()
            or not isinstance(highlights, list)
            or not all(isinstance(item, str) for item in highlights)
            or not isinstance(keywords, list)
            or not all(isinstance(item, str) for item in keywords)
        ):
            raise AnalysisError("Ollama JSON does not match the analysis schema")
        return Analysis(
            description=description.strip(),
            highlights=tuple(item.strip() for item in highlights if item.strip()),
            keywords=tuple(item.strip() for item in keywords if item.strip()),
        )
