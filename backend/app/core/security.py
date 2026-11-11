import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Response

from app.core.config import get_settings


_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    try:
        _hasher.verify(stored_hash, plain)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    token = jwt.encode(payload, settings.jwt_access_secret, algorithm="HS256")
    return token, settings.access_token_ttl_minutes * 60


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token.

    Returns the payload dict on success.
    Raises ValueError("invalid token") on any validation failure.
    Internal PyJWT details are intentionally NOT propagated to the caller
    so they cannot leak through HTTP responses.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_access_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    except jwt.exceptions.InvalidTokenError:
        # Sanitize: do not leak PyJWT exception details to the caller.
        raise ValueError("invalid token")

    if payload.get("typ") != "access":
        raise ValueError("invalid token")

    return payload


def generate_refresh_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def set_refresh_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.cookie_name,
        value=raw_token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        path=settings.cookie_path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )


def clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.cookie_name,
        path=settings.cookie_path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
    )
