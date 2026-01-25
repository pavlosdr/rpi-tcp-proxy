"""
MQTT helper utilities

Sdílené utility pro práci s MQTT v rpi-admin-ui.

Funkce:
- Sestavování MQTT topiců
- Publish helpery
- JSON publish
- Společné konvence pro MQTT komunikaci

Používáno:
- mqtt_report.py
- infigy_ws_to_mqtt.py
- ha_watchdog.py
"""

import time
import json
from typing import Dict, Any, List, Optional
import paho.mqtt.client as mqtt
import fnmatch
from services_control import (
     MQTT_DISCOVERY_TARGETS,
)
from envfile import env_str, env_int

def expand_delete_commands_to_topics(commands_text: str, retained_items: list[dict]) -> dict:
    """
    Převede textové příkazy z UI na seznam konkrétních topiců.
    Podporuje:
      - přímý topic
      - glob pattern s *
      - DEVICE <identifier>
    """
    retained_topics = [i["topic"] for i in retained_items if i.get("topic")]
    out = []
    errors = []

    lines = [ln.strip() for ln in (commands_text or "").splitlines() if ln.strip()]
    for ln in lines:
        if ln.upper().startswith("DEVICE "):
            ident = ln.split(None, 1)[1].strip()
            matched = []
            for it in retained_items:
                pj = it.get("payload_json") or {}
                dev = pj.get("device") or {}
                ids = dev.get("identifiers") or dev.get("ids") or []
                # ids může být string nebo list
                if isinstance(ids, str):
                    ids = [ids]
                if ident in ids:
                    matched.append(it["topic"])
            if not matched:
                errors.append(f"DEVICE {ident}: nic nenalezeno")
            out.extend(matched)
            continue

        # glob / exact
        if "*" in ln or "?" in ln:
            matched = [t for t in retained_topics if fnmatch.fnmatch(t, ln)]
            if not matched:
                errors.append(f"{ln}: glob nic nenašel")
            out.extend(matched)
        else:
            out.append(ln)

    # unikátní + stabilní pořadí
    seen = set()
    uniq = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)

    return {"topics": uniq, "errors": errors}

