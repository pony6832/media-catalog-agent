from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path


RESULT_DIRECTORY_NAME = "媒體整理成果"


class WorkspacePathError(ValueError):
    pass


def is_reparse_point(path: Path) -> bool:
    try:
        path_stat = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(path_stat, "st_file_attributes", 0)
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


@dataclass(frozen=True, slots=True)
class MediaWorkspace:
    root: Path
    result_root: Path
    database_path: Path
    excel_path: Path
    markdown_dir: Path
    backup_dir: Path
    index_dir: Path
    temp_dir: Path

    @classmethod
    def from_root(cls, root: Path) -> "MediaWorkspace":
        candidate = Path(root).expanduser().absolute()
        if not candidate.exists():
            raise WorkspacePathError(f"找不到資料夾：{candidate}")
        if is_reparse_point(candidate):
            raise WorkspacePathError(
                "符號連結或 reparse point 不能作為掃描根目錄"
            )
        if not candidate.is_dir():
            raise WorkspacePathError(f"路徑不是資料夾：{candidate}")

        resolved = candidate.resolve()
        if resolved == Path(resolved.anchor):
            raise WorkspacePathError("磁碟根目錄不能作為掃描根目錄")
        if resolved.name.casefold() == RESULT_DIRECTORY_NAME.casefold():
            raise WorkspacePathError("成果目錄不能作為掃描根目錄")

        result_root = resolved / RESULT_DIRECTORY_NAME
        return cls(
            root=resolved,
            result_root=result_root,
            database_path=result_root / "catalog.sqlite",
            excel_path=result_root / "媒體清冊.xlsx",
            markdown_dir=result_root / "Markdown",
            backup_dir=result_root / "備份",
            index_dir=result_root / "索引",
            temp_dir=result_root / "工作暫存",
        )

    @property
    def directories(self) -> tuple[Path, ...]:
        return (
            self.result_root,
            self.markdown_dir,
            self.backup_dir,
            self.index_dir,
            self.temp_dir,
        )

    def ensure_directories(self) -> None:
        try:
            for directory in self.directories:
                directory.mkdir(parents=True, exist_ok=True)
            probe_path = self.result_root / f".write-probe-{uuid.uuid4().hex}"
            try:
                with probe_path.open("x", encoding="utf-8") as probe:
                    probe.write("ok")
            finally:
                if probe_path.exists():
                    probe_path.unlink()
        except OSError as error:
            raise WorkspacePathError(
                f"無法建立或寫入成果目錄：{self.result_root}（{error}）"
            ) from error
