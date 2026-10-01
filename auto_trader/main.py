"""국내주식 모의 자동매매 FastAPI 애플리케이션."""

import asyncio
import logging
import json
from functools import wraps
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import monotonic

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import (
    SESSION_COOKIE,
    AuthenticatedUser,
    authenticate,
    delete_session,
    live_pin_status,
    lock_session,
    require_csrf,
    require_session,
    require_session_csrf,
    require_user,
    session_from_request,
    unlock_session,
    verify_live_pin,
)
from .models import (Account, DeletedOrderCount, FavoriteStockCreate, LiveBuyingPower, LiveCandidateList,
                     LiveDryRunConfirmRequest, LiveDryRunOrder, LiveFavoriteStock,
                     LiveOrderPreview, LiveOrderPreviewCheck, LiveOrderPreviewRequest,
                     LivePinRequest, LivePinStatus, LivePortfolio,
                     LiveCompanyProfile, LiveRealOrderConfirmRequest, LiveStockDetail, LongTermAnalysis,
                     LongTermWatchCandidate,
                     LiveStockSearchPage, LiveStrategy, LiveStrategyWrite, LiveTradingReadiness,
                     LoginRequest, NewsDigest, Order, OrderRequest, PaperWorkspaceStatus, PaperManagementUpdate,
                     Quote, RiskSettings, RiskSettingsUpdate, RiskStatus, SessionInfo, Stock,
                     StrategySettingsUpdate, StrategyStatus, TossConnectionStatus)
from .paper import PaperBroker
from .paper_feed import PaperPriceFeed
from .settings import settings
from .simulator import MarketSimulator
from .strategy import MovingAverageEngine
from .quote_stream import QuoteStream
from .risk import RiskManager
from .toss import TossApiError, TossClient
from .database import connect, initialize
from .favorites import add_favorite, favorite_symbols, list_favorites, remove_favorite
from .news import NewsFeedError, news_service
from .ai_news import ai_news_service
from .dart import dart_client
from .live_orders import (auto_position_quantities, cancel_dry_run_order, delete_today_dry_run_orders,
                          list_dry_run_orders, list_real_orders, prepare_real_order,
                          orders_for_reconciliation, real_order_for_user,
                          save_dry_run_order, update_real_order)
from .live_strategies import delete_strategy, list_strategies, save_strategy
from .strategy_presets import weekly_preset
from .long_term import (analyze_long_term, long_term_recommendation_assessment,
                        next_daily_scan_at)
from .long_term_repository import (add_long_term_watch, fresh_long_term_analysis,
                                   list_long_term_watch,
                                   long_term_watched_symbols,
                                   ranked_long_term_analyses, remove_long_term_watch,
                                   replace_long_term_recommendations,
                                   save_long_term_analysis)


market = MarketSimulator(symbols=settings.watch_symbols)
broker = PaperBroker(market, initial_cash=settings.paper_initial_cash,
                     fee_rate=settings.paper_fee_rate, sell_tax_rate=settings.paper_sell_tax_rate,
                     slippage_rate=settings.paper_slippage_rate,
                     journal_path=Path(__file__).parent.parent / ".paper-history" / "orders.jsonl",
                     ignore_min_cash_ratio=settings.paper_ignore_min_cash_ratio,
                     ignore_daily_order_limit=settings.paper_ignore_daily_order_limit)
engine = MovingAverageEngine(
    market,
    broker,
    interval_seconds=settings.strategy_interval_seconds,
    short_period=settings.strategy_short_period,
    long_period=settings.strategy_long_period,
    order_quantity=settings.order_quantity,
)
risk_manager = RiskManager(market)
toss_client = TossClient()
quote_stream = QuoteStream(toss_client, settings.toss_ws_url)
engine.price_feed = PaperPriceFeed(toss_client)
watchlist_source = "fallback"
paper_snapshot_at = None
paper_source_account_label = None
paper_selected_strategy: LiveStrategy | None = None
paper_account_mode = "LIVE_COPY"
_paper_sessions = {}
_paper_change_lock = asyncio.Lock()


def _serialize_paper_change(function):
    @wraps(function)
    async def serialized(*args, **kwargs):
        async with _paper_change_lock:
            return await function(*args, **kwargs)
    return serialized


async def _stop_all_paper_engines():
    workers = {engine, *(session[1] for session in _paper_sessions.values())}
    await asyncio.gather(*(worker.stop() for worker in workers))


static_dir = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)
_readiness_lock = Lock()
_readiness_cache: tuple[float, dict] | None = None
_long_term_scan_status = {
    "running": False, "completed": 0, "target": 0,
    "selected": 0, "excluded": 0, "failed": 0, "skipped": 0,
    "phase": "idle", "current_name": None,
    "last_started_at": None, "last_finished_at": None, "next_run_at": None,
    "message": "분석 대기 중",
}
_long_term_scan_lock = asyncio.Lock()


def _startup_readiness(*, force: bool = False) -> dict:
    """로그인 전에 DB와 토스 계좌 연결을 한 번 확인하고 짧게 캐시한다."""
    global _readiness_cache
    now = monotonic()
    with _readiness_lock:
        if not force and _readiness_cache is not None and now < _readiness_cache[0]:
            return _readiness_cache[1]

        try:
            with connect() as conn:
                conn.execute("SELECT 1")
            database_connected = True
            database_message = "PostgreSQL 연결 완료"
        except Exception as exc:
            database_connected = False
            database_message = "PostgreSQL에 연결하지 못했습니다."
            logger.warning("Startup database readiness check failed: %s", exc)

        toss_configured = toss_client.configured
        toss_connected = False
        if not toss_configured:
            toss_message = "토스 API 인증 정보가 설정되지 않았습니다."
        else:
            try:
                result = toss_client.test_connection()
                toss_connected = result.connected
                toss_message = result.message
            except TossApiError as exc:
                toss_message = str(exc)
                logger.warning("Startup Toss API readiness check failed: %s", exc)

        ready = database_connected and toss_connected
        payload = {
            "ready": ready,
            "database": {"connected": database_connected, "message": database_message},
            "toss_api": {
                "configured": toss_configured,
                "connected": toss_connected,
                "message": toss_message,
            },
        }
        _readiness_cache = (monotonic() + (30 if ready else 5), payload)
        return payload


def _local_order_status(broker_status: str, fallback: str) -> str:
    supported = {"FILLED", "PARTIAL_FILLED", "PENDING", "PENDING_CANCEL", "CANCELED", "REJECTED"}
    return broker_status if broker_status in supported else fallback


