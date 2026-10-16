"""api / settings"""

from ..services import connection_service

import asyncio
from decimal import Decimal
from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from ..auth import SESSION_COOKIE, AuthenticatedUser, account_profile, change_credential, require_csrf, require_user
from ..models import LiveStrategy, LiveStrategyWrite
from ..settings import settings
from ..toss import TossApiError
from ..live_strategies import delete_strategy, list_strategies, save_strategy
from ..strategy_presets import weekly_preset, preset_snapshot_status
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


@router.get("/settings/strategies", response_model=list[LiveStrategy])
def list_strategy_settings(user: AuthenticatedUser = Depends(require_user)) -> list[LiveStrategy]:
    return list_strategies(user.id)


@router.get("/settings/strategy-preset-status")
def strategy_preset_status(_: AuthenticatedUser = Depends(require_user)) -> dict:
    return preset_snapshot_status()


@router.get("/settings/strategy-presets/{kind}")
def strategy_preset_preview(
    kind: str, order_amount: Decimal = Query(default=Decimal("300000"), ge=10000, le=1000000),
    _: AuthenticatedUser = Depends(require_user),
) -> dict:
    if kind not in {"popular", "affordable"}:
        raise HTTPException(status_code=404, detail="지원하지 않는 기본 프리셋입니다.")
    try:
        return weekly_preset(kind, order_amount, runtime.toss_client.strategy_preset_candidates,
                             fee_rate=settings.paper_fee_rate, slippage_rate=settings.paper_slippage_rate,
                             max_count=10)
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _strategy_stock_name(symbol: str) -> str:
    try:
        stock = runtime.toss_client.domestic_stock(symbol.upper())
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not stock:
        raise HTTPException(status_code=404, detail="국내 상장 종목을 찾지 못했습니다.")
    return str(stock.get("name") or symbol.upper())


def _strategy_targets(payload: LiveStrategyWrite) -> list[tuple[str, str]]:
    symbols = list(dict.fromkeys(symbol.upper() for symbol in (payload.symbols or [payload.symbol])))
    if not symbols or len(symbols) > 30:
        raise HTTPException(status_code=422, detail="전략 대상 종목은 1개 이상 30개 이하로 선택하세요.")
    targets = []
    for symbol in symbols:
        if not symbol.isdigit() or len(symbol) != 6:
            raise HTTPException(status_code=422, detail=f"올바르지 않은 국내 종목코드입니다: {symbol}")
        targets.append((symbol, _strategy_stock_name(symbol)))
    return targets


@router.post("/settings/strategies", response_model=LiveStrategy, status_code=201)
def create_strategy_settings(
    payload: LiveStrategyWrite, user: AuthenticatedUser = Depends(require_csrf),
) -> LiveStrategy:
    if payload.execution_mode == "LIVE" and payload.enabled:
        raise HTTPException(status_code=409, detail="LIVE 자동매매 엔진이 준비될 때까지 실제 실행 전략은 활성화할 수 없습니다.")
    try:
        targets = _strategy_targets(payload)
        return save_strategy(user.id, payload, targets[0][1], targets=targets)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/settings/strategies/{strategy_id}", response_model=LiveStrategy)
def update_strategy_settings(
    strategy_id: int, payload: LiveStrategyWrite,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveStrategy:
    if payload.execution_mode == "LIVE" and payload.enabled:
        raise HTTPException(status_code=409, detail="LIVE 자동매매 엔진이 준비될 때까지 실제 실행 전략은 활성화할 수 없습니다.")
    try:
        targets = _strategy_targets(payload)
        return save_strategy(user.id, payload, targets[0][1], strategy_id=strategy_id, targets=targets)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/settings/strategies/{strategy_id}", status_code=204)
def delete_strategy_settings(
    strategy_id: int, user: AuthenticatedUser = Depends(require_csrf),
) -> Response:
    try:
        delete_strategy(user.id, strategy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)
