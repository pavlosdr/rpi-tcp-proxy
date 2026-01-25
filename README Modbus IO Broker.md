# Modbus IO Broker (modbus_io_broker)

## Účel služby
`modbus_io_broker` běží na Raspberry Pi a zajišťuje převod rychlých vstupů/výstupů z Modbus RTU sběrnice (RS485) do MQTT.
V praxi to znamená, že služba periodicky čte stavy IO modulů po RS485 a publikuje je jako MQTT *state* (pro spínače) a MQTT *event* (pro tlačítka).
Volitelně také vytváří entity v Home Assistantu pomocí MQTT Discovery.

### Co tím řeším?
Protože vstupy vypínačů a tlačítek prostřednictvím MODBUS sběrnice na RS485 není možné obsluhovat přímo z Home Assistant z důvodu omezení intervalu poolingu sběrnice MODBUS na min. 5 sekund. Takový interval není uživatelsky přijatelný, protože dochází k velkým prodlevám reakce na vypínače. Za tímto účelem je vytvořena samostatná sběrnice MODBUS, kde pooling může být v řádů ms. Na této sběrnici budou umístěna malá IO zařízení, která jako SLAVE reagují na dotazování MODBUS Mastera v podobě Raspberry. Takt dotazování MODBUS mimo HA tímto neovlivní vlastní výkon HA a umožní okamžité reakce.

## Co služba dělá
- Připojí se na Modbus RTU (RS485) linku a periodicky čte vstupy ze zadaných slave adres.
- Podle konfigurace rozlišuje **switch** (stav) vs. **button** (události).
- Transformuje stavy/události do MQTT topiců:
  - `.../state/<name>` (retained) pro stavy vstupů
  - `.../event/<name>` (non-retained) pro události tlačítek
- Publikuje provozní/health topicy ve „Infigy-normě“:
  - `bridge/online` (LWT)
  - `bridge/last_event_age_s`
  - `bridge/ws_flow_ok`
  - `bridge/heartbeat_ts`
- (Volitelně) publikuje Home Assistant MQTT Discovery konfiguraci (retained), aby se entity v HA vytvořily automaticky.

## Datový tok
Modbus RTU (RS485) IO moduly
        ↓ (polling)
modbus_io_broker
        ↓ (MQTT publish)
MQTT Broker
        ↓ (MQTT Discovery + entities)
Home Assistant

## Klíčové vlastnosti
- Robustní MQTT reconnect loop s exponenciálním backoffem.
- Heartbeat + watchdog (detekce „netečou data“).
- Oddělení stavu (state) a událostí (event) v MQTT.
- Debounce pro vstupy (separátně pro switch a button).
- Podpora automatické tvorby entity v HA (MQTT Discovery).

## MQTT topic struktura
Základ je `MODBUS_IO_MQTT_BASE_TOPIC` (default `modbus_io`).

### Stavové topicy (state)
- `modbus_io/state/<signal>`  
  Publikuje se `ON` / `OFF` jako **retained** (aby si HA po restartu hned načetl poslední stav).

Příklad signálu: `modbus_io/state/modbus_io_128_0`

### Událostní topicy (event)
- `modbus_io/event/<signal>`  
  Publikuje se krátká textová událost (např. `press`, `release`) jako **non-retained**.

Příklad: `modbus_io/event/modbus_io_129_0`

### Stav služby (Infigy-norma)
Služba publikuje standardizované topicy pro dohled:
- `modbus_io/bridge/online` – 1/0 podle dostupnosti služby (LWT).
- `modbus_io/bridge/last_event_age_s` – stáří posledního úspěšného publish cyklu (sekundy; -1 pokud ještě žádný cyklus neproběhl).
- `modbus_io/bridge/ws_flow_ok` – 1 pokud „tečou data“ (last_event_age_s <= MAX_AGE_OK_S), jinak 0.
- `modbus_io/bridge/heartbeat_ts` – epoch timestamp posledního heartbeat (informativní).

Tyto hodnoty se hodí pro:
- dashboard v Home Assistantu,
- alarmy/automatizace,
- externí watchdog (např. Telegram notifikace).

## Home Assistant MQTT Discovery
Pokud je zapnuto `MODBUS_IO_HA_DISCOVERY=1`, služba publikuje retained discovery config pod:
- `<discovery_prefix>/<domain>/<device_id>/<object_id>/config`  
  (default prefix je `homeassistant`).

