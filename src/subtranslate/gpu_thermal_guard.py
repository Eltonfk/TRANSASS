"""Best-effort thermal guard for local Ollama jobs.

The guard deliberately lives outside the translation pipeline.  It observes
vendor or system thermal sensors while a local Ollama transport is in use,
temporarily suspends the application's preventive response for a configured
window when the limit is reached, and asks the queue to stop only if the
temperature remains high after that window.  Cloud providers and systems
without an exposed thermal sensor are left untouched.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


LOGGER = logging.getLogger(__name__)


DEFAULT_WARNING_C = 90.0
DEFAULT_STOP_C = 100.0
DEFAULT_INTERVAL_S = 1.0
DEFAULT_TRIP_CONFIRMATIONS = 2
DEFAULT_COOLING_WINDOW_S = 30.0
DEFAULT_TRIP_OVERRIDE_WINDOW_S = 60.0


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


def _first_float_env(
    environ: Mapping[str, str],
    names: tuple[str, ...],
    default: float,
    *,
    minimum: float,
) -> float:
    for name in names:
        if name in environ:
            return _float_env(environ, name, default, minimum=minimum)
    return max(minimum, default)


class ThermalProvider(Protocol):
    """Minimal interface required by the vendor-neutral thermal guard."""

    def is_available(self) -> bool:
        """Return whether a current reading can be obtained."""

    def get_current_temperature(self) -> float | None:
        """Return the provider's most critical temperature in Celsius."""


@dataclass(frozen=True)
class ThermalGuardSettings:
    """Configuration for the vendor-neutral, synchronous thermal backoff."""

    throttle_temp: float = 95.0
    cooldown_temp: float = 85.0
    critical_temp: float = 105.0
    check_interval_seconds: float = 2.0
    max_wait_seconds: float = 300.0
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.cooldown_temp >= self.throttle_temp:
            raise ValueError("cooldown_temp must be below throttle_temp")
        if self.critical_temp < self.throttle_temp:
            raise ValueError("critical_temp must be at least throttle_temp")
        if self.check_interval_seconds <= 0:
            raise ValueError("check_interval_seconds must be positive")
        if self.max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be positive")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ThermalGuardSettings":
        env = environ if environ is not None else os.environ
        throttle = _first_float_env(
            env,
            ("TRANSASS_THERMAL_THROTTLE_C", "TRANSASS_GPU_THERMAL_THROTTLE_C"),
            95.0,
            minimum=1.0,
        )
        cooldown = _first_float_env(
            env,
            ("TRANSASS_THERMAL_COOLDOWN_C", "TRANSASS_GPU_THERMAL_COOLDOWN_C"),
            85.0,
            minimum=0.0,
        )
        critical = _first_float_env(
            env,
            ("TRANSASS_THERMAL_CRITICAL_C", "TRANSASS_GPU_THERMAL_CRITICAL_C"),
            105.0,
            minimum=1.0,
        )
        if cooldown >= throttle:
            cooldown = max(0.0, throttle - 1.0)
        if critical < throttle:
            critical = throttle
        return cls(
            throttle_temp=throttle,
            cooldown_temp=cooldown,
            critical_temp=critical,
            check_interval_seconds=_first_float_env(
                env,
                ("TRANSASS_THERMAL_CHECK_INTERVAL_S", "TRANSASS_GPU_THERMAL_INTERVAL_S"),
                2.0,
                minimum=0.1,
            ),
            max_wait_seconds=_first_float_env(
                env,
                ("TRANSASS_THERMAL_MAX_WAIT_S", "TRANSASS_GPU_THERMAL_MAX_WAIT_S"),
                300.0,
                minimum=0.1,
            ),
            enabled=_bool_env(env, "TRANSASS_THERMAL_GUARD", True)
            and _bool_env(env, "TRANSASS_GPU_THERMAL_GUARD", True),
        )


class ThermalTripException(RuntimeError):
    """Raised when a hot provider does not reach the hysteresis target."""

    def __init__(
        self,
        message: str,
        *,
        temperature_c: float | None,
        elapsed_seconds: float,
        provider_name: str,
    ) -> None:
        super().__init__(message)
        self.temperature_c = temperature_c
        self.elapsed_seconds = elapsed_seconds
        self.provider_name = provider_name


