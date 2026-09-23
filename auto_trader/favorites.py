"""사용자별 국내 관심 종목 영구 저장."""

from .database import connect


MAX_FAVORITES = 20


def list_favorites(user_id: int) -> list[dict]:
    with connect() as conn:
        return list(conn.execute(
            """SELECT symbol,name,market,security_type,is_common_share,created_at
               FROM favorite_stocks WHERE user_id=%s ORDER BY created_at DESC,symbol""",
            (user_id,),
        ).fetchall())


def favorite_symbols(user_id: int) -> set[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT symbol FROM favorite_stocks WHERE user_id=%s", (user_id,)
        ).fetchall()
    return {str(row["symbol"]) for row in rows}


def add_favorite(user_id: int, stock: dict) -> dict:
    symbol = str(stock.get("symbol", "")).strip()
    if not symbol:
        raise ValueError("종목코드가 올바르지 않습니다.")
    with connect() as conn:
        conn.execute("SELECT id FROM admin_users WHERE id=%s FOR UPDATE", (user_id,))
        existing = conn.execute(
            """SELECT symbol,name,market,security_type,is_common_share,created_at
               FROM favorite_stocks WHERE user_id=%s AND symbol=%s""",
            (user_id, symbol),
        ).fetchone()
        if existing:
            return existing
        count = conn.execute(
            "SELECT count(*) AS count FROM favorite_stocks WHERE user_id=%s", (user_id,)
        ).fetchone()["count"]
        if count >= MAX_FAVORITES:
            raise ValueError(f"관심 종목은 최대 {MAX_FAVORITES}개까지 저장할 수 있습니다.")
        return conn.execute(
            """INSERT INTO favorite_stocks
               (user_id,symbol,name,market,security_type,is_common_share)
               VALUES (%s,%s,%s,%s,%s,%s)
               RETURNING symbol,name,market,security_type,is_common_share,created_at""",
            (
                user_id,
                symbol,
                str(stock.get("name", symbol)),
                str(stock.get("market", "KR")),
                str(stock.get("securityType", "")),
                bool(stock.get("isCommonShare", False)),
            ),
        ).fetchone()


def remove_favorite(user_id: int, symbol: str) -> bool:
    with connect() as conn:
        deleted = conn.execute(
            "DELETE FROM favorite_stocks WHERE user_id=%s AND symbol=%s RETURNING symbol",
            (user_id, symbol.strip()),
        ).fetchone()
    return deleted is not None
