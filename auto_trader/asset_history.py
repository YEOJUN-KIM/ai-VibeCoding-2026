"""Successful account observations, isolated by user and broker account label."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .database import connect


def record_and_read(user_id, portfolio, buying_power, *, now=None):
    now = now or datetime.now(timezone.utc)
    minute = now.replace(second=0, microsecond=0)
    market = portfolio.market_value
    cash = buying_power.krw_cash_buying_power
    if portfolio.account_label != buying_power.account_label:
        raise ValueError("계좌 정보가 일치하지 않습니다. 다시 조회하세요.")
    if not all(Decimal(value).is_finite() for value in (market, cash)):
        raise ValueError("자산 금액이 올바르지 않습니다.")
    with connect() as conn:
        conn.execute(
            """INSERT INTO live_asset_history
               (user_id,account_label,observed_minute,observed_at,market_value,cash,total_assets)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (user_id,account_label,observed_minute) DO UPDATE SET
               observed_at=EXCLUDED.observed_at,market_value=EXCLUDED.market_value,
               cash=EXCLUDED.cash,total_assets=EXCLUDED.total_assets""",
            (user_id, portfolio.account_label, minute, now, market, cash, market + cash),
        )
        # Daily closing observations keep a year of charts small without fabricating gaps.
        rows = conn.execute(
            """SELECT DISTINCT ON ((observed_at AT TIME ZONE 'Asia/Seoul')::date)
               observed_at,total_assets FROM live_asset_history
               WHERE user_id=%s AND account_label=%s AND observed_at>=%s
               ORDER BY (observed_at AT TIME ZONE 'Asia/Seoul')::date,observed_at DESC""",
            (user_id, portfolio.account_label, now - timedelta(days=366)),
        ).fetchall()
    return [{"at": row["observed_at"], "total_assets": row["total_assets"]} for row in rows]
