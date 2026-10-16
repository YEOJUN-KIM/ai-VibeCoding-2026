"""services / live_order_service"""

from datetime import date, timedelta, timezone
from decimal import Decimal
from fastapi import HTTPException, Request
from ..auth import AuthenticatedUser, live_pin_status
from ..models import LiveDryRunConfirmRequest, LiveDryRunOrder, LiveOrderPreview, LiveOrderPreviewCheck, LiveOrderPreviewRequest, LiveRealOrderConfirmRequest
from ..settings import settings
from ..toss import TossApiError
from ..live_orders import prepare_real_order, orders_for_reconciliation, real_order_for_user, save_dry_run_order, update_real_order
from .. import runtime
from ..risk_policy import RiskPolicy


def _local_order_status(broker_status: str, fallback: str) -> str:
    supported = {"FILLED", "PARTIAL_FILLED", "PENDING", "PENDING_CANCEL", "CANCELED", "REJECTED"}
    return broker_status if broker_status in supported else fallback


def reconcile_saved_real_orders(*, active_only: bool = False) -> int:
    account = runtime.toss_client.selected_account()
    orders = orders_for_reconciliation(str(account["accountSeq"]), active_only=active_only, user_id=runtime.paper_state().user_id)
    reconciled = 0
    history_cache: dict[str, dict[str, dict]] = {}
    for order in orders:
        if not order.external_order_id:
            if order.reconciliation_status != "NEEDS_REVIEW":
                update_real_order(
                    order.user_id, order.id,
                    status=order.status, message="토스 주문 ID가 없어 자동 대조할 수 없습니다.",
                    details=order.broker_snapshot, reconciliation_status="NEEDS_REVIEW",
                )
            continue
        try:
            order_date = order.created_at.astimezone(timezone(timedelta(hours=9))).date().isoformat()
            if order_date not in history_cache:
                history = runtime.toss_client.orders("OPEN", from_date=order_date, to_date=order_date)
                history += runtime.toss_client.orders("CLOSED", from_date=order_date, to_date=order_date)
                history_cache[order_date] = {
                    str(item.get("orderId")): item for item in history if item.get("orderId")
                }
            detail = history_cache[order_date].get(order.external_order_id)
            if detail is None:
                detail = runtime.toss_client.order_detail(order.external_order_id)
        except TossApiError as exc:
            update_real_order(
                order.user_id, order.id,
                status=order.status, broker_status=order.broker_status,
                message="토스 주문 상태 대조에 실패했습니다.", details={"error": str(exc)},
                reconciliation_status="ERROR",
            )
            continue
        broker_status = str(detail.get("status") or order.broker_status or order.status)
        execution = detail.get("execution") or {}
        filled_quantity = Decimal(str(execution.get("filledQuantity") or 0))
        def optional_decimal(value):
            return None if value is None else Decimal(str(value))
        average_filled_price = optional_decimal(execution.get("averageFilledPrice"))
        filled_amount = optional_decimal(execution.get("filledAmount"))
        commission = optional_decimal(execution.get("commission"))
        tax = optional_decimal(execution.get("tax"))
        status = _local_order_status(broker_status, order.status)
        if (status != order.status or broker_status != order.broker_status
                or filled_quantity != order.filled_quantity
                or average_filled_price != order.average_filled_price
                or filled_amount != order.filled_amount
                or commission != order.commission or tax != order.tax
                or order.reconciliation_status != "MATCHED"):
            update_real_order(
                order.user_id, order.id,
                status=status, broker_status=broker_status,
                message="서버 시작 시 토스 주문 상태와 DB를 대조했습니다.", details=detail,
            )
        reconciled += 1
    return reconciled


