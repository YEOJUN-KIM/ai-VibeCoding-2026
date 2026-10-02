"""Collect exchange-rate and U.S. daily context on a fixed Korean schedule."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..toss import TossClient
from .macro_pipeline import collect_macro_context


KST = ZoneInfo("Asia/Seoul")
COLLECTION_TIMES = ((8, 10), (13, 10))
logger = logging.getLogger(__name__)


def next_macro_collection_at(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now에는 시간대 정보가 필요합니다.")
    local_now = now.astimezone(KST)
    for day_offset in range(8):
        day = local_now + timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for hour, minute in COLLECTION_TIMES:
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > local_now:
                return candidate
    raise RuntimeError("다음 거시 지표 수집 시각을 계산하지 못했습니다.")


class MacroCollectionWorker:
    def __init__(self, client: TossClient, *, enabled: bool = True, days: int = 14) -> None:
        self.client = client
        self.enabled = enabled
        self.days = days
        self._task: asyncio.Task | None = None
        self._status = {
            "enabled": enabled,
            "running": False,
            "phase": "idle" if enabled else "disabled",
            "next_run_at": None,
            "last_run_id": None,
            "last_status": None,
            "last_inserted_rows": 0,
            "last_duplicate_rows": 0,
            "last_errors": {},
            "last_finished_at": None,
            "message": "거시 지표 수집 대기" if enabled else "거시 지표 수집 비활성화",
        }

    def status(self) -> dict:
        return dict(self._status)

    async def start(self) -> None:
        if not self.enabled or not self.client.configured:
            self._status.update(
                phase="disabled",
                message="수집 설정 또는 토스 인증 정보가 없어 거시 지표 수집이 비활성화됐습니다.",
            )
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="ml-macro-collection")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._status.update(running=False, phase="stopped", next_run_at=None)

    async def run_once(self) -> dict:
        self._status.update(running=True, phase="collecting", message="환율·미국 지표 수집 중")
        try:
            result = await asyncio.to_thread(
                collect_macro_context, self.client, days=self.days
            )
            self._status.update(
                last_run_id=result["run_id"],
                last_status=result["status"],
                last_inserted_rows=result["inserted_rows"],
                last_duplicate_rows=result["duplicate_rows"],
                last_errors=result["errors"],
                message=(
                    f"거시 지표 {result['status']} · 신규 {result['inserted_rows']}건 · "
                    f"중복 {result['duplicate_rows']}건 · 오류 {len(result['errors'])}건"
                ),
            )
            return result
        except Exception as exc:
            self._status.update(
                last_status="FAILED",
                last_errors={"worker": str(exc)},
                message="거시 지표 수집 실패",
            )
            logger.warning("ML macro collection failed: %s", exc)
            return {"status": "FAILED", "errors": {"worker": str(exc)}}
        finally:
            self._status.update(
                running=False,
                phase="waiting",
                last_finished_at=datetime.now(timezone.utc),
            )

    async def _run(self) -> None:
        await self.run_once()
        while True:
            scheduled_at = next_macro_collection_at(datetime.now(timezone.utc))
            self._status.update(
                phase="waiting",
                next_run_at=scheduled_at,
                message=f"다음 거시 지표 수집 {scheduled_at:%Y-%m-%d %H:%M:%S}",
            )
            await asyncio.sleep(
                max(0, (scheduled_at - datetime.now(scheduled_at.tzinfo)).total_seconds())
            )
            await self.run_once()
