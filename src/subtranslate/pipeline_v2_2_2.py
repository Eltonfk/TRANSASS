"""Versioned V2.2.2 facade.

The frozen V2.1.3 engine and V2.2.1 complex-ASS behavior are reused; the
candidate-specific lexical break anchors live in ``production_v2_2_2_adapter``.
"""

from production_v2_2_2_adapter import (  # noqa: F401
    APPROVED_MODEL,
    APPROVED_PIPELINE,
    V222MemoryRunner,
    translate_subtitle_file,
    translate_subtitle_file_v2_2_2,
)
