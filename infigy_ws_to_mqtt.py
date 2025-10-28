import os
import time
import threading
import socketio
import traceback
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# --- connection latches ---
connected = threading.Event()
last_event_ts = time.monotonic() # heartbeat for WS payloads

# --- .env ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

INFIGY_HOST = os.getenv("INFIGY_HOST", "http://127.0.0.1")
SOCKET_PATH = os.getenv("SOCKET_PATH", "/socket.io")
MQTT_HOST   = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT   = int(os.getenv("MQTT_PORT", "1883"))
MQTT_BASE   = os.getenv("MQTT_BASE", "infigy")
AUTH_COOKIE = os.getenv("AUTH_COOKIE", "").strip()
AUTH_BEARER = os.getenv("AUTH_BEARER", "").strip()
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")

# --- MQTT ---
mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id="infigy-bridge")
if MQTT_USER:
    mqttc.username_pw_set(MQTT_USER, MQTT_PASS)

# LWT (bridge offline)
mqttc.will_set(f"{MQTT_BASE}/bridge/online", "0", qos=1, retain=True)
# Auto-reconnect backoff
mqttc.reconnect_delay_set(min_delay=1, max_delay=60)

# --- helpers ---
def touch():
    global last_event_ts
    last_event_ts = time.monotonic()

def kw_to_w(x):
    try:
        return float(x) * 1000.0
    except Exception:
        return 0.0

def publish(mqttc, name, value, retain=True):
    # wrap to keep base/prefix consistent
    mqttc.publish(f"{MQTT_BASE}/{name}", str(value), qos=0, retain=retain)

# logování MQTT stavu
def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"MQTT connected rc={reason_code}")
    if reason_code == mqtt.MQTT_ERR_SUCCESS or reason_code == 0:
        client.publish("zzz_probe", "1", qos=1, retain=True)  # bez prefixu, musí být vidět při odběru na '#'
        # LWT: bridge online
        client.publish(f"{MQTT_BASE}/bridge/online", "1", qos=1, retain=True)
        # selftest
        client.publish(f"{MQTT_BASE}/selftest", "ok", qos=1, retain=True)

        connected.set()

def on_disconnect(client, userdata, reason_code, properties=None):
    print(f"MQTT disconnected rc={reason_code}")

def on_publish(client, userdata, mid, reason_code=None, properties=None):
    # reason_code může být None (MQTT 3.1.1) nebo objekt/list (MQTT v5)
    try:
        print(f"MQTT published mid={mid} rc={reason_code}")
    except Exception:
        print(f"MQTT published mid={mid}")

mqttc.on_connect = on_connect
mqttc.on_disconnect = on_disconnect
mqttc.on_publish = on_publish

# --- Socket.IO ---
sio = socketio.Client(reconnection=True, reconnection_attempts=0, logger=False, engineio_logger=False)

@sio.event
def connect():
    print("infigy_ws_to_mqtt: connected")

@sio.event
def disconnect():
    print("infigy_ws_to_mqtt: disconnected")

@sio.event
def connect_error(msg):
    print("infigy_ws_to_mqtt connect_error:", msg)

