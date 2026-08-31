"""V2.2.6 sign-group consistency over the frozen V2.2.5 adapter.

The only semantic change in this candidate is a post-translation sign-group
contract.  Equivalent linguistic frames share one deterministic semantic
result; the V2.2.4/V2.2.5 ASS envelope remains authoritative for every member.
Dialogue, songs, drawings, timing, tags and retry policy are not broadened.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pysubs2

import production_v2_2_5_adapter as frozen_v225
from pipeline_v2_1_3 import CRITICAL_FLAGS, Event, TAG_RE, Unit, load_events, validate_structure
from production_v2_2_1_adapter import _restore_tags_by_source_segment
from production_v2_2_4_adapter import _restore_multiline_sign_card_v224
from production_v2_2_5_adapter import (
    APPROVED_MODEL as V225_MODEL,
    V225MemoryRunner,
    V225MemoryClient,
    is_effective_source_copy,
    effective_source_copy_key,
    translate_subtitle_file_v2_2_5,
)


APPROVED_PIPELINE = "v2_2_6"
APPROVED_MODEL = V225_MODEL
SIGN_GROUP_TRANSLATION_RETRY = "SIGN_GROUP_TRANSLATION"
SIGN_GROUP_TRANSLATION_AMBIGUOUS = "SIGN_GROUP_TRANSLATION_AMBIGUOUS"
SIGN_GROUP_STRUCTURAL_FAILURE = "SIGN_GROUP_STRUCTURAL_FAILURE"

_PROTECTED_CLASSES = {
    "MUSIC_OR_KARAOKE", "SONG_LYRICS_PRESERVED", "ROMAJI_PRESERVED",
    "ROMANIZATION_GLOSS", "TECHNICAL_OR_EMPTY",
}
_STYLE_HINTS = ("sign", "plate", "card", "screen", "onscreen", "on-screen", "caption", "title", "text")
_SAFE_IDENTITY_TERMS = {
    "menu", "status", "online", "offline", "login", "logout", "warning",
    "error", "ok", "start", "stop", "cancel", "option", "options",
}
_WORD_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)


def _visible(text: str) -> str:
    return " ".join(
        TAG_RE.sub("", text or "")
        .replace(r"\N", " ")
        .replace(r"\n", " ")
        .replace(r"\h", " ")
        .split()
    ).strip()


def _words(text: str) -> tuple[str, ...]:
    return tuple(word.casefold().replace("’", "'") for word in _WORD_RE.findall(_visible(text)))


def _drawing_only(event: Event) -> bool:
    raw = event.original_text or ""
    if re.search(r"\\p[1-9]", raw):
        return True
    visible = _visible(raw)
    return bool(re.match(r"^(?:m|n|l|b|s|p|c)(?:\s|[-+0-9])", visible, re.I)) and not re.search(r"[A-Za-z]{3,}", visible)


def is_sign_event(event: Event) -> bool:
    """Conservative, release-independent sign eligibility predicate."""
    if event.classification in _PROTECTED_CLASSES:
        return False
    style = (event.style or "").casefold()
    explicit = event.classification == "SIGN_OR_SCREEN_TEXT"
    hinted = any(token in style for token in _STYLE_HINTS)
    # Positioning alone is intentionally insufficient: ordinary dialogue is
    # frequently positioned.  Production classification or an explicit sign
    # style is required before touching an event.
    return bool(explicit or hinted)


def _temporal_clusters(events: list[Event], tolerance_ms: int = 180) -> dict[int, int]:
    ordered = sorted(events, key=lambda item: (item.start, item.end, item.id))
    mapping: dict[int, int] = {}
    current: list[Event] = []
    current_end = -1
    cluster = 0
    for event in ordered:
        if current and event.start > current_end + tolerance_ms:
            for member in current:
                mapping[member.id] = cluster
            cluster += 1
            current = []
            current_end = -1
        current.append(event)
        current_end = max(current_end, event.end)
    for member in current:
        mapping[member.id] = cluster
    return mapping


def _source_fingerprint(event: Event) -> str:
    payload = {
        "words": _words(event.original_text),
        "breaks": event.original_text.count(r"\N"),
        "style": event.style.casefold(),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def build_sign_semantic_groups(events: list[Event]) -> list[dict[str, Any]]:
    """Group equivalent linguistic frames without merging drawings or dialogue."""
    candidates = [event for event in events if is_sign_event(event)]
    clusters = _temporal_clusters(candidates)
    grouped: dict[tuple[int, str], list[Event]] = defaultdict(list)
    for event in candidates:
        if _drawing_only(event) or not _words(event.original_text):
            continue
        grouped[(clusters.get(event.id, event.id), _source_fingerprint(event))].append(event)
    result: list[dict[str, Any]] = []
    for (cluster, fingerprint), members in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        members.sort(key=lambda event: event.id)
        source = members[0].clean_text
        semantic_id = f"sign-semantic-{cluster}-{fingerprint}"
        result.append({
            "semantic_id": semantic_id,
            "temporal_cluster": cluster,
            "source_fingerprint": fingerprint,
            "source_text": source,
            "event_ids": [event.id for event in members],
            "event_count": len(members),
            "style_set": sorted({event.style for event in members}),
            "layer_set": sorted({event.layer for event in members}),
            "timestamp_range": [min(event.start for event in members), max(event.end for event in members)],
            "members": members,
        })
    return result


def _state(source: str, output: str) -> str:
    if not _words(source):
        return "NONLINGUISTIC"
    if not _visible(output):
        return "MISSED"
    if _words(source) == _words(output):
        return "PRESERVED_OR_IDENTITY"
    return "TRANSLATED"


def _identity_expected(source: str) -> bool:
    words = _words(source)
    return len(words) == 1 and words[0] in _SAFE_IDENTITY_TERMS


def _canonical_variant(outputs: list[str]) -> str | None:
    visible = [value.strip() for value in outputs if _visible(value)]
    if not visible:
        return None
    counts = Counter(_words(value) for value in visible)
    best_words, _ = max(counts.items(), key=lambda item: (item[1], -visible.index(next(value for value in visible if _words(value) == item[0]))))
    return next(value for value in visible if _words(value) == best_words)


def _clean_group_surface(text: str, source_part: str = "") -> str:
    """Remove malformed ASS wrappers without treating source markers as words."""
    value = re.sub(r"\\\{[^{}]*\\\}", "", text or "")
    value = TAG_RE.sub("", value).replace(r"\h", " ")
    # Some legacy materialized cards serialized ``\h`` as a leading ``h``
    # after an escaped override.  Only remove that marker when the source
    # segment actually owns a hard-space token and the following character is
    # an uppercase lexical start; ordinary words beginning with h are safe.
    if r"\h" in source_part and re.match(r"^\s*h(?=[A-ZÀ-Ý])", value):
        value = re.sub(r"^(\s*)h(?=[A-ZÀ-Ý])", r"\1", value, count=1)
    return value


def _replace_source_payload(source_part: str, target_part: str) -> str:
    """Replace only lexical payload; retain source tags and ``\\h`` tokens."""
    token_re = re.compile(r"(\{[^}]*\}|\\h)")
    tokens = token_re.split(source_part)
    lexical_indices = [i for i, token in enumerate(tokens) if token and not token_re.fullmatch(token)]
    if not lexical_indices:
        return source_part
    if len(lexical_indices) == 1:
        replacements = {lexical_indices[0]: target_part}
    else:
        source_chunks = [tokens[i] for i in lexical_indices]
        target_words = re.findall(r"\S+", target_part)
        total = max(1, sum(len(re.findall(r"[\wÀ-ÿ]+", chunk, re.UNICODE)) for chunk in source_chunks))
        replacements: dict[int, str] = {}
        start = 0
        for pos, index in enumerate(lexical_indices):
            if pos == len(lexical_indices) - 1:
                end = len(target_words)
            else:
                count = len(re.findall(r"[\wÀ-ÿ]+", source_chunks[pos], re.UNICODE))
                end = min(len(target_words), max(start + 1, round(len(target_words) * count / total)))
            replacements[index] = " ".join(target_words[start:end])
            start = end
    return "".join(replacements.get(i, token) for i, token in enumerate(tokens))


def validate_animated_sign_group_translation_coverage(report: dict[str, Any]) -> dict[str, Any]:
    """Validator contract: equivalent animation members share one state."""
    failures = list(report.get("ambiguous_groups", [])) + list(report.get("mixed_after_groups", []))
    return {
        "name": "ANIMATED_SIGN_GROUP_TRANSLATION_COVERAGE",
        "valid": not failures,
        "failure_count": len(failures),
    }


def validate_sign_group_language_consistency(report: dict[str, Any]) -> dict[str, Any]:
    """Validator contract: one semantic sign group cannot alternate languages."""
    failures = list(report.get("mixed_after_groups", []))
    return {
        "name": "SIGN_GROUP_LANGUAGE_CONSISTENCY",
        "valid": not failures,
        "failure_count": len(failures),
    }


def _member_envelope_is_safe(event: Event, text: str) -> bool:
    """Whether an existing member can be retained without fan-out repair."""
    if re.search(r"\\\{", text or ""):
        return False
    if (text or "").count(r"\N") != len(event.line_break_boundaries):
        return False
    if (text or "").count(r"\h") != event.original_text.count(r"\h"):
        return False
    if sorted(TAG_RE.findall(event.original_text or "")) != sorted(TAG_RE.findall(text or "")):
        return False
    return True


def _restore_semantic_text(event: Event, translated: str) -> tuple[str | None, dict[str, Any]]:
    """Fan out only semantic text through the existing V2.2.4 envelope."""
    # Keep the candidate's segment boundaries/leading spaces as semantic
    # input.  Flattening through ``_visible`` loses the deliberate space after
    # a tag at a multiline-card boundary (and can make a valid ``\N`` look
    # like a lexical split).  Tags are discarded only while the source-owned
    # envelope is rebuilt below.
    source_parts = event.original_text.split(r"\N")
    raw_parts = (translated or "").split(r"\N")
    semantic_parts = [
        _clean_group_surface(part, source_part)
        for part, source_part in zip(raw_parts, source_parts)
    ]
    if len(raw_parts) > len(source_parts):
        semantic_parts.extend(_clean_group_surface(part) for part in raw_parts[len(source_parts):])
    semantic = r"\N".join(semantic_parts)
    if not _visible(semantic):
        return None, {"failure": "EMPTY_SEMANTIC_OUTPUT"}

    translated_parts = semantic.split(r"\N")
    if len(source_parts) == len(translated_parts):
        # Reapply the source-owned ASS tags at their source segment/offset.
        # This is the same deterministic contract used by V2.2.4, but keeps
        # the translated member's intentional leading whitespace.
        # Standalone visual symbols (notably ``&`` in sign cards) are not
        # linguistic slots.  Preserve their source glyph instead of allowing
        # a model variant such as ``e`` to create a false break inside a word.
        for index, source_part in enumerate(source_parts):
            source_visible = TAG_RE.sub("", source_part).strip()
            if source_visible and not _WORD_RE.search(source_visible) and source_visible not in {"\\N", "\\h"}:
                translated_parts[index] = source_part
        restored = r"\N".join(
            _replace_source_payload(source_part, target_part)
            for source_part, target_part in zip(source_parts, translated_parts)
        )
        evidence = {
            "class": "SIGN_GROUP_FANOUT",
            "source_segments": source_parts,
            "translated_segments": translated_parts,
            "reconstructed": restored,
        }
    else:
        # A single-line canonical result may still be safely split by the
        # existing V2.2.4 lexical-safe splitter.  It is deliberately a
        # fallback; no raw-character offset is used to place a break.
        restored, evidence = _restore_multiline_sign_card_v224(event, semantic)
    if restored is None:
        return None, evidence if evidence else {"failure": "ASS_INLINE_TAG_ANCHOR_FAILURE"}
    return restored, {"class": "SIGN_GROUP_FANOUT", "reconstructed": restored}


def _apply_groups_to_results(events: list[Event], result_items: list[dict[str, Any]], *, allow_ambiguous: bool = False) -> dict[str, Any]:
    by_id = {item["id"]: item for item in result_items}
    groups = build_sign_semantic_groups(events)
    changed_events: set[int] = set()
    changed_groups: set[str] = set()
    ambiguous: list[dict[str, Any]] = []
    structural: list[dict[str, Any]] = []
    before_counts = Counter()
    after_counts = Counter()
    for group in groups:
        members = group["members"]
        source = group["source_text"]
        outputs = [str(by_id[event.id].get("final_text") or "") for event in members if event.id in by_id]
        states = [_state(source, output) for output in outputs]
        before_counts.update(states)
        translated = [output for output, state in zip(outputs, states) if state == "TRANSLATED"]
        canonical = _canonical_variant(translated)
        if canonical is None:
            if len(set(states)) > 1 or any(state == "MISSED" for state in states):
                if not _identity_expected(source) and not allow_ambiguous:
                    ambiguous.append({"semantic_id": group["semantic_id"], "event_ids": group["event_ids"], "source": source, "states": states})
            after_counts.update(states)
            continue
        for event in members:
            item = by_id[event.id]
            # A member that already has the canonical semantic result is not
            # reconstructed a second time.  This is important for valid
            # multiline cards whose existing ASS envelope intentionally puts a
            # tag immediately after ``\N``; rebuilding it would erase the
            # validated leading-space contract and create a false structural
            # failure.
            if item.get("final_text") == canonical and _member_envelope_is_safe(event, str(item.get("final_text") or "")):
                continue
            restored, evidence = _restore_semantic_text(event, canonical)
            if restored is None:
                structural.append({"semantic_id": group["semantic_id"], "event_id": event.id, "evidence": evidence})
                continue
            if item.get("final_text") != restored:
                item["final_text"] = restored
                item["final_model"] = item.get("final_model") or "v226-sign-group-fanout"
                item.setdefault("flags", []).append("SIGN_GROUP_FANOUT")
                changed_events.add(event.id)
                changed_groups.add(group["semantic_id"])
        after_counts.update(_state(source, str(by_id[event.id].get("final_text") or "")) for event in members)
    mixed_after = []
    for group in groups:
        states = [_state(group["source_text"], str(by_id[event.id].get("final_text") or "")) for event in group["members"] if event.id in by_id]
        if len(set(states)) > 1 and not _identity_expected(group["source_text"]):
            mixed_after.append({"semantic_id": group["semantic_id"], "event_ids": group["event_ids"], "states": states, "source": group["source_text"]})
    result = {
        "groups": [{key: value for key, value in group.items() if key != "members"} for group in groups],
        "semantic_group_count": len(groups),
        "linguistic_event_count": sum(group["event_count"] for group in groups),
        "fanout_group_count": len(changed_groups),
        "fanout_event_count": len(changed_events),
        "changed_event_ids": sorted(changed_events),
        "ambiguous_groups": ambiguous,
        "mixed_after_groups": mixed_after,
        "structural_failures": structural,
        "states_before": dict(before_counts),
        "states_after": dict(after_counts),
        "coverage_valid": not ambiguous and not mixed_after and not structural,
    }
    result["coverage_validator"] = validate_animated_sign_group_translation_coverage(result)
    result["language_validator"] = validate_sign_group_language_consistency(result)
    result["coverage_valid"] = bool(
        result["coverage_valid"]
        and result["coverage_validator"]["valid"]
        and result["language_validator"]["valid"]
    )
    return result


def apply_sign_group_consistency(source_subs: pysubs2.SSAFile, candidate_subs: pysubs2.SSAFile, events: list[Event]) -> dict[str, Any]:
    """Apply/fan-out signs in a candidate ASS without touching dialogue."""
    items = [{"id": event.id, "final_text": candidate_subs[event.original_index].text, "flags": []} for event in events]
    report = _apply_groups_to_results(events, items)
    for item in items:
        candidate_subs[item["id"]].text = item["final_text"]
    return report


class V226MemoryClient(V225MemoryClient):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.sign_group_translation = False

    def finalize_request_payload(self, payload, units, phase):
        payload = super().finalize_request_payload(payload, units, phase)
        if not self.sign_group_translation:
            return payload
        payload = dict(payload)
        messages = list(payload.get("messages") or [])
        if messages:
            first = dict(messages[0])
            first["content"] = str(first.get("content") or "") + (
                "\n\nSIGN_GROUP_TRANSLATION: este TARGET é texto linguístico de uma placa/card. "
                "Traduza uma única vez para PT-BR natural. Não devolva desenho, tags ASS, "
                "timestamps ou estrutura; não copie o inglês quando houver tradução possível."
            )
            messages[0] = first
            payload["messages"] = messages
        return payload

    def call(self, units, events, contexts, simplified=False, phase="main"):
        found, issues, observation = super().call(units, events, contexts, simplified=simplified, phase=phase)
        if self.sign_group_translation:
            observation["retry_reason"] = SIGN_GROUP_TRANSLATION_RETRY
        return found, issues, observation


class V226MemoryRunner(V225MemoryRunner):
    """V2.2.5 runner plus bounded sign-group semantic fan-out."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        previous = self.client
        self.client = V226MemoryClient(
            previous.config, self.calls, previous.glossary,
            previous.memory, previous.anime_series_id, previous.episode_id,
            previous.job_id,
        )
        self.v226_sign_group_report: dict[str, Any] = {}

    def _translate_ambiguous_group(self, event: Event) -> str | None:
        result = self.results[event.id]
        if self.retry_budget.remaining <= 0:
            return None
        self.client.sign_group_translation = True
        try:
            self._attempt(
                [Unit(f"sign-semantic-{event.id}", [event])],
                phase="retry_sign_group_translation", parent_call_id=self._last_call_id,
                attempt_type="RETRY", logical_batch_id=f"v226-sign-semantic-{event.id}",
            )
        finally:
            self.client.sign_group_translation = False
        value = result.final_text or ""
        return value if value and not is_effective_source_copy(event.clean_text, value) else None

    def run(self) -> dict[str, Any]:
        summary = super().run()
        result_items = summary["results"]
        # First apply existing translated variants.  Only a genuinely
        # ambiguous eligible group may consume the shared retry budget.
        groups = build_sign_semantic_groups(self.v221_original_events)
        by_id = {item["id"]: item for item in result_items}
        for group in groups:
            states = [_state(group["source_text"], str(by_id[event.id].get("final_text") or "")) for event in group["members"]]
            if any(state == "TRANSLATED" for state in states) or _identity_expected(group["source_text"]):
                continue
            if all(state == "PRESERVED_OR_IDENTITY" for state in states):
                continue
            value = self._translate_ambiguous_group(group["members"][0])
            if value:
                by_id[group["members"][0].id].update(self._result_update(self.results[group["members"][0].id]))
        self.v226_sign_group_report = _apply_groups_to_results(self.v221_original_events, result_items)
        for item in result_items:
            result = self.results[item["id"]]
            result.final_text = item.get("final_text")
            result.flags = list(item.get("flags", []))
        summary["results"] = result_items
        summary.update({
            "sign_group_semantic_count": self.v226_sign_group_report["semantic_group_count"],
            "sign_group_fanout_groups": self.v226_sign_group_report["fanout_group_count"],
            "sign_group_fanout_events": self.v226_sign_group_report["fanout_event_count"],
            "sign_group_ambiguous_groups": len(self.v226_sign_group_report["ambiguous_groups"]),
            "sign_group_mixed_after_groups": len(self.v226_sign_group_report["mixed_after_groups"]),
            "sign_group_structural_failures": len(self.v226_sign_group_report["structural_failures"]),
            "sign_group_coverage_validator": self.v226_sign_group_report["coverage_validator"],
            "sign_group_language_validator": self.v226_sign_group_report["language_validator"],
            "sign_group_report": self.v226_sign_group_report,
        })
        blockers = []
        if self.v226_sign_group_report["ambiguous_groups"] or self.v226_sign_group_report["mixed_after_groups"]:
            blockers.append(SIGN_GROUP_TRANSLATION_AMBIGUOUS)
        if self.v226_sign_group_report["structural_failures"]:
            blockers.append(SIGN_GROUP_STRUCTURAL_FAILURE)
        critical = set(summary.get("critical_flags", [])) | set(blockers)
        summary["critical_flags"] = sorted(critical)
        summary["resolved"] = sum(item.get("status") == "resolved" for item in result_items)
        summary["failed"] = len(result_items) - summary["resolved"]
        summary["eligible"] = summary["failed"] == 0 and not blockers
        summary["eligible_experimental"] = summary["eligible"]
        return summary


