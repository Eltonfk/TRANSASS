"""Best-effort thermal guard for local Ollama jobs.

The guard deliberately lives outside the translation pipeline.  It only
observes Linux ``amdgpu`` hwmon sensors while a local Ollama transport is in
use and asks the queue to stop when a conservative temperature limit is
reached.  Cloud providers and systems without an AMD hwmon sensor are left
untouched.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping


DEFAULT_WARNING_C = 90.0
DEFAULT_STOP_C = 100.0
DEFAULT_INTERVAL_S = 2.0


def _float_env(environ: Mapping[str, str], name: str, default: float, *, minimum: float) -> float:
    try:
        value = float(environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _bool_env(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(environ.get(name, "" if default else "0")).strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "disabled"}


@dataclass(frozen=True)
class ThermalGuardConfig:
    enabled: bool = True
    warning_c: float = DEFAULT_WARNING_C
    stop_c: float = DEFAULT_STOP_C
    interval_s: float = DEFAULT_INTERVAL_S

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ThermalGuardConfig":
        env = environ if environ is not None else os.environ
        warning = _float_env(env, "TRANSASS_GPU_THERMAL_WARN_C", DEFAULT_WARNING_C, minimum=1.0)
        stop = _float_env(env, "TRANSASS_GPU_THERMAL_STOP_C", DEFAULT_STOP_C, minimum=2.0)
        if stop <= warning:
            warning = max(1.0, stop - 5.0)
        return cls(
            enabled=_bool_env(env, "TRANSASS_GPU_THERMAL_GUARD", True),
            warning_c=warning,
            stop_c=stop,
            interval_s=_float_env(env, "TRANSASS_GPU_THERMAL_INTERVAL_S", DEFAULT_INTERVAL_S, minimum=0.25),
        )


@dataclass(frozen=True)
class ThermalSnapshot:
    """One sampled AMD GPU hwmon reading."""

    available: bool
    device: str | None = None
    temperatures_c: dict[str, float] = field(default_factory=dict)
    critical_c: dict[str, float] = field(default_factory=dict)
    emergency_c: dict[str, float] = field(default_factory=dict)
    fan_rpm: float | None = None
    power_w: float | None = None
    error: str | None = None

    @property
    def hottest_c(self) -> float | None:
        return max(self.temperatures_c.values()) if self.temperatures_c else None

    @property
    def hottest_sensor(self) -> str | None:
        if not self.temperatures_c:
            return None
        return max(self.temperatures_c, key=self.temperatures_c.get)

    @property
    def hardware_limit_c(self) -> float | None:
        limits = [*self.critical_c.values(), *self.emergency_c.values()]
        return min(limits) if limits else None

    def effective_stop_c(self, configured_stop_c: float) -> float:
        """Stay below the first hardware critical/emergency threshold."""
        limit = self.hardware_limit_c
        if limit is None:
            return configured_stop_c
        margin = 5.0 if self.critical_c else 10.0
        return min(configured_stop_c, max(1.0, limit - margin))

    def as_dict(self, config: ThermalGuardConfig | None = None) -> dict[str, object]:
        configured = config.stop_c if config else DEFAULT_STOP_C
        return {
            "available": self.available,
            "device": self.device,
            "temperatures_c": dict(self.temperatures_c),
            "hottest_c": self.hottest_c,
            "hottest_sensor": self.hottest_sensor,
            "critical_c": dict(self.critical_c),
            "emergency_c": dict(self.emergency_c),
            "fan_rpm": self.fan_rpm,
            "power_w": self.power_w,
            "effective_stop_c": self.effective_stop_c(configured) if self.available else configured,
            "error": self.error,
        }


def _read_float(path: Path, *, scale: float = 1.0) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").strip()) / scale
    except (OSError, ValueError):
        return None


def _find_amdgpu_hwmon(sysfs_root: Path) -> list[Path]:
    roots: list[Path] = []
    for candidate in sorted(sysfs_root.glob("class/drm/card*/device/hwmon/hwmon*")):
        try:
            name = (candidate / "name").read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        if name == "amdgpu":
            roots.append(candidate)
    return roots


def read_amdgpu_snapshot(sysfs_root: Path | str = "/sys") -> ThermalSnapshot:
    """Read the first AMD GPU hwmon device without invoking shell tools."""
    roots = _find_amdgpu_hwmon(Path(sysfs_root))
    if not roots:
        return ThermalSnapshot(available=False, error="amdgpu hwmon sensor not found")
    root = roots[0]
    temperatures: dict[str, float] = {}
    critical: dict[str, float] = {}
    emergency: dict[str, float] = {}
    for input_path in sorted(root.glob("temp*_input")):
        number = input_path.name.removesuffix("_input")
        label_path = root / f"{number}_label"
        try:
            label = label_path.read_text(encoding="utf-8").strip().lower() if label_path.is_file() else number
        except OSError:
            label = number
        value = _read_float(input_path, scale=1000.0)
        if value is None:
            continue
        temperatures[label] = value
        crit = _read_float(root / f"{number}_crit", scale=1000.0)
        emerg = _read_float(root / f"{number}_emergency", scale=1000.0)
        if crit is not None:
            critical[label] = crit
        if emerg is not None:
            emergency[label] = emerg
    fan = _read_float(root / "fan1_input")
    power = _read_float(root / "power1_average", scale=1_000_000.0)
    if not temperatures:
        return ThermalSnapshot(available=False, device=str(root), error="amdgpu temperatures unavailable")
    return ThermalSnapshot(
        available=True,
        device=str(root),
        temperatures_c=temperatures,
        critical_c=critical,
        emergency_c=emergency,
        fan_rpm=fan,
        power_w=power,
    )


class GpuThermalGuard:
    """Poll AMD temperatures and invoke callbacks on warning/trip."""

    def __init__(
        self,
        *,
        on_warning: Callable[[ThermalSnapshot, ThermalGuardConfig], None],
        on_trip: Callable[[ThermalSnapshot, ThermalGuardConfig], None],
        config: ThermalGuardConfig | None = None,
        reader: Callable[[], ThermalSnapshot] | None = None,
    ) -> None:
        self.config = config or ThermalGuardConfig.from_environment()
        self.reader = reader or read_amdgpu_snapshot
        self.on_warning = on_warning
        self.on_trip = on_trip
        self.latest: ThermalSnapshot | None = None
        self.tripped = False
        self.warning_sent = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _evaluate(self, snapshot: ThermalSnapshot) -> None:
        self.latest = snapshot
        if not snapshot.available or snapshot.hottest_c is None:
            return
        stop_c = snapshot.effective_stop_c(self.config.stop_c)
        warning_c = min(self.config.warning_c, max(1.0, stop_c - 5.0))
        if snapshot.hottest_c >= stop_c:
            if not self.tripped:
                self.tripped = True
                self.on_trip(snapshot, self.config)
            return
        if snapshot.hottest_c >= warning_c and not self.warning_sent:
            self.warning_sent = True
            self.on_warning(snapshot, self.config)

    def start(self) -> bool:
        if not self.config.enabled:
            return False
        first = self.reader()
        self._evaluate(first)
        if not first.available or self.tripped:
            return False
        self._thread = threading.Thread(target=self._run, name="gpu-thermal-guard", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        while not self._stop.wait(self.config.interval_s):
            try:
                self._evaluate(self.reader())
            except Exception:
                # Sensor reads must never kill the translation worker.
                continue
            if self.tripped:
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self.config.interval_s + 0.5))

