"""Build and persist a daily quality report for immutable ML observations."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..database import connect, initialize
from ..settings import settings


KST = ZoneInfo("Asia/Seoul")
MARKET_OPEN = time(9, 0)
LAST_BAR = time(15, 30)
FULL_SESSION_BARS = 391
CORE_MACRO_INDICATORS = (
    "USD_KRW",
    "US_SP500_PROXY",
    "US_NASDAQ100_PROXY",
    "US_DJIA_PROXY",
    "US_VOLATILITY_PROXY",
    "US_TREASURY_7_10Y_PROXY",
)


def _session_bounds(report_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(report_date, MARKET_OPEN, KST)
    return start, datetime.combine(report_date, LAST_BAR, KST) + timedelta(minutes=1)


def expected_bar_count(report_date: date, generated_at: datetime) -> int:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at에는 시간대 정보가 필요합니다.")
    local_now = generated_at.astimezone(KST)
    if report_date < local_now.date():
        return FULL_SESSION_BARS
    if report_date > local_now.date():
        return 0
    first_at, end_at = _session_bounds(report_date)
    completed_until = local_now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    completed_until = min(completed_until, end_at - timedelta(minutes=1))
    if completed_until < first_at:
        return 0
    return int((completed_until - first_at).total_seconds() // 60) + 1


def _series_summary(
    table: str,
    key_column: str,
    requested: tuple[str, ...],
    *,
    start_at: datetime,
    end_at: datetime,
    expected_bars: int,
) -> dict[str, Any]:
    if table not in {"ml_raw_candles", "ml_market_indicator_candles"}:
        raise ValueError("지원하지 않는 품질 검사 테이블입니다.")
    if key_column not in {"symbol", "indicator"}:
        raise ValueError("지원하지 않는 품질 검사 키입니다.")
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {key_column} AS item, count(*) AS row_count,
                   min(event_at) AS first_event_at, max(event_at) AS last_event_at,
                   count(*) FILTER (
                       WHERE available_at - event_at > interval '5 minutes'
                   ) AS delayed_over_5m,
                   count(*) FILTER (WHERE volume = 0) AS zero_volume_rows
              FROM {table}
             WHERE interval='1m' AND event_at >= %s AND event_at < %s
               AND {key_column} = ANY(%s)
             GROUP BY {key_column}
            """,
            (start_at, end_at, list(requested)),
        ).fetchall()
    found = {str(row["item"]): row for row in rows}
    items: dict[str, dict[str, Any]] = {}
    for item in requested:
        row = found.get(item, {})
        row_count = int(row.get("row_count", 0))
        items[item] = {
            "row_count": row_count,
            "expected_bars": expected_bars,
            "missing_bars": max(0, expected_bars - row_count),
            "first_event_at": (
                row["first_event_at"].isoformat() if row.get("first_event_at") else None
            ),
            "last_event_at": (
                row["last_event_at"].isoformat() if row.get("last_event_at") else None
            ),
            "delayed_over_5m": int(row.get("delayed_over_5m", 0)),
            "zero_volume_rows": int(row.get("zero_volume_rows", 0)),
        }
    return {
        "requested_count": len(requested),
        "row_count": sum(item["row_count"] for item in items.values()),
        "missing_bars": sum(item["missing_bars"] for item in items.values()),
        "delayed_over_5m": sum(item["delayed_over_5m"] for item in items.values()),
        "zero_volume_rows": sum(item["zero_volume_rows"] for item in items.values()),
        "items": items,
    }


