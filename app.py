"""
rpi-admin-ui web application

Hlavní Flask aplikace poskytující webové rozhraní
pro správu služeb, monitoring a konfiguraci Raspberry Pi.

Funkce:
- Web UI (status, start/stop služeb)
- Zobrazení agend a jejich stavu
- Autentizace uživatele
- Integrace s monitor.py a agendas.py

Spouštěno jako hlavní entry-point aplikace.
"""
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from dotenv import load_dotenv
import os
import logging
import sys
import time
import threading
import datetime as dt
from typing import Dict
from mqtt_tools import (
    mqtt_list_retained_discovery, 
    _resolve_device_id_for_service,
    _get_mqtt_conn_from_env,
    mqtt_cleanup_discovery_for_device
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
)
from services_control import (
    SERVICES_META,
    MQTT_DISCOVERY_TARGETS,
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
from envfile import env_str, env_int

# ---------------------- KONFIGURACE ---------------------- #
# service_id, které restartují přímo tuto webovou appku (pro odložený restart)
UI_SERVICE_IDS = {"ui"}  # případně přidej aliasy
# ------------------------ .env --------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # načti .env ze stejného adresáře
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

# ------------------- Konfig z .env ----------------------- #
PORT   = env_int("UI_PORT",8080)
# MODBUS LOG_PKT FILE pro službu rpi-tcp-proxy
LOG_FILE_PKT = env_str("LOG_FILE", "/var/log/modbus_proxy.log")

# ---------------------- Logging ---------------------------
# Log minimization
LOG_LEVEL = getattr(logging, env_str("UI_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
    force=True,  # přepíše předchozí konfigurace (Flask/werkzeug to občas nastaví dřív)
)
logger = logging.getLogger("ui_")
logger.setLevel(LOG_LEVEL)
# Utišení příliš ukecaného werkzeug
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ---------------------- LOG runtime ---------------------- #
logger.debug("__file__ running from: %s", __file__)
logger.debug("PYTHON: %s", sys.executable)


# --------------------Flask app logger -------------------- #
app = Flask(__name__)
app.logger.handlers.clear()
app.logger.propagate = True
app.logger.setLevel(LOG_LEVEL)
app.secret_key = os.getenv("UI_SECRET", "change-me")

# ----------------------- helpers ------------------------- #
def _restart_ui_delayed(service_id: str, delay_s: float = 0.7) -> None:
    """
    Odložený restart – nechá doběhnout HTTP response (redirect + flash),
    potom teprve provede restart služby.
    """
    try:
        time.sleep(float(delay_s))
        ok, msg = restart_service_safe(service_id)
        if ok:
            logger.info("UI delayed restart OK service_id=%s msg=%s", service_id, msg)
        else:
            logger.warning("UI delayed restart FAIL service_id=%s msg=%s", service_id, msg)
    except Exception:
        logger.exception("UI delayed restart exception service_id=%s", service_id)
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
    logger.info("UI action=restart service_id=%s", service_id)

    # Speciální režim pro restart samotného UI
    if service_id in UI_SERVICE_IDS:
        flash("Restartuji UI... za pár sekund obnov stránku.", "success")
        threading.Thread(
            target=_restart_ui_delayed,
            args=(service_id, 0.7),
            name="ui-restart-delayed",
            daemon=True,
        ).start()

        # Redirect pryč z /restart/... aby prohlížeč nebyl “na mrtvém endpointu”
        return redirect(url_for("services_page"))

    # Ostatní služby – původní chování
    ok, msg = restart_service_safe(service_id)
    if ok:
        logger.info("UI restart OK service_id=%s msg=%s", service_id, msg)
    else:
        logger.warning("UI restart FAIL service_id=%s msg=%s", service_id, msg)
    flash(msg, "success" if ok else "error")
    return redirect(request.referrer or url_for("services_page"))


@app.route("/start/<service_id>", methods=["POST"])
@login_required
def start_service(service_id):
    logger.info("UI action=start service_id=%s", service_id)
    ok, msg = start_service_safe(service_id)
    if ok:
        logger.info("UI start OK service_id=%s msg=%s", service_id, msg)
    else:
        logger.warning("UI start FAIL service_id=%s msg=%s", service_id, msg)
    flash(msg, "success" if ok else "error")
    return redirect(request.referrer or url_for("services_page"))


@app.route("/stop/<service_id>", methods=["POST"])
@login_required
def stop_service(service_id):
    logger.info("UI action=stop service_id=%s", service_id)
    ok, msg = stop_service_safe(service_id)
    if ok:
        logger.info("UI stop OK service_id=%s msg=%s", service_id, msg)
    else:
        logger.warning("UI stop FAIL service_id=%s msg=%s", service_id, msg)
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
    logger.debug("UI service_detail service_id=%s journal_lines=%s log_lines=%s",
             service_id, journal_lines, log_lines)
    key = resolve_service_key(service_id)

    meta = get_meta(key) or {
        "pretty_name": key,
        "unit": "",
        "description": "",
        "icon": "",
    }

    ok, state, err = is_active(key)
    if not ok and err:
        logger.warning("UI service_detail is_active failed key=%s err=%s", key, err)
        flash(err, "error")
        return redirect(url_for("services_page"))

    status_out, journal_out, err2, unit = get_service_detail(key, journal_lines=journal_lines)
    if err2:
        logger.warning("UI service_detail get_service_detail failed key=%s unit=%s err=%s", key, unit, err2)
        flash(err2, "error")
        return redirect(url_for("services_page"))

    # service pro templaty: kanonický id = service_id
    m = dict(meta)
    m["key"] = key
    m["service_key"] = key
    m["unit"] = unit
    m["state"] = state

    # -------------------
    # LOG do suboru pouze pro modbus-proxy
    # -------------------
    log_enabled = (key == "modbus-proxy")

    log_path = (LOG_FILE_PKT or "").strip() if log_enabled else ""
    log_exists = bool(log_path and os.path.exists(log_path))
    log_size = os.path.getsize(log_path) if log_exists else 0
    log_out = tail_file(log_path, log_lines) if log_exists else ""
    
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
        log_lines=log_lines,
    )

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        if check_credentials(request.form.get("username"), request.form.get("password")):
            session["authenticated"] = True
            logger.info("UI login OK username=%s", username)
            return redirect(url_for("index"))
        logger.warning("UI login FAIL username=%s", username)
        flash("Neplatné přihlašovací údaje", "error")
    return render_template("login.html", title="Přihlášení", active_nav="dashboard")

@app.route("/logout")
def logout():
    logger.info("UI logout")
    session.clear()
    return redirect(url_for("login"))

@app.route("/network", methods=["GET", "POST"])
@login_required
def network():
    import ipaddress

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
            logger.info("UI network action=ping targets=%s", ip_list)
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
            logger.info("UI network action=iperf target=%s duration=%s", iperf_ip, duration)
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
                logger.exception("UI network action=mqtt_latency failed")
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
                logger.exception("UI network action=modbus_rtt failed")
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

@app.route("/settings/mqtt-discovery", methods=["GET"])
@login_required
def settings_mqtt_discovery_page():
    # default = první položka v mapě (nebo prázdno)
    default_service = next(iter(MQTT_DISCOVERY_TARGETS.keys()), "")
    service_key = (request.args.get("service") or default_service or "").strip().lower()

    # Form defaults
    contains = (request.args.get("contains") or "").strip()
    window_s = str(request.args.get("window_s") or "2.0").strip()
    limit = str(request.args.get("limit") or "500").strip()

    # Targets pro select
    targets = []
    for k, meta in MQTT_DISCOVERY_TARGETS.items():
        c = _get_mqtt_conn_from_env(k)
        dev_id = (c.get("device_id") or "").strip()

        # fallback pro kompatibilitu (pokud resolver existuje)
        if not dev_id:
            try:
                dev_id = (_resolve_device_id_for_service(k) or "").strip()
            except Exception:
                dev_id = ""

        targets.append({"key": k, "label": meta.get("label", k), "device_id": dev_id})

    # Preview conn pro aktuálně vybranou službu
    conn = _get_mqtt_conn_from_env(service_key)
    device_id = (conn.get("device_id") or "").strip()
    if not device_id:
        try:
            device_id = (_resolve_device_id_for_service(service_key) or "").strip()
        except Exception:
            device_id = ""

    conn_preview = {
        "service": service_key,
        "device_id": device_id,
        "host": conn.get("host", ""),
        "port": conn.get("port", ""),
        "discovery_prefix": conn.get("discovery_prefix", "homeassistant"),
        "username_set": bool(conn.get("username", "")),
    }

    if service_key and not device_id:
        flash(
            f"Pro sluzbu '{service_key}' chybi DEVICE_ID v .env "
            f"(chybi odpovidajici *_MQTT_DEVICE_ID).",
            "warning",
        )

    # GET jen vykreslí stránku; items se načítají POSTem /list
    return render_template(
        "settings_mqtt_discovery.html",
        targets=targets,
        selected=service_key,
        contains=contains,
        window_s=window_s,
        limit=limit,
        items=[],
        stats=None,
        conn_preview=conn_preview,  # <- nové: použij v šabloně
    )



@app.route("/settings/mqtt-discovery/list", methods=["POST"])
@login_required
def settings_mqtt_discovery_list():
    service_key = (request.form.get("service") or "").strip().lower()
    contains = (request.form.get("contains") or "").strip()
    window_s = float(request.form.get("window_s") or "2.0")
    limit = int(request.form.get("limit") or "500")

    conn = _get_mqtt_conn_from_env(service_key)

    # DEVICE_ID: preferuj z conn (z mapy), fallback na legacy resolver pokud existuje
    device_id = (conn.get("device_id") or "").strip()
    if not device_id:
        try:
            device_id = (_resolve_device_id_for_service(service_key) or "").strip()
        except Exception:
            device_id = ""

    if not device_id:
        flash(
            f"Pro sluzbu '{service_key}' nemam DEVICE_ID v .env "
            f"(chybi odpovidajici *_MQTT_DEVICE_ID).",
            "error",
        )
        return redirect(url_for("settings_mqtt_discovery_page", service=service_key))

    try:
        items = mqtt_list_retained_discovery(
            host=conn["host"],
            port=int(conn["port"]),
            username=conn.get("username", ""),
            password=conn.get("password", ""),
            discovery_prefix=conn.get("discovery_prefix", "homeassistant"),
            device_id=device_id,
            contains=contains,
            window_s=window_s,
            limit=limit,
        )
        flash(f"Nacteno {len(items)} retained discovery topicu pro device_id={device_id}.", "success")
        stats = {"service": service_key, "device_id": device_id, "found": len(items), "deleted": 0}
    except Exception as e:
        flash(f"Chyba pri nacitani retained discovery: {e}", "error")
        items = []
        stats = None

    # priprav data pro select (ať je 1 zdroj pravdy = _get_mqtt_conn_from_env)
    targets = []
    for k, meta in MQTT_DISCOVERY_TARGETS.items():
        c = _get_mqtt_conn_from_env(k)
        dev_id = (c.get("device_id") or "").strip()
        if not dev_id:
            try:
                dev_id = (_resolve_device_id_for_service(k) or "").strip()
            except Exception:
                dev_id = ""
        targets.append({"key": k, "label": meta.get("label", k), "device_id": dev_id})

    return render_template(
        "settings_mqtt_discovery.html",
        targets=targets,
        selected=service_key,
        contains=contains,
        window_s=str(window_s),
        limit=str(limit),
        items=items,
        stats=stats,
    )


@app.route("/settings/mqtt-discovery/cleanup", methods=["POST"])
@login_required
def settings_mqtt_discovery_cleanup():
    service_key = (request.form.get("service") or "").strip().lower()
    contains = (request.form.get("contains") or "").strip()
    window_s = float(request.form.get("window_s") or "2.0")
    limit = int(request.form.get("limit") or "2000")

    # jednotný zdroj pravdy pro MQTT připojení + device_id (dle service_key)
    conn = _get_mqtt_conn_from_env(service_key)

    device_id = (conn.get("device_id") or "").strip()
    if not device_id:
        # fallback pro kompatibilitu, pokud máš resolver
        try:
            device_id = (_resolve_device_id_for_service(service_key) or "").strip()
        except Exception:
            device_id = ""

    if not device_id:
        flash(
            f"Pro sluzbu '{service_key}' nemam DEVICE_ID v .env "
            f"(chybi odpovidajici *_MQTT_DEVICE_ID).",
            "error",
        )
        return redirect(url_for("settings_mqtt_discovery_page", service=service_key))

    try:
        res = mqtt_cleanup_discovery_for_device(
            host=conn["host"],
            port=conn["port"],
            username=conn.get("username", ""),
            password=conn.get("password", ""),
            discovery_prefix=conn.get("discovery_prefix", "homeassistant"),
            device_id=device_id,
            contains=contains,
            window_s=window_s,
            limit=limit,
        )
        flash(
            f"Smazano retained discovery: deleted={res.get('deleted')} "
            f"(found={res.get('found')}) pro device_id={device_id}.",
            "success",
        )
    except Exception as e:
        flash(f"Chyba pri mazani retained discovery: {e}", "error")

    # po cleanupu návrat na stránku
    return redirect(url_for("settings_mqtt_discovery_page", service=service_key, contains=contains))


@app.route("/api/service-status/<service_id>", methods=["GET"])
@login_required
def api_service_status(service_id):
    ok, state, err = is_active(service_id)
    if not ok and err:
        logger.debug("UI api_service_status failed service_id=%s err=%s", service_id, err)
        return jsonify({"state": "unknown", "error": err}), 400
    return jsonify({"state": state})

@app.route("/agenda/<agenda_id>", methods=["GET", "POST"])
@login_required
def agenda_env(agenda_id):
    if request.method == "POST":
        logger.info("UI agenda_env POST agenda_id=%s", agenda_id)
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
        logger.warning("UI agenda_env POST failed agenda_id=%s msg=%s", agenda_id, msg)
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
    logger.info("UI starting dev server host=0.0.0.0 port=%s", PORT)
    app.run(host="0.0.0.0", port=PORT)
