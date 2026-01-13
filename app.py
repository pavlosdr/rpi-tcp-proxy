# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort, jsonify, current_app
from dotenv import load_dotenv
import os
import io
import re
import logging
import sys
import datetime as dt
from collections import defaultdict, deque
from typing import Optional, Dict
from mqtt_tools import (
    mqtt_list_retained_discovery, 
    mqtt_delete_retained, 
)
from auth import login_required, check_credentials
from monitor import (
    get_system_info,
    get_multi_ping_stats,
    get_all_vnstat_stats,
    get_iperf_test,
    get_mqtt_latency_test,
    get_modbus_rtt_test,
    tail_file,
    parse_mqtt_report_log_stats,
)
from services_control import (
    SERVICES_META,
    get_meta,
    is_active,
    restart_service_safe,
    start_service_safe,
    stop_service_safe,
    get_service_detail,
    resolve_service_key,
    _normalize_unit_base,
)
from envfile import read_env_file
from agenda_env import build_agenda_context, handle_agenda_post
from config.agendas import AGENDAS

# ---------------------- KONFIGURACE ---------------------- #

# ------------------------ .env --------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # načti .env ze stejného adresáře
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)


# --- force logs to journald (stdout) ---
root = logging.getLogger()
root.handlers.clear()

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
root.addHandler(handler)
root.setLevel(logging.INFO)
LOG_FILE = os.getenv("LOG_FILE", "/var/log/modbus_proxy.log")

app = Flask(__name__)
# --------------------Flask app logger -------------------- #
app.logger.handlers.clear()
app.logger.propagate = True
app.logger.setLevel(logging.INFO)

app.secret_key = os.getenv("UI_SECRET", "change-me")
# -------------Werkzeug (HTTP access + errors) ------------ #
logging.getLogger("werkzeug").setLevel(logging.INFO)

# ------------------------- helpers ------------------------- #
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

# ------------------------ ROUTES ------------------------- #

@app.route("/", methods=["GET"])
@login_required
def index():
    info = get_system_info()
    return render_template(
        "index.html",
        info=info,
        title="Dashboard",
    )

@app.route("/restart/<service_id>", methods=["POST"])
@login_required
def restart(service_id):
    ok, msg = restart_service_safe(service_id)
    flash(msg, "success" if ok else "error")
    return redirect(request.referrer or url_for("services_page"))


@app.route("/start/<service_id>", methods=["POST"])
@login_required
def start_service(service_id):
    ok, msg = start_service_safe(service_id)
    flash(msg, "success" if ok else "error")
    return redirect(request.referrer or url_for("services_page"))


@app.route("/stop/<service_id>", methods=["POST"])
@login_required
def stop_service(service_id):
    ok, msg = stop_service_safe(service_id)
    flash(msg, "success" if ok else "error")
    return redirect(request.referrer or url_for("services_page"))

@app.route("/services", methods=["GET"])
@login_required
def services_page():
    services = []

    # --- načti agendy (pokud existují) ---
    try:
        from config.agendas import AGENDAS
    except Exception:
        AGENDAS = {}

    # mapování: service_id (bez .service) -> agenda_id
    service_to_agenda: Dict[str, str] = {}
    for agenda_id, a in (AGENDAS or {}).items():
        svc = (a or {}).get("service_id")
        if not svc:
            continue
        base = _normalize_unit_base(str(svc).strip())
        if base:
            service_to_agenda[base] = agenda_id

    # --- vytvoř seznam služeb pro template ---
    for key, meta in (SERVICES_META or {}).items():
        # state získáváme přes kanonický key (whitelist -> unit)
        _, state, _ = is_active(key)

        m = dict(meta or {})
        m["service_key"] = key   # kanonický identifikátor pro routy/UI
        m["key"] = key           # volitelné (kdybys někde používal)

        # config_url: pouze podle AGENDAS (podle service_id nebo unit base)
        unit_base = _normalize_unit_base(str(m.get("unit", "")).strip())
        agenda_id = service_to_agenda.get(key) or (service_to_agenda.get(unit_base) if unit_base else None)
        if agenda_id:
            m["config_url"] = url_for("agenda_env", agenda_id=agenda_id)

        services.append({"sid": key, "meta": m, "state": state})

    return render_template("services.html", services=services, title="Služby")

