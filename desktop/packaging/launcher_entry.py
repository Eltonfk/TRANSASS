"""PyInstaller entry point kept outside the import-flat core."""

from __future__ import annotations

import sys

from transass_desktop import __version__


def main() -> int:
    """Handle lightweight probes before importing the Qt launcher."""

    if any(argument in {"--version", "-V"} for argument in sys.argv[1:]):
        print(f"Transass {__version__}")
        return 0
    from transass_desktop.launcher import main as launch

    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
