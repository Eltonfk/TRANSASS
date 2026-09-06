"""Credential storage with an OS keyring when one is available.

The web application never returns secret values.  ``keyring`` is optional so
the core remains usable in headless Docker environments; in that case the
existing mode-600 JSON storage is retained and clearly reported as fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialStore:
    service: str = "Transass"

    @property
    def backend(self) -> str:
        if os.environ.get("TRANSASS_CREDENTIAL_BACKEND", "").lower() == "file":
            return "file"
        try:
            import keyring  # type: ignore[import-not-found]

            keyring.get_keyring()
            return "keyring"
        except Exception:
            return "file"

    def get(self, provider: str) -> str | None:
        if self.backend != "keyring":
            return None
        try:
            import keyring

            value = keyring.get_password(self.service, provider)
            return value or None
        except Exception:
            return None

    def set(self, provider: str, value: str) -> bool:
        if self.backend != "keyring":
            return False
        try:
            import keyring

            keyring.set_password(self.service, provider, value)
            return True
        except Exception:
            return False

    def delete(self, provider: str) -> bool:
        if self.backend != "keyring":
            return False
        try:
            import keyring

            keyring.delete_password(self.service, provider)
            return True
        except Exception:
            return False