V HA se pak automaticky vytvoří:
- `binary_sensor` pro stavy switch kanálů,
- `sensor` pro události tlačítek (event topic, `force_update: true`),
- bridge/health entity (online, last_event_age, ws_flow_ok).

Poznámka: Discovery config je *retained*. Pokud budete měnit názvy/device_id/entity prefix, je dobré nejdřív staré retained discovery topicy uklidit (v UI máte k tomu nástroj „Clean-up retained discovery config“).

## Konfigurace v `.env` (MODBUS_IO_*)
Níže jsou parametry, které služba čte z `.env`. Popisy vycházejí z aktuálního kódu `modbus_io_broker.py`.

### Základní přepínač
- `MODBUS_IO_ENABLED` (0/1)  
  1 = služba běží a publikuje, 0 = po startu se ukončí.

### MQTT připojení
- `MODBUS_IO_MQTT_HOST`  
  Host/IP MQTT brokeru.
- `MODBUS_IO_MQTT_PORT`  
  Port MQTT (typicky 1883).
- `MODBUS_IO_MQTT_USERNAME` / `MODBUS_IO_MQTT_PASSWORD`  
  Přihlašovací údaje (pokud broker vyžaduje autentizaci).
- `MODBUS_IO_MQTT_CLIENT_ID`  
  Client ID (musí být unikátní na brokeru).
- `MODBUS_IO_MQTT_BASE_TOPIC`  
  Base topic, pod kterým se publikuje (např. `modbus_io`).
- `MODBUS_IO_MQTT_RECONNECT_BACKOFF_MAX_S`  
  Maximum pro reconnect backoff (sekundy).
- `MODBUS_IO_MQTT_WATCHDOG_INTERVAL_S`  
  Interní watchdog interval pro MQTT (sekundy).

### Modbus RTU (RS485)
- `MODBUS_IO_MODBUS_PORT`  
  Cesta k sériovému portu (doporučeno `/dev/serial/by-id/...`).
- `MODBUS_IO_MODBUS_BAUDRATE`  
  Baudrate (např. 9600/19200/38400).
- `MODBUS_IO_MODBUS_PARITY`  
  Parita: `N`, `E`, `O`.
- `MODBUS_IO_MODBUS_STOPBITS`  
  Stop bity: `1` nebo `2`.
- `MODBUS_IO_MODBUS_BYTESIZE`  
  Datové bity: `7` nebo `8`.
- `MODBUS_IO_MODBUS_TIMEOUT`  
  Timeout Modbus operací (sekundy).

### Polling a debounce
- `MODBUS_IO_POLL_INTERVAL_S`  
  Pauza mezi dotazy (sekundy). Malé hodnoty zvyšují zátěž linky.
- `MODBUS_IO_DEBOUNCE_SWITCH_MS`  
  Debounce pro přepínače (ms).
- `MODBUS_IO_DEBOUNCE_BUTTON_MS`  
  Debounce pro tlačítka (ms).

### Topologie IO
- `MODBUS_IO_SLAVES`  
  CSV seznam slave adres (např. `128,129,130`).
- `MODBUS_IO_CHANNELS_PER_SLAVE`  
  Kolik kanálů má každý slave (např. 6).
- `MODBUS_IO_USED_CHANNELS`  
  Volitelné omezení kanálů ve formátu `slave:channel` (např. `128:0,128:1,129:0`).
  Pokud je prázdné, bere se celý rozsah dle `SLAVES` a `CHANNELS_PER_SLAVE`.
- `MODBUS_IO_DEFAULT_TYPE`  
  Výchozí typ kanálů: `switch` nebo `button`.
- `MODBUS_IO_BUTTONS`  
  Seznam kanálů, které se mají chovat jako tlačítko (event) ve formátu `slave:channel` (CSV).

### Home Assistant / identita
- `MODBUS_IO_HA_DISCOVERY` (0/1)  
  Zapnutí MQTT Discovery.
- `MODBUS_IO_HA_DISCOVERY_PREFIX`  
  Discovery prefix (typicky `homeassistant`).
- `MODBUS_IO_MQTT_DEVICE_ID`  
  Device ID pro HA device (např. `rpi-io`).
