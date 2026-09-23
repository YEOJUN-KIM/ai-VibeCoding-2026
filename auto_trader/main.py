"""국내주식 모의 자동매매 FastAPI 애플리케이션."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import (
    SESSION_COOKIE,
    AuthenticatedUser,
    authenticate,
    delete_session,
    live_pin_status,
    require_csrf,
    require_user,
    session_from_request,
    verify_live_pin,
)
from .models import (Account, FavoriteStockCreate, LiveBuyingPower, LiveCandidateList,
                     LiveFavoriteStock, LivePinRequest, LivePinStatus, LivePortfolio,
                     LiveStockSearchPage, LoginRequest, Order, OrderRequest,
                     Quote, RiskSettings, RiskSettingsUpdate, RiskStatus, SessionInfo, Stock,
                     StrategySettingsUpdate, StrategyStatus, TossConnectionStatus)
from .paper import PaperBroker
from .settings import settings
from .simulator import MarketSimulator
from .strategy import MovingAverageEngine
from .risk import RiskManager
from .toss import TossApiError, TossClient
from .database import connect, initialize
from .favorites import add_favorite, favorite_symbols, list_favorites, remove_favorite


market = MarketSimulator(symbols=settings.watch_symbols)
broker = PaperBroker(market, initial_cash=settings.paper_initial_cash,
                     fee_rate=settings.paper_fee_rate, sell_tax_rate=settings.paper_sell_tax_rate)
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
watchlist_source = "fallback"
static_dir = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    global watchlist_source
    initialize()
    if toss_client.configured:
        try:
            popular_stocks, popular_prices = await asyncio.to_thread(toss_client.domestic_trading_amount_top, 10)
            market.replace_stocks(popular_stocks, popular_prices)
            engine.configure(interval_seconds=engine.interval_seconds, short_period=engine.short_period,
                             long_period=engine.long_period, order_quantity=engine.order_quantity)
            watchlist_source = "toss_daily_trading_amount"
        except (TossApiError, KeyError, ValueError, ArithmeticError):
            watchlist_source = "fallback"
    broker.initialize()
    broker.set_risk_manager(risk_manager)
    with connect() as conn:
        conn.execute("DELETE FROM auth_sessions")
    try:
        yield
    finally:
        await engine.stop()


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
    return RedirectResponse("/paper", status_code=303)


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
def current_session(user: AuthenticatedUser = Depends(require_user)) -> SessionInfo:
    return SessionInfo(username=user.username, csrf_token=user.csrf_token, expires_at=user.expires_at)


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


@app.get("/live/portfolio", response_model=LivePortfolio)
def live_portfolio(_: AuthenticatedUser = Depends(require_user)) -> LivePortfolio:
    try:
        return toss_client.portfolio()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/live/buying-power", response_model=LiveBuyingPower)
def live_buying_power(_: AuthenticatedUser = Depends(require_user)) -> LiveBuyingPower:
    try:
        return toss_client.buying_power()
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
        return toss_client.favorite_stock_snapshots([saved])[0]
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


@app.get("/stocks", response_model=list[Stock])
def stocks(_: AuthenticatedUser = Depends(require_user)) -> list[Stock]:
    return market.stocks()


@app.get("/quotes", response_model=list[Quote])
def quotes(move: bool = False, _: AuthenticatedUser = Depends(require_user)) -> list[Quote]:
    if move:
        raise HTTPException(status_code=400, detail="시세 갱신은 자동매매 실행 중에만 가능합니다.")
    return market.quotes(move=move)


@app.get("/account", response_model=Account)
def account(_: AuthenticatedUser = Depends(require_user)) -> Account:
    return broker.account()


@app.get("/orders", response_model=list[Order])
def orders(_: AuthenticatedUser = Depends(require_user)) -> list[Order]:
    return broker.orders()


@app.get("/risk", response_model=RiskStatus)
def risk_status(_: AuthenticatedUser = Depends(require_user)) -> RiskStatus:
    return risk_manager.status()


@app.put("/risk/settings", response_model=RiskSettings)
def update_risk_settings(
    payload: RiskSettingsUpdate, _: AuthenticatedUser = Depends(require_csrf)
) -> RiskSettings:
    if engine.running:
        raise HTTPException(status_code=409, detail="자동매매를 중지한 뒤 한도를 변경하세요.")
    try:
        return risk_manager.update(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/paper/reset", response_model=Account)
async def reset_paper_practice(_: AuthenticatedUser = Depends(require_csrf)) -> Account:
    await engine.stop()
    return broker.reset_practice()


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
async def strategy_start(_: AuthenticatedUser = Depends(require_csrf)) -> StrategyStatus:
    await engine.start()
    return engine.status()


@app.post("/strategy/stop", response_model=StrategyStatus)
async def strategy_stop(_: AuthenticatedUser = Depends(require_csrf)) -> StrategyStatus:
    await engine.stop()
    return engine.status()


@app.post("/strategy/emergency-stop", response_model=StrategyStatus)
async def strategy_emergency_stop(_: AuthenticatedUser = Depends(require_csrf)) -> StrategyStatus:
    await engine.stop(emergency=True)
    return engine.status()
