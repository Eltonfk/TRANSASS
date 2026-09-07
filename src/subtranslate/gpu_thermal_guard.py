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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping


DEFAULT_WARNING_C = 90.0
DEFAULT_STOP_C = 100.0
DEFAULT_INTERVAL_S = 2.0
DEFAULT_TRIP_CONFIRMATIONS = 2
DEFAULT_COOLING_WINDOW_S = 30.0


def _float_env(environ: Mapping[str, str], name: str, default: float, *, minimum: float) -> float:
    try:
        value = float(environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _int_env(environ: Mapping[str, str], name: str, default: int, *, minimum: int) -> int:
    try:
        value = int(environ.get(name, str(default)))
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
    trip_confirmations: int = DEFAULT_TRIP_CONFIRMATIONS
    cooling_window_s: float = DEFAULT_COOLING_WINDOW_S
    resume_c: float | None = None

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ThermalGuardConfig":
        env = environ if environ is not None else os.environ
        warning = _float_env(env, "TRANSASS_GPU_THERMAL_WARN_C", DEFAULT_WARNING_C, minimum=1.0)
        stop = _float_env(env, "TRANSASS_GPU_THERMAL_STOP_C", DEFAULT_STOP_C, minimum=2.0)
        if stop <= warning:
            warning = max(1.0, stop - 5.0)
        resume_raw = str(env.get("TRANSASS_GPU_THERMAL_RESUME_C", "")).strip()
        resume: float | None = None
        if resume_raw:
            try:
                resume = max(1.0, float(resume_raw))
            except ValueError:
                resume = None
        if resume is not None and resume >= warning:
            resume = max(1.0, warning - 1.0)
        return cls(
            enabled=_bool_env(env, "TRANSASS_GPU_THERMAL_GUARD", True),
            warning_c=warning,
            stop_c=stop,
            resume_c=resume,
            interval_s=_float_env(env, "TRANSASS_GPU_THERMAL_INTERVAL_S", DEFAULT_INTERVAL_S, minimum=0.25),
            trip_confirmations=_int_env(
                env,
                "TRANSASS_GPU_THERMAL_CONFIRMATIONS",
                DEFAULT_TRIP_CONFIRMATIONS,
                minimum=1,
            ),
            cooling_window_s=_float_env(
                env,
                "TRANSASS_GPU_THERMAL_COOLING_WINDOW_S",
                DEFAULT_COOLING_WINDOW_S,
                minimum=0.0,
            ),
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

    def hardware_breach(self) -> tuple[str, float, float, str] | None:
        """Return a per-sensor hardware breach without mixing sensor limits.

        The previous global ``hardware_limit_c`` value is useful for selecting
        a conservative software threshold, but it must not be compared with a
        different sensor's temperature.  A memory critical limit, for example,
        cannot be treated as the junction critical limit.
        """
        for sensor, temperature in self.temperatures_c.items():
            emergency = self.emergency_c.get(sensor)
            if emergency is not None and temperature >= emergency:
                return sensor, temperature, emergency, "emergency"
            critical = self.critical_c.get(sensor)
            if critical is not None and temperature >= critical:
                return sensor, temperature, critical, "critical"
        return None

    def effective_stop_c(self, configured_stop_c: float) -> float:
        """Stay below the first hardware critical/emergency threshold."""
        limit = self.hardware_limit_c
        if limit is None:
            return configured_stop_c
        margin = 5.0 if self.critical_c else 10.0
        return min(configured_stop_c, max(1.0, limit - margin))

    def as_dict(self, config: ThermalGuardConfig | None = None) -> dict[str, object]:
        configured = config.stop_c if config else DEFAULT_STOP_C
        warning = config.warning_c if config else DEFAULT_WARNING_C
        configured_resume = config.resume_c if config else None
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
            "cooling_resume_c": (
                max(1.0, min(warning - 1.0, configured_resume))
                if configured_resume is not None
                else max(1.0, warning - 5.0)
            ),
            "cooling_window_s": config.cooling_window_s if config else DEFAULT_COOLING_WINDOW_S,
            "hardware_breach": (
                {
                    "sensor": breach[0],
                    "temperature_c": breach[1],
                    "limit_c": breach[2],
                    "limit_type": breach[3],
                }
                if (breach := self.hardware_breach()) is not None
                else None
            ),
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
    """Poll AMD temperatures and coordinate a cooperative cooling gate.

    The guard never changes the GPU fan curve and never delays a hardware
    critical/emergency trip.  Once the software warning is reached, callers
    can use :meth:`wait_for_cooling` before starting another model request.
    This removes new GPU work while the driver/firmware fan curve responds.
    """

    def __init__(
        self,
        *,
        on_warning: Callable[[ThermalSnapshot, ThermalGuardConfig], None],
        on_trip: Callable[[ThermalSnapshot, ThermalGuardConfig], None],
        on_sample: Callable[[ThermalSnapshot, ThermalGuardConfig], None] | None = None,
        config: ThermalGuardConfig | None = None,
        reader: Callable[[], ThermalSnapshot] | None = None,
    ) -> None:
        self.config = config or ThermalGuardConfig.from_environment()
        self.reader = reader or read_amdgpu_snapshot
        self.on_warning = on_warning
        self.on_trip = on_trip
        self.on_sample = on_sample
        self.latest: ThermalSnapshot | None = None
        self.tripped = False
        self.warning_sent = False
        self.consecutive_over_stop = 0
        self.over_stop_since: float | None = None
        self.cooling_active = False
        self.cooling_since: float | None = None
        self.trip_reason: str | None = None
        self._stop = threading.Event()
        self._sample_event = threading.Event()
        self._trip_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def over_stop_elapsed_s(self) -> float:
        if self.over_stop_since is None:
            return 0.0
        return max(0.0, time.monotonic() - self.over_stop_since)

    @property
    def cooling_elapsed_s(self) -> float:
        if self.cooling_since is None:
            return 0.0
        return max(0.0, time.monotonic() - self.cooling_since)

    def cooling_resume_c(self, config: ThermalGuardConfig | None = None) -> float:
        """Return the hysteresis point at which new model work may resume."""
        active_config = config or self.config
        configured = active_config.resume_c
        if configured is not None:
            return max(1.0, min(active_config.warning_c - 1.0, configured))
        return max(1.0, active_config.warning_c - 5.0)

    def _emit_sample(self, snapshot: ThermalSnapshot) -> None:
        try:
            if self.on_sample is not None:
                self.on_sample(snapshot, self.config)
        except Exception:
            # Telemetry must never change the safety decision or kill the
            # translation worker when a UI/state sink is unavailable.
            return
        finally:
            # Wake a translation thread waiting for the next sensor sample.
            self._sample_event.set()

    def _trip(self, snapshot: ThermalSnapshot, reason: str) -> bool:
        """Trip once and identify why, without weakening hardware safety."""
        with self._trip_lock:
            if self.tripped:
                return False
            self.tripped = True
            self.trip_reason = reason
        self.on_trip(snapshot, self.config)
        return True

    def wait_for_cooling(self) -> bool:
        """Wait until the GPU cools enough for one more model request.

        ``True`` means work may continue.  ``False`` means the guard has
        tripped (hardware breach or cooling timeout) and the caller must stop.
        A missing sensor keeps the historical best-effort behavior: the guard
        cannot gate on a reading it does not have.
        """
        if not self.config.enabled:
            return True
        while True:
            if self.tripped:
                return False
            snapshot = self.latest
            if snapshot is None or not snapshot.available or snapshot.hottest_c is None:
                return True
            if not self.cooling_active:
                return True
            if snapshot.hottest_c < self.cooling_resume_c():
                return True
            cooling_since = self.cooling_since
            if cooling_since is None:
                cooling_since = time.monotonic()
            deadline = cooling_since + self.config.cooling_window_s
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._trip(snapshot, "cooling_window_expired")
                return False
            self._sample_event.wait(min(max(self.config.interval_s, 0.25), remaining))
            self._sample_event.clear()

    def _evaluate(self, snapshot: ThermalSnapshot) -> None:
        self.latest = snapshot
        if not snapshot.available or snapshot.hottest_c is None:
            self.consecutive_over_stop = 0
            self.over_stop_since = None
            self.cooling_active = False
            self.cooling_since = None
            self.warning_sent = False
            self._emit_sample(snapshot)
            return
        stop_c = snapshot.effective_stop_c(self.config.stop_c)
        warning_c = min(self.config.warning_c, max(1.0, stop_c - 5.0))

        # Cooling starts at the warning threshold, not at the softer stop
        # threshold.  At this point the current request may still finish, but
        # no subsequent request should add another burst of GPU work.
        if snapshot.hottest_c >= warning_c:
            if not self.cooling_active:
                self.cooling_since = time.monotonic()
            self.cooling_active = True
        elif self.cooling_active and snapshot.hottest_c < self.cooling_resume_c():
            self.cooling_active = False
            self.cooling_since = None
            self.warning_sent = False

        if snapshot.hottest_c >= stop_c:
            if self.over_stop_since is None:
                self.over_stop_since = time.monotonic()
            self.consecutive_over_stop += 1
            hardware_breach = snapshot.hardware_breach()
            confirmed = (
                self.consecutive_over_stop >= self.config.trip_confirmations
                and self.over_stop_elapsed_s >= self.config.cooling_window_s
            )
            if not self.tripped and (hardware_breach is not None or confirmed):
                self._trip(
                    snapshot,
                    "hardware_critical" if hardware_breach is not None else "cooling_window_expired",
                )
            self._emit_sample(snapshot)
            return
        self.consecutive_over_stop = 0
        self.over_stop_since = None
        if snapshot.hottest_c >= warning_c and not self.warning_sent:
            self.warning_sent = True
            self.on_warning(snapshot, self.config)
        self._emit_sample(snapshot)

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
        self._sample_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self.config.interval_s + 0.5))
