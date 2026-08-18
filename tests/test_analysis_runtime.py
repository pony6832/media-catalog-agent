import json
import subprocess
from pathlib import Path

import pytest

from media_catalog.analysis_runtime import (
    RuntimePreflightError,
    build_local_analyzer,
)
from media_catalog.inference import FallbackVideoExtractor, McpVideoExtractor
from media_catalog.workspace import MediaWorkspace


def _skill_tree(tmp_path: Path, *, include_mcp: bool = True) -> Path:
    skills_root = tmp_path / "skills"
    skill_root = skills_root / "media-inventory"
    skill_root.mkdir(parents=True)
    watch_scripts = skills_root / "watch" / "scripts"
    watch_scripts.mkdir(parents=True)
    (watch_scripts / "watch.py").write_text("# watch", encoding="utf-8")
    (watch_scripts / "setup.py").write_text("# setup", encoding="utf-8")
    if include_mcp:
        mcp_root = skill_root / ".tools" / "mcp-video-analyzer"
        executable = (
            mcp_root / "node_modules" / ".bin" / "mcp-video-analyzer.cmd"
        )
        executable.parent.mkdir(parents=True)
        executable.write_text("@echo off", encoding="utf-8")
        package_json = (
            mcp_root
            / "node_modules"
            / "mcp-video-analyzer"
            / "package.json"
        )
        package_json.parent.mkdir(parents=True)
        package_json.write_text(
            json.dumps({"name": "mcp-video-analyzer", "version": "0.8.0"}),
            encoding="utf-8",
        )
    return skill_root


def _workspace(tmp_path: Path) -> MediaWorkspace:
    root = tmp_path / "media"
    root.mkdir()
    workspace = MediaWorkspace.from_root(root)
    workspace.ensure_directories()
    return workspace


def _runner(
    *,
    watch_exit: int = 0,
    ffmpeg_exit: int = 0,
    model: str = "Qwen3-vl:8b-instruct",
):
    calls: list[list[str]] = []

    def run(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments[1:] == ["list"]:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=f"NAME ID SIZE MODIFIED\n{model} abc 6.1 GB now\n",
                stderr="",
            )
        if arguments[1:] == ["-version"]:
            return subprocess.CompletedProcess(
                arguments,
                ffmpeg_exit,
                stdout="ffmpeg version 7.1" if ffmpeg_exit == 0 else "",
                stderr="ffmpeg unavailable" if ffmpeg_exit else "",
            )
        if arguments[-1] == "--check":
            return subprocess.CompletedProcess(
                arguments, watch_exit, stdout="", stderr="watch unavailable"
            )
        raise AssertionError(f"unexpected command: {arguments}")

    return run, calls


def test_build_runtime_resolves_watch_and_pinned_private_mcp(
    tmp_path: Path,
) -> None:
    skill_root = _skill_tree(tmp_path)
    workspace = _workspace(tmp_path)
    runner, calls = _runner()

    analyzer = build_local_analyzer(
        skill_root=skill_root,
        workspace=workspace,
        model="qwen3-VL:8B-instruct",
        runner=runner,
        ollama_executable="ollama.exe",
        python_executable="python.exe",
        ffmpeg_executable="ffmpeg.exe",
    )

    extractor = analyzer.video_extractor
    assert isinstance(extractor, FallbackVideoExtractor)
    assert extractor.primary.script_path == (
        skill_root.parent / "watch" / "scripts" / "watch.py"
    ).resolve()
    assert isinstance(extractor.fallback, McpVideoExtractor)
    assert extractor.fallback.expected_version == "0.8.0"
    assert calls[0] == ["ollama.exe", "list"]
    assert calls[1] == ["ffmpeg.exe", "-version"]
    assert calls[2][-1] == "--check"


def test_build_runtime_omits_watch_when_its_preflight_fails(
    tmp_path: Path,
) -> None:
    skill_root = _skill_tree(tmp_path)
    runner, _ = _runner(watch_exit=2)

    analyzer = build_local_analyzer(
        skill_root=skill_root,
        workspace=_workspace(tmp_path),
        model="Qwen3-vl:8b-instruct",
        runner=runner,
    )

    assert analyzer.video_extractor.primary is None
    assert isinstance(analyzer.video_extractor.fallback, McpVideoExtractor)


def test_build_runtime_allows_watch_when_private_mcp_is_unavailable(
    tmp_path: Path,
) -> None:
    skill_root = _skill_tree(tmp_path, include_mcp=False)
    runner, _ = _runner()

    analyzer = build_local_analyzer(
        skill_root=skill_root,
        workspace=_workspace(tmp_path),
        model="Qwen3-vl:8b-instruct",
        runner=runner,
    )

    assert analyzer.video_extractor.primary is not None
    assert analyzer.video_extractor.fallback is None


def test_build_runtime_rejects_a_missing_model(tmp_path: Path) -> None:
    runner, _ = _runner(model="qwen3.5:9b")

    with pytest.raises(RuntimePreflightError, match="Qwen3-vl:8b-instruct"):
        build_local_analyzer(
            skill_root=_skill_tree(tmp_path),
            workspace=_workspace(tmp_path),
            model="Qwen3-vl:8b-instruct",
            runner=runner,
        )


def test_build_runtime_rejects_missing_ffmpeg_before_analyzing_rows(
    tmp_path: Path,
) -> None:
    runner, _ = _runner(ffmpeg_exit=1)

    with pytest.raises(RuntimePreflightError, match="FFmpeg"):
        build_local_analyzer(
            skill_root=_skill_tree(tmp_path),
            workspace=_workspace(tmp_path),
            model="Qwen3-vl:8b-instruct",
            runner=runner,
        )
