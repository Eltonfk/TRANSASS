from __future__ import annotations

from pathlib import Path

import gpu_thermal_guard as thermal
from gpu_thermal_guard import (
    GpuThermalGuard,
    ThermalGuardConfig,
    ThermalSnapshot,
    read_amdgpu_snapshot,
)


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
        config=ThermalGuardConfig(interval_s=0.25, cooling_window_s=0.0),
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
        config=ThermalGuardConfig(trip_confirmations=3),
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


def test_guard_resets_soft_trip_confirmation_after_temperature_falls():
    trips = []
    hot = ThermalSnapshot(available=True, temperatures_c={"junction": 101.0})
    cool = ThermalSnapshot(available=True, temperatures_c={"junction": 95.0})
    guard = GpuThermalGuard(
        config=ThermalGuardConfig(trip_confirmations=2, cooling_window_s=0.0),
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
