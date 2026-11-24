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
    for area in ("viewport", "timeline", "Scene hierarchy", "project-search"):
        assert area in app


def test_web_preview_does_not_claim_project_persistence_is_live():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "Project persistence is not connected yet" in app
    assert "Saving and cloud sync will be enabled" in app
