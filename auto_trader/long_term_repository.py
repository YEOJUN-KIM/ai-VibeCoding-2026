"""장기 관찰 점수를 PostgreSQL에 저장해 반복 API 호출을 피한다."""

from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from .database import connect
from .long_term import long_term_recommendation_assessment
from .models import LongTermAnalysis, LongTermWatchCandidate


CACHE_HOURS = 24
MIN_RECOMMENDATION_SCORE = 70


def fresh_long_term_analysis(symbol: str) -> LongTermAnalysis | None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=CACHE_HOURS)
    with connect() as conn:
        row = conn.execute(
            "SELECT analysis FROM long_term_analyses WHERE symbol=%s AND analyzed_at >= %s",
            (symbol.strip().zfill(6), cutoff),
        ).fetchone()
    return LongTermAnalysis.model_validate(row["analysis"]) if row else None


def save_long_term_analysis(analysis: LongTermAnalysis) -> None:
    payload = analysis.model_dump(mode="json")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO long_term_analyses(symbol,name,market,overall_score,rank,grade,analysis,analyzed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(symbol) DO UPDATE SET
              name=excluded.name, market=excluded.market, overall_score=excluded.overall_score,
              rank=excluded.rank, grade=excluded.grade, analysis=excluded.analysis,
              analyzed_at=excluded.analyzed_at
            """,
            (analysis.symbol, analysis.name, analysis.market, analysis.overall_score,
             analysis.rank, analysis.grade, Jsonb(payload), analysis.generated_at),
        )


def ranked_long_term_analyses(limit: int = 12, user_id: int | None = None) -> list[LongTermAnalysis]:
    fetch_limit = max(12, limit * 3)
    with connect() as conn:
        if user_id is None:
            rows = conn.execute(
                """
                SELECT a.analysis FROM long_term_recommendations r
                JOIN long_term_analyses a ON a.symbol=r.symbol
                WHERE a.overall_score >= %s
                ORDER BY a.overall_score DESC,a.analyzed_at DESC
                LIMIT %s
                """,
                (MIN_RECOMMENDATION_SCORE, fetch_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT a.analysis FROM long_term_recommendations r
                JOIN long_term_analyses a ON a.symbol=r.symbol
                WHERE a.overall_score >= %s
                  AND NOT EXISTS (
                    SELECT 1 FROM favorite_stocks f WHERE f.user_id=%s AND f.symbol=a.symbol
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM long_term_watchlist w WHERE w.user_id=%s AND w.symbol=a.symbol
                  )
                ORDER BY a.overall_score DESC,a.analyzed_at DESC
                LIMIT %s
                """,
                (MIN_RECOMMENDATION_SCORE, user_id, user_id, fetch_limit),
            ).fetchall()
    analyses = [LongTermAnalysis.model_validate(row["analysis"]) for row in rows]
    return [analysis for analysis in analyses
            if long_term_recommendation_assessment(analysis)[0]][:limit]


def replace_long_term_recommendations(symbols: list[str]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM long_term_recommendations")
        for source_rank, symbol in enumerate(symbols, start=1):
            conn.execute(
                """
                INSERT INTO long_term_recommendations(symbol,source_rank)
                SELECT %s,%s WHERE EXISTS (SELECT 1 FROM long_term_analyses WHERE symbol=%s)
                """,
                (symbol, source_rank, symbol),
            )


def long_term_watched_symbols() -> set[str]:
    """단일 사용자 운영에서 이미 관찰 중인 종목을 자동 추천에서 제외한다."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT symbol FROM favorite_stocks
            WHERE security_type='STOCK' AND is_common_share=true
            UNION
            SELECT symbol FROM long_term_watchlist
            """
        ).fetchall()
    return {str(row["symbol"]) for row in rows}


def fresh_long_term_analysis_count() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=CACHE_HOURS)
    with connect() as conn:
        row = conn.execute(
            "SELECT count(*) AS count FROM long_term_analyses WHERE analyzed_at >= %s",
            (cutoff,),
        ).fetchone()
    return int(row["count"])


def add_long_term_watch(user_id: int, symbol: str, name: str, market: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO long_term_watchlist(user_id,symbol,name,market)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT(user_id,symbol) DO UPDATE SET name=excluded.name,market=excluded.market
            """,
            (user_id, symbol, name, market),
        )


def remove_long_term_watch(user_id: int, symbol: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM long_term_watchlist WHERE user_id=%s AND symbol=%s",
            (user_id, symbol),
        )


def list_long_term_watch(user_id: int) -> list[LongTermWatchCandidate]:
    with connect() as conn:
        rows = conn.execute(
            """
            WITH candidates AS (
              SELECT symbol,name,market,true AS is_favorite,false AS added_manually,created_at
              FROM favorite_stocks WHERE user_id=%s AND security_type='STOCK' AND is_common_share=true
              UNION ALL
              SELECT symbol,name,market,false AS is_favorite,true AS added_manually,created_at
              FROM long_term_watchlist WHERE user_id=%s
            ), combined AS (
              SELECT symbol,max(name) AS name,max(market) AS market,
                     bool_or(is_favorite) AS is_favorite,bool_or(added_manually) AS added_manually,
                     min(created_at) AS created_at
              FROM candidates GROUP BY symbol
            )
            SELECT c.*,a.analysis FROM combined c
            LEFT JOIN long_term_analyses a ON a.symbol=c.symbol
            ORDER BY (a.overall_score IS NULL),a.overall_score DESC,c.created_at DESC
            """,
            (user_id, user_id),
        ).fetchall()
    return [LongTermWatchCandidate(
        symbol=row["symbol"], name=row["name"], market=row["market"],
        is_favorite=row["is_favorite"], added_manually=row["added_manually"],
        created_at=row["created_at"],
        analysis=LongTermAnalysis.model_validate(row["analysis"]) if row["analysis"] else None,
    ) for row in rows]
