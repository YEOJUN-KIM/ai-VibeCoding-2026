"""사용자·증권계좌별 LIVE DRY RUN 주문 기록 저장소."""

from psycopg.types.json import Jsonb

from .database import connect
from .models import (LiveDryRunConfirmRequest, LiveDryRunOrder, LiveOrderPreview,
                     LiveRealOrderConfirmRequest)


def _order_from_row(row: dict) -> LiveDryRunOrder:
    return LiveDryRunOrder(**row)


def save_dry_run_order(
    user_id: int,
    account: dict,
    account_label: str,
    payload: LiveDryRunConfirmRequest,
    preview: LiveOrderPreview,
) -> tuple[LiveDryRunOrder, bool]:
    account_ref = str(account["accountSeq"])
    snapshot = {
        "approved": preview.approved,
        "checks": [check.model_dump(mode="json") for check in preview.checks],
        "message": preview.message,
    }
    with connect() as conn:
        broker_account = conn.execute(
            """INSERT INTO broker_accounts(user_id,broker,external_account_ref,account_label)
               VALUES (%s,'TOSS',%s,%s)
               ON CONFLICT(user_id,broker,external_account_ref) DO UPDATE
               SET account_label=excluded.account_label,updated_at=now()
               RETURNING id""",
            (user_id, account_ref, account_label),
        ).fetchone()
        existing = conn.execute(
            """SELECT lo.*,ba.account_label FROM live_orders lo
               JOIN broker_accounts ba ON ba.id=lo.broker_account_id
               WHERE lo.user_id=%s AND lo.client_order_id=%s""",
            (user_id, payload.client_order_id),
        ).fetchone()
        if existing:
            comparable = (
                existing["symbol"], existing["side"], existing["mode"], existing["order_type"],
                existing["quantity"], existing["order_price"], existing["trigger_price"], existing["expire_date"],
            )
            requested = (
                preview.symbol, payload.side.value, payload.mode, payload.order_type,
                payload.quantity, payload.order_price, payload.trigger_price, payload.expire_date,
            )
            if comparable != requested:
                raise ValueError("같은 clientOrderId를 다른 주문에 사용할 수 없습니다.")
            return _order_from_row(existing), False
        row = conn.execute(
            """INSERT INTO live_orders(
                   user_id,broker_account_id,client_order_id,symbol,stock_name,side,mode,order_type,
                   quantity,order_price,trigger_price,expire_date,reference_price,estimated_amount,
                   status,dry_run,validation_snapshot)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DRY_RUN_CONFIRMED',true,%s)
               RETURNING *,%s AS account_label""",
            (user_id, broker_account["id"], payload.client_order_id, preview.symbol, preview.name,
             payload.side.value, payload.mode, payload.order_type, payload.quantity, payload.order_price,
             payload.trigger_price, payload.expire_date, preview.reference_price, preview.estimated_amount,
             Jsonb(snapshot), account_label),
        ).fetchone()
        conn.execute(
            """INSERT INTO live_order_events(live_order_id,event_type,message,details)
               VALUES (%s,'DRY_RUN_CONFIRMED',%s,%s)""",
            (row["id"], "DRY RUN 주문을 최종 확인해 저장했습니다.", Jsonb(snapshot)),
        )
        return _order_from_row(row), True


