"""
Modbus IO → MQTT broker

Služba čte digitální vstupy z malých 8DI/8DO Modbus RTU IO modulů (RS-485)
a publikuje jejich stav nebo události do MQTT pro Home Assistant.

Funkce:
- Modbus RTU master (RS-485)
- Čtení DI modulů (round-robin polling)
- Debounce logika (switch / button)
- MQTT publish (state / event)
- MQTT HA Discovery (dynamické entity dle konfigurace)
- Heartbeat a stav brokeru (bridge online, data flow)
- Graceful shutdown + LWT

MQTT:
- base topic: <MQTT_BASE_TOPIC>
- discovery: <DISCOVERY_PREFIX>/<domain>/<DEVICE_ID>/<object_id>/config
- výsledné entity_id je určeno položkou `default_entity_id` v discovery payloadu

Konfigurace:
- .env (Modbus, MQTT, mapování vstupů, discovery enable)

Určeno pro běh jako systemd service na Raspberry Pi.
"""
import time
import logging
import os
import sys
import json
import socket
import threading
import signal
from typing import Set, Tuple, Dict, Any, Optional
from dotenv import load_dotenv
from envfile import env_str, env_int, env_float, env_bool

try:
    # pymodbus 3.x
    from pymodbus.client import ModbusSerialClient
except ImportError:
    # pymodbus 2.x (Debian/RPi OS často)
    from pymodbus.client.sync import ModbusSerialClient

import paho.mqtt.client as mqtt

# ---------------------- KONFIGURACE ---------------------- #

# ------------------------ .env --------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

# ------------------- Konfig z .env ----------------------- #
MODBUS_IO_ENABLED = env_bool("MODBUS_IO_ENABLED", True)
# MQTT broker
MQTT_HOST = env_str("MODBUS_IO_MQTT_HOST", "192.168.1.20") # IP, kde běží MQTT (core-mosquitto/HA)
MQTT_PORT = env_int("MODBUS_IO_MQTT_PORT", 1883)
MQTT_USERNAME = env_str("MODBUS_IO_MQTT_USERNAME", "")
MQTT_PASSWORD = env_str("MODBUS_IO_MQTT_PASSWORD", "")
MQTT_CLIENT_ID = env_str("MODBUS_IO_MQTT_CLIENT_ID", "modbus-io-broker-rpi3")
MQTT_BASE_TOPIC = env_str("MODBUS_IO_MQTT_BASE_TOPIC", "modbus_io")
DEVICE_ID   = env_str("MODBUS_IO_MQTT_DEVICE_ID","rpi-3b-broker")
DEVICE_NAME = env_str("MODBUS_IO_MQTT_DEVICE_NAME","Raspberry 3B broker")
ENTITY_PREFIX = env_str("MODBUS_IO_MQTT_ENTITY_PREFIX", "rpi_broker_modbus_io")
HEARTBEAT_S = env_int("MODBUS_IO_HEARTBEAT_S", 20)
MAX_AGE_OK_S = env_int("MODBUS_IO_MAX_AGE_OK_S", 60)
# Výsledný topic pak bude např.: modbus_io/obyvak_sw1/in1
MQTT_TOPIC_STATE = f"{MQTT_BASE_TOPIC}/state"
MQTT_TOPIC_EVENT = f"{MQTT_BASE_TOPIC}/event"
MQTT_TOPIC_BRIDGE_ONLINE = f"{MQTT_BASE_TOPIC}/bridge/online"
MQTT_TOPIC_BRIDGE_LAST_AGE = f"{MQTT_BASE_TOPIC}/bridge/last_event_age_s"
MQTT_TOPIC_BRIDGE_FLOW_OK = f"{MQTT_BASE_TOPIC}/bridge/ws_flow_ok"
# Modbus RTU parametry – RS485 převodník na RPi3
# MODBUS_PORT = "/dev/ttyUSB0" <<< nahrazeno přesnou cestou 
# (výstup po zadání: ls -l /dev/serial/by-id/)
#  >>> nehrozí změna ttyUSBx při přidání jiného zařízení USB: 
# /dev/serial/by-id/usb-1a86_USB2.0-Ser_-if00-port0
MODBUS_PORT = env_str("MODBUS_IO_MODBUS_PORT", "/dev/ttyUSB0")
MODBUS_BAUDRATE = env_int("MODBUS_IO_MODBUS_BAUDRATE", 9600)
MODBUS_PARITY = env_str("MODBUS_IO_MODBUS_PARITY", "N")
MODBUS_STOPBITS = env_int("MODBUS_IO_MODBUS_STOPBITS", 1)
MODBUS_BYTESIZE = env_int("MODBUS_IO_MODBUS_BYTESIZE", 8)
MODBUS_TIMEOUT = env_float("MODBUS_IO_MODBUS_TIMEOUT", 0.5) # v sekundách timeout na odpověď slave

