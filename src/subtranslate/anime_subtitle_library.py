"""Persistent, anime-only subtitle archive.

This module is deliberately independent from the V2.1.3 translation engine.
It owns provenance, immutable content-addressed storage and Jellyfin copies at
the edges of a translation job.  No prompt, parser or validator is imported
here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CLASSIFICATIONS = {"ANIME", "NON_ANIME", "UNKNOWN"}
SOURCE_KINDS = {
    "EXTRACTED", "EXTERNAL", "TRANSLATED", "IMPORTED_EXISTING",
    "UPLOAD", "HUMAN_CORRECTED",
}
REVIEW_STATUSES = {"GENERATED", "VALIDATED", "HUMAN_REVIEWED", "APPROVED", "REJECTED"}
PUBLICATION_STATUSES = {"NOT_PUBLISHED", "PUBLISHED", "PUBLICATION_FAILED", "REPLACED"}


class LibraryError(RuntimeError):
    """Base error for safe API error handling."""


class PathSecurityError(LibraryError):
    pass


class ClassificationError(LibraryError):
    pass


class PublicationConflict(LibraryError):
    pass


class ObjectIntegrityError(LibraryError):
    pass


class LineageIntegrityError(LibraryError):
    """Raised when a subtitle-record provenance edge is not referentially safe."""

    def __init__(self, code: str, **details: Any):
        self.code = str(code)
        self.details = {str(key): value for key, value in details.items()}
        super().__init__(self.code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normal_title(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS media_series (
    id INTEGER PRIMARY KEY,
    series_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    library_relative_path TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('ANIME','NON_ANIME','UNKNOWN')),
    classification_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_classification (
    id INTEGER PRIMARY KEY,
    series_id INTEGER NOT NULL REFERENCES media_series(id) ON DELETE CASCADE,
    classification TEXT NOT NULL CHECK (classification IN ('ANIME','NON_ANIME','UNKNOWN')),
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_episode (
    id INTEGER PRIMARY KEY,
    series_id INTEGER NOT NULL REFERENCES media_series(id) ON DELETE CASCADE,
    season TEXT,
    episode TEXT,
    episode_title TEXT,
    media_relative_path TEXT NOT NULL,
    media_filename TEXT NOT NULL,
    release TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(series_id, media_relative_path)
);
CREATE TABLE IF NOT EXISTS subtitle_object (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    format TEXT NOT NULL,
    storage_path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subtitle_record (
    id INTEGER PRIMARY KEY,
    object_id INTEGER NOT NULL REFERENCES subtitle_object(id),
    episode_id INTEGER NOT NULL REFERENCES media_episode(id),
    language TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('EXTRACTED','EXTERNAL','TRANSLATED','IMPORTED_EXISTING','UPLOAD','HUMAN_CORRECTED')),
    original_filename TEXT,
    source_language TEXT,
    release TEXT,
    track_index TEXT,
    track_title TEXT,
    job_id TEXT,
    pipeline_version TEXT,
    model TEXT,
    validation_status TEXT,
    events_total INTEGER,
    preferred INTEGER NOT NULL DEFAULT 0,
    review_status TEXT CHECK (review_status IS NULL OR review_status IN ('GENERATED','VALIDATED','HUMAN_REVIEWED','APPROVED','REJECTED')),
    notes TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT
);
CREATE TABLE IF NOT EXISTS subtitle_lineage (
    id INTEGER PRIMARY KEY,
    source_record_id INTEGER NOT NULL REFERENCES subtitle_record(id),
    parent_record_id INTEGER REFERENCES subtitle_record(id),
    relation_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_record_id, parent_record_id, relation_type)
);
CREATE TABLE IF NOT EXISTS publication (
    id INTEGER PRIMARY KEY,
    subtitle_record_id INTEGER NOT NULL REFERENCES subtitle_record(id),
    episode_id INTEGER NOT NULL REFERENCES media_episode(id),
    target_relative_path TEXT NOT NULL,
    target_sha256 TEXT,
    status TEXT NOT NULL CHECK (status IN ('NOT_PUBLISHED','PUBLISHED','PUBLICATION_FAILED','REPLACED')),
    published_at TEXT,
    last_verified_at TEXT,
    UNIQUE(subtitle_record_id, target_relative_path)
);
CREATE TABLE IF NOT EXISTS ingest_event (
    id INTEGER PRIMARY KEY,
    episode_id INTEGER REFERENCES media_episode(id),
    source_path TEXT,
    source_kind TEXT,
    object_id INTEGER REFERENCES subtitle_object(id),
    record_id INTEGER REFERENCES subtitle_record(id),
    job_id TEXT,
    result TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episode_series ON media_episode(series_id);
CREATE INDEX IF NOT EXISTS idx_record_episode ON subtitle_record(episode_id);
CREATE INDEX IF NOT EXISTS idx_record_object ON subtitle_record(object_id);
CREATE INDEX IF NOT EXISTS idx_publication_episode ON publication(episode_id);
"""


