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


def test_make_v3_transport_call_batch_and_fallback(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-llm/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    fake_calls = []
    system_prompts = []

    def fake_http_post(url, headers, request, delay=0.0):
        fake_calls.append(request)
        # Se for o lote inicial
        messages = request.get("messages", [])
        system_prompts.append(messages[0]["content"])
        user_msg = messages[-1]["content"]
        if "items" in user_msg:
            # Retorna apenas 1 dos 2 itens em JSON para forçar fallback no segundo
            return '{"translations": [{"id": "1", "translation": "Olá, Yasumori"}]}'.encode("utf-8")
        # Fallback individual
        return '"A Sra. Yasumori está hospitalizada"'.encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)

    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    payload = [
        {"id": 1, "source_text": "Bonjour, Yasumori", "is_sign": False},
        {"id": 2, "source_text": "Mme Yasumori est hospitalisée", "is_sign": False},
    ]
    res = caller(payload)

    assert res[1] == "Olá, Yasumori"
    assert res[2] == "A Sra. Yasumori está hospitalizada"
    assert len(fake_calls) == 2  # 1 lote + 1 fallback individual
    assert len(system_prompts) == 2
    assert all("somente texto visível" in prompt for prompt in system_prompts)
    assert all("Não invente" in prompt for prompt in system_prompts)
    assert all("Não omita nem invente informação" in prompt for prompt in system_prompts)
    assert all("preserve quem age" in prompt.casefold() for prompt in system_prompts)
    assert all("Mantenha tags ASS" not in prompt for prompt in system_prompts)
    assert all("não preserve a cópula francesa ‘est’" in prompt for prompt in system_prompts)
    assert all("‘est hospitalisée’" in prompt and "‘está hospitalizada’" in prompt for prompt in system_prompts)


