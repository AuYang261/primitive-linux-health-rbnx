# SPDX-License-Identifier: MulanPSL-2.0
import unittest

from linux_health import driver


class DriverProjectionTests(unittest.TestCase):
    def test_combines_selected_voltage_and_current_for_declared_component(self):
        mapping = driver.ReadingMapping.from_config(
            {
                "name": "body/compute_node/input_power",
                "voltage": {"metric": "voltage", "driver": "ina238", "label": "in1"},
                "current_a": {"metric": "current", "driver": "ina238", "label": "curr1"},
            }
        )
        items = [
            {"metric": "voltage", "driver": "ina238", "label": "in1", "value": None, "quality": "unavailable"},
            {"metric": "voltage", "driver": "ina238", "label": "in1", "value": 28.0, "quality": "valid"},
            {"metric": "current", "driver": "ina238", "label": "curr1", "value": 1.2, "quality": "valid"},
        ]
        reading = driver._mapped_reading(mapping, items)
        self.assertEqual(reading.name, "body/compute_node/input_power")
        self.assertEqual(reading.voltage, 28.0)
        self.assertAlmostEqual(reading.current_a, 1.2, places=5)
        self.assertEqual(reading.temp_c, -1.0)

    def test_omits_unavailable_or_negative_legacy_values(self):
        mapping = driver.ReadingMapping.from_config(
            {
                "name": "body/compute_node/battery",
                "current_a": {"metric": "current", "supply_type": "Battery"},
                "battery_percent": {"metric": "battery_percent", "supply_type": "Battery"},
            }
        )
        items = [
            {"metric": "current", "supply_type": "Battery", "value": -0.5, "quality": "valid"},
            {"metric": "battery_percent", "supply_type": "Battery", "value": None, "quality": "unavailable"},
        ]
        self.assertIsNone(driver._mapped_reading(mapping, items))

    def test_rejects_metric_that_does_not_match_wire_field(self):
        with self.assertRaisesRegex(ValueError, "temp_c selector metric"):
            driver.ReadingMapping.from_config(
                {
                    "name": "body/compute_node/cpu",
                    "temp_c": {"metric": "voltage"},
                }
            )


if __name__ == "__main__":
    unittest.main()
