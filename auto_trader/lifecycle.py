"""Application startup and shutdown orchestration."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI
from .paper_feed import PaperPriceFeed
from .settings import settings
from .quote_stream import QuoteStream
from .toss import TossApiError
from .user_connections import initialize_ownership
from .database import connect, initialize
import logging
from . import runtime
from .services.readiness import _startup_readiness
from .services.live_order_service import reconcile_saved_real_orders
from .services.research import long_term_candidate_worker
from .services.paper_workspace_service import select_paper_account
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    stock_directory_warm_task: asyncio.Task | None = None
    long_term_candidate_task: asyncio.Task | None = None
    initialize()
    owner = initialize_ownership()
    runtime.legacy_workspace.user_id = owner
    runtime.legacy_workspace.broker.user_id = owner
    if owner is not None:
        runtime.legacy_workspace.client = runtime.connections.client(owner)
        runtime.legacy_workspace.quote_stream = QuoteStream(runtime.legacy_workspace.client, settings.toss_ws_url)
        runtime.legacy_workspace.engine.price_feed = PaperPriceFeed(runtime.legacy_workspace.client)
        runtime.workspaces.items[owner] = runtime.legacy_workspace
    await runtime.ml_decision_recorder.start()
    await runtime.ml_candidate_recorder.start()
    if runtime.toss_client.configured:
        readiness = await asyncio.to_thread(_startup_readiness, force=True)
        try:
            if readiness["ready"]:
                popular_stocks, popular_prices = await asyncio.to_thread(runtime.toss_client.domestic_trading_amount_top, 10)
                runtime.market.replace_stocks(popular_stocks, popular_prices)
                runtime.engine.configure(interval_seconds=runtime.engine.interval_seconds, short_period=runtime.engine.short_period,
                                 long_period=runtime.engine.long_period, order_quantity=runtime.engine.order_quantity)
                runtime.watchlist_source = "toss_daily_trading_amount"
        except (TossApiError, KeyError, ValueError, ArithmeticError) as exc:
            logger.warning("Startup Toss market warm-up failed: %s", exc)
            runtime.watchlist_source = "fallback"
        try:
            await asyncio.to_thread(reconcile_saved_real_orders)
        except (TossApiError, LookupError, ValueError, ArithmeticError) as exc:
            logger.warning("Startup real-order reconciliation failed: %s", exc)
    runtime.broker.initialize()
    if runtime.broker.snapshot_metadata:
        runtime.paper_state().snapshot_at = datetime.fromisoformat(runtime.broker.snapshot_metadata['snapshot_at'])
        runtime.paper_state().source_label = runtime.broker.snapshot_metadata.get('source_label')
    runtime.broker.set_risk_manager(runtime.legacy_workspace.broker.risk_manager)
    runtime.paper_state().selected_strategy = runtime._restore_paper_strategy(runtime.broker, runtime.engine)
    if owner is not None:
        with connect() as conn:
            preference = conn.execute('SELECT selected_mode FROM user_paper_preferences WHERE user_id=%s', (owner,)).fetchone()
        if preference and preference['selected_mode'] == 'EXPERIMENT':
            await select_paper_account('EXPERIMENT', None)
        for worker in (runtime.ml_collection_worker, runtime.ml_quality_worker, runtime.ml_macro_worker):
            worker.client = runtime.legacy_workspace.client
    if runtime.toss_client.configured:
        async def warm_stock_directory() -> None:
            try:
                stock_count, ranking_count = await asyncio.to_thread(
                    runtime.toss_client.warm_domestic_stock_directory
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
        await runtime.ml_collection_worker.start()
        await runtime.ml_quality_worker.start()
        await runtime.ml_macro_worker.start()
    try:
        yield
    finally:
        if stock_directory_warm_task is not None and not stock_directory_warm_task.done():
            stock_directory_warm_task.cancel()
        if long_term_candidate_task is not None and not long_term_candidate_task.done():
            long_term_candidate_task.cancel()
        manual_refresh_task = getattr(application.state, "long_term_manual_refresh_task", None)
        if manual_refresh_task is not None and not manual_refresh_task.done():
            manual_refresh_task.cancel()
        await runtime.ml_collection_worker.stop()
        await runtime.ml_quality_worker.stop()
        await runtime.ml_macro_worker.stop()
        await runtime._stop_all_paper_engines()
        await runtime.ml_candidate_recorder.stop()
        await runtime.ml_decision_recorder.stop()
        await asyncio.gather(*(state.quote_stream.close() for state in list(runtime.workspaces.items.values()) or [runtime.legacy_workspace]))
