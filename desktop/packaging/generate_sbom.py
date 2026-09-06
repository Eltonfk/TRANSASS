"""Generate a dependency SBOM without requiring a network service."""

from __future__ import annotations

import json
import platform
import sys
from importlib.metadata import distributions
from pathlib import Path


root = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
components = []
for package in sorted(distributions(), key=lambda item: item.metadata.get("Name", "").lower()):
    name = package.metadata.get("Name")
    version = package.version
    if name and version:
        components.append({"type": "library", "name": name, "version": version, "purl": f"pkg:pypi/{name.lower()}@{version}"})
report = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1, "metadata": {"tool": "Transass Desktop", "python": platform.python_version()}, "components": components}
root.mkdir(parents=True, exist_ok=True)
(root / "sbom.cdx.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(root / "sbom.cdx.json")
