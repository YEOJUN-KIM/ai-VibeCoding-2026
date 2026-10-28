"""Collect completed KOSPI and KOSDAQ candles for ML market context."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from ..database import initialize
from ..settings import settings
from ..toss import TossClient
from .collection_errors import error_detail
from .gap_repair import opening_observation
from .data_pipeline import (
    RawMarketIndicator,
    finish_collection_run,
    market_indicator_quality_report,
    save_market_indicator_candles,
    start_collection_run,
)


def collect_market_indicators(
    client: TossClient,
    indicators: list[str],
    *,
    count: int,
    collected_at: datetime | None = None,
    cache_seconds: int = 0,
) -> dict:
    now = collected_at or datetime.now(timezone.utc)
    normalized = list(dict.fromkeys(item.strip().upper() for item in indicators if item.strip()))
    if not normalized:
        raise ValueError("수집할 시장 지표가 필요합니다.")
    if any(item not in {"KOSPI", "KOSDAQ"} for item in normalized):
        raise ValueError("시장 지표 1분봉은 KOSPI와 KOSDAQ만 지원합니다.")
    if count <= 0:
        raise ValueError("count는 1 이상이어야 합니다.")

    run_id = start_collection_run("TOSS_MARKET_INDICATOR", "1m", normalized, count, now)
    inserted = duplicates = 0
    errors: dict[str, str] = {}
    details = {}
    observations = {}
    for indicator in normalized:
        try:
            received = client._market_indicator_candles(
                indicator, "1m", count, cache_seconds=cache_seconds
            )
            received_at = collected_at or datetime.now(timezone.utc)
            observations[indicator] = opening_observation(received, received_at, count)
            completed = [
                RawMarketIndicator.from_live_candle(indicator, candle, collected_at=received_at)
                for candle in received
                if candle.timestamp + timedelta(minutes=1) <= received_at
            ]
            new_rows, duplicate_rows = save_market_indicator_candles(completed, run_id=run_id)
            inserted += new_rows
            duplicates += duplicate_rows
        except Exception as exc:
            errors[indicator] = str(exc)
            details[indicator] = error_detail(exc)

    status = finish_collection_run(
        run_id,
        inserted_rows=inserted,
        duplicate_rows=duplicates,
        errors=errors,
        finished_at=datetime.now(timezone.utc),
        diagnostics={"kind": "regular", "error_details": details, "series": observations},
    )
    return {
        "run_id": run_id,
        "status": status,
        "indicators": normalized,
        "inserted_rows": inserted,
        "duplicate_rows": duplicates,
        "errors": errors,
        "error_details": details,
        "quality": [market_indicator_quality_report(item) for item in normalized],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--indicators", default=",".join(settings.ml_market_indicators))
    parser.add_argument("--count", type=int, default=200)
    args = parser.parse_args()
    initialize()
    result = collect_market_indicators(
        TossClient(), args.indicators.split(","), count=args.count
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["status"] == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
