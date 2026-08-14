"""Human review/versioning layer for the permanent anime subtitle library.

This module is intentionally outside the V2.1.3 linguistic core.  It never
calls Ollama and it only edits the semantic text field of an ASS event while
retaining the event envelope and validating structural invariants.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anime_subtitle_library import AnimeSubtitleLibrary, LibraryError, ObjectIntegrityError


class ReviewError(LibraryError):
    pass


class SourceVersionMismatch(ReviewError):
    code = "SOURCE_VERSION_MISMATCH"


TAG_RE = re.compile(r"\{[^{}]*\}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_session (
    id INTEGER PRIMARY KEY,
    subtitle_record_id INTEGER NOT NULL REFERENCES subtitle_record(id),
    episode_id INTEGER NOT NULL REFERENCES media_episode(id),
    status TEXT NOT NULL CHECK(status IN ('OPEN','COMPLETED','ABANDONED')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    created_by TEXT NOT NULL,
    notes TEXT,
    base_record_sha256 TEXT NOT NULL,
    materialized_record_id INTEGER REFERENCES subtitle_record(id)
);
CREATE TABLE IF NOT EXISTS segment_review (
    id INTEGER PRIMARY KEY,
    review_session_id INTEGER NOT NULL REFERENCES review_session(id) ON DELETE CASCADE,
    source_record_id INTEGER REFERENCES subtitle_record(id),
    translated_record_id INTEGER NOT NULL REFERENCES subtitle_record(id),
    event_index INTEGER NOT NULL,
    start_time TEXT,
    end_time TEXT,
    source_text TEXT NOT NULL,
    generated_text TEXT NOT NULL,
    source_text_hash TEXT NOT NULL,
    generated_text_hash TEXT NOT NULL,
    base_record_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('UNREVIEWED','OK','NEEDS_CORRECTION','CORRECTED','APPROVED','REJECTED','SOURCE_VERSION_MISMATCH')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(review_session_id,event_index)
);
CREATE TABLE IF NOT EXISTS human_correction (
    id INTEGER PRIMARY KEY,
    segment_review_id INTEGER NOT NULL REFERENCES segment_review(id) ON DELETE CASCADE,
    parent_subtitle_record_id INTEGER NOT NULL REFERENCES subtitle_record(id),
    source_text TEXT NOT NULL,
    generated_text TEXT NOT NULL,
    corrected_text TEXT NOT NULL,
    reason TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('DRAFT','REVIEWED','APPROVED','REJECTED','SUPERSEDED')),
    memory_eligible INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS approval_event (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    reason TEXT,
    metadata TEXT
);
CREATE TABLE IF NOT EXISTS audit_event (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER,
    previous_state TEXT,
    new_state TEXT,
    actor TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_record ON review_session(subtitle_record_id);
CREATE INDEX IF NOT EXISTS idx_segment_status ON segment_review(status);
CREATE INDEX IF NOT EXISTS idx_correction_status ON human_correction(status);
"""


