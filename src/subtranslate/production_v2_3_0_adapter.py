"""V2.3.0 karaoke translation layer composed over the frozen V2.2.6 output.

Only events classified as SONG_TRANSLATION are translated.  The ASS envelope
belongs to V2.2.6: timing, layer, style, tags, drawings, line breaks and
animation are copied from the input event and never supplied as model output.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import pysubs2
import requests

from ass_structure import (
    break_count,
    break_tokens,
    hard_space_count,
    is_drawing_event,
    replace_source_payload,
    visible_text,
)
from ollama_runtime import ollama_keep_alive
from production_v2_2_6_adapter import APPROVED_MODEL as V226_MODEL
from runtime_config import default_glossary_path

APPROVED_PIPELINE = "v2_3_0"
APPROVED_MODEL = os.environ.get("TRANSLATOR_OLLAMA_MODEL", V226_MODEL)
KARAOKE_TRANSLATION_TIMING_UNSUPPORTED = "KARAOKE_TRANSLATION_TIMING_UNSUPPORTED"
KARAOKE_TRANSLATION_RETRY = "KARAOKE_TRANSLATION"
KARAOKE_DEFAULT_RETRY_LIMIT = 1

_TAG_RE = re.compile(r"\{[^}]*\}")
_STYLE_TRANSLATION_HINTS = ("english", " eng", " tl", "translation", "translated")
_STYLE_SONG_RE = re.compile(r"(?<![a-z])(?:song|op|ed|opening|ending|insert)(?![a-z])", re.IGNORECASE)
_NON_LYRIC_EVENT_METADATA_RE = re.compile(
    r"(?<![a-z])(?:cast|staff|credits?|narrator|title|comment)(?![a-z])",
    re.IGNORECASE,
)
_CREDIT_LABEL_RE = re.compile(
    r"^(?:translation|translator|timing(?:/edits?)?|editing|typesetting|encoding|subtitles?|fansub)\s*:",
    re.IGNORECASE,
)
_ENGLISH_WORDS = {
    "a", "about", "all", "and", "are", "around", "be", "but", "can", "do",
    "for", "from", "have", "how", "i", "in", "is", "it", "my", "of", "on",
    "or", "that", "the", "this", "to", "we", "what", "when", "with", "you",
    "don't", "i'm", "it's", "that's", "they're", "we're", "what's", "you're",
}
_ENGLISH_CONTENT_WORDS = {
    "above", "after", "again", "answers", "around", "beyond", "burden",
    "called", "carrying", "child", "cloud", "clouds", "day", "endlessly",
    "everyone", "fate", "fair", "going", "headed", "happen", "hey", "know",
    "leads", "lily", "light", "meetings", "moonlight", "now", "partings",
    "passing", "pure", "reach", "repeating", "sacred", "say", "serene",
    "shadow", "shine", "signpost", "sin", "somebody", "start", "star",
    "starlight", "tell", "time", "tomorrow", "walking", "wanders", "way",
    "wherever", "yeah", "benign", "how",
}
_ENGLISH_FUNCTION_CONTRACTIONS = {
    "don't", "i'm", "it's", "that's", "they're", "we're", "what's", "you're",
}
_ENGLISH_SINGLETONS = {"hey", "yeah", "oh", "moonlight", "signpost", "tomorrow"}
_PT_HOMOGRAPHS = {"a", "do", "for"}
_ENGLISH_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ]+(?:['’][A-Za-zÀ-ÿ]+)?", re.UNICODE)


def _glossary_hints(text: str) -> list[dict[str, str]]:
    """Load only matching approved entries as prompt guidance.

    This is deliberately contextual: it never replaces subtitle text after
    the model and an empty/missing glossary is neutral.
    """
    path = default_glossary_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries", data) if isinstance(data, dict) else data
    except (OSError, ValueError, TypeError):
        return []
    folded = " ".join((text or "").casefold().split())
    matches = []
    for entry in entries if isinstance(entries, list) else []:
        source = " ".join(str(entry.get("source_expression", "")).casefold().split())
        if entry.get("status") == "APPROVED" and source and source in folded:
            matches.append({"source_expression": source, "preferred_pt_br": str(entry.get("preferred_pt_br", "")), "notes": str(entry.get("avoid_notes", ""))})
    return matches[:8]


def visible(text: str) -> str:
    return " ".join(visible_text(text or "", line_break=" ").split())


def _is_drawing(text: str) -> bool:
    return is_drawing_event(text)


def _looks_like_english_song_text(text: str) -> bool:
    """Recognize short English lyric content without release-specific IDs."""
    words = [word.casefold().replace("’", "'") for word in _ENGLISH_TOKEN_RE.findall(text or "")]
    if not words:
        return False
    function_hits = sum(word in _ENGLISH_WORDS or word in _ENGLISH_FUNCTION_CONTRACTIONS for word in words)
    content_hits = sum(word in _ENGLISH_CONTENT_WORDS for word in words)
    unambiguous_function_hits = sum(
        (word in _ENGLISH_WORDS and word not in _PT_HOMOGRAPHS) or word in _ENGLISH_FUNCTION_CONTRACTIONS
        for word in words
    )
    if len(words) == 1:
        return words[0] in _ENGLISH_SINGLETONS
    return content_hits >= 2 or (content_hits >= 1 and function_hits >= 1) or (
        len(words) >= 4 and function_hits >= 2 and unambiguous_function_hits >= 1 and function_hits / len(words) >= 0.30
    )


def classify_song_translation(style: str, text: str) -> str | None:
    """Conservative generic discovery predicate; no release-specific IDs."""
    low = (style or "").casefold()
    clean = visible(text)
    if not clean or _is_drawing(text):
        return "SONG_EFFECT"
    if not _STYLE_SONG_RE.search(low):
        return None
    if "romaji" in low or re.search(r"\b(ro|romanized)\b", low):
        return "SONG_ROMAJI"
    if any(token in low for token in _STYLE_TRANSLATION_HINTS):
        return "SONG_TRANSLATION"
    # OP/ED bottom styles are often named only by position (for example
    # ``OP Bottom``), while their payload is already English.  The conservative
    # lexical check makes those lines eligible without converting romaji.
    if _looks_like_english_song_text(clean):
        return "SONG_TRANSLATION"
    return "SONG_UNKNOWN"


def classify_song_event(line: pysubs2.SSAEvent) -> str | None:
    """Classify one ASS event while rejecting non-lyric style reuse.

    Fansub scripts commonly reuse a karaoke style for cast, staff, episode
    titles and commented timing notes.  Style alone therefore establishes a
    candidate region, while ASS event metadata decides whether the event is
    actually a lyric.
    """
    classification = classify_song_translation(line.style, line.text)
    if classification != "SONG_TRANSLATION":
        return classification
    if line.is_comment:
        return "SONG_NON_LYRIC"
    metadata = f"{line.name or ''} {line.effect or ''}"
    if _NON_LYRIC_EVENT_METADATA_RE.search(metadata):
        return "SONG_NON_LYRIC"
    clean = visible(line.text)
    if _CREDIT_LABEL_RE.match(clean):
        return "SONG_NON_LYRIC"
    # Multiple role labels separated by ASS line breaks are cast credits, not
    # a lyric merely because their shared style contains "Translation".
    role_labels = re.findall(r"(?:^|\\N+)[^\\N:]{1,48}:", _TAG_RE.sub("", line.text or ""))
    if len(role_labels) >= 2:
        return "SONG_NON_LYRIC"
    return classification


def _has_syllabic_tags(text: str) -> bool:
    return bool(re.search(r"\\(?:kt|kf|ko|k)\d+", text or "", re.IGNORECASE))


def _replace_payload(source: str, translated: str) -> str | None:
    """Replace payload through the single source-owned ASS envelope helper.

    The model is not authoritative for line breaks, inline tags, hard spaces
    or per-character styling.  Empty output remains a fail-closed result for
    the karaoke caller; every non-empty output is rebuilt from the source
    envelope by :func:`ass_structure.replace_source_payload`.
    """
    if not visible_text(str(translated or ""), line_break=" ").strip():
        return None
    return replace_source_payload(source or "", str(translated or ""))


def _event_fields(line: pysubs2.SSAEvent) -> dict[str, Any]:
    return {"start": line.start, "end": line.end, "layer": line.layer,
            "style": line.style, "name": line.name, "text": line.text}


def _structural_signature(line: pysubs2.SSAEvent) -> tuple[Any, ...]:
    tags = _TAG_RE.findall(line.text or "")
    return (line.start, line.end, line.layer, line.style, line.name,
            break_count(line.text), tuple(break_tokens(line.text)), hard_space_count(line.text), tags,
            is_drawing_event(line.text))


def discover_song_units(subs: pysubs2.SSAFile) -> dict[str, Any]:
    units: dict[tuple[str, str], list[int]] = defaultdict(list)
    classifications: dict[int, str] = {}
    unsupported: list[int] = []
    for idx, line in enumerate(subs):
        cls = classify_song_event(line)
        if not cls:
            continue
        classifications[idx] = cls
        if cls == "SONG_TRANSLATION" and _has_syllabic_tags(line.text):
            unsupported.append(idx)
        if cls == "SONG_TRANSLATION":
            units[(line.style, visible(line.text))].append(idx)
    return {"units": units, "classifications": classifications, "unsupported": unsupported}


def _normalized_visible(text: str) -> str:
    return " ".join(visible(text).casefold().split())


def _still_requires_song_translation(source_text: str, candidate_text: str) -> bool:
    """Return whether a base-stage candidate still contains English source.

    V2.3.8 already translates English music payloads during its full stage.
    V2.3.0 uses the original event to discover the semantic song role, then
    this content check prevents a second PT-BR -> PT-BR model call.  Exact
    source copies are always pending; changed text is pending only when it
    still looks English.
    """
    source_key = _normalized_visible(source_text)
    candidate_key = _normalized_visible(candidate_text)
    if not candidate_key:
        return True
    return candidate_key == source_key or _looks_like_english_song_text(visible(candidate_text))


def _song_context(
    subs: pysubs2.SSAFile,
    classifications: dict[int, str],
    indices: list[int],
) -> tuple[str, str]:
    """Return the closest source lyric lines around one canonical unit."""
    first, last = min(indices), max(indices)
    song_indices = sorted(
        index for index, classification in classifications.items()
        if classification == "SONG_TRANSLATION" and index not in indices
    )
    previous = [index for index in song_indices if index < first][-2:]
    following = [index for index in song_indices if index > last][:2]
    return (
        " / ".join(visible(subs[index].text) for index in previous),
        " / ".join(visible(subs[index].text) for index in following),
    )


def _ollama_translate(text: str, *, context_before: str = "", context_after: str = "", url: str | None = None, model: str | None = None) -> str:
    endpoint = url or os.environ.get("TRANSLATOR_OLLAMA_URL", "http://ollama:11434/api/chat")
    chosen_model = model or APPROVED_MODEL
    hints = _glossary_hints(text)
    prompt = (
        "Translate only this English song translation line to natural PT-BR. "
        "Return JSON exactly as {\\\"translation\\\":\\\"...\\\"}. "
        "Do not return ASS tags, timing, style, layer, \u005c\u005cN, explanations or the English source. "
        f"\nContext before: {context_before}\nTarget: {text}\nContext after: {context_after}"
        f"\nContextual glossary hints (guidance only, no blind replacement): {json.dumps(hints, ensure_ascii=False)}"
    )
    response = requests.post(endpoint, json={"model": chosen_model, "stream": False,
        "think": False, "keep_alive": ollama_keep_alive(),
        "options": {"temperature": 0, "num_ctx": 2560, "num_predict": 384}, "format": "json",
        "messages": [{"role": "user", "content": prompt}]}, timeout=240)
    response.raise_for_status()
    payload = response.json()
    content = payload.get("message", {}).get("content", "")
    data = json.loads(content)
    value = str(data.get("translation", "")).strip()
    if not value or value.casefold() == text.casefold():
        raise RuntimeError("KARAOKE_TRANSLATION_SOURCE_COPY")
    return value


def augment_karaoke_candidate_v2_3_0(input_path: Path, output_path: Path,
                                     translator: Callable[[str, str, str], str] | None = None,
                                     *, model: str | None = None, ollama_url: str | None = None,
                                     original_source_path: Path | None = None) -> dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(output_path)
    subs = pysubs2.load(str(input_path))
    original_signatures = [_structural_signature(line) for line in subs]
    discovery_subs = subs
    if original_source_path is not None:
        discovery_subs = pysubs2.load(str(original_source_path))
        if len(discovery_subs) != len(subs):
            raise RuntimeError(json.dumps({
                "reason": "V230_ORIGINAL_CANDIDATE_CARDINALITY_MISMATCH",
                "original_events": len(discovery_subs),
                "candidate_events": len(subs),
            }, sort_keys=True))
    discovered = discover_song_units(discovery_subs)
    translated_units = 0
    translated_events = 0
    already_translated_units = 0
    already_translated_events = 0
    calls = 0
    provider_calls = 0
    failures: list[dict[str, Any]] = []
    pending_unsupported: set[int] = set()
    retry_limit = max(
        KARAOKE_DEFAULT_RETRY_LIMIT,
        int(getattr(translator, "karaoke_retry_limit", KARAOKE_DEFAULT_RETRY_LIMIT) or KARAOKE_DEFAULT_RETRY_LIMIT),
    ) if translator else 0
    for (style, source_canonical), indices in sorted(discovered["units"].items()):
        pending_indices = list(indices)
        completed_indices: list[int] = []
        if original_source_path is not None:
            pending_indices = []
            for index in indices:
                if _still_requires_song_translation(
                    discovery_subs[index].text,
                    subs[index].text,
                ):
                    pending_indices.append(index)
                else:
                    completed_indices.append(index)
            already_translated_events += len(completed_indices)
            if not pending_indices:
                already_translated_units += 1
                translated_units += 1
                translated_events += len(indices)
                continue

        if any(index in discovered["unsupported"] for index in pending_indices):
            pending_unsupported.update(
                index for index in pending_indices
                if index in discovered["unsupported"]
            )
            failures.append({"style": style, "source": source_canonical, "event_indices": pending_indices,
                             "reason": KARAOKE_TRANSLATION_TIMING_UNSUPPORTED})
            continue

        pending_values = {visible(subs[index].text) for index in pending_indices}
        canonical = source_canonical if original_source_path is not None else (
            next(iter(pending_values)) if len(pending_values) == 1 else source_canonical
        )
        context_before, context_after = _song_context(
            discovery_subs,
            discovered["classifications"],
            indices,
        )
        retry_translator = getattr(translator, "retry", None) if translator else None
        if translator:
            value = translator(canonical, context_before, context_after)
            provider_calls += 1
        else:
            value = _ollama_translate(
                canonical,
                context_before=context_before,
                context_after=context_after,
                model=model,
                url=ollama_url,
            )
            calls += 1
        retry_count = 0
        rendered_by_index: dict[int, str | None] = {}
        invalid_indices: list[int] = []
        while True:
            source_copy = visible(value).casefold() == canonical.casefold()
            if source_copy:
                if callable(retry_translator) and retry_count < retry_limit:
                    value = retry_translator(canonical, context_before, context_after)
                    provider_calls += 1
                    retry_count += 1
                    continue
                failures.append({"style": style, "source": canonical, "event_indices": pending_indices,
                                 "reason": "KARAOKE_TRANSLATION_SOURCE_COPY",
                                 "attempts": retry_count + 1})
                break

            rendered_by_index = {
                index: _replace_payload(subs[index].text, value)
                for index in pending_indices
            }
            invalid_indices = [index for index, restored in rendered_by_index.items() if restored is None]
            if invalid_indices:
                if callable(retry_translator) and retry_count < retry_limit:
                    value = retry_translator(canonical, context_before, context_after)
                    provider_calls += 1
                    retry_count += 1
                    continue
                failures.extend({"style": style, "source": canonical, "event_indices": [index],
                                 "reason": "STRUCTURAL_SEGMENT_COUNT_MISMATCH",
                                 "attempts": retry_count + 1}
                                for index in invalid_indices)
            break
        if source_copy or invalid_indices:
            continue
        translated_units += 1
        translated_events += len(completed_indices)
        for index, restored in rendered_by_index.items():
            subs[index].text = restored
            translated_events += 1
    final_signatures = [_structural_signature(line) for line in subs]
    structural_failures = [i for i, (a, b) in enumerate(zip(original_signatures, final_signatures)) if a != b]
    if structural_failures:
        raise RuntimeError(json.dumps({"reason": "V230_STRUCTURAL_PARITY_FAILURE", "event_indices": structural_failures[:50]}))
    if output_path.exists():
        raise FileExistsError(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(output_path), encoding="utf-8")
    return {"pipeline": APPROVED_PIPELINE, "model": model or APPROVED_MODEL,
            "mode": "KARAOKE_AUGMENTATION", "song_units": len(discovered["units"]),
            "translated_units": translated_units, "translated_events": translated_events,
            "already_translated_units": already_translated_units,
            "already_translated_events": already_translated_events,
            "ollama_calls": calls, "provider_calls": provider_calls,
            "unsupported": len(pending_unsupported),
            "failures": failures, "structural_failures": structural_failures,
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}


translate_subtitle_file = augment_karaoke_candidate_v2_3_0
