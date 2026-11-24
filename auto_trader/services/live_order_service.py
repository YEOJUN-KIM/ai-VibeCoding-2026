"""services / live_order_service"""

from datetime import date, timedelta, timezone
from decimal import Decimal
from fastapi import HTTPException, Request
from ..auth import AuthenticatedUser, live_pin_status
from ..models import LiveDryRunConfirmRequest, LiveDryRunOrder, LiveOrderImpactItem, LiveOrderPreview, LiveOrderPreviewCheck, LiveOrderPreviewRequest, LiveRealOrderConfirmRequest
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
    *, relax_financial_limits: bool = False,  # retained for call compatibility; no financial-limit checks remain
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
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    checks: list[LiveOrderPreviewCheck] = []

    def add_check(name: str, passed: bool, success: str, failure: str) -> None:
        checks.append(LiveOrderPreviewCheck(name=name, passed=passed, message=success if passed else failure))

    add_check("LIVE PIN", True, "LIVE PIN 인증이 유효합니다.", "LIVE PIN 인증이 필요합니다.")
    # No investment-policy warnings (per-order / per-symbol / total / min-cash) are produced here.
    # Hard safety checks below (cash, holdings) stay; the user decides the rest from the neutral impact view.
    holding = next((item for item in portfolio.holdings if item.symbol.upper() == payload.symbol.upper()), None)
    held_quantity = holding.quantity if holding else Decimal(0)
    cash_before = buying_power.krw_cash_buying_power
    total_asset = portfolio.market_value + cash_before
    impact: list[LiveOrderImpactItem] = []
    if payload.side.value == "BUY":
        add_check("주문 가능 금액", estimated_amount <= cash_before,
                  "원화 주문 가능 금액 이내입니다.", "원화 주문 가능 금액이 부족합니다.")
        cash_after = cash_before - estimated_amount
        quantity_after = held_quantity + payload.quantity
        symbol_value_before = holding.market_value if holding else Decimal(0)
        symbol_value_after = symbol_value_before + estimated_amount
        # A buy converts cash to stock at the reference price, so total assets are unchanged by the estimate.
        total_after = total_asset
    else:
        add_check("보유 수량", payload.quantity <= held_quantity,
                  "현재 보유 수량 이내입니다.", "매도할 보유 수량이 부족합니다.")
        cash_after = cash_before + estimated_amount
        quantity_after = held_quantity - payload.quantity
        symbol_value_before = holding.market_value if holding else Decimal(0)
        symbol_value_after = max(symbol_value_before - estimated_amount, Decimal(0))
        total_after = total_asset
    impact.append(LiveOrderImpactItem(label="원화 주문 가능 금액", before=cash_before, after=cash_after, unit="원"))
    impact.append(LiveOrderImpactItem(label="보유 수량", before=held_quantity, after=quantity_after, unit="주"))
    if total_asset:
        impact.append(LiveOrderImpactItem(
            label="종목 비중(주문가 기준 추정)",
            before=RiskPolicy.cash_ratio(symbol_value_before, total_asset),
            after=RiskPolicy.cash_ratio(symbol_value_after, total_after), unit="%"))
        impact.append(LiveOrderImpactItem(
            label="현금 비중(주문가 기준 추정)",
            before=RiskPolicy.cash_ratio(cash_before, total_asset),
            after=RiskPolicy.cash_ratio(cash_after, total_after), unit="%"))
    if payload.side.value == "SELL" and holding is not None:
        impact.append(LiveOrderImpactItem(
            label="예상 매도 손익(평균단가 기준, 수수료·세금 제외)",
            after=(reference_price - holding.average_purchase_price) * payload.quantity, unit="원"))
    impact.append(LiveOrderImpactItem(
        label="수수료·세금", unit="원",
        note="주문 전에는 확정할 수 없습니다. 체결 후 토스증권 내역에서 확인하세요."))
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
        impact=impact,
        message=("필수 사전 검사를 통과했습니다. 아래 예상 변화를 확인하세요. 실제 주문은 전송되지 않았습니다."
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
