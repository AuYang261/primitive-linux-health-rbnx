# Linux health primitive for Robonix

This package reads compute-node health data from Linux sysfs and publishes the
existing `robonix/primitive/health/state` and `stream` contracts. Robonix API
supplies the shared lifecycle driver used for init and shutdown.
It is read-only: it never enables sensors, changes thresholds, or writes device
configuration.

Supported inputs are CPU temperatures from known CPU `hwmon` drivers or named
CPU thermal zones, voltage/current channels from `hwmon` and `power_supply`, and
battery charge from `power_supply/*/capacity`. Availability depends on the
machine's hardware and kernel drivers; absent values remain unavailable.

```bash
rbnx build /path/to/primitive-linux-health-rbnx
rbnx deploy /path/to/primitive-linux-health-rbnx \
  --config '{"interval_s":1,"component_prefix":"body/compute_node"}'
```

The collector preserves individual channel identity, Linux units, signed
current, genuine zero, and unavailable/invalid states. It does not infer that
an unnamed rail is CPU voltage or whole-system voltage.

Configuration:

- `interval_s`: sample interval, positive and finite; default `1`.
- `sysfs_root`: sysfs mount; default `/sys`.
- `component_prefix`: component path below `body/`; default `body/compute_node`.
- `display_name`: compute-node label exposed to Soma; default `Linux compute node`.

Run collector tests with `python3 -m unittest discover -s tests -v`.

For a complete local demonstration with a mock robot body and real host sensor
data, build Robonix core and Client, then run:

```bash
python3 run_demo.py start
python3 run_demo.py status
python3 run_demo.py stop
```

The local deployment uses ports 51051, 51091, 51093, and 17860 so it does not
interfere with the default Atlas/Soma stack on ports 50051 and 50091.
