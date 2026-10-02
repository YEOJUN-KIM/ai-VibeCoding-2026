"""Background collection of completed one-minute candles during Korean market hours."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..toss import TossClient
from .collect_candles import collect_candles
from .collect_market_indicators import collect_market_indicators


KST = ZoneInfo("Asia/Seoul")
FIRST_COLLECTION = time(9, 1, 2)
LAST_COLLECTION = time(15, 31, 2)
logger = logging.getLogger(__name__)


def next_collection_at(now: datetime) -> datetime:
    """Return the next weekday minute boundary used for KRX candle collection."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now에는 시간대 정보가 필요합니다.")
    candidate = now.astimezone(KST).replace(second=2, microsecond=0)
    if now.astimezone(KST) >= candidate:
        candidate += timedelta(minutes=1)

    while True:
        if candidate.weekday() >= 5:
            candidate = (candidate + timedelta(days=1)).replace(
                hour=FIRST_COLLECTION.hour,
                minute=FIRST_COLLECTION.minute,
                second=FIRST_COLLECTION.second,
                microsecond=0,
            )
            continue
        if candidate.time() < FIRST_COLLECTION:
            return candidate.replace(
                hour=FIRST_COLLECTION.hour,
                minute=FIRST_COLLECTION.minute,
                second=FIRST_COLLECTION.second,
                microsecond=0,
            )
        if candidate.time() <= LAST_COLLECTION:
            return candidate
        candidate = (candidate + timedelta(days=1)).replace(
            hour=FIRST_COLLECTION.hour,
            minute=FIRST_COLLECTION.minute,
            second=FIRST_COLLECTION.second,
            microsecond=0,
        )


class CandleCollectionWorker:
    def __init__(
        self,
        client: TossClient,
        symbols: tuple[str, ...],
        *,
        market_indicators: tuple[str, ...] = ("KOSPI", "KOSDAQ"),
        count: int = 20,
        enabled: bool = True,
    ) -> None:
        self.client = client
        self.symbols = tuple(
            dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip())
        )
        self.market_indicators = tuple(
            dict.fromkeys(item.strip().upper() for item in market_indicators if item.strip())
        )
        self.count = count
        self.enabled = enabled
        self._task: asyncio.Task | None = None
        self._status = {
            "enabled": enabled,
            "running": False,
            "symbols": list(self.symbols),
            "market_indicators": list(self.market_indicators),
            "count": count,
            "phase": "idle" if enabled else "disabled",
            "next_run_at": None,
            "last_started_at": None,
            "last_finished_at": None,
            "last_run_id": None,
            "last_market_indicator_run_id": None,
            "last_status": None,
            "last_inserted_rows": 0,
            "last_duplicate_rows": 0,
            "last_errors": {},
            "total_inserted_rows": 0,
            "message": "정기 수집 대기" if enabled else "정기 수집 비활성화",
        }

    def status(self) -> dict:
        return dict(self._status)

    async def start(self) -> None:
        if not self.enabled or not self.client.configured or not self.symbols:
            self._status.update(
                phase="disabled",
                message="설정, 토스 인증 정보 또는 수집 종목이 없어 정기 수집이 비활성화됐습니다.",
            )
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="ml-candle-collection")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._status.update(
            running=False, phase="stopped", next_run_at=None, message="정기 수집 중지"
        )

    async def run_once(self, collected_at: datetime | None = None) -> dict:
        started_at = collected_at or datetime.now(timezone.utc)
        self._status.update(
            running=True,
            phase="collecting",
            last_started_at=started_at,
            message="완료 1분봉 수집 중",
        )
        try:
            _, market_open_today = await asyncio.to_thread(
                self.client.kr_market_open_today
            )
            if not market_open_today:
                result = {
                    "run_id": None,
                    "status": "SKIPPED",
                    "inserted_rows": 0,
                    "duplicate_rows": 0,
                    "errors": {},
                }
                self._status.update(
                    last_status="SKIPPED",
                    last_inserted_rows=0,
                    last_duplicate_rows=0,
                    last_errors={},
                    message="국내 증시 휴장일이라 정기 수집을 건너뛰었습니다.",
                )
                return result
            stock_result = await asyncio.to_thread(
                collect_candles,
                self.client,
                list(self.symbols),
                count=self.count,
                collected_at=started_at,
                cache_seconds=5,
            )
            indicator_result = await asyncio.to_thread(
                collect_market_indicators,
                self.client,
                list(self.market_indicators),
                count=self.count,
                collected_at=started_at,
                cache_seconds=5,
            )
            statuses = {stock_result["status"], indicator_result["status"]}
            if statuses == {"COMPLETED"}:
                combined_status = "COMPLETED"
            elif "FAILED" in statuses and len(statuses) == 1:
                combined_status = "FAILED"
            else:
                combined_status = "PARTIAL"
            result = {
                **stock_result,
                "status": combined_status,
                "inserted_rows": stock_result["inserted_rows"] + indicator_result["inserted_rows"],
                "duplicate_rows": stock_result["duplicate_rows"] + indicator_result["duplicate_rows"],
                "errors": {
                    **{f"stock:{key}": value for key, value in stock_result["errors"].items()},
                    **{f"indicator:{key}": value for key, value in indicator_result["errors"].items()},
                },
                "market_indicators": indicator_result,
            }
            self._status.update(
                last_run_id=result["run_id"],
                last_market_indicator_run_id=indicator_result["run_id"],
                last_status=result["status"],
                last_inserted_rows=result["inserted_rows"],
                last_duplicate_rows=result["duplicate_rows"],
                last_errors=result["errors"],
                total_inserted_rows=(
                    self._status["total_inserted_rows"] + result["inserted_rows"]
                ),
                message=(
                    f"종목·지수 수집 {result['status']} · 신규 {result['inserted_rows']}건 · "
                    f"중복 {result['duplicate_rows']}건 · 오류 {len(result['errors'])}종목"
                ),
            )
            return result
        except Exception as exc:
            self._status.update(
                last_status="FAILED",
                last_errors={"worker": str(exc)},
                message="정기 수집 실행에 실패했습니다.",
            )
            logger.warning("ML candle collection failed: %s", exc)
            return {"status": "FAILED", "errors": {"worker": str(exc)}}
        finally:
            self._status.update(
                running=False,
                phase="waiting",
                last_finished_at=datetime.now(timezone.utc),
            )

    async def _run(self) -> None:
        while True:
            scheduled_at = next_collection_at(datetime.now(timezone.utc))
            self._status.update(
                phase="waiting",
                next_run_at=scheduled_at,
                message=f"다음 정기 수집 {scheduled_at.astimezone(KST):%Y-%m-%d %H:%M:%S}",
            )
            delay = max(
                0, (scheduled_at - datetime.now(scheduled_at.tzinfo)).total_seconds()
            )
            await asyncio.sleep(delay)
            await self.run_once(datetime.now(timezone.utc))