def list_dry_run_orders(user_id: int, limit: int = 20) -> list[LiveDryRunOrder]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT lo.*,ba.account_label FROM live_orders lo
               JOIN broker_accounts ba ON ba.id=lo.broker_account_id
               WHERE lo.user_id=%s AND lo.dry_run=true
               ORDER BY lo.created_at DESC LIMIT %s""",
            (user_id, limit),
        ).fetchall()
    return [_order_from_row(row) for row in rows]


def prepare_real_order(
    user_id: int, account: dict, account_label: str,
    payload: LiveRealOrderConfirmRequest, preview: LiveOrderPreview,
    *, order_source: str = "MANUAL",
) -> tuple[LiveDryRunOrder, bool]:
    if order_source not in {"MANUAL", "AUTO"}:
        raise ValueError("실제 주문 출처는 MANUAL 또는 AUTO여야 합니다.")
    account_ref = str(account["accountSeq"])
    snapshot = {
        "approved": preview.approved,
        "checks": [check.model_dump(mode="json") for check in preview.checks],
        "message": preview.message,
        "confirmation": payload.confirmation,
        "accept_financial_warnings": payload.accept_financial_warnings,
    }
    with connect() as conn:
        broker_account = conn.execute(
            """INSERT INTO broker_accounts(user_id,broker,external_account_ref,account_label)
               VALUES (%s,'TOSS',%s,%s)
               ON CONFLICT(user_id,broker,external_account_ref) DO UPDATE
               SET account_label=excluded.account_label,updated_at=now()
               RETURNING id""",
            (user_id, account_ref, account_label),
        ).fetchone()
        existing = conn.execute(
            """SELECT lo.*,ba.account_label FROM live_orders lo
               JOIN broker_accounts ba ON ba.id=lo.broker_account_id
               WHERE lo.user_id=%s AND lo.client_order_id=%s""",
            (user_id, payload.client_order_id),
        ).fetchone()
        if existing:
            return _order_from_row(existing), False
        row = conn.execute(
            """INSERT INTO live_orders(
                   user_id,broker_account_id,client_order_id,symbol,stock_name,side,mode,order_type,
                   quantity,order_price,trigger_price,expire_date,reference_price,estimated_amount,
                   status,dry_run,order_source,validation_snapshot)
               VALUES (%s,%s,%s,%s,%s,%s,'STANDARD','LIMIT',%s,%s,NULL,NULL,%s,%s,
                       'SUBMITTING',false,%s,%s)
               RETURNING *,%s AS account_label""",
            (user_id, broker_account["id"], payload.client_order_id, preview.symbol, preview.name,
             payload.side.value, payload.quantity, payload.order_price, preview.reference_price,
             preview.estimated_amount, order_source, Jsonb(snapshot), account_label),
        ).fetchone()
        conn.execute(
            """INSERT INTO live_order_events(live_order_id,event_type,message,details)
               VALUES (%s,'SUBMITTING','실제 주문 전송을 시작했습니다.',%s)""",
            (row["id"], Jsonb({**snapshot, "orderSource": order_source})),
        )
        return _order_from_row(row), True


def update_real_order(
    user_id: int, order_id: int, *, status: str, external_order_id: str | None = None,
    broker_status: str | None = None, message: str, details: dict | None = None,
    reconciliation_status: str = "MATCHED",
) -> LiveDryRunOrder:
    snapshot = details or {}
    execution = snapshot.get("execution") if isinstance(snapshot, dict) else {}
    if not isinstance(execution, dict):
        execution = {}
    with connect() as conn:
        row = conn.execute(
            """UPDATE live_orders SET status=%s,
                   external_order_id=COALESCE(%s,external_order_id),
                   broker_status=COALESCE(%s,broker_status),broker_snapshot=%s,
                   filled_quantity=COALESCE(%s,filled_quantity),
                   average_filled_price=COALESCE(%s,average_filled_price),
                   filled_amount=COALESCE(%s,filled_amount),
                   commission=COALESCE(%s,commission),tax=COALESCE(%s,tax),
                   reconciliation_status=%s,last_synced_at=now(),updated_at=now()
               WHERE id=%s AND user_id=%s AND dry_run=false
               RETURNING *,(
                   SELECT account_label FROM broker_accounts WHERE id=broker_account_id
               ) AS account_label""",
            (status, external_order_id, broker_status, Jsonb(snapshot),
             execution.get("filledQuantity"), execution.get("averageFilledPrice"),
             execution.get("filledAmount"), execution.get("commission"), execution.get("tax"),
             reconciliation_status, order_id, user_id),
        ).fetchone()
        if not row:
            raise LookupError("실제 주문 기록을 찾지 못했습니다.")
        conn.execute(
            """INSERT INTO live_order_events(live_order_id,event_type,message,details)
               VALUES (%s,%s,%s,%s)""",
            (order_id, status, message, Jsonb(details or {})),
        )
        return _order_from_row(row)


def list_real_orders(user_id: int, limit: int = 20) -> list[LiveDryRunOrder]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT lo.*,ba.account_label FROM live_orders lo
               JOIN broker_accounts ba ON ba.id=lo.broker_account_id
               WHERE lo.user_id=%s AND lo.dry_run=false
               ORDER BY lo.created_at DESC LIMIT %s""",
            (user_id, limit),
        ).fetchall()
    return [_order_from_row(row) for row in rows]


