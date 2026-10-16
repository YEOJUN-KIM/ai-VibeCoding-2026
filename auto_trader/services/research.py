"""services / research"""

import asyncio
from datetime import datetime, timezone
from fastapi import HTTPException
from ..models import LongTermAnalysis
from ..settings import settings
from ..toss import TossApiError
from ..dart import dart_client
from ..long_term import analyze_long_term, long_term_recommendation_assessment, next_daily_scan_at
from ..long_term_repository import fresh_long_term_analysis, long_term_watched_symbols, replace_long_term_recommendations, save_long_term_analysis
import logging
from .. import runtime
logger = logging.getLogger(__name__)


_long_term_scan_status = {
    "running": False, "completed": 0, "target": 0,
    "selected": 0, "excluded": 0, "failed": 0, "skipped": 0,
    "phase": "idle", "current_name": None,
    "last_started_at": None, "last_finished_at": None, "next_run_at": None,
    "message": "분석 대기 중",
}


_long_term_scan_lock = asyncio.Lock()


_long_term_watch_status = {
    "running": False, "completed": 0, "target": 0, "failed": 0,
    "last_finished_at": None, "message": "내 후보 자동 분석 대기",
}


async def refresh_long_term_candidates(
    candidate_count: int = 20, max_count: int = 10, *, force: bool = False,
) -> None:
    """거래대금 상위 보통주를 검증해 품질 조건을 통과한 추천을 저장한다."""
    status = _long_term_scan_status
    if _long_term_scan_lock.locked():
        return
    async with _long_term_scan_lock:
        status.update(
            running=True, completed=0, target=0, selected=0, excluded=0, failed=0,
            skipped=0, phase="running", current_name=None, last_finished_at=None,
            last_started_at=datetime.now(timezone.utc),
            message="거래대금 상위 보통주를 분석하고 있습니다.",
        )
        try:
            page = await asyncio.to_thread(
                runtime.toss_client.list_domestic_stocks,
                query="", market="ALL", security_type="COMMON", sort="POPULAR",
                page=1, page_size=candidate_count,
            )
            candidates = [item for item in page.results if item.security_type == "STOCK" and item.is_common_share]
            watched = await asyncio.to_thread(long_term_watched_symbols)
            status["target"] = len(candidates)
            eligible: list[LongTermAnalysis] = []
            processed = 0
            excluded = 0
            failed = 0
            skipped = 0
            for candidate in candidates:
                status.update(current_name=candidate.name, phase="running")
                if candidate.symbol in watched:
                    skipped += 1
                    processed += 1
                    status.update(completed=processed, skipped=skipped)
                    continue
                analysis = None if force else await asyncio.to_thread(fresh_long_term_analysis, candidate.symbol)
                if analysis is None:
                    for attempt in range(3):
                        try:
                            detail, company = await asyncio.gather(
                                asyncio.to_thread(runtime.toss_client.domestic_stock_detail, candidate.symbol, "1Y"),
                                asyncio.to_thread(dart_client.company_profile, candidate.symbol),
                            )
                            analysis = analyze_long_term(detail, company)
                            await asyncio.to_thread(save_long_term_analysis, analysis)
                            break
                        except TossApiError as exc:
                            if exc.status_code == 429 and attempt < 2:
                                status.update(phase="waiting", message=f"{candidate.name}: API 요청 한도 대기 · 잠시 후 이어서 분석합니다.")
                                await asyncio.sleep(15)
                                continue
                            failed += 1
                            logger.warning("Long-term candidate analysis failed for %s: %s", candidate.symbol, exc)
                            break
                        except (ValueError, TypeError, ArithmeticError) as exc:
                            failed += 1
                            logger.warning("Long-term candidate analysis failed for %s: %s", candidate.symbol, exc)
                            break
                processed += 1
                if analysis is not None:
                    recommended, _ = long_term_recommendation_assessment(analysis)
                    if recommended:
                        eligible.append(analysis)
                    else:
                        excluded += 1
                status.update(completed=processed, selected=min(len(eligible), max_count), excluded=excluded, failed=failed, phase="running")
                status["message"] = (
                    f"후보 {processed}개 검토 · 추천 기준 통과 {min(len(eligible), max_count)}개"
                )
            selected = sorted(
                eligible,
                key=lambda item: (item.overall_score or -1, item.generated_at),
                reverse=True,
            )[:max_count]
            if selected:
                await asyncio.to_thread(
                    replace_long_term_recommendations, [item.symbol for item in selected]
                )
                message = f"{processed}개를 검토해 장기 관찰 후보 {len(selected)}개를 선별했습니다."
            else:
                message = "추천 기준을 통과한 새 후보가 없어 기존 목록을 유지했습니다."
            if failed:
                message += f" 조회 실패 {failed}개는 이번 선별에서 제외되었습니다. 다시 분석해 주세요."
            status.update(
                running=False, selected=len(selected), excluded=excluded,
                phase="partial" if failed else "complete", current_name=None,
                last_finished_at=datetime.now(timezone.utc), message=message,
            )
            logger.info("Long-term scan finished: reviewed=%s/%s excluded=%s skipped=%s failed=%s selected=%s",
                        processed, status["target"], excluded, skipped, failed, len(selected))
        except asyncio.CancelledError:
            status.update(running=False, phase="cancelled", current_name=None,
                          last_finished_at=datetime.now(timezone.utc), message="장기 관찰 후보 분석이 중지되었습니다.")
            raise
        except Exception as exc:
            status.update(running=False, phase="failed", current_name=None, last_finished_at=datetime.now(timezone.utc), message="장기 관찰 후보 분석을 완료하지 못했습니다. 다시 분석해 주세요.")
            logger.warning("Long-term candidate refresh failed: %s", exc)


