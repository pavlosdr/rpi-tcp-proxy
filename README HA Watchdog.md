# Home Assistant Watchdog (ha_watchdog)

## Účel služby

Služba `ha_watchdog` běží na Raspberry Pi a dohlíží na dostupnost Home Assistantu (HA OS) v síti.
Když HA přestane odpovídat, watchdog provede restart přes SSH a volitelně odešle notifikaci přes Telegram.

Typické použití:
- dohled nad HA OS bez veřejné IP (v lokální síti),
- automatický restart HA při zamrznutí,
- okamžité upozornění na mobil (Telegram).

## Co služba dělá

- Periodicky testuje dostupnost HA pomocí TCP spojení na port HA (výchozí 8123).
- Při opakovaném výpadku spustí restart příkazem přes SSH.
- Používá hysterezi (prahy pro FAIL/OK), aby se předešlo restartům při krátkých výpadcích.
- Umí poslat Telegram zprávu při pádu i při zotavení.

## Datový tok

```text
Raspberry Pi (ha_watchdog)
  |  TCP probe (8123)
  v
Home Assistant (HA OS)
  ^
  |  SSH (restart command)
  +------------------------
  |
  +--> Telegram API (notifikace, volitelné)
```

## Požadavky (requirements)

### Nutné
- Raspberry Pi má síťový přístup na HA (IP/hostname).
- Na HA je povolen SSH přístup a watchdog se umí přihlásit bez interakce (doporučeno přes SSH klíč).
- Na RPi je dostupný příkaz `ssh` (OpenSSH client).
- Home Assistant naslouchá na portu (výchozí 8123).

### Volitelné (Telegram)
- Telegram účet (mobilní aplikace).
- Vytvořený Telegram bot (BotFather) + token.
- Zjištěné `chat_id`, kam se budou posílat zprávy.

## Konfigurace v `.env`

Níže jsou parametry čtené přímo z aktuální verze `ha_watchdog.py`.

| Proměnná v .env | Typ | Výchozí hodnota | Význam |
|---|---:|---:|---|
| `HA_WD_ENABLED` | BOOL | `True` | Konfigurace pro `ENABLED` |
| `HA_WD_HA_HOST` | STR | `"192.168.1.20"` | Konfigurace pro `HA_HOST` |
| `HA_WD_POLL_S` | INT | `10` | Konfigurace pro `POLL_S` |
| `HA_WD_FAIL_COUNT` | INT | `6` | Konfigurace pro `FAIL_COUNT` |
| `HA_WD_RECOVER_COUNT` | INT | `2` | Konfigurace pro `RECOVER_COUNT` |
| `HA_WD_SSH_USER` | STR | `"root"` | Konfigurace pro `SSH_USER` |
| `HA_WD_SSH_PORT` | INT | `22` | Konfigurace pro `SSH_PORT` |
| `HA_WD_SSH_CONNECT_TIMEOUT_S` | INT | `5` | Konfigurace pro `SSH_CONNECT_TIMEOUT_S` |
| `HA_WD_HA_RESTART_CMD` | STR | `"ha host reboot"` | Konfigurace pro `SSH_CMD` |
| `HA_WD_TELEGRAM_ENABLED` | BOOL | `True` | Konfigurace pro `TG_ENABLED` |
| `HA_WD_TELEGRAM_TOKEN` | STR | `""` | Konfigurace pro `TG_BOT_TOKEN` |
| `HA_WD_TELEGRAM_CHAT_ID` | STR | `""` | Konfigurace pro `TG_CHAT_ID` |
| `HA_WD_TELEGRAM_PREFIX` | STR | `"RPi Watchdog"` | Konfigurace pro `TG_PREFIX` |
| `HA_WD_TELEGRAM_TIMEOUT_S` | INT | `6` | Konfigurace pro `TG_TIMEOUT_S` |
| `HA_WD_NOTIFY_COOLDOWN_S` | INT | `300` | Konfigurace pro `NOTIFY_COOLDOWN_S` |
| `HA_WD_UI_RESTART_COOLDOWN_S` | INT | `120` | Konfigurace pro `UI_RESTART_COOLDOWN_S` |
| `HA_WD_HA_RESTART_COOLDOWN_S` | INT | `600` | Konfigurace pro `HA_RESTART_COOLDOWN_S` |

### Detail významu parametrů

