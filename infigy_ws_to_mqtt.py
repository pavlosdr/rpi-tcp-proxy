"""
Infigy → MQTT bridge

Služba zajišťuje přenos dat z Infigy (Socket.IO / WebSocket API)
do MQTT brokeru a jejich integraci do Home Assistantu pomocí MQTT Discovery.

Funkce:
- Připojení k Infigy API (Socket.IO)
- Publikování živých výkonů, stavů a energií do MQTT
- MQTT HA Discovery (senzory, binary_senzory)
- Heartbeat / watchdog pro detekci výpadků dat
- Graceful shutdown + LWT

MQTT:
- base topic: <MQTT_BASE_TOPIC>
- discovery: <DISCOVERY_PREFIX>/<domain>/<DEVICE_ID>/<object_id>/config
- výsledné entity_id je určeno položkou `default_entity_id` v discovery payloadu

Konfigurace:
- .env (MQTT, Infigy, entity prefix, timing)

Určeno pro běh jako systemd service na Raspberry Pi.
"""
import os
import time
import json
import threading
import logging
import socketio
import socket
import sys
import signal
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from envfile import env_str, env_int

# ---------------------- KONFIGURACE ---------------------- #

# ------------------------ .env --------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

# ------------------- Konfig z .env ----------------------- #
# ------------------------ MQTT --------------------------- # 
MQTT_HOST   = env_str("INFIGY_MQTT_HOST", "localhost")
MQTT_PORT   = env_int("INFIGY_MQTT_PORT", 1883)
MQTT_USER   = env_str("INFIGY_MQTT_USER", "")
MQTT_PASS   = env_str("INFIGY_MQTT_PASS", "")
MQTT_BASE_TOPIC   = env_str("INFIGY_MQTT_BASE", "infigy")
MQTT_CLIENT_ID   = env_str("INFIGY_MQTT_CLIENT_ID","infigy-bridge")
AUTH_COOKIE = env_str("INFIGY_AUTH_COOKIE", "").strip()
AUTH_BEARER = env_str("INFIGY_AUTH_BEARER", "").strip()
MQTT_WATCHDOG_INTERVAL_S = env_int("INFIGY_MQTT_WATCHDOG_INTERVAL_S", 15)
MQTT_RECONNECT_BACKOFF_MAX_S = env_int("INFIGY_MQTT_RECONNECT_BACKOFF_MAX_S", 60)
# ----------------------- INFIGY --------------------------- #
INFIGY_HOST = env_str("INFIGY_HOST", "http://127.0.0.1")
SOCKET_PATH = env_str("INFIGY_SOCKET_PATH", "/socket.io")
DISCOVERY_PREFIX = env_str("INFIGY_MQTT_DISCOVERY_PREFIX", "homeassistant").strip()
DEVICE_ID = env_str("INFIGY_MQTT_DEVICE_ID", "rpi-3b-broker") 
DEVICE_NAME = env_str("INFIGY_MQTT_DEVICE_NAME","Raspberry 3B broker")
ENTITY_PREFIX = env_str("INFIGY_MQTT_ENTITY_PREFIX", "rpi_broker_infigy")
HEARTBEAT_MAX_AGE_S = env_int("INFIGY_HEARTBEAT_MAX_AGE_S", 180)