@dataclass(frozen=True)
class ThermalGuardConfig:
    enabled: bool = True
    warning_c: float = DEFAULT_WARNING_C
    stop_c: float = DEFAULT_STOP_C
    interval_s: float = DEFAULT_INTERVAL_S
    trip_confirmations: int = DEFAULT_TRIP_CONFIRMATIONS
    cooling_window_s: float = DEFAULT_COOLING_WINDOW_S
    trip_override_window_s: float = DEFAULT_TRIP_OVERRIDE_WINDOW_S
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
            trip_override_window_s=_float_env(
                env,
                "TRANSASS_GPU_THERMAL_TRIP_OVERRIDE_S",
                DEFAULT_TRIP_OVERRIDE_WINDOW_S,
                minimum=0.0,
            ),
        )


@dataclass(frozen=True)
class ThermalSnapshot:
    """One sampled vendor or system thermal reading."""

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
            "trip_override_window_s": (
                config.trip_override_window_s
                if config else DEFAULT_TRIP_OVERRIDE_WINDOW_S
            ),
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


def _optional_module(name: str) -> Any | None:
    """Import an optional hardware library without making it a hard dependency."""
    try:
        return importlib.import_module(name)
    except (ImportError, OSError):
        return None


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
        if match:
            try:
                return float(match.group(0).replace(",", "."))
            except ValueError:
                return None
    return None


def _preferred_temperature(temperatures: Mapping[str, float]) -> float | None:
    """Choose a critical reading, preferring AMD junction/hotspot sensors."""
    valid = [(str(label), float(value)) for label, value in temperatures.items() if value is not None]
    if not valid:
        return None

    def priority(label: str) -> int:
        normalized = re.sub(r"[^a-z0-9]+", " ", label.casefold())
        if re.search(r"\b(junction|hotspot|hot spot)\b", normalized):
            return 0
        if re.search(r"\b(edge|package|gpu|graphics|core|cpu)\b", normalized):
            return 1
        return 2

    best_priority = min(priority(label) for label, _ in valid)
    return max(value for label, value in valid if priority(label) == best_priority)


def _snapshot_for_temperatures(
    device: str,
    temperatures: Mapping[str, float],
    *,
    error: str | None = None,
) -> ThermalSnapshot:
    clean = {str(label): float(value) for label, value in temperatures.items() if value is not None}
    return ThermalSnapshot(
        available=bool(clean),
        device=device,
        temperatures_c=clean,
        error=error if not clean else None,
    )


