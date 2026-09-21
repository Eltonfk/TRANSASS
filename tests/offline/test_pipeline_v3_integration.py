"""Integration test for the unified end-to-end Pipeline V3."""

import tempfile
from pathlib import Path
import pytest
import pysubs2

from pipeline_v3 import translate_subtitle_file_v3
from ass_engine import ASSDocumentAST


def test_pipeline_v3_end_to_end_in_memory(tmp_path: Path):
    raw_source = (
        "[Script Info]\nTitle: Integration Test\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n"
        "Style: Sign,Arial,25,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n"
        "Style: OP,Arial,18,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Good morning, Kousei!\\NHow are you today?\n"
        "Dialogue: 0,0:00:03.50,0:00:05.50,Sign,,0,0,0,,{\\an8}Towa Music Festival\\NTowa High School\n"
        "Dialogue: 0,0:00:03.55,0:00:05.55,Sign,,0,0,0,,{\\an8\\c&HFF0000&}Towa Music Festival\\NTowa High School\n"
        "Dialogue: 0,0:00:06.00,0:00:09.00,OP,,0,0,0,,{\\k15}Hi{\\k25}ka{\\k35}ru\n"
        "Dialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,{\\p1}m 0 0 l 20 20\n"
        "Comment: 0,0:00:12.00,0:00:13.00,Default,,0,0,0,,End of scene\n"
    )

    src_file = tmp_path / "test_ep.ass"
    dest_file = tmp_path / "test_ep.pt-BR.ass"
    src_file.write_text(raw_source, encoding="utf-8")

    def mock_transport_call(payload: list[dict]):
        # Mock do modelo traduzindo semântica
        responses = {}
        for item in payload:
            text = item["source_text"]
            if "Good morning" in text:
                responses[item["id"]] = r"Bom dia, Kousei!\NComo você está hoje?"
            elif "Towa Music Festival" in text:
                # Placa com quebra legítima em palavra curta ("da Escola")
                responses[item["id"]] = r"Festival de Música de Towa\Nda Escola Towa"
        return responses

    result = translate_subtitle_file_v3(
        src_file,
        dest_file,
        transport_call=mock_transport_call,
        target_batch_size=4,
    )

    assert result["status"] == "COMPLETED"
    assert result["pipeline"] == "v3_0_0"
    assert result["total_events"] == 6
    assert result["validation"]["valid"] is True
    assert dest_file.is_file()

    # Inspeciona o arquivo final gerado
    final_doc = ASSDocumentAST.from_file(dest_file)
    assert final_doc[0].text == r"Bom dia, Kousei!\NComo você está hoje?"
    # Consistência das placas
    assert r"Festival de Música de Towa\Nda Escola Towa" in final_doc[1].text
    assert r"{\an8\c&HFF0000&}" in final_doc[2].text
    # Karaokê preservado
    assert r"{\k15}Hi" in final_doc[3].text
    # Desenho vetorial intacto
    assert final_doc[4].text == r"{\p1}m 0 0 l 20 20"
    # Comentário intacto
    assert final_doc[5].is_comment is True