# ---------------------- Logging ---------------------------
# Log minimization
LOG_LEVEL = getattr(logging, env_str("INFIGY_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger("infigy-mqtt")
logger.setLevel(LOG_LEVEL)

# ---------------------- LOG runtime ---------------------- #
logger.debug("__file__ running from: %s", __file__)
logger.debug("PYTHON: %s", sys.executable)
logger.debug("PAHO_VERSION: %s", getattr(mqtt, "__version__", "unknown"))
logger.debug("HAS_V2: %s", hasattr(mqtt, "CallbackAPIVersion"))

# ------------------ connection latches ------------------- #
connected = threading.Event()
# last_event_ts chráníme lockem (touch() + watchdog + heartbeat)
last_event_lock = threading.Lock()
last_event_ts = time.monotonic()  # monotonic timestamp poslední "události" (store:change)
stop_evt = threading.Event()
# ------- Singleton lock: zabran spusteni 2. instance ----- #
_singleton = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
try:
    _singleton.bind("\0infigy_ws_to_mqtt.singleton")
except OSError:
    logger.warning("Another infigy_ws_to_mqtt instance is running. Exiting.")
    sys.exit(1)

# ------------------------ MQTT helpers---------------------- #
def mqtt_topic(*parts: str) -> str:
    base = MQTT_BASE_TOPIC.strip("/")
    p = "/".join(x.strip("/") for x in parts if x)
    return f"{base}/{p}" if p else base
# ------------------------ MQTT klient ---------------------- #
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=MQTT_CLIENT_ID,clean_session=True)
if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)

# LWT (bridge offline) dostupnost zařízení
client.will_set(f"{MQTT_BASE_TOPIC}/bridge/online", "0", qos=1, retain=True)
# Auto-reconnect backoff
client.reconnect_delay_set(min_delay=2, max_delay=MQTT_RECONNECT_BACKOFF_MAX_S)

# ------------------------ helpers -------------------------- #
def touch() -> None:
    global last_event_ts
    with last_event_lock:
        last_event_ts = time.monotonic()

def get_last_event_age_s() -> int:
    """Vrátí stáří poslední události v sekundách (monotonic, vždy >= 0)."""
    with last_event_lock:
        ts = float(last_event_ts)
    age = time.monotonic() - ts
    if age < 0:
        age = 0.0
    return int(age)

def kw_to_w(x):
    try:
        return float(x) * 1000.0
    except Exception:
        return 0.0

def publish(topic_suffix, payload, retain=True, qos=1):
    # Bezpecny publish s odchytem vyjimek
    topic = f"{MQTT_BASE_TOPIC}/{topic_suffix}"
    try:
        client.publish(topic, str(payload), qos=qos, retain=retain)
    except Exception as e:
        logger.exception("MQTT publish failed topic=%s", topic)    


###############################################################
# HELPERY PRO SELEKTIVNÍ VÝPIS PAYLOADU DO LOGU >>> pro vývoj
###############################################################
TOTAL_EXCLUDE_PATTERNS = [
    "PV_SURPLUS_ENERGY_PERC_TOTAL.0", 
    "PV_SURPLUS_ENERGY_PERC_TOTAL.1",
    "PV_SURPLUS_ENERGY_PERC_TOTAL.2",
    "SURPLUS_INFO_TOTAL",    
    "NEW_EM_ENERGY_CONSUMED_PHASE_TOTAL.0", 
    "NEW_EM_ENERGY_CONSUMED_PHASE_TOTAL.1", 
    "NEW_EM_ENERGY_CONSUMED_PHASE_TOTAL.2",  
    "NEW_EM_ENERGY_CONSUMED_TOTAL",
    "NEW_PV_BATTERY_DISCHARGE_POWER_TOTAL.",
    "NEW_PV_BATTERY_DISCHARGE_POWER_TOTAL.goodwe-1",
    "NEW_PV_BATTERY_DISCHARGE_TOTAL",
    "PV_ENERGY_PRODUCED_TOTAL_FOR_GRAPHS",
    "PV_ENERGY_PRODUCED_TOTAL_FOR_GRAPHS_R",
    "NEW_PV_ENERGY_PRODUCED_TOTAL_FOR_GRAPHS",

    "PWM_pulse_time_real",
    "ACTUAL_TIME",
    "EM_ACTUAL_LOAD_MAX",
    "PWM_pulse_time_real",
    "DO.6",
    "DO.7",
    "DO.8",
    "PV_cnt_com_ok",
    "anim_HOME_ACTUAL_POWER",

    # zajímavá
    "EM_ENERGY_OVERFLOW_TOTAL_R",
    "NEW_EM_ENERGY_OVERFLOW_TOTAL",
    "PV_BATTERY_DISCHARGE_TOTAL",
    "PV_BATTERY_DISCHARGE_TOTAL_R",
    "EM_ENERGY_CONSUMED_TOTAL_R",
    "EM_ENERGY_CONSUMED_TOTAL",
    "HOME_CONSUMPTION_TOTAL",
    "HW_ENERGY_PRODUCED_TOTAL",
    "HW_TEMP",
    "PWM_pulse_time_real",
]