def test_v3_transport_makes_one_targeted_same_provider_quality_repair(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = "Falta de energia?" if is_repair else "Um blefe de energia?"
        response = {"translations": [{"id": "1", "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = caller([{
        "id": 1,
        "source_text": "Une panne de courant ?",
        "is_sign": False,
    }])

    assert result[1] == "Falta de energia?"
    assert len(calls) == 2
    assert "falta/queda de energia" in calls[1]["messages"][0]["content"]


def test_v3_transport_repairs_untranslated_french_ces_demonstrative(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = (
            "É como eliminar\\Nesses Shi Ki."
            if is_repair else "É como eliminar\\Nces Shi Ki."
        )
        response = {"translations": [{"id": "1", "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = caller([{
        "id": 1,
        "source_text": "C'est comment éliminer\\Nces Shi Ki.",
        "is_sign": False,
    }])

    assert result[1] == "É como eliminar\\Nesses Shi Ki."
    assert len(calls) == 2
    assert "Traduza o demonstrativo francês ‘ces’" in calls[1]["messages"][0]["content"]


def test_v3_transport_repairs_uppercase_residue_when_source_has_no_acronym(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = (
            "É como eliminar\\Nesses Shi Ki."
            if is_repair else "É como eliminar\\NCES Shi Ki."
        )
        response = {"translations": [{"id": "1", "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = caller([{
        "id": 1,
        "source_text": "C'est comment éliminer\\Nces Shi Ki.",
        "is_sign": False,
    }])

    assert result[1] == "É como eliminar\\Nesses Shi Ki."
    assert len(calls) == 2
    assert "FRENCH_CES_DEMONSTRATIVE_UNTRANSLATED" in calls[1]["messages"][1]["content"]


def test_v3_transport_repairs_added_quote_and_preserves_source_event_ownership(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        response = {"translations": [{"id": "1", "translation": '"Livres?"'}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = caller([{
        "id": 1,
        "source_text": '"Libres ?',
        "is_sign": False,
    }])

    assert result[1] == '"Livres?'
    assert len(calls) == 1


def test_v3_transport_repairs_excess_curly_single_quotes(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = "‘Livres ?" if is_repair else "‘Livres?’’"
        response = {"translations": [{"id": "1", "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = caller([{
        "id": 1,
        "source_text": "‘Libres ?",
        "is_sign": False,
    }])

    assert result[1] == "‘Livres ?"
    assert len(calls) == 2
    assert "ASS_DELIMITER_TOKEN_COUNT_MISMATCH" in calls[1]["messages"][1]["content"]


def test_v3_transport_repairs_reordered_delimiters_with_unchanged_counts(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = "(Texto)" if is_repair else ")Texto("
        response = {"translations": [{"id": "1", "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = caller([{
        "id": 1,
        "source_text": "(Texte)",
        "is_sign": False,
    }])

    assert result[1] == "(Texto)"
    assert len(calls) == 2
    assert "ASS_DELIMITER_SEQUENCE_MISMATCH" in calls[1]["messages"][1]["content"]
    assert "Não reordene" in calls[1]["messages"][0]["content"]


def test_v3_transport_repairs_literal_french_dors_bien(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = (
            "Durma bem e se recupere logo."
            if is_repair
            else "Dê bom sono e se recupere logo."
        )
        response = {"translations": [{"id": "1", "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = caller([{
        "id": 1,
        "source_text": "D'accord. Dors bien et rétablis-toi vite.",
        "is_sign": False,
    }])

    assert result[1] == "Durma bem e se recupere logo."
    assert len(calls) == 2
    assert "FRENCH_DORS_BIEN_LITERAL" in calls[1]["messages"][1]["content"]
    assert "‘durma bem’/‘descanse bem’" in calls[1]["messages"][0]["content"]


def test_v3_pipeline_repairs_copied_vade_retro_and_still_fails_closed(tmp_path: Path, monkeypatch):
    from pipeline_v3 import V3ResponseContractError, make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    source = tmp_path / "vade-retro.ass"
    source.write_text(_small_ass_source(
        'Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,"Vade retro !"',
    ), encoding="utf-8")
    calls = []
    repair_translation = '"Para trás!"'

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = repair_translation if is_repair else '"Vade retro!"'
        item = __import__("json").loads(request["messages"][-1]["content"])["items"][0]
        response = {"translations": [{"id": str(item["id"]), "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    output = tmp_path / "vade-retro.pt-BR.ass"
    transport_call = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=transport_call,
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert ASSDocumentAST.from_file(output)[0].text == '"Para trás!"'
    assert len(calls) == 2
    assert "LATIN_VADE_RETRO_UNTRANSLATED" in calls[1]["messages"][1]["content"]
    assert "‘Para trás!’/‘Afaste-se!’" in calls[1]["messages"][0]["content"]

    # A failed targeted repair must not bypass the strict source-copy gate.
    repair_translation = '"Vade retro!"'
    failed_output = tmp_path / "vade-retro-fail.pt-BR.ass"
    calls.clear()
    with pytest.raises(V3ResponseContractError, match=r"LATIN_VADE_RETRO_UNTRANSLATED.*:REPAIR_FAILED"):
        translate_subtitle_file_v3(
            source,
            failed_output,
            transport_call=transport_call,
            source_language="francês",
        )
    assert len(calls) == 2
    assert not failed_output.exists()


def test_v3_pipeline_repairs_copied_pompes_funebres_de_sotoba(tmp_path: Path, monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    source = tmp_path / "pompes-funebres.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:11:55.08,0:11:57.88,Default,,0,0,0,,{\a6}POMPES FUNÈBRES DE SOTOBA",
    ), encoding="utf-8")
    calls = []
    repair_translation = r"{\a6}FUNERÁRIA DE SOTOBA"

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = repair_translation if is_repair else r"{\a6}POMPES FUNÈBRES DE SOTOBA"
        item = __import__("json").loads(request["messages"][-1]["content"])["items"][0]
        response = {"translations": [{"id": str(item["id"]), "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    output = tmp_path / "pompes-funebres.pt-BR.ass"
    transport_call = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=transport_call,
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert ASSDocumentAST.from_file(output)[0].text == r"{\a6}FUNERÁRIA DE SOTOBA"
    assert len(calls) == 2
    assert "FRENCH_POMPES_FUNEBRES_UNTRANSLATED" in calls[1]["messages"][1]["content"]
    assert "‘Pompes funèbres’ significa ‘funerária’" in calls[1]["messages"][0]["content"]


def test_v3_quality_repair_prompt_preserves_exact_protected_name_spelling(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        system = request["messages"][0]["content"]
        assert "Grafias exatas encontradas na fonte" in system
        assert "MUROI" in system
        item = __import__("json").loads(request["messages"][-1]["content"])["items"][0]
        repair = "Faça uma única correção direcionada" in system
        translation = (
            "SHINMEI MUROI,\nCHEFE DO TEMPLO, PAI DE SEISHIN"
            if repair
            else "SHINMEI MURUI,\nCHEFE DO TEMPLO, PAI DE SEISHIN"
        )
        response = {"translations": [{"id": str(item["id"]), "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = caller([{
        "id": 36,
        "source_text": "SHINMEI MUROI,\nCHEF DU TEMPLE, PÈRE DE SEISHIN",
        "is_sign": False,
        "protected_names": ("Muroi", "Seishin"),
    }])

    assert result["36"] == "SHINMEI MUROI,\nCHEFE DO TEMPLO, PAI DE SEISHIN"
    assert len(calls) == 2


def test_v3_repairs_french_semantic_hallucinations_and_fails_closed_if_they_remain(
    tmp_path: Path,
    monkeypatch,
):
    from pipeline_v3 import V3ResponseContractError, make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    source = tmp_path / "line-break-mismatch.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Puisque vous insistez...",
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Parce que ce sont des okiagari !",
    ), encoding="utf-8")
    calls = []
    repair_bad = False

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        items = __import__("json").loads(request["messages"][-1]["content"])["items"]
        translations = []
        for item in items:
            source_text = item.get("source_text") or item.get("text") or ""
            if source_text.startswith("Puisque"):
                translation = (
                    "Como você insiste... Tudo bem..."
                    if not is_repair or repair_bad else "Já que você insiste..."
                )
            else:
                translation = (
                    "Ouça-me! Se não os caçarmos,"
                    if not is_repair or repair_bad else "Porque são okiagari!"
                )
            translations.append({"id": str(item["id"]), "translation": translation})
        response = {"translations": translations}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    output = tmp_path / "line-break-mismatch.pt-BR.ass"
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=caller,
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    output_doc = ASSDocumentAST.from_file(output)
    assert output_doc[0].text == "Já que você insiste..."
    assert output_doc[1].text == "Porque são okiagari!"
    assert len(calls) == 2
    repair_items = __import__("json").loads(calls[1]["messages"][1]["content"])["items"]
    assert all("ASS_LINE_BREAK_COUNT_MISMATCH" not in item["quality_risks"] for item in repair_items)
    assert any("FRENCH_INSISTEZ_ADDED_CONCESSION" in item["quality_risks"] for item in repair_items)
    assert any("FRENCH_OKIAGARI_MEANING_SHIFT" in item["quality_risks"] for item in repair_items)
    assert "não acrescente uma segunda fala" in calls[1]["messages"][0]["content"]
    assert "ASS_LINE_BREAK_COUNT_MISMATCH" not in calls[1]["messages"][0]["content"]

    repair_bad = True
    failed_output = tmp_path / "line-break-mismatch-fail.pt-BR.ass"
    calls.clear()
    failed_caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    with pytest.raises(
        V3ResponseContractError,
        match="FRENCH_INSISTEZ_ADDED_CONCESSION:REPAIR_FAILED",
    ):
        translate_subtitle_file_v3(
            source,
            failed_output,
            transport_call=failed_caller,
            source_language="francês",
        )
    assert len(calls) == 2
    assert not failed_output.exists()


def test_v3_reflows_line_count_only_mismatch_without_an_extra_model_call(
    tmp_path: Path,
    monkeypatch,
):
    from pipeline_v3 import make_v3_transport_call
    from ass_engine import visible_text

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    source = tmp_path / "line-count-only.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Je n'ai guère sauvé\Nbeaucoup de vies, en effet.",
    ), encoding="utf-8")
    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        item = __import__("json").loads(request["messages"][-1]["content"])["items"][0]
        response = {"translations": [{"id": str(item["id"]), "translation": "Na verdade, não salvei muitas vidas."}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    output = tmp_path / "line-count-only.pt-BR.ass"
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=caller,
        source_language="francês",
    )

    translated = visible_text(ASSDocumentAST.from_file(output)[0].text)
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert translated.count("\n") == 1
    assert " ".join(translated.split()) == "Na verdade, não salvei muitas vidas."
    assert len(calls) == 1  # no quality-repair model call for layout-only risk


def test_v3_reflow_selects_a_boundary_accepted_by_the_ass_lexical_validator():
    from pipeline_v3 import _reflow_plain_translation
    from ass_engine import line_break_inside_word

    source_translation = "Eu te via batendo na porta!"
    reflowed = _reflow_plain_translation(source_translation, 1)
    ass_break = "\\N"

    assert reflowed == "Eu te via batendo\nna porta!"
    assert line_break_inside_word("Eu te via" + ass_break + "batendo na porta!")
    assert not line_break_inside_word(reflowed.replace("\n", ass_break))
    assert " ".join(reflowed.split()) == source_translation


def test_v3_accepts_a_valid_break_after_the_short_infinitive_ir(tmp_path: Path):
    source = tmp_path / "short-infinitive-ir.ass"
    output = tmp_path / "short-infinitive-ir.pt-BR.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,on peut aller\Nà l'hôpital demain.",
    ), encoding="utf-8")

    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=lambda payload: {
            str(item["id"]): "podemos ir\nao hospital amanhã."
            for item in payload
        },
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True


def test_v3_accepts_a_visual_break_after_a_source_evidenced_acronym(tmp_path: Path):
    from ass_engine import (
        line_break_inside_word,
        source_uppercase_break_tokens,
        visible_text,
    )

    source_text = r"L'EEG commence\Nà réagir régulièrement."
    translated = r"O EEG\Ncomeça a reagir regularmente."
    source = tmp_path / "source-acronym-break.ass"
    output = tmp_path / "source-acronym-break.pt-BR.ass"
    source.write_text(_small_ass_source(
        rf"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{source_text}",
    ), encoding="utf-8")

    assert source_uppercase_break_tokens(source_text) == ("EEG",)
    assert line_break_inside_word(translated)
    assert not line_break_inside_word(
        translated,
        allowed_short_words=source_uppercase_break_tokens(source_text),
    )

    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=lambda payload: {
            str(item["id"]): translated for item in payload
        },
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert visible_text(ASSDocumentAST.from_file(output)[0].text) == (
        "O EEG\ncomeça a reagir regularmente."
    )


def test_v3_safe_reflow_fails_closed_on_oversized_events():
    from pipeline_v3 import _reflow_plain_translation

    too_many_words = " ".join(["palavra"] * 257)
    too_many_lines = " ".join(["palavra"] * 10)

    assert _reflow_plain_translation(too_many_words, 1) is None
    assert _reflow_plain_translation(too_many_lines, 8) is None


def test_v3_reflows_french_event_138_without_a_model_repair(
    tmp_path: Path,
    monkeypatch,
):
    from pipeline_v3 import make_v3_transport_call
    from ass_engine import visible_text

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    source = tmp_path / "event-138-short-word.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Je t'ai vue taper\Ndans la porte !",
    ), encoding="utf-8")
    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        item = __import__("json").loads(request["messages"][-1]["content"])["items"][0]
        response = {"translations": [{"id": str(item["id"]), "translation": "Eu te vi batendo na porta!"}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    output = tmp_path / "event-138-short-word.pt-BR.ass"
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=make_v3_transport_call(FakeTransport(), source_language="francês"),
        source_language="francês",
    )

    translated = visible_text(ASSDocumentAST.from_file(output)[0].text)
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert translated.splitlines() == ["Eu te vi batendo", "na porta!"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "text",
    [
        "—Olá. —E você?",
        "– Olá. – E você?",
        "-Sim. -E você?",
        ".— E você?",
        "Olá.—E você?",
        "Olá—E você?",
        "Olá-E você?",
        r"texto\Nsegunda linha",
        r"texto\nsegunda linha",
        r"{\i1}texto{\i0}",
    ],
)
def test_v3_does_not_reflow_dash_markers_or_ass_syntax(text: str):
    from pipeline_v3 import _reflow_plain_translation

    assert _reflow_plain_translation(text, 1) is None


def test_v3_reflow_keeps_a_lexical_hyphenated_word_intact():
    from pipeline_v3 import _reflow_plain_translation

    for text in ("uma palavra-composta com sentido", "uma palavra‐composta com sentido"):
        reflowed = _reflow_plain_translation(text, 1)
        assert reflowed is not None
        assert " ".join(reflowed.split()) == text


def test_v3_reflows_remaining_line_mismatch_after_semantic_repair(
    tmp_path: Path,
    monkeypatch,
):
    from pipeline_v3 import make_v3_transport_call
    from ass_engine import visible_text

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    source = tmp_path / "semantic-plus-layout.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Puisque vous\Ninsistez...",
    ), encoding="utf-8")
    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        item = __import__("json").loads(request["messages"][-1]["content"])["items"][0]
        value = "Já que você insiste..." if is_repair else "Como você insiste... Tudo bem..."
        response = {"translations": [{"id": str(item["id"]), "translation": value}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    output = tmp_path / "semantic-plus-layout.pt-BR.ass"
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=make_v3_transport_call(FakeTransport(), source_language="francês"),
        source_language="francês",
    )

    translated = visible_text(ASSDocumentAST.from_file(output)[0].text)
    repair_items = __import__("json").loads(calls[1]["messages"][-1]["content"])["items"]
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert len(calls) == 2
    assert "FRENCH_INSISTEZ_ADDED_CONCESSION" in repair_items[0]["quality_risks"]
    assert "ASS_LINE_BREAK_COUNT_MISMATCH" in repair_items[0]["quality_risks"]
    assert translated.count("\n") == 1
    assert " ".join(translated.split()) == "Já que você insiste..."


def test_v3_keeps_speaker_turn_markers_on_their_lines_during_quality_repair(
    tmp_path: Path,
    monkeypatch,
):
    from pipeline_v3 import make_v3_transport_call
    from ass_engine import visible_text

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    source = tmp_path / "speaker-turns.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,— Bonjour.\N— Et toi?",
    ), encoding="utf-8")
    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        item = __import__("json").loads(request["messages"][-1]["content"])["items"][0]
        value = "— Olá. — E você?" if not is_repair else "— Olá.\n— E você?"
        response = {"translations": [{"id": str(item["id"]), "translation": value}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    output = tmp_path / "speaker-turns.pt-BR.ass"
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=make_v3_transport_call(FakeTransport(), source_language="francês"),
        source_language="francês",
    )

    translated = visible_text(ASSDocumentAST.from_file(output)[0].text)
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert len(calls) == 2  # the dash-marked draft bypasses deterministic reflow
    assert translated.splitlines() == ["— Olá.", "— E você?"]


def test_v3_fails_closed_when_speaker_turn_line_repair_is_ignored(
    tmp_path: Path,
    monkeypatch,
):
    from pipeline_v3 import make_v3_transport_call, V3ResponseContractError

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    source = tmp_path / "speaker-turns-unrepaired.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,— Bonjour.\N— Et toi?",
    ), encoding="utf-8")
    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        item = __import__("json").loads(request["messages"][-1]["content"])["items"][0]
        response = {"translations": [{"id": str(item["id"]), "translation": "— Olá. — E você?"}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    output = tmp_path / "speaker-turns-unrepaired.pt-BR.ass"
    with pytest.raises(
        V3ResponseContractError,
        match="ASS_LINE_BREAK_COUNT_MISMATCH:REPAIR_FAILED",
    ):
        translate_subtitle_file_v3(
            source,
            output,
            transport_call=make_v3_transport_call(FakeTransport(), source_language="francês"),
            source_language="francês",
        )

    assert len(calls) == 2
    assert not output.exists()


def test_v3_quality_repair_budget_handles_more_than_three_affected_batches(
    tmp_path: Path,
    monkeypatch,
):
    from pipeline_v3 import make_v3_transport_call
    from v3_runtime import create_v3_live_transport

    config = {
        "primary": {"provider": "ollama", "model": "qwen-test", "base_url": "http://ollama.test:11434"},
        "fallback": None,
        "keys": {},
        "model_digest": "sha256:qwen-test",
    }
    monkeypatch.setenv("V238_QWEN_PHYSICAL_MAXIMUM", "12")

    source = tmp_path / "many-quality-repairs.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Une panne de courant ?",
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Honte à toi !",
        "Dialogue: 0,0:00:07.00,0:00:09.00,Default,,0,0,0,,Dors bien !",
        "Dialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,Rester de garde tout seul !",
    ), encoding="utf-8")
    drafts = {
        "Une panne de courant ?": "Um blefe de energia?",
        "Honte à toi !": "Você tem vergonha de mim!",
        "Dors bien !": "Dê bom sono!",
        "Rester de garde tout seul !": "Devolver a guarda sozinho!",
    }
    repairs = {
        "Une panne de courant ?": "Falta de energia?",
        "Honte à toi !": "Que vergonha!",
        "Dors bien !": "Durma bem!",
        "Rester de garde tout seul !": "Fique de plantão sozinho!",
    }
    physical_requests = []
    repair_requests = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        physical_requests.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        if is_repair:
            repair_requests.append(request)
        items = __import__("json").loads(request["messages"][-1]["content"])["items"]
        translations = []
        for item in items:
            text = item.get("source_text") or item.get("text") or ""
            translation = (repairs if is_repair else drafts)[text]
            translations.append({"id": str(item["id"]), "translation": translation})
        content = __import__("json").dumps({"translations": translations}, ensure_ascii=False)
        return __import__("json").dumps({"message": {"content": content}}).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    capture_root = tmp_path / "many-line-break-captures"
    operation_id = "four-batch-quality-repair-replay"
    capture_prefix = "four-batch-quality-repair"
    transport, provider = create_v3_live_transport(
        config, config["primary"], capture_root=capture_root,
    )
    output = tmp_path / "many-quality-repairs.pt-BR.ass"
    transport_call = make_v3_transport_call(
        transport,
        response_provider=provider,
        capture_id_prefix=capture_prefix,
        operation_id=operation_id,
        source_language="francês",
    )
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=transport_call,
        target_batch_size=1,
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert len(physical_requests) == 8  # quatro traduções e quatro reparos semânticos
    assert len(repair_requests) == 4
    assert provider.metrics["model_generation_calls"] == 8
    assert provider.metrics["requests_by_operation"] == {"V3_TRANSLATION": 4, "V3_QUALITY_REPAIR": 4}
    repair_items = [
        item
        for request in repair_requests
        for item in __import__("json").loads(request["messages"][-1]["content"])["items"]
    ]
    assert len(repair_items) == 4
    assert all("ASS_LINE_BREAK_COUNT_MISMATCH" not in item["quality_risks"] for item in repair_items)

    def unexpected_http(*_args, **_kwargs):
        pytest.fail("durable four-batch replay must not make a second physical model call")

    monkeypatch.setattr("web_durable_provider._http_post", unexpected_http)
    replay_transport, replay_provider = create_v3_live_transport(
        config, config["primary"], capture_root=capture_root,
    )
    replay_call = make_v3_transport_call(
        replay_transport,
        response_provider=replay_provider,
        capture_id_prefix=capture_prefix,
        operation_id=operation_id,
        source_language="francês",
    )
    replay_output = tmp_path / "many-quality-repairs-replayed.pt-BR.ass"
    replay_result = translate_subtitle_file_v3(
        source,
        replay_output,
        transport_call=replay_call,
        target_batch_size=1,
        source_language="francês",
    )

    assert replay_result["output_sha256"] == result["output_sha256"]
    assert replay_provider.metrics["offline_replay_reads"] == 8
    assert replay_provider.metrics["model_generation_calls"] == 0
    assert len(physical_requests) == 8


def test_v3_transport_repairs_high_confidence_french_residue(monkeypatch):
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = "Sra. Yasumori está hospitalizada." if is_repair else "Sra. Yasumori est hospitalizada."
        response = {"translations": [{"id": "1", "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(
        FakeTransport(),
        source_language="français",
    )
    result = caller([{
        "id": 1,
        "source_text": "Mme Yasumori est hospitalisée.",
        "is_sign": False,
    }])

    assert result[1] == "Sra. Yasumori está hospitalizada."
    assert len(calls) == 2
    assert "resíduos não intencionais" in calls[1]["messages"][0]["content"]


def test_v3_unmetered_quality_repair_calls_have_a_small_bounded_ceiling(monkeypatch):
    from pipeline_v3 import (
        V3_MAX_UNMETERED_QUALITY_REPAIR_CALLS,
        V3ResponseContractError,
        make_v3_transport_call,
    )

    class FakeTransport:
        delay_between_calls = 0.0

        def endpoint(self):
            return "http://fake-ollama/api"

        def headers(self):
            return {"Content-Type": "application/json"}

        def build_request(self, canonical_payload):
            return canonical_payload

        def extract_content(self, body):
            return body.decode("utf-8")

    calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        calls.append(request)
        is_repair = "Faça uma única correção direcionada" in request["messages"][0]["content"]
        translation = "Falta de energia?" if is_repair else "Um blefe de energia?"
        response = {"translations": [{"id": "1", "translation": translation}]}
        return __import__("json").dumps(response, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    caller = make_v3_transport_call(FakeTransport(), source_language="francês")
    payload = [{"id": 1, "source_text": "Une panne de courant ?", "is_sign": False}]

    for _ in range(V3_MAX_UNMETERED_QUALITY_REPAIR_CALLS):
        assert caller(payload)[1] == "Falta de energia?"
    with pytest.raises(V3ResponseContractError, match="REPAIR_BUDGET_EXHAUSTED"):
        caller(payload)

    assert len(calls) == (2 * V3_MAX_UNMETERED_QUALITY_REPAIR_CALLS) + 1


def test_v3_french_prompt_guidance_is_source_language_specific():
    from pipeline_v3 import _payload_specific_prompt_guidance, _source_language_prompt_guidance

    french = _source_language_prompt_guidance("fre", "português do Brasil (pt-BR)")
    english = _source_language_prompt_guidance("inglês", "português do Brasil (pt-BR)")

    assert "cópula francesa ‘est’" in french
    assert "‘est hospitalisée’" in french and "‘está hospitalizada’" in french
    assert "concordância natural de gênero e número" in french
    assert "português europeu" in french
    assert english == ""

    contextual = _payload_specific_prompt_guidance(
        [
            {
                "source_text": "Une panne de courant ?",
                "protected_names": ("Nao",),
            },
        ],
        "francês",
        "pt-BR",
    )
    assert "Nao" in contextual
    assert "falta/queda de energia" in contextual


def test_v3_validator_rejects_ass_markup_hallucinated_in_plain_text():
    from ass_engine import validate_document_structure

    source = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Voyons voir...\n"
    )
    candidate = ASSDocumentAST.from_string(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Vamos ver...\N{\N}" + "\n"
    )

    validation = validate_document_structure(source, candidate)

    assert validation["valid"] is False
    assert any("tags alteradas" in issue for issue in validation["issues"])
    assert any("quebra ASS alterada" in issue for issue in validation["issues"])


def test_pipeline_v3_progress_callback(tmp_path: Path):
    raw_source = (
        "[Script Info]\nTitle: Progress Test\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Line 1\n"
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Line 2\n"
    )
    src_file = tmp_path / "prog_ep.ass"
    dest_file = tmp_path / "prog_ep.pt-BR.ass"
    src_file.write_text(raw_source, encoding="utf-8")

    progress_ticks = []

    def on_progress(current, total):
        progress_ticks.append((current, total))

    def mock_call(payload):
        return {item["id"]: f"Trad: {item['source_text']}" for item in payload}

    result = translate_subtitle_file_v3(
        src_file,
        dest_file,
        transport_call=mock_call,
        target_batch_size=1,
        progress_callback=on_progress,
    )

    assert result["status"] == "COMPLETED"
    assert len(progress_ticks) == 2
    assert progress_ticks[-1] == (2, 2)


def _small_ass_source(*events: str) -> str:
    header = (
        "[Script Info]\nTitle: V3 Gate Test\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n"
        "Style: OP,Arial,18,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header + "\n".join(events) + "\n"


@pytest.mark.parametrize(
    ("events", "translations", "expected_flag"),
    (
        (
            ("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Une panne de courant ?",),
            {"Une panne de courant ?": "Um blefe de energia?"},
            "FRENCH_POWER_OUTAGE_FALSE_FRIEND",
        ),
        (
            ("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Il changea le contenu d'un seau.",),
            {"Il changea le contenu d'un seau.": "Ele mudou o conteúdo de uma balde."},
            "PTBR_GENDER_MISMATCH:uma:balde",
        ),
        (
            (
                "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Tu crois que Nao reviendra ce soir ?",
                "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Nao m'appelle !",
            ),
            {
                "Tu crois que Nao reviendra ce soir ?": "Você acha que Nao voltará esta noite?",
                "Nao m'appelle !": "Não me chame!",
            },
            "PROTECTED_NAME_NOT_PRESERVED:Nao",
        ),
    ),
)
def test_pipeline_v3_fails_closed_on_high_confidence_translation_quality_risk(
    tmp_path: Path,
    events: tuple[str, ...],
    translations: dict[str, str],
    expected_flag: str,
):
    from pipeline_v3 import V3ResponseContractError

    source = tmp_path / "quality-risk.ass"
    output = tmp_path / "quality-risk.pt-BR.ass"
    source.write_text(_small_ass_source(*events), encoding="utf-8")

    def translate(payload):
        return {
            str(item["id"]): translations[item["source_text"]]
            for item in payload
        }

    with pytest.raises(V3ResponseContractError, match="V3_TRANSLATION_QUALITY_RISK") as error:
        translate_subtitle_file_v3(
            source,
            output,
            transport_call=translate,
            source_language="francês",
        )

    assert expected_flag in str(error.value)
    assert not output.exists()


def test_pipeline_v3_does_not_let_ass_comments_create_protected_names(tmp_path: Path):
    source = tmp_path / "comment-name.ass"
    output = tmp_path / "comment-name.pt-BR.ass"
    source.write_text(
        _small_ass_source(
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Je pense que Nao reviendra.",
            "Comment: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Nao est déjà partie.",
        ),
        encoding="utf-8",
    )
    received_payloads = []

    def translate(payload):
        received_payloads.append(payload)
        return {str(item["id"]): "Acho que Nao voltará." for item in payload}

    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=translate,
        source_language="français",
    )

    assert result["status"] == "COMPLETED"
    assert received_payloads
    assert all(not item.get("protected_names") for batch in received_payloads for item in batch)


def test_pipeline_v3_accepts_break_before_a_protected_name_in_same_event(tmp_path: Path):
    source = tmp_path / "name-break.ass"
    output = tmp_path / "name-break.pt-BR.ass"
    second_source = r"Setsuko, Mikiyasu, Susumu et Nao\Nsont tous morts, non ?"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Tu crois que Nao reviendra ce soir ?",
        f"Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{second_source}",
    ), encoding="utf-8")
    received_payloads = []

    def translate(payload):
        received_payloads.extend(payload)
        return {
            str(item["id"]): (
                "Você acha que Nao voltará esta noite?"
                if "Tu crois" in item["source_text"]
                else "Setsuko, Mikiyasu, Susumu e\nNao morreram, não é?"
            )
            for item in payload
        }

    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=translate,
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert any("Nao" in item.get("protected_names", ()) for item in received_payloads)
    assert ASSDocumentAST.from_file(output)[1].text == r"Setsuko, Mikiyasu, Susumu e\NNao morreram, não é?"


def test_pipeline_v3_missing_unit_fails_without_output(tmp_path: Path):
    from pipeline_v3 import V3TranslationCoverageError

    source = tmp_path / "missing.ass"
    output = tmp_path / "missing.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,First line",
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Second line",
    ), encoding="utf-8")

    with pytest.raises(V3TranslationCoverageError, match="V3_TRANSLATION_MISSING"):
        translate_subtitle_file_v3(
            source,
            output,
            transport_call=lambda payload: {str(payload[0]["id"]): "Primeira linha"},
            target_batch_size=2,
        )

    assert not output.exists()


def test_pipeline_v3_rejects_copied_english_dialogue_before_output(tmp_path: Path):
    from pipeline_v3 import V3ResponseContractError

    source = tmp_path / "copied-dialogue.ass"
    output = tmp_path / "copied-dialogue.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello!",
    ), encoding="utf-8")

    with pytest.raises(V3ResponseContractError, match="V3_TRANSLATION_SOURCE_COPY"):
        translate_subtitle_file_v3(
            source,
            output,
            transport_call=lambda payload: {
                str(item["id"]): item["source_text"] for item in payload
            },
        )

    assert not output.exists()


def test_pipeline_v3_rejects_copied_french_dialogue_and_partial_residue(tmp_path: Path):
    from pipeline_v3 import V3ResponseContractError

    source_copy = tmp_path / "french-copy.ass"
    output_copy = tmp_path / "french-copy.pt-BR.ass"
    source_copy.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Je ne comprends pas ce que tu dis.",
    ), encoding="utf-8")
    with pytest.raises(V3ResponseContractError, match="V3_TRANSLATION_SOURCE_COPY"):
        translate_subtitle_file_v3(
            source_copy,
            output_copy,
            transport_call=lambda payload: {
                str(item["id"]): item["source_text"] for item in payload
            },
            source_language="francês",
        )
    assert not output_copy.exists()

    source_residue = tmp_path / "french-residue.ass"
    output_residue = tmp_path / "french-residue.pt-BR.ass"
    source_residue.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Je ne peux pas rester ici.",
    ), encoding="utf-8")
    with pytest.raises(V3ResponseContractError, match="V3_TRANSLATION_SOURCE_RESIDUE"):
        translate_subtitle_file_v3(
            source_residue,
            output_residue,
            transport_call=lambda payload: {
                str(item["id"]): "Eu não posso ficar, je ne sais pas, agora."
                for item in payload
            },
            source_language="francês",
        )
    assert not output_residue.exists()


