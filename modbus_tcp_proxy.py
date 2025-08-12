import socket
import threading
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)


LISTEN_IP = os.getenv("LISTEN_IP", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "502"))

TARGET_IP = os.getenv("PROXY_TARGET_IP", "10.10.100.253") # ← IP měniče přes wlan0
TARGET_PORT = int(os.getenv("PROXY_TARGET_PORT", "502"))

BUFFER_SIZE = 1024

def enable_keepalive(sock):
    """Nastaví TCP Keep-Alive na daném socketu."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)     # start po 60 s
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)    # každých 10 s
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)       # max 5 pokusů

def handle_client(client_socket, address):
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        enable_keepalive(server_socket)
        server_socket.connect((TARGET_IP, TARGET_PORT))
    except Exception as e:
        print(f"[ERROR] Nelze se připojit k cílovému zařízení: {e}")
        client_socket.close()
        return

    enable_keepalive(client_socket)

    print(f"[INFO] Nové spojení od {address[0]}")

    def forward(src, dst):
        while True:
            try:
                data = src.recv(BUFFER_SIZE)
                if not data:
                    break
                dst.sendall(data)
            except:
                break

    t1 = threading.Thread(target=forward, args=(client_socket, server_socket))
    t2 = threading.Thread(target=forward, args=(server_socket, client_socket))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    client_socket.close()
    server_socket.close()
    print(f"[INFO] Spojení od {address[0]} uzavřeno")

def start_proxy():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_IP, LISTEN_PORT))
    server.listen(10)
    print(f"[INFO] Proxy spuštěna na {LISTEN_IP}:{LISTEN_PORT}, přeposílá na {TARGET_IP}:{TARGET_PORT}")

    while True:
        client_sock, addr = server.accept()
        threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True).start()

if __name__ == "__main__":
    try:
        start_proxy()
    except KeyboardInterrupt:
        print("[INFO] Proxy ukončena")
