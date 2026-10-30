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
from .collection_errors import error_category
from .gap_repair import missing_timestamps


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
    as_of: datetime | None = None,
) -> dict[str, Any]:
    if table not in {"ml_raw_candles", "ml_market_indicator_candles"}:
        raise ValueError("지원하지 않는 품질 검사 테이블입니다.")
    if key_column not in {"symbol", "indicator"}:
        raise ValueError("지원하지 않는 품질 검사 키입니다.")
    source = "TOSS" if table == "ml_raw_candles" else "TOSS_MARKET_INDICATOR"
    expected = [start_at + timedelta(minutes=index) for index in range(expected_bars)]
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {key_column} AS item, count(*) AS row_count,
                   min(event_at) AS first_event_at, max(event_at) AS last_event_at,
                   count(*) FILTER (
                       WHERE available_at - event_at > interval '5 minutes'
                   ) AS delayed_over_5m,
                   count(*) FILTER (WHERE volume = 0) AS zero_volume_rows,
                   array_agg(DISTINCT event_at ORDER BY event_at) AS timestamps
              FROM {table}
             WHERE interval='1m' AND event_at >= %s AND event_at < %s
               AND {key_column} = ANY(%s)
               AND source=%s AND available_at<=%s
             GROUP BY {key_column}
            """,
            (start_at, min(end_at, start_at + timedelta(minutes=expected_bars)), list(requested), source, as_of or datetime.now(timezone.utc)),
        ).fetchall()
        outside_rows = conn.execute(
            f"SELECT {key_column} AS item,count(*) AS row_count FROM {table} "
            "WHERE source=%s AND interval='1m' AND event_at>=%s AND event_at<%s "
            f"AND {key_column}=ANY(%s) AND available_at<=%s "
            "AND (event_at<%s OR event_at>=%s) "
            f"GROUP BY {key_column}",
            (source, start_at.replace(hour=0, minute=0), start_at.replace(hour=0, minute=0) + timedelta(days=1),
             list(requested), as_of or datetime.now(timezone.utc), start_at, end_at),
        ).fetchall()
    found = {str(row["item"]): row for row in rows}
    outside = {str(row["item"]): int(row["row_count"]) for row in outside_rows}
    items: dict[str, dict[str, Any]] = {}
    for item in requested:
        row = found.get(item, {})
        row_count = int(row.get("row_count", 0))
        missing = missing_timestamps(row.get("timestamps", []), expected)
        items[item] = {
            "row_count": row_count,
            "expected_bars": expected_bars,
            "missing_bars": len(missing),
            "missing_timestamps": [stamp.isoformat() for stamp in missing],
            "raw_missing_bars": len(missing),
            "raw_missing_timestamps": [stamp.isoformat() for stamp in missing],
            "regular_session_rows": row_count,
            "outside_regular_session_rows": outside.get(item, 0),
            "gap_origin": "UNVERIFIED" if missing else "COMPLETE",
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
        "regular_session_rows": sum(item["regular_session_rows"] for item in items.values()),
        "outside_regular_session_rows": sum(item["outside_regular_session_rows"] for item in items.values()),
        "missing_bars": sum(item["missing_bars"] for item in items.values()),
        "delayed_over_5m": sum(item["delayed_over_5m"] for item in items.values()),
        "zero_volume_rows": sum(item["zero_volume_rows"] for item in items.values()),
        "items": items,
    }


GAP_COUNTS = ("raw_missing_bars", "provider_absent_bars", "collection_gap_bars", "unverified_bars", "effective_missing_bars")


def _gap_totals(summaries):
    return {key: sum(summary.get(key, 0) for summary in summaries) for key in GAP_COUNTS}


def _final_provider_evidence(diagnostics, info, session_end, as_of):
    """Absence is confirmed only by a full-window, post-close response.

    V1 final evidence is accepted without requiring a V2-only metadata flag.
    Opening evidence can identify positive provider candles, never final absence.
    """
    try:
        received = datetime.fromisoformat(info["received_at"])
        return (diagnostics.get("expected_bars") == FULL_SESSION_BARS
                and not diagnostics.get("kind", "").startswith("opening")
                and info.get("provider_window_complete") is True
                and not info.get("error")
                and session_end <= received <= as_of)
    except (KeyError, ValueError, TypeError):
        return False


def _prefer_provider_evidence(current, candidate, *, as_of):
    """Prefer strength, then receive time; failures cannot erase confirmation.

    The SQL finished_at boundary still applies. Check received_at independently
    so future response data cannot be used even in an inconsistent stored run.
    """
    info, _ = candidate
    received = None
    if info.get("received_at") is not None:
        try:
            received = datetime.fromisoformat(info["received_at"])
            if received.utcoffset() is None or received > as_of:
                return current
        except (TypeError, ValueError):
            return current

    def strength(evidence):
        details, confirmed = evidence
        if confirmed:
            return 3
        if not details.get("provider_checked") or details.get("error"):
            return 0
        return 2 if details.get("provider_window_complete") and details.get("provider_timestamps") else 1

    if current is not None:
        if strength(candidate) < strength(current):
            return current
        if strength(candidate) == strength(current) and received is not None and current[0].get("received_at"):
            if received < datetime.fromisoformat(current[0]["received_at"]):
                return current
    return candidate


def _classify_gaps(info, evidence, final_confirmed):
    def stamps(values):
        return {datetime.fromisoformat(value).astimezone(KST).isoformat() for value in values}
    missing = stamps(info["raw_missing_timestamps"])
    provided = stamps(evidence.get("provider_timestamps", []))
    collection = missing & provided
    absent = (missing & stamps(evidence.get("provider_absent_timestamps", []))) - provided if final_confirmed else set()
    unverified = missing - absent - collection
    info.update(provider_absent_timestamps=sorted(absent), collection_gap_timestamps=sorted(collection),
                unverified_timestamps=sorted(unverified), provider_absent_bars=len(absent),
                collection_gap_bars=len(collection), unverified_bars=len(unverified),
                effective_missing_bars=len(collection) + len(unverified),
                final_provider_evidence_confirmed=final_confirmed,
                provider_semantics="UNCONFIRMED", provider_checked_at=evidence.get("received_at"))
    info["gap_origin"] = ("COMPLETE" if not missing else "COLLECTION_GAP" if collection
                          else "PROVIDER_ABSENT" if len(absent) == len(missing) else "UNVERIFIED")
    info["data_completeness_status"] = ("FAIL" if info["expected_bars"] and not info["row_count"]
                                        else "WARN" if info["effective_missing_bars"] or not info["expected_bars"] else "PASS")


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
        start_at=start_at, end_at=end_at, expected_bars=expected_bars, as_of=now,
    )
    indicator = _series_summary(
        "ml_market_indicator_candles", "indicator", normalized_indicators,
        start_at=start_at, end_at=end_at, expected_bars=expected_bars, as_of=now,
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
        day_start = datetime.combine(report_date, time.min, KST)
        day_end = day_start + timedelta(days=1)
        collection = conn.execute(
            """
            SELECT count(*) AS run_count,
                   count(*) FILTER (WHERE status='FAILED') AS failed_runs,
                   count(*) FILTER (WHERE status='PARTIAL') AS partial_runs,
                   coalesce(sum(inserted_rows), 0) AS inserted_rows,
                   coalesce(sum(duplicate_rows), 0) AS duplicate_rows
              FROM ml_collection_runs
             WHERE started_at >= %s AND started_at < %s AND started_at<=%s
            """,
            (day_start, day_end, now),
        ).fetchone()
        collection_runs = conn.execute(
            "SELECT id,source,status,inserted_rows,errors FROM ml_collection_runs "
            "WHERE started_at>=%s AND started_at<%s AND started_at<=%s ORDER BY id",
            (day_start, day_end, now),
        ).fetchall()
        # Rechecks of historical dates are performed later than the event day.
        # Keep health counters on their execution day, but read evidence for the
        # requested market date, constrained by this report's knowledge time.
        reconciliation_runs = conn.execute(
            "SELECT id,source,status,inserted_rows,errors FROM ml_collection_runs "
            "WHERE source='CANDLE_RECONCILIATION' AND errors->'_diagnostics'->>'report_date'=%s "
            "AND finished_at<=%s ORDER BY id",
            (report_date.isoformat(), now),
        ).fetchall()
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
    error_counts = {}
    reconciliation = {"run_count": 0, "recovered_rows": 0, "by_kind": {}}
    provider_evidence = {}
    for run in collection_runs:
        errors = run["errors"] or {}
        diagnostics = errors.get("_diagnostics", {})
        details = diagnostics.get("error_details", {})
        for key, message in errors.items():
            if key.startswith("_"):
                continue
            category = error_category(details.get(key, message))
            error_counts[category] = error_counts.get(category, 0) + 1
    for run in reconciliation_runs:
        diagnostics = (run["errors"] or {}).get("_diagnostics", {})
        kind = diagnostics.get("kind", "unknown")
        recovered = int(run["inserted_rows"])
        reconciliation["run_count"] += 1
        reconciliation["recovered_rows"] += recovered
        reconciliation["by_kind"][kind] = reconciliation["by_kind"].get(kind, 0) + recovered
        for key, info in diagnostics.get("series", {}).items():
            if info.get("provider_checked") or info.get("error"):
                candidate = (info, _final_provider_evidence(diagnostics, info, end_at, now))
                preferred = _prefer_provider_evidence(provider_evidence.get(key), candidate, as_of=now)
                if preferred is not None:
                    provider_evidence[key] = preferred
    for family, summary in (("stock", stock), ("indicator", indicator)):
        for key, info in summary["items"].items():
            evidence, final_confirmed = provider_evidence.get(f"{family}:{key}", ({}, False))
            _classify_gaps(info, evidence, final_confirmed)
        summary.update(_gap_totals(summary["items"].values()))
        statuses = {item["data_completeness_status"] for item in summary["items"].values()}
        summary["data_completeness_status"] = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses or not expected_bars else "PASS"
    reconciliation["unrecovered_rows"] = stock["missing_bars"] + indicator["missing_bars"]
    gap_summary = _gap_totals((stock, indicator))
    reconciliation["effective_unrecovered_rows"] = gap_summary["effective_missing_bars"]
    collection_summary["error_counts"] = error_counts
    collection_summary["reconciliation"] = reconciliation
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
    statuses = {stock["data_completeness_status"], indicator["data_completeness_status"]}
    completeness = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    health = "WARN" if (collection_summary["failed_runs"] or collection_summary["partial_runs"]
                         or stock["delayed_over_5m"] or indicator["delayed_over_5m"]) else "PASS"
    collection_summary["quality_semantics_version"] = 2
    collection_summary["gap_summary"] = gap_summary
    collection_summary["data_completeness_status"] = completeness
    collection_summary["collection_health_status"] = health
    report = {
        "report_date": report_date,
        "status": status,
        "data_completeness_status": completeness,
        "collection_health_status": health,
        "reconciliation_summary": reconciliation,
        "quality_semantics_version": 2,
        "gap_summary": gap_summary,
        **gap_summary,
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


def _with_detail_status(row):
    if row is None:
        return None
    result = dict(row)
    stock, indicator = result["stock_summary"], result["indicator_summary"]
    collection = result["collection_summary"]
    expected = result["expected_bars"]
    no_data = expected > 0 and any(s.get("requested_count", 0) and not s.get("row_count", 0) for s in (stock, indicator))
    result["data_completeness_status"] = collection.get("data_completeness_status") or (
        "FAIL" if no_data else "WARN" if not expected or stock.get("missing_bars") or indicator.get("missing_bars") else "PASS")
    result["collection_health_status"] = collection.get("collection_health_status") or (
        "WARN" if collection.get("partial_runs") or collection.get("failed_runs") or stock.get("delayed_over_5m") or indicator.get("delayed_over_5m") else "PASS")
    result["reconciliation_summary"] = collection.get("reconciliation", {})
    result["quality_semantics_version"] = collection.get("quality_semantics_version", 1)
    if result["quality_semantics_version"] >= 2:
        result["gap_summary"] = collection.get("gap_summary", _gap_totals((stock, indicator)))
        result.update(result["gap_summary"])
    return result


def latest_quality_report() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM ml_data_quality_reports ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
    return _with_detail_status(row)


def quality_report_for_date(report_date: date) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM ml_data_quality_reports WHERE report_date=%s", (report_date,)
        ).fetchone()
    return _with_detail_status(row)


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
