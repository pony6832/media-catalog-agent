from pathlib import Path


SKILL_ROOT = Path("skills/media-inventory")


def test_analysis_launcher_delegates_to_ui_launcher_without_credentials() -> None:
    launcher = (SKILL_ROOT / "scripts/run_media_analysis.ps1").read_text(
        encoding="utf-8"
    )

    assert "run_media_analysis_ui.ps1" in launcher
    assert "GEMINI_API_KEY" not in launcher


def test_ui_launcher_uses_private_pythonw_and_optional_root() -> None:
    launcher = (SKILL_ROOT / "scripts/run_media_analysis_ui.ps1").read_text(
        encoding="utf-8"
    )

    assert "$env:PYTHONUTF8 = '1'" in launcher
    assert ".runtime\\Scripts\\pythonw.exe" in launcher
    assert "$uiArguments = @(" in launcher
    assert "'-m', 'media_catalog.status_ui'" in launcher
    assert "if (-not [string]::IsNullOrWhiteSpace($RootPath))" in launcher
    assert "GEMINI_API_KEY" not in launcher
    assert "gemini-3.7-flash" not in launcher
