"""Production journal facade; all storage is supplied explicitly."""
from __future__ import annotations

from typing import Any

from .state import ProductionStateStore


class DurableJournal:
    def __init__(self, store: ProductionStateStore):
        self.store = store

    def append(self, event: str, payload: dict[str, Any], state: str) -> None:
        self.store.append_event(event, payload, state)
