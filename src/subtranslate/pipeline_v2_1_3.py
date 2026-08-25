"""PIPELINE V2.1 experimental laboratory.

The module is intentionally independent from both production and V2.  It keeps
ASS structure in Python and sends only clean linguistic units to Ollama.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pysubs2
import requests


TAG_RE = re.compile(r"\{[^}]*\}")
TOKEN_RE = re.compile(r"(\{[^}]*\}|\\N)")
KARAOKE_RE = re.compile(r"\\k(?:f|o)?\d+", re.I)
WORD_RE = re.compile(r"[A-Za-zÀ-ÿ]+", re.UNICODE)
BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")
POSITION_RE = re.compile(r"\\(?:pos|move|an\d|p\d)", re.I)
SPEAKER_RE = re.compile(r"^\s*(?:--|[^:]{1,32}:)")
ENGLISH_COMMON = {
    "the", "a", "an", "and", "or", "but", "what", "why", "how", "you", "we",
    "i", "he", "she", "it", "is", "are", "was", "were", "to", "of", "in",
    "on", "for", "with", "this", "that", "not", "do", "dont", "don't", "can",
    "will", "just", "all", "have", "has", "be", "my", "your", "our", "they",
}
# Frequent Portuguese words and established loans/cognates which can be
# identical in both languages.  This is deliberately a conservative exclusion
# list, not a translation dictionary; it prevents names and valid Portuguese
# forms such as "dose", "altar" and "cruel" from causing retries.
PORTUGUESE_IDENTICAL = {
    "about", "altar", "and", "are", "ata", "atari", "courageous", "cruel",
    "deluxe", "dose", "fatal", "final", "super", "sacrificial", "serial",
    "manual", "nuclear", "grave", "bacteria", "celsius",
    "total", "normal", "original", "animal", "central", "control", "digital",
    "especial", "formal", "general", "local", "material", "natural", "normal",
    "popular", "real", "regular", "social", "similar", "simple", "usual",
}
SONG_WORDS = {"op", "ed", "opening", "ending", "song", "theme", "lyric", "lyrics", "karaoke", "romaji", "kanji", "insert"}
VISUAL_STOPWORDS = {"a", "o", "as", "os", "um", "uma", "de", "do", "da", "dos", "das", "e", "em", "por", "para", "com", "no", "na", "nos", "nas", "que"}
CRITICAL_FLAGS = {
    "LINE_BREAK_INSIDE_WORD", "LINE_BREAK_COUNT_MISMATCH", "CONTENT_LOSS",
    "ASS_TAG_MISMATCH", "SEGMENT_ID_MISMATCH", "STRUCTURAL_CONTENT_IN_MODEL_OUTPUT",
    "UNBALANCED_QUOTES", "UNBALANCED_DELIMITERS", "MODEL_EMITTED_STRUCTURAL_TOKEN",
    "DELIMITER_COUNT_MISMATCH",
    "ASS_INLINE_TAG_SPLIT_WORD", "ASS_INLINE_TAG_ANCHOR_FAILURE",
    "ASS_INLINE_TAG_DUPLICATION",
    "IDIOMATIC_LITERAL_RISK",
    "UNTRANSLATED_DIALOGUE",
}
SDH_RULES = {
    "sighs": "suspiros", "laughs": "risos", "gasps": "arfando", "crying": "chorando",
    "door closes": "porta se fecha", "footsteps": "passos", "whispers": "sussurros",
    "screams": "gritos",
}

# Conservative, release-independent signals for Japanese written in Latin
# characters.  This is intentionally not a Japanese translator or a title
# dictionary: a line is preserved only when several weak signals agree.
ROMAJI_MARKERS = {
    "ga", "wa", "o", "wo", "no", "ni", "de", "to", "mo", "e", "he",
    "yo", "ne", "ka", "kara", "made", "nara", "deshou", "desho", "dakara",
    "sono", "naka", "tokidoki", "tashika", "sagashite", "sore",
}
ROMAJI_COMMON_SYLLABLES = re.compile(r"(?:shi|chi|tsu|kya|kyu|kyo|sha|shu|sho|nya|nyu|nyo|rya|ryu|ryo|sore|sono|naka|kara|nara|desh|toki|machi|ame|yasa|zanku)", re.I)
ROMANIZATION_GLOSS_RE = re.compile(r"^[\"']?(?P<base>[A-Za-z][A-Za-z'’-]{2,})\s+\[(?P<gloss>[^\[\]]+)\][\"']?$")


@dataclass
class Config:
    ollama_url: str
    model: str = "qwen3.5:9b"
    think: bool = False
    temperature: float = 0.0
    num_ctx: int = 4096
    # V226 responses must have enough output headroom for the canonical JSON
    # envelope.  This is part of the family configuration identity; callers
    # may still override it explicitly, but the canonical default is 1024.
    num_predict: int = 1024
    keep_alive: str = "30m"
    timeout_seconds: float = 240
    batch_target_size: int = 6
    context_before: int = 3
    context_after: int = 3
    context_budget_tokens: int = 1100
    context_max_chars: int = 2600
    scene_gap_ms: int = 6000
    # Optional pluggable transport provider.  When set, Client.call performs
    # the POST through the provider (endpoint/headers/wire format) instead of
    # the raw Ollama URL, and extracts the assistant text via the provider.
    transport: Any = None
    max_retries: int = 2
    operation_retry_transport_cap: int | None = None
    per_event_retry_transport_cap: int | None = None
    series_title: str = ""
    episode_title: str = ""
    glossary_path: str = ""
    strict_json: bool = True
    enable_sign_grouping: bool = True
    sdh_deterministic_enabled: bool = True
    diagnostic_capture: bool = False
    # Shared retry budget.  It is consumed by real descendant calls (not by
    # recursive batch splitting) and is inherited by every retry path.
    retry_budget_calls: int = 32
    # Laboratory-only safety valve; zero means disabled.
    diagnostic_hard_stop_calls: int = 0
    # Optional local word list used only to distinguish a genuinely retained
    # English token from a legitimate name/romanisation/onomatopoeia.
    english_dictionary_path: str = "/usr/share/dict/american-english"
    # Optional operation-scoped reservation ledger.  Legacy callers leave it
    # unset; the V2.3.8 canonical materializer injects it explicitly.
    operation_budget: Any = None
    model_digest: str | None = None
    # Opt-in V2.3.8 per-call durability context. Legacy callers leave this
    # unset and retain the historical request path byte-for-byte.
    durable_context: Any = None
    # Explicit per-attempt taxonomy/identity, installed by Runner._attempt.
    # Textual phase names are diagnostics only and never budget authority.
    durable_attempt_contract: Any = None

    @classmethod
    def from_file(cls, path: Path) -> "Config":
        data = json.loads(path.read_text(encoding="utf-8"))
        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"configuração desconhecida: {unknown}")
        return cls(**data)

    def override(self, **values: Any) -> "Config":
        data = asdict(self)
        for key, value in values.items():
            if value is not None:
                data[key] = value
        return Config(**data)


@dataclass
class CleanSegment:
    segment_id: int
    source_text: str
    clean_text: str
    speaker_like: bool = False


@dataclass
class Event:
    id: int
    original_index: int
    layer: int
    start: int
    end: int
    style: str
    name: str
    marginl: int
    marginr: int
    marginv: int
    effect: str
    original_text: str
    visible_text: str
    clean_text: str
    segments: list[CleanSegment]
    tag_anchors: list[dict[str, Any]]
    line_break_boundaries: list[int]
    has_positioning: bool
    classification: str = "UNKNOWN"
    classification_reason: str = ""
    screen_confidence: float = 0.0
    sign_group: str = ""
    romanization_base: str = ""
    romanization_gloss: str = ""
    block_classification: str = ""
    block_confidence: float = 0.0
    song_block_id: str = ""
    song_confidence: float = 0.0
    song_evidence: list[str] = field(default_factory=list)


@dataclass
class Unit:
    unit_id: str
    events: list[Event]
    grouped_sign: bool = False


@dataclass
class Result:
    id: int
    status: str = "pending"
    final_text: str | None = None
    final_segments: list[dict[str, Any]] | None = None
    final_model: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    retry_count: int = 0
    flags: list[str] = field(default_factory=list)
    failure_reason: str = ""
    retry_recommended: bool = False


@dataclass
class RetryBudget:
    remaining: int
    consumed: int = 0
    exhausted: bool = False
    last_reason: str = ""
    max_depth: int = 0

    def consume(self, reason: str, depth: int) -> bool:
        self.max_depth = max(self.max_depth, depth)
        self.last_reason = reason
        if self.remaining <= 0:
            self.exhausted = True
            return False
        self.remaining -= 1
        self.consumed += 1
        return True


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 3.5))


def strict_json(content: str) -> Any:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("resposta vazia ou não textual")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"chave JSON duplicada: {key}")
            result[key] = value
        return result

    return json.loads(content.strip(), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _field(obj: Any, name: str, default: Any = "") -> Any:
    value = getattr(obj, name, default)
    return default if value is None else value


def _style_tokens(line: Any) -> set[str]:
    raw = f"{_field(line, 'style', '')} {_field(line, 'effect', '')}".lower()
    return {re.sub(r"\d+$", "", token) for token in re.split(r"[\s_\-]+", raw) if token}


def split_ass_text(text: str) -> tuple[list[str], list[dict[str, Any]], list[int]]:
    """Remove tags while recording exact tag anchors and visual boundaries."""
    pieces: list[str] = []
    anchors: list[dict[str, Any]] = []
    breaks: list[int] = []
    current: list[str] = []
    visible_offset = 0
    for token in TOKEN_RE.split(text):
        if not token:
            continue
        if token == r"\N":
            pieces.append("".join(current))
            current = []
            breaks.append(len(pieces) - 1)
            continue
        if token.startswith("{") and token.endswith("}"):
            anchors.append({"position": visible_offset, "tag": token})
            continue
        current.append(token)
        visible_offset += len(token)
    pieces.append("".join(current))
    return pieces, anchors, breaks


def _visible(text: str) -> str:
    return TAG_RE.sub("", text).replace(r"\N", "\n")


def looks_romanized_token(token: str, english_dictionary: set[str] | None = None) -> bool:
    """Conservative signal for a romanized base immediately before a gloss."""
    word = token.strip().lower()
    dictionary = english_dictionary or set(ENGLISH_COMMON)
    if not re.fullmatch(r"[a-z][a-z'’-]{2,}", word) or word in dictionary:
        return False
    if word.isupper() or len(set(word)) <= 2:
        return False
    vowels = sum(char in "aeiou" for char in word)
    doubled_consonant = bool(re.search(r"([bcdfghjklmnpqrstvwxyz])\1", word))
    return vowels >= 2 and (word.endswith(tuple("aeiou")) or doubled_consonant)


def extract_romanization_gloss(text: str, english_dictionary: set[str] | None = None) -> tuple[str, str] | None:
    """Extract `RomanizedBase [English gloss]` without anime-specific names."""
    plain = TAG_RE.sub("", text).replace(r"\N", " ").strip()
    match = ROMANIZATION_GLOSS_RE.fullmatch(plain)
    if not match:
        return None
    base, gloss = match.group("base"), match.group("gloss").strip()
    if not looks_romanized_token(base, english_dictionary) or not gloss:
        return None
    return base, gloss


def probable_romaji(text: str, english_dictionary: set[str] | None = None) -> tuple[bool, float, str]:
    """Return a high-confidence *possible* romaji/song signal.

    The detector deliberately errs toward preservation.  It combines unknown
    English-vocabulary ratio, Japanese particles/suffixes and syllable-like
    patterns; a single unknown word is never enough.  No anime/title-specific
    rule is used here.
    """
    plain = TAG_RE.sub("", text).replace(r"\N", " ").strip()
    words = [word.lower() for word in WORD_RE.findall(plain)]
    if len(words) < 3 or not plain or not all(word.isascii() for word in words):
        return False, 0.0, "insufficient ASCII word sequence"
    if any(any(char.isdigit() for char in word) for word in words):
        return False, 0.0, "digits indicate non-lyrical content"
    dictionary = english_dictionary or set(ENGLISH_COMMON)
    english_hits = sum(word in dictionary for word in words)
    unknown_ratio = 1.0 - english_hits / max(1, len(words))
    marker_hits = sum(word in ROMAJI_MARKERS for word in words)
    syllable_hit = bool(ROMAJI_COMMON_SYLLABLES.search(" ".join(words)))
    # Plausible English syntax is a strong veto.  This keeps ordinary dialogue
    # with one or two proper names on the normal translation path.
    english_syntax = sum(word in {"the", "you", "are", "have", "has", "will", "should", "we", "i", "to", "in", "of", "and", "but", "this", "that"} for word in words)
    if english_syntax >= 2 and marker_hits < 2:
        return False, 0.0, "English syntax dominates"
    if unknown_ratio < 0.55 or marker_hits < 1 or not syllable_hit:
        return False, 0.0, "weak romaji evidence"
    confidence = min(0.99, 0.45 + 0.30 * min(1.0, unknown_ratio) + 0.15 * min(1.0, marker_hits / 3) + (0.10 if syllable_hit else 0.0))
    return True, round(confidence, 4), f"unknown_ratio={unknown_ratio:.2f}; markers={marker_hits}; syllable_pattern={syllable_hit}"


def classify_event(line: Any, clean_text: str, profile: dict[str, Any], english_dictionary: set[str] | None = None) -> tuple[str, str, float]:
    if not clean_text.strip():
        return "TECHNICAL_OR_EMPTY", "sem texto linguístico", 1.0
    romanization_gloss = extract_romanization_gloss(clean_text, english_dictionary)
    if romanization_gloss:
        return "ROMANIZATION_GLOSS", "base romanizada preservável + gloss delimitado", 0.96
    romaji, confidence, reason = probable_romaji(clean_text, english_dictionary)
    if romaji:
        return "ROMAJI_PRESERVED", "possível romaji/letra japonesa; preservação conservadora: " + reason, confidence
    tokens = _style_tokens(line)
    if KARAOKE_RE.search(_field(line, "text", "")) or tokens & SONG_WORDS:
        return "MUSIC_OR_KARAOKE", "karaoke/Style/Effect musical", 1.0
    if BRACKET_RE.fullmatch(clean_text.strip()):
        return "SDH", "indicação sonora entre colchetes", 0.95
    style_info = profile.get("style_hypotheses", {}).get(str(_field(line, "style", "")), {})
    confidence = float(style_info.get("screen_confidence", 0.0))
    if confidence >= 0.7 and POSITION_RE.search(_field(line, "text", "")):
        return "SIGN_OR_SCREEN_TEXT", "perfil desta release + posicionamento", confidence
    if not re.search(r"\w", clean_text, re.UNICODE):
        return "TECHNICAL_OR_EMPTY", "sem palavras", 0.9
    if style_info.get("probable_function") == "SIGN_OR_SCREEN_TEXT" and confidence >= 0.85:
        return "SIGN_OR_SCREEN_TEXT", "hipótese forte do perfil da release", confidence
    if clean_text.lstrip().startswith(("[", "(", "♪")):
        return "UNKNOWN", "padrão ambíguo", 0.4
    return "MAIN_DIALOGUE", "padrão narrativo padrão", 0.7


def _overlap(a: Event, b: Event, tolerance: int = 120) -> bool:
    return a.start <= b.end + tolerance and b.start <= a.end + tolerance


def analyze_profile(subs: pysubs2.SSAFile, events: list[Event]) -> dict[str, Any]:
    style_counts = Counter(event.style for event in events)
    style_stats: dict[str, dict[str, Any]] = {}
    for style, count in style_counts.items():
        items = [event for event in events if event.style == style]
        positioned = sum(event.has_positioning for event in items)
        quoted = sum(('"' in event.visible_text or "[" in event.visible_text) for event in items)
        simultaneous = sum(any(_overlap(event, other) and event.id != other.id for other in events) for event in items)
        confidence = min(1.0, positioned / max(1, count) * 0.55 + quoted / max(1, count) * 0.25 + simultaneous / max(1, count) * 0.2)
        style_stats[style] = {
            "count": count,
            "positioning_rate": round(positioned / max(1, count), 4),
            "quoted_or_bracket_rate": round(quoted / max(1, count), 4),
            "simultaneous_rate": round(simultaneous / max(1, count), 4),
            "screen_confidence": round(confidence, 4),
            "probable_function": "SIGN_OR_SCREEN_TEXT" if confidence >= 0.7 else "UNKNOWN_OR_DIALOGUE",
        }
    fingerprint_data = {
        "styles": dict(sorted(style_counts.items())),
        "name_filled": sum(bool(event.name and event.name.lower() != "unknown") for event in events),
        "effect_filled": sum(bool(event.effect) for event in events),
        "karaoke": sum(bool(KARAOKE_RE.search(event.original_text)) for event in events),
        "positioning": sum(event.has_positioning for event in events),
        "line_breaks": sum(bool(event.line_break_boundaries) for event in events),
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()[:16]
    return {
        "fingerprint": fingerprint,
        "events": len(events),
        "styles": dict(style_counts),
        "name_filled_rate": round(fingerprint_data["name_filled"] / max(1, len(events)), 4),
        "effect_filled_rate": round(fingerprint_data["effect_filled"] / max(1, len(events)), 4),
        "karaoke_events": fingerprint_data["karaoke"],
        "positioning_events": fingerprint_data["positioning"],
        "line_break_events": fingerprint_data["line_breaks"],
        "overlap_pairs": sum(1 for i, left in enumerate(events) for right in events[i + 1:] if _overlap(left, right)),
        "style_hypotheses": style_stats,
        "universal_style_rule_warning": "Style functions are release-specific hypotheses, never universal rules.",
    }


def _format_signature(event: Event) -> tuple[str, ...]:
    """Formatting signature used only for conservative musical propagation."""
    return tuple(sorted(set(tag.lower() for tag in TAG_RE.findall(event.original_text) if "\\pos" not in tag.lower() and "\\an" not in tag.lower())))


def _short_romaji_candidate(event: Event, english_dictionary: set[str]) -> bool:
    text = event.clean_text.strip()
    words = [word.lower() for word in WORD_RE.findall(text)]
    if not words or len(text) > 80 or any(word in {"the", "you", "are", "have", "will", "should", "this", "that"} for word in words):
        return False
    unknown_ratio = sum(word not in english_dictionary for word in words) / max(1, len(words))
    marker = any(word in ROMAJI_MARKERS for word in words)
    syllable = bool(ROMAJI_COMMON_SYLLABLES.search(" ".join(words)))
    token_signal = any(looks_romanized_token(word, english_dictionary) for word in words)
    vowels = sum(char.lower() in "aeiou" for char in text if char.isascii())
    letters = sum(char.isalpha() for char in text)
    return unknown_ratio >= 0.55 and (marker or syllable or token_signal) and vowels >= max(2, letters * 0.22)


def propagate_romaji_blocks(events: list[Event], english_dictionary: set[str], block_gap_ms: int = 12000) -> None:
    """Propagate ROMAJI_PRESERVED only across a compatible musical sequence."""
    seeds = [event for event in events if event.classification == "ROMAJI_PRESERVED"]
    for event in events:
        if event.classification != "MAIN_DIALOGUE" or not _short_romaji_candidate(event, english_dictionary):
            continue
        compatible = [seed for seed in seeds if seed.style == event.style and _format_signature(seed) == _format_signature(event) and abs(seed.start - event.start) <= block_gap_ms]
        if not compatible:
            continue
        nearest = min(compatible, key=lambda seed: abs(seed.original_index - event.original_index))
        # A strong scene gap or positioning mismatch keeps the candidate on
        # the normal dialogue path. This protects short English dialogue near
        # an opening/ending.
        if event.has_positioning or abs(nearest.end - event.start) > block_gap_ms and abs(event.end - nearest.start) > block_gap_ms:
            continue
        event.classification = "ROMAJI_PRESERVED"
        event.block_classification = "ROMAJI_PRESERVED"
        event.block_confidence = round(min(0.9, nearest.screen_confidence * 0.82), 4)
        event.screen_confidence = event.block_confidence
        event.classification_reason = f"herança conservadora do bloco musical; seed={nearest.id}; distância_ms={abs(nearest.start - event.start)}"


def _song_event_signals(event: Event) -> dict[str, Any]:
    """Collect release-independent evidence that an event belongs to lyrics."""
    raw_meta = f"{event.style} {event.name} {event.effect}".lower()
    metadata_hits = sorted({token for token in SONG_WORDS if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", raw_meta)})
    tags = TAG_RE.findall(event.original_text or "")
    italic = any(re.search(r"\\i(?:1|0)", tag, re.I) for tag in tags)
    karaoke = bool(KARAOKE_RE.search(event.original_text or ""))
    words = WORD_RE.findall(event.clean_text)
    speaker = bool(SPEAKER_RE.match(event.clean_text))
    positioned = event.has_positioning
    return {
        "metadata_hits": metadata_hits,
        "italic": italic,
        "karaoke": karaoke,
        "word_count": len(words),
        "speaker_like": speaker,
        "positioned": positioned,
        "format_signature": _format_signature(event),
    }


def _song_block_fingerprint(events: list[Event]) -> str:
    payload = {
        "text": [" ".join(WORD_RE.findall(event.clean_text.lower())) for event in events],
        "format": [_format_signature(event) for event in events],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def classify_song_blocks(events: list[Event]) -> dict[str, Any]:
    """Classify only coherent lyric blocks, never a line by position alone.

    High confidence requires several independent signals: contiguous timing,
    compatible formatting, a sufficiently long verse block, and either an
    explicit song/karaoke marker or the combination of italic presentation and
    a temporal/romanized lyric hint.  Short or conflicting blocks are marked
    ``SONG_AMBIGUOUS`` and remain on the normal translation path.
    """
    if not events:
        return {"blocks": [], "ambiguous_events": [], "recurrent_song_blocks": []}
    ordered = sorted(events, key=lambda item: item.original_index)
    max_end = max(event.end for event in ordered)
    blocks: list[list[Event]] = []
    current: list[Event] = []
    previous: Event | None = None
    previous_signal: dict[str, Any] | None = None
    for event in ordered:
        signal = _song_event_signals(event)
        contiguous = (
            previous is not None
            and previous.style == event.style
            and previous_signal is not None
            and previous_signal["format_signature"] == signal["format_signature"]
            and event.start - previous.end <= 12000
            and not signal["positioned"]
            and not signal["speaker_like"]
        )
        if current and contiguous:
            current.append(event)
        else:
            if current:
                blocks.append(current)
            current = [event] if not signal["positioned"] and not signal["speaker_like"] else []
        previous = event
        previous_signal = signal
    if current:
        blocks.append(current)

    descriptions: list[dict[str, Any]] = []
    ambiguous_events: list[int] = []
    recurrent_fingerprints: list[str] = []
    for block_index, block in enumerate(blocks):
        signals = [_song_event_signals(event) for event in block]
        if not block:
            continue
        explicit = sum(bool(signal["metadata_hits"] or signal["karaoke"]) for signal in signals)
        italic_rate = sum(signal["italic"] for signal in signals) / len(block)
        romanized_rate = sum(event.classification in {"ROMAJI_PRESERVED", "ROMANIZATION_GLOSS"} for event in block) / len(block)
        temporal_hint = block[0].start <= 120000 or max_end - block[-1].end <= 180000
        # Six-line italic teasers and narration blocks occur near episode
        # endings in real releases.  Requiring eight coherent lines for the
        # non-explicit path avoids treating those teasers as songs.
        coherent = len(block) >= 8 and italic_rate >= 0.80 and all(not signal["positioned"] for signal in signals)
        high = bool(
            explicit >= max(1, len(block) // 2)
            or (coherent and (temporal_hint or romanized_rate >= 0.40))
        )
        if high and len(block) >= 3:
            fingerprint = _song_block_fingerprint(block)
            evidence = []
            if explicit:
                evidence.append(f"metadata_or_karaoke_events={explicit}/{len(block)}")
            if italic_rate:
                evidence.append(f"italic_rate={italic_rate:.2f}")
            if temporal_hint:
                evidence.append("opening_or_ending_temporal_hint")
            if romanized_rate:
                evidence.append(f"romanized_rate={romanized_rate:.2f}")
            block_id = f"song-{block_index}-{fingerprint}"
            for event in block:
                event.classification = "SONG_LYRICS_PRESERVED"
                event.block_classification = "SONG_HIGH_CONFIDENCE"
                event.block_confidence = 0.98 if explicit else 0.92
                event.song_block_id = block_id
                event.song_confidence = event.block_confidence
                event.song_evidence = evidence
                event.classification_reason = "bloco lírico coerente; " + "; ".join(evidence)
            if len(block) >= 8:
                recurrent_fingerprints.append(fingerprint)
            descriptions.append({
                "block_id": block_id,
                "event_ids": [event.id for event in block],
                "fingerprint": fingerprint,
                "confidence": round(block[0].song_confidence, 4),
                "evidence": evidence,
                "temporal_start_ms": block[0].start,
                "temporal_end_ms": block[-1].end,
            })
        elif len(block) >= 3 and italic_rate >= 0.50:
            block_id = f"ambiguous-song-{block_index}"
            evidence = [f"italic_rate={italic_rate:.2f}", f"block_events={len(block)}"]
            # Keep the original dialogue/romaji classification.  The block is
            # observable as ambiguous, but it must not become a preservation
            # escape hatch for English dialogue.
            ambiguous_events.extend(event.id for event in block)
    return {
        "blocks": descriptions,
        "ambiguous_events": ambiguous_events,
        "recurrent_song_blocks": sorted(set(recurrent_fingerprints)),
    }


def load_events(path: Path, glossary: dict[str, str], profile: dict[str, Any] | None = None) -> tuple[pysubs2.SSAFile, list[Event], dict[str, Any]]:
    subs = pysubs2.load(str(path))
    english_dictionary = load_english_dictionary("/usr/share/dict/american-english")
    preliminary: list[Event] = []
    for index, line in enumerate(subs):
        pieces, anchors, breaks = split_ass_text(_field(line, "text", ""))
        clean_pieces = [piece.strip() for piece in pieces]
        clean_text = " ".join(piece for piece in clean_pieces if piece)
        segments = [CleanSegment(i, pieces[i], clean_pieces[i], bool(SPEAKER_RE.match(clean_pieces[i]))) for i in range(len(pieces))]
        romanized_gloss = extract_romanization_gloss(clean_text, english_dictionary)
        preliminary.append(Event(
            id=index, original_index=index, layer=int(_field(line, "layer", 0)),
            start=int(_field(line, "start", 0)), end=int(_field(line, "end", 0)),
            style=str(_field(line, "style", "")), name=str(_field(line, "name", "")),
            marginl=int(_field(line, "marginl", 0)), marginr=int(_field(line, "marginr", 0)),
            marginv=int(_field(line, "marginv", 0)), effect=str(_field(line, "effect", "")),
            original_text=str(_field(line, "text", "")), visible_text=_visible(_field(line, "text", "")),
            clean_text=clean_text, segments=segments, tag_anchors=anchors,
            line_break_boundaries=breaks, has_positioning=bool(POSITION_RE.search(_field(line, "text", ""))),
            romanization_base=romanized_gloss[0] if romanized_gloss else "",
            romanization_gloss=romanized_gloss[1] if romanized_gloss else "",
        ))
    profile = profile or analyze_profile(subs, preliminary)
    # Classify with the actual source fields while keeping pysubs2 objects out
    # of the serializable Event representation.
    for event, line in zip(preliminary, subs):
        event.classification, event.classification_reason, event.screen_confidence = classify_event(line, event.clean_text, profile, english_dictionary)
    propagate_romaji_blocks(preliminary, english_dictionary)
    song_info = classify_song_blocks(preliminary)
    profile["song_blocks"] = song_info["blocks"]
    profile["song_ambiguous_events"] = song_info["ambiguous_events"]
    profile["recurrent_song_blocks"] = song_info["recurrent_song_blocks"]
    profile["song_policy"] = "SONG_HIGH_CONFIDENCE preserve source; SONG_AMBIGUOUS remains translatable"
    return subs, preliminary, profile


def deterministic_sdh(text: str) -> tuple[str | None, str]:
    matches = list(BRACKET_RE.finditer(text))
    if not matches or BRACKET_RE.sub("", text).strip():
        return None, ""
    result = text
    names: list[str] = []
    for match in matches:
        key = " ".join(match.group(1).lower().split())
        if key not in SDH_RULES:
            return None, ""
        result = result.replace(match.group(0), f"[{SDH_RULES[key]}]")
        names.append(key)
    return result, ",".join(names)


def build_sign_groups(events: list[Event], enabled: bool = True) -> list[Unit]:
    if not enabled:
        return [Unit(f"event-{event.id}", [event]) for event in events]
    units: list[Unit] = []
    used: set[int] = set()
    for event in events:
        if event.id in used:
            continue
        if event.classification != "SIGN_OR_SCREEN_TEXT" or not event.has_positioning:
            units.append(Unit(f"event-{event.id}", [event]))
            used.add(event.id)
            continue
        group = [candidate for candidate in events if candidate.id not in used and candidate.classification == "SIGN_OR_SCREEN_TEXT" and candidate.has_positioning and _overlap(event, candidate, 180)]
        if len(group) > 1:
            group_id = f"sign-{event.start}-{event.end}"
            for item in group:
                item.sign_group = group_id
                used.add(item.id)
            units.append(Unit(group_id, group, True))
        else:
            units.append(Unit(f"event-{event.id}", [event]))
            used.add(event.id)
    return units


def choose_context(events: list[Event], target: Event, config: Config) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: event.original_index)
    pos = next(index for index, event in enumerate(ordered) if event.id == target.id)
    previous: list[dict[str, Any]] = []
    following: list[dict[str, Any]] = []
    budget = 0
    for direction, count, output in ((-1, config.context_before, previous), (1, config.context_after, following)):
        for step in range(1, count + 1):
            index = pos + direction * step
            if index < 0 or index >= len(ordered):
                break
            candidate = ordered[index]
            gap = abs(target.start - candidate.end) if direction < 0 else abs(candidate.start - target.end)
            if step > 1 and gap > config.scene_gap_ms:
                break
            item = {"id": candidate.id, "text": candidate.clean_text, "classification": candidate.classification}
            cost = estimate_tokens(json.dumps(item, ensure_ascii=False))
            if budget + cost > config.context_budget_tokens:
                break
            output.append(item)
            budget += cost
    context = {"previous": list(reversed(previous)), "next": following}
    if config.series_title:
        context["series_title"] = config.series_title
    if config.episode_title:
        context["episode_title"] = config.episode_title
    if target.name and target.name.lower() != "unknown":
        context["character"] = target.name
    encoded = json.dumps(context, ensure_ascii=False)
    if len(encoded) > config.context_max_chars:
        context = {"context_truncated": encoded[:config.context_max_chars]}
    return context


def semantic_domain_hint(target_text: str, context: dict[str, Any]) -> str:
    """Add a small, generic domain cue when surrounding text is unambiguous.

    This is not a phrase dictionary and does not prescribe a fixed translation;
    it helps Qwen distinguish common senses (phone, food, game/draw) using the
    same context a human reviewer sees.
    """
    pieces = [target_text]
    for side in ("previous", "next"):
        pieces.extend(item.get("text", "") for item in context.get(side, []) if isinstance(item, dict))
    joined = " ".join(pieces).lower()
    hints: list[str] = []
    if any(term in joined for term in ("telephone", "phone", "call", "voicemail", "beep", "take your call", "leave your message", "reached")):
        hints.append("Domínio contextual: telefonia. Em uma saudação de atendimento, formule reached como contato/ligação com a empresa (por exemplo, 'você ligou para' ou equivalente), não como alcançar fisicamente.")
    if any(term in joined for term in ("spicy", "curry", "bread", "vendor", "sold out", "eat", "food", "hot!!", "hot!")):
        hints.append("Domínio contextual: comida. Para uma reação a alimento picante, interprete hot como ardência/picância quando esse for o sentido, não apenas temperatura.")
    if any(term in joined for term in ("draw", "re-draw", "winner", "who gets", "your turn", "decide")):
        hints.append("Domínio contextual: jogo ou sorteio. Use o sentido da ação estabelecida pela cena, sem assumir que a palavra indica desenho.")
    if any(term in joined for term in ("stinkin", "no way", "accept this", "way i can")):
        hints.append("Domínio contextual: recusa coloquial e intensificador. Transmita a força pragmática da fala; não traduza o intensificador como adjetivo literal.")
    if any(term in joined for term in ("had enough", "super-hot", "can't take", "cannot take", "no longer")):
        hints.append("Domínio contextual: limite/saturação diante de comida ou situação. Interprete have had enough como já chega/não aguento mais quando esse for o sentido; não transforme isso em quantidade consumida.")
    return " ".join(hints)


def is_multi_speaker(event: Event) -> bool:
    """Only explicit speaker-like segments use the segmented contract."""
    parts = [segment for segment in event.segments if segment.clean_text]
    return len(parts) > 1 and sum(segment.speaker_like for segment in parts) >= 2


def unit_schema_kind(units: list[Unit]) -> str:
    kinds = {"segmented" if is_multi_speaker(event) else "normal" for unit in units for event in unit.events}
    if len(kinds) != 1:
        raise ValueError(f"lote mistura schemas: {sorted(kinds)}")
    return next(iter(kinds))


def _schema(units: list[Unit]) -> dict[str, Any]:
    kind = unit_schema_kind(units)
    ids = [event.id for unit in units for event in unit.events]
    segment_schema = {
        "type": "object",
        "properties": {"segment_id": {"type": "integer"}, "text": {"type": "string"}},
        "required": ["segment_id", "text"], "additionalProperties": False,
    }
    if kind == "normal":
        item = {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["id", "text"], "additionalProperties": False,
        }
    else:
        item = {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "segments": {"type": "array", "items": segment_schema}},
            "required": ["id", "segments"], "additionalProperties": False,
        }
    return {"type": "object", "properties": {"translations": {"type": "array", "items": item, "minItems": len(ids), "maxItems": len(ids)}}, "required": ["translations"], "additionalProperties": False}


def validate_response(value: Any, expected: dict[int, Event]) -> tuple[dict[int, dict[str, Any]], list[str]]:
    issues: list[str] = []
    if not isinstance(value, dict) or set(value) != {"translations"} or not isinstance(value["translations"], list):
        return {}, ["root/translation array inválido"]
    found: dict[int, dict[str, Any]] = {}
    for item in value["translations"]:
        if not isinstance(item, dict) or "id" not in item:
            issues.append("propriedades de item inválidas")
            continue
        item_id = item.get("id")
        if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id not in expected:
            issues.append(f"id inválido/desconhecido: {item_id}")
            continue
        expected_kind = "segmented" if is_multi_speaker(expected[item_id]) else "normal"
        required_keys = {"id", "segments"} if expected_kind == "segmented" else {"id", "text"}
        if set(item) != required_keys:
            issues.append(f"schema {expected_kind} inválido: {item_id}")
            continue
        if item_id in found:
            issues.append(f"id duplicado: {item_id}")
            continue
        if expected_kind == "normal" and not isinstance(item["text"], str):
            issues.append(f"text não string: {item_id}")
            continue
        if expected_kind == "segmented":
            if not isinstance(item["segments"], list):
                issues.append(f"segments não array: {item_id}")
                continue
            good = True
            seen: set[int] = set()
            for segment in item["segments"]:
                if not isinstance(segment, dict) or set(segment) != {"segment_id", "text"} or not isinstance(segment.get("segment_id"), int) or not isinstance(segment.get("text"), str) or segment["segment_id"] in seen:
                    good = False
                    break
                seen.add(segment["segment_id"])
            if not good:
                issues.append(f"segments inválidos: {item_id}")
                continue
        text_values = [item.get("text", "")] if expected_kind == "normal" else [segment["text"] for segment in item["segments"]]
        if any("§T" in text or "§N" in text or "{" in text or "}" in text or r"\N" in text for text in text_values):
            issues.append(f"estrutura/placeholder enviado na resposta: {item_id}")
            continue
        found[item_id] = item
    missing = sorted(set(expected) - set(found))
    if missing:
        issues.append(f"ids ausentes: {missing}")
    if len(value["translations"]) != len(expected):
        issues.append(f"quantidade inesperada: {len(value['translations'])}")
    return found, issues


def load_english_dictionary(path: str | Path | None) -> set[str]:
    """Load a local word list without making network or production changes."""
    words = set(ENGLISH_COMMON)
    if not path:
        return words
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
            word = line.strip().lower()
            if re.fullmatch(r"[a-z]{4,}(?:'[a-z]+)?", word):
                words.add(word)
    except (OSError, UnicodeError):
        # The optional dictionary is an aid, never a reason to fail a run.
        pass
    return words


def linguistic_flags(source: str, output: str, context: dict[str, Any], english_dictionary: set[str] | None = None, protected_terms: set[str] | None = None) -> list[str]:
    flags: list[str] = []
    source_norm = " ".join(source.lower().split())
    output_norm = " ".join(output.lower().split())
    if source_norm == output_norm:
        flags.append("POSSIBLE_UNTRANSLATED_OUTPUT")
    source_words = set(word.lower() for word in WORD_RE.findall(source))
    english_output = [word.lower() for word in WORD_RE.findall(output) if word.lower() in ENGLISH_COMMON]
    if len(source_words) > 1 and len(english_output) >= 3 and output_norm != source_norm:
        flags.append("POSSIBLE_UNTRANSLATED_OUTPUT")
    dictionary = english_dictionary or set(ENGLISH_COMMON)
    protected = {term.lower() for term in (protected_terms or set()) if term}
    protected_words = {word for term in protected for word in WORD_RE.findall(term)}
    output_words = {item.lower() for item in WORD_RE.findall(output)}
    unchanged_translatable = {
        word.lower() for word in WORD_RE.findall(source)
        if len(word) >= 5 and word.isascii() and word.lower() in dictionary
        and word.lower() not in PORTUGUESE_IDENTICAL
        # A capitalised token occurring in a quoted/sign position is usually a
        # proper name or romanisation; keep it for human audit instead of a
        # speculative retry.
        and not (word[0].isupper() and (source.strip().startswith('"') or source.strip().startswith("'")))
        and word.lower() in output_words
        and word.lower() not in protected
        and word.lower() not in protected_words
    }
    if unchanged_translatable and output_norm != source_norm:
        flags.append("UNTRANSLATED_TRANSLATABLE_TOKEN")
    if len(source_norm) < 80 and len(output_norm) > max(160, len(source_norm) * 2.8):
        flags.append("UNSUPPORTED_ADDITION")
    context_words = set()
    for side in ("previous", "next"):
        for item in context.get(side, []):
            context_words.update(word.lower() for word in WORD_RE.findall(item.get("text", "")) if len(word) > 4)
    output_words = set(word.lower() for word in WORD_RE.findall(output))
    source_words_long = {word.lower() for word in WORD_RE.findall(source) if len(word) > 4}
    if len((context_words & output_words) - source_words_long) >= 3:
        flags.append("CONTEXT_LEAK")
    # A short target that suddenly expands into several clauses is a strong
    # generic leak signal even when the target/context languages differ.
    source_count = sum(len(word) > 1 for word in WORD_RE.findall(source))
    output_count = sum(len(word) > 1 for word in WORD_RE.findall(output))
    if source_count <= 4 and output_count >= max(6, source_count * 3) and context_words:
        flags.append("CONTEXT_LEAK")
    return sorted(set(flags))


def likely_english_sentence_source(event: Event, english_dictionary: set[str] | None = None) -> bool:
    """Return whether a preservation-class event is sentence-shaped English.

    This guard is used before the deterministic ROMAJI_PRESERVED short-circuit
    so an uncertain block profile cannot silently preserve an English clause.
    """
    source_norm = " ".join(event.clean_text.lower().replace(r"\N", " ").split())
    words = [word.lower() for word in WORD_RE.findall(source_norm)]
    if len(words) < 4:
        return False
    sentence_signal = bool(re.search(r"[.!?]", source_norm) or "'" in source_norm or any(char.isdigit() for char in source_norm))
    common_hits = sum(word in ENGLISH_COMMON for word in words)
    # A subtitle clause may be split before punctuation (or end with a comma
    # in the next event).  With the optional dictionary absent, requiring
    # punctuation allowed ordinary English clauses with several function words
    # to look like romaji.  Three common/function-word hits in a sufficiently
    # long ASCII sequence are a generic syntax signal; short names and lyric
    # fragments remain below this threshold.
    syntax_hits = sum(word in {
        "a", "an", "and", "are", "be", "but", "for", "from", "have", "has",
        "he", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or",
        "that", "the", "this", "to", "was", "we", "were", "what", "when",
        "where", "who", "will", "with", "you", "your",
    } for word in words)
    return (sentence_signal and common_hits >= 1) or (len(words) >= 5 and (common_hits >= 2 or syntax_hits >= 2))


def high_confidence_untranslated_dialogue(event: Event, source: str, output: str, english_dictionary: set[str] | None = None, protected_terms: set[str] | None = None) -> bool:
    """Identify a sentence that is still substantially English after a call.

    This is intentionally stricter than ``POSSIBLE_UNTRANSLATED_OUTPUT``.  It
    only applies to linguistic dialogue/narration, requires a multi-word
    sentence-shaped target, and exempts the deterministic preservation
    classes and short names/codes.  The detector is release-independent and
    never treats one English-looking token as a failure.
    """
    source_norm = " ".join(source.lower().replace(r"\N", " ").split())
    output_norm = " ".join(output.lower().replace(r"\N", " ").split())
    if not source_norm or not output_norm or source_norm == output_norm and len(source_norm.split()) < 4:
        return False
    protected = {term.lower().strip() for term in (protected_terms or set()) if term}
    if source_norm in protected or output_norm in protected:
        return False
    source_words = [word.lower() for word in WORD_RE.findall(source_norm)]
    output_words = [word.lower() for word in WORD_RE.findall(output_norm)]
    if len(source_words) < 4 or len(output_words) < 4:
        return False
    dictionary = english_dictionary or set(ENGLISH_COMMON)
    source_hits = sum(word in dictionary for word in source_words)
    output_hits = sum(word in dictionary for word in output_words)
    common_hits = sum(word in ENGLISH_COMMON for word in source_words)
    sentence_signal = bool(re.search(r"[.!?]", source_norm) or "'" in source_norm or any(char.isdigit() for char in source_norm))
    # ROMAJI_PRESERVED is deterministic only when the text actually looks like
    # a lyric/romanisation.  A sentence-shaped English sequence must take
    # precedence over an uncertain block classification (notably when the
    # optional system dictionary is absent in the production image).
    if event.classification == "ROMAJI_PRESERVED":
        if not (sentence_signal and common_hits >= 1):
            return False
    elif event.classification not in {"MAIN_DIALOGUE", "NARRATION_OR_THOUGHT", "SDH", "SONG_AMBIGUOUS"}:
        return False
    if source_norm == output_norm:
        # Full unchanged sentence: either broad dictionary coverage or several
        # English function words is required.  This catches clauses such as
        # “Switching to search mode” without catching names/short codes.
        if sentence_signal and len(source_words) >= 4 and common_hits >= 1:
            return True
        return (source_hits >= max(3, len(source_words) // 2) and common_hits >= 1) or common_hits >= 2
    overlap = len(set(source_words) & set(output_words)) / max(1, len(set(source_words)))
    output_ratio = output_hits / max(1, len(output_words))
    # A mostly-English response that retains most source words is a residual
    # sentence, even when a model prepended a small Portuguese fragment.
    return output_ratio >= 0.65 and overlap >= 0.60 and not re.search(r"[áàâãéêíóôõúç]", output_norm)


def _word_char(char: str) -> bool:
    return bool(char) and (char == "_" or char.isalnum() or unicodedata.category(char).startswith("M"))


def inline_tag_split_word(text: str) -> bool:
    r"""Detect a tag sequence introduced between two word characters.

    Existing source formatting is allowed to contain an intentional inline tag
    (Kana-{\i1}chan{\i0}); callers compare source and candidate and only
    reject a *new* split introduced during reconstruction.
    """
    matches = list(TAG_RE.finditer(text))
    for match in matches:
        # Tags can occur consecutively immediately after a structural ASS
        # break (`\\N{\\i0}{\\i1}next line`).  Looking only at the raw text
        # makes the second tag appear to sit between the `N` of `\\N` and the
        # first character of the next line.  Normalize tags first and treat
        # `\\N` as a hard boundary before applying the lexical test.
        before_without_tags = TAG_RE.sub("", text[:match.start()])
        after_without_tags = TAG_RE.sub("", text[match.end():])
        if re.search(r"\\N\s*$", before_without_tags) or re.match(r"^\s*\\N", after_without_tags):
            continue
        left = before_without_tags.replace(r"\N", "")
        right = after_without_tags.replace(r"\N", "")
        if left and right and _word_char(left[-1]) and _word_char(right[0]):
            return True
    return False


def inline_tag_counts(text: str) -> Counter[str]:
    return Counter(TAG_RE.findall(text or ""))


def _visible_length(text: str) -> int:
    return len(TAG_RE.sub("", text).replace(r"\N", ""))


def _raw_index_for_visible_offset(text: str, offset: int) -> int:
    """Map an offset in visible text to a raw ASS insertion position."""
    offset = max(0, offset)
    visible = 0
    index = 0
    while index < len(text):
        tag = TAG_RE.match(text, index)
        if tag:
            index = tag.end()
            continue
        if text.startswith(r"\N", index):
            index += 2
            continue
        if visible >= offset:
            return index
        visible += 1
        index += 1
    return len(text)


def _safe_inline_boundary(text: str, desired: int) -> int | None:
    """Snap a visible offset to the nearest lexical boundary.

    Boundaries are between words, punctuation, whitespace, or at the ends. A
    tag is never inserted in the middle of a translated Unicode word.
    """
    plain = TAG_RE.sub("", text).replace(r"\N", "")
    if not plain:
        return 0
    desired = max(0, min(len(plain), desired))
    if desired == 0 or desired == len(plain) or not (_word_char(plain[desired - 1]) and _word_char(plain[desired])):
        return desired
    candidates = [index for index in range(len(plain) + 1) if index == 0 or index == len(plain) or not (_word_char(plain[index - 1]) and _word_char(plain[index]))]
    if not candidates:
        return None
    # Prefer the closest boundary. Ties go to the right, which generally
    # preserves the source tag's trailing styling on the following phrase.
    return min(candidates, key=lambda index: (abs(index - desired), 0 if index >= desired else 1))


def validate_inline_tags(source: str, candidate: str) -> list[str]:
    """Validate tag count/order and reject newly introduced lexical splits."""
    flags: list[str] = []
    source_counts = inline_tag_counts(source)
    candidate_counts = inline_tag_counts(candidate)
    if source_counts != candidate_counts:
        if any(candidate_counts[tag] > source_counts[tag] for tag in candidate_counts):
            flags.append("ASS_INLINE_TAG_DUPLICATION")
        flags.append("ASS_TAG_MISMATCH")
    # A source-intended split is tolerated; only reconstruction-introduced
    # splits are invalid. This is what keeps Kana-chan compatible.
    if inline_tag_split_word(candidate) and not inline_tag_split_word(source):
        flags.append("ASS_INLINE_TAG_SPLIT_WORD")
        flags.append("ASS_INLINE_TAG_ANCHOR_FAILURE")
    return sorted(set(flags))


def line_break_inside_word(text: str) -> bool:
    for match in re.finditer(r"\\N", text):
        index = match.start()
        left_raw = text[:index]
        right_raw = text[index + 2:]
        # ASS style tags commonly sit immediately on both sides of a visual
        # break (`{\\i0}\\N{\\i1}`); those tags are an explicit boundary.
        if re.search(r"\{[^}]*\}\s*$", left_raw) or re.match(r"\s*\{[^}]*\}", right_raw):
            continue
        plain = TAG_RE.sub("", text)
        plain_index = len(TAG_RE.sub("", text[:index]))
        if plain_index > 0 and plain_index + 2 < len(plain) and _word_char(plain[plain_index - 1]) and _word_char(plain[plain_index + 2]):
            # ASS visual breaks are often emitted without spaces.  Treat a
            # break between two complete lexical runs as a boundary, while
            # rejecting the short fragments produced by an actual split
            # (``refletin\\Ndo``, ``vi\\Nda``, etc.).  The source-aware
            # reconstruction still preserves the original line intent.
            left_match = re.search(r"[\\wÀ-ÿ]+$", plain[:plain_index])
            right_match = re.match(r"[\\wÀ-ÿ]+", plain[plain_index + 2:])
            left_len = len(left_match.group(0)) if left_match else 0
            right_len = len(right_match.group(0)) if right_match else 0
            if left_len >= 4 and right_len >= 4:
                continue
            return True
    return False


def _break_candidates(text: str) -> list[int]:
    candidates: list[int] = []
    for index in range(1, len(text)):
        raw_left = text[index - 1]
        raw_right = text[index]
        left = text[:index].rstrip()
        right = text[index:].lstrip()
        if not left or not right:
            continue
        # A whitespace at the candidate boundary is a valid visual break even
        # though the stripped words on either side are both alphanumeric.
        if _word_char(raw_left) and _word_char(raw_right):
            continue
        candidates.append(index)
    return candidates


def _choose_visual_break(text: str, ratio: float) -> int | None:
    candidates = _break_candidates(text)
    if not candidates:
        return None
    ideal = max(1, min(len(text) - 1, round(ratio * len(text))))

    def score(index: int) -> tuple[float, float]:
        left = text[:index].rstrip()
        right = text[index:].lstrip()
        left_words = re.findall(r"[\wÀ-ÿ]+", left, re.UNICODE)
        penalty = 0.0
        if len(left) < 4 or len(right) < 4:
            penalty += 8.0
        if left_words and left_words[-1].lower() in VISUAL_STOPWORDS:
            penalty += 18.0
        # Prefer an actual whitespace/punctuation boundary over a hard split.
        if index < len(text) and text[index - 1].isspace():
            penalty -= 2.0
        return penalty, abs(index - ideal)

    return min(candidates, key=score)


def delimiter_flags(source: str, output: str) -> list[str]:
    """Conservative balance check for visible delimiters, not apostrophes."""
    flags: list[str] = []
    source_plain = TAG_RE.sub("", source)
    output_plain = TAG_RE.sub("", output)
    for opening, closing, name in (("\"", "\"", "QUOTES"), ("(", ")", "DELIMITERS"), ("[", "]", "DELIMITERS")):
        source_count = source_plain.count(opening)
        if source_count == 0:
            continue
        # A sign can span multiple simultaneous ASS events (for example an
        # opening quote in one event and its closing quote in the next). In
        # that case event-local validation must not reject a balanced group.
        if opening == closing and source_count % 2 != 0:
            continue
        output_count = output_plain.count(opening)
        if opening == closing:
            balanced = output_count % 2 == 0
            enough = output_count >= source_count
        else:
            balanced = output_plain.count(opening) == output_plain.count(closing)
            enough = output_plain.count(opening) >= source_plain.count(opening) and output_plain.count(closing) >= source_plain.count(closing)
        if not balanced:
            flags.append("UNBALANCED_QUOTES" if name == "QUOTES" else "UNBALANCED_DELIMITERS")
        elif not enough:
            flags.append("DELIMITER_COUNT_MISMATCH")
    return sorted(set(flags))


_QUOTED_APOSTROPHE_RE = re.compile(r"(?<![\wÀ-ÿ])'([^'\r\n]+)'(?![\wÀ-ÿ])")


def normalize_delimiter_style(source: str, output: str) -> tuple[str, bool]:
    """Restore source quote delimiters when the model changed only their style.

    The model may translate a source span written as ``"wait and see"`` to a
    Portuguese span surrounded by ASCII apostrophes.  Those apostrophes are
    punctuation around a phrase, not lexical apostrophes (``d'água``).  When
    the source has balanced ASCII quote pairs, the candidate has no ASCII
    quotes, and it has exactly the same number of safe apostrophe pairs, map
    the pairs back to the source delimiter.  Ambiguous or unbalanced cases are
    left untouched and remain critical under ``delimiter_flags``.
    """
    source_plain = TAG_RE.sub("", source or "")
    output_plain = TAG_RE.sub("", output or "")
    source_quotes = source_plain.count('"')
    output_quotes = output_plain.count('"')
    if source_quotes == 0 or source_quotes % 2 or output_quotes != 0:
        return output, False
    expected_pairs = source_quotes // 2
    matches = list(_QUOTED_APOSTROPHE_RE.finditer(output))
    if len(matches) != expected_pairs:
        return output, False
    normalized = output
    for match in reversed(matches):
        normalized = normalized[:match.start()] + '"' + match.group(1) + '"' + normalized[match.end():]
    return normalized, normalized != output


def replace_romanization_gloss(original_text: str, translated_gloss: str) -> str:
    """Replace only the first visible bracket gloss; preserve base/tags exactly."""
    replacement = "[" + translated_gloss.strip() + "]"
    return re.sub(r"\[[^\[\]]+\]", replacement, original_text, count=1)


def idiomatic_flags(source: str, output: str) -> list[str]:
    """Detect one generic, high-confidence literal reading of `had enough`."""
    source_norm = source.lower()
    output_norm = output.lower()
    if re.search(r"\bhad\s+enough\b", source_norm) and re.search(r"\b(?:tomei|comi|consumi|ingeri|cheguei\s+(?:do|ao|no)\s+limite)\b", output_norm):
        return ["IDIOMATIC_LITERAL_RISK"]
    return []


def normalize_idiomatic_output(source: str, output: str, context: dict[str, Any] | None = None) -> tuple[str, bool]:
    """Safely normalize a literal `had enough` construction after model output.

    This is a generic idiom guard, not a phrase dictionary: it changes only
    high-confidence Portuguese quantity/limit calques when the English source
    explicitly contains `had enough`.
    """
    normalized = output
    changed = False
    source_norm = source.lower()
    if re.search(r"\bhad\s+enough\b", source_norm):
        pattern = re.compile(r"\b(?:já\s+)?(?:tomei\s+o\s+suficiente|comi\s+demais|consumi\s+o\s+suficiente|ingeri\s+o\s+suficiente|cheguei\s+(?:do|ao|no)\s+limite)(?:\s+(?:de|com))?", re.I)
        normalized, count = pattern.subn("Já chega de", normalized, count=1)
        changed = changed or bool(count)
    context_text = " ".join([source] + [item.get("text", "") for side in ("previous", "next") for item in (context or {}).get(side, []) if isinstance(item, dict)]).lower()
    food_context = any(term in context_text for term in ("spicy", "curry", "food", "hot!!", "super-hot", "vendor", "eat"))
    if food_context and re.search(r"\bhot\b", source_norm) and re.search(r"\bardente\b", normalized.lower()):
        normalized = re.sub(r"\bardente\b", "picante", normalized, flags=re.I)
        changed = True
    if re.search(r"\bre-?draw\b", source_norm) and re.search(r"\bnova tentativa\b", normalized.lower()):
        normalized = re.sub(r"\buma\s+nova tentativa\b", "um novo sorteio", normalized, flags=re.I)
        normalized = re.sub(r"\bnova tentativa\b", "novo sorteio", normalized, flags=re.I)
        changed = True
    if changed:
        normalized = re.sub(r"\s+([,.!?])", r"\1", normalized)
    return normalized, changed


def content_flags(event: Event, output: str, context: dict[str, Any], english_dictionary: set[str] | None = None, protected_terms: set[str] | None = None) -> list[str]:
    linguistic = TAG_RE.sub("", output).replace(r"\N", " ").strip()
    flags: list[str] = []
    source_has_content = bool(re.search(r"[\wÀ-ÿ]", event.clean_text, re.UNICODE))
    output_has_content = bool(re.search(r"[\wÀ-ÿ]", linguistic, re.UNICODE))
    if source_has_content and (not linguistic or not output_has_content):
        flags.append("CONTENT_LOSS")
    flags.extend(flag for flag in delimiter_flags(event.clean_text, linguistic) if flag not in flags)
    flags.extend(flag for flag in linguistic_flags(event.clean_text, linguistic, context, english_dictionary, protected_terms) if flag not in flags)
    if high_confidence_untranslated_dialogue(event, event.clean_text, linguistic, english_dictionary, protected_terms):
        flags.append("UNTRANSLATED_DIALOGUE")
    flags.extend(flag for flag in idiomatic_flags(event.clean_text, linguistic) if flag not in flags)
    for flag in validate_inline_tags(event.original_text, output):
        if flag not in flags:
            flags.append(flag)
    # A segmented/multi-speaker event intentionally keeps \N between speaker
    # segments; it is not a visual reflow candidate.
    if not is_multi_speaker(event) and line_break_inside_word(output):
        flags.append("LINE_BREAK_INSIDE_WORD")
    return sorted(set(flags))


def reconstruct_event(event: Event, response: dict[str, Any]) -> tuple[str, list[str]]:
    flags: list[str] = []
    segmented_response = "segments" in response
    if segmented_response:
        if not is_multi_speaker(event):
            return event.original_text, ["SEGMENT_ID_MISMATCH"]
        values = sorted(response["segments"], key=lambda item: item["segment_id"])
        expected = [segment.segment_id for segment in event.segments if segment.clean_text]
        actual = [item["segment_id"] for item in values]
        if expected != actual:
            return event.original_text, ["SEGMENT_ID_MISMATCH"]
        translated_segments = [item["text"].strip() for item in values]
        base = r"\N".join(translated_segments)
    else:
        base = response.get("text", "").strip()
        if not isinstance(base, str):
            return event.original_text, ["TEXT_NOT_STRING"]
        if r"\N" in base:
            return event.original_text, ["MODEL_EMITTED_STRUCTURAL_TOKEN"]
        if event.classification == "ROMANIZATION_GLOSS":
            if any(token in base for token in ("§T", "§N", "§G", "{", "}", "[", "]")) or not base or base[:1] in {"\"", "'"} or base[-1:] in {"\"", "'"}:
                return event.original_text, ["CONTENT_LOSS" if not base else "STRUCTURAL_CONTENT_IN_MODEL_OUTPUT"]
            rebuilt = replace_romanization_gloss(event.original_text, base)
            flags.extend(flag for flag in validate_inline_tags(event.original_text, rebuilt) if flag not in flags)
            if rebuilt.count("[") != event.original_text.count("["):
                flags.append("DELIMITER_COUNT_MISMATCH")
            return rebuilt, flags
    if not segmented_response and len(event.segments) > 1 and all(segment.clean_text for segment in event.segments):
            # The whole event was translated as one semantic unit.  Visual
            # line breaks are placed later by proportional source boundaries.
            source_len = max(1, len(event.clean_text))
            for boundary in sorted(event.line_break_boundaries, reverse=True):
                # split_ass_text records the index of the segment before the
                # visual break.  Reinsert after that segment, never at the
                # beginning of the translated event.
                before = sum(len(segment.clean_text) for segment in event.segments[:boundary + 1]) + boundary
                ratio = before / source_len
                index = _choose_visual_break(base, ratio)
                if index is None:
                    return event.original_text, ["LINE_BREAK_INSIDE_WORD"]
                left = base[:index].rstrip()
                right = base[index:].lstrip()
                # Keep a boundary marker on the left side.  Without it,
                # `word\\Nword` is indistinguishable from a real intra-word
                # split during the post-reconstruction validator.
                base = left + " " + r"\N" + right
    base, delimiter_style_normalized = normalize_delimiter_style(event.clean_text, base)
    if delimiter_style_normalized:
        flags.append("DELIMITER_STYLE_NORMALIZED")
    if any(token in base for token in ("§T", "§N", "§G")) or "{" in base or "}" in base:
        flags.append("STRUCTURAL_CONTENT_IN_MODEL_OUTPUT")
        return event.original_text, flags
    # Reinsert original ASS tags using visible offsets and lexical-safe
    # boundaries.  Raw proportional indices are unsafe: translated words have
    # different lengths and would yield e.g. `veze{\\i1}{\\i0}s`.
    source_visible_len = max(1, _visible_length(event.clean_text))
    for anchor in sorted(event.tag_anchors, key=lambda item: item["position"], reverse=True):
        desired = _visible_length(base) if anchor["position"] >= source_visible_len else min(_visible_length(base), max(0, anchor["position"]))
        safe = _safe_inline_boundary(base, desired)
        if safe is None:
            flags.append("ASS_INLINE_TAG_ANCHOR_FAILURE")
            continue
        raw_position = _raw_index_for_visible_offset(base, safe)
        base = base[:raw_position] + anchor["tag"] + base[raw_position:]
    if base.count(r"\N") != len(event.line_break_boundaries):
        flags.append("LINE_BREAK_COUNT_MISMATCH")
    if not segmented_response and not is_multi_speaker(event) and line_break_inside_word(base):
        flags.append("LINE_BREAK_INSIDE_WORD")
    if sorted(TAG_RE.findall(base)) != sorted(TAG_RE.findall(event.original_text)):
        flags.append("ASS_TAG_MISMATCH")
    flags.extend(flag for flag in validate_inline_tags(event.original_text, base) if flag not in flags)
    return base, flags


class Client:
    def __init__(self, config: Config, calls: list[dict[str, Any]], glossary: dict[str, str] | None = None, model: str | None = None):
        self.config = config
        self.calls = calls
        self.glossary = glossary or {}
        self.model = model or config.model

    def finalize_request_payload(self, payload: dict[str, Any], units: list[Unit], phase: str) -> dict[str, Any]:
        """Return the exact transport payload before the durable boundary.

        Adapter specializations override this hook instead of intercepting
        ``requests.post`` after the request has already been captured.
        """
        return payload

    def _post_transport(self, payload: dict[str, Any]) -> tuple[Any, bytes]:
        """POST via the configured transport provider, or the raw Ollama URL
        when no provider is set.  Returns (response, raw_body_bytes)."""
        transport = getattr(self.config, "transport", None)
        if transport is not None:
            request_body = transport.build_request(payload)
            request_bytes = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
            response = requests.post(transport.endpoint(), data=request_bytes,
                                     headers=transport.headers(),
                                     timeout=self.config.timeout_seconds)
            raw_body = bytes(getattr(response, "content", b""))
            return response, raw_body
        response = requests.post(self.config.ollama_url, json=payload,
                                 timeout=self.config.timeout_seconds)
        raw_body = getattr(response, "content", None)
        if raw_body is None:
            text_body = getattr(response, "text", None)
            if isinstance(text_body, str):
                raw_body = text_body.encode("utf-8")
            else:
                raw_body = json.dumps(response.json(), ensure_ascii=False).encode("utf-8")
        return response, bytes(raw_body)

    def _extract_content(self, raw_body: bytes, body: dict[str, Any]) -> str:
        """Assistant text from the response: provider-aware when configured."""
        transport = getattr(self.config, "transport", None)
        if transport is not None:
            return transport.extract_content(raw_body)
        return (body.get("message") or {}).get("content")

    def call(self, units: list[Unit], events: dict[int, Event], contexts: dict[int, dict[str, Any]], simplified: bool = False, phase: str = "main") -> tuple[dict[int, dict[str, Any]], list[str], dict[str, Any]]:
        ids = [event.id for unit in units for event in unit.events]
        durable_context = getattr(self.config, "durable_context", None)
        # Preserve the legacy reservation order exactly.  V2.3.8 opts into
        # per-call durability below, where the persistent ledger reserves
        # immediately before request materialization.
        if not durable_context and self.config.operation_budget is not None:
            self.config.operation_budget.reserve(
                model_tag=self.model,
                model_digest=getattr(self.config, "model_digest", None),
                phase="V226_QWEN",
            )
        schema = _schema(units)
        schema_kind = unit_schema_kind(units)
        targets: list[dict[str, Any]] = []
        for unit in units:
            for event in unit.events:
                if is_multi_speaker(event):
                    item: dict[str, Any] = {"id": event.id, "kind": event.classification}
                    item["segments"] = [{"segment_id": segment.segment_id, "text": segment.clean_text} for segment in event.segments if segment.clean_text]
                else:
                    target_text = event.romanization_gloss if event.classification == "ROMANIZATION_GLOSS" else event.clean_text
                    item = {"id": event.id, "text": target_text, "kind": event.classification}
                item["context"] = {} if simplified else contexts[event.id]
                if event.name and event.name.lower() != "unknown":
                    item["character"] = event.name
                domain_hint = semantic_domain_hint(event.clean_text, contexts[event.id])
                if domain_hint:
                    item["semantic_hint"] = domain_hint
                targets.append(item)
        block_text = json.dumps(targets, ensure_ascii=False).lower()
        relevant_glossary = {
            source: target for source, target in self.glossary.items()
            if source.lower() in block_text
        }
        # A glossary is experimental and local to this block; never send an
        # unbounded global glossary with every request.
        relevant_glossary = dict(list(relevant_glossary.items())[:12])
        retry_instruction = ""
        if phase.startswith("retry"):
            retry_instruction = (
                " Esta é uma tentativa de correção. Reavalie somente o TARGET; "
                "não copie contexto. A resposta anterior permaneceu total ou parcialmente em inglês; "
                "traduza a fala para português brasileiro natural agora. Preserve somente nomes próprios, "
                "siglas, códigos, romanizações e termos protegidos quando forem realmente não traduzíveis. "
                "Não devolva a frase inglesa integral. Preserve romanizações/onomatopeias quando não houver tradução confiável."
            )
        if phase == "retry_simplified":
            # A compact second retry avoids the model copying a difficult
            # English target from the long contextual instruction.  It keeps
            # the same strict schema and event ID, but removes all optional
            # prose/context so the request is unambiguously a translation.
            prompt = (
                "Você é um tradutor de legendas. Traduza o TARGET para português brasileiro natural. "
                "A resposta anterior permaneceu em inglês; não repita o texto-fonte. "
                "Preserve somente nomes próprios, siglas, códigos e termos explicitamente protegidos. "
                "Responda somente JSON válido, exatamente com o id solicitado.\n\n"
                f"TARGET: {json.dumps(targets, ensure_ascii=False)}\n"
                f"GLOSSARY: {json.dumps(relevant_glossary, ensure_ascii=False)}\n"
                f"SCHEMA: {json.dumps(schema, ensure_ascii=False)}"
            )
        else:
            prompt = (
            "Traduza somente os itens TARGET de inglês para português do Brasil. "
            "Cada evento completo é uma unidade semântica. CONTEXT existe apenas para "
            "entender sujeito, tom, referência e continuidade; nunca copie ou traduza "
            "conteúdo do contexto para outro id. Não produza explicações. Não produza "
            "tags ASS, placeholders, timestamps ou quebras técnicas. Para eventos com "
            "segments, devolva os mesmos segment_id juntos, sem criar ou remover segmentos. "
            "Nomes, romanizações, onomatopeias e termos do glossário devem ser preservados quando não houver tradução confiável. "
            "Quando o TARGET for um gloss de romanização entre colchetes, traduza somente o gloss e preserve a base romanizada fora dele; não inclua os colchetes na resposta. "
            "Nesse caso, o gloss curto também deve ser traduzido (por exemplo, Attack para Ataque e Warm para Quente), enquanto a base permanece idêntica. "
            "Nunca devolva o inglês integral quando houver tradução possível. "
            "Use o contexto para traduzir o sentido idiomático em português brasileiro natural, inclusive em telefonia, comida, jogos e sorteios; evite tradução palavra por palavra quando a situação exigir uma expressão natural. "
            "Expressões coloquiais, intensificadores e idioms devem transmitir função pragmática e sentido, não uma tradução lexical palavra por palavra. "
            "Quando have had enough expressar limite ou saturação, use uma formulação idiomática de limite em PT-BR (já chega de.../não aguento mais...), e não uma quantidade consumida ou uma construção literal como 'cheguei do limite'. "
            "Preserve aspas, parênteses e colchetes visíveis e não descarte o conteúdo delimitado. "
            f"{retry_instruction}"
            "Responda somente JSON estrito.\n\n"
            f"TARGET: {json.dumps(targets, ensure_ascii=False)}\n"
            f"GLOSSARY: {json.dumps(relevant_glossary, ensure_ascii=False)}\n"
            f"SCHEMA: {json.dumps(schema, ensure_ascii=False)}"
            )
        call_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "model": self.model, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "format": schema, "think": self.config.think,
            "options": {"temperature": self.config.temperature, "num_ctx": self.config.num_ctx, "num_predict": self.config.num_predict},
            "keep_alive": self.config.keep_alive,
        }
        payload = self.finalize_request_payload(payload, units, phase)
        started = time.perf_counter()
        observation: dict[str, Any] = {
            "call_id": call_id, "model": self.model, "event_ids": ids,
            "target_count": len(ids), "context_event_count": sum(len(contexts[event.id].get("previous", [])) + len(contexts[event.id].get("next", [])) for unit in units for event in unit.events),
            "prompt_chars": len(prompt), "prompt_tokens_estimate": estimate_tokens(prompt),
            "glossary_keys_count": len(relevant_glossary),
            "schema_kind": schema_kind,
            "simplified": simplified, "phase": phase,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "config": {"think": self.config.think, "temperature": self.config.temperature, "num_ctx": self.config.num_ctx, "num_predict": self.config.num_predict, "keep_alive": self.config.keep_alive},
            # Explicit deltas make fresh POSTs distinguishable from durable
            # replays.  The transport path below overrides these defaults.
            "physical_transport": False,
            "model_call_delta": 0,
            "provider_call_delta": 0,
            "durable_response_delta": 0,
            "retry_delta": 0,
        }
        durable_call = None
        if durable_context:
            from v238_per_call_durability import DurableV226Call
            attempt_contract = getattr(self.config, "durable_attempt_contract", None)
            if not isinstance(attempt_contract, dict):
                from v238_per_call_durability import DurableCallError
                raise DurableCallError("V238_DURABLE_ATTEMPT_CONTRACT_REQUIRED")
            membership = hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode("utf-8")).hexdigest()
            durable_call = DurableV226Call(
                durable_context,
                payload,
                {
                    "phase": phase,
                    "attempt_type": attempt_contract.get("attempt_type"),
                    "logical_batch_id": attempt_contract.get("logical_batch_id"),
                    "attempt_ordinal": attempt_contract.get("attempt_ordinal"),
                    "parent_attempt_id": attempt_contract.get("parent_attempt_id"),
                    "batch_index": attempt_contract.get("batch_index"),
                    "unit_ids": ids,
                    "event_count": len(ids),
                    "unit_membership_sha256": membership,
                    "model": self.model,
                    "model_digest": getattr(self.config, "model_digest", None),
                    "timeout_seconds": self.config.timeout_seconds,
                    "configuration": {"think": self.config.think, "temperature": self.config.temperature, "num_ctx": self.config.num_ctx, "num_predict": self.config.num_predict, "keep_alive": self.config.keep_alive,
                                       "operation_retry_transport_cap": self.config.operation_retry_transport_cap,
                                       "per_event_retry_transport_cap": self.config.per_event_retry_transport_cap},
                },
                operation_budget=self.config.operation_budget,
            )
            observation["call_id"] = durable_call.request_id
            observation["durable_request_id"] = durable_call.request_id
            observation["logical_call_id"] = durable_call.logical_call_id
            observation["physical_attempt_id"] = durable_call.physical_attempt_id
            observation["durable_attempt_type"] = durable_call.metadata.get("attempt_type")
            observation["durable_attempt_ordinal"] = durable_call.metadata.get("attempt_ordinal")
            observation["parent_attempt_id"] = durable_call.metadata.get("parent_attempt_id")
            observation["unit_membership_sha256"] = membership
        try:
            body = None
            derived_body = False
            subset_reused = False
            subset_data = None
            if durable_call is not None:
                state = durable_call.prepare_request()
                observation["durable_state_before_transport"] = state.get("state")
                if state.get("state") in {"VALID_SUBSET_RECORDED", "RETRIES_PENDING", "RETRY_RESPONSE_DURABLE"}:
                    subset_data = durable_call.load_valid_subset()
                    body = {"translations": list(subset_data["subset"].get("rows", []))}
                    subset_reused = True
                    observation.update({"reused_valid_subset": True, "reused_durable_response": True, "physical_transport": False,
                                        "valid_subset_ids": subset_data["subset"].get("valid_ids", []),
                                        "unresolved_ids": subset_data["subset"].get("unresolved_ids", []),
                                        "model_call_delta": 0, "retry_delta": 0})
                elif state.get("state") in {"PARSED_VALID", "PARSED_INVALID", "DERIVED_NORMALIZATION_RECORDED", "DERIVED_PARSED_VALID", "RESPONSE_DURABLE"}:
                    derived_body = state.get("state") in {"DERIVED_NORMALIZATION_RECORDED", "DERIVED_PARSED_VALID"}
                    raw_body = durable_call.load_derived_response() if state.get("state") in {"DERIVED_NORMALIZATION_RECORDED", "DERIVED_PARSED_VALID"} else durable_call.load_raw()
                    body = json.loads(raw_body.decode("utf-8"))
                    observation["reused_durable_response"] = True
                    observation["physical_transport"] = False
                    if state.get("state") == "DERIVED_NORMALIZATION_RECORDED":
                        observation["derived_state_before_validation"] = "DERIVED_NORMALIZATION_RECORDED"
                elif state.get("state") in {"CANCELLED_CONFIRMED", "TRANSPORT_FAILED_CONFIRMED", "TRANSPORT_OUTCOME_UNKNOWN", "RESERVATION_FAILED"}:
                    from v238_per_call_durability import DurableCallError
                    raise DurableCallError("V238_DURABLE_CALL_TERMINAL_RECONCILIATION_REQUIRED:" + str(state.get("state")))
                else:
                    # The interprocess claim spans the state check, the POST,
                    # exact response capture, and durable state promotion.  A
                    # waiter either reuses the captured response or stops; it
                    # can never become a second transport owner.
                    with durable_call.exclusive_transport_claim() as transport_owner:
                        if not transport_owner:
                            raw_body = durable_call.load_raw()
                            body = json.loads(raw_body.decode("utf-8"))
                            observation["reused_durable_response"] = True
                            observation["physical_transport"] = False
                        else:
                            durable_call.begin_transport()
                            observation["durable_state"] = "TRANSPORT_IN_PROGRESS"
                            durable_call._fault("before_post")
                            durable_call._assert_retry_boundary()
                            response, raw_body = self._post_transport(payload)
                            durable_call._fault("after_response_received_before_capture")
                            status_code = int(response.status_code)
                            observation["http_status"] = status_code
                            durable_call.record_response(raw_body, status_code=status_code)
                            observation.update({
                                "physical_transport": True,
                                "model_call_delta": 1,
                                "provider_call_delta": 1,
                                "durable_response_delta": 1,
                            })
                            if status_code != 200:
                                # The response/status is already durable.  Do
                                # not let the legacy Runner treat this as an
                                # ordinary retryable exception.
                                from v238_per_call_durability import DurableCallError
                                raise DurableCallError(f"V238_DURABLE_HTTP_STATUS:{status_code}")
                            body = json.loads(bytes(raw_body).decode("utf-8"))
            else:
                response, raw_body = self._post_transport(payload)
                observation["http_status"] = response.status_code
                observation.update({
                    "physical_transport": True,
                    "model_call_delta": 1,
                    "provider_call_delta": 1,
                    "durable_response_delta": 1,
                })
                response.raise_for_status()
                body = json.loads(raw_body.decode("utf-8"))
            content = json.dumps(body, ensure_ascii=False) if (derived_body or subset_reused) else self._extract_content(raw_body, body)
            observation["content_chars"] = len(content) if isinstance(content, str) else None
            observation["content_sha256"] = hashlib.sha256(content.encode()).hexdigest() if isinstance(content, str) else None
            if self.config.diagnostic_capture and isinstance(content, str):
                # Laboratory diagnostics retain the model response so an
                # invalid candidate can be inspected.  Do not persist the
                # complete request prompt: it is unnecessary for the tag
                # diagnosis and may contain contextual user content.
                observation["response_content"] = content
                observation["response_message_keys"] = sorted((body.get("message") or {}).keys())
            observation["has_thinking"] = bool((body.get("message") or {}).get("thinking"))
            for key in ("prompt_eval_count", "eval_count", "done_reason"):
                if key in body: observation[key] = body[key]
            for key in ("prompt_eval_duration", "eval_duration", "load_duration", "total_duration"):
                if key in body: observation[key + "_seconds"] = body[key] / 1_000_000_000
            # Ollama's length stop is a durable, known non-success.  The raw
            # envelope remains immutable and auditable, but its content must
            # never enter strict parsing, normalization, valid-subset
            # recovery, or retry planning.  Mark the durable attempt first so
            # restart cannot reinterpret it as an ordinary parse exception.
            if body.get("done_reason") == "length":
                physical_transport = observation.get("physical_transport") is True
                observation.update({
                    "raw_schema_status": "INVALID_TRUNCATED",
                    "raw_noncompliance_class": "OUTPUT_TRUNCATED",
                    "normalization_attempted": False,
                    "normalization_status": "NOT_APPLICABLE_OUTPUT_TRUNCATED",
                    "derived_schema_status": "NOT_CREATED",
                    "structural_issues": ["OUTPUT_TRUNCATED"],
                    "retry_delta": 0,
                    # A fresh POST is an actual provider call; a durable
                    # restart/replay is not.  Keep those facts distinct even
                    # though both paths terminate on the same raw envelope.
                    "physical_transport": physical_transport,
                    "model_call_delta": 1 if physical_transport else 0,
                    "provider_call_delta": 1 if physical_transport else 0,
                    "durable_response_delta": 1 if physical_transport else 0,
                })
                if durable_call is not None:
                    durable_call.mark_parsed(valid=False, error="OUTPUT_TRUNCATED")
                    observation["durable_state"] = durable_call.state()
                from v238_per_call_durability import DurableCallError
                raise DurableCallError("V238_OUTPUT_TRUNCATED")
            if durable_call is not None:
                durable_call._fault("before_parse")
            value = body if (derived_body or subset_reused) else (strict_json(content) if self.config.strict_json else json.loads(content))
            normalization_policy = str((durable_context or {}).get("response_normalization_policy") or "") if durable_context else ""
            normalized = False
            if normalization_policy in {"V238_ITEM_EXTRA_PROPERTY_PROJECTION_V1", "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V2_MULTI_KIND", "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V3_OPAQUE_CONTEXT_METADATA"} and not subset_reused:
                from v238_response_normalization import NormalizationRejected, POLICY, POLICY_V2, POLICY_V3, project_extra_property_response, project_multi_kind_response, project_opaque_context_response
                if durable_call is None:
                    raise NormalizationRejected("V238_NORMALIZATION_REQUIRES_DURABLE_CONTEXT")
                found_before, issues_before = validate_response(value, events)
                if derived_body:
                    # A recorded derivation is never projected again.  Its
                    # persisted bytes are only revalidated and promoted by
                    # the common validator below; any defect is fail-closed.
                    observation.update({
                        "raw_schema_status": "INVALID_EXTRA_PROPERTY",
                        "raw_noncompliance_class": "MODEL_RESPONSE_EXTRA_PROPERTY_VIOLATING_STRICT_SCHEMA",
                        "normalization_attempted": False,
                        "normalization_policy": normalization_policy,
                        "normalization_status": "DERIVED_REVALIDATION_PENDING",
                        "offending_item_count": 1,
                        "dropped_property_count": "RECORDED_IN_DERIVED_MANIFEST",
                        "derived_schema_status": "REVALIDATION_PENDING",
                        "derived_response_sha": hashlib.sha256((json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest() if isinstance(value, dict) else None,
                        "retry_delta": 0,
                    })
                elif issues_before:
                    # Preserve the raw response's invalid classification before
                    # attempting the explicitly authorized projection.  A
                    # fresh live response reaches RESPONSE_DURABLE directly;
                    # the derived seam is only legal after this forward state.
                    durable_call.mark_parsed(valid=False, error="; ".join(issues_before))
                    observation["raw_durable_state"] = "PARSED_INVALID"
                    expected_item_keys = {
                        int(event_id): ("id", "segments") if is_multi_speaker(event) else ("id", "text")
                        for event_id, event in events.items()
                    }
                    if normalization_policy == POLICY_V2:
                        projector = project_multi_kind_response
                    elif normalization_policy == POLICY_V3:
                        projector = project_opaque_context_response
                    else:
                        projector = project_extra_property_response
                    projected, audit = projector(value, sorted(events), expected_item_keys=expected_item_keys)
                    projected_found, projected_issues = validate_response(projected, events)
                    if projected_issues:
                        raise NormalizationRejected("V238_DERIVED_VALIDATION_FAILED:" + ";".join(projected_issues))
                    value = projected
                    recorded_state = durable_call.record_derived_normalization(value, audit)
                    derived_state = durable_call.mark_derived_parsed_valid()
                    observation.update({
                        "raw_schema_status": audit["raw_schema_status"],
                        "raw_noncompliance_class": "MODEL_RESPONSE_EXTRA_PROPERTY_VIOLATING_STRICT_SCHEMA",
                        "normalization_attempted": True,
                        "normalization_policy": normalization_policy,
                        "normalization_status": "DERIVED_PARSED_VALID",
                        "offending_item_count": audit["offending_item_count"],
                        "dropped_property_count": audit["extra_property_count"],
                        "derived_schema_status": audit["derived_schema_status"],
                        "derived_response_sha": audit["normalized_response_sha256"],
                        "retry_delta": 0,
                        "derived_state": derived_state.get("state"),
                        "derived_recorded_state": recorded_state.get("state"),
                    })
                    normalized = True
                else:
                    observation.update({
                        "raw_schema_status": "VALID",
                        "raw_noncompliance_class": "NONE",
                        "normalization_attempted": False,
                        "normalization_policy": normalization_policy,
                        "normalization_status": "NOT_NEEDED",
                        "offending_item_count": 0,
                        "dropped_property_count": 0,
                        "derived_schema_status": "NOT_CREATED",
                        "derived_response_sha": None,
                        "retry_delta": 0,
                    })
            found, issues = validate_response(value, events)
            if normalized and issues:
                raise NormalizationRejected("V238_DERIVED_VALIDATION_FAILED:" + ";".join(issues))
            observation["json_valid"] = True
            observation["structural_issues"] = issues
            if durable_call is not None:
                durable_state = durable_call.state()
                if durable_state in {"DERIVED_NORMALIZATION_RECORDED", "DERIVED_PARSED_VALID"}:
                    if issues:
                        raise NormalizationRejected("V238_DERIVED_VALIDATION_FAILED:" + ";".join(issues))
                    if durable_state == "DERIVED_NORMALIZATION_RECORDED":
                        promoted = durable_call.mark_derived_parsed_valid()
                        observation["derived_state"] = promoted.get("state")
                    if derived_body:
                        observation["normalization_status"] = "DERIVED_PARSED_VALID_REUSED"
                        observation["derived_schema_status"] = "VALID_AFTER_DETERMINISTIC_PROJECTION"
                        observation["reused_durable_response"] = True
                        observation["physical_transport"] = False
                        observation["model_call_delta"] = 0
                        observation["retry_delta"] = 0
                elif durable_state not in {"DERIVED_PARSED_VALID", "VALID_SUBSET_RECORDED", "RETRIES_PENDING", "RETRY_RESPONSE_DURABLE", "BATCH_COMPLETE"}:
                    durable_call.mark_parsed(valid=not issues, error="; ".join(issues) if issues else None)
                    if issues and found and str((durable_context or {}).get("valid_subset_policy") or "") == "V238_VALID_SUBSET_V1":
                        durable_call.record_valid_subset(found, sorted(events), sorted(set(events) - set(found)))
                observation["durable_state"] = durable_call.state()
            observation["elapsed_client_seconds"] = time.perf_counter() - started
            observation["duration_seconds"] = observation["elapsed_client_seconds"]
            self.calls.append(observation)
            return found, issues, observation
        except Exception as exc:
            if durable_call is not None:
                try:
                    if durable_call.state() == "TRANSPORT_IN_PROGRESS":
                        durable_call.mark_unknown(exc)
                        observation["durable_state"] = "TRANSPORT_OUTCOME_UNKNOWN"
                    elif durable_call.state() == "RESPONSE_DURABLE" and not getattr(exc, "durability_stop", False):
                        durable_call.mark_parsed(valid=False, error=f"{type(exc).__name__}: {exc}")
                        observation["durable_state"] = "PARSED_INVALID"
                except Exception as durability_exc:
                    observation["durability_error"] = f"{type(durability_exc).__name__}: {durability_exc}"
            observation.update({"json_valid": False, "error_type": type(exc).__name__, "error": str(exc)[:500], "elapsed_client_seconds": time.perf_counter() - started})
            observation["duration_seconds"] = observation["elapsed_client_seconds"]
            self.calls.append(observation)
            raise


class Runner:
    def __init__(self, events: list[Event], profile: dict[str, Any], config: Config, glossary: dict[str, str]):
        self.events = events
        self.profile = profile
        self.config = config
        self.glossary = glossary
        self.english_dictionary = load_english_dictionary(config.english_dictionary_path)
        self.protected_terms = set(glossary) | set(glossary.values())
        self.by_id = {event.id: event for event in events}
        self.contexts = {event.id: choose_context(events, event, config) for event in events}
        self.calls: list[dict[str, Any]] = []
        self.results = {event.id: Result(event.id) for event in events}
        self.client = Client(config, self.calls, glossary=glossary)
        self.units = build_sign_groups(events, config.enable_sign_grouping)
        self.diagnostic_records: list[dict[str, Any]] = []
        self._diagnostic_phase = "unknown"
        self.retry_budget = RetryBudget(max(0, int(config.retry_budget_calls)))
        self._last_call_id: str | None = None
        self._call_sequence = 0
        self._durable_attempt_ordinals: dict[str, int] = {}

    def _set_model_result(self, event: Event, response: dict[str, Any], model: str) -> bool:
        text, flags = reconstruct_event(event, response)
        text, idiomatic_normalized = normalize_idiomatic_output(event.clean_text, text, self.contexts[event.id])
        if idiomatic_normalized:
            flags.append("IDIOMATIC_LITERAL_NORMALIZED")
        result = self.results[event.id]
        # Flags from a rejected attempt remain in `attempts`; the event-level
        # flags describe the candidate currently being considered.
        # A retry represents a new candidate. Do not carry linguistic flags
        # from a rejected intermediate response into the final event record.
        result.flags = []
        result.flags.extend(flag for flag in flags if flag not in result.flags)
        result.final_text = text
        result.final_segments = response.get("segments")
        result.final_model = model
        if not any(flag in flags for flag in CRITICAL_FLAGS):
            for flag in content_flags(event, text, self.contexts[event.id], self.english_dictionary, self.protected_terms):
                if flag not in result.flags:
                    result.flags.append(flag)
        critical = set(flags) & CRITICAL_FLAGS
        critical.update(set(result.flags) & CRITICAL_FLAGS)
        if self.config.diagnostic_capture and critical:
            self.diagnostic_records.append({
                "event_id": event.id,
                "original_index": event.original_index,
                "phase": self._diagnostic_phase,
                "model": model,
                "original_ass": event.original_text,
                "visible_source": event.visible_text,
                "clean_text": event.clean_text,
                "translation_plain": response.get("text") if isinstance(response.get("text"), str) else None,
                "response_segments": response.get("segments"),
                "candidate_ass": text,
                "original_tags": TAG_RE.findall(event.original_text or ""),
                "original_tag_anchors": event.tag_anchors,
                "candidate_tag_positions": [
                    {"tag": match.group(0), "raw_index": match.start()}
                    for match in TAG_RE.finditer(text or "")
                ],
                "line_breaks": event.line_break_boundaries,
                "classification": event.classification,
                "retry_history": list(self.results[event.id].attempts),
                "flags": sorted(set(flags) | set(result.flags)),
                "validator_reason": "; ".join(sorted(critical)),
            })
        if critical:
            result.status = "pending"
            result.failure_reason = "; ".join(sorted(critical))
            return False
        # POSSIBLE_UNTRANSLATED_OUTPUT intentionally remains an audit flag.
        # Retry only when the generic dictionary-backed detector found a
        # translatable English token, or when a separate high-confidence
        # linguistic validator requires recovery. Names, romanisations and
        # onomatopoeias therefore do not create needless calls.
        retry_flags = {"UNTRANSLATED_TRANSLATABLE_TOKEN", "CONTEXT_LEAK", "UNSUPPORTED_ADDITION"}
        if event.classification == "ROMANIZATION_GLOSS":
            retry_flags.add("POSSIBLE_UNTRANSLATED_OUTPUT")
        result.retry_recommended = any(flag in result.flags for flag in retry_flags)
        result.status = "resolved"
        result.failure_reason = ""
        return True

    def _attempt(self, units: list[Unit], simplified: bool = False, phase: str = "main",
                 parent_call_id: str | None = None, *, attempt_type: str | None = None,
                 logical_batch_id: str | None = None, batch_index: int | None = None) -> tuple[set[int], list[str]]:
        expected = {event.id: event for unit in units for event in unit.events}
        self._diagnostic_phase = phase
        retry_call = phase != "initial"
        retry_depth = 0 if phase == "initial" else (2 if phase == "retry_simplified" else 1)
        reason = "primary_translation" if phase == "initial" else ("batch_isolation" if phase == "split_isolation" else "localized_retry")
        if self.config.diagnostic_hard_stop_calls and len(self.calls) >= self.config.diagnostic_hard_stop_calls:
            return set(), ["LAB_HARD_STOP_CALL_LIMIT"]
        if retry_call and not self.retry_budget.consume(reason, retry_depth):
            return set(), ["RETRY_BUDGET_EXHAUSTED"]
        # A primary batch is a root.  Descendant calls receive their explicit
        # parent from _process_units; falling back to the previous call is
        # useful only for a localized retry chain within one unit.
        effective_parent_call_id = parent_call_id if parent_call_id is not None else (self._last_call_id if retry_call else None)
        previous_attempt_contract = getattr(self.config, "durable_attempt_contract", None)
        if getattr(self.config, "durable_context", None):
            normalized_attempt_type = str(attempt_type or "").upper()
            if normalized_attempt_type not in {"INITIAL", "RETRY"}:
                from v238_per_call_durability import DurableCallError
                raise DurableCallError("V238_DURABLE_ATTEMPT_TYPE_REQUIRED")
            if not logical_batch_id:
                from v238_per_call_durability import DurableCallError
                raise DurableCallError("V238_DURABLE_LOGICAL_BATCH_ID_REQUIRED")
            parent_ordinal = int(getattr(self, "_durable_attempt_ordinals", {}).get(effective_parent_call_id, 0))
            if normalized_attempt_type == "RETRY" and (not effective_parent_call_id or parent_ordinal <= 0):
                from v238_per_call_durability import DurableCallError
                raise DurableCallError("V238_DURABLE_RETRY_PARENT_IDENTITY_REQUIRED")
            self.config.durable_attempt_contract = {
                "attempt_type": normalized_attempt_type,
                "logical_batch_id": str(logical_batch_id),
                "attempt_ordinal": 1 if normalized_attempt_type == "INITIAL" else parent_ordinal + 1,
                "parent_attempt_id": effective_parent_call_id if normalized_attempt_type == "RETRY" else None,
                "batch_index": batch_index,
            }
        try:
            found, issues, observation = self.client.call(units, expected, self.contexts, simplified, phase)
            if self.calls:
                observation = self.calls[-1]
                observation.update({
                    "parent_call_id": effective_parent_call_id,
                    "retry_depth": retry_depth,
                    "episode": self.config.episode_title,
                    "batch_id": f"batch-{self._call_sequence}",
                    "call_type": "PRIMARY_TRANSLATION" if phase == "initial" else ("LOCALIZED_RETRY" if phase.startswith("retry") else "VALIDATION_RETRY"),
                    "attempt": self.retry_budget.consumed if retry_call else 0,
                    "retry_reason": reason,
                    "reason": reason,
                    "validator_trigger": bool(issues),
                    "song_classification_trigger": phase == "song_classification",
                    "residual_english_trigger": "UNTRANSLATED" in " ".join(issues).upper(),
                    "json_schema_trigger": any(token in " ".join(issues).lower() for token in ("json", "schema", "ids", "quantidade")),
                    "structural_trigger": bool(issues),
                    "success": bool(found) and not issues,
                })
                self._last_call_id = observation.get("call_id")
                if observation.get("durable_attempt_ordinal") is not None:
                    self._durable_attempt_ordinals[observation.get("call_id")] = int(observation["durable_attempt_ordinal"])
                self._call_sequence += 1
            valid: set[int] = set()
            for event_id, response in found.items():
                event = expected[event_id]
                if self._set_model_result(event, response, self.config.model):
                    valid.add(event_id)
                else:
                    issues.append(f"reconstrução inválida: {event_id}")
            return valid, issues
        except Exception as exc:
            if getattr(exc, "durability_stop", False):
                raise
            if self.calls:
                observation = self.calls[-1]
                observation.update({
                    "parent_call_id": effective_parent_call_id,
                    "retry_depth": retry_depth,
                    "episode": self.config.episode_title,
                    "batch_id": f"batch-{self._call_sequence}",
                    "call_type": "PRIMARY_TRANSLATION" if phase == "initial" else ("LOCALIZED_RETRY" if phase.startswith("retry") else "VALIDATION_RETRY"),
                    "attempt": self.retry_budget.consumed if retry_call else 0,
                    "retry_reason": reason,
                    "reason": reason,
                    "validator_trigger": True,
                    "song_classification_trigger": phase == "song_classification",
                    "residual_english_trigger": "UNTRANSLATED" in str(exc).upper(),
                    "json_schema_trigger": isinstance(exc, (ValueError, json.JSONDecodeError)) or "json" in str(exc).lower(),
                    "structural_trigger": True,
                    "success": False,
                })
                self._last_call_id = observation.get("call_id")
                self._call_sequence += 1
            return set(), [f"{type(exc).__name__}: {str(exc)[:300]}"]
        finally:
            self.config.durable_attempt_contract = previous_attempt_contract

    def _process_units(self, units: list[Unit], phase: str = "initial", parent_call_id: str | None = None,
                       *, attempt_type: str, logical_batch_id: str, batch_index: int | None = None) -> None:
        if not units:
            return
        ids = [event.id for unit in units for event in unit.events]
        valid, issues = self._attempt(
            units, phase=phase, parent_call_id=parent_call_id,
            attempt_type=attempt_type, logical_batch_id=logical_batch_id, batch_index=batch_index,
        )
        current_call_id = self._last_call_id
        if any(reason in issues for reason in ("RETRY_BUDGET_EXHAUSTED", "LAB_HARD_STOP_CALL_LIMIT")):
            failure = "; ".join(issues)
            for unit in units:
                for event in unit.events:
                    result = self.results[event.id]
                    result.status = "failed"
                    result.failure_reason = failure
            return
        missing = [unit for unit in units if any(event.id not in valid or self.results[event.id].retry_recommended for event in unit.events)]
        if not issues and not missing:
            return
        # A failed batch is isolated before any retry budget is consumed.
        # Counting/recording a retry at every recursive split caused an
        # exponential retry storm when several events in a batch were
        # rejected.  Only an actual Ollama retry below consumes budget.
        if len(missing) > 1:
            midpoint = max(1, len(missing) // 2)
            for label, subset in (("left", missing[:midpoint]), ("right", missing[midpoint:])):
                member_ids = [event.id for unit in subset for event in unit.events]
                member_hash = hashlib.sha256(json.dumps(member_ids, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
                self._process_units(
                    subset, "split_isolation", current_call_id, attempt_type="RETRY",
                    logical_batch_id=f"{logical_batch_id}/split-{label}-{member_hash}",
                )
            return
        unit = missing[0]
        event = unit.events[0] if len(unit.events) == 1 else None
        if event is None:
            # A grouped sign is split conservatively if its combined response
            # fails; members become independent units.
            for member in unit.events:
                self._process_units(
                    [Unit(f"event-{member.id}", [member])], "split_isolation", current_call_id,
                    attempt_type="RETRY", logical_batch_id=f"{logical_batch_id}/event-{member.id}",
                )
            return
        result = self.results[event.id]
        retry_parent = current_call_id
        for retry in range(self.config.max_retries):
            simplified = retry >= 1
            retry_phase = "retry_simplified" if simplified else "retry_local"
            result.retry_count += 1
            valid_retry, retry_issues = self._attempt(
                [Unit(f"event-{event.id}", [event])], simplified, retry_phase, retry_parent,
                attempt_type="RETRY", logical_batch_id=f"{logical_batch_id}/retry-{retry + 1}",
            )
            retry_parent = self._last_call_id or retry_parent
            result.attempts.append({"model": self.config.model, "phase": retry_phase, "retry_type": "RETRY_SEGMENTED_EVENT" if is_multi_speaker(event) else "RETRY_NORMAL_EVENT", "event_id": event.id, "reason": "; ".join(retry_issues)})
            if event.id in valid_retry:
                if not result.retry_recommended:
                    return
        if result.status != "resolved":
            result.status = "failed"
            result.failure_reason = "; ".join(issues) or result.failure_reason or "falha definitiva experimental"

    def plan_initial_batches(self) -> list[list[Unit]]:
        """Return the canonical deterministic initial V226 request groups."""
        pending_units: list[Unit] = []
        for event in self.events:
            result = self.results[event.id]
            uncertain_romaji_is_english = event.classification == "ROMAJI_PRESERVED" and likely_english_sentence_source(event, self.english_dictionary)
            if event.classification == "SONG_LYRICS_PRESERVED":
                result.status = "resolved"; result.final_text = event.original_text; result.final_model = "song-preserve"
                if "SONG_LYRICS_PRESERVED" not in result.flags:
                    result.flags.append("SONG_LYRICS_PRESERVED")
                continue
            if event.classification in {"TECHNICAL_OR_EMPTY", "MUSIC_OR_KARAOKE"} or (event.classification == "ROMAJI_PRESERVED" and not uncertain_romaji_is_english):
                result.status = "resolved"
                result.final_text = event.original_text
                result.final_model = "romaji-preserve" if event.classification == "ROMAJI_PRESERVED" else "deterministic-preserve"
                if event.classification == "ROMAJI_PRESERVED" and "ROMAJI_PRESERVED" not in result.flags:
                    result.flags.append("ROMAJI_PRESERVED")
                continue
            if event.classification == "SDH" and self.config.sdh_deterministic_enabled:
                translated, rule = deterministic_sdh(event.clean_text)
                if translated is not None:
                    result.status = "resolved"
                    result.final_text = translated
                    result.final_model = "deterministic-sdh"
                    marker = "SDH_DETERMINISTIC:" + rule
                    if marker not in result.flags:
                        result.flags.append(marker)
                    continue
            pending_units.extend(unit for unit in self.units if event in unit.events and unit not in pending_units)
        # batch_target_size is measured in target events, not Unit objects.
        # A grouped sign unit may contain several events and is kept intact;
        # otherwise units are packed until the event budget is reached.
        packed: list[list[Unit]] = []
        current: list[Unit] = []
        current_events = 0
        current_kind = ""
        for unit in pending_units:
            unit_events = len(unit.events)
            unit_kind = "segmented" if any(is_multi_speaker(event) for event in unit.events) else "normal"
            if current and (current_kind != unit_kind or current_events + unit_events > self.config.batch_target_size):
                packed.append(current)
                current, current_events, current_kind = [], 0, ""
            current.append(unit)
            current_events += unit_events
            current_kind = unit_kind
        if current:
            packed.append(current)
        return packed

    def run(self) -> dict[str, Any]:
        packed = self.plan_initial_batches()
        for batch_index, batch in enumerate(packed):
            self._process_units(
                batch, "initial", attempt_type="INITIAL",
                logical_batch_id=f"v226-initial-{batch_index:06d}", batch_index=batch_index,
            )
        eligible = all(result.status == "resolved" for result in self.results.values())
        critical_flags = sorted({flag.split(":", 1)[0] for result in self.results.values() for flag in result.flags if flag.split(":", 1)[0] in CRITICAL_FLAGS})
        flag_counts = dict(Counter(flag.split(":", 1)[0] for result in self.results.values() for flag in result.flags))
        optional_dictionary = Path(self.config.english_dictionary_path) if self.config.english_dictionary_path else None
        return {
            "eligible": eligible,
            "eligible_experimental": eligible and not critical_flags,
            "events": len(self.events),
            "resolved": sum(result.status == "resolved" for result in self.results.values()),
            "failed": sum(result.status != "resolved" for result in self.results.values()),
            "units": len(self.units),
            "grouped_sign_units": sum(unit.grouped_sign for unit in self.units),
            "initial_batch_count": len(packed),
            "classifications": dict(Counter(event.classification for event in self.events)),
            "models_final": dict(Counter(result.final_model for result in self.results.values())),
            "flags": flag_counts,
            "critical_flags": critical_flags,
            "results": [asdict(self.results[event.id]) for event in self.events],
            "calls": self.calls,
            "initial_ollama_calls": sum(call.get("phase") == "initial" for call in self.calls),
            "split_isolation_ollama_calls": sum(call.get("phase") == "split_isolation" for call in self.calls),
            "actual_retry_ollama_calls": sum(call.get("phase") != "initial" for call in self.calls),
            "total_ollama_calls": len(self.calls),
            "events_retried": sum(result.retry_count > 0 for result in self.results.values()),
            "retry_budget": {
                "configured": self.retry_budget.consumed + self.retry_budget.remaining,
                "consumed": self.retry_budget.consumed,
                "remaining": self.retry_budget.remaining,
                "exhausted": self.retry_budget.exhausted,
                "max_retry_depth": self.retry_budget.max_depth,
                "last_reason": self.retry_budget.last_reason,
            },
            "retry_budget_exhausted_count": int(self.retry_budget.exhausted),
            "diagnostics": self.diagnostic_records,
            "song_lyrics_preserved_count": sum(event.classification == "SONG_LYRICS_PRESERVED" for event in self.events),
            "song_ambiguous_count": len(self.profile.get("song_ambiguous_events", [])),
            "romaji_preserved_count": sum(event.classification == "ROMAJI_PRESERVED" for event in self.events),
            "possible_untranslated_count": flag_counts.get("POSSIBLE_UNTRANSLATED_OUTPUT", 0),
            "confirmed_untranslated_count": flag_counts.get("UNTRANSLATED_DIALOGUE", 0),
            "untranslated_retry_count": sum(1 for result in self.results.values() for attempt in result.attempts if "UNTRANSLATED" in str(attempt.get("reason", "")).upper()),
            "optional_resource_status": {
                "english_dictionary": {
                    "path": str(optional_dictionary) if optional_dictionary else None,
                    "available": bool(optional_dictionary and optional_dictionary.is_file()),
                    "fallback": "built-in conservative vocabulary",
                }
            },
        }


def write_ass(original: pysubs2.SSAFile, events: list[Event], summary: dict[str, Any], path: Path) -> None:
    output = pysubs2.SSAFile.from_string(original.to_string(format_="ass"), format_="ass")
    results = {item["id"]: item for item in summary["results"]}
    for event in events:
        item = results.get(event.id)
        if item and item["status"] == "resolved" and item["final_text"] is not None:
            output[event.original_index].text = item["final_text"]
    output.save(str(path), encoding="utf-8")


def validate_structure(original: pysubs2.SSAFile, candidate: pysubs2.SSAFile, selected_indices: set[int] | None = None, segmented_indices: set[int] | None = None) -> dict[str, Any]:
    fields = ("layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect")
    issues: list[str] = []
    if len(original) != len(candidate):
        issues.append("quantidade de eventos alterada")
    for index in range(min(len(original), len(candidate))):
        left, right = original[index], candidate[index]
        for field_name in fields:
            if getattr(left, field_name, None) != getattr(right, field_name, None):
                issues.append(f"evento {index}: campo {field_name} alterado")
        if sorted(TAG_RE.findall(left.text or "")) != sorted(TAG_RE.findall(right.text or "")):
            issues.append(f"evento {index}: tags alteradas")
        for flag in validate_inline_tags(left.text or "", right.text or ""):
            if flag in {"ASS_INLINE_TAG_SPLIT_WORD", "ASS_INLINE_TAG_DUPLICATION", "ASS_INLINE_TAG_ANCHOR_FAILURE"}:
                issues.append(f"evento {index}: {flag}")
        if (left.text or "").count(r"\N") != (right.text or "").count(r"\N"):
            issues.append(f"evento {index}: \\N alterado")
        if selected_indices is not None and index not in selected_indices:
            continue
        # If a selected event was preserved byte-for-byte (technical/music or
        # an unresolved laboratory item), retain the source release's visual
        # break instead of misclassifying it as a newly introduced split.
        if (index not in (segmented_indices or set()) and (left.text or "") != (right.text or "") and line_break_inside_word(right.text or "")):
            issues.append(f"evento {index}: LINE_BREAK_INSIDE_WORD")
        source_clean = TAG_RE.sub("", left.text or "").replace(r"\N", " ").strip()
        candidate_clean = TAG_RE.sub("", right.text or "").replace(r"\N", " ").strip()
        if re.search(r"[\wÀ-ÿ]", source_clean, re.UNICODE) and not re.search(r"[\wÀ-ÿ]", candidate_clean, re.UNICODE):
            issues.append(f"evento {index}: CONTENT_LOSS")
        if any(token in (right.text or "") for token in ("§T", "§N", "§G")):
            issues.append(f"evento {index}: placeholder vazado")
    return {"valid": not issues, "original_events": len(original), "candidate_events": len(candidate), "issues": issues}


def resource_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            mem[key] = value.strip()
        result["meminfo"] = {key: mem.get(key) for key in ("MemTotal", "MemAvailable", "SwapFree")}
    except Exception as exc:
        result["meminfo_error"] = type(exc).__name__
    try:
        proc = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
        result["nvidia_smi"] = proc.stdout.strip() or proc.stderr.strip()[:300]
    except Exception as exc:
        result["nvidia_smi_error"] = type(exc).__name__
    return result


def load_glossary(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in data.items()):
        raise ValueError("glossário deve ser objeto string -> string")
    return data
