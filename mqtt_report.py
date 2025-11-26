import os
import time
import json
import threading
import sys
import socket
import shutil
import subprocess
from datetime import datetime, timezone
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

# --- connection latches ---
connected = threading.Event()
last_any_publish_ts = time.monotonic() # heartbeat for published payloads

# --- log runtime ---
print("__file__ running from:", __file__)
print("PYTHON:", sys.executable)
print("PAHO_VERSION:", getattr(mqtt, "__version__", "unknown"))
print("HAS_V2:", hasattr(mqtt, "CallbackAPIVersion"))

# --- .env ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

# --- Konfig z .env ---
# --- MQTT ---
MQTT_HOST   = os.getenv("MQTT_HOST","localhost")
MQTT_PORT   = int(os.getenv("MQTT_PORT","1883"))
MQTT_USER   = os.getenv("MQTT_USER","")
MQTT_PASS   = os.getenv("MQTT_PASS","")
MQTT_BASE   = os.getenv("MQTT_BASE_RPI","rpi-bridge")
CLIENT_ID   = os.getenv("CLIENT_ID_RPI","rpi-monitor")
MQTT_RECONNECT_BACKOFF_MAX_S = int(os.getenv("MQTT_RECONNECT_BACKOFF_MAX_S", "60"))
DISCOVERY_PREFIX = os.getenv("DISCOVERY_PREFIX", "homeassistant")
# --- Inverter a další ---
INVERTER_HOST = os.getenv("INVERTER_HOST","10.10.100.253")
INVERTER_PORT = int(os.getenv("INVERTER_PORT","502"))
PING_HA_HOST  = os.getenv("PING_HA_HOST","192.168.1.20")
PING_INV_HOST = os.getenv("PING_INVERTER_HOST", INVERTER_HOST)
PROXY_UNIT    = os.getenv("PROXY_SYSTEMD_UNIT","modbus_tcp_proxy.service")

POLL_SYS_S    = int(os.getenv("POLL_SYS_S","10"))
POLL_NET_S    = int(os.getenv("POLL_NET_S","10"))
POLL_PROXY_S  = int(os.getenv("POLL_PROXY_S","10"))
HEARTBEAT_S   = int(os.getenv("HEARTBEAT_S","5"))
MAX_AGE_OK_S  = int(os.getenv("MAX_AGE_OK_S","60"))

DEVICE_ID   = os.getenv("DEVICE_ID","RPi-Monitor")
DEVICE_NAME = os.getenv("DEVICE_NAME","RPi Monitor")
DEVICE_MODEL= os.getenv("DEVICE_MODEL","RPi Bridge Utils")
DEVICE_MF   = os.getenv("DEVICE_MF","RPi")

# --- Singleton lock: zabran spusteni 2. instance ---
_singleton = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
try:
    _singleton.bind("\0mqtt_report.singleton")
except OSError:
    print("Another mqtt-report instance is running. Exiting.")
    sys.exit(1)

# --- MQTT klient ---
mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=CLIENT_ID,clean_session=True)
if MQTT_USER:
    mqttc.username_pw_set(MQTT_USER, MQTT_PASS)

# LWT: dostupnost zarizeni
mqttc.will_set(f"{MQTT_BASE}/bridge/online", "0", qos=1, retain=True)
# Auto-reconnect backoff
mqttc.reconnect_delay_set(min_delay=2, max_delay=MQTT_RECONNECT_BACKOFF_MAX_S)

# --- helpers ---
def publish(topic_suffix, payload, retain=True, qos=1):
    """Bezpecny publish s odchytem vyjimek + update TS."""
    global last_any_publish_ts
    topic = f"{MQTT_BASE}/{topic_suffix}"
    try:
        mqttc.publish(topic, str(payload), qos=qos, retain=retain)
        last_any_publish_ts = time.monotonic()
    except Exception as e:
        print(f"[MQTT] publish failed {topic}: {e}")

# --- MQTT callbacks (v1/v2 kompatibilita) ---
def _normalize_code(raw):
    code = getattr(raw, "value", raw)
    try: return int(code)
    except: return 0

def on_connect(client, userdata, *args, **kwargs):
    raw = kwargs.get("reason_code", kwargs.get("rc", 0))
    if len(args) > 1:
        raw = args[1]
    code = _normalize_code(raw)
    print(f"MQTT connected rc={code}")
    if code == 0:
        publish("bridge/online", "online", retain=True, qos=1)
        try:
            publish_discovery()
        except Exception as e:
            print(f"publish_discovery failed: {e}")
        connected.set()

