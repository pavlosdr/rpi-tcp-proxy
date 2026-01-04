"""
Modbus IO broker pro vypínače / tlačítka
- RPi3 je Modbus RTU master na RS485 sběrnici
- Čte vstupy z malých 8DI/8DO modulů (např. R4pin08)
- Při změně stavu DI publikuje MQTT zprávu do Home Assistantu
"""

import time
import logging
import os
import sys
import json
from typing import Set, Tuple, Dict, Any, Optional
from dotenv import load_dotenv

try:
    # pymodbus 3.x
    from pymodbus.client import ModbusSerialClient
except ImportError:
    # pymodbus 2.x (Debian/RPi OS často)
    from pymodbus.client.sync import ModbusSerialClient

import paho.mqtt.client as mqtt

# ---------------------- LOG runtime ---------------------- #
print("__file__ running from:", __file__)
print("PYTHON:", sys.executable)
print("PAHO_VERSION:", getattr(mqtt, "__version__", "unknown"))
print("HAS_V2:", hasattr(mqtt, "CallbackAPIVersion"))
# ---------------------- KONFIGURACE ---------------------- #

# ------------------------ .env --------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

# ----------------- Helpery pro čtení .env  --------------- #
def env_str(key: str, default: str = "") -> str:
    v = os.getenv(key)
    return v.strip() if v is not None and str(v).strip() != "" else default

def env_int(key: str, default: int) -> int:
    v = env_str(key, "")
    return int(v) if v else default

def env_float(key: str, default: float) -> float:
    v = env_str(key, "")
    return float(v) if v else default

def env_bool(key: str, default: bool = False) -> bool:
    v = env_str(key, "")
    if not v:
        return default
    return v.lower() in ("1", "true", "yes", "on")
# ------------------- Konfig z .env ----------------------- #
MODBUS_IO_ENABLED = env_bool("MODBUS_IO_ENABLED", True)
# MQTT broker
MQTT_HOST = env_str("MODBUS_IO_MQTT_HOST", "192.168.1.20") # IP, kde běží MQTT (core-mosquitto/HA)
MQTT_PORT = env_int("MODBUS_IO_MQTT_PORT", 1883)
MQTT_USERNAME = env_str("MODBUS_IO_MQTT_USERNAME", "")
MQTT_PASSWORD = env_str("MODBUS_IO_MQTT_PASSWORD", "")
MQTT_CLIENT_ID = env_str("MODBUS_IO_MQTT_CLIENT_ID", "modbus-io-broker-rpi3")
MQTT_BASE_TOPIC = env_str("MODBUS_IO_MQTT_BASE_TOPIC", "modbus_io")
# Výsledný topic pak bude např.: modbus_io/obyvak_sw1/in1
MQTT_TOPIC_STATE = f"{MQTT_BASE_TOPIC}/state"
MQTT_TOPIC_EVENT = f"{MQTT_BASE_TOPIC}/event"
MQTT_TOPIC_STATUS = f"{MQTT_BASE_TOPIC}/status"
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
HA_DISCOVERY_PREFIX = env_str("MODBUS_IO_HA_DISCOVERY_PREFIX", "homeassistant")
# --------------- Generování INPUTS z .env -----------------
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
            logger.warning(f"Invalid MODBUS_IO_BUTTONS item '{item}' (expected slave:channel)")
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
    prefix = env_str("MODBUS_IO_NAME_PREFIX", "modbus_io")
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

            name = f"{prefix}_{slave}_{ch}"
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
            logger.warning(f"Invalid MODBUS_IO_USED_CHANNELS item '{item}' (expected slave:channel)")
            continue
        a, b = item.split(":", 1)
        try:
            res.add((int(a.strip()), int(b.strip())))
        except ValueError:
            logger.warning(f"Invalid MODBUS_IO_USED_CHANNELS item '{item}' (not integers)")
    return res

