"""api / paper"""

from ..services import paper_workspace_service

from datetime import date
from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse
from ..auth import AuthenticatedUser, require_csrf, require_user
from ..models import Account, Order, OrderRequest, PaperWorkspaceStatus, PaperManagementUpdate, Quote, RiskSettings, RiskSettingsUpdate, RiskStatus, Stock, StrategySettingsUpdate, StrategyStatus
from ..decision_history import read_decision_history
from fastapi import APIRouter
from .. import runtime
router = APIRouter()


@router.get("/paper/decisions/history")
def paper_decision_history(
    account_id: int | None = Query(None, ge=1),
    strategy_id: int | None = Query(None, ge=1),
    symbol: str = Query("", max_length=50), action: str = Query("", max_length=20),
    start: date | None = None, end: date | None = None,
    page: int = Query(1, ge=1, le=1000000), page_size: int = Query(20, ge=20, le=100),
    before_id: int | None = Query(None, ge=0),
    user: AuthenticatedUser = Depends(require_user),
):
    try:
        return read_decision_history(user.id, account_id=account_id, strategy_id=strategy_id,
                                     symbol=symbol, action=action, start=start, end=end,
                                     page=page, page_size=page_size, before_id=before_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/paper/stocks", response_model=list[Stock])
def stocks(_: AuthenticatedUser = Depends(require_user)) -> list[Stock]:
    return runtime.market.stocks()


@router.get("/paper/workspace", response_model=PaperWorkspaceStatus)
def paper_workspace(_: AuthenticatedUser = Depends(require_user)) -> PaperWorkspaceStatus:
    return paper_workspace_service.paper_workspace(_)


@router.put('/paper/management', response_model=PaperWorkspaceStatus)
async def paper_management(payload: PaperManagementUpdate, user: AuthenticatedUser = Depends(require_csrf)):
    return await paper_workspace_service.paper_management(payload, user)


@router.post("/paper/accounts/{mode}/select", response_model=PaperWorkspaceStatus)
async def select_paper_account(mode: str, user: AuthenticatedUser = Depends(require_csrf)) -> PaperWorkspaceStatus:
    return await paper_workspace_service.select_paper_account(mode, user)


@router.post("/paper/snapshot/live", response_model=PaperWorkspaceStatus)
async def snapshot_live_account(_: AuthenticatedUser = Depends(require_csrf)) -> PaperWorkspaceStatus:
    return await paper_workspace_service.snapshot_live_account(_)


@router.post("/paper/strategies/{strategy_id}/select", response_model=PaperWorkspaceStatus)
async def select_paper_strategy(
    strategy_id: int, user: AuthenticatedUser = Depends(require_csrf),
) -> PaperWorkspaceStatus:
    return await paper_workspace_service.select_paper_strategy(strategy_id, user)


@router.get("/quotes", response_model=list[Quote])
def quotes(move: bool = False, _: AuthenticatedUser = Depends(require_user)) -> list[Quote]:
    if move:
        raise HTTPException(status_code=400, detail="시세 갱신은 자동매매 실행 중에만 가능합니다.")
    return runtime.market.quotes(move=move)


@router.get("/paper/journal", response_class=FileResponse)
def paper_journal(_: AuthenticatedUser = Depends(require_user)):
    if runtime.broker.journal_path is None or not runtime.broker.journal_path.exists():
        raise HTTPException(status_code=404, detail="저장된 모의 거래 기록이 없습니다.")
    return FileResponse(runtime.broker.journal_path, media_type="application/x-ndjson", filename="paper-orders.jsonl")


@router.get("/account", response_model=Account)
def account(_: AuthenticatedUser = Depends(require_user)) -> Account:
    return runtime.broker.account()


@router.get("/orders", response_model=list[Order])
def orders(_: AuthenticatedUser = Depends(require_user)) -> list[Order]:
    return runtime.broker.orders()


@router.get("/risk", response_model=RiskStatus)
def risk_status(_: AuthenticatedUser = Depends(require_user)) -> RiskStatus:
    return runtime.broker.risk_manager.status()


@router.put("/risk/settings", response_model=RiskSettings)
def update_risk_settings(
    payload: RiskSettingsUpdate, _: AuthenticatedUser = Depends(require_csrf)
) -> RiskSettings:
    if runtime.engine.running:
        raise HTTPException(status_code=409, detail="자동매매를 중지한 뒤 한도를 변경하세요.")
    try:
        return runtime.broker.risk_manager.update(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/paper/reset", response_model=Account)
@runtime._serialize_paper_change
async def reset_paper_practice(_: AuthenticatedUser = Depends(require_csrf)) -> Account:
    await runtime.engine.stop()
    account = runtime.broker.reset_practice()
    runtime.engine.reset_performance_baseline()
    return account


@router.post("/orders", response_model=Order)
def create_order(
    request: OrderRequest, _: AuthenticatedUser = Depends(require_csrf)
) -> Order:
    if not runtime.market.has_symbol(request.symbol):
        raise HTTPException(status_code=404, detail="감시 목록에 없는 종목입니다.")
    try:
        return runtime.broker.submit(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/strategy/status", response_model=StrategyStatus)
def strategy_status(_: AuthenticatedUser = Depends(require_user)) -> StrategyStatus:
    return runtime.engine.status()


@router.put("/strategy/settings", response_model=StrategyStatus)
def strategy_settings(
    payload: StrategySettingsUpdate, _: AuthenticatedUser = Depends(require_csrf)
) -> StrategyStatus:
    try:
        runtime.engine.configure(interval_seconds=payload.interval_seconds,
                         short_period=payload.short_period,
                         long_period=payload.long_period,
                         order_quantity=payload.order_quantity)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return runtime.engine.status()


@router.post("/strategy/start", response_model=StrategyStatus)
@runtime._serialize_paper_change
async def strategy_start(_: AuthenticatedUser = Depends(require_csrf)) -> StrategyStatus:
    if runtime.paper_state().snapshot_at is None:
        raise HTTPException(status_code=409, detail="먼저 현재 실제 자산을 모의계좌에 복사하세요.")
    if runtime.paper_state().selected_strategy is None:
        raise HTTPException(status_code=409, detail="검증할 저장 전략을 먼저 선택하세요.")
    await runtime.engine.start()
    return runtime.engine.status()


@router.post("/strategy/stop", response_model=StrategyStatus)
@runtime._serialize_paper_change
async def strategy_stop(_: AuthenticatedUser = Depends(require_csrf)) -> StrategyStatus:
    await runtime.engine.stop()
    return runtime.engine.status()


@router.post("/strategy/emergency-stop", response_model=StrategyStatus)
@runtime._serialize_paper_change
async def strategy_emergency_stop(_: AuthenticatedUser = Depends(require_csrf)) -> StrategyStatus:
    await runtime.engine.stop(emergency=True)
    return runtime.engine.status()
