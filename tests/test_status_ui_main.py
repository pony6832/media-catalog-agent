from pathlib import Path

import media_catalog.status_ui as status_ui


class FakeRoot:
    def __init__(self) -> None:
        self.mainloop_calls = 0
        self.destroy_calls = 0

    def mainloop(self) -> None:
        self.mainloop_calls += 1

    def destroy(self) -> None:
        self.destroy_calls += 1


def test_parser_accepts_no_arguments_for_desktop_mode() -> None:
    arguments = status_ui._parser().parse_args([])

    assert arguments.root is None
    assert arguments.skill_root is None


def test_main_builds_idle_application_without_root(
    tmp_path: Path, monkeypatch,
) -> None:
    fake_root = FakeRoot()
    captured: dict[str, object] = {}
    monkeypatch.setattr(status_ui, "_create_tk_root", lambda: fake_root)
    monkeypatch.setattr(status_ui, "_default_skill_root", lambda: tmp_path)
    monkeypatch.setattr(
        status_ui,
        "StatusApplication",
        lambda root, **kwargs: captured.update(root=root, **kwargs),
    )

    result = status_ui.main([])

    assert result == 0
    assert captured["media_root"] is None
    assert captured["skill_root"] == tmp_path.resolve()
    assert fake_root.mainloop_calls == 1


def test_main_keeps_existing_catalog_requirement_for_codex_root(
    tmp_path: Path, monkeypatch,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    fake_root = FakeRoot()
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(status_ui, "_create_tk_root", lambda: fake_root)
    monkeypatch.setattr(
        status_ui,
        "_show_startup_error",
        lambda _root, title, message: errors.append((title, message)),
    )

    result = status_ui.main([
        "--root",
        str(media_root),
        "--skill-root",
        str(tmp_path),
    ])

    assert result == 2
    assert "找不到媒體清冊" in errors[0][1]
    assert fake_root.mainloop_calls == 0