def on_disconnect(client, userdata, *args, **kwargs):
    raw = kwargs.get("reason_code", kwargs.get("rc", -1))
    if len(args) > 0:
        raw = args[0]
    code = _normalize_code(raw)
    print(f"MQTT disconnected rc={code}")
    # NEPOSILAT "offline" – spolehni se na LWT pri necis. padu

mqttc.on_connect = on_connect
mqttc.on_disconnect = on_disconnect

# --- Discovery helper ---
DEVICE_BLOCK = {
    "ids": [DEVICE_ID],
    "name": DEVICE_NAME,
    "mdl": DEVICE_MODEL,
    "mf":  DEVICE_MF,
}
AVAIL = [{
    "topic": f"{MQTT_BASE}/bridge/online",
    "payload_available": "1",
    "payload_not_available": "0"
}]

def _disc_pub(kind, obj, key, cfg):
    topic = f"{DISCOVERY_PREFIX}/{kind}/{obj}/{key}/config"
    mqttc.publish(topic, json.dumps(cfg), qos=1, retain=True)

def publish_discovery():
    # Sensors
    sensors = [
        ("cpu_temp_c",      "Teplota CPU",            "temperature", "measurement", "°C"),
        ("load_1m",         "Zátěž 1m",               None,          "measurement", None),
        ("mem_used_pct",    "RAM použito",            "battery",     "measurement", "%"),
        ("disk_root_used_pct","Disk / využití",       "battery",     "measurement", "%"),
        ("uptime_s",        "Uptime",                 "duration",    "measurement", "s"),
        ("ping_ha_ms",      "Ping HA (ms)",           None,          "measurement", "ms"),
        ("ping_inverter_ms","Ping Inverter (ms)",     None,          "measurement", "ms"),
        ("tcp_inverter_latency_ms","TCP Inverter latency (ms)", None, "measurement","ms"),
        ("last_poll_age_s", "Doba od poslední publikace (s)", None, "measurement","s"),
    ]
    for key, name, dev_cla, stat_cla, unit in sensors:
        cfg = {
            "name": f"{DEVICE_NAME} {name}",
            "uniq_id": f"{DEVICE_ID}_{key}",
            "stat_t": f"{MQTT_BASE}/sys/{key}" if key in ["cpu_temp_c","load_1m","mem_used_pct","disk_root_used_pct","uptime_s"] else (
                      f"{MQTT_BASE}/net/{key}" if key.startswith(("ping_","tcp_")) else
                      f"{MQTT_BASE}/bridge/{key}"),
            "avty": AVAIL,
            "dev": DEVICE_BLOCK,
        }
        if dev_cla:   cfg["dev_cla"]  = dev_cla
        if stat_cla:  cfg["stat_cla"] = stat_cla
        if unit:      cfg["unit_of_meas"] = unit
        _disc_pub("sensor", "rpi", key, cfg)

    # Binary sensors
    bin_sensors = [
        ("tcp_inverter_ok", "TCP inverter OK"),
        ("flow_ok",         "Flow OK"),           # age < MAX_AGE_OK_S
        ("systemd_active",  "Proxy aktivní"),
    ]
    for key, name in bin_sensors:
        cfg = {
            "name": f"{DEVICE_NAME} {name}",
            "uniq_id": f"{DEVICE_ID}_{key}",
            "stat_t": f"{MQTT_BASE}/net/{key}" if key.startswith("tcp_") else (
                      f"{MQTT_BASE}/bridge/{key}" if key in ["flow_ok"] else
                      f"{MQTT_BASE}/proxy/{key}"),
            "pl_on": "1",
            "pl_off":"0",
            "dev_cla": "connectivity" if key != "systemd_active" else "power",
            "avty": AVAIL,
            "dev": DEVICE_BLOCK,
        }
        _disc_pub("binary_sensor", "rpi", key, cfg)

# --- Helpers: system info / ping / tcp / service ---
def read_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp","r") as f:
            return round(int(f.read().strip())/1000.0, 1)
    except: return None

def load_1m():
    try: return round(os.getloadavg()[0], 2)
    except: return None

