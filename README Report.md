# MQTT Report (mqtt_report)

## Účel služby

Služba `mqtt_report` periodicky sbírá základní diagnostiku Raspberry Pi a souvisejících komponent (síť, dostupnost cílových hostů, stav systemd služby proxy) a publikuje ji do MQTT.
Součástí je i publikování MQTT Discovery konfigurace pro Home Assistant (automatické vytvoření entit).

Typické použití:
- přehled o stavu RPi (teplota CPU, loadavg, uptime),
- kontrola dostupnosti Home Assistantu a cílových zařízení (ping + TCP connect),
- sledování stavu `modbus_tcp_proxy.service`,
- jednoduchý watchdog datového toku přes standardizované bridge topicy (infigy-norma).

## Co služba dělá

- Připojí se k MQTT brokeru (s volitelným user/pass).
- Publikuje (retain) měřené hodnoty do stromu topiců pod `MQTT_REPORT_BASE_TOPIC`.
- Publikuje MQTT Discovery (retain) pod `homeassistant/...`, aby se entity samy objevily v Home Assistantu.
- Drží LWT (Last Will) pro `bridge/online`.
- Udržuje heartbeat a „flow“ indikaci:
  - `bridge/last_event_age_s`
  - `bridge/ws_flow_ok`

## Datový tok

Raspberry Pi (sběr metrik)
        ↓
mqtt_report
        ↓
MQTT Broker
        ↓
Home Assistant (MQTT Discovery + entity)

## Klíčové vlastnosti

- Robustní MQTT connect loop s exponenciálním backoffem.
- MQTT LWT pro `bridge/online` (offline při pádu procesu / výpadku sítě).
- Standardizované bridge topicy (infigy-norma):
  - `bridge/online`
  - `bridge/last_event_age_s`
  - `bridge/ws_flow_ok`
- MQTT Discovery (varianta B): bezpečné, protože entity lze měnit, dokud nejsou v HA „zafixované“ ručními úpravami.
- Publikování `report/snapshot` (JSON) pro ladění.

## MQTT topicy

Základní strom topiců:

- `{base}/sys/*` – systémové metriky
  - `sys/cpu_temp_c`
  - `sys/load_1m`, `sys/load_5m`, `sys/load_15m`
  - `sys/uptime_s`

- `{base}/net/*` – síťová diagnostika
  - `net/ping_ha_ok`, `net/ping_ha_ms`
  - `net/ping_inverter_ok`, `net/ping_inverter_ms`
  - `net/tcp_inverter_ok`, `net/tcp_inverter_ms`

- `{base}/proxy/*` – stav systemd proxy
  - `proxy/active`

- `{base}/report/*` – pomocné debug výstupy
  - `report/snapshot` (JSON)

- `{base}/bridge/*` – stav služby (infigy-norma)
  - `bridge/online` (LWT + explicitně nastavováno při connect/stop)
  - `bridge/heartbeat_ts` (unix timestamp)
  - `bridge/last_event_age_s` (sekundy; pokud ještě nebyl event, publikuje se -1)
  - `bridge/ws_flow_ok` (0/1)

Poznámka: `{base}` je hodnota `MQTT_REPORT_BASE_TOPIC` bez koncových/počátečních lomítek.

## Home Assistant MQTT Discovery

Služba publikuje Discovery config pod:

`{DISCOVERY_PREFIX}/{component}/{object_id}/config`

kde:
- `DISCOVERY_PREFIX` je `MQTT_REPORT_DISCOVERY_PREFIX` (typicky `homeassistant`)
- `object_id` je tvořeno jako `{ENTITY_PREFIX}_{suffix}`

Discovery payloady obsahují:
- `state_topic` ukazující na konkrétní `{base}/...` topic
- `unique_id` stabilní napříč restarty
- `device` blok s identitou zařízení (Device v HA)

Pokud chceš entity odstranit/změnit:
- je potřeba smazat retained discovery config (publikovat `NULL` na discovery topic), nebo použít clean-up funkci v UI.

## Konfigurace (.env)

Níže je přehled parametrů čtených přímo ze skriptu `mqtt_report.py`.

### Základní zapnutí služby

- `MQTT_REPORT_ENABLED`
  - `1` = služba běží a publikuje
  - `0` = po startu se ukončí (nepublikuje nic)

### MQTT připojení

