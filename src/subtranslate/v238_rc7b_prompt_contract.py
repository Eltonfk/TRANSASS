"""RC7B reasoning-enabled wrapper for the explicit RC7A contract.

RC7B deliberately changes only the synthetic semantic-arbiter runtime
configuration.  The cross-lingual prompt text, candidate generator, wire
schema, and semantic validator remain the RC7A contracts.
"""
from __future__ import annotations

import json
from typing import Any

from v238_rc7a_prompt_contract import build_rc7a_request


RC7B_MODEL = "qwen3.5:9b"
RC7B_OPTIONS = {
    "temperature": 0.0,
    "num_ctx": 4096,
    "num_predict": 384,
}


def build_rc7b_request(
    *,
    stage: str,
    semantic_group_id: str,
    owners: list[dict[str, Any]],
    target: str,
    presentation: dict[str, Any],
    fixed_order: list[int] | None = None,
    model: str = RC7B_MODEL,
) -> dict[str, Any]:
    """Build an RC7A-equivalent request with RC7B reasoning configuration."""
    request = build_rc7a_request(
        stage=stage,
        semantic_group_id=semantic_group_id,
        owners=owners,
        target=target,
        presentation=presentation,
        model=model,
        fixed_order=fixed_order,
    )
    request["think"] = True
    request["options"] = json.loads(json.dumps(RC7B_OPTIONS))
    return request


def model_facing_text(request: dict[str, Any]) -> str:
    return "\n".join(str(message.get("content", "")) for message in request.get("messages", []))
