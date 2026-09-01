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

APPROVED_PIPELINE = "v2_3_0"
APPROVED_MODEL = os.environ.get("TRANSLATOR_OLLAMA_MODEL", V226_MODEL)
KARAOKE_TRANSLATION_TIMING_UNSUPPORTED = "KARAOKE_TRANSLATION_TIMING_UNSUPPORTED"
KARAOKE_TRANSLATION_RETRY = "KARAOKE_TRANSLATION"

_TAG_RE = re.compile(r"\{[^}]*\}")
_STYLE_TRANSLATION_HINTS = ("english", " eng", " tl", "translation", "translated")
_STYLE_SONG_HINTS = ("song", " op", " ed", "opening", "ending", "insert")
_ENGLISH_WORDS = {
    "a", "about", "all", "and", "are", "around", "be", "but", "can", "do",
    "for", "from", "have", "how", "i", "in", "is", "it", "my", "of", "on",
    "or", "that", "the", "this", "to", "we", "what", "when", "with", "you",
}


def _glossary_hints(text: str) -> list[dict[str, str]]:
    """Load only matching approved entries as prompt guidance.

    This is deliberately contextual: it never replaces subtitle text after
    the model and an empty/missing glossary is neutral.
    """
    path = Path(os.environ.get("TRANSLATOR_GLOSSARY_PATH", "/app/state/glossary_v1.json"))
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


def classify_song_translation(style: str, text: str) -> str | None:
    """Conservative generic discovery predicate; no release-specific IDs."""
    low = (style or "").casefold()
    clean = visible(text)
    if not clean or _is_drawing(text):
        return "SONG_EFFECT"
    if not any(token in low for token in _STYLE_SONG_HINTS):
        return None
    if "romaji" in low or re.search(r"\b(ro|romanized)\b", low):
        return "SONG_ROMAJI"
    if any(token in low for token in _STYLE_TRANSLATION_HINTS):
        return "SONG_TRANSLATION"
    # A Latin line with multiple common English words is eligible only when
    # the style itself identifies a translation layer.  Otherwise preserve it.
    words = [w.casefold() for w in re.findall(r"[A-Za-zÀ-ÿ]+", clean)]
    if len(set(words) & _ENGLISH_WORDS) >= 2 and "translation" in low:
        return "SONG_TRANSLATION"
    return "SONG_UNKNOWN"


def _has_syllabic_tags(text: str) -> bool:
    return bool(re.search(r"\\(?:k|K|kf|ko)\d+", text or ""))


def _replace_payload(source: str, translated: str) -> str | None:
    """Replace lexical payload while retaining source-owned ASS tags/\\N."""
    source_parts = source.split(r"\N")
    target_parts = translated.split(r"\N")
    if len(source_parts) != len(target_parts):
        # A common ASS karaoke envelope has a tag-only leading segment before
        # the first visible segment (``{tags}\\Ntext``).  A model correctly
        # returns one linguistic segment in that case.  Map it to the sole
        # lexical source segment and preserve all empty/tag-only boundaries.
        lexical_source_indices = [i for i, part in enumerate(source_parts)
                                  if _TAG_RE.sub("", part).strip()]
        if len(lexical_source_indices) == 1 and len(target_parts) == 1:
            target_parts = [source_parts[i] for i in range(len(source_parts))]
            target_parts[lexical_source_indices[0]] = translated
        else:
            return None
    out = []
    for src, tgt in zip(source_parts, target_parts):
        # Keep the delimiters as tokens so their exact order/content survives.
        tokens = re.split(r"(\{[^}]*\})", src)
        lexical_indices = [i for i, token in enumerate(tokens)
                           if token and not _TAG_RE.fullmatch(token)]
        if not lexical_indices:
            out.append(src)
            continue
        target_words = re.findall(r"\S+", tgt.strip())
        source_word_counts = [len(re.findall(r"[\wÀ-ÿ]+", tokens[i], re.UNICODE))
                              for i in lexical_indices]
        total = max(1, sum(source_word_counts))
        replacements: dict[int, str] = {}
        start = 0
        for pos, index in enumerate(lexical_indices):
            if pos == len(lexical_indices) - 1:
                end = len(target_words)
            else:
                end = min(len(target_words), max(start + 1,
                    round(len(target_words) * source_word_counts[pos] / total)))
            replacements[index] = " ".join(target_words[start:end])
            start = end
        out.append("".join(replacements.get(i, token) for i, token in enumerate(tokens)))
    result = r"\N".join(out)
    # Structural tags are validated against the source after reinjection.
    return result


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
    failures: list[dict[str, Any]] = []
    for (style, canonical), indices in sorted(discovered["units"].items()):
        if any(index in discovered["unsupported"] for index in indices):
            failures.append({"style": style, "source": canonical, "event_indices": indices,
                             "reason": KARAOKE_TRANSLATION_TIMING_UNSUPPORTED})
            continue
        if translator:
            value = translator(canonical, "", "")
        else:
            value = _ollama_translate(canonical, model=model, url=ollama_url)
        calls += 0 if translator else 1
        if visible(value).casefold() == canonical.casefold():
            failures.append({"style": style, "source": canonical, "event_indices": indices,
                             "reason": "KARAOKE_TRANSLATION_SOURCE_COPY"})
            continue
        translated_units += 1
        for index in indices:
            restored = _replace_payload(subs[index].text, value)
            if restored is None:
                failures.append({"style": style, "source": canonical, "event_indices": [index],
                                 "reason": "STRUCTURAL_SEGMENT_COUNT_MISMATCH"})
                continue
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
            "ollama_calls": calls, "unsupported": len(discovered["unsupported"]),
            "failures": failures, "structural_failures": structural_failures,
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}


translate_subtitle_file = augment_karaoke_candidate_v2_3_0
