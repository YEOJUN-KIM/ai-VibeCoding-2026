"""api / market"""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from time import monotonic
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from ..auth import AuthenticatedUser, require_user
from ..models import LiveCandidateList, LiveCompanyProfile, LiveStockDetail, LiveStockSearchPage
from ..toss import TossApiError
from ..favorites import favorite_symbols
from ..dart import dart_client
from fastapi import APIRouter
from .. import runtime
router = APIRouter()


@router.get("/live/candidates", response_model=LiveCandidateList)
def live_candidates(_: AuthenticatedUser = Depends(require_user)) -> LiveCandidateList:
    try:
        return runtime.toss_client.affordable_domestic_candidates()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/live/stocks/search", response_model=LiveStockSearchPage)
def live_stock_search(
    q: str = Query(min_length=1, max_length=50),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=8, ge=5, le=10),
    user: AuthenticatedUser = Depends(require_user),
) -> LiveStockSearchPage:
    try:
        result = runtime.toss_client.search_domestic_stocks(q, page, page_size, include_quotes=False)
        saved = favorite_symbols(user.id)
        return result.model_copy(update={
            "results": [item.model_copy(update={"is_favorite": item.symbol in saved})
                        for item in result.results]
        })
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/live/stocks/list", response_model=LiveStockSearchPage)
def live_stock_list(
    q: str = Query(default="", max_length=50),
    market: str = Query(default="ALL", pattern=r"^(ALL|KOSPI|KOSDAQ)$"),
    security_type: str = Query(default="ALL", pattern=r"^(ALL|COMMON|STOCK|ETF|ETN)$"),
    sort: str = Query(default="POPULAR", pattern=r"^(POPULAR|NAME|CODE)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=10, le=100),
    include_quotes: bool = Query(default=True),
    user: AuthenticatedUser = Depends(require_user),
) -> LiveStockSearchPage:
    try:
        result = runtime.toss_client.list_domestic_stocks(
            query=q, market=market, security_type=security_type,
            sort=sort, page=page, page_size=page_size, include_quotes=include_quotes,
        )
        saved = favorite_symbols(user.id)
        return result.model_copy(update={
            "results": [item.model_copy(update={"is_favorite": item.symbol in saved})
                        for item in result.results]
        })
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/live/stocks/sparklines", response_model=dict[str, list[Decimal]])
def live_stock_sparklines(
    symbols: str = Query(min_length=1, max_length=419),
    period: str = Query(default="1D", pattern="^(1D|1W|1M|3M|1Y)$"),
    _: AuthenticatedUser = Depends(require_user),
) -> dict[str, list[Decimal]]:
    requested = list(dict.fromkeys(item.strip().upper() for item in symbols.split(",") if item.strip()))
    if not requested or len(requested) > 20 or any(
        len(item) > 12 or not item.replace("-", "").isalnum() for item in requested
    ):
        raise HTTPException(status_code=422, detail="종목코드는 한 번에 20개까지 조회할 수 있습니다.")
    try:
        return runtime.toss_client.domestic_sparklines(requested, period=period)
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/live/stocks/stream")
async def live_market_stream(request: Request, symbols: str = Query(max_length=699),
                             _: AuthenticatedUser = Depends(require_user)):
    codes = list(dict.fromkeys(symbols.split(",")))
    if not 1 <= len(codes) <= 100 or any(len(code) != 6 or not code.isalnum() for code in codes):
        raise HTTPException(status_code=422, detail="국내 종목 코드를 최대 100개까지 지정하세요.")

    async def events():
        subscriptions = []
        try:
            for code in codes:
                try:
                    subscriptions.append((code, runtime.quote_stream.subscribe(code)))
                except ValueError:
                    yield 'data: {"type":"status","state":"rejected"}\n\n'
                    return
            last_auth = 0
            last_send = monotonic()
            while not await request.is_disconnected():
                if monotonic() - last_auth >= 15:
                    try:
                        await asyncio.to_thread(require_user, request)
                    except HTTPException:
                        yield 'data: {"type":"status","state":"unauthorized"}\n\n'
                        return
                    last_auth = monotonic()
                # Each symbol retains its own latest frame, so a busy stock
                # cannot crowd quieter stocks out of this batch.
                frames = []
                for code, queue in subscriptions:
                    if not queue.empty():
                        frame = dict(queue.get_nowait())
                        frame["symbol"] = code
                        frames.append(frame)
                if frames:
                    yield "data: " + json.dumps({"type": "batch", "frames": frames}) + "\n\n"
                    last_send = monotonic()
                elif monotonic() - last_send >= 5:
                    yield ": keepalive\n\n"
                    last_send = monotonic()
                await asyncio.sleep(.15)
        finally:
            for code, queue in subscriptions:
                await runtime.quote_stream.unsubscribe(code, queue)
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/live/stocks/{symbol}/stream")
async def live_quote_stream(symbol: str, request: Request, _: AuthenticatedUser = Depends(require_user)):
    if len(symbol) != 6 or not symbol.isalnum():
        raise HTTPException(status_code=422, detail="국내 종목 코드를 확인하세요.")
    async def events():
        try:
            queue = runtime.quote_stream.subscribe(symbol)
        except ValueError:
            yield 'data: {"type":"status","state":"rejected"}\n\n'
            return
        try:
            last_auth = 0
            while not await request.is_disconnected():
                if monotonic() - last_auth >= 15:
                    try:
                        await asyncio.to_thread(require_user, request)
                    except HTTPException:
                        yield 'data: {"type":"status","state":"unauthorized"}\n\n'
                        return
                    last_auth = monotonic()
                try:
                    frame = await asyncio.wait_for(queue.get(), 5)
                    yield "data: " + json.dumps(frame) + "\n\n"
                    # Coalesce very busy tick streams for the browser, never claim
                    # these lossy ticks reconstruct exact candle volume.
                    await asyncio.sleep(.15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await runtime.quote_stream.unsubscribe(symbol, queue)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})


@router.get("/live/stocks/{symbol}/detail", response_model=LiveStockDetail)
def live_stock_detail(
    symbol: str,
    refresh: bool = False,
    period: str = Query(default="1D", pattern="^(1D|1W|1M|3M|1Y|3Y|5Y|10Y)$"),
    candle_interval: str | None = Query(default=None, pattern="^(1m|1h|1d|1w|1mo|1y)$"),
    _: AuthenticatedUser = Depends(require_user),
) -> LiveStockDetail:
    try:
        return runtime.toss_client.domestic_stock_detail(symbol, period=period, refresh=refresh, candle_interval=candle_interval)
    except TossApiError as exc:
        status_code = 404 if exc.status_code == 404 else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/live/stocks/{symbol}/company", response_model=LiveCompanyProfile)
def live_stock_company(
    symbol: str,
    _: AuthenticatedUser = Depends(require_user),
) -> LiveCompanyProfile:
    return dart_client.company_profile(symbol)


@router.get('/market-hours')
async def read_market_hours(user: AuthenticatedUser = Depends(require_user)):
    client = runtime.paper_state().client
    kr, us = await asyncio.gather(
        asyncio.to_thread(runtime.market_hours.status, 'KR', client),
        asyncio.to_thread(runtime.market_hours.status, 'US', client),
    )
    return {'KR': kr, 'US': us, 'checked_at': datetime.now(timezone.utc).isoformat()}
