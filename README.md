What the software does:
    - Sensors send environmental data (air, water, light).
    - The host server stores the data in SQLite.
    - A live dashboard updates in real time.
    - Alerts are generated for abnormal conditions.
    
The main files:
    - 'Host.py' – Runs the web server, dashboard, and API.
    - 'DB.py' – Handles database storage.
    - 'Alerts.py' – Contains alert rules.
    - 'MSG.py' – Defines the sensor data format.
    - 'Mock_ESP32_Transmission.py' – Simulates sensor data for testing.
    
The optional files (are hardcoded into Mock_ESP32_Transmission and can be interchanged):
    - 'Opt_MSG' - Creates normal functioning sensor data
    - 'Flagged_MSG' - Creates random functioning sensor data, with alert behavior sprinkled in
    
Quick Start:
    - Run dependencies:
        -pip install fastapi uvicorn pydantic
    - Open a new terminal and run 
        -uvicorn Host:app --host 0.0.0.0 --port 8000 --reload
    - Open your browser and go to 
        -http://localhost:8000
    - To generate simulated packets, open another terminal and run:
        -python Mock_ESP32_Transmission.py
    - Reload your connection to the server
