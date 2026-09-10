#!/usr/bin/env python3
# SPDX-License-Identifier: MulanPSL-2.0
"""Publish configured Linux sysfs telemetry through the existing health contract."""

from dataclasses import dataclass
import math
from pathlib import Path
import re
import threading

from robonix_api import Err, Ok, Primitive

from . import collector

primitive = Primitive(id="linux_health", namespace="robonix/primitive/health")
import health_pb2

FIELD_METRICS = {
    "temp_c": "cpu_temperature",
    "voltage": "voltage",
    "current_a": "current",
    "battery_percent": "battery_percent",
}
MATCH_KEYS = frozenset({"metric", "device", "label", "driver", "supply_type", "source"})
_COMPONENT_ID = re.compile(r"body(?:/[A-Za-z0-9_.-]+)+")


@dataclass(frozen=True)
class Selector:
    """Match one collector item using deployment-owned hardware identity."""

    values: tuple[tuple[str, str], ...]

    @classmethod
    def from_config(cls, field, raw):
        """Validate one field selector from the primitive instance config."""
        if not isinstance(raw, dict):
            raise ValueError(f"{field} selector must be an object")
        unsupported = sorted(set(raw) - MATCH_KEYS)
        if unsupported:
            raise ValueError(f"{field} selector has unsupported keys: {', '.join(unsupported)}")
        metric = str(raw.get("metric", "")).strip()
        if metric != FIELD_METRICS[field]:
            raise ValueError(f"{field} selector metric must be {FIELD_METRICS[field]}")
        values = tuple(
            (key, str(value).strip())
            for key, value in sorted(raw.items())
            if str(value).strip()
        )
        return cls(values)

    def matches(self, item):
        """Return whether all configured attributes match one collected channel."""
        return all(str(item.get(key, "")) == value for key, value in self.values)


@dataclass(frozen=True)
class ReadingMapping:
    """Map selected sysfs channels into one Soma-declared component reading."""

    name: str
    fields: tuple[tuple[str, Selector], ...]

    @classmethod
    def from_config(cls, raw):
        """Validate a stable component id and one or more scalar selectors."""
        if not isinstance(raw, dict):
            raise ValueError("each readings entry must be an object")
        name = str(raw.get("name", "")).strip()
        if len(name) > 200 or not _COMPONENT_ID.fullmatch(name):
            raise ValueError("reading name must be a safe component path below body/")
        unsupported = sorted(set(raw) - {"name", *FIELD_METRICS})
        if unsupported:
            raise ValueError(f"reading {name} has unsupported keys: {', '.join(unsupported)}")
        fields = tuple(
            (field, Selector.from_config(field, raw[field]))
            for field in FIELD_METRICS
            if field in raw
        )
        if not fields:
            raise ValueError(f"reading {name} must configure at least one scalar field")
        return cls(name=name, fields=fields)


_stop = threading.Event()
_lock = threading.Lock()
_interval_s = 1.0
_sysfs_root = Path("/sys")
_mappings = ()


def _mapped_reading(mapping, items):
    """Build one legacy SensorReading, omitting values its sentinel cannot represent."""
    values = {field: -1.0 for field in FIELD_METRICS}
    observed = False
    for field, selector in mapping.fields:
        for item in items:
            if not selector.matches(item) or item.get("quality") != "valid":
                continue
            value = item.get("value")
            if value is None or not math.isfinite(value) or value < 0:
                continue
            values[field] = value
            observed = True
            break
    if not observed:
        return None
    return health_pb2.SensorReading(name=mapping.name, **values)


def build_health_state():
    """Collect one frame and publish only deployment-configured component readings."""
    with _lock:
        report = collector.collect(_sysfs_root)
        items = report["readings"]
        readings = [
            reading
            for mapping in _mappings
            if (reading := _mapped_reading(mapping, items)) is not None
        ]
        charging = any(
            item.get("metric") == "battery_percent" and item.get("status") == "Charging"
            for item in items
        )
        return health_pb2.HealthState(
            voltage=-1.0,
            charging=charging,
            remaining_s=-1,
            readings=readings,
        )


@primitive.grpc("robonix/primitive/health/state")
def get_health_state(_request):
    """Return a freshly collected health frame."""
    return health_pb2.GetHealthState_Response(state=build_health_state())


@primitive.grpc("robonix/primitive/health/stream")
def stream_health_state(_request, context):
    """Stream fresh frames at the configured interval until shutdown."""
    while context.is_active() and not _stop.is_set():
        yield build_health_state()
        _stop.wait(_interval_s)


@primitive.on_init
def init(config):
    """Validate sysfs access and deployment-owned channel mappings."""
    global _interval_s, _mappings, _sysfs_root
    try:
        interval_s = float(config.get("interval_s", 1.0))
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("interval_s must be finite and positive")
        sysfs_root = Path(config.get("sysfs_root", "/sys"))
        if not (sysfs_root / "class").is_dir():
            raise ValueError("sysfs_root must contain a class directory")
        raw_mappings = config.get("readings")
        if not isinstance(raw_mappings, list) or not raw_mappings:
            raise ValueError("readings must be a non-empty list")
        mappings = tuple(ReadingMapping.from_config(raw) for raw in raw_mappings)
        names = [mapping.name for mapping in mappings]
        if len(names) != len(set(names)):
            raise ValueError("reading names must be unique")
        _interval_s = interval_s
        _sysfs_root = sysfs_root
        _mappings = mappings
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
