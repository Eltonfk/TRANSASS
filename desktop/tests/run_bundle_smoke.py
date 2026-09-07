"""Run a no-model smoke test against a frozen Transass executable."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


STARTUP_TIMEOUT_SECONDS = 30


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _read_json(url: str) -> dict:
    with urlopen(url, timeout=2) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} em {url}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Resposta JSON inesperada em {url}")
    return payload


def _resolve_executable(argument: str) -> Path:
    executable = Path(argument).expanduser().resolve()
    if os.name == "nt" and not executable.exists() and executable.suffix.lower() != ".exe":
        executable = executable.with_suffix(".exe")
    if not executable.is_file():
        raise FileNotFoundError(f"Executável Desktop não encontrado: {executable}")
    return executable


def _assert_media_manifest(executable: Path) -> None:
    candidates = (
        executable.parent / "_internal" / "bin" / "MEDIA_TOOLS.json",
        executable.parent / "bin" / "MEDIA_TOOLS.json",
    )
    manifest_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if manifest_path is None:
        raise RuntimeError("O bundle não contém MEDIA_TOOLS.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(manifest.get("tools", {})) != {"ffmpeg", "ffprobe"}:
        raise RuntimeError("O manifesto do bundle não lista ffmpeg e ffprobe")


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> dict:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            raise RuntimeError(f"O executável encerrou antes do health check:\n{output[-4000:]}")
        try:
            return _read_json(base_url + "/health")
        except (OSError, URLError, TimeoutError, ValueError, RuntimeError) as error:
            last_error = error
            time.sleep(0.2)
    raise RuntimeError(
        f"Health check não respondeu em {STARTUP_TIMEOUT_SECONDS}s: {last_error}"
    )


def _terminate(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
    try:
        output, _ = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=5)
    return output or ""


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Uso: {Path(sys.argv[0]).name} CAMINHO_DO_EXECUTÁVEL", file=sys.stderr)
        return 2

    executable = _resolve_executable(sys.argv[1])
    _assert_media_manifest(executable)
    environment = os.environ.copy()
    for name in (
        "TRANSASS_CORE_ROOT",
        "TRANSASS_BIN_DIR",
        "TRANSASS_MEDIA_BIN_DIR",
        "STATE_DIR",
        "MEDIA_ROOT",
        "TRANSLATOR_BASE_LIBRARY",
        "TRANSLATOR_WEB_STATE_DIR",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "TRANSASS_PORT": str(_free_local_port()),
        }
    )
    if os.name != "nt":
        environment["BROWSER"] = "true"

    with tempfile.TemporaryDirectory(prefix="transass-bundle-smoke-") as temporary_root:
        temporary = Path(temporary_root)
        environment["TRANSASS_DATA_DIR"] = str(temporary / "data")
        environment["TRANSASS_CONFIG_DIR"] = str(temporary / "config")
        version = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=20,
        )
        if version.returncode != 0 or not version.stdout.strip().startswith("Transass "):
            raise RuntimeError(
                f"Probe de versão falhou (código {version.returncode}): "
                f"{version.stdout}{version.stderr}"
            )

        process = subprocess.Popen(
            [str(executable)],
            cwd=str(executable.parent),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            base_url = f"http://127.0.0.1:{environment['TRANSASS_PORT']}"
            health = _wait_for_health(base_url, process)
            if health.get("status") != "ok":
                raise RuntimeError(f"Health check inválido: {health}")
            onboarding = _read_json(base_url + "/onboarding/status")
            if not isinstance(onboarding.get("completed"), bool):
                raise RuntimeError(f"Resposta de onboarding inválida: {onboarding}")
        finally:
            output = _terminate(process)

    print("BUNDLE_SMOKE_OK", "version,media-manifest,health,onboarding")
    if output.strip():
        print(output[-2000:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