def _is_excluded_total(full_key: str) -> bool:
    fk = full_key.upper()

    # vylouceni podle nazvu
    for pat in TOTAL_EXCLUDE_PATTERNS:
        if pat in fk:
            return True
    # vylouceni fazovych indexu .0 .1 .2
    #if fk.endswith((".0", ".1", ".2")):
    #    return True
    return False

def log_total_keys(payload: dict, prefix: str = "") -> None:
    """
    Rekurzivne projde payload a zaloguje vsechny klice,
    nejsou na seznamu vyloucenych.
    """
    if not isinstance(payload, dict):
        return

    for key, value in payload.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if not _is_excluded_total(full_key):
            logger.debug(
                "INFIGY TOTAL: %s = %s",
                full_key,
                value,
            )
        if isinstance(value, dict):
            log_total_keys(value, full_key)
###############################################################
###############################################################
# ----------------------- MQTT Discovery -------------------- #
def _disc_topic(domain: str, object_id: str) -> str:
    return f"{DISCOVERY_PREFIX}/{domain}/{DEVICE_ID}/{object_id}/config"

def _disc_device():
    return {
        "identifiers": [DEVICE_ID],
        "manufacturer": "PavlosDr",
        "model": "infigy_ws_to_mqtt.py",
        "name": DEVICE_NAME,
    }

def _oid(suffix: str) -> str:
    suf = (suffix or "").strip().lower().replace(" ", "_")
    return f"{ENTITY_PREFIX}_{suf}"

def _uid(suffix: str) -> str:
    # globálně unikátní napříč HA + stabilní
    suf = (suffix or "").strip().lower().replace(" ", "_")
    return f"{ENTITY_PREFIX}_{suf}".lower()

