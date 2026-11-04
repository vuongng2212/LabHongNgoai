from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "src" / "ir_terminal" / "ui" / "web"
COMPONENTS_DIR = WEB_DIR / "components"


def test_polish_assets_are_wired() -> None:
    index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'href="assets/css/app.css"' in index
    assert 'src="js/ui_badges.js"' in index
    for element_id in (
        "app-sidebar",
        "ribbon-badge",
        "hw-tx-status",
        "hw-rx-status",
        "hw-rtt-state",
        "file-active-status",
        "chat-link-state",
        "kpi-rtt-badge",
    ):
        assert f'id="{element_id}"' in index


def test_no_fullscreen_backdrop_blur_or_pulse() -> None:
    paths = (
        WEB_DIR / "index.html",
        COMPONENTS_DIR / "ui_feedback.html",
        COMPONENTS_DIR / "settings_modal.html",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "backdrop-blur" not in text
        assert "animate-ping" not in text
        assert "status-pulse" not in text


def test_connect_success_uses_toast() -> None:
    script = (WEB_DIR / "js" / "bridge_client.js").read_text(encoding="utf-8")
    assert "toast(" in script
    assert "'Link connected'" in script
    assert "title: 'Link connected'" not in script


def test_build_script_includes_polish_assets() -> None:
    script = (REPO_ROOT / "scripts" / "build_modular_web.py").read_text(encoding="utf-8")
    assert "assets/css/app.css" in script
    assert "js/ui_badges.js" in script


def test_split_screen_window_floor() -> None:
    source = (REPO_ROOT / "src" / "ir_terminal" / "ui" / "main_window_web.py").read_text(encoding="utf-8")
    assert "setMinimumSize(720, 600)" in source
    assert "max(720, geo.width() // 2)" in source
