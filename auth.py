"""
Authentication & authorization

Modul zajišťující autentizaci uživatele
pro rpi-admin-ui webové rozhraní.

Funkce:
- Login / logout
- Ověření hesla
- Session management
- Ochrana UI endpointů

Používáno:
- app.py
"""

import os
from functools import wraps
from flask import session, redirect, url_for

def check_credentials(username: str, password: str) -> bool:
    return (
        username == os.getenv("UI_USER", "admin")
        and password == os.getenv("UI_PASS", "admin")
    )

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper
