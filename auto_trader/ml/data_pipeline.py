"""Store immutable market observations and read them without future leakage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from psycopg.types.json import Jsonb

from ..database import connect
from ..models import LiveStockCandle


@dataclass(frozen=True)
class RawCandle:
    source: str
    symbol: str
    interval: str
    event_at: datetime
    available_at: datetime
    collected_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal

    @classmethod
    def from_live_candle(
        cls,
        symbol: str,
        candle: LiveStockCandle,
        *,
        collected_at: datetime,
        source: str = "TOSS",
        interval: str = "1m",
    ) -> "RawCandle":
        # Historical API data becomes usable only when this process receives it.
        return cls(
            source=source,
            symbol=symbol.strip().upper(),
            interval=interval,
            event_at=candle.timestamp,
            available_at=collected_at,
            collected_at=collected_at,
            open_price=candle.open_price,
            high_price=candle.high_price,
            low_price=candle.low_price,
            close_price=candle.close_price,
            volume=candle.volume,
        )

    def validate(self) -> None:
        if not self.source or not self.symbol or not self.interval:
            raise ValueError("source, symbol, interval은 비어 있을 수 없습니다.")
        for name, value in (
            ("event_at", self.event_at),
            ("available_at", self.available_at),
            ("collected_at", self.collected_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name}에는 시간대 정보가 필요합니다.")
        if not self.event_at <= self.available_at <= self.collected_at:
            raise ValueError("event_at <= available_at <= collected_at 순서여야 합니다.")
        prices = (self.open_price, self.high_price, self.low_price, self.close_price)
        if any(not price.is_finite() or price <= 0 for price in prices):
            raise ValueError("OHLC 가격은 0보다 큰 유한한 값이어야 합니다.")
        if not self.volume.is_finite() or self.volume < 0:
            raise ValueError("거래량은 0 이상의 유한한 값이어야 합니다.")
        if self.high_price < max(self.open_price, self.low_price, self.close_price):
            raise ValueError("고가는 OHLC 중 가장 높은 값이어야 합니다.")
        if self.low_price > min(self.open_price, self.high_price, self.close_price):
            raise ValueError("저가는 OHLC 중 가장 낮은 값이어야 합니다.")

    def raw_payload(self) -> dict[str, str]:
        return {
            "timestamp": self.event_at.isoformat(),
            "openPrice": str(self.open_price),
            "highPrice": str(self.high_price),
            "lowPrice": str(self.low_price),
            "closePrice": str(self.close_price),
            "volume": str(self.volume),
        }


@dataclass(frozen=True)
class RawMarketIndicator:
    source: str
    indicator: str
    interval: str
    event_at: datetime
    available_at: datetime
    collected_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal

    @classmethod
    def from_live_candle(
        cls,
        indicator: str,
        candle: LiveStockCandle,
        *,
        collected_at: datetime,
        source: str = "TOSS_MARKET_INDICATOR",
        interval: str = "1m",
    ) -> "RawMarketIndicator":
        return cls(
            source=source,
            indicator=indicator.strip().upper(),
            interval=interval,
            event_at=candle.timestamp,
            available_at=collected_at,
            collected_at=collected_at,
            open_price=candle.open_price,
            high_price=candle.high_price,
            low_price=candle.low_price,
            close_price=candle.close_price,
            volume=candle.volume,
        )

    def validate(self) -> None:
        RawCandle(
            source=self.source,
            symbol=self.indicator,
            interval=self.interval,
            event_at=self.event_at,
            available_at=self.available_at,
            collected_at=self.collected_at,
            open_price=self.open_price,
            high_price=self.high_price,
            low_price=self.low_price,
            close_price=self.close_price,
            volume=self.volume,
        ).validate()
        if self.interval == "1m" and self.indicator not in {"KOSPI", "KOSDAQ"}:
            raise ValueError("시장 지표 1분봉은 KOSPI와 KOSDAQ만 지원합니다.")

    def raw_payload(self) -> dict[str, str]:
        return {
            "timestamp": self.event_at.isoformat(),
            "openPrice": str(self.open_price),
            "highPrice": str(self.high_price),
            "lowPrice": str(self.low_price),
            "closePrice": str(self.close_price),
            "volume": str(self.volume),
        }


def start_collection_run(
    source: str, interval: str, symbols: list[str], count: int, started_at: datetime
) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO ml_collection_runs(
                source, interval, requested_symbols, requested_count, status, started_at
            ) VALUES (%s,%s,%s,%s,'RUNNING',%s)
            RETURNING id
            """,
            (source, interval, Jsonb(symbols), count, started_at),
        ).fetchone()
    return int(row["id"])


