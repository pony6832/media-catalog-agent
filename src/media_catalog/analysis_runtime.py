from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .frame_selector import FrameSelector
from .gemini_client import GeminiClient
from .inference import (
    FallbackVideoExtractor,
    FfmpegImagePreparer,
    LocalAnalyzer,
    McpVideoExtractor,
    Runner,
    WatchVideoExtractor,
)
from .process_utils import HIDDEN_PROCESS_CREATION_FLAGS
from .run_state import RunStateStore
from .scene_segments import SceneSegmenter
from .segment_pipeline import SegmentPipeline
from .workspace import MediaWorkspace


MCP_VIDEO_ANALYZER_VERSION = "0.8.0"


class RuntimePreflightError(RuntimeError):
    pass


@dataclass(slots=True)
class AnalysisRuntime:
    local_analyzer: LocalAnalyzer
    segment_pipeline: SegmentPipeline
    run_state: RunStateStore

    def analyze(self, source: Path):
        return self.local_analyzer.analyze(source)

    @property
    def video_extractor(self):
        return self.local_analyzer.video_extractor


def _preflight(
    runner: Runner,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            creationflags=HIDDEN_PROCESS_CREATION_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimePreflightError(
            f"無法執行環境檢查：{arguments[0]} ({error})"
        ) from error


def _installed_models(output: str) -> set[str]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return {
        line.split(maxsplit=1)[0].casefold()
        for line in lines[1:]
        if line.split(maxsplit=1)
    }


def build_local_analyzer(
    *,
    skill_root: Path,
    workspace: MediaWorkspace,
    model: str,
    runner: Runner = subprocess.run,
    ollama_executable: str = "ollama",
    python_executable: str = sys.executable,
    ffmpeg_executable: str = "ffmpeg",
    ffprobe_executable: str = "ffprobe",
) -> AnalysisRuntime:
    skill_root = Path(skill_root).resolve()
    model_check = _preflight(runner, [ollama_executable, "list"])
    if model_check.returncode != 0:
        detail = model_check.stderr.strip() or "unknown error"
        raise RuntimePreflightError(f"Ollama 無法使用：{detail}")
    if model.casefold() not in _installed_models(model_check.stdout):
        raise RuntimePreflightError(f"找不到本機 Ollama 模型：{model}")

    ffmpeg_check = _preflight(runner, [ffmpeg_executable, "-version"])
    if ffmpeg_check.returncode != 0:
        detail = ffmpeg_check.stderr.strip() or "unknown error"
        raise RuntimePreflightError(f"FFmpeg 無法使用：{detail}")

    analysis_output = workspace.temp_dir / "analysis"
    watch_scripts = skill_root.parent / "watch" / "scripts"
    watch_script = watch_scripts / "watch.py"
    watch_setup = watch_scripts / "setup.py"
    watch = None
    if watch_script.is_file() and watch_setup.is_file():
        watch_check = _preflight(
            runner,
            [python_executable, str(watch_setup), "--check"],
        )
        if watch_check.returncode == 0:
            watch = WatchVideoExtractor(
                script_path=watch_script,
                output_root=analysis_output,
                python_executable=python_executable,
                runner=runner,
            )

    mcp_root = skill_root / ".tools" / "mcp-video-analyzer"
    mcp_executable = (
        mcp_root / "node_modules" / ".bin" / "mcp-video-analyzer.cmd"
    )
    mcp_package = (
        mcp_root
        / "node_modules"
        / "mcp-video-analyzer"
        / "package.json"
    )
    mcp = None
    if mcp_executable.is_file() and mcp_package.is_file():
        try:
            mcp = McpVideoExtractor(
                executable_path=mcp_executable,
                package_json_path=mcp_package,
                expected_version=MCP_VIDEO_ANALYZER_VERSION,
                output_root=analysis_output,
                runner=runner,
            )
        except (OSError, ValueError):
            mcp = None

    evidence_extractor = FallbackVideoExtractor(watch, mcp)
    local_analyzer = LocalAnalyzer(
        model=model,
        video_extractor=evidence_extractor,
        image_preparer=FfmpegImagePreparer(
            output_root=analysis_output / "normalized",
            ffmpeg_executable=ffmpeg_executable,
            runner=runner,
        ),
        ollama_executable=ollama_executable,
        runner=runner,
        timeout=600,
    )
    run_state = RunStateStore(
        workspace.database_path, excel_path=workspace.excel_path
    )
    segment_pipeline = SegmentPipeline(
        segmenter=SceneSegmenter(
            ffmpeg_executable=ffmpeg_executable,
            ffprobe_executable=ffprobe_executable,
            runner=runner,
        ),
        selector=FrameSelector(),
        local_analyzer=local_analyzer,
        gemini_client=GeminiClient(),
        store=run_state,
        output_root=analysis_output / "segments",
        evidence_extractor=evidence_extractor,
    )
    return AnalysisRuntime(local_analyzer, segment_pipeline, run_state)
