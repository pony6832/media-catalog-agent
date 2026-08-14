import json
import subprocess
from pathlib import Path

import pytest

from media_catalog.inference import (
    AnalysisError,
    FallbackVideoExtractor,
    LocalAnalyzer,
    McpVideoExtractor,
    VideoEvidence,
    WatchVideoExtractor,
)


class RecordingExtractor:
    def __init__(self, evidence: VideoEvidence) -> None:
        self.evidence = evidence
        self.sources: list[Path] = []

    def extract(self, source: str | Path) -> VideoEvidence:
        self.sources.append(Path(source).resolve())
        return self.evidence


class FailingExtractor:
    def __init__(self, message: str) -> None:
        self.message = message

    def extract(self, _: str | Path) -> VideoEvidence:
        raise AnalysisError(self.message)


def test_watch_extractor_uses_claude_video_once_without_whisper(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    script = tmp_path / "watch.py"
    script.write_text("# test command target", encoding="utf-8")
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def runner(
        arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        environments.append(kwargs["env"])
        out_dir = Path(arguments[arguments.index("--out-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "frame_0001.jpg").write_bytes(b"frame")
        return subprocess.CompletedProcess(arguments, 0, stdout="ok", stderr="")

    evidence = WatchVideoExtractor(
        script_path=script,
        output_root=tmp_path / "watch-output",
        python_executable="python.exe",
        runner=runner,
    ).extract(source)

    assert len(calls) == 1
    assert "--no-whisper" in calls[0]
    assert "--detail" in calls[0]
    assert environments[0]["PYTHONUTF8"] == "1"
    assert evidence.frames[0].name == "frame_0001.jpg"


def test_mcp_extractor_is_pinned_offline_and_normalizes_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    package_root = tmp_path / "mcp-package"
    package_root.mkdir()
    executable = package_root / "mcp-video-analyzer.cmd"
    executable.write_text("@echo off", encoding="utf-8")
    package_json = package_root / "package.json"
    package_json.write_text(
        json.dumps({"name": "mcp-video-analyzer", "version": "0.8.0"}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    cloud_keys = (
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "TWELVELABS_API_KEY",
        "MCP_WRITE_SIDECARS",
    )
    for key in cloud_keys:
        monkeypatch.setenv(key, "must-not-leak")

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["arguments"] = arguments
        captured["environment"] = kwargs["env"]
        captured["encoding"] = kwargs.get("encoding")
        out_dir = Path(arguments[arguments.index("--out") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        frame = out_dir / "scene_001.jpg"
        frame.write_bytes(b"frame")
        payload = {
            "metadata": {"duration": 12.5},
            "frames": [{"time": 1.0, "filePath": str(frame)}],
            "ocrResults": [{"text": "門牌 25 號"}],
            "warnings": [],
        }
        return subprocess.CompletedProcess(
            arguments, 0, stdout=json.dumps(payload), stderr=""
        )

    evidence = McpVideoExtractor(
        executable_path=executable,
        package_json_path=package_json,
        expected_version="0.8.0",
        output_root=tmp_path / "mcp-output",
        runner=runner,
    ).extract(source)

    arguments = captured["arguments"]
    environment = captured["environment"]
    assert isinstance(arguments, list)
    assert arguments[:2] == [str(executable.resolve()), "analyze"]
    assert "metadata,frames,ocrResults" in arguments
    assert isinstance(environment, dict)
    assert environment["npm_config_offline"] == "true"
    assert all(key not in environment for key in cloud_keys)
    assert captured["encoding"] == "utf-8"
    assert evidence.frames[0].name == "scene_001.jpg"
    assert evidence.ocr_text == ("門牌 25 號",)


def test_fallback_video_extractor_does_not_call_mcp_when_watch_succeeds(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    primary = RecordingExtractor(
        VideoEvidence((tmp_path / "watch.jpg",), {"backend": "watch"})
    )
    fallback = RecordingExtractor(
        VideoEvidence((tmp_path / "mcp.jpg",), {"backend": "mcp"})
    )

    evidence = FallbackVideoExtractor(primary, fallback).extract(source)

    assert evidence.metadata["backend"] == "watch"
    assert primary.sources == [source.resolve()]
    assert fallback.sources == []


def test_fallback_video_extractor_calls_mcp_once_after_watch_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    primary = FailingExtractor("watch produced no frames")
    fallback = RecordingExtractor(
        VideoEvidence((tmp_path / "mcp.jpg",), {"backend": "mcp"})
    )

    evidence = FallbackVideoExtractor(primary, fallback).extract(source)

    assert evidence.metadata["backend"] == "mcp"
    assert fallback.sources == [source.resolve()]


def test_fallback_video_extractor_reports_both_failures(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")

    with pytest.raises(AnalysisError) as captured:
        FallbackVideoExtractor(
            FailingExtractor("no watch frames"),
            FailingExtractor("mcp crashed"),
        ).extract(source)

    assert "watch: no watch frames" in str(captured.value)
    assert "mcp: mcp crashed" in str(captured.value)


def test_video_extractors_reject_urls_and_unpinned_latest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="local media files"):
        WatchVideoExtractor(
            script_path=tmp_path / "watch.py", output_root=tmp_path
        ).extract("https://example.com/video.mp4")

    package_root = tmp_path / "mcp-package"
    package_root.mkdir()
    executable = package_root / "mcp-video-analyzer.cmd"
    executable.write_text("@echo off", encoding="utf-8")
    package_json = package_root / "package.json"
    package_json.write_text(
        json.dumps({"name": "mcp-video-analyzer", "version": "0.8.0"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match pinned version"):
        McpVideoExtractor(
            executable_path=executable,
            package_json_path=package_json,
            expected_version="0.8.1",
            output_root=tmp_path,
        )


def test_local_analyzer_rejects_malformed_model_json(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    captured: list[str] = []

    def runner(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        captured.extend(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout="not json", stderr="")

    analyzer = LocalAnalyzer(model="qwen3-vl:8b", runner=runner)

    with pytest.raises(AnalysisError, match="valid JSON"):
        analyzer.analyze(photo)

    assert "--format" in captured
    assert "json" in captured
    assert "--hidethinking" in captured
    assert "--nowordwrap" in captured


def test_local_analyzer_uses_readable_strict_schema_prompt(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    captured_prompt = ""

    def runner(
        arguments: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_prompt
        captured_prompt = arguments[-1]
        payload = {
            "description": "紅色色塊",
            "highlights": ["單色畫面"],
            "keywords": ["紅色"],
        }
        return subprocess.CompletedProcess(
            arguments, 0, stdout=json.dumps(payload), stderr=""
        )

    LocalAnalyzer(model="qwen3-vl:8b", runner=runner).analyze(photo)

    assert "請只輸出" in captured_prompt
    assert all(
        field in captured_prompt
        for field in ("description", "highlights", "keywords")
    )
