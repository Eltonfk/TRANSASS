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

from ollama_runtime import ollama_keep_alive
from production_v2_2_6_adapter import APPROVED_MODEL as V226_MODEL
from runtime_config import default_glossary_path

APPROVED_PIPELINE = "v2_3_0"
APPROVED_MODEL = os.environ.get("TRANSLATOR_OLLAMA_MODEL", V226_MODEL)
KARAOKE_TRANSLATION_TIMING_UNSUPPORTED = "KARAOKE_TRANSLATION_TIMING_UNSUPPORTED"
KARAOKE_TRANSLATION_RETRY = "KARAOKE_TRANSLATION"

_TAG_RE = re.compile(r"\{[^}]*\}")
_STYLE_TRANSLATION_HINTS = ("english", " eng", " tl", "translation", "translated")
_STYLE_SONG_RE = re.compile(r"(?<![a-z])(?:song|op|ed|opening|ending|insert)(?![a-z])", re.IGNORECASE)
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
    return " ".join(_TAG_RE.sub("", text or "").replace(r"\N", " ").replace(r"\n", " ").split())


def _is_drawing(text: str) -> bool:
    return bool(re.search(r"\\p[1-9]", text or "")) and not re.search(r"[A-Za-zÀ-ÿ]{2,}", visible(text))


