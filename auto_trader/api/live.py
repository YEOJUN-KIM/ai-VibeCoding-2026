"""api / live"""

import logging
from decimal import Decimal
from fastapi import Depends, HTTPException, Request, Response
from ..auth import AuthenticatedUser, require_csrf, require_user
from ..models import DeletedOrderCount, FavoriteStockCreate, LiveBuyingPower, LiveDryRunConfirmRequest, LiveDryRunOrder, LiveFavoriteStock, LiveOrderPreview, LiveOrderPreviewRequest, LivePortfolio, LiveRealOrderConfirmRequest, LiveTradingReadiness, TossConnectionStatus
from ..settings import settings
from ..toss import TossApiError
from ..asset_history import record_and_read as record_asset_history
from ..favorites import add_favorite, list_favorites, remove_favorite
from ..live_orders import auto_position_quantities, cancel_dry_run_order, delete_today_dry_run_orders, list_dry_run_orders, list_real_orders
from fastapi import APIRouter
from ..services import live_order_service
from .. import runtime
from ..services.live_order_service import _preview_live_order, reconcile_saved_real_orders
router = APIRouter()


@router.post("/toss/test-connection", response_model=TossConnectionStatus)
def test_toss_connection(_: AuthenticatedUser = Depends(require_csrf)) -> TossConnectionStatus:
    if not runtime.toss_client.configured:
        return TossConnectionStatus(configured=False, connected=False,
                                    message="Client ID와 Client Secret을 먼저 설정하세요.")
    try:
        result = runtime.toss_client.test_connection()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TossConnectionStatus(configured=True, connected=result.connected,
                                account_count=result.account_count, message=result.message)


@router.get("/live/orders/real/readiness", response_model=LiveTradingReadiness)
def live_trading_readiness(_: AuthenticatedUser = Depends(require_user)) -> LiveTradingReadiness:
    enabled = settings.live_trading_enabled
    configured = runtime.toss_client.configured
    if not configured:
        message = "토스 API 인증 정보를 먼저 설정해야 합니다."
    elif not enabled:
        message = "실제 주문 연결부는 준비됐지만 안전 잠금으로 비활성화되어 있습니다."
    else:
        message = "실제 주문 전송이 활성화되어 있습니다. 주문 전 최종 확인이 필요합니다."
    return LiveTradingReadiness(
        configured=configured,
        enabled=enabled,
        supported_order="국내 주식 · 정수 수량 · DAY 지정가",
        message=message,
    )


@router.get("/live/portfolio", response_model=LivePortfolio)
def live_portfolio(user: AuthenticatedUser = Depends(require_user)) -> LivePortfolio:
    try:
        portfolio = runtime.toss_client.portfolio()
        auto_positions = auto_position_quantities(user.id)
        holdings = []
        for holding in portfolio.holdings:
            auto_quantity = max(Decimal(0), min(
                holding.quantity, Decimal(str(auto_positions.get(holding.symbol, 0)))
            ))
            holdings.append(holding.model_copy(update={
                "auto_managed_quantity": auto_quantity,
                "existing_quantity": holding.quantity - auto_quantity,
            }))
        return portfolio.model_copy(update={"holdings": holdings})
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/live/buying-power", response_model=LiveBuyingPower)
def live_buying_power(_: AuthenticatedUser = Depends(require_user)) -> LiveBuyingPower:
    try:
        return runtime.toss_client.buying_power()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/live/assets")
def live_assets(user: AuthenticatedUser = Depends(require_user)):
    portfolio = live_portfolio(user)
    try:
        buying_power = runtime.toss_client.buying_power()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        history = record_asset_history(user.id, portfolio, buying_power)
        history_error = None
    except Exception:
        logging.exception("Live asset history could not be saved")
        history, history_error = [], "자산 이력을 저장하지 못했습니다. 잠시 후 다시 조회하세요."
    return {"portfolio": portfolio, "buying_power": buying_power,
            "history": history, "history_error": history_error}


@router.post("/live/orders/preview", response_model=LiveOrderPreview)
def preview_live_order(
    payload: LiveOrderPreviewRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveOrderPreview:
    return _preview_live_order(
        payload, request, user,
        relax_financial_limits=False,
    )


@router.get("/live/orders/real", response_model=list[LiveDryRunOrder])
def live_real_orders(user: AuthenticatedUser = Depends(require_user)) -> list[LiveDryRunOrder]:
    try:
        reconcile_saved_real_orders(active_only=True)
    except (TossApiError, LookupError, ValueError, ArithmeticError):
        pass
    return list_real_orders(user.id)


@router.get("/live/orders/dry-run", response_model=list[LiveDryRunOrder])
def live_dry_run_orders(user: AuthenticatedUser = Depends(require_user)) -> list[LiveDryRunOrder]:
    return list_dry_run_orders(user.id)


@router.post("/live/orders/dry-run/{order_id}/cancel", response_model=LiveDryRunOrder)
def cancel_live_dry_run_order(
    order_id: int, user: AuthenticatedUser = Depends(require_csrf)
) -> LiveDryRunOrder:
    try:
        return cancel_dry_run_order(user.id, order_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/live/orders/dry-run/today", response_model=DeletedOrderCount)
def delete_live_dry_run_orders_today(
    user: AuthenticatedUser = Depends(require_csrf),
) -> DeletedOrderCount:
    return DeletedOrderCount(deleted=delete_today_dry_run_orders(user.id))


@router.get("/live/favorites", response_model=list[LiveFavoriteStock])
def live_favorites(user: AuthenticatedUser = Depends(require_user)) -> list[LiveFavoriteStock]:
    try:
        return runtime.toss_client.favorite_stock_snapshots(list_favorites(user.id))
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/live/favorites", response_model=LiveFavoriteStock, status_code=201)
def create_live_favorite(
    payload: FavoriteStockCreate,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveFavoriteStock:
    try:
        stock = runtime.toss_client.domestic_stock(payload.symbol)
        if not stock:
            raise HTTPException(status_code=404, detail="국내 상장 종목을 찾지 못했습니다.")
        saved = add_favorite(user.id, stock)
        return LiveFavoriteStock(**saved)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/live/favorites/{symbol}", status_code=204)
def delete_live_favorite(
    symbol: str,
    user: AuthenticatedUser = Depends(require_csrf),
) -> Response:
    remove_favorite(user.id, symbol)
    return Response(status_code=204)


@router.post("/live/orders/dry-run", response_model=LiveDryRunOrder, status_code=201)
def confirm_live_dry_run_order(
    payload: LiveDryRunConfirmRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveDryRunOrder:
    return live_order_service.confirm_live_dry_run_order(payload, request, user)


@router.post("/live/orders/real", response_model=LiveDryRunOrder, status_code=201)
def submit_live_real_order(
    payload: LiveRealOrderConfirmRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveDryRunOrder:
    return live_order_service.submit_live_real_order(payload, request, user)


@router.post("/live/orders/real/{order_id}/cancel", response_model=LiveDryRunOrder)
def cancel_live_real_order(
    order_id: int, _: Request, user: AuthenticatedUser = Depends(require_csrf),
) -> LiveDryRunOrder:
    return live_order_service.cancel_live_real_order(order_id, _, user)
