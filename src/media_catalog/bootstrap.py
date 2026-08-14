from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .database import CatalogDatabase
from .excel_catalog import write_excel
from .scanner import ScanResult, scan
from .workspace import MediaWorkspace


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    workspace: MediaWorkspace
    scan: ScanResult
    total_records: int


def bootstrap_workspace(root: Path) -> BootstrapResult:
    workspace = MediaWorkspace.from_root(root)
    workspace.ensure_directories()

    database = CatalogDatabase(workspace.database_path)
    scan_result = scan(
        workspace.root,
        database,
        excluded_roots=(workspace.result_root,),
    )
    records = database.list_records()
    write_excel(records, workspace.excel_path)

    return BootstrapResult(
        workspace=workspace,
        scan=scan_result,
        total_records=len(records),
    )
