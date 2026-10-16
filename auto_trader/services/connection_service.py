"""Connection service."""

import asyncio
from fastapi import HTTPException
from ..auth import AuthenticatedUser
from ..paper_feed import PaperPriceFeed
from ..settings import settings
from ..quote_stream import QuoteStream
from ..toss import TossApiError
from ..database import connect
from .. import runtime


@runtime._serialize_paper_change
async def save_settings_connection(payload: dict, user: AuthenticatedUser):
    client_id = payload.get('client_id')
    secret = payload.get('client_secret')
    if not all(isinstance(value, str) and 1 <= len(value.strip()) <= 2048 for value in (client_id, secret)):
        raise HTTPException(status_code=422, detail='API 키와 Secret을 입력하세요.')
    state = runtime.paper_state()
    if any(worker.running for worker in [state.engine, *(session[1] for session in state.sessions.values())]):
        raise HTTPException(status_code=409, detail='모의매매 전략을 모두 정지한 뒤 연결을 변경하세요.')
    with connect() as conn:
        pending = conn.execute("SELECT 1 FROM live_orders WHERE user_id=%s AND dry_run=false AND status NOT IN ('FILLED','CANCELED','REJECTED') LIMIT 1", (user.id,)).fetchone()
    if pending:
        raise HTTPException(status_code=409, detail='진행 중인 실제 주문을 확인한 뒤 연결을 변경하세요.')
    try:
        client = await asyncio.to_thread(runtime.connections.save, user.id, client_id.strip(), secret.strip())
    except TossApiError:
        raise HTTPException(status_code=400, detail='연결하지 못했습니다. API 키와 허용 IP를 확인하세요.') from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except OSError:
        raise HTTPException(status_code=500, detail='로컬 연결 정보를 저장하지 못했습니다.') from None
    await state.quote_stream.close()
    state.client = client
    state.quote_stream = QuoteStream(client, settings.toss_ws_url)
    for worker in [state.engine, *(session[1] for session in state.sessions.values())]:
        worker.price_feed = PaperPriceFeed(client)
    if state is runtime.legacy_workspace:
        for worker in (runtime.ml_collection_worker, runtime.ml_quality_worker, runtime.ml_macro_worker):
            worker.client = client
    return {'message': '이 계정에 증권 계좌를 연결했습니다. 주문 인증을 다시 진행하세요.'}
