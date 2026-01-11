import subprocess
import shutil
import re
from services_control import SERVICE_WHITELIST

SYSTEMCTL = "/bin/systemctl"
SUDO = "/usr/bin/sudo"

def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True).strip()
    except Exception:
        return "N/A"

def get_wifi_signal():
    # iwconfig je na raspbianu, pokud ne – vrátí N/A
    if shutil.which("iwconfig"):
        out = run("iwconfig wlan0 2>/dev/null | grep -i --color=never 'signal level'")
        return out if out != "" else "N/A"
    return "N/A"

def get_system_info():
    return {
        "hostname": run("hostname"),
        "ip_address": run("hostname -I"),
        "uptime": run("uptime -p"),
        "loadavg": run("cat /proc/loadavg | awk '{print $1, $2, $3}'"),
        "cpu_temp": run("vcgencmd measure_temp 2>/dev/null | cut -d= -f2") if shutil.which("vcgencmd") else "N/A",
        "wifi_strength": get_wifi_signal(),
        "tailscale_status": get_tailscale_status()
    }

def get_services_status():
    """
    Vrací dict: service_id -> systemctl is-active (active/inactive/failed/unknown...)
    """
    out = {}
    for service_id, unit in SERVICE_WHITELIST.items():
        try:
            state = subprocess.check_output(
                ["systemctl", "is-active", unit],
                text=True,
                stderr=subprocess.STDOUT,
            ).strip()
        except subprocess.CalledProcessError as e:
            state = (e.output or "").strip() or "unknown"
        out[service_id] = state
    return out


def get_ping_stats(target="8.8.8.8", count=4):
    try:
        result = subprocess.run([
            "ping", "-c", str(count), target
        ], capture_output=True, text=True)

        loss_match = re.search(r"(\d+)% packet loss", result.stdout)
        time_match = re.search(r"= [^/]+/([^/]+)/", result.stdout)

        return {
            "target": target,
            "loss": int(loss_match.group(1)) if loss_match else None,
            "avg_time_ms": float(time_match.group(1)) if time_match else None
        }
    except Exception as e:
        return {"target": target, "error": str(e)}


def get_multi_ping_stats(targets=None, count=4):
    if targets is None:
        targets = [
            "8.8.8.8",           # Google DNS
            "192.168.1.1",      # Huawei router
            "192.168.1.9",      # ASUS AP 1
            "192.168.1.10",     # ASUS AP 2
            "192.168.1.20"      # Home Assistant
        ]
    return [get_ping_stats(target, count) for target in targets]


def get_vnstat_interface_stats(interface="eth0"):
    try:
        result = subprocess.run(["vnstat", "--oneline", "-i", interface], capture_output=True, text=True)
        if not result.stdout.strip():
            subprocess.run(["vnstat", "--create", "-i", interface], capture_output=True)
            subprocess.run(["systemctl", "restart", "vnstat"], capture_output=True)
            return {"interface": interface, "error": "Databáze vnstat byla vytvořena. Čeká se na sběr dat."}

        parts = result.stdout.strip().split(";")
        if len(parts) < 15:
            return {"interface": interface, "error": "Nedostatečná data (vnstat výstup má méně než 15 částí)."}

        return {
            "interface": interface,
            "rx_today": parts[3],
            "tx_today": parts[4],
            "total_today": parts[5],
            "rate_today": parts[6],
            "rx_month": parts[8],
            "tx_month": parts[9],
            "total_month": parts[10],
            "rate_month": parts[11],
            "rx_total": parts[12],
            "tx_total": parts[13],
            "total_total": parts[14]
        }
    except Exception as e:
        return {"interface": interface, "error": str(e)}

def get_all_vnstat_stats():
    interfaces = ["eth0", "wlan0"]
    return [get_vnstat_interface_stats(i) for i in interfaces]


