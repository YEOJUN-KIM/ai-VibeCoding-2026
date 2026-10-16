"""FastAPI composition root. Run with auto_trader.main:app."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import runtime
from .api import pages, auth, settings, ml, live, market, research, paper
from .lifecycle import lifespan
from .middleware import security_headers, user_account_scope

app = FastAPI(
    title="국내주식 모의 자동매매 API",
    description="토스증권 Open API 연결 전 사용하는 학습용 가상매매 서버",
    version="0.2.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory=runtime.static_dir), name="static")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
app.middleware("http")(security_headers)
app.middleware("http")(user_account_scope)

for router in (pages.router, auth.router, settings.router, ml.router, live.router, market.router, research.router, paper.router):
    app.include_router(router)
