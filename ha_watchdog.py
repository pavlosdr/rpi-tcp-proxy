"""
Home Assistant watchdog & auto-recovery

Watchdog služba pro dohled nad Home Assistantem a souvisejícími službami.
Při detekci nedostupnosti provádí automatický restart vybraných systemd služeb
a publikuje stav do MQTT pro Home Assistant.

Hlavní úkol:
- Detekovat nedostupnost Home Assistantu (API / HTTP / ping)
- Automaticky restartovat HA nebo navázané služby při výpadku
- Zabránit „tichým“ zablokovaným stavům bez zásahu uživatele

Další funkce:
- Publikace stavových MQTT senzorů a binary_senzorů
- Watchdog logika (timeouty, debounce, retry)
- Integrace do Home Assistantu přes MQTT Discovery

Určeno pro běh jako systemd service.
"""

import os
import time
import socket
import logging
import threading
import subprocess
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from envfile import env_str, env_int, env_bool

# ------------------------ .env --------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

# ---------------------- Logging --------------------------- #
LOG_LEVEL = getattr(logging, env_str("HA_WD_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger("ha_watchdog")
logger.setLevel(LOG_LEVEL)

# v INFO nechceme spamovat debug věcmi
DEBUG = LOG_LEVEL <= logging.DEBUG

# ------------------- Config (.env) ------------------------ #
ENABLED = env_bool("HA_WD_ENABLED", True)
# Target
HA_HOST = env_str("HA_WD_HA_HOST", "192.168.1.20").strip()
POLL_S = env_int("HA_WD_POLL_S", 10)
FAIL_COUNT = env_int("HA_WD_FAIL_COUNT", 6)
RECOVER_COUNT = env_int("HA_WD_RECOVER_COUNT", 2)
# SSH HA
SSH_USER = env_str("HA_WD_SSH_USER", "root").strip()
SSH_PORT = env_int("HA_WD_SSH_PORT", 22)
SSH_CONNECT_TIMEOUT_S = env_int("HA_WD_SSH_CONNECT_TIMEOUT_S", 5)
SSH_CMD = env_str("HA_WD_HA_RESTART_CMD", "ha host reboot").strip()
# Telegram
TG_ENABLED = env_bool("HA_WD_TELEGRAM_ENABLED", True)
TG_BOT_TOKEN = env_str("HA_WD_TELEGRAM_TOKEN", "").strip()
TG_CHAT_ID = env_str("HA_WD_TELEGRAM_CHAT_ID", "").strip()
TG_PREFIX = env_str("HA_WD_TELEGRAM_PREFIX", "RPi Watchdog").strip()
TG_TIMEOUT_S = env_int("HA_WD_TELEGRAM_TIMEOUT_S", 6)
# Anti-spam
NOTIFY_COOLDOWN_S = env_int("HA_WD_NOTIFY_COOLDOWN_S", 300)
UI_RESTART_COOLDOWN_S = env_int("HA_WD_UI_RESTART_COOLDOWN_S", 120)
HA_RESTART_COOLDOWN_S = env_int("HA_WD_HA_RESTART_COOLDOWN_S", 600)

# ------------------ Singleton lock ------------------------ #
_singleton = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
try:
    _singleton.bind("\0ha_watchdog.singleton")
except OSError:
    logger.warning("Another ha_watchdog instance is running. Exiting.")
    raise SystemExit(1)

# ------------------ Runtime state ------------------------- #
stop_evt = threading.Event()

# "status" = up/down
status_lock = threading.Lock()
status: str = "unknown"  # unknown|up|down

# counters
fail_streak = 0
ok_streak = 0

# anti-spam timestamps
ts_lock = threading.Lock()
last_notify: Dict[str, float] = {}
last_restart_ui_ts: float = 0.0
last_restart_ha_ts: float = 0.0

# ------------------ Helpers ------------------------------- #
def _now() -> float:
    return time.time()

def _cooldown_ok(key: str, cooldown_s: int) -> bool:
    with ts_lock:
        t = float(last_notify.get(key, 0.0))
    return (_now() - t) >= float(cooldown_s)

def _mark_notified(key: str) -> None:
    with ts_lock:
        last_notify[key] = _now()

def _ui_restart_cooldown_ok() -> bool:
    with ts_lock:
        t = float(last_restart_ui_ts)
    return (_now() - t) >= float(UI_RESTART_COOLDOWN_S)

def _mark_ui_restart() -> None:
    global last_restart_ui_ts
    with ts_lock:
        last_restart_ui_ts = _now()

def _ha_restart_cooldown_ok() -> bool:
    with ts_lock:
        t = float(last_restart_ha_ts)
    return (_now() - t) >= float(HA_RESTART_COOLDOWN_S)

def _mark_ha_restart() -> None:
    global last_restart_ha_ts
    with ts_lock:
        last_restart_ha_ts = _now()

def tcp_ping(host: str, port: int = 8123, timeout_s: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout_s)):
            return True
    except Exception:
        return False

def send_telegram(text: str) -> bool:
    if not (TG_ENABLED and TG_BOT_TOKEN and TG_CHAT_ID):
        return False
    try:
        msg = f"{TG_PREFIX}: {text}"
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT_ID,
            "text": msg,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=float(TG_TIMEOUT_S)) as resp:
            _ = resp.read()
        return True
    except Exception as e:
        if DEBUG:
            logger.exception("Telegram send failed")
        else:
            logger.warning("Telegram send failed: %r", e)
        return False

