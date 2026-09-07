#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""Publish read-only Linux sysfs telemetry through the health primitive contracts."""

import hashlib
import math
from pathlib import Path
import re
import threading

from robonix_api import Err, Ok, Primitive

from . import collector

primitive = Primitive(id="linux_health", namespace="robonix/primitive/health")
import health_pb2

FIELD_BITS = {
    "cpu_temperature": 1,
    "voltage": 2,
    "current": 4,
    "battery_percent": 8,
}
FIELD_NAMES = {
    "cpu_temperature": "temp_c",
    "voltage": "voltage",
    "current": "current_a",
    "battery_percent": "battery_percent",
}
QUALITY = {"valid": 0, "stale": 1, "unavailable": 2, "invalid": 3}
SUPPORTED_METRICS = frozenset(FIELD_BITS)

_stop = threading.Event()
_lock = threading.Lock()
_interval_s = 1.0
_sysfs_root = Path("/sys")
_component_prefix = "body/compute_node"
_display_name = "Linux compute node"


def _channel_id(item):
    """Return a stable readable id while preventing source-path collisions."""
    label = f"{item['device']}_{item['label']}_{item['metric']}"
    readable = re.sub(r"[^a-zA-Z0-9_]+", "_", label).strip("_")[:64] or "sensor"
    digest = hashlib.sha256(item["source"].encode()).hexdigest()[:10]
    return f"{readable}_{digest}"


def _reading(item):
    """Project one sysfs channel without changing its sign or inventing values."""
    metric = item["metric"]
    values = {
        "temp_c": -1.0,
        "voltage": -1.0,
        "current_a": -1.0,
        "battery_percent": -1.0,
    }
    observed_fields = 0
    value = item.get("value")
    if item.get("quality") == "valid" and value is not None and math.isfinite(value):
        values[FIELD_NAMES[metric]] = value
        observed_fields = FIELD_BITS[metric]
    driver = item.get("driver") or item.get("supply_type") or "Linux"
    display_name = f"{driver}: {item['device']}/{item['label']}"
    component_type = "battery" if metric == "battery_percent" else "sensor"
    return health_pb2.SensorReading(
        name=f"{_component_prefix}/{_channel_id(item)}",
        observed_fields=observed_fields,
        quality=QUALITY.get(item.get("quality"), QUALITY["invalid"]),
        display_name=display_name,
        component_type=component_type,
        source=item["source"],
        **values,
    )


def build_health_state():
    """Collect one frame and expose only kernel-reported channels."""
    with _lock:
        report = collector.collect(_sysfs_root)
        readings = [
            health_pb2.SensorReading(
                name=_component_prefix,
                temp_c=-1.0,
                voltage=-1.0,
                current_a=-1.0,
                battery_percent=-1.0,
                display_name=_display_name,
                component_type="computer",
                source=str(_sysfs_root),
            )
        ]
        readings.extend(
            _reading(item)
            for item in report["readings"]
            if item["metric"] in SUPPORTED_METRICS
        )
        return health_pb2.HealthState(
            voltage=-1.0,
            charging=False,
            remaining_s=-1,
            readings=readings,
        )


@primitive.grpc("robonix/primitive/health/state")
def get_health_state(_request):
    """Return a freshly collected health frame."""
    return health_pb2.GetHealthState_Response(state=build_health_state())


@primitive.grpc("robonix/primitive/health/stream")
def stream_health_state(_request, context):
    """Stream a fresh frame at the configured interval until shutdown."""
    while context.is_active() and not _stop.is_set():
        yield build_health_state()
        _stop.wait(_interval_s)


@primitive.on_init
def init(config):
    """Validate deployment configuration without writing to sysfs."""
    global _component_prefix, _display_name, _interval_s, _sysfs_root
    try:
        interval_s = float(config.get("interval_s", 1.0))
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("interval_s must be finite and positive")
        sysfs_root = Path(config.get("sysfs_root", "/sys"))
        if not (sysfs_root / "class").is_dir():
            raise ValueError("sysfs_root must contain a class directory")
        component_prefix = str(config.get("component_prefix", "body/compute_node")).strip("/")
        if len(component_prefix) > 200 or not re.fullmatch(
            r"body(?:/[A-Za-z0-9_.-]+)+", component_prefix
        ):
            raise ValueError("component_prefix must use safe path segments below body/")
        display_name = str(config.get("display_name", "Linux compute node")).strip()
        if not display_name:
            raise ValueError("display_name must not be empty")
        _interval_s = interval_s
        _sysfs_root = sysfs_root
        _component_prefix = component_prefix
        _display_name = display_name
        _stop.clear()
        return Ok()
    except (OSError, TypeError, ValueError) as error:
        return Err(str(error))


@primitive.on_shutdown
def shutdown():
    """Stop active stream loops without touching device state."""
    _stop.set()
    return Ok()


if __name__ == "__main__":
    primitive.run()
