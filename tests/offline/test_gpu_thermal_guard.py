from __future__ import annotations

from pathlib import Path

import pytest
import gpu_thermal_guard as thermal
from gpu_thermal_guard import (
    AmdLinuxHwmonProvider,
    GpuThermalGuard,
    NvidiaThermalProvider,
    NullThermalProvider,
    PsutilFallbackProvider,
    ThermalGuard,
    ThermalGuardConfig,
    ThermalGuardFactory,
    ThermalGuardSettings,
    ThermalSnapshot,
    ThermalTripException,
    read_amdgpu_snapshot,
)
from types import SimpleNamespace


def _write_sensor(root: Path, name: str, value: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_reads_labeled_amdgpu_temperatures_and_limits(tmp_path: Path):
    hwmon = tmp_path / "class/drm/card1/device/hwmon/hwmon7"
    _write_sensor(hwmon, "name", "amdgpu\n")
    _write_sensor(hwmon, "temp1_label", "edge\n")
    _write_sensor(hwmon, "temp1_input", "59000\n")
    _write_sensor(hwmon, "temp1_crit", "110000\n")
    _write_sensor(hwmon, "temp1_emergency", "115000\n")
    _write_sensor(hwmon, "temp2_label", "junction\n")
    _write_sensor(hwmon, "temp2_input", "101000\n")
    _write_sensor(hwmon, "temp2_crit", "110000\n")
    _write_sensor(hwmon, "temp2_emergency", "115000\n")
    _write_sensor(hwmon, "temp3_label", "mem\n")
    _write_sensor(hwmon, "temp3_input", "64000\n")
    _write_sensor(hwmon, "temp3_crit", "105000\n")
    _write_sensor(hwmon, "temp3_emergency", "110000\n")
    _write_sensor(hwmon, "fan1_input", "512\n")
    _write_sensor(hwmon, "power1_average", "40000000\n")

    snapshot = read_amdgpu_snapshot(tmp_path)

    assert snapshot.available is True
    assert snapshot.temperatures_c == {"edge": 59.0, "junction": 101.0, "mem": 64.0}
    assert snapshot.hottest_sensor == "junction"
    assert snapshot.hardware_limit_c == 105.0
    assert snapshot.effective_stop_c(100.0) == 100.0
    assert snapshot.fan_rpm == 512.0
    assert snapshot.power_w == 40.0


def test_missing_sensor_is_non_available(tmp_path: Path):
    snapshot = read_amdgpu_snapshot(tmp_path)
    assert snapshot.available is False
    assert snapshot.error == "amdgpu hwmon sensor not found"


def test_guard_trips_at_conservative_threshold_without_pipeline_call():
    warnings = []
    trips = []
    snapshot = ThermalSnapshot(
        available=True,
        device="/sys/class/drm/card1/device/hwmon/hwmon7",
        temperatures_c={"edge": 99.0, "junction": 100.5, "mem": 98.0},
        critical_c={"edge": 110.0, "junction": 110.0, "mem": 105.0},
        emergency_c={"edge": 115.0, "junction": 115.0, "mem": 110.0},
    )
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(interval_s=0.25, cooling_window_s=0.0, trip_override_window_s=0.0),
        reader=lambda: snapshot,
        on_warning=lambda value, config: warnings.append(value),
        on_trip=lambda value, config: trips.append(value),
    )

    guard._evaluate(snapshot)

    assert guard.tripped is False
    assert warnings == []
    guard._evaluate(snapshot)

    assert guard.tripped is True
    assert trips == [snapshot]


def test_guard_trips_immediately_when_matching_hardware_limit_is_breached():
    trips = []
    snapshot = ThermalSnapshot(
        available=True,
        temperatures_c={"junction": 111.0, "mem": 64.0},
        critical_c={"junction": 110.0, "mem": 105.0},
        emergency_c={"junction": 115.0, "mem": 110.0},
    )
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(trip_confirmations=3, trip_override_window_s=0.0),
        reader=lambda: snapshot,
        on_warning=lambda value, config: None,
        on_trip=lambda value, config: trips.append(value),
    )

    guard._evaluate(snapshot)

    assert guard.tripped is True
    assert trips == [snapshot]


