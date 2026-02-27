#This program creates realistic sensor data with periodic alert events.
#Generates normal behavior for 30 packets, then one sensor goes out of bounds for 10 packets,
#then resumes normal behavior. This simulates real farm sensor malfunctions.

import random
from datetime import datetime

class FlaggedSensorGenerator:
    """Generates farm sensor data with predictable alert patterns every 30 packets."""

    def __init__(self, device_id="farm-esp32-1"):
        self.device_id = device_id
        self.sequence = 0
        self.packets_since_alert = 0  # Track packets in current cycle
        self.alert_active = False      # Currently in alert period
        self.alert_sensor = None       # Which sensor is currently flagged
        
        # Normal sensor parameters (realistic means and std deviations)
        self.normal_params = {
            "air_temp": {"mean": 24.0, "stddev": 1.5},
            "air_humidity": {"mean": 55.0, "stddev": 7.0},
            "air_pressure": {"mean": 1007.5, "stddev": 3.5},
            "water_temp": {"mean": 20.0, "stddev": 1.2},
            "water_ph": {"mean": 6.3, "stddev": 0.25},
            "water_ec": {"mean": 1.4, "stddev": 0.18},
            "light_lux": {"mean": 500, "stddev": 100}
        }
        
        # Out-of-bounds alert ranges
        self.alert_ranges = {
            "air_temp": [(15.0, 18.0), (30.0, 35.0)],
            "air_humidity": [(20.0, 35.0), (85.0, 95.0)],
            "air_pressure": [(990.0, 998.0), (1020.0, 1035.0)],
            "water_temp": [(12.0, 16.0), (26.0, 32.0)],
            "water_ph": [(4.5, 5.5), (7.5, 8.5)],
            "water_ec": [(0.3, 0.7), (2.5, 3.5)],
            "light_lux": [(20, 80), (1200, 1800)]
        }
        
        # Available sensors for alerts
        self.all_sensors = ["air_temp", "air_humidity", "air_pressure", 
                           "water_temp", "water_ph", "water_ec", "light_lux"]

    def _timestamp(self):
        """Returns ISO8601 timestamp in local time with timezone offset."""
        return datetime.now().astimezone().isoformat()

    def _clamp(self, value, min_val, max_val):
        """Clamp value to acceptable range."""
        return max(min_val, min(max_val, value))

    def _update_alert_cycle(self):
        """Update alert state based on packet count.
        
        Pattern: 30 packets normal -> 10 packets alert -> repeat
        """
        cycle_position = self.packets_since_alert % 40  # 30 + 10 = 40 packet cycle
        
        if cycle_position == 0:
            # Start new alert event
            self.alert_active = True
            self.alert_sensor = random.choice(self.all_sensors)
        elif cycle_position == 10:
            # End alert event, resume normal
            self.alert_active = False
            self.alert_sensor = None

    def _get_sensor_value(self, sensor_name, is_float=True):
        """Get a sensor value - either normal or alerted."""
        if self.alert_active and sensor_name == self.alert_sensor:
            # Return out-of-bounds alert value
            alert_range = random.choice(self.alert_ranges[sensor_name])
            if is_float:
                return round(random.uniform(alert_range[0], alert_range[1]), 2)
            else:
                return int(self._clamp(
                    random.uniform(alert_range[0], alert_range[1]),
                    alert_range[0], alert_range[1]))
        else:
            # Return normal statistical value
            params = self.normal_params[sensor_name]
            normal_val = random.gauss(params["mean"], params["stddev"])
            
            # Determine reasonable bounds for this sensor
            bounds = {
                "air_temp": (18.0, 30.0),
                "air_humidity": (30.0, 90.0),
                "air_pressure": (990.0, 1030.0),
                "water_temp": (15.0, 28.0),
                "water_ph": (4.5, 8.5),
                "water_ec": (0.5, 3.0),
                "light_lux": (50, 1500)
            }
            
            min_bound, max_bound = bounds[sensor_name]
            clamped = self._clamp(normal_val, min_bound, max_bound)
            
            if is_float:
                return round(clamped, 2)
            else:
                return int(clamped)

    def _air_readings(self):
        """Generate air sensor readings."""
        return {
            "t_c": self._get_sensor_value("air_temp", is_float=True),
            "rh_pct": self._get_sensor_value("air_humidity", is_float=True),
            "p_hpa": self._get_sensor_value("air_pressure", is_float=True),
        }

    def _water_readings(self):
        """Generate water sensor readings."""
        return {
            "t_c": self._get_sensor_value("water_temp", is_float=True),
            "ph": self._get_sensor_value("water_ph", is_float=True),
            "ec_ms_cm": self._get_sensor_value("water_ec", is_float=True),
        }

    def _light_readings(self):
        """Generate light sensor readings."""
        return {
            "lux": self._get_sensor_value("light_lux", is_float=False)
        }

    def _level_reading(self):
        """Generate water level sensor reading (float sensor)."""
        return {
            "float": random.choice([0, 1])
        }

    def generate(self):
        """Generate a full sensor event with predictable alert patterns."""
        # Update alert cycle state
        self._update_alert_cycle()
        
        self.sequence += 1
        self.packets_since_alert += 1

        return {
            "type": "sensor",
            "ts": self._timestamp(),
            "device": self.device_id,
            "seq": self.sequence,
            "air": self._air_readings(),
            "water": self._water_readings(),
            "light": self._light_readings(),
            "level": self._level_reading(),
            "flagged": [self.alert_sensor] if self.alert_active else []  # List of alerted sensors
        }
