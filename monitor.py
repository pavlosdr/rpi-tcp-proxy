import subprocess
import shutil
import os
import re

SERVICES = {
    "modbus_tcp_proxy": "modbus_tcp_proxy.service",
    "modbus_io_broker": "modbus_io_broker.service",
    "infigy_ws_to_mqtt": "infigy_ws_to_mqtt.service",
    "rpi-mqtt-report": "rpi-mqtt-report.service",
    "rpi-admin-ui": "rpi-admin-ui.service",
}

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

def get_tailscale_status():
    if shutil.which("tailscale"):
        return run("tailscale status 2>/dev/null | head -n 10")
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
    status = {}
    for pretty, unit in SERVICES.items():
        try:
            out = subprocess.check_output(
                ["systemctl", "is-active", unit],
                text=True,
                stderr=subprocess.STDOUT
            ).strip()
        except subprocess.CalledProcessError as e:
            out = (e.output or "").strip() or "unknown"
        status[pretty] = out
    return status


def restart_service_safe(pretty_name: str):
    unit = SERVICES.get(pretty_name)
    if not unit:
        return False, f"Služba '{pretty_name}' není povolena"

    try:
        # Speciální případ: restart samotného UI musí být "odložený",
        # aby Flask stihl poslat odpověď prohlížeči.
        if unit == "rpi-admin-ui.service":
            subprocess.check_call([
                "sudo", "systemd-run",
                "--unit", "rpi-admin-ui-restart-job",
                "--on-active=2s",
                "/bin/systemctl", "restart", unit
            ])
            return True, f"Služba '{pretty_name}' bude restartována (za 2 s)"
        else:
            subprocess.check_call(["sudo", "systemctl", "restart", unit])
            return True, f"Služba '{pretty_name}' restartována"

    except subprocess.CalledProcessError as e:
        return False, f"Restart selhal: {e}"


def start_service_safe(pretty_name: str):
    unit = SERVICES.get(pretty_name)
    if not unit:
        return False, f"Služba '{pretty_name}' není povolena"
    try:
        subprocess.check_call(["sudo", "systemctl", "start", unit])
        return True, f"Služba '{pretty_name}' spuštěna"
    except subprocess.CalledProcessError as e:
        return False, f"Start selhal: {e}"

def stop_service_safe(pretty_name: str):
    unit = SERVICES.get(pretty_name)
    if not unit:
        return False, f"Služba '{pretty_name}' není povolena"
    try:
        subprocess.check_call(["sudo", "systemctl", "stop", unit])
        return True, f"Služba '{pretty_name}' zastavena"
    except subprocess.CalledProcessError as e:
        return False, f"Stop selhal: {e}"

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
    
def get_service_detail(pretty_name: str, journal_lines: int = 200):
    unit = SERVICES.get(pretty_name)
    if not unit:
        return None, None, f"Služba '{pretty_name}' není povolena"

    # systemctl status
    try:
        status_out = subprocess.check_output(
            ["systemctl", "status", unit, "--no-pager", "--full"],
            text=True,
            stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as e:
        status_out = (e.output or "").strip()
        if not status_out:
            status_out = f"systemctl status selhal: {e}"

    # journalctl (posledních N řádků)
    try:
        journal_out = subprocess.check_output(
            ["journalctl", "-u", unit, "-n", str(journal_lines), "--no-pager", "--output=short-iso"],
            text=True,
            stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as e:
        journal_out = (e.output or "").strip()
        if not journal_out:
            journal_out = f"journalctl selhal: {e}"

    return status_out, journal_out, None

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
    import statistics
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