class NvidiaThermalProvider:
    """NVIDIA provider using pynvml first and nvidia-smi as a fallback."""

    def __init__(
        self,
        *,
        nvml_module: Any | None = None,
        command: str = "nvidia-smi",
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        self._nvml = nvml_module if nvml_module is not None else _optional_module("pynvml")
        self._command = command
        self._run = command_runner or subprocess.run
        self._nvml_initialized = False
        self._last_error: str | None = None

    def _read_pynvml(self) -> ThermalSnapshot | None:
        module = self._nvml
        if module is None:
            return None
        try:
            if not self._nvml_initialized:
                module.nvmlInit()
                self._nvml_initialized = True
            count = int(module.nvmlDeviceGetCount())
            temperatures: dict[str, float] = {}
            sensor_kind = getattr(module, "NVML_TEMPERATURE_GPU", 0)
            for index in range(count):
                handle = module.nvmlDeviceGetHandleByIndex(index)
                value = _coerce_number(module.nvmlDeviceGetTemperature(handle, sensor_kind))
                if value is not None:
                    temperatures[f"gpu{index}"] = value
            snapshot = _snapshot_for_temperatures("pynvml", temperatures)
            if snapshot.available:
                return snapshot
            self._last_error = "pynvml returned no GPU temperatures"
        except Exception as exc:  # vendor libraries vary across driver versions
            self._last_error = f"pynvml: {exc}"
        return None

    def _read_nvidia_smi(self) -> ThermalSnapshot | None:
        try:
            result = self._run(
                [
                    self._command,
                    "--query-gpu=temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
            output = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else str(result.stdout)
            temperatures = {}
            for index, line in enumerate(output.splitlines()):
                value = _coerce_number(line.strip())
                if value is not None:
                    temperatures[f"gpu{index}"] = value
            snapshot = _snapshot_for_temperatures("nvidia-smi", temperatures)
            if snapshot.available:
                return snapshot
            self._last_error = "nvidia-smi returned no GPU temperatures"
        except Exception as exc:  # FileNotFoundError, driver errors, and timeouts are passive
            self._last_error = f"nvidia-smi: {exc}"
        return None

    def read_snapshot(self) -> ThermalSnapshot:
        snapshot = self._read_pynvml()
        if snapshot is not None:
            return snapshot
        snapshot = self._read_nvidia_smi()
        if snapshot is not None:
            return snapshot
        return ThermalSnapshot(available=False, device="nvidia", error=self._last_error or "NVIDIA sensor unavailable")

    def is_available(self) -> bool:
        return self.read_snapshot().available

    def get_current_temperature(self) -> float | None:
        return _preferred_temperature(self.read_snapshot().temperatures_c)


def _extract_temperature_values(value: Any, *, path: str = "") -> dict[str, float]:
    """Extract temperature-like numeric leaves from vendor JSON payloads."""
    found: dict[str, float] = {}
    if isinstance(value, Mapping):
        for key, child in value.items():
            label = str(key)
            lowered = label.casefold()
            if re.search(r"temp|temperature|hotspot|junction|edge", lowered):
                numeric = _coerce_number(child)
                if numeric is not None:
                    found[f"{path}:{label}".strip(":")] = numeric
            found.update(_extract_temperature_values(child, path=f"{path}:{label}".strip(":")))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.update(_extract_temperature_values(child, path=f"{path}:{index}".strip(":")))
    return found


class AmdLinuxHwmonProvider:
    """AMD Linux provider using dynamic hwmon discovery and rocm-smi fallback."""

    def __init__(
        self,
        *,
        sysfs_root: Path | str = "/sys",
        command: str = "rocm-smi",
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        self.sysfs_root = Path(sysfs_root)
        self._command = command
        self._run = command_runner or subprocess.run
        self._last_error: str | None = None

    def _read_rocm_smi(self) -> ThermalSnapshot | None:
        try:
            result = self._run(
                [self._command, "--showtemp", "--json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else str(result.stdout)
            payload = json.loads(output)
            temperatures = _extract_temperature_values(payload)
            snapshot = _snapshot_for_temperatures("rocm-smi", temperatures)
            if snapshot.available:
                return snapshot
            self._last_error = "rocm-smi returned no temperature values"
        except Exception as exc:  # ROCm is optional and must never make a job fail
            self._last_error = f"rocm-smi: {exc}"
        return None

    def read_snapshot(self) -> ThermalSnapshot:
        if platform.system().casefold() != "linux":
            return ThermalSnapshot(available=False, device="amd-linux-hwmon", error="AMD hwmon requires Linux")
        snapshot = read_amdgpu_snapshot(self.sysfs_root)
        if snapshot.available:
            return snapshot
        rocm_snapshot = self._read_rocm_smi()
        if rocm_snapshot is not None:
            return rocm_snapshot
        return ThermalSnapshot(
            available=False,
            device="amd-linux-hwmon",
            error=self._last_error or snapshot.error or "AMD thermal sensor unavailable",
        )

    def is_available(self) -> bool:
        return self.read_snapshot().available

    def get_current_temperature(self) -> float | None:
        return _preferred_temperature(self.read_snapshot().temperatures_c)


def _entry_value(entry: Any, key: str, default: Any = None) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(key, default)
    return getattr(entry, key, default)


class PsutilFallbackProvider:
    """Cross-platform psutil fallback with optional Windows thermal WMI."""

    def __init__(
        self,
        *,
        psutil_module: Any | None = None,
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        self._psutil = psutil_module if psutil_module is not None else _optional_module("psutil")
        self._run = command_runner or subprocess.run
        self._windows_cache: ThermalSnapshot | None = None
        self._windows_cache_at = 0.0
        self._last_error: str | None = None

    def _read_psutil(self) -> ThermalSnapshot | None:
        if self._psutil is None or not callable(getattr(self._psutil, "sensors_temperatures", None)):
            return None
        try:
            try:
                groups = self._psutil.sensors_temperatures(fahrenheit=False) or {}
            except TypeError:
                groups = self._psutil.sensors_temperatures() or {}
            temperatures: dict[str, float] = {}
            for group_name, entries in groups.items():
                for index, entry in enumerate(entries or []):
                    current = _coerce_number(_entry_value(entry, "current"))
                    if current is None:
                        continue
                    label = str(_entry_value(entry, "label", "") or f"sensor{index}").strip()
                    temperatures[f"{group_name}:{label}"] = current
            snapshot = _snapshot_for_temperatures("psutil", temperatures)
            if snapshot.available:
                return snapshot
        except Exception as exc:
            self._last_error = f"psutil: {exc}"
        return None

    def _read_windows_thermal_zones(self) -> ThermalSnapshot | None:
        if platform.system().casefold() != "windows":
            return None
        now = time.monotonic()
        if self._windows_cache is not None and now - self._windows_cache_at < 1.0:
            return self._windows_cache
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if not shell:
            self._last_error = "PowerShell unavailable for Windows thermal zones"
            return None
        command = (
            "$ErrorActionPreference='SilentlyContinue'; "
            "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
            "| ForEach-Object { '{0}|{1}' -f $_.InstanceName,$_.CurrentTemperature }; "
            "Get-CimInstance -Namespace root/cimv2 -ClassName Win32_PerfRawData_Counters_ThermalZoneInformation "
            "| ForEach-Object { '{0}|{1}' -f $_.Name,$_.Temperature }"
        )
        try:
            result = self._run(
                [shell, "-NoProfile", "-NonInteractive", "-Command", command],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else str(result.stdout)
            temperatures: dict[str, float] = {}
            for index, line in enumerate(output.splitlines()):
                if "|" not in line:
                    continue
                label, raw = line.rsplit("|", 1)
                value = _coerce_number(raw)
                if value is None:
                    continue
                # Both Windows classes conventionally expose tenths of Kelvin;
                # tolerate a Celsius value from a third-party provider as well.
                celsius = value / 10.0 - 273.15 if value > 200 else value
                if -50.0 < celsius < 150.0:
                    temperatures[f"windows:{label.strip() or index}"] = celsius
            snapshot = _snapshot_for_temperatures("windows-wmi", temperatures)
            self._windows_cache = snapshot if snapshot.available else None
            self._windows_cache_at = now
            if snapshot.available:
                return snapshot
            self._last_error = "Windows thermal counters returned no values"
        except Exception as exc:  # WMI permissions and missing classes are normal fallbacks
            self._last_error = f"Windows thermal counters: {exc}"
        return None

    def read_snapshot(self) -> ThermalSnapshot:
        snapshot = self._read_psutil()
        if snapshot is not None:
            return snapshot
        snapshot = self._read_windows_thermal_zones()
        if snapshot is not None:
            return snapshot
        return ThermalSnapshot(available=False, device="psutil/windows", error=self._last_error or "thermal metrics unavailable")

    def is_available(self) -> bool:
        return self.read_snapshot().available

    def get_current_temperature(self) -> float | None:
        return _preferred_temperature(self.read_snapshot().temperatures_c)


class NullThermalProvider:
    """No-op provider used for graceful degradation when sensors are absent."""

    def __init__(self, reason: str = "no thermal provider available") -> None:
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def get_current_temperature(self) -> float | None:
        return None

    def read_snapshot(self) -> ThermalSnapshot:
        return ThermalSnapshot(available=False, device="null", error=self.reason)


class ThermalGuardFactory:
    """Select the first working provider in vendor-specific priority order."""

    @classmethod
    def default_providers(cls) -> list[ThermalProvider]:
        return [NvidiaThermalProvider(), AmdLinuxHwmonProvider(), PsutilFallbackProvider()]

    @classmethod
    def create_auto(cls, providers: list[ThermalProvider] | None = None) -> ThermalProvider:
        candidates = providers if providers is not None else cls.default_providers()
        failures: list[str] = []
        for provider in candidates:
            try:
                if provider.is_available():
                    return provider
            except Exception as exc:
                failures.append(f"{type(provider).__name__}: {exc}")
        reason = "no thermal provider available"
        if failures:
            reason += "; " + "; ".join(failures)
        return NullThermalProvider(reason)


def read_provider_snapshot(provider: ThermalProvider) -> ThermalSnapshot:
    """Read rich telemetry from a provider while honoring the small Protocol."""
    reader = getattr(provider, "read_snapshot", None)
    if callable(reader):
        try:
            snapshot = reader()
            if isinstance(snapshot, ThermalSnapshot):
                return snapshot
        except Exception as exc:
            return ThermalSnapshot(available=False, device=type(provider).__name__, error=str(exc))
    try:
        temperature = provider.get_current_temperature()
    except Exception as exc:
        return ThermalSnapshot(available=False, device=type(provider).__name__, error=str(exc))
    if temperature is None:
        return ThermalSnapshot(available=False, device=type(provider).__name__, error="temperature unavailable")
    return _snapshot_for_temperatures(type(provider).__name__, {"temperature": temperature})


class ThermalGuard:
    """Vendor-neutral hysteresis guard for synchronous inference loops.

    A missing or inaccessible sensor is passive by design.  The guard only
    sleeps the current worker before the next inference request; it never
    changes fan curves, power limits, clocks, or Ollama configuration.
    """

    def __init__(
        self,
        provider: ThermalProvider,
        *,
        config: ThermalGuardSettings | None = None,
        logger: logging.Logger | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.config = config or ThermalGuardSettings()
        self.logger = logger or LOGGER
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._passive_logged = False
        self._hot_latched = False
        self.last_temperature_c: float | None = None

    @classmethod
    def create_auto(
        cls,
        *,
        config: ThermalGuardSettings | None = None,
        logger: logging.Logger | None = None,
    ) -> "ThermalGuard":
        return cls(ThermalGuardFactory.create_auto(), config=config, logger=logger)

    @property
    def provider_name(self) -> str:
        return type(self.provider).__name__

    def _read_temperature(self) -> float | None:
        try:
            value = self.provider.get_current_temperature()
            self.last_temperature_c = value
            if value is None and not self._passive_logged:
                self.logger.warning(
                    "thermal_monitoring_disabled",
                    extra={
                        "thermal_event": "thermal_monitoring_disabled",
                        "thermal_provider": self.provider_name,
                        "thermal_reason": "temperature unavailable",
                    },
                )
                self._passive_logged = True
            return value
        except Exception as exc:
            self.last_temperature_c = None
            if not self._passive_logged:
                self.logger.warning(
                    "thermal_monitoring_disabled",
                    extra={
                        "thermal_event": "thermal_monitoring_disabled",
                        "thermal_provider": self.provider_name,
                        "thermal_reason": str(exc),
                    },
                )
                self._passive_logged = True
            return None

    def wait_if_hot(self) -> None:
        """Back off before the next inference batch until the GPU/system cools."""
        if not self.config.enabled:
            return
        if isinstance(self.provider, NullThermalProvider):
            if not self._passive_logged:
                self.logger.warning(
                    "thermal_monitoring_disabled",
                    extra={
                        "thermal_event": "thermal_monitoring_disabled",
                        "thermal_provider": self.provider_name,
                        "thermal_reason": self.provider.reason,
                    },
                )
                self._passive_logged = True
            return
        temperature = self._read_temperature()
        if temperature is None:
            return
        if not self._hot_latched and temperature < self.config.throttle_temp:
            return

        self._hot_latched = True
        started = self._monotonic()
        while temperature is not None and temperature >= self.config.cooldown_temp:
            elapsed = max(0.0, self._monotonic() - started)
            remaining = max(0.0, self.config.max_wait_seconds - elapsed)
            if remaining <= 0.0:
                raise ThermalTripException(
                    (
                        f"thermal cooldown timeout: {temperature:.1f}°C remained at or above "
                        f"{self.config.cooldown_temp:.1f}°C for {elapsed:.1f}s"
                    ),
                    temperature_c=temperature,
                    elapsed_seconds=elapsed,
                    provider_name=self.provider_name,
                )
            self.logger.warning(
                "thermal_backoff",
                extra={
                    "thermal_event": "thermal_backoff",
                    "thermal_provider": self.provider_name,
                    "temperature_c": round(temperature, 2),
                    "throttle_temp_c": self.config.throttle_temp,
                    "cooldown_temp_c": self.config.cooldown_temp,
                    "critical_temp_c": self.config.critical_temp,
                    "cooldown_elapsed_seconds": round(elapsed, 2),
                    "cooldown_remaining_seconds": round(remaining, 2),
                    "critical": temperature >= self.config.critical_temp,
                },
            )
            self._sleep(min(self.config.check_interval_seconds, remaining))
            temperature = self._read_temperature()

        # A sensor disappearing is graceful degradation, not a false trip.
        self._hot_latched = False


class GpuThermalGuard:
    """Poll a selected thermal provider and coordinate a cooperative cooling gate.

    The guard never changes the GPU fan curve.  Once a configured thermal trip
    would occur, it can temporarily suspend the application's preventive
    blocking window while the driver/firmware fan curve responds.  The
    translation is stopped only if the configured override window expires
    while the temperature remains above the trip threshold.
    """

    def __init__(
        self,
        *,
        on_warning: Callable[[ThermalSnapshot, ThermalGuardConfig], None],
        on_trip: Callable[[ThermalSnapshot, ThermalGuardConfig], None],
        on_sample: Callable[[ThermalSnapshot, ThermalGuardConfig], None] | None = None,
        on_override: Callable[[ThermalSnapshot, ThermalGuardConfig], None] | None = None,
        config: ThermalGuardConfig | None = None,
        reader: Callable[[], ThermalSnapshot] | None = None,
        provider: ThermalProvider | None = None,
    ) -> None:
        self.config = config or ThermalGuardConfig.from_environment()
        self.provider = provider or ThermalGuardFactory.create_auto()
        self.reader = reader or (lambda: read_provider_snapshot(self.provider))
        self.on_warning = on_warning
        self.on_trip = on_trip
        self.on_sample = on_sample
        self.on_override = on_override
        self.latest: ThermalSnapshot | None = None
        self.tripped = False
        self.warning_sent = False
        self.consecutive_over_stop = 0
        self.over_stop_since: float | None = None
        self.cooling_active = False
        self.cooling_since: float | None = None
        self.trip_reason: str | None = None
        self.override_active = False
        self.override_since: float | None = None
        self.override_reason: str | None = None
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

    @property
    def override_elapsed_s(self) -> float:
        if self.override_since is None:
            return 0.0
        return max(0.0, time.monotonic() - self.override_since)

    @property
    def override_remaining_s(self) -> float:
        return max(0.0, self.config.trip_override_window_s - self.override_elapsed_s)

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
        """Trip once and identify why."""
        with self._trip_lock:
            if self.tripped:
                return False
            self.tripped = True
            self.trip_reason = reason
        self.on_trip(snapshot, self.config)
        return True

    def _begin_override(self, snapshot: ThermalSnapshot, reason: str) -> bool:
        """Temporarily suspend the application's preventive trip response."""
        with self._trip_lock:
            if self.tripped or self.override_active:
                return False
            self.override_active = True
            self.override_since = time.monotonic()
            self.override_reason = reason
        try:
            if self.on_override is not None:
                self.on_override(snapshot, self.config)
        except Exception:
            # A status sink must never change the thermal state machine.
            pass
        return True

    def _end_override(self) -> None:
        with self._trip_lock:
            self.override_active = False
            self.override_since = None
            self.override_reason = None
            self.trip_reason = None

    def _request_trip(self, snapshot: ThermalSnapshot, reason: str) -> bool:
        """Start the configured grace window or trip immediately when disabled."""
        if self.config.trip_override_window_s > 0:
            return self._begin_override(snapshot, reason)
        return self._trip(snapshot, reason)

    def wait_for_cooling(self) -> bool:
        """Wait until the GPU cools enough for one more model request.

        ``True`` means work may continue.  ``False`` means the guard has
        tripped after the temporary override window and the caller must stop.
        A missing sensor keeps the historical best-effort behavior: the guard
        cannot gate on a reading it does not have.
        """
        if not self.config.enabled:
            return True
        while True:
            if self.tripped:
                return False
            if self.override_active:
                if self.override_remaining_s <= 0:
                    current = self.latest
                    if current is not None and current.available and current.hottest_c is not None:
                        stop_c = current.effective_stop_c(self.config.stop_c)
                        if current.hottest_c >= stop_c:
                            self._trip(current, "thermal_override_expired")
                            return False
                    self._end_override()
                return True
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
                self._request_trip(snapshot, "cooling_window_expired")
                return not self.tripped
            self._sample_event.wait(min(max(self.config.interval_s, 0.25), remaining))
            self._sample_event.clear()

    def _evaluate(self, snapshot: ThermalSnapshot) -> None:
        self.latest = snapshot
        if self.override_active:
            if not snapshot.available or snapshot.hottest_c is None:
                if self.override_remaining_s > 0:
                    self._emit_sample(snapshot)
                    return
                self._end_override()
            elif snapshot.hottest_c < self.cooling_resume_c():
                self._end_override()
                self.cooling_active = False
                self.cooling_since = None
                self.warning_sent = False
                self.consecutive_over_stop = 0
                self.over_stop_since = None
            elif self.override_remaining_s > 0:
                self._emit_sample(snapshot)
                return
            elif snapshot.hottest_c >= snapshot.effective_stop_c(self.config.stop_c):
                self._trip(snapshot, "thermal_override_expired")
                self._emit_sample(snapshot)
                return
            else:
                self._end_override()
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
                self._request_trip(
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