- `MODBUS_IO_MQTT_DEVICE_NAME`  
  Lidský název zařízení v HA.
- `MODBUS_IO_MQTT_ENTITY_PREFIX`  
  Prefix pro entity/object_id (např. `modbus_io`).

### Heartbeat / watchdog (Infigy-norma)
- `MODBUS_IO_HEARTBEAT_S`  
  Jak často publikovat heartbeat (sekundy).
- `MODBUS_IO_MAX_AGE_OK_S`  
  Max stáří posledního publish cyklu, kdy je `ws_flow_ok=1` (sekundy).

### Logování
- `MODBUS_IO_LOG_LEVEL`  
  `DEBUG` / `INFO` / `WARNING` / `ERROR`.

## Typická minimální konfigurace (.env)
```env
MODBUS_IO_ENABLED=1

MODBUS_IO_MQTT_HOST=192.168.1.20
MODBUS_IO_MQTT_PORT=1883
MODBUS_IO_MQTT_USERNAME=mqtt_bridge
MODBUS_IO_MQTT_PASSWORD=...
MODBUS_IO_MQTT_BASE_TOPIC=modbus_io
MODBUS_IO_MQTT_CLIENT_ID=modbus-io-broker-rpi

MODBUS_IO_MODBUS_PORT=/dev/serial/by-id/usb-...
MODBUS_IO_MODBUS_BAUDRATE=9600
MODBUS_IO_MODBUS_PARITY=N
MODBUS_IO_MODBUS_STOPBITS=1
MODBUS_IO_MODBUS_BYTESIZE=8
MODBUS_IO_MODBUS_TIMEOUT=0.5

MODBUS_IO_SLAVES=128,129
MODBUS_IO_CHANNELS_PER_SLAVE=6
MODBUS_IO_DEFAULT_TYPE=switch
MODBUS_IO_BUTTONS=128:0,129:0

MODBUS_IO_HA_DISCOVERY=1
MODBUS_IO_HA_DISCOVERY_PREFIX=homeassistant
MODBUS_IO_MQTT_DEVICE_ID=rpi-io-broker
MODBUS_IO_MQTT_DEVICE_NAME=RPi IO Broker
MODBUS_IO_MQTT_ENTITY_PREFIX=modbus_io
```
---

## Předpoklady (User Requirements)
- přístup na MQTT broker (dostupná z RPi + povolená autentizace user/password)
- shodně nastavená MODBUS RS485 sběrnice na straně Raspberry a jednotlivých IO zařízení na sběrnici
---
## Doporučené ověření funkčnosti
1. Ověřte, že MQTT broker přijímá publikace (na RPi nebo z jiné stanice):
   - `mosquitto_sub -h <broker> -u <user> -P <pass> -v -t 'modbus_io/#'`
2. Zkontrolujte bridge health topicy:
   - `modbus_io/bridge/online` = 1
   - `modbus_io/bridge/ws_flow_ok` = 1
3. Pokud je zapnuté discovery, zkontrolujte v HA, že se vytvořil device a entity (MQTT integrace musí být aktivní a Discovery povolené).

## Časté problémy
- **Ničeho se nepublikuje do MQTT**  
  Zkontrolujte `MODBUS_IO_ENABLED`, připojení na broker (host/port/credentials) a log (`journalctl -u modbus_io_broker.service -f`).
- **Chyby na Modbus RTU**  
  Zkontrolujte port `/dev/serial/by-id/...`, parametry linky (baud/parity/stopbits), kabeláž RS485, adresy slave.
- **„Drží se“ staré entity v HA**  
  Discovery config je retained. Použijte v UI funkci pro vylistování/mazání retained discovery topiců pro konkrétní `device_id`.

## Testy MODBUS
Součástí funkcí pro Raspberry jsou i systémové testy včetně `Modbus RTT test (RTU) - servisní`, který je dostupný prostřednictvím UI rozhraní služeb pod položkou menu `Síť`
Tento test měří čistou odezvu sběrnice RS485 Modbus připojenou k Raspberry. Aby test byl korektní, je potřeba zastavit službu `modbus_io_broker.service`. Slouží pro ověření správného zapojení, časování, počtu modulů na sběrnici. Parametry testu se nastavují v konfiguraci (je dostupné tlačítku, které zobrazí přímo sekci pro nastavení testu)