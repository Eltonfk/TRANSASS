"""Desktop runtime for Transass."""

try:
    # The web/core package is the single release-version source of truth.
    from _version import __version__
except (ImportError, AttributeError):
    # Keep the Desktop shell importable in isolated tooling environments.
    __version__ = "2.5.2"
