# /opt/rpi-admin-ui/config/agendas.py
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
            {"id": "ha",     "label": "Home Assistant"},
            {"id": "other",  "label": "Ostatni"},
        ],

        "sections": [
            {
                "id": "basic_main",
                "tab": "basic",
                "label": "Obecne",
                "tooltip": "Zakladni prepinac agendy. Pokud je broker vypnuty (0), po startu se ukonci a nic nepublikuje do MQTT.",
            },
            {
                "id": "mqtt_conn",
                "tab": "mqtt",
                "label": "Pripojeni k brokeru",
                "tooltip": "Parametry pripojeni k MQTT brokeru (host, port, volitelne prihlaseni). Bez spravneho pripojeni broker nepublikuje stavy ani udalosti.",
            },
            {
                "id": "mqtt_topic",
                "tab": "mqtt",
                "label": "Topic / identita",
                "tooltip": "Identita klienta a zakladni namespace topicu. Client ID musi byt unikatni na brokeru. Base topic urcuje, kam broker publikuje state/event.",
            },
            {
                "id": "modbus_rtu",
                "tab": "modbus",
                "label": "Parametry linky",
                "tooltip": "Nastaveni seriove Modbus RTU linky (port, baudrate, parita, stopbity, bytesize, timeout). Pri chybach zkontroluj port /dev/serial/by-id a parametry linky na IO modulech.",
            },
            {
                "id": "poll_debounce",
                "tab": "modbus",
                "label": "Polling / debounce",
                "tooltip": "Rychlost dotazovani a odruseni vstupu. Poll interval urcuje pauzu mezi dotazy, debounce filtruje zakmit tlacitek/vypinacu. Prilis male hodnoty mohou zatezovat sbernici nebo delat falešne prechody.",
            },
            {
                "id": "iomap_main",
                "tab": "iomap",
                "label": "Topologie / mapovani",
                "tooltip": "Definice slave adres a kanalu. Slaves + channels-per-slave urcuji topologii. Used channels omezi, ktere vstupy se vubec zpracovavaji. Buttons prepinaji vybrane kanaly do rezimu tlacitka (event).",
            },
            {
                "id": "diag_mqtt",
                "tab": "diag",
                "label": "MQTT latency test",
                "tooltip": "Nastaveni diagnostickeho testu latence MQTT (pocet vzorku, interval, timeout). Test meri publish -> broker -> receive v ramci stejneho klienta (loopback pres broker).",
            },
            {
                "id": "diag_mqtt_thr",
                "tab": "diag",
                "label": "MQTT latency semafor",
                "tooltip": "Prahy pro vyhodnoceni latence (OK/WARN) a topic prefix pro diagnostiku. UI pouzije oddeleny prefix, aby se nepletly produkcni topicy s diagnostikou.",
            },
            {
                "id": "diag_modbus",
                "tab": "diag",
                "label": "Modbus RTT test",
                "tooltip": "Nastaveni RTT testu sbernice Modbus (metoda, adresa, count, interval, prahy OK/WARN). Slouzi k overeni odezvy IO modulu a stability linky.",
            },
            {
                "id": "ha_pub",
                "tab": "ha",
                "label": "Publikovani do HA",
                "tooltip": "Home Assistant MQTT Discovery. Zapnutim se generuji discovery entity pod prefixem (typicky homeassistant). Pokud mas discovery vypnute, entity si musis definovat rucne v HA.",
            },
            {
                "id": "other_auto",
                "tab": "other",
                "label": "Ostatni (detekovano)",
                "description": "Klice MODBUS_IO_* nalezene v .env, ktere nejsou explicitne v konfiguraci (read-only).",
                "tooltip": "Automaticky detekovane klice MODBUS_IO_* z .env, ktere nemaji metadata. Zobrazuji se pouze pro prehled (read-only), aby se nic omylem neupravilo.",
            },
        ],

        "fields": [
            # -----------------
            # BASIC
            # -----------------
            {
                "key": "MODBUS_IO_ENABLED",
                "label": "Enabled",
                "tab": "basic",
                "section": "basic_main",
                "type": "select",
                "choices": ["0", "1"],
                "required": True,
                "help": "1 = zapnuto, 0 = vypnuto",
                "tooltip": "1 = broker aktivni, 0 = broker se po startu ukonci (vypnuto).",
            },

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

            {"key": "MODBUS_IO_MQTT_CLIENT_ID", "label": "Client ID", "tab": "mqtt", "section": "mqtt_topic",
             "type": "str", "required": False, "placeholder": "modbus-io-broker-rpi3",
             "tooltip": "Client ID pro MQTT (musi byt unikAtni v ramci brokeru)."},
            {"key": "MODBUS_IO_MQTT_BASE_TOPIC", "label": "Base topic", "tab": "mqtt", "section": "mqtt_topic",
             "type": "str", "required": False, "placeholder": "modbus_io",
             "tooltip": "Base topic (napr. modbus_io). Broker publikuje do: base/state/* a base/event/*."},

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
            {"key": "MODBUS_IO_NAME_PREFIX", "label": "Name prefix", "tab": "iomap", "section": "iomap_main",
             "type": "str", "required": False, "placeholder": "modbus_io",
             "tooltip": "Prefix nazvu signalu. Vysledne jmeno: prefix_slave_channel (napr. modbus_io_128_2)."},
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

            # -----------------
            # HOME ASSISTANT
            # -----------------
            {"key": "MODBUS_IO_HA_DISCOVERY", "label": "HA discovery", "tab": "ha", "section": "ha_pub",
             "type": "select", "choices": ["0", "1"], "required": True,
             "tooltip": "1 = publikovat Home Assistant MQTT Discovery, 0 = nevytvaret discovery entity."},
            {"key": "MODBUS_IO_HA_DISCOVERY_PREFIX", "label": "Discovery prefix", "tab": "ha", "section": "ha_pub",
             "type": "str", "required": True, "placeholder": "homeassistant",
             "tooltip": "Prefix discovery topicu (typicky homeassistant)."},
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
            {"id": "ha",     "label": "Home Assistant"},
            {"id": "timing", "label": "Timing"},
            {"id": "other",  "label": "Ostatni"},
        ],

        "sections": [
            {"id": "mqtt_conn", "tab": "mqtt", "label": "Pripojeni k brokeru",
            "tooltip": "Parametry pripojeni k MQTT brokeru (host/port/uzivatel/heslo)."},
            {"id": "mqtt_ident", "tab": "mqtt", "label": "Topic / identita",
            "tooltip": "Identita klienta a base topic pro publikovani."},
            {"id": "mqtt_reliability", "tab": "mqtt", "label": "Spolehlivost / watchdog",
            "tooltip": "Parametry watchdogu a reconnect backoffu."},

            {"id": "infigy_conn", "tab": "infigy", "label": "Pripojeni na Infigy",
            "tooltip": "Cilovy host a lokalni socket (pokud se pouziva)."},
            {"id": "auth_main", "tab": "auth", "label": "Autentizace",
            "tooltip": "Cookie/Bearer pro pristup. Pouzij jen jednu metodu, podle implementace sluzby."},

            {"id": "ha_discovery", "tab": "ha", "label": "MQTT Discovery / identifikace",
            "tooltip": "Prefix discovery a identifikace zarizeni/entity v HA."},
            {"id": "meta", "tab": "ha", "label": "Metadata / cesty",
            "tooltip": "Verze a cesty pro energy state (podle implementace sluzby)."},

            {"id": "timing_main", "tab": "timing", "label": "Casovani a heartbeat",
            "tooltip": "Intervaly publikovani, tick integratoru a heartbeat age."},

            {
                "id": "other_auto",
                "tab": "other",
                "label": "Ostatni (detekovano)",
                "description": "Klice z .env, ktere nejsou explicitne v konfiguraci (read-only).",
            },
        ],

        "fields": [
            # -----------------
            # MQTT / conn
            # -----------------
            {"key": "MQTT_HOST", "label": "Host", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": True, "placeholder": "192.168.1.20",
            "tooltip": "Hostname/IP MQTT brokeru (napr. 192.168.1.20 nebo core-mosquitto)."},
            {"key": "MQTT_PORT", "label": "Port", "tab": "mqtt", "section": "mqtt_conn",
            "type": "int", "required": True, "min": 1, "max": 65535, "placeholder": "1883",
            "tooltip": "Port MQTT (obvykle 1883)."},
            {"key": "MQTT_USER", "label": "Username", "tab": "mqtt", "section": "mqtt_conn",
            "type": "str", "required": False,
            "tooltip": "Uzivatel pro MQTT (pokud broker vyzaduje)."},
            {"key": "MQTT_PASS", "label": "Password", "tab": "mqtt", "section": "mqtt_conn",
            "type": "secret", "required": False,
            "tooltip": "Heslo pro MQTT (pokud broker vyzaduje)."},

            # MQTT identita / topic
            {"key": "MQTT_BASE_INFIGY", "label": "Base topic", "tab": "mqtt", "section": "mqtt_ident",
            "type": "str", "required": True, "placeholder": "infigy",
            "tooltip": "Zakladni topic pro publikovani dat (napr. infigy)."},
            {"key": "CLIENT_ID_INFIGY", "label": "Client ID", "tab": "mqtt", "section": "mqtt_ident",
            "type": "str", "required": True, "placeholder": "infigy-ws-to-mqtt",
            "tooltip": "Client ID pro MQTT (musi byt unikAtni v ramci brokeru)."},

            # MQTT reliability
            {"key": "MQTT_WATCHDOG_INTERVAL_S", "label": "Watchdog interval (s)", "tab": "mqtt", "section": "mqtt_reliability",
            "type": "int", "required": False, "min": 1, "max": 3600, "placeholder": "30",
            "tooltip": "Jak casto sluzba kontroluje spojeni / publish (sekundy)."},
            {"key": "MQTT_RECONNECT_BACKOFF_MAX_S", "label": "Reconnect backoff max (s)", "tab": "mqtt", "section": "mqtt_reliability",
            "type": "int", "required": False, "min": 1, "max": 3600, "placeholder": "60",
            "tooltip": "Maximalni cekani pri opakovanych reconnect pokusech (sekundy)."},

            # -----------------
            # INFIGY
            # -----------------
            {"key": "INFIGY_HOST", "label": "Infigy host", "tab": "infigy", "section": "infigy_conn",
            "type": "str", "required": True, "placeholder": "10.10.100.10",
            "tooltip": "Hostname/IP Infigy endpointu (dle implementace sluzby)."},
            {"key": "SOCKET_PATH", "label": "Socket path", "tab": "infigy", "section": "infigy_conn",
            "type": "str", "required": False, "placeholder": "/run/infigy.sock",
            "tooltip": "Cesta k UNIX socketu, pokud se pouziva misto TCP."},

            # -----------------
            # AUTH
            # -----------------
            {"key": "AUTH_COOKIE", "label": "Auth cookie", "tab": "auth", "section": "auth_main",
            "type": "secret", "required": False,
            "tooltip": "Cookie pro autentizaci (pokud sluzba pouziva cookie auth)."},
            {"key": "AUTH_BEARER", "label": "Auth bearer", "tab": "auth", "section": "auth_main",
            "type": "secret", "required": False,
            "tooltip": "Bearer token pro autentizaci (pokud sluzba pouziva token auth)."},

            # -----------------
            # HOME ASSISTANT / discovery
            # -----------------
            {"key": "DISCOVERY_PREFIX", "label": "Discovery prefix", "tab": "ha", "section": "ha_discovery",
            "type": "str", "required": True, "placeholder": "homeassistant",
            "tooltip": "Prefix pro HA MQTT Discovery (typicky homeassistant)."},
            {"key": "DEVICE_ID", "label": "Device ID", "tab": "ha", "section": "ha_discovery",
            "type": "str", "required": True, "placeholder": "infigy_gateway",
            "tooltip": "Identifikator zarizeni pro discovery (device id)."},
            {"key": "ENTITY_PREFIX", "label": "Entity prefix", "tab": "ha", "section": "ha_discovery",
            "type": "str", "required": False, "placeholder": "infigy",
            "tooltip": "Prefix pro entity/senzory v HA (napr. infigy_...)."},

            # Metadata / paths
            {"key": "SW_VERSION", "label": "SW version", "tab": "ha", "section": "meta",
            "type": "str", "required": False, "placeholder": "1.0.0",
            "tooltip": "Verze sluzby (informativni)."},
            {"key": "ENERGY_STATE_PATH", "label": "Energy state path", "tab": "ha", "section": "meta",
            "type": "str", "required": False, "placeholder": "/energy/state",
            "tooltip": "Cesta/endpoint pro zdroj energy state (dle implementace sluzby)."},

            # -----------------
            # TIMING
            # -----------------
            {"key": "ENERGY_PUBLISH_INTERVAL_S", "label": "Publish interval (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 3600, "placeholder": "5",
            "tooltip": "Jak casto publikovat energy hodnoty do MQTT (sekundy)."},
            {"key": "INTEGRATOR_TICK_S", "label": "Integrator tick (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 3600, "placeholder": "1",
            "tooltip": "Krok integratoru (sekundy)."},
            {"key": "HEARTBEAT_MAX_AGE_S", "label": "Heartbeat max age (s)", "tab": "timing", "section": "timing_main",
            "type": "int", "required": True, "min": 1, "max": 86400, "placeholder": "30",
            "tooltip": "Maximalni stari heartbeat, po kterem se bere spojeni jako neaktualni (sekundy)."},
        ],
    }
}