def generate_quality_report(
    report_date: date,
    *,
    symbols: tuple[str, ...],
    indicators: tuple[str, ...],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    expected_bars = expected_bar_count(report_date, now)
    start_at, end_at = _session_bounds(report_date)
    normalized_symbols = tuple(dict.fromkeys(item.strip().upper() for item in symbols if item.strip()))
    normalized_indicators = tuple(
        dict.fromkeys(item.strip().upper() for item in indicators if item.strip())
    )
    stock = _series_summary(
        "ml_raw_candles", "symbol", normalized_symbols,
        start_at=start_at, end_at=end_at, expected_bars=expected_bars,
    )
    indicator = _series_summary(
        "ml_market_indicator_candles", "indicator", normalized_indicators,
        start_at=start_at, end_at=end_at, expected_bars=expected_bars,
    )
    with connect() as conn:
        decision = conn.execute(
            """
            SELECT count(*) AS row_count,
                   count(DISTINCT run_id) AS run_count,
                   count(*) FILTER (WHERE action LIKE 'BUY_%%') AS buy_actions,
                   count(*) FILTER (WHERE action LIKE 'SELL_%%') AS sell_actions,
                   count(*) FILTER (WHERE action IN ('BUY_BLOCKED','SELL_BLOCKED')) AS blocked_actions
              FROM ml_strategy_decisions
             WHERE decision_at >= %s AND decision_at < %s
            """,
            (start_at, end_at),
        ).fetchone()
        collection = conn.execute(
            """
            SELECT count(*) AS run_count,
                   count(*) FILTER (WHERE status='FAILED') AS failed_runs,
                   count(*) FILTER (WHERE status='PARTIAL') AS partial_runs,
                   coalesce(sum(inserted_rows), 0) AS inserted_rows,
                   coalesce(sum(duplicate_rows), 0) AS duplicate_rows
              FROM ml_collection_runs
             WHERE started_at >= %s AND started_at < %s
            """,
            (start_at, end_at),
        ).fetchone()
        day_start = datetime.combine(report_date, time.min, KST)
        day_end = day_start + timedelta(days=1)
        macro_rows = conn.execute(
            """
            SELECT DISTINCT ON (indicator)
                   indicator,value,unit,event_at,available_at,source
              FROM ml_macro_observations
             WHERE indicator = ANY(%s)
               AND available_at >= %s AND available_at < %s
             ORDER BY indicator,available_at DESC,event_at DESC
            """,
            (list(CORE_MACRO_INDICATORS), day_start, day_end),
        ).fetchall()

    decision_summary = {key: int(value or 0) for key, value in decision.items()}
    collection_summary = {key: int(value or 0) for key, value in collection.items()}
    macro_found = {row["indicator"]: row for row in macro_rows}
    macro_summary = {
        "expected_count": len(CORE_MACRO_INDICATORS),
        "present_count": len(macro_found),
        "missing": [item for item in CORE_MACRO_INDICATORS if item not in macro_found],
        "items": {
            item: {
                "value": str(row["value"]),
                "unit": row["unit"],
                "source": row["source"],
                "event_at": row["event_at"].isoformat(),
                "available_at": row["available_at"].isoformat(),
            }
            for item, row in macro_found.items()
        },
    }
    issues: list[str] = []
    if expected_bars == 0:
        issues.append("아직 완료된 정규장 1분봉이 없는 시각입니다.")
    if stock["missing_bars"]:
        issues.append(f"종목 1분봉 {stock['missing_bars']}건이 예상 수량보다 부족합니다.")
    if indicator["missing_bars"]:
        issues.append(f"시장 지표 1분봉 {indicator['missing_bars']}건이 예상 수량보다 부족합니다.")
    if stock["delayed_over_5m"]:
        issues.append(f"종목 1분봉 {stock['delayed_over_5m']}건이 발생 후 5분을 넘겨 수집됐습니다.")
    if indicator["delayed_over_5m"]:
        issues.append(
            f"시장 지표 1분봉 {indicator['delayed_over_5m']}건이 발생 후 5분을 넘겨 수집됐습니다."
        )
    if collection_summary["failed_runs"] or collection_summary["partial_runs"]:
        issues.append(
            f"수집 실패 {collection_summary['failed_runs']}회, 부분 성공 {collection_summary['partial_runs']}회가 있습니다."
        )
    if decision_summary["row_count"] == 0:
        issues.append("PAPER 전략 판단 기록이 없습니다. 전략 미실행 여부를 확인하세요.")
    if macro_summary["missing"]:
        issues.append(
            f"환율·미국 대용 지표 {len(macro_summary['missing'])}개가 아직 수집되지 않았습니다."
        )

    no_stock_data = bool(normalized_symbols) and stock["row_count"] == 0 and expected_bars > 0
    no_indicator_data = bool(normalized_indicators) and indicator["row_count"] == 0 and expected_bars > 0
    status = "FAIL" if no_stock_data or no_indicator_data else ("WARN" if issues else "PASS")
    report = {
        "report_date": report_date,
        "status": status,
        "expected_bars": expected_bars,
        "stock_summary": stock,
        "indicator_summary": indicator,
        "macro_summary": macro_summary,
        "decision_summary": decision_summary,
        "collection_summary": collection_summary,
        "issues": issues,
        "generated_at": now,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ml_data_quality_reports(
                report_date,status,expected_bars,stock_summary,indicator_summary,macro_summary,
                decision_summary,collection_summary,issues,generated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (report_date) DO UPDATE SET
                status=EXCLUDED.status,
                expected_bars=EXCLUDED.expected_bars,
                stock_summary=EXCLUDED.stock_summary,
                indicator_summary=EXCLUDED.indicator_summary,
                macro_summary=EXCLUDED.macro_summary,
                decision_summary=EXCLUDED.decision_summary,
                collection_summary=EXCLUDED.collection_summary,
                issues=EXCLUDED.issues,
                generated_at=EXCLUDED.generated_at
            """,
            (
                report_date, status, expected_bars, Jsonb(stock), Jsonb(indicator), Jsonb(macro_summary),
                Jsonb(decision_summary), Jsonb(collection_summary), Jsonb(issues), now,
            ),
        )
    return report


def latest_quality_report() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM ml_data_quality_reports ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def quality_report_for_date(report_date: date) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM ml_data_quality_reports WHERE report_date=%s", (report_date,)
        ).fetchone()
    return dict(row) if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Korean market date in YYYY-MM-DD; defaults to today")
    args = parser.parse_args()
    initialize()
    report_date = date.fromisoformat(args.date) if args.date else datetime.now(KST).date()
    report = generate_quality_report(
        report_date,
        symbols=settings.watch_symbols,
        indicators=settings.ml_market_indicators,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
