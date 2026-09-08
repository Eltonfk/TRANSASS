from pathlib import Path


ROOT = Path(__file__).parents[2]
TEMPLATES = ROOT / "src/subtranslate/templates"
STATIC = ROOT / "src/subtranslate/static"


def test_translation_workflow_is_rendered_in_step_order():
    page = (TEMPLATES / "index.html").read_text(encoding="utf-8")

    step_1 = page.index('data-i18n="step.1"')
    step_2 = page.index('data-i18n="step.2"')
    step_3 = page.index('data-i18n="step.3"')

    assert step_1 < step_2 < step_3


def test_contextual_controls_start_hidden_and_navigation_counts_exist():
    page = (TEMPLATES / "index.html").read_text(encoding="utf-8")

    assert 'id="activeQueueControls" hidden' in page
    assert 'id="thermalTelemetryControl" hidden' in page
    assert 'id="navQueueCount"' in page
    assert 'id="navInboxCount"' in page


def test_transport_dialog_is_responsive_and_honors_hidden_state():
    page = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")

    assert 'id="transportConfigForm"' in page
    assert 'class="app-dialog transport-dialog"' in page
    assert ".model-tools[hidden]{display:none!important}" in css
    assert ".dialog-card{max-width:100%" in css


def test_publication_feedback_uses_record_version_instead_of_stale_literal():
    app = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "V2.2.1 publicada no Jellyfin" not in app
    assert "`${recordLabel(record)} publicada no Jellyfin.`" in app


def test_glossary_is_reachable_and_uses_the_product_shell():
    page = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    glossary = (TEMPLATES / "glossary.html").read_text(encoding="utf-8")

    assert 'href="/glossary/ui"' in page
    assert 'href="/static/app.css"' in glossary
    assert "Translation Memory" not in glossary
    assert "Glossário 1.0" not in glossary


def test_frontend_has_one_effective_definition_for_core_renderers():
    app = (STATIC / "app.js").read_text(encoding="utf-8")

    for name in ("syncSelectionUi", "badge", "sourceBadge", "renderEpisodes", "auditBadge", "openArchiveSeries", "renderStatus"):
        assert app.count(f"function {name}(") == 1, name

    assert "renderStatus=function" not in app


def test_qi83_uses_a_qt_safe_icon_renderer_and_keeps_human_aria_labels():
    catalog = (STATIC / "i18n.js").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "function iconizeQi83" in catalog
    assert '["🐒", "◉"]' in catalog
    assert 'key.startsWith("aria.")' in catalog
    assert 'document.documentElement.dataset.locale = current' in catalog
    assert "function isQi83()" in app
    for key in ("chip.service", "chip.pipeline", "chip.model", "chip.motor"):
        assert f"t('{key}')" in app
    assert "if(typeof loadPipeline==='function')loadPipeline()" in app
