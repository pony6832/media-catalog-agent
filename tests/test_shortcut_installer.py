from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHORTCUT_SCRIPT = PROJECT_ROOT / "scripts/create-media-catalog-shortcut.ps1"


def run_powershell(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        capture_output=True,
        check=False,
    )


def make_installed_skill(tmp_path: Path) -> Path:
    skill = tmp_path / "installed skill"
    scripts = skill / "scripts"
    runtime = skill / ".runtime" / "Scripts"
    scripts.mkdir(parents=True)
    runtime.mkdir(parents=True)
    (scripts / "run_media_analysis_ui.ps1").write_text(
        "# fixture", encoding="utf-8"
    )
    (runtime / "pythonw.exe").write_bytes(b"fixture")
    return skill


def read_shortcut(link_path: Path) -> dict[str, str]:
    command = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
        "$env:MEDIA_CATALOG_TEST_LINK);"
        "[pscustomobject]@{TargetPath=$s.TargetPath;Arguments=$s.Arguments;"
        "WorkingDirectory=$s.WorkingDirectory;IconLocation=$s.IconLocation;"
        "Description=$s.Description}|ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            command,
        ],
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        check=True,
        env={**os.environ, "MEDIA_CATALOG_TEST_LINK": str(link_path)},
    )
    return json.loads(result.stdout)


def test_shortcut_script_creates_one_credential_free_link_and_updates_in_place(
    tmp_path: Path,
) -> None:
    skill = make_installed_skill(tmp_path)
    desktop = tmp_path / "桌面"
    desktop.mkdir()

    first = run_powershell(
        SHORTCUT_SCRIPT,
        "-SkillRoot",
        str(skill),
        "-DesktopPath",
        str(desktop),
    )
    second = run_powershell(
        SHORTCUT_SCRIPT,
        "-SkillRoot",
        str(skill),
        "-DesktopPath",
        str(desktop),
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    links = list(desktop.glob("Media Catalog A+ Stable.lnk"))
    assert len(links) == 1

    fields = read_shortcut(links[0])
    serialized = json.dumps(fields, ensure_ascii=False)

    assert fields["TargetPath"].lower().endswith("powershell.exe")
    assert (
        "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass"
        in fields["Arguments"]
    )
    assert "run_media_analysis_ui.ps1" in fields["Arguments"]
    assert fields["WorkingDirectory"] == str(skill.resolve())
    assert "shell32.dll,3" in fields["IconLocation"].lower()
    assert fields["Description"] == "Media Catalog A+ Stable 媒體整理與分析"
    assert "GEMINI_API_KEY" not in serialized
    assert "GEMINI_API_KEY=" not in serialized
    assert "--api-key" not in serialized
    assert "gemini-3.7-flash" not in serialized


def test_shortcut_script_rejects_missing_desktop(tmp_path: Path) -> None:
    skill = make_installed_skill(tmp_path)
    missing_desktop = tmp_path / "missing desktop"

    result = run_powershell(
        SHORTCUT_SCRIPT,
        "-SkillRoot",
        str(skill),
        "-DesktopPath",
        str(missing_desktop),
    )

    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert list(tmp_path.rglob("*.lnk")) == []