def test_guard_waits_for_cooling_window_before_soft_trip(monkeypatch):
    trips = []
    clock = [100.0]
    hot = ThermalSnapshot(available=True, temperatures_c={"junction": 101.0})
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(
            interval_s=2.0,
            trip_confirmations=2,
            cooling_window_s=30.0,
            trip_override_window_s=0.0,
        ),
        reader=lambda: hot,
        on_warning=lambda value, config: None,
        on_trip=lambda value, config: trips.append(value),
    )
    monkeypatch.setattr(thermal.time, "monotonic", lambda: clock[0])

    guard._evaluate(hot)
    clock[0] = 101.0
    guard._evaluate(hot)
    assert guard.tripped is False
    assert trips == []

    clock[0] = 130.0
    guard._evaluate(hot)

    assert guard.tripped is True
    assert trips == [hot]


def test_guard_starts_cooling_at_warning_and_resumes_after_hysteresis():
    warning = ThermalSnapshot(
        available=True,
        temperatures_c={"junction": 97.0},
        critical_c={"junction": 110.0},
    )
    cool = ThermalSnapshot(
        available=True,
        temperatures_c={"junction": 84.0},
        critical_c={"junction": 110.0},
    )
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(warning_c=90.0, stop_c=100.0),
        reader=lambda: warning,
        on_warning=lambda value, config: None,
        on_trip=lambda value, config: None,
    )

    guard._evaluate(warning)
    assert guard.cooling_active is True
    assert guard.cooling_resume_c() == 85.0

    guard._evaluate(cool)
    assert guard.cooling_active is False
    assert guard.wait_for_cooling() is True


def test_guard_trips_when_warning_cooling_window_expires_without_hardware_breach():
    trips = []
    hot = ThermalSnapshot(
        available=True,
        temperatures_c={"junction": 95.0},
        critical_c={"junction": 110.0},
    )
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(cooling_window_s=0.0, trip_override_window_s=0.0),
        reader=lambda: hot,
        on_warning=lambda value, config: None,
        on_trip=lambda value, config: trips.append(value),
    )

    guard._evaluate(hot)

    assert guard.cooling_active is True
    assert guard.wait_for_cooling() is False
    assert guard.trip_reason == "cooling_window_expired"
    assert trips == [hot]


def test_guard_resets_soft_trip_confirmation_after_temperature_falls():
    trips = []
    hot = ThermalSnapshot(available=True, temperatures_c={"junction": 101.0})
    cool = ThermalSnapshot(available=True, temperatures_c={"junction": 95.0})
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(trip_confirmations=2, cooling_window_s=0.0, trip_override_window_s=0.0),
        reader=lambda: hot,
        on_warning=lambda value, config: None,
        on_trip=lambda value, config: trips.append(value),
    )

    guard._evaluate(hot)
    guard._evaluate(cool)
    guard._evaluate(hot)

    assert guard.tripped is False
    assert trips == []


def test_config_reads_thermal_trip_confirmations_from_environment():
    config = ThermalGuardConfig.from_environment({"TRANSASS_GPU_THERMAL_CONFIRMATIONS": "3"})

    assert config.trip_confirmations == 3


def test_config_reads_thermal_cooling_window_from_environment():
    config = ThermalGuardConfig.from_environment({"TRANSASS_GPU_THERMAL_COOLING_WINDOW_S": "30"})

    assert config.cooling_window_s == 30.0


def test_config_reads_thermal_trip_override_and_one_second_interval():
    config = ThermalGuardConfig.from_environment({
        "TRANSASS_GPU_THERMAL_TRIP_OVERRIDE_S": "60",
        "TRANSASS_GPU_THERMAL_INTERVAL_S": "1",
    })

    assert config.trip_override_window_s == 60.0
    assert config.interval_s == 1.0


