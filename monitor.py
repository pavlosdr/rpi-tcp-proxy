import subprocess
import shutil
import os
import re

SERVICES = {
    "modbus_tcp_proxy": "modbus_tcp_proxy.service",
    "rpi-mqtt-report": "rpi-mqtt-report.service",
    "rpi-admin-ui": "rpi-admin-ui.service"
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
            out = subprocess.check_output(["systemctl", "is-active", unit], text=True).strip()
        except subprocess.CalledProcessError:
            out = "unknown"
        status[pretty] = out
    return status

def restart_service_safe(pretty_name: str):
    unit = SERVICES.get(pretty_name)
    if not unit:
        return False, f"Služba '{pretty_name}' není povolena"
    try:
        subprocess.check_call(["sudo", "systemctl", "restart", unit])
        return True, f"Služba '{pretty_name}' restartována"
    except subprocess.CalledProcessError as e:
        return False, f"Restart selhal: {e}"

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