"""api / auth"""

from fastapi import Depends, HTTPException, Request, Response
from ..auth import SESSION_COOKIE, AuthenticatedUser, authenticate, delete_session, live_pin_status, lock_session, require_csrf, require_session, require_session_csrf, require_user, unlock_session, verify_live_pin
from ..models import LivePinRequest, LivePinStatus, LoginRequest, SessionInfo
from ..settings import settings
from fastapi import APIRouter
from .. import runtime
router = APIRouter()


@router.post("/auth/login", response_model=SessionInfo)
def login(payload: LoginRequest, response: Response) -> SessionInfo:
    result = authenticate(payload.username, payload.password)
    if not result:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    raw_token, user = result
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=settings.session_minutes * 60,
        httponly=True,
        secure=False,
        samesite="strict",
        path="/",
    )
    return SessionInfo(username=user.username, csrf_token=user.csrf_token, expires_at=user.expires_at)


@router.get("/auth/me", response_model=SessionInfo)
def current_session(
    request: Request, response: Response, user: AuthenticatedUser = Depends(require_session)
) -> SessionInfo:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if raw_token:
        response.set_cookie(
            SESSION_COOKIE, raw_token, max_age=settings.session_minutes * 60,
            httponly=True, secure=False, samesite="strict", path="/",
        )
    return SessionInfo(
        username=user.username, csrf_token=user.csrf_token,
        expires_at=user.expires_at, locked=user.locked,
    )


@router.post("/auth/lock", response_model=SessionInfo)
def lock_current_session(
    request: Request, user: AuthenticatedUser = Depends(require_csrf),
) -> SessionInfo:
    try:
        lock_session(request, user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SessionInfo(
        username=user.username, csrf_token=user.csrf_token,
        expires_at=user.expires_at, locked=True,
    )


@router.post("/auth/unlock", response_model=SessionInfo)
def unlock_current_session(
    payload: LivePinRequest, request: Request,
    user: AuthenticatedUser = Depends(require_session_csrf),
) -> SessionInfo:
    try:
        unlock_session(request, user, payload.pin)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SessionInfo(
        username=user.username, csrf_token=user.csrf_token,
        expires_at=user.expires_at, locked=False,
    )


@router.get("/auth/live-pin", response_model=LivePinStatus)
def current_live_pin_status(request: Request, user: AuthenticatedUser = Depends(require_user)) -> LivePinStatus:
    configured, authorized_until = live_pin_status(request, user)
    return LivePinStatus(configured=configured, authorized=authorized_until is not None,
                         authorized_until=authorized_until)


@router.post("/auth/live-pin/verify", response_model=LivePinStatus)
def authorize_live_pin(
    payload: LivePinRequest, request: Request, user: AuthenticatedUser = Depends(require_csrf)
) -> LivePinStatus:
    try:
        authorized_until = verify_live_pin(request, user, payload.pin)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return LivePinStatus(configured=True, authorized=True, authorized_until=authorized_until)


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request,
    _: AuthenticatedUser = Depends(require_csrf),
) -> Response:
    delete_session(request)
    await runtime.engine.stop()
    result = Response(status_code=204)
    result.delete_cookie(SESSION_COOKIE, path="/")
    return result
