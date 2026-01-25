"""
MQTT connectivity & system reporter

Služba publikuje systémové a síťové informace Raspberry Pi do MQTT
a integruje je do Home Assistantu pomocí MQTT Discovery.

Funkce:
- Stav zařízení (online/offline)
- CPU teplota, load, uptime
- Síťová latence (ping, TCP testy)
- Stav proxy / dalších služeb
- Heartbeat a watchdog logika

MQTT:
- base topic: <MQTT_BASE_TOPIC>
- discovery: <DISCOVERY_PREFIX>/<domain>/<DEVICE_ID>/<object_id>/config

Konfigurace:
- .env (MQTT, device ID, discovery prefix, intervaly)

Určeno pro běh jako systemd service.
"""

import os
import time
import json
import threading
import logging
import sys
import socket
import signal
import subprocess
import datetime as dt
from typing import Any, Dict, Optional, Tuple
from dotenv import load_dotenv
from envfile import env_str, env_int, env_bool
try:
    import paho.mqtt.client as mqtt
except Exception:
    mqtt = None

# ---------------------- KONFIGURACE ---------------------- #

# ------------------------ .env --------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

# ------------------- Konfig z .env ----------------------- #
# ------------------------ MQTT --------------------------- #
ENABLED = env_bool("MQTT_REPORT_ENABLED", True)
MQTT_HOST   = env_str("MQTT_REPORT_HOST","localhost")
MQTT_PORT   = env_int("MQTT_REPORT_PORT",1883)
MQTT_USERNAME   = env_str("MQTT_REPORT_USER","")
MQTT_PASSWORD   = env_str("MQTT_REPORT_PASS","")
MQTT_BASE_TOPIC   = env_str("MQTT_REPORT_BASE_TOPIC","rpi_report").strip().strip("/")
MQTT_CLIENT_ID   = env_str("MQTT_REPORT_CLIENT_ID","rpi-report")

RECONNECT_BACKOFF_MAX_S = env_int("MQTT_REPORT_RECONNECT_BACKOFF_MAX_S", 60)

DISCOVERY_PREFIX = env_str("MQTT_REPORT_DISCOVERY_PREFIX", "homeassistant").strip()
DEVICE_ID   = env_str("MQTT_REPORT_DEVICE_ID","rpi-3b-broker")
DEVICE_NAME = env_str("MQTT_REPORT_DEVICE_NAME","Raspberry 3B broker")
ENTITY_PREFIX = env_str("MQTT_REPORT_ENTITY_PREFIX", "rpi_broker")

# ------------------ Inverter a další ---------------------- #
INVERTER_HOST = env_str("MQTT_REPORT_INVERTER_HOST","10.10.100.253")
INVERTER_PORT = env_int("MQTT_REPORT_INVERTER_PORT",502)

PING_HA_HOST  = env_str("MQTT_REPORT_PING_HA_HOST","192.168.1.20")
PING_INVERTER_HOST = env_str("MQTT_REPORT_PING_INVERTER_HOST", INVERTER_HOST)

PROXY_SYSTEMD_UNIT = env_str("MQTT_REPORT_PROXY_SYSTEMD_UNIT","modbus_tcp_proxy.service")

POLL_SYS_S    = env_int("MQTT_REPORT_POLL_SYS_S",10)
POLL_NET_S    = env_int("MQTT_REPORT_POLL_NET_S",10)
POLL_PROXY_S  = env_int("MQTT_REPORT_POLL_PROXY_S",10)

