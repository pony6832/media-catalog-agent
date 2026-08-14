from pathlib import Path


SKILL_ROOT = Path("skills/media-inventory")


def test_skill_package_has_required_entrypoints() -> None:
    assert (SKILL_ROOT / "SKILL.md").is_file()
    assert (SKILL_ROOT / "agents/openai.yaml").is_file()
    assert (SKILL_ROOT / "scripts/run_media_catalog.ps1").is_file()


def test_skill_instructions_define_safe_path_only_workflow() -> None:
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "貼上" in text
    assert "本機資料夾路徑" in text
    assert "媒體整理成果" in text
    assert "MEDIA_CATALOG_READY" in text
    assert "MEDIA_CATALOG_ERROR" in text
    assert "不要自動分析" in text
    assert "不要修改或移動原始媒體" in text