- `MQTT_REPORT_HOST` – hostname/IP MQTT brokeru (např. `192.168.1.20`)
- `MQTT_REPORT_PORT` – port MQTT (typicky `1883`)
- `MQTT_REPORT_USER` – uživatel pro broker (pokud je vyžadován)
- `MQTT_REPORT_PASS` – heslo pro broker
- `MQTT_REPORT_BASE_TOPIC` – base topic (např. `rpi_report`)
- `MQTT_REPORT_CLIENT_ID` – MQTT client id (musí být unikátní na brokeru)
- `MQTT_REPORT_RECONNECT_BACKOFF_MAX_S` – max. backoff mezi reconnect pokusy (sekundy)

### MQTT Discovery / identita v HA

- `MQTT_REPORT_DISCOVERY_PREFIX` – prefix pro HA discovery (typicky `homeassistant`)
- `MQTT_REPORT_DEVICE_ID` – identifikátor zařízení (jde do `device.identifiers`)
- `MQTT_REPORT_DEVICE_NAME` – název zařízení v HA
- `MQTT_REPORT_ENTITY_PREFIX` – prefix pro object_id všech entit (např. `rpi_report`)

### Cíle pro diagnostiku

- `MQTT_REPORT_PING_HA_HOST` – host pro ping test Home Assistantu
- `MQTT_REPORT_PING_INVERTER_HOST` – host pro ping inverteru (nebo jiné cílové zařízení)
- `MQTT_REPORT_INVERTER_HOST` – host pro TCP connect test (např. GoodWe / proxy)
- `MQTT_REPORT_INVERTER_PORT` – port pro TCP connect test (typicky `502`)
- `MQTT_REPORT_PROXY_SYSTEMD_UNIT` – systemd unit, jejíž stav se sleduje (např. `modbus_tcp_proxy.service`)

### Intervaly a watchdog

- `MQTT_REPORT_POLL_SYS_S` – interval sběru systémových metrik
- `MQTT_REPORT_POLL_NET_S` – interval síťových testů
- `MQTT_REPORT_POLL_PROXY_S` – interval kontroly systemd unit
- `MQTT_REPORT_HEARTBEAT_S` – interval publikování metrik + heartbeat (sekundy)
- `MQTT_REPORT_MAX_AGE_OK_S` – hranice pro `bridge/ws_flow_ok` (pokud je poslední publish starší, ws_flow_ok = 0)

Poznámka k implementaci:
Aktuální verze skriptu používá `MQTT_REPORT_HEARTBEAT_S` jako interval pro publish loop (metriky se publikují v tomto rytmu). Parametr typu „publish interval“ se v kódu samostatně nepoužívá.

### Logování

- `MQTT_REPORT_LOG_LEVEL` – `DEBUG|INFO|WARNING|ERROR`

## Systemd služba

Typický unit soubor:

- `ExecStart=/usr/bin/python3 /opt/rpi-admin-ui/mqtt_report.py`
- `EnvironmentFile=/opt/rpi-admin-ui/.env`

Doporučení:
- nechat `Restart=on-failure`
- logovat do journald (StandardOutput/StandardError)
---
## Předpoklady (User Requirements)
- přístup na MQTT broker (dostupná z RPi + povolená autentizace user/password)

## Ověření funkčnosti

1) Ověř, že se služba připojila k brokeru:
- v logu hledej `MQTT connected rc=0`

2) Ověř, že vznikají topicy:
- z jiného stroje/terminálu:
  - `mosquitto_sub -h <broker> -u <user> -P <pass> -v -t 'rpi_report/#'`

3) V Home Assistantu:
- zkontroluj, že se vytvořilo zařízení s názvem `MQTT_REPORT_DEVICE_NAME`
- ověř binary sensor `bridge online` a `ws flow ok`

## Troubleshooting

- Connection Refused: not authorised
  - broker vyžaduje autentizaci; nastav `MQTT_REPORT_USER` a `MQTT_REPORT_PASS`
  - ověř ACL na brokeru (zda uživatel smí číst/zapisovat do `{base}/#`)

- Entity se v HA neobjevují
  - zkontroluj `MQTT_REPORT_DISCOVERY_PREFIX` (typicky `homeassistant`)
  - musí být zapnuté MQTT integration v HA a povolené discovery
  - discovery config je retained – pokud jsi měnil strukturu, smaž starý retained config (clean-up)

