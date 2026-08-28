"""Canonical V2.3.8 source-payload envelope helper.

This small deterministic seam retains source whitespace while allocating a
validated target payload.  It has no episode, event, record or staging
identity and is safe to reuse for new episodes.
"""
from __future__ import annotations

import re

_TAG_OR_H = re.compile(r"(\{[^}]*\}|\\h)")
_WORD = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)


def _whitespace_edges(value: str) -> tuple[str, str]:
    leading_match = re.match(r"\s*", value)
    trailing_match = re.search(r"\s*$", value)
    return (leading_match.group(0) if leading_match else "", trailing_match.group(0) if trailing_match else "")


def rc4_replace_source_payload(source_part: str, target_part: str) -> str:
    """Replace lexical chunks while retaining the source ASS envelope.

    The base V2.2.6 materializer may return a translated payload that still
    contains the source override tags.  Those tags are model/output data, not
    part of the linguistic payload; accepting them here duplicates the
    source-owned envelope.  Likewise, a model may move or omit ``\\N``.  Both
    presentation controls are therefore removed from the target before the
    source envelope is rebuilt.
    """
    target_plain = _TAG_OR_H.sub("", target_part or "")
    source_parts = (source_part or "").split(r"\N")
    if len(source_parts) > 1:
        target_parts = target_plain.split(r"\N")
        if len(target_parts) == len(source_parts):
            return r"\N".join(
                rc4_replace_source_payload(source_piece, target_piece)
                for source_piece, target_piece in zip(source_parts, target_parts)
            )
        target_words = re.findall(r"\S+", target_plain.replace(r"\N", " "))
        source_word_counts = [len(_WORD.findall(_TAG_OR_H.sub("", piece))) for piece in source_parts]
        total = max(1, sum(source_word_counts))
        rendered: list[str] = []
        start = 0
        for index, (source_piece, source_count) in enumerate(zip(source_parts, source_word_counts)):
            if index == len(source_parts) - 1:
                end = len(target_words)
            else:
                end = min(len(target_words), max(start, round(len(target_words) * source_count / total)))
            rendered.append(rc4_replace_source_payload(source_piece, " ".join(target_words[start:end])))
            start = end
        return r"\N".join(rendered)

    tokens = _TAG_OR_H.split(source_part or "")
    lexical_indices = [i for i, token in enumerate(tokens) if token and not _TAG_OR_H.fullmatch(token)]
    if not lexical_indices:
        return source_part
    target_words = re.findall(r"\S+", target_plain)
    wordful = [i for i in lexical_indices if _WORD.findall(tokens[i])]
    total = max(1, sum(len(_WORD.findall(tokens[i])) for i in wordful))
    replacements: dict[int, str] = {}
    start = 0
    wordful_position = {index: pos for pos, index in enumerate(wordful)}
    for index in lexical_indices:
        source_chunk = tokens[index]
        leading, trailing = _whitespace_edges(source_chunk)
        if index not in wordful_position:
            replacements[index] = source_chunk
            continue
        pos = wordful_position[index]
        source_count = len(_WORD.findall(source_chunk))
        if pos == len(wordful) - 1:
            end = len(target_words)
        else:
            end = min(len(target_words), max(start + 1, round(len(target_words) * source_count / total)))
        replacements[index] = leading + " ".join(target_words[start:end]) + trailing
        start = end
    return "".join(replacements.get(i, token) for i, token in enumerate(tokens))


__all__ = ["rc4_replace_source_payload"]
