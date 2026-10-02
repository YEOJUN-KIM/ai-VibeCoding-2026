"""Non-blocking persistence for PAPER strategy decisions."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from threading import Lock
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..database import connect


VALID_ACTIONS = {
    "DATA_WAIT",
    "WAIT",
    "BUY_FILLED",
    "BUY_BLOCKED",
    "SELL_FILLED",
    "SELL_BLOCKED",
}
logger = logging.getLogger(__name__)


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}에는 시간대 정보가 필요합니다.")


def validate_decision(row: dict[str, Any]) -> None:
    required = {
        "run_id",
        "account_name",
        "data_source",
        "symbol",
        "decision_at",
        "action",
        "reason",
        "price",
        "history_count",
        "entry_armed",
        "confirmation_count",
        "strategy_quantity",
        "cash",
        "total_asset",
    }
    missing = sorted(required - row.keys())
    if missing:
        raise ValueError(f"판단 데이터 필드가 없습니다: {', '.join(missing)}")
    if row["action"] not in VALID_ACTIONS:
        raise ValueError("지원하지 않는 전략 판단 action입니다.")
    if row["data_source"] not in {"TOSS", "SIMULATED"}:
        raise ValueError("data_source는 TOSS 또는 SIMULATED여야 합니다.")
    _aware(row["decision_at"], "decision_at")
    if row.get("available_at") is not None:
        _aware(row["available_at"], "available_at")
        if row["available_at"] < row["decision_at"]:
            raise ValueError("available_at은 decision_at보다 빠를 수 없습니다.")
    if row.get("candle_event_at") is not None:
        _aware(row["candle_event_at"], "candle_event_at")
    for field in ("price", "cash", "total_asset"):
        value = Decimal(row[field])
        if not value.is_finite() or value < 0 or (field == "price" and value == 0):
            raise ValueError(f"{field} 값이 올바르지 않습니다.")
    for field in ("history_count", "confirmation_count", "strategy_quantity"):
        if int(row[field]) < 0:
            raise ValueError(f"{field}는 0 이상이어야 합니다.")


def save_strategy_decisions(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    prepared = [dict(row) for row in rows]
    for row in prepared:
        row.setdefault("decision_key", str(uuid4()))
        validate_decision(row)
    collected_at = datetime.now(timezone.utc)
    inserted = 0
    with connect() as conn:
        for row in prepared:
            decision_at = row["decision_at"]
            available_at = row.get("available_at", decision_at)
            saved = conn.execute(
                """
                INSERT INTO ml_strategy_decisions(
                    decision_key,run_id,account_id,account_name,strategy_id,strategy_name,data_source,
                    symbol,candle_event_at,decision_at,available_at,collected_at,
                    action,reason,price,short_average,long_average,trend,previous_trend,
                    history_count,entry_armed,confirmation_count,strategy_quantity,
                    cash,total_asset,metadata
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                ON CONFLICT (decision_key) DO NOTHING
                RETURNING 1
                """,
                (
                    row["decision_key"],
                    row["run_id"],
                    row.get("account_id"),
                    row["account_name"],
                    row.get("strategy_id"),
                    row.get("strategy_name"),
                    row["data_source"],
                    row["symbol"],
                    row.get("candle_event_at"),
                    decision_at,
                    available_at,
                    max(collected_at, available_at),
                    row["action"],
                    row["reason"],
                    row["price"],
                    row.get("short_average"),
                    row.get("long_average"),
                    row.get("trend"),
                    row.get("previous_trend"),
                    row["history_count"],
                    row["entry_armed"],
                    row["confirmation_count"],
                    row["strategy_quantity"],
                    row["cash"],
                    row["total_asset"],
                    Jsonb(row.get("metadata", {})),
                ),
            ).fetchone()
            inserted += int(saved is not None)
    return inserted


class StrategyDecisionRecorder:
    """Queue strategy decisions so a database delay cannot block trading."""

    def __init__(self, *, flush_seconds: float = 1.0, batch_size: int = 200, max_queue: int = 10000):
        self.flush_seconds = flush_seconds
        self.batch_size = batch_size
        self.max_queue = max_queue
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = Lock()
        self._task: asyncio.Task | None = None
        self._saved = 0
        self._dropped = 0
        self._failures = 0
        self._last_error: str | None = None
        self._last_saved_at: datetime | None = None

    def record(self, row: dict[str, Any]) -> None:
        queued_row = dict(row)
        queued_row.setdefault("decision_key", str(uuid4()))
        try:
            validate_decision(queued_row)
        except (ValueError, TypeError, ArithmeticError) as exc:
            self._dropped += 1
            self._last_error = str(exc)
            logger.warning("Invalid ML strategy decision dropped: %s", exc)
            return
        with self._lock:
            if len(self._queue) >= self.max_queue:
                self._dropped += 1
                self._last_error = "판단 기록 큐가 가득 찼습니다."
                return
            self._queue.append(queued_row)

    def _take_batch(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._queue.popleft() for _ in range(min(self.batch_size, len(self._queue)))]

    def _restore_batch(self, batch: list[dict[str, Any]]) -> None:
        with self._lock:
            for row in reversed(batch):
                self._queue.appendleft(row)

    async def flush(self) -> int:
        batch = self._take_batch()
        if not batch:
            return 0
        try:
            saved = await asyncio.to_thread(save_strategy_decisions, batch)
        except Exception as exc:
            self._restore_batch(batch)
            self._failures += 1
            self._last_error = str(exc)
            logger.warning("ML strategy decision flush failed: %s", exc)
            return 0
        self._saved += saved
        self._last_saved_at = datetime.now(timezone.utc)
        self._last_error = None
        return saved

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="ml-strategy-decisions")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        while self._queue:
            saved = await self.flush()
            if not saved:
                break

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.flush_seconds)
            await self.flush()

    def status(self) -> dict[str, Any]:
        with self._lock:
            queued = len(self._queue)
        return {
            "running": self._task is not None and not self._task.done(),
            "queued": queued,
            "saved": self._saved,
            "dropped": self._dropped,
            "failures": self._failures,
            "last_error": self._last_error,
            "last_saved_at": self._last_saved_at,
        }