def publish_discovery():
    dev = _disc_device()

    entities = [
        # -------- Živé výkonové a teplotní senzory --------
        ("sensor", _oid("boiler_temperature"), {
            "name": "Boiler aktuální teplota",
            "unique_id": _uid("boiler_temperature"),
            "state_topic": mqtt_topic("boiler", "temperature"),
            "unit_of_measurement": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
            "device": dev,
        }),

        # ------- Boiler per-phase power (W) + total -------
        ("sensor", _oid("boiler_power_w_phase1"), {
            "name": "Boiler aktuální odběr fáze 1",
            "unique_id": _uid("boiler_power_w_phase1"),
            "state_topic": mqtt_topic("boiler", "power_w", "phase1"),
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "icon": "mdi:water-boiler",
            "device": dev,
        }),
        ("sensor", _oid("boiler_power_w_phase2"), {
            "name": "Boiler aktuální odběr fáze 2",
            "unique_id": _uid("boiler_power_w_phase2"),
            "state_topic": mqtt_topic("boiler", "power_w", "phase2"),
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "icon": "mdi:water-boiler",
            "device": dev,
        }),
        ("sensor", _oid("boiler_power_w_phase3"), {
            "name": "Boiler aktuální odběr fáze 3",
            "unique_id": _uid("boiler_power_w_phase3"),
            "state_topic": mqtt_topic("boiler", "power_w", "phase3"),
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "icon": "mdi:water-boiler",
            "device": dev,
        }),
        ("sensor", _oid("boiler_power_w_total"), {
            "name": "Boiler aktuální odběr",
            "unique_id": _uid("boiler_power_w_total"),
            "state_topic": mqtt_topic("boiler", "power_w", "total"),
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "icon": "mdi:water-boiler",
            "device": dev,
        }),

        ("sensor", _oid("home_power_w"), {
            "name": "Spotřeba domu",
            "unique_id": _uid("home_power_w"),
            "state_topic": mqtt_topic("home", "power_w", "total"),
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "icon": "mdi:power-plug-outline",
            "device": dev,
        }),
        ("sensor", _oid("battery_power_w"), {
            "name": "Baterie",
            "unique_id": _uid("battery_power_w"),
            "state_topic": mqtt_topic("battery", "power_w"),
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "icon": "mdi:battery-high",
            "device": dev,
        }),
        ("sensor", _oid("grid_surplus_w"), {
            "name": "Síť",
            "unique_id": _uid("grid_surplus_w"),
            "state_topic": mqtt_topic("grid", "surplus_total_w"),
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "icon": "mdi:transmission-tower",
            "device": dev,
        }),
        ("sensor", _oid("pv_power_w"), {
            "name": "FVE",
            "unique_id": _uid("pv_power_w"),
            "state_topic": mqtt_topic("pv", "power_w"),
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "icon": "mdi:solar-power",
            "device": dev,
        }),
        ("sensor", _oid("battery_soc"), {
            "name": "Stav baterie",
            "unique_id": _uid("battery_soc"),
            "state_topic": mqtt_topic("battery", "soc"),
            "unit_of_measurement": "%",
            "device_class": "battery",
            "state_class": "measurement",
            "icon": "mdi:battery-high",
            "device": dev,
        }),

        # -------- Health --------
        ("sensor", _oid("bridge_last_event_age_s"), {
            "name": "Infigy doba od poslední události",
            "unique_id": _uid("bridge_last_event_age_s"),
            "state_topic": mqtt_topic("bridge", "last_event_age_s"),
            "unit_of_measurement": "s",
            "device_class": "duration",
            "state_class": "measurement",
            "device": dev,
        }),
        ("binary_sensor", _oid("bridge_online"), {
            "name": "Infigy bridge online",
            "unique_id": _uid("bridge_online"),
            "state_topic": mqtt_topic("bridge", "online"),
            "payload_on": "1",
            "payload_off": "0",
            "device_class": "connectivity",
            "device": dev,
        }),
        ("binary_sensor", _oid("ws_flow_ok"), {
            "name": "Infigy poskytuje data",
            "unique_id": _uid("ws_flow_ok"),
            "state_topic": mqtt_topic("bridge", "ws_flow_ok"),
            "payload_on": "1",
            "payload_off": "0",
            "device_class": "connectivity",
            "availability_topic": mqtt_topic("bridge", "online"),
            "payload_available": "1",
            "payload_not_available": "0",
            "device": dev,
        }),

        # -------- Integrované energie (kWh) --------
        ("sensor", _oid("energy_home_kwh"), {
            "name": "Home Energy",
            "unique_id": _uid("energy_home_kwh"),
            "state_topic": mqtt_topic("energy", "home_kwh"),
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:power-plug-outline",
            "device": dev,
        }),
        ("sensor", _oid("energy_pv_kwh"), {
            "name": "PV Energy",
            "unique_id": _uid("energy_pv_kwh"),
            "state_topic": mqtt_topic("energy", "pv_kwh"),
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:solar-power",
            "device": dev,
        }),
        ("sensor", _oid("energy_grid_import_kwh"), {
            "name": "Grid Import Energy",
            "unique_id": _uid("energy_grid_import_kwh"),
            "state_topic": mqtt_topic("energy", "grid_import_kwh"),
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:transmission-tower-export",
            "device": dev,
        }),
        ("sensor", _oid("energy_grid_export_kwh"), {
            "name": "Grid Export Energy",
            "unique_id": _uid("energy_grid_export_kwh"),
            "state_topic": mqtt_topic("energy", "grid_export_kwh"),
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:transmission-tower-import",
            "device": dev,
        }),
        ("sensor", _oid("energy_bat_charge_kwh"), {
            "name": "Battery Charge Energy",
            "unique_id": _uid("energy_bat_charge_kwh"),
            "state_topic": mqtt_topic("energy", "bat_charge_kwh"),
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:battery-high",
            "device": dev,
        }),
        ("sensor", _oid("energy_bat_discharge_kwh"), {
            "name": "Battery Discharge Energy",
            "unique_id": _uid("energy_bat_discharge_kwh"),
            "state_topic": mqtt_topic("energy", "bat_discharge_kwh"),
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:battery-high",
            "device": dev,
        }),
        ("sensor", _oid("energy_boiler_kwh"), {
            "name": "Boiler Energy",
            "unique_id": _uid("energy_boiler_kwh"),
            "state_topic": mqtt_topic("energy", "boiler_kwh"),
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:water-boiler",
            "device": dev,
        }),
    ]

    for domain, discovery_object_id, payload in entities:
        #    default_entity_id = domain + object_id
        ent_slug = str(discovery_object_id).strip().lower()
        payload["default_entity_id"] = f"{domain}.{ent_slug}"

        topic = _disc_topic(domain, discovery_object_id)
        client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=True)

    logger.info("HA discovery published (%s entities)", len(entities))

