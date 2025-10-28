# app.py — finální s metrikami logu
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
from dotenv import load_dotenv
import os
import io
import re
import time
import datetime as dt
from collections import defaultdict, deque
from typing import Optional  # pro kompatibilitu s Python <3.10

from auth import login_required, check_credentials
from monitor import (
    get_system_info,
    get_services_status,
    restart_service_safe,
    get_multi_ping_stats,
    get_all_vnstat_stats,
    get_iperf_test,
)

# načti .env ze stejného adresáře
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

app = Flask(__name__)
app.secret_key = os.getenv("UI_SECRET", "change-me")

# ---------- Pomocné ----------
LOG_FILE = os.getenv("LOG_FILE", "/var/log/modbus_proxy.log")

def _read_tail(path: str, max_bytes: int = 200_000) -> str:
    """Rychlé přečtení konce souboru (max_bytes)."""
    if not os.path.exists(path):
        return ""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(-max_bytes, os.SEEK_END)
        data = f.read()
    # uklid UTF-8 i když jsou v logu binární útržky
    return data.decode("utf-8", errors="replace")

_time_re = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
_kind_re = re.compile(r"\b(out_of_order|stray_response|duplicate_request)\b")
# volitelně RTT a tidy
_rtt_re  = re.compile(r"\brtt=(\d+)ms\b")
_tid_re  = re.compile(r"\btid=(\d+)\b")

def _parse_dt_from_line(line: str) -> Optional[dt.datetime]:
    m = _time_re.match(line)
    if not m:
        return None
    dates, times = m.group(1), m.group(2)
    try:
        return dt.datetime.fromisoformat(f"{dates} {times}")
    except Exception:
        return None

def parse_log_metrics(
    path: str,
    window_minutes: int = 60,
    max_scan_bytes: int = 2_000_000,
):
    """
    Vrátí metriky za posledních `window_minutes`:
      {
        'counts': {'out_of_order': X, 'stray_response': Y, 'duplicate_request': Z, 'total': N},
        'series': [{'t':'HH:MM','out_of_order':a,'stray_response':b,'duplicate_request':c,'total':s}, ...],
        'rtt': {'avg_ms':..., 'p95_ms':..., 'samples':K}
      }
    Čteme jen konec souboru (max_scan_bytes) pro rychlost.
    """
    out = {
        "counts": {"out_of_order": 0, "stray_response": 0, "duplicate_request": 0, "total": 0},
        "series": [],
        "rtt": {"avg_ms": None, "p95_ms": None, "samples": 0},
    }
    if not os.path.exists(path):
        return out

    now = dt.datetime.now()
    window_start = now - dt.timedelta(minutes=window_minutes)

    tail = _read_tail(path, max_bytes=max_scan_bytes)
    if not tail:
        return out

    # agregace po minutách
    buckets = defaultdict(lambda: {"out_of_order": 0, "stray_response": 0, "duplicate_request": 0, "total": 0})
    rtts = []

    for line in tail.splitlines():
        ts = _parse_dt_from_line(line)
        if not ts or ts < window_start:
            continue

        km = _kind_re.search(line)
        if not km:
            continue

        kind = km.group(1)
        out["counts"][kind] += 1
        out["counts"]["total"] += 1

        minute_key = ts.replace(second=0, microsecond=0)
        buckets[minute_key][kind] += 1
        buckets[minute_key]["total"] += 1

        # RTT pokud je v řádku
        rm = _rtt_re.search(line)
        if rm:
            try:
                rtts.append(int(rm.group(1)))
            except Exception:
                pass

    # převod bucketů do seřazené řady
    for t in sorted(buckets.keys()):
        v = buckets[t]
        out["series"].append({
            "t": t.strftime("%H:%M"),
            "out_of_order": v["out_of_order"],
            "stray_response": v["stray_response"],
            "duplicate_request": v["duplicate_request"],
            "total": v["total"],
        })

    # RTT statistiky
    if rtts:
        rtts.sort()
        n = len(rtts)
        out["rtt"]["samples"] = n
        out["rtt"]["avg_ms"] = int(sum(rtts) / n)
        p95_idx = max(0, int(0.95 * n) - 1)
        out["rtt"]["p95_ms"] = rtts[p95_idx]

    return out

# ---------- ROUTES ----------

@app.route("/", methods=["GET"])
@login_required
def index():
    info = get_system_info()
    services = get_services_status()
    ping_stats = get_multi_ping_stats()
    vnstat_stats = get_all_vnstat_stats()
    return render_template(
        "index.html",
        info=info,
        services=services,
        ping_stats=ping_stats,
        vnstat_stats=vnstat_stats,
        title="Dashboard",
    )

@app.route("/restart/<service>", methods=["POST"])
@login_required
def restart(service):
    ok, msg = restart_service_safe(service)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("index"))

