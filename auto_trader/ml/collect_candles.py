"""Collect completed Toss one-minute candles into the immutable ML source table."""

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
    RawCandle,
    candle_quality_report,
    finish_collection_run,
    save_raw_candles,
    start_collection_run,
)


def collect_candles(
    client: TossClient,
    symbols: list[str],
    *,
    count: int,
    collected_at: datetime | None = None,
    cache_seconds: int = 0,
) -> dict:
    now = collected_at or datetime.now(timezone.utc)
    normalized = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    if not normalized:
        raise ValueError("수집할 종목 코드가 필요합니다.")
    if count <= 0:
        raise ValueError("count는 1 이상이어야 합니다.")

    run_id = start_collection_run("TOSS", "1m", normalized, count, now)
    inserted = duplicates = 0
    errors: dict[str, str] = {}
    details = {}
    observations = {}
    for symbol in normalized:
        try:
            received = client._domestic_candles(
                symbol, "1m", count, cache_seconds=cache_seconds
            )
            received_at = collected_at or datetime.now(timezone.utc)
            observations[symbol] = opening_observation(received, received_at, count)
            completed = [
                RawCandle.from_live_candle(symbol, candle, collected_at=received_at)
                for candle in received
                if candle.timestamp + timedelta(minutes=1) <= received_at
            ]
            new_rows, duplicate_rows = save_raw_candles(completed, run_id=run_id)
            inserted += new_rows
            duplicates += duplicate_rows
        except Exception as exc:
            errors[symbol] = str(exc)
            details[symbol] = error_detail(exc)

    finished_at = datetime.now(timezone.utc)
    status = finish_collection_run(
        run_id,
        inserted_rows=inserted,
        duplicate_rows=duplicates,
        errors=errors,
        finished_at=finished_at,
        diagnostics={"kind": "regular", "error_details": details, "series": observations},
    )
    return {
        "run_id": run_id,
        "status": status,
        "symbols": normalized,
        "inserted_rows": inserted,
        "duplicate_rows": duplicates,
        "errors": errors,
        "error_details": details,
        "quality": [candle_quality_report(symbol) for symbol in normalized],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        default=",".join(settings.watch_symbols),
        help="Comma-separated symbols. Defaults to WATCH_SYMBOLS.",
    )
    parser.add_argument("--count", type=int, default=200, help="Recent one-minute candles per symbol")
    args = parser.parse_args()

    initialize()
    result = collect_candles(TossClient(), args.symbols.split(","), count=args.count)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["status"] == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
