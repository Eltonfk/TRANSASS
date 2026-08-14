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
    """Replace lexical chunks while retaining source chunk whitespace."""
    tokens = _TAG_OR_H.split(source_part or "")
    lexical_indices = [i for i, token in enumerate(tokens) if token and not _TAG_OR_H.fullmatch(token)]
    if not lexical_indices:
        return source_part
    target_words = re.findall(r"\S+", target_part or "")
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
