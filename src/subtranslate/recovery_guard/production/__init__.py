"""Uninstalled production-source adapters for the recovery one-shot guard.

Nothing in this package registers an OpenCode tool, opens a real state root,
or starts a service at import time.
"""

from .broker import ProductionBroker
from .crypto import Ed25519Verifier
from .schema import CAPABILITY_SCHEMA_VERSION

__all__ = ["CAPABILITY_SCHEMA_VERSION", "Ed25519Verifier", "ProductionBroker"]
