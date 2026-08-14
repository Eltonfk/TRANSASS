"""Curated Glossary 1.0, independent from Translation Memory.

Entries guide model prompts only.  No entry is applied by blind post-
replacement and no generated subtitle can create or approve an entry.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STATUSES = {"APPROVED", "DRAFT", "DISABLED", "SUPERSEDED"}
SCOPES = {"GLOBAL", "ANIME", "CHARACTER"}
CATEGORIES = {"SLANG", "IDIOM", "INSULT", "TECHNICAL_TERM", "CHARACTER_TERM", "UNIVERSE_TERM", "OTHER"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def validate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    required = {"source_expression", "preferred_pt_br", "alternatives", "category", "register", "intensity", "context_usage", "avoid_notes", "scope", "status", "version", "provenance"}
    missing = sorted(required - set(entry))
    if missing:
        raise ValueError(f"glossary entry missing fields: {missing}")
    if not str(entry["source_expression"]).strip() or not str(entry["preferred_pt_br"]).strip():
        raise ValueError("glossary source/preferred cannot be empty")
    if entry["status"] not in STATUSES or entry["scope"] not in SCOPES or entry["category"] not in CATEGORIES:
        raise ValueError("invalid glossary enum")
    if not isinstance(entry["alternatives"], list):
        raise ValueError("alternatives must be a list")
    return entry


# Curated seed.  It is static, reviewed data—not translation-memory learning.
_SEED = [
    ("hold on", "espera aí", "IDIOM"), ("wait a second", "espera um segundo", "IDIOM"),
    ("come on", "qual é", "SLANG"), ("no way", "nem pensar", "SLANG"),
    ("you're kidding", "você só pode estar brincando", "IDIOM"), ("are you serious", "está falando sério", "IDIOM"),
    ("what the hell", "que diabos", "INSULT"), ("damn it", "droga", "INSULT"),
    ("shut up", "cala a boca", "INSULT"), ("get lost", "cai fora", "INSULT"),
    ("leave me alone", "me deixe em paz", "IDIOM"), ("back off", "afaste-se", "SLANG"),
    ("watch out", "cuidado", "IDIOM"), ("look out", "cuidado", "IDIOM"),
    ("take cover", "abriguem-se", "TECHNICAL_TERM"), ("fall back", "recuem", "TECHNICAL_TERM"),
    ("stand by", "aguarde", "TECHNICAL_TERM"), ("roger that", "entendido", "TECHNICAL_TERM"),
    ("copy that", "entendido", "TECHNICAL_TERM"), ("affirmative", "afirmativo", "TECHNICAL_TERM"),
    ("negative", "negativo", "TECHNICAL_TERM"), ("target acquired", "alvo adquirido", "TECHNICAL_TERM"),
    ("target locked", "alvo travado", "TECHNICAL_TERM"), ("mission accomplished", "missão cumprida", "TECHNICAL_TERM"),
    ("mission failed", "missão fracassada", "TECHNICAL_TERM"), ("engage", "iniciar combate", "TECHNICAL_TERM"),
    ("cease fire", "cessar-fogo", "TECHNICAL_TERM"), ("fire", "fogo", "TECHNICAL_TERM"),
    ("incoming", "inimigo se aproximando", "TECHNICAL_TERM"), ("all clear", "tudo limpo", "TECHNICAL_TERM"),
    ("get down", "abaixe-se", "IDIOM"), ("move out", "vamos", "TECHNICAL_TERM"),
    ("let's go", "vamos", "SLANG"), ("we're done", "acabamos", "IDIOM"),
    ("it's over", "acabou", "IDIOM"), ("not yet", "ainda não", "IDIOM"),
    ("here we go", "lá vamos nós", "IDIOM"), ("there you go", "aí está", "IDIOM"),
    ("good grief", "pelos céus", "IDIOM"), ("thank goodness", "ainda bem", "IDIOM"),
    ("what a relief", "que alívio", "IDIOM"), ("keep it up", "continue assim", "IDIOM"),
    ("hang in there", "aguente firme", "IDIOM"), ("cheer up", "anime-se", "IDIOM"),
    ("calm down", "calma", "IDIOM"), ("settle down", "acalmem-se", "IDIOM"),
    ("don't worry", "não se preocupe", "IDIOM"), ("trust me", "confie em mim", "IDIOM"),
    ("believe me", "acredite em mim", "IDIOM"), ("I mean it", "estou falando sério", "IDIOM"),
    ("you bet", "pode apostar", "SLANG"), ("of course", "é claro", "OTHER"),
    ("no kidding", "sério mesmo", "SLANG"), ("for real", "de verdade", "SLANG"),
    ("my bad", "foi mal", "SLANG"), ("whatever", "tanto faz", "SLANG"),
    ("give me a break", "me poupe", "SLANG"), ("cut it out", "pare com isso", "SLANG"),
    ("knock it off", "pare com isso", "SLANG"), ("get a grip", "se controle", "SLANG"),
    ("wise guy", "espertinho", "INSULT"), ("idiot", "idiota", "INSULT"),
    ("jerk", "babaca", "INSULT"), ("brat", "pirralho", "INSULT"),
    ("moron", "imbecil", "INSULT"), ("damn fool", "idiota maldito", "INSULT"),
    ("piece of junk", "lixo", "INSULT"), ("son of a gun", "filho da mãe", "INSULT"),
    ("big deal", "grande coisa", "SLANG"), ("easy does it", "devagar", "IDIOM"),
    ("watch your step", "cuidado onde pisa", "IDIOM"), ("keep your head down", "mantenha a cabeça baixa", "TECHNICAL_TERM"),
    ("on your left", "à sua esquerda", "TECHNICAL_TERM"), ("right behind you", "logo atrás de você", "TECHNICAL_TERM"),
    ("behind schedule", "atrasado", "OTHER"), ("mission control", "controle da missão", "TECHNICAL_TERM"),
    ("command center", "centro de comando", "TECHNICAL_TERM"), ("communications link", "link de comunicação", "TECHNICAL_TERM"),
    ("visual contact", "contato visual", "TECHNICAL_TERM"), ("radar contact", "contato no radar", "TECHNICAL_TERM"),
    ("weapons hot", "armas prontas", "TECHNICAL_TERM"), ("weapons cold", "armas desativadas", "TECHNICAL_TERM"),
    ("system online", "sistema online", "TECHNICAL_TERM"), ("system offline", "sistema offline", "TECHNICAL_TERM"),
    ("power output", "potência de saída", "TECHNICAL_TERM"), ("emergency shutdown", "desligamento de emergência", "TECHNICAL_TERM"),
    ("life signs", "sinais vitais", "TECHNICAL_TERM"), ("medical bay", "enfermaria", "TECHNICAL_TERM"),
    ("classified information", "informação confidencial", "TECHNICAL_TERM"), ("top secret", "ultrassecreto", "TECHNICAL_TERM"),
    ("understood", "entendido", "TECHNICAL_TERM"), ("yes sir", "sim, senhor", "TECHNICAL_TERM"),
    ("no sir", "não, senhor", "TECHNICAL_TERM"), ("permission to speak", "permissão para falar", "TECHNICAL_TERM"),
    ("as you wish", "como quiser", "IDIOM"), ("at your service", "às suas ordens", "IDIOM"),
    ("you have my word", "dou minha palavra", "IDIOM"), ("make it quick", "seja rápido", "IDIOM"),
    ("time is up", "o tempo acabou", "IDIOM"), ("time to go", "hora de ir", "IDIOM"),
    ("one more thing", "mais uma coisa", "IDIOM"), ("hear me out", "escute o que tenho a dizer", "IDIOM"),
    ("long story short", "resumindo", "IDIOM"), ("if you say so", "se você diz", "SLANG"),
    ("don't push it", "não abuse", "SLANG"), ("you asked for it", "você pediu por isso", "IDIOM"),
    ("mind your own business", "cuide da sua vida", "INSULT"), ("get out of here", "saia daqui", "SLANG"),
    ("what's going on", "o que está acontecendo", "OTHER"), ("what happened", "o que aconteceu", "OTHER"),
    ("I can't believe it", "não acredito", "IDIOM"), ("that's enough", "já chega", "IDIOM"),
    ("have had enough", "já chega", "IDIOM"), ("I'm counting on you", "conto com você", "IDIOM"),
    ("leave it to me", "deixe comigo", "IDIOM"), ("it's up to you", "depende de você", "IDIOM"),
]


def seed_entries() -> list[dict[str, Any]]:
    entries = []
    for index, (source, preferred, category) in enumerate(_SEED, 1):
        entries.append({
            "id": f"seed-{index:03d}", "source_expression": source,
            "preferred_pt_br": preferred, "alternatives": [], "category": category,
            "register": "neutral", "intensity": "normal", "context_usage": "curated anime dialogue context",
            "avoid_notes": "Use as contextual guidance; never replace blindly.",
            "scope": "GLOBAL", "status": "APPROVED", "version": "glossary-1.0",
            "created_at": "2026-08-10T00:00:00+00:00", "updated_at": "2026-08-10T00:00:00+00:00",
            "provenance": "CURATED_SEED_V1_0",
        })
    return entries


class GlossaryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(seed_entries())

    def _read(self) -> list[dict[str, Any]]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        entries = data.get("entries", data) if isinstance(data, dict) else data
        return [validate_entry(dict(item)) for item in entries]

    def _write(self, entries: list[dict[str, Any]]) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "name": "Glossary 1.0", "updated_at": _now(), "entries": entries}
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=str(self.path.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def list(self, query: str = "", scope: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        q = normalize(query)
        return [e for e in self._read() if (not q or q in normalize(e["source_expression"]) or q in normalize(e["preferred_pt_br"])) and (not scope or e["scope"] == scope) and (not status or e["status"] == status)]

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        entries = self._read(); entry = dict(entry); entry.setdefault("id", f"manual-{len(entries)+1:04d}"); entry.setdefault("created_at", _now()); entry.setdefault("updated_at", _now()); entry.setdefault("version", "glossary-1.0"); entry.setdefault("provenance", "HUMAN_CURATED"); validate_entry(entry); entries.append(entry); self._write(entries); return entry

    def update(self, entry_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        entries = self._read()
        for entry in entries:
            if entry["id"] == entry_id:
                entry.update(patch); entry["id"] = entry_id; entry["updated_at"] = _now(); validate_entry(entry); self._write(entries); return entry
        raise KeyError(entry_id)

    def relevant(self, text: str, *, scope: str = "GLOBAL") -> list[dict[str, Any]]:
        words = normalize(text)
        return [e for e in self._read() if e["status"] == "APPROVED" and e["scope"] in {"GLOBAL", scope} and normalize(e["source_expression"]) in words]

