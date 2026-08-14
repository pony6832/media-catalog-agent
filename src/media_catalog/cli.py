from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .bootstrap import bootstrap_workspace
from .workspace import WorkspacePathError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="media-catalog")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser(
        "start", help="Create or refresh a catalog below a local media folder."
    )
    start_parser.add_argument("root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)

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


if __name__ == "__main__":
    raise SystemExit(main())
