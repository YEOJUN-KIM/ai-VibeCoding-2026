"""Isolated verification mode for the real FOLIO app (PAPER-APP-UI-01).

Off by default. Switched on only by FOLIO_MVP_ISOLATED=1, and then the app refuses to start unless EVERY guard holds:
a throwaway database named folio_mvp_test*, loopback host, a non-default port, and no Toss credentials configured
(so no broker/market request and no collection worker can exist). When active, the real auth, CSRF, routes, runtime
state and `start_guard` are all unchanged; only the market data source and clock are replaced by the fictional
synthetic market, and the page shows a visible synthetic-data badge (status.data_source == "SYNTHETIC").
Without the flag nothing here has any effect and PaperMvp keeps its fail-closed HOLD with no data source.
"""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from .settings import settings

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_clock = None
_source = None


def requested() -> bool:
    return os.environ.get("FOLIO_MVP_ISOLATED") == "1"


def violations() -> list[str]:
    bad = []
    if not settings.postgres_db.startswith("folio_mvp_test"):
        bad.append("POSTGRES_DB must start with folio_mvp_test")
    if settings.postgres_host not in _LOOPBACK:
        bad.append("POSTGRES_HOST must be loopback")
    if settings.postgres_port == 5432:
        bad.append("POSTGRES_PORT must not be the default 5432")
    if settings.toss_api_ready:
        bad.append("Toss credentials are configured")
    return bad


def assert_safe() -> None:
    """Called at app startup. A requested-but-unsafe isolated run never gets to start."""
    if requested() and (bad := violations()):
        raise RuntimeError("isolated PAPER-MVP mode refused: " + "; ".join(bad))


def active() -> bool:
    return requested() and not violations()


def synthetic():
    """(source, clock) shared by every request of this isolated process."""
    global _clock, _source
    if _clock is None:
        from .paper_mvp_synthetic import DAY, SyntheticSource, VirtualClock
        _clock = VirtualClock(DAY + timedelta(minutes=25, seconds=5))
        _source = SyntheticSource(_clock)
    return _source, _clock


def prepare_engine(engine) -> None:
    """Make this one engine use virtual time and no broker market-hours lookup (which would call Toss)."""
    _, clock = synthetic()
    engine._clock = clock
    engine.broker.clock = clock          # order times, daily counts and the risk day follow the same virtual time as the engine
    engine.market_hours_provider = None


def start_virtual_time(engines) -> asyncio.Task:
    """One virtual minute per second, only while some engine runs. `engines` is a callable returning engines."""
    _, clock = synthetic()

    async def loop():
        while True:
            await asyncio.sleep(float(os.environ.get("FOLIO_MVP_TICK", "1")))
            if any(e.running for e in engines()):
                clock.advance(minutes=1)
    return asyncio.create_task(loop())