def mem_used_pct():
    try:
        with open("/proc/meminfo") as f:
            kv = {}
            for line in f:
                parts = line.split(":")
                if len(parts)>=2:
                    kv[parts[0]] = int(parts[1].strip().split()[0])  # kB
        total = kv.get("MemTotal",0)
        free  = kv.get("MemFree",0) + kv.get("Buffers",0) + kv.get("Cached",0)
        used  = max(0, total - free)
        return round(used*100.0/total, 1) if total>0 else None
    except: return None

def disk_root_used_pct():
    try:
        total, used, free = shutil.disk_usage("/")
        return round(used*100.0/total, 1)
    except: return None

def uptime_s():
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except: return None

def ping_once(host, timeout_s=1):
    try:
        # -c 1 (jedno echo), -W timeout v sekundách
        r = subprocess.run(["/bin/ping","-c","1","-W",str(timeout_s),host],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if r.returncode != 0:
            return -1
        # najdi time=XX ms
        for line in r.stdout.splitlines():
            if "time=" in line:
                try:
                    ms = float(line.split("time=")[1].split()[0])
                    return ms
                except:
                    pass
        return -1
    except:
        return -1

def tcp_latency_ms(host, port, timeout_s=1.0):
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            dt = (time.perf_counter()-t0)*1000.0
            return True, round(dt,1)
    except:
        return False, -1.0

def systemd_is_active(unit):
    try:
        r = subprocess.run(["/bin/systemctl","is-active", unit],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        return 1 if r.stdout.strip()=="active" else 0
    except:
        return 0

# --- Workers ---
stop_evt = threading.Event()
last_publish_ts = time.monotonic()

def worker_sys():
    while not stop_evt.is_set():
        if connected.is_set():
            v = read_cpu_temp()
            if v is not None: publish("sys/cpu_temp_c", v)
            v = load_1m()
            if v is not None: publish("sys/load_1m", v)
            v = mem_used_pct()
            if v is not None: publish("sys/mem_used_pct", v)
            v = disk_root_used_pct()
            if v is not None: publish("sys/disk_root_used_pct", v)
            v = uptime_s()
            if v is not None: publish("sys/uptime_s", v)
            global last_publish_ts
            last_publish_ts = time.monotonic()
        time.sleep(POLL_SYS_S)

def worker_net():
    while not stop_evt.is_set():
        if connected.is_set():
            ms = ping_once(PING_HA_HOST, 1)
            publish("net/ping_ha_ms", ms)
            ms = ping_once(PING_INV_HOST, 1)
            publish("net/ping_inverter_ms", ms)
            ok, lat = tcp_latency_ms(INVERTER_HOST, INVERTER_PORT, 1.0)
            publish("net/tcp_inverter_ok", "1" if ok else "0")
            publish("net/tcp_inverter_latency_ms", lat)
        time.sleep(POLL_NET_S)

def worker_proxy():
    last_state = None
    while not stop_evt.is_set():
        if connected.is_set():
            st = systemd_is_active(PROXY_UNIT)  # 1|0
            publish("proxy/systemd_active", st)
            # zmena statu -> publ. timestamp
            if st != last_state:
                ts = datetime.now(timezone.utc).isoformat()
                publish("proxy/last_status_change_ts", ts)
                last_state = st
        time.sleep(POLL_PROXY_S)

def worker_heartbeat():
    while not stop_evt.is_set():
        if connected.is_set():
            age = int(time.monotonic() - last_publish_ts)
            publish("bridge/last_poll_age_s", age)
            flow_ok = "1" if age < MAX_AGE_OK_S else "0"
            publish("bridge/flow_ok", flow_ok)
        time.sleep(HEARTBEAT_S)

def main():
    print("__file__ running from:", os.path.abspath(__file__))
    print("MQTT:", f"{MQTT_HOST}:{MQTT_PORT}", "BASE:", MQTT_BASE, "CLIENT_ID:", CLIENT_ID)

    mqttc.on_connect = on_connect
    mqttc.on_disconnect = on_disconnect

    mqttc.connect(MQTT_HOST, MQTT_PORT, 60)
    mqttc.loop_start()

    threads = [
        threading.Thread(target=worker_sys, daemon=True),
        threading.Thread(target=worker_net, daemon=True),
        threading.Thread(target=worker_proxy, daemon=True),
        threading.Thread(target=worker_heartbeat, daemon=True),
    ]
    for t in threads: t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        time.sleep(0.5)
        try:
            publish("bridge/online", "offline", retain=True)  # korektni vypnuti
        except Exception:
            pass
        mqttc.loop_stop()
        mqttc.disconnect()

if __name__ == "__main__":
    main()