def reconcile_saved_real_orders(*, active_only: bool = False) -> int:
    account = toss_client.selected_account()
    orders = orders_for_reconciliation(str(account["accountSeq"]), active_only=active_only)
    reconciled = 0
    history_cache: dict[str, dict[str, dict]] = {}
    for order in orders:
        if not order.external_order_id:
            if order.reconciliation_status != "NEEDS_REVIEW":
                update_real_order(
                    order.user_id, order.id,
                    status=order.status, message="토스 주문 ID가 없어 자동 대조할 수 없습니다.",
                    details=order.broker_snapshot, reconciliation_status="NEEDS_REVIEW",
                )
            continue
        try:
            order_date = order.created_at.astimezone(timezone(timedelta(hours=9))).date().isoformat()
            if order_date not in history_cache:
                history = toss_client.orders("OPEN", from_date=order_date, to_date=order_date)
                history += toss_client.orders("CLOSED", from_date=order_date, to_date=order_date)
                history_cache[order_date] = {
                    str(item.get("orderId")): item for item in history if item.get("orderId")
                }
            detail = history_cache[order_date].get(order.external_order_id)
            if detail is None:
                detail = toss_client.order_detail(order.external_order_id)
        except TossApiError as exc:
            update_real_order(
                order.user_id, order.id,
                status=order.status, broker_status=order.broker_status,
                message="토스 주문 상태 대조에 실패했습니다.", details={"error": str(exc)},
                reconciliation_status="ERROR",
            )
            continue
        broker_status = str(detail.get("status") or order.broker_status or order.status)
        execution = detail.get("execution") or {}
        filled_quantity = Decimal(str(execution.get("filledQuantity") or 0))
        def optional_decimal(value):
            return None if value is None else Decimal(str(value))
        average_filled_price = optional_decimal(execution.get("averageFilledPrice"))
        filled_amount = optional_decimal(execution.get("filledAmount"))
        commission = optional_decimal(execution.get("commission"))
        tax = optional_decimal(execution.get("tax"))
        status = _local_order_status(broker_status, order.status)
        if (status != order.status or broker_status != order.broker_status
                or filled_quantity != order.filled_quantity
                or average_filled_price != order.average_filled_price
                or filled_amount != order.filled_amount
                or commission != order.commission or tax != order.tax
                or order.reconciliation_status != "MATCHED"):
            update_real_order(
                order.user_id, order.id,
                status=status, broker_status=broker_status,
                message="서버 시작 시 토스 주문 상태와 DB를 대조했습니다.", details=detail,
            )
        reconciled += 1
    return reconciled


async def refresh_long_term_candidates(
    candidate_count: int = 20, target_count: int = 5, *, force: bool = False,
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
                toss_client.list_domestic_stocks,
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
                                asyncio.to_thread(toss_client.domestic_stock_detail, candidate.symbol, "1Y"),
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
                status.update(completed=processed, selected=min(len(eligible), target_count), excluded=excluded, failed=failed, phase="running")
                status["message"] = (
                    f"후보 {processed}개 검토 · 추천 기준 통과 {min(len(eligible), target_count)}개"
                )
            selected = sorted(
                eligible,
                key=lambda item: (item.overall_score or -1, item.generated_at),
                reverse=True,
            )[:target_count]
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


_long_term_watch_status = {
    "running": False, "completed": 0, "target": 0, "failed": 0,
    "last_finished_at": None, "message": "내 후보 자동 분석 대기",
}


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    global watchlist_source, paper_snapshot_at, paper_source_account_label
    stock_directory_warm_task: asyncio.Task | None = None
    long_term_candidate_task: asyncio.Task | None = None
    initialize()
    if toss_client.configured:
        readiness = await asyncio.to_thread(_startup_readiness, force=True)
        try:
            if readiness["ready"]:
                popular_stocks, popular_prices = await asyncio.to_thread(toss_client.domestic_trading_amount_top, 10)
                market.replace_stocks(popular_stocks, popular_prices)
                engine.configure(interval_seconds=engine.interval_seconds, short_period=engine.short_period,
                                 long_period=engine.long_period, order_quantity=engine.order_quantity)
                watchlist_source = "toss_daily_trading_amount"
        except (TossApiError, KeyError, ValueError, ArithmeticError) as exc:
            logger.warning("Startup Toss market warm-up failed: %s", exc)
            watchlist_source = "fallback"
        try:
            await asyncio.to_thread(reconcile_saved_real_orders)
        except (TossApiError, LookupError, ValueError, ArithmeticError) as exc:
            logger.warning("Startup real-order reconciliation failed: %s", exc)
    broker.initialize()
    if broker.snapshot_metadata:
        paper_snapshot_at = datetime.fromisoformat(broker.snapshot_metadata['snapshot_at'])
        paper_source_account_label = broker.snapshot_metadata.get('source_label')
    broker.set_risk_manager(risk_manager)
    if toss_client.configured:
        async def warm_stock_directory() -> None:
            try:
                stock_count, ranking_count = await asyncio.to_thread(
                    toss_client.warm_domestic_stock_directory
                )
                logger.info(
                    "Domestic stock directory cache ready: %s stocks, %s rankings",
                    stock_count, ranking_count,
                )
            except (TossApiError, ValueError, TypeError) as exc:
                logger.warning("Domestic stock directory warm-up failed: %s", exc)

        stock_directory_warm_task = asyncio.create_task(warm_stock_directory())
        long_term_candidate_task = asyncio.create_task(
            long_term_candidate_worker(stock_directory_warm_task)
        )
    try:
        yield
    finally:
        if stock_directory_warm_task is not None and not stock_directory_warm_task.done():
            stock_directory_warm_task.cancel()
        if long_term_candidate_task is not None and not long_term_candidate_task.done():
            long_term_candidate_task.cancel()
        manual_refresh_task = getattr(app.state, "long_term_manual_refresh_task", None)
        if manual_refresh_task is not None and not manual_refresh_task.done():
            manual_refresh_task.cancel()
        await _stop_all_paper_engines()
        await quote_stream.close()


app = FastAPI(
    title="국내주식 모의 자동매매 API",
    description="토스증권 Open API 연결 전 사용하는 학습용 가상매매 서버",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "다른 사이트의 요청은 허용하지 않습니다."}, status_code=403)
    if request.url.path in {"/static/index.html", "/static/live.html"}:
        return RedirectResponse("/", status_code=303)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/", include_in_schema=False, response_model=None)
def dashboard_root(request: Request) -> RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/live", status_code=303)


