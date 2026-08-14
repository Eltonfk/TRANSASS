import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_health_endpoint(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

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


if __name__ == "__main__":
    unittest.main()
