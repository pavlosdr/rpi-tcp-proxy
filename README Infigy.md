# Infigy WS → MQTT (infigy_ws_to_mqtt)

Služba **`infigy_ws_to_mqtt`** zajišťuje spolehlivý přenos dat z platformy **Infigy** (Socket.IO / WebSocket) do **MQTT** a jejich automatickou integraci do **Home Assistantu** pomocí **MQTT Discovery**.

Infigy jako chytré řízení fotovoltaické elektrárny nemá zveřejněné api, prostřednictvím kterého by bylo možné jeho stavy zařadit do Home Assistant a proto jsem vyrobil tuto službu, která umí vytěžit websocket poskytované aplikací. Například teplota vody v boileru je jedna z hodnot, kterou vytěžuji pro další řízení v HA.Služba jen naslouchá, co Infigy autonomně zveřejňuje a neovlivňuje, jaké informace chce získat. Proto například v případě SCO baterie se aktualizuje méně často, ale není to vada služby jako takové.
V rámci služby se počítají kumulované výstupy pod topicem infigy/energy/... ze získaných okamžitých dat pomocí integrátoru. 
---

## Účel služby

- Připojí se k Infigy backendu přes **WebSocket / Socket.IO**
- Přijímá energetická a provozní data (výroba, spotřeba, stavy, teploty)
- Transformuje je do MQTT zpráv (state + metriky)
- Publikuje:
  - **stavové hodnoty** (state topicy)
  - **heartbeat / watchdog** topicy (health)
  - **Home Assistant MQTT Discovery** konfigurace (retained)

---

## Datový tok

```text
Infigy (WS / Socket.IO)
        ↓
infigy_ws_to_mqtt
        ↓
MQTT Broker
        ↓
Home Assistant (MQTT Discovery + entity)
```

---

## Klíčové vlastnosti

- Robustní reconnect loop (Socket.IO) + **exponenciální backoff** (MQTT reconnect)
- Heartbeat a watchdog (detekce výpadku datového toku)
- Podpora autentizace:
  - Cookie
  - Bearer token
- Automatická tvorba entit v Home Assistantu (**MQTT Discovery**, retained config)
- Oddělení transportní vrstvy (WS) a aplikační vrstvy (MQTT)

---

## Stavové a watchdog topicy (Infigy-norma)

Služba publikuje standardizované topicy (základ = `INFIGY_MQTT_BASE`):

- `bridge/online`  
  Dostupnost služby (LWT).  
  - `1` = běží a je připojena k MQTT  
  - `0` = offline (typicky při pádu procesu / ztrátě spojení)

- `bridge/last_event_age_s`  
  Doba od posledního validního datového eventu v sekundách.

- `bridge/ws_flow_ok`  
  Indikace, zda „tečou data“ z Infigy (1 = ano / 0 = ne).  
  Typicky se vyhodnocuje proti `INFIGY_HEARTBEAT_MAX_AGE_S`.

Tyto hodnoty se hodí pro:
- alarmy v Home Assistantu,
- dohled a notifikace (např. watchdog služba),
- rychlou diagnostiku „jede / nejede tok dat“.

---

## Typické použití

- Integrace Infigy energetických dat do Home Assistantu
- Monitoring výroby / spotřeby / baterie / bojleru
- Základní dohled nad dostupností Infigy služby a kvalitou dat
- Zdroj pro automatizace (např. řízení zátěže podle přebytků)

---

## Konfigurace (.env)

Níže jsou hlavní parametry používané v aktuální verzi skriptu.

### MQTT připojení

- `INFIGY_MQTT_HOST`  
  Hostname/IP MQTT brokeru (např. `192.168.1.20` nebo `core-mosquitto`).

- `INFIGY_MQTT_PORT`  
  Port MQTT (standardně `1883`).

- `INFIGY_MQTT_USER`, `INFIGY_MQTT_PASS`  
  Přihlašovací údaje, pokud broker vyžaduje autentizaci.

- `INFIGY_MQTT_BASE`  
  Base topic pro publikování dat (např. `infigy`).  
  Vše se publikuje pod:  
  `INFIGY_MQTT_BASE/<subtopic>` (např. `infigy/bridge/online`).

- `INFIGY_MQTT_CLIENT_ID`  
  Identita klienta na brokeru. Musí být **unikátní**.

- `INFIGY_MQTT_WATCHDOG_INTERVAL_S`  
  Interval interního MQTT watchdogu:
  - kontrola, zda je klient připojený,
  - pokus o reconnect,
  - zajištění běhu `loop_start()` threadu.

- `INFIGY_MQTT_RECONNECT_BACKOFF_MAX_S`  
  Max. backoff při opakovaných reconnect pokusech.

### Infigy připojení (Socket.IO)

