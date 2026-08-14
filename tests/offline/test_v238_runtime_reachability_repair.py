from __future__ import annotations

import json
import ast
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_orchestrator import execute_pipeline_plan
from v238_full_translation_stage import execute_v238_stage
from v238_response_provider import DurableResponseProvider, ResponseProviderError
from v238_base_materializer import CanonicalV226LiveMaterializer, BaseTranslationMaterializerError
from v238_llama_policy import LlamaPolicyError, enforce_v238_runtime_context, run_single_fallback_phase, review_suspect_qwen_outputs


ASS = """[Script Info]
ScriptType: v4.00+
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello world
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\b1}styled{\\b0} text
"""


class V238RuntimeReachabilityRepairTests(unittest.TestCase):
    class FixtureMaterializer:
        mode = "TEST_FIXTURE"

        def materialize(self, source, output, *, context):
            Path(output).write_bytes(Path(source).read_bytes())
            return {"mode": self.mode, "lineage": "TEST_FIXTURE"}

    class AmbiguousMaterializer(FixtureMaterializer):
        def materialize(self, source, output, *, context):
            import pysubs2
            subs = pysubs2.load(str(source), format="ass")
            for event in subs.events:
                if "styled" in event.text:
                    event.text = "translated text"
            subs.save(str(output), encoding="utf-8")
            return {"mode": self.mode, "lineage": "TEST_FIXTURE_AMBIGUOUS"}

    def _source(self, root: Path) -> Path:
        source = root / "source.ass"
        source.write_text(ASS, encoding="utf-8")
        return source

    def test_canonical_entrypoint_reaches_full_stage_then_v230(self):
        with tempfile.TemporaryDirectory(prefix="v238-runtime-") as raw:
            root = Path(raw)
            source, output = self._source(root), root / "final.ass"
            provider = DurableResponseProvider("TEST_FAKE")
            result = execute_pipeline_plan(
                "v2_3_8", source, output,
                {"response_provider": provider, "base_materializer": self.FixtureMaterializer(), "execution_mode": "TEST_FAKE", "model": "test-configured-model"},
            )
            self.assertEqual([row["id"] for row in result["stages"]], ["FULL_TRANSLATION_V238", "KARAOKE_AUGMENTATION_V230"])
            stage = result["stages"][0]["result"]
            self.assertEqual(stage["stage_id"], "FULL_TRANSLATION_V238")
            self.assertEqual(stage["component_calls"]["provider_requests"], 0)
            self.assertGreater(stage["component_calls"]["source_payload"], 0)
            self.assertEqual(result["stages"][1]["result"]["song_units"], 0)
            self.assertTrue(output.is_file())

    def test_ambiguous_ownership_fails_closed(self):
        def fake(request):
            if request.get("operation") == "v238_linguistic_translation":
                return {"translation": "translated text"}
            return {"translation": "translated text"}

        ass = ASS.replace(r"{\b1}styled{\b0} text", r"{\c&HFFFFFF&}styled{\c&H000000&} text")
        with tempfile.TemporaryDirectory(prefix="v238-ambiguous-") as raw:
            root = Path(raw)
            source = root / "source.ass"
            source.write_text(ass, encoding="utf-8")
            output = root / "final.ass"
            provider = DurableResponseProvider("TEST_FAKE", fake=fake)
            with self.assertRaises(ResponseProviderError):
                execute_pipeline_plan(
                    "v2_3_8", source, output,
                    {"response_provider": provider, "base_materializer": self.AmbiguousMaterializer(), "execution_mode": "TEST_FAKE", "model": "test-configured-model"},
                )
            self.assertFalse(output.exists())

    def test_live_capture_requires_injected_client_and_persists_before_parse(self):
        with tempfile.TemporaryDirectory(prefix="v238-capture-") as raw:
            root = Path(raw)
            provider = DurableResponseProvider(
                "LIVE_CAPTURED", capture_root=root,
                client=lambda request: {"translation": "deterministic injected response"},
            )
            result = provider.respond({"operation": "synthetic", "model": "configured", "text": "source"}, capture_id="call-1")
            self.assertEqual(result["translation"], "deterministic injected response")
            self.assertTrue((root / "call-1" / "request_payload.json").is_file())
            self.assertTrue((root / "call-1" / "raw-http-response.bin").is_file())
            self.assertTrue((root / "call-1" / "parsed_response.json").is_file())
            self.assertEqual(provider.metrics["physical_client_calls"], 1)
            self.assertEqual(provider.metrics["model_generation_calls"], 1)

    def test_invalid_json_retains_raw_and_records_failure(self):
        with tempfile.TemporaryDirectory(prefix="v238-invalid-json-") as raw:
            root = Path(raw)
            provider = DurableResponseProvider("LIVE_CAPTURED", capture_root=root, client=lambda request: b"{not-json")
            with self.assertRaises(ResponseProviderError):
                provider.respond({"operation": "invalid", "text": "source"}, capture_id="bad-1")
            call = root / "bad-1"
            self.assertTrue((call / "request_payload.json").is_file())
            self.assertTrue((call / "raw-http-response.bin").is_file())
            self.assertTrue((call / "validation_failure.json").is_file())
            self.assertFalse((call / "parsed_response.json").exists())
            self.assertEqual(json.loads((call / "capture_state.json").read_text())["state"], "VALIDATED_FAIL")
            self.assertEqual(provider.metrics["physical_client_calls"], 1)
            self.assertEqual(provider.metrics["parse_failures"], 1)

    def test_invalid_utf8_wrong_root_and_missing_field_fail_closed(self):
        cases = ((b"\xff\xfe", "utf8"), (b"[]", "root"), (b"{}", "schema"))
        for raw_body, label in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix=f"v238-invalid-{label}-") as raw:
                root = Path(raw)
                provider = DurableResponseProvider("LIVE_CAPTURED", capture_root=root, client=lambda request, body=raw_body: body)
                with self.assertRaises(ResponseProviderError):
                    provider.respond({"operation": f"invalid_{label}", "text": "source"}, capture_id=f"bad-{label}")
                call = root / f"bad-{label}"
                self.assertTrue((call / "request_payload.json").is_file())
                self.assertTrue((call / "raw-http-response.bin").is_file())
                self.assertFalse((call / "parsed_response.json").exists())
                self.assertTrue((call / "validation_failure.json").is_file())

    def test_client_exception_after_request_is_counted_and_not_retried(self):
        with tempfile.TemporaryDirectory(prefix="v238-client-failure-") as raw:
            root = Path(raw)

            def fail(_request):
                raise OSError("injected transport failure")

            provider = DurableResponseProvider("LIVE_CAPTURED", capture_root=root, client=fail)
            with self.assertRaises(OSError):
                provider.respond({"operation": "exception", "text": "source"}, capture_id="bad-exception")
            call = root / "bad-exception"
            self.assertTrue((call / "request_payload.json").is_file())
            self.assertFalse((call / "raw-http-response.bin").exists())
            self.assertEqual(provider.metrics["physical_client_calls"], 1)
            self.assertEqual(provider.metrics["application_network_calls"], 1)
            self.assertEqual(provider.metrics["model_generation_calls"], 1)

    def test_transport_semantics_are_explicit(self):
        with self.assertRaises(ValueError):
            DurableResponseProvider("LIVE_CAPTURED", capture_root=Path("/tmp"), transport_semantics="UNKNOWN")

    def test_repeated_linguistic_unit_fans_out_without_raw_event_amplification(self):
        ass = ASS.replace("hello world", "same unit").replace(r"{\b1}styled{\b0} text", "same unit")
        with tempfile.TemporaryDirectory(prefix="v238-unit-") as raw:
            root = Path(raw)
            source = root / "source.ass"
            source.write_text(ass, encoding="utf-8")
            provider = DurableResponseProvider("TEST_FAKE", fake={"default": {"translation": "same unit"}})
            result = execute_v238_stage(
                source, root / "final.ass",
                context={"response_provider": provider, "execution_mode": "TEST_FAKE", "model": "configured", "linguistic_unit_ids": {0: "unit-a", 1: "unit-a"}},
            )
            self.assertEqual(result["component_calls"]["provider_requests"], 1)
            self.assertEqual(provider.metrics["test_fake_responses"], 1)

    def test_equal_text_different_explicit_units_are_not_fused(self):
        with tempfile.TemporaryDirectory(prefix="v238-unit-boundary-") as raw:
            root = Path(raw)
            source = self._source(root)
            provider = DurableResponseProvider("TEST_FAKE", fake={"default": {"translation": "same unit"}})
            result = execute_v238_stage(
                source, root / "final.ass",
                context={"response_provider": provider, "execution_mode": "TEST_FAKE", "model": "configured", "linguistic_unit_ids": {0: "unit-a", 1: "unit-b"}},
            )
            self.assertEqual(result["component_calls"]["provider_requests"], 2)

    def test_full_stage_metrics_are_non_null_and_atomic(self):
        with tempfile.TemporaryDirectory(prefix="v238-metrics-") as raw:
            root = Path(raw)
            source, output = self._source(root), root / "final.ass"
            provider = DurableResponseProvider("TEST_FAKE")
            result = execute_v238_stage(source, output, context={"response_provider": provider, "execution_mode": "TEST_FAKE", "model": "configured"})
            for key in ("metrics_before", "metrics_after", "metrics_delta", "base_materializer_metrics", "v238_metrics", "aggregated_metrics"):
                self.assertIsNotNone(result[key])
            self.assertTrue(output.is_file())
            self.assertFalse(any(root.glob(".*.ass.*")))
            self.assertIsInstance(result["aggregated_metrics"]["v226_model_generation_attempts"], int)

    def test_transitive_runtime_modules_are_in_docker_allowlist(self):
        root = Path(__file__).resolve().parents[2]
        source_root = root / "src" / "subtranslate"
        docker = (root / "deploy" / "Dockerfile").read_text(encoding="utf-8")
        copied = set(re.findall(r"src/subtranslate/([A-Za-z0-9_]+\.py)", docker))
        roots = {"app.py", "pipeline_registry.py", "pipeline_orchestrator.py", "pipeline_lineage.py", "production_v2_3_8_adapter.py", "v238_full_translation_stage.py", "v238_base_materializer.py", "v238_response_provider.py", "production_v2_3_0_adapter.py", "pipeline_v2_3_0.py", "production_v2_2_6_adapter.py", "production_v2_2_5_adapter.py", "pipeline_v2_1_3.py", "production_v2_1_3_adapter.py", "v238_llama_policy.py"}
        seen, queue = set(), list(roots)
        while queue:
            name = queue.pop()
            if name in seen or not (source_root / name).is_file():
                continue
            seen.add(name)
            tree = ast.parse((source_root / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                target = None
                if isinstance(node, ast.Import):
                    target = node.names[0].name.split(".")[0] + ".py"
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    target = node.module.split(".")[0] + ".py"
                if target and (source_root / target).is_file() and target not in seen:
                    queue.append(target)
        missing = sorted(name for name in seen if name not in copied and name not in {"__init__.py"})
        self.assertEqual(missing, [], f"Docker runtime import closure missing: {missing}")

    def test_canonical_v226_checkpoint_reuse_is_exact(self):
        with tempfile.TemporaryDirectory(prefix="v238-checkpoint-") as raw:
            root = Path(raw)
            source, first, second = self._source(root), root / "base1.ass", root / "base2.ass"
            calls = {"count": 0}
            def fake_v226(src, dst, **kwargs):
                calls["count"] += 1
                Path(dst).write_bytes(Path(src).read_bytes())
                return {"calls": 1, "retry_calls": 0, "elapsed_client_seconds": 0.1}
            materializer = CanonicalV226LiveMaterializer()
            context = {"checkpoint_root": root / "state", "operation_id": "op-1", "episode_id": "E99", "model": "configured", "candidate_commit": "candidate"}
            with patch("v238_base_materializer.translate_subtitle_file_v2_2_6", fake_v226):
                created = materializer.materialize(source, first, context=context)
                reused = materializer.materialize(source, second, context=context)
            self.assertEqual(calls["count"], 1)
            self.assertEqual(created["checkpoint_created"], 1)
            self.assertEqual(reused["checkpoint_reused"], 1)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            bad = dict(context, episode_id="OTHER")
            with self.assertRaises(BaseTranslationMaterializerError):
                materializer.materialize(source, root / "bad.ass", context=bad)
            partial = root / "state" / "v238-base-checkpoints" / "op-partial"
            partial.mkdir(parents=True)
            (partial / "base.ass").write_bytes(b"partial")
            with self.assertRaises(BaseTranslationMaterializerError):
                materializer.materialize(source, root / "partial.ass", context=dict(context, operation_id="op-partial"))
            concurrent = root / "state" / "v238-base-checkpoints" / "op-concurrent"
            concurrent.mkdir(parents=True)
            (concurrent / "CLAIM").write_text("claimed\n")
            with self.assertRaises(BaseTranslationMaterializerError):
                materializer.materialize(source, root / "concurrent.ass", context=dict(context, operation_id="op-concurrent"))

    def test_selective_llama_policy_is_single_phase_and_nonpublishable(self):
        calls = []
        ledger = [
            {"canonical_unit_id": "u2", "status": "BLOCKED", "reason_code": "PRIMARY_SCHEMA_REJECTED", "episode_id": "e", "source_object": "s"},
            {"canonical_unit_id": "u1", "status": "SUSPECT", "reason_code": "DETERMINISTIC_SUSPECT_FLAG", "episode_id": "e", "source_object": "s"},
            {"canonical_unit_id": "u1", "status": "BLOCKED", "reason_code": "PRIMARY_SCHEMA_REJECTED", "episode_id": "e", "source_object": "s"},
        ]
        phase = run_single_fallback_phase(ledger, lambda request: calls.append(request) or {"candidates": [{"canonical_unit_id": unit["canonical_unit_id"], "text": "candidate"} for unit in request["units"]]}, model_tag="llama", model_digest="digest", capture_root=Path(tempfile.mkdtemp(prefix="llama-capture-")), unload=lambda: calls.append({"operation": "unload"}))
        self.assertEqual(phase["phase_count"], 1)
        self.assertEqual(phase["calls"], 1)
        self.assertEqual(phase["batches"], 1)
        self.assertTrue(phase["unload_requested"])
        self.assertEqual(phase["unload_status"], "PASS")
        self.assertTrue(all(row["state"] == "FALLBACK_CANDIDATE_ONLY" and not row["publication_authorization"] for row in phase["results"]))
        with self.assertRaises(LlamaPolicyError):
            run_single_fallback_phase([{"canonical_unit_id": "bad", "status": "BLOCKED"}], lambda _: {}, model_tag="llama", model_digest="digest")
        with self.assertRaises(LlamaPolicyError):
            enforce_v238_runtime_context({"fallback_translator": object()})
        self.assertEqual(enforce_v238_runtime_context({})["legacy_fallback_enabled"], False)
        self.assertEqual(review_suspect_qwen_outputs([{"canonical_unit_id": "u1", "status": "SUSPECT", "role": "PRIMARY"}], lambda _: {"verdict": "REVIEWER_NO_OBJECTION"})[0]["advisory"], True)

    def test_llama_unload_is_finally_even_when_group_request_fails(self):
        calls = []
        def failing(_request):
            raise RuntimeError("fake transport failure")
        with self.assertRaises(RuntimeError):
            run_single_fallback_phase(
                [{"canonical_unit_id": "u1", "status": "BLOCKED", "reason_code": "PRIMARY_SCHEMA_REJECTED"}],
                failing,
                model_tag="llama",
                model_digest="digest",
                unload=lambda: calls.append("unload"),
            )
        self.assertEqual(calls, ["unload"])

    def test_v238_rejects_legacy_fallback_injection(self):
        with tempfile.TemporaryDirectory(prefix="v238-legacy-fallback-") as raw:
            root = Path(raw)
            source, output = self._source(root), root / "final.ass"
            provider = DurableResponseProvider("TEST_FAKE")
            with self.assertRaises(LlamaPolicyError):
                execute_pipeline_plan(
                    "v2_3_8", source, output,
                    {"response_provider": provider, "base_materializer": self.FixtureMaterializer(), "execution_mode": "TEST_FAKE", "fallback_translator": object()},
                )


if __name__ == "__main__":
    unittest.main()
