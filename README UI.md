# rpi-admin-ui (Web Application)

## Účel služby

**rpi-admin-ui** je centrální webová aplikace běžící na Raspberry Pi, která slouží jako administrační a diagnostické rozhraní pro celý ekosystém služeb (MQTT, Modbus, watchdog, reporting).

Aplikace poskytuje jednotné místo pro:

* správu systemd služeb,
* monitoring stavu systému a sítě,
* konfiguraci pomocí `.env` agend,
* diagnostické testy (síť, MQTT, Modbus).

---

## Co aplikace dělá

* Poskytuje **webové UI** (Flask aplikace)
* Autentizuje uživatele (jednoduchý login)
* Zobrazuje stav všech registrovaných služeb
* Umožňuje:

  * start / stop / restart služeb
  * náhled journal logů
  * náhled aplikačních logů (např. Modbus proxy)
* Umožňuje editaci `.env` konfigurace pomocí **agend**
* Obsahuje diagnostické nástroje:

  * ping testy
  * iperf test
  * MQTT latency test
  * Modbus RTT test
* Obsahuje správu **MQTT Discovery**:

  * výpis retained discovery configů
  * mazání (cleanup) retained MQTT Discovery entit

---

## Datový a řídicí tok

```
Uživatel (browser)
        ↓ HTTP
rpi-admin-ui (Flask)
        ↓
 systemd / OS / MQTT / Modbus
```

Aplikace sama **negeneruje provozní data**, ale:

* čte stav systému,
* ovládá služby,
* spouští testy,
* zprostředkovává konfiguraci.

---

## Klíčové vlastnosti

✅ Centrální řídicí bod celého řešení

✅ Bezpečné ovládání služeb přes systemd

✅ Jednotná práce s `.env` konfigurací

✅ Diagnostika sítě, MQTT a Modbus

✅ Správa MQTT Discovery entit (list + cleanup)

✅ Modulární architektura (monitor, services_control, agendas)

---

## MQTT Discovery – správa retained configů

Aplikace obsahuje speciální sekci **Settings → MQTT Discovery**, která umožňuje:

* zobrazit všechny retained discovery topic-y dané služby
* filtrovat podle `device_id` a názvu
* bezpečně **odstranit (cleanup)** staré nebo chybné discovery entity

Typické použití:

* změna DEVICE_ID
* změna struktury entit
* oprava rozbité konfigurace v Home Assistantu

⚠️ Mazání probíhá publishnutím `NULL payload + retain=True`.

---

## Konfigurace (.env)

Aplikace používá tyto klíčové proměnné:

### Základní nastavení

```env
UI_PORT=8080
UI_LOG_LEVEL=INFO
UI_SECRET=change-me
```

| Proměnná     | Význam                         |
| ------------ | ------------------------------ |
| UI_PORT      | Port, na kterém běží webové UI |
| UI_LOG_LEVEL | Úroveň logování aplikace       |
| UI_SECRET    | Secret key pro Flask session   |

---

### Logy Modbus proxy

```env
LOG_FILE=/var/log/modbus_proxy.log
```

Používá se pouze pro službu **modbus_tcp_proxy** – umožňuje náhled aplikačního logu přímo z UI.

---

## Správa služeb

Aplikace pracuje s definicemi v `SERVICES_META`:

* mapování service_id → systemd unit
* popisy, ikony, vazba na agendy

Podporované akce:

* start
* stop
* restart

Speciální režim:

* **odložený restart UI** (aby nedošlo k přerušení HTTP odpovědi)

---

## Bezpečnost

* Přístup chráněn loginem
* Hesla nejsou ukládána do UI
* Citlivé údaje jsou pouze v `.env`

Doporučení:

* omezit přístup pomocí firewallu
* používat VPN nebo LAN-only přístup

---

## Typické použití

* Denní monitoring stavu RPi
* Restart / kontrola služeb bez SSH
* Diagnostika problémů se sítí, MQTT, Modbus
* Čištění MQTT Discovery při vývoji
* Editace konfigurace bez ručního přepisu `.env`

---

## Vztah k ostatním službám

rpi-admin-ui **neprovádí business logiku**, ale:

* řídí služby:

  * infigy_ws_to_mqtt
  * modbus_io_broker
  * modbus_tcp_proxy
  * mqtt_report
  * ha_watchdog
* poskytuje nad nimi jednotnou kontrolní vrstvu

---

## Shrnutí

rpi-admin-ui je **řídicí a dohledová aplikace**, která výrazně zjednodušuje provoz, konfiguraci a ladění celého řešení založeného na Raspberry Pi, MQTT, Modbus a Home Assistantu.
