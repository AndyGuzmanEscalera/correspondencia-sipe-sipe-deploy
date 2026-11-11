from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from app.modules.auth.models import AuthSession
from app.modules.identity.user import User


class InvalidSessionError(Exception):
    """Refresh cookie missing or session not found / already revoked."""


class ExpiredSessionError(Exception):
    """Session reached its absolute expiry."""


class UserUnavailableError(Exception):
    """User no longer exists or is not active; session is revoked as a side effect."""


def authenticate_user(
    db: Session,
    username: str,
    password: str,
) -> User | None:
    normalized = username.strip().lower()
    user = db.execute(
        select(User).where(
            User.is_active == True,  # noqa: E712
            func.lower(User.username) == normalized,
        )
    ).scalar_one_or_none()
    if user is None:
        return None
    if not verify_password(user.password_hash, password):
        return None

    user.last_login_at = datetime.now(timezone.utc)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    db.commit()
    db.refresh(user)
    return user


def create_session(db: Session, user: User) -> AuthSession:
    settings = get_settings()
    raw, digest = generate_refresh_token()
    now = datetime.now(timezone.utc)
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=digest,
        expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    # raw is returned to the caller via this side-channel; it is not persisted.
    session._pending_raw_token = raw  # type: ignore[attr-defined]
    return session


def pop_pending_raw_token(session: AuthSession) -> str:
    raw = getattr(session, "_pending_raw_token", None)
    if raw is None:
        raise RuntimeError("No pending raw token on this session")
    return raw


def find_active_session_by_raw(db: Session, raw: str) -> AuthSession | None:
    digest = hash_refresh_token(raw)
    return db.execute(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == digest,
            AuthSession.revoked_at.is_(None),
        )
    ).scalar_one_or_none()


def rotate_session_atomically(db: Session, raw: str) -> tuple[User, str]:
    """Validate, lock, and rotate a refresh session in a single transaction.

    Uses SELECT ... FOR UPDATE (via SQLAlchemy with_for_update()) to ensure
    that two concurrent refresh requests for the same cookie cannot both
    succeed: the second one waits for the first to commit, then re-reads
    and finds the now-revoked/invalidated session.

    Returns (user, new_raw_token) on success.
    Raises:
        InvalidSessionError  - cookie missing / session not found / revoked
        ExpiredSessionError  - session.expires_at < now
        UserUnavailableError - user gone or is_active=false (session is revoked)
    """
    digest = hash_refresh_token(raw)

    # SELECT ... FOR UPDATE: row-level lock until commit/rollback.
    # The lock is released when this transaction ends, keeping it short.
    session = db.execute(
        select(AuthSession)
        .where(
            AuthSession.refresh_token_hash == digest,
            AuthSession.revoked_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if session is None:
        raise InvalidSessionError("Sesión inválida")

    if session.expires_at < datetime.now(timezone.utc):
        raise ExpiredSessionError("Sesión expirada")

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
        raise UserUnavailableError("Usuario no disponible")

    new_raw, new_digest = generate_refresh_token()
    session.refresh_token_hash = new_digest
    session.last_used_at = datetime.now(timezone.utc)
    # IMPORTANT: expires_at is NOT modified. Refresh TTL is absolute.
    db.commit()
    db.refresh(session)
    return user, new_raw


def revoke_session(db: Session, session: AuthSession) -> None:
    if session.revoked_at is not None:
        return
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
