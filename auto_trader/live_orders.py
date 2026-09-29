"""사용자·증권계좌별 LIVE DRY RUN 주문 기록 저장소."""

from psycopg.types.json import Jsonb

from .database import connect
from .models import LiveDryRunConfirmRequest, LiveDryRunOrder, LiveOrderPreview


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