@app.route("/services/<service_id>", methods=["GET"])
@login_required
def service_detail(service_id):
    journal_lines = int(request.args.get("n", 200))
    log_lines = int(request.args.get("ln", 200))

    key = resolve_service_key(service_id)

    meta = get_meta(key) or {
        "pretty_name": key,
        "unit": "",
        "description": "",
        "icon": "",
    }

    ok, state, err = is_active(key)
    if not ok and err:
        flash(err, "error")
        return redirect(url_for("services_page"))

    status_out, journal_out, err2, unit = get_service_detail(key, journal_lines=journal_lines)
    if err2:
        flash(err2, "error")
        return redirect(url_for("services_page"))

    # service pro templaty: kanonický id = service_id
    m = dict(meta)
    m["key"] = key
    m["service_key"] = key
    m["unit"] = unit
    m["state"] = state

    # -------------------
    # LOG pouze pro modbus-proxy
    # -------------------
    log_enabled = (key == "modbus-proxy")

    log_path = (LOG_FILE or "").strip() if log_enabled else ""
    log_exists = bool(log_path and os.path.exists(log_path))
    log_size = os.path.getsize(log_path) if log_exists else 0
    log_out = tail_file(log_path, log_lines) if log_exists else ""
    log_stats = parse_mqtt_report_log_stats(log_out) if log_enabled else {"out_of_order": 0, "stray_response": 0, "duplicate_request": 0, "total": 0}
    
    return render_template(
        "service_detail.html",
        title=f"Detail služby: {m.get('pretty_name', key)}",
        service=m,
        service_id=service_id,          # <<< důležité pro správné URL v template
        unit=unit,
        state=state,
        status_out=status_out,
        journal_out=journal_out,
        journal_lines=journal_lines,
        active_nav="services",
        # log karta
        log_enabled=log_enabled,
        log_path=log_path,
        log_out=log_out,
        log_exists=log_exists,
        log_size=log_size,
        log_stats=log_stats,
        log_lines=log_lines,
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if check_credentials(request.form.get("username"), request.form.get("password")):
            session["authenticated"] = True
            return redirect(url_for("index"))
        flash("Neplatné přihlašovací údaje", "error")
    return render_template("login.html", title="Přihlášení", active_nav="dashboard")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/network", methods=["GET", "POST"])
@login_required
def network():
    import ipaddress
    import os

    # pro diag_cards (a obecně metadata agend)
    try:
        from config.agendas import AGENDAS
    except Exception:
        AGENDAS = {}

    def _get_field(r, key, default=None):
        # umí dict i objekt
        if isinstance(r, dict):
            return r.get(key, default)
        return getattr(r, key, default)

    def _set_field(r, key, value):
        # umí dict i objekt
        if isinstance(r, dict):
            r[key] = value
        else:
            setattr(r, key, value)

    def _is_private_target(target: str) -> bool:
        if not target:
            return False
        t = target.strip()
        try:
            ip = ipaddress.ip_address(t)
        except ValueError:
            return False
        if ip.is_private:
            return True
        cgnat = ipaddress.ip_network("100.64.0.0/10")
        return ip in cgnat

    def _latency_key(r) -> float:
        """
        řazení podle avg_time_ms
        - None/nelze převést => inf (na konec)
        - string číslo => float
        """
        v = _get_field(r, "avg_time_ms", None)
        if v is None:
            return float("inf")
        try:
            return float(v)
        except Exception:
            return float("inf")

    def _semafor_from_latency(avg_ms, p95_ms, loss, ok_ms=30, warn_ms=80):
        """
        Jednoduchý semafor:
          - bad: loss>0 nebo p95>=warn_ms nebo avg>=warn_ms
          - warning: p95>=ok_ms nebo avg>=ok_ms
          - ok: jinak
        """
        try:
            loss_i = int(loss) if loss is not None else 0
        except Exception:
            loss_i = 0

        def _to_float(x):
            try:
                return float(x) if x is not None else None
            except Exception:
                return None

        a = _to_float(avg_ms)
        p = _to_float(p95_ms)

        if loss_i > 0:
            return ("bad", "danger")

        # když nemáme data, neumíme hodnotit
        if a is None and p is None:
            return ("unknown", "secondary")

        # "bad"
        if (p is not None and p >= warn_ms) or (a is not None and a >= warn_ms):
            return ("bad", "danger")

        # "warning"
        if (p is not None and p >= ok_ms) or (a is not None and a >= ok_ms):
            return ("warning", "warning")

        return ("ok", "success")

    def _to_int(x, default=0):
        try:
            return int(str(x).strip())
        except Exception:
            return default

    def _to_float_safe(x, default=0.0):
        try:
            return float(str(x).strip().replace(",", "."))
        except Exception:
            return default

    # ------------------------------------------------------------
    # Načtení .env přes existující read_env_file()
    # ------------------------------------------------------------
    # pro tuto aplikaci je .env obvykle /opt/rpi-admin-ui/.env
    env_path = (AGENDAS.get("io-modbus-mqtt", {}) or {}).get("env_path") or "/opt/rpi-admin-ui/.env"
    env_values, _env_lines = read_env_file(env_path)

    # ------------------------------------------------------------
    # diag_cards (aktuální konfigurace testů + deep-link do agendy)
    # ------------------------------------------------------------
    diag_cards = []
    for agenda_id, agenda in (AGENDAS or {}).items():
        diags = agenda.get("diagnostics") or []
        if not diags:
            continue

        a_env_path = agenda.get("env_path") or env_path
        a_env_values = env_values if a_env_path == env_path else read_env_file(a_env_path)[0]

        for d in diags:
            params = []
            for p in d.get("params", []):
                key = p.get("key", "")
                params.append({
                    "label": p.get("label", key),
                    "value": a_env_values.get(key, ""),
                    "suffix": p.get("suffix", ""),
                    "key": key,
                })

            thresholds = []
            for th in d.get("thresholds", []):
                ks = th.get("keys") or []
                a = a_env_values.get(ks[0], "") if len(ks) > 0 else ""
                b = a_env_values.get(ks[1], "") if len(ks) > 1 else ""
                thresholds.append({
                    "label": th.get("label", "Threshold"),
                    "value": f"{a}/{b}",
                    "suffix": th.get("suffix", ""),
                })

            edit_url = url_for(
                "agenda_env",
                agenda_id=agenda_id,
                tab=d.get("tab", ""),
                sec=d.get("section", ""),
            )

            diag_cards.append({
                "id": d.get("id", ""),  # DULEZITE: aby slo vybrat v sablone podle id
                "agenda_id": agenda_id,
                "agenda_title": agenda.get("title", agenda_id),
                "title": d.get("title", d.get("id", "diagnostic")),
                "edit_url": edit_url,
                "params": params,
                "thresholds": thresholds,
                # volitelne, kdybys to nekdy chtel zobrazovat / ladit:
                # "tab": d.get("tab", ""),
                # "section": d.get("section", ""),
            })

    ping_results = []
    iperf_result = None
    mqtt_latency = None  # posíláme do šablony
    modbus_rtt = None
    default_targets = "8.8.8.8, 192.168.1.1, 192.168.1.9, 192.168.1.10, 192.168.1.20"

    if request.method == "POST":
        action = request.form.get("action")

        if action == "ping":
            targets = request.form.get("targets", default_targets)
            ip_list = [ip.strip() for ip in targets.split(",") if ip.strip()]
            ping_results = get_multi_ping_stats(ip_list)

            for r in ping_results:
                try:
                    _set_field(r, "is_private", _is_private_target(_get_field(r, "target", "")))
                except Exception:
                    pass

            # seřazení podle latence
            ping_results.sort(key=_latency_key)

        elif action == "iperf":
            iperf_ip = request.form.get("iperf_ip", "192.168.1.20")
            duration = int(request.form.get("duration", 10))
            iperf_result = get_iperf_test(iperf_ip, duration)

        elif action == "mqtt_latency":
            try:
                # Konfigurace z .env přes read_env_file
                host = env_values.get("MODBUS_IO_MQTT_HOST", "192.168.1.20")
                port = _to_int(env_values.get("MODBUS_IO_MQTT_PORT", "1883"), 1883)
                user = env_values.get("MODBUS_IO_MQTT_USERNAME", "")
                pwd  = env_values.get("MODBUS_IO_MQTT_PASSWORD", "")

                samples = _to_int(env_values.get("MODBUS_IO_MQTT_LATENCY_COUNT", "10"), 10)
                interval_ms = _to_int(env_values.get("MODBUS_IO_MQTT_LATENCY_INTERVAL_MS", "100"), 100)
                timeout_s = _to_float_safe(env_values.get("MODBUS_IO_MQTT_LATENCY_TIMEOUT_S", "2.0"), 2.0)

                topic_prefix = env_values.get("MODBUS_IO_MQTT_LATENCY_TOPIC_PREFIX", "diag/mqtt_latency")

                ok_ms = _to_float_safe(env_values.get("MODBUS_IO_MQTT_LATENCY_OK_MS", "30"), 30.0)
                warn_ms = _to_float_safe(env_values.get("MODBUS_IO_MQTT_LATENCY_WARN_MS", "80"), 80.0)

                result = get_mqtt_latency_test(
                    host=host,
                    port=port,
                    username=user,
                    password=pwd,
                    samples=samples,
                    interval_ms=interval_ms,
                    timeout_s=timeout_s,
                    topic_prefix=topic_prefix,
                )

                if not result:
                    mqtt_latency = {
                        "error": "MQTT latency test nevrátil žádná data (None / prázdný výsledek).",
                        "semafor": "unknown",
                        "badge": "secondary",
                        "sent": 0,
                        "received": 0,
                        "loss": "n/a",
                        "min_ms": None,
                        "avg_ms": None,
                        "p95_ms": None,
                        "max_ms": None,
                        "ok_ms": ok_ms,
                        "warn_ms": warn_ms,
                        "details": [],
                    }
                else:
                    sem, badge = _semafor_from_latency(
                        result.get("avg_ms"),
                        result.get("p95_ms"),
                        result.get("loss"),
                        ok_ms=ok_ms,
                        warn_ms=warn_ms
                    )
                    result["semafor"] = sem
                    result["badge"] = badge
                    result["ok_ms"] = ok_ms
                    result["warn_ms"] = warn_ms
                    mqtt_latency = result

            except Exception as e:
                mqtt_latency = {
                    "error": str(e),
                    "semafor": "bad",
                    "badge": "danger",
                    "sent": 0,
                    "received": 0,
                    "loss": "n/a",
                    "min_ms": None,
                    "avg_ms": None,
                    "p95_ms": None,
                    "max_ms": None,
                    "ok_ms": None,
                    "warn_ms": None,
                    "details": [],
                }

        elif action == "modbus_rtt":
            try:
                # Parametry z .env přes read_env_file
                port = env_values.get("MODBUS_IO_MODBUS_PORT", "/dev/ttyUSB0")
                baudrate = _to_int(env_values.get("MODBUS_IO_MODBUS_BAUDRATE", "9600"), 9600)
                parity = env_values.get("MODBUS_IO_MODBUS_PARITY", "N")
                stopbits = _to_int(env_values.get("MODBUS_IO_MODBUS_STOPBITS", "1"), 1)
                bytesize = _to_int(env_values.get("MODBUS_IO_MODBUS_BYTESIZE", "8"), 8)
                timeout_s = _to_float_safe(env_values.get("MODBUS_IO_MODBUS_TIMEOUT", "0.5"), 0.5)

                slaves_raw = env_values.get("MODBUS_IO_SLAVES", "")
                slaves = []
                for x in (slaves_raw or "").split(","):
                    x = (x or "").strip()
                    if x.isdigit():
                        slaves.append(int(x))

                samples = _to_int(env_values.get("MODBUS_IO_MODBUS_RTT_SAMPLES", "30"), 30)
                interval_ms = _to_int(env_values.get("MODBUS_IO_MODBUS_RTT_INTERVAL_MS", "50"), 50)
                method = env_values.get("MODBUS_IO_MODBUS_RTT_METHOD", "di")
                address = _to_int(env_values.get("MODBUS_IO_MODBUS_RTT_ADDR", "0"), 0)
                count = _to_int(env_values.get("MODBUS_IO_MODBUS_RTT_COUNT", "1"), 1)

                ok_ms = _to_float_safe(env_values.get("MODBUS_IO_MODBUS_RTT_OK_MS", "50"), 50.0)
                warn_ms = _to_float_safe(env_values.get("MODBUS_IO_MODBUS_RTT_WARN_MS", "150"), 150.0)

                modbus_rtt = get_modbus_rtt_test(
                    port=port,
                    baudrate=baudrate,
                    parity=parity,
                    stopbits=stopbits,
                    bytesize=bytesize,
                    timeout_s=timeout_s,
                    slaves=slaves,
                    samples=samples,
                    interval_ms=interval_ms,
                    address=address,
                    count=count,
                    method=method,
                    ok_ms=ok_ms,
                    warn_ms=warn_ms,
                )
            except Exception as e:
                modbus_rtt = {"error": str(e)}

    return render_template(
        "network.html",
        ping_results=ping_results,
        iperf_result=iperf_result,
        mqtt_latency=mqtt_latency,
        modbus_rtt=modbus_rtt,
        diag_cards=diag_cards,  # nove
        vnstat_stats=get_all_vnstat_stats(),
        default_targets=default_targets,
        iperf_ip=request.form.get("iperf_ip", "192.168.1.20") if request.method == "POST" else "192.168.1.20",
        duration=request.form.get("duration", 10) if request.method == "POST" else 10,
        title="Síťové testy",
        active_nav="network",
    )

@app.route("/service", methods=["GET"])
@login_required
def service_page():
    return render_template("service.html", title="Servis", active_nav="services")

@app.route("/mqtt-discovery", methods=["GET", "POST"])
@login_required
def mqtt_discovery():
    import os

    host = os.getenv("MODBUS_IO_MQTT_HOST", "192.168.1.20")
    port = int(os.getenv("MODBUS_IO_MQTT_PORT", "1883"))
    user = os.getenv("MODBUS_IO_MQTT_USERNAME", "")
    pwd  = os.getenv("MODBUS_IO_MQTT_PASSWORD", "")
    prefix = os.getenv("MODBUS_IO_HA_DISCOVERY_PREFIX", "homeassistant")

    items = []
    result = None
    error = None

    if request.method == "POST":
        app.logger.info("mqtt-discovery POST action=%s form=%s", request.form.get("action"), dict(request.form))
        app.logger.info("mqtt-discovery env host=%s port=%s prefix=%s", host, port, prefix)
        action = (request.form.get("action") or "").strip().lower()
        # fallback: když template neposílá action, bereme POST jako "list"
        if not action:
            action = "list"
        # UI používá scan => chovej se jako list
        if action == "scan":
            action = "list"    
        try:
            if action == "list":
                items = mqtt_list_retained_discovery(
                    host=host, port=port, username=user, password=pwd,
                    discovery_prefix=prefix,
                    contains = (request.form.get("contains") or "modbus_io").strip(),
                    window_s = float(request.form.get("window_s") or 1.5),
                    limit = int(request.form.get("limit") or 500),
                )
                app.logger.info("mqtt-discovery: loaded %s items", len(items))

            elif action == "delete":
                # textové pole: jeden topic na řádek
                raw = request.form.get("delete_topics", "")
                topics = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                result = mqtt_delete_retained(
                    host=host, port=port, username=user, password=pwd,
                    topics=topics,
                )
        except Exception as e:
            error = str(e)

    return render_template(
        "mqtt_discovery.html",
        items=items,
        result=result,
        error=error,
        mqtt_host=host,
        mqtt_port=port,
        title="MQTT Discovery – servis",
        active_nav="settings"
    )

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
    path = (LOG_FILE or "").strip()
    if not path:
        abort(404)

    path = os.path.abspath(path)
    if not os.path.isfile(path):
        abort(404)

    try:
        return send_file(
            path,
            as_attachment=True,
            attachment_filename=os.path.basename(path),
            mimetype="text/plain",
            conditional=True,
        )
    except PermissionError:
        abort(403)
    except Exception as e:
        current_app.logger.exception("logs_download failed: %s", e)
        abort(500)

@app.route("/api/service-status/<service_id>", methods=["GET"])
@login_required
def api_service_status(service_id):
    ok, state, err = is_active(service_id)
    if not ok and err:
        return jsonify({"state": "unknown", "error": err}), 400
    return jsonify({"state": state})

@app.route("/agenda/<agenda_id>", methods=["GET", "POST"])
@login_required
def agenda_env(agenda_id):
    if request.method == "POST":
        ok, ctx, msg = handle_agenda_post(agenda_id)
        if ok:
            flash(msg, "success")
            return redirect(url_for("agenda_env", agenda_id=agenda_id))
        flash(msg, "error")
        return render_template(
            "agenda_env.html",
            title=ctx["agenda"]["title"],
            active_nav="settings",     # <---
            active_agenda=agenda_id,   # <--- volitelné (hodí se pro submenu)
            **ctx
        )

    ok, ctx, msg = build_agenda_context(agenda_id)
    if not ok:
        flash(msg, "error")
        return redirect(url_for("index"))

    return render_template(
        "agenda_env.html",
        title=ctx["agenda"]["title"],
        active_nav="settings",       # <---
        active_agenda=agenda_id,     # <--- volitelné (hodí se pro submenu)
        **ctx
    )


if __name__ == "__main__":
    # pro vývoj; v produkci běží přes systemd
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
