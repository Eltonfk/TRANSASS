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
            web.state["cancel_requested"] = False
            web.state["thermal_stop_requested"] = False
            web.state["thermal_guard"] = None

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

    def test_transport_config_path_preserves_runtime_override(self):
        self.assertEqual(web.TRANSPORT_CONFIG_PATH, web.RUNTIME_CONFIG.transport_config)

    def test_new_start_clears_stale_cooperative_cancel_flag(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            folder = Path(tmp_dir) / "anime"
            folder.mkdir()
            with patch.object(web, "BASE_LIBRARY", Path(tmp_dir)), \
                    patch.object(web.threading, "Thread"):
                with web.state_lock:
                    web.state["cancel_requested"] = True
                response = self.client.post("/start", json={"folder": "anime"})

        self.assertEqual(response.status_code, 200)
        with web.state_lock:
            self.assertFalse(web.state["cancel_requested"])

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

    def test_worker_exception_is_recorded_instead_of_leaving_waiting_job(self):
        job = {
            "id": "job-preparation-error",
            "session_id": "session-preparation-error",
            "status": "WAITING",
            "name": "episode.mkv",
        }
        with web.state_lock:
            web.state["session_id"] = job["session_id"]
            web.state["running"] = True
            web.state["jobs"] = [job]
            web.state["history"] = []

        with patch.object(web, "_run_episode", side_effect=RuntimeError("V238_NO_SUBTITLE_STREAM_FOUND")), \
                patch.object(web, "_persist_locked"), patch.object(web, "_append_log") as append_log:
            web._worker_loop()

        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["stage"], "FAILED")
        self.assertEqual(job["reason"], "worker_exception")
        self.assertEqual(job["error"], "V238_NO_SUBTITLE_STREAM_FOUND")
        append_log.assert_any_call(
            "Falhou: episode.mkv — V238_NO_SUBTITLE_STREAM_FOUND",
            level="error",
            job_id="job-preparation-error",
        )

    def test_episode_discovery_deduplicates_symlink_and_hardlink_aliases(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            folder = Path(raw_dir)
            original = folder / "episode.mkv"
            original.write_bytes(b"media")
            os.link(original, folder / "episode-hardlink.mkv")
            try:
                (folder / "episode-symlink.mkv").symlink_to(original)
            except OSError:
                self.skipTest("filesystem não permite symlink")

            web._episode_discovery_cache.pop(str(folder), None)
            discovered = web._discover_episode_videos(folder)

            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0].stat().st_ino, original.stat().st_ino)

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
            "tcTest",
            "tcPrimaryModelSelect",
            "tcGeminiModelsRefresh",
        ):
            with self.subTest(element_id=element_id):
                self.assertEqual(web.PAGE.count(f'id="{element_id}"'), 1)

    def test_provider_test_uses_http_status_without_exposing_key(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "transport_config.json"
            config_path.write_text(
                json.dumps({
                    "primary": {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
                    "keys": {"gemini": "test-key"},
                }),
                encoding="utf-8",
            )
            fake_response = type("FakeResponse", (), {"status_code": 403, "close": lambda self: None})()
            with patch.object(web, "TRANSPORT_CONFIG_PATH", config_path), \
                    patch("requests.get", return_value=fake_response) as request_get:
                response = self.client.post("/onboarding/provider-test", json={})

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["message"], "O motor respondeu HTTP 403. Verifique endereço e credencial.")
        request_get.assert_called_once()
        self.assertEqual(request_get.call_args.kwargs["headers"]["x-goog-api-key"], "test-key")

    def test_provider_models_returns_stable_fallback_without_key(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "transport_config.json"
            config_path.write_text(
                json.dumps({"primary": {"provider": "gemini", "model": "gemini-2.5-flash-lite"}}),
                encoding="utf-8",
            )
            with patch.object(web, "TRANSPORT_CONFIG_PATH", config_path), patch("requests.get") as request_get:
                response = self.client.get("/onboarding/provider-models?provider=gemini")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["source"], "catalog")
        self.assertEqual(payload["models"][0]["id"], "gemini-3.5-flash-lite")
        request_get.assert_not_called()

    def test_provider_models_filters_online_catalogue(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "transport_config.json"
            config_path.write_text(
                json.dumps({
                    "primary": {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
                    "keys": {"gemini": "test-key"},
                }),
                encoding="utf-8",
            )
            fake_response = type(
                "FakeResponse",
                (),
                {
                    "status_code": 200,
                    "json": lambda self: {"models": [
                        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
                    ]},
                    "close": lambda self: None,
                },
            )()
            with patch.object(web, "TRANSPORT_CONFIG_PATH", config_path), patch("requests.get", return_value=fake_response):
                response = self.client.get("/onboarding/provider-models?provider=gemini")

        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "gemini-api")
        self.assertEqual([item["id"] for item in payload["models"]], ["gemini-2.5-flash"])

    def test_gemini_profile_does_not_replace_selected_model(self):
        import anime_subtitle_translator as translator

        config = {
            "primary": {"provider": "gemini", "model": "gemini-2.5-flash"},
            "keys": {"gemini": "test-key"},
            "gemini_profile": {
                "enabled": True,
                "model": "gemini-1.5-flash",
                "batch_size": 16,
                "retry_budget": 32,
                "delay_between_calls": 0.5,
            },
        }
        previous_batch = translator.BATCH_SIZE
        try:
            web._apply_gemini_profile(config)
        finally:
            translator.BATCH_SIZE = previous_batch

        self.assertEqual(config["primary"]["model"], "gemini-2.5-flash")

    def test_gemini_without_key_fails_closed_instead_of_switching_to_ollama(self):
        config = {
            "primary": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
            "keys": {},
            "gemini_profile": {"enabled": True, "batch_size": 16, "retry_budget": 32},
        }
        with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY_MISSING"):
            web._apply_gemini_profile(config)
        self.assertEqual(config["primary"]["provider"], "gemini")

    def test_gemini_profile_accepts_key_from_environment(self):
        config = {
            "primary": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
            "keys": {},
            "gemini_profile": {"enabled": True, "batch_size": 16, "retry_budget": 32},
        }
        with patch.dict(os.environ, {"GEMINI_API_KEY": "env-secret"}):
            web._apply_gemini_profile(config)

    def test_groq_without_key_fails_closed(self):
        config = {
            "primary": {"provider": "groq", "model": "openai/gpt-oss-20b"},
            "keys": {},
            "groq_profile": {"enabled": True, "batch_size": 4, "retry_budget": 16},
        }
        with self.assertRaisesRegex(RuntimeError, "GROQ_API_KEY_MISSING"):
            web._apply_groq_profile(config)

    def test_deepseek_profile_applies_batch_retry_and_delay_limits(self):
        import anime_subtitle_translator as translator

        config = {
            "primary": {"provider": "deepseek", "model": "deepseek-chat"},
            "keys": {"deepseek": "test-key"},
            "deepseek_profile": {
                "enabled": True,
                "batch_size": 99,
                "retry_budget": 99,
                "delay_between_calls": 0.0,
            },
        }
        previous_batch = translator.BATCH_SIZE
        try:
            web._apply_deepseek_profile(config)
            self.assertEqual(translator.BATCH_SIZE, 30)
        finally:
            translator.BATCH_SIZE = previous_batch

        self.assertEqual(config["deepseek_profile"]["batch_size"], 30)
        self.assertEqual(config["deepseek_profile"]["retry_budget"], 64)
        self.assertGreaterEqual(config["deepseek_profile"]["delay_between_calls"], 2.0)

    def test_deepseek_without_key_fails_closed(self):
        config = {
            "primary": {"provider": "deepseek", "model": "deepseek-chat"},
            "keys": {},
            "deepseek_profile": {"enabled": True},
        }
        with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY_MISSING"):
            web._apply_deepseek_profile(config)

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
