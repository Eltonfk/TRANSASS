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
    def _asset_text(self, path):
        response = self.client.get(path)
        try:
            return response.get_data(as_text=True)
        finally:
            response.close()

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
        script = self._asset_text("/static/app.js")
        self.assertIn("option.textContent = folder", script)
        self.assertNotIn("sel.innerHTML", script)

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
        for workspace in ("translate", "inbox", "library", "memory", "diagnostics"):
            self.assertEqual(page.count(f'data-view-panel="{workspace}"'), 1)
            self.assertEqual(page.count(f'data-view-button="{workspace}"'), 1)
        self.assertIn('data-view-panel="library" hidden', page)
        self.assertIn('data-view-panel="memory" hidden', page)
        self.assertIn('data-view-panel="diagnostics" hidden', page)

    def test_web_assets_are_extracted_from_python_module(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('href="/static/app.css"', page)
        self.assertIn('src="/static/app.js"', page)
        self.assertNotIn("<style>", page)
        self.assertNotIn("<script>\n", page)
        css_response = self.client.get("/static/app.css")
        js_response = self.client.get("/static/app.js")
        try:
            self.assertEqual(css_response.status_code, 200)
            self.assertEqual(js_response.status_code, 200)
        finally:
            css_response.close()
            js_response.close()

    def test_glossary_ui_uses_external_assets_and_escapes_entries(self):
        response = self.client.get("/glossary/ui")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn('href="/static/glossary.css"', page)
        self.assertIn('src="/static/glossary.js"', page)
        self.assertNotIn("<style>", page)
        self.assertNotIn("<script>", page)
        script = self._asset_text("/static/glossary.js")
        self.assertIn("escapeHtml", script)

    def test_primary_workflow_has_explicit_load_select_translate_steps(self):
        page = self.client.get("/").get_data(as_text=True)

        self.assertIn("Carregar esta temporada", page)
        self.assertIn("Escolha os episódios", page)
        self.assertIn('id="startBtn" disabled', page)
        self.assertIn("function syncSelectionUi()", self._asset_text("/static/app.js"))
        self.assertIn("Selecione episódios", page)
        self.assertIn("Ações avançadas e de manutenção", page)

    def test_feedback_and_accessibility_contracts_are_present(self):
        page = self.client.get("/").get_data(as_text=True)
        script = self._asset_text("/static/app.js")

        self.assertIn('id="toastRegion"', page)
        self.assertIn('aria-live="polite"', page)
        self.assertIn('aria-label="Áreas do Transass"', page)
        self.assertIn("function notify(message", script)
        self.assertIn("event.key==='/'", script)
        self.assertIn("event.key==='Escape'", script)

    def test_episode_metadata_refresh_is_throttled_without_slowing_queue_status(self):
        page = self.client.get("/").get_data(as_text=True)
        script = self._asset_text("/static/app.js")

        self.assertIn("setInterval(()=>{refresh();loadHistory()},10000)", script)
        self.assertIn("Date.now()-lastEpisodesRefreshAt>=10000", script)
        self.assertIn("lastEpisodesRefreshAt=Date.now()", script)
        self.assertIn("await refresh(true)", script)
        self.assertIn("button.textContent='Carregando…'", script)

    def test_episode_endpoint_exposes_bounded_pagination(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "shows" / "season"
            root.mkdir(parents=True)
            for index in range(3):
                (root / f"Episode {index + 1:02d}.mkv").write_bytes(b"video")
            with patch.object(web, "BASE_LIBRARY", Path(tmp_dir) / "shows"):
                response = self.client.get("/episodes?path=season&offset=1&limit=1")
            payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["offset"], 1)
        self.assertEqual(payload["limit"], 1)
        self.assertEqual(payload["total"], 3)
        self.assertTrue(payload["has_more"])
        self.assertEqual(len(payload["episodes"]), 1)

    def test_inbox_endpoint_returns_actionable_categories(self):
        with web.state_lock:
            web.state["jobs"] = [
                {"id": "ok", "status": "COMPLETED", "stage": "CANDIDATE_READY", "candidate_output_name": "ok.ass", "candidate_download_url": "/download/ok"},
                {"id": "bad", "status": "FAILED", "error": "falha de teste"},
            ]
        payload = self.client.get("/inbox").get_json()
        self.assertEqual(payload["counts"]["ready_to_publish"], 1)
        self.assertEqual(payload["counts"]["failed"], 1)
        self.assertIn("ok", [item["id"] for item in payload["categories"]["ready_to_publish"]])

    def test_translation_preflight_is_read_only_and_reports_batches(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "shows" / "season"
            root.mkdir(parents=True)
            video = root / "Episode 01.mkv"
            video.write_bytes(b"video")
            (root / "Episode 01.ass").write_text(
                "[Script Info]\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,Strike,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Hello\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,World\n",
                encoding="utf-8",
            )
            with patch.object(web, "BASE_LIBRARY", Path(tmp_dir) / "shows"):
                response = self.client.post("/preflight", json={"folder": "season", "episodes": ["Episode 01.mkv"]})
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["counts"]["selected"], 1)
        self.assertEqual(payload["counts"]["estimated_units"], 2)
        self.assertEqual(payload["counts"]["estimated_batches"], 1)

    def test_sse_endpoint_uses_status_event_stream(self):
        response = self.client.get("/events", buffered=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/event-stream")
        chunk = next(response.response)
        response.close()
        self.assertIn(b"event: status", chunk)

    def test_memory_sync_is_automatic_after_human_approval(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('id="memorySync"', page)
        self.assertIn("Sincronizada automaticamente após cada aprovação humana", page)

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
