from pathlib import Path


def test_installer_bootstraps_setuptools_before_nonisolated_project_build() -> None:
    project_root = Path(__file__).resolve().parents[1]
    installer = (
        project_root / "scripts" / "install-media-inventory-skill.ps1"
    ).read_text(encoding="utf-8")

    common = "-m pip install --disable-pip-version-check --no-cache-dir --no-compile "
    setuptools_install = common + "'setuptools>=68'"
    project_install = common + "--no-build-isolation $projectRootPath"

    assert setuptools_install in installer
    assert project_install in installer
    assert installer.index(setuptools_install) < installer.index(project_install)


def test_installer_uses_skill_local_npm_cache() -> None:
    project_root = Path(__file__).resolve().parents[1]
    installer = (
        project_root / "scripts" / "install-media-inventory-skill.ps1"
    ).read_text(encoding="utf-8")

    assert "$npmCache = Join-Path $mcpRoot '.npm-cache'" in installer
    assert "--cache $npmCache --no-audit --no-fund" in installer
