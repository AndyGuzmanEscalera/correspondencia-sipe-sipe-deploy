from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    clear_refresh_cookie,
    create_access_token,
    set_refresh_cookie,
)
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import (
    AuthResponse,
    LoginRequest,
    MeResponse,
    RefreshResponse,
)
from app.modules.auth.institutional_context import resolve_me_employee_context
from app.modules.auth.service import (
    ExpiredSessionError,
    InvalidSessionError,
    UserUnavailableError,
    authenticate_user,
    create_session,
    find_active_session_by_raw,
    pop_pending_raw_token,
    revoke_session,
    rotate_session_atomically,
)
from app.modules.identity.rbac_service import RbacService
from app.modules.identity.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_to_brief(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "employee_id": str(user.employee_id) if user.employee_id else None,
        "is_active": user.is_active,
    }


@router.post("/login", response_model=AuthResponse)
def login(
    body: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> AuthResponse:
    user = authenticate_user(db, body.username, body.password)
    if user is None:
        # Generic 401: do not reveal whether the username exists.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
        )

    access_token, expires_in = create_access_token(user.id)
    session = create_session(db, user)
    raw_refresh = pop_pending_raw_token(session)
    set_refresh_cookie(response, raw_refresh)

    return AuthResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=_user_to_brief(user),  # type: ignore[arg-type]
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> RefreshResponse:
    settings = get_settings()
    raw = request.cookies.get(settings.cookie_name)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión inválida",
        )

    try:
        user, new_raw = rotate_session_atomically(db, raw)
    except InvalidSessionError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión inválida",
        )
    except ExpiredSessionError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión expirada",
        )
    except UserUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no disponible",
        )

    set_refresh_cookie(response, new_raw)
    access_token, expires_in = create_access_token(user.id)
    return RefreshResponse(access_token=access_token, expires_in=expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    settings = get_settings()
    raw = request.cookies.get(settings.cookie_name)
    if raw:
        session = find_active_session_by_raw(db, raw)
        if session is not None:
            revoke_session(db, session)
    clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
def me(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MeResponse:
    rbac = RbacService(db)
    return MeResponse(
        **_user_to_brief(current_user),
        employee=resolve_me_employee_context(db, current_user),
        roles=rbac.get_user_role_codes(current_user.id),
        permissions=rbac.get_effective_permission_codes(current_user.id),
    )
