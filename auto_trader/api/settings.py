"""api / settings"""

from ..services import connection_service

import asyncio
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from ..auth import SESSION_COOKIE, AuthenticatedUser, account_profile, change_credential, require_csrf, require_user
from ..toss import TossApiError
from fastapi import APIRouter
from .. import runtime
router = APIRouter()


@router.get("/settings/account")
def settings_account(user: AuthenticatedUser = Depends(require_user)) -> dict:
    return account_profile(user)


@router.put("/settings/account/{kind}")
async def update_account_credential(kind: str, request: Request, user: AuthenticatedUser = Depends(require_csrf)) -> Response:
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="입력 내용을 확인하세요.")
    if not isinstance(payload, dict) or any(not isinstance(payload.get(key), str) for key in ("current_password", "new_value")):
        raise HTTPException(status_code=400, detail="현재 비밀번호와 변경할 값을 입력하세요.")
    try:
        await asyncio.to_thread(change_credential, user, payload["current_password"], payload["new_value"], kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    response = JSONResponse({"message": "비밀번호를 변경했습니다. 다시 로그인하세요." if kind == "password" else "PIN을 변경했습니다. 주문 인증을 다시 진행하세요."})
    if kind == "password":
        response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/settings/connection")
def settings_connection(_: AuthenticatedUser = Depends(require_user)) -> dict:
    if not runtime.toss_client.configured:
        return {"connected": False, "accounts": [], "message": "연결 정보가 없거나 로컬 키 파일을 읽을 수 없습니다. 연결 등록·변경에서 API 키와 Secret을 등록하세요. 컴퓨터를 옮겼다면 키를 다시 등록해야 합니다."}
    try:
        accounts = runtime.toss_client.accounts()
        selected = runtime.toss_client.selected_account()
        return {"connected": True, "accounts": [
            {"label": "*" * max(0, len(str(a.get("accountNo", ""))) - 4) + str(a.get("accountNo", ""))[-4:] or "증권 계좌",
             "selected": str(a["accountSeq"]) == str(selected["accountSeq"])} for a in accounts
        ], "message": "토스증권에 연결되었습니다."}
    except TossApiError:
        return {"connected": False, "accounts": [], "message": "연결을 확인하지 못했습니다. 토스 API 인증 정보와 서버의 사용 계좌 설정을 확인하세요."}


@router.put('/settings/connection')
async def save_settings_connection(payload: dict, user: AuthenticatedUser = Depends(require_csrf)):
    return await connection_service.save_settings_connection(payload, user)