# ---------------------- MQTT callbacks --------------------- #
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
    logger.info("MQTT connected rc=%s", code)
    if code == 0:
        publish("bridge/online", "1", retain=True, qos=1)
        try:
            publish_discovery() # auto discovery po připojení
        except Exception as e:
            logger.exception("publish_discovery failed")
        connected.set()

def on_disconnect(client, userdata, *args, **kwargs):
    # v2: (reason_code, properties) / v1: (rc)
    raw = kwargs.get("reason_code", kwargs.get("rc", -1))
    if len(args) > 0:
        raw = args[0]
    code = _normalize_code(raw)
    if code == 0:
        logger.info("MQTT disconnected rc=%s", code)
    else:
        logger.warning("MQTT disconnected rc=%s", code)
    connected.clear()

client.on_connect = on_connect
client.on_disconnect = on_disconnect

# ------------------------ Socket.IO ------------------------ #
sio = socketio.Client(
    reconnection=True, 
    reconnection_attempts=0, 
    logger=False, 
    engineio_logger=False
)

@sio.event
def connect():
    logger.info("Socket.IO connected")

@sio.event
def disconnect():
    logger.warning("Socket.IO disconnected")

@sio.event
def connect_error(msg):
    logger.warning("Socket.IO connect_error: %s", msg)

