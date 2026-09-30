"""In-memory semantic translation orchestrator for the V3 unified architecture.

Orchestrates batching, sign-group consistency, and linguistic reconstruction
directly on the in-memory ASSDocumentAST without writing intermediate temporary
files to disk.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Sequence

from ass_engine import (
    ASSDocumentAST,
    ASSEventNode,
    break_count,
    break_tokens,
    clean_residual_override_tags,
    is_drawing_event,
    validate_document_structure,
    visible_text,
    TAG_RE,
)
from ass_structure import replace_source_payload

# ---------------------------------------------------------------------------
# Sign Grouping & Clustering
# ---------------------------------------------------------------------------

_STYLE_HINTS = ("sign", "plate", "card", "screen", "onscreen", "on-screen", "caption", "title", "text")
_WORD_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)


@lru_cache(maxsize=4096)
def _visible_words(text: str) -> tuple[str, ...]:
    clean = visible_text(text, line_break=" ")
    return tuple(w.casefold().replace("’", "'") for w in _WORD_RE.findall(clean))


def is_sign_node(node: ASSEventNode) -> bool:
    """Determine if an event is a sign/screen text requiring consistency."""
    if node.is_comment or node.is_drawing:
        return False
    style = (node.style or "").casefold()
    return any(hint in style for hint in _STYLE_HINTS)


def cluster_temporal_signs(nodes: list[ASSEventNode], tolerance_ms: int = 200) -> dict[int, int]:
    """Group sign nodes occurring in overlapping or close temporal intervals."""
    ordered = sorted(nodes, key=lambda n: (n.start, n.end, n.index))
    mapping: dict[int, int] = {}
    current: list[ASSEventNode] = []
    current_end = -1
    cluster = 0
    for node in ordered:
        if current and node.start > current_end + tolerance_ms:
            for m in current:
                mapping[m.index] = cluster
            cluster += 1
            current = []
            current_end = -1
        current.append(node)
        current_end = max(current_end, node.end)
    for m in current:
        mapping[m.index] = cluster
    return mapping


def _sign_equivalence_key(node: ASSEventNode) -> tuple[str, tuple[str, ...], str]:
    """Include punctuation and control positions in semantic equivalence.

    Override blocks can differ between layers of the same sign. Visible
    punctuation, spacing, line breaks and hard spaces belong to its wording.
    """
    return (TAG_RE.sub("", node.text), tuple(break_tokens(node.text)), node.style.casefold())


def build_sign_groups(nodes: list[ASSEventNode]) -> list[dict[str, Any]]:
    """Build semantic sign groups that share identical wording and styling."""
    candidates = [n for n in nodes if is_sign_node(n) and _visible_words(n.text)]
    if not candidates:
        return []
    clusters = cluster_temporal_signs(candidates)
    grouped: dict[tuple[int, tuple[str, tuple[str, ...], str]], list[ASSEventNode]] = defaultdict(list)

    for node in candidates:
        cluster_id = clusters.get(node.index, node.index)
        grouped[(cluster_id, _sign_equivalence_key(node))].append(node)

    result: list[dict[str, Any]] = []
    for (cluster_id, key), members in sorted(grouped.items(), key=lambda x: x[0]):
        fingerprint = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]
        members.sort(key=lambda m: m.index)
        sample = members[0]
        result.append({
            "group_id": f"sign-{cluster_id}-{fingerprint}",
            "fingerprint": fingerprint,
            "source_text": sample.visible,
            "sample_node": sample,
            "member_indices": [m.index for m in members],
            "members": members,
        })
    return result


# ---------------------------------------------------------------------------
# In-Memory Translation Batching
# ---------------------------------------------------------------------------

@dataclass
class TranslationUnit:
    """Single linguistic unit submitted to the translation provider."""
    id: int | str
    source_text: str
    context: str = ""
    is_sign: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranslationBatch:
    """Group of units sent in one model API request."""
    batch_index: int
    units: list[TranslationUnit] = field(default_factory=list)

    @property
    def total_words(self) -> int:
        return sum(len(_visible_words(u.source_text)) for u in self.units)


class InMemorySemanticOrchestrator:
    """Orchestrates in-memory batching, dispatching, and envelope reconstruction."""

    def __init__(
        self,
        target_batch_size: int = 16,
        *,
        max_batch_words: int = 90,
        max_batch_chars: int = 420,
    ) -> None:
        self.target_batch_size = max(1, target_batch_size)
        self.max_batch_words = max(1, max_batch_words)
        self.max_batch_chars = max(1, max_batch_chars)

    def plan_batches(
        self,
        doc: ASSDocumentAST,
        *,
        excluded_event_indices: set[int] | None = None,
        additional_units: Sequence[TranslationUnit] = (),
    ) -> tuple[list[TranslationBatch], list[dict[str, Any]]]:
        """Create planned dialogue batches and distinct sign groups."""
        excluded_event_indices = excluded_event_indices or set()
        sign_groups = build_sign_groups(doc.events)
        sign_member_indices = set()
        for g in sign_groups:
            sign_member_indices.update(g["member_indices"])

        units: list[TranslationUnit] = []

        # 1. Dialogue units
        for node in doc.events:
            if node.is_comment or node.is_drawing:
                continue
            if node.index in excluded_event_indices:
                continue
            if node.index in sign_member_indices:
                continue
            vis = node.visible.strip()
            if not vis or not _visible_words(vis):
                continue
            units.append(TranslationUnit(
                id=node.index,
                source_text=vis,
                is_sign=False,
                metadata={"node": node},
            ))

        # 2. Add representative sign units (translated once per group)
        for g in sign_groups:
            units.append(TranslationUnit(
                id=g["group_id"],
                source_text=g["source_text"],
                is_sign=True,
                metadata={"group": g},
            ))

        units.extend(additional_units)

        # 3. Assemble adaptive cognitive batches
        batches: list[TranslationBatch] = []
        current: list[TranslationUnit] = []
        current_words = 0
        current_chars = 0

        for u in units:
            unit_words = len(_visible_words(u.source_text))
            unit_chars = len(u.source_text)

            exceeds_count = len(current) >= self.target_batch_size
            exceeds_density = current and (
                (current_words + unit_words > self.max_batch_words)
                or (current_chars + unit_chars > self.max_batch_chars)
            )

            if exceeds_count or exceeds_density:
                batches.append(TranslationBatch(batch_index=len(batches), units=current))
                current = []
                current_words = 0
                current_chars = 0

            current.append(u)
            current_words += unit_words
            current_chars += unit_chars

        if current:
            batches.append(TranslationBatch(batch_index=len(batches), units=current))

        return batches, sign_groups

    def apply_translations(
        self,
        doc: ASSDocumentAST,
        translations: dict[int | str, str],
        sign_groups: list[dict[str, Any]],
    ) -> ASSDocumentAST:
        """Apply received translations to document nodes while preserving ASS style envelopes."""
        # 1. Map sign group translations to member indices
        sign_translations_by_index: dict[int, str] = {}
        nodes_by_index = {node.index: node for node in doc.events}
        seen_members: set[int] = set()
        seen_group_ids: set[str] = set()
        # Validate every membership before mutating any event, including stale
        # or externally supplied groups. Hash equality is not semantic proof.
        for group in sign_groups:
            if group["group_id"] in seen_group_ids:
                raise ValueError("SIGN_GROUP_ID_MISMATCH")
            seen_group_ids.add(group["group_id"])
            sample = group["sample_node"]
            sample_key = _sign_equivalence_key(sample)
            for index in group["member_indices"]:
                member = nodes_by_index.get(index)
                if (
                    member is None
                    or not is_sign_node(member)
                    or _sign_equivalence_key(member) != sample_key
                    or member.visible != group["source_text"]
                    or index in seen_members
                ):
                    raise ValueError("SIGN_GROUP_MEMBER_MISMATCH")
                seen_members.add(index)
        for g in sign_groups:
            trans = translations.get(g["group_id"])
            if trans:
                for idx in g["member_indices"]:
                    sign_translations_by_index[idx] = trans

        # 2. Apply translations to event nodes in-memory using payload replacement
        for node in doc.events:
            target_text = sign_translations_by_index.get(node.index) or translations.get(node.index)
            if not target_text:
                continue

            # Normaliza quebras de linha literais (\n) para controle ASS (\N)
            normalized_target = target_text.replace("\r\n", r"\N").replace("\n", r"\N")

            orig_raw = node.text
            has_tags = "{" in orig_raw or r"\h" in orig_raw
            break_mismatch = break_count(orig_raw) != break_count(normalized_target)

            if has_tags or (break_count(orig_raw) > 0 and break_mismatch):
                node.text = replace_source_payload(orig_raw, normalized_target)
            else:
                node.text = normalized_target

        return doc