def translate_subtitle_file_v2_2_6(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run the frozen V2.2.5 materialization with the V2.2.6 runner."""
    old_runner = frozen_v225.V225MemoryRunner
    old_pipeline = frozen_v225.APPROVED_PIPELINE
    frozen_v225.V225MemoryRunner = V226MemoryRunner
    frozen_v225.APPROVED_PIPELINE = APPROVED_PIPELINE
    try:
        result = frozen_v225.translate_subtitle_file_v2_2_5(*args, **kwargs)
        result["pipeline"] = APPROVED_PIPELINE
        return result
    finally:
        frozen_v225.V225MemoryRunner = old_runner
        frozen_v225.APPROVED_PIPELINE = old_pipeline


def augment_sign_candidate_v2_2_6(source_path: Path, candidate_path: Path, output_path: Path) -> dict[str, Any]:
    """Deterministic sign-only augmentation over an immutable validated candidate."""
    if output_path.exists():
        raise FileExistsError(output_path)
    source_subs, events, _ = load_events(source_path, {}, {"style_hypotheses": {}})
    candidate_subs = pysubs2.load(str(candidate_path))
    report = apply_sign_group_consistency(source_subs, candidate_subs, events)
    changed_indices = {
        events[event_id].original_index
        for event_id in report.get("changed_event_ids", [])
        if 0 <= event_id < len(events)
    }
    segmented_indices = {
        index for index in changed_indices
        if (candidate_subs[index].text or "").count(r"\N")
    }
    validation = validate_structure(source_subs, candidate_subs, changed_indices, segmented_indices)
    report["structural_validation"] = validation
    if not report["coverage_valid"] or not validation.get("valid"):
        raise RuntimeError(json.dumps({"reason": "v2_2_6_sign_group_not_eligible", "report": report}, ensure_ascii=False))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{output_path.name}.v2_2_6-", suffix=output_path.suffix, dir=str(output_path.parent))
    os.close(fd)
    tmp = Path(raw_tmp)
    try:
        candidate_subs.save(str(tmp), encoding="utf-8")
        os.replace(tmp, output_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    report.update({"pipeline": APPROVED_PIPELINE, "model": APPROVED_MODEL, "mode": "SIGN_AUGMENTATION", "source": str(source_path), "candidate_input": str(candidate_path), "output": str(output_path), "ollama_calls": 0})
    return report


translate_subtitle_file = translate_subtitle_file_v2_2_6
