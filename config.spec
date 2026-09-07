# Runtime configuration accepted as the package instance's `config:` value.
# This file documents defaults; the primitive validates them during init.

config:
  # Positive finite number of seconds between samples.
  interval_s: 1.0

  # Linux sysfs mount containing class/{hwmon,thermal,power_supply}.
  sysfs_root: /sys

  # Stable Soma component path below body/.
  component_prefix: body/compute_node

  # Human-readable compute-node name.
  display_name: Linux compute node
