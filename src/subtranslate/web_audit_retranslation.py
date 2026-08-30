"""Read-only subtitle auditing and safe library helpers for the web layer.

This module deliberately imports the already approved V2.1.3 parser and
validators.  It never calls Ollama and never writes a sidecar.  Retranslation
orchestration remains in :mod:`app` so the core translation engine is kept
outside this feature.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import pysubs2

from pipeline_v2_1_3 import (
    content_flags,
    delimiter_flags,
    high_confidence_untranslated_dialogue,
    load_english_dictionary,
    load_events,
    validate_inline_tags,
    validate_structure,
)


TEXTUAL_SUBTITLE_CODECS = {
    "ass": ".ass", "ssa": ".ssa", "subrip": ".srt", "srt": ".srt",
    "webvtt": ".vtt", "mov_text": ".srt",
}
BITMAP_SUBTITLE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "vobsub"}
SIDE_CAR_EXTENSIONS = {".ass", ".ssa", ".srt"}
ENGLISH_NAMES = {"en", "eng", "english", "en-us", "en_us", "en-gb", "en_gb"}

# Source language selection map: pt-BR display name -> subtitle track/sidecar
# language codes (ISO 639-2/3 and common short forms).  The target language is
# always Brazilian Portuguese; only the source is configurable.
LANGUAGE_NAME_TO_CODES = {
    "inglês": ["eng", "en", "english"],
    "espanhol": ["spa", "es", "esp", "spanish"],
    "japonês": ["jpn", "ja", "jap", "japanese"],
    "francês": ["fre", "fra", "fr", "french"],
    "coreano": ["kor", "ko", "korean"],
    "chinês": ["chi", "zho", "zh", "chinese", "cmn"],
    "alemão": ["ger", "de", "german"],
    "italiano": ["ita", "it", "italian"],
    "russo": ["rus", "ru", "russian"],
    "português": ["por", "pt", "portuguese"],
}
CODE_TO_LANGUAGE_NAME = {}
for _name, _codes in LANGUAGE_NAME_TO_CODES.items():
    for _code in _codes:
        CODE_TO_LANGUAGE_NAME[_code] = _name


def _normalize_lang_code(code: object) -> str:
    return str(code or "").strip().casefold()


def _language_matches(track_lang: object, source_language: str) -> bool:
    """True when a track/sidecar language matches the configured source language."""
    track = _normalize_lang_code(track_lang)
    if not track or track == "und":
        return False
    codes = LANGUAGE_NAME_TO_CODES.get(source_language, [source_language.casefold()])
    return track in codes


def _display_language(source_language: str) -> str:
    return source_language.strip().title() if source_language else "Inglês"
TEXT_FORMAT_EXTENSIONS = {
    "ass": ".ass",
    "ssa": ".ssa",
    "srt": ".srt",
    "subrip": ".srt",
}

# These events are intentionally copied from the source by the approved
# pipelines.  Running linguistic source-vs-target checks over a byte-identical
# preserved event is both misleading (it will always look "untranslated") and
# unsafe for malformed-but-preserved ASS animation blocks, whose command
# parentheses are not linguistic delimiters.
DETERMINISTIC_PRESERVE_CLASSES = {
    "MUSIC_OR_KARAOKE",
    "ROMAJI_PRESERVED",
    "SONG_LYRICS_PRESERVED",
    "TECHNICAL_OR_EMPTY",
}
MUST_PRESERVE_CLASSES = DETERMINISTIC_PRESERVE_CLASSES - {"ROMAJI_PRESERVED"}

# This is deliberately an audit/review signal, not proof that dialogue stayed
# untranslated.  The stricter TRUE_UNTRANSLATED_DIALOGUE check remains fatal.
REVIEW_ONLY_FLAGS = {"POSSIBLE_UNTRANSLATED_OUTPUT", "LINE_BREAK_INSIDE_WORD", "PRESERVED_EVENT_CHANGED"}

DELIMITER_SOURCE_PRESERVED = "SOURCE_PREEXISTING_UNBALANCED_PRESERVED"
DELIMITER_OUTPUT_INTRODUCED = "OUTPUT_INTRODUCED_UNBALANCE"
DELIMITER_OUTPUT_CHANGED = "OUTPUT_CHANGED_EXISTING_UNBALANCE"
DELIMITER_BALANCED = "BALANCED"


def archive_eligibility(audit: dict[str, Any] | None) -> dict[str, Any]:
    """Return the canonical archive decision for all web/archive callers.

    Findings are not failures by themselves. Review-only findings remain
    attached to the audit; only canonical ``blocking_flags`` prevent archive.
    """
    if not isinstance(audit, dict):
        return {"eligible_for_archive": False, "blocking_flags": [], "review_flags": [], "reason": "auditoria ausente"}
    blocking = sorted({str(item) for item in (audit.get("blocking_flags") or []) if item})
    review = sorted({str(item) for item in (audit.get("review_flags") or []) if item})
    return {
        "eligible_for_archive": not blocking,
        "blocking_flags": blocking,
        "review_flags": review,
        "reason": "bloqueios fatais presentes" if blocking else ("revisão recomendada" if review else "sem bloqueios"),
    }


def _plain(value: str) -> str:
    return re.sub(r"\{[^}]*\}", "", value or "").replace(r"\N", " ").strip()


def _canonical_language_code(source_language: str) -> str:
    """Return the persisted code for a configured display language."""
    normalized = str(source_language or "").strip().casefold()
    codes = LANGUAGE_NAME_TO_CODES.get(normalized)
    return (codes[0] if codes else normalized) or "eng"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _delimiter_balance_signature(value: str) -> dict[str, int]:
    """Count visible paired delimiters without treating ASS braces as prose."""
    visible = _plain(value)
    return {
        "double_quotes": visible.count('"') % 2,
        "parentheses": visible.count("(") - visible.count(")"),
        "brackets": visible.count("[") - visible.count("]"),
    }


def source_relative_delimiter_audit(
    source: str,
    output: str,
    *,
    protected_exact: bool = False,
) -> dict[str, Any]:
    """Separate inherited preserved anomalies from output corruption.

    ``SOURCE_PREEXISTING_UNBALANCED_PRESERVED`` requires the strongest
    available evidence: a deterministic preservation class and byte-identical
    event text.  Changed text never receives that exemption.
    """
    source_signature = _delimiter_balance_signature(source)
    output_signature = _delimiter_balance_signature(output)
    source_core_flags = sorted(set(delimiter_flags(source, source)))
    candidate_core_flags = sorted(set(delimiter_flags(source, output)))
    source_unbalanced = any(source_signature.values()) or bool(source_core_flags)
    output_unbalanced = any(output_signature.values()) or bool(candidate_core_flags)
    exact = source == output
    if source_unbalanced and protected_exact and exact:
        state = DELIMITER_SOURCE_PRESERVED
        fatal = False
    elif source_unbalanced and not exact:
        state = DELIMITER_OUTPUT_CHANGED
        fatal = True
    elif not source_unbalanced and output_unbalanced:
        state = DELIMITER_OUTPUT_INTRODUCED
        fatal = True
    elif source_unbalanced:
        # Byte equality without a proven deterministic preservation class is
        # insufficient to waive a malformed linguistic delimiter.
        state = DELIMITER_OUTPUT_CHANGED
        fatal = True
    else:
        state = DELIMITER_BALANCED
        fatal = False
    return {
        "state": state,
        "fatal": fatal,
        "byte_identical": exact,
        "protected_exact": bool(protected_exact and exact),
        "source_signature": source_signature,
        "output_signature": output_signature,
        "source_core_flags": source_core_flags,
        "candidate_core_flags": candidate_core_flags,
    }


def _line_payload(line: Any) -> dict[str, Any]:
    return {
        "start": int(getattr(line, "start", 0)),
        "end": int(getattr(line, "end", 0)),
        "style": str(getattr(line, "style", "")),
        "name": str(getattr(line, "name", "")),
        "effect": str(getattr(line, "effect", "")),
        "text": str(getattr(line, "text", "")),
    }


def audit_record(source_path: str | Path | None, output_path: str | Path, *, source_language: str = "inglês") -> dict[str, Any]:
    """Audit an existing subtitle without translation or publication.

    With a source, the approved engine parser and structural validator are
    reused.  Without one, only checks that are deterministic from the output
    are performed and the result is explicitly ``AUDITORIA PARCIAL``.
    """
    output_path = Path(output_path)
    if not output_path.is_file():
        raise FileNotFoundError(str(output_path))
    output_subs = pysubs2.load(str(output_path))
    result: dict[str, Any] = {
        "status": "AUDITORIA PARCIAL",
        "source_available": bool(source_path and Path(source_path).is_file()),
        "source_path_present": bool(source_path),
        "output_events": len(output_subs),
        "checks": {},
        "flags": [],
        "blocking_flags": [],
        "review_flags": [],
        "informational_flags": [],
        "eligible_for_archive": False,
        "events": [],
        "ollama_calls": 0,
    }
    if not result["source_available"]:
        output_flags: list[str] = []
        for index, line in enumerate(output_subs):
            text = str(getattr(line, "text", ""))
            if any(token in text for token in ("§T", "§N", "§G")):
                output_flags.append("PLACEHOLDER_LEAK")
            if not _plain(text):
                continue
            result["events"].append({"event_id": index, "timestamp": [int(line.start), int(line.end)], "output": text})
        result["flags"] = sorted(set(output_flags))
        result["blocking_flags"] = result["flags"]
        result["checks"] = {"output_parse": True, "source_comparison": False, "structural": not bool(output_flags)}
        if output_flags:
            result["status"] = "PROBLEMAS DETECTADOS"
        result["reason"] = "fonte original não disponível; somente verificações determinísticas da saída foram executadas"
        return result

    source_path = Path(source_path)
    source_subs, source_events, profile = load_events(source_path, {})
    source_flags: list[str] = []
    informational_flags: list[str] = []
    delimiter_states: Counter[str] = Counter()
    if len(source_subs) != len(output_subs):
        source_flags.append("EVENT_COUNT_MISMATCH")
    structural = validate_structure(source_subs, output_subs)
    source_flags.extend(str(issue).split(": ")[-1] for issue in structural.get("issues", []))
    dictionary = load_english_dictionary("/nonexistent/american-english")
    for index, event in enumerate(source_events):
        source_line = source_subs[index]
        output_line = output_subs[index] if index < len(output_subs) else None
        source_text = str(getattr(source_line, "text", ""))
        output_text = str(getattr(output_line, "text", "")) if output_line is not None else ""
        event_flags: list[str] = []
        delimiter_audit: dict[str, Any] | None = None
        if output_line is None:
            event_flags.append("MISSING_EVENT")
        else:
            event_flags.extend(validate_inline_tags(source_text, output_text))
            preserved_exactly = event.classification in DETERMINISTIC_PRESERVE_CLASSES and source_text == output_text
            delimiter_audit = source_relative_delimiter_audit(
                source_text,
                output_text,
                protected_exact=preserved_exactly,
            )
            delimiter_states[delimiter_audit["state"]] += 1
            if delimiter_audit["state"] == DELIMITER_SOURCE_PRESERVED:
                informational_flags.append(DELIMITER_SOURCE_PRESERVED)
            elif delimiter_audit["fatal"]:
                event_flags.append("UNBALANCED_DELIMITERS")
            if not preserved_exactly:
                context = {"previous": [], "next": []}
                event_flags.extend(content_flags(event, output_text, context, dictionary, source_language=source_language))
                if high_confidence_untranslated_dialogue(event, _plain(source_text), _plain(output_text), dictionary, source_language=source_language):
                    event_flags.append("TRUE_UNTRANSLATED_DIALOGUE")
                if event.classification in MUST_PRESERVE_CLASSES:
                    event_flags.append("PRESERVED_EVENT_CHANGED")
            if event.classification == "SONG_LYRICS_PRESERVED" and _plain(source_text) != _plain(output_text):
                event_flags.append("SONG_MISMATCH")
            if any(token in output_text for token in ("§T", "§N", "§G")):
                event_flags.append("PLACEHOLDER_LEAK")
        event_flags = sorted(set(event_flags))
        source_flags.extend(event_flags)
        result["events"].append({
            "event_id": event.id,
            "original_index": event.original_index,
            "timestamp": [int(event.start), int(event.end)],
            "style": event.style,
            "classification": event.classification,
            "source": source_text,
            "output": output_text,
            "source_hash": _hash(source_text),
            "output_hash": _hash(output_text),
            "deterministic_preservation": bool(
                output_line is not None
                and event.classification in DETERMINISTIC_PRESERVE_CLASSES
                and source_text == output_text
            ),
            "delimiter_audit": delimiter_audit,
            "flags": event_flags,
        })
    result["flags"] = sorted(set(source_flags))
    result["informational_flags"] = sorted(set(informational_flags))
    result["delimiter_audit_counts"] = dict(sorted(delimiter_states.items()))
    result["review_flags"] = sorted(set(result["flags"]) & REVIEW_ONLY_FLAGS)
    result["blocking_flags"] = sorted(set(result["flags"]) - REVIEW_ONLY_FLAGS)
    # Review findings remain visible metadata; only canonical blocking flags
    # prevent archival.  All callers use the same decision helper.
    result.update(archive_eligibility(result))
    result["checks"] = {
        "source_comparison": True,
        "structural": bool(structural.get("valid")),
        "event_alignment": len(source_subs) == len(output_subs),
        "residual_english": "TRUE_UNTRANSLATED_DIALOGUE" not in result["flags"],
        "song_integrity": "SONG_MISMATCH" not in result["flags"],
    }
    result["profile"] = {key: profile.get(key) for key in ("fingerprint", "song_policy", "recurrent_song_blocks") if key in profile}
    if result["blocking_flags"]:
        result["status"] = "PROBLEMAS DETECTADOS"
    elif result["review_flags"]:
        result["status"] = "REVISÃO RECOMENDADA"
    else:
        result["status"] = "SEM PROBLEMAS DETECTADOS"
    return result


def compare_records(old_path: str | Path, new_path: str | Path) -> dict[str, Any]:
    """Compare semantic visible text while retaining timestamps and source."""
    old = pysubs2.load(str(old_path))
    new = pysubs2.load(str(new_path))
    changed: list[dict[str, Any]] = []
    for index in range(max(len(old), len(new))):
        left = old[index] if index < len(old) else None
        right = new[index] if index < len(new) else None
        left_text = str(getattr(left, "text", "")) if left else None
        right_text = str(getattr(right, "text", "")) if right else None
        if left_text != right_text:
            changed.append({
                "event_id": index,
                "timestamp": [int(getattr(right or left, "start", 0)), int(getattr(right or left, "end", 0))],
                "old": left_text,
                "new": right_text,
                "old_visible": _plain(left_text or ""),
                "new_visible": _plain(right_text or ""),
            })
    return {"old_events": len(old), "new_events": len(new), "changed_events": changed, "changed_count": len(changed)}


def _record_format_extension(record: dict[str, Any]) -> str | None:
    """Return a trusted text extension from record metadata, never object path."""
    raw_format = str(record.get("format") or "").strip().casefold().lstrip(".")
    if raw_format in TEXT_FORMAT_EXTENSIONS:
        return TEXT_FORMAT_EXTENSIONS[raw_format]
    filename = str(record.get("original_filename") or "")
    suffix = Path(filename).suffix.casefold()
    return suffix if suffix in SIDE_CAR_EXTENSIONS else None


def _materialize_library_record(library: Any, record_id: int) -> dict[str, Any]:
    """Create an extension-bearing staging copy of an immutable library object."""
    record = library.get_record(int(record_id))
    if not record:
        return {"available": False, "status": "SOURCE_NOT_FOUND", "reason": "registro de fonte não encontrado"}
    extension = _record_format_extension(record)
    if extension is None:
        return {
            "available": False,
            "status": "SOURCE_FORMAT_UNSUPPORTED",
            "kind": "LIBRARY",
            "display": "Formato de fonte não suportado",
            "reason": f"format metadata não textual: {record.get('format') or 'desconhecido'}",
            "record_id": int(record_id),
        }
    canonical = Path(library.object_path_for_record(int(record_id)))
    staging_root = Path(getattr(library, "staging_root", canonical.parent / ".staging"))
    staging_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=f"source-{int(record_id)}-", dir=str(staging_root)))
    target = directory / f"source-{int(record_id)}{extension}"
    try:
        with canonical.open("rb") as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if hashlib.sha256(target.read_bytes()).hexdigest() != str(record.get("sha256")):
            raise RuntimeError("hash divergente durante materialização da fonte")
        return {
            "available": True,
            "path": str(target),
            "staging_path": str(directory),
            "format": str(record.get("format") or extension.lstrip(".")),
            "record_id": int(record_id),
        }
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def resolve_source_record(library: Any, record_id: int, *, materialize: bool = False) -> dict[str, Any]:
    """Resolve a source using only library record IDs and lineage."""
    record = library.get_record(int(record_id))
    if not record:
        return {"available": False, "reason": "registro não encontrado"}
    if record.get("source_kind") in {"EXTERNAL", "EXTRACTED"}:
        try:
            result = {"available": True, "record_id": record["id"], "path": str(library.object_path_for_record(record["id"])), "old_record_id": record["id"], "format": record.get("format"), "reason": "registro de fonte"}
            if materialize:
                result.update(_materialize_library_record(library, int(record["id"])))
                result["old_record_id"] = record["id"]
                result["reason"] = "registro de fonte materializado"
            return result
        except Exception as exc:
            return {"available": False, "reason": f"fonte arquivada indisponível: {exc}"}
    links = library.lineage(record["id"])
    candidates = [link.get("parent_record_id") for link in links if link.get("source_record_id") == record["id"] and link.get("relation_type") in {"TRANSLATED_FROM", "SOURCE_OF"} and link.get("parent_record_id")]
    for parent_id in candidates:
        parent = library.get_record(int(parent_id))
        if parent and parent.get("source_kind") in {"EXTERNAL", "EXTRACTED"}:
            try:
                result = {"available": True, "record_id": parent["id"], "path": str(library.object_path_for_record(parent["id"])), "old_record_id": record["id"], "format": parent.get("format"), "reason": "fonte resolvida pela linhagem"}
                if materialize:
                    result.update(_materialize_library_record(library, int(parent["id"])))
                    result["old_record_id"] = record["id"]
                    result["reason"] = "fonte da linhagem materializada"
                return result
            except Exception:
                continue
    return {"available": False, "old_record_id": record["id"], "reason": "FONTE ORIGINAL NÃO DISPONÍVEL"}


def record_audit_status(audit: dict[str, Any] | None) -> str:
    return str((audit or {}).get("status") or "NÃO AUDITADA")


def _language_is_english(value: str | None) -> bool:
    raw = str(value or "").strip().casefold().replace("_", "-")
    return raw in ENGLISH_NAMES or raw.startswith("en-")


def _probe_subtitle_tracks(video_path: Path) -> list[dict[str, Any]]:
    """Probe only subtitle streams of one authorized media file."""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "s",
        "-show_entries", "stream=index,codec_name,codec_type:stream_tags=language,title,handler_name:stream_disposition=forced,default",
        "-of", "json", str(video_path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        payload = json.loads(completed.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    tracks: list[dict[str, Any]] = []
    for stream in payload.get("streams", []):
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        codec = str(stream.get("codec_name") or "").casefold()
        tracks.append({
            "index": stream.get("index"),
            "codec": codec,
            "language": tags.get("language"),
            "title": tags.get("title") or tags.get("handler_name"),
            "forced": bool(disposition.get("forced")),
            "default": bool(disposition.get("default")),
            "textual": codec in TEXTUAL_SUBTITLE_CODECS,
            "bitmap": codec in BITMAP_SUBTITLE_CODECS,
        })
    return tracks


def _sidecar_candidates(video_path: Path, source_language: str = "inglês") -> list[Path]:
    """Find explicit sidecars for the configured source language; never recurse.

    When ``source_language`` is None, every textual sidecar is returned
    (used by language discovery, which reports each sidecar's language).
    """
    prefix = video_path.stem.casefold()
    candidates: list[Path] = []
    try:
        siblings = list(video_path.parent.iterdir())
    except OSError:
        return []
    codes = LANGUAGE_NAME_TO_CODES.get(source_language, [source_language.casefold()]) if source_language else None
    pattern = None
    if codes:
        pattern = r"(?:^|[._ -])(" + "|".join(re.escape(c) for c in codes) + r")(?:$|[._ -])"
    for item in siblings:
        if not item.is_file() or item.suffix.casefold() not in SIDE_CAR_EXTENSIONS:
            continue
        stem = item.stem.casefold()
        if not stem.startswith(prefix):
            continue
        tail = stem[len(prefix):]
        if source_language is None:
            candidates.append(item)
        elif pattern and re.search(pattern, tail):
            candidates.append(item)
    # Filtra variantes hearing-impaired (.hi.*) quando há sidecar primário.
    # Ex: se existem ".pt-BR.ass" e ".pt-BR.hi.srt", retorna só o primário.
    if len(candidates) > 1:
        primary = [c for c in candidates if ".hi." not in c.stem.casefold()]
        if primary:
            return sorted(primary)
    return sorted(candidates)


def _track_is_signs_or_songs(track: dict[str, Any]) -> bool:
    title = str(track.get("title") or "").casefold()
    return bool(re.search(r"\b(signs?|songs?|karaoke|lyrics?)\b", title))


def _select_track_for_language(tracks: list[dict[str, Any]], source_language: str = "inglês") -> tuple[dict[str, Any] | None, str | None, list[dict[str, Any]]]:
    """Select a dialogue track for the configured source language.

    Mirrors the previous English-only logic but is language-agnostic: it picks
    the textual, non-signs/songs track whose language matches ``source_language``.
    """
    matching = [track for track in tracks if _language_matches(track.get("language"), source_language)]
    textual = [track for track in matching if track.get("textual")]
    bitmap = [track for track in matching if track.get("bitmap")]
    dialogue = [track for track in textual if not _track_is_signs_or_songs(track)]
    if len(dialogue) == 1:
        return dialogue[0], None, bitmap
    if len(dialogue) > 1:
        preferred = [track for track in dialogue if track.get("default") and not track.get("forced")]
        if len(preferred) == 1:
            return preferred[0], None, bitmap
        return None, f"múltiplas tracks {source_language} textuais plausíveis", bitmap
    if len(textual) == 1:
        # A lone signs/songs track is not a safe full-dialogue source.  Keep
        # it visible as an ambiguity (or alongside a bitmap/PGS track) rather
        # than silently translating the wrong subtitle stream.
        if _track_is_signs_or_songs(textual[0]):
            return None, "somente track Signs/Songs textual; fonte de diálogo não confirmada", bitmap
        return textual[0], None, bitmap
    if len(textual) > 1:
        return None, f"múltiplas tracks {source_language} textuais sem desempate seguro", bitmap
    if bitmap:
        return None, f"PGS/bitmap {source_language} detectada; OCR não suportado", bitmap
    return None, None, bitmap


def _existing_source_by_hash(library: Any, episode_id: int, digest: str) -> dict[str, Any] | None:
    for record in library.list_records(episode_id=int(episode_id)):
        if record.get("source_kind") not in {"EXTERNAL", "EXTRACTED"} or record.get("sha256") != digest:
            continue
        try:
            return {"record_id": int(record["id"]), "path": str(library.object_path_for_record(int(record["id"]))), "record": record}
        except Exception:
            continue
    return None


def _ingest_source(library: Any, episode_id: int, source_path: Path, *, source_kind: str, source_language: str = "inglês", track: dict[str, Any] | None = None, job_id: str | None = None) -> dict[str, Any]:
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    existing = _existing_source_by_hash(library, episode_id, digest)
    if existing:
        return existing
    language_code = _canonical_language_code(source_language)
    record = library.ingest_file(
        source_path, episode_id=int(episode_id), language=language_code, source_kind=source_kind,
        source_language=language_code, original_filename=source_path.name,
        track_index=str(track.get("index")) if track else None,
        track_title=str(track.get("title") or "") if track else None,
        job_id=job_id, validation_status="EXTRACTED" if source_kind == "EXTRACTED" else "IMPORTED",
        created_by="web-source-resolver", notes=f"Fonte {language_code} resolvida pela camada web; não é tradução.",
        require_authorized_path=(source_kind != "EXTRACTED"),
    )
    return {"record_id": int(record["id"]), "path": str(library.object_path_for_record(int(record["id"]))), "record": record}


def _extract_track(library: Any, episode_id: int, video_path: Path, track: dict[str, Any], *, source_language: str = "inglês", job_id: str | None = None) -> dict[str, Any]:
    extension = TEXTUAL_SUBTITLE_CODECS.get(str(track.get("codec") or "").casefold(), ".ass")
    staging = Path(getattr(library, "staging_root", video_path.parent / ".subtranslate-staging"))
    staging.mkdir(parents=True, exist_ok=True)
    raw = tempfile.NamedTemporaryFile(prefix=f"source-{episode_id}-", suffix=extension, dir=staging, delete=False)
    raw.close()
    target = Path(raw.name)
    command = ["ffmpeg", "-y", "-v", "error", "-i", str(video_path), "-map", f"0:{track['index']}", "-c:s", "copy", str(target)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError((completed.stderr or "falha ao extrair track ENG")[-500:])
        return _ingest_source(library, episode_id, target, source_kind="EXTRACTED", source_language=source_language, track=track, job_id=job_id)
    finally:
        target.unlink(missing_ok=True)


def _public_source_status(result: dict[str, Any]) -> dict[str, Any]:
    """Remove host paths before returning source status to the browser."""
    allowed = {"available", "status", "kind", "display", "reason", "record_id", "track", "candidates"}
    return {key: value for key, value in result.items() if key in allowed}


def resolve_episode_source(library: Any, episode_id: int, record_id: int | None = None, *, materialize: bool = False, job_id: str | None = None, source_language: str = "inglês") -> dict[str, Any]:
    """Resolve an original textual source for an anime episode.

    Priority is library lineage, explicit sidecar for ``source_language``, then
    one unambiguous textual track in that language in the associated MKV.
    Bitmap subtitles are reported, never treated as text.  The target language
    is always Brazilian Portuguese; only the source is configurable.
    """
    lang_label = _display_language(source_language)
    old = library.get_record(int(record_id)) if record_id else None
    if old:
        linked = resolve_source_record(library, int(old["id"]), materialize=materialize)
        if linked.get("available"):
            fmt = str(linked.get("format") or "").upper()
            result = {**linked, "status": "SOURCE_AVAILABLE_LIBRARY", "kind": "LIBRARY", "display": f"{lang_label} {fmt} — Biblioteca".strip()}
            return result if materialize else {**result, "path": result.get("path")}
    try:
        video_path = library._episode_video(int(episode_id))
    except Exception as exc:
        return {"available": False, "status": "SOURCE_NOT_FOUND", "display": "Fonte não encontrada", "reason": str(exc)}

    sidecars = _sidecar_candidates(video_path, source_language)
    if len(sidecars) == 1:
        source_path = sidecars[0]
        if materialize:
            ingested = _ingest_source(library, episode_id, source_path, source_kind="EXTERNAL", source_language=source_language, job_id=job_id)
            if materialize:
                ingested.update(_materialize_library_record(library, int(ingested["record_id"])))
            return {"available": True, "status": "SOURCE_AVAILABLE_SIDECAR", "kind": "SIDECAR_TEXT", "display": f"{lang_label} ASS/SRT — Sidecar", **ingested}
        return {"available": True, "status": "SOURCE_AVAILABLE_SIDECAR", "kind": "SIDECAR_TEXT", "display": f"{source_path.suffix[1:].upper()} — Sidecar {lang_label}", "path": str(source_path), "candidates": [source_path.name]}
    if len(sidecars) > 1:
        return {"available": False, "status": "SOURCE_AMBIGUOUS", "kind": "SIDECAR_TEXT", "display": f"Múltiplos sidecars {lang_label}", "reason": "escolha explícita necessária", "candidates": [item.name for item in sidecars]}

    tracks = _probe_subtitle_tracks(video_path)
    selected, selection_reason, bitmaps = _select_track_for_language(tracks, source_language)
    if selected:
        result = {"available": True, "status": "SOURCE_AVAILABLE_INTERNAL_TEXT", "kind": "EMBEDDED_TEXT", "display": f"{str(selected.get('codec') or '').upper()} — track {lang_label} interna {selected.get('index')}", "track": selected}
        if materialize:
            result.update(_extract_track(library, episode_id, video_path, selected, source_language=source_language, job_id=job_id))
            if result.get("record_id") is not None:
                result.update(_materialize_library_record(library, int(result["record_id"])))
        else:
            result["path"] = None
        return result
    if bitmaps:
        return {"available": False, "status": "SOURCE_AVAILABLE_PGS_UNSUPPORTED", "kind": "PGS", "display": "PGS — OCR não suportado", "reason": selection_reason or f"track {lang_label} bitmap detectada", "track": bitmaps}
    if selection_reason:
        return {"available": False, "status": "SOURCE_AMBIGUOUS", "kind": "EMBEDDED_TEXT", "display": f"Múltiplas tracks {lang_label}", "reason": selection_reason, "track": tracks}
    return {"available": False, "status": "SOURCE_NOT_FOUND", "kind": "NONE", "display": f"Fonte {lang_label} não encontrada", "reason": f"nenhuma fonte {lang_label} textual arquivada, sidecar ou track interna"}


def detect_source_options(video_path: Path) -> list[dict[str, Any]]:
    """Discover every translatable subtitle source for an episode.

    Returns sidecars and embedded tracks of any language (textual or bitmap),
    each annotated with its detected language, whether it is signs/songs only,
    and whether it is a safe dialogue source.  The UI uses this to let the user
    pick which language to translate.
    """
    options: list[dict[str, Any]] = []
    for sidecar in _sidecar_candidates(video_path, source_language=None):
        stem = sidecar.stem.casefold()
        prefix = video_path.stem.casefold()
        tail = stem[len(prefix):] if stem.startswith(prefix) else stem
        detected = None
        for code, name in CODE_TO_LANGUAGE_NAME.items():
            if re.search(r"(?:^|[._ -])" + re.escape(code) + r"(?:$|[._ -])", tail):
                detected = name
                break
        options.append({
            "kind": "sidecar",
            "language": detected or "desconhecido",
            "name": sidecar.name,
            "path": str(sidecar),
            "textual": sidecar.suffix.casefold() in SIDE_CAR_EXTENSIONS,
            "bitmap": False,
            "signs_songs_only": False,
        })
    for track in _probe_subtitle_tracks(video_path):
        lang_code = _normalize_lang_code(track.get("language"))
        detected = CODE_TO_LANGUAGE_NAME.get(lang_code, lang_code or "desconhecido")
        options.append({
            "kind": "track",
            "language": detected,
            "index": track.get("index"),
            "title": track.get("title"),
            "codec": track.get("codec"),
            "textual": bool(track.get("textual")),
            "bitmap": bool(track.get("bitmap")),
            "signs_songs_only": _track_is_signs_or_songs(track),
            "default": bool(track.get("default")),
            "forced": bool(track.get("forced")),
        })
    return options
