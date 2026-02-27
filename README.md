# Vertical Farm Automation

A lightweight vertical farm monitoring system that runs locally on your machine.

---

## What the Software Does

- Sensors send environmental data (air, water, light).
- The host server stores the data in SQLite.
- A live dashboard updates in real time.
- Alerts are generated for abnormal conditions.

---

## Main Files

- `Host.py` – Runs the web server, dashboard, and API.
- `DB.py` – Handles database storage.
- `Alerts.py` – Contains alert rules.
- `MSG.py` – Defines the sensor data format.
- `Mock_ESP32_Transmission.py` – Simulates sensor data for testing.

---

## Optional Files

These are hardcoded into `Mock_ESP32_Transmission.py` and can be interchanged:

- `Opt_MSG.py` – Creates normal functioning sensor data.
- `Flagged_MSG.py` – Creates random sensor data with alert-triggering behavior included.

---

## Quick Start

### 1. Open a terminal and install dependencies

pip install fastapi uvicorn pydantic

### 2. Start the virtual environment, and then the server

source ~/Vertical-Farm-Automation/.venv/bin/activate

uvicorn Host:app --host 0.0.0.0 --port 8000 --reload

### 3. Open another terminal and generate virtual sensor information. 

python Mock_ESP32_Transmission.py

### 4. Go to the server on your browser. Go to:

http://localhost:8000

### 5. You can connect to the server from your phone, as long as the WiFi is the same across all devices

### 6. Manage alerts, sensor history, or view charts from the app