class HumanFeedbackService:
    def __init__(self, library: AnimeSubtitleLibrary) -> None:
        self.library = library
        with self.library._db() as db:
            db.executescript(REVIEW_SCHEMA)

    def _audit(self, db: sqlite3.Connection, event_type: str, target_type: str, target_id: int | None, previous: Any, new: Any, actor: str, metadata: Any = None) -> None:
        db.execute("INSERT INTO audit_event(event_type,target_type,target_id,previous_state,new_state,actor,metadata,created_at) VALUES (?,?,?,?,?,?,?,?)", (event_type, target_type, target_id, _json(previous) if not isinstance(previous, str) else previous, _json(new) if not isinstance(new, str) else new, actor, _json(metadata) if metadata is not None else None, _now()))

    def _approval(self, db: sqlite3.Connection, target_type: str, target_id: int, previous: str | None, new: str, actor: str, reason: str | None, metadata: Any = None) -> None:
        db.execute("INSERT INTO approval_event(target_type,target_id,previous_status,new_status,approved_at,approved_by,reason,metadata) VALUES (?,?,?,?,?,?,?,?)", (target_type, target_id, previous, new, _now(), actor, reason, _json(metadata) if metadata is not None else None))

    @staticmethod
    def _parse_ass(path: Path) -> list[dict[str, Any]]:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8")
        events: list[dict[str, Any]] = []
        in_events = False
        fmt = ["Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR", "MarginV", "Effect", "Text"]
        for raw in text.splitlines(keepends=True):
            stripped = raw.rstrip("\r\n")
            if stripped.strip().lower() == "[events]":
                in_events = True; continue
            if in_events and stripped.startswith("Format:"):
                fmt = [item.strip() for item in stripped.split(":", 1)[1].split(",")]
            if not in_events or not stripped.startswith("Dialogue:"):
                continue
            values = stripped.split(":", 1)[1].split(",", len(fmt) - 1)
            fields = dict(zip(fmt, values + [""] * max(0, len(fmt) - len(values))))
            events.append({"index": len(events), "raw": raw, "prefix": raw[: len(raw) - len(raw.lstrip())], "start": fields.get("Start", "").strip(), "end": fields.get("End", "").strip(), "style": fields.get("Style", "").strip(), "name": fields.get("Name", "").strip(), "effect": fields.get("Effect", "").strip(), "text": fields.get("Text", "")})
        return events

    @staticmethod
    def _replace_event_text(raw: str, new_text: str) -> str:
        newline = "\n" if raw.endswith("\n") else ""
        body = raw[:-1] if newline else raw
        if body.endswith("\r"): body = body[:-1]
        parts = body.split(":", 1)
        if len(parts) != 2: raise ReviewError("evento ASS inválido")
        # Text is the final ASS field; all commas inside it are semantic text.
        head, payload = parts
        fields = payload.split(",")
        if len(fields) < 2: raise ReviewError("evento ASS sem campos")
        # Format normally has ten fields; use the last field to preserve commas.
        first = payload.split(",", 9)
        if len(first) < 10: raise ReviewError("evento ASS sem campo Text")
        first[-1] = new_text
        return head + ":" + ",".join(first) + newline

    @staticmethod
    def _structural_tags(text: str) -> list[str]:
        return TAG_RE.findall(text)

    @classmethod
    def _apply_semantic_edit(cls, generated: str, corrected: str) -> str:
        if "\r" in corrected or "\n" in corrected:
            raise ReviewError("A correção não pode conter quebra de linha física")
        if generated.count("\\N") != corrected.count("\\N"):
            raise ReviewError("Esta correção alteraria o número de \\N estruturais")
        tags = cls._structural_tags(generated)
        corrected_tags = cls._structural_tags(corrected)
        if tags:
            if corrected_tags == tags:
                return corrected
            # Prefix/suffix style spans can be retained without exposing tags
            # as editable content. Inline spans require a new explicit review.
            leading = re.match(r"^(?:\{[^{}]*\})+", generated)
            trailing = re.search(r"(?:\{[^{}]*\})+$", generated)
            inline = generated[len(leading.group(0)) if leading else 0: len(generated) - len(trailing.group(0)) if trailing else len(generated)]
            if TAG_RE.search(inline):
                raise ReviewError("Esta correção contém tags inline que exigem nova âncora estrutural")
            prefix = leading.group(0) if leading else ""
            suffix = trailing.group(0) if trailing else ""
            return prefix + corrected + suffix
        if cls._structural_tags(corrected):
            raise ReviewError("tags ASS não podem ser introduzidas pelo editor humano")
        return corrected

    @classmethod
    def _editable_projection(cls, text: str) -> tuple[str, bool, str | None]:
        """Return only editable semantic text and lock unsafe inline spans."""
        leading = re.match(r"^(?:\{[^{}]*\})+", text)
        trailing = re.search(r"(?:\{[^{}]*\})+$", text)
        start = len(leading.group(0)) if leading else 0
        end = len(text) - len(trailing.group(0)) if trailing else len(text)
        core = text[start:end]
        if TAG_RE.search(core):
            return text, True, "tags inline exigem reconstrução estrutural segura"
        return core, False, None

    def _record_events(self, record_id: int) -> list[dict[str, Any]]:
        path = self.library.object_path_for_record(record_id)
        return self._parse_ass(path)

    def _parent_source(self, record_id: int, explicit: int | None = None) -> int | None:
        if explicit is not None: return explicit
        for item in self.library.lineage(record_id):
            if item.get("source_record_id") == record_id and item.get("parent_record_id"):
                return int(item["parent_record_id"])
        return None

    def open_session(self, record_id: int, *, source_record_id: int | None = None, created_by: str = "local_operator", notes: str | None = None) -> dict[str, Any]:
        record = self.library.get_record(record_id)
        if not record or record.get("classification") != "ANIME": raise ReviewError("somente registros de anime podem ser revisados")
        source_record_id = self._parent_source(record_id, source_record_id)
        generated_events = self._record_events(record_id)
        source_events = self._record_events(source_record_id) if source_record_id else []
        with self.library._db() as db:
            # A browser refresh must not create a second live review for the
            # same immutable base.  Reuse the existing OPEN session when its
            # identity still matches; a changed base naturally gets a new
            # session and is handled by the identity check below.
            existing = db.execute("SELECT id FROM review_session WHERE subtitle_record_id=? AND status='OPEN' AND base_record_sha256=? ORDER BY id DESC LIMIT 1", (record_id, record["sha256"])).fetchone()
            if existing is not None:
                return self.session(int(existing["id"]))
            cur = db.execute("INSERT INTO review_session(subtitle_record_id,episode_id,status,started_at,created_by,notes,base_record_sha256) VALUES (?,?,?,?,?,?,?)", (record_id, record["episode_id"], "OPEN", _now(), created_by, notes, record["sha256"]))
            session_id = int(cur.lastrowid)
            for event in generated_events:
                source = source_events[event["index"]]["text"] if event["index"] < len(source_events) else ""
                db.execute("INSERT INTO segment_review(review_session_id,source_record_id,translated_record_id,event_index,start_time,end_time,source_text,generated_text,source_text_hash,generated_text_hash,base_record_sha256,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (session_id, source_record_id, record_id, event["index"], event["start"], event["end"], source, event["text"], _sha(source), _sha(event["text"]), record["sha256"], "UNREVIEWED", _now(), _now()))
            self._audit(db, "review_started", "REVIEW_SESSION", session_id, None, "OPEN", created_by, {"record_id": record_id, "source_record_id": source_record_id, "events": len(generated_events)})
        return self.session(session_id)

    def session(self, session_id: int) -> dict[str, Any]:
        with self.library._db() as db:
            row = db.execute("SELECT * FROM review_session WHERE id=?", (session_id,)).fetchone()
            if row is None: raise ReviewError("sessão não encontrada")
            segments = [dict(x) for x in db.execute("SELECT * FROM segment_review WHERE review_session_id=? ORDER BY event_index", (session_id,))]
            corrections = [dict(x) for x in db.execute("SELECT * FROM human_correction WHERE segment_review_id IN (SELECT id FROM segment_review WHERE review_session_id=?) ORDER BY id", (session_id,))]
        result = dict(row); result["segments"] = []
        for segment in segments:
            before = segments[max(0, segment["event_index"] - 3):segment["event_index"]]
            after = segments[segment["event_index"] + 1:segment["event_index"] + 4]
            segment["context_before"] = [{"event_index": x["event_index"], "text": x["generated_text"], "source": x["source_text"]} for x in before]
            segment["context_after"] = [{"event_index": x["event_index"], "text": x["generated_text"], "source": x["source_text"]} for x in after]
            segment["corrections"] = [c for c in corrections if c["segment_review_id"] == segment["id"]]
            segment["editable_text"], segment["editing_locked"], segment["editing_lock_reason"] = self._editable_projection(segment["generated_text"])
            result["segments"].append(segment)
        result["corrections"] = corrections
        return result

    def _check_identity(self, segment: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        current = self.library.get_record(int(segment["translated_record_id"]))
        if not current or current.get("sha256") != segment["base_record_sha256"]:
            with self.library._db() as db: db.execute("UPDATE segment_review SET status='SOURCE_VERSION_MISMATCH',updated_at=? WHERE id=?", (_now(), segment["id"]))
            raise SourceVersionMismatch("A versão base mudou desde o início da revisão")
        events = self._record_events(int(segment["translated_record_id"]))
        index = int(segment["event_index"])
        if index >= len(events) or _sha(events[index]["text"]) != segment["generated_text_hash"]:
            with self.library._db() as db: db.execute("UPDATE segment_review SET status='SOURCE_VERSION_MISMATCH',updated_at=? WHERE id=?", (_now(), segment["id"]))
            raise SourceVersionMismatch("O evento fonte/gerado não corresponde mais à revisão")
        source_record_id = segment.get("source_record_id") if isinstance(segment, dict) else segment["source_record_id"]
        if source_record_id:
            source_events = self._record_events(int(source_record_id))
            source_hash = segment.get("source_text_hash") if isinstance(segment, dict) else segment["source_text_hash"]
            if index >= len(source_events) or _sha(source_events[index]["text"]) != source_hash:
                with self.library._db() as db: db.execute("UPDATE segment_review SET status='SOURCE_VERSION_MISMATCH',updated_at=? WHERE id=?", (_now(), segment["id"]))
                raise SourceVersionMismatch("O texto da fonte mudou desde o início da revisão")
        return events[index]

    def save_correction(self, segment_id: int, corrected_text: str, *, reason: str | None = None, notes: str | None = None, created_by: str = "local_operator") -> dict[str, Any]:
        with self.library._db() as db:
            segment = db.execute("SELECT * FROM segment_review WHERE id=?", (segment_id,)).fetchone()
            if segment is None: raise ReviewError("segmento não encontrado")
        event = self._check_identity(segment)
        if TAG_RE.search(corrected_text):
            raise ReviewError("edite somente o texto linguístico; tags ASS são preservadas automaticamente")
        corrected = self._apply_semantic_edit(event["text"], corrected_text)
        with self.library._db() as db:
            current = db.execute("SELECT id,status FROM human_correction WHERE segment_review_id=? AND status IN ('DRAFT','REVIEWED','APPROVED') ORDER BY id DESC LIMIT 1", (segment_id,)).fetchone()
            # An APPROVED correction is immutable history.  A later edit is
            # a new correction and supersedes the old decision; it never
            # rewrites the approved bytes/decision in place.
            if current and current["status"] == "APPROVED":
                db.execute("UPDATE human_correction SET status='SUPERSEDED',updated_at=?,memory_eligible=0 WHERE id=?", (_now(), current["id"]))
                correction_id = db.execute("INSERT INTO human_correction(segment_review_id,parent_subtitle_record_id,source_text,generated_text,corrected_text,reason,notes,created_at,updated_at,created_by,status) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (segment_id, segment["translated_record_id"], segment["source_text"], segment["generated_text"], corrected, reason, notes, _now(), _now(), created_by, "DRAFT")).lastrowid
                audit_type = "correction_created_superseding"
            elif current:
                db.execute("UPDATE human_correction SET corrected_text=?,reason=?,notes=?,updated_at=?,created_by=?,status='DRAFT',memory_eligible=0 WHERE id=?", (corrected, reason, notes, _now(), created_by, current["id"])); correction_id = current["id"]
                audit_type = "correction_updated"
            else:
                correction_id = db.execute("INSERT INTO human_correction(segment_review_id,parent_subtitle_record_id,source_text,generated_text,corrected_text,reason,notes,created_at,updated_at,created_by,status) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (segment_id, segment["translated_record_id"], segment["source_text"], segment["generated_text"], corrected, reason, notes, _now(), _now(), created_by, "DRAFT")).lastrowid
                audit_type = "correction_created"
            db.execute("UPDATE segment_review SET status='CORRECTED',updated_at=? WHERE id=?", (_now(), segment_id))
            self._audit(db, audit_type, "HUMAN_CORRECTION", int(correction_id), current["status"] if current else None, "DRAFT", created_by, {"segment_id": segment_id, "reason": reason, "superseded_id": int(current["id"]) if current and current["status"] == "APPROVED" else None})
            row = db.execute("SELECT * FROM human_correction WHERE id=?", (correction_id,)).fetchone()
        return dict(row)

    def approve_correction(self, correction_id: int, *, approved_by: str = "local_operator", reason: str | None = None) -> dict[str, Any]:
        with self.library._db() as db:
            row = db.execute("SELECT c.*,s.status AS segment_status,s.translated_record_id,s.source_record_id,s.source_text_hash,s.base_record_sha256,s.event_index FROM human_correction c JOIN segment_review s ON s.id=c.segment_review_id WHERE c.id=?", (correction_id,)).fetchone()
            if row is None: raise ReviewError("correção não encontrada")
        self._check_identity({"id": row["segment_review_id"], "translated_record_id": row["translated_record_id"], "source_record_id": row["source_record_id"], "source_text_hash": row["source_text_hash"], "base_record_sha256": row["base_record_sha256"], "event_index": row["event_index"], "generated_text_hash": _sha(row["generated_text"])})
        if row["status"] not in {"DRAFT", "REVIEWED"}: raise ReviewError("somente DRAFT/REVIEWED pode ser aprovado")
        with self.library._db() as db:
            db.execute("UPDATE human_correction SET status='APPROVED',updated_at=?,memory_eligible=1 WHERE id=?", (_now(), correction_id))
            db.execute("UPDATE segment_review SET status='APPROVED',updated_at=? WHERE id=?", (_now(), row["segment_review_id"]))
            self._approval(db, "HUMAN_CORRECTION", correction_id, row["status"], "APPROVED", approved_by, reason, {"segment_id": row["segment_review_id"]})
            self._audit(db, "correction_approved", "HUMAN_CORRECTION", correction_id, row["status"], "APPROVED", approved_by, {"segment_id": row["segment_review_id"]})
            result = db.execute("SELECT * FROM human_correction WHERE id=?", (correction_id,)).fetchone()
        return dict(result)

    def reject_correction(self, correction_id: int, *, rejected_by: str = "local_operator", reason: str | None = None) -> dict[str, Any]:
        with self.library._db() as db:
            row = db.execute("SELECT * FROM human_correction WHERE id=?", (correction_id,)).fetchone()
            if row is None: raise ReviewError("correção não encontrada")
            db.execute("UPDATE human_correction SET status='REJECTED',updated_at=?,memory_eligible=0 WHERE id=?", (_now(), correction_id))
            db.execute("UPDATE segment_review SET status='REJECTED',updated_at=? WHERE id=?", (_now(), row["segment_review_id"]))
            self._approval(db, "HUMAN_CORRECTION", correction_id, row["status"], "REJECTED", rejected_by, reason)
            self._audit(db, "correction_rejected", "HUMAN_CORRECTION", correction_id, row["status"], "REJECTED", rejected_by, {"reason": reason})
            result = db.execute("SELECT * FROM human_correction WHERE id=?", (correction_id,)).fetchone()
        return dict(result)

    def mark_segment(self, segment_id: int, status: str, *, actor: str = "local_operator", reason: str | None = None) -> dict[str, Any]:
        status = status.upper()
        if status not in {"OK", "NEEDS_CORRECTION", "UNREVIEWED"}:
            raise ReviewError("estado de segmento inválido")
        with self.library._db() as db:
            row = db.execute("SELECT status,review_session_id FROM segment_review WHERE id=?", (segment_id,)).fetchone()
            if row is None: raise ReviewError("segmento não encontrado")
            db.execute("UPDATE segment_review SET status=?,updated_at=? WHERE id=?", (status, _now(), segment_id))
            self._audit(db, "segment_review_updated", "SEGMENT_REVIEW", segment_id, row["status"], status, actor, {"reason": reason})
            result = db.execute("SELECT * FROM segment_review WHERE id=?", (segment_id,)).fetchone()
        return dict(result)

    def abandon_session(self, session_id: int, *, actor: str = "local_operator", reason: str | None = None) -> dict[str, Any]:
        with self.library._db() as db:
            row = db.execute("SELECT status FROM review_session WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise ReviewError("sessão não encontrada")
            if row["status"] == "COMPLETED":
                raise ReviewError("sessão já concluída")
            db.execute("UPDATE review_session SET status='ABANDONED',completed_at=? WHERE id=?", (_now(), session_id))
            self._audit(db, "review_abandoned", "REVIEW_SESSION", session_id, row["status"], "ABANDONED", actor, {"reason": reason})
        return self.session(session_id)

    def _segment_event_index(self, segment_id: int) -> int:
        with self.library._db() as db:
            row = db.execute("SELECT event_index FROM segment_review WHERE id=?", (segment_id,)).fetchone()
        if row is None: raise ReviewError("segmento não encontrado")
        return int(row["event_index"])

    def materialize(self, session_id: int, *, created_by: str = "local_operator") -> dict[str, Any]:
        session = self.session(session_id)
        if session.get("materialized_record_id"):
            raise ReviewError("sessão já materializada; crie uma nova revisão sobre a versão resultante")
        corrections = [c for c in session["corrections"] if c["status"] == "APPROVED"]
        if not corrections: raise ReviewError("não há correções APPROVED para materializar")
        parent_id = int(session["subtitle_record_id"]); parent = self.library.get_record(parent_id)
        if not parent: raise ReviewError("versão pai não encontrada")
        base_path = self.library.object_path_for_record(parent_id)
        raw = base_path.read_text(encoding="utf-8-sig")
        lines = raw.splitlines(keepends=True)
        events = self._parse_ass(base_path)
        correction_by_index = {self._segment_event_index(c["segment_review_id"]): c for c in corrections}
        event_line_indexes = [i for i, line in enumerate(lines) if line.rstrip("\r\n").startswith("Dialogue:")]
        if len(event_line_indexes) != len(events): raise ReviewError("contagem de eventos mudou; materialização bloqueada")
        for index, correction in correction_by_index.items():
            if index >= len(events): raise ReviewError("evento de correção não encontrado")
            new_text = self._apply_semantic_edit(events[index]["text"], correction["corrected_text"])
            lines[event_line_indexes[index]] = self._replace_event_text(lines[event_line_indexes[index]], new_text)
        candidate = self.library.staging_root / f"human-corrected-{session_id}-{next(tempfile._get_candidate_names())}.{parent.get('format','ass')}"
        candidate.write_text("".join(lines), encoding="utf-8")
        try:
            if len(self._parse_ass(candidate)) != len(events): raise ReviewError("candidato alterou estrutura ASS")
            for before, after in zip(events, self._parse_ass(candidate)):
                if before["start"] != after["start"] or before["end"] != after["end"] or before["style"] != after["style"] or before["name"] != after["name"] or before["effect"] != after["effect"] or before["text"].count("\\N") != after["text"].count("\\N") or self._structural_tags(before["text"]) != self._structural_tags(after["text"]):
                    raise ReviewError("Esta correção não pôde ser aplicada sem alterar a estrutura da legenda")
            new_record = self.library.ingest_file(candidate, episode_id=int(parent["episode_id"]), language=parent["language"], source_kind="HUMAN_CORRECTED", source_language=parent.get("source_language"), original_filename=f"{Path(parent.get('original_filename') or 'subtitle').stem}.human-corrected.{parent.get('format','ass')}", pipeline_version="v2_1_3", model="qwen3.5:9b", validation_status="VALIDATED", review_status="HUMAN_REVIEWED", events_total=parent.get("events_total"), created_by=created_by, require_authorized_path=False, notes=_json({"parent_record_id": parent_id, "review_session_id": session_id, "human_corrected": True}))
            self.library.add_lineage(int(new_record["id"]), parent_id, "HUMAN_CORRECTED_FROM")
        finally:
            candidate.unlink(missing_ok=True)
        with self.library._db() as db:
            db.execute("UPDATE review_session SET status='COMPLETED',completed_at=?,materialized_record_id=? WHERE id=?", (_now(), new_record["id"], session_id))
            self._audit(db, "version_materialized", "SUBTITLE_RECORD", int(new_record["id"]), "HUMAN_REVIEWED", "HUMAN_CORRECTED", created_by, {"parent_record_id": parent_id, "review_session_id": session_id})
        return self.library.get_record(int(new_record["id"])) or new_record

    def approve_version(self, record_id: int, *, approved_by: str = "local_operator", reason: str | None = None) -> dict[str, Any]:
        record = self.library.get_record(record_id)
        if not record or record.get("source_kind") != "HUMAN_CORRECTED": raise ReviewError("somente versão HUMAN_CORRECTED pode ser aprovada")
        with self.library._db() as db:
            session = db.execute("SELECT * FROM review_session WHERE materialized_record_id=?", (record_id,)).fetchone()
            if session is None: raise ReviewError("sessão da versão não encontrada")
            pending = db.execute("SELECT COUNT(*) AS n FROM human_correction c JOIN segment_review s ON s.id=c.segment_review_id WHERE s.review_session_id=? AND c.status NOT IN ('APPROVED','SUPERSEDED')", (session["id"],)).fetchone()["n"]
            if pending: raise ReviewError("todas as correções incluídas devem estar APPROVED")
            self._approval(db, "SUBTITLE_RECORD", record_id, record.get("review_status"), "APPROVED", approved_by, reason, {"review_session_id": session["id"], "segment_approval_is_separate": True})
            self._audit(db, "version_approved", "SUBTITLE_RECORD", record_id, record.get("review_status"), "APPROVED", approved_by, {"review_session_id": session["id"]})
        return self.library.set_record_review_status(record_id, "APPROVED", preferred=True)

    def audit_events(self, *, target_type: str | None = None, target_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_event"; clauses=[]; params=[]
        if target_type: clauses.append("target_type=?"); params.append(target_type)
        if target_id is not None: clauses.append("target_id=?"); params.append(target_id)
        if clauses: query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        with self.library._db() as db: return [dict(row) for row in db.execute(query, params)]

    def review_counts(self, session_id: int) -> dict[str, int]:
        with self.library._db() as db:
            rows = db.execute("SELECT status,COUNT(*) AS n FROM segment_review WHERE review_session_id=? GROUP BY status", (session_id,)).fetchall()
        result = {key: 0 for key in ("UNREVIEWED", "OK", "NEEDS_CORRECTION", "CORRECTED", "APPROVED", "REJECTED", "SOURCE_VERSION_MISMATCH")}
        result.update({row["status"]: row["n"] for row in rows}); return result