def test_pipeline_v3_allows_name_only_french_call_exchange_without_weakening_dialogue_gate(tmp_path: Path):
    from pipeline_v3 import _is_untranslated_source_copy, translate_subtitle_file_v3

    source = tmp_path / "name-call-exchange.ass"
    output = tmp_path / "name-call-exchange.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,- Shinmei !\\N- Tokujirô...\n"
        "Dialogue: 0,0:00:03.50,0:00:05.00,Default,,0,0,0,,Je veux voir Tokujirô !",
    ), encoding="utf-8")

    def translate(payload):
        result = {}
        for item in payload:
            if "Shinmei" in item["source_text"]:
                result[str(item["id"])] = "- Shinmei!\n- Tokujirô..."
            else:
                result[str(item["id"])] = "Quero ver Tokujirô!"
        return result

    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=translate,
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert ASSDocumentAST.from_file(output)[0].visible == "- Shinmei!\n- Tokujirô..."

    for call_word in (
        "Bonjour", "Bonsoir", "Attends", "Vite", "Chérie", "Tsunami",
        "Banane", "Marianne", "Papaye", "Chanson", "Chapeau", "Chimie",
        "Chimère", "Chinoise", "Chienne", "Pyjama",
    ):
        assert _is_untranslated_source_copy(
            f"- {call_word} !\n- Tokujirô...",
            f"- {call_word}!\n- Tokujirô...",
            "francês",
            protected_names=("Tokujirô", call_word),
        )
    assert not _is_untranslated_source_copy(
        "- Shinmei !\n- Tokujirô...",
        "- Shinmei!\n- Tokujirô...",
        "francês",
        protected_names=("Tokujirô",),
    )
    assert _is_untranslated_source_copy(
        "- Shinmei !\n- Tokujirô...",
        "- Shinmei!!\n- Tokujirô...",
        "francês",
        protected_names=("Tokujirô",),
    )
    assert _is_untranslated_source_copy(
        "- Shinmei !\n- Tokujirô...",
        "- Shinmei! - Tokujirô...",
        "francês",
        protected_names=("Tokujirô",),
    )
    assert _is_untranslated_source_copy(
        "- Shinmei !\n- Tokujirô...",
        "- Shinmei!\n- Tokujirô...",
        "francês",
        protected_names=("Tokujirô",),
        target_language="inglês",
    )
    assert _is_untranslated_source_copy(
        "- Shinmei !\n- Tokujirô...",
        "- Shinmei!\n- Tokujirô...",
        "francês",
    )