@app.get("/paper", include_in_schema=False, response_model=None)
def paper_dashboard(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(static_dir / "index.html")


@app.get("/live", include_in_schema=False, response_model=None)
def live_dashboard(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(static_dir / "live.html")


@app.get("/stocks", include_in_schema=False, response_model=None)
def domestic_stocks_page(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(static_dir / "stocks.html")


@app.get("/news", include_in_schema=False, response_model=None)
def news_page(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(static_dir / "news.html")


@app.get("/long-term", include_in_schema=False, response_model=None)
def long_term_page(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(static_dir / "long-term.html")


@app.get("/settings", include_in_schema=False, response_model=None)
def settings_page(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(static_dir / "settings.html")


@app.get("/stocks/{symbol}", include_in_schema=False, response_model=None)
def domestic_stock_detail_page(symbol: str, request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(static_dir / "stock-detail.html")


@app.get("/login", include_in_schema=False, response_model=None)
def login_page(request: Request) -> FileResponse | RedirectResponse:
    if session_from_request(request):
        return RedirectResponse("/", status_code=303)
    return FileResponse(static_dir / "login.html")


@app.post("/auth/login", response_model=SessionInfo)
def login(payload: LoginRequest, response: Response) -> SessionInfo:
    result = authenticate(payload.username, payload.password)
    if not result:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    raw_token, user = result
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=settings.session_minutes * 60,
        httponly=True,
        secure=False,
        samesite="strict",
        path="/",
    )
    return SessionInfo(username=user.username, csrf_token=user.csrf_token, expires_at=user.expires_at)


@app.get("/auth/me", response_model=SessionInfo)
def current_session(
    request: Request, response: Response, user: AuthenticatedUser = Depends(require_session)
) -> SessionInfo:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if raw_token:
        response.set_cookie(
            SESSION_COOKIE, raw_token, max_age=settings.session_minutes * 60,
            httponly=True, secure=False, samesite="strict", path="/",
        )
    return SessionInfo(
        username=user.username, csrf_token=user.csrf_token,
        expires_at=user.expires_at, locked=user.locked,
    )


@app.post("/auth/lock", response_model=SessionInfo)
def lock_current_session(
    request: Request, user: AuthenticatedUser = Depends(require_csrf),
) -> SessionInfo:
    try:
        lock_session(request, user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SessionInfo(
        username=user.username, csrf_token=user.csrf_token,
        expires_at=user.expires_at, locked=True,
    )


@app.post("/auth/unlock", response_model=SessionInfo)
def unlock_current_session(
    payload: LivePinRequest, request: Request,
    user: AuthenticatedUser = Depends(require_session_csrf),
) -> SessionInfo:
    try:
        unlock_session(request, user, payload.pin)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SessionInfo(
        username=user.username, csrf_token=user.csrf_token,
        expires_at=user.expires_at, locked=False,
    )


@app.get("/auth/live-pin", response_model=LivePinStatus)
def current_live_pin_status(request: Request, user: AuthenticatedUser = Depends(require_user)) -> LivePinStatus:
    configured, authorized_until = live_pin_status(request, user)
    return LivePinStatus(configured=configured, authorized=authorized_until is not None,
                         authorized_until=authorized_until)


@app.post("/auth/live-pin/verify", response_model=LivePinStatus)
def authorize_live_pin(
    payload: LivePinRequest, request: Request, user: AuthenticatedUser = Depends(require_csrf)
) -> LivePinStatus:
    try:
        authorized_until = verify_live_pin(request, user, payload.pin)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return LivePinStatus(configured=True, authorized=True, authorized_until=authorized_until)


@app.post("/auth/logout", status_code=204)
async def logout(
    request: Request,
    _: AuthenticatedUser = Depends(require_csrf),
) -> Response:
    delete_session(request)
    await engine.stop()
    result = Response(status_code=204)
    result.delete_cookie(SESSION_COOKIE, path="/")
    return result


@app.get("/openapi.json", include_in_schema=False)
def protected_openapi(_: AuthenticatedUser = Depends(require_user)) -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
def protected_docs(_: AuthenticatedUser = Depends(require_user)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="자동매매 API 문서")


@app.get("/health")
def health(_: AuthenticatedUser = Depends(require_user)) -> dict[str, str]:
    try:
        with connect() as conn:
            conn.execute('SELECT 1')
    except Exception:
        raise HTTPException(status_code=503, detail='데이터베이스 연결 실패')
    return {
        "status": "ok",
        "mode": settings.app_mode,
        "toss_api": "ready" if settings.toss_api_ready else "not_configured",
        "postgres": "connected",
        "paper_watchlist": watchlist_source,
    }


@app.get("/startup/readiness")
def startup_readiness() -> dict:
    return _startup_readiness()


@app.post("/toss/test-connection", response_model=TossConnectionStatus)
def test_toss_connection(_: AuthenticatedUser = Depends(require_csrf)) -> TossConnectionStatus:
    if not toss_client.configured:
        return TossConnectionStatus(configured=False, connected=False,
                                    message="Client ID와 Client Secret을 먼저 설정하세요.")
    try:
        result = toss_client.test_connection()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TossConnectionStatus(configured=True, connected=result.connected,
                                account_count=result.account_count, message=result.message)


@app.get("/live/orders/real/readiness", response_model=LiveTradingReadiness)
def live_trading_readiness(_: AuthenticatedUser = Depends(require_user)) -> LiveTradingReadiness:
    enabled = settings.live_trading_enabled
    configured = toss_client.configured
    if not configured:
        message = "토스 API 인증 정보를 먼저 설정해야 합니다."
    elif not enabled:
        message = "실제 주문 연결부는 준비됐지만 안전 잠금으로 비활성화되어 있습니다."
    else:
        message = "실제 주문 전송이 활성화되어 있습니다. 주문 전 최종 확인이 필요합니다."
    return LiveTradingReadiness(
        configured=configured,
        enabled=enabled,
        supported_order="국내 주식 · 정수 수량 · DAY 지정가",
        message=message,
    )


@app.get("/settings/strategies", response_model=list[LiveStrategy])
def list_strategy_settings(user: AuthenticatedUser = Depends(require_user)) -> list[LiveStrategy]:
    return list_strategies(user.id)


@app.get("/settings/strategy-presets/{kind}")
def strategy_preset_preview(
    kind: str, order_amount: Decimal = Query(default=Decimal("300000"), ge=10000, le=1000000),
    _: AuthenticatedUser = Depends(require_user),
) -> dict:
    if kind not in {"popular", "affordable"}:
        raise HTTPException(status_code=404, detail="지원하지 않는 기본 프리셋입니다.")
    try:
        return weekly_preset(kind, order_amount, toss_client.strategy_preset_candidates,
                             fee_rate=settings.paper_fee_rate, slippage_rate=settings.paper_slippage_rate)
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _strategy_stock_name(symbol: str) -> str:
    try:
        stock = toss_client.domestic_stock(symbol.upper())
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not stock:
        raise HTTPException(status_code=404, detail="국내 상장 종목을 찾지 못했습니다.")
    return str(stock.get("name") or symbol.upper())


def _strategy_targets(payload: LiveStrategyWrite) -> list[tuple[str, str]]:
    symbols = list(dict.fromkeys(symbol.upper() for symbol in (payload.symbols or [payload.symbol])))
    if not symbols or len(symbols) > 20:
        raise HTTPException(status_code=422, detail="전략 대상 종목은 1개 이상 20개 이하로 선택하세요.")
    targets = []
    for symbol in symbols:
        if not symbol.isdigit() or len(symbol) != 6:
            raise HTTPException(status_code=422, detail=f"올바르지 않은 국내 종목코드입니다: {symbol}")
        targets.append((symbol, _strategy_stock_name(symbol)))
    return targets


@app.post("/settings/strategies", response_model=LiveStrategy, status_code=201)
def create_strategy_settings(
    payload: LiveStrategyWrite, user: AuthenticatedUser = Depends(require_csrf),
) -> LiveStrategy:
    if payload.execution_mode == "LIVE" and payload.enabled:
        raise HTTPException(status_code=409, detail="LIVE 자동매매 엔진이 준비될 때까지 실제 실행 전략은 활성화할 수 없습니다.")
    try:
        targets = _strategy_targets(payload)
        return save_strategy(user.id, payload, targets[0][1], targets=targets)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/settings/strategies/{strategy_id}", response_model=LiveStrategy)
def update_strategy_settings(
    strategy_id: int, payload: LiveStrategyWrite,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveStrategy:
    if payload.execution_mode == "LIVE" and payload.enabled:
        raise HTTPException(status_code=409, detail="LIVE 자동매매 엔진이 준비될 때까지 실제 실행 전략은 활성화할 수 없습니다.")
    try:
        targets = _strategy_targets(payload)
        return save_strategy(user.id, payload, targets[0][1], strategy_id=strategy_id, targets=targets)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/settings/strategies/{strategy_id}", status_code=204)
def delete_strategy_settings(
    strategy_id: int, user: AuthenticatedUser = Depends(require_csrf),
) -> Response:
    try:
        delete_strategy(user.id, strategy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@app.get("/live/portfolio", response_model=LivePortfolio)
def live_portfolio(user: AuthenticatedUser = Depends(require_user)) -> LivePortfolio:
    try:
        portfolio = toss_client.portfolio()
        auto_positions = auto_position_quantities(user.id)
        holdings = []
        for holding in portfolio.holdings:
            auto_quantity = max(Decimal(0), min(
                holding.quantity, Decimal(str(auto_positions.get(holding.symbol, 0)))
            ))
            holdings.append(holding.model_copy(update={
                "auto_managed_quantity": auto_quantity,
                "existing_quantity": holding.quantity - auto_quantity,
            }))
        return portfolio.model_copy(update={"holdings": holdings})
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/live/buying-power", response_model=LiveBuyingPower)
def live_buying_power(_: AuthenticatedUser = Depends(require_user)) -> LiveBuyingPower:
    try:
        return toss_client.buying_power()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _preview_live_order(
    payload: LiveOrderPreviewRequest,
    request: Request,
    user: AuthenticatedUser,
    *, relax_financial_limits: bool,
) -> LiveOrderPreview:
    _, authorized_until = live_pin_status(request, user)
    if authorized_until is None:
        raise HTTPException(status_code=403, detail="주문을 검토하려면 LIVE PIN 인증이 필요합니다.")
    if payload.mode == "SINGLE" and (payload.trigger_price is None or payload.expire_date is None):
        raise HTTPException(status_code=422, detail="목표가 도달 주문에는 감시 가격과 만료일이 필요합니다.")
    if payload.mode == "SINGLE" and payload.expire_date < date.today():
        raise HTTPException(status_code=422, detail="조건주문 만료일은 오늘 이후여야 합니다.")
    if payload.order_type == "LIMIT" and payload.order_price is None:
        raise HTTPException(status_code=422, detail="지정가 주문에는 주문 가격이 필요합니다.")
    try:
        stock = toss_client.domestic_stock(payload.symbol)
        if not stock:
            raise HTTPException(status_code=404, detail="국내 상장 종목을 찾지 못했습니다.")
        detail = toss_client.domestic_stock_detail(payload.symbol, period="1D")
        if detail.price is None:
            raise HTTPException(status_code=409, detail="현재가를 확인할 수 없어 주문을 검토할 수 없습니다.")
        reference_price = payload.order_price if payload.order_type == "LIMIT" else detail.price
        estimated_amount = reference_price * payload.quantity
        portfolio = toss_client.portfolio()
        buying_power = toss_client.buying_power()
        config = risk_manager.settings()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    checks: list[LiveOrderPreviewCheck] = []
    relaxed_financial_limits = relax_financial_limits

    def add_check(name: str, passed: bool, success: str, failure: str) -> None:
        checks.append(LiveOrderPreviewCheck(name=name, passed=passed, message=success if passed else failure))

    def add_financial_check(name: str, passed: bool, success: str, failure: str) -> None:
        if relaxed_financial_limits:
            message = success if passed else f"테스트 모드에서 한도 적용을 생략했습니다: {failure}"
            checks.append(LiveOrderPreviewCheck(name=name, passed=True, message=message))
        else:
            checks.append(LiveOrderPreviewCheck(name=name, passed=passed, warning=not passed, message=success if passed else failure))

    add_check("LIVE PIN", True, "LIVE PIN 인증이 유효합니다.", "LIVE PIN 인증이 필요합니다.")
    if relaxed_financial_limits:
        add_check("DRY RUN 테스트", True, "투자 정책 한도만 적용하지 않는 테스트 모드입니다. 실제 현금과 보유 수량은 검사합니다.", "")
    add_financial_check("주문 금액", estimated_amount <= config.max_order_amount,
                        "1회 주문 한도 이내입니다.", "1회 최대 주문 금액을 초과합니다.")
    holding = next((item for item in portfolio.holdings if item.symbol.upper() == payload.symbol.upper()), None)
    if payload.side.value == "BUY":
        symbol_value = holding.market_value if holding else Decimal(0)
        total_asset = portfolio.market_value + buying_power.krw_cash_buying_power
        remaining_cash = buying_power.krw_cash_buying_power - estimated_amount
        current_cash_ratio = buying_power.krw_cash_buying_power / total_asset * 100 if total_asset else Decimal(0)
        cash_ratio = remaining_cash / total_asset * 100 if total_asset else Decimal(0)
        add_check("주문 가능 금액", estimated_amount <= buying_power.krw_cash_buying_power,
                  "원화 주문 가능 금액 이내입니다.", "원화 주문 가능 금액이 부족합니다.")
        add_financial_check("종목별 한도", symbol_value + estimated_amount <= config.max_symbol_amount,
                            "종목별 투자 한도 이내입니다.", "종목별 최대 투자 금액을 초과합니다.")
        add_financial_check("전체 투자 한도", portfolio.market_value + estimated_amount <= config.max_total_investment,
                            "전체 투자 한도 이내입니다.", "전체 최대 투자 금액을 초과합니다.")
        add_financial_check("최소 현금", cash_ratio >= config.min_cash_ratio,
                            f"현금 비율 {current_cash_ratio:.1f}% → {cash_ratio:.1f}%로, 기준 {config.min_cash_ratio:.1f}% 이상입니다.",
                            f"현금 비율 {current_cash_ratio:.1f}% → {cash_ratio:.1f}%로, 안전 기준 {config.min_cash_ratio:.1f}%보다 낮아집니다.")
    else:
        available_quantity = holding.quantity if holding else Decimal(0)
        add_check("보유 수량", payload.quantity <= available_quantity,
                  "현재 보유 수량 이내입니다.", "매도할 보유 수량이 부족합니다.")
    approved = all(check.passed or check.warning for check in checks)
    return LiveOrderPreview(
        approved=approved,
        symbol=payload.symbol.upper(),
        name=str(stock.get("name", payload.symbol)),
        side=payload.side,
        mode=payload.mode,
        order_type=payload.order_type,
        quantity=payload.quantity,
        reference_price=reference_price,
        estimated_amount=estimated_amount,
        checks=checks,
        message=("투자 한도 초과 경고가 있습니다. 확인 후 계속할 수 있습니다. 실제 주문은 전송되지 않았습니다."
                 if approved and any(check.warning for check in checks) else "모든 사전 검사를 통과했습니다. 실제 주문은 전송되지 않았습니다."
                 if approved else "통과하지 못한 항목이 있습니다. 실제 주문은 전송되지 않았습니다."),
    )


@app.post("/live/orders/preview", response_model=LiveOrderPreview)
def preview_live_order(
    payload: LiveOrderPreviewRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveOrderPreview:
    return _preview_live_order(
        payload, request, user,
        relax_financial_limits=False,
    )


@app.post("/live/orders/dry-run", response_model=LiveDryRunOrder, status_code=201)
def confirm_live_dry_run_order(
    payload: LiveDryRunConfirmRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveDryRunOrder:
    preview = _preview_live_order(
        payload, request, user,
        relax_financial_limits=settings.live_dry_run_ignore_financial_limits,
    )
    if not preview.approved:
        failed = next((check.message for check in preview.checks if not check.passed), preview.message)
        raise HTTPException(status_code=409, detail=f"주문 검토를 통과하지 못했습니다: {failed}")
    try:
        account = toss_client.selected_account()
        account_label = toss_client.buying_power().account_label
        order, _ = save_dry_run_order(user.id, account, account_label, payload, preview)
        return order
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/live/orders/real", response_model=LiveDryRunOrder, status_code=201)
def submit_live_real_order(
    payload: LiveRealOrderConfirmRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveDryRunOrder:
    if not settings.live_trading_enabled:
        raise HTTPException(status_code=423, detail="실제 주문 안전 잠금이 켜져 있습니다.")
    if payload.mode != "STANDARD" or payload.order_type != "LIMIT":
        raise HTTPException(status_code=422, detail="첫 실제 주문은 일반 DAY 지정가만 지원합니다.")
    if payload.quantity != payload.quantity.to_integral_value():
        raise HTTPException(status_code=422, detail="첫 실제 주문은 정수 수량만 지원합니다.")
    preview = _preview_live_order(payload, request, user, relax_financial_limits=False)
    if not preview.approved:
        failed = next((check.message for check in preview.checks if not check.passed), preview.message)
        raise HTTPException(status_code=409, detail=f"실제 주문 검토를 통과하지 못했습니다: {failed}")
    if any(check.warning for check in preview.checks) and not payload.accept_financial_warnings:
        raise HTTPException(status_code=409, detail="투자 한도 초과 경고를 확인하고 계속 주문에 동의해야 합니다.")
    try:
        account = toss_client.selected_account()
        account_label = toss_client.buying_power().account_label
        order, created = prepare_real_order(user.id, account, account_label, payload, preview)
        if not created:
            return order
        try:
            submitted = toss_client.create_limit_order(
                symbol=preview.symbol, side=payload.side.value, quantity=payload.quantity,
                price=payload.order_price, client_order_id=payload.client_order_id,
            )
        except TossApiError as exc:
            update_real_order(
                user.id, order.id, status="UNKNOWN", message="실제 주문 전송 결과를 확정하지 못했습니다.",
                details={"error": str(exc)}, reconciliation_status="NEEDS_REVIEW",
            )
            raise HTTPException(
                status_code=502,
                detail="주문 결과를 확정하지 못했습니다. 자동 재전송하지 말고 토스 주문 내역을 확인하세요.",
            ) from exc
        external_order_id = str(submitted["orderId"])
        try:
            detail = toss_client.order_detail(external_order_id)
            broker_status = str(detail.get("status") or "SUBMITTED")
            status = broker_status if broker_status in {
                "FILLED", "PARTIAL_FILLED", "PENDING", "PENDING_CANCEL", "CANCELED", "REJECTED"
            } else "SUBMITTED"
        except TossApiError:
            detail = submitted
            broker_status = "SUBMITTED"
            status = "SUBMITTED"
        return update_real_order(
            user.id, order.id, status=status, external_order_id=external_order_id,
            broker_status=broker_status, message="토스증권이 실제 주문을 접수했습니다.", details=detail,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/live/orders/real", response_model=list[LiveDryRunOrder])
def live_real_orders(user: AuthenticatedUser = Depends(require_user)) -> list[LiveDryRunOrder]:
    try:
        reconcile_saved_real_orders(active_only=True)
    except (TossApiError, LookupError, ValueError, ArithmeticError):
        pass
    return list_real_orders(user.id)


@app.post("/live/orders/real/{order_id}/cancel", response_model=LiveDryRunOrder)
def cancel_live_real_order(
    order_id: int, _: Request, user: AuthenticatedUser = Depends(require_csrf),
) -> LiveDryRunOrder:
    if not settings.live_trading_enabled:
        raise HTTPException(status_code=423, detail="실제 주문 안전 잠금이 켜져 있습니다.")
    try:
        order = real_order_for_user(user.id, order_id)
        if not order.external_order_id:
            raise HTTPException(status_code=409, detail="토스 주문 ID가 없어 취소할 수 없습니다.")
        result = toss_client.cancel_order(order.external_order_id)
        try:
            detail = toss_client.order_detail(order.external_order_id)
            broker_status = str(detail.get("status") or "PENDING_CANCEL")
        except TossApiError:
            detail = result
            broker_status = "PENDING_CANCEL"
        status = broker_status if broker_status in {"CANCELED", "PENDING_CANCEL"} else "PENDING_CANCEL"
        return update_real_order(
            user.id, order.id, status=status, broker_status=broker_status,
            message="토스증권에 실제 주문 취소를 요청했습니다.", details=detail,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/live/orders/dry-run", response_model=list[LiveDryRunOrder])
def live_dry_run_orders(user: AuthenticatedUser = Depends(require_user)) -> list[LiveDryRunOrder]:
    return list_dry_run_orders(user.id)


@app.post("/live/orders/dry-run/{order_id}/cancel", response_model=LiveDryRunOrder)
def cancel_live_dry_run_order(
    order_id: int, user: AuthenticatedUser = Depends(require_csrf)
) -> LiveDryRunOrder:
    try:
        return cancel_dry_run_order(user.id, order_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/live/orders/dry-run/today", response_model=DeletedOrderCount)
def delete_live_dry_run_orders_today(
    user: AuthenticatedUser = Depends(require_csrf),
) -> DeletedOrderCount:
    return DeletedOrderCount(deleted=delete_today_dry_run_orders(user.id))


@app.get("/live/candidates", response_model=LiveCandidateList)
def live_candidates(_: AuthenticatedUser = Depends(require_user)) -> LiveCandidateList:
    try:
        return toss_client.affordable_domestic_candidates()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/live/stocks/search", response_model=LiveStockSearchPage)
def live_stock_search(
    q: str = Query(min_length=1, max_length=50),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=8, ge=5, le=10),
    user: AuthenticatedUser = Depends(require_user),
) -> LiveStockSearchPage:
    try:
        result = toss_client.search_domestic_stocks(q, page, page_size)
        saved = favorite_symbols(user.id)
        return result.model_copy(update={
            "results": [item.model_copy(update={"is_favorite": item.symbol in saved})
                        for item in result.results]
        })
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/live/stocks/list", response_model=LiveStockSearchPage)
def live_stock_list(
    q: str = Query(default="", max_length=50),
    market: str = Query(default="ALL", pattern=r"^(ALL|KOSPI|KOSDAQ)$"),
    security_type: str = Query(default="ALL", pattern=r"^(ALL|COMMON|STOCK|ETF|ETN)$"),
    sort: str = Query(default="POPULAR", pattern=r"^(POPULAR|NAME|CODE)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=10, le=50),
    user: AuthenticatedUser = Depends(require_user),
) -> LiveStockSearchPage:
    try:
        result = toss_client.list_domestic_stocks(
            query=q, market=market, security_type=security_type,
            sort=sort, page=page, page_size=page_size,
        )
        saved = favorite_symbols(user.id)
        return result.model_copy(update={
            "results": [item.model_copy(update={"is_favorite": item.symbol in saved})
                        for item in result.results]
        })
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/live/stocks/sparklines", response_model=dict[str, list[Decimal]])
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
        return toss_client.domestic_sparklines(requested, period=period)
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/live/stocks/stream")
async def live_market_stream(request: Request, symbols: str = Query(max_length=139),
                             _: AuthenticatedUser = Depends(require_user)):
    codes = list(dict.fromkeys(symbols.split(",")))
    if not 1 <= len(codes) <= 20 or any(len(code) != 6 or not code.isalnum() for code in codes):
        raise HTTPException(status_code=422, detail="국내 종목 코드를 최대 20개까지 지정하세요.")

    async def events():
        subscriptions = []
        try:
            for code in codes:
                try:
                    subscriptions.append((code, quote_stream.subscribe(code)))
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
                await quote_stream.unsubscribe(code, queue)
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/live/stocks/{symbol}/stream")
async def live_quote_stream(symbol: str, request: Request, _: AuthenticatedUser = Depends(require_user)):
    if len(symbol) != 6 or not symbol.isalnum():
        raise HTTPException(status_code=422, detail="국내 종목 코드를 확인하세요.")
    async def events():
        try:
            queue = quote_stream.subscribe(symbol)
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
            await quote_stream.unsubscribe(symbol, queue)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})


@app.get("/live/stocks/{symbol}/detail", response_model=LiveStockDetail)
def live_stock_detail(
    symbol: str,
    refresh: bool = False,
    period: str = Query(default="1D", pattern="^(1D|1W|1M|3M|1Y|3Y|5Y|10Y)$"),
    candle_interval: str | None = Query(default=None, pattern="^(1m|1h|1d|1w|1mo|1y)$"),
    _: AuthenticatedUser = Depends(require_user),
) -> LiveStockDetail:
    try:
        return toss_client.domestic_stock_detail(symbol, period=period, refresh=refresh, candle_interval=candle_interval)
    except TossApiError as exc:
        status_code = 404 if exc.status_code == 404 else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/live/stocks/{symbol}/company", response_model=LiveCompanyProfile)
def live_stock_company(
    symbol: str,
    _: AuthenticatedUser = Depends(require_user),
) -> LiveCompanyProfile:
    return dart_client.company_profile(symbol)


@app.get("/research/long-term/candidates", response_model=list[LongTermAnalysis])
def long_term_candidates(
    limit: int = Query(default=5, ge=1, le=12),
    user: AuthenticatedUser = Depends(require_user),
) -> list[LongTermAnalysis]:
    return ranked_long_term_analyses(limit, user.id)


@app.get("/research/long-term/candidate-status")
def long_term_candidate_status(_: AuthenticatedUser = Depends(require_user)) -> dict:
    return dict(_long_term_scan_status)


@app.post("/research/long-term/candidate-refresh", status_code=202)
async def start_long_term_candidate_refresh(
    _: AuthenticatedUser = Depends(require_csrf),
) -> dict:
    if _long_term_scan_status["running"] or _long_term_scan_lock.locked():
        raise HTTPException(status_code=409, detail="장기 관찰 후보를 이미 분석하고 있습니다.")
    if not toss_client.configured:
        raise HTTPException(status_code=409, detail="토스 API 연결 후 후보 분석을 시작할 수 있습니다.")
    _long_term_scan_status.update(
        running=True, completed=0, target=0, selected=0, excluded=0, failed=0,
            skipped=0, phase="running", current_name=None, last_finished_at=None,
        message="수동 후보 분석을 준비하고 있습니다.",
    )
    task = asyncio.create_task(refresh_long_term_candidates(force=True))
    app.state.long_term_manual_refresh_task = task
    return dict(_long_term_scan_status)


@app.get("/research/long-term/watchlist-status")
def long_term_watchlist_status(_: AuthenticatedUser = Depends(require_user)) -> dict:
    return {**_long_term_watch_status, "next_run_at": _long_term_scan_status["next_run_at"]}


@app.get("/research/long-term/watchlist", response_model=list[LongTermWatchCandidate])
def long_term_watchlist(user: AuthenticatedUser = Depends(require_user)) -> list[LongTermWatchCandidate]:
    return list_long_term_watch(user.id)


@app.post("/research/long-term/watchlist/{symbol}")
def add_long_term_watchlist_symbol(
    symbol: str, user: AuthenticatedUser = Depends(require_csrf),
) -> dict[str, bool]:
    stock = toss_client.domestic_stock(symbol)
    if stock is None:
        raise HTTPException(status_code=404, detail="국내 종목을 찾지 못했습니다.")
    if str(stock.get("securityType", "")) != "STOCK" or not bool(stock.get("isCommonShare", False)):
        raise HTTPException(status_code=409, detail="장기 관찰 후보에는 국내 보통주만 추가할 수 있습니다.")
    add_long_term_watch(
        user.id, str(stock.get("symbol", symbol)), str(stock.get("name", symbol)),
        str(stock.get("market", "KRX")),
    )
    return {"added": True}


@app.delete("/research/long-term/watchlist/{symbol}")
def remove_long_term_watchlist_symbol(
    symbol: str, user: AuthenticatedUser = Depends(require_csrf),
) -> dict[str, bool]:
    remove_long_term_watch(user.id, symbol.strip().zfill(6))
    return {"removed": True}


async def build_and_save_long_term_analysis(symbol: str) -> LongTermAnalysis:
    detail, company = await asyncio.gather(
        asyncio.to_thread(toss_client.domestic_stock_detail, symbol, "1Y"),
        asyncio.to_thread(dart_client.company_profile, symbol),
    )
    if not detail.is_common_share or detail.security_type != "STOCK":
        raise HTTPException(status_code=409, detail="장기 재무분석은 국내 보통주를 대상으로 합니다.")
    analysis = analyze_long_term(detail, company)
    await asyncio.to_thread(save_long_term_analysis, analysis)
    return analysis


@app.get("/research/long-term/{symbol}", response_model=LongTermAnalysis)
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


@app.get("/research/news", response_model=NewsDigest)
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


@app.get("/live/favorites", response_model=list[LiveFavoriteStock])
def live_favorites(user: AuthenticatedUser = Depends(require_user)) -> list[LiveFavoriteStock]:
    try:
        return toss_client.favorite_stock_snapshots(list_favorites(user.id))
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/live/favorites", response_model=LiveFavoriteStock, status_code=201)
def create_live_favorite(
    payload: FavoriteStockCreate,
    user: AuthenticatedUser = Depends(require_csrf),
) -> LiveFavoriteStock:
    try:
        stock = toss_client.domestic_stock(payload.symbol)
        if not stock:
            raise HTTPException(status_code=404, detail="국내 상장 종목을 찾지 못했습니다.")
        saved = add_favorite(user.id, stock)
        return LiveFavoriteStock(**saved)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete("/live/favorites/{symbol}", status_code=204)
def delete_live_favorite(
    symbol: str,
    user: AuthenticatedUser = Depends(require_csrf),
) -> Response:
    remove_favorite(user.id, symbol)
    return Response(status_code=204)


@app.get("/paper/stocks", response_model=list[Stock])
def stocks(_: AuthenticatedUser = Depends(require_user)) -> list[Stock]:
    return market.stocks()


@app.get("/paper/workspace", response_model=PaperWorkspaceStatus)
def paper_workspace(_: AuthenticatedUser = Depends(require_user)) -> PaperWorkspaceStatus:
    return PaperWorkspaceStatus(
        account=broker.account(), account_mode=paper_account_mode,
        management_scope=broker.management_scope,
        background_runs=[dict(account_mode=mode, strategy_name=session[4].name if session[4] else None,
                              tick_count=session[1].tick_count, data_message=session[1].data_message)
                         for mode, session in _paper_sessions.items()
                         if mode != paper_account_mode and session[1].running],
        managed_holdings=broker.managed_lots(),
        holding_management=broker.holding_management(),
        snapshot_ready=paper_snapshot_at is not None,
        snapshot_at=paper_snapshot_at, source_account_label=paper_source_account_label,
        selected_strategy_id=paper_selected_strategy.id if paper_selected_strategy else None,
        selected_strategy_name=paper_selected_strategy.name if paper_selected_strategy else None,
        selected_symbol=paper_selected_strategy.symbol if paper_selected_strategy else None,
        selected_symbols=[target.symbol for target in paper_selected_strategy.targets] if paper_selected_strategy else [],
    )


@app.put('/paper/management', response_model=PaperWorkspaceStatus)
@_serialize_paper_change
async def paper_management(payload: PaperManagementUpdate, user: AuthenticatedUser = Depends(require_csrf)):
    try:
        engine.set_management_scope(payload.scope)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return paper_workspace(user)


@app.post("/paper/accounts/{mode}/select", response_model=PaperWorkspaceStatus)
@_serialize_paper_change
async def select_paper_account(mode: str, user: AuthenticatedUser = Depends(require_csrf)) -> PaperWorkspaceStatus:
    global broker, engine, paper_account_mode, paper_snapshot_at
    global paper_source_account_label, paper_selected_strategy
    if mode not in {"EXPERIMENT", "LIVE_COPY"}:
        raise HTTPException(status_code=422, detail="지원하지 않는 모의계좌입니다.")
    if mode == paper_account_mode:
        return paper_workspace(user)
    # Switching the displayed account does not interrupt either account worker.
    # Preserve both portfolios and their separate strategy performance in memory.
    current = (broker, engine, paper_snapshot_at, paper_source_account_label, paper_selected_strategy)
    target = _paper_sessions.get(mode)
    if target is None:
        if mode != "EXPERIMENT":
            raise HTTPException(status_code=409, detail="기존 모의계좌가 준비되지 않았습니다.")
        new_broker = PaperBroker(
            market, initial_cash=Decimal("10000000"), account_name="paper-experiment",
            fee_rate=settings.paper_fee_rate, sell_tax_rate=settings.paper_sell_tax_rate,
            slippage_rate=settings.paper_slippage_rate,
            ignore_min_cash_ratio=settings.paper_ignore_min_cash_ratio,
            ignore_daily_order_limit=settings.paper_ignore_daily_order_limit,
            journal_path=broker.journal_path,
        )
        await asyncio.to_thread(new_broker.initialize)
        await asyncio.to_thread(new_broker.set_risk_manager, RiskManager(market))
        new_engine = MovingAverageEngine(market, new_broker, price_feed=PaperPriceFeed(toss_client))
        target = (new_broker, new_engine, datetime.now().astimezone(), "실험계좌", None)
    _paper_sessions[paper_account_mode] = current
    broker, engine, paper_snapshot_at, paper_source_account_label, paper_selected_strategy = target
    paper_account_mode = mode
    return paper_workspace(user)


@app.post("/paper/snapshot/live", response_model=PaperWorkspaceStatus)
@_serialize_paper_change
async def snapshot_live_account(_: AuthenticatedUser = Depends(require_csrf)) -> PaperWorkspaceStatus:
    global paper_snapshot_at, paper_source_account_label
    if paper_account_mode == "EXPERIMENT":
        raise HTTPException(status_code=409, detail="기존 모의계좌로 돌아간 뒤 실제 자산을 복사하세요.")
    await engine.stop()
    try:
        portfolio, buying_power = await asyncio.gather(
            asyncio.to_thread(toss_client.portfolio), asyncio.to_thread(toss_client.buying_power),
        )
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    positions = []
    for holding in portfolio.holdings:
        if holding.market_country != "KR" or holding.currency != "KRW":
            continue
        quantity = int(holding.quantity)
        if quantity <= 0:
            continue
        market.upsert_stock(
            Stock(symbol=holding.symbol, name=holding.name, market="KRX"), holding.last_price,
        )
        positions.append({
            "symbol": holding.symbol, "quantity": quantity,
            "average_price": holding.average_purchase_price, "current_price": holding.last_price,
        })
    snapshot_at = datetime.now().astimezone()
    broker.load_snapshot(cash=buying_power.krw_cash_buying_power, positions=positions,
                         metadata={'snapshot_at': snapshot_at.isoformat(), 'source_label': portfolio.account_label})
    engine.reset_performance_baseline()
    paper_snapshot_at = snapshot_at
    paper_source_account_label = portfolio.account_label
    return paper_workspace(_)


@app.post("/paper/strategies/{strategy_id}/select", response_model=PaperWorkspaceStatus)
@_serialize_paper_change
async def select_paper_strategy(
    strategy_id: int, user: AuthenticatedUser = Depends(require_csrf),
) -> PaperWorkspaceStatus:
    global paper_selected_strategy
    await engine.stop()
    strategy = next((item for item in list_strategies(user.id) if item.id == strategy_id), None)
    if strategy is None:
        raise HTTPException(status_code=404, detail="저장된 전략을 찾지 못했습니다.")
    if strategy.execution_mode != "DRY_RUN":
        raise HTTPException(status_code=409, detail="PAPER에서는 DRY RUN 전략만 선택할 수 있습니다.")
    target_symbols = [target.symbol for target in strategy.targets]
    try:
        prices = await asyncio.to_thread(toss_client.current_prices, target_symbols)
        for target in strategy.targets:
            price = prices.get(target.symbol.upper())
            if price is None:
                raise HTTPException(status_code=409, detail=f"{target.stock_name}의 현재가를 확인할 수 없습니다.")
            market.upsert_stock(Stock(symbol=target.symbol, name=target.stock_name, market="KRX"), price)
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    engine.configure(
        interval_seconds=max(10, settings.strategy_interval_seconds),
        short_period=strategy.short_period, long_period=strategy.long_period,
        order_quantity=strategy.order_quantity, target_symbols=target_symbols,
        sizing_mode=strategy.sizing_mode, order_amount=strategy.order_amount,
        take_profit_rate=strategy.take_profit_rate,
        stop_loss_rate=strategy.stop_loss_rate,
        max_holding_days=strategy.max_holding_days,
        trading_start=strategy.trading_start,
        trading_end=strategy.trading_end,
        cooldown_minutes=strategy.cooldown_minutes,
        daily_order_limit=strategy.daily_order_limit,
        strategy_id=strategy.id, strategy_name=strategy.name,
    )
    paper_selected_strategy = strategy
    return paper_workspace(user)


@app.get("/quotes", response_model=list[Quote])
def quotes(move: bool = False, _: AuthenticatedUser = Depends(require_user)) -> list[Quote]:
    if move:
        raise HTTPException(status_code=400, detail="시세 갱신은 자동매매 실행 중에만 가능합니다.")
    return market.quotes(move=move)


@app.get("/paper/journal", response_class=FileResponse)
def paper_journal(_: AuthenticatedUser = Depends(require_user)):
    if broker.journal_path is None or not broker.journal_path.exists():
        raise HTTPException(status_code=404, detail="저장된 모의 거래 기록이 없습니다.")
    return FileResponse(broker.journal_path, media_type="application/x-ndjson", filename="paper-orders.jsonl")


@app.get("/account", response_model=Account)
def account(_: AuthenticatedUser = Depends(require_user)) -> Account:
    return broker.account()


@app.get("/orders", response_model=list[Order])
def orders(_: AuthenticatedUser = Depends(require_user)) -> list[Order]:
    return broker.orders()


@app.get("/risk", response_model=RiskStatus)
def risk_status(_: AuthenticatedUser = Depends(require_user)) -> RiskStatus:
    return broker.risk_manager.status()


@app.put("/risk/settings", response_model=RiskSettings)
def update_risk_settings(
    payload: RiskSettingsUpdate, _: AuthenticatedUser = Depends(require_csrf)
) -> RiskSettings:
    if engine.running:
        raise HTTPException(status_code=409, detail="자동매매를 중지한 뒤 한도를 변경하세요.")
    try:
        return broker.risk_manager.update(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/paper/reset", response_model=Account)
@_serialize_paper_change
async def reset_paper_practice(_: AuthenticatedUser = Depends(require_csrf)) -> Account:
    await engine.stop()
    account = broker.reset_practice()
    engine.reset_performance_baseline()
    return account


@app.post("/orders", response_model=Order)
def create_order(
    request: OrderRequest, _: AuthenticatedUser = Depends(require_csrf)
) -> Order:
    if not market.has_symbol(request.symbol):
        raise HTTPException(status_code=404, detail="감시 목록에 없는 종목입니다.")
    try:
        return broker.submit(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/strategy/status", response_model=StrategyStatus)
def strategy_status(_: AuthenticatedUser = Depends(require_user)) -> StrategyStatus:
    return engine.status()


@app.put("/strategy/settings", response_model=StrategyStatus)
def strategy_settings(
    payload: StrategySettingsUpdate, _: AuthenticatedUser = Depends(require_csrf)
) -> StrategyStatus:
    try:
        engine.configure(interval_seconds=payload.interval_seconds,
                         short_period=payload.short_period,
                         long_period=payload.long_period,
                         order_quantity=payload.order_quantity)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return engine.status()


@app.post("/strategy/start", response_model=StrategyStatus)
@_serialize_paper_change
async def strategy_start(_: AuthenticatedUser = Depends(require_csrf)) -> StrategyStatus:
    if paper_snapshot_at is None:
        raise HTTPException(status_code=409, detail="먼저 현재 실제 자산을 모의계좌에 복사하세요.")
    if paper_selected_strategy is None:
        raise HTTPException(status_code=409, detail="검증할 저장 전략을 먼저 선택하세요.")
    await engine.start()
    return engine.status()


@app.post("/strategy/stop", response_model=StrategyStatus)
@_serialize_paper_change
async def strategy_stop(_: AuthenticatedUser = Depends(require_csrf)) -> StrategyStatus:
    await engine.stop()
    return engine.status()


@app.post("/strategy/emergency-stop", response_model=StrategyStatus)
@_serialize_paper_change
async def strategy_emergency_stop(_: AuthenticatedUser = Depends(require_csrf)) -> StrategyStatus:
    await engine.stop(emergency=True)
    return engine.status()
