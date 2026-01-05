# mqtt_tools.py
import time
import json
from typing import Dict, Any, List, Optional
import paho.mqtt.client as mqtt
import fnmatch


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
    contains: str = "modbus_io",
    window_s: float = 1.5,
    limit: int = 500
) -> List[Dict[str, Any]]:

    items: List[Dict[str, Any]] = []
    done_at = time.monotonic() + max(0.2, min(float(window_s), 10.0))

    userdata = {"error": None, "connected": False}

    def on_connect(c, ud, flags, rc):
        if rc != 0:
            ud["error"] = f"MQTT connect failed rc={rc}"
            return
        ud["connected"] = True
        # discovery topics: homeassistant/<component>/<object_id>/config
        c.subscribe(f"{discovery_prefix}/+/+/config", qos=0)

    def on_message(c, ud, msg):
        try:
            t = msg.topic or ""
            if contains and (contains not in t):
                return

            payload = (msg.payload or b"").decode("utf-8", errors="replace")

            parsed = None
            try:
                parsed = json.loads(payload)
            except Exception:
                parsed = None

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
        clean_session=True
    )
    if username:
        client.username_pw_set(username, password)

    client.user_data_set(userdata)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(host, int(port), keepalive=20)
    client.loop_start()
    try:
        start = time.monotonic()
        while time.monotonic() < done_at and len(items) < int(limit) and not userdata["error"]:
            if (not userdata["connected"]) and (time.monotonic() - start) > 2.0:
                userdata["error"] = "MQTT not connected (timeout)"
                break
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

def mqtt_delete_retained(
    host: str,
    port: int = 1883,
    username: str = "",
    password: str = "",
    topics: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Smaže retained messages na zadaných topics: publish NULL payload, retain=True.
    Vrací { "deleted": n, "errors": [ ... ] }
    """
    if not topics:
        return {"deleted": 0, "errors": ["No topics provided"]}

    errors: List[str] = []
    deleted = 0

    client = mqtt.Client(client_id=f"rpi-admin-ui-del-{int(time.time())}", clean_session=True)
    if username:
        client.username_pw_set(username, password)

    client.connect(host, int(port), keepalive=20)
    client.loop_start()
    try:
        for t in topics:
            t = (t or "").strip()
            if not t:
                continue
            try:
                # NULL payload + retain=True -> broker smaže retained na topicu
                info = client.publish(t, payload=None, qos=1, retain=True)
                info.wait_for_publish()
                deleted += 1
            except Exception as e:
                errors.append(f"{t}: {e}")
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

    return {"deleted": deleted, "errors": errors}