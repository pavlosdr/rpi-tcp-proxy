#!/usr/bin/env python3
import os
import socket
import threading
import time
import select
import itertools
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from typing import Tuple, Deque
from collections import deque

# ---------- Config z .env ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

LISTEN_IP   = os.getenv("LISTEN_IP", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "502"))

TARGET_IP   = os.getenv("PROXY_TARGET_IP", "10.10.100.253")
TARGET_PORT = int(os.getenv("PROXY_TARGET_PORT", "502"))

BUFFER_SIZE = int(os.getenv("BUFFER_SIZE", "4096"))
SOCK_TIMEOUT_S = int(os.getenv("SOCK_TIMEOUT_S", "30"))   # recv timeout pro detekci „ticha“

LOG_FILE          = os.getenv("LOG_FILE", "/var/log/modbus_proxy.log")
LOG_LEVEL         = os.getenv("LOG_LEVEL", "INFO").upper()  # DEBUG|INFO|WARNING|ERROR
LOG_MAX_BYTES     = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_BACKUP_COUNT  = int(os.getenv("LOG_BACKUP_COUNT", "5"))
LOG_HEXDUMP       = os.getenv("LOG_HEXDUMP", "0") in ("1", "true", "True")
LOG_SAMPLE_BYTES  = int(os.getenv("LOG_SAMPLE_BYTES", "64"))  # kolik bajtů vypsat z payloadu
LOG_STATS_INTERVAL = int(os.getenv("LOG_STATS_INTERVAL", "60"))  # s – periodické souhrny
DROP_STRAY_SILENT = int(os.getenv("DROP_STRAY_SILENT", "0"))   # 1 = pokud je stray nic nelogovat

# ---- nové tolerantní přepínače ----
TID_REWRITE = os.getenv("TID_REWRITE", "1") in ("1", "true", "True")
TID_STRICT  = os.getenv("TID_STRICT", "0") in ("1", "true", "True")   # když 1, nepřepisuje, jen loguje
STRICT_UID  = os.getenv("STRICT_UID", "0") in ("1", "true", "True")   # volitelná kontrola UID
PASS_STRAY = int(os.getenv("PASS_STRAY", "0"))                 # 1 = přeposílat i bez pending (nedoporučeno)

