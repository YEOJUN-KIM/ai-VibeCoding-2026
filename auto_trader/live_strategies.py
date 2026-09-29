"""사용자별 LIVE 자동매매 전략 설정 저장소."""

from .database import connect
from .models import LiveStrategy, LiveStrategyWrite
from psycopg.errors import UniqueViolation


def _strategy(row: dict) -> LiveStrategy:
    values = dict(row)
    for key in ("trading_start", "trading_end"):
        value = values.get(key)
        if hasattr(value, "strftime"):
            values[key] = value.strftime("%H:%M")
    return LiveStrategy(**values)


def list_strategies(user_id: int) -> list[LiveStrategy]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM live_strategies WHERE user_id=%s ORDER BY updated_at DESC,id DESC",
            (user_id,),
        ).fetchall()
    return [_strategy(row) for row in rows]


def save_strategy(user_id: int, payload: LiveStrategyWrite, stock_name: str,
                  strategy_id: int | None = None) -> LiveStrategy:
    if payload.short_period >= payload.long_period:
        raise ValueError("단기 이동평균 기간은 장기 기간보다 작아야 합니다.")
    if payload.trading_start >= payload.trading_end:
        raise ValueError("운영 종료 시간은 시작 시간보다 늦어야 합니다.")
    values = (
        payload.name.strip(), payload.symbol.upper(), stock_name, payload.enabled,
        payload.execution_mode, payload.short_period, payload.long_period,
        payload.order_quantity, payload.take_profit_rate, payload.stop_loss_rate,
        payload.max_holding_days, payload.trading_start, payload.trading_end,
        payload.daily_order_limit, payload.cooldown_minutes,
    )
    try:
        with connect() as conn:
            if strategy_id is None:
                row = conn.execute(
                """INSERT INTO live_strategies(
                       user_id,name,symbol,stock_name,enabled,execution_mode,short_period,long_period,
                       order_quantity,take_profit_rate,stop_loss_rate,max_holding_days,trading_start,
                       trading_end,daily_order_limit,cooldown_minutes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (user_id, *values),
                ).fetchone()
            else:
                row = conn.execute(
                """UPDATE live_strategies SET name=%s,symbol=%s,stock_name=%s,enabled=%s,
                       execution_mode=%s,short_period=%s,long_period=%s,order_quantity=%s,
                       take_profit_rate=%s,stop_loss_rate=%s,max_holding_days=%s,trading_start=%s,
                       trading_end=%s,daily_order_limit=%s,cooldown_minutes=%s,updated_at=now()
                   WHERE id=%s AND user_id=%s RETURNING *""",
                (*values, strategy_id, user_id),
                ).fetchone()
    except UniqueViolation as exc:
        raise ValueError("같은 이름의 전략이 이미 있습니다.") from exc
    if not row:
        raise LookupError("전략 설정을 찾지 못했습니다.")
    return _strategy(row)


def delete_strategy(user_id: int, strategy_id: int) -> None:
    with connect() as conn:
        row = conn.execute(
            "DELETE FROM live_strategies WHERE id=%s AND user_id=%s RETURNING id",
            (strategy_id, user_id),
        ).fetchone()
    if not row:
        raise LookupError("전략 설정을 찾지 못했습니다.")
