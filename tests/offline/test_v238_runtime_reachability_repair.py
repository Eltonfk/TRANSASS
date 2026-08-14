from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline_orchestrator import execute_pipeline_plan
from v238_response_provider import DurableResponseProvider, ResponseProviderError


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
                {"response_provider": provider, "execution_mode": "TEST_FAKE", "model": "test-configured-model"},
            )
            self.assertEqual([row["id"] for row in result["stages"]], ["FULL_TRANSLATION_V238", "KARAOKE_AUGMENTATION_V230"])
            stage = result["stages"][0]["result"]
            self.assertEqual(stage["stage_id"], "FULL_TRANSLATION_V238")
            self.assertGreater(stage["component_calls"]["provider_requests"], 0)
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
                    {"response_provider": provider, "execution_mode": "TEST_FAKE", "model": "test-configured-model"},
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


if __name__ == "__main__":
    unittest.main()
