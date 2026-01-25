# 🔁 Modbus TCP Proxy (modbus_tcp_proxy)

## Účel služby
`modbus_tcp_proxy` je TCP proxy pro Modbus komunikaci mezi **Home Assistantem** a **GoodWe měničem** (nebo jiným Modbus TCP zařízením).
Služba:
- přijímá Modbus TCP spojení z jedné sítě (typicky LAN s Home Assistantem),
- přeposílá je do druhé sítě (typicky technologická síť s měničem),
- a současně **filtruje / upravuje** problematické chování protokolu (TID/UID, „stray“ odpovědi), aby se minimalizovaly chyby a výpadky.

> Důležité: Tato služba **nečte RS-485**. Je to čistě **Modbus TCP ↔ Modbus TCP** proxy.
> 
> Protože měnič GoodWe nekomunikuje s HA prostřednictvím integrace GoodWe Inverter zcela korektně a hodnoty načítané z inverteru často nejsou často dostupné a nemám možnost dostat inverter do stejné sítě jako je HA, musel jsem realizovat proxy službu, která zajistí můstek mezi 2 sítěmi a současně bude řešit opravu komunikace MODBUS.
>
> Jistější alternativou pro získání informací z FVE je vytěžení Infigy a to je řešeno jinou službou.
---

## Co služba dělá
- Naslouchá na TCP portu (default 502) a přijímá Modbus TCP klienty (HA / integrace).
- Otevírá upstream spojení na cílové zařízení (GoodWe) na cílový TCP port (default 502).
- Přeposílá rámce mezi klientem a cílem.
- Volitelně **přepisuje TID** (Transaction ID) pro konzistenci.
- Volitelně kontroluje **UID** (Unit ID) v odpovědích.
- Řeší „stray“ / out-of-order odpovědi (odpověď bez odpovídajícího pending requestu).
- Umí periodicky logovat statistiky a (volitelně) ukládat rámce do log souboru.

---

## Datový tok
Home Assistant (Modbus TCP klient)
        |
        |  Modbus TCP
        v
RPi: modbus_tcp_proxy (listen)
        |
        |  Modbus TCP (upravené / stabilizované)
        v
GoodWe (Modbus TCP server)

V praxi to často zároveň řeší i propojení **dvou sítí** (např. 192.168.1.x ↔ 10.10.100.x), protože RPi má přístup do obou.

---

## Klíčové vlastnosti
- ✅ Stabilizace komunikace vůči „stray“ / out-of-order odpovědím
- ✅ Volitelný přepis TID (`MODBUS_PROXY_TID_REWRITE`)
- ✅ Volitelná kontrola UID (`MODBUS_PROXY_STRICT_UID`)
- ✅ Nastavitelný socket timeout (detekce „ticha“)
- ✅ Paketový log (soubor), volitelný hexdump a sample bytes
- ✅ Periodické statistiky do logu

---

## Konfigurace (.env)

### Připojení (listen / target)
- `MODBUS_PROXY_LISTEN_IP`  
  IP adresa, na které proxy naslouchá.  
  Default: `0.0.0.0` (všechny rozhraní)

- `MODBUS_PROXY_LISTEN_PORT`  
  Port, na kterém proxy naslouchá.  
  Default: `502`

  Poznámka: Port `<1024` (např. 502) typicky vyžaduje root/capability. V praxi se často používá např. **1502** a v HA se nastaví port 1502.

- `MODBUS_PROXY_TARGET_IP`  
  IP cílového zařízení (GoodWe / Modbus server).  
  Default: `10.10.100.253`

- `MODBUS_PROXY_TARGET_PORT`  
  Port cílového zařízení (typicky 502).  
  Default: `502`

### Socket a buffery
- `MODBUS_PROXY_BUFFER_SIZE`  
  Velikost bufferu pro socket operace (bytes).  
  Default: `4096`

- `MODBUS_PROXY_SOCK_TIMEOUT_S`  
  Socket timeout pro recv (sekundy) – používá se i pro detekci „ticha“.  
  Default: `30.0`

> Poznámka k minulému pádu: pokud je v `.env` hodnota `30.0`, musí se číst jako `float`. V kódu se používá `env_float(...)`, takže `30.0` je OK.

### Logování paketů (detailní Modbus provoz)
- `MODBUS_PROXY_LOG_FILE_PKT`  
  Cesta k log souboru s provozem (pokud je zapnuté logování paketů).  
  Default: `/var/log/modbus_proxy.log`

