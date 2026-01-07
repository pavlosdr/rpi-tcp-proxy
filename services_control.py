# services_control.py
import subprocess
from typing import Dict, Tuple, Optional, List

SYSTEMCTL = "/usr/bin/systemctl"
SUDO = "/usr/bin/sudo"

SERVICES_META: Dict[str, dict] = {
    # service_id : meta
    "ui": {
        "id": "ui",
        "pretty_name": "RPi Admin UI",
        "unit": "rpi-admin-ui.service",
        "description": (
            "Webové administrační rozhraní pro správu služeb, konfigurace "
            "a diagnostiku RPi."
        ),
        "icon": "bi bi-window",
        "route": "index",
        "agenda": "RPi UI",
        "has_mqtt": False,
        "has_diagnostics": False,
    },
    "mqtt-report": {
        "id": "mqtt-report",
        "pretty_name": "RPi Report MQTT",
        "unit": "rpi-mqtt-report.service",
        "description": (
            "Periodický reporting stavu RPi, sítě a proxy služeb do MQTT. "
            "Slouží jako watchdog a diagnostický zdroj pro Home Assistant."
        ),
        "icon": "bi bi-broadcast",
        "route": "services_page",
        "agenda": "RPi Report MQTT",
        "has_mqtt": True,
        "has_diagnostics": False,
    },
    "infigy-mqtt": {
        "id": "infigy-mqtt",
        "pretty_name": "RPi FVE Infigy MQTT",
        "unit": "infigy_ws_to_mqtt.service",
        "description": (
            "Napojení Infigy (websocket/API) na MQTT. "
            "Publikuje energetická data a stavové informace do Home Assistantu."
        ),
        "icon": "bi bi-plug",
        "route": "services_page",
        "agenda": "RPi FVE Infigy MQTT",
        "has_mqtt": True,
        "has_diagnostics": False,
    },
    "modbus-io-broker": {
        "id": "modbus-io-broker",
        "pretty_name": "RPi IO MODBUS–MQTT broker",
        "unit": "modbus_io_broker.service",  # UNDERSCORE jak píšeš
        "description": (
            "Broker pro čtení Modbus RTU vstupů (tlačítka, vypínače) "
            "a jejich publikaci do MQTT včetně Home Assistant discovery."
        ),
        "icon": "bi bi-diagram-3",
        "route": "io_modbus_mqtt",
        "agenda": "RPi IO MODBUS–MQTT broker",
        "has_mqtt": True,
        "has_diagnostics": True,
    },
    "modbus-proxy": {
        "id": "modbus-proxy",
        "pretty_name": "RPi FVE GoodWe MODBUS proxy",
        "unit": "modbus_tcp_proxy.service",
        "description": (
            "TCP proxy pro Modbus komunikaci mezi Home Assistantem "
            "a GoodWe měničem. Řeší nestandardní chování TID/UID a zajišťuje stabilitu spojení."
        ),
        "icon": "bi bi-hdd-network",
        "route": "services_page",
        "agenda": "RPi FVE GoodWe MODBUS proxy",
        "has_mqtt": False,
        "has_diagnostics": True,
    },
}

# WHITELIST je odvozený – žádná druhá ruční mapa
SERVICE_WHITELIST: Dict[str, str] = {sid: meta["unit"] for sid, meta in SERVICES_META.items()}


def get_meta(service_id: str) -> Optional[dict]:
    return SERVICES_META.get(service_id)


def resolve_unit(service_id: str) -> str:
    """Jediný povolený překlad service_id -> systemd unit. ŽÁDNÝ fallback."""
    sid = (service_id or "").strip()
    return SERVICE_WHITELIST.get(sid, "")

def _require_service(service_id: str) -> Tuple[bool, str, str]:
    sid = (service_id or "").strip()
    unit = resolve_unit(sid)
    if not unit:
        return False, f"Služba '{sid}' není povolena.", ""
    return True, "", unit


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

def _systemctl(action: str, unit: str, *, use_sudo: bool) -> subprocess.CompletedProcess:
    # --no-ask-password zabrání případnému "čekání" na auth prompt
    base = [SYSTEMCTL, "--no-ask-password", action, unit]
    if use_sudo:
        base = [SUDO, "-n"] + base
    return _run(base)

def is_active(service_id: str) -> Tuple[bool, str, str]:
    ok, msg, unit = _require_service(service_id)
    if not ok:
        return False, "unknown", msg

    r = _run(["systemctl", "is-active", unit])
    state = (r.stdout or "").strip() or "unknown"
    if r.returncode != 0 and state == "unknown":
        return False, state, r.stdout.strip()
    return True, state, ""


def restart_service_safe(service_id: str) -> Tuple[bool, str]:
    ok, msg, unit = _require_service(service_id)
    if not ok:
        return False, msg

    r = _systemctl("restart", unit, use_sudo=True)
    if r.returncode == 0:
        return True, f"Služba '{unit}' byla restartována."
    return False, f"Chyba při restartu služby '{unit}': {r.stdout.strip()}"


def start_service_safe(service_id: str) -> Tuple[bool, str]:
    ok, msg, unit = _require_service(service_id)
    if not ok:
        return False, msg

    r = _systemctl("start", unit, use_sudo=True)
    if r.returncode == 0:
        return True, f"Služba '{unit}' byla spuštěna."
    return False, f"Chyba při startu služby '{unit}': {r.stdout.strip()}"


def stop_service_safe(service_id: str) -> Tuple[bool, str]:
    ok, msg, unit = _require_service(service_id)
    if not ok:
        return False, msg

    r = _systemctl("stop", unit, use_sudo=True)
    if r.returncode == 0:
        return True, f"Služba '{unit}' byla zastavena."
    return False, f"Chyba při zastavení služby '{unit}': {r.stdout.strip()}"

def get_service_detail(service_id: str, journal_lines: int = 200) -> Tuple[str, str, Optional[str], str]:
    """
    Vrací: (status_out, journal_out, err, unit)

    - unit je vždy jen z whitelistu
    - inactive service není chyba (systemctl status může mít returncode != 0)
    - chyba je jen když unit není povolena, nebo "Unit ... could not be found."
    """
    ok, msg, unit = _require_service(service_id)
    if not ok:
        return "", "", msg, ""

    # systemctl status (nezabíjet to na returncode != 0)
    r1 = _run(["systemctl", "status", unit, "--no-pager", "--full"])
    status_out = (r1.stdout or "").strip()

    # skutečná chyba: unit nenalezena
    if "could not be found" in status_out.lower():
        return "", "", (status_out or f"Unit {unit} could not be found."), unit

    # journalctl (když selže, vrátíme co jde)
    r2 = _run(["journalctl", "-u", unit, "-n", str(journal_lines), "--no-pager", "--output=short-iso"])
    journal_out = (r2.stdout or "").strip()

    return status_out, journal_out, None, unit