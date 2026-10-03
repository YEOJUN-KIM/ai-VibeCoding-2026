"""Read saved PAPER decisions within the authenticated account owner."""
from datetime import date, datetime, time, timedelta, timezone

from .database import connect
from .ml.decision_pipeline import VALID_ACTIONS

KST = timezone(timedelta(hours=9))


def read_decision_history(user_id, *, account_id=None, strategy_id=None, symbol="",
                          action="", start=None, end=None, page=1, page_size=20, before_id=None):
    if start and end and start > end:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    if end == date.max:
        raise ValueError("종료일은 9999-12-30 이전으로 입력하세요.")
    if action and action not in VALID_ACTIONS:
        raise ValueError("지원하지 않는 판단 결과입니다.")
    if page < 1 or page_size not in (20, 50, 100):
        raise ValueError("페이지와 표시 개수를 확인하세요.")
    where = ["a.user_id = %s", "a.mode = 'PAPER'", "d.id <= %s"]
    values = [user_id]
    with connect() as conn:
        # Count and rows share a snapshot; new decisions cannot move later pages.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        ceiling = conn.execute("""SELECT COALESCE(MAX(d.id),0) AS id
            FROM ml_strategy_decisions d JOIN accounts a ON a.id=d.account_id
            WHERE a.user_id=%s AND a.mode='PAPER'""", (user_id,)).fetchone()["id"]
        ceiling = min(ceiling, before_id) if before_id is not None else ceiling
        values.append(ceiling)
        for field, value in (("account_id", account_id), ("strategy_id", strategy_id)):
            if value is not None:
                where.append(f"d.{field} = %s")
                values.append(value)
        if symbol.strip():
            # Literal substring: SQL wildcards in user input are escaped.
            query = symbol.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("(d.symbol ILIKE %s OR s.name ILIKE %s)")
            values.extend([f"%{query}%"] * 2)
        if action:
            where.append("d.action = %s")
            values.append(action)
        if start:
            where.append("d.decision_at >= %s")
            values.append(datetime.combine(start, time.min, KST))
        if end:
            where.append("d.decision_at < %s")
            values.append(datetime.combine(end + timedelta(days=1), time.min, KST))
        source = "FROM ml_strategy_decisions d JOIN accounts a ON a.id=d.account_id LEFT JOIN stocks s ON s.symbol=d.symbol WHERE " + " AND ".join(where)
        total = conn.execute("SELECT COUNT(*) AS total " + source, values).fetchone()["total"]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = conn.execute("""SELECT d.id,d.account_id,a.name AS account_name,d.strategy_id,
            d.strategy_name,d.symbol,COALESCE(s.name,d.symbol) AS stock_name,d.decision_at,
            d.action,d.reason,d.price,d.short_average,d.long_average,d.data_source,
            d.candle_event_at,d.history_count,d.confirmation_count,d.strategy_quantity
            """ + source + " ORDER BY d.decision_at DESC,d.id DESC LIMIT %s OFFSET %s",
            [*values, page_size, (page - 1) * page_size]).fetchall()
        accounts = conn.execute("SELECT id,name FROM accounts WHERE user_id=%s AND mode='PAPER' ORDER BY id", (user_id,)).fetchall()
        strategies = conn.execute("""SELECT DISTINCT d.strategy_id AS id,d.strategy_name AS name
            FROM ml_strategy_decisions d JOIN accounts a ON a.id=d.account_id
            WHERE a.user_id=%s AND a.mode='PAPER' AND d.strategy_id IS NOT NULL
            ORDER BY name,id""", (user_id,)).fetchall()
    def label(name):
        return "실험계좌" if name == "paper-experiment" or name.endswith("-paper-experiment") else "기존 모의계좌"
    for row in rows:
        row["account_label"] = label(row.pop("account_name"))
    return dict(items=rows, total=total, page=page, pages=pages, page_size=page_size,
                before_id=ceiling, accounts=[dict(id=a["id"],name=label(a["name"])) for a in accounts],
                strategies=strategies)