async def refresh_long_term_watch_candidates() -> None:
    status = _long_term_watch_status
    status.update(running=True, completed=0, target=0, failed=0, message="내 후보 자동 분석 중")
    try:
        symbols = sorted(await asyncio.to_thread(long_term_watched_symbols))
        status["target"] = len(symbols)
        for symbol in symbols:
            status["message"] = f"내 후보 자동 분석 중 · {status['completed']}/{len(symbols)}개 · {symbol}"
            for attempt in range(3):
                try:
                    await build_and_save_long_term_analysis(symbol)
                    break
                except TossApiError as exc:
                    if exc.status_code == 429 and attempt < 2:
                        status["message"] = f"내 후보 자동 분석 · {symbol} 요청 한도 대기"
                        await asyncio.sleep(15)
                        continue
                    status["failed"] += 1
                    logger.warning("Long-term watch analysis failed for %s: %s", symbol, exc)
                    break
                except Exception as exc:
                    status["failed"] += 1
                    logger.warning("Long-term watch analysis failed for %s: %s", symbol, exc)
                    break
            status["completed"] += 1
        status["message"] = (
            f"내 후보 자동 분석 {'종료 · 일부 조회 실패' if status['failed'] else '완료'} · "
            f"{status['completed']}/{status['target']}개 검토 · "
            f"갱신 {status['completed'] - status['failed']}개 · 실패 {status['failed']}개"
        )
    except asyncio.CancelledError:
        status["message"] = "내 후보 자동 분석이 중단되었습니다."
        raise
    except Exception as exc:
        status["message"] = "내 후보 자동 분석을 완료하지 못했습니다."
        logger.warning("Long-term watch refresh failed: %s", exc)
    finally:
        status.update(running=False, last_finished_at=datetime.now(timezone.utc))


async def long_term_candidate_worker(stock_directory_task: asyncio.Task | None) -> None:
    if stock_directory_task is not None:
        try:
            await stock_directory_task
        except (asyncio.CancelledError, Exception):
            if stock_directory_task.cancelled():
                raise asyncio.CancelledError
    while True:
        next_run = next_daily_scan_at(
            datetime.now(timezone.utc), settings.long_term_scan_hour, settings.long_term_scan_minute
        )
        _long_term_scan_status["next_run_at"] = next_run
        await asyncio.sleep(max(1, (next_run - datetime.now(next_run.tzinfo)).total_seconds()))
        await refresh_long_term_watch_candidates()
        await refresh_long_term_candidates(force=True)


async def build_and_save_long_term_analysis(symbol: str) -> LongTermAnalysis:
    detail, company = await asyncio.gather(
        asyncio.to_thread(runtime.toss_client.domestic_stock_detail, symbol, "1Y"),
        asyncio.to_thread(dart_client.company_profile, symbol),
    )
    if not detail.is_common_share or detail.security_type != "STOCK":
        raise HTTPException(status_code=409, detail="장기 재무분석은 국내 보통주를 대상으로 합니다.")
    analysis = analyze_long_term(detail, company)
    await asyncio.to_thread(save_long_term_analysis, analysis)
    return analysis