- `MODBUS_PROXY_LOG_LEVEL_PKT`  
  Úroveň paketového logu: `DEBUG|INFO|WARNING|ERROR`  
  Default: `INFO`

- `MODBUS_PROXY_LOG_HEXDUMP_PKT`  
  `1` = hexdump rámců (velmi ukecané, používej jen dočasně)  
  Default: `0`

- `MODBUS_PROXY_LOG_SAMPLE_BYTES_PKT`  
  Kolik bajtů z payloadu vypsat (pro „sample“ výpis)  
  Default: `64`

### Periodické statistiky
- `MODBUS_PROXY_LOG_STATS_INTERVAL`  
  Interval periodických souhrnů do logu (sekundy).  
  `0` = vypnuto.  
  Default: `60`

- `MODBUS_PROXY_DROP_STRAY_SILENT`  
  `1` = pokud je stray, nic nelogovat (omezí spam v logu).  
  Default: `0`

### Pravidla protokolu (TID/UID/stray)
- `MODBUS_PROXY_TID_REWRITE`  
  `1` = přepisovat TID, `0` = nepřepisovat.  
  Default: `1`

- `MODBUS_PROXY_VTID_STRICT`  
  `1` = „strict“ režim, kdy se TID nepřepisuje a řeší se konzervativně (viz poznámka níže).  
  Default: `0`

  Poznámka: V kódu je proměnná načítaná jako **`MODBUS_PROXY_VTID_STRICT`** (s písmenem `V`).  
  Pokud máš v `.env` klíč `MODBUS_PROXY_TID_STRICT`, nebude se brát v potaz, dokud se kód/klíč nesjednotí. Doporučení:
  - buď používej `MODBUS_PROXY_VTID_STRICT`,
  - nebo oprav v kódu klíč na `MODBUS_PROXY_TID_STRICT` (a následně sjednoť i UI).

- `MODBUS_PROXY_STRICT_UID`  
  `1` = kontrolovat UID (Unit-ID) v odpovědích  
  Default: `0`

- `MODBUS_PROXY_PASS_STRAY`  
  `1` = přeposílat i stray rámce bez pending requestu (**nedoporučeno**)  
  Default: `0`

### Log úroveň samotné služby
- `MODBUS_PROXY_LOG_LEVEL`  
  Úroveň logu aplikace: `DEBUG|INFO|WARNING|ERROR`  
  Default: `INFO`

---

## Systemd služba
Typická unit: `modbus_tcp_proxy.service` (název se může lišit podle instalace).

Základní operace:
- status: `systemctl status modbus_tcp_proxy.service`
- log: `journalctl -u modbus_tcp_proxy.service -f`
- restart: `sudo systemctl restart modbus_tcp_proxy.service`

---

## Předpoklady (User Requirements)
- dostupnost Inverteru GoodWe prostřednictvím jiné sítě
- zprovoznění integrace GoodWe v HA prostřednictvím TCP

---

## Jak ověřit funkčnost
1) Ověř, že proxy naslouchá na správném portu:
- `ss -ltnp | grep -E ':502|:1502'`

2) Ověř spojení z HA (Modbus integrace) na IP RPi a port proxy.

3) Sleduj log:
- `journalctl -u modbus_tcp_proxy.service -f`

Pokud je zapnutý paketový log:
- `tail -f /var/log/modbus_proxy.log`

---

## Typické problémy a řešení

### HA hlásí chyby / timeouty
- Zkontroluj, že RPi vidí cílovou IP:  
  `ping 10.10.100.253`
- Zkontroluj TCP dostupnost:  
  `nc -vz 10.10.100.253 502`
- Zkus zvýšit `MODBUS_PROXY_SOCK_TIMEOUT_S`.

### Port 502 nejde spustit
- Použij listen port např. `1502` a nastav v HA port 1502,
  nebo přidej capability (pokročilé) – jednodušší je 1502.

### Stray / out-of-order spam v logu
- Zapni `MODBUS_PROXY_DROP_STRAY_SILENT=1`
- Ponech `MODBUS_PROXY_PASS_STRAY=0`
- V praxi často pomáhá i `MODBUS_PROXY_TID_REWRITE=1`

---

## Bezpečnost
- Proxy je síťová služba. Neotvírej ji do internetu.
- Ideálně omez přístup firewall pravidly jen na IP Home Assistantu.
- Pokud je RPi most mezi sítěmi, dávej pozor na směrování a pravidla (aby se do technologické sítě nedostalo něco, co tam nemá být).
