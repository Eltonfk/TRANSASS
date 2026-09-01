"""RC7A explicit cross-lingual semantic model-facing contract."""
from __future__ import annotations

import json
from typing import Any

from v238_rc3_atom_owner_vector import target_atoms
from v238_rc7_factorized_selector import CHOICE_SCHEMA


def build_rc7a_request(*, stage: str, semantic_group_id: str, owners: list[dict[str, Any]], target: str, presentation: dict[str, Any], model: str, fixed_order: list[int] | None = None) -> dict[str, Any]:
    atoms = target_atoms(target)
    labels = presentation["canonical_to_presented"]
    source_lines = [f"OWNER {chr(64 + labels[i])}: {owner['source_text']}" for i, owner in enumerate(owners, 1)]
    common = [
        "SOURCE_LANGUAGE: English",
        "SOURCE OWNERS are semantic source fragments written in English.",
        "TARGET_LANGUAGE: Brazilian Portuguese (pt-BR)",
        "TARGET ATOMS are immutable fragments of an already approved Brazilian Portuguese translation.",
        "The task is CROSS-LINGUAL SEMANTIC ALIGNMENT.",
        "For each source owner, identify where the SAME MEANING appears in the approved Portuguese target.",
        "Match by meaning, not by spelling, word similarity, token similarity, or source position.",
        "English and Portuguese words are expected to differ.",
        "The approved Portuguese translation may reorder semantic concepts.",
        "Do NOT preserve source owner order unless the meanings actually occur in that order in the target.",
        "Do not use ASS, visual, timestamp, or source-offset ownership; this is semantic ownership only.",
    ]
    lines = [f"SEMANTIC_GROUP: {semantic_group_id}", *common, "SOURCE OWNERS:", *source_lines, "TARGET ATOMS (immutable, shown once):", *[f"ATOM {i}: {atom}" for i, atom in enumerate(atoms, 1)]]
    if stage == "owner_order":
        lines.extend([
            "STAGE: SEMANTIC ORDER ONLY.",
            "Do not decide run lengths, atom boundaries, ASS styling, or visual properties.",
            "OWNER_ORDER means the order in which the meanings of the source owners appear in the target atoms.",
            "For example, if the meaning of OWNER C appears first in the target, OWNER C must appear first in the selected OWNER_ORDER.",
            "CHOICE X such as C A B means the meaning represented by OWNER C appears first in the approved target, then OWNER A, then OWNER B.",
            "Choose NONE only if NO listed semantic owner order can represent the order of meanings in the approved target.",
        ])
    elif stage == "run_composition":
        if fixed_order is None:
            raise ValueError("RC7A_FIXED_ORDER_REQUIRED")
        fixed_labels = " ".join(chr(64 + labels[i]) for i in fixed_order)
        lines.extend([
            "STAGE: CONTIGUOUS SEMANTIC RUN COMPOSITION ONLY.",
            f"The canonical semantic owner order is already fixed for this stage: {fixed_labels}.",
            "Do NOT reorder owners in Stage 2.",
            "A run composition specifies how many consecutive TARGET ATOMS belong semantically to each owner, in the fixed owner order.",
            "For example, fixed order C A B and composition [2,3,1] means C owns target atoms 1–2, A owns atoms 3–5, and B owns atom 6.",
            "Here 'owns' means those target atoms express the meaning of that English source owner in the approved Portuguese translation.",
            "This does not mean visual ownership, ASS tag ownership, string similarity, or source-offset ownership.",
            "Choose NONE only if no listed composition can represent the semantic contiguous runs.",
        ])
    else:
        raise ValueError("RC7A_UNKNOWN_STAGE")
    lines.extend(["COMPLETE CANDIDATE CATALOG:", *presentation["catalog"], f"NONE CHOICE: {presentation['none_choice_id']}", "Return only a JSON root array containing exactly one integer choice ID. Do not return text, labels, vectors, explanations, objects, or mappings."])
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "Select exactly one candidate under the explicit cross-lingual semantic contract. Return only [choice_id]. Do not return mappings, text, or objects."},
            {"role": "user", "content": "\n".join(lines)},
        ],
        "format": json.loads(json.dumps(CHOICE_SCHEMA)),
        "options": {"temperature": 0.0, "num_ctx": 2560, "num_predict": 32},
        "stream": False,
        "think": False,
        "keep_alive": "30m",
    }
