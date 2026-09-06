"""Create the Windows ICO from the canonical PNG mark."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


root = Path(__file__).resolve().parents[2]
output = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "build" / "transass.ico"
output.parent.mkdir(parents=True, exist_ok=True)
with Image.open(root / "src/subtranslate/transass_logo.png") as image:
    image.save(output, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
print(output)