def orders_for_reconciliation(account_ref: str, *, active_only: bool = False, user_id: int | None = None) -> list[LiveDryRunOrder]:
    active = ("SUBMITTING", "SUBMITTED", "PENDING", "PARTIAL_FILLED", "PENDING_CANCEL", "UNKNOWN")
    with connect() as conn:
        rows = conn.execute(
            """SELECT lo.*,ba.account_label FROM live_orders lo
               JOIN broker_accounts ba ON ba.id=lo.broker_account_id
               WHERE lo.dry_run=false AND ba.external_account_ref=%s
                 AND (%s=false OR lo.status=ANY(%s))
                 AND (%s::bigint IS NULL OR lo.user_id=%s)
               ORDER BY lo.created_at DESC LIMIT 100""",
            (account_ref, active_only, list(active), user_id, user_id),
        ).fetchall()
    return [_order_from_row(row) for row in rows]


def auto_position_quantities(user_id: int) -> dict[str, object]:
    """AUTO 실제 주문의 순체결 수량을 종목별로 계산한다."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT symbol,
                      SUM(CASE WHEN side='BUY' THEN filled_quantity ELSE -filled_quantity END) AS quantity
               FROM live_orders
               WHERE user_id=%s AND dry_run=false AND order_source='AUTO'
               GROUP BY symbol""",
            (user_id,),
        ).fetchall()
    return {str(row["symbol"]): row["quantity"] for row in rows}


def real_order_for_user(user_id: int, order_id: int) -> LiveDryRunOrder:
    with connect() as conn:
        row = conn.execute(
            """SELECT lo.*,ba.account_label FROM live_orders lo
               JOIN broker_accounts ba ON ba.id=lo.broker_account_id
               WHERE lo.id=%s AND lo.user_id=%s AND lo.dry_run=false""",
            (order_id, user_id),
        ).fetchone()
    if not row:
        raise LookupError("실제 주문 기록을 찾지 못했습니다.")
    return _order_from_row(row)


def cancel_dry_run_order(user_id: int, order_id: int) -> LiveDryRunOrder:
    with connect() as conn:
        row = conn.execute(
            """UPDATE live_orders SET status='CANCELLED'
               WHERE id=%s AND user_id=%s AND dry_run=true AND status='DRY_RUN_CONFIRMED'
               RETURNING *,(
                   SELECT account_label FROM broker_accounts WHERE id=broker_account_id
               ) AS account_label""",
            (order_id, user_id),
        ).fetchone()
        if not row:
            existing = conn.execute(
                """SELECT lo.*,ba.account_label FROM live_orders lo
                   JOIN broker_accounts ba ON ba.id=lo.broker_account_id
                   WHERE lo.id=%s AND lo.user_id=%s AND lo.dry_run=true""",
                (order_id, user_id),
            ).fetchone()
            if not existing:
                raise LookupError("DRY RUN 주문을 찾지 못했습니다.")
            return _order_from_row(existing)
        conn.execute(
            """INSERT INTO live_order_events(live_order_id,event_type,message)
               VALUES (%s,'CANCELLED','사용자가 DRY RUN 주문을 취소했습니다.')""",
            (order_id,),
        )
        return _order_from_row(row)


def delete_today_dry_run_orders(user_id: int) -> int:
    with connect() as conn:
        rows = conn.execute(
            """DELETE FROM live_orders
               WHERE user_id=%s AND dry_run=true AND created_at >= CURRENT_DATE
                 AND created_at < CURRENT_DATE + INTERVAL '1 day'
               RETURNING id""",
            (user_id,),
        ).fetchall()
    return len(rows)