def finish_collection_run(
    run_id: int,
    *,
    inserted_rows: int,
    duplicate_rows: int,
    errors: dict[str, str],
    finished_at: datetime,
) -> str:
    status = "COMPLETED" if not errors else (
        "PARTIAL" if inserted_rows or duplicate_rows else "FAILED"
    )
    with connect() as conn:
        conn.execute(
            """
            UPDATE ml_collection_runs
               SET status=%s, inserted_rows=%s, duplicate_rows=%s,
                   errors=%s, finished_at=%s
             WHERE id=%s
            """,
            (status, inserted_rows, duplicate_rows, Jsonb(errors), finished_at, run_id),
        )
    return status


def save_raw_candles(candles: Iterable[RawCandle], *, run_id: int | None = None) -> tuple[int, int]:
    records = list(candles)
    for candle in records:
        candle.validate()
    if not records:
        return 0, 0

    inserted = 0
    with connect() as conn:
        for candle in records:
            row = conn.execute(
                """
                INSERT INTO ml_raw_candles(
                    source,symbol,interval,event_at,available_at,collected_at,
                    open_price,high_price,low_price,close_price,volume,
                    collection_run_id,raw_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source,symbol,interval,event_at) DO NOTHING
                RETURNING 1
                """,
                (
                    candle.source,
                    candle.symbol,
                    candle.interval,
                    candle.event_at,
                    candle.available_at,
                    candle.collected_at,
                    candle.open_price,
                    candle.high_price,
                    candle.low_price,
                    candle.close_price,
                    candle.volume,
                    run_id,
                    Jsonb(candle.raw_payload()),
                ),
            ).fetchone()
            inserted += int(row is not None)
    return inserted, len(records) - inserted


def save_market_indicator_candles(
    candles: Iterable[RawMarketIndicator], *, run_id: int | None = None
) -> tuple[int, int]:
    records = list(candles)
    for candle in records:
        candle.validate()
    if not records:
        return 0, 0

    inserted = 0
    with connect() as conn:
        for candle in records:
            row = conn.execute(
                """
                INSERT INTO ml_market_indicator_candles(
                    source,indicator,interval,event_at,available_at,collected_at,
                    open_price,high_price,low_price,close_price,volume,
                    collection_run_id,raw_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source,indicator,interval,event_at) DO NOTHING
                RETURNING 1
                """,
                (
                    candle.source,
                    candle.indicator,
                    candle.interval,
                    candle.event_at,
                    candle.available_at,
                    candle.collected_at,
                    candle.open_price,
                    candle.high_price,
                    candle.low_price,
                    candle.close_price,
                    candle.volume,
                    run_id,
                    Jsonb(candle.raw_payload()),
                ),
            ).fetchone()
            inserted += int(row is not None)
    return inserted, len(records) - inserted


