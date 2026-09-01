"""Single source of truth for the application version (SemVer).

Consumers: web app (/health, /version), readonly probe (snapshot.app_version),
context inspector and release tooling.  Bump together with CHANGELOG.md and the
annotated Git tag.
"""

__version__ = "2.4.12"
