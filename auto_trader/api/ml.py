"""api / ml"""

from fastapi import Depends
from ..auth import AuthenticatedUser, require_user
from ..ml.quality_report import latest_quality_report
from fastapi import APIRouter
from .. import runtime
router = APIRouter()


@router.get("/ml/data-collection/status")
def ml_data_collection_status(_: AuthenticatedUser = Depends(require_user)) -> dict:
    return runtime.ml_collection_worker.status()


@router.get("/ml/decision-recording/status")
def ml_decision_recording_status(_: AuthenticatedUser = Depends(require_user)) -> dict:
    return runtime.ml_decision_recorder.status()


@router.get("/ml/data-quality/latest")
def ml_data_quality_latest(_: AuthenticatedUser = Depends(require_user)) -> dict:
    return {"worker": runtime.ml_quality_worker.status(), "report": latest_quality_report()}


@router.get("/ml/macro-collection/status")
def ml_macro_collection_status(_: AuthenticatedUser = Depends(require_user)) -> dict:
    return runtime.ml_macro_worker.status()
