# SPDX-License-Identifier: MulanPSL-2.0
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from linux_health.collector import collect


class LinuxHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("hwmon", "thermal", "power_supply"):
            (self.root / "class" / name).mkdir(parents=True)

    def put(self, relative, text):
        path = self.root / "class" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text))
        return path

    def readings(self, metric):
        return [r for r in collect(self.root)["readings"] if r["metric"] == metric]

    def test_cpu_thermal_is_not_gpu_or_acpi_temperature(self):
        for index, name in enumerate(("cpu-thermal", "gpu-thermal", "acpitz")):
            self.put(f"thermal/thermal_zone{index}/type", name)
            self.put(f"thermal/thermal_zone{index}/temp", 42500)
        values = self.readings("cpu_temperature")
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["value"], 42.5)
        self.assertEqual(values[0]["unit"], "degC")

    def test_coretemp_preserves_package_label_and_negative_temperature(self):
        self.put("hwmon/hwmon3/name", "coretemp")
        self.put("hwmon/hwmon3/temp1_label", "Package id 0")
        self.put("hwmon/hwmon3/temp1_input", -5000)
        value = self.readings("cpu_temperature")[0]
        self.assertEqual((value["value"], value["label"]), (-5.0, "Package id 0"))

    def test_non_cpu_hwmon_temperature_is_not_claimed_as_cpu(self):
        self.put("hwmon/hwmon0/name", "ina238")
        self.put("hwmon/hwmon0/temp1_input", 60000)
        self.assertFalse(collect(self.root)["availability"]["cpu_temperature"])

    def test_ina3221_distinguishes_bus_shunt_and_sum(self):
        self.put("hwmon/hwmon6/name", "ina3221")
        self.put("hwmon/hwmon6/in2_label", "VDD_CPU_SOC_MSS")
        for name, value in {"in2_input": 19744, "in5_input": 1160,
                            "in7_input": 1440, "curr2_input": 580,
                            "curr4_input": 780}.items():
            self.put(f"hwmon/hwmon6/{name}", value)
        values = {Path(r["source"]).name: r for r in collect(self.root)["readings"]}
        self.assertEqual(values["in2_input"]["value"], 19.744)
        self.assertEqual(values["in5_input"]["value"], 0.00116)
        self.assertEqual(values["in5_input"]["metric"], "shunt_voltage")
        self.assertEqual(values["in7_input"]["metric"], "shunt_voltage_sum")
        self.assertEqual(values["curr2_input"]["label"], "VDD_CPU_SOC_MSS")
        self.assertEqual(values["curr4_input"]["metric"], "current_sum")

    def test_ina238_shunt_is_millivolts(self):
        self.put("hwmon/hwmon0/name", "ina238")
        self.put("hwmon/hwmon0/in0_input", 2)
        self.put("hwmon/hwmon0/in1_input", 28028)
        self.assertEqual(self.readings("shunt_voltage")[0]["value"], 0.002)
        self.assertEqual(self.readings("voltage")[0]["value"], 28.028)

    def test_generic_hwmon_zero_voltage_and_signed_current(self):
        self.put("hwmon/hwmon9/name", "board_sensor")
        self.put("hwmon/hwmon9/in0_input", 0)
        self.put("hwmon/hwmon9/curr1_input", -250)
        self.assertEqual(self.readings("voltage")[0]["value"], 0.0)
        self.assertEqual(self.readings("current")[0]["value"], -0.25)

    def test_power_supply_micro_units_capacity_and_status(self):
        for name, value in {"type": "Battery", "present": 1, "capacity": 0,
                            "voltage_now": 12000000, "current_now": -500000,
                            "status": "Discharging"}.items():
            self.put(f"power_supply/BAT0/{name}", value)
        values = {r["metric"]: r for r in collect(self.root)["readings"]}
        self.assertEqual(values["voltage"]["value"], 12.0)
        self.assertEqual(values["current"]["value"], -0.5)
        self.assertEqual(values["battery_percent"]["value"], 0.0)
        self.assertEqual(values["battery_percent"]["supply_status"], "Discharging")

    def test_absent_battery_does_not_expose_cached_capacity(self):
        self.put("power_supply/BAT0/type", "Battery")
        self.put("power_supply/BAT0/present", 0)
        self.put("power_supply/BAT0/capacity", 95)
        value = self.readings("battery_percent")[0]
        self.assertIsNone(value["value"])
        self.assertEqual(value["quality"], "unavailable")

    def test_multiple_batteries_stay_separate_and_capacity_is_not_invented(self):
        for name, value in (("BAT0", 40), ("UPS0", 80)):
            self.put(f"power_supply/{name}/type", "Battery" if name == "BAT0" else "UPS")
            self.put(f"power_supply/{name}/capacity", value)
        values = self.readings("battery_percent")
        self.assertEqual([r["value"] for r in values], [40.0, 80.0])
        self.assertEqual([r["device"] for r in values], ["BAT0", "UPS0"])

    def test_missing_capacity_remains_null_even_with_charge_counter(self):
        self.put("power_supply/BAT0/type", "Battery")
        self.put("power_supply/BAT0/charge_now", 500)
        value = self.readings("battery_percent")[0]
        self.assertIsNone(value["value"])
        self.assertIn("FileNotFoundError", value["reason"])

    def test_invalid_capacity_and_bad_numeric_input_are_explicit(self):
        self.put("power_supply/BAT0/type", "Battery")
        self.put("power_supply/BAT0/capacity", 101)
        self.put("power_supply/BAT0/voltage_now", "NaN")
        self.assertEqual(self.readings("battery_percent")[0]["quality"], "invalid")
        self.assertEqual(self.readings("voltage")[0]["quality"], "invalid")

    def test_permission_error_does_not_abort_other_sensors(self):
        blocked = self.put("hwmon/hwmon0/in0_input", 1)
        self.put("hwmon/hwmon0/name", "board")
        self.put("hwmon/hwmon0/in1_input", 12000)
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError("test denial")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", read):
            values = self.readings("voltage")
        self.assertEqual(values[0]["quality"], "unavailable")
        self.assertEqual(values[1]["value"], 12.0)

    def test_disabled_hwmon_channel_is_unavailable(self):
        self.put("hwmon/hwmon0/name", "coretemp")
        self.put("hwmon/hwmon0/temp1_input", 90000)
        self.put("hwmon/hwmon0/temp1_enable", 0)
        self.assertEqual(self.readings("cpu_temperature")[0]["quality"], "unavailable")

    def test_empty_machine_reports_all_four_metrics_unavailable(self):
        self.assertEqual(collect(self.root)["availability"], {
            "cpu_temperature": False, "voltage": False,
            "current": False, "battery_percent": False,
        })

    def test_cli_emits_two_json_frames_and_rejects_invalid_options(self):
        result = subprocess.run([sys.executable, "-m", "linux_health.collector",
                                 "--sysfs-root", str(self.root),
                                 "--count", "2", "--interval", "0.001"],
                                text=True, capture_output=True, check=True)
        frames = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(frames), 2)
        self.assertLessEqual(frames[0]["timestamp_unix_ns"], frames[1]["timestamp_unix_ns"])
        for args in (("--interval", "nan"), ("--count", "0"),
                     ("--sysfs-root", str(self.root / "missing"))):
            failed = subprocess.run([sys.executable, "-m", "linux_health.collector", *args], capture_output=True)
            self.assertNotEqual(failed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
