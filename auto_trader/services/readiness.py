"""services / readiness"""

from threading import Lock
from time import monotonic
from ..toss import TossApiError
from ..database import connect
import logging
from .. import runtime
logger = logging.getLogger(__name__)


_readiness_lock = Lock()


_readiness_cache: tuple[float, dict] | None = None


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

        toss_configured = runtime.toss_client.configured
        toss_connected = False
        if not toss_configured:
            toss_message = "토스 API 인증 정보가 설정되지 않았습니다."
        else:
            try:
                result = runtime.toss_client.test_connection()
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