- `INFIGY_HOST`  
  URL Infigy endpointu (např. `http://10.10.100.x`).

- `INFIGY_SOCKET_PATH`  
  Cesta Socket.IO endpointu (typicky `/socket.io`).

### Autentizace

- `INFIGY_AUTH_COOKIE`  
  Cookie string (pokud Infigy používá cookie auth).  
  Nastaví se do HTTP hlavičky `Cookie`.

- `INFIGY_AUTH_BEARER`  
  Bearer token (pokud Infigy používá token auth).  
  Nastaví se do hlavičky `Authorization: Bearer ...`.

> Poznámka: použij jen to, co opravdu potřebuješ. Pokud je Infigy dostupné v LAN bez auth, nech obě prázdné.

### Home Assistant MQTT Discovery

- `INFIGY_MQTT_DISCOVERY_PREFIX`  
  Prefix pro discovery topic (typicky `homeassistant`).

- `INFIGY_MQTT_DEVICE_ID`  
  Identifikátor zařízení v HA (stabilní). Používá se i v discovery topic struktuře.

- `INFIGY_MQTT_DEVICE_NAME`  
  Lidsky čitelný název zařízení v HA.

- `INFIGY_MQTT_ENTITY_PREFIX`  
  Prefix pro `object_id` / `unique_id` entit.  
  Doporučení: držet stabilně, ať se entity v HA „nepřejmenují“.

### Energie + integrátor

- `INFIGY_ENERGY_STATE_PATH`  
  Cesta k JSON souboru pro persist integrovaných energií (kWh).  
  Používá se pro přežití restartu služby.

- `INFIGY_ENERGY_PUBLISH_INTERVAL_S`  
  Jak často publikovat integrované energie + uložit je do souboru.

- `INFIGY_INTEGRATOR_TICK_S`  
  Perioda integrátoru (výpočet kWh z W pomocí trapézové aproximace).

### Heartbeat a watchdog Infigy dat

- `INFIGY_HEARTBEAT_MAX_AGE_S`  
  Max. stáří datového eventu (`store:change`), po kterém:
  - `bridge/ws_flow_ok` přejde na 0,
  - WS watchdog vyvolá reconnect Socket.IO.

### Logging

- `INFIGY_LOG_LEVEL`  
  Úroveň logování: `DEBUG`, `INFO`, `WARNING`, `ERROR`.

---

## Doporučená minimální ukázka .env

```ini
# MQTT
INFIGY_MQTT_HOST=192.168.1.20
INFIGY_MQTT_PORT=1883
INFIGY_MQTT_USER=
INFIGY_MQTT_PASS=
INFIGY_MQTT_BASE=infigy
INFIGY_MQTT_CLIENT_ID=infigy-bridge
INFIGY_MQTT_WATCHDOG_INTERVAL_S=15
INFIGY_MQTT_RECONNECT_BACKOFF_MAX_S=60

# Infigy
INFIGY_HOST=http://10.10.100.10
INFIGY_SOCKET_PATH=/socket.io

# Auth (nepovinné)
INFIGY_AUTH_COOKIE=
INFIGY_AUTH_BEARER=

# HA discovery
INFIGY_MQTT_DISCOVERY_PREFIX=homeassistant
INFIGY_MQTT_DEVICE_ID=rpi-3b-broker-infigy
INFIGY_MQTT_DEVICE_NAME=Raspberry 3B broker - Infigy
INFIGY_MQTT_ENTITY_PREFIX=rpi_broker_infigy

# Energy
INFIGY_ENERGY_STATE_PATH=/opt/rpi-admin-ui/energy_state.json
INFIGY_ENERGY_PUBLISH_INTERVAL_S=30
INFIGY_INTEGRATOR_TICK_S=5

# Heartbeat
INFIGY_HEARTBEAT_MAX_AGE_S=180

# Logging
INFIGY_LOG_LEVEL=INFO
```

---

## Předpoklady (User Requirements)
- přístup na MQTT broker (dostupná z RPi + povolená autentizace user/password)
- přístup na websocket Infigy z Rpi (síť mimo HA typicky 10.10.100.xxx) - v mém případě řešeno přes wifi
---

## Provoz a ověření funkčnosti

### 1) Ověření MQTT topiců (rychlá kontrola)

```bash
mosquitto_sub -h <MQTT_HOST> -p <MQTT_PORT> -u <USER> -P <PASS> -v -t 'infigy/#'
```

Očekávej např.:
- `infigy/bridge/online`
- `infigy/bridge/last_event_age_s`
- `infigy/bridge/ws_flow_ok`

### 2) Ověření v Home Assistantu
- V HA musí běžet **MQTT integrace** a být zapnuté discovery (standardně ano).
- Po startu služby se objeví nové zařízení dle `INFIGY_MQTT_DEVICE_ID`.
