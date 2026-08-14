"""Versioned V2.2.1 pipeline facade.

The linguistic engine remains imported from the frozen V2.1.3 baseline; the
candidate-specific structural envelope lives in ``production_v2_2_1_adapter``.
"""

from production_v2_2_1_adapter import (  # noqa: F401
    APPROVED_MODEL,
    APPROVED_PIPELINE,
    V221MemoryRunner,
    analyze_vector_event,
    translate_subtitle_file,
    translate_subtitle_file_v2_2_1,
)

