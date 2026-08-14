from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .inference import (
    FallbackVideoExtractor,
    LocalAnalyzer,
    McpVideoExtractor,
    Runner,
    WatchVideoExtractor,
)
from .workspace import MediaWorkspace


MCP_VIDEO_ANALYZER_VERSION = "0.8.0"


class RuntimePreflightError(RuntimeError):
    pass


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
) -> LocalAnalyzer:
    skill_root = Path(skill_root).resolve()
    model_check = _preflight(runner, [ollama_executable, "list"])
    if model_check.returncode != 0:
        detail = model_check.stderr.strip() or "unknown error"
        raise RuntimePreflightError(f"Ollama 無法使用：{detail}")
    if model.casefold() not in _installed_models(model_check.stdout):
        raise RuntimePreflightError(f"找不到本機 Ollama 模型：{model}")

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

    return LocalAnalyzer(
        model=model,
        video_extractor=FallbackVideoExtractor(watch, mcp),
        ollama_executable=ollama_executable,
        runner=runner,
    )
