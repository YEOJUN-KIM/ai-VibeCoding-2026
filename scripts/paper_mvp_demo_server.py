"""Isolated PAPER-MVP-01 demo server (verification only). NOT the FOLIO app: no workers, no Toss client, no auth.

Safety: refuses to start unless FOLIO_MVP_ISOLATED=1 and POSTGRES_DB starts with 'folio_mvp_test'. Uses the fictional
synthetic market with a virtual clock (1 virtual minute per tick) and loopback only.
  FOLIO_MVP_ISOLATED=1 POSTGRES_PORT=55432 POSTGRES_DB=folio_mvp_test POSTGRES_PASSWORD=... python scripts/paper_mvp_demo_server.py
"""
import asyncio
import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if os.environ.get("FOLIO_MVP_ISOLATED") != "1" or not os.environ.get("POSTGRES_DB", "").startswith("folio_mvp_test"):
    sys.exit("refusing to start: set FOLIO_MVP_ISOLATED=1 and use a POSTGRES_DB named folio_mvp_test*")

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from auto_trader import database, paper, risk
from auto_trader.api import paper_mvp as api
from auto_trader.auth import AuthenticatedUser, require_csrf, require_user
from auto_trader.paper_mvp import PaperMvp
from auto_trader.paper_mvp_synthetic import DAY, SyntheticSource, VirtualClock
from auto_trader.settings import settings
from auto_trader.simulator import MarketSimulator
from auto_trader.strategy import MovingAverageEngine

TICK_SECONDS = int(os.environ.get("FOLIO_MVP_TICK", "1"))
database.initialize()
clock = VirtualClock(DAY + timedelta(minutes=25, seconds=5))
market = MarketSimulator()
broker = paper.PaperBroker(market, Decimal("10000000"), account_name="mvp-demo")
broker.initialize()
broker.set_risk_manager(risk.RiskManager(market))
engine = MovingAverageEngine(market, broker, interval_seconds=TICK_SECONDS, clock=clock)
mvp = PaperMvp(broker, engine, source=SyntheticSource(clock), clock=clock, bind=False)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
user = AuthenticatedUser(id=1, username="demo", csrf_token="demo-csrf", expires_at=None)
app.dependency_overrides[require_user] = lambda: user
app.dependency_overrides[require_csrf] = lambda: user
app.dependency_overrides[api.read_controller] = lambda: mvp
app.dependency_overrides[api.write_controller] = lambda: mvp
app.include_router(api.router)
app.mount("/static", StaticFiles(directory=Path(api.__file__).resolve().parent.parent / "static"), name="static")


@app.get("/auth/me")
def me():
    return {"username": "demo", "csrf_token": "demo-csrf"}


@app.get("/paper-mvp")
def page():
    return FileResponse(Path(api.__file__).resolve().parent.parent / "static" / "paper-mvp.html")


@app.on_event("startup")
async def drive_virtual_time():
    async def loop():
        while True:                                    # one virtual minute per tick, only while the engine runs
            await asyncio.sleep(TICK_SECONDS)
            if engine.running:
                clock.advance(minutes=1)
    asyncio.create_task(loop())


if __name__ == "__main__":
    print("PAPER-MVP demo (isolated, synthetic) on http://127.0.0.1:%s/paper-mvp  db=%s:%s/%s" % (
        os.environ.get("FOLIO_MVP_PORT", "8765"), settings.postgres_host, settings.postgres_port, settings.postgres_db))
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FOLIO_MVP_PORT", "8765")), log_level="warning")
