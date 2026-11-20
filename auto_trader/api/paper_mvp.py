"""api / paper_mvp -- PAPER-MVP-01 controls for the PC page. PAPER only: nothing here can reach a real order path."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ..auth import AuthenticatedUser, require_csrf, require_user
from ..paper_mvp import MvpConflict, MvpError, MvpSettings, PaperMvp, controller_for

router = APIRouter(prefix="/paper/mvp")


def _controller() -> PaperMvp:
    """Controller for the current user's selected PAPER account (lazy import keeps the demo server runtime-free)."""
    from .. import paper_mvp_isolated as isolated, runtime
    state = runtime.paper_state()
    if isolated.active():      # verification only: synthetic source + virtual clock; auth/CSRF/start_guard stay real
        source, clock = isolated.synthetic()
        isolated.prepare_engine(state.engine)
        mvp = controller_for(state.broker, state.engine, source=source, clock=clock)
    else:
        mvp = controller_for(state.broker, state.engine)
    mvp.lock = state.lock            # same lock as the legacy /strategy/start|stop, so the two can never interleave
    # The controller above was chosen before the lock was taken; an account switch can finish in between.
    mvp.account_is_current = lambda: runtime.paper_state() is state and state.broker is mvp.broker and state.engine is mvp.engine
    mvp.start_guard = lambda: (None if state.snapshot_at is not None
                               else "먼저 모의계좌를 준비하세요(실험계좌 선택 또는 실제 자산 복사).")
    return mvp


@asynccontextmanager
async def locked(mvp: PaperMvp):
    """The shared PAPER lock, then a re-check that this request still targets the account the user has selected.
    Otherwise a start/settings/stop that waited for an account switch would act on the previous account (409 instead)."""
    async with mvp.lock:
        current = getattr(mvp, "account_is_current", None)
        if current is not None and not current():
            raise HTTPException(status_code=409, detail="모의계좌가 전환되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.")
        yield


def read_controller(_: AuthenticatedUser = Depends(require_user)) -> PaperMvp:
    return _controller()


def write_controller(_: AuthenticatedUser = Depends(require_csrf)) -> PaperMvp:
    return _controller()


def _http(exc: MvpError) -> HTTPException:
    return HTTPException(status_code=409 if isinstance(exc, MvpConflict) else 422, detail=str(exc))


@router.get("/status")
def mvp_status(period: Literal["day", "week", "month", "year", "all"] = "day", mvp: PaperMvp = Depends(read_controller)):
    return mvp.status(period)


@router.put("/settings")
async def mvp_settings(payload: MvpSettings, mvp: PaperMvp = Depends(write_controller)):
    async with locked(mvp):
        try:
            mvp.apply(payload, activate=False)
        except MvpError as exc:
            raise _http(exc) from exc
        return mvp.status()


@router.post("/start")
async def mvp_start(mvp: PaperMvp = Depends(write_controller)):
    async with locked(mvp):
        try:
            await mvp.start()
        except MvpError as exc:
            raise _http(exc) from exc
        return mvp.status()


@router.post("/stop")
async def mvp_stop(mvp: PaperMvp = Depends(write_controller)):
    async with locked(mvp):
        await mvp.stop()
        return mvp.status()


@router.post("/emergency-stop")
async def mvp_emergency_stop(mvp: PaperMvp = Depends(write_controller)):
    async with locked(mvp):
        await mvp.stop(emergency=True)
        return mvp.status()