def mqtt_list_retained_discovery(
    host: str,
    port: int = 1883,
    username: str = "",
    password: str = "",
    discovery_prefix: str = "homeassistant",
    device_id: str = "",
    contains: str = "",
    window_s: float = 2.0,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """
    Nacte retained MQTT Discovery configy.

    Discovery topic tvar:
      homeassistant/<domain>/<device_id>/<object_id>/config

    - pokud je device_id zadano, omezime subscribe na konkretni zarizeni
    - vraci list polozek: topic, payload, json, retain, qos
    """
    items: List[Dict[str, Any]] = []
    seen_topics: set[str] = set()
    done_at = time.monotonic() + max(0.2, min(float(window_s), 10.0))

    userdata = {"error": None, "connected": False, "subscribed": False}

    # topic filter
    if device_id:
        sub_topic = f"{discovery_prefix}/+/{device_id}/+/config"
    else:
        sub_topic = f"{discovery_prefix}/+/+/+/config"

    def on_connect(c, ud, flags, rc):
        if int(rc) != 0:
            ud["error"] = f"MQTT connect failed rc={rc}"
            return
        ud["connected"] = True
        c.subscribe(sub_topic, qos=0)

    def on_subscribe(c, ud, mid, granted_qos):
        ud["subscribed"] = True

    def on_message(c, ud, msg):
        try:
            t = msg.topic or ""
            if contains and (contains not in t):
                return
            if t in seen_topics:
                return

            payload = (msg.payload or b"").decode("utf-8", errors="replace")
            parsed = None
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = None

            seen_topics.add(t)
            items.append({
                "topic": t,
                "payload": payload,
                "json": parsed,
                "retain": bool(getattr(msg, "retain", False)),
                "qos": int(getattr(msg, "qos", 0)),
            })
        except Exception:
            return

    client = mqtt.Client(
        client_id=f"rpi-admin-ui-discovery-{int(time.time())}",
        clean_session=True,
    )
    if username:
        client.username_pw_set(username, password)

    client.user_data_set(userdata)
    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message

    client.connect(host, int(port), keepalive=20)
    client.loop_start()

    try:
        start = time.monotonic()

        # cekej na connect
        while not userdata["connected"] and not userdata["error"]:
            if (time.monotonic() - start) > 2.0:
                userdata["error"] = "MQTT not connected (timeout)"
                break
            time.sleep(0.05)

        # cekej na subscribe (dulezite, aby retained stihly prijit)
        if userdata["connected"] and not userdata["error"]:
            t0 = time.monotonic()
            while not userdata["subscribed"] and not userdata["error"]:
                if (time.monotonic() - t0) > 1.0:
                    # i kdyz to nedorazi, nezabij to, jen pokracuj
                    break
                time.sleep(0.05)

        # sber okno
        while time.monotonic() < done_at and len(items) < int(limit) and not userdata["error"]:
            time.sleep(0.05)

    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

    if userdata["error"]:
        raise RuntimeError(userdata["error"])

    items.sort(key=lambda x: x["topic"])
    return items


def _get_mqtt_conn_from_env(service_key: str) -> Dict[str, Any]:
    """
    Vrátí MQTT připojení podle service_key (infigy/report/modbus_io).
    Fallback pořadí:
      1) klíče specifické pro service_key (MQTT_DISCOVERY_TARGETS)
      2) UI_MQTT_* (pokud chceš mít UI vlastní creds)
      3) obecné MQTT_* (legacy)
      4) hard default (192.168.1.20:1883, discovery_prefix=homeassistant)
    """
    key = (service_key or "").strip().lower()
    meta = MQTT_DISCOVERY_TARGETS.get(key, {})

    def pick_str(*env_keys: str, default: str = "") -> str:
        for k in env_keys:
            if not k:
                continue
            v = env_str(k, "").strip()
            if v != "":
                return v
        return default

    def pick_int(*env_keys: str, default: int = 0) -> int:
        for k in env_keys:
            if not k:
                continue
            # env_int s defaultem -> když klíč není, vrátí default, tj. 0, což nechceme
            # proto si to vezmeme jako str a až pak převedeme
            raw = env_str(k, "").strip()
            if raw != "":
                try:
                    return int(raw)
                except ValueError:
                    # ignoruj špatnou hodnotu, spadne to do fallbacku níž
                    pass
        return default

    host = pick_str(
        meta.get("host_env", ""),
        "UI_MQTT_HOST",
        "MQTT_HOST",
        default="192.168.1.20",
    )

    port = pick_int(
        meta.get("port_env", ""),
        "UI_MQTT_PORT",
        "MQTT_PORT",
        default=1883,
    )

    username = pick_str(
        meta.get("username_env", ""),
        "UI_MQTT_USERNAME",
        "MQTT_USERNAME",
        default="",
    )

    password = pick_str(
        meta.get("password_env", ""),
        "UI_MQTT_PASSWORD",
        "MQTT_PASSWORD",
        default="",
    )

    discovery_prefix = (
        pick_str(
            meta.get("discovery_prefix_env", ""),
            "UI_MQTT_DISCOVERY_PREFIX",
            "MQTT_DISCOVERY_PREFIX",
            default="homeassistant",
        ).strip() or "homeassistant"
    )

    # volitelně: užitečné pro filtrování záznamů v UI
    device_id = pick_str(meta.get("device_id_env", ""), default="")
    base_topic = pick_str(meta.get("base_topic_env", ""), default="")
    client_id = pick_str(meta.get("client_id_env", ""), default="")
    label = meta.get("label", key or "MQTT")

    return {
        "service_key": key,
        "label": label,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "discovery_prefix": discovery_prefix,
        # doplňky pro UI (nepovinné)
        "device_id": device_id,
        "base_topic": base_topic,
        "client_id": client_id,
    }

def _resolve_device_id_for_service(service_key: str) -> str:
    meta = MQTT_DISCOVERY_TARGETS.get(service_key) or {}
    env_key = (meta.get("device_id_env") or "").strip()
    if not env_key:
        return ""
    return env_str(env_key, "").strip()

def mqtt_delete_retained_discovery(
    host: str,
    port: int = 1883,
    username: str = "",
    password: str = "",
    topics: Optional[List[str]] = None,
    qos: int = 1,
) -> int:
    """
    Smaze retained discovery configy pro dane topicy.
    Vraci pocet smazanych topicu.
    """
    if not topics:
        return 0

    client = mqtt.Client(
        client_id=f"rpi-admin-ui-discovery-del-{int(time.time())}",
        clean_session=True,
    )
    if username:
        client.username_pw_set(username, password)

    client.connect(host, int(port), keepalive=20)
    client.loop_start()
    try:
        deleted = 0
        for t in topics:
            # null payload + retain=True smaze retained message
            client.publish(t, payload=None, qos=int(qos), retain=True)
            deleted += 1
        return deleted
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

def mqtt_cleanup_discovery_for_device(
    host: str,
    port: int,
    username: str,
    password: str,
    discovery_prefix: str,
    device_id: str,
    contains: str = "",
    window_s: float = 2.0,
    limit: int = 2000,
) -> Dict[str, Any]:
    items = mqtt_list_retained_discovery(
        host=host,
        port=port,
        username=username,
        password=password,
        discovery_prefix=discovery_prefix,
        device_id=device_id,
        contains=contains,
        window_s=window_s,
        limit=limit,
    )
    topics = [x["topic"] for x in items]
    n = mqtt_delete_retained_discovery(
        host=host, port=port, username=username, password=password, topics=topics, qos=1
    )
    return {"found": len(items), "deleted": n, "device_id": device_id, "contains": contains}