class AnimeSubtitleLibrary:
    """SQLite metadata + immutable SHA-256 object store."""

    def __init__(self, root: str | Path, *, media_roots: Iterable[str | Path] = ()) -> None:
        self.root = Path(root).expanduser()
        self.objects_root = self.root / "objects" / "sha256"
        self.db_root = self.root / "db"
        self.diagnostics_root = self.root / "diagnostics"
        self.staging_root = self.root / "staging"
        for path in (self.objects_root, self.db_root, self.diagnostics_root, self.staging_root):
            path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_root / "subtitle_library.sqlite3"
        self.media_roots = [Path(p).expanduser().resolve() for p in media_roots if str(p).strip()]
        self._init_db()

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._db() as db:
            db.executescript(SCHEMA)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def _authorized_path(self, path: str | Path, *, must_exist: bool = True) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise PathSecurityError("caminho absoluto exigido internamente")
        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError:
            if must_exist:
                raise
            resolved = candidate.resolve(strict=False)
        if not self.media_roots:
            raise PathSecurityError("nenhuma raiz de mídia autorizada")
        for root in self.media_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise PathSecurityError("caminho fora das raízes de anime autorizadas")

    def _require_anime_series(self, db: sqlite3.Connection, series_id: int) -> sqlite3.Row:
        row = db.execute("SELECT * FROM media_series WHERE id=?", (series_id,)).fetchone()
        if row is None:
            raise LibraryError("série não encontrada")
        if row["classification"] != "ANIME":
            raise ClassificationError(f"ingestão bloqueada para classificação {row['classification']}")
        return row

    def _record_episode(self, db: sqlite3.Connection, episode_id: int) -> sqlite3.Row:
        row = db.execute(
            "SELECT e.*, s.classification, s.title AS series_title, s.series_key "
            "FROM media_episode e JOIN media_series s ON s.id=e.series_id WHERE e.id=?",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise LibraryError("episódio não encontrado")
        if row["classification"] != "ANIME":
            raise ClassificationError(f"ingestão bloqueada para classificação {row['classification']}")
        return row

    def register_series(
        self,
        title: str,
        relative_path: str,
        *,
        classification: str = "UNKNOWN",
        source: str = "USER",
        series_key: str | None = None,
    ) -> dict[str, Any]:
        classification = classification.upper()
        if classification not in CLASSIFICATIONS:
            raise ClassificationError("classificação inválida")
        if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise PathSecurityError("relative_path inválido")
        key = series_key or relative_path.strip("/")
        now = _now()
        with self._db() as db:
            row = db.execute("SELECT * FROM media_series WHERE series_key=?", (key,)).fetchone()
            if row is None:
                cur = db.execute(
                    "INSERT INTO media_series(series_key,title,normalized_title,library_relative_path,classification,classification_source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (key, title, _normal_title(title), relative_path.strip("/"), classification, source, now, now),
                )
                series_id = cur.lastrowid
            else:
                series_id = row["id"]
                db.execute(
                    "UPDATE media_series SET title=?,normalized_title=?,library_relative_path=?,classification=?,classification_source=?,updated_at=? WHERE id=?",
                    (title, _normal_title(title), relative_path.strip("/"), classification, source, now, series_id),
                )
            db.execute(
                "INSERT INTO media_classification(series_id,classification,source,created_at,updated_at) VALUES (?,?,?,?,?)",
                (series_id, classification, source, now, now),
            )
            result = db.execute("SELECT * FROM media_series WHERE id=?", (series_id,)).fetchone()
        return self._row(result) or {}

    def set_classification(self, series_id: int, classification: str, *, source: str = "USER") -> dict[str, Any]:
        classification = classification.upper()
        if classification not in CLASSIFICATIONS:
            raise ClassificationError("classificação inválida")
        now = _now()
        with self._db() as db:
            if db.execute("SELECT 1 FROM media_series WHERE id=?", (series_id,)).fetchone() is None:
                raise LibraryError("série não encontrada")
            db.execute("UPDATE media_series SET classification=?,classification_source=?,updated_at=? WHERE id=?", (classification, source, now, series_id))
            db.execute("INSERT INTO media_classification(series_id,classification,source,created_at,updated_at) VALUES (?,?,?,?,?)", (series_id, classification, source, now, now))
            row = db.execute("SELECT * FROM media_series WHERE id=?", (series_id,)).fetchone()
        return self._row(row) or {}

    def list_series(self, *, classification: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM media_series"
        params: list[Any] = []
        if classification:
            query += " WHERE classification=?"
            params.append(classification.upper())
        query += " ORDER BY normalized_title"
        with self._db() as db:
            return [self._row(row) for row in db.execute(query, params)]

    def register_episode(
        self,
        series_id: int,
        *,
        season: str | None,
        episode: str | None,
        episode_title: str | None,
        media_relative_path: str,
        media_filename: str,
        release: str | None = None,
    ) -> dict[str, Any]:
        with self._db() as db:
            self._require_anime_series(db, series_id)
            existing = db.execute("SELECT id FROM media_episode WHERE series_id=? AND media_relative_path=?", (series_id, media_relative_path)).fetchone()
            if existing:
                db.execute("UPDATE media_episode SET season=?,episode=?,episode_title=?,media_filename=?,release=? WHERE id=?", (season, episode, episode_title, media_filename, release, existing["id"]))
                episode_id = existing["id"]
            else:
                cur = db.execute(
                    "INSERT INTO media_episode(series_id,season,episode,episode_title,media_relative_path,media_filename,release,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (series_id, season, episode, episode_title, media_relative_path, media_filename, release, _now()),
                )
                episode_id = cur.lastrowid
            row = db.execute("SELECT e.*,s.title AS series_title,s.classification FROM media_episode e JOIN media_series s ON s.id=e.series_id WHERE e.id=?", (episode_id,)).fetchone()
        return self._row(row) or {}

    def register_episode_for_path(self, series_id: int, media_path: str | Path, **metadata: Any) -> dict[str, Any]:
        path = self._authorized_path(media_path)
        root = None
        for candidate_root in self.media_roots:
            try:
                relative = path.relative_to(candidate_root).as_posix()
                root = candidate_root
                break
            except ValueError:
                continue
        if root is None:
            raise PathSecurityError("mídia fora das raízes autorizadas")
        return self.register_episode(series_id, media_relative_path=relative, media_filename=path.name, **metadata)

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _format(path: Path) -> str:
        suffix = path.suffix.lower().lstrip(".")
        if suffix not in {"ass", "ssa", "srt"}:
            raise LibraryError("formato de legenda não suportado")
        if path.stat().st_size == 0:
            raise LibraryError("legenda vazia")
        return suffix

    def _object_path(self, sha256: str) -> Path:
        return self.objects_root / sha256[:2] / sha256

    def _set_canonical_object_permissions(self, path: Path) -> None:
        """Make an immutable object readable by the service account.

        The archive normally runs as the owner of ``self.root``.  A controlled
        diagnostic or local-operator workflow can instead materialize a staged
        file as a different UID, and ``os.replace`` preserves that UID and its
        restrictive ``0600`` mode.  That creates a valid content-addressed
        object which the normal web service cannot read.  Normalize ownership
        before the atomic replace, then verify it rather than silently storing
        an inaccessible object.
        """
        library_stat = self.root.stat()
        expected_uid, expected_gid = library_stat.st_uid, library_stat.st_gid
        try:
            os.chown(path, expected_uid, expected_gid)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise LibraryError("não foi possível aplicar permissões canônicas ao objeto") from exc
        object_stat = path.stat()
        if (
            object_stat.st_uid != expected_uid
            or object_stat.st_gid != expected_gid
            or (object_stat.st_mode & 0o777) != 0o600
        ):
            raise LibraryError("objeto não possui permissões canônicas legíveis pelo serviço")

    def _ensure_object(self, source: Path, fmt: str, sha256: str, size: int) -> tuple[int, Path]:
        target = self._object_path(sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing_sha, existing_size = self._hash_file(target)
            if existing_sha != sha256 or existing_size != size:
                raise ObjectIntegrityError("objeto existente não corresponde ao hash")
        else:
            fd, raw = tempfile.mkstemp(prefix=".object-", dir=str(self.staging_root))
            staged = Path(raw)
            try:
                with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
                    shutil.copyfileobj(inp, out, length=1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                staged_sha, staged_size = self._hash_file(staged)
                if staged_sha != sha256 or staged_size != size:
                    raise ObjectIntegrityError("hash divergente durante staging")
                self._set_canonical_object_permissions(staged)
                os.replace(staged, target)
                dir_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            finally:
                staged.unlink(missing_ok=True)
        rel = target.relative_to(self.root).as_posix()
        with self._db() as db:
            row = db.execute("SELECT id FROM subtitle_object WHERE sha256=?", (sha256,)).fetchone()
            if row is None:
                cur = db.execute("INSERT INTO subtitle_object(sha256,size_bytes,format,storage_path,created_at) VALUES (?,?,?,?,?)", (sha256, size, fmt, rel, _now()))
                return int(cur.lastrowid), target
            return int(row["id"]), target

    def ingest_file(
        self,
        source_path: str | Path,
        *,
        episode_id: int,
        language: str,
        source_kind: str,
        source_language: str | None = None,
        original_filename: str | None = None,
        release: str | None = None,
        track_index: str | None = None,
        track_title: str | None = None,
        job_id: str | None = None,
        pipeline_version: str | None = None,
        model: str | None = None,
        validation_status: str | None = None,
        events_total: int | None = None,
        preferred: bool = False,
        review_status: str | None = None,
        created_by: str | None = None,
        notes: str | None = None,
        require_authorized_path: bool = True,
    ) -> dict[str, Any]:
        source_kind = source_kind.upper()
        if source_kind not in SOURCE_KINDS:
            raise LibraryError("source_kind inválido")
        if review_status is not None and review_status not in REVIEW_STATUSES:
            raise LibraryError("review_status inválido")
        path = self._authorized_path(source_path) if require_authorized_path else Path(source_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(str(path))
        fmt = self._format(path)
        sha256, size = self._hash_file(path)
        with self._db() as db:
            episode = self._record_episode(db, episode_id)
        object_id, target = self._ensure_object(path, fmt, sha256, size)
        rel_source = str(path)
        with self._db() as db:
            row = db.execute("SELECT * FROM subtitle_object WHERE id=?", (object_id,)).fetchone()
            if row is None or not target.exists():
                raise ObjectIntegrityError("metadata sem objeto físico")
            cur = db.execute(
                "INSERT INTO subtitle_record(object_id,episode_id,language,source_kind,original_filename,source_language,release,track_index,track_title,job_id,pipeline_version,model,validation_status,events_total,preferred,review_status,notes,created_at,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (object_id, episode_id, language, source_kind, original_filename or path.name, source_language, release, track_index, track_title, job_id, pipeline_version, model, validation_status, events_total, int(preferred), review_status, notes, _now(), created_by),
            )
            record_id = int(cur.lastrowid)
            db.execute("INSERT INTO ingest_event(episode_id,source_path,source_kind,object_id,record_id,job_id,result,created_at) VALUES (?,?,?,?,?,?,?,?)", (episode_id, rel_source, source_kind, object_id, record_id, job_id, "INGESTED", _now()))
            result = db.execute("SELECT * FROM subtitle_record WHERE id=?", (record_id,)).fetchone()
        return self._public_record(result, object_sha=sha256, size=size)

    def _public_record(self, row: sqlite3.Row | dict[str, Any] | None, *, object_sha: str | None = None, size: int | None = None) -> dict[str, Any]:
        data = dict(row) if row is not None else {}
        if object_sha is not None:
            data["sha256"] = object_sha
        if size is not None:
            data["size_bytes"] = size
        # storage_path is intentionally never exposed to the browser/API.
        data.pop("storage_path", None)
        return data

    def get_record(self, record_id: int) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(
                "SELECT r.*,o.sha256,o.size_bytes,o.format,e.season,e.episode,e.episode_title,e.media_filename,e.media_relative_path,s.id AS series_id,s.title AS series_title,s.classification "
                "FROM subtitle_record r JOIN subtitle_object o ON o.id=r.object_id JOIN media_episode e ON e.id=r.episode_id JOIN media_series s ON s.id=e.series_id WHERE r.id=?",
                (record_id,),
            ).fetchone()
        return self._public_record(row) if row else None

    def set_record_review_status(self, record_id: int, status: str, *, preferred: bool | None = None, notes: str | None = None) -> dict[str, Any]:
        """Change human review metadata without changing the immutable object."""
        status = status.upper()
        if status not in REVIEW_STATUSES:
            raise LibraryError("review_status inválido")
        with self._db() as db:
            if db.execute("SELECT 1 FROM subtitle_record WHERE id=?", (record_id,)).fetchone() is None:
                raise LibraryError("legenda não encontrada")
            assignments = ["review_status=?"]
            params: list[Any] = [status]
            if preferred is not None:
                assignments.append("preferred=?"); params.append(int(preferred))
            if notes is not None:
                assignments.append("notes=?"); params.append(notes)
            params.append(record_id)
            db.execute(f"UPDATE subtitle_record SET {', '.join(assignments)} WHERE id=?", params)
        return self.get_record(record_id) or {}

    def list_records(self, *, series_id: int | None = None, episode_id: int | None = None, language: str | None = None, source_kind: str | None = None, published: bool | None = None) -> list[dict[str, Any]]:
        query = "SELECT r.*,o.sha256,o.size_bytes,o.format,e.season,e.episode,e.episode_title,e.media_filename,e.media_relative_path,s.id AS series_id,s.title AS series_title,s.classification FROM subtitle_record r JOIN subtitle_object o ON o.id=r.object_id JOIN media_episode e ON e.id=r.episode_id JOIN media_series s ON s.id=e.series_id"
        clauses: list[str] = []
        params: list[Any] = []
        if series_id is not None:
            clauses.append("s.id=?"); params.append(series_id)
        if episode_id is not None:
            clauses.append("e.id=?"); params.append(episode_id)
        if language:
            clauses.append("r.language=?"); params.append(language)
        if source_kind:
            clauses.append("r.source_kind=?"); params.append(source_kind)
        if published is not None:
            clauses.append("EXISTS (SELECT 1 FROM publication p WHERE p.subtitle_record_id=r.id AND p.status='PUBLISHED')" if published else "NOT EXISTS (SELECT 1 FROM publication p WHERE p.subtitle_record_id=r.id AND p.status='PUBLISHED')")
        if clauses: query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY r.created_at DESC"
        with self._db() as db:
            return [self._public_record(row) for row in db.execute(query, params)]

    def list_episodes(self, series_id: int) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT e.*,s.title AS series_title,s.classification FROM media_episode e JOIN media_series s ON s.id=e.series_id WHERE e.series_id=? ORDER BY e.season,e.episode,e.media_filename", (series_id,)).fetchall()
            result = []
            for row in rows:
                item = self._row(row) or {}
                item["records"] = self.list_records(episode_id=row["id"])
                result.append(item)
            return result

    def lineage(self, record_id: int) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT l.*, sr.language AS source_language, sr.source_kind AS source_kind, pr.language AS parent_language, pr.source_kind AS parent_kind FROM subtitle_lineage l JOIN subtitle_record sr ON sr.id=l.source_record_id LEFT JOIN subtitle_record pr ON pr.id=l.parent_record_id WHERE l.source_record_id=? OR l.parent_record_id=? ORDER BY l.id", (record_id, record_id)).fetchall()
        return [self._row(row) or {} for row in rows]

    def add_lineage(self, source_record_id: int, parent_record_id: int | None, relation_type: str) -> dict[str, Any]:
        try:
            child_id = int(source_record_id)
        except (TypeError, ValueError) as exc:
            raise LineageIntegrityError("lineage_child_record_missing", child_record_id=source_record_id) from exc
        if parent_record_id is None:
            raise LineageIntegrityError("lineage_parent_record_missing", child_record_id=child_id, parent_record_id=None, relation_type=relation_type)
        try:
            parent_id = int(parent_record_id)
        except (TypeError, ValueError) as exc:
            raise LineageIntegrityError("lineage_parent_record_missing", child_record_id=child_id, parent_record_id=parent_record_id, relation_type=relation_type) from exc
        with self._db() as db:
            child = db.execute("SELECT id,episode_id FROM subtitle_record WHERE id=?", (child_id,)).fetchone()
            parent = db.execute("SELECT id,episode_id FROM subtitle_record WHERE id=?", (parent_id,)).fetchone()
            if child is None:
                raise LineageIntegrityError("lineage_child_record_missing", child_record_id=child_id, parent_record_id=parent_id, relation_type=relation_type)
            if parent is None:
                raise LineageIntegrityError("lineage_parent_record_missing", child_record_id=child_id, parent_record_id=parent_id, relation_type=relation_type)
            child_episode = child["episode_id"]
            parent_episode = parent["episode_id"]
            if child_episode is None or parent_episode is None or int(child_episode) != int(parent_episode):
                raise LineageIntegrityError(
                    "lineage_episode_mismatch", child_record_id=child_id, parent_record_id=parent_id,
                    child_episode_id=child_episode, parent_episode_id=parent_episode, relation_type=relation_type,
                )
            cur = db.execute("INSERT OR IGNORE INTO subtitle_lineage(source_record_id,parent_record_id,relation_type,created_at) VALUES (?,?,?,?)", (child_id, parent_id, relation_type, _now()))
            row = db.execute("SELECT * FROM subtitle_lineage WHERE id=?", (cur.lastrowid,)).fetchone() if cur.lastrowid else db.execute("SELECT * FROM subtitle_lineage WHERE source_record_id=? AND parent_record_id=? AND relation_type=?", (child_id, parent_id, relation_type)).fetchone()
        return self._row(row) or {}

    def _episode_video(self, episode_id: int) -> Path:
        with self._db() as db:
            row = db.execute("SELECT media_relative_path FROM media_episode WHERE id=?", (episode_id,)).fetchone()
        if row is None or not self.media_roots:
            raise LibraryError("episódio sem mídia")
        for root in self.media_roots:
            candidate = (root / row["media_relative_path"]).resolve()
            try:
                candidate.relative_to(root)
                return candidate
            except ValueError:
                continue
        raise PathSecurityError("mídia do episódio fora da raiz autorizada")

    def publish(self, record_id: int, *, target_path: str | Path | None = None, allow_replace: bool = False) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT r.*,o.sha256,o.storage_path,o.format,e.id AS episode_id,e.media_relative_path,e.media_filename FROM subtitle_record r JOIN subtitle_object o ON o.id=r.object_id JOIN media_episode e ON e.id=r.episode_id WHERE r.id=?", (record_id,)).fetchone()
            if row is None:
                raise LibraryError("legenda não encontrada")
            if row["validation_status"] not in {"VALIDATED", "OK", "PUBLISHED"}:
                raise LibraryError("somente legenda validada pode ser publicada")
        source = self.root / row["storage_path"]
        if not source.is_file() or self._hash_file(source)[0] != row["sha256"]:
            raise ObjectIntegrityError("objeto de legenda ausente ou corrompido")
        target = Path(target_path).expanduser() if target_path is not None else self._episode_video(row["episode_id"]).with_suffix(".pt-BR." + row["format"])
        target = self._authorized_path(target, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        existing_sha = self._hash_file(target)[0] if target.is_file() else None
        if target.exists() and not target.is_file():
            raise PublicationConflict("destino não é arquivo regular")
        if existing_sha == row["sha256"]:
            status = "PUBLISHED"
        elif existing_sha is not None and not allow_replace:
            raise PublicationConflict("sidecar existente possui hash diferente; substituição explícita necessária")
        else:
            fd, raw = tempfile.mkstemp(prefix=f".{target.name}.library-", dir=str(target.parent))
            staged = Path(raw)
            try:
                with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
                    shutil.copyfileobj(inp, out, length=1024 * 1024); out.flush(); os.fsync(out.fileno())
                if self._hash_file(staged)[0] != row["sha256"]:
                    raise ObjectIntegrityError("hash mudou durante publicação")
                os.replace(staged, target)
                dir_fd = os.open(target.parent, os.O_RDONLY)
                try: os.fsync(dir_fd)
                finally: os.close(dir_fd)
            finally:
                staged.unlink(missing_ok=True)
            status = "PUBLISHED"
        with self._db() as db:
            previous = db.execute("SELECT id FROM publication WHERE episode_id=? AND target_relative_path=? AND status='PUBLISHED' AND subtitle_record_id<>?", (row["episode_id"], self._target_relative(target), record_id)).fetchall()
            if allow_replace and previous:
                db.executemany("UPDATE publication SET status='REPLACED' WHERE id=?", [(item["id"],) for item in previous])
            cur = db.execute("SELECT id FROM publication WHERE subtitle_record_id=? AND target_relative_path=?", (record_id, self._target_relative(target))).fetchone()
            if cur:
                db.execute("UPDATE publication SET target_sha256=?,status=?,published_at=?,last_verified_at=? WHERE id=?", (row["sha256"], status, _now(), _now(), cur["id"])); publication_id = cur["id"]
            else:
                publication_id = db.execute("INSERT INTO publication(subtitle_record_id,episode_id,target_relative_path,target_sha256,status,published_at,last_verified_at) VALUES (?,?,?,?,?,?,?)", (record_id, row["episode_id"], self._target_relative(target), row["sha256"], status, _now(), _now())).lastrowid
            result = db.execute("SELECT * FROM publication WHERE id=?", (publication_id,)).fetchone()
        return self._row(result) or {}

    def _target_relative(self, target: Path) -> str:
        for root in self.media_roots:
            try:
                return target.resolve(strict=False).relative_to(root).as_posix()
            except ValueError:
                continue
        raise PathSecurityError("destino fora da raiz autorizada")

    def publications(self, record_id: int | None = None, episode_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM publication"; clauses=[]; params=[]
        if record_id is not None: clauses.append("subtitle_record_id=?"); params.append(record_id)
        if episode_id is not None: clauses.append("episode_id=?"); params.append(episode_id)
        if clauses: query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC"
        with self._db() as db: return [self._row(row) or {} for row in db.execute(query, params)]

    def verify_publication(self, publication_id: int) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM publication WHERE id=?", (publication_id,)).fetchone()
        if row is None: raise LibraryError("publicação não encontrada")
        target = None
        for root in self.media_roots:
            candidate = (root / row["target_relative_path"]).resolve(strict=False)
            try: candidate.relative_to(root); target = candidate; break
            except ValueError: continue
        if target is None or not target.is_file():
            result = dict(row); result["verified"] = False; return result
        sha, _ = self._hash_file(target)
        verified = sha == row["target_sha256"]
        with self._db() as db:
            db.execute("UPDATE publication SET last_verified_at=? WHERE id=?", (_now(), publication_id))
        result = dict(row); result["verified"] = verified; return result

    def object_path_for_record(self, record_id: int) -> Path:
        with self._db() as db:
            row = db.execute("SELECT o.storage_path,o.sha256 FROM subtitle_record r JOIN subtitle_object o ON o.id=r.object_id WHERE r.id=?", (record_id,)).fetchone()
        if row is None: raise LibraryError("legenda não encontrada")
        path = self.root / row["storage_path"]
        if not path.is_file() or self._hash_file(path)[0] != row["sha256"]: raise ObjectIntegrityError("objeto ausente/corrompido")
        return path

    def counts(self) -> dict[str, int]:
        with self._db() as db:
            return {"series": db.execute("SELECT COUNT(*) FROM media_series").fetchone()[0], "episodes": db.execute("SELECT COUNT(*) FROM media_episode").fetchone()[0], "records": db.execute("SELECT COUNT(*) FROM subtitle_record").fetchone()[0], "objects": db.execute("SELECT COUNT(*) FROM subtitle_object").fetchone()[0], "publications": db.execute("SELECT COUNT(*) FROM publication WHERE status='PUBLISHED'").fetchone()[0]}
