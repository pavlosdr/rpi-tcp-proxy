import socket, threading, os
from dotenv import load_dotenv
load_dotenv(dotenv_path="./.env")

LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 502
TARGET_IP = os.getenv("PROXY_TARGET_IP", "10.10.100.253")
TARGET_PORT = int(os.getenv("PROXY_TARGET_PORT", "502"))
BUFFER_SIZE = 1024

def handle_client(client_socket, address):
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.connect((TARGET_IP, TARGET_PORT))
    except Exception as e:
        print(f"[ERROR] Nelze se připojit: {e}")
        client_socket.close()
        return
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
    t1.start(); t2.start(); t1.join(); t2.join()
    client_socket.close(); server_socket.close()

def start_proxy():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((LISTEN_IP, LISTEN_PORT))
    server.listen(10)
    print(f"[INFO] Proxy běží na {LISTEN_IP}:{LISTEN_PORT} → {TARGET_IP}:{TARGET_PORT}")
    while True:
        client_sock, addr = server.accept()
        threading.Thread(target=handle_client, args=(client_sock, addr)).start()

if __name__ == "__main__":
    try: start_proxy()
    except KeyboardInterrupt: print("[INFO] Proxy ukončena")