- `HA_WD_ENABLED`  
  1 = služba běží, 0 = po startu se ukončí (vypnuto).

- `HA_WD_HA_HOST`  
  IP/hostname Home Assistantu (např. `192.168.1.20`).

- `HA_WD_HA_PORT`  
  Port, na kterém HA odpovídá na HTTP (typicky `8123`). Watchdog dělá TCP connect test, nemusí umět HTTP.

- `HA_WD_CHECK_INTERVAL_S`  
  Jak často testovat dostupnost HA (sekundy).

- `HA_WD_FAIL_THRESHOLD` a `HA_WD_OK_THRESHOLD`  
  Po kolika po sobě jdoucích neúspěších se HA bere jako „down“ (FAIL) a po kolika úspěších se bere jako „up“ (OK).
  Tohle je klíčové pro stabilitu – krátký výpadek nezpůsobí restart.

- `HA_WD_HA_SSH_USER` / `HA_WD_HA_SSH_PORT`  
  Přihlašovací údaje pro SSH do HA. Port může být libovolný (22, 1022, ...), hlavní je mít otevřený port na HA a správně nastavený SSH server.

- `HA_WD_HA_SSH_CMD`  
  Příkaz, který se spustí na HA při restartu. Výchozí: `ha host reboot` (reboot celého hosta).  
  Poznámka: Pokud chceš jen restart Core/OS, uprav příkaz podle toho, co máš ověřené jako funkční.

- `HA_WD_HA_SSH_TIMEOUT_S`  
  Timeout SSH příkazu (sekundy). Když HA visí, SSH může timeoutnout – je to očekávané.

- `HA_WD_TELEGRAM_ENABLED`  
  1 = posílat Telegram notifikace, 0 = bez notifikací.

- `HA_WD_TELEGRAM_TOKEN`, `HA_WD_TELEGRAM_CHAT_ID`  
  Token bota a cílové chat_id.
---

## Předpoklady (User Requirements)
- nastavení SSH pro HA
- existujíc služba pro zasílání notifikací na Telegram
## Nastavení SSH pro HA OS (doporučený postup)

1. V Home Assistantu nainstaluj add-on „Terminal & SSH“ (Add-ons).
2. V add-onu povol SSH server a nastav přihlášení:
   - doporučeno: `authorized_keys` (veřejný klíč z RPi),
   - případně: heslo (méně bezpečné a hůře automatizovatelné).
3. Ověř z RPi ručně:
   - `ssh -p <port> <user>@<ha_host> "<cmd>"`
   - příklad: `ssh -p 22 root@192.168.1.20 "ha host reboot"`

Pokud ruční příkaz funguje, bude fungovat i watchdog.

## Nastavení Telegram notifikací (stručně)

1. V Telegramu vytvoř bota přes BotFather a získej token.
2. Pošli botovi zprávu (aby se vytvořil update).
3. Získej `chat_id` (např. přes volání Telegram API `getUpdates`).
4. Nastav do `.env`:
   - `HA_WD_TELEGRAM_ENABLED=1`
   - `HA_WD_TELEGRAM_TOKEN=...`
   - `HA_WD_TELEGRAM_CHAT_ID=...`

## Provoz a kontrola funkčnosti

### Systemd
Typicky je služba spuštěna přes systemd (např. `ha_watchdog.service`).

Užitečné příkazy:
- `sudo systemctl status ha_watchdog.service`
- `sudo journalctl -u ha_watchdog.service -f`

### Test scénáře
1. Ověř „OK“ stav: HA běží, watchdog loguje úspěšné probe.
2. Simuluj výpadek (dočasně blokni port 8123 firewall pravidlem nebo vypni HA) a sleduj:
   - po `HA_WD_FAIL_THRESHOLD` pokusech dojde k restartu přes SSH,
   - pokud je Telegram zapnutý, přijde zpráva.
3. Po zotavení (HA opět odpovídá) watchdog čeká na `HA_WD_OK_THRESHOLD` úspěchů, pak přepne zpět do „OK“.

## Poznámky k bezpečnosti

- Nepoužívej hardcodované heslo v příkazové řádce.
- Preferuj SSH klíče a omez přístup (např. pouze z IP Raspberry Pi).
- Telegram token je citlivý údaj – drž ho jen v `.env` a necommituj do repozitáře.
