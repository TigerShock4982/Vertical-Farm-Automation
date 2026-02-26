#The Mock ESP32 Transmission script simulates the behavior of 
#an ESP32 microcontroller transmitting sensor data to a server. 
#It generates mock sensor readings and sends them as JSON payloads 
#to a specified endpoint at regular intervals. 

# Mock_ESP32_Transmission.py
import time
import json
import urllib.request
import urllib.error

from Mock_Sensor_Generation import MockSensorGenerator
import MSG


def post_json(url: str, payload: dict, timeout_s: float = 3.0) -> tuple[int, str]:
    # Post a JSON payload to a URL 
    # Returns (status_code, response_body_text), 
    # or (0, error_message) on network error.
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except urllib.error.URLError as e:
        return 0, str(e)


def main():
    # Change this to your laptop’s LAN IP once the server is running:
    # Example: "http://192.168.1.50:8000/ingest"
    ingest_url = "http://127.0.0.1:8000/ingest"

    device_id = "farm-esp32-1"
    period_s = 1.0         # send once per second
    timeout_s = 3.0        # HTTP timeout
    backoff_s = 0.5        # retry backoff if server is down
    max_backoff_s = 8.0

    gen = MockSensorGenerator(device_id=device_id)

    print(f"[ESP32-MOCK] Sending to: {ingest_url}")
    print(f"[ESP32-MOCK] Device: {device_id}  Period: {period_s}s")

    while True:
        msg = gen.generate()

        # Validate message against canonical schema before sending
        try:
            MSG.validate_event(msg)
        except Exception as e:
            print(f"[VALIDATION-ERR] seq={msg.get('seq')} err={e}")
            time.sleep(period_s)
            continue

        status, body = post_json(ingest_url, msg, timeout_s=timeout_s)

        if status == 200:
            # Keep logs concise but informative
            print(f"[OK] seq={msg['seq']} ts={msg['ts']} lux={msg['light']['lux']} ph={msg['water']['ph']} ec={msg['water']['ec_ms_cm']}")
            backoff_s = 0.5  # reset backoff on success
            time.sleep(period_s)
        else:
            # status==0 means likely network/server unreachable
            print(f"[ERR] seq={msg['seq']} status={status} detail={body}")
            time.sleep(backoff_s)
            backoff_s = min(max_backoff_s, backoff_s * 2.0)


if __name__ == "__main__":
    main()
    
