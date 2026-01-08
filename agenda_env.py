# /opt/rpi-admin-ui/agenda_env.py
from __future__ import annotations

from typing import Dict, Tuple, Any
from flask import request

from envfile import read_env_file, update_env_file
from config.agendas import AGENDAS

def _field_is_editable(agenda: dict, field: dict) -> bool:
    if "editable" in field:
        return bool(field["editable"])
    # default editable=True
    return True

def _parse_value(field: dict, raw: str, existing: str) -> Tuple[bool, Any, str]:
    """Vrátí (ok, parsed_value, error_message). existing je původní hodnota z .env (kvůli secret)."""
    t = (field.get("type") or "str").lower()
    required = bool(field.get("required", False))

    # secret: prázdné = neměnit
    if t == "secret" and (raw is None or raw == ""):
        return True, None, ""  # None = skip update

    if raw is None:
        raw = ""

    raw = raw.strip()

    if required and raw == "":
        return False, None, "Povinné pole."

    if raw == "" and not required:
        # povolujeme prázdné (uloží se jako "")
        if t in ("int", "float"):
            return True, "", ""
        return True, "", ""

    try:
        if t == "int":
            v = int(raw)
            mn = field.get("min")
            mx = field.get("max")
            if mn is not None and v < mn:
                return False, None, f"Min. hodnota je {mn}."
            if mx is not None and v > mx:
                return False, None, f"Max. hodnota je {mx}."
            return True, str(v), ""

        if t == "float":
            v = float(raw.replace(",", "."))
            mn = field.get("min")
            mx = field.get("max")
            if mn is not None and v < mn:
                return False, None, f"Min. hodnota je {mn}."
            if mx is not None and v > mx:
                return False, None, f"Max. hodnota je {mx}."
            return True, str(v), ""

        if t == "select":
            choices = field.get("choices") or []
            if raw and choices and raw not in choices:
                return False, None, "Neplatná volba."
            return True, raw, ""

        # text/str
        return True, raw, ""

    except Exception:
        return False, None, "Neplatný formát."

def build_agenda_context(agenda_id: str) -> Tuple[bool, dict, str]:
    agenda = AGENDAS.get(agenda_id)
    if not agenda:
        return False, {}, "Neznámá agenda."

    env_path = agenda["env_path"]
    env_data, _lines = read_env_file(env_path)

    # explicit keys
    explicit = {f["key"] for f in agenda.get("fields", [])}

    # auto “other”: najdi MODBUS_IO_* v .env, které nejsou v configu
    auto_prefix = agenda.get("auto_prefix")
    auto_fields = []
    if auto_prefix:
        for k in sorted(env_data.keys()):
            if k.startswith(auto_prefix) and k not in explicit:
                auto_fields.append({
                    "key": k,
                    "label": k,
                    "tab": "other",
                    "section": "other_auto",
                    "type": "str",
                    "required": False,
                    "editable": False,
                    "help": "Detekováno v .env (není explicitně v konfiguraci).",
                })

    fields = list(agenda.get("fields", [])) + auto_fields

    values = {f["key"]: env_data.get(f["key"], "") for f in fields}

    ctx = {
        "agenda_id": agenda_id,
        "agenda": agenda,
        "tabs": agenda.get("tabs", []),
        "sections": agenda.get("sections", []),
        "fields": fields,
        "values": values,
    }
    return True, ctx, ""

def handle_agenda_post(agenda_id: str) -> Tuple[bool, dict, str]:
    ok, ctx, err = build_agenda_context(agenda_id)
    if not ok:
        return False, {}, err

    agenda = ctx["agenda"]
    env_path = agenda["env_path"]

    # aktuální env (kvůli secret)
    env_data, _ = read_env_file(env_path)

    updates: Dict[str, str] = {}
    errors: Dict[str, str] = {}

    for field in ctx["fields"]:
        key = field["key"]
        if not _field_is_editable(agenda, field):
            continue  # server-side enforcement

        raw = request.form.get(key, "")
        existing = env_data.get(key, "")

        okv, parsed, emsg = _parse_value(field, raw, existing)
        if not okv:
            errors[key] = emsg
            continue

        # secret: parsed None => skip
        if field.get("type") == "secret" and parsed is None:
            continue

        updates[key] = parsed

    if errors:
        # přepíš values tak, aby UI ukázalo to, co user zadal (a chyby)
        posted_values = dict(ctx["values"])
        for k in request.form.keys():
            posted_values[k] = request.form.get(k, "")
        ctx["values"] = posted_values
        ctx["errors"] = errors
        return False, ctx, "Formulář obsahuje chyby."

    ok_save, msg = update_env_file(env_path, updates)
    if not ok_save:
        ctx["errors"] = {}
        return False, ctx, msg

    return True, ctx, msg