def get_iperf_test(server_ip="127.0.0.1", duration=10):
    if not shutil.which("iperf3"):
        return {"server": server_ip, "error": "iperf3 není nainstalován. Spusť: sudo apt install iperf3"}
    try:
        result = subprocess.run([
            "iperf3", "-c", server_ip, "--bind", "127.0.0.1", "-t", str(duration)
        ], capture_output=True, text=True)

        if result.returncode != 0:
            return {"server": server_ip, "error": result.stderr.strip() or "iperf3 test selhal"}

        lines = result.stdout.splitlines()
        summary_line = next((l for l in lines if "sender" in l or "receiver" in l), None)
        summary = summary_line.strip() if summary_line else result.stdout.strip() or "Žádný výstup ze serveru"

        return {"server": server_ip, "summary": summary}
    except Exception as e:
        return {"server": server_ip, "error": str(e)}


def get_tailscale_status():
    try:
        result = subprocess.run(["tailscale", "status"], capture_output=True, text=True)
        return result.stdout.strip()
    except Exception as e:
        return f"Error: {e}"
    
def get_mqtt_latency_test(
    host: str,
    port: int = 1883,
    username: str = "",
    password: str = "",
    samples: int = 20,
    interval_ms: int = 50,
    timeout_s: float = 2.0,
    topic_prefix: str = "diag/mqtt_latency",
):
    """
    MQTT loopback latency test (publish -> broker -> receive do stejneho klienta).
    Vraci dict:
      {
        "ok": True/False,
        "loss": int,
        "sent": int,
        "received": int,
        "min_ms": float|None,
        "avg_ms": float|None,
        "p95_ms": float|None,
        "max_ms": float|None,
        "details": [ { "seq":1, "rtt_ms": 12.3 } ... ],
        "error": "..." | None
      }
    """
    import time
    import uuid
    from threading import Event, Lock
    import paho.mqtt.client as mqtt

    # sane defaults
    try:
        samples = int(samples)
    except Exception:
        samples = 20
    samples = max(1, min(samples, 200))

    try:
        interval_ms = int(interval_ms)
    except Exception:
        interval_ms = 50
    interval_ms = max(0, min(interval_ms, 5000))

    try:
        timeout_s = float(timeout_s)
    except Exception:
        timeout_s = 2.0
    timeout_s = max(0.2, min(timeout_s, 10.0))

    run_id = uuid.uuid4().hex[:8]
    topic = f"{topic_prefix}/{run_id}"

    result = {
        "ok": False,
        "loss": 0,
        "sent": 0,
        "received": 0,
        "min_ms": None,
        "avg_ms": None,
        "p95_ms": None,
        "max_ms": None,
        "details": [],
        "error": None,
        "topic": topic,
    }

    lock = Lock()
    got_connect = Event()
    got_sub = Event()

    # seq -> send_time_monotonic
    sent_at = {}
    rtts = []

    def on_connect(c, userdata, flags, rc):
        if rc == 0:
            got_connect.set()
            c.subscribe(topic, qos=0)
        else:
            result["error"] = f"MQTT connect failed rc={rc}"
            got_connect.set()

    def on_subscribe(c, userdata, mid, granted_qos):
        got_sub.set()

    def on_message(c, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
            # format: "seq=<n>;t=<monotonic>"
            parts = {}
            for p in payload.split(";"):
                if "=" in p:
                    k, v = p.split("=", 1)
                    parts[k.strip()] = v.strip()

            seq = int(parts.get("seq", "0"))
            t_sent = float(parts.get("t", "0"))

            t_now = time.monotonic()
            rtt_ms = (t_now - t_sent) * 1000.0

            with lock:
                if seq in sent_at:
                    rtts.append(rtt_ms)
                    result["details"].append({"seq": seq, "rtt_ms": round(rtt_ms, 2)})
                    result["received"] += 1
        except Exception:
            # ignore malformed
            pass

    client = mqtt.Client(client_id=f"rpi-ui-lat-{run_id}", clean_session=True)
    if username or password:
        client.username_pw_set(username, password)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    try:
        client.connect(host, int(port), keepalive=20)
        client.loop_start()

        # wait connect + subscribe
        got_connect.wait(timeout=timeout_s)
        if result["error"]:
            return result
        if not got_connect.is_set():
            result["error"] = "MQTT connect timeout"
            return result

        got_sub.wait(timeout=timeout_s)
        if not got_sub.is_set():
            result["error"] = "MQTT subscribe timeout"
            return result

        # send samples
        for seq in range(1, samples + 1):
            t0 = time.monotonic()
            with lock:
                sent_at[seq] = t0
                result["sent"] += 1
            payload = f"seq={seq};t={t0}"
            client.publish(topic, payload, qos=0, retain=False)

            if interval_ms > 0:
                time.sleep(interval_ms / 1000.0)

        # wait for remaining responses
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with lock:
                if result["received"] >= result["sent"]:
                    break
            time.sleep(0.01)

        with lock:
            result["loss"] = max(0, result["sent"] - result["received"])

        if rtts:
            rtts_sorted = sorted(rtts)
            result["min_ms"] = round(rtts_sorted[0], 2)
            result["max_ms"] = round(rtts_sorted[-1], 2)
            result["avg_ms"] = round(sum(rtts_sorted) / len(rtts_sorted), 2)

            # p95
            idx = int(0.95 * len(rtts_sorted)) - 1
            idx = max(0, min(idx, len(rtts_sorted) - 1))
            result["p95_ms"] = round(rtts_sorted[idx], 2)

            result["ok"] = True
        else:
            result["error"] = "No MQTT responses received"
            result["ok"] = False

        return result

    except Exception as e:
        result["error"] = str(e)
        return result
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

def get_modbus_rtt_test(
    port: str,
    baudrate: int = 9600,
    parity: str = "N",
    stopbits: int = 1,
    bytesize: int = 8,
    timeout_s: float = 0.5,
    slaves=None,                 # list[int]
    samples: int = 30,
    interval_ms: int = 50,
    address: int = 0,
    count: int = 1,
    method: str = "di",          # "di" (FC02) nebo "hr" (FC03)
    ok_ms: float = 50.0,
    warn_ms: float = 150.0,
):
    """
    Modbus RTU RTT test (čas jednoho read požadavku/odpovědi).
    - method="di" => read_discrete_inputs (FC02)
    - method="hr" => read_holding_registers (FC03)

    Vrací dict ve stylu MQTT latency:
      {
        "ok": True/False,
        "sent": N,
        "received": M,
        "loss": N-M,
        "min_ms": .., "avg_ms": .., "p95_ms": .., "max_ms": ..,
        "details": [{"seq":1,"unit":128,"rtt_ms":12.3}, ...],
        "error": None | "...",
        "ok_ms": ok_ms,
        "warn_ms": warn_ms,
        "semafor": "ok"|"warning"|"bad"|"unknown",
        "badge": "success"|"warning"|"danger"|"secondary"
      }
    """
    import time
    from threading import Lock

    try:
        # pymodbus 3.x
        from pymodbus.client import ModbusSerialClient
    except Exception:
        # pymodbus 2.x
        from pymodbus.client.sync import ModbusSerialClient

    # sanitize
    try:
        samples = int(samples)
    except Exception:
        samples = 30
    samples = max(1, min(samples, 300))

    try:
        interval_ms = int(interval_ms)
    except Exception:
        interval_ms = 50
    interval_ms = max(0, min(interval_ms, 5000))

    try:
        timeout_s = float(timeout_s)
    except Exception:
        timeout_s = 0.5
    timeout_s = max(0.05, min(timeout_s, 5.0))

    if slaves is None:
        slaves = []
    try:
        slaves = [int(x) for x in slaves if str(x).strip() != ""]
    except Exception:
        slaves = []

    result = {
        "ok": False,
        "sent": 0,
        "received": 0,
        "loss": 0,
        "min_ms": None,
        "avg_ms": None,
        "p95_ms": None,
        "max_ms": None,
        "details": [],
        "error": None,
        "ok_ms": float(ok_ms),
        "warn_ms": float(warn_ms),
        "semafor": "unknown",
        "badge": "secondary",
        "method": method,
        "address": address,
        "count": count,
        "slaves": slaves,
    }

    if not slaves:
        result["error"] = "MODBUS: seznam slave je prázdný"
        return result

    lock = Lock()
    rtts = []

    client = ModbusSerialClient(
        method="rtu",
        port=port,
        baudrate=int(baudrate),
        parity=str(parity),
        stopbits=int(stopbits),
        bytesize=int(bytesize),
        timeout=float(timeout_s),
    )

    try:
        if not client.connect():
            result["error"] = f"MODBUS: nelze otevřít port {port}"
            return result

        unit_idx = 0
        for seq in range(1, samples + 1):
            unit = slaves[unit_idx]
            unit_idx = (unit_idx + 1) % len(slaves)

            t0 = time.monotonic()
            result["sent"] += 1

            try:
                if method == "hr":
                    rr = client.read_holding_registers(address=address, count=count, unit=unit)
                else:
                    rr = client.read_discrete_inputs(address=address, count=count, unit=unit)

                t1 = time.monotonic()
                rtt_ms = (t1 - t0) * 1000.0

                ok = (rr is not None) and (not rr.isError())
                if ok:
                    with lock:
                        result["received"] += 1
                        rtts.append(rtt_ms)
                        result["details"].append({
                            "seq": seq,
                            "unit": unit,
                            "rtt_ms": round(rtt_ms, 2),
                        })
                else:
                    # error response
                    with lock:
                        result["details"].append({
                            "seq": seq,
                            "unit": unit,
                            "rtt_ms": None,
                        })

            except Exception:
                with lock:
                    result["details"].append({
                        "seq": seq,
                        "unit": unit,
                        "rtt_ms": None,
                    })

            if interval_ms > 0:
                time.sleep(interval_ms / 1000.0)

        result["loss"] = max(0, result["sent"] - result["received"])

        if rtts:
            rtts_sorted = sorted(rtts)
            result["min_ms"] = round(rtts_sorted[0], 2)
            result["max_ms"] = round(rtts_sorted[-1], 2)
            result["avg_ms"] = round(sum(rtts_sorted) / len(rtts_sorted), 2)

            idx = int(0.95 * len(rtts_sorted)) - 1
            idx = max(0, min(idx, len(rtts_sorted) - 1))
            result["p95_ms"] = round(rtts_sorted[idx], 2)

            result["ok"] = True

            # semafor podle p95
            p95 = result["p95_ms"] if result["p95_ms"] is not None else None
            if p95 is None:
                result["semafor"] = "unknown"
                result["badge"] = "secondary"
            elif result["loss"] > 0:
                result["semafor"] = "bad"
                result["badge"] = "danger"
            elif p95 <= result["ok_ms"]:
                result["semafor"] = "ok"
                result["badge"] = "success"
            elif p95 <= result["warn_ms"]:
                result["semafor"] = "warning"
                result["badge"] = "warning"
            else:
                result["semafor"] = "bad"
                result["badge"] = "danger"
        else:
            result["error"] = "MODBUS: žádné odpovědi (0 received)"
            result["ok"] = False
            result["semafor"] = "bad"
            result["badge"] = "danger"

        return result

    except Exception as e:
        result["error"] = str(e)
        result["ok"] = False
        result["semafor"] = "bad"
        result["badge"] = "danger"
        return result
    finally:
        try:
            client.close()
        except Exception:
            pass

def systemctl_is_active(unit: str) -> str:
    try:
        out = subprocess.check_output(
            [SYSTEMCTL, "is-active", unit],
            text=True,
            stderr=subprocess.STDOUT
        ).strip()
        return out
    except subprocess.CalledProcessError as e:
        return (e.output or "").strip() or "unknown"

def systemctl_control(action: str, unit: str):
    """
    action: start|stop|restart
    returns: (ok: bool, msg: str)
    """
    cmd = [SUDO, "-n", SYSTEMCTL, action, unit]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        return True, (out or f"{action} OK: {unit}")
    except subprocess.CalledProcessError as e:
        err = (e.output or "").strip()
        if "Interactive authentication required" in err or "a password is required" in err:
            return False, (
                f"{action} selhalo: chybí oprávnění (sudoers NOPASSWD). "
                f"Unit={unit}. Detail: {err}"
            )
        return False, f"{action} selhalo: {err or str(e)}"
    
