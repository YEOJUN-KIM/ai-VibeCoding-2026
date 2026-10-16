"""api / pages"""

from fastapi import Depends, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from ..auth import AuthenticatedUser, require_user, session_from_request
from ..settings import settings
from ..database import connect
from fastapi import APIRouter
from .. import runtime
router = APIRouter()


@router.get("/", include_in_schema=False, response_model=None)
def dashboard_root(request: Request) -> RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/live", status_code=303)


@router.get("/paper", include_in_schema=False, response_model=None)
def paper_dashboard(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(runtime.static_dir / "index.html")


@router.get("/live", include_in_schema=False, response_model=None)
def live_dashboard(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(runtime.static_dir / "live.html")


@router.get("/stocks", include_in_schema=False, response_model=None)
def domestic_stocks_page(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(runtime.static_dir / "stocks.html")


@router.get("/news", include_in_schema=False, response_model=None)
def news_page(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(runtime.static_dir / "news.html")


@router.get("/long-term", include_in_schema=False, response_model=None)
def long_term_page(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(runtime.static_dir / "long-term.html")


@router.get("/settings", include_in_schema=False, response_model=None)
def settings_page(request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(runtime.static_dir / "settings.html")


@router.get("/stocks/{symbol}", include_in_schema=False, response_model=None)
def domestic_stock_detail_page(symbol: str, request: Request) -> FileResponse | RedirectResponse:
    if not session_from_request(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(runtime.static_dir / "stock-detail.html")


@router.get("/login", include_in_schema=False, response_model=None)
def login_page(request: Request) -> FileResponse | RedirectResponse:
    if session_from_request(request):
        return RedirectResponse("/", status_code=303)
    return FileResponse(runtime.static_dir / "login.html")


@router.get("/openapi.json", include_in_schema=False)
def protected_openapi(request: Request, _: AuthenticatedUser = Depends(require_user)) -> JSONResponse:
    return JSONResponse(request.app.openapi())


@router.get("/docs", include_in_schema=False)
def protected_docs(_: AuthenticatedUser = Depends(require_user)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="자동매매 API 문서")


@router.get("/health")
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
        "paper_watchlist": runtime.watchlist_source,
        "ml_collection": runtime.ml_collection_worker.status()["phase"],
    }


@router.get("/startup/readiness")
def startup_readiness() -> dict:
    # Signing in needs the local database, not the owner's broker connection.
    # Never make an external broker request on this public login endpoint.
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        connected = True
        message = "계정 저장소에 연결되었습니다."
    except Exception:
        connected = False
        message = "계정 저장소에 연결하지 못했습니다. 프로그램과 DB 실행 상태를 확인하세요."
    from ..launcher import workspace_id
    return {"application": "FOLIO", "workspace_id": workspace_id(),
            "ready": connected, "database": {"connected": connected, "message": message}}