def available_candles(
    symbol: str,
    *,
    start_at: datetime,
    end_at: datetime,
    as_of: datetime,
    source: str = "TOSS",
    interval: str = "1m",
) -> list[dict[str, Any]]:
    """Return only rows the process had received by the requested historical time."""
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT source,symbol,interval,event_at,available_at,collected_at,
                       open_price,high_price,low_price,close_price,volume
                  FROM ml_raw_candles
                 WHERE source=%s AND symbol=%s AND interval=%s
                   AND event_at >= %s AND event_at <= %s AND available_at <= %s
                 ORDER BY event_at
                """,
                (source, symbol.strip().upper(), interval, start_at, end_at, as_of),
            ).fetchall()
        )


def available_market_indicator_candles(
    indicator: str,
    *,
    start_at: datetime,
    end_at: datetime,
    as_of: datetime,
    source: str = "TOSS_MARKET_INDICATOR",
    interval: str = "1m",
) -> list[dict[str, Any]]:
    """Return only index rows received by the requested historical time."""
    with connect() as conn:
        return list(
            conn.execute(
                """
                SELECT source,indicator,interval,event_at,available_at,collected_at,
                       open_price,high_price,low_price,close_price,volume
                  FROM ml_market_indicator_candles
                 WHERE source=%s AND indicator=%s AND interval=%s
                   AND event_at >= %s AND event_at <= %s AND available_at <= %s
                 ORDER BY event_at
                """,
                (source, indicator.strip().upper(), interval, start_at, end_at, as_of),
            ).fetchall()
        )


def candle_quality_report(
    symbol: str, *, source: str = "TOSS", interval: str = "1m"
) -> dict[str, Any]:
    with connect() as conn:
        rows = list(
            conn.execute(
                """
                SELECT event_at,available_at,open_price,high_price,low_price,close_price,volume
                  FROM ml_raw_candles
                 WHERE source=%s AND symbol=%s AND interval=%s
                 ORDER BY event_at
                """,
                (source, symbol.strip().upper(), interval),
            ).fetchall()
        )

    delayed = sum(1 for row in rows if row["available_at"] - row["event_at"] > timedelta(minutes=5))
    missing = 0
    if interval == "1m":
        for previous, current in zip(rows, rows[1:]):
            previous_at = previous["event_at"]
            current_at = current["event_at"]
            if previous_at.astimezone(timezone.utc).date() == current_at.astimezone(timezone.utc).date():
                gap = int((current_at - previous_at).total_seconds() // 60) - 1
                missing += max(0, gap)

    return {
        "source": source,
        "symbol": symbol.strip().upper(),
        "interval": interval,
        "row_count": len(rows),
        "first_event_at": rows[0]["event_at"] if rows else None,
        "last_event_at": rows[-1]["event_at"] if rows else None,
        "delayed_over_5m": delayed,
        "missing_intervals_between_rows": missing,
        "zero_volume_rows": sum(1 for row in rows if row["volume"] == 0),
    }


def market_indicator_quality_report(
    indicator: str,
    *,
    source: str = "TOSS_MARKET_INDICATOR",
    interval: str = "1m",
) -> dict[str, Any]:
    normalized = indicator.strip().upper()
    with connect() as conn:
        rows = list(
            conn.execute(
                """
                SELECT event_at,available_at,volume
                  FROM ml_market_indicator_candles
                 WHERE source=%s AND indicator=%s AND interval=%s
                 ORDER BY event_at
                """,
                (source, normalized, interval),
            ).fetchall()
        )
    delayed = sum(1 for row in rows if row["available_at"] - row["event_at"] > timedelta(minutes=5))
    missing = 0
    if interval == "1m":
        for previous, current in zip(rows, rows[1:]):
            if previous["event_at"].date() == current["event_at"].date():
                missing += max(0, int((current["event_at"] - previous["event_at"]).total_seconds() // 60) - 1)
    return {
        "source": source,
        "indicator": normalized,
        "interval": interval,
        "row_count": len(rows),
        "first_event_at": rows[0]["event_at"] if rows else None,
        "last_event_at": rows[-1]["event_at"] if rows else None,
        "delayed_over_5m": delayed,
        "missing_intervals_between_rows": missing,
        "zero_volume_rows": sum(1 for row in rows if row["volume"] == 0),
    }
