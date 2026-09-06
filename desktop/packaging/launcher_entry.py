"""PyInstaller entry point kept outside the import-flat core."""

from transass_desktop.launcher import main


if __name__ == "__main__":
    raise SystemExit(main())