def test_pipeline_v3_title_case_source_copy_requires_exact_protected_names(tmp_path: Path):
    from pipeline_v3 import _is_untranslated_source_copy

    assert _is_untranslated_source_copy(
        "Banane\nBanane", "Banane\nBanane", "francês",
    )
    assert _is_untranslated_source_copy(
        "- Chanson\n- Tokujirô...", "- Chanson\n- Tokujirô...", "francês",
        protected_names=("Chanson", "Tokujirô"),
    )
    assert not _is_untranslated_source_copy(
        "Akira\nAkira", "Akira\nAkira", "francês",
    )
    for translated, target in (
        ("Akira!\nAkira", "pt-BR"),
        ("Akira\n Akira", "pt-BR"),
        ("Akira\nAkira", "inglês"),
        ("Akira\nAkira", "pt"),
    ):
        assert _is_untranslated_source_copy(
            "Akira\nAkira", translated, "francês", target_language=target,
        )

    assert not _is_untranslated_source_copy(
        "Kyôko Ozaki.", "Kyôko Ozaki.", "francês",
        protected_names=("Kyôko", "Ozaki"),
    )
    for translated, target in (
        ("Kyôko Ozaki!", "pt-BR"),
        ("Kyôko Ozaki.", "inglês"),
    ):
        assert _is_untranslated_source_copy(
            "Kyôko Ozaki.", translated, "francês",
            protected_names=("Kyôko", "Ozaki"),
            target_language=target,
        )

    source = tmp_path / "protected-name-only.ass"
    output = tmp_path / "protected-name-only.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Je connais Kyôko Ozaki.",
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Kyôko Ozaki.",
    ), encoding="utf-8")
    result = translate_subtitle_file_v3(
        source,
        output,
        source_language="francês",
        transport_call=lambda payload: {
            str(item["id"]): (
                "Conheço Kyôko Ozaki."
                if item["source_text"].startswith("Je connais")
                else "Kyôko Ozaki."
            )
            for item in payload
        },
    )
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert ASSDocumentAST.from_file(output)[1].text == "Kyôko Ozaki."


def test_pipeline_v3_preserves_strong_unlisted_romanized_name_identity(tmp_path: Path):
    from pipeline_v3 import _is_untranslated_source_copy

    assert not _is_untranslated_source_copy("Atsushi !", "Atsushi!", "francês")
    assert _is_untranslated_source_copy(
        "Atsushi est ici.", "Atsushi est ici.", "francês",
    )
    assert _is_untranslated_source_copy(
        "Chimère!", "Chimère!", "francês", protected_names=("Chimère",),
    )
    assert _is_untranslated_source_copy(
        "Atsushi!", "Atsushi!", "francês", target_language="inglês",
    )

    source = tmp_path / "unlisted-romanized-name.ass"
    output = tmp_path / "unlisted-romanized-name.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Atsushi !",
    ), encoding="utf-8")
    result = translate_subtitle_file_v3(
        source,
        output,
        source_language="francês",
        transport_call=lambda payload: {
            str(item["id"]): "Atsushi!" for item in payload
        },
    )
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert ASSDocumentAST.from_file(output)[0].text == "Atsushi!"


def test_pipeline_v3_preserves_reviewed_unmarked_japanese_name_identity(tmp_path: Path):
    from pipeline_v3 import _is_untranslated_source_copy

    assert not _is_untranslated_source_copy("Kaori !", "Kaori!", "francês")
    assert not _is_untranslated_source_copy("Sachiko...", "Sachiko...", "francês")
    assert not _is_untranslated_source_copy("Kiyomi...", "Kiyomi...", "francês")
    assert _is_untranslated_source_copy(
        "Kaori est ici.", "Kaori est ici.", "francês",
    )
    assert _is_untranslated_source_copy(
        "Sachiko est ici.", "Sachiko est ici.", "francês",
    )
    assert _is_untranslated_source_copy(
        "Kaori!", "Kaori!", "francês", target_language="English, pt-BR",
    )

    source = tmp_path / "reviewed-unmarked-name.ass"
    output = tmp_path / "reviewed-unmarked-name.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Kaori !",
    ), encoding="utf-8")
    result = translate_subtitle_file_v3(
        source,
        output,
        source_language="francês",
        transport_call=lambda payload: {
            str(item["id"]): "Kaori!" for item in payload
        },
    )
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert ASSDocumentAST.from_file(output)[0].text == "Kaori!"


