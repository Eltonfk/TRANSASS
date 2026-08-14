"""Versioned V2.2.6 facade over the frozen V2.2.5 adapter."""

from production_v2_2_6_adapter import (  # noqa: F401
    APPROVED_MODEL,
    APPROVED_PIPELINE,
    SIGN_GROUP_TRANSLATION_AMBIGUOUS,
    SIGN_GROUP_STRUCTURAL_FAILURE,
    V226MemoryRunner,
    apply_sign_group_consistency,
    augment_sign_candidate_v2_2_6,
    build_sign_semantic_groups,
    is_sign_event,
    validate_animated_sign_group_translation_coverage,
    validate_sign_group_language_consistency,
    translate_subtitle_file,
    translate_subtitle_file_v2_2_6,
)
