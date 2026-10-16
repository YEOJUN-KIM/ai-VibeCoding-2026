"""api / research"""

import asyncio
from fastapi import Depends, HTTPException, Query
from ..auth import AuthenticatedUser, require_csrf, require_user
from ..models import LongTermAnalysis, LongTermWatchCandidate, LongTermWatchNoteWrite, NewsDigest
from ..toss import TossApiError
from ..news import NewsFeedError, news_service
from ..ai_news import ai_news_service
from ..long_term_repository import add_long_term_watch, fresh_long_term_analysis, list_long_term_watch, save_long_term_watch_note, ranked_long_term_analyses, remove_long_term_watch
from fastapi import APIRouter
from fastapi import Request
from .. import runtime
from ..services import research as research_service
from ..services.research import build_and_save_long_term_analysis, refresh_long_term_candidates
router = APIRouter()


@router.get("/research/long-term/candidates", response_model=list[LongTermAnalysis])
def long_term_candidates(
    limit: int = Query(default=5, ge=1, le=12),
    user: AuthenticatedUser = Depends(require_user),
) -> list[LongTermAnalysis]:
    return ranked_long_term_analyses(limit, user.id)


@router.get("/research/long-term/candidate-status")
def long_term_candidate_status(_: AuthenticatedUser = Depends(require_user)) -> dict:
    return dict(research_service._long_term_scan_status)


@router.post("/research/long-term/candidate-refresh", status_code=202)
async def start_long_term_candidate_refresh(
    request: Request,
    _: AuthenticatedUser = Depends(require_csrf),
) -> dict:
    if research_service._long_term_scan_status["running"] or research_service._long_term_scan_lock.locked():
        raise HTTPException(status_code=409, detail="장기 관찰 후보를 이미 분석하고 있습니다.")
    if not runtime.toss_client.configured:
        raise HTTPException(status_code=409, detail="토스 API 연결 후 후보 분석을 시작할 수 있습니다.")
    research_service._long_term_scan_status.update(
        running=True, completed=0, target=0, selected=0, excluded=0, failed=0,
            skipped=0, phase="running", current_name=None, last_finished_at=None,
        message="수동 후보 분석을 준비하고 있습니다.",
    )
    task = asyncio.create_task(refresh_long_term_candidates(force=True))
    request.app.state.long_term_manual_refresh_task = task
    return dict(research_service._long_term_scan_status)


@router.get("/research/long-term/watchlist-status")
def long_term_watchlist_status(_: AuthenticatedUser = Depends(require_user)) -> dict:
    return {**research_service._long_term_watch_status, "next_run_at": research_service._long_term_scan_status["next_run_at"]}


@router.get("/research/long-term/watchlist", response_model=list[LongTermWatchCandidate])
def long_term_watchlist(user: AuthenticatedUser = Depends(require_user)) -> list[LongTermWatchCandidate]:
    return list_long_term_watch(user.id)


@router.put('/research/long-term/watchlist/{symbol}/note')
def write_long_term_watch_note(symbol: str, payload: LongTermWatchNoteWrite,
                              user: AuthenticatedUser = Depends(require_csrf)):
    code = symbol.strip().zfill(6)
    if not save_long_term_watch_note(user.id, code, payload.note.strip()):
        raise HTTPException(status_code=404, detail='내 관찰 목록에 있는 기업만 메모할 수 있습니다.')
    return {'saved': True}


@router.post("/research/long-term/watchlist/{symbol}")
def add_long_term_watchlist_symbol(
    symbol: str, user: AuthenticatedUser = Depends(require_csrf),
) -> dict[str, bool]:
    stock = runtime.toss_client.domestic_stock(symbol)
    if stock is None:
        raise HTTPException(status_code=404, detail="국내 종목을 찾지 못했습니다.")
    if str(stock.get("securityType", "")) != "STOCK" or not bool(stock.get("isCommonShare", False)):
        raise HTTPException(status_code=409, detail="장기 관찰 후보에는 국내 보통주만 추가할 수 있습니다.")
    add_long_term_watch(
        user.id, str(stock.get("symbol", symbol)), str(stock.get("name", symbol)),
        str(stock.get("market", "KRX")),
    )
    return {"added": True}


@router.delete("/research/long-term/watchlist/{symbol}")
def remove_long_term_watchlist_symbol(
    symbol: str, user: AuthenticatedUser = Depends(require_csrf),
) -> dict[str, bool]:
    remove_long_term_watch(user.id, symbol.strip().zfill(6))
    return {"removed": True}


@router.get("/research/long-term/{symbol}", response_model=LongTermAnalysis)
async def long_term_analysis(
    symbol: str, _: AuthenticatedUser = Depends(require_user),
    refresh: bool = False,
) -> LongTermAnalysis:
    try:
        cached = None if refresh else await asyncio.to_thread(fresh_long_term_analysis, symbol)
        if cached is not None:
            return cached
        return await build_and_save_long_term_analysis(symbol)
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/research/news", response_model=NewsDigest)
def research_news(
    q: str | None = Query(default=None, min_length=1, max_length=60),
    limit: int = Query(default=12, ge=3, le=20),
    _: AuthenticatedUser = Depends(require_user),
) -> NewsDigest:
    try:
        if q is not None:
            return ai_news_service.enrich(news_service.search(q, limit=limit))
        digest = news_service.search("(주식 OR 증시 OR 코스피 OR 코스닥) when:1d", limit=limit)
        digest = digest.model_copy(update={"query": "오늘의 주요 증시 이슈"})
        return ai_news_service.enrich(digest)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NewsFeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
