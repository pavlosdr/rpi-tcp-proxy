"""
Modbus IO broker pro vypínače / tlačítka
- RPi3 je Modbus RTU master na RS485 sběrnici
- Čte vstupy z malých 8DI/8DO modulů (např. R4pin08)
- Při změně stavu DI publikuje MQTT zprávu do Home Assistantu
"""

import time
import logging
from typing import Dict, List

try:
    # pymodbus 3.x
    from pymodbus.client import ModbusSerialClient
except ImportError:
    # pymodbus 2.x (Debian/RPi OS často)
    from pymodbus.client.sync import ModbusSerialClient

from pymodbus.exceptions import ModbusException
import paho.mqtt.client as mqtt


# ------------- KONFIGURACE ------------- #

# MQTT broker – uprav podle své sítě
MQTT_HOST = "192.168.1.20"  # IP/hostname, kde běží MQTT (core-mosquitto/HA)
MQTT_PORT = 1883
MQTT_USERNAME = "MQTT_bridge"        # nebo "mqtt_user"
MQTT_PASSWORD = "mqtt_bridge_2091"  # nebo "mqtt_pass"
MQTT_CLIENT_ID = "modbus-io-broker-rpi3"

# Základní prefix pro topic
# Výsledný topic pak bude např.: modbus_io/obyvak_sw1/in1
MQTT_BASE_TOPIC = "modbus_io"

# Modbus RTU parametry – RS485 převodník na RPi3
# MODBUS_PORT = "/dev/ttyUSB0" <<< nahrazeno přesnou cestou (výstup po zadání: ls -l /dev/serial/by-id/)
#  >>> nehrozí změna ttyUSBx při přidání jiného zařízení USB
MODBUS_PORT = "/dev/serial/by-id/usb-1a86_USB2.0-Ser_-if00-port0"
MODBUS_BAUDRATE = 9600
MODBUS_PARITY = "N"      # 'N', 'E', 'O'
MODBUS_STOPBITS = 1
MODBUS_BYTESIZE = 8
MODBUS_TIMEOUT = 0.5     # v sekundách timeout na odpověď slave
POLL_INTERVAL = 0.03     # 30ms mezi dotazy na sběrnici

# ------------ INPUT TIMING ------------
DEBOUNCE_MS = 40
DOUBLE_CLICK_MS = 400
LONG_PRESS_MS = 700
# pro případ, že některé kanály jsou "aktivní LOW"
# True = bit 1 znamená stisk/sepnuto, False = bit 0 znamená stisk/sepnuto
DEFAULT_ACTIVE_HIGH = True

# Definice IO modulů na sběrnici
# - unit_id = Modbus adresa zařízení (1–247)
# - name = logický název (pro MQTT topic)
# - inputs = kolik DI kanálů skutečně používáš (max 8)
MODBUS_MODULES = [
    {
        "unit_id": 128,
        "name": "test_sw1",
        "inputs": 2,   # např. vypínač se 2 tlačítky
    },
    {
        "unit_id": 129,
        "name": "test_sw2",
        "inputs": 1,
    },
    {
        "unit_id": 130,
        "name": "test_sw3",
        "inputs": 1,
    },
    # další moduly podle potřeby...
]

# Logování
LOG_LEVEL = logging.INFO
# -------------------------------------- #


logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("modbus_io_broker")


def create_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    # LWT - broker oznámí offline pokud proces spadne
    client.will_set(f"{MQTT_BASE_TOPIC}/status", "offline", qos=1, retain=True)

    def on_connect(c, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT: connected")
            c.publish(f"{MQTT_BASE_TOPIC}/status", "online", qos=1, retain=True)
        else:
            logger.error(f"MQTT: connect failed rc={rc}")

    def on_disconnect(c, userdata, rc):
        logger.warning(f"MQTT: disconnected rc={rc}")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


def create_modbus_client() -> ModbusSerialClient:
    client = ModbusSerialClient(
        method="rtu",
        port=MODBUS_PORT,
        baudrate=MODBUS_BAUDRATE,
        parity=MODBUS_PARITY,
        stopbits=MODBUS_STOPBITS,
        bytesize=MODBUS_BYTESIZE,
        timeout=MODBUS_TIMEOUT,
    )
    if client.connect():
        logger.info("Modbus: connected")
    else:
        logger.error("Modbus: connect failed")
    return client


def publish_input_state(mqtt_client: mqtt.Client, module_name: str, idx: int, value: bool) -> None:
    # topic: modbus_io/<module>/in1
    topic = f"{MQTT_BASE_TOPIC}/{module_name}/in{idx+1}"
    payload = "ON" if value else "OFF"
    mqtt_client.publish(topic, payload, qos=1, retain=False)


def poll_one(modbus_client, mqtt_client, prev, mod):
    unit = mod["unit_id"]
    name = mod["name"]
    n_inputs = mod["inputs"]

    rr = modbus_client.read_discrete_inputs(address=0, count=n_inputs, unit=unit)
    if rr.isError():
        logger.warning(f"Modbus read error unit={unit}: {rr}")
        return

    bits = list(rr.bits)[:n_inputs]

    if unit not in prev:
        prev[unit] = bits
        return

    for i in range(n_inputs):
        if bool(bits[i]) != bool(prev[unit][i]):
            new_val = bool(bits[i])
            logger.info(f"Change: unit={unit} {name} in{i+1} -> {'ON' if new_val else 'OFF'}")
            publish_input_state(mqtt_client, name, i, new_val)
            prev[unit][i] = new_val


def main():
    logger.info("Starting Modbus IO Broker")

    mqtt_client = create_mqtt_client()
    modbus_client = create_modbus_client()
    prev_states: Dict[int, List[bool]] = {}
    poll_idx = 0

    try:
        while True:
            mod = MODBUS_MODULES[poll_idx]
            poll_idx = (poll_idx + 1) % len(MODBUS_MODULES)

            try:
                poll_one(modbus_client, mqtt_client, prev_states, mod)
            except Exception as e:
                logger.exception(f"Polling exception: {e}")
                try:
                    modbus_client.close()
                except Exception:
                    pass
                time.sleep(0.2)
                try:
                    modbus_client.connect()
                except Exception:
                    pass

            time.sleep(POLL_INTERVAL)


    except KeyboardInterrupt:
        logger.info("Stopping (Ctrl+C)")
    finally:
        try:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            pass
        try:
            modbus_client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