@sio.on("store:change")
def on_store_change(data):
    try:
        payload = data.get("payload", {})
        # Teplota bojleru (°C)
        if "HW_TEMP" in payload:
            publish(mqttc, "boiler/temperature", round(float(payload["HW_TEMP"]), 2))

        # Příkon bojleru po fázích (kW -> W) + celkem
        p1 = p2 = p3 = None
        hw_info = payload.get("HW_INFO") or {}
        cons = hw_info.get("Consumption")
        if isinstance(cons, (list, tuple)) and len(cons) >= 3:
            p1, p2, p3 = (kw_to_w(cons[0]), kw_to_w(cons[1]), kw_to_w(cons[2]))
        else:
            # fallback na ploché klíče
            if "HW_INFO.Consumption.0" in payload:
                p1 = kw_to_w(payload.get("HW_INFO.Consumption.0", 0))
                p2 = kw_to_w(payload.get("HW_INFO.Consumption.1", 0))
                p3 = kw_to_w(payload.get("HW_INFO.Consumption.2", 0))

        if all(v is not None for v in (p1, p2, p3)):
            publish(mqttc, "boiler/power_w/phase1", round(p1, 1))
            publish(mqttc, "boiler/power_w/phase2", round(p2, 1))
            publish(mqttc, "boiler/power_w/phase3", round(p3, 1))
            publish(mqttc, "boiler/power_w/total",  round(p1 + p2 + p3, 1))

        # Stavové příznaky
        if "Status" in hw_info:
            publish(mqttc, "boiler/status", str(hw_info["Status"]))
        if "Surplus" in hw_info:
            publish(mqttc, "boiler/surplus_active", "1" if hw_info["Surplus"] else "0")
        if "Err" in hw_info:
            publish(mqttc, "boiler/error", "1" if hw_info["Err"] else "0")

        # Bonus metriky
        if "PV_ACTUAL_POWER" in payload:           # kW
            publish(mqttc, "pv/power_w", round(kw_to_w(payload["PV_ACTUAL_POWER"]), 1))
        if "PV_ACTUAL_POWER_BATTERY" in payload:   # kW
            publish(mqttc, "battery/power_w", round(kw_to_w(payload["PV_ACTUAL_POWER_BATTERY"]), 1))
        if "SURPLUS_INFO_TOTAL" in payload:
            publish(mqttc, "grid/surplus_total_kw", round(float(payload["SURPLUS_INFO_TOTAL"]), 4))

        # Dům – po fázích (pokud Infigy posílá)
        if "EM_INFO_Consumption" in payload:
            em = payload["EM_INFO_Consumption"]
            if isinstance(em, (list, tuple)) and len(em) >= 3:
                tot = em[0] + em[1] + em[2]
                publish(mqttc, "home/power_w/phase1", round(kw_to_w(em[0]), 1))
                publish(mqttc, "home/power_w/phase2", round(kw_to_w(em[1]), 1))
                publish(mqttc, "home/power_w/phase3", round(kw_to_w(em[2]), 1))
                publish(mqttc, "home/power_w/total",  round(kw_to_w(tot), 1))

    #   diagnostické poslání vstupních dat osekaný na délku 800 znaků
    #   publish(mqttc, "debug/last_payload", json.dumps(payload)[:800])  # omezíme délku
    #   diagnostické poslání "1" bez prefixu MQTT_BASE pro lepší ladění
    #   mqttc.publish("zzz_stream", "1", qos=0, retain=False)  # každá zpráva = tichý impulz

    except Exception as e:
        print("infigy_ws_to_mqtt parse error:", e)
        traceback.print_exc()

# --- background watchdogs ---
def watchdog_ws():
    # Reconnect WS if no payload for > 180 s
    while True:
        try:
            age = time.monotonic() - last_event_ts
            if age > 180:
                print("WATCHDOG: no store:change >180s → reconnect WS")
                try:
                    sio.disconnect()
                except Exception:
                    pass
            time.sleep(15)
        except Exception as e:
            print("WATCHDOG error:", e)
            time.sleep(15)

def publish_heartbeat():
    # Publish last_event age in seconds for HA monitoring
    while True:
        try:
            age = int(time.monotonic() - last_event_ts)
            publish("bridge/last_event_age_s", age, retain=True, qos=0)
        except Exception:
            pass
        time.sleep(30)

# --- connect options for Socket.IO ---
EXTRA_HEADERS = {}
if AUTH_COOKIE:
    EXTRA_HEADERS["Cookie"] = AUTH_COOKIE
if AUTH_BEARER:
    EXTRA_HEADERS["Authorization"] = f"Bearer {AUTH_BEARER}"

print(f"CFG INFIGY_HOST={INFIGY_HOST} SOCKET_PATH={SOCKET_PATH}")
print(f"CFG MQTT_HOST={MQTT_HOST}:{MQTT_PORT} USER={'set' if MQTT_USER else 'none'}")
print(f"CFG MQTT_BASE={MQTT_BASE}")


def main():
    # MQTT connect
    mqttc.connect(MQTT_HOST, MQTT_PORT, 60)
    mqttc.loop_start()

    # Počkej max 5 s na připojení (jinak to zkusíme dál – WS poběží a MQTT se připojí později)
    if not connected.wait(5):
       print("MQTT not connected yet; will publish after connect() callback.")
    
    # start background threads
    threading.Thread(target=watchdog_ws, daemon=True).start()
    threading.Thread(target=publish_heartbeat, daemon=True).start()

    # WS loop – prefer pure websocket (more robust long-term)
    while True:
        try:
            sio.connect(
                INFIGY_HOST,
                socketio_path=SOCKET_PATH,
                headers=EXTRA_HEADERS,
                transports=["websocket"],
                wait_timeout=10
            )
            sio.wait()
        except Exception as e:
            print("infigy_ws_to_mqtt connect error:", e)
            traceback.print_exc()
            time.sleep(5)

if __name__ == "__main__":
    main()
