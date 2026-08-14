from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .analysis_runtime import RuntimePreflightError, build_local_analyzer
from .batch_analysis import analyze_pending
from .bootstrap import bootstrap_workspace
from .database import CatalogDatabase
from .excel_catalog import write_excel
from .inference import LocalAnalyzer
from .models import Status
from .source_guard import (
    SourceIntegrityError,
    capture_source,
    verify_record_source,
)
from .workspace import MediaWorkspace, WorkspacePathError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="media-catalog")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser(
        "start", help="Create or refresh a catalog below a local media folder."
    )
    start_parser.add_argument("root", type=Path)

    analyze_parser = subparsers.add_parser(
        "analyze-all", help="Analyze every pending catalog record locally."
    )
    analyze_parser.add_argument("root", type=Path)
    analyze_parser.add_argument("--skill-root", type=Path, required=True)
    analyze_parser.add_argument(
        "--model", default="Qwen3-vl:8b-instruct"
    )

    resume_parser = subparsers.add_parser(
        "resume-processing", help="Return interrupted records to pending."
    )
    resume_parser.add_argument("root", type=Path)

    retry_parser = subparsers.add_parser(
        "retry-failed", help="Return failed records to pending for another run."
    )
    retry_parser.add_argument("root", type=Path)

    verify_parser = subparsers.add_parser(
        "verify-sources", help="Verify that cataloged source files are unchanged."
    )
    verify_parser.add_argument("root", type=Path)
    return parser


def _existing_workspace(root: Path) -> MediaWorkspace:
    workspace = MediaWorkspace.from_root(root)
    if not workspace.database_path.is_file() or not workspace.excel_path.is_file():
        raise WorkspacePathError(
            f"找不到既有媒體清冊，請先執行 start：{workspace.result_root}"
        )
    return workspace


def main(
    argv: Sequence[str] | None = None,
    *,
    runtime_builder: Callable[..., LocalAnalyzer] = build_local_analyzer,
) -> int:
    arguments = _parser().parse_args(argv)

    if arguments.command == "start":
        try:
            result = bootstrap_workspace(arguments.root)
        except WorkspacePathError as error:
            print(f"MEDIA_CATALOG_ERROR {error}", file=sys.stderr)
            return 2

        print(
            "MEDIA_CATALOG_READY"
            f" added={result.scan.discovered}"
            f" existing={result.scan.existing}"
            f" skipped={result.scan.unsupported}"
            f" total={result.total_records}"
            f" catalog={result.workspace.excel_path}"
        )
        return 0

    try:
        workspace = _existing_workspace(arguments.root)
        database = CatalogDatabase(workspace.database_path)

        if arguments.command == "analyze-all":
            analyzer = runtime_builder(
                skill_root=arguments.skill_root,
                workspace=workspace,
                model=arguments.model,
            )
            result = analyze_pending(workspace, analyzer)
            print(
                "MEDIA_ANALYSIS_READY"
                f" analyzed={result.analyzed}"
                f" failed={result.failed}"
                f" skipped={result.skipped}"
                f" remaining={result.remaining}"
                f" catalog={workspace.excel_path}"
            )
            return 0

        if arguments.command == "resume-processing":
            count = database.requeue_processing()
            write_excel(database.list_records(), workspace.excel_path)
            print(f"MEDIA_ANALYSIS_RESUMED count={count}")
            return 0

        if arguments.command == "retry-failed":
            count = database.requeue_failed()
            write_excel(database.list_records(), workspace.excel_path)
            print(f"MEDIA_ANALYSIS_RETRY_QUEUED count={count}")
            return 0

        records = database.list_records()
        for record in records:
            verify_record_source(record, capture_source(record.path))
        print(f"MEDIA_SOURCES_VERIFIED total={len(records)}")
        return 0
    except (
        WorkspacePathError,
        RuntimePreflightError,
        SourceIntegrityError,
        FileNotFoundError,
        PermissionError,
    ) as error:
        print(f"MEDIA_ANALYSIS_ERROR {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