HEARTBEAT_S   = env_int("MQTT_REPORT_HEARTBEAT_S",20)
MAX_AGE_OK_S  = env_int("MQTT_REPORT_MAX_AGE_OK_S",60)
# ---------------------- Logging ---------------------------
# Log minimization
LOG_LEVEL = getattr(logging, env_str("MQTT_REPORT_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger("mqtt_report")
logger.setLevel(LOG_LEVEL)

# ---------------------- LOG runtime ---------------------- #
logger.debug("__file__ running from: %s", __file__)
logger.debug("PYTHON: %s", sys.executable)
logger.debug("PAHO_VERSION: %s", getattr(mqtt, "__version__", "unknown"))
logger.debug("HAS_V2: %s", hasattr(mqtt, "CallbackAPIVersion"))

# ------- Singleton lock: zabrán spusteni 2. instance ----- #
_singleton = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
try:
    _singleton.bind("\0mqtt_report.singleton")
except OSError:
    logger.warning("Another mqtt_report instance is running. Exiting.")
    sys.exit(1)
# ------------------ Runtime / shared state ---------------- #
stop_evt = threading.Event()

mqtt_connected_evt = threading.Event()
mqtt_client_lock = threading.Lock()
mqtt_client = None  # type: ignore

# "event" = úspěšný publish cyklus
last_event_ts_lock = threading.Lock()
last_event_ts: float = 0.0

# poslední známé hodnoty (pro publish loop)
state_lock = threading.Lock()
state: Dict[str, Any] = {
    "sys": {},
    "net": {},
    "proxy": {},
}

# Backoff reconnect
reconnect_lock = threading.Lock()
reconnect_backoff_s = 1.0

# ------------------ Small helpers ------------------ #
def now_iso() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def set_last_event_now() -> None:
    global last_event_ts
    with last_event_ts_lock:
        last_event_ts = time.time()


def get_last_event_age_s() -> Optional[int]:
    with last_event_ts_lock:
        ts = float(last_event_ts or 0.0)
    if ts <= 0:
        return None
    return int(max(0.0, time.time() - ts))


def ws_flow_ok() -> int:
    """
    Infigy-norma: bridge/ws_flow_ok = 1 pokud "tečou data"
    Pro report: tečou = poslední úspěšný publish cyklus není starší než MAX_AGE_OK_S.
    """
    age = get_last_event_age_s()
    if age is None:
        return 0
    return 1 if age <= int(MAX_AGE_OK_S) else 0


def mqtt_topic(*parts: str) -> str:
    base = MQTT_BASE_TOPIC.strip("/")
    p = "/".join(x.strip("/") for x in parts if x)
    return f"{base}/{p}" if p else base

def safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(str(v).strip().replace(",", "."))
    except Exception:
        return None


def run_cmd(args: list, timeout: float = 5.0) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            text=True,
        )
        return int(p.returncode), (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 999, "", repr(e)


def ping_host(host: str, timeout_s: float = 1.0) -> Dict[str, Any]:
    """
    Jednoduchý ping přes /bin/ping. Na RPi typicky funguje bez root.
    Vrací { ok, ms }.
    """
    host = (host or "").strip()
    if not host:
        return {"ok": 0, "ms": None}

    # -c 1 one packet, -W timeout (seconds, linux ping)
    rc, out, err = run_cmd(["/bin/ping", "-c", "1", "-W", str(int(max(1, timeout_s))), host], timeout=timeout_s + 2.0)
    if rc != 0:
        return {"ok": 0, "ms": None, "err": err or out}

    # parsuj "time=XX ms"
    ms = None
    try:
        import re
        m = re.search(r"time=([0-9.]+)\s*ms", out)
        if m:
            ms = float(m.group(1))
    except Exception:
        ms = None

    return {"ok": 1, "ms": ms}


def tcp_check(host: str, port: int, timeout_s: float = 1.0) -> Dict[str, Any]:
    host = (host or "").strip()
    if not host or not port:
        return {"ok": 0, "ms": None}
    t0 = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout_s)):
            pass
        ms = (time.time() - t0) * 1000.0
        return {"ok": 1, "ms": int(ms)}
    except Exception as e:
        return {"ok": 0, "ms": None, "err": repr(e)}


def systemctl_is_active(unit: str) -> Dict[str, Any]:
    unit = (unit or "").strip()
    if not unit:
        return {"active": 0, "state": "unknown"}

    rc, out, err = run_cmd(["/bin/systemctl", "is-active", unit], timeout=3.0)
    s = (out or err or "").strip()
    if rc == 0 and s == "active":
        return {"active": 1, "state": "active"}
    return {"active": 0, "state": s or "inactive"}


