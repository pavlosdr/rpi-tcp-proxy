import os
import time
import json
import threading
import socketio
import socket
import traceback
import sys
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# --- connection latches ---
connected = threading.Event()
last_event_ts = time.monotonic() # heartbeat for WS payloads

# --- log runtime ---
print("__file__ running from:", __file__)
print("PYTHON:", sys.executable)
print("PAHO_VERSION:", getattr(mqtt, "__version__", "unknown"))
print("HAS_V2:", hasattr(mqtt, "CallbackAPIVersion"))

# aktuální výkonové hodnoty (W) pro integrátor
current_power = {
"home": 0.0,
"pv": 0.0,
"grid_import": 0.0, # >0 = beru ze sítě
"grid_export": 0.0, # >0 = posílám do sítě
"bat_charge": 0.0, # >0 = nabíjím z/do sítě/FVE
"bat_discharge": 0.0, # >0 = vybíjím do domu/sítě
"boiler_total": 0.0
}


# integrované energie (kWh)
energy_totals = {
"home": 0.0,
"pv": 0.0,
"grid_import": 0.0,
"grid_export": 0.0,
"bat_charge": 0.0,
"bat_discharge": 0.0,
"boiler_total": 0.0
}

# --- .env ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

# --- Konfig z .env ---
# --- MQTT ---
MQTT_HOST   = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT   = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER   = os.getenv("MQTT_USER", "")
MQTT_PASS   = os.getenv("MQTT_PASS", "")
MQTT_BASE   = os.getenv("MQTT_BASE_INFIGY", "infigy")
CLIENT_ID   = os.getenv("CLIENT_ID_INFIGY","infigy-bridge")
AUTH_COOKIE = os.getenv("AUTH_COOKIE", "").strip()
AUTH_BEARER = os.getenv("AUTH_BEARER", "").strip()
MQTT_WATCHDOG_INTERVAL_S = int(os.getenv("MQTT_WATCHDOG_INTERVAL_S", "15"))
MQTT_RECONNECT_BACKOFF_MAX_S = int(os.getenv("MQTT_RECONNECT_BACKOFF_MAX_S", "60"))
# --- Infigy ---
INFIGY_HOST = os.getenv("INFIGY_HOST", "http://127.0.0.1")
SOCKET_PATH = os.getenv("SOCKET_PATH", "/socket.io")
DISCOVERY_PREFIX = os.getenv("DISCOVERY_PREFIX", "homeassistant")
DEVICE_ID = os.getenv("DEVICE_ID", "Infigy") 
ENTITY_PREFIX = os.getenv("ENTITY_PREFIX", "infigy") # optional, defaults to "infigy"
SW_VERSION = os.getenv("SW_VERSION", "1.1")
ENERGY_STATE_PATH = os.getenv("ENERGY_STATE_PATH", os.path.join(BASE_DIR, "energy_state.json"))
ENERGY_PUBLISH_INTERVAL_S = int(os.getenv("ENERGY_PUBLISH_INTERVAL_S", "30"))
INTEGRATOR_TICK_S = float(os.getenv("INTEGRATOR_TICK_S", "5"))
HEARTBEAT_MAX_AGE_S = int(os.getenv("HEARTBEAT_MAX_AGE_S", "180"))

# --- Singleton lock: zabran spusteni 2. instance ---
_singleton = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
try:
    _singleton.bind("\0infigy_ws_to_mqtt.singleton")
except OSError:
    print("Another infigy_ws_to_mqtt instance is running. Exiting.")
    sys.exit(1)

# --- MQTT klient ---
mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=CLIENT_ID,clean_session=True)
if MQTT_USER:
    mqttc.username_pw_set(MQTT_USER, MQTT_PASS)

# LWT (bridge offline) dostupnost zařízení
mqttc.will_set(f"{MQTT_BASE}/bridge/online", "0", qos=1, retain=True)
# Auto-reconnect backoff
mqttc.reconnect_delay_set(min_delay=2, max_delay=MQTT_RECONNECT_BACKOFF_MAX_S)

# --- helpers ---
def touch():
    global last_event_ts
    last_event_ts = time.monotonic()

def kw_to_w(x):
    try:
        return float(x) * 1000.0
    except Exception:
        return 0.0

def publish(topic_suffix, payload, retain=True, qos=1):
    # Bezpecny publish s odchytem vyjimek
    topic = f"{MQTT_BASE}/{topic_suffix}"
    try:
        mqttc.publish(topic, str(payload), qos=qos, retain=retain)
    except Exception as e:
        print(f"[MQTT] publish failed {topic}: {e}")    