def _preview_live_order(
    payload: LiveOrderPreviewRequest,
    request: Request,
    user: AuthenticatedUser,
    *, relax_financial_limits: bool,
) -> LiveOrderPreview:
    _, authorized_until = live_pin_status(request, user)
    if authorized_until is None:
        raise HTTPException(status_code=403, detail="주문을 검토하려면 LIVE PIN 인증이 필요합니다.")
    if payload.mode == "SINGLE" and (payload.trigger_price is None or payload.expire_date is None):
        raise HTTPException(status_code=422, detail="목표가 도달 주문에는 감시 가격과 만료일이 필요합니다.")
    if payload.mode == "SINGLE" and payload.expire_date < date.today():
        raise HTTPException(status_code=422, detail="조건주문 만료일은 오늘 이후여야 합니다.")
    if payload.order_type == "LIMIT" and payload.order_price is None:
        raise HTTPException(status_code=422, detail="지정가 주문에는 주문 가격이 필요합니다.")
    try:
        stock = runtime.toss_client.domestic_stock(payload.symbol)
        if not stock:
            raise HTTPException(status_code=404, detail="국내 상장 종목을 찾지 못했습니다.")
        detail = runtime.toss_client.domestic_stock_detail(payload.symbol, period="1D")
        if detail.price is None:
            raise HTTPException(status_code=409, detail="현재가를 확인할 수 없어 주문을 검토할 수 없습니다.")
        reference_price = payload.order_price if payload.order_type == "LIMIT" else detail.price
        estimated_amount = reference_price * payload.quantity
        portfolio = runtime.toss_client.portfolio()
        buying_power = runtime.toss_client.buying_power()
        config = runtime.risk_manager.settings()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    checks: list[LiveOrderPreviewCheck] = []
    relaxed_financial_limits = relax_financial_limits

    def add_check(name: str, passed: bool, success: str, failure: str) -> None:
        checks.append(LiveOrderPreviewCheck(name=name, passed=passed, message=success if passed else failure))

    def add_financial_check(name: str, passed: bool, success: str, failure: str) -> None:
        if relaxed_financial_limits:
            message = success if passed else f"테스트 모드에서 한도 적용을 생략했습니다: {failure}"
            checks.append(LiveOrderPreviewCheck(name=name, passed=True, message=message))
        else:
            checks.append(LiveOrderPreviewCheck(name=name, passed=passed, warning=not passed, message=success if passed else failure))

    add_check("LIVE PIN", True, "LIVE PIN 인증이 유효합니다.", "LIVE PIN 인증이 필요합니다.")
    if relaxed_financial_limits:
        add_check("DRY RUN 테스트", True, "투자 정책 한도만 적용하지 않는 테스트 모드입니다. 실제 현금과 보유 수량은 검사합니다.", "")
    add_financial_check("주문 금액", RiskPolicy.order_amount_allowed(config, estimated_amount),
                        "1회 주문 한도 이내입니다.", "1회 최대 주문 금액을 초과합니다.")
    holding = next((item for item in portfolio.holdings if item.symbol.upper() == payload.symbol.upper()), None)
    if payload.side.value == "BUY":
        symbol_value = holding.market_value if holding else Decimal(0)
        total_asset = portfolio.market_value + buying_power.krw_cash_buying_power
        remaining_cash = buying_power.krw_cash_buying_power - estimated_amount
        current_cash_ratio = RiskPolicy.cash_ratio(buying_power.krw_cash_buying_power, total_asset)
        cash_ratio = RiskPolicy.cash_ratio(remaining_cash, total_asset)
        add_check("주문 가능 금액", estimated_amount <= buying_power.krw_cash_buying_power,
                  "원화 주문 가능 금액 이내입니다.", "원화 주문 가능 금액이 부족합니다.")
        add_financial_check("종목별 한도", RiskPolicy.symbol_amount_allowed(config, symbol_value, estimated_amount),
                            "종목별 투자 한도 이내입니다.", "종목별 최대 투자 금액을 초과합니다.")
        add_financial_check("전체 투자 한도", RiskPolicy.total_investment_allowed(config, portfolio.market_value, estimated_amount),
                            "전체 투자 한도 이내입니다.", "전체 최대 투자 금액을 초과합니다.")
        add_financial_check("최소 현금", cash_ratio >= config.min_cash_ratio,
                            f"현금 비율 {current_cash_ratio:.1f}% → {cash_ratio:.1f}%로, 기준 {config.min_cash_ratio:.1f}% 이상입니다.",
                            f"현금 비율 {current_cash_ratio:.1f}% → {cash_ratio:.1f}%로, 안전 기준 {config.min_cash_ratio:.1f}%보다 낮아집니다.")
    else:
        available_quantity = holding.quantity if holding else Decimal(0)
        add_check("보유 수량", payload.quantity <= available_quantity,
                  "현재 보유 수량 이내입니다.", "매도할 보유 수량이 부족합니다.")
    approved = all(check.passed or check.warning for check in checks)
    return LiveOrderPreview(
        approved=approved,
        symbol=payload.symbol.upper(),
        name=str(stock.get("name", payload.symbol)),
        side=payload.side,
        mode=payload.mode,
        order_type=payload.order_type,
        quantity=payload.quantity,
        reference_price=reference_price,
        estimated_amount=estimated_amount,
        checks=checks,
        message=("투자 한도 초과 경고가 있습니다. 확인 후 계속할 수 있습니다. 실제 주문은 전송되지 않았습니다."
                 if approved and any(check.warning for check in checks) else "모든 사전 검사를 통과했습니다. 실제 주문은 전송되지 않았습니다."
                 if approved else "통과하지 못한 항목이 있습니다. 실제 주문은 전송되지 않았습니다."),
    )


