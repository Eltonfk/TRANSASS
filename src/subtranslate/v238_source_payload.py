"""Canonical V2.3.8 source-payload envelope helper.

This small deterministic seam retains source whitespace while allocating a
validated target payload.  It has no episode, event, record or staging
identity and is safe to reuse for new episodes.
"""
from __future__ import annotations

from ass_structure import replace_source_payload


def rc4_replace_source_payload(source_part: str, target_part: str) -> str:
    """Replace linguistic payload using the shared ASS structure contract."""
    return replace_source_payload(source_part, target_part)


__all__ = ["rc4_replace_source_payload"]
