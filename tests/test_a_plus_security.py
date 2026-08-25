from __future__ import annotations

import re
import subprocess
from pathlib import Path


KEY_SHAPE = re.compile(r"AIza[0-9A-Za-z_-]{30,}")


def test_repository_files_have_no_gemini_key_shape() -> None:
    names = subprocess.check_output(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
    ).splitlines()
    matches = []
    for name in names:
        path = Path(name)
        if not path.is_file() or path.suffix.casefold() == ".xlsx":
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        if KEY_SHAPE.search(content):
            matches.append(name)

    assert matches == []