# ---------- Logger ----------
logger = logging.getLogger("modbus_tcp_proxy")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8")
formatter = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# Úroveň pro „per-packet“ výpisy – držím na DEBUG
PKT_LOG = logger.getChild("pkt")
PKT_LOG.setLevel(logging.DEBUG)

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
        logger.debug(f"Keepalive detail options not supported: {e}")

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
        meta = f"[{conn_tag}] {direction} len={length} tid={tid} uid={uid} func={func}"
        if LOG_HEXDUMP:
            PKT_LOG.debug(f"{meta} data={hexdump(data, LOG_SAMPLE_BYTES)}")
        else:
            PKT_LOG.debug(meta)

    try:
        while True:
            r, _, _ = select.select(sockets, [], [], SOCK_TIMEOUT_S)

            now = time.time()
            if LOG_STATS_INTERVAL > 0 and (now - last_stats_ts) >= LOG_STATS_INTERVAL:
                logger.info(
                    f"[{conn_tag}] stats: up={up_bytes}B/{up_frames}f, down={down_bytes}B/{down_frames}f, "
                    f"alive={int(now - start_ts)}s"
                )
                last_stats_ts = now

            if not r:
                logger.debug(f"[{conn_tag}] idle {SOCK_TIMEOUT_S}s – waiting")
                continue

            for s in r:
                try:
                    data = s.recv(BUFFER_SIZE)
                except socket.timeout:
                    logger.debug(f"[{conn_tag}] recv timeout on {'client' if s is client else 'backend'}")
                    continue
                except Exception as e:
                    logger.warning(f"[{conn_tag}] recv error on {'client' if s is client else 'backend'}: {repr(e)}")
                    return

                if not data:
                    side = "client" if s is client else "backend"
                    logger.info(f"[{conn_tag}] EOF from {side}, closing")
                    # pokud končíme a něco čeká – zaloguj
                    if pending:
                        left = [p[0] for p in list(pending)]
                        logger.warning(f"[{conn_tag}] closing with pending={len(pending)} (unanswered tids: {left})")
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
                        logger.warning(f"[{conn_tag}] send backend error: {repr(e)}")
                        return
                else:
                    # ---- Backend -> Client ----
                    log_pkt("W>C", data)
                    b_tid, b_uid, b_func = parse_modbus_header(data)

                    if not pending:
                        # nic nečekáme – odpověď „navíc“
                        if not DROP_STRAY_SILENT:
                            logger.warning(f"[{conn_tag}] stray_response tid={b_tid} (no pending requests)")
                        # PASS_STRAY=1 -> propustit; 0 -> zahodit. V obou případech nepokračovat na popleft().
                        if PASS_STRAY:
                            try:
                                client.sendall(data)  # propustíme, i když nemáme pending
                            except Exception as e:
                                logger.warning(f"[{conn_tag}] send client error: {repr(e)}")
                                return
                        continue
                    # --- KLÍČOVÁ ZMĚNA: nejdřív jen peek na očekávaný požadavek, popleft až při akceptaci ---
                    exp_tid, exp_uid, exp_func = pending[0]

                    # volitelná informativní kontrola UID
                    if STRICT_UID and b_uid != -1 and exp_uid != -1 and b_uid != exp_uid:
                        logger.warning(f"[{conn_tag}] uid_mismatch resp_uid={b_uid} expected_uid={exp_uid} tid={b_tid}->{exp_tid}")

                    if b_tid == exp_tid:
                        # pořadí sedí -> přijímáme a teprve teď pop
                        pending.popleft()
                        try:
                            client.sendall(data)
                        except Exception as e:
                            logger.warning(f"[{conn_tag}] send client error: {repr(e)}")
                            return
                        continue

                    # TID nesedí
                    if TID_STRICT and not TID_REWRITE:
                        # diagnostický režim: jen loguj; pending NECHÁVÁME, aby mohla projít další správná odpověď
                        logger.warning(f"[{conn_tag}] tid_mismatch resp={b_tid} expected={exp_tid} (pending={len(pending)})")
                        if PASS_STRAY:
                            # volitelně propustíme „cizí“ odpověď, ale pending nepopujeme
                            try:
                                client.sendall(data)
                            except Exception as e:
                                logger.warning(f"[{conn_tag}] send client error: {repr(e)}")
                                return
                        # nepopujeme, čekáme dál na správný TID
                        continue

                    if TID_REWRITE:
                        # tolerantní režim: přepiš na očekávané TID, pop a pošli
                        data = set_modbus_tid(data, exp_tid)
                        pending.popleft()
                        logger.info(f"[{conn_tag}] tid_rewrite {b_tid} -> {exp_tid} (pending_after_pop={len(pending)})")
                        try:
                            client.sendall(data)
                        except Exception as e:
                            logger.warning(f"[{conn_tag}] send client error: {repr(e)}")
                            return
                        continue

                    # fallback: zaloguj a podle PASS_STRAY případně pošli, pending zůstává
                    if not DROP_STRAY_SILENT:
                        logger.warning(f"[{conn_tag}] stray_response tid={b_tid} expected={exp_tid} pending={len(pending)}")
                    if PASS_STRAY:
                        try:
                            client.sendall(data)
                        except Exception as e:
                            logger.warning(f"[{conn_tag}] send client error: {repr(e)}")
                            return
                    # pending NEPOPujeme
                    continue

    finally:
        # konec spojení – shrnutí
        dur = time.time() - start_ts
        logger.info(
            f"[{conn_tag}] closed: duration={int(dur)}s, "
            f"up={up_bytes}B/{up_frames}f, down={down_bytes}B/{down_frames}f"
        )
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
        logger.error(f"[{conn_tag}] backend connect error to {TARGET_IP}:{TARGET_PORT}: {repr(e)}")
        try:
            client_socket.close()
        finally:
            return

    enable_keepalive(client_socket)

    logger.info(f"[{conn_tag}] new connection from {peer} -> {TARGET_IP}:{TARGET_PORT}")

    try:
        forward_loop(conn_id, client_socket, backend_socket, peer)
    except Exception as e:
        logger.exception(f"[{conn_tag}] unexpected error in forward_loop: {repr(e)}")

def start_proxy():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_IP, LISTEN_PORT))
    server.listen(50)

    logger.info(
        "Proxy listening on %s:%s, forwarding to %s:%s, buf=%s, timeout=%ss, hexdump=%s, "
        "tid_rewrite=%s, tid_strict=%s, strict_uid=%s, pass_stray=%s, drop_stray_silent=%s",
        LISTEN_IP, LISTEN_PORT, TARGET_IP, TARGET_PORT, BUFFER_SIZE, SOCK_TIMEOUT_S,
        "ON" if LOG_HEXDUMP else "OFF",
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
            logger.error(f"Accept error: {repr(e)}")
            time.sleep(1)

if __name__ == "__main__":
    start_proxy()