# ------------------- INPUT TIMING -------------------------
# Round-robin interval mezi dotazy na sběrnici běžně 30 ms
POLL_INTERVAL_S = env_float("MODBUS_IO_POLL_INTERVAL_S", 0.03) 
DEBOUNCE_SWITCH_MS = env_int("MODBUS_IO_DEBOUNCE_SWITCH_MS", 60)
DEBOUNCE_BUTTON_MS = env_int("MODBUS_IO_DEBOUNCE_BUTTON_MS", 15)

DEFAULT_ACTIVE_HIGH = True  # True = stisk/sepnuto je logická 1
CHANNELS_PER_SLAVE = env_int("MODBUS_IO_CHANNELS_PER_SLAVE", 6)

HA_DISCOVERY = env_bool("MODBUS_IO_HA_DISCOVERY", True)
DISCOVERY_PREFIX = env_str("MODBUS_IO_HA_DISCOVERY_PREFIX", "homeassistant").strip()

# ---------------------- Logging ---------------------------
# Log minimization
LOG_LEVEL = getattr(logging, env_str("MODBUS_IO_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger("modbus_io_broker")
logger.setLevel(LOG_LEVEL)
# Utišení příliš ukecaného werkzeug
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("pymodbus").setLevel(logging.WARNING)

# ---------------------- LOG runtime ---------------------- #
logger.debug("__file__ running from: %s", __file__)
logger.debug("PYTHON: %s", sys.executable)
logger.debug("PAHO_VERSION: %s", getattr(mqtt, "__version__", "unknown"))
logger.debug("HAS_V2: %s", hasattr(mqtt, "CallbackAPIVersion"))

# ------- Singleton lock: zabran spusteni 2. instance ----- #
_singleton = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
try:
    _singleton.bind("\0modbus_io_broker.singleton")
except OSError:
    logger.warning("Another modbus_io_broker instance is running. Exiting.")
    sys.exit(1)
# --------------------- Runtime / stop -------------------- #
stop_evt = threading.Event()
mqtt_lock = threading.Lock()

def _handle_stop(signum=None, frame=None):
    logger.info("Stopping (signal=%s)", signum)
    stop_evt.set()

signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)
# event timestamp
last_event_lock = threading.Lock()
last_event_ts = time.monotonic()

def touch_event() -> None:
    global last_event_ts
    with last_event_lock:
        last_event_ts = time.monotonic()

def get_last_event_age_s() -> int:
    with last_event_lock:
        ts = float(last_event_ts)
    age = time.monotonic() - ts
    if age < 0:
        age = 0
    return int(age)

# --------------- Generování INPUTS z .env ---------------- #
# Definice IO modulů na sběrnici
def parse_slave_list(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]

def parse_pairs_csv(s: str) -> Set[Tuple[int, int]]:
    res: Set[Tuple[int, int]] = set()
    if not s:
        return res
    # odstranění případných komentářů / nových řádků
    s = s.split("#", 1)[0].strip()

    for item in s.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            logger.warning("Invalid MODBUS_IO_BUTTONS item '%s' (expected slave:channel)", item)
            continue
        a, b = item.split(":", 1)
        try:
            res.add((int(a.strip()), int(b.strip())))
        except ValueError:
            logger.warning(f"Invalid MODBUS_IO_BUTTONS item '{item}' (not integers)")
    return res

def build_inputs_from_env() -> Dict[int, Dict[int, Dict[str, Any]]]:
    slaves = parse_slave_list(env_str("MODBUS_IO_SLAVES", ""))
    channels = max(1, env_int("MODBUS_IO_CHANNELS_PER_SLAVE", 6))
    default_type = env_str("MODBUS_IO_DEFAULT_TYPE", "switch").lower()
    if default_type not in ("switch", "button"):
        default_type = "switch"

    buttons = parse_pairs_csv(env_str("MODBUS_IO_BUTTONS", ""))

    used = parse_used_channels(env_str("MODBUS_IO_USED_CHANNELS", ""))
    use_filter = len(used) > 0  # když není vyplněno, chovej se jako dřív (všechny)

    inputs: Dict[int, Dict[int, Dict[str, Any]]] = {}

    for slave in slaves:
        unit_map: Dict[int, Dict[str, Any]] = {}

        for ch in range(channels):
            if use_filter and (slave, ch) not in used:
                continue  # <- klíčové: ignorujeme nepoužívané kanály

            name = f"{ENTITY_PREFIX}_{slave}_{ch}"
            typ = default_type
            # původní logika: MODBUS_IO_BUTTONS přepíná typ oproti default
            if (slave, ch) in buttons:
                typ = "button" if default_type == "switch" else "switch"

            unit_map[ch] = {"name": name, "type": typ}

        if unit_map:
            inputs[slave] = unit_map

    return inputs

def parse_used_channels(s: str) -> Set[Tuple[int, int]]:
    """
    MODBUS_IO_USED_CHANNELS="128:0,128:1,129:0,130:0"
    Vrátí množinu (slave, ch).
    """
    res: Set[Tuple[int, int]] = set()
    if not s:
        return res
    s = s.split("#", 1)[0].strip()
    for item in s.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            logger.warning("Invalid MODBUS_IO_USED_CHANNELS item '%s' (expected slave:channel)", item)
            continue
        a, b = item.split(":", 1)
        try:
            res.add((int(a.strip()), int(b.strip())))
        except ValueError:
            logger.warning("Invalid MODBUS_IO_USED_CHANNELS item '%s' (not integers)", item)
    return res

# --------------- Nastavení MODBUS vstupů ------------------
INPUTS = build_inputs_from_env()
if not INPUTS:
    logger.warning("INPUTS is empty. Check MODBUS_IO_SLAVES in .env")
if env_int("MODBUS_IO_CHANNELS_PER_SLAVE", 6) <= 0:
    logger.warning("MODBUS_IO_CHANNELS_PER_SLAVE <= 0, forcing 6")

# z INPUTS sestaví seznam unitů pro round-robin
UNITS = sorted(INPUTS.keys())

# Helper na rozpad name modbus_io_128_0 -> (128,0)
def parse_name_to_slave_ch(name: str) -> Optional[Tuple[int, int]]:
    # očekává prefix_slave_channel
    try:
        parts = name.split("_")
        slave = int(parts[-2])
        ch = int(parts[-1])
        return slave, ch
    except Exception:
        return None
# -------------------- Helpery pro čas  --------------------
def now_ms() -> int:
    return int(time.monotonic() * 1000)
# ----------------------------------------------------------
class DebouncedInput:
    """
    Jednoduchý debounce: stabilní změna až po DEBOUNCE_MS.
    """
    def __init__(self, debounce_ms: int, initial: bool = False):
        self.debounce_ms = debounce_ms
        self.raw = initial
        self.stable = initial
        self.changed_at = now_ms()

    def update(self, raw_value: bool, t_ms: int) -> Optional[bool]:
        """
        Vrátí novou stabilní hodnotu, pokud nastala stabilní změna.
        Jinak None.
        """
        if raw_value != self.raw:
            self.raw = raw_value
            self.changed_at = t_ms

        if self.stable != self.raw:
            if (t_ms - self.changed_at) >= self.debounce_ms:
                self.stable = self.raw
                return self.stable

        return None

def _mqtt_publish(client: mqtt.Client, topic: str, payload: str, qos: int = 1, retain: bool = True) -> None:
    with mqtt_lock:
        client.publish(topic, payload, qos=qos, retain=retain)

def _mqtt_teardown(client: mqtt.Client) -> None:
    try:
        # čistý stop: explicitně nastavíme bridge offline (LWT řeší pády)
        _mqtt_publish(client, MQTT_TOPIC_BRIDGE_ONLINE, "0", qos=1, retain=True)
    except Exception:
        pass
    try:
        with mqtt_lock:
            client.loop_stop()
    except Exception:
        pass
    try:
        with mqtt_lock:
            client.disconnect()
    except Exception:
        pass

# ----------------- MQTT Discovery -------------------- #
def _oid(suffix: str) -> str:
    suf = (suffix or "").strip().lower().replace(" ", "_")
    return f"{ENTITY_PREFIX}_{suf}".strip("_")

def _disc_topic(domain: str, object_id: str) -> str:
    # sjednocený discovery topic tvar
    return f"{DISCOVERY_PREFIX}/{domain}/{DEVICE_ID}/{object_id}/config"

def _disc_device() -> Dict[str, Any]:
    return {
        "identifiers": [DEVICE_ID],
        "name": DEVICE_NAME,
        "manufacturer": "PavlosDr",
        "model": "modbus_io_broker.py",
    }

# ---------------------- Publish ---------------------------
def publish_discovery(mqtt_client: mqtt.Client) -> None:
    if not HA_DISCOVERY:
        return

    dev = _disc_device()

    # availability sjednocena s infigy
    avail = {
        "availability_topic": MQTT_TOPIC_BRIDGE_ONLINE,
        "payload_available": "1",
        "payload_not_available": "0",
    }

    entities = []

    for unit, chans in INPUTS.items():
        for ch, cfg in chans.items():
            if not cfg.get("enabled", True):
                continue

            typ = cfg["type"]

            # suffix sesjkládaný z adresy a kanálu
            oid_suffix = f"{unit}_{ch}"   # 128_0

            if typ == "switch":
                state_topic = f"{MQTT_TOPIC_STATE}/{unit}/{ch}"
                entities.append(
                    ("binary_sensor", _oid(oid_suffix), {
                        "name": f"Vypínač {unit}:{ch}",
                        "unique_id": _oid(oid_suffix),
                        "state_topic": state_topic,
                        "payload_on": "ON",
                        "payload_off": "OFF",
                        **avail,
                        "device": dev,
                    })
                )

            elif typ == "button":
                event_topic = f"{MQTT_TOPIC_EVENT}/{unit}/{ch}"
                entities.append(
                    ("sensor", _oid(f"{oid_suffix}_action"), {
                        "name": f"Tlačítko {unit}:{ch}",
                        "unique_id": _oid(oid_suffix),
                        "state_topic": event_topic,
                        "icon": "mdi:gesture-tap",
                        "force_update": True,
                        **avail,
                        "device": dev,
                    })
                )

    # Bridge health
    entities.append(
        ("sensor", _oid("bridge_last_event_age_s"), {
            "name": f"{DEVICE_NAME} doba od poslední události",
            "unique_id": _oid("bridge_last_event_age_s"),
            "state_topic": MQTT_TOPIC_BRIDGE_LAST_AGE,
            "unit_of_measurement": "s",
            "device_class": "duration",
            "state_class": "measurement",
            **avail,
            "device": dev,
        })
    )

    entities.append(
        ("binary_sensor", _oid("bridge_online"), {
            "name": f"{DEVICE_NAME} bridge online",
            "unique_id": _oid("bridge_online"),
            "state_topic": MQTT_TOPIC_BRIDGE_ONLINE,
            "payload_on": "1",
            "payload_off": "0",
            "device_class": "connectivity",
            "device": dev,
        })
    )

    entities.append(
        ("binary_sensor", _oid("ws_flow_ok"), {
            "name": f"{DEVICE_NAME} poskytuje data",
            "unique_id": _oid("ws_flow_ok"),
            "state_topic": MQTT_TOPIC_BRIDGE_FLOW_OK,
            "payload_on": "1",
            "payload_off": "0",
            "device_class": "connectivity",
            **avail,
            "device": dev,
        })
    )

    for domain, discovery_object_id, payload in entities:
        #  default_entity_id = domain + object_id
        ent_slug = str(discovery_object_id).strip().lower()
        payload["default_entity_id"] = f"{domain}.{ent_slug}"

        topic = _disc_topic(domain, discovery_object_id)
        mqtt_client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=True)

    logger.info("HA discovery published (%s entities)", len(entities))

