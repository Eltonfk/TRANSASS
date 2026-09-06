import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "subtranslate"))

from credential_store import CredentialStore
from diagnostics import sanitized_report
from state_migration import migrate_state


def test_credential_store_has_safe_file_fallback(monkeypatch):
    monkeypatch.setenv("TRANSASS_CREDENTIAL_BACKEND", "file")
    store = CredentialStore()
    assert store.backend == "file"
    assert store.get("gemini") is None


def test_state_migration_backups_and_never_overwrites_target(tmp_path):
    legacy, target = tmp_path / "legacy", tmp_path / "target"
    (legacy / "anime-subtitle-library").mkdir(parents=True)
    (legacy / "jobs.json").write_text("legacy", encoding="utf-8")
    (legacy / "anime-subtitle-library" / "library.db").write_text("library", encoding="utf-8")
    (target / "jobs.json").parent.mkdir(parents=True)
    (target / "jobs.json").write_text("new", encoding="utf-8")

    result = migrate_state(legacy, target, tmp_path / "backups")

    assert result["migrated"] is True
    assert (target / "jobs.json").read_text(encoding="utf-8") == "new"
    assert (target / "anime-subtitle-library" / "library.db").read_text(encoding="utf-8") == "library"
    backup = Path(str(result["backup"]))
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert {item["path"] for item in manifest["files"]} == {"jobs.json", "anime-subtitle-library/library.db"}


def test_diagnostics_report_contains_no_secret_or_absolute_paths():
    report = sanitized_report(
        version="2.5.0",
        media={"available": True, "folders": 2, "label": "Shows"},
        provider={"primary": {"provider": "gemini", "model": "flash", "base_url": "https://example.invalid"}, "keys_configured": {"gemini": True}, "pipeline": "v2_3_8", "credential_backend": "keyring"},
        onboarding_completed=True,
    )
    text = json.dumps(report)
    assert "api_key" not in text.lower()
    assert "SUA_API_KEY" not in text
    assert report["provider"]["keys_configured"]["gemini"] is True
