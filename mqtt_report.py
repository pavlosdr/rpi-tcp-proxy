import os
import time
import socket
import psutil
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from monitor import get_wifi_signal

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

MQTT_ENABLED = os.getenv("MQTT_ENABLED", "true").lower() == "true"
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "goodwe_rpi")
DISCOVERY_PREFIX = "homeassistant"

PROXY_TARGET_IP = os.getenv("PROXY_TARGET_IP", "10.10.100.253")
PROXY_TARGET_PORT = int(os.getenv("PROXY_TARGET_PORT", "502"))
PUBLISH_EVERY_SECONDS = int(os.getenv("MQTT_REPORT_INTERVAL", "30"))

client = mqtt.Client()

def check_inverter_reachable(ip, port, timeout=2):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except:
        return False

def publish_discovery(sensor_id, name, unit, device_class=None, state_class=None):
    topic = f"{DISCOVERY_PREFIX}/sensor/{TOPIC_PREFIX}_{sensor_id}/config"
    payload = {
        "name": name,
        "state_topic": f"{TOPIC_PREFIX}/status/{sensor_id}",
        "unique_id": f"{TOPIC_PREFIX}_{sensor_id}",
        "unit_of_measurement": unit,
        "device": {
            "identifiers": [TOPIC_PREFIX],
            "name": "GoodWe Inverter",
            "model": "GW6.5K-ET",
            "manufacturer": "GoodWe"
        }
    }
    if device_class:
        payload["device_class"] = device_class
    if state_class:
        payload["state_class"] = state_class

    client.publish(topic, json_dump(payload), retain=True)

def json_dump(payload: dict) -> str:
    import json
    return json.dumps(payload)

def publish_sensor(sensor_id, value):
    topic = f"{TOPIC_PREFIX}/status/{sensor_id}"
    client.publish(topic, value, retain=True)

def main():
    if not MQTT_ENABLED:
        print("MQTT reporting disabled in .env")
        return

    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    # Jednorázová konfigurace autodiscovery
    publish_discovery("wifi_signal", "WiFi Signal", "dBm", "signal_strength", "measurement")
    publish_discovery("inverter_available", "Inverter Reachable", "", None, None)
    #publish_discovery("active_power", "Active Power", "W", "power", "measurement")
    #publish_discovery("battery_soc", "Battery SoC", "%", "battery", "measurement")
    #publish_discovery("house_consumption", "House Consumption", "W", "power", "measurement")
    #publish_discovery("pv_power", "PV Power", "W", "power", "measurement")

    while True:
        # wifi signal
        wifi = get_wifi_signal()
        publish_sensor("wifi_signal", wifi)

        # dostupnost střídače
        inv_ok = check_inverter_reachable(PROXY_TARGET_IP, PROXY_TARGET_PORT)
        publish_sensor("inverter_available", "1" if inv_ok else "0")
        
        # simulované hodnoty – nahradíš reálnými daty
        #publish_sensor("active_power", 3120)
        #publish_sensor("battery_soc", 87)
        #publish_sensor("house_consumption", 1550)
        #publish_sensor("pv_power", 4230)

        time.sleep(PUBLISH_EVERY_SECONDS)

if __name__ == "__main__":
    main()
