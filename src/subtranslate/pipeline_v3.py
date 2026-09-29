"""Pipeline V3: Unified in-memory anime subtitle translation architecture.

Consolidates all historical layers into a cohesive, single-pass pipeline:
1. AST Document Loading (ass_engine.py)
2. In-Memory Semantic Translation & Sign-Group Consistency (semantic_orchestrator.py)
3. In-Memory Visual Glyphs & Karaoke Post-Processing (effects_engine.py)
4. Comprehensive Structural Validation (ass_engine.py)
5. Direct Atomic Persistence

Eliminates repetitive intermediate disk checkpoints, reducing I/O latency by ~50%.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from ass_engine import (
    ASSDocumentAST,
    validate_document_structure,
    visible_text,
)
from semantic_orchestrator import (
    InMemorySemanticOrchestrator,
    TranslationUnit,
)
from effects_engine import (
    InMemoryEffectsEngine,
)
from translation_quality import (
    _is_french_de_protected_name_phrase,
    _is_french_de_single_name_shape,
    _is_french_interjection_title_name_call,
    _is_french_name_call_exchange,
    _is_french_protected_name_fragment_identity,
    _is_french_protected_name_only_identity,
    _is_french_repeated_name_call,
    _is_portuguese_target_language,
    _normalize_translated_punctuation_spacing,
    extract_repeated_french_names,
    extract_repeated_names,
    french_ptbr_context_hints,
    is_clean_romaji_token,
    is_french_de_protected_name_phrase,
    is_french_de_single_name_shape,
    is_french_interjection_title_name_call,
    is_french_name_call_exchange,
    is_french_name_stopword,
    is_french_protected_name_fragment_identity,
    is_french_protected_name_only_identity,
    is_french_repeated_name_call,
    is_likely_romanized_japanese_name_token,
    is_portuguese_target_language,
    is_reviewed_romanized_japanese_name_token,
    names_in_source,
    normalize_translated_punctuation_spacing,
    progressive_protected_name_prefixes_in_sequence,
    remove_unowned_terminal_ascii_quote_closures,
    restore_source_enclosing_ascii_quotes,
    source_name_spellings,
    translation_quality_flags,
)

PIPELINE_VERSION = "v3_0_0"
LOGGER = logging.getLogger(__name__)
# Ollama can durably return HTTP 200 with an empty or explicitly unfinished
# assistant body after an internal decode/grammar error. Retry that condition
# once as a new physical call/capture; never replay the same call ID.
V3_MAX_OLLAMA_RESPONSE_RETRIES = 1
V3_OLLAMA_RETRY_DELAY_SECONDS = 1.0
# A repair is at most one extra call per affected batch. A tiny job-global cap
# causes later batches to fail even when each correction succeeds; the provider's
# physical-call budget remains the hard upper bound for the whole translation.
V3_MAX_QUALITY_REPAIR_CALLS = 64
V3_MAX_UNMETERED_QUALITY_REPAIR_CALLS = 32
V3_MAX_REFLOW_WORDS = 256
V3_MAX_REFLOW_LINES = 8


def _publish_no_clobber(staged_path: Path, destination: Path) -> None:
    """Atomically create a destination only if it is still absent."""
    os.link(staged_path, destination)
    staged_path.unlink()


class PipelineV3Error(RuntimeError):
    """Raised when the V3 pipeline encounters a non-recoverable error."""
    pass


class V3ResponseContractError(PipelineV3Error):
    """A provider response violates the V3 translation contract."""


class V3TranslationCoverageError(PipelineV3Error):
    """At least one planned translation unit has no usable result."""


_SOURCE_COPY_SINGLETONS = {
    "english": {
        "hello", "hi", "yes", "no", "please", "thanks", "sorry", "stop",
        "wait", "look", "what", "why", "run", "go", "come", "help", "stay",
        "leave", "jump", "listen", "hurry", "move", "hide", "duck", "careful",
    },
    "french": {
        "bonjour", "salut", "oui", "non", "merci", "cours", "viens", "allez",
        "attends", "regarde", "aide", "aidez", "écoute", "écoutez", "vite",
    },
    "spanish": {"hola", "sí", "si", "no", "gracias"},
}
_FRENCH_TO_PTBR_SHARED_INTERJECTIONS = {"hein"}
_TITLE_CONNECTORS = {
    "a", "an", "and", "as", "at", "but", "by", "de", "del", "des", "do",
    "da", "das", "dos", "e", "en", "for", "in", "la", "le", "les", "of",
    "on", "or", "the", "to", "um", "uma", "un", "une", "with",
}
_ENGLISH_STRONG_RESIDUE_WORDS = {
    "because", "cannot", "could", "couldn't", "doesn't", "don't", "hadn't",
    "hasn't", "haven't", "isn't", "might", "must", "should", "shouldn't",
    "wasn't", "weren't", "when", "where", "which", "who", "why", "won't",
    "would", "wouldn't", "what", "without",
}
_ENGLISH_SUPPORT_RESIDUE_WORDS = {
    "and", "are", "but", "from", "have", "here", "into", "their", "there",
    "they", "this", "those", "through", "too", "with", "you", "your",
}


def _canonical_source_language_v3(source_language: str | None) -> str:
    value = str(source_language or "").strip().casefold().replace("_", "-")
    if value in {"fr", "fra", "fre", "french", "francais", "français", "francês"}:
        return "french"
    if value in {"en", "eng", "english", "inglês", "ingles"}:
        return "english"
    if value in {"pt", "pt-br", "português", "portugues", "português do brasil", "portugues do brasil"}:
        return "portuguese"
    return "other"


def _visible_line_break_count(text: str) -> int:
    """Count visible source/target line breaks after stripping ASS overrides."""
    normalized = visible_text(str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    return normalized.count("\n")


def _adjacent_ellipsis_source_context(
    batch: Any,
    unit: TranslationUnit,
) -> tuple[tuple[tuple[str, ...], int], ...]:
    """Return validated three-event windows containing this unit.

    Each window is limited to this model batch, consecutive ASS event indices,
    one style, and non-overlapping timing gaps of at most 2.5 seconds. The
    returned index identifies this unit inside each ordered three-event window.
    """
    if not isinstance(unit.id, int) or isinstance(unit.id, bool) or unit.is_sign:
        return ()
    units_by_index = {
        candidate.id: candidate
        for candidate in batch.units
        if isinstance(candidate.id, int)
        and not isinstance(candidate.id, bool)
        and not candidate.is_sign
    }
    current_node = unit.metadata.get("node")
    if current_node is None or getattr(current_node, "index", None) != unit.id:
        return ()

    windows: list[tuple[tuple[str, ...], int]] = []
    for first_index in (unit.id - 2, unit.id - 1, unit.id):
        event_indices = (first_index, first_index + 1, first_index + 2)
        window_units = [units_by_index.get(index) for index in event_indices]
        if any(candidate is None for candidate in window_units):
            continue
        concrete_units = [candidate for candidate in window_units if candidate is not None]
        nodes = [candidate.metadata.get("node") for candidate in concrete_units]
        if any(node is None for node in nodes):
            continue
        concrete_nodes = [node for node in nodes if node is not None]
        if any(
            getattr(node, "index", None) != event_index
            for node, event_index in zip(concrete_nodes, event_indices)
        ):
            continue
        first_style = str(getattr(concrete_nodes[0], "style", "")).casefold()
        if any(
            str(getattr(node, "style", "")).casefold() != first_style
            for node in concrete_nodes[1:]
        ):
            continue
        gaps = [
            int(getattr(concrete_nodes[index + 1], "start", 0))
            - int(getattr(concrete_nodes[index], "end", 0))
            for index in range(2)
        ]
        if any(not 0 <= gap_ms <= 2500 for gap_ms in gaps):
            continue
        windows.append((
            tuple(candidate.source_text for candidate in concrete_units),
            unit.id - first_index,
        ))
    return tuple(windows)


def _reflow_plain_translation(text: str, line_break_count: int) -> str | None:
    """Reflow plain translated words to an exact line count without model calls."""
    from ass_engine import line_break_inside_word

    value = str(text or "")
    if re.search(r"\{[^{}]*\}|\\[Nn]", value):
        # Model output must remain plain text; never strip or rewrite ASS syntax.
        return None
    if (
        any(marker in value for marker in "‒–—―")
        or re.search(r"(?<!\w)[-‐‑−]", value)
        or re.search(r"(?<=\w)[-‐‑−](?=[A-ZÀ-ÖØ-Þ])", value)
    ):
        # Typographic dashes can mark speaker turns even when attached to the
        # prior word. Hyphen-like characters are guarded at non-word boundaries
        # while lexical compounds remain eligible for deterministic reflow.
        return None
    words = value.replace("\r\n", "\n").replace("\r", "\n").split()
    line_count = max(0, int(line_break_count)) + 1
    word_count = len(words)
    if (
        word_count < line_count
        or word_count > V3_MAX_REFLOW_WORDS
        or line_count > V3_MAX_REFLOW_LINES
    ):
        return None

    ass_break = "\\N"
    safe_boundaries = {
        index
        for index in range(1, word_count)
        if not line_break_inside_word(
            words[index - 1] + ass_break + words[index],
        )
    }
    prefix_lengths = [0]
    for word in words:
        prefix_lengths.append(prefix_lengths[-1] + len(word))
    target_chars = (prefix_lengths[-1] + word_count - 1) / line_count

    @lru_cache(maxsize=None)
    def best_split(start: int, lines_left: int) -> tuple[float, tuple[int, ...]] | None:
        if lines_left == 1:
            length = prefix_lengths[word_count] - prefix_lengths[start] + word_count - start - 1
            return (length - target_chars) ** 2, ()

        best: tuple[float, tuple[int, ...]] | None = None
        final_boundary = word_count - lines_left + 1
        for end in range(start + 1, final_boundary + 1):
            if end not in safe_boundaries:
                continue
            remaining = best_split(end, lines_left - 1)
            if remaining is None:
                continue
            length = prefix_lengths[end] - prefix_lengths[start] + end - start - 1
            candidate = ((length - target_chars) ** 2 + remaining[0], (end, *remaining[1]))
            if best is None or candidate[0] < best[0]:
                best = candidate
        return best

    split = best_split(0, line_count)
    if split is None:
        return None
    boundaries = (0, *split[1], word_count)
    lines = [
        " ".join(words[start:end])
        for start, end in zip(boundaries, boundaries[1:])
    ]
    result = "\n".join(lines)
    return (
        result
        if _visible_line_break_count(result) == line_break_count
        and not line_break_inside_word(result.replace("\n", ass_break))
        else None
    )


def _is_untranslated_source_copy(
    source: str,
    translated: str,
    source_language: str,
    *,
    protected_names: Iterable[str] = (),
    contextually_protected_names: Iterable[str] = (),
    target_language: str = "português do Brasil (pt-BR)",
) -> bool:
    """Reject high-confidence verbatim source copies without blocking names."""
    words = [
        word for word in re.findall(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", source, flags=re.UNICODE)
        if any(c.isalpha() for c in word)
    ]
    output_words = [
        word for word in re.findall(r"[\wÀ-ÿ]+(?:['’][\wÀ-ÿ]+)?", translated, flags=re.UNICODE)
        if any(c.isalpha() for c in word)
    ]
    normalized_source = [word.casefold().replace("’", "'") for word in words]
    normalized_output = [word.casefold().replace("’", "'") for word in output_words]
    if not normalized_source or normalized_source != normalized_output:
        return False
    from pipeline_v2_1_3 import ENGLISH_COMMON, FRENCH_INDICATORS

    language = _canonical_source_language_v3(source_language)
    terminal_punctuation = bool(re.search(r"[.!?…][\"'’)]*\s*$", source))
    if language == "portuguese":
        # Source and target are both Portuguese variants; identity can be a
        # valid localization result and requires the normal audit instead.
        return False
    _FRENCH_PTBR_SHARED_SINGLETON_WORDS = frozenset({
        "mal", "indulgente", "indulgentes", "grave", "graves",
    })
    if (
        language == "french"
        and _is_portuguese_target_language(target_language)
        and len(normalized_source) == 1
        and normalized_source[0] in _FRENCH_PTBR_SHARED_SINGLETON_WORDS
        and _normalize_translated_punctuation_spacing(
            source, source_language, target_language,
        ).strip() in (
            str(translated or "").strip(),
            _normalize_translated_punctuation_spacing(
                str(translated or ""), source_language, target_language,
            ).strip(),
        )
    ):
        # In this one-word response, French and pt-BR share the same natural
        # word; allow only exact identity in the explicit target.
        return False
    if language == "french":
        if _is_french_repeated_name_call(source, translated, target_language):
            # Repeated name-only calls may be identical across French and
            # pt-BR. No sentence or other French lexical material is exempted.
            return False
        if _is_french_de_protected_name_phrase(
            source, translated, protected_names, target_language,
        ):
            # "De Seishin ?" is valid in both languages. The exception is
            # limited to one protected, Japanese-looking name after "de";
            # ordinary French questions and longer copied clauses still fail.
            return False
        if _is_french_name_call_exchange(
            source, translated, protected_names, target_language,
        ):
            # A speaker exchange such as "- Shinmei! / - Tokujirô..." contains
            # only proper-name calls. At least one name must already be known
            # to the protected-name inventory; every line must be a single
            # Hepburn-like name, not merely a capitalized French call word.
            return False
        if _is_french_protected_name_only_identity(
            source, translated, protected_names, target_language,
        ):
            return False
        if _is_french_protected_name_fragment_identity(
            source, translated, contextually_protected_names, target_language,
        ):
            return False
        if _is_french_interjection_title_name_call(
            source, translated, protected_names, target_language,
        ):
            return False
        protected_tokens = {
            token.casefold()
            for name in protected_names
            for token in re.findall(r"[\wÀ-ÿ]+", str(name), re.UNICODE)
        }
        protected_hits = set(normalized_source) & protected_tokens
        name_connectors = {"de", "del", "da", "do", "dos", "van", "von"}
        unknown_surname_tokens = []
        name_only_phrase = (
            len(protected_hits) >= 2
            and not source.strip().isupper()
            and not re.search(r"[,;:!?]", source)
            and any(token in name_connectors for token in normalized_source)
        )
        if name_only_phrase:
            for index, (word, normalized) in enumerate(zip(words, normalized_source)):
                if normalized in protected_tokens or normalized in name_connectors:
                    continue
                # Permit one title-cased surname following a name particle
                # when it is not frequent enough to be globally protected.
                # Every other visible token must be a protected name; this
                # prevents a capitalized/copied French clause from riding on
                # the exemption merely because it mentions two characters.
                if (
                    index > 0
                    and normalized_source[index - 1] in name_connectors
                    and word[:1].isupper()
                    and not word.isupper()
                    and not is_french_name_stopword(word)
                    and is_likely_romanized_japanese_name_token(word)
                ):
                    unknown_surname_tokens.append(index)
                    continue
                name_only_phrase = False
                break
        if name_only_phrase and len(unknown_surname_tokens) <= 1:
            normalized_visible_source = _normalize_translated_punctuation_spacing(
                visible_text(source), source_language, target_language,
            ).strip()
            normalized_visible_translation = _normalize_translated_punctuation_spacing(
                visible_text(translated), source_language, target_language,
            ).strip()
            if (
                _is_portuguese_target_language(target_language)
                and normalized_visible_translation == normalized_visible_source
            ):
                # E.g. "Shizuka Matsuo de Sakaimatsu..." is a name introduction,
                # not dialogue, but identity is allowed only for exact pt-BR.
                return False
        if _is_french_de_single_name_shape(source):
            # Do not let generic title-case preservation exempt copied French
            # `de + token`, including a colon/semicolon or ASS line break.
            return True
    if language == "french":
        shared = [
            (word, normalized)
            for word, normalized in zip(words, normalized_source)
            if normalized in _FRENCH_TO_PTBR_SHARED_INTERJECTIONS
        ]
        remaining = [
            (word, normalized)
            for word, normalized in zip(words, normalized_source)
            if normalized not in _FRENCH_TO_PTBR_SHARED_INTERJECTIONS
        ]
        if (
            len(shared) == 1
            and all(word[:1].isupper() for word, _ in remaining)
        ):
            normalized_visible_source = _normalize_translated_punctuation_spacing(
                visible_text(source), source_language, target_language,
            ).strip()
            normalized_visible_translation = _normalize_translated_punctuation_spacing(
                visible_text(translated), source_language, target_language,
            ).strip()
            remaining_are_protected_names = all(
                normalized in protected_tokens and not is_french_name_stopword(word)
                for word, normalized in remaining
            )
            if (
                _is_portuguese_target_language(target_language)
                and normalized_visible_translation == normalized_visible_source
                and remaining_are_protected_names
            ):
                # "Hein? Kyôko." is shared French/pt-BR, but the rest must be
                # exact known names. Title casing alone cannot exempt "Hein Banane".
                return False
        # Every exact-identity exception for French has now been evaluated:
        # reviewed name calls, protected names, and shared interjections. Any
        # remaining word-for-word copy is untranslated source, regardless of
        # casing or whether the words occur in the heuristic vocabulary.
        return True
    if len(normalized_source) == 1:
        known = _SOURCE_COPY_SINGLETONS.get(language, set())
        if language == "english":
            known = known | ENGLISH_COMMON
        elif language == "french":
            known = known | FRENCH_INDICATORS
        return normalized_source[0] in known
    common_words = ENGLISH_COMMON if language == "english" else (
        FRENCH_INDICATORS if language == "french" else set()
    )
    if language == "english":
        common_words = common_words | _SOURCE_COPY_SINGLETONS["english"]
    elif language == "french":
        common_words = common_words | _SOURCE_COPY_SINGLETONS["french"]

    # Preserve title-cased names/titles before applying the long-copy rule.
    # Sentence-like copies (e.g. "Run away with me!") remain rejectable.
    title_case = (
        len(words) >= 2
        and not source.strip().isupper()
        and all(word[:1].isupper() or word.casefold() in _TITLE_CONNECTORS for word in words)
        and (not terminal_punctuation or not all(w.casefold() in common_words for w in words))
    )
    if title_case:
        if language == "french":
            # French title casing alone is weak evidence: copied words such as
            # "Banane / Banane" must not pass. Fully protected name-only
            # identities were handled above with exact text/target checks.
            return True
        return False
    if len(normalized_source) >= 4:
        return True
    return bool(set(normalized_source) & common_words) or terminal_punctuation


def _has_high_confidence_source_residue(
    source: str,
    translated: str,
    source_language: str,
) -> bool:
    """Detect high-confidence source-language residue without blocking names."""
    from pipeline_v2_1_3 import source_residue_evidence, source_residue_strong

    language = _canonical_source_language_v3(source_language)
    source_words = set(re.findall(
        r"[\wÀ-ÿ]+", visible_text(source).casefold(), re.UNICODE,
    ))
    output_words = set(re.findall(
        r"[\wÀ-ÿ]+", visible_text(translated).casefold(), re.UNICODE,
    ))
    overlap = len(source_words & output_words) / max(1, len(source_words))
    if language == "english":
        # The legacy residue table is intentionally French-only. For English,
        # require source overlap plus an unambiguous interrogative/auxiliary,
        # or two independent supporting words; a lone shared word is not enough.
        strong = output_words & _ENGLISH_STRONG_RESIDUE_WORDS
        support = output_words & _ENGLISH_SUPPORT_RESIDUE_WORDS
        return bool(strong and source_words & strong) or (
            len(support) >= 2 and overlap >= 0.25
        )
    residue_language = "francês" if language == "french" else source_language
    evidence = source_residue_evidence(visible_text(translated), residue_language)
    if not evidence.get("count"):
        return False
    if (
        language == "french"
        and "est" in (evidence.get("word_hits") or [])
        and overlap >= 0.50
    ):
        # ``est`` is an unambiguous French copula, not a PT-BR word. A line
        # with a preserved proper name can lower source overlap below the
        # generic 0.60 threshold (e.g. ``Yasumori est hospitalisée``), so use
        # the still-conservative 0.50 threshold for this marker alone.
        return True
    return source_residue_strong(evidence, overlap)


def _source_language_prompt_guidance(source_language: str, target_language: str) -> str:
    """Return narrow source-language instructions for known translation gaps."""
    normalized_source = source_language.strip().casefold()
    if normalized_source not in {
        "francês", "frances", "français", "francais", "french", "fre", "fra", "fr",
    }:
        return ""

    guidance = (
        "A fonte está em francês: traduza também verbos e auxiliares curtos; "
        "não preserve a cópula francesa ‘est’ no idioma-alvo. Traduza-a conforme "
        "o contexto. Preserve nomes próprios e romanizações, não palavras "
        "funcionais francesas."
    )
    normalized_target = target_language.casefold().replace("_", "-")
    if "pt-br" in normalized_target or "português do brasil" in normalized_target:
        guidance += (
            " Em pt-BR, por exemplo, ‘est hospitalisée’ deve ser traduzido como "
            "‘está hospitalizada’. Use concordância natural de gênero e número, "
            "preserve negação, pessoa e modo verbal, e prefira colocação "
            "pronominal brasileira em vez de construções típicas do português "
            "europeu. Traduza expressões idiomáticas e falsos cognatos pelo "
            "sentido no contexto, nunca pela semelhança gráfica."
        )
    return guidance


def _payload_specific_prompt_guidance(
    payload: list[dict[str, Any]],
    source_language: str,
    target_language: str,
) -> str:
    """Add only the name and French lexical notes relevant to this batch."""
    protected_names = sorted({
        str(name)
        for item in payload
        for name in item.get("protected_names", ())
        if str(name).strip()
    }, key=str.casefold)
    hints = french_ptbr_context_hints(
        (str(item.get("source_text") or "") for item in payload),
        source_language,
        target_language,
    )
    parts: list[str] = []
    if protected_names:
        parts.append(
            "Nomes próprios identificados na fonte; preserve a grafia exatamente, "
            "sem traduzir nem converter em palavras do idioma-alvo: "
            + ", ".join(protected_names)
            + "."
        )
    literal_spellings = sorted({
        spelling
        for item in payload
        for spelling in source_name_spellings(
            str(item.get("source_text") or ""),
            item.get("protected_names", ()),
        )
    }, key=str.casefold)
    if literal_spellings:
        parts.append(
            "Grafias exatas encontradas na fonte (copie os caracteres e a caixa "
            "sem alterações): "
            + ", ".join(literal_spellings)
            + "."
        )
    if hints:
        parts.append("Cuidados lexicais para este lote: " + " ".join(hints))
    return " ".join(parts)


def make_v3_transport_call(
    transport: Any,
    *,
    response_provider: Any | None = None,
    capture_id_prefix: str = "v3",
    operation_id: str | None = None,
    source_language: str = "inglês",
    target_language: str = "português do Brasil (pt-BR)",
    thermal_gate: Callable[[], bool] | None = None,
    delay: float = 0.0,
) -> Callable[[list[dict[str, Any]]], dict[str | int, str]]:
    """Build a robust, structured batch transport caller for Pipeline V3."""
    from web_durable_provider import _http_post

    language_guidance = _source_language_prompt_guidance(source_language, target_language)
    guidance_prefix = f"{language_guidance} " if language_guidance else ""
    effective_delay = delay or getattr(transport, "delay_between_calls", 0.0)
    call_sequence = 0
    quality_repair_calls = 0
    quality_repair_limit = (
        V3_MAX_QUALITY_REPAIR_CALLS
        if getattr(response_provider, "operation_budget", None) is not None
        else V3_MAX_UNMETERED_QUALITY_REPAIR_CALLS
    )

    def _call_model(
        canonical_payload: dict[str, Any],
        *,
        operation: str = "V3_TRANSLATION",
    ) -> str:
        nonlocal call_sequence
        ollama_response_retries = 0
        retry_payload = canonical_payload
        while True:
            call_sequence += 1
            if thermal_gate and thermal_gate():
                raise RuntimeError("TRANSLATION_CANCELLED_OR_THERMAL_STOP")
            attempt_capture_id = f"{capture_id_prefix}-{call_sequence:06d}"
            try:
                if response_provider is not None:
                    provider_payload = dict(retry_payload)
                    provider_payload.update({
                        "operation": operation,
                        "model": getattr(transport, "model", None),
                        "model_digest": getattr(transport, "model_digest", None),
                        "operation_id": operation_id,
                        "pipeline_version": PIPELINE_VERSION,
                    })
                    captured = response_provider.respond(
                        provider_payload,
                        capture_id=attempt_capture_id,
                    )
                    content = captured.get("translation", captured.get("text"))
                    if not isinstance(content, str) or not content.strip():
                        raise V3ResponseContractError("V3_PROVIDER_EMPTY_RESPONSE")
                    return content
                request_body = transport.build_request(retry_payload)
                raw_bytes = _http_post(
                    transport.endpoint(),
                    transport.headers(),
                    request_body,
                    delay=effective_delay,
                )
                return transport.extract_content(raw_bytes)
            except Exception as exc:
                # This is deliberately narrower than the generic transport
                # retry policy: only a fully captured Ollama HTTP response
                # that is empty or explicitly unfinished is eligible. Socket
                # timeouts, incomplete captures, schema/translation failures,
                # hosted providers, and offline replay remain fail-closed.
                from transport_providers import TransportBlocked

                retryable_incomplete_ollama = (
                    isinstance(exc, TransportBlocked)
                    and str(exc).startswith((
                        "OLLAMA_EMPTY_CONTENT:", "OLLAMA_INCOMPLETE_RESPONSE:",
                    ))
                    and str(getattr(transport, "name", "")).casefold() == "ollama"
                    and response_provider is not None
                    and str(getattr(response_provider, "mode", "")).upper() == "LIVE_CAPTURED"
                )
                if (
                    not retryable_incomplete_ollama
                    or ollama_response_retries >= V3_MAX_OLLAMA_RESPONSE_RETRIES
                ):
                    raise
                ollama_response_retries += 1
                retry_payload = dict(retry_payload)
                # The constrained schema triggered a reproducible llama.cpp
                # grammar-stack failure. Retry with Ollama's generic JSON mode
                # while retaining the exact same prompt/model and the strict
                # V3 JSON/coverage/quality validation after generation.
                retry_payload["format"] = "json"
                LOGGER.warning(
                    "V3: resposta Ollama vazia/incompleta capturada; retry limitado %s/%s "
                    "em modo JSON genérico com nova chamada física — operation=%s model=%s capture=%s",
                    ollama_response_retries,
                    V3_MAX_OLLAMA_RESPONSE_RETRIES,
                    operation,
                    getattr(transport, "model", "unknown"),
                    attempt_capture_id,
                )
                time.sleep(V3_OLLAMA_RETRY_DELAY_SECONDS)

    def _clean_json_markdown(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    def _single_fallback_translate(
        source_text: str,
        protected_names: tuple[str, ...] = (),
    ) -> str:
        item_context = [{"source_text": source_text, "protected_names": protected_names}]
        context_guidance = _payload_specific_prompt_guidance(
            item_context, source_language, target_language,
        )
        canonical_payload = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Você é um tradutor profissional de legendas de anime. "
                        f"Traduza a legenda de {source_language} para {target_language}. "
                        f"{guidance_prefix}"
                        f"{context_guidance} "
                        "Traduza todo o sentido: preserve quem age e quem recebe a ação, negação, "
                        "tempo verbal, gênero/número e se a fala é pergunta, afirmação ou ordem. "
                        "Não omita nem invente informação. Mantenha nomes próprios, títulos e romanização "
                        "quando não houver tradução estabelecida; traduza o restante integralmente. "
                        "Traduza também letras de músicas e canções (inclusive linhas demarcadas com notas musicais ♪), "
                        "preservando os símbolos musicais ♪ ao redor da tradução em português. "
                        "O item contém somente texto visível, não comandos ASS. Não invente nem acrescente "
                        "tags, chaves de estilo ou escapes de formatação. Preserve a quantidade de quebras "
                        "de linha recebida e nunca divida uma palavra. Preserve também a quantidade e a "
                        "posse por evento de aspas, parênteses e colchetes; não acrescente nem remova "
                        "delimitadores. Retorne APENAS a tradução em texto "
                        "simples, sem comentários."
                    ),
                },
                {"role": "user", "content": source_text},
            ],
            "options": {"temperature": 0.0, "num_predict": 512},
            "stream": False,
            "think": False,
            "response_mode": "text",
        }
        content = _call_model(canonical_payload)
        return content.strip().strip('"').strip("'")

    response_schema = {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "translation": {"type": "string"},
                    },
                    "required": ["id", "translation"],
                },
            }
        },
        "required": ["translations"],
    }

    def _parse_translation_response(
        raw_text: str,
        expected_ids: list[str],
        error_prefix: str,
    ) -> dict[str, str]:
        try:
            parsed = json.loads(_clean_json_markdown(raw_text))
        except (TypeError, json.JSONDecodeError) as exc:
            raise V3ResponseContractError(f"{error_prefix}_NOT_JSON") from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("translations"), list):
            raise V3ResponseContractError(f"{error_prefix}_SCHEMA_INVALID")

        results: dict[str, str] = {}
        seen_ids: set[str] = set()
        for item in parsed["translations"]:
            if not isinstance(item, dict) or "id" not in item or not isinstance(item.get("translation"), str):
                raise V3ResponseContractError(f"{error_prefix}_ITEM_SCHEMA_INVALID")
            item_key = str(item["id"])
            if item_key not in expected_ids:
                raise V3ResponseContractError(f"{error_prefix}_UNKNOWN_UNIT_ID")
            if item_key in seen_ids:
                raise V3ResponseContractError(f"{error_prefix}_DUPLICATE_UNIT_ID")
            seen_ids.add(item_key)
            translation = item["translation"].strip()
            if translation:
                results[item_key] = translation
        return results

    def _quality_risks_for_payload(
        payload: list[dict[str, Any]],
        translations: dict[str | int, str],
    ) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
        risks: list[tuple[dict[str, Any], tuple[str, ...]]] = []
        for item in payload:
            item_id = str(item["id"])
            translated = translations.get(item_id) or translations.get(item["id"]) or ""
            source_text = str(item["source_text"])
            flags = translation_quality_flags(
                source_text,
                translated,
                source_language=source_language,
                target_language=target_language,
                protected_names=tuple(item.get("protected_names", ())),
            )
            if _visible_line_break_count(source_text) != _visible_line_break_count(translated):
                flags = (*flags, "ASS_LINE_BREAK_COUNT_MISMATCH")
            if _has_high_confidence_source_residue(
                source_text, translated, source_language,
            ):
                flags = (*flags, "SOURCE_LANGUAGE_RESIDUE")
            if _is_untranslated_source_copy(
                source_text,
                translated,
                source_language,
                protected_names=tuple(item.get("protected_names", ())),
                contextually_protected_names=tuple(item.get("contextually_protected_names", ())),
                target_language=target_language,
            ):
                flags = (*flags, "V3_TRANSLATION_SOURCE_COPY")
            if flags:
                risks.append((item, flags))
        return risks

    def _repair_quality_risks(
        flagged: list[tuple[dict[str, Any], tuple[str, ...]]],
        translations: dict[str | int, str],
    ) -> None:
        nonlocal quality_repair_calls
        if not flagged:
            return

        def _store_translation(item: dict[str, Any], translated: str) -> None:
            item_id = str(item["id"])
            translations[item_id] = translated
            try:
                translations[int(item_id)] = translated
            except ValueError:
                pass

        # A line-count-only risk is formatting, not translation semantics.
        # Preserve the model's words and deterministically wrap them at word
        # boundaries instead of spending another model call that may ignore
        # the line-count instruction.
        flagged_items = [item for item, _flags in flagged]
        for item, flags in flagged:
            if flags != ("ASS_LINE_BREAK_COUNT_MISMATCH",):
                continue
            item_id = str(item["id"])
            draft = translations.get(item_id) or translations.get(item["id"]) or ""
            reflowed = _reflow_plain_translation(
                draft, _visible_line_break_count(str(item["source_text"])),
            )
            if reflowed is not None:
                _store_translation(item, reflowed)
        flagged = _quality_risks_for_payload(flagged_items, translations)
        if not flagged:
            return

        if quality_repair_calls >= quality_repair_limit:
            item, flags = flagged[0]
            raise V3ResponseContractError(
                f"V3_TRANSLATION_QUALITY_RISK:{item['id']}:{','.join(flags)}:REPAIR_BUDGET_EXHAUSTED"
            )

        quality_repair_calls += 1
        repair_items = []
        for item, flags in flagged:
            item_id = str(item["id"])
            draft = translations.get(item_id) or translations.get(item["id"]) or ""
            if "V3_TRANSLATION_SOURCE_COPY" in flags and _is_untranslated_source_copy(
                str(item["source_text"]),
                draft,
                source_language,
                target_language=target_language,
            ):
                draft = f"(texto não traduzido da fonte; traduza integralmente para {target_language})"
            repair_items.append({
                "id": item_id,
                "source_text": str(item["source_text"]),
                "draft_translation": draft,
                "quality_risks": list(flags),
                "protected_names": list(item.get("protected_names", ())),
                "contextually_protected_names": list(item.get("contextually_protected_names", ())),
            })

        context_guidance = _payload_specific_prompt_guidance(
            [
                {
                    "source_text": item["source_text"],
                    "protected_names": item.get("protected_names", ()),
                }
                for item, _flags in flagged
            ],
            source_language,
            target_language,
        )
        source_copy_guidance = (
            f"Tradução obrigatória: não copie o texto em língua estrangeira ({source_language}) para a tradução; "
            f"traduza termos, expressões comuns, títulos, versos e músicas (incluindo linhas demarcadas com notas musicais ♪) "
            f"integralmente para o português do Brasil, mantendo os símbolos musicais ♪ ao redor da tradução."
            if any({"SOURCE_LANGUAGE_RESIDUE", "V3_TRANSLATION_SOURCE_COPY"}.intersection(flags) for _item, flags in flagged)
            else ""
        )
        break_count_guidance = (
            "Estrutura obrigatória: a tradução corrigida deve preservar exatamente "
            "a quantidade de quebras de linha da fonte. Não crie quebras adicionais, "
            "não divida frases em itens novos e não acrescente fala ou conteúdo ausente."
            if any("ASS_LINE_BREAK_COUNT_MISMATCH" in flags for _item, flags in flagged)
            else ""
        )
        delimiter_guidance = (
            "Preserve exatamente no item a quantidade e a sequência de aspas, parênteses e colchetes da fonte. "
            "Não reordene, acrescente, remova ou transfira delimitadores entre eventos; se a fonte deixa "
            "uma abertura continuar no evento seguinte, mantenha essa mesma posse."
            if any(
                {"ASS_DELIMITER_TOKEN_COUNT_MISMATCH", "ASS_DELIMITER_SEQUENCE_MISMATCH"}.intersection(flags)
                for _item, flags in flagged
            )
            else ""
        )
        repair_payload = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Você é um revisor profissional de tradução de {source_language} para {target_language}. "
                        "Faça uma única correção direcionada para os riscos descritos em cada item. "
                        "Use a fonte como autoridade; preserve negação, agente, pessoa, modo verbal, "
                        "sentido idiomático, nomes próprios e concordância natural. Não introduza fatos, "
                        "traduza resíduos não intencionais da língua-fonte, não apenas repita o rascunho "
                        "sinalizado e escreva em português brasileiro natural. "
                        f"{context_guidance} {source_copy_guidance} {break_count_guidance} {delimiter_guidance} "
                        "Retorne estritamente um objeto JSON no formato "
                        '{"translations": [{"id": "<id>", "translation": "<texto corrigido>"}]}, '
                        "com exatamente todos os IDs recebidos e nenhum outro."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"items": repair_items}, ensure_ascii=False),
                },
            ],
            "options": {"temperature": 0.0, "num_predict": 4096},
            "format": response_schema,
            "stream": False,
            "think": False,
        }
        raw_repair = _call_model(repair_payload, operation="V3_QUALITY_REPAIR")
        expected_ids = [item["id"] for item in repair_items]
        repaired = _parse_translation_response(
            raw_repair, expected_ids, "V3_QUALITY_REPAIR_RESPONSE",
        )
        if set(repaired) != set(expected_ids):
            missing_ids = sorted(set(expected_ids) - set(repaired))
            raise V3TranslationCoverageError(
                f"V3_QUALITY_REPAIR_RESPONSE_INCOMPLETE:{','.join(missing_ids)}"
            )
        for item_id, translated in repaired.items():
            translations[item_id] = translated
            try:
                translations[int(item_id)] = translated
            except ValueError:
                pass

        repair_items_by_id = {str(item["id"]): item for item, _flags in flagged}
        unresolved = _quality_risks_for_payload(
            list(repair_items_by_id.values()), translations,
        )
        for item, flags in unresolved:
            if flags != ("ASS_LINE_BREAK_COUNT_MISMATCH",):
                continue
            item_id = str(item["id"])
            repaired_text = translations.get(item_id) or translations.get(item["id"]) or ""
            reflowed = _reflow_plain_translation(
                repaired_text, _visible_line_break_count(str(item["source_text"])),
            )
            if reflowed is not None:
                _store_translation(item, reflowed)
        unresolved = _quality_risks_for_payload(
            list(repair_items_by_id.values()), translations,
        )
        if unresolved:
            from translation_quality import (
                remove_unowned_terminal_ascii_quote_closures,
                restore_source_enclosing_ascii_quotes,
            )
            for item, flags in unresolved:
                item_id = str(item["id"])
                txt = translations.get(item_id) or translations.get(item["id"]) or ""
                cleaned = remove_unowned_terminal_ascii_quote_closures(str(item.get("source_text", "")), txt)
                cleaned = restore_source_enclosing_ascii_quotes(str(item.get("source_text", "")), cleaned)
                if cleaned != txt:
                    _store_translation(item, cleaned)
            delimiter_flags = {"ASS_DELIMITER_TOKEN_COUNT_MISMATCH", "ASS_DELIMITER_SEQUENCE_MISMATCH"}
            unresolved_semantic = [
                (item, tuple(f for f in flags if f not in delimiter_flags))
                for item, flags in unresolved
            ]
            unresolved_semantic = [(item, flags) for item, flags in unresolved_semantic if flags]
            if unresolved_semantic:
                item, flags = unresolved_semantic[0]
                raise V3ResponseContractError(
                    f"V3_TRANSLATION_QUALITY_RISK:{item['id']}:{','.join(flags)}:REPAIR_FAILED"
                )

    def transport_call(payload: list[dict[str, Any]]) -> dict[str | int, str]:
        if not payload:
            return {}

        context_guidance = _payload_specific_prompt_guidance(
            payload, source_language, target_language,
        )

        system_prompt = (
            f"Você é um tradutor profissional de legendas de anime de {source_language} para {target_language}. "
            f"{guidance_prefix}"
            f"{context_guidance} "
            "Traduza cada item integralmente, com linguagem natural para legendas. Preserve quem age e "
            "quem recebe a ação, negação, tempo verbal, gênero/número e a intenção da fala (pergunta, "
            "afirmação ou ordem). Não omita nem invente informação. Mantenha nomes próprios, títulos e "
            "romanização quando não houver tradução estabelecida; traduza o restante integralmente. "
            "Traduza também letras de músicas e canções (inclusive linhas demarcadas com notas musicais ♪), "
            "preservando os símbolos musicais ♪ ao redor da tradução em português. "
            "Os itens contêm somente texto visível, não comandos ASS. Não invente nem acrescente "
            "tags, chaves de estilo ou escapes de formatação. Preserve exatamente a quantidade de "
            "quebras de linha de cada item e nunca divida uma palavra. Preserve também a quantidade "
            "e a posse por evento de aspas, parênteses e colchetes; não acrescente nem remova "
            "delimitadores nem altere a sequência deles. Em português, não mantenha "
            "espaço antes de ?, !, : ou ;. "
            "Retorne estritamente um objeto JSON com o formato: "
            '{"translations": [{"id": "<id>", "translation": "<texto traduzido>"}]}. '
            "Retorne APENAS o JSON válido."
        )

        user_content = json.dumps(
            {
                "source_language": source_language,
                "target_language": target_language,
                "items": [
                    {"id": str(item["id"]), "text": item["source_text"]}
                    for item in payload
                ],
            },
            ensure_ascii=False,
        )

        canonical_payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "options": {"temperature": 0.0, "num_predict": 4096},
            "format": response_schema,
            "stream": False,
            "think": False,
        }

        expected_ids = [str(item["id"]) for item in payload]
        if len(set(expected_ids)) != len(expected_ids):
            raise V3ResponseContractError("V3_REQUEST_DUPLICATE_UNIT_ID")
        try:
            raw_text = _call_model(canonical_payload)
            parsed_translations = _parse_translation_response(
                raw_text, expected_ids, "V3_BATCH_RESPONSE",
            )
        except V3ResponseContractError as batch_error:
            if len(payload) > 1:
                LOGGER.warning(
                    "V3: falha no lote de %d unidades (%s); dividindo lote pela metade de forma resiliente",
                    len(payload), batch_error,
                )
                mid = len(payload) // 2
                first_half = transport_call(payload[:mid])
                second_half = transport_call(payload[mid:])
                combined = dict(first_half)
                combined.update(second_half)
                return combined
            raise
        results: dict[str | int, str] = {}
        for item_key, translation in parsed_translations.items():
            results[item_key] = translation
            try:
                results[int(item_key)] = translation
            except ValueError:
                pass

        # Isolamento individual somente para IDs ausentes/vazios em uma
        # resposta de lote estruturalmente válida. Erros de rede/schema não
        # são mascarados por uma sequência de novas chamadas.
        for item in payload:
            item_id = item["id"]
            if item_id not in results and str(item_id) not in results:
                fallback_text = _single_fallback_translate(
                    item["source_text"],
                    tuple(item.get("protected_names", ())),
                )
                if not fallback_text:
                    raise V3TranslationCoverageError(f"V3_TRANSLATION_EMPTY:{item_id}")
                results[item_id] = fallback_text
                results[str(item_id)] = fallback_text

        for item in payload:
            item_id = str(item["id"])
            translated = results.get(item_id) or results.get(item["id"]) or ""
            corrected = remove_unowned_terminal_ascii_quote_closures(
                str(item["source_text"]), translated,
            )
            corrected = restore_source_enclosing_ascii_quotes(
                str(item["source_text"]), corrected,
            )
            if corrected != translated:
                results[item_id] = corrected
                try:
                    results[int(item_id)] = corrected
                except ValueError:
                    pass
                LOGGER.info("V3_SOURCE_QUOTE_OWNERSHIP_RESTORED unit_id=%s", item_id)

        _repair_quality_risks(_quality_risks_for_payload(payload, results), results)

        return results

    return transport_call


def translate_subtitle_file_v3(
    input_path: str | Path,
    output_path: str | Path,
    *,
    transport_call: Callable[[list[dict[str, Any]]], dict[str, str]],
    target_batch_size: int = 16,
    max_batch_words: int = 90,
    max_batch_chars: int = 420,
    enable_visual_effects: bool = True,
    enable_karaoke: bool = True,
    allow_source_copy: bool = False,
    source_language: str = "inglês",
    target_language: str = "português do Brasil (pt-BR)",
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Execute the end-to-end V3 in-memory translation pipeline."""
    import copy
    start_time = time.monotonic()
    src_path = Path(input_path)
    dest_path = Path(output_path)

    if not src_path.is_file():
        raise FileNotFoundError(f"Arquivo fonte não encontrado: {src_path}")
    if dest_path.exists():
        raise FileExistsError(f"final output already exists: {dest_path}")
    if not 1 <= int(target_batch_size) <= 64:
        raise ValueError("V3_TARGET_BATCH_SIZE_OUT_OF_RANGE")

    # 1. Carregamento em memória (AST único com cópia em RAM)
    original_doc = ASSDocumentAST.from_file(src_path)
    working_doc = ASSDocumentAST(copy.deepcopy(original_doc.ssa))
    total_events = len(original_doc)
    protected_source_names = extract_repeated_french_names(
        (
            node.visible
            for node in original_doc.events
            if not node.is_comment and node.type != "Comment"
        ),
        source_language,
    )

    # 2. Planejamento semântico em memória. Letras são classificadas pela
    # autoridade já usada no V2.3.0: romaji/efeitos/créditos não viram prosa,
    # e karaoke silábico em inglês falha fechado porque seu timing não pode
    # ser remapeado sem uma política linguística específica.
    from effects_engine import has_karaoke_tags
    from production_v2_3_0_adapter import (
        _looks_like_english_song_text,
        discover_song_units,
    )
    from pipeline_v2_1_3 import classify_event

    orchestrator = InMemorySemanticOrchestrator(
        target_batch_size=target_batch_size,
        max_batch_words=max_batch_words,
        max_batch_chars=max_batch_chars,
    )
    events_pysubs2 = [node.to_pysubs2() for node in working_doc.events]
    song_discovery = discover_song_units(events_pysubs2)
    song_event_indices = set(song_discovery["classifications"])
    preserved_event_indices: set[int] = set()
    for index, node in enumerate(working_doc.events):
        classification, _reason, _confidence = classify_event(
            events_pysubs2[index],
            node.visible,
            {},
            source_language=source_language,
        )
        if classification in {"TECHNICAL_OR_EMPTY", "ROMAJI_PRESERVED", "ROMANIZATION_GLOSS"}:
            preserved_event_indices.add(node.index)
    additional_song_units: list[TranslationUnit] = []
    song_unit_members: dict[str, list[int]] = {}
    song_unit_sources: dict[str, str] = {}
    for index, node in enumerate(working_doc.events):
        if has_karaoke_tags(node.text):
            song_event_indices.add(index)
            if _looks_like_english_song_text(node.visible):
                raise PipelineV3Error("KARAOKE_TRANSLATION_TIMING_UNSUPPORTED")
    for (style, source_text), indices in sorted(song_discovery["units"].items()):
        indices = [index for index in indices if index not in preserved_event_indices]
        if not indices:
            continue
        if any(index in song_discovery["unsupported"] for index in indices):
            raise PipelineV3Error("KARAOKE_TRANSLATION_TIMING_UNSUPPORTED")
        unit_id = "song-" + hashlib.sha256(
            f"{style}\0{source_text}".encode("utf-8")
        ).hexdigest()[:24]
        song_unit_members[unit_id] = list(indices)
        song_unit_sources[unit_id] = source_text
        additional_song_units.append(TranslationUnit(
            id=unit_id,
            source_text=source_text,
            is_sign=False,
            metadata={"song_style": style, "member_indices": list(indices)},
        ))

    batches, sign_groups = orchestrator.plan_batches(
        working_doc,
        excluded_event_indices=song_event_indices | preserved_event_indices,
        additional_units=additional_song_units,
    )

    # 3. Execução das chamadas de tradução
    translations: dict[int | str, str] = {}
    for batch_idx, batch in enumerate(batches):
        protected_names_by_unit: dict[str, tuple[str, ...]] = {}
        contextual_protected_names_by_unit: dict[str, tuple[str, ...]] = {}
        for unit in batch.units:
            names = names_in_source(unit.source_text, protected_source_names)
            contextual_names = tuple(dict.fromkeys(
                name
                for source_texts, source_index in _adjacent_ellipsis_source_context(batch, unit)
                for name in progressive_protected_name_prefixes_in_sequence(
                    source_texts, source_index, protected_source_names,
                )
            ))
            contextual_protected_names_by_unit[str(unit.id)] = contextual_names
            protected_names_by_unit[str(unit.id)] = tuple(dict.fromkeys(
                names + contextual_names,
            ))
        payload = [
            {
                "id": unit.id,
                "source_text": unit.source_text,
                "is_sign": unit.is_sign,
                "protected_names": protected_names_by_unit[str(unit.id)],
                "contextually_protected_names": contextual_protected_names_by_unit[str(unit.id)],
            }
            for unit in batch.units
        ]
        # Invocação do provedor de modelo
        batch_results = transport_call(payload)
        if not isinstance(batch_results, dict):
            raise V3ResponseContractError("V3_BATCH_RESULT_NOT_MAPPING")
        for unit in batch.units:
            res = batch_results.get(str(unit.id)) or batch_results.get(unit.id)
            if res:
                if not isinstance(res, str) or not res.strip():
                    raise V3TranslationCoverageError(f"V3_TRANSLATION_EMPTY:{unit.id}")
                translated_text = _normalize_translated_punctuation_spacing(
                    res.strip(), source_language, target_language,
                )
                if (
                    str(unit.id) not in song_unit_members
                    and _is_untranslated_source_copy(
                        unit.source_text,
                        translated_text,
                        source_language,
                        protected_names=protected_names_by_unit[str(unit.id)],
                        contextually_protected_names=(
                            contextual_protected_names_by_unit[str(unit.id)]
                        ),
                        target_language=target_language,
                    )
                ):
                    raise V3ResponseContractError(f"V3_TRANSLATION_SOURCE_COPY:{unit.id}")
                if _has_high_confidence_source_residue(
                    unit.source_text, translated_text, source_language,
                ):
                    raise V3ResponseContractError(f"V3_TRANSLATION_SOURCE_RESIDUE:{unit.id}")
                translated_text = remove_unowned_terminal_ascii_quote_closures(unit.source_text, translated_text)
                translated_text = restore_source_enclosing_ascii_quotes(unit.source_text, translated_text)
                quality_flags = translation_quality_flags(
                    unit.source_text,
                    translated_text,
                    source_language=source_language,
                    target_language=target_language,
                    protected_names=protected_names_by_unit[str(unit.id)],
                )
                if quality_flags:
                    semantic_flags = [
                        f for f in quality_flags
                        if f not in {"ASS_DELIMITER_TOKEN_COUNT_MISMATCH", "ASS_DELIMITER_SEQUENCE_MISMATCH"}
                    ]
                    if semantic_flags:
                        raise V3ResponseContractError(
                            f"V3_TRANSLATION_QUALITY_RISK:{unit.id}:{','.join(semantic_flags)}"
                        )
                translations[unit.id] = translated_text
            else:
                raise V3TranslationCoverageError(f"V3_TRANSLATION_MISSING:{unit.id}")
        if progress_callback:
            progress_callback(batch_idx + 1, len(batches))

    # 4. Aplicação das traduções em memória
    working_doc = orchestrator.apply_translations(working_doc, translations, sign_groups)
    from ass_structure import replace_source_payload

    for unit_id, member_indices in song_unit_members.items():
        translated = translations[unit_id]
        source = next(node.text for node in original_doc.events if node.index == member_indices[0])
        normalized_translation = " ".join(translated.casefold().split())
        normalized_source = " ".join(song_unit_sources[unit_id].casefold().split())
        if normalized_translation == normalized_source and not allow_source_copy:
            raise V3ResponseContractError("V3_SONG_TRANSLATION_SOURCE_COPY")
        rendered = replace_source_payload(source, translated)
        if not rendered:
            raise V3ResponseContractError("V3_SONG_TRANSLATION_EMPTY")
        for index in member_indices:
            working_doc.events[index].text = replace_source_payload(
                original_doc.events[index].text, translated
            )

    # 5. Pós-processamento visual de glifos e karaokê em memória
    effects_engine = InMemoryEffectsEngine(
        enable_visual_glyphs=enable_visual_effects,
        enable_karaoke=enable_karaoke,
    )
    effects_stats = effects_engine.process_effects(working_doc, original_doc)

    # 6. Validação profunda de conformidade estrutural
    sign_member_indices = set()
    for g in sign_groups:
        sign_member_indices.update(g["member_indices"])

    break_adjacent_re = re.compile(r"([\wÀ-ÿ]+)?[ \t]*\\(?:[Nn]|h)[ \t]*([\wÀ-ÿ]+)?", re.UNICODE)
    short_titlecase_re = re.compile(r"(?<!\w)([A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]{1,2})(?!\w)")

    allowed_break_words_by_event = {}
    for node in original_doc.events:
        tokens = set()
        for name in names_in_source(node.visible, protected_source_names):
            for part in re.findall(r"[\wÀ-ÿ]+", name):
                if len(part) < 4:
                    tokens.add(part)
        for match in break_adjacent_re.finditer(node.text or ""):
            for g in match.groups():
                if g and len(g) < 4:
                    tokens.add(g)
        visible = visible_text(str(node.visible or ""), line_break=" ")
        for match in short_titlecase_re.finditer(visible):
            tokens.add(match.group(1))
        allowed_break_words_by_event[node.index] = tuple(tokens)

    validation = validate_document_structure(
        original_doc,
        working_doc,
        segmented_indices=sign_member_indices,
        allow_tag_reflow_indices=sign_member_indices,
        allowed_break_words_by_event=allowed_break_words_by_event,
    )

    if not validation["valid"]:
        raise PipelineV3Error(f"Validação estrutural falhou: {validation['issues']}")

    # 7. Gravação atômica no destino, validando também o artefato serializado.
    if dest_path.exists():
        raise FileExistsError(f"final output already exists: {dest_path}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{dest_path.name}.", suffix=dest_path.suffix, dir=str(dest_path.parent)
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    try:
        working_doc.save(temporary_path)
        with temporary_path.open("rb") as output_file:
            os.fsync(output_file.fileno())
        serialized_doc = ASSDocumentAST.from_file(temporary_path)
        serialized_validation = validate_document_structure(
            original_doc,
            serialized_doc,
            segmented_indices=sign_member_indices,
            allow_tag_reflow_indices=sign_member_indices,
            allowed_break_words_by_event=allowed_break_words_by_event,
        )
        if not serialized_validation["valid"]:
            raise PipelineV3Error(
                f"Validação estrutural após serialização falhou: {serialized_validation['issues']}"
            )
        if dest_path.exists():
            raise FileExistsError(f"final output appeared during translation: {dest_path}")
        _publish_no_clobber(temporary_path, dest_path)
        if os.name != "nt":
            directory_fd = os.open(dest_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)

    output_bytes = dest_path.read_bytes()
    output_sha256 = hashlib.sha256(output_bytes).hexdigest()
    elapsed = time.monotonic() - start_time

    return {
        "status": "COMPLETED",
        "pipeline": PIPELINE_VERSION,
        "total_events": total_events,
        "batches_processed": len(batches),
        "sign_groups_aligned": len(sign_groups),
        "effects_stats": effects_stats,
        "validation": validation,
        "serialized_validation": serialized_validation,
        "output_path": str(dest_path),
        "output_sha256": output_sha256,
        "elapsed_seconds": elapsed,
    }