def test_pipeline_v3_preserves_only_contextually_proven_name_fragments(tmp_path: Path):
    from pipeline_v3 import _is_untranslated_source_copy

    for fragment in ("To...", "Toshi..."):
        assert _is_untranslated_source_copy(
            fragment, fragment, "francês", protected_names=("Toshio",),
        )
        assert not _is_untranslated_source_copy(
            fragment,
            fragment,
            "francês",
            protected_names=("Toshio",),
            contextually_protected_names=("Toshio",),
        )
    assert _is_untranslated_source_copy("To...", "To...", "francês")
    assert _is_untranslated_source_copy(
        "To...", "To...", "francês", protected_names=("Megumi",),
    )
    assert _is_untranslated_source_copy(
        "To...", "To...", "francês",
        protected_names=("Toshio", "Tomoko"),
    )
    assert _is_untranslated_source_copy(
        "To...", "To..", "francês", protected_names=("Toshio",),
    )
    assert _is_untranslated_source_copy(
        "To...", "To...", "francês", protected_names=("Toshio",),
        contextually_protected_names=("Toshio",),
        target_language="English, pt-BR",
    )
    assert _is_untranslated_source_copy(
        "To...", "To..", "francês", protected_names=("Toshio",),
        contextually_protected_names=("Toshio",),
    )

    source = tmp_path / "protected-name-prefixes.ass"
    output = tmp_path / "protected-name-prefixes.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Je connais Tokujirô.",
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Tokujirô arrive.",
        "Dialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,Je connais Toshio.",
        "Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,To...",
        "Dialogue: 0,0:00:04.00,0:00:05.00,Default,,0,0,0,,Toshi...",
        "Dialogue: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,Toshio...",
    ), encoding="utf-8")

    seen_prefix_names = []

    def translate_name_reveal(payload):
        results = {}
        for item in payload:
            text = item["source_text"]
            if text in {"To...", "Toshi..."}:
                seen_prefix_names.append((text, tuple(item["protected_names"])))
                results[str(item["id"])] = text
            elif text == "Toshi..." or text == "Toshio...":
                results[str(item["id"])] = text
            elif text == "Je connais Toshio.":
                results[str(item["id"])] = "Conheço Toshio."
            elif text == "Je connais Tokujirô.":
                results[str(item["id"])] = "Conheço Tokujirô."
            elif text == "Tokujirô arrive.":
                results[str(item["id"])] = "Tokujirô chegou."
        return results

    result = translate_subtitle_file_v3(
        source,
        output,
        source_language="francês",
        transport_call=translate_name_reveal,
    )
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert seen_prefix_names == [
        ("To...", ("Toshio",)),
        ("Toshi...", ("Toshio",)),
    ]
    assert [event.text for event in ASSDocumentAST.from_file(output)] == [
        "Conheço Tokujirô.", "Tokujirô chegou.", "Conheço Toshio.",
        "To...", "Toshi...", "Toshio...",
    ]

    from pipeline_v3 import V3ResponseContractError

    split_output = tmp_path / "protected-name-prefixes-split.pt-BR.ass"
    split_prefix_names = []

    def translate_split_batch(payload):
        for item in payload:
            if item["source_text"] == "To...":
                split_prefix_names.append(tuple(item["protected_names"]))
        return translate_name_reveal(payload)

    with pytest.raises(V3ResponseContractError, match="V3_TRANSLATION_SOURCE_COPY:3"):
        translate_subtitle_file_v3(
            source,
            split_output,
            source_language="francês",
            target_batch_size=4,
            transport_call=translate_split_batch,
        )
    assert split_prefix_names == [()]
    assert not split_output.exists()


def test_progressive_name_context_requires_same_batch_adjacent_ass_events():
    from ass_engine import ASSEventNode
    from pipeline_v3 import _adjacent_ellipsis_source_context
    from translation_quality import progressive_protected_name_prefixes_in_sequence
    from semantic_orchestrator import TranslationBatch, TranslationUnit

    def _unit(index: int, start: int, end: int, text: str, style: str = "Default"):
        node = ASSEventNode(index=index, start=start, end=end, style=style, text=text)
        return TranslationUnit(
            id=index,
            source_text=text,
            is_sign=False,
            metadata={"node": node},
        )

    units = [
        _unit(10, 1000, 1200, "To..."),
        _unit(11, 1500, 1700, "Toshi..."),
        _unit(12, 2000, 2200, "Toshio..."),
    ]
    batch = TranslationBatch(batch_index=0, units=units)
    expected_sequence = (("To...", "Toshi...", "Toshio..."),)
    assert _adjacent_ellipsis_source_context(batch, units[0]) == (
        (("To...", "Toshi...", "Toshio..."), 0),
    )
    assert _adjacent_ellipsis_source_context(batch, units[1]) == (
        (("To...", "Toshi...", "Toshio..."), 1),
    )
    assert progressive_protected_name_prefixes_in_sequence(
        expected_sequence[0], 0, ("Toshio",),
    ) == ("Toshio",)
    assert progressive_protected_name_prefixes_in_sequence(
        expected_sequence[0], 1, ("Toshio",),
    ) == ("Toshio",)

    split_batch = TranslationBatch(batch_index=0, units=units[:2])
    assert _adjacent_ellipsis_source_context(split_batch, units[0]) == ()

    wrong_style_units = [units[0], _unit(11, 1500, 1700, "Toshi...", "Sign"), units[2]]
    assert _adjacent_ellipsis_source_context(
        TranslationBatch(batch_index=0, units=wrong_style_units), wrong_style_units[0],
    ) == ()

    long_gap_units = [units[0], _unit(11, 4000, 4200, "Toshi..."), units[2]]
    assert _adjacent_ellipsis_source_context(
        TranslationBatch(batch_index=0, units=long_gap_units), long_gap_units[0],
    ) == ()

    negative_gap_units = [units[0], _unit(11, 1100, 1300, "Toshi..."), units[2]]
    assert _adjacent_ellipsis_source_context(
        TranslationBatch(batch_index=0, units=negative_gap_units), negative_gap_units[0],
    ) == ()

    boundary_gap_units = [
        _unit(10, 1000, 1200, "To..."),
        _unit(11, 3700, 3900, "Toshi..."),
        _unit(12, 6400, 6600, "Toshio..."),
    ]
    assert len(_adjacent_ellipsis_source_context(
        TranslationBatch(batch_index=0, units=boundary_gap_units), boundary_gap_units[0],
    )) == 1


def test_pipeline_v3_allows_protected_name_phrase_with_french_particle(tmp_path: Path):
    """Recognized names plus a French surname particle are not copied dialogue."""
    source = tmp_path / "protected-name-particle.ass"
    output = tmp_path / "protected-name-particle.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Je connais Shizuka Matsuo.",
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Shizuka Matsuo\\Nde Sakaimatsu...",
    ), encoding="utf-8")

    def transport_call(payload):
        result = {}
        for item in payload:
            text = item["source_text"]
            result[str(item["id"])] = (
                text if "de Sakaimatsu" in text
                else "Conheço a doutora Shizuka Matsuo."
            )
        return result

    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=transport_call,
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert output.is_file()
    translated = ASSDocumentAST.from_file(output)
    assert translated[1].text == r"Shizuka Matsuo\Nde Sakaimatsu..."


def test_pipeline_v3_accepts_shared_french_de_before_protected_romaji_name(tmp_path: Path):
    from pipeline_v3 import (
        _is_untranslated_source_copy,
        _normalize_translated_punctuation_spacing,
    )

    source_text = "De Seishin ?"
    assert _normalize_translated_punctuation_spacing(
        source_text, "francês", "português do Brasil (pt-BR)",
    ) == "De Seishin?"
    assert _normalize_translated_punctuation_spacing(
        source_text, "francês", "Português do Brasil",
    ) == "De Seishin?"
    assert _normalize_translated_punctuation_spacing(
        "De Seishin\u202f?", "francês", "pt-BR",
    ) == "De Seishin?"
    assert _normalize_translated_punctuation_spacing(
        "ligne\n!", "francês", "pt-BR",
    ) == "ligne\n!"
    assert _normalize_translated_punctuation_spacing(
        source_text, "inglês", "pt-BR",
    ) == source_text
    assert not _is_untranslated_source_copy(
        source_text,
        "De Seishin?",
        "francês",
        protected_names=("Seishin",),
    )
    assert _is_untranslated_source_copy(
        "De Seishin ?", "De Seishin?", "francês",
    )
    assert _is_untranslated_source_copy(
        "De Seishin ?", "De Seishin", "francês",
        protected_names=("Seishin",),
    )
    assert _is_untranslated_source_copy(
        "De Seishin ?", "De Seishin!", "francês",
        protected_names=("Seishin",),
    )
    assert _is_untranslated_source_copy(
        "De Seishin ?", "De Seishin?", "francês",
        protected_names=("Seishin",), target_language="inglês",
    )
    for generic_portuguese_target in ("pt", "português", "portugues"):
        assert _normalize_translated_punctuation_spacing(
            source_text, "francês", generic_portuguese_target,
        ) == source_text
        assert _is_untranslated_source_copy(
            source_text,
            "De Seishin?",
            "francês",
            protected_names=("Seishin",),
            target_language=generic_portuguese_target,
        )
    for invalid_target in ("not-pt-BR", "English, pt-BR"):
        assert _normalize_translated_punctuation_spacing(
            source_text, "francês", invalid_target,
        ) == source_text
        assert _is_untranslated_source_copy(
            source_text,
            "De Seishin?",
            "francês",
            protected_names=("Seishin",),
            target_language=invalid_target,
        )
    assert not _is_untranslated_source_copy(
        source_text, "De Seishin?", "francês",
        protected_names=("Seishin",), target_language="pt_BR",
    )
    assert _is_untranslated_source_copy(
        "De Seishin", "De Seishin", "francês",
    )
    assert _is_untranslated_source_copy(
        "De Seishin :", "De Seishin:", "francês",
        protected_names=("Seishin",),
    )
    assert _is_untranslated_source_copy(
        "De\nSeishin", "De\nSeishin", "francês",
    )
    assert not _is_untranslated_source_copy(
        "De\nSeishin", "De\nSeishin", "francês",
        protected_names=("Seishin",),
    )
    assert _is_untranslated_source_copy(
        "De\nSeishin", "De Seishin", "francês",
        protected_names=("Seishin",),
    )
    assert not _is_untranslated_source_copy(
        "De Seishin\u202f?", "De Seishin?", "francês",
        protected_names=("Seishin",),
    )
    assert _is_untranslated_source_copy(
        "De quoi ?", "De quoi?", "francês", protected_names=("Seishin",),
    )
    assert _is_untranslated_source_copy(
        "De Seishin, partez !", "De Seishin, partez!", "francês",
        protected_names=("Seishin",),
    )
    assert _is_untranslated_source_copy(
        "De Dieu ?", "De Dieu?", "francês", protected_names=("Dieu",),
    )
    for ambiguous_french_word in ("Chanson", "Chapeau"):
        assert _is_untranslated_source_copy(
            f"De {ambiguous_french_word} ?",
            f"De {ambiguous_french_word}?",
            "francês",
            protected_names=(ambiguous_french_word,),
        )

    source = tmp_path / "shared-de-name.ass"
    output = tmp_path / "shared-de-name.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Bonjour Seishin !",
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,De Seishin ?",
    ), encoding="utf-8")

    received_payloads = []

    def translate(payload):
        received_payloads.extend(payload)
        return {
            str(item["id"]): (
                "Olá, Seishin !"
                if item["source_text"].startswith("Bonjour")
                else "De Seishin ?"
            )
            for item in payload
        }

    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=translate,
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert any("Seishin" in item.get("protected_names", ()) for item in received_payloads)
    translated = ASSDocumentAST.from_file(output)
    assert translated[1].text == "De Seishin?"