@sio.on("store:change")
def on_store_change(data):
    touch()
    try:
        payload = data.get("payload", {})
        # Teplota bojleru (°C)
        if "HW_TEMP" in payload:
            publish("boiler/temperature", round(float(payload["HW_TEMP"]), 2), qos=0)
        # Celková spotřeba bojleru kWh
        if "HW_ENERGY_PRODUCED_TOTAL" in payload:
            publish("energy/boiler_kwh", round(float(payload["HW_ENERGY_PRODUCED_TOTAL"]), 6), retain=True, qos=1)
        # Celková výroba FVE kWh
        if "PV_ENERGY_PRODUCED_TOTAL" in payload:
            publish("energy/pv_kwh", round(float(payload["PV_ENERGY_PRODUCED_TOTAL"]), 6), retain=True, qos=1)
        # Celkový odběr ze sítě kWh
        if "EM_ENERGY_CONSUMED_TOTAL" in payload:
            publish("energy/grid_import_kwh", round(float(payload["EM_ENERGY_CONSUMED_TOTAL"]), 6), retain=True, qos=1)
        # Celkové vybití baterie kWh (Infigy to vrací obráceně)
        if "PV_BATTERY_CHARGE_TOTAL" in payload:
            publish("energy/bat_discharge_kwh", round(float(payload["PV_BATTERY_CHARGE_TOTAL"]), 6), retain=True, qos=1)
        # Celkové nabití baterie kWh (Infigy to vrací obráceně)
        if "PV_BATTERY_DISCHARGE_TOTAL" in payload:
            publish("energy/bat_charge_kwh", round(float(payload["PV_BATTERY_DISCHARGE_TOTAL"]), 6), retain=True, qos=1)
        # Celkové spotřeba domu kWh
        if "HOME_CONSUMPTION_TOTAL" in payload:
            publish("energy/home_kwh", round(float(payload["HOME_CONSUMPTION_TOTAL"]), 6), retain=True, qos=1)
        # Celkový přetok do sítě kWh
        if "EM_ENERGY_OVERFLOW_TOTAL" in payload:
            publish("energy/grid_export_kwh", round(float(payload["EM_ENERGY_OVERFLOW_TOTAL"]), 6), retain=True, qos=1)

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

        # Metriky
        if "PV_ACTUAL_POWER" in payload:           # kW
            pv_w = round(kw_to_w(payload["PV_ACTUAL_POWER"]), 1)
            publish("pv/power_w", pv_w, qos=0)
        if "PV_ACTUAL_POWER_BATTERY" in payload:   # kW (kladné = charge, záporné = discharge)
            bat_w = round(kw_to_w(payload["PV_ACTUAL_POWER_BATTERY"]), 1)
            publish("battery/power_w", bat_w, qos=0)
        if "SURPLUS_INFO_TOTAL" in payload:        # kW (+ export, - import) >>> přepočet na W
            s_kw = round(float(payload["SURPLUS_INFO_TOTAL"]), 4)
            publish("grid/surplus_total_w", int(round(kw_to_w(s_kw))), qos=0)

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

    #   diagnostické poslání vstupních dat osekaný na délku 800 znaků
    #   logger.debug("INFIGY payload: %s", payload)
    #   log_total_keys(payload)
    #   publish("debug/last_payload", json.dumps(payload)[:800])  # omezíme délku
    #   diagnostické poslání "1" bez prefixu MQTT_BASE_TOPIC pro lepší ladění
    #   mqttc.publish("zzz_stream", "1", qos=0, retain=False)  # každá zpráva = tichý impulz

    except Exception as e:
        logger.exception("infigy_ws_to_mqtt parse error (store:change)")

# --- background watchdogs + heartbeat ---
def watchdog_ws(stop_evt: threading.Event):
    while not stop_evt.is_set():
        try:
            age = get_last_event_age_s()
            if age > HEARTBEAT_MAX_AGE_S:
                logger.warning("WATCHDOG: no store:change for %ss -> reconnect Socket.IO", age)
                try:
                    sio.disconnect()
                except Exception:
                    pass
            stop_evt.wait(MQTT_WATCHDOG_INTERVAL_S)
        except Exception:
            logger.exception("WATCHDOG error")
            stop_evt.wait(MQTT_WATCHDOG_INTERVAL_S)

def publish_heartbeat(stop_evt: threading.Event):
    """
    Publikuje heartbeat metriky pro HA:
      - bridge/last_event_age_s
      - bridge/ws_flow_ok
    Respektuje stop_evt pro čisté ukončení vlákna.
    """
    while not stop_evt.is_set():
        try:
            age = get_last_event_age_s()

            if age is None:
                # ještě nepřišla žádná data
                publish("bridge/last_event_age_s", -1, retain=True, qos=0)
                publish("bridge/ws_flow_ok", "0", retain=True, qos=0)
            else:
                publish("bridge/last_event_age_s", int(age), retain=True, qos=0)
                ws_ok = "1" if age < HEARTBEAT_MAX_AGE_S else "0"
                publish("bridge/ws_flow_ok", ws_ok, retain=True, qos=0)

        except Exception:
            logger.debug("Heartbeat publish failed", exc_info=True)
        
        stop_evt.wait(30)

