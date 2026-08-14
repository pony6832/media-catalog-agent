from __future__ import annotations

import errno
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .database import CatalogDatabase
from .workspace import is_reparse_point


SUPPORTED_MEDIA: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


@dataclass(frozen=True, slots=True)
class ScanResult:
    discovered: int
    existing: int
    supported: int
    unsupported: int


def _fingerprint(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within_excluded(path: Path, excluded_roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    return any(
        resolved == excluded or excluded in resolved.parents
        for excluded in excluded_roots
    )


def _iter_files(
    root: Path, excluded_roots: tuple[Path, ...]
) -> Iterator[Path]:
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            ordered_entries = sorted(entries, key=lambda entry: entry.name.casefold())
        child_directories: list[Path] = []
        for entry in ordered_entries:
            path = Path(entry.path)
            if entry.is_symlink() or is_reparse_point(path):
                continue
            if entry.is_dir(follow_symlinks=False):
                if not _is_within_excluded(path, excluded_roots):
                    child_directories.append(path)
            elif entry.is_file(follow_symlinks=False):
                yield path
        pending.extend(reversed(child_directories))


def scan(
    root: Path,
    database: CatalogDatabase,
    *,
    excluded_roots: Iterable[Path] = (),
) -> ScanResult:
    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        raise FileNotFoundError(
            errno.ENOENT, os.strerror(errno.ENOENT), str(resolved_root)
        )

    known = {
        (str(record.path).casefold(), record.fingerprint)
        for record in database.list_records()
    }
    exclusions = tuple(Path(path).resolve() for path in excluded_roots)
    discovered = 0
    existing = 0
    supported = 0
    unsupported = 0

    for path in _iter_files(resolved_root, exclusions):
        media_type = SUPPORTED_MEDIA.get(path.suffix.casefold())
        if media_type is None:
            unsupported += 1
            continue
        supported += 1
        fingerprint = _fingerprint(path)
        key = (str(path.resolve()).casefold(), fingerprint)
        if key not in known:
            discovered += 1
            known.add(key)
        else:
            existing += 1
        database.upsert_discovered(path, fingerprint, media_type)

    return ScanResult(
        discovered=discovered,
        existing=existing,
        supported=supported,
        unsupported=unsupported,
    )
