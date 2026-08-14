import os
import subprocess
import sys
from pathlib import Path


SKILL_ROOT = Path("skills/media-inventory")


def test_skill_package_has_required_entrypoints() -> None:
    assert (SKILL_ROOT / "SKILL.md").is_file()
    assert (SKILL_ROOT / "agents/openai.yaml").is_file()
    assert (SKILL_ROOT / "scripts/run_media_catalog.ps1").is_file()
    assert (SKILL_ROOT / "scripts/run_media_analysis.ps1").is_file()


def test_skill_package_passes_official_validation() -> None:
    validator = (
        Path.home()
        / ".codex"
        / "skills"
        / ".system"
        / "skill-creator"
        / "scripts"
        / "quick_validate.py"
    )
    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"

    result = subprocess.run(
        [sys.executable, str(validator), str(SKILL_ROOT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