#  Watchdog vlákno MQTT (automatický reconnect + obnova loopu) #
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
            if not client.is_connected() and not stop_evt.is_set():
                logger.debug("[MQTT-WD] Not connected -> reconnect() (backoff=%ss)", backoff)
                try:
                    client.reconnect()
                except Exception as e:
                    logger.warning("[MQTT-WD] reconnect() failed: %s", e)

            # 2) Pokud neběží vnitřní loop thread, znovu ho nastartuj
            t = getattr(client, "_thread", None)
            if (t is None or not getattr(t, "is_alive", lambda: False)()) and not stop_evt.is_set():
                try:
                    client.loop_start()
                    logger.debug("[MQTT-WD] loop_start() ensured")
                except Exception as e:
                    logger.warning("[MQTT-WD] loop_start() failed: %s", e)

            # 3) Wait/backoff
            if not client.is_connected():
                if stop_evt.wait(min(backoff, MQTT_RECONNECT_BACKOFF_MAX_S)):
                    break
                backoff = min(backoff * 2, MQTT_RECONNECT_BACKOFF_MAX_S)
            else:
                backoff = 2  # reset až když jsme fakt connected
                if stop_evt.wait(float(MQTT_WATCHDOG_INTERVAL_S)):
                    break

        except Exception:
            logger.exception("[MQTT-WD] Unexpected error")
            if stop_evt.wait(float(MQTT_WATCHDOG_INTERVAL_S)):
                break

# --- connect options for Socket.IO ---
EXTRA_HEADERS = {}
if AUTH_COOKIE:
    EXTRA_HEADERS["Cookie"] = AUTH_COOKIE
if AUTH_BEARER:
    EXTRA_HEADERS["Authorization"] = f"Bearer {AUTH_BEARER}"

logger.info("CFG INFIGY_HOST=%s SOCKET_PATH=%s", INFIGY_HOST, SOCKET_PATH)
logger.info("CFG MQTT_HOST=%s:%s USER=%s", MQTT_HOST, MQTT_PORT, "set" if MQTT_USER else "none")
logger.info("CFG MQTT_BASE_TOPIC=%s DEVICE_ID=%s", MQTT_BASE_TOPIC, DEVICE_ID)


def main():

    def _handle_stop(signum=None, frame=None):
        logger.info("Stopping (signal=%s)", signum)
        stop_evt.set()
        # pokus ukončit Socket.IO hned (ať sio.wait() neběží věčně)
        try:
            sio.disconnect()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    logger.info("Connecting to MQTT broker %s:%s ...", MQTT_HOST, MQTT_PORT)

    # MQTT connect
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
    except Exception:
        logger.exception("MQTT initial connect failed")
        # watchdog to pak zkusí zvednout, ale aspoň pokračuj a neskonči hned

    # Počkej max 5 s na MQTT
    if not connected.wait(5):
        logger.warning("MQTT not connected yet; will publish after connect() callback.")

    # --- background threads ---
    threading.Thread(
        target=_mqtt_watchdog_loop,
        args=(stop_evt,),
        name="mqtt-watchdog",
        daemon=True,
    ).start()

    threading.Thread(
        target=watchdog_ws,
        args=(stop_evt,),
        name="ws-watchdog",
        daemon=True,
    ).start()

    threading.Thread(
        target=publish_heartbeat,
        args=(stop_evt,),
        name="heartbeat",
        daemon=True,
    ).start()

    # --- Socket.IO connect loop ---
    try:
        while not stop_evt.is_set():
            try:
                sio.connect(
                    INFIGY_HOST,
                    socketio_path=SOCKET_PATH,
                    headers=EXTRA_HEADERS,
                    transports=["websocket"],
                    wait_timeout=10,
                )
                sio.wait()  # blokuje do disconnectu
            except Exception:
                if stop_evt.is_set():
                    break
                logger.exception("Socket.IO connect loop error")
                stop_evt.wait(5)
    finally:
        logger.info("Shutting down...")
        stop_evt.set()

        try:
            sio.disconnect()
        except Exception:
            pass

        try:
            client.loop_stop()
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass

if __name__ == "__main__":
    main()
