#This program creates theoretical sensor data for software testing, in the same format 
#as the ESP32 transmission. 

import time
import random
from datetime import datetime, timezone

class MockSensorGenerator:

    def __init__(self, device_id="farm-esp32-1"):
        self.device_id = device_id
        self.sequence = 0

    def _timestamp(self):
        #Returns ISO8601 timestamp in local time with timezone offset.
        return datetime.now().astimezone().isoformat()

    def _air_readings(self):
        return {
            "t_c": round(random.uniform(22.0, 26.0), 2), #Temp in Celsius
            "rh_pct": round(random.uniform(45.0, 65.0), 2), #Relative Humidity in percentage
            "p_hpa": round(random.uniform(1000.0, 1015.0), 2), #Pressure in hPa
        }

    def _water_readings(self):
        return {
            "t_c": round(random.uniform(18.0, 22.0), 2), #Temp in Celsius
            "ph": round(random.uniform(5.8, 6.8), 2), #pH level of water
            "ec_ms_cm": round(random.uniform(1.0, 1.8), 2), #Electrical Conductivity in mS/cm
        }

    def _light_readings(self):
        return {
            "lux": random.randint(200, 800) #Light intensity in lux
        }

    def _level_reading(self):
        return {
            "float": random.choice([0, 1]) #Water level float sensor (0 for low, 1 for high)
        }

    def generate(self):
        #Generate a full sensor event.
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