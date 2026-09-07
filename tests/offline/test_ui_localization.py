"""Small contract tests for the presentation-only QI 83 locale."""

import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src/subtranslate/static"
TEMPLATE = ROOT / "src/subtranslate/templates/index.html"


def test_qi83_is_selectable_without_hardcoding_meme_emoji_in_template():
    page = TEMPLATE.read_text(encoding="utf-8")
    catalog = (STATIC / "i18n.js").read_text(encoding="utf-8")

    assert 'id="uiLanguageSelect"' in page
    assert 'value="pt-BR"' in page
    assert 'value="qi-83"' in page
    assert "🐒" not in page
    assert '"qi-83": qi83' in catalog
    assert '"locale.qi83": "🐒 QI 83 🍌"' in catalog


def test_localization_has_safe_pt_br_fallback_and_accessibility_metadata():
    catalog = (STATIC / "i18n.js").read_text(encoding="utf-8")
    page = TEMPLATE.read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")

    assert 'const FALLBACK = "pt-BR"' in catalog
    assert "dictionaries[FALLBACK]" in catalog
    assert "data-i18n-aria-label" in page
    assert "aria.episodeSelect" in app
    assert "transass:locale-changed" in app


def test_qi83_is_ui_only_and_does_not_touch_pipeline_modules():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    catalog = (STATIC / "i18n.js").read_text(encoding="utf-8")

    assert "localStorage.setItem(STORAGE_KEY" in catalog
    assert "target_locale" not in catalog
    assert "production_v2" not in catalog
    assert "TRANSLATOR_SOURCE_LANGUAGE" not in app


def test_dynamic_workspace_containers_are_not_overwritten_by_localization_observer():
    page = TEMPLATE.read_text(encoding="utf-8")

    for element_id in (
        "inboxReady",
        "inboxReview",
        "inboxFailed",
        "libraryStats",
        "archiveSeries",
        "memoryStats",
        "memoryItems",
    ):
        match = re.search(rf'<[^>]+id="{element_id}"[^>]*>', page)
        assert match, element_id
        assert "data-i18n=" not in match.group(0), element_id