def load_energy_state():
    try:
        with open(ENERGY_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                energy_totals.update({k: float(v) for k, v in data.items() if k in energy_totals})
    except FileNotFoundError:
        pass
    except Exception as e:
        print("ENERGY STATE load error:", e)

def save_energy_state():
    try:
        tmp_path = ENERGY_STATE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(energy_totals, f, ensure_ascii=False)
        os.replace(tmp_path, ENERGY_STATE_PATH)
    except Exception as e:
        print("ENERGY STATE save error:", e)

# ---------- MQTT Discovery ----------
def _disc_topic(domain: str, object_id: str) -> str:
    return f"{DISCOVERY_PREFIX}/{domain}/{DEVICE_ID}/{object_id}/config"

def _disc_device():
    return {
        "identifiers": [DEVICE_ID],
        "manufacturer": "PavlosDr",
        "model": "Infigy WS>>MQTT Bridge",
        "name": "Infigy",
        "sw_version": SW_VERSION,
    }

def _oid(suffix: str) -> str:
    # Build a stable, lowercase, underscore-only object_id
    # Final entity_id becomes: <domain>.<object_id>
    return f"{ENTITY_PREFIX}_{suffix}".lower()

def publish_discovery():
    dev = _disc_device()
    # Home Assistant will create
    # entities with deterministic entity_id based on `object_id` (domain.object_id),
    # independent of the display `name`.
    cfgs = [
        # -------- Živé výkonové a teplotní senzory --------
        ("sensor", _oid("boiler_temperature"), {
            #"object_id": _oid("boiler_temperature"),
            "default_entity_id": f"sensor.{_oid('boiler_temperature')}",
            "name": "Boiler aktuální teplota",
            "unique_id": f"{DEVICE_ID.lower()}_boiler_temperature",
            "state_topic": f"{MQTT_BASE}/boiler/temperature",
            "unit_of_measurement": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
            "device": dev,
        }),
        # ------- Boiler per-phase power (W) + total -------
        ("sensor", _oid("boiler_power_w_phase1"), {
            #"object_id": _oid("boiler_power_w_phase1"),
            "default_entity_id": f"sensor.{_oid('boiler_power_w_phase1')}",
            "name": "Boiler aktuální odběr fáze 1",
            "unique_id": f"{DEVICE_ID.lower()}_boiler_power_w_phase1",
            "state_topic": f"{MQTT_BASE}/boiler/power_w/phase1",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("boiler_power_w_phase2"), {
            #"object_id": _oid("boiler_power_w_phase2"),
            "default_entity_id": f"sensor.{_oid('boiler_power_w_phase2')}",
            "name": "Boiler aktuální odběr fáze 2",
            "unique_id": f"{DEVICE_ID.lower()}_boiler_power_w_phase2",
            "state_topic": f"{MQTT_BASE}/boiler/power_w/phase2",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("boiler_power_w_phase3"), {
            #"object_id": _oid("boiler_power_w_phase3"),
            "default_entity_id": f"sensor.{_oid('boiler_power_w_phase3')}",
            "name": "Boiler aktuální odběr fáze 3",
            "unique_id": f"{DEVICE_ID.lower()}_boiler_power_w_phase3",
            "state_topic": f"{MQTT_BASE}/boiler/power_w/phase3",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("boiler_power_w_total"), {
            #"object_id": _oid("boiler_power_w_total"),
            "default_entity_id": f"sensor.{_oid('boiler_power_w_total')}",
            "name": "Boiler aktuální odběr",
            "unique_id": f"{DEVICE_ID.lower()}_boiler_power_w_total",
            "state_topic": f"{MQTT_BASE}/boiler/power_w/total",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("home_power_w"), {
            #"object_id": _oid("home_power_w"),
            "default_entity_id": f"sensor.{_oid('home_power_w')}",
            "name": "Spotřeba domu",
            "unique_id": f"{DEVICE_ID.lower()}_home_power",
            "state_topic": f"{MQTT_BASE}/home/power_w/total",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("battery_power_w"), {
            #"object_id": _oid("battery_power_w"),
            "default_entity_id": f"sensor.{_oid('battery_power_w')}",
            "name": "Baterie",
            "unique_id": f"{DEVICE_ID.lower()}_battery_power",
            "state_topic": f"{MQTT_BASE}/battery/power_w",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("grid_surplus_kw"), {
            #"object_id": _oid("grid_surplus_kw"),
            "default_entity_id": f"sensor.{_oid('grid_surplus_kw')}",
            "name": "Síť",
            "unique_id": f"{DEVICE_ID.lower()}_grid_surplus_kw",
            "state_topic": f"{MQTT_BASE}/grid/surplus_total_kw",
            "unit_of_measurement": "kW",
            "device_class": "power",
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("pv_power_w"), {
            #"object_id": _oid("pv_power_w"),
            "default_entity_id": f"sensor.{_oid('pv_power_w')}",
            "name": "FVE",
            "unique_id": f"{DEVICE_ID.lower()}_pv_power",
            "state_topic": f"{MQTT_BASE}/pv/power_w",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("battery_soc"), {
            #"object_id": _oid("battery_soc"),
            "default_entity_id": f"sensor.{_oid('battery_soc')}",
            "name": "Stav baterie",
            "unique_id": f"{DEVICE_ID.lower()}_battery_soc",
            "state_topic": f"{MQTT_BASE}/battery/soc",
            "unit_of_measurement": "%",
            "device_class": "battery",
            "state_class": "measurement",
            "device": dev,
        }),

        # -------- Health --------
        ("sensor", _oid("bridge_last_event_age_s"), {
            #"object_id": _oid("bridge_last_event_age_s"),
            "default_entity_id": f"sensor.{_oid('bridge_last_event_age_s')}",
            "name": "Infigy doba od poslední události",
            "unique_id": f"{DEVICE_ID.lower()}_last_event_age",
            "state_topic": f"{MQTT_BASE}/bridge/last_event_age_s",
            "unit_of_measurement": "s",
            "device_class": "duration",
            "state_class": "measurement",
            "device": dev,
        }),
        ("binary_sensor", _oid("bridge_online"), {
            #"object_id": _oid("bridge_online"),
            "default_entity_id": f"sensor.{_oid('bridge_online')}",
            "name": "Infigy bridge online",
            "unique_id": f"{DEVICE_ID.lower()}_bridge_online",
            "state_topic": f"{MQTT_BASE}/bridge/online",
            "payload_on": "1",
            "payload_off": "0",
            "device": dev,
        }),
        ("binary_sensor", _oid("ws_flow_ok"), {
            #"object_id": _oid("ws_flow_ok"),
            "default_entity_id": f"sensor.{_oid('ws_flow_ok')}",
            "name": "Infigy poskytuje data",
            "unique_id": f"{DEVICE_ID.lower()}_ws_flow_ok",
            "state_topic": f"{MQTT_BASE}/bridge/ws_flow_ok",
            "payload_on": "1",
            "payload_off": "0",
            "device_class": "connectivity",
            # (volitelné) dostupnost podle LWT
            "availability_topic": f"{MQTT_BASE}/bridge/online",
            "payload_available": "1",
            "payload_not_available": "0",
            "device": dev,
        }),

        # -------- Integrované energie (kWh) --------
        ("sensor", _oid("energy_home_kwh"), {
            #"object_id": _oid("energy_home_kwh"),
            "default_entity_id": f"sensor.{_oid('energy_home_kwh')}",
            "name": "Home Energy",
            "unique_id": f"{DEVICE_ID.lower()}_energy_home_kwh",
            "state_topic": f"{MQTT_BASE}/energy/home_kwh",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "device": dev,
        }),
            ("sensor", _oid("energy_pv_kwh"), {
            #"object_id": _oid("energy_pv_kwh"),
            "default_entity_id": f"sensor.{_oid('energy_pv_kwh')}",
            "name": "PV Energy",
            "unique_id": f"{DEVICE_ID.lower()}_energy_pv_kwh",
            "state_topic": f"{MQTT_BASE}/energy/pv_kwh",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "device": dev,
        }),
        ("sensor", _oid("energy_grid_import_kwh"), {
            #"object_id": _oid("energy_grid_import_kwh"),
            "default_entity_id": f"sensor.{_oid('energy_grid_import_kwh')}",
            "name": "Grid Import Energy",
            "unique_id": f"{DEVICE_ID.lower()}_energy_grid_import_kwh",
            "state_topic": f"{MQTT_BASE}/energy/grid_import_kwh",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "device": dev,
        }),
        ("sensor", _oid("energy_grid_export_kwh"), {
            #"object_id": _oid("energy_grid_export_kwh"),
            "default_entity_id": f"sensor.{_oid('energy_grid_export_kwh')}",
            "name": "Grid Export Energy",
            "unique_id": f"{DEVICE_ID.lower()}_energy_grid_export_kwh",
            "state_topic": f"{MQTT_BASE}/energy/grid_export_kwh",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "device": dev,
        }),
        ("sensor", _oid("energy_bat_charge_kwh"), {
            #"object_id": _oid("energy_bat_charge_kwh"),
            "default_entity_id": f"sensor.{_oid('energy_bat_charge_kwh')}",
            "name": "Battery Charge Energy",
            "unique_id": f"{DEVICE_ID.lower()}_energy_bat_charge_kwh",
            "state_topic": f"{MQTT_BASE}/energy/bat_charge_kwh",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "device": dev,
        }),
        ("sensor", _oid("energy_bat_discharge_kwh"), {
            #"object_id": _oid("energy_bat_discharge_kwh"),
            "default_entity_id": f"sensor.{_oid('energy_bat_discharge_kwh')}",
            "name": "Battery Discharge Energy",
            "unique_id": f"{DEVICE_ID.lower()}_energy_bat_discharge_kwh",
            "state_topic": f"{MQTT_BASE}/energy/bat_discharge_kwh",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "device": dev,
        }),
        # ------- Boiler Energy (kWh) integrated -------
        ("sensor", _oid("energy_boiler_kwh"), {
            #"object_id": _oid("energy_boiler_kwh"),
            "default_entity_id": f"sensor.{_oid('energy_boiler_kwh')}",
            "name": "Boiler Energy",
            "unique_id": f"{DEVICE_ID.lower()}_energy_boiler_kwh",
            "state_topic": f"{MQTT_BASE}/energy/boiler_kwh",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "device": dev,
        }),
    ]

    for domain, object_id, payload in cfgs:
        topic = _disc_topic(domain, object_id)
        mqttc.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=True)

    # Notes:
    # - entity_id will be stable: e.g. sensor.infigy_boiler_temperature, binary_sensor.infigy_bridge_online, ...
    # - You can change ENTITY_PREFIX via .env to namespace multiple bridges.

# --- MQTT callbacks ---
# v2 i v1 kompatibilní on_connect - zamezí error v rozdílném počtu parametrů
def _normalize_code(raw):
    code = getattr(raw, "value", raw)
    try: return int(code)
    except: return 0

def on_connect(client, userdata, *args, **kwargs):
    # v2: (flags, reason_code, properties) / v1: (flags, rc)
    raw = kwargs.get("reason_code", kwargs.get("rc",0))
    # properties = args[2] if len(args) > 2 else kwargs.get("properties")
    if len(args) > 1:
        raw = args[1]
    code = _normalize_code(raw)
    print(f"MQTT connected rc={code}")
    if code == 0:
        publish("bridge/online", "1", retain=True, qos=1)
        try:
            publish_discovery() # auto discovery po připojení
        except Exception as e:
            print(f"publish_discovery() failed: {e}")
        connected.set()

def on_disconnect(client, userdata, *args, **kwargs):
    # v2: (reason_code, properties) / v1: (rc)
    raw = kwargs.get("reason_code", kwargs.get("rc", -1))
    if len(args) > 0:
        raw = args[0]
    code = _normalize_code(raw)
    print(f"MQTT disconnected rc={code}")
    connected.clear()

mqttc.on_connect = on_connect
mqttc.on_disconnect = on_disconnect

# --- Socket.IO ---
sio = socketio.Client(
    reconnection=True, 
    reconnection_attempts=0, 
    logger=False, 
    engineio_logger=False
)

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
    touch()
    try:
        payload = data.get("payload", {})
        # Teplota bojleru (°C)
        if "HW_TEMP" in payload:
            publish("boiler/temperature", round(float(payload["HW_TEMP"]), 2), qos=0)

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
            publish("boiler/power_w/phase1", round(p1, 1), qos=0)
            publish("boiler/power_w/phase2", round(p2, 1), qos=0)
            publish("boiler/power_w/phase3", round(p3, 1), qos=0)
            total_w = round(p1 + p2 + p3, 1)
            publish("boiler/power_w/total", total_w, qos=0)
            current_power["boiler_total"] = float(total_w)

        # Stavové příznaky
        if "Status" in hw_info:
            publish("boiler/status", str(hw_info["Status"]), qos=1)
        if "Surplus" in hw_info:
            publish("boiler/surplus_active", "1" if hw_info["Surplus"] else "0", qos=1)
        if "Err" in hw_info:
            publish("boiler/error", "1" if hw_info["Err"] else "0", qos=1)

        # SOC battery
        if "PV_ACTUAL_SOC" in payload:
            publish("battery/soc", round(float(payload["PV_ACTUAL_SOC"]), 1), qos=1)

        # Bonus metriky
        if "PV_ACTUAL_POWER" in payload:           # kW
            pv_w = round(kw_to_w(payload["PV_ACTUAL_POWER"]), 1)
            publish("pv/power_w", pv_w, qos=0)
            current_power["pv"] = float(pv_w)
        if "PV_ACTUAL_POWER_BATTERY" in payload:   # kW (kladné = charge, záporné = discharge)
            bat_w = round(kw_to_w(payload["PV_ACTUAL_POWER_BATTERY"]), 1)
            publish("battery/power_w", bat_w, qos=0)
            # rozdělení na charge/discharge
            current_power["bat_charge"] = max(0.0, float(bat_w))
            current_power["bat_discharge"] = max(0.0, -float(bat_w))
        if "SURPLUS_INFO_TOTAL" in payload:        # kW (+ export, - import)
            s_kw = round(float(payload["SURPLUS_INFO_TOTAL"]), 4)
            publish("grid/surplus_total_kw", s_kw, qos=0)
            # odvoď import/export ve W
            if s_kw >= 0:
                current_power["grid_export"] = float(s_kw) * 1000.0
                current_power["grid_import"] = 0.0
            else:
                current_power["grid_import"] = float(-s_kw) * 1000.0
                current_power["grid_export"] = 0.0

        # Dům – po fázích (pokud Infigy posílá) (kW -> W)
        if "EM_INFO_Consumption" in payload:
            em = payload["EM_INFO_Consumption"]
            if isinstance(em, (list, tuple)) and len(em) >= 3:
                tot = em[0] + em[1] + em[2]
                tot_w = round(kw_to_w(tot), 1)
                publish("home/power_w/phase1", round(kw_to_w(em[0]), 1), qos=0)
                publish("home/power_w/phase2", round(kw_to_w(em[1]), 1), qos=0)
                publish("home/power_w/phase3", round(kw_to_w(em[2]), 1), qos=0)
                publish("home/power_w/total", tot_w, qos=0)
                current_power["home"] = float(tot_w)

    #   diagnostické poslání vstupních dat osekaný na délku 800 znaků
    #   publish(mqttc, "debug/last_payload", json.dumps(payload)[:800])  # omezíme délku
    #   diagnostické poslání "1" bez prefixu MQTT_BASE pro lepší ladění
    #   mqttc.publish("zzz_stream", "1", qos=0, retain=False)  # každá zpráva = tichý impulz

    except Exception as e:
        print("infigy_ws_to_mqtt parse error:", e)
        traceback.print_exc()

# --- background watchdogs + heartbeat ---
def watchdog_ws():
    # Reconnect WS if no payload for > 180 s
    while True:
        try:
            age = time.monotonic() - last_event_ts
            if age > 180:
                print("WATCHDOG: no store:change >180s >>> reconnect infigy_ws_to_mqtt")
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
            # binární stav toku z infigy podle prahu HEARTBEAT_MAX_AGE_S
            ws_ok = "1" if age < HEARTBEAT_MAX_AGE_S else "0"
            publish("bridge/ws_flow_ok", ws_ok, retain=True, qos=0)
        except Exception:
            pass
        time.sleep(30)

#  Watchdog vlákno MQTT (automatický reconnect + obnova loopu)
def _mqtt_watchdog_loop(stop_evt: threading.Event):
    """
    - Každých MQTT_WATCHDOG_INTERVAL_S ověří připojení.
    - Pokud není připojeno, zkusí reconnect s exponenciálním backoffem (2..MAX s).
    - Pokud by z nějakého důvodu neběželo loop_start() vlákno, znovu ho spustí.
    """
    backoff = 2
    while not stop_evt.is_set():
        try:
            # 1) Když není připojeno → reconnect
            if not mqttc.is_connected():
                print(f"[MQTT-WD] Not connected >> reconnect() (backoff={backoff}s)")
                try:
                    mqttc.reconnect()
                except Exception as e:
                    print(f"[MQTT-WD] reconnect() failed: {e}")
                else:
                    backoff = 2  # po úspěchu reset backoffu

            # 2) Pokud neběží vnitřní loop thread, znovu ho nastartuj
            t = getattr(mqttc, "_thread", None)
            if t is None or not getattr(t, "is_alive", lambda: False)():
                # Pozn.: Paho bez problémů snese opakované volání loop_start()
                try:
                    mqttc.loop_start()
                    print("[MQTT-WD] loop_start() ensured")
                except Exception as e:
                    print(f"[MQTT-WD] loop_start() failed: {e}")

            # 3) Jemný backoff, pokud stále odpojeno
            if not mqttc.is_connected():
                stop_evt.wait(min(backoff, MQTT_RECONNECT_BACKOFF_MAX_S))
                backoff = min(backoff * 2, MQTT_RECONNECT_BACKOFF_MAX_S)
            else:
                stop_evt.wait(MQTT_WATCHDOG_INTERVAL_S)
        except Exception as e:
            print(f"[MQTT-WD] Unexpected error: {e}")
            stop_evt.wait(MQTT_WATCHDOG_INTERVAL_S)

# --- integrátor energií (trapézová aproximace) ---

def energy_integrator():
    load_energy_state()
    last_t = time.monotonic()
    last_p = current_power.copy() # W
    pub_timer = 0.0
    while True:
        try:
            time.sleep(INTEGRATOR_TICK_S)
            now = time.monotonic()
            dt = now - last_t # s
            if dt <= 0:
                last_t = now
                continue

            # trapézová integrace pro každý kanál
            for key, p_curr in current_power.items():
                p_prev = float(last_p.get(key, 0.0))
                # Wh = (P_prev + P_curr)/2 * dt[h]
                wh = (p_prev + float(p_curr)) / 2.0 * (dt / 3600.0)
                kwh = wh / 1000.0
                if kwh > 0:
                    energy_totals[key] = float(energy_totals.get(key, 0.0)) + kwh
            # posun stavů
            last_p = current_power.copy()
            last_t = now

            # periodické publikování a persist
            pub_timer += dt
            if pub_timer >= ENERGY_PUBLISH_INTERVAL_S:
                pub_timer = 0.0
                publish("energy/home_kwh", round(energy_totals["home"], 6), retain=True, qos=1)
                publish("energy/pv_kwh", round(energy_totals["pv"], 6), retain=True, qos=1)
                publish("energy/grid_import_kwh", round(energy_totals["grid_import"], 6), retain=True, qos=1)
                publish("energy/grid_export_kwh", round(energy_totals["grid_export"], 6), retain=True, qos=1)
                publish("energy/bat_charge_kwh", round(energy_totals["bat_charge"], 6), retain=True, qos=1)
                publish("energy/bat_discharge_kwh", round(energy_totals["bat_discharge"], 6), retain=True, qos=1)
                publish("energy/boiler_kwh", round(energy_totals["boiler_total"], 6), retain=True, qos=1)
                save_energy_state()
        except Exception as e:
            print("ENERGY integrator error:", e)
            time.sleep(2)

# --- connect options for Socket.IO ---
EXTRA_HEADERS = {}
if AUTH_COOKIE:
    EXTRA_HEADERS["Cookie"] = AUTH_COOKIE
if AUTH_BEARER:
    EXTRA_HEADERS["Authorization"] = f"Bearer {AUTH_BEARER}"

print(f"CFG INFIGY_HOST={INFIGY_HOST} SOCKET_PATH={SOCKET_PATH}")
print(f"CFG MQTT_HOST={MQTT_HOST}:{MQTT_PORT} USER={'set' if MQTT_USER else 'none'}")
print(f"CFG MQTT_BASE={MQTT_BASE} DEVICE_ID={DEVICE_ID}")


def main():
    # MQTT connect
    mqttc.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    mqttc.loop_start()

    # Počkej max 5 s na připojení (jinak to zkusíme dál – WS poběží a MQTT se připojí později)
    if not connected.wait(5):
       print("MQTT not connected yet; will publish after connect() callback.")
    
    # Start MQTT watchdogu
    stop_evt = threading.Event()
    threading.Thread(target=_mqtt_watchdog_loop, args=(stop_evt,), daemon=True).start()

    # start background vlákna
    threading.Thread(target=watchdog_ws, daemon=True).start()
    threading.Thread(target=publish_heartbeat, daemon=True).start()
    threading.Thread(target=energy_integrator, daemon=True).start()

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
