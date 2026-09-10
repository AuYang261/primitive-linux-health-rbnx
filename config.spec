# Runtime configuration accepted as the package instance's `config:` value.
# The deployment owns component topology and maps Linux channels to those ids.

config:
  # Positive finite number of seconds between samples.
  interval_s: 1.0

  # Linux sysfs mount containing class/{hwmon,thermal,power_supply}.
  sysfs_root: /sys

  # Required list of Soma-declared component readings. A selector always has
  # its field-specific metric and may also match device, label, driver,
  # supply_type, or source exactly.
  readings:
    - name: body/compute_node/cpu
      temp_c: { metric: cpu_temperature, label: cpu-thermal }
    - name: body/compute_node/input_power
      voltage: { metric: voltage, driver: ina238, label: in1 }
      current_a: { metric: current, driver: ina238, label: curr1 }
