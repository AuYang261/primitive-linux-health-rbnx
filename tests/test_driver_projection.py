# SPDX-License-Identifier: MulanPSL-2.0
import math
import unittest
from unittest.mock import patch

from linux_health import driver


class DriverProjectionTests(unittest.TestCase):
    def test_signed_current_uses_presence_bit(self):
        item = {
            "metric": "current",
            "device": "BAT0",
            "label": "current_now",
            "source": "/sys/class/power_supply/BAT0/current_now",
            "unit": "A",
            "value": -0.5,
            "quality": "valid",
            "supply_type": "Battery",
        }
        reading = driver._reading(item)
        self.assertEqual(reading.current_a, -0.5)
        self.assertEqual(reading.observed_fields, 4)
        self.assertEqual(reading.quality, 0)

    def test_unavailable_channel_does_not_claim_a_value(self):
        item = {
            "metric": "voltage",
            "device": "BAT0",
            "label": "voltage_now",
            "source": "/sys/class/power_supply/BAT0/voltage_now",
            "unit": "V",
            "value": None,
            "quality": "unavailable",
            "supply_type": "Battery",
        }
        reading = driver._reading(item)
        self.assertEqual(reading.observed_fields, 0)
        self.assertEqual(reading.quality, 2)


if __name__ == "__main__":
    unittest.main()
