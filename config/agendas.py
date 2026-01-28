"""
Agendas registry & metadata

Obsahuje metadata pro UI, systemd a konfigurační vazby. Podle nastavení se zobrazují
konfigurační parametry v jednotlivých záložkách a sekcích

Funkce:
- Popisy, ikony a cesty ke konfiguraci
- Zdroj pravdy pro UI přehled a správu služeb

Používáno:
- app.py (UI)
- monitor.py
- ha_watchdog.py
"""
from __future__ import annotations

AGENDAS = {
    "io-modbus-mqtt": {
        "title": "RPi IO - MODBUS <-> MQTT broker",
        "description": "Konfigurace modbus_io_broker (.env) generovana z metadat.",
        "env_path": "/opt/rpi-admin-ui/.env",
        "service_id": "modbus-io-broker",
        "auto_prefix": "MODBUS_IO_",

        "tabs": [
            {"id": "basic",  "label": "Basic"},
            {"id": "mqtt",   "label": "MQTT"},
            {"id": "modbus", "label": "Modbus RTU"},
            {"id": "iomap",  "label": "IO map"},
            {"id": "diag",   "label": "Diagnostika"},
            {"id": "logging", "label": "Logging"},
            {"id": "other",  "label": "Ostatni"},
        ],

        "sections": [
            {"id": "basic_main", "tab": "basic", "label": "Obecné", 
             "tooltip": "Zakladní přepínač agendy. Pokud je broker vypnutý (0), po startu se ukončí a nic nepublikuje do MQTT." },
            {"id": "mqtt_conn", "tab": "mqtt", "label": "Připojení k MQTT brokeru",
              "tooltip": "Parametry pripojeni k MQTT brokeru (host, port, volitelne prihlaseni). Bez spravneho pripojeni broker nepublikuje stavy ani udalosti."},
            {"id": "mqtt_topic", "tab": "mqtt", "label": "Topic pro publikaci dat",
              "tooltip": "Základní namespace topicu. Base topic určuje, kam broker publikuje state/event."},
            {"id": "device_main","tab": "mqtt",  "label": "Identita MQTT zařízení",
            "tooltip": "Jak se RPi prezentuje v HA/MQTT (device info). DOPORUČENÍ: Shodné ID pro všechny služby na RPi3, aby šlo v HA o jedno zařízení"},
            {"id": "modbus_rtu", "tab": "modbus", "label": "Parametry linky", 
             "tooltip": "Nastaveni seriove Modbus RTU linky (port, baudrate, parita, stopbity, bytesize, timeout). Pri chybach zkontroluj port /dev/serial/by-id a parametry linky na IO modulech.",},
            {"id": "poll_debounce", "tab": "modbus", "label": "Polling / debounce", 
             "tooltip": "Rychlost dotazovani a odruseni vstupu. Poll interval urcuje pauzu mezi dotazy, debounce filtruje zakmit tlacitek/vypinacu. Prilis male hodnoty mohou zatezovat sbernici nebo delat falešne prechody.",},
            {"id": "iomap_main", "tab": "iomap", "label": "Topologie / mapovani", 
             "tooltip": "Definice slave adres a kanalu. Slaves + channels-per-slave urcuji topologii. Used channels omezi, ktere vstupy se vubec zpracovavaji. Buttons prepinaji vybrane kanaly do rezimu tlacitka (event).",},
            {"id": "diag_mqtt", "tab": "diag", "label": "MQTT latency test",
             "tooltip": "Nastaveni diagnostickeho testu latence MQTT (pocet vzorku, interval, timeout). Test meri publish -> broker -> receive v ramci stejneho klienta (loopback pres broker).",},
            {"id": "diag_mqtt_thr", "tab": "diag", "label": "MQTT latency semafor",
             "tooltip": "Prahy pro vyhodnoceni latence (OK/WARN) a topic prefix pro diagnostiku. UI pouzije oddeleny prefix, aby se nepletly produkcni topicy s diagnostikou.",},
            {"id": "diag_modbus", "tab": "diag", "label": "Modbus RTT test",
             "tooltip": "Nastaveni RTT testu sbernice Modbus (metoda, adresa, count, interval, prahy OK/WARN). Slouzi k overeni odezvy IO modulu a stability linky.",},
            {"id": "ha_pub", "tab": "mqtt", "label": "Publikovaní entit do HA",
             "tooltip": "Home Assistant MQTT Discovery. Zapnutim se generuji discovery entity pod prefixem (typicky homeassistant). Pokud mas discovery vypnute, entity si musis definovat rucne v HA.",},
            {"id": "other_auto", "tab": "other", "label": "Ostatni (detekovano)", "description": "Klice MODBUS_IO_* nalezene v .env, ktere nejsou explicitne v konfiguraci (read-only).",
             "tooltip": "Automaticky detekovane klice MODBUS_IO_* z .env, ktere nemaji metadata. Zobrazuji se pouze pro prehled (read-only), aby se nic omylem neupravilo."},
            {"id": "log_main",    "tab": "logging", "label": "Logování",
            "tooltip": "Nastavení logu - úroveň"},
        ],

        "fields": [
            # -----------------
            # BASIC
            # -----------------
            {"key": "MODBUS_IO_ENABLED", "label": "Enabled", "tab": "basic", "section": "basic_main",
             "type": "select", "choices": ["0", "1"], "required": True, "help": "1 = zapnuto, 0 = vypnuto",
             "tooltip": "1 = broker aktivni, 0 = broker se po startu ukonci (vypnuto)."},

            # -----------------
            # MQTT
            # -----------------
            {"key": "MODBUS_IO_MQTT_HOST", "label": "Host", "tab": "mqtt", "section": "mqtt_conn",
             "type": "str", "required": True, "placeholder": "192.168.1.20",
             "tooltip": "Hostname/IP MQTT brokeru (napr. 192.168.1.20 nebo core-mosquitto)."},
            {"key": "MODBUS_IO_MQTT_PORT", "label": "Port", "tab": "mqtt", "section": "mqtt_conn",
             "type": "int", "required": True, "min": 1, "max": 65535, "placeholder": "1883",
             "tooltip": "Port MQTT (obvykle 1883)."},
            {"key": "MODBUS_IO_MQTT_USERNAME", "label": "Username", "tab": "mqtt", "section": "mqtt_conn",
             "type": "str", "required": False,
             "tooltip": "Uzivatel pro MQTT."},
            {"key": "MODBUS_IO_MQTT_PASSWORD", "label": "Password", "tab": "mqtt", "section": "mqtt_conn",
             "type": "secret", "required": False, "help": "Nech prazdne = heslo se nezmeni.",
             "tooltip": "Heslo pro MQTT."},
            {"key": "MODBUS_IO_MQTT_CLIENT_ID", "label": "Client ID", "tab": "mqtt", "section": "mqtt_conn",
             "type": "str", "required": True, "placeholder": "modbus-io-rpi3",
             "tooltip": "Client ID pro MQTT (musi byt unikátni v ramci brokeru)."},
            {"key": "MODBUS_IO_HA_DISCOVERY_PREFIX", "label": "Discovery prefix pro HA", "tab": "mqtt", "section": "mqtt_conn",
             "type": "str", "required": True, "placeholder": "homeassistant",
             "tooltip": "Prefix discovery topicu (typicky homeassistant)."},             

            {"key": "MODBUS_IO_MQTT_BASE_TOPIC", "label": "Base topic", "tab": "mqtt", "section": "mqtt_topic",
             "type": "str", "required": False, "placeholder": "modbus_io",
             "tooltip": "Base topic (napr. modbus_io). Broker publikuje do: base/state/* a base/event/*."},
            # MQTT device
            {"key": "MODBUS_IO_MQTT_DEVICE_ID", "label": "Device ID", "tab": "mqtt", "section": "device_main",
            "type": "str", "required": True, "placeholder": "rpi-3b-broker",
            "tooltip": "Identifikator zarizeni pro discovery (device id)."},
            {"key": "MODBUS_IO_MQTT_DEVICE_NAME", "label": "Device name", "tab": "mqtt", "section": "device_main",
            "type": "str", "required": True, "placeholder": "Raspberry 3B broker",
            "tooltip": "Lidsky čitelný název zařízení."},
            # MQTT publikování do HA
            {"key": "MODBUS_IO_HA_DISCOVERY", "label": "HA discovery", "tab": "mqtt", "section": "ha_pub",
             "type": "select", "choices": ["0", "1"], "required": True,
             "tooltip": "1 = publikovat Home Assistant MQTT Discovery, 0 = nevytvaret discovery entity."},            
            {"key": "MODBUS_IO_MQTT_ENTITY_PREFIX", "label": "Entity prefix", "tab": "mqtt", "section": "ha_pub",
            "type": "str", "required": False, "placeholder": "rpi_broker_modbus_io",
            "tooltip": "Prefix pro entity/senzory v HA (napr. rpi_broker_modbus_io...)."},
            # -----------------
            # LOGGING
            # -----------------
            {"key": "MODBUS_IO_LOG_LEVEL", "label": "Log level", "tab": "logging", "section": "log_main",
            "type": "select", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"], "required": True,
            "tooltip": "Uroven logu. INFO pro bezny provoz, DEBUG pri ladeni."},

            # -----------------
            # MODBUS RTU
            # -----------------
            {"key": "MODBUS_IO_MODBUS_PORT", "label": "Port", "tab": "modbus", "section": "modbus_rtu",
             "type": "str", "required": True, "placeholder": "/dev/serial/by-id/...",
             "tooltip": "Seriovy port (doporuceno /dev/serial/by-id/...)."},
            {"key": "MODBUS_IO_MODBUS_BAUDRATE", "label": "Baudrate", "tab": "modbus", "section": "modbus_rtu",
             "type": "int", "required": True, "min": 1200, "max": 1000000, "placeholder": "9600",
             "tooltip": "Baudrate Modbus RTU (napr. 9600, 19200, 38400...)."},
            {"key": "MODBUS_IO_MODBUS_PARITY", "label": "Parity", "tab": "modbus", "section": "modbus_rtu",
             "type": "select", "choices": ["N", "E", "O"], "required": True,
             "tooltip": "Parita seriove linky: N = none, E = even, O = odd."},
            {"key": "MODBUS_IO_MODBUS_STOPBITS", "label": "Stopbits", "tab": "modbus", "section": "modbus_rtu",
             "type": "select", "choices": ["1", "2"], "required": True,
             "tooltip": "Pocet stop bitu (typicky 1)."},
            {"key": "MODBUS_IO_MODBUS_BYTESIZE", "label": "Bytesize", "tab": "modbus", "section": "modbus_rtu",
             "type": "select", "choices": ["7", "8"], "required": True,
             "tooltip": "Pocet datovych bitu (typicky 8)."},
            {"key": "MODBUS_IO_MODBUS_TIMEOUT", "label": "Timeout (s)", "tab": "modbus", "section": "modbus_rtu",
             "type": "float", "required": True, "min": 0.01, "max": 30.0, "placeholder": "0.5",
             "tooltip": "Timeout pro Modbus operace (sekundy). Doporuceni 0.2-0.8."},

            # Polling / debounce
            {"key": "MODBUS_IO_POLL_INTERVAL_S", "label": "Poll interval (s)", "tab": "modbus", "section": "poll_debounce",
             "type": "float", "required": True, "min": 0.001, "max": 10.0, "placeholder": "0.03",
             "tooltip": "Pauza mezi dotazy (sekundy). Efektivni rychlost na jeden slave je zhruba: POLL_INTERVAL_S x pocet slave."},
            {"key": "MODBUS_IO_DEBOUNCE_SWITCH_MS", "label": "Debounce switch (ms)", "tab": "modbus", "section": "poll_debounce",
             "type": "int", "required": True, "min": 0, "max": 5000, "placeholder": "60",
             "tooltip": "Debounce pro vypinace (ms). Doporuceni 40-80."},
            {"key": "MODBUS_IO_DEBOUNCE_BUTTON_MS", "label": "Debounce button (ms)", "tab": "modbus", "section": "poll_debounce",
             "type": "int", "required": True, "min": 0, "max": 5000, "placeholder": "15",
             "tooltip": "Debounce pro tlacitka (ms). Doporuceni 10-25."},

            # -----------------
            # IO MAP
            # -----------------
            {"key": "MODBUS_IO_SLAVES", "label": "Slaves", "tab": "iomap", "section": "iomap_main",
             "type": "str", "required": True, "placeholder": "128,129,130",
             "help": "CSV seznam slave adres (napr. 128,129,130).",
             "tooltip": "Seznam slave ID oddeleny carkou (napr. 128,129,130)."},
            {"key": "MODBUS_IO_CHANNELS_PER_SLAVE", "label": "Channels per slave", "tab": "iomap", "section": "iomap_main",
             "type": "int", "required": True, "min": 1, "max": 64, "placeholder": "6",
             "tooltip": "Pocet vstupu na jednom IO modulu (u tebe 6)."},
            {"key": "MODBUS_IO_DEFAULT_TYPE", "label": "Default type", "tab": "iomap", "section": "iomap_main",
             "type": "select", "choices": ["switch", "button"], "required": True,
             "tooltip": "Vychozi typ vsech vstupu. Vyjimky definuj v MODBUS_IO_BUTTONS (nebo pozdeji MODBUS_IO_SWITCHES)."},

            {"key": "MODBUS_IO_USED_CHANNELS", "label": "Used channels", "tab": "iomap", "section": "iomap_main",
             "type": "text", "rows": 3, "required": False,
             "placeholder": "128:0,128:1,129:0,...",
             "help": "Seznam aktivnich kanalu (zbytek se ignoruje).",
             "tooltip": "Seznam aktivnich kanalu ve formatu slave:kanal oddeleny carkami. Zbytek se ignoruje (zadne MQTT, zadne discovery)."},
            {"key": "MODBUS_IO_BUTTONS", "label": "Buttons", "tab": "iomap", "section": "iomap_main",
             "type": "text", "rows": 2, "required": False, "placeholder": "130:0",
             "tooltip": "Seznam tlacitek ve formatu slave:kanal oddeleny carkami. Napr. 128:2,129:1,130:0,130:1"},

            # -----------------
            # DIAGNOSTIKA - MQTT latency
            # -----------------
            {"key": "MODBUS_IO_MQTT_LATENCY_COUNT", "label": "Count", "tab": "diag", "section": "diag_mqtt",
             "type": "int", "required": True, "min": 1, "max": 10000, "placeholder": "10",
             "tooltip": "Pocet vzorku testu (samples). Doporuceni 10-30."},
            {"key": "MODBUS_IO_MQTT_LATENCY_INTERVAL_MS", "label": "Interval (ms)", "tab": "diag", "section": "diag_mqtt",
             "type": "int", "required": True, "min": 1, "max": 60000, "placeholder": "50",
             "tooltip": "Interval mezi vzorky v ms. 0 = bez pauzy. Doporuceni 50-200."},
            {"key": "MODBUS_IO_MQTT_LATENCY_TIMEOUT_S", "label": "Timeout (s)", "tab": "diag", "section": "diag_mqtt",
             "type": "float", "required": True, "min": 0.1, "max": 60.0, "placeholder": "2.0",
             "tooltip": "Timeout testu v sekundach (cekani na connect/subscribe a dozneni odpovedi)."},

            {"key": "MODBUS_IO_MQTT_LATENCY_OK_MS", "label": "OK (ms)", "tab": "diag", "section": "diag_mqtt_thr",
             "type": "int", "required": True, "min": 0, "max": 60000, "placeholder": "20",
             "tooltip": "Limit pro OK (ms). Pokud avg/p95 prekroci, spadne to do VAROVANI."},
            {"key": "MODBUS_IO_MQTT_LATENCY_WARN_MS", "label": "WARN (ms)", "tab": "diag", "section": "diag_mqtt_thr",
             "type": "int", "required": True, "min": 0, "max": 60000, "placeholder": "100",
             "tooltip": "Limit pro SPATNE (ms). Pokud avg/p95 prekroci, je to BAD (cervena)."},
            {"key": "MODBUS_IO_MQTT_LATENCY_TOPIC_PREFIX", "label": "Topic prefix", "tab": "diag", "section": "diag_mqtt_thr",
             "type": "str", "required": False, "placeholder": "diag/mqtt_latency",
             "tooltip": "Prefix topicu pro diagnostiku. UI pouzije topic: prefix/<run_id>. Doporuceni: diag/mqtt_latency"},

            # -----------------
            # DIAGNOSTIKA - Modbus RTT
            # -----------------
            {"key": "MODBUS_IO_MODBUS_RTT_SAMPLES", "label": "Samples", "tab": "diag", "section": "diag_modbus",
             "type": "int", "required": True, "min": 1, "max": 10000, "placeholder": "30",
             "tooltip": "Pocet vzorku RTT testu (dotaz/odpoved)."},
            {"key": "MODBUS_IO_MODBUS_RTT_INTERVAL_MS", "label": "Interval (ms)", "tab": "diag", "section": "diag_modbus",
             "type": "int", "required": True, "min": 1, "max": 60000, "placeholder": "50",
             "tooltip": "Interval mezi RTT vzorky v ms."},
            {"key": "MODBUS_IO_MODBUS_RTT_METHOD", "label": "Method", "tab": "diag", "section": "diag_modbus",
             "type": "select", "choices": ["di", "coils", "holding", "input"], "required": True,
             "tooltip": "Typ dotazu pro RTT test (di = discrete inputs, coils, holding, input)."},
            {"key": "MODBUS_IO_MODBUS_RTT_ADDR", "label": "Addr", "tab": "diag", "section": "diag_modbus",
             "type": "int", "required": True, "min": 0, "max": 65535, "placeholder": "0",
             "tooltip": "Adresa registru pro RTT test."},
            {"key": "MODBUS_IO_MODBUS_RTT_COUNT", "label": "Count", "tab": "diag", "section": "diag_modbus",
             "type": "int", "required": True, "min": 1, "max": 125, "placeholder": "1",
             "tooltip": "Kolik hodnot cist v RTT testu (typicky 1)."},
            {"key": "MODBUS_IO_MODBUS_RTT_OK_MS", "label": "OK (ms)", "tab": "diag", "section": "diag_modbus",
             "type": "int", "required": True, "min": 0, "max": 60000, "placeholder": "50",
             "tooltip": "Limit pro OK RTT (ms)."},
            {"key": "MODBUS_IO_MODBUS_RTT_WARN_MS", "label": "WARN (ms)", "tab": "diag", "section": "diag_modbus",
             "type": "int", "required": True, "min": 0, "max": 60000, "placeholder": "150",
             "tooltip": "Limit pro spatne RTT (ms)."},

        ],

        "diagnostics": [
            {
                "id": "mqtt_latency",
                "title": "MQTT latency test",
                "tab": "diag",
                "section": "diag_mqtt",
                # co ukazat na karte (label, key, suffix)
                "params": [
                    {"label": "Count", "key": "MODBUS_IO_MQTT_LATENCY_COUNT"},
                    {"label": "Interval", "key": "MODBUS_IO_MQTT_LATENCY_INTERVAL_MS", "suffix": "ms"},
                    {"label": "Timeout", "key": "MODBUS_IO_MQTT_LATENCY_TIMEOUT_S", "suffix": "s"},
                ],
                "thresholds": [
                    {"label": "OK/WARN", "keys": ["MODBUS_IO_MQTT_LATENCY_OK_MS", "MODBUS_IO_MQTT_LATENCY_WARN_MS"], "suffix": "ms"},
                ],
            },
            {
                "id": "modbus_rtt",
                "title": "Modbus RTT test",
                "tab": "diag",
                "section": "diag_modbus",
                "params": [
                    {"label": "Samples", "key": "MODBUS_IO_MODBUS_RTT_SAMPLES"},
                    {"label": "Interval", "key": "MODBUS_IO_MODBUS_RTT_INTERVAL_MS", "suffix": "ms"},
                    {"label": "Method", "key": "MODBUS_IO_MODBUS_RTT_METHOD"},
                    {"label": "Addr", "key": "MODBUS_IO_MODBUS_RTT_ADDR"},
                    {"label": "Count", "key": "MODBUS_IO_MODBUS_RTT_COUNT"},
                ],
                "thresholds": [
                    {"label": "OK/WARN", "keys": ["MODBUS_IO_MODBUS_RTT_OK_MS", "MODBUS_IO_MODBUS_RTT_WARN_MS"], "suffix": "ms"},
                ],
            },
        ],
    },
    "infigy_ws_to_mqtt" : {
        "title": "Infigy WS -> MQTT",
        "description": "Konfigurace sluzby infigy_ws_to_mqtt (websocket -> MQTT + HA discovery).",
        "env_path": "/opt/rpi-admin-ui/.env",
        "service_id": "infigy-mqtt",   # dulezite: shodne se systemd unit bez .service
        "auto_prefix": "INFIGY_",            # jen pro 'other_auto' (muze byt i prazdne, ale prefix je lepsi)

        "tabs": [
            {"id": "mqtt",   "label": "MQTT"},
            {"id": "infigy", "label": "Infigy"},
            {"id": "auth",   "label": "Auth"},
            {"id": "timing", "label": "Timing"},
            {"id": "logging", "label": "Logging"},
            {"id": "other",  "label": "Ostatni"},
        ],

        "sections": [
            {"id": "mqtt_conn", "tab": "mqtt", "label": "Připojení k MQTT brokeru",
            "tooltip": "Parametry pripojeni k MQTT brokeru (host/port/uzivatel/heslo)."},
            {"id": "mqtt_ident", "tab": "mqtt", "label": "Topic pro publikaci dat",
            "tooltip": "Base topic pro publikovani dat."},
            {"id": "device_main","tab": "mqtt",  "label": "Identita MQTT zařízení",
            "tooltip": "Jak se RPi prezentuje v HA/MQTT (device info). DOPORUČENÍ: Shodné ID pro všechny služby na RPi3, aby šlo v HA o jedno zařízení"},
            {"id": "mqtt_reliability", "tab": "mqtt", "label": "Spolehlivost / watchdog",
            "tooltip": "Parametry watchdogu a reconnect backoffu."},
            {"id": "infigy_conn", "tab": "infigy", "label": "Pripojeni na Infigy",
            "tooltip": "Cilovy host a lokalni socket (pokud se pouziva)."},
            {"id": "auth_main", "tab": "auth", "label": "Autentizace",
            "tooltip": "Cookie/Bearer pro pristup. Pouzij jen jednu metodu, podle implementace sluzby."},
            {"id": "timing_main", "tab": "timing", "label": "Heartbeat",
            "tooltip": "Heartbeat age."},
            {"id": "log_main",    "tab": "logging", "label": "Logování",
            "tooltip": "Nastavení logu - úroveň"},
            {"id": "other_auto", "tab": "other", "label": "Ostatni (detekovano)",
            "description": "Automaticky detekované klíče INFIGY_* z .env, které nejsou explicitně v konfiguraci (read-only)."},
            {"id": "ha_pub", "tab": "mqtt", "label": "Publikovaní entit do HA",
             "tooltip": "Home Assistant MQTT Discovery.",},
        ],

        "fields": [
            # -----------------
            # MQTT / conn
            # -----------------
            {"key": "INFIGY_MQTT_HOST", "label": "Host", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": True, "placeholder": "192.168.1.20",
            "tooltip": "Hostname/IP MQTT brokeru (napr. 192.168.1.20 nebo core-mosquitto)."},
            {"key": "INFIGY_MQTT_PORT", "label": "Port", "tab": "mqtt", "section": "mqtt_conn",
            "type": "int", "required": True, "min": 1, "max": 65535, "placeholder": "1883",
            "tooltip": "Port MQTT (obvykle 1883)."},
            {"key": "INFIGY_MQTT_USER", "label": "Username", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": False,
            "tooltip": "Uzivatel pro MQTT (pokud broker vyzaduje)."},
            {"key": "INFIGY_MQTT_PASS", "label": "Password", "tab": "mqtt", "section": "mqtt_conn",
            "type": "secret", "required": False,
            "tooltip": "Heslo pro MQTT (pokud broker vyzaduje)."},
            {"key": "INFIGY_MQTT_CLIENT_ID", "label": "Client ID", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": True, "placeholder": "infigy-rpi3",
            "tooltip": "Client ID pro MQTT (musi byt unikátní v ramci brokeru)."},
            {"key": "INFIGY_MQTT_DISCOVERY_PREFIX", "label": "Discovery prefix pro HA", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": True, "placeholder": "homeassistant",
            "tooltip": "Prefix pro HA MQTT Discovery (typicky homeassistant)."},            

            # MQTT identita / topic
            {"key": "INFIGY_MQTT_BASE", "label": "Base topic", "tab": "mqtt", "section": "mqtt_ident",
            "type": "str", "required": True, "placeholder": "infigy",
            "tooltip": "Zakladni topic pro publikovani dat (např. infigy)."},

            # MQTT device
            {"key": "INFIGY_MQTT_DEVICE_ID", "label": "Device ID", "tab": "mqtt", "section": "device_main",
            "type": "str", "required": True, "placeholder": "rpi-3b-broker",
            "tooltip": "Identifikator zarizeni pro discovery (device id)."},
            {"key": "INFIGY_MQTT_DEVICE_NAME", "label": "Device name", "tab": "mqtt", "section": "device_main",
            "type": "str", "required": True, "placeholder": "Raspberry 3B broker",
            "tooltip": "Lidsky čitelný název zařízení."},

            # MQTT reliability
            {"key": "INFIGY_MQTT_WATCHDOG_INTERVAL_S", "label": "Watchdog interval (s)", "tab": "mqtt", "section": "mqtt_reliability",
            "type": "int", "required": False, "min": 1, "max": 3600, "placeholder": "30",
            "tooltip": "Jak casto sluzba kontroluje spojeni / publish (sekundy)."},
            {"key": "INFIGY_MQTT_RECONNECT_BACKOFF_MAX_S", "label": "Reconnect backoff max (s)", "tab": "mqtt", "section": "mqtt_reliability",
            "type": "int", "required": False, "min": 1, "max": 3600, "placeholder": "60",
            "tooltip": "Maximalni cekani pri opakovanych reconnect pokusech (sekundy)."},

            {"key": "INFIGY_MQTT_ENTITY_PREFIX", "label": "Entity prefix", "tab": "mqtt", "section": "ha_pub",
            "type": "str", "required": False, "placeholder": "rpi_broker_infigy",
            "tooltip": "Prefix pro entity/senzory v HA (napr. rpi_broker_infigy...)."},

            # -----------------
            # INFIGY
            # -----------------
            {"key": "INFIGY_HOST", "label": "Infigy host", "tab": "infigy", "section": "infigy_conn",
            "type": "str", "required": True, "placeholder": "10.10.100.10",
            "tooltip": "Hostname/IP Infigy endpointu (dle implementace sluzby)."},
            {"key": "INFIGY_SOCKET_PATH", "label": "Socket path", "tab": "infigy", "section": "infigy_conn",
            "type": "str", "required": False, "placeholder": "/run/infigy.sock",
            "tooltip": "Cesta k UNIX socketu, pokud se pouziva misto TCP."},

            # -----------------
            # AUTH
            # -----------------
            {"key": "INFIGY_AUTH_COOKIE", "label": "Auth cookie", "tab": "auth", "section": "auth_main",
            "type": "secret", "required": False,
            "tooltip": "Cookie pro autentizaci (pokud sluzba pouziva cookie auth)."},
            {"key": "INFIGY_AUTH_BEARER", "label": "Auth bearer", "tab": "auth", "section": "auth_main",
            "type": "secret", "required": False,
            "tooltip": "Bearer token pro autentizaci (pokud sluzba pouziva token auth)."},

            # -----------------
            # TIMING
            # -----------------
            {"key": "INFIGY_HEARTBEAT_MAX_AGE_S", "label": "Heartbeat max age (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 86400, "placeholder": "30",
            "tooltip": "Maximalni stari heartbeat, po kterem se bere spojeni jako neaktualni (sekundy)."},
            # -----------------
            # LOGGING
            # -----------------
            {"key": "INFIGY_LOG_LEVEL", "label": "Log level", "tab": "logging", "section": "log_main",
            "type": "select", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"], "required": True,
            "tooltip": "Uroven logu. INFO pro bezny provoz, DEBUG pri ladeni."},    
        ],
    },
    "modbus_tcp_proxy": {
        "title": "Modbus TCP Proxy",
        "description": "Konfigurace sluzby modbus_tcp_proxy (TCP proxy pro Modbus).",
        "env_path": "/opt/rpi-admin-ui/.env",
        "service_id": "modbus-proxy",   # musi sedet s SERVICES_META key/id (kvuli tlacitkum a statusum)
        "auto_prefix": "MODBUS_PROXY_", # jen formalne; realne klice jsou bez prefixu (zatim nepouzivame other_auto)

        "tabs": [
            {"id": "basic",   "label": "Basic"},
            {"id": "socket",  "label": "Socket"},
            {"id": "proto",   "label": "Protocol"},
            {"id": "logging", "label": "Logging"},
            {"id": "other",  "label": "Ostatní"},
        ],

        "sections": [
            {"id": "listen_main", "tab": "basic",  "label": "Naslouchani (server)",
            "tooltip": "Kde proxy nasloucha pro prichozi Modbus TCP klienty."},

            {"id": "target_main", "tab": "basic",  "label": "Cil (upstream)",
            "tooltip": "Kam proxy preposila komunikaci (cilovy Modbus TCP server/zarizeni)."},
            
            {"id": "socket_main", "tab": "socket",  "label": "Socket / buffery",
            "tooltip": "Timeouty a velikosti bufferu. Ovlivnuje stabilitu a latenci."},

            {"id": "log_main",    "tab": "logging", "label": "Logovani komunikace Modbus do souboru",
            "tooltip": "Kam a jak se zapisuje log (soubor, uroven)."},
            {"id": "log_debug",   "tab": "logging", "label": "Logování",
            "tooltip": "Nastavení úrovně logování služby a periodicke statistiky."},

            {"id": "proto_tid",   "tab": "proto",   "label": "TID/UID pravidla",
            "tooltip": "Chovani pro transaction-id (TID) a jednotkove ID (UID)."},
            {"id": "proto_stray", "tab": "proto",   "label": "Stray / neocekavane ramce",
            "tooltip": "Co delat s neocekavanymi odpovedmi a zbytky provozu (stray)."},

            {"id": "other_auto", "tab": "other", "label": "Ostatni (detekovano)",
            "description": "Automaticky detekované klíče MODBUS_PROXY_* nalezene v .env, které nejsou explicitně v konfiguraci (read-only)."},
        ],

        "fields": [
            # -----------------
            # LISTEN
            # -----------------
            {"key": "MODBUS_PROXY_LISTEN_IP", "label": "Listen IP", "tab": "basic", "section": "listen_main",
            "type": "str", "required": True, "placeholder": "0.0.0.0",
            "tooltip": "IP adresa, na ktere proxy nasloucha. 0.0.0.0 = vsechny rozhrani."},

            {"key": "MODBUS_PROXY_LISTEN_PORT", "label": "Listen port", "tab": "basic", "section": "listen_main",
            "type": "int", "required": True, "min": 1, "max": 65535, "placeholder": "1502",
            "tooltip": "Port pro prichozi spojeni. 502 je standard, ale vyzaduje root; doporuceni 1502."},

            # -----------------
            # TARGET
            # -----------------
            {"key": "MODBUS_PROXY_TARGET_IP", "label": "Target IP", "tab": "basic", "section": "target_main",
            "type": "str", "required": True, "placeholder": "10.10.100.253",
            "tooltip": "IP ciloveho Modbus TCP serveru/zarizeni, kam se provoz preposila."},

            {"key": "MODBUS_PROXY_TARGET_PORT", "label": "Target port", "tab": "basic", "section": "target_main",
            "type": "int", "required": True, "min": 1, "max": 65535, "placeholder": "502",
            "tooltip": "Port ciloveho Modbus TCP serveru (typicky 502)."},
            
            # -----------------
            # SOCKET
            # -----------------
            {"key": "MODBUS_PROXY_BUFFER_SIZE", "label": "Buffer size", "tab": "socket", "section": "socket_main",
            "type": "int", "required": True, "min": 256, "max": 1048576, "placeholder": "4096",
            "tooltip": "Velikost socket bufferu (bytes). Typicky 4096 nebo 8192."},

            {"key": "MODBUS_PROXY_SOCK_TIMEOUT_S", "label": "Socket timeout (s)", "tab": "socket", "section": "socket_main",
            "type": "float", "required": True, "min": 0.1, "max": 120.0, "placeholder": "5.0",
            "tooltip": "Timeout pro socket operace (sekundy). Prilis nizko = chyby, prilis vysoko = dlouhe cekani."},

            # -----------------
            # LOGGING
            # -----------------
            {"key": "MODBUS_PROXY_LOG_FILE_PKT", "label": "Log file", "tab": "logging", "section": "log_main",
            "type": "str", "required": False, "placeholder": "/var/log/modbus_tcp_proxy.log",
            "tooltip": "Cesta k log souboru. Nech prazdne = log do stdout (dle implementace sluzby)."},

            {"key": "MODBUS_PROXY_LOG_LEVEL_PKT", "label": "Log level MODBUS", "tab": "logging", "section": "log_main",
            "type": "select", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"], "required": True,
            "tooltip": "Uroven logu. INFO nezapisuje do souboru, DEBUG zapisuje."},

            {"key": "MODBUS_PROXY_LOG_HEXDUMP_PKT", "label": "Hexdump", "tab": "logging", "section": "log_main",
            "type": "select", "choices": ["0", "1"], "required": True,
            "tooltip": "1 = loguje hexdump ramcu (velmi ukecane). Pouzivej jen docasne. V logu jsou konkrétní povely MODBUS"},

            {"key": "MODBUS_PROXY_LOG_SAMPLE_BYTES_PKT", "label": "Sample bytes", "tab": "logging", "section": "log_main",
            "type": "int", "required": True, "min": 0, "max": 65535, "placeholder": "128",
            "tooltip": "Kolik prvnich bytu logovat pri sample/hexdump (0 = vypnuto / dle implementace)."},
            
            {"key": "MODBUS_PROXY_LOG_LEVEL", "label": "Log level proxy", "tab": "logging", "section": "log_debug",
            "type": "select", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"], "required": True,
            "tooltip": "Uroven logu. INFO pro bezny provoz, DEBUG pri ladeni."},

            {"key": "MODBUS_PROXY_LOG_STATS_INTERVAL", "label": "Stats interval", "tab": "logging", "section": "log_debug",
            "type": "int", "required": True, "min": 0, "max": 86400, "placeholder": "60",
            "tooltip": "Interval periodickych statistik v logu v sekundach (0 = vypnuto)."},

            {"key": "MODBUS_PROXY_DROP_STRAY_SILENT", "label": "Drop stray silent", "tab": "logging", "section": "log_debug",
            "type": "select", "choices": ["0", "1"], "required": True,
            "tooltip": "1 = neloguje neocekavane odpovedi/ramce, 0 = loguje neočekávané odpovědi (v DEBUG)"},
            
            # -----------------
            # PROTOCOL: stray + TID/UID
            # -----------------
            {"key": "MODBUS_PROXY_PASS_STRAY", "label": "Pass stray", "tab": "proto", "section": "proto_stray",
            "type": "select", "choices": ["0", "1"], "required": True,
            "tooltip": "1 = pokusí se propustit stray rámce a přepošle je dál. Bezpečnější je 0 (podle implementace)."},
            
            {"key": "MODBUS_PROXY_TID_REWRITE", "label": "TID rewrite", "tab": "proto", "section": "proto_tid",
            "type": "select", "choices": ["0", "1"], "required": True,
            "tooltip": "1 = proxy muze prepisovat Transaction-ID (TID) kvuli konzistenci mezi klienty. (Doporučeno zapnout, pokud vidíš četné stray_response / out_of_order)"},

            {"key": "MODBUS_PROXY_TID_STRICT", "label": "TID strict", "tab": "proto", "section": "proto_tid",
            "type": "select", "choices": ["0", "1"], "required": True,
            "tooltip": "1 = striktni kontrola TID (nesedi-li, bere se jako chyba/stray). 0 = benevoletní"},
            
            {"key": "MODBUS_PROXY_STRICT_UID", "label": "Strict UID", "tab": "proto", "section": "proto_tid",
            "type": "select", "choices": ["0", "1"], "required": True,
            "tooltip": "1 = striktni kontrola Unit-ID (UID). U nekterych zarizeni muze byt problem. Zapni, pokud zařízení posílá odpovědi s divným UID"},
        ],
    },

    "mqtt-report": {
        "title": "RPi MQTT Report",
        "description": "Konfigurace služby rpi-mqtt-report (periodicky reporting/diagnostika do MQTT + HA discovery).",
        "env_path": "/opt/rpi-admin-ui/.env",
        "service_id": "mqtt-report",     # musi sedet s SERVICES_META key/id (kvuli tlacitkum a stavu)
        "auto_prefix": "MQTT_REPORT_",   # jen formalne; klice jsou bez prefixu (zatim)

        "tabs": [
            {"id": "basic",  "label": "Basic"},
            {"id": "targets",  "label": "Targets"},
            {"id": "mqtt",     "label": "MQTT"},
            {"id": "timing",   "label": "Intervals"},
            {"id": "logging", "label": "Logging"},
            {"id": "other",  "label": "Ostatní"},
        ],

        "sections": [
            {"id": "basic_main", "tab": "basic", "label": "Obecné", 
             "tooltip": "Zakladní přepínač agendy. Pokud je reporter vypnutý (0), po startu se ukončí a nic nepublikuje do MQTT." },

            {"id": "mqtt_conn",  "tab": "mqtt",    "label": "Připojení k MQTT brokeru",
            "tooltip": "Nastavení přístupu na MQTT broker a klientskou identitu."},

            {"id": "mqtt_topic", "tab": "mqtt",    "label": "Topic pro publikaci dat",
            "tooltip": "Base topic, pod kterým služba publikuje data."},

            {"id": "targets_main","tab": "targets","label": "Reportované systémy",
            "tooltip": "Hosty a porty pro diagnostiku (inverter, HA ping, proxy unit)."},
            
            {"id": "timing_main","tab": "timing",  "label": "Časování a watchdog",
            "tooltip": "Polling intervaly a heartbeat. Ovlivnuje zátěž i citlivost hlídaní."},

            {"id": "device_main","tab": "mqtt",  "label": "Identita MQTT zařízení",
            "tooltip": "Jak se RPi prezentuje v HA/MQTT (device info). DOPORUČENÍ: Shodné ID pro všechny služby na RPi3, aby šlo v HA o jedno zařízení"},

            {"id": "log_main", "tab": "logging", "label": "Logování",
            "tooltip": "Nastavení logu - úroveň"},
            
            {"id": "ha_pub", "tab": "mqtt", "label": "Publikovaní entit do HA",
             "tooltip": "Home Assistant MQTT Discovery.",},            

            {"id": "other_auto", "tab": "other", "label": "Ostatni (detekovano)",
            "description": "Automaticky detekované klíče MQTT_REPORT_* nalezene v .env, které nejsou explicitně v konfiguraci (read-only)."},            
        ],

        "fields": [
            # -----------------
            # BASIC
            # -----------------
            {"key": "MQTT_REPORT_ENABLED", "label": "Enabled", "tab": "basic", "section": "basic_main",
             "type": "select", "choices": ["0", "1"], "required": True, "help": "1 = zapnuto, 0 = vypnuto",
             "tooltip": "1 = reporter aktivní, 0 = reporter se po startu ukončí (vypnuto)."},

            # -----------------
            # MQTT - connection
            # -----------------
            {"key": "MQTT_REPORT_HOST", "label": "MQTT host", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": True, "placeholder": "localhost",
            "tooltip": "Hostname/IP MQTT brokeru (napr. 192.168.1.20 nebo core-mosquitto)."},
            {"key": "MQTT_REPORT_PORT", "label": "MQTT port", "tab": "mqtt", "section": "mqtt_conn",
            "type": "int", "required": True, "min": 1, "max": 65535, "placeholder": "1883",
            "tooltip": "Port MQTT (obvykle 1883)."},
            {"key": "MQTT_REPORT_USER", "label": "MQTT user", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": False, "placeholder": "",
            "tooltip": "Uzivatel pro MQTT (pokud broker vyzaduje autentizaci)."},
            {"key": "MQTT_REPORT_PASS", "label": "MQTT pass", "tab": "mqtt", "section": "mqtt_conn",
            "type": "secret", "required": False, "help": "Nech prazdne = heslo se nezmeni (pokud to sluzba podporuje).",
            "tooltip": "Heslo pro MQTT."},
            {"key": "MQTT_REPORT_CLIENT_ID", "label": "Client ID", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": True, "placeholder": "rpi-report",
            "tooltip": "MQTT Client ID (musi byt unikAtni v ramci brokeru)."},
            {"key": "MQTT_REPORT_DISCOVERY_PREFIX", "label": "Discovery prefix pro HA", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": True, "placeholder": "homeassistant",
            "tooltip": "Prefix pro Home Assistant MQTT Discovery (typicky homeassistant)."},

            # -----------------
            # MQTT - topic / identity
            # -----------------
            {"key": "MQTT_REPORT_BASE_TOPIC", "label": "Base topic", "tab": "mqtt", "section": "mqtt_topic",
            "type": "str", "required": True, "placeholder": "rpi_report",
            "tooltip": "Base topic pro publikovani (napr. rpi-bridge)."},
            {"key": "MQTT_REPORT_RECONNECT_BACKOFF_MAX_S", "label": "Reconnect backoff max (s)", "tab": "mqtt", "section": "mqtt_topic",
            "type": "int", "required": True, "min": 1, "max": 3600, "placeholder": "60",
            "tooltip": "Maximalni prodleva reconnectu pri vypadku (sekundy)."},


            # -----------------
            # MQTT DEVICE identity
            # -----------------
            {"key": "MQTT_REPORT_DEVICE_ID", "label": "Device ID", "tab": "mqtt", "section": "device_main",
            "type": "str", "required": True, "placeholder": "rpi-3b-broker",
            "tooltip": "Jednoznacny identifikator zarizeni (napr. pro HA device)."},
            {"key": "MQTT_REPORT_DEVICE_NAME", "label": "Device name", "tab": "mqtt", "section": "device_main",
            "type": "str", "required": True, "placeholder": "Raspberry 3B broker",
            "tooltip": "Lidsky čitelný název zařízení."},

            {"key": "MQTT_REPORT_ENTITY_PREFIX", "label": "Entity prefix", "tab": "mqtt", "section": "ha_pub",
            "type": "str", "required": False, "placeholder": "rpi_broker",
            "tooltip": "Prefix pro entity/senzory v HA (např. rpi_broker...)."},            

            # -----------------
            # TARGETS
            # -----------------
            {"key": "MQTT_REPORT_INVERTER_HOST", "label": "Inverter host", "tab": "targets", "section": "targets_main",
            "type": "str", "required": True, "placeholder": "10.10.100.253",
            "tooltip": "IP/hostname menice (nebo proxy) pro diagnostiku a stav."},
            {"key": "MQTT_REPORT_INVERTER_PORT", "label": "Inverter port", "tab": "targets", "section": "targets_main",
            "type": "int", "required": True, "min": 1, "max": 65535, "placeholder": "502",
            "tooltip": "Port inverteru (typicky 502 pro Modbus TCP)."},
            {"key": "MQTT_REPORT_PING_HA_HOST", "label": "Ping HA host", "tab": "targets", "section": "targets_main",
            "type": "str", "required": True, "placeholder": "192.168.1.20",
            "tooltip": "Cil pro ping kontroly Home Assistantu."},
            {"key": "MQTT_REPORT_PING_INVERTER_HOST", "label": "Ping inverter host", "tab": "targets", "section": "targets_main",
            "type": "str", "required": False, "placeholder": "10.10.100.253",
            "tooltip": "Cil pro ping kontroly inverteru. Nech prazdne = pouzije se INVERTER_HOST."},
            {"key": "MQTT_REPORT_PROXY_SYSTEMD_UNIT", "label": "Proxy systemd unit", "tab": "targets", "section": "targets_main",
            "type": "str", "required": True, "placeholder": "modbus_tcp_proxy.service",
            "tooltip": "Nazev systemd unit, kterou ma report kontrolovat (status/health)."},
            
            # -----------------
            # TIMING / WATCHDOG
            # -----------------
            {"key": "MQTT_REPORT_POLL_SYS_S", "label": "Poll sys (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 3600, "placeholder": "10",
            "tooltip": "Interval dotazovani systemovych metrik (CPU/RAM/disk) v sekundach."},
            {"key": "MQTT_REPORT_POLL_NET_S", "label": "Poll net (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 3600, "placeholder": "10",
            "tooltip": "Interval site (ping/latence) v sekundach."},
            {"key": "MQTT_REPORT_POLL_PROXY_S", "label": "Poll proxy (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 3600, "placeholder": "10",
            "tooltip": "Interval kontroly proxy sluzby (systemd status) v sekundach."},
            {"key": "MQTT_REPORT_HEARTBEAT_S", "label": "Heartbeat (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 3600, "placeholder": "5",
            "tooltip": "Jak casto se publikuje heartbeat do MQTT (sekundy)."},
            {"key": "MQTT_REPORT_MAX_AGE_OK_S", "label": "Max age OK (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 86400, "placeholder": "60",
            "tooltip": "Maximalni stari dat, kdy je stav jeste povazovan za OK (sekundy)."},
            
            # -----------------
            # LOGGING
            # -----------------
            {"key": "MQTT_REPORT_LOG_LEVEL", "label": "Log level", "tab": "logging", "section": "log_main",
            "type": "select", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"], "required": True,
            "tooltip": "Uroven logu. INFO pro bezny provoz, DEBUG pri ladeni."},

        ],
    },

    "ui": {
        "title": "RPi Admin UI",
        "description": "Konfigurace webove aplikace rpi-admin-ui.",
        "env_path": "/opt/rpi-admin-ui/.env",
        "service_id": "ui",              # musi sedet s SERVICES_META id (viz dalsi poznamka)
        "auto_prefix": "UI_",

        "tabs": [
            {"id": "web",    "label": "Web"},
            {"id": "auth",   "label": "Přihlášení"},
            {"id": "sys",   "label": "Systém"},
            {"id": "logging", "label": "Logging"},
            {"id": "other",  "label": "Ostatní"},
        ],

        "sections": [
            {"id": "web_main",  "tab": "web",  "label": "Web server",
             "tooltip": "Nastaveni portu a behu UI."},

            {"id": "auth_main", "tab": "auth", "label": "Prihlaseni",
             "tooltip": "Prihlasovaci udaje a tajny klic pro session."},

            {"id": "log_main", "tab": "logging", "label": "Logování",
            "tooltip": "Nastavení logu - úroveň"},

            {"id": "sys_main", "tab": "sys", "label": "Systémové nastavení",
            "tooltip": "Nastavení systémových adresářů RPi"},

            {"id": "other_auto", "tab": "other", "label": "Ostatni (detekovano)",
            "description": "Automaticky detekované klíče UI_* nalezené v .env, které nejsou explicitně v konfiguraci (read-only)."},
        ],

        "fields": [
            # -----------------
            # WEB
            # -----------------
            {"key": "UI_PORT", "label": "Port", "tab": "web", "section": "web_main",
             "type": "int", "required": True, "min": 1, "max": 65535, "placeholder": "8080",
             "tooltip": "TCP port, na kterem UI posloucha (napr. 8080)."},

            # -----------------
            # AUTH
            # -----------------
            {"key": "UI_USER", "label": "UI user", "tab": "auth", "section": "auth_main",
             "type": "str", "required": True, "placeholder": "admin",
             "tooltip": "Uzivatelske jmeno pro prihlaseni do UI."},

            {"key": "UI_PASS", "label": "UI password", "tab": "auth", "section": "auth_main",
             "type": "secret", "required": False,
             "help": "Nech prazdne = heslo se nezmeni.",
             "tooltip": "Heslo pro prihlaseni do UI."},

            {"key": "UI_SECRET", "label": "UI secret", "tab": "auth", "section": "auth_main",
             "type": "secret", "required": True,
             "tooltip": "Tajny klic pro session/cookies. Zmenou se odhlasi vsichni uzivatele. (min. 16 znaků)"},

            # -----------------
            # SYS
            # -----------------
            {"key": "UI_SUDO", "label": "SUDO", "tab": "sys", "section": "sys_main",
             "type": "str", "required": True, "placeholder": "/usr/bin/sudo",
             "tooltip": "Adresář SUDO na RPi."},

            {"key": "UI_SYSTEMCTL", "label": "SYSTEMCTL", "tab": "sys", "section": "sys_main",
             "type": "str", "required": True, "placeholder": "/bin/systemctl",
             "tooltip": "Adresář SYSTEMCTL na RPi."},

            # -----------------
            # LOG
            # -----------------
            {"key": "UI_LOG_LEVEL", "label": "Log level", "tab": "logging", "section": "log_main",
            "type": "select", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"], "required": True,
            "tooltip": "Uroven logu. INFO pro bezny provoz, DEBUG pri ladeni."},
        ],

    },

    "ha-watchdog": {
        "title": "HA Watchdog",
        "description": "Hlídání dostupnosti Home Assistant (HA OS) z RPi + volitelná notifikace (Telegram) + publikace do MQTT.",
        "env_path": "/opt/rpi-admin-ui/.env",
        "service_id": "ha-watchdog",  
        "auto_prefix": "HA_WD_",

        "tabs": [
            {"id": "basic",    "label": "Basic"},
            {"id": "targets",  "label": "Targets"},
            {"id": "telegram", "label": "Telegram"},
            {"id": "timing",   "label": "Cooldowns / Timing"},
            {"id": "logging",  "label": "Logging"},
            {"id": "other",    "label": "Ostatní"},
        ],

        "sections": [
            {"id": "basic_main", "tab": "basic", "label": "Obecné",
             "tooltip": "Zapnutí/vypnutí watchdogu. Pokud je vypnutý (0), po startu se ukončí a nic nedělá."},

            {"id": "targets_main", "tab": "targets", "label": "Home Assistant SSH (target)",
             "tooltip": "Kam watchdog kontroluje dostupnost HA a jakým způsobem."},

            {"id": "tg_main", "tab": "telegram", "label": "Telegram notifikace",
             "tooltip": "Volitelná notifikace na Telegram, když HA nejede / obnoví se."},

            {"id": "timing_main", "tab": "timing", "label": "Časování",
             "tooltip": "Interval kontroly a retry/backoff. Ovlivňuje rychlost detekce i zátěž."},

            {"id": "log_main", "tab": "logging", "label": "Logování",
             "tooltip": "Nastavení logu - úroveň."},

            {"id": "other_auto", "tab": "other", "label": "Ostatni (detekovano)",
             "description": "Automaticky detekované klíče HA_WD_* z .env, které nejsou explicitně v konfiguraci (read-only)."},
        ],

        "fields": [
            # -----------------
            # BASIC
            # -----------------
            {"key": "HA_WD_ENABLED", "label": "Enabled", "tab": "basic", "section": "basic_main",
            "type": "select", "choices": ["0", "1"], "required": True, "placeholder": "1",
            "tooltip": "1 = watchdog aktivní, 0 = watchdog se po startu ukončí (vypnuto)."},

            # -----------------
            # TARGETS (HA)
            # -----------------
            {"key": "HA_WD_HA_HOST", "label": "HA host", "tab": "targets", "section": "targets_main",
            "type": "str", "required": True, "placeholder": "192.168.1.20",
            "tooltip": "IP/hostname Home Assistant (HA OS). Používá se pro TCP test (port 8123) i pro SSH."},

            {"key": "HA_WD_POLL_S", "label": "Poll interval (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 3600, "placeholder": "10",
            "tooltip": "Jak často kontrolovat dostupnost HA (sekundy)."},


            {"key": "HA_WD_FAIL_COUNT", "label": "Fail count (DOWN threshold)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 999, "placeholder": "6",
            "tooltip": "Kolik po sobě jdoucích neúspěchů je potřeba pro přechod do stavu DOWN."},

            {"key": "HA_WD_RECOVER_COUNT", "label": "Recover count (UP threshold)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 999, "placeholder": "2",
            "tooltip": "Kolik po sobě jdoucích úspěchů je potřeba pro přechod do stavu UP."},

            # -----------------
            # SSH HA
            # -----------------
            {"key": "HA_WD_SSH_USER", "label": "HA SSH user", "tab": "targets", "section": "targets_main",
            "type": "str", "required": False, "placeholder": "root",
            "tooltip": "Uživatel pro SSH na HA OS (typicky root)."},


            {"key": "HA_WD_SSH_PORT", "label": "HA SSH port", "tab": "targets", "section": "targets_main",
            "type": "int", "required": False, "min": 1, "max": 65535, "placeholder": "22",
            "tooltip": "SSH port HA OS (typicky 22)."},


            {"key": "HA_WD_SSH_CONNECT_TIMEOUT_S", "label": "SSH connect timeout (s)",
            "tab": "targets", "section": "targets_main",
            "type": "int", "required": True, "min": 1, "max": 60, "placeholder": "5",
            "tooltip": "Maximální doba (v sekundách) pro navázání SSH spojení při restartu HA."},


            {"key": "HA_WD_HA_RESTART_CMD", "label": "Restart command", "tab": "targets", "section": "targets_main",
            "type": "str", "required": False, "placeholder": "ha host reboot",
            "tooltip": "Příkaz spuštěný přes SSH na HA. Typicky 'ha host reboot' nebo 'reboot'."},

            # -----------------
            # TELEGRAM
            # -----------------
            {"key": "HA_WD_TELEGRAM_ENABLED", "label": "Telegram enabled", "tab": "telegram", "section": "tg_main",
            "type": "select", "choices": ["0", "1"], "required": True, "placeholder": "1",
            "tooltip": "1 = posílat Telegram notifikace, 0 = bez Telegramu."},

            {"key": "HA_WD_TELEGRAM_TOKEN", "label": "Bot token", "tab": "telegram", "section": "tg_main",
            "type": "secret", "required": False,
            "tooltip": "Token od BotFather."},

            {"key": "HA_WD_TELEGRAM_CHAT_ID", "label": "Chat ID", "tab": "telegram", "section": "tg_main",
            "type": "str", "required": False, "placeholder": "123456789",
            "tooltip": "Chat ID (z getUpdates nebo z bota)."},


            {"key": "HA_WD_TELEGRAM_PREFIX", "label": "Message prefix", "tab": "telegram", "section": "tg_main",
            "type": "str", "required": False, "placeholder": "RPi Watchdog",
            "tooltip": "Prefix před zprávou (např. název zařízení / lokality)."},


            {"key": "HA_WD_TELEGRAM_TIMEOUT_S", "label": "Telegram timeout (s)", "tab": "telegram", "section": "tg_main",
            "type": "int", "required": False, "min": 1, "max": 60, "placeholder": "6",
            "tooltip": "Timeout HTTP požadavku na Telegram API."},


            # -----------------
            # COOLDOWNS / TIMING
            # -----------------
            {"key": "HA_WD_NOTIFY_COOLDOWN_S", "label": "Notify cooldown (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 10, "max": 86400, "placeholder": "300",
            "tooltip": "Minimální odstup mezi notifikacemi stejného typu (sekundy)."},


            {"key": "HA_WD_UI_RESTART_COOLDOWN_S", "label": "UI restart cooldown (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 10, "max": 86400, "placeholder": "120",
            "tooltip": "Minimální odstup mezi restarty rpi-admin-ui.service (sekundy)."},


            {"key": "HA_WD_HA_RESTART_COOLDOWN_S", "label": "HA restart cooldown (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 10, "max": 86400, "placeholder": "600",
            "tooltip": "Minimální odstup mezi restarty HA (sekundy), aby nedošlo k restart loopu."},


            # -----------------
            # LOGGING
            # -----------------
            {"key": "HA_WD_LOG_LEVEL", "label": "Log level", "tab": "logging", "section": "log_main",
            "type": "select", "choices": ["DEBUG", "INFO", "WARNING", "ERROR"], "required": True, "placeholder": "INFO",
            "tooltip": "Úroveň logu. INFO pro běžný provoz, DEBUG při ladění."},
        ],
    },


}
