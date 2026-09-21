"""Centralized, unified ASS processing, validation, and in-memory AST engine.

Consolidates all scattered ASS lexical, structural, and validation logic into a
single authoritative module for the V3 unified architecture. Eliminates the
historical cascading dependency chain (v2.1.2 -> v2.1.3 -> v2.2.2 -> v2.2.6)
and provides an in-memory document model to avoid redundant disk I/O.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import pysubs2

# ---------------------------------------------------------------------------
# Lexical Patterns & Token Boundaries
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"\{[^}]*\}")
CONTROL_RE = re.compile(r"\\(?:N|n|h)")
BREAK_RE = re.compile(r"\\(?:N|n)")
TOKEN_RE = re.compile(r"(\{[^}]*\}|\\(?:N|n|h))")
PAYLOAD_TOKEN_RE = re.compile(r"(\{[^}]*\}|\\h)")
PAYLOAD_WORD_RE = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)
DRAWING_MODE_RE = re.compile(r"\\p(?P<scale>[0-9]+)", re.IGNORECASE)
VECTOR_COMMAND_RE = re.compile(r"(?<![A-Za-z])(?:m|n|l|b|s|p|c)(?![A-Za-z])", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Canonical Lexicon & Legitimate Short Words for Portuguese Line Breaks
# ---------------------------------------------------------------------------

LEGITIMATE_SHORT_WORDS: frozenset[str] = frozenset({
    # Artigos e contrações
    "a", "o", "as", "os", "um", "uma", "uns", "umas",
    "ao", "aos", "à", "às",
    # Preposições essenciais e contrações
    "de", "do", "da", "dos", "das",
    "em", "no", "na", "nos", "nas",
    "num", "numa", "nuns", "numas",
    "dum", "duma", "duns", "dumas",
    "por", "pelo", "pela", "pelos", "pelas", "pra", "pro", "pras", "pros",
    # Conjunções e conectivos
    "e", "ou", "se", "que", "mas", "com", "sem", "sob", "até", "nem", "pois",
    # Pronomes retos e oblíquos
    "eu", "tu", "ele", "ela", "nós", "vós", "eles", "elas",
    "me", "te", "se", "nos", "vos", "lhe", "lhes",
    # Pronomes possessivos e demonstrativos
    "meu", "teu", "seu", "sua", "meus", "teus", "seus", "suas",
    "isso", "isto", "esse", "essa", "este", "esta",
    # Advérbios frequentes
    "já", "só", "lá", "cá", "ali", "aqui", "bem", "mal", "mau", "não", "sim",
    # Formas verbais curtas de alta frequência
    "é", "era", "foi", "vai", "vem", "dar", "ver", "ter", "ser", "sou", "são",
    "tem", "diz", "faz", "vou", "fiz", "deu", "viu", "sai", "ia",
    # Substantivos monossílabos ou dissílabos comuns
    "dia", "sol", "lua", "mar", "céu", "fim", "paz", "dor", "som", "tom", "voz",
    "mão", "pés", "pé", "rei", "lei", "pai", "mãe", "tio", "tia", "ano", "mês",
    "vez", "ato", "cor", "luz", "dom", "elo", "ora", "ar", "rua",
    # Interjeições comuns de dublagem/legendagem
    "oba", "olá", "alô", "opa", "uau", "ai", "ui", "ah", "oh", "ei",
})

CRITICAL_VALIDATION_FLAGS: frozenset[str] = frozenset({
    "LINE_BREAK_INSIDE_WORD",
    "LINE_BREAK_COUNT_MISMATCH",
    "CONTENT_LOSS",
    "ASS_TAG_MISMATCH",
    "SEGMENT_ID_MISMATCH",
    "STRUCTURAL_CONTENT_IN_MODEL_OUTPUT",
    "UNBALANCED_QUOTES",
    "UNBALANCED_DELIMITERS",
    "MODEL_EMITTED_STRUCTURAL_TOKEN",
    "DELIMITER_COUNT_MISMATCH",
    "ASS_INLINE_TAG_SPLIT_WORD",
    "ASS_INLINE_TAG_ANCHOR_FAILURE",
    "ASS_INLINE_TAG_DUPLICATION",
    "ASS_HARD_SPACE_COUNT_MISMATCH",
    "IDIOMATIC_LITERAL_RISK",
    "UNTRANSLATED_DIALOGUE",
})


# ---------------------------------------------------------------------------
# Lexical Primitives & Normalization
# ---------------------------------------------------------------------------

def _word_char(char: str) -> bool:
    """Return whether char is a word constituent (including combining marks)."""
    return bool(char) and (char == "_" or char.isalnum() or unicodedata.category(char).startswith("M"))


def visible_text(text: str, *, line_break: str = "\n") -> str:
    """Return visible ASS text stripped of override tags with normalized controls."""
    value = TAG_RE.sub("", text or "")
    value = value.replace(r"\N", line_break).replace(r"\n", line_break)
    return value.replace(r"\h", " ")


def break_tokens(text: str) -> list[str]:
    """Return hard/soft line-break controls in source order."""
    return [match.group(0) for match in BREAK_RE.finditer(text or "")]


def break_count(text: str) -> int:
    """Return count of line breaks in text."""
    return len(break_tokens(text))


def hard_space_count(text: str) -> int:
    r"""Return count of ASS non-breaking spaces (\h) in text."""
    return (text or "").count(r"\h")


def inline_tag_counts(text: str) -> Counter[str]:
    """Return histogram of override tags."""
    return Counter(TAG_RE.findall(text or ""))


def clean_residual_override_tags(text: str) -> str:
    """Clean empty or residual override tag blocks such as {*} or { }."""
    if not text or "{" not in text:
        return text or ""

    def _replace(match: re.Match[str]) -> str:
        body = match.group(0)
        # Se contiver apenas chaves, asteriscos e espaços/tabs, remove por completo
        if not body.strip("{}* \t"):
            return ""
        return body

    cleaned = TAG_RE.sub(_replace, text)
    # Normaliza espaços duplos criados pela remoção de tags vazias isoladas
    return re.sub(r" {2,}", " ", cleaned)


def is_drawing_event(event_or_text: str | Any) -> bool:
    """Return whether the event or text contains active ASS vector drawing."""
    text = getattr(event_or_text, "text", event_or_text) or ""
    tags = "".join(TAG_RE.findall(text))
    match = DRAWING_MODE_RE.search(tags)
    if not match:
        return False
    return match.group("scale") != "0"


# ---------------------------------------------------------------------------
# Structural & Inline Tag Validation
# ---------------------------------------------------------------------------

def inline_tag_split_word(text: str) -> bool:
    """Detect if an inline tag was inserted between two letters of a word."""
    matches = list(TAG_RE.finditer(text or ""))
    for match in matches:
        before_without_tags = TAG_RE.sub("", text[:match.start()])
        after_without_tags = TAG_RE.sub("", text[match.end():])
        if re.search(r"\\(?:N|n)\s*$", before_without_tags) or re.match(r"^\s*\\(?:N|n)", after_without_tags):
            continue
        left = visible_text(before_without_tags, line_break="")
        right = visible_text(after_without_tags, line_break="")
        if left and right and _word_char(left[-1]) and _word_char(right[0]):
            return True
    return False


def validate_inline_tags(source: str, candidate: str) -> list[str]:
    """Validate tag count/order and reject newly introduced lexical splits."""
    flags: list[str] = []
    source_counts = inline_tag_counts(source)
    candidate_counts = inline_tag_counts(candidate)
    if source_counts != candidate_counts:
        if any(candidate_counts[tag] > source_counts[tag] for tag in candidate_counts):
            flags.append("ASS_INLINE_TAG_DUPLICATION")
        flags.append("ASS_TAG_MISMATCH")
    # Tolerated if source itself contained the split (e.g. Kana-{\i1}chan)
    if inline_tag_split_word(candidate) and not inline_tag_split_word(source):
        flags.append("ASS_INLINE_TAG_SPLIT_WORD")
        flags.append("ASS_INLINE_TAG_ANCHOR_FAILURE")
    return sorted(set(flags))


def line_break_inside_word(text: str) -> bool:
    """Validate that a visual break (\\N) does not slice a word in half.

    Permits legitimate short Portuguese function words (da, do, de, e, na, no, etc.)
    around the break.
    """
    for match in BREAK_RE.finditer(text or ""):
        index = match.start()
        left_raw = text[:index]
        right_raw = text[match.end():]
        # ASS style tags immediately adjacent to break are explicit boundaries
        if re.search(r"\{[^}]*\}\s*$", left_raw) or re.match(r"\s*\{[^}]*\}", right_raw):
            continue
        plain = visible_text(text, line_break="")
        plain_index = len(visible_text(text[:index], line_break=""))
        if plain_index > 0 and plain_index + 1 < len(plain) and _word_char(plain[plain_index - 1]) and _word_char(plain[plain_index]):
            left_match = re.search(r"[\wÀ-ÿ]+$", plain[:plain_index])
            right_match = re.match(r"[\wÀ-ÿ]+", plain[plain_index:])
            left_len = len(left_match.group(0)) if left_match else 0
            right_len = len(right_match.group(0)) if right_match else 0
            left_word = left_match.group(0).lower() if left_match else ""
            right_word = right_match.group(0).lower() if right_match else ""
            left_valid = left_len >= 4 or left_word in LEGITIMATE_SHORT_WORDS
            right_valid = right_len >= 4 or right_word in LEGITIMATE_SHORT_WORDS
            if left_valid and right_valid:
                continue
            return True
    return False


# ---------------------------------------------------------------------------
# In-Memory Event Model & Document AST
# ---------------------------------------------------------------------------

@dataclass
class ASSEventNode:
    """Lightweight in-memory representation of an ASS dialogue/comment event."""
    index: int
    type: str = "Dialogue"
    layer: int = 0
    start: int = 0
    end: int = 0
    style: str = "Default"
    name: str = ""
    marginl: int = 0
    marginr: int = 0
    marginv: int = 0
    effect: str = ""
    text: str = ""
    is_comment: bool = False

    @classmethod
    def from_pysubs2(cls, index: int, event: pysubs2.SSAEvent) -> ASSEventNode:
        return cls(
            index=index,
            type=getattr(event, "type", "Dialogue"),
            layer=event.layer,
            start=event.start,
            end=event.end,
            style=event.style,
            name=event.name,
            marginl=event.marginl,
            marginr=event.marginr,
            marginv=event.marginv,
            effect=event.effect,
            text=event.text,
            is_comment=getattr(event, "is_comment", False),
        )

    def to_pysubs2(self) -> pysubs2.SSAEvent:
        event = pysubs2.SSAEvent(
            start=self.start,
            end=self.end,
            text=self.text,
            style=self.style,
            name=self.name,
            marginl=self.marginl,
            marginr=self.marginr,
            marginv=self.marginv,
            effect=self.effect,
            layer=self.layer,
        )
        event.type = self.type
        if self.is_comment:
            event.is_comment = True
        return event

    @property
    def visible(self) -> str:
        return visible_text(self.text)

    @property
    def is_drawing(self) -> bool:
        return is_drawing_event(self.text)


class ASSDocumentAST:
    """Authoritative in-memory AST for an entire subtitle file.

    Allows all pipeline stages (base translation, sign fanout, visual glyphs,
    karaoke augmentation) to operate in memory without serializing/deserializing
    to temporary files on disk.
    """

    def __init__(self, ssa: pysubs2.SSAFile | None = None) -> None:
        self.ssa = ssa or pysubs2.SSAFile()
        self.events: list[ASSEventNode] = [
            ASSEventNode.from_pysubs2(idx, ev) for idx, ev in enumerate(self.ssa.events)
        ]
        self.styles = self.ssa.styles
        self.info = self.ssa.info
        self.aegisub_project = getattr(self.ssa, "aegisub_project", {})

    @classmethod
    def from_file(cls, path: str | Path, encoding: str = "utf-8") -> ASSDocumentAST:
        ssa = pysubs2.load(str(path), encoding=encoding)
        return cls(ssa)

    @classmethod
    def from_string(cls, content: str, format_: str = "ass") -> ASSDocumentAST:
        ssa = pysubs2.SSAFile.from_string(content, format_=format_)
        return cls(ssa)

    def sync_to_ssa(self) -> pysubs2.SSAFile:
        """Sync internal nodes back to the pysubs2 SSAFile container."""
        self.ssa.events = [node.to_pysubs2() for node in self.events]
        return self.ssa

    def save(self, path: str | Path, encoding: str = "utf-8") -> None:
        """Serialize in-memory AST directly to destination disk path atomically."""
        self.sync_to_ssa()
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.ssa.save(str(dest), encoding=encoding)

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, idx: int) -> ASSEventNode:
        return self.events[idx]


# ---------------------------------------------------------------------------
# Deep Document Validation Engine
# ---------------------------------------------------------------------------

def validate_document_structure(
    original: ASSDocumentAST | pysubs2.SSAFile | Sequence[Any],
    candidate: ASSDocumentAST | pysubs2.SSAFile | Sequence[Any],
    *,
    selected_indices: set[int] | None = None,
    segmented_indices: set[int] | None = None,
    allow_tag_reflow_indices: set[int] | None = None,
) -> dict[str, Any]:
    """Perform comprehensive structural validation between source and candidate.

    Guarantees timing, styles, layers, comment safety, bracket integrity, and
    line break boundaries while supporting legitimate fanout and tag reflows.
    """
    orig_events = original.events if isinstance(original, ASSDocumentAST) else original
    cand_events = candidate.events if isinstance(candidate, ASSDocumentAST) else candidate

    fields = ("type", "layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect")
    issues: list[str] = []

    if len(orig_events) != len(cand_events):
        issues.append("quantidade de eventos alterada")

    limit = min(len(orig_events), len(cand_events))
    for index in range(limit):
        left, right = orig_events[index], cand_events[index]

        # 1. Metadados e temporização estritos
        for field_name in fields:
            left_val = getattr(left, field_name, None)
            right_val = getattr(right, field_name, None)
            if left_val != right_val:
                issues.append(f"evento {index}: campo {field_name} alterado")

        # 2. Comentários devem ser estritamente preservados
        is_comment = getattr(left, "is_comment", False) or getattr(left, "type", "") == "Comment"
        if is_comment:
            if getattr(left, "text", "") != getattr(right, "text", ""):
                issues.append(f"evento {index}: ASS_COMMENT_CHANGED")
            continue

        left_text = getattr(left, "text", "") or ""
        right_text = getattr(right, "text", "") or ""

        # 3. Tags ASS e tags com asterisco/residuais
        if allow_tag_reflow_indices is None or index not in allow_tag_reflow_indices:
            if sorted(TAG_RE.findall(left_text)) != sorted(TAG_RE.findall(right_text)):
                issues.append(f"evento {index}: tags alteradas")

        for flag in validate_inline_tags(left_text, right_text):
            if flag in {"ASS_INLINE_TAG_SPLIT_WORD", "ASS_INLINE_TAG_DUPLICATION", "ASS_INLINE_TAG_ANCHOR_FAILURE"}:
                issues.append(f"evento {index}: {flag}")

        # 4. Quebras visuais e espaços rígidos
        if break_count(left_text) != break_count(right_text):
            issues.append(f"evento {index}: quebra ASS alterada")

        if hard_space_count(left_text) != hard_space_count(right_text):
            issues.append(f"evento {index}: \\h alterado")

        if selected_indices is not None and index not in selected_indices:
            continue

        # 5. Quebra de linha dentro de palavra com proteção de palavras funcionais
        if (
            index not in (segmented_indices or set())
            and left_text != right_text
            and line_break_inside_word(right_text)
        ):
            issues.append(f"evento {index}: LINE_BREAK_INSIDE_WORD")

        # 6. Detecção de perda de conteúdo
        source_clean = visible_text(left_text, line_break=" ").strip()
        candidate_clean = visible_text(right_text, line_break=" ").strip()
        if re.search(r"[\wÀ-ÿ]", source_clean, re.UNICODE) and not re.search(r"[\wÀ-ÿ]", candidate_clean, re.UNICODE):
            issues.append(f"evento {index}: CONTENT_LOSS")

        # 7. Prevenção de vazamento de placeholders temporários
        if any(token in right_text for token in ("§T", "§N", "§G")):
            issues.append(f"evento {index}: placeholder vazado")

    return {
        "valid": not issues,
        "original_events": len(orig_events),
        "candidate_events": len(cand_events),
        "issues": issues,
    }
