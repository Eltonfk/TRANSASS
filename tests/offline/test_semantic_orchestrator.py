"""Unit tests for the in-memory semantic_orchestrator.py module."""

import pytest
from ass_engine import ASSDocumentAST, ASSEventNode
from semantic_orchestrator import (
    InMemorySemanticOrchestrator,
    build_sign_groups,
    cluster_temporal_signs,
    is_sign_node,
)


def test_sign_clustering_and_grouping():
    nodes = [
        ASSEventNode(index=0, start=1000, end=2000, style="Sign", text=r"{\an8}Towa High School"),
        ASSEventNode(index=1, start=1050, end=2050, style="Sign", text=r"{\an8\fs30}Towa High School"),
        # Outra placa noutro momento
        ASSEventNode(index=2, start=8000, end=9000, style="Sign", text=r"{\pos(100,200)}Music Festival"),
        # Diálogo normal
        ASSEventNode(index=3, start=1500, end=3000, style="Default", text="Hey, look at that!"),
    ]

    assert is_sign_node(nodes[0]) is True
    assert is_sign_node(nodes[1]) is True
    assert is_sign_node(nodes[2]) is True
    assert is_sign_node(nodes[3]) is False

    groups = build_sign_groups(nodes)
    assert len(groups) == 2
    # O primeiro grupo agrupa os dois eventos da mesma placa (Towa High School)
    assert groups[0]["source_text"] == "Towa High School"
    assert groups[0]["member_indices"] == [0, 1]
    # O segundo grupo contém a segunda placa (Music Festival)
    assert groups[1]["source_text"] == "Music Festival"
    assert groups[1]["member_indices"] == [2]


def test_in_memory_batch_planning_and_application():
    raw_ass = (
        "[Script Info]\nTitle: Test\nScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n"
        "Style: Sign,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Good morning!\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,Sign,,0,0,0,,{\\an8}Library Entrance\n"
        "Dialogue: 0,0:00:01.05,0:00:03.05,Sign,,0,0,0,,{\\an8\\c&HFF0000&}Library Entrance\n"
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Let us enter.\n"
    )

    doc = ASSDocumentAST.from_string(raw_ass)
    orchestrator = InMemorySemanticOrchestrator(target_batch_size=2)

    batches, sign_groups = orchestrator.plan_batches(doc)

    # 2 falas + 1 grupo de placa = 3 unidades no total, divididas em batches de tamanho 2
    assert len(sign_groups) == 1
    assert sign_groups[0]["source_text"] == "Library Entrance"
    assert sign_groups[0]["member_indices"] == [1, 2]

    total_units = sum(len(b.units) for b in batches)
    assert total_units == 3

    # Simular resposta do modelo de IA
    simulated_translations = {
        0: "Bom dia!",
        sign_groups[0]["group_id"]: "Entrada da Biblioteca",
        3: "Vamos entrar.",
    }

    translated_doc = orchestrator.apply_translations(doc, simulated_translations, sign_groups)

    assert translated_doc[0].text == "Bom dia!"
    # Os dois eventos da placa devem receber a tradução preservando suas tags originais individuais
    assert translated_doc[1].text == r"{\an8}Entrada da Biblioteca"
    assert translated_doc[2].text == r"{\an8\c&HFF0000&}Entrada da Biblioteca"
    assert translated_doc[3].text == "Vamos entrar."
