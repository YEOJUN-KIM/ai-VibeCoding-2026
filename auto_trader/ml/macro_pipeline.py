"""Collect USD/KRW and daily U.S. market context without future leakage."""

from __future__ import annotations

import argparse
import csv
import io
import json
from io import BytesIO
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from time import sleep
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..database import connect, initialize
from ..toss import TossClient
from .data_pipeline import finish_collection_run, start_collection_run


NEW_YORK = ZoneInfo("America/New_York")
FRED_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_SERIES = {
    "SP500": ("US_SP500", "index"),
    "NASDAQCOM": ("US_NASDAQ_COMPOSITE", "index"),
    "DJIA": ("US_DJIA", "index"),
    "VIXCLS": ("US_VIX", "index"),
    "DGS10": ("US_TREASURY_10Y", "percent"),
}
TOSS_US_PROXIES = {
    "SPY": "US_SP500_PROXY",
    "QQQ": "US_NASDAQ100_PROXY",
    "DIA": "US_DJIA_PROXY",
    "VIXY": "US_VOLATILITY_PROXY",
    "IEF": "US_TREASURY_7_10Y_PROXY",
}


@dataclass(frozen=True)
class MacroObservation:
    source: str
    indicator: str
    event_at: datetime
    available_at: datetime
    collected_at: datetime
    value: Decimal
    unit: str
    frequency: str
    raw_payload: dict[str, Any]

    def validate(self) -> None:
        if not self.source or not self.indicator or not self.unit:
            raise ValueError("거시 지표의 source, indicator, unit은 비어 있을 수 없습니다.")
        if self.frequency not in {"SNAPSHOT", "DAILY"}:
            raise ValueError("거시 지표 frequency는 SNAPSHOT 또는 DAILY여야 합니다.")
        for field, value in (
            ("event_at", self.event_at),
            ("available_at", self.available_at),
            ("collected_at", self.collected_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field}에는 시간대 정보가 필요합니다.")
        if not self.event_at <= self.available_at <= self.collected_at:
            raise ValueError("event_at <= available_at <= collected_at 순서여야 합니다.")
        if not self.value.is_finite():
            raise ValueError("거시 지표 값은 유한해야 합니다.")


def save_macro_observations(
    observations: Iterable[MacroObservation], *, run_id: int | None = None
) -> tuple[int, int]:
    records = list(observations)
    for record in records:
        record.validate()
    inserted = 0
    with connect() as conn:
        for record in records:
            row = conn.execute(
                """
                INSERT INTO ml_macro_observations(
                    source,indicator,event_at,available_at,collected_at,value,
                    unit,frequency,collection_run_id,raw_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source,indicator,event_at,value) DO NOTHING
                RETURNING 1
                """,
                (
                    record.source, record.indicator, record.event_at, record.available_at,
                    record.collected_at, record.value, record.unit, record.frequency,
                    run_id, Jsonb(record.raw_payload),
                ),
            ).fetchone()
            inserted += int(row is not None)
    return inserted, len(records) - inserted


def available_macro_observations(indicator: str, *, as_of: datetime) -> list[dict[str, Any]]:
    """Return the latest known version of each event as of a historical decision time."""
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT DISTINCT ON (source,indicator,event_at)
                       source,indicator,event_at,available_at,collected_at,value,unit,frequency
                  FROM ml_macro_observations
                 WHERE indicator=%s AND available_at <= %s
                 ORDER BY source,indicator,event_at,available_at DESC
                """,
                (indicator.strip().upper(), as_of),
            ).fetchall()
        )


def fetch_fred_series(
    series_id: str,
    *,
    start_date: date,
    end_date: date,
    timeout: float = 12,
) -> list[tuple[date, Decimal, dict[str, str]]]:
    if series_id not in FRED_SERIES:
        raise ValueError("지원하지 않는 FRED 시계열입니다.")
    return fetch_fred_series_batch(
        tuple([series_id]), start_date=start_date, end_date=end_date, timeout=timeout
    )[series_id]


def fetch_fred_series_batch(
    series_ids: tuple[str, ...],
    *,
    start_date: date,
    end_date: date,
    timeout: float = 12,
) -> dict[str, list[tuple[date, Decimal, dict[str, str]]]]:
    if not series_ids or any(series_id not in FRED_SERIES for series_id in series_ids):
        raise ValueError("지원하지 않는 FRED 시계열입니다.")
    query = urlencode(
        {"id": ",".join(series_ids), "cosd": start_date.isoformat(), "coed": end_date.isoformat()}
    )
    request = Request(
        f"{FRED_BASE_URL}?{query}",
        headers={"Accept": "text/csv", "User-Agent": "FOLIO/0.2"},
    )
    body: bytes | None = None
    last_error: OSError | TimeoutError | None = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read()
            break
        except (OSError, TimeoutError) as exc:
            last_error = exc
            if attempt < 1:
                sleep(1.5 * (attempt + 1))
    if body is None:
        assert last_error is not None
        raise last_error
    texts: list[str]
    if body.startswith(b"PK\x03\x04"):
        with ZipFile(BytesIO(body)) as archive:
            texts = [
                archive.read(name).decode("utf-8-sig")
                for name in archive.namelist()
                if name.lower().endswith(".csv")
            ]
    else:
        texts = [body.decode("utf-8-sig")]

    result: dict[str, list[tuple[date, Decimal, dict[str, str]]]] = {
        series_id: [] for series_id in series_ids
    }
    for text in texts:
        for row in csv.DictReader(io.StringIO(text)):
            observed_on = date.fromisoformat(row["observation_date"])
            if observed_on < start_date or observed_on > end_date:
                continue
            for series_id in series_ids:
                raw_value = row.get(series_id, "").strip()
                if not raw_value or raw_value == ".":
                    continue
                try:
                    value = Decimal(raw_value)
                except InvalidOperation as exc:
                    raise ValueError(f"FRED {series_id} 값이 숫자가 아닙니다.") from exc
                result[series_id].append((observed_on, value, dict(row)))
    return result


def collect_macro_context(
    toss_client: TossClient,
    *,
    days: int = 14,
    collected_at: datetime | None = None,
) -> dict[str, Any]:
    now = collected_at or datetime.now(timezone.utc)
    requested = [
        "USD_KRW",
        *TOSS_US_PROXIES.values(),
        *(name for name, _ in FRED_SERIES.values()),
    ]
    run_id = start_collection_run("MACRO_CONTEXT", "MIXED", requested, days, now)
    inserted = duplicates = 0
    errors: dict[str, str] = {}

    try:
        rate = toss_client.exchange_rate("USD", "KRW")
        valid_from = datetime.fromisoformat(str(rate["validFrom"]))
        observation = MacroObservation(
            source="TOSS_EXCHANGE_RATE",
            indicator="USD_KRW",
            event_at=valid_from,
            available_at=now,
            collected_at=now,
            value=Decimal(str(rate["midRate"])),
            unit="KRW_PER_USD",
            frequency="SNAPSHOT",
            raw_payload=rate,
        )
        new_rows, duplicate_rows = save_macro_observations([observation], run_id=run_id)
        inserted += new_rows
        duplicates += duplicate_rows
    except Exception as exc:
        errors["USD_KRW"] = str(exc)

    for symbol, indicator in TOSS_US_PROXIES.items():
        try:
            candles = toss_client._domestic_candles(
                symbol, "1d", min(days, 200), cache_seconds=0
            )
            observations = [
                MacroObservation(
                    source=f"TOSS_US_PROXY:{symbol}",
                    indicator=indicator,
                    event_at=candle.timestamp,
                    available_at=now,
                    collected_at=now,
                    value=candle.close_price,
                    unit="USD",
                    frequency="DAILY",
                    raw_payload={
                        "symbol": symbol,
                        "timestamp": candle.timestamp.isoformat(),
                        "openPrice": str(candle.open_price),
                        "highPrice": str(candle.high_price),
                        "lowPrice": str(candle.low_price),
                        "closePrice": str(candle.close_price),
                        "volume": str(candle.volume),
                    },
                )
                for candle in candles
                if candle.timestamp <= now
            ]
            new_rows, duplicate_rows = save_macro_observations(observations, run_id=run_id)
            inserted += new_rows
            duplicates += duplicate_rows
        except Exception as exc:
            errors[indicator] = str(exc)

    start_date = now.astimezone(NEW_YORK).date() - timedelta(days=days)
    end_date = now.astimezone(NEW_YORK).date()
    try:
        fred_rows = fetch_fred_series_batch(
            tuple(FRED_SERIES), start_date=start_date, end_date=end_date
        )
    except Exception as exc:
        fred_rows = {}
        for _, (indicator, _) in FRED_SERIES.items():
            errors[indicator] = str(exc)

    for series_id, (indicator, unit) in FRED_SERIES.items():
        if series_id not in fred_rows:
            continue
        try:
            rows = fred_rows[series_id]
            observations = [
                MacroObservation(
                    source=f"FRED:{series_id}",
                    indicator=indicator,
                    event_at=datetime.combine(observed_on, time(16, 0), NEW_YORK),
                    available_at=now,
                    collected_at=now,
                    value=value,
                    unit=unit,
                    frequency="DAILY",
                    raw_payload=raw,
                )
                for observed_on, value, raw in rows
                if datetime.combine(observed_on, time(16, 0), NEW_YORK) <= now
            ]
            new_rows, duplicate_rows = save_macro_observations(observations, run_id=run_id)
            inserted += new_rows
            duplicates += duplicate_rows
        except Exception as exc:
            errors[indicator] = str(exc)

    status = finish_collection_run(
        run_id,
        inserted_rows=inserted,
        duplicate_rows=duplicates,
        errors=errors,
        finished_at=datetime.now(timezone.utc),
    )
    return {
        "run_id": run_id,
        "status": status,
        "inserted_rows": inserted,
        "duplicate_rows": duplicates,
        "errors": errors,
        "requested": requested,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    if not 1 <= args.days <= 3650:
        raise SystemExit("--days must be between 1 and 3650")
    initialize()
    result = collect_macro_context(TossClient(), days=args.days)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["status"] == "FAILED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