def _rc_int(rc: Any) -> int:
    """
    Paho MQTT v2 předává reason_code jako ReasonCode objekt.
    V1 předává int.
    """
    try:
        return int(rc)
    except Exception:
        try:
            return int(getattr(rc, "value", 0))
        except Exception:
            return 0

def create_mqtt_client() -> mqtt.Client:
    if mqtt is None:
        raise RuntimeError("paho-mqtt not installed")

    client = mqtt.Client(
        client_id=MQTT_CLIENT_ID,
        clean_session=True,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )

    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    # LWT: jediný zdroj dostupnosti (infigy styl)
    client.will_set(MQTT_TOPIC_BRIDGE_ONLINE, "0", qos=1, retain=True)

    client.reconnect_delay_set(min_delay=1, max_delay=30)

    # callbacks (API v2)
    def on_connect_v2(c, userdata, flags, reason_code, properties):
        rc = _rc_int(reason_code)
        if rc == 0:
            logger.info("MQTT: connected rc=%s", rc)
            _mqtt_publish(c, MQTT_TOPIC_BRIDGE_ONLINE, "1", qos=1, retain=True)
            touch_event()
            try:
                publish_discovery(c)
            except Exception:
                logger.exception("HA discovery publish failed")
        else:
            logger.error("MQTT: connect failed rc=%s", rc)

    # !!! v2 podpis má 5 args: (client, userdata, disconnect_flags, reason_code, properties)
    def on_disconnect_v2(c, userdata, disconnect_flags, reason_code, properties):
        rc = _rc_int(reason_code)
        if rc == 0:
            logger.info("MQTT: disconnected rc=%s", rc)
        else:
            logger.warning(
                "MQTT: disconnected rc=%s flags=%s",
                rc,
                getattr(disconnect_flags, "value", disconnect_flags),
            )

    client.on_connect = on_connect_v2
    client.on_disconnect = on_disconnect_v2

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


