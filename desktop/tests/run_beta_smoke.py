"""Run a local, no-model beta smoke test against the Desktop runtime."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desktop" / "src"))

from transass_desktop.paths import DesktopPaths  # noqa: E402
from transass_desktop.runtime import LocalRuntime  # noqa: E402


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return int(error.code), json.loads(error.read().decode("utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="transass-beta-") as raw_root:
        root = Path(raw_root)
        media = root / "Biblioteca de Animes — teste [PT-BR]"
        (media / "Série Á [Temporada 1]").mkdir(parents=True)
        paths = DesktopPaths(root / "Dados do Transass", root / "Configuração")
        paths.ensure()
        paths.selected_media_folder_file.write_text(str(media), encoding="utf-8")
        runtime = LocalRuntime(paths=paths, core_root=ROOT)
        try:
            base = runtime.start(port=0)
            checks: list[str] = []
            status, health = request_json(base + "health")
            assert status == 200 and health.get("status") == "ok"
            checks.append("health")
            status, browse = request_json(base + "browse?path=")
            assert status == 200 and any("Série" in str(item) for item in browse.get("subfolders", []))
            checks.append("unicode-path")
            status, onboarding = request_json(base + "onboarding/status")
            assert status == 200 and onboarding["media"]["available"]
            checks.append("onboarding")
            payload = {"primary": {"provider": "ollama", "model": "beta-smoke", "base_url": "http://127.0.0.1:9"}, "fallback": None, "keys": {}, "pipeline": "v2_3_8"}
            status, _ = request_json(base + "transport-config", "POST", payload)
            assert status == 200
            status, provider = request_json(base + "onboarding/provider-test", "POST", {})
            assert status == 200 and provider["ok"] is False and "conectar" in provider["message"].lower()
            checks.append("provider-offline")
            status, complete = request_json(base + "onboarding/complete", "POST", {})
            assert status == 200 and complete["ok"] is True
            status, diagnostic = request_json(base + "diagnostics/export")
            assert status == 200 and "api_key" not in json.dumps(diagnostic).lower()
            checks.append("diagnostic")
            print("BETA_SMOKE_OK", ",".join(checks))
            return 0
        finally:
            runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