def _looks_like_english_song_text(text: str) -> bool:
    """Recognize short English lyric content without release-specific IDs."""
    words = [word.casefold().replace("’", "'") for word in _ENGLISH_TOKEN_RE.findall(text or "")]
    if not words:
        return False
    function_hits = sum(word in _ENGLISH_WORDS or word in _ENGLISH_FUNCTION_CONTRACTIONS for word in words)
    content_hits = sum(word in _ENGLISH_CONTENT_WORDS for word in words)
    if len(words) == 1:
        return words[0] in _ENGLISH_SINGLETONS
    return content_hits >= 2 or (content_hits >= 1 and function_hits >= 1) or (
        len(words) >= 4 and function_hits >= 2 and function_hits / len(words) >= 0.30
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


def _has_syllabic_tags(text: str) -> bool:
    return bool(re.search(r"\\(?:k|K|kf|ko)\d+", text or ""))


def _allocate_words(words: list[str], weights: list[int]) -> list[str]:
    """Distribute a model line over source-owned lexical segments."""
    if not weights:
        return []
    if len(words) < len(weights):
        # Never lose translated content merely because the target is shorter
        # than the number of source lines. Keep the sentence order and leave
        # only the trailing source segments empty.
        return [word if index < len(words) else "" for index, word in enumerate(weights)]
    total = max(1, sum(weights))
    chunks: list[str] = []
    start = 0
    cumulative = 0
    for position, weight in enumerate(weights):
        cumulative += max(1, weight)
        if position == len(weights) - 1:
            end = len(words)
        else:
            remaining_segments = len(weights) - position - 1
            ideal = round(len(words) * cumulative / total)
            end = min(
                len(words) - remaining_segments,
                max(start + 1, ideal),
            )
        chunks.append(" ".join(words[start:end]))
        start = end
    return chunks


def _replace_lexical_payload(source: str, translated: str) -> str | None:
    """Replace one source segment while retaining its exact ASS tags."""
    # ASS tags belong to the source envelope. If a model emits them anyway,
    # discard only those model-owned tags before reinjection.
    target_words = re.findall(r"\S+", _TAG_RE.sub("", translated or "").strip())
    tokens = re.split(r"(\{[^}]*\})", source)
    lexical_indices = [
        index for index, token in enumerate(tokens)
        if token and not _TAG_RE.fullmatch(token)
    ]
    if not lexical_indices:
        return source
    source_word_counts = [
        max(1, len(re.findall(r"[\wÀ-ÿ]+", tokens[index], re.UNICODE)))
        for index in lexical_indices
    ]
    replacements = dict(zip(lexical_indices, _allocate_words(target_words, source_word_counts)))
    return "".join(replacements.get(index, token) for index, token in enumerate(tokens))


def _replace_payload(source: str, translated: str) -> str | None:
    """Replace lexical payload while retaining the source ASS envelope.

    The model is not authoritative for line breaks. It may return one line,
    more lines, or accidentally include ASS tags; all such presentation is
    normalized back onto the source's exact envelope.
    """
    source_parts = source.split(r"\N")
    source_lexical_indices = [
        index for index, part in enumerate(source_parts)
        if _TAG_RE.sub("", part).strip()
    ]
    if not source_lexical_indices:
        return source
    target_parts = [
        _TAG_RE.sub("", part).strip()
        for part in re.split(r"(?:\\N|\r?\n)", str(translated or ""))
    ]
    if not any(target_parts):
        return None

    if len(target_parts) == len(source_parts):
        target_by_source = {
            index: target_parts[index] for index in source_lexical_indices
        }
    elif len(target_parts) == len(source_lexical_indices):
        # Common case: source has tag-only prefix/suffix segments around the
        # visible text and the model returns only the linguistic segments.
        target_by_source = dict(zip(source_lexical_indices, target_parts))
    else:
        target_words = re.findall(r"\S+", " ".join(target_parts))
        source_word_counts = [
            max(1, len(re.findall(r"[\wÀ-ÿ]+", source_parts[index], re.UNICODE)))
            for index in source_lexical_indices
        ]
        target_by_source = dict(zip(
            source_lexical_indices,
            _allocate_words(target_words, source_word_counts),
        ))

    out = []
    for index, source_part in enumerate(source_parts):
        if index not in target_by_source:
            out.append(source_part)
            continue
        rendered = _replace_lexical_payload(source_part, target_by_source[index])
        if rendered is None:
            return None
        out.append(rendered)
    # The source owns the delimiters; the final result always has identical
    # line-break count and exact source tag sequence.
    return r"\N".join(out)


def _event_fields(line: pysubs2.SSAEvent) -> dict[str, Any]:
    return {"start": line.start, "end": line.end, "layer": line.layer,
            "style": line.style, "name": line.name, "text": line.text}


def _structural_signature(line: pysubs2.SSAEvent) -> tuple[Any, ...]:
    tags = _TAG_RE.findall(line.text or "")
    return (line.start, line.end, line.layer, line.style, line.name,
            line.text.count(r"\N"), tags,
            bool(re.search(r"\\p[1-9]", line.text or "")))


def discover_song_units(subs: pysubs2.SSAFile) -> dict[str, Any]:
    units: dict[tuple[str, str], list[int]] = defaultdict(list)
    classifications: dict[int, str] = {}
    unsupported: list[int] = []
    for idx, line in enumerate(subs):
        cls = classify_song_translation(line.style, line.text)
        if not cls:
            continue
        classifications[idx] = cls
        if cls == "SONG_TRANSLATION" and _has_syllabic_tags(line.text):
            unsupported.append(idx)
        if cls == "SONG_TRANSLATION":
            units[(line.style, visible(line.text))].append(idx)
    return {"units": units, "classifications": classifications, "unsupported": unsupported}


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
                                     *, model: str | None = None, ollama_url: str | None = None) -> dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(output_path)
    subs = pysubs2.load(str(input_path))
    original_signatures = [_structural_signature(line) for line in subs]
    discovered = discover_song_units(subs)
    translated_units = 0
    translated_events = 0
    calls = 0
    provider_calls = 0
    failures: list[dict[str, Any]] = []
    for (style, canonical), indices in sorted(discovered["units"].items()):
        if any(index in discovered["unsupported"] for index in indices):
            failures.append({"style": style, "source": canonical, "event_indices": indices,
                             "reason": KARAOKE_TRANSLATION_TIMING_UNSUPPORTED})
            continue
        retry_translator = getattr(translator, "retry", None) if translator else None
        if translator:
            value = translator(canonical, "", "")
            provider_calls += 1
        else:
            value = _ollama_translate(canonical, model=model, url=ollama_url)
            calls += 1

        source_copy = visible(value).casefold() == canonical.casefold()
        if source_copy and callable(retry_translator):
            value = retry_translator(canonical, "", "")
            provider_calls += 1
            source_copy = visible(value).casefold() == canonical.casefold()
        rendered_by_index = {}
        if not source_copy:
            rendered_by_index = {
                index: _replace_payload(subs[index].text, value)
                for index in indices
            }
            if any(rendered is None for rendered in rendered_by_index.values()) and callable(retry_translator):
                value = retry_translator(canonical, "", "")
                provider_calls += 1
                source_copy = visible(value).casefold() == canonical.casefold()
                if not source_copy:
                    rendered_by_index = {
                        index: _replace_payload(subs[index].text, value)
                        for index in indices
                    }
        if source_copy:
            failures.append({"style": style, "source": canonical, "event_indices": indices,
                             "reason": "KARAOKE_TRANSLATION_SOURCE_COPY"})
            continue
        invalid_indices = [index for index, restored in rendered_by_index.items() if restored is None]
        if invalid_indices:
            failures.extend({"style": style, "source": canonical, "event_indices": [index],
                             "reason": "STRUCTURAL_SEGMENT_COUNT_MISMATCH"}
                            for index in invalid_indices)
            continue
        translated_units += 1
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
            "ollama_calls": calls, "provider_calls": provider_calls,
            "unsupported": len(discovered["unsupported"]),
            "failures": failures, "structural_failures": structural_failures,
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}


translate_subtitle_file = augment_karaoke_candidate_v2_3_0
