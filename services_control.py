"""
Systemd services control (RPi Admin UI)

Modul pro bezpečné ovládání vybraných systemd služeb z webového UI.

Co dělá:
- Definuje katalog služeb (SERVICES_META) používaný v UI (název, unit, popis, ikonka)
- Z katalogu vytvoří whitelist (SERVICE_WHITELIST) a validuje duplicity / chybné unity
- Normalizuje vstupní service_id (resolve_service_key) pro kompatibilitu:
  - kanonický klíč (např. "ui", "mqtt-report")
  - unit ("rpi-admin-ui.service")
  - base unit bez .service ("rpi-admin-ui")
  - legacy meta["id"] (fallback)
- Spouští systemctl příkazy pouze z povolené množiny akcí (_ALLOWED_SYSTEMCTL_ACTIONS)
  a bezpečně (sudo -n, --no-ask-password, timeout, stdout/stderr sloučeno)

Veřejné funkce (používá UI):
- start_service_safe(), stop_service_safe(), restart_service_safe()
- is_active()
- get_service_detail() (systemctl status + journalctl -u)

Bezpečnostní zásady:
- Nelze ovládat libovolnou službu: pouze služby definované v SERVICES_META
- Nelze provést libovolnou akci: pouze akce z _ALLOWED_SYSTEMCTL_ACTIONS
- Bez interaktivních promptů: používá sudo -n a systemctl --no-ask-password

Poznámky:
- Tento modul neřeší autentizaci UI (to je v auth/app vrstvě).
- Při timeoutu systemctl vrací returncode 124 a text "Timeout..." místo pádu aplikace.
"""

import subprocess
from typing import Dict, Tuple, Optional, Sequence
from envfile import SUDO, SYSTEMCTL

_ALLOWED_SYSTEMCTL_ACTIONS = {
    "start",
    "stop",
    "restart",
    "reload",
    "status",
    "is-active",
    "is-enabled",
    "enable",
    "disable",
    "daemon-reload",
}

SERVICES_META: Dict[str, dict] = {
    # key = kanonický service_key (používá se všude v routách)
    "ui": {
        "pretty_name": "RPi Admin UI",
        "unit": "rpi-admin-ui.service",
        "description": (
            "Webové administrační rozhraní pro správu služeb, konfigurace "
            "a diagnostiku RPi."
        ),
        "icon": "bi bi-window",
    },

    "mqtt-report": {
        "pretty_name": "RPi Report MQTT",
        "unit": "rpi-mqtt-report.service",
        "description": (
            "Periodický reporting stavu RPi, sítě a proxy služeb do MQTT. "
            "Slouží jako watchdog a diagnostický zdroj pro Home Assistant."
        ),
        "icon": "bi bi-broadcast",
    },

    "infigy-mqtt": {
        "pretty_name": "RPi FVE Infigy MQTT",
        "unit": "infigy_ws_to_mqtt.service",
        "description": (
            "Napojení Infigy (websocket/API) na MQTT. "
            "Publikuje energetická data a stavové informace do Home Assistantu."
        ),
        "icon": "bi bi-plug",
    },

    "modbus-io-broker": {
        "pretty_name": "RPi IO MODBUS–MQTT broker",
        "unit": "modbus_io_broker.service",
        "description": (
            "Broker pro čtení Modbus RTU vstupů (tlačítka, vypínače) "
            "a jejich publikaci do MQTT včetně Home Assistant discovery."
        ),
        "icon": "bi bi-diagram-3",
    },

    "modbus-proxy": {
        "pretty_name": "RPi FVE GoodWe MODBUS proxy",
        "unit": "modbus_tcp_proxy.service",
        "description": (
            "TCP proxy pro Modbus komunikaci mezi Home Assistantem "
            "a GoodWe měničem. Řeší nestandardní chování TID/UID a zajišťuje stabilitu spojení."
        ),
        "icon": "bi bi-hdd-network",
    },

    "ha-watchdog": {
        "pretty_name": "RPi Home Assistant Watchdog",
        "unit": "ha_watchdog.service",
        "description": (
            "Watchdog běžící na Raspberry Pi, který hlídá dostupnost Home Assistant (HA OS) "
            "na síti (např. 192.168.1.20). Při opakovaném výpadku může přes SSH provést restart "
            "HA a současně umí publikovat stav do MQTT (bridge/* dle infigy normy) a posílat "
            "notifikace na Telegram."
        ),
        "icon": "bi bi-shield-check",
    },    
}

