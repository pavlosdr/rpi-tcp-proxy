"""
Modbus TCP proxy / bridge

Proxy server pro Modbus TCP komunikaci mezi Home Assistantem
a zařízeními v oddělené síti (např. GoodWe měnič). V konkrétní 
implementaci je Home Assistant připojen na LAN a Good We na wifi.
Protože Good We posílá mnoho nevyžádaných dat, je potřeba filtrovat
ta data, která požadujeme v Home Assistant.

Funkce:
- Přeposílání Modbus TCP požadavků
- Úprava TID / UID (nestandardní chování zařízení)
- Stabilizace spojení
- Monitoring a logování provozu

Používáno jako systemd service na Raspberry Pi.
"""
import os
import socket
import threading
import time
import select
import itertools
import logging
import sys
from dotenv import load_dotenv
from typing import Tuple, Deque
from collections import deque
from envfile import env_str, env_int, env_float

# ---------------------- KONFIGURACE ---------------------- #

# ------------------------ .env --------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

# ------------------- Konfig z .env ----------------------- #
LISTEN_IP   = env_str("MODBUS_PROXY_LISTEN_IP", "0.0.0.0")
LISTEN_PORT = env_int("MODBUS_PROXY_LISTEN_PORT", 502)

TARGET_IP   = env_str("MODBUS_PROXY_TARGET_IP", "10.10.100.253")
TARGET_PORT = env_int("MODBUS_PROXY_TARGET_PORT", 502)

BUFFER_SIZE = env_int("MODBUS_PROXY_BUFFER_SIZE", 4096)
SOCK_TIMEOUT_S = env_float("MODBUS_PROXY_SOCK_TIMEOUT_S", 30.0)   # recv timeout pro detekci „ticha“

LOG_FILE_PKT         = env_str("MODBUS_PROXY_LOG_FILE_PKT", "/var/log/modbus_proxy.log")
LOG_LEVEL_PKT        = env_str("MODBUS_PROXY_LOG_LEVEL_PKT", "INFO").upper()  # DEBUG|INFO|WARNING|ERROR
LOG_HEXDUMP_PKT      = env_str("MODBUS_PROXY_LOG_HEXDUMP_PKT", "0") in ("1", "true", "True")
LOG_SAMPLE_BYTES_PKT = env_int("MODBUS_PROXY_LOG_SAMPLE_BYTES_PKT", 64)  # kolik bajtů vypsat z payloadu
LOG_STATS_INTERVAL   = env_int("MODBUS_PROXY_LOG_STATS_INTERVAL", 60)  # s – periodické souhrny
DROP_STRAY_SILENT    = env_int("MODBUS_PROXY_DROP_STRAY_SILENT", 0)   # 1 = pokud je stray nic nelogovat

# ---- nové tolerantní přepínače ----
TID_REWRITE = env_str("MODBUS_PROXY_TID_REWRITE", "1") in ("1", "true", "True")
TID_STRICT  = env_str("MODBUS_PROXY_VTID_STRICT", "0") in ("1", "true", "True")   # když 1, nepřepisuje, jen loguje
STRICT_UID  = env_str("MODBUS_PROXY_STRICT_UID", "0") in ("1", "true", "True")   # volitelná kontrola UID
PASS_STRAY = env_int("MODBUS_PROXY_PASS_STRAY", 0)         # 1 = přeposílat i bez pending (nedoporučeno)