def test_pipeline_v3_accepts_exact_shared_french_mal_singleton_in_ptbr(tmp_path: Path):
    from pipeline_v3 import _is_untranslated_source_copy

    assert not _is_untranslated_source_copy("Mal.", "Mal.", "francês")
    assert not _is_untranslated_source_copy("Mal !", "Mal!", "francês")
    assert not _is_untranslated_source_copy("Indulgente ?", "Indulgente?", "francês")
    assert not _is_untranslated_source_copy("Indulgente ?", "Indulgente ?", "francês")
    assert _is_untranslated_source_copy(
        "Mal.", "Mal!", "francês",
    )
    assert _is_untranslated_source_copy(
        "Indulgente ?", "Indulgente!", "francês",
    )
    assert _is_untranslated_source_copy(
        "Mal.", "Mal.", "francês", target_language="inglês",
    )
    assert not _is_untranslated_source_copy(
        "Ah, Dr Ozaki !", "Ah, Dr. Ozaki!", "francês", protected_names=("Ozaki",),
    )
    assert not _is_untranslated_source_copy(
        "Dr Ozaki !", "Dr. Ozaki!", "francês", protected_names=("Ozaki",),
    )
    assert not _is_untranslated_source_copy(
        "M. Ookawa !", "M. Ookawa!", "francês", protected_names=("Ookawa",),
    )
    assert _is_untranslated_source_copy(
        "Ah, c'est grave !", "Ah, c'est grave !", "francês",
    )
    assert _is_untranslated_source_copy(
        "Mal, c'est grave.", "Mal, c'est grave.", "francês",
    )

    source = tmp_path / "shared-mal.ass"
    output = tmp_path / "shared-mal.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Mal.",
    ), encoding="utf-8")
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=lambda payload: {
            str(item["id"]): "Mal." for item in payload
        },
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert ASSDocumentAST.from_file(output)[0].text == "Mal."


def test_pipeline_v3_accepts_only_exact_repeated_romaji_name_calls(tmp_path: Path):
    from pipeline_v3 import _is_french_repeated_name_call, _is_untranslated_source_copy

    assert not _is_untranslated_source_copy(
        "Akira !\\NAkira !", "Akira!\\NAkira!", "francês",
    )
    assert not _is_untranslated_source_copy(
        "Akira !\\NAkira !", "Akira!\nAkira!", "francês",
    )
    assert _is_untranslated_source_copy(
        "Akira !\\NAkira !", "Akira!!\\NAkira!", "francês",
    )
    assert not _is_french_repeated_name_call(
        "Akira !\\NAkira !", "Akira! Akira!", "pt-BR",
    )
    assert _is_untranslated_source_copy(
        "Bonjour !\\NAkira !", "Bonjour!\\NAkira!", "francês",
    )
    for french_word in (
        "Chanson", "Chapeau", "Banane", "Marianne", "Papaye",
        "Chimie", "Chinoise", "Chienne", "Pyjama",
    ):
        assert _is_untranslated_source_copy(
            f"{french_word} !\\N{french_word} !",
            f"{french_word}!\\N{french_word}!",
            "francês",
        )
    assert _is_untranslated_source_copy(
        "Akira !\\NAkira !", "Akira!\\NAkira!", "francês",
        target_language="inglês",
    )

    source = tmp_path / "repeated-name-call.ass"
    output = tmp_path / "repeated-name-call.pt-BR.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Akira !\NAkira !",
    ), encoding="utf-8")
    result = translate_subtitle_file_v3(
        source,
        output,
        transport_call=lambda payload: {
            str(item["id"]): "Akira!\nAkira!" for item in payload
        },
        source_language="francês",
    )

    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    assert ASSDocumentAST.from_file(output)[0].text == r"Akira!\NAkira!"


def test_protected_name_phrase_exemption_rejects_copied_french_dialogue():
    from pipeline_v3 import _is_untranslated_source_copy

    protected_names = ("Shizuka", "Matsuo")
    copied_lines = (
        "SHIZUKA MATSUO DE SAKAIMATSU, PARTONS!",
        "Shizuka Matsuo de Sakaimatsu Partons!",
    )
    for copied in copied_lines:
        assert _is_untranslated_source_copy(
            copied,
            copied,
            "francês",
            protected_names=protected_names,
        ) is True

    for unrecognized_surname in ("France", "Danse", "Chambre"):
        copied_name_intro = f"Kyôko Ozaki de {unrecognized_surname}."
        assert _is_untranslated_source_copy(
            copied_name_intro,
            copied_name_intro,
            "francês",
            protected_names=("Kyôko", "Ozaki"),
        ) is True

    source_intro = "Shizuka Matsuo de Sakaimatsu..."
    assert not _is_untranslated_source_copy(
        source_intro, source_intro, "francês",
        protected_names=protected_names,
    )
    assert _is_untranslated_source_copy(
        source_intro, "Shizuka Matsuo de Sakaimatsu!", "francês",
        protected_names=protected_names,
    )
    assert _is_untranslated_source_copy(
        source_intro, source_intro, "francês",
        protected_names=protected_names, target_language="inglês",
    )


def test_pipeline_v3_rejects_french_copula_residue_with_preserved_proper_name(tmp_path: Path):
    from pipeline_v3 import V3ResponseContractError, _has_high_confidence_source_residue

    source_text = r"Mme Yasumori\Nest hospitalisée"
    copied_copula = r"Sra. Yasumori\Nest hospitalizada"
    clean_translation = r"Sra. Yasumori\Nestá hospitalizada"
    assert _has_high_confidence_source_residue(
        source_text, copied_copula, "francês",
    )
    assert not _has_high_confidence_source_residue(
        source_text, clean_translation, "francês",
    )

    source = tmp_path / "french-copula-residue.ass"
    output = tmp_path / "french-copula-residue.pt-BR.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Mme Yasumori\Nest hospitalisée",
    ), encoding="utf-8")

    with pytest.raises(V3ResponseContractError, match="V3_TRANSLATION_SOURCE_RESIDUE"):
        translate_subtitle_file_v3(
            source,
            output,
            transport_call=lambda payload: {
                str(item["id"]): copied_copula for item in payload
            },
            source_language="francês",
        )

    assert not output.exists()


def test_pipeline_v3_rejects_partial_english_residue(tmp_path: Path):
    from pipeline_v3 import V3ResponseContractError

    source = tmp_path / "english-residue.ass"
    output = tmp_path / "english-residue.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,I do not know what to do.",
    ), encoding="utf-8")
    with pytest.raises(V3ResponseContractError, match="V3_TRANSLATION_SOURCE_RESIDUE"):
        translate_subtitle_file_v3(
            source,
            output,
            transport_call=lambda payload: {
                str(item["id"]): "Eu não sei what to do."
                for item in payload
            },
            source_language="inglês",
        )

    assert not output.exists()


def test_pipeline_v3_detects_short_copy_and_preserves_long_proper_title():
    from pipeline_v3 import _is_untranslated_source_copy

    assert _is_untranslated_source_copy("Run!", "Run", "inglês") is True
    assert _is_untranslated_source_copy(
        "Run away with me!", "Run away with me", "inglês",
    ) is True
    assert _is_untranslated_source_copy(
        "The Legend of the Galactic Heroes",
        "The Legend of the Galactic Heroes",
        "inglês",
    ) is False
    assert _is_untranslated_source_copy("He Yu.", "He Yu.", "inglês") is False
    assert _is_untranslated_source_copy(
        "- Hein ?\n- Kyôko.",
        "- Hein?\n- Kyôko.",
        "francês",
        protected_names=("Kyôko",),
    ) is False
    assert _is_untranslated_source_copy(
        "Hein Banane", "Hein Banane", "francês",
    ) is True
    assert _is_untranslated_source_copy(
        "Hein Banane", "Hein Banane", "francês",
        protected_names=("Banane",),
    ) is True
    for copied_french in (
        "HEIN BANANE", "BANANE BANANE", "CHIMÈRE", "Banane",
        "hein banane", "banane banane", "chimère", "banane",
    ):
        assert _is_untranslated_source_copy(
            copied_french, copied_french, "francês",
        ) is True
    assert _is_untranslated_source_copy(
        "Kyôko Ozaki de Banane.", "Kyôko Ozaki de Banane.", "francês",
        protected_names=("Kyôko", "Ozaki"),
    ) is True
    assert _is_untranslated_source_copy(
        "Hein? Kyôko.", "Hein! Kyôko.", "francês",
        protected_names=("Kyôko",),
    ) is True
    assert _is_untranslated_source_copy(
        "Hein? Kyôko.", "Hein? Kyôko.", "francês",
        protected_names=("Kyôko",), target_language="inglês",
    ) is True
    assert _is_untranslated_source_copy("Je viens.", "Je viens!", "francês") is True
    assert _is_untranslated_source_copy(
        "Je ne comprends pas.", "Je ne comprends pas!", "francês",
    ) is True


def test_pipeline_v3_accepts_shared_french_interjection_and_proper_name(tmp_path: Path):
    source = tmp_path / "shared-interjection.ass"
    output = tmp_path / "shared-interjection.pt-BR.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,- Hein ?\N- Kyôko.",
        "Dialogue: 0,0:00:03.50,0:00:05.00,Default,,0,0,0,,Je connais Kyôko.",
    ), encoding="utf-8")

    def translate(payload):
        return {
            str(item["id"]): (
                item["source_text"].replace("Hein ?", "Hein?")
                if item["source_text"].startswith("- Hein")
                else "Conheço Kyôko."
            )
            for item in payload
        }

    translate_subtitle_file_v3(
        source,
        output,
        transport_call=translate,
        source_language="francês",
    )

    assert output.is_file()


def test_pipeline_v3_publication_does_not_clobber_a_concurrent_file(tmp_path: Path, monkeypatch):
    import pipeline_v3

    source = tmp_path / "race.ass"
    output = tmp_path / "race.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,This is a test",
    ), encoding="utf-8")
    real_link = pipeline_v3.os.link

    def concurrent_create(staged, destination):
        Path(destination).write_bytes(b"created by another process")
        return real_link(staged, destination)

    monkeypatch.setattr(pipeline_v3.os, "link", concurrent_create)
    with pytest.raises(FileExistsError):
        translate_subtitle_file_v3(
            source,
            output,
            transport_call=lambda payload: {str(payload[0]["id"]): "Isto é um teste"},
        )
    assert output.read_bytes() == b"created by another process"


def test_pipeline_v3_translates_english_song_and_preserves_romaji(tmp_path: Path):
    source = tmp_path / "songs.ass"
    output = tmp_path / "songs.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,OP,,0,0,0,,The moonlight calls my name",
        "Dialogue: 0,0:00:04.00,0:00:06.00,OP,,0,0,0,,kaze no naka",
    ), encoding="utf-8")
    seen = []

    def translate(payload):
        seen.extend(item["source_text"] for item in payload)
        return {str(item["id"]): "O luar chama meu nome" for item in payload}

    translate_subtitle_file_v3(source, output, transport_call=translate)
    result = ASSDocumentAST.from_file(output)

    assert seen == ["The moonlight calls my name"]
    assert result[0].visible == "O luar chama meu nome"
    assert result[1].visible == "kaze no naka"