# ---------------------- Logging ---------------------------
# Log minimization
LOG_FIRST_ERROR_ONLY = True   # jen první chyba po OK + recovered
LOG_ERROR_COOLDOWN_S = 0      # 0 = nepoužívat periodické logy, opravdu ticho
LOG_LEVEL = logging.INFO
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("modbus_io_broker")

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

# ---------------------- Publish ---------------------------
def publish_ha_discovery(mqtt_client: mqtt.Client) -> None:
    if not HA_DISCOVERY:
        return

    device = {
        "identifiers": ["modbus_io_rpi"],
        "name": "Modbus IO – RPi",
        "manufacturer": "PavlosDr",
        "model": "RS485 Modbus IO",
    }

    for unit, chans in INPUTS.items():
        for ch, cfg in chans.items():
            enabled = cfg.get("enabled", True)
            if not enabled:
                continue
            name = cfg["name"]
            typ = cfg["type"]

            # společné
            avail = {
                "availability_topic": MQTT_TOPIC_STATUS,
                "payload_available": "online",
                "payload_not_available": "offline",
            }

            if typ == "switch":
                # binary_sensor (stav ON/OFF)
                state_topic = f"{MQTT_TOPIC_STATE}/{unit}/{ch}"
                discovery_topic = f"{HA_DISCOVERY_PREFIX}/binary_sensor/{name}/config"
                payload = {
                    "name": name,
                    "unique_id": name,
                    "state_topic": state_topic,
                    "payload_on": "ON",
                    "payload_off": "OFF",
                    **avail,
                    "device": device,
                }
                mqtt_client.publish(discovery_topic, json.dumps(payload), qos=1, retain=True)

            elif typ == "button":
                # tlačítko jako sensor action (press/release)
                # HA si to převedeš v automations podle hodnoty
                event_topic = f"{MQTT_TOPIC_EVENT}/{unit}/{ch}"
                discovery_topic = f"{HA_DISCOVERY_PREFIX}/sensor/{name}_action/config"
                payload = {
                    "name": f"{name}_action",
                    "unique_id": f"{name}_action",
                    "state_topic": event_topic,
                    "icon": "mdi:gesture-tap",
                    "force_update": True,          # ať HA vezme i opakované stejné hodnoty
                    **avail,
                    "device": device,
                }
                mqtt_client.publish(discovery_topic, json.dumps(payload), qos=1, retain=True)