def create_modbus_client() -> ModbusSerialClient:
    return ModbusSerialClient(
        method="rtu",
        port=MODBUS_PORT,
        baudrate=MODBUS_BAUDRATE,
        parity=MODBUS_PARITY,
        stopbits=MODBUS_STOPBITS,
        bytesize=MODBUS_BYTESIZE,
        timeout=MODBUS_TIMEOUT,
    )

def mqtt_publish_state(mqtt_client: mqtt.Client, name: str, value: bool) -> None:
    parsed = parse_name_to_slave_ch(name)
    if not parsed:
        return
    slave, ch = parsed
    topic = f"{MQTT_TOPIC_STATE}/{slave}/{ch}"
    payload = "ON" if value else "OFF"
    _mqtt_publish(mqtt_client, topic, payload, qos=1, retain=True)


def mqtt_publish_event(mqtt_client: mqtt.Client, name: str, event: str) -> None:
    parsed = parse_name_to_slave_ch(name)
    if not parsed:
        return
    slave, ch = parsed
    topic = f"{MQTT_TOPIC_EVENT}/{slave}/{ch}"
    _mqtt_publish(mqtt_client, topic, event, qos=0, retain=False)

def worker_heartbeat(mqtt_client: mqtt.Client) -> None:
    while not stop_evt.is_set():
        try:
            age = get_last_event_age_s()
            flow_ok = "1" if age <= int(MAX_AGE_OK_S) else "0"
            _mqtt_publish(mqtt_client, MQTT_TOPIC_BRIDGE_LAST_AGE, str(age), qos=0, retain=True)
            _mqtt_publish(mqtt_client, MQTT_TOPIC_BRIDGE_FLOW_OK, flow_ok, qos=0, retain=True)
        except Exception:
            logger.debug("Heartbeat publish failed", exc_info=True)

        stop_evt.wait(float(max(1, HEARTBEAT_S)))