def test_pipeline_v3_preserves_high_confidence_romaji_outside_song_styles(tmp_path: Path):
    source = tmp_path / "romaji.ass"
    output = tmp_path / "romaji.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,kaze no naka",
    ), encoding="utf-8")
    submitted = []

    translate_subtitle_file_v3(
        source,
        output,
        transport_call=lambda payload: submitted.extend(payload) or {},
        source_language="inglês",
    )

    assert submitted == []
    assert ASSDocumentAST.from_file(output)[0].visible == "kaze no naka"


def test_pipeline_v3_blocks_english_syllabic_karaoke_before_model_call(tmp_path: Path):
    from pipeline_v3 import PipelineV3Error

    source = tmp_path / "timed-song.ass"
    output = tmp_path / "timed-song.pt-BR.ass"
    source.write_text(_small_ass_source(
        r"Dialogue: 0,0:00:01.00,0:00:03.00,OP,,0,0,0,,{\k15}The {\k25}moonlight {\k35}calls my name",
    ), encoding="utf-8")
    calls = []

    with pytest.raises(PipelineV3Error, match="KARAOKE_TRANSLATION_TIMING_UNSUPPORTED"):
        translate_subtitle_file_v3(
            source,
            output,
            transport_call=lambda payload: calls.append(payload) or {},
        )

    assert calls == []
    assert not output.exists()


def test_pipeline_v3_rejects_copied_english_song_translation(tmp_path: Path):
    from pipeline_v3 import V3ResponseContractError

    source = tmp_path / "copied-song.ass"
    output = tmp_path / "copied-song.pt-BR.ass"
    source_text = "The moonlight calls my name"
    source.write_text(_small_ass_source(
        f"Dialogue: 0,0:00:01.00,0:00:03.00,OP,,0,0,0,,{source_text}",
    ), encoding="utf-8")

    with pytest.raises(V3ResponseContractError, match="V3_SONG_TRANSLATION_SOURCE_COPY"):
        translate_subtitle_file_v3(
            source,
            output,
            transport_call=lambda payload: {
                str(item["id"]): item["source_text"] for item in payload
            },
        )

    assert not output.exists()


def test_v3_durable_transport_replays_captured_response_without_second_http(tmp_path: Path, monkeypatch):
    from pipeline_v3 import make_v3_transport_call
    from v3_runtime import create_v3_live_transport

    config = {
        "primary": {"provider": "openai_compat", "model": "local-test", "base_url": "http://llm.test/v1"},
        "fallback": None,
        "keys": {},
        "model_digest": "sha256:test-model",
    }
    http_calls = []

    def fake_http_post(url, headers, request, delay=0.0):
        http_calls.append(request)
        content = '{"translations":[{"id":"1","translation":"Olá"}]}'
        return (
            '{"choices":[{"message":{"content":'
            + __import__("json").dumps(content)
            + '}}]}'
        ).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    capture_root = tmp_path / "captures"
    transport, provider = create_v3_live_transport(
        config,
        config["primary"],
        capture_root=capture_root,
    )
    first = make_v3_transport_call(
        transport, response_provider=provider, capture_id_prefix="stable-call", operation_id="op-1"
    )
    payload = [{"id": 1, "source_text": "Hello", "is_sign": False}]
    assert first(payload)[1] == "Olá"
    assert len(http_calls) == 1
    assert provider.metrics["model_generation_calls"] == 1

    def unexpected_http(*_args, **_kwargs):
        pytest.fail("captured V3 call was sent to the model a second time")

    monkeypatch.setattr("web_durable_provider._http_post", unexpected_http)
    second_transport, second_provider = create_v3_live_transport(
        config,
        config["primary"],
        capture_root=capture_root,
    )
    second = make_v3_transport_call(
        second_transport,
        response_provider=second_provider,
        capture_id_prefix="stable-call",
        operation_id="op-1",
    )
    assert second(payload)[1] == "Olá"
    assert second_provider.metrics["offline_replay_reads"] == 1

    parsed_cache = capture_root / "stable-call-000001" / "parsed_response.json"
    parsed_cache.write_text('{"translation":"conteúdo adulterado"}', encoding="utf-8")
    tampered_replay = make_v3_transport_call(
        second_transport,
        response_provider=second_provider,
        capture_id_prefix="stable-call",
        operation_id="op-1",
    )
    with pytest.raises(RuntimeError, match="V3_CAPTURE_PARSED_CACHE_MISMATCH"):
        tampered_replay(payload)

    changed_request = make_v3_transport_call(
        second_transport,
        response_provider=second_provider,
        capture_id_prefix="stable-call",
        operation_id="op-1",
    )
    with pytest.raises(RuntimeError, match="V3_CAPTURE_REQUEST_IDENTITY_MISMATCH"):
        changed_request([{"id": 1, "source_text": "Changed source", "is_sign": False}])
    assert len(http_calls) == 1


def test_v3_retries_one_durable_incomplete_ollama_response_in_json_mode(tmp_path: Path, monkeypatch, caplog):
    import json

    from pipeline_v3 import make_v3_transport_call
    from v3_runtime import create_v3_live_transport

    config = {
        "primary": {"provider": "ollama", "model": "qwen-test", "base_url": "http://ollama.test:11434"},
        "fallback": None,
        "keys": {},
        "model_digest": "sha256:qwen-test",
    }
    monkeypatch.setenv("V238_QWEN_PHYSICAL_MAXIMUM", "2")
    monkeypatch.setattr("pipeline_v3.time.sleep", lambda _seconds: None)
    http_calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        http_calls.append(request)
        if len(http_calls) == 1:
            return json.dumps({
                "model": "qwen-test",
                "message": {"role": "assistant", "content": "{\n", "thinking": ""},
                "done": False,
            }).encode("utf-8")
        content = json.dumps(
            {"translations": [{"id": "1", "translation": "Como você está?"}]},
            ensure_ascii=False,
        )
        return json.dumps({"model": "qwen-test", "message": {"content": content}, "done": True}).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    capture_root = tmp_path / "incomplete-response-captures"
    transport, provider = create_v3_live_transport(
        config, config["primary"], capture_root=capture_root,
    )
    caller = make_v3_transport_call(
        transport,
        response_provider=provider,
        capture_id_prefix="incomplete-retry",
        operation_id="op-incomplete-retry",
    )

    assert caller([{"id": 1, "source_text": "Comment vas-tu ?", "is_sign": False}])[1] == "Como você está?"
    assert len(http_calls) == 2
    assert provider.metrics["physical_client_calls"] == 2
    assert provider.metrics["model_generation_calls"] == 2
    assert isinstance(http_calls[0]["format"], dict)
    assert http_calls[1]["format"] == "json"
    assert http_calls[0]["messages"] == http_calls[1]["messages"]
    assert http_calls[0]["options"] == http_calls[1]["options"]
    assert "modo JSON genérico" in caplog.text

    first_dir = capture_root / "incomplete-retry-000001"
    second_dir = capture_root / "incomplete-retry-000002"
    first_state = json.loads((first_dir / "capture_state.json").read_text(encoding="utf-8"))
    second_state = json.loads((second_dir / "capture_state.json").read_text(encoding="utf-8"))
    assert first_state["state"] == "RESPONSE_DURABLE"
    assert second_state["state"] == "RESPONSE_DURABLE"
    assert not (first_dir / "parsed_response.json").exists()
    assert (second_dir / "parsed_response.json").is_file()
    assert json.loads((first_dir / "request_payload.json").read_text(encoding="utf-8"))["capture_id"] == "incomplete-retry-000001"
    retry_request = json.loads((second_dir / "request_payload.json").read_text(encoding="utf-8"))
    assert retry_request["capture_id"] == "incomplete-retry-000002"
    assert retry_request["format"] == "json"


def test_v3_stops_after_single_ollama_incomplete_response_retry(tmp_path: Path, monkeypatch):
    import json

    from pipeline_v3 import make_v3_transport_call
    from transport_providers import TransportBlocked
    from v3_runtime import create_v3_live_transport

    config = {
        "primary": {"provider": "ollama", "model": "qwen-test", "base_url": "http://ollama.test:11434"},
        "fallback": None,
        "keys": {},
        "model_digest": "sha256:qwen-test",
    }
    monkeypatch.setenv("V238_QWEN_PHYSICAL_MAXIMUM", "2")
    monkeypatch.setattr("pipeline_v3.time.sleep", lambda _seconds: None)
    http_calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        http_calls.append(request)
        return json.dumps({
            "model": "qwen-test",
            "message": {"role": "assistant", "content": "{\n", "thinking": ""},
            "done": False,
        }).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    capture_root = tmp_path / "bounded-incomplete-captures"
    transport, provider = create_v3_live_transport(
        config, config["primary"], capture_root=capture_root,
    )
    caller = make_v3_transport_call(
        transport,
        response_provider=provider,
        capture_id_prefix="bounded-retry",
        operation_id="op-bounded-retry",
    )

    with pytest.raises(TransportBlocked, match="OLLAMA_INCOMPLETE_RESPONSE:.*content_chars=2"):
        caller([{"id": 1, "source_text": "Bonjour.", "is_sign": False}])
    assert len(http_calls) == 2
    assert provider.metrics["physical_client_calls"] == 2
    assert not (capture_root / "bounded-retry-000003").exists()
    assert json.loads((capture_root / "bounded-retry-000001" / "request_payload.json").read_text(encoding="utf-8"))["format"] != "json"
    assert json.loads((capture_root / "bounded-retry-000002" / "request_payload.json").read_text(encoding="utf-8"))["format"] == "json"


def test_v3_does_not_retry_other_ollama_transport_errors(tmp_path: Path, monkeypatch):
    import json

    from pipeline_v3 import make_v3_transport_call
    from transport_providers import TransportBlocked
    from v3_runtime import create_v3_live_transport

    config = {
        "primary": {"provider": "ollama", "model": "qwen-test", "base_url": "http://ollama.test:11434"},
        "fallback": None,
        "keys": {},
        "model_digest": "sha256:qwen-test",
    }
    monkeypatch.setenv("V238_QWEN_PHYSICAL_MAXIMUM", "2")
    http_calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        http_calls.append(request)
        return json.dumps({"error": "model decode failed"}).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    capture_root = tmp_path / "non-empty-error-captures"
    transport, provider = create_v3_live_transport(
        config, config["primary"], capture_root=capture_root,
    )
    caller = make_v3_transport_call(
        transport,
        response_provider=provider,
        capture_id_prefix="no-error-retry",
        operation_id="op-no-error-retry",
    )

    with pytest.raises(TransportBlocked, match="OLLAMA_ERROR:model decode failed"):
        caller([{"id": 1, "source_text": "Bonjour.", "is_sign": False}])
    assert len(http_calls) == 1
    assert provider.metrics["physical_client_calls"] == 1
    assert (capture_root / "no-error-retry-000001" / "raw-http-response.bin").is_file()
    assert not (capture_root / "no-error-retry-000002").exists()


