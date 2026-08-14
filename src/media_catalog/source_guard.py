from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .models import MediaRecord


_SECRET_KEYS = (
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "TWELVELABS_API_KEY",
)


class SourceIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    path: Path
    size: int
    mtime_ns: int
    sha256: str


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def capture_source(path: Path) -> SourceSnapshot:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    stat_result = resolved.stat()
    return SourceSnapshot(
        path=resolved,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
        sha256=_sha256(resolved),
    )


def verify_record_source(
    record: MediaRecord, snapshot: SourceSnapshot
) -> SourceSnapshot:
    try:
        current = capture_source(record.path)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise SourceIntegrityError(
            f"找不到來源媒體：{record.path}"
        ) from error

    if record.path.resolve() != snapshot.path or current.path != snapshot.path:
        raise SourceIntegrityError(f"來源路徑已變更：{record.path}")
    if snapshot.sha256 != record.fingerprint:
        raise SourceIntegrityError(f"來源指紋與清冊不符：{record.path}")
    if current.size != snapshot.size:
        raise SourceIntegrityError(f"來源大小已變更：{record.path}")
    if current.mtime_ns != snapshot.mtime_ns:
        raise SourceIntegrityError(f"來源修改時間已變更：{record.path}")
    if current.sha256 != snapshot.sha256:
        raise SourceIntegrityError(f"來源指紋已變更：{record.path}")
    return current


def sanitize_error(
    message: str,
    environment: Mapping[str, str] | None = None,
) -> str:
    safe = str(message)
    source_environment = os.environ if environment is None else environment
    for key in _SECRET_KEYS:
        value = source_environment.get(key)
        if value:
            safe = safe.replace(value, "[redacted]")
    safe = "".join(
        character
        if character == "\t" or ord(character) >= 32
        else " "
        for character in safe
    )
    return safe[:1000]