def main() -> int:
    logger.info("Starting Modbus IO Broker")

    if not MODBUS_IO_ENABLED:
        logger.warning("MODBUS_IO_ENABLED=0 -> exiting")
        return 0

    mqtt_client = None
    modbus_client = None

    try:
        # --- MQTT ---
        mqtt_client = create_mqtt_client()

        # heartbeat thread (bridge/last_event_age_s + bridge/ws_flow_ok)
        threading.Thread(
            target=worker_heartbeat,
            args=(mqtt_client,),
            name="heartbeat",
            daemon=True,
        ).start()

        # --- MODBUS ---
        modbus_client = create_modbus_client()

        # pokud MODBUS port nejde otevřít, zkus opakovaně (přerušitelné stop_evt)
        while not stop_evt.is_set():
            try:
                if modbus_client.connect():
                    logger.info("Modbus: connected (port opened)")
                    # po otevření portu dej čas USB/driveru a vyčisti RX/TX buffery
                    time.sleep(0.3)
                    try:
                        if getattr(modbus_client, "socket", None):
                            s = modbus_client.socket
                            if hasattr(s, "reset_input_buffer"):
                                s.reset_input_buffer()
                            if hasattr(s, "reset_output_buffer"):
                                s.reset_output_buffer()
                    except Exception:
                        pass
                    break
            except Exception:
                logger.exception("Modbus: connect exception")

            logger.error("Modbus: connect failed, retry in 2s")
            stop_evt.wait(2.0)

        if stop_evt.is_set():
            return 0

        if not UNITS:
            logger.error("No MODBUS units configured (UNITS is empty). Exiting.")
            return 2

        # per input state
        debouncers: Dict[str, DebouncedInput] = {}
        stable_states: Dict[str, bool] = {}

        def ensure_channel(name: str, typ: str) -> None:
            if name not in debouncers:
                d = DEBOUNCE_BUTTON_MS if typ == "button" else DEBOUNCE_SWITCH_MS
                debouncers[name] = DebouncedInput(d, initial=False)
                stable_states[name] = False

        unit_idx = 0

        # log suppression state per unit
        unit_err_state: Dict[int, Dict[str, Any]] = {}  # {unit: {"in_error": bool, "count": int}}

        # startup publish state
        published_states: Dict[str, bool] = {}  # name -> last published
        pending_units = set(UNITS)              # unity, co ještě neměly první OK čtení
        startup_done = False

        # --- polling loop ---
        while not stop_evt.is_set():
            t = now_ms()

            unit = UNITS[unit_idx]
            unit_idx = (unit_idx + 1) % len(UNITS)

            try:
                rr = modbus_client.read_discrete_inputs(
                    address=0,
                    count=CHANNELS_PER_SLAVE,
                    unit=unit,
                )

                st = unit_err_state.get(unit)
                if st is None:
                    st = {"in_error": False, "count": 0}
                    unit_err_state[unit] = st

                if rr.isError():
                    st["count"] += 1
                    if not st["in_error"]:
                        st["in_error"] = True
                        logger.error("Modbus read error unit=%s: %s", unit, rr)
                    # ticho a jedeme dál
                    stop_evt.wait(float(POLL_INTERVAL_S))
                    continue

                # OK: pokud jsme byli v chybě, zaloguj recovered 1×
                if st["in_error"]:
                    st["in_error"] = False
                    logger.info("Modbus unit=%s recovered (errors=%s)", unit, st["count"])
                    st["count"] = 0

                # “tečou data” = máme úspěšné čtení
                touch_event()

                bits = list(getattr(rr, "bits", []))[:CHANNELS_PER_SLAVE]
                cfg_unit = INPUTS.get(unit, {})

                # startup snapshot pro tuto jednotku (jen switch)
                startup_snapshot: Dict[str, bool] = {}

                for ch in range(CHANNELS_PER_SLAVE):
                    cfg = cfg_unit.get(ch)
                    if not cfg:
                        continue
                    if not cfg.get("enabled", True):
                        continue

                    name = cfg["name"]
                    typ = cfg["type"]
                    active_high = cfg.get("active_high", DEFAULT_ACTIVE_HIGH)

                    ensure_channel(name, typ)

                    raw = bool(bits[ch]) if ch < len(bits) else False
                    logical = raw if active_high else (not raw)

                    if typ == "switch":
                        startup_snapshot[name] = logical

                    new_stable = debouncers[name].update(logical, t)
                    if new_stable is None:
                        continue

                    old = stable_states.get(name, False)
                    stable_states[name] = new_stable

                    if typ == "switch":
                        prev_pub = published_states.get(name, None)
                        if prev_pub is None or prev_pub != new_stable:
                            logger.debug("State: %s -> %s", name, "ON" if new_stable else "OFF")
                            mqtt_publish_state(mqtt_client, name, new_stable)
                            published_states[name] = new_stable

                    elif typ == "button":
                        if (not old) and new_stable:
                            mqtt_publish_event(mqtt_client, name, "press")
                        elif old and (not new_stable):
                            mqtt_publish_event(mqtt_client, name, "release")

                # Startup sync: po prvním OK čtení unity publishni initial switch states
                if not startup_done and unit in pending_units:
                    for n, stval in startup_snapshot.items():
                        if published_states.get(n, None) is None:
                            mqtt_publish_state(mqtt_client, n, stval)
                            published_states[n] = stval

                    pending_units.discard(unit)
                    if not pending_units:
                        startup_done = True
                        logger.info("Startup sync completed: published initial switch states for all units")

            except Exception:
                logger.exception("Polling exception")
                try:
                    modbus_client.close()
                except Exception:
                    pass

                # krátké čekání, přerušitelné
                stop_evt.wait(0.2)
                if stop_evt.is_set():
                    break

                try:
                    modbus_client.connect()
                    logger.info("Modbus: reconnected")
                except Exception:
                    logger.exception("Modbus reconnect failed")

            stop_evt.wait(float(POLL_INTERVAL_S))

        return 0

    except KeyboardInterrupt:
        logger.info("Stopping (Ctrl+C)")
        stop_evt.set()
        return 0

    finally:
        # čistý shutdown
        if mqtt_client is not None:
            _mqtt_teardown(mqtt_client)
        if modbus_client is not None:
            try:
                modbus_client.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
