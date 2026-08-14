from __future__ import annotations

import errno
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .database import CatalogDatabase


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
    supported: int
    unsupported: int


def _fingerprint(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def scan(root: Path, database: CatalogDatabase) -> ScanResult:
    resolved_root = Path(root).resolve()
    if not resolved_root.is_dir():
        raise FileNotFoundError(
            errno.ENOENT, os.strerror(errno.ENOENT), str(resolved_root)
        )

    known = {
        (str(record.path).casefold(), record.fingerprint)
        for record in database.list_records()
    }
    discovered = 0
    supported = 0
    unsupported = 0

    for path in sorted(resolved_root.rglob("*")):
        if not path.is_file():
            continue
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
        database.upsert_discovered(path, fingerprint, media_type)

    return ScanResult(
        discovered=discovered,
        supported=supported,
        unsupported=unsupported,
    )
