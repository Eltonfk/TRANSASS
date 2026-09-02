import tempfile
import os
import json
import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# app.py materializes its control-plane library during import. Keep this
# safety suite away from the container-only /app/state default.
_TEST_STATE_ROOT = tempfile.mkdtemp(prefix="app-safety-state-")
os.environ.setdefault("TRANSLATOR_WEB_STATE_DIR", _TEST_STATE_ROOT)
os.environ.setdefault("ANIME_SUBTITLE_LIBRARY_ROOT", str(Path(_TEST_STATE_ROOT) / "library"))
os.environ.setdefault("ANIME_LIBRARY_ROOTS", str(Path(_TEST_STATE_ROOT) / "media"))

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import app as web


class AppSafetyTests(unittest.TestCase):
    def setUp(self):
        self.client = web.app.test_client()
        with web.state_lock:
            web.state["log"].clear()
            web.state["log_sequence"] = 0
            web.state["running"] = False
            web.state["paused"] = False
            web.state["process"] = None
            web.state["finished_ok"] = None
            web.state["worker"] = None
            web.state["session_id"] = None
            web.state["jobs"] = []
            web.state["folder"] = None

    def test_health_endpoint(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_brand_logo_endpoint_serves_bundled_asset(self):
        response = self.client.get("/transass-logo.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertGreater(len(response.data), 100_000)
        self.assertEqual(hashlib.sha256(response.data).hexdigest(), "bfed2c710b8e31edbf007f5c907c134bf69822ec0505e400f23ced87326b1a71")

    def test_favicon_uses_transass_mark(self):
        response = self.client.get("/favicon.svg")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/svg+xml")
        self.assertIn("TransASS", response.get_data(as_text=True))
        self.assertNotIn("🎬", response.get_data(as_text=True))

    def test_status_returns_only_log_entries_after_cursor(self):
        with web.state_lock:
            web._append_log("first")
            web._append_log("second")

        response = self.client.get("/status?after=1")
        payload = response.get_json()

        self.assertEqual(payload["last_log_id"], 2)
        self.assertEqual(payload["log"], [{"id": 2, "line": "second"}])

    def test_invalid_start_request_does_not_reserve_job(self):
        response = self.client.post("/start", data="not-json", content_type="text/plain")

        self.assertEqual(response.status_code, 400)
        with web.state_lock:
            self.assertFalse(web.state["running"])

    def test_second_start_is_rejected_before_the_worker_runs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir) / "anime"
            folder.mkdir()
            with patch.object(web, "BASE_LIBRARY", Path(tmp_dir)):
                with patch.object(web.threading, "Thread") as thread:
                    first = self.client.post("/start", json={"folder": "anime"})
                    second = self.client.post("/start", json={"folder": "anime"})

            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 409)
            thread.return_value.start.assert_called_once()

    def test_folder_names_are_not_inserted_as_html(self):
        self.assertIn("option.textContent = folder", web.PAGE)
        self.assertNotIn("sel.innerHTML", web.PAGE)

    def test_main_page_exposes_focused_professional_workspaces(self):
        response = self.client.get("/")
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("Transass · Central de tradução", page)
        self.assertIn('src="/transass-logo.png?v=1"', page)
        self.assertIn('alt="TransASS"', page)
        self.assertIn('href="/favicon.svg?v=2"', page)
        self.assertIn("Troca o idioma. O nome continua questionável.", page)
        for workspace in ("translate", "library", "memory", "diagnostics"):
            self.assertEqual(page.count(f'data-view-panel="{workspace}"'), 1)
            self.assertEqual(page.count(f'data-view-button="{workspace}"'), 1)
        self.assertIn('data-view-panel="library" hidden', page)
        self.assertIn('data-view-panel="memory" hidden', page)
        self.assertIn('data-view-panel="diagnostics" hidden', page)

    def test_primary_workflow_has_explicit_load_select_translate_steps(self):
        page = self.client.get("/").get_data(as_text=True)

        self.assertIn("Carregar esta temporada", page)
        self.assertIn("Escolha os episódios", page)
        self.assertIn('id="startBtn" disabled', page)
        self.assertIn("function syncSelectionUi()", page)
        self.assertIn("Selecione episódios", page)
        self.assertIn("Ações avançadas e de manutenção", page)

    def test_feedback_and_accessibility_contracts_are_present(self):
        page = self.client.get("/").get_data(as_text=True)

        self.assertIn('id="toastRegion"', page)
        self.assertIn('aria-live="polite"', page)
        self.assertIn('aria-label="Áreas do Transass"', page)
        self.assertIn("function notify(message", page)
        self.assertIn("event.key==='/'", page)
        self.assertIn("event.key==='Escape'", page)

    def test_episode_metadata_refresh_is_throttled_without_slowing_queue_status(self):
        page = self.client.get("/").get_data(as_text=True)

        self.assertIn("setInterval(()=>{refresh();loadHistory()},2000)", page)
        self.assertIn("Date.now()-lastEpisodesRefreshAt>=10000", page)
        self.assertIn("lastEpisodesRefreshAt=Date.now()", page)
        self.assertIn("await refresh(true)", page)
        self.assertIn("button.textContent='Carregando…'", page)

    def test_critical_interactive_ids_are_unique(self):
        for element_id in (
            "startBtn",
            "episodes",
            "logs",
            "memoryItems",
            "archiveDetails",
            "transportConfigDialog",
        ):
            with self.subTest(element_id=element_id):
                self.assertEqual(web.PAGE.count(f'id="{element_id}"'), 1)

    def test_public_job_keeps_forensic_ledgers_out_of_ui_payloads(self):
        large_ledger = [{"event_id": index, "payload": "x" * 2048} for index in range(40)]
        job = {
            "id": "job-large",
            "status": "COMPLETED",
            "summary": {
                "pipeline": "v2_3_8",
                "events": 40,
                "resolved": 40,
                "primary_ledger": large_ledger,
                "calls": large_ledger,
            },
        }

        public = web._public_job(job)

        self.assertEqual(
            public["summary"],
            {"pipeline": "v2_3_8", "events": 40, "resolved": 40},
        )
        self.assertNotIn("primary_ledger", public["summary"])
        self.assertNotIn("calls", public["summary"])
        self.assertLess(len(json.dumps(public)), 4096)
        self.assertIs(job["summary"]["primary_ledger"], large_ledger)


if __name__ == "__main__":
    unittest.main()
