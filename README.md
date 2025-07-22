# RPi TCP Proxy + Admin UI

Hlavním cílem projektu je zajištění proxy služby na Raspberry Pi, která zprostředkuje proxy mezi domácí LAN sítí Home Assistant a WLAN sítě střídače FVE GoodWe. Projekt dále poskytuje jednoduché webové rozhraní pro správu stavu Raspberry Pi a monitoring služeb včetně Modbus TCP Proxy a MQTT reportingu.
Určeno pro domácí automatizaci a integraci s Home Assistant. Součástí je také možnost restartovat jednotlivé služby a nastavovbat základní parametry.

## Požadavky
- Raspberry Pi s Raspbian (Bullseye nebo novější)
- Internetové připojení (pro stažení ZIP a balíčků)
- Přístup přes SSH nebo připojený monitor

## Instalace

1. **Stažení a spuštění instalačního skriptu**:

```bash
curl -fsSL https://raw.githubusercontent.com/pavlosdr/rpi-tcp-proxy/master/download/Rpi_Admin_Ui_Setup.sh -o Rpi_Admin_Ui_Setup.sh
chmod +x Rpi_Admin_Ui_Setup.sh
./Rpi_Admin_Ui_Setup.sh
```

2. **Co skript provádí**:

- Instaluje systémové závislosti (unzip, curl, Flask, pip, fping, atd.)
- Stáhne archiv `rpi-tcp-proxy-no-git.zip` ze zadané URL
- Rozbalí soubory do `/opt/rpi-admin-ui`
- Nainstaluje Python závislosti z `requirements.txt`
- Zaregistruje a spustí systemd služby:
  - `rpi-admin-ui.service` – webové rozhraní pro správu systému
  - `modbus_tcp_proxy.service` – TCP proxy přeposílající požadavky mezi Home Assistantem a GoodWe měničem
  - `rpi-mqtt-report.service` – periodické hlášení síly Wi-Fi připojení do MQTT

---

## Přístup k webovému rozhraní

Po dokončení instalace otevři webový prohlížeč a přejdi na:

```
http://<IP_adresa_Raspberry_Pi>:8080
```

Příklad:

```
http://192.168.1.42:8080
```
Přihlašovací údaje jsou uloženy v  `.env`
Přihlašovací údaje:
- **Uživatel:** `admin`
- **Heslo:** `raspberry` *(lze změnit v `.env` souboru)*
---

## Opakovaná instalace nebo aktualizace

Pokud chceš systém přeinstalovat nebo aktualizovat:

```bash
./Rpi_Admin_Ui_Setup.sh
```

Skript vše provede automaticky — staré soubory odstraní a nasadí nové.

---

## Sestavení ZIP balíčku z Git repozitáře (Windows)

V adresáři tools\ spusť skript:
```
generate_deploy_zip_from_git.bat
```

Tento skript provede:

- Vytvoření složky `C:\Git\rpi-tcp-proxy\download` (pokud neexistuje)
- Vygenerování archivu `rpi-tcp-proxy-no-git.zip` bez pomocných souborů (`.git`, `tools/`, `download/`, atd.)
- Pokud ve složce zip již existoval, smaže jej a nahradí novým

ZIP můžeš následně nahrát na vlastní web a použít pro instalaci přes `Rpi_Admin_Ui_Setup.sh`.

---

## Struktura projektu

```
rpi-tcp-proxy/
├── app.py
├── download/
│   ├── Rpi_Admin_Ui_Setup.sh
│   └── rpi-tcp-proxy-no-git.zip
├── modbus_tcp_proxy.py
├── .env
├── requirements.txt
├── templates/
│   └── env.html
├── systemd/
│   ├── rpi-admin-ui.service
│   ├── modbus_tcp_proxy.service
│   └── rpi-mqtt-report.service
├── README.md
└── tools/
    ├── generate_deploy_zip_from_git.bat
    └── generate_deploy_zip_from_git.ps1

```

---

## Konfigurace

Hodnoty v `.env` lze měnit přímo ve webovém rozhraní v sekci **„Env proměnné“** (`/env`).


Repozitář obsahuje:
- Webové rozhraní pro monitoring a správu RPi >>> `app.py`,
- Modbus TCP proxy skript pro přemostění komunikace mezi Home Assistant a měničem GoodWe >>> `modbus_tcp_proxy.py`,
- MQTT reporting skript pro stavové informace,
- `.env` konfigurační soubor s parametry jako IP měniče, MQTT adresa apod.
- `templates/env.html`: Webová editace .env
- `Rpi_Admin_Ui_Setup.sh`: Instalační skript pro Raspberry Pi

---

## Řešení problémů

- **Web UI nejde otevřít:** Zkontroluj, že běží služba `rpi-admin-ui`:
  ```bash
  sudo systemctl status rpi-admin-ui
  ```
- **GoodWe měnič nereaguje:** Ověř, že Raspberry Pi má přístup na IP adresu měniče (např. `ping 10.10.100.253`).
- **MQTT reporty se neobjevují:** Zkontroluj, že `mqtt://core-mosquitto:1883` je dostupný a že v `.env` souboru jsou správné MQTT údaje.
- **Změna nastavení:** Úprav `.env` soubor v `/opt/rpi-admin-ui/` a restartuj příslušné služby:
  ```bash
  sudo systemctl restart rpi-admin-ui modbus_tcp_proxy rpi-mqtt-report
  ```

---

## Autor

Projekt vytvořen pro správu a monitoring domácího serveru s Home Assistantem a připojeným měničem GoodWe.