def test_guard_suspends_application_trip_for_override_window(monkeypatch):
    overrides = []
    trips = []
    clock = [100.0]
    hot = ThermalSnapshot(
        available=True,
        temperatures_c={"junction": 111.0},
        critical_c={"junction": 110.0},
    )
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(trip_override_window_s=60.0),
        on_warning=lambda value, config: None,
        on_trip=lambda value, config: trips.append(value),
        on_override=lambda value, config: overrides.append(value),
    )
    monkeypatch.setattr(thermal.time, "monotonic", lambda: clock[0])

    guard._evaluate(hot)
    assert guard.override_active is True
    assert guard.tripped is False
    assert overrides == [hot]
    assert guard.wait_for_cooling() is True

    clock[0] = 159.0
    guard._evaluate(hot)
    assert guard.tripped is False
    assert trips == []

    clock[0] = 160.0
    guard._evaluate(hot)
    assert guard.tripped is True
    assert trips == [hot]


def test_guard_reenables_monitoring_when_gpu_cools_during_override(monkeypatch):
    clock = [100.0]
    hot = ThermalSnapshot(
        available=True,
        temperatures_c={"junction": 111.0},
        critical_c={"junction": 110.0},
    )
    cool = ThermalSnapshot(
        available=True,
        temperatures_c={"junction": 84.0},
        critical_c={"junction": 110.0},
    )
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(trip_override_window_s=60.0),
        on_warning=lambda value, config: None,
        on_trip=lambda value, config: None,
    )
    monkeypatch.setattr(thermal.time, "monotonic", lambda: clock[0])

    guard._evaluate(hot)
    clock[0] = 101.0
    guard._evaluate(cool)

    assert guard.override_active is False
    assert guard.tripped is False
    assert guard.cooling_active is False
    assert guard.wait_for_cooling() is True


def test_guard_warning_precedes_trip():
    warnings = []
    trips = []
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(warning_c=90.0, stop_c=100.0),
        reader=lambda: ThermalSnapshot(available=False),
        on_warning=lambda value, config: warnings.append(value),
        on_trip=lambda value, config: trips.append(value),
    )
    warning = ThermalSnapshot(available=True, temperatures_c={"junction": 92.0}, critical_c={"junction": 110.0})
    guard._evaluate(warning)

    assert guard.tripped is False
    assert warnings == [warning]
    assert trips == []


def test_nvidia_provider_prefers_pynvml_over_nvidia_smi():
    class FakeNvml:
        NVML_TEMPERATURE_GPU = 7

        def nvmlInit(self):
            return None

        def nvmlDeviceGetCount(self):
            return 2

        def nvmlDeviceGetHandleByIndex(self, index):
            return index

        def nvmlDeviceGetTemperature(self, handle, sensor):
            return (61, 74)[handle]

    def unexpected_smi(*args, **kwargs):
        raise AssertionError("nvidia-smi não deveria ser consultado quando pynvml funciona")

    provider = NvidiaThermalProvider(nvml_module=FakeNvml(), command_runner=unexpected_smi)

    assert provider.is_available() is True
    assert provider.get_current_temperature() == 74.0


def test_nvidia_provider_falls_back_to_nvidia_smi():
    result = SimpleNamespace(stdout="57\n61\n")
    provider = NvidiaThermalProvider(command_runner=lambda *args, **kwargs: result)
    provider._nvml = None

    assert provider.is_available() is True
    assert provider.get_current_temperature() == 61.0


def test_amd_provider_prefers_junction_and_uses_rocm_fallback(tmp_path: Path):
    hwmon = tmp_path / "class/drm/card0/device/hwmon/hwmon4"
    _write_sensor(hwmon, "name", "amdgpu\n")
    _write_sensor(hwmon, "temp1_label", "edge\n")
    _write_sensor(hwmon, "temp1_input", "70000\n")
    _write_sensor(hwmon, "temp2_label", "junction\n")
    _write_sensor(hwmon, "temp2_input", "80000\n")
    provider = AmdLinuxHwmonProvider(sysfs_root=tmp_path)

    assert provider.is_available() is True
    assert provider.get_current_temperature() == 80.0

    rocm_result = SimpleNamespace(stdout='{"card0": {"Temperature (Sensor junction) (C)": 67.0}}')
    fallback = AmdLinuxHwmonProvider(
        sysfs_root=tmp_path / "missing",
        command_runner=lambda *args, **kwargs: rocm_result,
    )
    assert fallback.get_current_temperature() == 67.0


