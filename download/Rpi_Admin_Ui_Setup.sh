#!/bin/bash

# ==== KONFIGURACE ====
APP_DIR="/opt/rpi-admin-ui"
SERVICE_NAME="rpi-admin-ui"
ARCHIVE_NAME="rpi-tcp-proxy-no-git.zip"
ARCHIVE_URL="https://raw.githubusercontent.com/pavlosdr/rpi-tcp-proxy/master/download/$ARCHIVE_NAME"
TEMP_DIR="$HOME/tmp_rpi_ui_setup"

# ==== KROK 1: Závislosti ====
echo "[1/6] Instalace závislostí..."
sudo apt update && sudo apt install -y unzip curl python3-flask python3-pip net-tools wireless-tools jq fping mosquitto-clients

# ==== KROK 2: Stažení archivu ====
echo "[2/6] Stahuji archiv se zdrojovými soubory..."
mkdir -p "$TEMP_DIR"
curl -L --fail "$ARCHIVE_URL" -o "$TEMP_DIR/$ARCHIVE_NAME"
if [ $? -ne 0 ]; then
  echo "[CHYBA] Nepodařilo se stáhnout archiv ze $ARCHIVE_URL. Zkontroluj URL nebo připojení."
  exit 1
fi

# ==== KROK 3: Rozbalení ====
echo "[3/6] Rozbaluji do $APP_DIR..."
sudo rm -rf "$APP_DIR"
sudo unzip -o "$TEMP_DIR/$ARCHIVE_NAME" -d "$APP_DIR"
sudo chown -R $USER:$USER "$APP_DIR"

# ==== KROK 4: Instalace prostředí ====
echo "[4/6] Instalace Python závislostí..."
sudo pip3 install -r "$APP_DIR/requirements.txt"

# ==== KROK 5: Aktivace systemd služeb ====
echo "[5/6] Aktivace systemd služeb..."
sudo cp "$APP_DIR/systemd/"*.service /etc/systemd/system/
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable rpi-admin-ui.service modbus_tcp_proxy.service rpi-mqtt-report.service
sudo systemctl restart rpi-admin-ui.service modbus_tcp_proxy.service rpi-mqtt-report.service

# ==== KROK 6: Dokončeno ====
echo "[OK] Instalace dokončena. Otevři: http://$(hostname -I | awk '{print $1}'):8080"

# Uklid
rm -rf "$TEMP_DIR"