def test_v3_durable_transport_replays_quality_repair_without_second_model_call(tmp_path: Path, monkeypatch):
    from pipeline_v3 import make_v3_transport_call
    from v3_runtime import create_v3_live_transport

    config = {
        "primary": {"provider": "ollama", "model": "qwen-test", "base_url": "http://ollama.test:11434"},
        "fallback": None,
        "keys": {},
        "model_digest": "sha256:qwen-test",
    }
    http_calls = []

    def fake_http_post(_url, _headers, request, delay=0.0):
        http_calls.append(request)
        repair = request["messages"][0]["content"].find("Faça uma única correção direcionada") >= 0
        translation = "Falta de energia?" if repair else "Um blefe de energia?"
        content = __import__("json").dumps(
            {"translations": [{"id": "1", "translation": translation}]},
            ensure_ascii=False,
        )
        return __import__("json").dumps({"message": {"content": content}}).encode("utf-8")

    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    capture_root = tmp_path / "quality-captures"
    payload = [{"id": 1, "source_text": "Une panne de courant ?", "is_sign": False}]

    transport, provider = create_v3_live_transport(
        config,
        config["primary"],
        capture_root=capture_root,
    )
    first = make_v3_transport_call(
        transport,
        response_provider=provider,
        capture_id_prefix="quality-repair",
        operation_id="op-quality",
        source_language="français",
    )
    assert first(payload)[1] == "Falta de energia?"
    assert len(http_calls) == 2
    assert provider.metrics["model_generation_calls"] == 2
    assert provider.metrics["requests_by_operation"] == {"V3_TRANSLATION": 1, "V3_QUALITY_REPAIR": 1}

    def unexpected_http(*_args, **_kwargs):
        pytest.fail("captured quality repair was sent to the model a second time")

    monkeypatch.setattr("web_durable_provider._http_post", unexpected_http)
    replay_transport, replay_provider = create_v3_live_transport(
        config,
        config["primary"],
        capture_root=capture_root,
    )
    replay = make_v3_transport_call(
        replay_transport,
        response_provider=replay_provider,
        capture_id_prefix="quality-repair",
        operation_id="op-quality",
        source_language="français",
    )
    assert replay(payload)[1] == "Falta de energia?"
    assert replay_provider.metrics["offline_replay_reads"] == 2
    assert replay_provider.metrics["model_generation_calls"] == 0
    assert len(http_calls) == 2


def test_v3_operation_budget_stops_physical_calls(tmp_path: Path, monkeypatch):
    from pipeline_v3 import make_v3_transport_call
    from v3_runtime import create_v3_live_transport

    config = {
        "primary": {"provider": "ollama", "model": "qwen-test", "base_url": "http://ollama.test:11434"},
        "fallback": None,
        "keys": {},
        "model_digest": "sha256:qwen-test",
    }
    http_calls = []

    def fake_http_post(url, headers, request, delay=0.0):
        http_calls.append(request)
        content = '{"translations":[{"id":"1","translation":"Olá"}]}'
        return __import__("json").dumps({"message": {"content": content}}).encode("utf-8")

    monkeypatch.setenv("V238_QWEN_PHYSICAL_MAXIMUM", "1")
    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    transport, provider = create_v3_live_transport(
        config,
        config["primary"],
        capture_root=tmp_path / "budget-captures",
    )
    caller = make_v3_transport_call(
        transport, response_provider=provider, capture_id_prefix="budget", operation_id="budget-op"
    )

    payload = [{"id": 1, "source_text": "Hello", "is_sign": False}]
    assert caller(payload)[1] == "Olá"
    with pytest.raises(RuntimeError, match="PHYSICAL_CALL_BUDGET_EXCEEDED"):
        caller(payload)
    assert len(http_calls) == 1


def test_v3_replay_restores_budget_before_new_physical_calls(tmp_path: Path, monkeypatch):
    from pipeline_v3 import make_v3_transport_call
    from v3_runtime import create_v3_live_transport

    config = {
        "primary": {"provider": "ollama", "model": "qwen-test", "base_url": "http://ollama.test:11434"},
        "fallback": None,
        "keys": {},
        "model_digest": "sha256:qwen-test",
    }
    http_calls = []

    def fake_http_post(url, headers, request, delay=0.0):
        http_calls.append(request)
        content = '{"translations":[{"id":"1","translation":"Olá"}]}'
        return __import__("json").dumps({"message": {"content": content}}).encode("utf-8")

    capture_root = tmp_path / "budget-recovery"
    monkeypatch.setenv("V238_QWEN_PHYSICAL_MAXIMUM", "2")
    monkeypatch.setattr("web_durable_provider._http_post", fake_http_post)
    transport, provider = create_v3_live_transport(config, config["primary"], capture_root=capture_root)
    first = make_v3_transport_call(
        transport, response_provider=provider, capture_id_prefix="resume", operation_id="resume-op"
    )
    payload = [{"id": 1, "source_text": "Hello", "is_sign": False}]
    assert first(payload)[1] == "Olá"

    monkeypatch.setenv("V238_QWEN_PHYSICAL_MAXIMUM", "1")
    resumed_transport, resumed_provider = create_v3_live_transport(
        config, config["primary"], capture_root=capture_root,
    )
    resumed = make_v3_transport_call(
        resumed_transport,
        response_provider=resumed_provider,
        capture_id_prefix="resume",
        operation_id="resume-op",
    )
    assert resumed(payload)[1] == "Olá"  # captured response is replayed without budget use
    with pytest.raises(RuntimeError, match="PHYSICAL_CALL_BUDGET_EXCEEDED"):
        resumed(payload)
    assert len(http_calls) == 1


def test_pipeline_orchestrator_dispatches_v3_retranslation_contract(tmp_path: Path):
    from pipeline_orchestrator import execute_pipeline_plan

    source = tmp_path / "retranslate.ass"
    output = tmp_path / "retranslate.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Good night",
    ), encoding="utf-8")

    result = execute_pipeline_plan(
        "v3",
        source,
        output,
        {
            "operation": "RETRANSLATE",
            "transport_call": lambda payload: {
                str(item["id"]): "Boa noite" for item in payload
            },
        },
    )

    assert result["status"] == "COMPLETED"
    assert result["pipeline"] == "v3"
    assert ASSDocumentAST.from_file(output)[0].visible == "Boa noite"


def test_pipeline_orchestrator_fails_closed_without_v3_transport(tmp_path: Path):
    from pipeline_orchestrator import UnsupportedPipelineError, execute_pipeline_plan

    source = tmp_path / "no-transport.ass"
    output = tmp_path / "no-transport.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Good night",
    ), encoding="utf-8")

    with pytest.raises(UnsupportedPipelineError, match="V3_TRANSPORT_CALL_REQUIRED"):
        execute_pipeline_plan("v3_0_0", source, output, {"operation": "RETRANSLATE"})
    assert not output.exists()


def test_pipeline_v3_preserves_romaji_title_and_sign_identity(tmp_path: Path):
    from pipeline_v3 import _is_untranslated_source_copy

    for term in ("SHI KI", "YAMAIRI", "SENBU", "SHAKKU", "Shiki", "Natsuno Yuuki"):
        assert not _is_untranslated_source_copy(term, term, "francês")

    source = tmp_path / "shiki-title.ass"
    output = tmp_path / "shiki-title.pt-BR.ass"
    source.write_text(_small_ass_source(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,SHI KI",
        "Dialogue: 0,0:00:04.00,0:00:06.00,Edits,,0,0,0,,YAMAIRI",
    ), encoding="utf-8")

    result = translate_subtitle_file_v3(
        source,
        output,
        source_language="francês",
        transport_call=lambda payload: {
            str(item["id"]): item["source_text"] for item in payload
        },
    )
    assert result["status"] == "COMPLETED"
    assert result["validation"]["valid"] is True
    doc = ASSDocumentAST.from_file(output)
    assert doc[0].text == "SHI KI"
    assert doc[1].text == "YAMAIRI"


def test_v3_quality_risks_excludes_apostrophe_and_delimiter_mismatch():
    from pipeline_v3 import make_v3_transport_call

    class FakeTransport:
        def endpoint(self): return "http://fake"
        def headers(self): return {}
        def build_request(self, payload): return payload
        def extract_content(self, raw_bytes): return ""

    # Call maker builds transport_call with inner _quality_risks_for_payload
    # We test via translate_subtitle_file_v3 with transport returning translation lacking apostrophe
    payload = [
        {"id": "1", "source_text": "I'm just worried about you.\\NThat's why I'm here.", "protected_names": ()},
    ]
    # In Portuguese, "Eu só estou preocupado com você.\NÉ por isso que estou aqui." has no apostrophes
    translated_map = {
        "1": "Eu só estou preocupado com você.\\NÉ por isso que estou aqui.",
    }
    transport_call = make_v3_transport_call(
        FakeTransport(),
        source_language="inglês",
        target_language="português do Brasil (pt-BR)",
    )
    # Reconstructing the quality risk function check by checking that transport_call accepts this translation without raising contract error
    # We can test translate_subtitle_file_v3 with a lambda returning this translation
    from ass_engine import ASSDocumentAST
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "s.ass"
        out = Path(tmp) / "o.ass"
        src.write_text(_small_ass_source(
            r"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,I'm just worried about you.\NThat's why I'm here."
        ), encoding="utf-8")
        result = translate_subtitle_file_v3(
            src,
            out,
            source_language="inglês",
            transport_call=lambda p: {"0": "Eu só estou preocupado com você.\\NÉ por isso que estou aqui."},
        )
        assert result["status"] == "COMPLETED"
        assert ASSDocumentAST.from_file(out)[0].text == r"Eu só estou preocupado com você.\NÉ por isso que estou aqui."


def test_v3_accepts_translated_song_lines_with_musical_notes():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "song.ass"
        out = Path(tmp) / "song.pt-BR.ass"
        src.write_text(_small_ass_source(
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,♪ Bright & sky, reflected in my eyes ♪"
        ), encoding="utf-8")
        result = translate_subtitle_file_v3(
            src,
            out,
            source_language="inglês",
            transport_call=lambda p: {"0": "♪ Céu brilhante, refletido nos meus olhos ♪"},
        )
        assert result["status"] == "COMPLETED"
        assert ASSDocumentAST.from_file(out)[0].text == "♪ Céu brilhante, refletido nos meus olhos ♪"

