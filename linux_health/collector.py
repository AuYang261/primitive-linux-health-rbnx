#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""Read Linux health telemetry from sysfs without changing device configuration."""

import argparse
import json
import math
from pathlib import Path
import re
import time


CPU_HWMON = {"coretemp", "k10temp", "k8temp", "zenpower"}
CPU_THERMAL = {"cpu-thermal", "cpu_thermal", "cpu", "x86_pkg_temp"}
METRICS = ("cpu_temperature", "voltage", "current", "battery_percent")


def text(path, diagnostics, optional=False):
    """Read metadata; missing optional labels are normal, other failures are reported."""
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError) as error:
        if not (optional and isinstance(error, FileNotFoundError)):
            diagnostics.append({"source": str(path), "reason": type(error).__name__})
        return None


def entries(path, diagnostics):
    """List a class/device directory, tolerating absent classes and hot removal."""
    try:
        return sorted(path.iterdir())
    except OSError as error:
        diagnostics.append({"source": str(path), "reason": type(error).__name__})
        return []


def sample(path, metric, divisor, unit, device, label, blocked=None, **metadata):
    """Read one integer ABI value and preserve missing/error states as JSON null."""
    value, quality, reason = None, "unavailable", blocked
    if blocked is None:
        try:
            value = int(path.read_text().strip()) / divisor
            if not math.isfinite(value):
                raise ValueError("non-finite value")
            if metric == "battery_percent" and not 0 <= value <= 100:
                raise ValueError("capacity outside 0..100")
            quality = "valid"
        except (OSError, UnicodeError) as error:
            value, reason = None, type(error).__name__
        except (ValueError, OverflowError) as error:
            value, quality, reason = None, "invalid", str(error)
    return {"metric": metric, "device": device, "label": label,
            "source": str(path), "unit": unit, "value": value,
            "quality": quality, "reason": reason, **metadata}


def channel_blocked(device, channel, diagnostics):
    """Respect optional enable/fault flags without ever writing to the device."""
    for suffix, expected in (("enable", "1"), ("fault", "0")):
        path = device / f"{channel}_{suffix}"
        before = len(diagnostics)
        value = text(path, diagnostics, optional=True)
        if value is None and len(diagnostics) > before:
            return f"cannot read {path.name}"
        if value is not None and value != expected:
            return f"{path.name}={value}"
    return None


def hwmon_spec(driver, channel):
    """Apply standard units plus INA shunt/sum exceptions; never infer whole-system power."""
    prefix, number = re.fullmatch(r"(temp|in|curr)(\d+)", channel).groups()
    index = int(number)
    if prefix == "temp":
        return "cpu_temperature", 1000, "degC"
    if prefix == "curr":
        metric = "current_sum" if driver == "ina3221" and index == 4 else "current"
        return metric, 1000, "A"
    if driver == "ina3221" and 4 <= index <= 7:
        metric = "shunt_voltage_sum" if index == 7 else "shunt_voltage"
        return metric, 1000000, "V"
    if driver in {"ina238", "ina237", "ina228", "ina219", "ina220", "ina226", "ina230", "ina231", "ina234", "sy24655"} and index == 0:
        return "shunt_voltage", 1000, "V"
    return "voltage", 1000, "V"


def hwmon_readings(root, diagnostics):
    """Read all voltage/current channels and temperatures from known CPU drivers."""
    readings = []
    for device in entries(root / "class/hwmon", diagnostics):
        driver = text(device / "name", diagnostics)
        if driver is None:
            continue
        for path in entries(device, diagnostics):
            match = re.fullmatch(r"((temp|in|curr)\d+)_input", path.name)
            if not match or (match[2] == "temp" and driver not in CPU_HWMON):
                continue
            channel = match[1]
            metric, divisor, unit = hwmon_spec(driver, channel)
            label = text(device / f"{channel}_label", diagnostics, optional=True)
            gate = channel
            if driver == "ina3221":
                if re.fullmatch(r"curr[123]", channel):
                    gate = f"in{channel[-1]}"
                    label = label or text(device / f"{gate}_label", diagnostics, optional=True)
                elif re.fullmatch(r"in[456]", channel):
                    gate = f"in{int(channel[-1]) - 3}"
            readings.append(sample(path, metric, divisor, unit, device.name, label or channel,
                                   channel_blocked(device, gate, diagnostics), driver=driver))
    return readings


def thermal_readings(root, diagnostics):
    """Read explicitly CPU-named thermal zones; do not assume zone0 is the CPU."""
    readings = []
    for device in entries(root / "class/thermal", diagnostics):
        if not re.fullmatch(r"thermal_zone\d+", device.name):
            continue
        kind = text(device / "type", diagnostics)
        if kind is not None and kind.lower() in CPU_THERMAL:
            readings.append(sample(device / "temp", "cpu_temperature", 1000,
                                   "degC", device.name, kind))
    return readings


def supply_readings(root, diagnostics):
    """Keep per-supply readings, preserve current sign, and avoid estimating capacity."""
    readings = []
    for device in entries(root / "class/power_supply", diagnostics):
        kind = text(device / "type", diagnostics)
        status = text(device / "status", diagnostics, optional=True)
        before = len(diagnostics)
        present = text(device / "present", diagnostics, optional=True)
        blocked = None
        if present not in (None, "1"):
            blocked = f"present={present}"
        elif present is None and len(diagnostics) > before:
            blocked = "cannot read present"
        specs = [("voltage_now", "voltage", 1000000, "V"),
                 ("current_now", "current", 1000000, "A")]
        if kind in ("Battery", "UPS"):
            specs.append(("capacity", "battery_percent", 1, "percent"))
        for attr, metric, divisor, unit in specs:
            readings.append(sample(device / attr, metric, divisor, unit, device.name,
                                   attr, blocked, supply_type=kind, supply_status=status))
    return readings


def collect(root):
    """Collect one non-atomic sample; availability means a valid reading exists this time."""
    root = Path(root)
    diagnostics = []
    readings = (thermal_readings(root, diagnostics) + hwmon_readings(root, diagnostics)
                + supply_readings(root, diagnostics))
    return {"timestamp_unix_ns": time.time_ns(), "readings": readings,
            "availability": {metric: any(r["metric"] == metric and r["quality"] == "valid"
                                         for r in readings) for metric in METRICS},
            "diagnostics": diagnostics}


def positive_interval(value):
    """Reject non-finite and non-positive sample intervals at the CLI boundary."""
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("interval must be finite and positive")
    return number


def main():
    """Emit one JSON object per sample, with bounded count and no sysfs writes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysfs-root", type=Path, default=Path("/sys"))
    parser.add_argument("--count", type=int, default=1, help="number of samples (default: 1)")
    parser.add_argument("--interval", type=positive_interval, default=1.0,
                        help="seconds between samples (default: 1)")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    if not (args.sysfs_root / "class").is_dir():
        parser.error("sysfs-root must contain a class directory")
    for index in range(args.count):
        if index:
            time.sleep(args.interval)
        print(json.dumps(collect(args.sysfs_root), allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