def create_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
    # jen pokud máš username (jinak některé brokery zbytečně řeší auth)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    # LWT - broker oznámí offline pokud proces spadne
    client.will_set(MQTT_TOPIC_STATUS, "offline", qos=1, retain=True)
    # auto reconnect backoff (sekundy)
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def on_connect(c, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT: connected")
            c.publish(MQTT_TOPIC_STATUS, "online", qos=1, retain=True)

            # autodiscovery po connectu
            try:
                publish_ha_discovery(c)
            except Exception as e:
                logger.warning(f"HA discovery publish failed: {e}")
        else:
            logger.error(f"MQTT: connect failed rc={rc}")

    def on_disconnect(c, userdata, rc):
        logger.warning(f"MQTT: disconnected rc={rc}")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    # neblokující připojení; pokud broker není dostupný, paho bude zkoušet znovu
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

    # doporučuji retain=True pro "state" (HA po restartu hned ví poslední stav)
    mqtt_client.publish(topic, payload, qos=1, retain=True)


def mqtt_publish_event(mqtt_client: mqtt.Client, name: str, event: str) -> None:
    parsed = parse_name_to_slave_ch(name)
    if not parsed:
        return
    slave, ch = parsed
    topic = f"{MQTT_TOPIC_EVENT}/{slave}/{ch}"

    # eventy NEretainovat
    mqtt_client.publish(topic, event, qos=0, retain=False)


def main():
    logger.info("Starting Modbus IO Broker")

    if not MODBUS_IO_ENABLED:
        logger.warning("MODBUS_IO_ENABLED=0 -> exiting")
        return

    mqtt_client = create_mqtt_client()
    modbus_client = create_modbus_client()

    # pokud MODBUS port nejde otevřít, zkus opakovaně
    while True:
        try:
            if modbus_client.connect():
                logger.info("Modbus: connected (port opened)")
                break
        except Exception:
            pass
        logger.error("Modbus: connect failed, retry in 2s")
        time.sleep(2)

    # per input state
    debouncers: Dict[str, DebouncedInput] = {}
    stable_states: Dict[str, bool] = {}

    if not UNITS:
        logger.error("No MODBUS units configured (UNITS is empty). Exiting.")
        return

    # init helper
    def ensure_channel(name: str, typ: str):
        if name not in debouncers:
            d = DEBOUNCE_BUTTON_MS if typ == "button" else DEBOUNCE_SWITCH_MS
            debouncers[name] = DebouncedInput(d, initial=False)
            stable_states[name] = False

    unit_idx = 0

    # --- log suppression state per unit ---
    unit_err_state: Dict[int, Dict[str, Any]] = {}  # {unit: {"in_error": bool, "count": int}}

    # --- startup publish state ---
    published_states: Dict[str, bool] = {}  # name -> last published
    pending_units = set(UNITS)              # které unity jsme ještě po startu nenačetli OK
    startup_done = False

    try:
        while True:
            t = now_ms()

            # 1) read jednoho slave (round-robin)
            unit = UNITS[unit_idx]
            unit_idx = (unit_idx + 1) % len(UNITS)

            try:
                rr = modbus_client.read_discrete_inputs(
                    address=0, count=CHANNELS_PER_SLAVE, unit=unit
                )

                st = unit_err_state.get(unit)
                if st is None:
                    st = {"in_error": False, "count": 0}
                    unit_err_state[unit] = st

                if rr.isError():
                    st["count"] += 1
                    if not st["in_error"]:
                        st["in_error"] = True
                        logger.warning(f"Modbus read error unit={unit}: {rr}")
                    # další chyby už netiskneme (ticho)
                    # POZOR: tady už nespíme, sleep je na konci smyčky
                    continue

                # OK: pokud jsme byli v chybě, zaloguj recovered 1×
                if st["in_error"]:
                    st["in_error"] = False
                    logger.info(f"Modbus unit={unit} recovered (errors={st['count']})")
                    st["count"] = 0

                # >>> TADY byla chyba: tenhle blok musí být mimo if st["in_error"] <<<
                bits = list(getattr(rr, "bits", []))[:CHANNELS_PER_SLAVE]
                cfg_unit = INPUTS.get(unit, {})

                # budeme sbírat i "startup snapshot" pro tuto jednotku
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

                    # uložíme snapshot pro startup publish (jen switch)
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
                            logger.info(f"State: {name} -> {'ON' if new_stable else 'OFF'}")
                            mqtt_publish_state(mqtt_client, name, new_stable)
                            published_states[name] = new_stable

                    elif typ == "button":
                        if (not old) and new_stable:
                            mqtt_publish_event(mqtt_client, name, "press")

                        elif old and (not new_stable):
                            mqtt_publish_event(mqtt_client, name, "release")

                # ---- Startup publish: po prvním OK čtení unity ----
                if not startup_done and unit in pending_units:
                    for name, stval in startup_snapshot.items():
                        prev_pub = published_states.get(name, None)
                        if prev_pub is None:
                            mqtt_publish_state(mqtt_client, name, stval)
                            published_states[name] = stval

                    pending_units.discard(unit)

                    if not pending_units:
                        startup_done = True
                        logger.info("Startup sync completed: published initial switch states for all units")

            except Exception as e:
                logger.exception(f"Polling exception: {e}")
                try:
                    modbus_client.close()
                except Exception:
                    pass
                time.sleep(0.2)
                try:
                    modbus_client.connect()
                    logger.info("Modbus: reconnected")
                except Exception:
                    logger.exception("Modbus reconnect failed")

            time.sleep(POLL_INTERVAL_S)

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