def read_cpu_temp_c() -> Optional[float]:
    # typicky /sys/class/thermal/thermal_zone0/temp
    path = "/sys/class/thermal/thermal_zone0/temp"
    try:
        if os.path.exists(path):
            raw = open(path, "r", encoding="utf-8").read().strip()
            v = safe_float(raw)
            if v is None:
                return None
            # bývá v milistupních
            if v > 1000:
                v = v / 1000.0
            return float(v)
    except Exception:
        return None
    return None


def read_loadavg() -> Tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        a1, a5, a15 = os.getloadavg()
        return float(a1), float(a5), float(a15)
    except Exception:
        return None, None, None


def read_uptime_s() -> Optional[int]:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            x = (f.read() or "").split()
        if not x:
            return None
        return int(float(x[0]))
    except Exception:
        return None


# ------------------ MQTT publish helpers ------------------ #
def _mqtt_publish(topic: str, payload: str, retain: bool = True, qos: int = 0) -> None:
    global mqtt_client
    with mqtt_client_lock:
        c = mqtt_client
        if c is None:
            return
        c.publish(topic, payload=payload, qos=int(qos), retain=bool(retain))

def publish_num(topic: str, value: Any, retain: bool = True) -> None:
    if value is None:
        return
    _mqtt_publish(topic, str(value), retain=retain, qos=0)


def publish_text(topic: str, value: str, retain: bool = True) -> None:
    _mqtt_publish(topic, str(value), retain=retain, qos=0)


def publish_json(topic: str, obj: Any, retain: bool = True) -> None:
    _mqtt_publish(topic, json.dumps(obj, ensure_ascii=False), retain=retain, qos=0)

def _mqtt_teardown(reset_client: bool = False) -> None:
    global mqtt_client
    with mqtt_client_lock:
        c = mqtt_client
        if c is None:
            return
        try:
            c.loop_stop()
        except Exception:
            pass
        try:
            c.disconnect()
        except Exception:
            pass
        if reset_client:
            mqtt_client = None
# ------------------ HA Discovery ------------------------------ #
def _disc_device():
    return {
        "identifiers": [DEVICE_ID],
        "manufacturer": "PavlosDr",
        "model": "mqtt_report.py",
        "name": DEVICE_NAME,
    }

def _oid(suffix: str) -> str:
    suf = (suffix or "").strip().lower().replace(" ", "_")
    return f"{ENTITY_PREFIX}_{suf}"

def _uid(suffix: str) -> str:
    # globálně unikátní napříč HA + stabilní
    suf = (suffix or "").strip().lower().replace(" ", "_")
    return f"{ENTITY_PREFIX}_{suf}".lower()

def _disc_topic(domain: str, object_id: str) -> str:
    # sjednocený discovery topic tvar
    return f"{DISCOVERY_PREFIX}/{domain}/{DEVICE_ID}/{object_id}/config"