@app.route("/env", methods=["GET", "POST"])
@login_required
def show_env():
    # seznam povolených klíčů (.env se přepisuje jen pro tyto)
    allowed = [
        # Proxy
        "LISTEN_IP", "LISTEN_PORT", "PROXY_TARGET_IP", "PROXY_TARGET_PORT",
        "BUFFER_SIZE", "SOCK_TIMEOUT_S",
        # Modbus/TID/UID režimy
        "TID_REWRITE", "TID_STRICT", "STRICT_UID", "PASS_STRAY",
        # Logging
        "LOG_FILE", "LOG_LEVEL", "LOG_HEXDUMP", "LOG_SAMPLE_BYTES",
        "LOG_STATS_INTERVAL", "LOG_MAX_BYTES", "LOG_BACKUP_COUNT", "DROP_STRAY_SILENT",
        # MQTT
        "MQTT_ENABLED", "MQTT_HOST", "MQTT_PORT", "MQTT_TOPIC_PREFIX", "MQTT_REPORT_INTERVAL",
        # UI
        "UI_USER", "UI_PASS", "UI_SECRET", "PORT",
    ]

    if request.method == "POST":
        # načti původní .env
        try:
            with open(ENV_PATH, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []

        # přepiš jen povolené klíče (zbytek zachovej)
        new_lines = []
        present = set()
        for line in lines:
            if "=" not in line or line.lstrip().startswith("#"):
                new_lines.append(line)
                continue
            key = line.split("=", 1)[0].strip()
            if key in allowed:
                val = request.form.get(key, "")
                new_lines.append(f"{key}={val}\n")
                present.add(key)
            else:
                new_lines.append(line)

        # klíče, které v .env vůbec nebyly – přidej na konec
        for key in allowed:
            if key not in present:
                val = request.form.get(key, os.getenv(key, ""))
                new_lines.append(f"{key}={val}\n")

        with open(ENV_PATH, "w") as f:
            f.writelines(new_lines)

        # reload do procesu
        load_dotenv(dotenv_path=ENV_PATH, override=True)
        flash(".env uloženo", "success")
        return redirect(url_for("show_env"))

    # GET – vyplň hodnoty
    keys = [
        "LISTEN_IP", "LISTEN_PORT", "PROXY_TARGET_IP", "PROXY_TARGET_PORT",
        "BUFFER_SIZE", "SOCK_TIMEOUT_S",
        "TID_REWRITE", "TID_STRICT", "STRICT_UID", "PASS_STRAY",
        "LOG_FILE", "LOG_LEVEL", "LOG_HEXDUMP", "LOG_SAMPLE_BYTES",
        "LOG_STATS_INTERVAL", "LOG_MAX_BYTES", "LOG_BACKUP_COUNT", "DROP_STRAY_SILENT",
        "MQTT_ENABLED", "MQTT_HOST", "MQTT_PORT", "MQTT_TOPIC_PREFIX", "MQTT_REPORT_INTERVAL",
        "UI_USER", "UI_PASS", "UI_SECRET", "PORT",
    ]
    values = {k: os.getenv(k, "") for k in keys}
    return render_template("env.html", values=values, title="Nastavení (.env)")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if check_credentials(request.form.get("username"), request.form.get("password")):
            session["authenticated"] = True
            return redirect(url_for("index"))
        flash("Neplatné přihlašovací údaje", "error")
    return render_template("login.html", title="Přihlášení")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/network", methods=["GET", "POST"])
@login_required
def network():
    ping_results = []
    iperf_result = None
    default_targets = "8.8.8.8, 192.168.1.1, 192.168.1.9, 192.168.1.10, 192.168.1.20"

    if request.method == "POST":
        action = request.form.get("action")
        if action == "ping":
            targets = request.form.get("targets", default_targets)
            ip_list = [ip.strip() for ip in targets.split(",") if ip.strip()]
            ping_results = get_multi_ping_stats(ip_list)
        elif action == "iperf":
            iperf_ip = request.form.get("iperf_ip", "192.168.1.20")
            duration = int(request.form.get("duration", 10))
            iperf_result = get_iperf_test(iperf_ip, duration)

    return render_template(
        "network.html",
        ping_results=ping_results,
        iperf_result=iperf_result,
        default_targets=default_targets,
        title="Síťové testy",
    )

# ---------- LOGS + METRIKY ----------

@app.route("/logs", methods=["GET"])
@login_required
def logs():
    """
    Zobrazí posledních N řádků logu + metriky z posledních M minut.
    /logs?tail=2000&minutes=120
    """
    tail_lines = int(request.args.get("tail", 1000))
    minutes = int(request.args.get("minutes", 60))

    if not os.path.exists(LOG_FILE):
        flash(f"[LOG] Soubor neexistuje: {LOG_FILE}", "error")
        tail_text = ""
        metrics = {"counts": {"out_of_order": 0, "stray_response": 0, "duplicate_request": 0, "total": 0},
                   "series": [], "rtt": {"avg_ms": None, "p95_ms": None, "samples": 0}}
    else:
        # načti jen konec souboru, pak ořízni na požadovaný počet řádků
        raw = _read_tail(LOG_FILE, max_bytes=2_000_000)
        lines = raw.splitlines()
        if tail_lines > 0 and len(lines) > tail_lines:
            lines = lines[-tail_lines:]
        tail_text = "\n".join(lines)

        # metriky
        metrics = parse_log_metrics(LOG_FILE, window_minutes=minutes, max_scan_bytes=2_000_000)

    # připravíme datasety pro Chart.js
    labels = [p["t"] for p in metrics["series"]]
    ds_out = [p["out_of_order"] for p in metrics["series"]]
    ds_str = [p["stray_response"] for p in metrics["series"]]
    ds_dup = [p["duplicate_request"] for p in metrics["series"]]
    ds_tot = [p["total"] for p in metrics["series"]]

    return render_template(
        "logs.html",
        title="Logy proxy",
        log_path=LOG_FILE,
        tail_text=tail_text,
        tail_lines=tail_lines,
        minutes=minutes,
        counts=metrics["counts"],
        rtt=metrics["rtt"],
        labels=labels,
        ds_out=ds_out,
        ds_str=ds_str,
        ds_dup=ds_dup,
        ds_tot=ds_tot,
    )

@app.route("/logs/download", methods=["GET"])
@login_required
def logs_download():
    if not os.path.exists(LOG_FILE):
        abort(404)
    with open(LOG_FILE, "rb") as f:
        data = f.read()
    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=os.path.basename(LOG_FILE),
        mimetype="text/plain",
    )

if __name__ == "__main__":
    # pro vývoj; v produkci běží přes systemd
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
