import json
import unittest
from unittest.mock import patch

import app


class CanonicalSummaryConsumptionTests(unittest.TestCase):
    def _job(self):
        return {
            "id": "job-test",
            "name": "technical",
            "summary": None,
            "flags": {"old": True},
            "critical_flags": ["OLD"],
            "progress": None,
            "status": "TRANSLATING",
            "stage": "TRANSLATING",
        }

    def _consume(self, job, line):
        with patch.object(app, "_append_log"), patch.object(app, "_persist_locked"):
            app._consume_worker_output_line(job, line)

    def test_all_adapter_success_markers_are_log_only(self):
        markers = (
            "V2_1_2_SUMMARY", "V2_1_3_SUMMARY", "V2_2_0_SUMMARY",
            "V2_2_1_SUMMARY", "V2_2_2_SUMMARY", "V2_2_3_SUMMARY",
            "V2_2_4_SUMMARY", "V2_2_5_SUMMARY",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                job = self._job()
                self._consume(job, marker + ' {"events":1,"resolved":1}')
                self.assertIsNone(job["summary"])
                self.assertEqual(job["flags"], {"old": True})
                self.assertEqual(job["critical_flags"], ["OLD"])
                self.assertEqual(job["status"], "TRANSLATING")
                self.assertEqual(job["stage"], "TRANSLATING")

    def test_v221_marker_has_no_stopiteration(self):
        job = self._job()
        self._consume(job, 'V2_2_1_SUMMARY {"events":1,"resolved":1}')
        self.assertIsNone(job["summary"])

    def test_adapter_then_canonical_sequence(self):
        job = self._job()
        self._consume(job, 'V2_2_5_SUMMARY {"events":1,"resolved":1}')
        self.assertEqual(job["status"], "TRANSLATING")
        canonical = {
            "pipeline": "v2_3_0", "plan_id": "v2_3_0", "events": 10,
            "resolved": 10, "flags": {"canonical": True}, "critical_flags": [],
            "stages": [{"id": "FULL_TRANSLATION_V226"}, {"id": "KARAOKE_AUGMENTATION_V230"}],
        }
        self._consume(job, "SUBTRANSLATE_PIPELINE_SUMMARY " + json.dumps(canonical))
        self.assertEqual(job["summary"], canonical)
        self.assertEqual(job["status"], "VALIDATING")
        self.assertEqual(job["stage"], "KARAOKE_AUGMENTATION_V230")
        self.assertEqual(job["flags"], {"canonical": True})

    def test_canonical_status_and_stage_are_respected(self):
        job = self._job()
        canonical = {"status": "COMPLETED", "stage": "DONE", "events": 1, "resolved": 1}
        self._consume(job, "SUBTRANSLATE_PIPELINE_SUMMARY " + json.dumps(canonical))
        self.assertEqual(job["status"], "COMPLETED")
        self.assertEqual(job["stage"], "DONE")

    def test_invalid_canonical_json_is_worker_safe(self):
        job = self._job()
        self._consume(job, "SUBTRANSLATE_PIPELINE_SUMMARY {invalid")
        self.assertEqual(job["reason"], "invalid_canonical_summary_json")
        self.assertEqual(job["status"], "TRANSLATING")
        self.assertIsNone(job["summary"])

    def test_canonical_non_object_is_worker_safe(self):
        job = self._job()
        self._consume(job, "SUBTRANSLATE_PIPELINE_SUMMARY []")
        self.assertEqual(job["reason"], "invalid_canonical_summary_json")
        self.assertIsNone(job["summary"])

    def test_failure_summary_compatibility_preserves_failure_state(self):
        job = self._job()
        failure = {"status": "FAILED", "stage": "FAILED", "events": 1, "resolved": 0, "flags": {"failed": 1}, "critical_flags": ["BLOCKING"]}
        self._consume(job, "V2_2_4_FAILURE_SUMMARY " + json.dumps(failure))
        self.assertEqual(job["summary"], failure)
        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["stage"], "FAILED")
        self.assertEqual(job["critical_flags"], ["BLOCKING"])

    def test_web_retranslation_summary_compatibility(self):
        job = self._job()
        summary = {"status": "VALIDATING", "stage": "RETRANSLATION", "events": 2, "resolved": 2, "flags": {}}
        self._consume(job, "WEB_RETRANSLATION_SUMMARY " + json.dumps(summary))
        self.assertEqual(job["summary"], summary)
        self.assertEqual(job["stage"], "RETRANSLATION")

    def test_app_has_no_adapter_success_marker_state_list(self):
        source = open(app.__file__, encoding="utf-8").read()
        for marker in ("V2_1_2_SUMMARY", "V2_1_3_SUMMARY", "V2_2_0_SUMMARY", "V2_2_1_SUMMARY", "V2_2_2_SUMMARY", "V2_2_3_SUMMARY", "V2_2_4_SUMMARY", "V2_2_5_SUMMARY"):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
