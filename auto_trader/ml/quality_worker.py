"""Create a persisted ML data-quality report after the Korean market closes."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..toss import TossClient
from .quality_report import generate_quality_report, quality_report_for_date


KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)


def next_quality_report_at(now: datetime, hour: int, minute: int) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now에는 시간대 정보가 필요합니다.")
    local_now = now.astimezone(KST)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local_now >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


class QualityReportWorker:
    def __init__(
        self,
        client: TossClient,
        symbols: tuple[str, ...],
        indicators: tuple[str, ...],
        *,
        hour: int = 15,
        minute: int = 40,
        enabled: bool = True,
    ) -> None:
        self.client = client
        self.symbols = symbols
        self.indicators = indicators
        self.hour = hour
        self.minute = minute
        self.enabled = enabled
        self._task: asyncio.Task | None = None
        self._status = {
            "enabled": enabled,
            "running": False,
            "phase": "idle" if enabled else "disabled",
            "next_run_at": None,
            "last_report_date": None,
            "last_status": None,
            "last_generated_at": None,
            "last_error": None,
            "message": "품질 보고서 대기" if enabled else "품질 보고서 비활성화",
        }

    def status(self) -> dict:
        return dict(self._status)

    async def start(self) -> None:
        if not self.enabled or not self.client.configured:
            self._status.update(
                phase="disabled",
                message="수집 설정 또는 토스 인증 정보가 없어 품질 보고서가 비활성화됐습니다.",
            )
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="ml-quality-report")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._status.update(running=False, phase="stopped", next_run_at=None)

    async def run_once(self, report_date: date | None = None) -> dict:
        target_date = report_date or datetime.now(KST).date()
        self._status.update(running=True, phase="generating", message="품질 보고서 생성 중")
        try:
            _, market_open = await asyncio.to_thread(self.client.kr_market_open_today)
            if target_date == datetime.now(KST).date() and not market_open:
                result = {"report_date": target_date, "status": "SKIPPED", "issues": ["휴장일"]}
            else:
                result = await asyncio.to_thread(
                    generate_quality_report,
                    target_date,
                    symbols=self.symbols,
                    indicators=self.indicators,
                )
            self._status.update(
                last_report_date=target_date,
                last_status=result["status"],
                last_generated_at=datetime.now(timezone.utc),
                last_error=None,
                message=f"{target_date} 품질 보고서 {result['status']}",
            )
            return result
        except Exception as exc:
            self._status.update(
                last_status="FAILED",
                last_error=str(exc),
                message="품질 보고서 생성 실패",
            )
            logger.warning("ML quality report failed: %s", exc)
            return {"report_date": target_date, "status": "FAILED", "error": str(exc)}
        finally:
            self._status.update(running=False, phase="waiting")

    async def _run(self) -> None:
        local_now = datetime.now(KST)
        scheduled_today = local_now.replace(
            hour=self.hour, minute=self.minute, second=0, microsecond=0
        )
        if local_now.weekday() < 5 and local_now >= scheduled_today:
            existing = await asyncio.to_thread(quality_report_for_date, local_now.date())
            if existing is None:
                await self.run_once(local_now.date())

        while True:
            scheduled_at = next_quality_report_at(
                datetime.now(timezone.utc), self.hour, self.minute
            )
            self._status.update(
                phase="waiting",
                next_run_at=scheduled_at,
                message=f"다음 품질 보고서 {scheduled_at:%Y-%m-%d %H:%M:%S}",
            )
            await asyncio.sleep(
                max(0, (scheduled_at - datetime.now(scheduled_at.tzinfo)).total_seconds())
            )
            await self.run_once(scheduled_at.date())
