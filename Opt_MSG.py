#This program creates realistic sensor data with normal statistical distributions.
#Optimal operational ranges for a functional automated farm system.

import random
from datetime import datetime

class MockSensorGenerator:
    """Generates realistic farm sensor data using normal distributions."""

    def __init__(self, device_id="farm-esp32-1"):
        self.device_id = device_id
        self.sequence = 0
        
        # Define realistic means and standard deviations for farm sensors
        # These represent optimal operating conditions with natural variation
        self.sensor_params = {
            "air_temp": {"mean": 24.0, "stddev": 1.5},      # Optimal around 24°C
            "air_humidity": {"mean": 55.0, "stddev": 7.0},   # Moderate humidity
            "air_pressure": {"mean": 1007.5, "stddev": 3.5}, # Standard atmospheric
            "water_temp": {"mean": 20.0, "stddev": 1.2},     # Cool water optimal
            "water_ph": {"mean": 6.3, "stddev": 0.25},       # Slightly acidic
            "water_ec": {"mean": 1.4, "stddev": 0.18},       # Moderate conductivity
            "light_lux": {"mean": 500, "stddev": 100}        # Day/night cycle variation
        }

    def _timestamp(self):
        """Returns ISO8601 timestamp in local time with timezone offset."""
        return datetime.now().astimezone().isoformat()

    def _clamp(self, value, min_val, max_val):
        """Clamp value to acceptable range."""
        return max(min_val, min(max_val, value))

    def _air_readings(self):
        """Generate air sensor readings with normal distribution."""
        return {
            "t_c": round(self._clamp(
                random.gauss(self.sensor_params["air_temp"]["mean"], 
                            self.sensor_params["air_temp"]["stddev"]), 
                18.0, 30.0), 2),
            "rh_pct": round(self._clamp(
                random.gauss(self.sensor_params["air_humidity"]["mean"], 
                            self.sensor_params["air_humidity"]["stddev"]), 
                30.0, 90.0), 2),
            "p_hpa": round(self._clamp(
                random.gauss(self.sensor_params["air_pressure"]["mean"], 
                            self.sensor_params["air_pressure"]["stddev"]), 
                990.0, 1030.0), 2),
        }

    def _water_readings(self):
        """Generate water sensor readings with normal distribution."""
        return {
            "t_c": round(self._clamp(
                random.gauss(self.sensor_params["water_temp"]["mean"], 
                            self.sensor_params["water_temp"]["stddev"]), 
                15.0, 28.0), 2),
            "ph": round(self._clamp(
                random.gauss(self.sensor_params["water_ph"]["mean"], 
                            self.sensor_params["water_ph"]["stddev"]), 
                4.5, 8.5), 2),
            "ec_ms_cm": round(self._clamp(
                random.gauss(self.sensor_params["water_ec"]["mean"], 
                            self.sensor_params["water_ec"]["stddev"]), 
                0.5, 3.0), 2),
        }

    def _light_readings(self):
        """Generate light sensor readings with normal distribution."""
        return {
            "lux": int(self._clamp(
                random.gauss(self.sensor_params["light_lux"]["mean"], 
                            self.sensor_params["light_lux"]["stddev"]), 
                50, 1500))
        }

    def _level_reading(self):
        """Generate water level sensor reading (float sensor)."""
        return {
            "float": random.choice([0, 1])  # Water level float sensor (0 for low, 1 for high)
        }

    def generate(self):
        """Generate a full sensor event with realistic statistical variation."""
        self.sequence += 1

        return {
            "type": "sensor",
            "ts": self._timestamp(),
            "device": self.device_id,
            "seq": self.sequence,
            "air": self._air_readings(),
            "water": self._water_readings(),
            "light": self._light_readings(),
            "level": self._level_reading()
        }