def test_psutil_provider_reads_system_temperature():
    fake_psutil = SimpleNamespace(
        sensors_temperatures=lambda **kwargs: {
            "coretemp": [SimpleNamespace(label="Package id 0", current=72.5)],
        }
    )
    provider = PsutilFallbackProvider(psutil_module=fake_psutil)

    assert provider.is_available() is True
    assert provider.get_current_temperature() == 72.5


def test_psutil_provider_uses_windows_wmi_when_psutil_is_empty(monkeypatch):
    fake_psutil = SimpleNamespace(sensors_temperatures=lambda **kwargs: {})
    result = SimpleNamespace(stdout="ACPI Thermal Zone|3781\n")
    provider = PsutilFallbackProvider(
        psutil_module=fake_psutil,
        command_runner=lambda *args, **kwargs: result,
    )
    monkeypatch.setattr(thermal.platform, "system", lambda: "Windows")
    monkeypatch.setattr(thermal.shutil, "which", lambda name: "powershell.exe")

    assert provider.get_current_temperature() == pytest.approx(104.95)


def test_factory_returns_first_available_provider():
    class FakeProvider:
        def __init__(self, available):
            self.available = available

        def is_available(self):
            return self.available

        def get_current_temperature(self):
            return 50.0 if self.available else None

    first = FakeProvider(False)
    second = FakeProvider(True)

    assert ThermalGuardFactory.create_auto([first, second]) is second


def test_factory_returns_null_provider_when_all_providers_are_unavailable():
    provider = ThermalGuardFactory.create_auto([NullThermalProvider("test")])

    assert isinstance(provider, NullThermalProvider)
    assert provider.get_current_temperature() is None


def test_thermal_guard_waits_until_hysteresis_temperature():
    temperatures = iter([96.0, 91.0, 84.0])
    clock = [0.0]
    sleeps = []

    class Provider:
        def is_available(self):
            return True

        def get_current_temperature(self):
            return next(temperatures)

    guard = ThermalGuard(
        Provider(),
        config=ThermalGuardSettings(check_interval_seconds=2.0, max_wait_seconds=10.0),
        sleep_fn=lambda seconds: (sleeps.append(seconds), clock.__setitem__(0, clock[0] + seconds)),
        monotonic_fn=lambda: clock[0],
    )

    guard.wait_if_hot()

    assert sleeps == [2.0, 2.0]
    assert guard.last_temperature_c == 84.0


def test_thermal_guard_raises_after_cooldown_timeout():
    clock = [0.0]

    class Provider:
        def is_available(self):
            return True

        def get_current_temperature(self):
            return 106.0

    guard = ThermalGuard(
        Provider(),
        config=ThermalGuardSettings(check_interval_seconds=2.0, max_wait_seconds=5.0),
        sleep_fn=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        monotonic_fn=lambda: clock[0],
    )

    try:
        guard.wait_if_hot()
    except ThermalTripException as exc:
        assert exc.temperature_c == 106.0
        assert exc.provider_name == "Provider"
        assert exc.elapsed_seconds == 5.0
    else:
        raise AssertionError("ThermalTripException era esperada")


def test_thermal_guard_null_provider_is_immediately_passive(caplog):
    guard = ThermalGuard(
        NullThermalProvider(),
        sleep_fn=lambda seconds: (_ for _ in ()).throw(AssertionError("não deve dormir")),
    )

    guard.wait_if_hot()
    assert "thermal_monitoring_disabled" in caplog.text
