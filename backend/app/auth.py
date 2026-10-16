"""Persistent multi-user authentication and CSRF helpers."""

import base64
import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import HTTPException, Request

from app import database


SESSION_COOKIE = "mini_notebooklm_session"
CSRF_COOKIE = "mini_notebooklm_csrf"
SESSION_TTL_SECONDS = 8 * 60 * 60
PBKDF2_ITERATIONS = 600_000
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _setting(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _auth_secret() -> bytes:
    value = _setting("AUTH_SECRET")
    try:
        secret = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Authentication is not configured correctly.") from exc
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured correctly.")
    return secret


def validate_identifier(identifier: str) -> str:
    value = identifier.strip()
    if len(value) > 254:
        raise HTTPException(status_code=422, detail="Email or username is too long.")
    if "@" in value:
        if not EMAIL_RE.fullmatch(value):
            raise HTTPException(status_code=422, detail="Enter a valid email address or a 3–32 character username.")
    elif not IDENTIFIER_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="Username must be 3–32 characters and use letters, numbers, dots, underscores, or hyphens.")
    return value


def validate_password(password: str) -> None:
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="Password must contain at least 6 characters.")
    if len(password) > 1024:
        raise HTTPException(status_code=422, detail="Password must be 1024 characters or fewer.")


def _parse_password_hash(encoded: str) -> tuple[int, bytes, bytes]:
    try:
        algorithm, rounds_text, salt_text, digest_text = encoded.split("$", 3)
        rounds = int(rounds_text)
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        digest = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
    except (ValueError, TypeError) as exc:
        raise ValueError("Malformed password hash") from exc
    if algorithm != "pbkdf2_sha256" or rounds < 600_000 or len(salt) < 16 or len(digest) != 32:
        raise ValueError("Unsupported password hash")
    return rounds, salt, digest


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=32)
    encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${encode(salt)}${encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        rounds, salt, expected = _parse_password_hash(encoded)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=32)
    return hmac.compare_digest(actual, expected)


def create_csrf_token() -> str:
    nonce = secrets.token_urlsafe(32)
    signature = hmac.new(_auth_secret(), nonce.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{nonce}.{signature}"


def valid_csrf_token(token: str) -> bool:
    try:
        nonce, signature = token.rsplit(".", 1)
        expected = hmac.new(_auth_secret(), nonce.encode("ascii"), hashlib.sha256).hexdigest()
    except (ValueError, UnicodeEncodeError, HTTPException):
        return False
    return hmac.compare_digest(signature, expected)


def allowed_origins() -> set[str]:
    configured = _setting("AUTH_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174")
    return {origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()}


def require_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or origin not in allowed_origins():
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


def issue_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    database.save_session(token_hash, user_id, time.time() + SESSION_TTL_SECONDS)
    return token


def revoke_session(token: str | None) -> None:
    if token:
        database.delete_session(hashlib.sha256(token.encode("utf-8")).hexdigest())


def require_session(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user = database.get_session_user(hashlib.sha256(token.encode("utf-8")).hexdigest())
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def require_csrf(request: Request) -> dict:
    user = require_session(request)
    require_allowed_origin(request)
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get("x-csrf-token", "")
    if not cookie_token or not hmac.compare_digest(cookie_token, header_token) or not valid_csrf_token(cookie_token):
        raise HTTPException(status_code=403, detail="A valid CSRF token is required.")
    return user


def login_rate_limited(key: str) -> bool:
    return database.count_recent_attempts(key, time.time() - 60) >= 10


def record_failed_login(key: str) -> None:
    database.record_login_attempt(key)


def clear_login_failures(key: str) -> None:
    database.clear_login_attempts(key)


def is_production() -> bool:
    return _setting("APP_ENV").lower() == "production"


def cookie_samesite() -> str:
    value = _setting("AUTH_COOKIE_SAMESITE", "strict").lower()
    if value not in {"strict", "lax", "none"}:
        raise HTTPException(status_code=503, detail="Cookie security configuration is invalid.")
    if value == "none" and not is_production():
        raise HTTPException(status_code=503, detail="SameSite=None cookies require production HTTPS.")
    return value


if __name__ == "__main__":
    from getpass import getpass

    first = getpass("Password to hash (input hidden): ")
    second = getpass("Confirm password: ")
    if len(first) < 6 or first != second:
        raise SystemExit("Passwords must match and be at least 6 characters long.")
    print("PASSWORD_HASH=" + hash_password(first))