MQTT_DISCOVERY_TARGETS = {
    "infigy": {
        "device_id_env": "INFIGY_MQTT_DEVICE_ID",
        "label": "Infigy bridge",
        "host_env": "INFIGY_MQTT_HOST",
        "port_env": "INFIGY_MQTT_PORT",
        "username_env": "INFIGY_MQTT_USER",
        "password_env": "INFIGY_MQTT_PASS",
        "discovery_prefix_env": "INFIGY_MQTT_DISCOVERY_PREFIX", 
        "base_topic_env": "INFIGY_MQTT_BASE",
        "client_id_env": "INFIGY_MQTT_CLIENT_ID",
    },
    "report": {
        "device_id_env": "MQTT_REPORT_DEVICE_ID",
        "label": "RPi report",
        "host_env": "MQTT_REPORT_HOST",
        "port_env": "MQTT_REPORT_PORT",
        "username_env": "MQTT_REPORT_USER",
        "password_env": "MQTT_REPORT_PASS",
        "discovery_prefix_env": "MQTT_REPORT_DISCOVERY_PREFIX",
        "base_topic_env": "MQTT_REPORT_BASE_TOPIC",
        "client_id_env": "MQTT_REPORT_CLIENT_ID",
    },
    "modbus_io": {
        "device_id_env": "MODBUS_IO_MQTT_DEVICE_ID",
        "label": "Modbus IO broker",
        "host_env": "MODBUS_IO_MQTT_HOST",
        "port_env": "MODBUS_IO_MQTT_PORT",
        "username_env": "MODBUS_IO_MQTT_USERNAME",
        "password_env": "MODBUS_IO_MQTT_PASSWORD",
        "discovery_prefix_env": "MODBUS_IO_HA_DISCOVERY_PREFIX",
        "base_topic_env": "MODBUS_IO_MQTT_BASE_TOPIC",
        "client_id_env": "MODBUS_IO_MQTT_CLIENT_ID",
    },
}

def build_service_whitelist(services_meta: dict[str, dict]) -> dict[str, str]:
    whitelist: dict[str, str] = {}
    units_seen: set[str] = set()

    for sid, meta in services_meta.items():
        unit = (meta.get("unit") or "").strip()

        if not unit:
            raise ValueError(f"SERVICE_META['{sid}'] nemá definovaný 'unit'")

        if not unit.endswith(".service"):
            raise ValueError(
                f"SERVICE_META['{sid}'].unit musí končit na '.service' (je: {unit})"
            )

        if unit in units_seen:
            raise ValueError(f"Duplicita systemd unit '{unit}' v SERVICES_META")

        units_seen.add(unit)
        whitelist[sid] = unit

    return whitelist

SERVICE_WHITELIST: dict[str, str] = build_service_whitelist(SERVICES_META)


def get_meta(service_key: str) -> Optional[dict]:
    """Vrátí meta podle kanonického klíče v SERVICES_META."""
    return SERVICES_META.get(service_key)

def _normalize_unit_base(unit: str) -> str:
    u = (unit or "").strip()
    if u.lower().endswith(".service"):
        return u[:-8]
    return u

def resolve_service_key(service_id: str) -> str:
    """
    Převod vstupního service_id na kanonický klíč v SERVICES_META.

    Akceptované vstupy (kvůli kompatibilitě):
      - přímo klíč v SERVICES_META (preferované)
      - "unit" (např. "modbus_tcp_proxy.service")
      - "unit base" bez .service (např. "modbus_tcp_proxy")
      - legacy meta["id"] (jen fallback pro staré odkazy)
    """
    sid = (service_id or "").strip()
    if not sid:
        return sid

    # 1) Přímý klíč
    if sid in SERVICES_META:
        return sid

    # 2) Shoda podle unit / unit base
    for k, m in (SERVICES_META or {}).items():
        unit = str((m or {}).get("unit", "")).strip()
        if not unit:
            continue
        base = _normalize_unit_base(unit)
        if sid == unit or sid == base or sid == f"{base}.service":
            return k

    # 3) Legacy: shoda podle meta["id"] (jen kompatibilita)
    for k, m in (SERVICES_META or {}).items():
        mid = str((m or {}).get("id", "")).strip()
        if mid and sid == mid:
            return k

    # fallback (nepovolené služby stejně zablokuje _require_service)
    return sid

def resolve_unit(service_id: str) -> str:
    return SERVICE_WHITELIST.get(service_id, "")

