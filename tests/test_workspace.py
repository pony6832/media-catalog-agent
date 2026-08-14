from pathlib import Path

import pytest

from media_catalog.workspace import MediaWorkspace, WorkspacePathError


def test_workspace_derives_and_creates_fixed_result_layout(tmp_path: Path) -> None:
    root = tmp_path / "家庭照片影片"
    root.mkdir()

    workspace = MediaWorkspace.from_root(root)
    workspace.ensure_directories()

    assert workspace.root == root.resolve()
    assert workspace.result_root == root.resolve() / "媒體整理成果"
    assert workspace.database_path == workspace.result_root / "catalog.sqlite"
    assert workspace.excel_path == workspace.result_root / "媒體清冊.xlsx"
    assert {
        workspace.markdown_dir.name,
        workspace.backup_dir.name,
        workspace.index_dir.name,
        workspace.temp_dir.name,
    } == {"Markdown", "備份", "索引", "工作暫存"}
    assert all(path.is_dir() for path in workspace.directories)
    assert not list(workspace.result_root.glob(".write-probe-*"))


def test_workspace_rejects_result_directory_as_root(tmp_path: Path) -> None:
    result_root = tmp_path / "媒體整理成果"
    result_root.mkdir()

    with pytest.raises(WorkspacePathError, match="成果目錄不能作為掃描根目錄"):
        MediaWorkspace.from_root(result_root)


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_workspace_rejects_non_directory_paths(tmp_path: Path, kind: str) -> None:
    candidate = tmp_path / kind
    if kind == "file":
        candidate.write_text("not a directory", encoding="utf-8")

    with pytest.raises(WorkspacePathError):
        MediaWorkspace.from_root(candidate)


def test_workspace_rejects_drive_root() -> None:
    drive_root = Path(Path.cwd().anchor)

    with pytest.raises(WorkspacePathError, match="磁碟根目錄不能作為掃描根目錄"):
        MediaWorkspace.from_root(drive_root)
