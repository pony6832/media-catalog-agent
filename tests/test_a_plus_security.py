from __future__ import annotations

import re
import subprocess
from pathlib import Path


KEY_SHAPE = re.compile(r"AI" r"za[0-9A-Za-z_-]{30,}")


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
        contains_runtime_prefix = (
            ("AI" + "za") in content
            and not name.replace("\\", "/").startswith(
                "docs/superpowers/"
            )
        )
        if KEY_SHAPE.search(content) or contains_runtime_prefix:
            matches.append(name)

    assert matches == []


def test_standalone_launch_artifacts_do_not_embed_provider_configuration() -> None:
    paths = (
        Path("skills/media-inventory/scripts/run_media_analysis_ui.ps1"),
        Path("scripts/create-media-catalog-shortcut.ps1"),
    )

    for path in paths:
        content = path.read_text(encoding="utf-8")
        assert "GEMINI_API_KEY" not in content
        assert "GEMINI_API_KEY=" not in content
        assert "GOOGLE_API_KEY" not in content
        assert "--api-key" not in content
        assert "gemini-3.7-flash" not in content
