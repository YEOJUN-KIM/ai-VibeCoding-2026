"""HTTP security headers and request-local workspace scope."""

import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from .auth import SESSION_COOKIE, session_from_request
from .user_workspace import current_workspace
from . import runtime


async def security_headers(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "다른 사이트의 요청은 허용하지 않습니다."}, status_code=403)
    if request.url.path in {"/static/index.html", "/static/live.html"}:
        return RedirectResponse("/", status_code=303)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def user_account_scope(request: Request, call_next):
    if SESSION_COOKIE not in request.cookies or request.url.path.startswith('/static/'):
        return await call_next(request)
    user = await asyncio.to_thread(session_from_request, request)
    if user is None:
        return await call_next(request)
    state = await asyncio.to_thread(runtime.workspaces.get, user.id)
    token = current_workspace.set(state)
    try:
        return await call_next(request)
    finally:
        current_workspace.reset(token)
