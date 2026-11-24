from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_motionlab_web_entrypoint_references_existing_assets():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'href="/web/styles.css"' in html
    assert 'src="/web/app.js"' in html
    assert (ROOT / "web" / "styles.css").is_file()
    assert (ROOT / "web" / "app.js").is_file()


def test_motionlab_web_shell_contains_core_product_areas():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for area in ("Overview", "Projects", "Creative workspace", "Asset library", "Animation", "AI studio"):
        assert area in html
    for area in ("viewport", "timeline", "SCENE HIERARCHY", "project-search"):
        assert area in app


def test_web_shell_uses_authenticated_project_persistence():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'style="display:none"' in html
    assert 'id="sign-out"' in html
    assert 'api("/v1/auth/me")' in app
    assert 'api("/v1/app/projects")' in app
    assert 'api("/v1/auth/logout"' in app
    assert "Saving and cloud sync will be enabled" not in app