def publish_discovery() -> None:
    dev = _disc_device()
    # (domain, object_id, payload)
    entities = [
        # -------- SYSTEM --------
        ("sensor", _oid("cpu_temp"), {
            "name": f"{DEVICE_NAME} CPU teplota",
            "object_id": _uid("cpu_temp"),
            "unique_id": _uid("cpu_temp"),
            "state_topic": mqtt_topic("sys", "cpu_temp_c"),
            "unit_of_measurement": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("load_1m"), {
            "name": f"{DEVICE_NAME} Load 1m",
            "object_id": _uid("load_1m"),
            "unique_id": _uid("load_1m"),
            "state_topic": mqtt_topic("sys", "load_1m"),
            "state_class": "measurement",
            "device": dev,
        }),
        ("sensor", _oid("uptime"), {
            "name": f"{DEVICE_NAME} Uptime",
            "object_id": _uid("uptime_s"),
            "unique_id": _uid("uptime_s"),
            "state_topic": mqtt_topic("sys", "uptime_s"),
            "unit_of_measurement": "s",
            "device_class": "duration",
            "state_class": "measurement",
            "device": dev,
        }),

        # -------- NETWORK --------
        ("binary_sensor", _oid("ping_ha_ok"), {
            "name": f"{DEVICE_NAME} Ping HA",
            "object_id": _uid("ping_ha_ok"),
            "unique_id": _uid("ping_ha_ok"),
            "state_topic": mqtt_topic("net", "ping_ha_ok"),
            "payload_on": "1",
            "payload_off": "0",
            "device_class": "connectivity",
            "device": dev,
        }),
        ("sensor", _oid("ping_ha_ms"), {
            "name": f"{DEVICE_NAME} Ping HA (ms)",
            "object_id": _uid("ping_ha_ms"),
            "unique_id": _uid("ping_ha_ms"),
            "state_topic": mqtt_topic("net", "ping_ha_ms"),
            "unit_of_measurement": "ms",
            "device_class": "duration",
            "state_class": "measurement",
            "device": dev,
        }),
        ("binary_sensor", _oid("ping_inverter_ok"), {
            "name": f"{DEVICE_NAME} Ping Inverter",
            "object_id": _uid("ping_inverter_ok"),
            "unique_id": _uid("ping_inverter_ok"),
            "state_topic": mqtt_topic("net", "ping_inverter_ok"),
            "payload_on": "1",
            "payload_off": "0",
            "device_class": "connectivity",
            "device": dev,
        }),
        ("sensor", _oid("tcp_inverter_ms"), {
            "name": f"{DEVICE_NAME} TCP Inverter (ms)",
            "object_id": _uid("tcp_inverter_ms"),
            "unique_id": _uid("tcp_inverter_ms"),
            "state_topic": mqtt_topic("net", "tcp_inverter_ms"),
            "unit_of_measurement": "ms",
            "device_class": "duration",
            "state_class": "measurement",
            "device": dev,
        }),

        # -------- PROXY / SYSTEMD --------
        ("binary_sensor", _oid("proxy_active"), {
            "name": f"{DEVICE_NAME} Proxy active",
            "object_id": _uid("proxy_active"),
            "unique_id": _uid("proxy_active"),
            "state_topic": mqtt_topic("proxy", "active"),
            "payload_on": "1",
            "payload_off": "0",
            "device": dev,
        }),

        # -------- BRIDGE (infigy-norma) --------
        ("sensor", _oid("last_event_age"), {
            "name": f"{DEVICE_NAME} doba od poslední události",
            "object_id": _uid("bridge_last_event_age_s"),
            "unique_id": _uid("bridge_last_event_age_s"),
            "state_topic": mqtt_topic("bridge", "last_event_age_s"),
            "unit_of_measurement": "s",
            "device_class": "duration",
            "state_class": "measurement",
            "device": dev,
        }),
        ("binary_sensor", _oid("bridge_online"), {
            "name": f"{DEVICE_NAME} bridge online",
            "object_id": _uid("bridge_online"),
            "unique_id": _uid("bridge_online"),
            "state_topic": mqtt_topic("bridge", "online"),
            "payload_on": "1",
            "payload_off": "0",
            "device_class": "connectivity",
            "device": dev,
        }),
        ("binary_sensor", _oid("ws_flow_ok"), {
            "name": f"{DEVICE_NAME} poskytuje data",
            "object_id": _uid("ws_flow_ok"),
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
    ]

    for domain, discovery_object_id, payload in entities:
        # discovery topic může zůstat jako dnes (používáš _oid), jen je to "adresář" v MQTT
        topic = _disc_topic(domain, discovery_object_id)
        publish_json(topic, payload, retain=True)

    logger.info("HA discovery published (%s entities)", len(entities))

# ------------------ MQTT callbacks / connect loop ------------------ #
def _rc_int(reason_code) -> int:
    """
    Paho MQTT v2: reason_code je objekt ReasonCode.
    Spolehlivě převedeme na int (0 = Success).
    """
    try:
        # Paho >= 1.6/2.x: ReasonCode má typicky .value (int)
        v = getattr(reason_code, "value", None)
        if v is not None:
            return int(v)
    except Exception:
        pass

    # Fallback: některé buildy vrací enum-like objekt, který se dá srovnat stringem
    try:
        s = str(reason_code)
        if s.lower() in ("success", "0"):
            return 0
    except Exception:
        pass

    # Poslední možnost: vrať nenulové (neznámé) -> chovej se jako failure
    return 1

def on_connect(client, userdata, flags, reason_code, properties=None):
    rc = _rc_int(reason_code)
    if rc == 0:
        mqtt_connected_evt.set()
        logger.info("MQTT connected rc=%s", rc)

        # LWT online -> teď explicitně nastav online=1
        publish_text(mqtt_topic("bridge", "online"), "1", retain=True)

        # Discovery + první heartbeat
        try:
            publish_discovery()
        except Exception:
            logger.exception("publish_discovery failed")

        # po connectu rovnou publish aktuálních metrik (pokud máme)
        try:
            publish_metrics()
        except Exception:
            logger.exception("publish_metrics after connect failed")
    else:
        mqtt_connected_evt.clear()
        logger.warning("MQTT connect failed rc=%s reason=%s", rc, reason_code)


def on_disconnect(client, userdata, reason_code, properties=None):
    rc = _rc_int(reason_code)
    mqtt_connected_evt.clear()
    logger.warning("MQTT disconnected rc=%s reason=%s", rc, reason_code)


def create_mqtt_client() -> mqtt.Client:
    if mqtt is None:
        raise RuntimeError("paho-mqtt not installed")

    client = mqtt.Client(
        client_id=MQTT_CLIENT_ID,
        clean_session=True,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )

    # jen pokud máš username (jinak některé brokery zbytečně řeší auth)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    # LWT: když proces spadne nebo odpadne síť, HA vidí bridge offline
    client.will_set(mqtt_topic("bridge", "online"), payload="0", qos=0, retain=True)

    # callbacks (API v2)
    def on_connect_v2(client, userdata, flags, reason_code, properties=None):
        return on_connect(client, userdata, flags, reason_code, properties)

    def on_disconnect_v2(client, userdata, reason_code, properties=None):
        return on_disconnect(client, userdata, reason_code, properties)

    client.on_connect = on_connect_v2
    client.on_disconnect = on_disconnect_v2

    return client



def connect_loop():
    """
    Robustní connect loop s backoff (infigy styl).
    """
    global mqtt_client, reconnect_backoff_s

    while not stop_evt.is_set():
        try:
            # 1) klient: vždy pod lockem
            with mqtt_client_lock:
                if mqtt_client is None:
                    mqtt_client = create_mqtt_client()
                c = mqtt_client

            logger.info("Connecting to MQTT broker %s:%s ...", MQTT_HOST, MQTT_PORT)

            # 2) connect + loop_start: také pod lockem
            with mqtt_client_lock:
                # c může být None jen pokud by ti ho někdo resetnul mezi locky,
                # ale tady jsme v locku, takže bezpečné.
                c = mqtt_client
                if c is None:
                    raise RuntimeError("MQTT client is None before connect")
                c.connect(MQTT_HOST, int(MQTT_PORT), keepalive=30)
                c.loop_start()

            # 3) čekej na connect nebo stop
            while not stop_evt.is_set():
                if mqtt_connected_evt.wait(timeout=1.0):
                    # jsme connected -> backoff reset
                    with reconnect_lock:
                        reconnect_backoff_s = 1.0

                    # pokud se mezitím odpojilo, spadneme ven a reconnect
                    if not mqtt_connected_evt.is_set():
                        break

            # 4) teardown vždy (a reset klienta, aby byl clean reconnect)
            _mqtt_teardown(reset_client=True)

        except Exception:
            logger.exception("MQTT connect loop error")
            # i po chybě: zajisti čistý stav pro další pokus
            _mqtt_teardown(reset_client=True)

        # 5) backoff
        with reconnect_lock:
            b = float(reconnect_backoff_s)
            reconnect_backoff_s = min(float(RECONNECT_BACKOFF_MAX_S), max(1.0, b * 2.0))
        time.sleep(b)



# ------------------ Workers: sys/net/proxy ------------------ #
def worker_sys():
    while not stop_evt.is_set():
        try:
            cpu_t = read_cpu_temp_c()
            l1, l5, l15 = read_loadavg()
            up = read_uptime_s()

            with state_lock:
                state["sys"] = {
                    "cpu_temp_c": cpu_t,
                    "load_1m": l1,
                    "load_5m": l5,
                    "load_15m": l15,
                    "uptime_s": up,
                    "ts": now_iso(),
                }
        except Exception:
            logger.exception("worker_sys error")
        stop_evt.wait(float(max(1, POLL_SYS_S)))


def worker_net():
    while not stop_evt.is_set():
        try:
            ha = ping_host(PING_HA_HOST, timeout_s=1.0)
            inv_ping = ping_host(PING_INVERTER_HOST, timeout_s=1.0)
            inv_tcp = tcp_check(INVERTER_HOST, INVERTER_PORT, timeout_s=1.0)

            with state_lock:
                state["net"] = {
                    "ping_ha_ok": int(ha.get("ok", 0)),
                    "ping_ha_ms": ha.get("ms"),
                    "ping_inverter_ok": int(inv_ping.get("ok", 0)),
                    "ping_inverter_ms": inv_ping.get("ms"),
                    "tcp_inverter_ok": int(inv_tcp.get("ok", 0)),
                    "tcp_inverter_ms": inv_tcp.get("ms"),
                    "ts": now_iso(),
                }
        except Exception:
            logger.exception("worker_net error")
        stop_evt.wait(float(max(1, POLL_NET_S)))


def worker_proxy():
    while not stop_evt.is_set():
        try:
            st = systemctl_is_active(PROXY_SYSTEMD_UNIT)
            with state_lock:
                state["proxy"] = {
                    "active": int(st.get("active", 0)),
                    "state": st.get("state", "unknown"),
                    "unit": PROXY_SYSTEMD_UNIT,
                    "ts": now_iso(),
                }
        except Exception:
            logger.exception("worker_proxy error")
        stop_evt.wait(float(max(1, POLL_PROXY_S)))


# ------------------ Publish loop + heartbeat ------------------ #
def publish_metrics() -> None:
    """
    Publikuje aktuální metriky ze state{}.
    Pokud doběhne bez výjimky => set_last_event_now()
    """
    if not mqtt_connected_evt.is_set():
        return

    with state_lock:
        sys_s = dict(state.get("sys") or {})
        net_s = dict(state.get("net") or {})
        proxy_s = dict(state.get("proxy") or {})

    # SYSTEM
    publish_num(mqtt_topic("sys", "cpu_temp_c"), sys_s.get("cpu_temp_c"), retain=True)
    publish_num(mqtt_topic("sys", "load_1m"), sys_s.get("load_1m"), retain=True)
    publish_num(mqtt_topic("sys", "load_5m"), sys_s.get("load_5m"), retain=True)
    publish_num(mqtt_topic("sys", "load_15m"), sys_s.get("load_15m"), retain=True)
    publish_num(mqtt_topic("sys", "uptime_s"), sys_s.get("uptime_s"), retain=True)

    # NETWORK
    publish_text(mqtt_topic("net", "ping_ha_ok"), str(int(net_s.get("ping_ha_ok", 0))), retain=True)
    publish_num(mqtt_topic("net", "ping_ha_ms"), net_s.get("ping_ha_ms"), retain=True)

    publish_text(mqtt_topic("net", "ping_inverter_ok"), str(int(net_s.get("ping_inverter_ok", 0))), retain=True)
    publish_num(mqtt_topic("net", "ping_inverter_ms"), net_s.get("ping_inverter_ms"), retain=True)

    publish_text(mqtt_topic("net", "tcp_inverter_ok"), str(int(net_s.get("tcp_inverter_ok", 0))), retain=True)
    publish_num(mqtt_topic("net", "tcp_inverter_ms"), net_s.get("tcp_inverter_ms"), retain=True)

    # PROXY
    publish_text(mqtt_topic("proxy", "active"), str(int(proxy_s.get("active", 0))), retain=True)

    # volitelně i detail state do JSON (pomáhá při ladění)
    publish_json(mqtt_topic("report", "snapshot"), {"sys": sys_s, "net": net_s, "proxy": proxy_s}, retain=True)

    # "event" = úspěšný publish
    set_last_event_now()


def worker_publish_loop():
    while not stop_evt.is_set():
        try:
            publish_metrics()
        except Exception:
            logger.exception("publish loop error")
        stop_evt.wait(float(max(1, HEARTBEAT_S)))


def worker_heartbeat():
    """
    Heartbeat + watchdog (infigy-norma):
      - bridge/online (už drží LWT + při connectu nastavíme 1)
      - bridge/heartbeat_ts (jen informativně)
      - bridge/last_event_age_s
      - bridge/ws_flow_ok
    """
    while not stop_evt.is_set():
        try:
            if mqtt_connected_evt.is_set():
                publish_num(mqtt_topic("bridge", "heartbeat_ts"), int(time.time()), retain=True)

                age = get_last_event_age_s()
                if age is None:
                    # ještě žádný event
                    publish_num(mqtt_topic("bridge", "last_event_age_s"), -1, retain=True)
                    publish_text(mqtt_topic("bridge", "ws_flow_ok"), "0", retain=True)
                else:
                    publish_num(mqtt_topic("bridge", "last_event_age_s"), int(age), retain=True)
                    publish_text(mqtt_topic("bridge", "ws_flow_ok"), str(int(ws_flow_ok())), retain=True)
        except Exception:
            logger.exception("heartbeat error")
        stop_evt.wait(float(max(1, HEARTBEAT_S)))


# ------------------ Shutdown handling ------------------ #
def handle_stop(signum=None, frame=None):
    logger.info("Stopping (signal=%s)", signum)
    stop_evt.set()
    try:
        if mqtt_connected_evt.is_set():
            # explicitně nastav online=0 při čistém stopu
            publish_text(mqtt_topic("bridge", "online"), "0", retain=True)
    except Exception:
        pass


# ------------------ main ------------------ #
def main():
    if not ENABLED:
        logger.warning("MQTT_REPORT_ENABLED=0 -> exiting.")
        return 0

    if mqtt is None:
        logger.error("paho-mqtt not available. Install: pip3 install paho-mqtt")
        return 2

    logger.debug("__file__ running from: %s", __file__)
    logger.debug("PYTHON: %s", sys.executable)
    logger.info("CFG MQTT_HOST=%s:%s USER=%s", MQTT_HOST, MQTT_PORT, "set" if MQTT_USERNAME else "empty")
    logger.info("CFG MQTT_BASE=%s DEVICE_ID=%s DISCOVERY_PREFIX=%s", MQTT_BASE_TOPIC, DEVICE_ID, DISCOVERY_PREFIX)
    logger.info("CFG INVERTER=%s:%s PING_HA=%s PING_INV=%s PROXY_UNIT=%s", INVERTER_HOST, INVERTER_PORT, PING_HA_HOST, PING_INVERTER_HOST, PROXY_SYSTEMD_UNIT)
    logger.info("CFG intervals: poll_sys=%ss poll_net=%ss poll_proxy=%ss heartbeat=%ss max_age_ok=%ss",
                POLL_SYS_S, POLL_NET_S, POLL_PROXY_S, HEARTBEAT_S, MAX_AGE_OK_S)

    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)

    threads = [
        threading.Thread(target=connect_loop, name="mqtt-connect", daemon=True),
        threading.Thread(target=worker_sys, name="poll-sys", daemon=True),
        threading.Thread(target=worker_net, name="poll-net", daemon=True),
        threading.Thread(target=worker_proxy, name="poll-proxy", daemon=True),
        threading.Thread(target=worker_publish_loop, name="publish", daemon=True),
        threading.Thread(target=worker_heartbeat, name="heartbeat", daemon=True),
    ]
    for t in threads:
        t.start()

    # main thread wait
    try:
        while not stop_evt.is_set():
            time.sleep(0.5)
    finally:
        handle_stop()
        time.sleep(0.5)
        _mqtt_teardown()
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
