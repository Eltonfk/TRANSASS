"""RC7B1 mechanical budget revision.

The RC7A/RC7B model-facing semantic contract is reused byte-for-byte.  The
only changed request option is num_predict=2048.
"""
from __future__ import annotations

import json
from typing import Any

from v238_rc7b_prompt_contract import build_rc7b_request


RC7B1_NUM_PREDICT = 2048


def build_rc7b1_request(
    *,
    stage: str,
    semantic_group_id: str,
    owners: list[dict[str, Any]],
    target: str,
    presentation: dict[str, Any],
    fixed_order: list[int] | None = None,
    model: str = "qwen3.5:9b",
) -> dict[str, Any]:
    request = build_rc7b_request(
        stage=stage,
        semantic_group_id=semantic_group_id,
        owners=owners,
        target=target,
        presentation=presentation,
        fixed_order=fixed_order,
        model=model,
    )
    request["options"] = json.loads(json.dumps(request["options"]))
    request["options"]["num_predict"] = RC7B1_NUM_PREDICT
    return request
