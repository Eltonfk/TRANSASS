"""Generic base-translation materializer seam for the V2.3.8 composition.

The runtime owns only the protocol and the canonical live V226 wrapper.  Any
historical episode replay implementation is injected by the execution
context and remains outside this package.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol


class BaseTranslationMaterializerError(RuntimeError):
    """The selected base materializer is absent or violated its contract."""


class BaseTranslationMaterializer(Protocol):
    mode: str

    def materialize(
        self,
        source: str | Path,
        output: str | Path,
        *,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Produce a durable base translation and return provenance metrics."""


def require_materializer(context: Mapping[str, Any]) -> BaseTranslationMaterializer:
    value = context.get("base_materializer")
    if value is None or not callable(getattr(value, "materialize", None)):
        raise BaseTranslationMaterializerError("V238_BASE_TRANSLATION_MATERIALIZER_REQUIRED")
    mode = str(getattr(value, "mode", "") or "").upper()
    if mode not in {"CANONICAL_V226_LIVE", "OFFLINE_GROUPED_CAPTURE_REPLAY", "TEST_FIXTURE"}:
        raise BaseTranslationMaterializerError("V238_UNKNOWN_BASE_MATERIALIZER_MODE")
    return value


__all__ = ["BaseTranslationMaterializer", "BaseTranslationMaterializerError", "require_materializer"]