def confirm_live_dry_run_order(
    payload: LiveDryRunConfirmRequest,
    request: Request,
    user: AuthenticatedUser,
) -> LiveDryRunOrder:
    preview = _preview_live_order(
        payload, request, user,
        relax_financial_limits=settings.live_dry_run_ignore_financial_limits,
    )
    if not preview.approved:
        failed = next((check.message for check in preview.checks if not check.passed), preview.message)
        raise HTTPException(status_code=409, detail=f"주문 검토를 통과하지 못했습니다: {failed}")
    try:
        account = runtime.toss_client.selected_account()
        account_label = runtime.toss_client.buying_power().account_label
        order, _ = save_dry_run_order(user.id, account, account_label, payload, preview)
        return order
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def submit_live_real_order(
    payload: LiveRealOrderConfirmRequest,
    request: Request,
    user: AuthenticatedUser,
) -> LiveDryRunOrder:
    if not settings.live_trading_enabled:
        raise HTTPException(status_code=423, detail="실제 주문 안전 잠금이 켜져 있습니다.")
    if payload.mode != "STANDARD" or payload.order_type != "LIMIT":
        raise HTTPException(status_code=422, detail="첫 실제 주문은 일반 DAY 지정가만 지원합니다.")
    if payload.quantity != payload.quantity.to_integral_value():
        raise HTTPException(status_code=422, detail="첫 실제 주문은 정수 수량만 지원합니다.")
    preview = _preview_live_order(payload, request, user, relax_financial_limits=False)
    if not preview.approved:
        failed = next((check.message for check in preview.checks if not check.passed), preview.message)
        raise HTTPException(status_code=409, detail=f"실제 주문 검토를 통과하지 못했습니다: {failed}")
    if any(check.warning for check in preview.checks) and not payload.accept_financial_warnings:
        raise HTTPException(status_code=409, detail="투자 한도 초과 경고를 확인하고 계속 주문에 동의해야 합니다.")
    try:
        account = runtime.toss_client.selected_account()
        account_label = runtime.toss_client.buying_power().account_label
        order, created = prepare_real_order(user.id, account, account_label, payload, preview)
        if not created:
            return order
        try:
            submitted = runtime.toss_client.create_limit_order(
                symbol=preview.symbol, side=payload.side.value, quantity=payload.quantity,
                price=payload.order_price, client_order_id=payload.client_order_id,
            )
        except TossApiError as exc:
            update_real_order(
                user.id, order.id, status="UNKNOWN", message="실제 주문 전송 결과를 확정하지 못했습니다.",
                details={"error": str(exc)}, reconciliation_status="NEEDS_REVIEW",
            )
            raise HTTPException(
                status_code=502,
                detail="주문 결과를 확정하지 못했습니다. 자동 재전송하지 말고 토스 주문 내역을 확인하세요.",
            ) from exc
        external_order_id = str(submitted["orderId"])
        try:
            detail = runtime.toss_client.order_detail(external_order_id)
            broker_status = str(detail.get("status") or "SUBMITTED")
            status = broker_status if broker_status in {
                "FILLED", "PARTIAL_FILLED", "PENDING", "PENDING_CANCEL", "CANCELED", "REJECTED"
            } else "SUBMITTED"
        except TossApiError:
            detail = submitted
            broker_status = "SUBMITTED"
            status = "SUBMITTED"
        return update_real_order(
            user.id, order.id, status=status, external_order_id=external_order_id,
            broker_status=broker_status, message="토스증권이 실제 주문을 접수했습니다.", details=detail,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def cancel_live_real_order(
    order_id: int, _: Request, user: AuthenticatedUser,
) -> LiveDryRunOrder:
    if not settings.live_trading_enabled:
        raise HTTPException(status_code=423, detail="실제 주문 안전 잠금이 켜져 있습니다.")
    try:
        order = real_order_for_user(user.id, order_id)
        if not order.external_order_id:
            raise HTTPException(status_code=409, detail="토스 주문 ID가 없어 취소할 수 없습니다.")
        result = runtime.toss_client.cancel_order(order.external_order_id)
        try:
            detail = runtime.toss_client.order_detail(order.external_order_id)
            broker_status = str(detail.get("status") or "PENDING_CANCEL")
        except TossApiError:
            detail = result
            broker_status = "PENDING_CANCEL"
        status = broker_status if broker_status in {"CANCELED", "PENDING_CANCEL"} else "PENDING_CANCEL"
        return update_real_order(
            user.id, order.id, status=status, broker_status=broker_status,
            message="토스증권에 실제 주문 취소를 요청했습니다.", details=detail,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