# ---------------------- Logging ---------------------------
# Log minimization
LOG_LEVEL = getattr(logging, env_str("MODBUS_PROXY_LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger("modbus_tcp_proxy")
logger.setLevel(LOG_LEVEL)
# Zapneme file log jen pokud:
#  - explicitně chceme hexdump
#  - nebo LOG pro modbus je DEBUG
ENABLE_PKT_LOG = LOG_HEXDUMP_PKT or getattr(logging, LOG_LEVEL_PKT, logging.INFO) <= logging.DEBUG

# ----------- Logging per-packet logger do souboru -------- #
PKT_LOG = logger.getChild("pkt")
PKT_LOG.setLevel(logging.DEBUG)
PKT_LOG.propagate = False  # zabrání, aby packet logy šly i do stdout

if ENABLE_PKT_LOG and LOG_FILE_PKT:
    pkt_handler = logging.FileHandler(LOG_FILE_PKT, encoding="utf-8")
    pkt_handler.setLevel(logging.DEBUG)
    pkt_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"))
    PKT_LOG.addHandler(pkt_handler)

    logger.info("Packet logging ENABLED (file=%s, hexdump=%s)", LOG_FILE_PKT, "ON" if LOG_HEXDUMP_PKT else "OFF")
else:
    logger.info("Packet logging DISABLED")
# ---------------------- LOG runtime ---------------------- #
logger.debug("__file__ running from: %s", __file__)
logger.debug("PYTHON: %s", sys.executable)

# ------- Singleton lock: zabran spusteni 2. instance ----- #
_singleton = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
try:
    _singleton.bind("\0modbus_tcp_proxy.singleton")
except OSError:
    logger.warning("Another modbus_tcp_proxy instance is running. Exiting.")
    sys.exit(1)
# --------------------------------------------------------- #
# Pořadí spojení
_conn_counter = itertools.count(1)

def enable_keepalive(sock: socket.socket):
    """Nastaví TCP Keep-Alive na daném socketu (Linux)."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
    except Exception as e:
        logger.debug("Keepalive detail options not supported: %s", e)

def hexdump(b: bytes, maxlen: int = 64) -> str:
    s = b[:maxlen]
    return s.hex(sep=" ")

def parse_modbus_header(payload: bytes) -> Tuple[int, int, int]:
    """
    Vrací (tid, uid, func).
    Modbus TCP MBAP: TID(2) PID(2=0) LEN(2) UID(1) FUNC(1) ...
    """
    if len(payload) < 8:
        return (-1, -1, -1)
    tid = int.from_bytes(payload[0:2], "big", signed=False)
    uid = payload[6]
    func = payload[7]
    return (tid, uid, func)

def set_modbus_tid(payload: bytes, new_tid: int) -> bytes:
    """
    Vrátí nový payload s přepsaným TID v MBAP hlavičce.
    """
    if len(payload) < 2:
        return payload
    return new_tid.to_bytes(2, "big") + payload[2:]

def forward_loop(conn_id: int, client: socket.socket, backend: socket.socket, peer: str):
    """
    Multiplex mezi client<->backend přes select().
    Přidá frontu čekajících požadavků (TID) a volitelné přepisování TID v odpovědi.
    """
    start_ts = time.time()
    last_stats_ts = start_ts

    # statistiky
    up_bytes = down_bytes = 0
    up_frames = down_frames = 0

    # fronta outstanding požadavků (FIFO); prvky: (tid, uid, func)
    pending: Deque[Tuple[int, int, int]] = deque()

    client.settimeout(SOCK_TIMEOUT_S)
    backend.settimeout(SOCK_TIMEOUT_S)

    sockets = [client, backend]
    conn_tag = f"conn-{conn_id}"

    def log_pkt(direction: str, data: bytes):
        nonlocal up_bytes, down_bytes, up_frames, down_frames
        length = len(data)
        if direction == "C>W":
            up_bytes += length
            up_frames += 1
        else:
            down_bytes += length
            down_frames += 1

        tid, uid, func = parse_modbus_header(data)
        if LOG_HEXDUMP_PKT:
            PKT_LOG.debug("[%s] %s len=%s tid=%s uid=%s func=%s data=%s",
                conn_tag, direction, length, tid, uid, func, hexdump(data, LOG_SAMPLE_BYTES_PKT),
            )
        else:
            PKT_LOG.debug("[%s] %s len=%s tid=%s uid=%s func=%s",
                conn_tag, direction, length, tid, uid, func,
            )

    try:
        while True:
            r, _, _ = select.select(sockets, [], [], SOCK_TIMEOUT_S)

            now = time.time()
            if LOG_STATS_INTERVAL > 0 and (now - last_stats_ts) >= LOG_STATS_INTERVAL:
                logger.info("[%s] stats: up=%sB/%sf, down=%sB/%sf, alive=%ss",
                    conn_tag, up_bytes, up_frames, down_bytes, down_frames, int(now - start_ts))
                last_stats_ts = now

            if not r:
                logger.debug("[%s] idle %ss - waiting", conn_tag, SOCK_TIMEOUT_S)
                continue

            for s in r:
                try:
                    data = s.recv(BUFFER_SIZE)
                except socket.timeout:
                    side = "client" if s is client else "backend"
                    logger.debug("[%s] recv timeout on %s", conn_tag, side)
                    continue
                except Exception as e:
                    side = "client" if s is client else "backend"
                    logger.warning("[%s] recv error on %s: %r", conn_tag, side, e)
                    return

                if not data:
                    side = "client" if s is client else "backend"
                    logger.info("[%s] EOF from %s, closing", conn_tag, side)
                    # pokud končíme a něco čeká – zaloguj
                    if pending:
                        left = [p[0] for p in list(pending)]
                        logger.warning("[%s] closing with pending=%s (unanswered tids: %s)", conn_tag, len(pending), left)
                    return

                if s is client:
                    # ---- Client -> Backend ----
                    log_pkt("C>W", data)
                    c_tid, c_uid, c_func = parse_modbus_header(data)
                    if c_tid >= 0:
                        pending.append((c_tid, c_uid, c_func))
                    try:
                        backend.sendall(data)
                    except Exception as e:
                        logger.warning("[%s] send backend error: %r", conn_tag, e)
                        return
                else:
                    # ---- Backend -> Client ----
                    log_pkt("W>C", data)
                    b_tid, b_uid, b_func = parse_modbus_header(data)

                    if not pending:
                        # nic nečekáme – odpověď „navíc“
                        if not DROP_STRAY_SILENT:
                            # v INFO to je jen šum (inverter posílá nevyžádané odpovědi)
                            logger.debug("[%s] stray_response tid=%s (no pending requests)", conn_tag, b_tid)
                        # PASS_STRAY=1 -> propustit; 0 -> zahodit. V obou případech nepokračovat na popleft().
                        if PASS_STRAY:
                            try:
                                client.sendall(data)  # propustíme, i když nemáme pending
                            except Exception as e:
                                logger.warning("[%s] send client error: %r", conn_tag, e)
                                return
                        continue
                    # --- KLÍČOVÁ ZMĚNA: nejdřív jen peek na očekávaný požadavek, popleft až při akceptaci ---
                    exp_tid, exp_uid, exp_func = pending[0]

                    # volitelná informativní kontrola UID
                    if STRICT_UID and b_uid != -1 and exp_uid != -1 and b_uid != exp_uid:
                        logger.warning("[%s] tid_mismatch resp=%s expected=%s (pending=%s)", conn_tag, b_tid, exp_tid, len(pending))

                    if b_tid == exp_tid:
                        # pořadí sedí -> přijímáme a teprve teď pop
                        pending.popleft()
                        try:
                            client.sendall(data)
                        except Exception as e:
                            logger.warning("[%s] send client error: %r", conn_tag, e)
                            return
                        continue

                    # TID nesedí
                    if TID_STRICT and not TID_REWRITE:
                        # diagnostický režim: jen loguj; pending NECHÁVÁME, aby mohla projít další správná odpověď
                        logger.warning("[%s] tid_mismatch resp=%s expected=%s (pending=%s)",
                            conn_tag, b_tid, exp_tid, len(pending),
                        )
                        if PASS_STRAY:
                            # volitelně propustíme „cizí“ odpověď, ale pending nepopujeme
                            try:
                                client.sendall(data)
                            except Exception as e:
                                logger.warning("[%s] send client error: %r", conn_tag, e)
                                return
                        # nepopujeme, čekáme dál na správný TID
                        continue

                    if TID_REWRITE:
                        # tolerantní režim: přepiš na očekávané TID, pop a pošli
                        data = set_modbus_tid(data, exp_tid)
                        pending.popleft()
                        logger.debug("[%s] tid_rewrite %s -> %s (pending_after_pop=%s)", conn_tag, b_tid, exp_tid, len(pending))
                        try:
                            client.sendall(data)
                        except Exception as e:
                            logger.warning("[%s] send client error: %r", conn_tag, e)
                            return
                        continue

                    # fallback: zaloguj a podle PASS_STRAY případně pošli, pending zůstává
                    if not DROP_STRAY_SILENT:
                        logger.debug("[%s] stray_response tid=%s expected=%s pending=%s",
                            conn_tag, b_tid, exp_tid, len(pending),
                        )
                    if PASS_STRAY:
                        try:
                            client.sendall(data)
                        except Exception as e:
                            logger.warning("[%s] send client error: %r", conn_tag, e)
                            return
                    # pending NEPOPujeme
                    continue

    finally:
        # konec spojení – shrnutí
        dur = time.time() - start_ts
        logger.info("[%s] closed: duration=%ss, up=%sB/%sf, down=%sB/%sf",
            conn_tag, int(dur), up_bytes, up_frames, down_bytes, down_frames)
        try:
            client.close()
        except Exception:
            pass
        try:
            backend.close()
        except Exception:
            pass

def handle_client(client_socket: socket.socket, address: Tuple[str, int]):
    conn_id = next(_conn_counter)
    conn_tag = f"conn-{conn_id}"
    peer = f"{address[0]}:{address[1]}"

    # Připojit na backend
    try:
        backend_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        enable_keepalive(backend_socket)
        backend_socket.connect((TARGET_IP, TARGET_PORT))
    except Exception as e:
        logger.error("[%s] backend connect error to %s:%s: %r", conn_tag, TARGET_IP, TARGET_PORT, e)
        try:
            client_socket.close()
        finally:
            return

    enable_keepalive(client_socket)

    logger.info("[%s] new connection from %s -> %s:%s", conn_tag, peer, TARGET_IP, TARGET_PORT)

    try:
        forward_loop(conn_id, client_socket, backend_socket, peer)
    except Exception as e:
        logger.exception("[%s] unexpected error in forward_loop", conn_tag)

def start_proxy():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_IP, LISTEN_PORT))
    server.listen(50)

    logger.info(
        "Proxy listening on %s:%s, forwarding to %s:%s, buf=%s, timeout=%ss, hexdump=%s, "
        "tid_rewrite=%s, tid_strict=%s, strict_uid=%s, pass_stray=%s, drop_stray_silent=%s",
        LISTEN_IP, LISTEN_PORT, TARGET_IP, TARGET_PORT, BUFFER_SIZE, SOCK_TIMEOUT_S,
        "ON" if LOG_HEXDUMP_PKT else "OFF",
        "ON" if TID_REWRITE else "OFF",
        "ON" if TID_STRICT else "OFF",
        "ON" if STRICT_UID else "OFF",
        "ON" if PASS_STRAY else "OFF",
        "ON" if DROP_STRAY_SILENT else "OFF",
    )

    while True:
        try:
            client_sock, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True)
            t.start()
        except KeyboardInterrupt:
            logger.info("Proxy stopping (KeyboardInterrupt)")
            break
        except Exception as e:
            logger.error("Accept error: %r", e)
            time.sleep(1)

if __name__ == "__main__":
    start_proxy()