def _require_service(service_id: str) -> Tuple[bool, str, str]:
    """
    Vrací: (ok, msg, unit)

    - whitelist je SERVICES_META
    - kanonický identifikátor je klíč v SERVICES_META
    - unit se bere primárně z meta["unit"]
    """
    key = resolve_service_key(service_id)
    meta = get_meta(key)
    if not meta:
        return False, f"Služba '{service_id}' není povolena.", ""

    unit = str(meta.get("unit", "")).strip()
    if not unit:
        return False, f"Služba '{key}' nemá definovaný systemd unit.", ""

    return True, "", unit


def _run(cmd: Sequence[str], timeout: Optional[int] = 30) -> subprocess.CompletedProcess[str]:
    t = timeout
    if t is not None and t <= 0:
        t = None  # <=0 bereme jako "bez timeoutu"

    return subprocess.run(
        list(cmd),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=t,
        check=False,
    )

def _systemctl(action: str, unit: str, *, use_sudo: bool) -> subprocess.CompletedProcess[str]:
    """
    Spustí systemctl bezpečně:
    - bez interaktivních promptů (sudo -n, --no-ask-password)
    - text stdout/stderr sloučené do stdout přes _run()
    - odchyt TimeoutExpired -> vrátí CompletedProcess s returncode 124
    """
    a = (action or "").strip()
    u = (unit or "").strip()

    if not a:
        raise ValueError("systemctl action je prázdná")
    if a not in _ALLOWED_SYSTEMCTL_ACTIONS:
        raise ValueError(f"Nepovolená systemctl akce: {a}")
    if not u:
        raise ValueError("systemctl unit je prázdná")

    # --no-ask-password zabrání případnému čekání na auth prompt
    cmd = [SYSTEMCTL, "--no-ask-password", a, u]
    if use_sudo:
        cmd = [SUDO, "-n"] + cmd

    try:
        return _run(cmd)
    except subprocess.TimeoutExpired as e:
        # napodobíme CompletedProcess, aby UI a logika nespadly
        out = f"Timeout při spuštění: {' '.join(cmd)}"
        if getattr(e, "stdout", None):
            try:
                out += "\n" + str(e.stdout)
            except Exception:
                pass
        return subprocess.CompletedProcess(args=cmd, returncode=124, stdout=out, stderr=None)

def is_active(service_id: str) -> Tuple[bool, str, str]:
    ok, msg, unit = _require_service(service_id)
    if not ok:
        return False, "unknown", msg

    r = _run(["systemctl", "is-active", unit])
    state = (r.stdout or "").strip() or "unknown"
    if r.returncode != 0 and state == "unknown":
        return False, state, (r.stdout or "").strip()
    return True, state, ""


def restart_service_safe(service_key: str) -> Tuple[bool, str]:
    ok, msg, unit = _require_service(service_key)
    if not ok:
        return False, msg

    r = _systemctl("restart", unit, use_sudo=True)
    out = (r.stdout or "").strip()
    if r.returncode == 0:
        return True, f"Služba '{unit}' byla restartována."
    return False, f"Chyba při restartu služby '{unit}': {out}"


def start_service_safe(service_key: str) -> Tuple[bool, str]:
    ok, msg, unit = _require_service(service_key)
    if not ok:
        return False, msg

    r = _systemctl("start", unit, use_sudo=True)
    out = (r.stdout or "").strip()
    if r.returncode == 0:
        return True, f"Služba '{unit}' byla spuštěna."
    return False, f"Chyba při startu služby '{unit}': {out}"


def stop_service_safe(service_key: str) -> Tuple[bool, str]:
    ok, msg, unit = _require_service(service_key)
    if not ok:
        return False, msg

    r = _systemctl("stop", unit, use_sudo=True)
    out = (r.stdout or "").strip()
    if r.returncode == 0:
        return True, f"Služba '{unit}' byla zastavena."
    return False, f"Chyba při zastavení služby '{unit}': {out}"

def get_service_detail(service_id: str, journal_lines: int = 200) -> Tuple[str, str, Optional[str], str]:
    """
    Vrací: (status_out, journal_out, err, unit)

    - unit je vždy jen z whitelistu (SERVICES_META)
    - inactive service není chyba (systemctl status může mít returncode != 0)
    - chyba je jen když unit není povolena, nebo "Unit ... could not be found."
    """
    ok, msg, unit = _require_service(service_id)
    if not ok:
        return "", "", msg, ""

    r1 = _run(["systemctl", "status", unit, "--no-pager", "--full"])
    status_out = (r1.stdout or "").strip()

    if "could not be found" in status_out.lower():
        return "", "", (status_out or f"Unit {unit} could not be found."), unit

    r2 = _run(["journalctl", "-u", unit, "-n", str(journal_lines), "--no-pager", "--output=short"])
    journal_out = (r2.stdout or "").strip()

    return status_out, journal_out, None, unit