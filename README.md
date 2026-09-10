# Linux health primitive for Robonix

This package reads Linux sysfs health telemetry and publishes the existing
`robonix/primitive/health/state` and `robonix/primitive/health/stream`
contracts. It is read-only: it never enables sensors, changes thresholds, or
writes device configuration.

Supported inputs are CPU temperatures from known CPU `hwmon` drivers or named
CPU thermal zones, voltage/current channels from `hwmon` and `power_supply`,
and battery charge from `power_supply/*/capacity`. Availability depends on the
machine's hardware and kernel drivers; absent values remain unavailable.

The primitive does not define a robot body or create component topology. Its
instance config maps kernel channels to stable component ids already declared
by the deployment's Soma YAML. Each reading entry accepts these scalar fields:

- `temp_c`, with selector metric `cpu_temperature`
- `voltage`, with selector metric `voltage`
- `current_a`, with selector metric `current`
- `battery_percent`, with selector metric `battery_percent`

A selector may additionally match `device`, `label`, `driver`, `supply_type`,
or `source` exactly. For example:

```yaml
primitive:
  - name: linux_health
    url: https://github.com/AuYang261/primitive-linux-health-rbnx
    config:
      interval_s: 1.0
      readings:
        - name: body/compute_node/cpu
          temp_c: { metric: cpu_temperature, label: cpu-thermal }
        - name: body/compute_node/input_power
          voltage: { metric: voltage, driver: ina238, label: in1 }
          current_a: { metric: current, driver: ina238, label: curr1 }
```

The collector retains raw Linux channel identity, units, signed values, and
quality for diagnostics. The existing `SensorReading` wire contract uses
negative values as unavailable sentinels, so the driver omits a configured
field when Linux reports a negative value rather than changing its meaning.

The complete Piper body and Vitals demonstration lives in
`examples/piper_vitals` in the Robonix repository. That deployment owns the
Piper URDF, mock Piper health, compute-node component tree, thresholds, and the
composition that starts this external primitive.

Build the package with:

```bash
rbnx build /path/to/primitive-linux-health-rbnx
```

Run tests after code generation with:

```bash
PYTHONPATH="$(rbnx path robonix-api):$PWD:$PWD/rbnx-build/codegen/proto_gen" \
  rbnx-build/venv/bin/python -m unittest discover -s tests -v
```