def ssh_restart_ha() -> bool:
    """
    Restart HA přes SSH: ssh root@HA 'ha host reboot'
    """
    cmd = [
        "/usr/bin/ssh",
        "-p", str(int(SSH_PORT)),
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={int(SSH_CONNECT_TIMEOUT_S)}",
        f"{SSH_USER}@{HA_HOST}",
        SSH_CMD,
    ]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20, check=False)
        if p.returncode == 0:
            return True
        logger.warning("SSH restart failed rc=%s stderr=%s", p.returncode, (p.stderr or "").strip())
        return False
    except Exception as e:
        logger.warning("SSH restart exception: %r", e)
        return False

def set_status(new_status: str) -> None:
    global status
    with status_lock:
        status = new_status

def get_status() -> str:
    with status_lock:
        return status
    
def restart_service_with_grace(
    service_name: str,
    grace_s: float = 2.0,
) -> bool:
    """
    Restartuje systemd službu a počká grace period,
    aby web/UI stihlo naběhnout a watchdog nevyhodnotil
    stav jako chybu.
    """
    try:
        logger.warning("Restarting service %s", service_name)
        subprocess.run(["systemctl", "restart", service_name], check=True)

        logger.info("Service %s restarted, waiting %.1fs for warm-up", service_name, grace_s)
        stop_evt.wait(float(max(0.0, grace_s)))
        return True

    except Exception:
        logger.exception("Service restart failed: %s", service_name)
        return False


# ------------------ Main loop ----------------------------- #
def main() -> int:
    if not ENABLED:
        logger.warning("HA_WD_ENABLED=0 -> exiting.")
        return 0

    logger.info("CFG HA_HOST=%s POLL_S=%s FAIL_COUNT=%s RECOVER_COUNT=%s", HA_HOST, POLL_S, FAIL_COUNT, RECOVER_COUNT)
    logger.info("CFG SSH=%s@%s:%s CMD=%s", SSH_USER, HA_HOST, SSH_PORT, SSH_CMD)
    logger.info("CFG TG enabled=%s chat_id=%s", "yes" if TG_ENABLED else "no", "set" if TG_CHAT_ID else "missing")

    set_status("unknown")

    global fail_streak, ok_streak

    while not stop_evt.is_set():
        # jednoduchý check: HA web port 8123
        ok = tcp_ping(HA_HOST, 8123, timeout_s=1.0)

        if ok:
            ok_streak += 1
            fail_streak = 0
        else:
            fail_streak += 1
            ok_streak = 0

        cur = get_status()

        # přechod na DOWN
        if cur in ("unknown", "up") and fail_streak >= int(FAIL_COUNT):
            set_status("down")
            logger.warning("HA DOWN (fail_streak=%s)", fail_streak)

            if _cooldown_ok("down", NOTIFY_COOLDOWN_S):
                send_telegram("HA je nedostupný (DOWN). Zkusím restart.")
                _mark_notified("down")

            # --- doplnění: restart rpi-admin-ui s grace ---
            # (pokud UI při výpadku HA / při vlastním restartu padá do 404,
            #  tohle mu dá 2 s na naběhnutí)
            # --- UI restart (oddělený cooldown) ---
            if _ui_restart_cooldown_ok():
                _mark_ui_restart()
                restart_service_with_grace("rpi-admin-ui.service", grace_s=2.0)

            # --- HA restart (oddělený cooldown) ---
            if _ha_restart_cooldown_ok():
                _mark_ha_restart()
                ok_restart = ssh_restart_ha()
                if ok_restart:
                    logger.info("HA restart triggered via SSH")
                    if _cooldown_ok("restart", NOTIFY_COOLDOWN_S):
                        send_telegram("Posílám restart HA (SSH).")
                        _mark_notified("restart")
                else:
                    logger.error("Failed to restart HA via SSH")
                    if _cooldown_ok("restart_fail", NOTIFY_COOLDOWN_S):
                        send_telegram("Nepodařilo se poslat restart HA přes SSH.")
                        _mark_notified("restart_fail")

        # přechod na UP
        if cur in ("unknown", "down") and ok_streak >= int(RECOVER_COUNT):
            set_status("up")
            logger.info("HA UP (ok_streak=%s)", ok_streak)
            if _cooldown_ok("up", NOTIFY_COOLDOWN_S):
                send_telegram("HA je opět dostupný (UP).")
                _mark_notified("up")

        if DEBUG:
            logger.debug("check ok=%s ok_streak=%s fail_streak=%s status=%s", ok, ok_streak, fail_streak, get_status())

        stop_evt.wait(float(max(1, POLL_S)))

    return 0

def handle_stop(signum=None, frame=None):
    logger.info("Stopping (signal=%s)", signum)
    stop_evt.set()

if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, handle_stop)
    signal.signal(signal.SIGINT, handle_stop)
    raise SystemExit(main())
