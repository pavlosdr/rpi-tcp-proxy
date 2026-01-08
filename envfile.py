# /opt/rpi-admin-ui/envfile.py
from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime
from typing import Dict, Tuple, List

_ENV_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$')

def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        v = v[1:-1]
    return v

def _quote(v: str) -> str:
    # bezpečné pro mezery, #, atd.
    if v is None:
        return '""'
    v = str(v)
    if v == "":
        return '""'
    needs = any(ch.isspace() for ch in v) or "#" in v or '"' in v or "'" in v
    if not needs:
        return v
    v = v.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{v}"'

def read_env_file(path: str) -> Tuple[Dict[str, str], List[str]]:
    if not os.path.exists(path):
        return {}, []

    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    data: Dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ENV_RE.match(line)
        if not m:
            continue
        k, raw = m.group(1), m.group(2)
        data[k] = _unquote(raw.strip())
    return data, lines

def update_env_file(path: str, updates: Dict[str, str]) -> Tuple[bool, str]:
    data, lines = read_env_file(path)

    # připrav mapu indexů pro existující klíče
    key_to_idx: Dict[str, int] = {}
    for i, line in enumerate(lines):
        m = _ENV_RE.match(line)
        if m:
            key_to_idx[m.group(1)] = i

    # aplikuj změny do lines (zachová komentáře/pořadí)
    for k, v in updates.items():
        new_line = f"{k}={_quote(v)}"
        if k in key_to_idx:
            lines[key_to_idx[k]] = new_line
        else:
            lines.append(new_line)

    # backup
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak.{ts}")

    # atomic write
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".env.", dir=d, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines).rstrip() + "\n")
        os.replace(tmp, path)
        return True, "Uloženo do .env."
    except Exception as e:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False, f"Chyba při zápisu .env: {e}"
