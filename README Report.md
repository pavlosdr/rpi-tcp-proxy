# MQTT Report (mqtt_report)

## Účel služby

Služba `mqtt_report` periodicky sbírá základní diagnostiku Raspberry Pi a souvisejících komponent (síť, dostupnost cílových hostů, stav systemd služby proxy) a publikuje ji do MQTT.

Součástí je i publikování MQTT Discovery konfigurace pro Home Assistant (automatické vytvoření entit).

Typické použití:

* přehled o stavu RPi (teplota CPU, loadavg, uptime),
* kontrola dostupnosti Home Assistantu a cílových zařízení (ping + TCP connect),
* sledování stavu `modbus_tcp_proxy.service`,
* jednoduchý watchdog datového toku přes standardizované bridge topicy.

---

## Co služba dělá

* Připojí se k MQTT brokeru (s volitelným user/pass).
* Publikuje (retain) měřené hodnoty do stromu topiců pod `MQTT_REPORT_BASE_TOPIC`.
* Publikuje MQTT Discovery (retain) pod `homeassistant/...`.
* Drží LWT (Last Will) pro `bridge/online`.
* Udržuje heartbeat a „flow“ indikaci:

  * `bridge/last_event_age_s`
  * `bridge/ws_flow_ok`

---

## Datový tok

Raspberry Pi (sběr metrik)
↓
mqtt_report
↓
MQTT Broker
↓
Home Assistant (MQTT Discovery + entity)

---

## Klíčové vlastnosti

* Robustní MQTT connect loop s exponenciálním backoffem
* MQTT LWT pro `bridge/online`
* Standardizované bridge topicy:

  * `bridge/online`
  * `bridge/last_event_age_s`
  * `bridge/ws_flow_ok`
* MQTT Discovery kompatibilní s Home Assistantem
* Optimalizované publikování (publish only on change)
* Volitelné debug JSON `report/snapshot` (doporučeno vypnout v produkci)

---

## MQTT topicy

### `{base}/sys/*` – systém

* `sys/cpu_temp_c`
* `sys/load_1m`, `sys/load_5m`, `sys/load_15m`
* `sys/uptime_s`

### `{base}/net/*` – síť

* `net/ping_ha_ok`, `net/ping_ha_ms`
* `net/ping_inverter_ok`, `net/ping_inverter_ms`
* `net/tcp_inverter_ok`, `net/tcp_inverter_ms`

### `{base}/proxy/*`

* `proxy/active`

### `{base}/bridge/*`

* `bridge/online` (LWT)
* `bridge/heartbeat_ts`
* `bridge/last_event_age_s`
* `bridge/ws_flow_ok`

### `{base}/report/*`

* `report/snapshot` (JSON – pouze debug, vypnout v produkci)

---

## Optimalizace publish (doporučeno)

### Princip

Hodnoty se publikují pouze pokud:

* dojde ke změně (nad definovanou toleranci), nebo
* uplyne interval (keepalive, default ~300 s)

### Výhody

* výrazné snížení zátěže MQTT
* menší síťový provoz
* menší zatížení Home Assistant Recorderu
* lepší škálovatelnost

### Použité funkce

* `publish_num_if_changed()`
* `publish_text_if_changed()`

### Doporučené tolerance

| Metrika       | tolerance |
| ------------- | --------- |
| cpu_temp_c    | 0.5 °C    |
| load          | 0.05      |
| uptime        | 300 s     |
| ping latency  | 2–5 ms    |
| binární stavy | 1         |

### Výjimky (publikují se vždy)

* `bridge/heartbeat_ts`

---

## Konfigurace (.env)

### MQTT

* `MQTT_REPORT_HOST`
* `MQTT_REPORT_PORT`
* `MQTT_REPORT_USER`
* `MQTT_REPORT_PASS`
* `MQTT_REPORT_BASE_TOPIC`
* `MQTT_REPORT_CLIENT_ID`

### Discovery

* `MQTT_REPORT_DISCOVERY_PREFIX` (default `homeassistant`)
* `MQTT_REPORT_DEVICE_ID`
* `MQTT_REPORT_DEVICE_NAME`
* `MQTT_REPORT_ENTITY_PREFIX`

### Diagnostika

* `MQTT_REPORT_PING_HA_HOST`
* `MQTT_REPORT_PING_INVERTER_HOST`
* `MQTT_REPORT_INVERTER_HOST`
* `MQTT_REPORT_INVERTER_PORT`
* `MQTT_REPORT_PROXY_SYSTEMD_UNIT`

### Intervaly

Doporučené hodnoty:

```
MQTT_REPORT_POLL_SYS_S=60
MQTT_REPORT_POLL_NET_S=30
MQTT_REPORT_POLL_PROXY_S=10
MQTT_REPORT_HEARTBEAT_S=30
```

Poznámka: publish probíhá jen při změně, ne při každém cyklu.

---

## Systemd služba

```
ExecStart=/usr/bin/python3 /opt/rpi-admin-ui/mqtt_report.py
EnvironmentFile=/opt/rpi-admin-ui/.env
Restart=on-failure
```

---

## Ověření funkčnosti

### MQTT

```
mosquitto_sub -h <broker> -u <user> -P <pass> -v -t 'rpi_report/#'
```

### Home Assistant

* zkontroluj zařízení
* sleduj `bridge/online`
* sleduj `ws_flow_ok`

---

## Troubleshooting

### Connection Refused

* špatné přihlašovací údaje
* chybějící ACL

### Starý snapshot v MQTT

```
mosquitto_pub -t "rpi_report/report/snapshot" -n -r
```

### Entity v HA neexistují

* zkontroluj discovery prefix
* případně smaž retained discovery

---

## Výkon a teplota Raspberry Pi

### Doporučené rozsahy

* <65 °C → ideální
* 65–75 °C → běžné
* > 80 °C → throttling

### Kontrola

```
vcgencmd measure_temp
vcgencmd get_throttled
```

### Doporučení

* pasivní chladič / ventilátor
* kvalitní napájení
* optimalizace publish (viz výše)

---

## Poznámky

* retained MQTT zprávy zůstávají i po restartu → nutné ručně mazat při změnách struktury
* snapshot JSON není vhodný pro produkční provoz
* publish-on-change je zásadní optimalizace pro dlouhodobý běh

---
