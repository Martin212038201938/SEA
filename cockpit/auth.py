"""Login mit Passwort-Hashes aus der Konfiguration, signierte Session-Cookies."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config

SESSION_COOKIE = "sea_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 Tage


def _serializer_obj() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.settings.secret_key, salt="sea-cockpit-session")

ITERATIONS = 390_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "PBKDF2$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "PBKDF2":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return hmac.compare_digest(actual, expected)


def authenticate(username: str, password: str) -> bool:
    username = (username or "").strip().lower()
    stored = config.settings.users.get(username)
    if not stored:
        # Zeit konstant halten
        hash_password(password or "")
        return False
    return verify_password(password or "", stored)


def make_session_cookie(username: str) -> str:
    return _serializer_obj().dumps({"u": username, "csrf": secrets.token_urlsafe(16)})


def read_session(request: Request) -> dict | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        return _serializer_obj().loads(raw, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None


def current_user(request: Request) -> str:
    """FastAPI-Dependency für geschützte Seiten (leitet auf Login um)."""
    sess = read_session(request)
    if not sess or sess.get("u") not in config.settings.users:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return sess["u"]


def current_user_api(request: Request) -> str:
    """Für JSON-Endpunkte: 401 statt Redirect."""
    sess = read_session(request)
    if not sess or sess.get("u") not in config.settings.users:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return sess["u"]


def csrf_token(request: Request) -> str:
    sess = read_session(request)
    return sess.get("csrf", "") if sess else ""


def check_csrf(request: Request, token: str) -> None:
    expected = csrf_token(request)
    if not expected or not hmac.compare_digest(expected, token or ""):
        raise HTTPException(status_code=400, detail="Ungültiges Formular-Token")
