"""V2.3.7 deterministic preservation of safe ASS temporal transforms.

This layer owns no translation.  It accepts only a transform in the leading,
event-wide ASS envelope and preserves its exact source AST while a frozen
envelope rebuilder changes the semantic text.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable

from pipeline_v2_1_3 import TAG_RE


TEMPORAL_EVENT_GLOBAL = "EVENT_OR_SPAN_GLOBAL_TEMPORAL_VISUAL_TRANSFORM"
TEMPORAL_GLYPH_DEPENDENT = "TEMPORAL_GLYPH_REPARAMETERIZATION_REQUIRED"
TEMPORAL_SEMANTIC_SCOPE = "TEMPORAL_SEMANTIC_SCOPE_REMAP_REQUIRED"
TEMPORAL_AMBIGUOUS = "TEMPORAL_TRANSFORM_AMBIGUOUS_OR_UNSUPPORTED"

_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_KARAOKE = re.compile(r"\\(?:[kK](?:f|o)?)(?![A-Za-z])")
_DRAWING = re.compile(r"\\p(?:\d+)?(?![A-Za-z\d])")


@dataclass(frozen=True)
class TemporalTransformAST:
    raw_body: str
    t1: str | None
    t2: str | None
    accel: str | None
    modifiers: tuple[str, ...]
    modifier_order: tuple[str, ...]
    block_index: int
    block_raw: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["modifiers"] = list(self.modifiers)
        value["modifier_order"] = list(self.modifier_order)
        return value


@dataclass(frozen=True)
class TemporalAssTransformProgram:
    source_sha256: str
    source_plain_text: str
    scope: str
    transforms: tuple[TemporalTransformAST, ...]
    has_nested_transform: bool
    has_karaoke: bool
    has_drawing: bool
    has_text_before_transform: bool
    has_post_text_override: bool
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["transforms"] = [item.to_dict() for item in self.transforms]
        return value


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _split_top_level(value: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("TEMPORAL_TRANSFORM_UNBALANCED_PARENTHESES")
        elif char == "," and depth == 0:
            pieces.append(value[start:index])
            start = index + 1
    if depth:
        raise ValueError("TEMPORAL_TRANSFORM_UNBALANCED_PARENTHESES")
    pieces.append(value[start:])
    return pieces


def _modifier_order(value: str) -> tuple[str, ...]:
    if not value:
        return tuple()
    # ASS transform modifiers are backslash-prefixed and have no nested
    # parentheses in the supported class. Preserve each raw token verbatim.
    if "(" in value or ")" in value:
        raise ValueError("TEMPORAL_TRANSFORM_NESTED_MODIFIER")
    tokens = tuple("\\" + token for token in re.findall(r"\\([^\\]+)", value))
    if "".join(tokens) != value:
        raise ValueError("TEMPORAL_TRANSFORM_MALFORMED_MODIFIER")
    return tokens


def _parse_body(body: str, block_index: int, block_raw: str) -> TemporalTransformAST:
    parts = _split_top_level(body)
    values = [part.strip() for part in parts]
    cursor = 0
    t1 = t2 = accel = None
    if cursor < len(values) and _NUMBER.fullmatch(values[cursor]):
        first = values[cursor]
        if cursor + 1 < len(values) and _NUMBER.fullmatch(values[cursor + 1]):
            t1, t2 = first, values[cursor + 1]
            cursor += 2
        else:
            accel = first
            cursor += 1
    if cursor < len(values) and _NUMBER.fullmatch(values[cursor]) and accel is None:
        accel = values[cursor]
        cursor += 1
    modifiers_text = ",".join(values[cursor:])
    order = _modifier_order(modifiers_text)
    if not order:
        raise ValueError("TEMPORAL_TRANSFORM_MODIFIERS_MISSING")
    return TemporalTransformAST(
        raw_body=body, t1=t1, t2=t2, accel=accel, modifiers=(modifiers_text,),
        modifier_order=order, block_index=block_index, block_raw=block_raw,
    )


def _find_transform_blocks(source: str) -> tuple[list[TemporalTransformAST], bool]:
    transforms: list[TemporalTransformAST] = []
    nested = False
    for block_match in re.finditer(r"\{[^{}]*\}", source):
        block = block_match.group(0)
        cursor = 0
        while True:
            match = re.search(r"\\t\(", block[cursor:])
            if not match:
                break
            start = cursor + match.start()
            body_start = cursor + match.end()
            depth = 1
            index = body_start
            while index < len(block) and depth:
                if block[index] == "(":
                    depth += 1
                elif block[index] == ")":
                    depth -= 1
                index += 1
            if depth:
                raise ValueError("TEMPORAL_TRANSFORM_UNBALANCED_PARENTHESES")
            body = block[body_start:index - 1]
            if "\\t(" in body:
                nested = True
            transforms.append(_parse_body(body, block_match.start(), block))
            cursor = index
    return transforms, nested


def parse_temporal_transform_program(source_ass: str) -> tuple[TemporalAssTransformProgram | None, dict[str, Any]]:
    source = source_ass or ""
    try:
        transforms, nested = _find_transform_blocks(source)
    except ValueError as exc:
        return None, {"valid": False, "classification": TEMPORAL_AMBIGUOUS, "reason": str(exc)}
    if not transforms:
        return None, {"valid": False, "classification": TEMPORAL_AMBIGUOUS, "reason": "TEMPORAL_TRANSFORM_NOT_FOUND"}
    # The first non-override character is the beginning of the event's
    # linguistic surface. A transform after it has span/semantic scope.
    has_text_before = any(bool(TAG_RE.sub("", source[:item.block_index])) for item in transforms)
    first_visible_raw = next((index for index, char in enumerate(source) if char.isalnum()), len(source))
    has_post_text_override = any(
        match.start() > first_visible_raw and r"\t(" not in match.group(0)
        for match in re.finditer(r"\{[^{}]*\}", source)
    )
    has_karaoke = bool(_KARAOKE.search(source))
    has_drawing = bool(_DRAWING.search(source))
    if nested or has_karaoke or has_drawing or has_post_text_override:
        classification = TEMPORAL_GLYPH_DEPENDENT if (has_karaoke or has_drawing) else TEMPORAL_AMBIGUOUS
    elif has_text_before:
        classification = TEMPORAL_SEMANTIC_SCOPE
    else:
        classification = TEMPORAL_EVENT_GLOBAL
    # Any visible surface before a transform means the transform starts in a
    # semantic span. The leading block class is the only automatically safe
    # representation in this revision.
    scope = "EVENT_GLOBAL_LEADING_OVERRIDE" if classification == TEMPORAL_EVENT_GLOBAL else "UNSUPPORTED"
    program = TemporalAssTransformProgram(
        source_sha256=_sha(source), source_plain_text=TAG_RE.sub("", source), scope=scope,
        transforms=tuple(transforms), has_nested_transform=nested, has_karaoke=has_karaoke,
        has_drawing=has_drawing, has_text_before_transform=has_text_before,
        has_post_text_override=has_post_text_override,
        fingerprint=_sha(repr([(item.to_dict()) for item in transforms])),
    )
    return program, {"valid": classification == TEMPORAL_EVENT_GLOBAL, "classification": classification, "scope": scope, "program": program.to_dict()}


def temporal_ast_equal(left: TemporalAssTransformProgram, right: TemporalAssTransformProgram) -> bool:
    # The program AST excludes linguistic text and its source hash. Those are
    # expected to change when the frozen envelope receives the approved target
    # text; timing, modifiers, order and scope are the immutable AST.
    return (
        left.scope == right.scope
        and left.transforms == right.transforms
        and left.has_nested_transform == right.has_nested_transform
        and left.has_karaoke == right.has_karaoke
        and left.has_drawing == right.has_drawing
        and left.has_text_before_transform == right.has_text_before_transform
        and left.has_post_text_override == right.has_post_text_override
    )


def preserve_temporal_transform_envelope(source_ass: str, target_text: str, *, base_rebuilder: Callable[[str, str], str]) -> tuple[str | None, dict[str, Any]]:
    source_program, source_trace = parse_temporal_transform_program(source_ass)
    if source_program is None or not source_trace.get("valid"):
        return None, source_trace
    candidate = base_rebuilder(source_ass, target_text)
    final_program, final_trace = parse_temporal_transform_program(candidate)
    if final_program is None or not temporal_ast_equal(source_program, final_program):
        return None, {"valid": False, "reason": "TEMPORAL_AST_NOT_IDENTICAL", "source": source_trace, "final": final_trace}
    final_plain = TAG_RE.sub("", candidate)
    if final_plain != target_text:
        return None, {"valid": False, "reason": "TEMPORAL_TARGET_TEXT_CHANGED", "expected": target_text, "actual": final_plain, "source": source_trace, "final": final_trace}
    return candidate, {"valid": True, "classification": TEMPORAL_EVENT_GLOBAL, "source_program": source_trace["program"], "final_program": final_trace["program"], "ast_equal": True, "target_text_identity": True}


def inject_source_temporal_transforms(source_ass: str, target_text: str, *, base_rebuilder: Callable[[str, str], str]) -> str | None:
    """Injeta blocos \t( do source no target quando o modelo não preservou.

    Quando preserve_temporal_transform_envelope retorna None (modelo removeu ou
    alterou os \t( ), esta função copia os blocos de transformação temporal do
    source para o target, preservando a animação original. Retorna None se o
    source não tiver \t( ou se a injeção não for possível.
    """
    if "\\t(" not in source_ass:
        return None
    # Extrai blocos {...\t(...)...} do source
    source_blocks: list[str] = []
    for block_match in re.finditer(r"\{[^{}]*\\t\([^)]*\)[^{}]*\}", source_ass):
        source_blocks.append(block_match.group(0))
    if not source_blocks:
        return None
    # Se o target já tem \t(, não injeta (já preservado pelo modelo)
    if "\\t(" in target_text:
        return None
    # Injeta blocos de transformação temporal do source antes do texto do target
    # Preserva a estrutura ASS: blocos de override ficam antes do texto visível
    injection = "".join(source_blocks)
    # Se o target já começa com blocos {}, injeta após o primeiro bloco vazio
    # ou antes do primeiro bloco de override
    first_brace = target_text.find("{")
    if first_brace == 0:
        # Encontra o fim do primeiro bloco {}
        end_brace = target_text.find("}", first_brace)
        if end_brace != -1:
            # Injeta após o primeiro bloco {}
            return target_text[:end_brace + 1] + injection + target_text[end_brace + 1:]
    # Caso contrário, injeta no início
    return injection + target_text
