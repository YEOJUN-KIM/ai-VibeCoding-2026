"""Application dependencies and account-scoped workspace construction."""

import asyncio
from functools import wraps
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from .paper import PaperBroker
from .paper_feed import PaperPriceFeed
from .settings import settings
from .simulator import MarketSimulator
from .strategy import MovingAverageEngine
from .quote_stream import QuoteStream
from .risk import RiskManager
from .toss import TossClient
from .user_connections import UserConnections
from .user_workspace import UserWorkspace, UserWorkspaces, ScopedProxy
from .database import connect
from .market_hours import MarketHours
from .live_strategies import list_strategies
from .ml.collection_worker import CandleCollectionWorker
from .ml.decision_pipeline import StrategyDecisionRecorder
from .ml.quality_worker import QualityReportWorker
from .ml.macro_worker import MacroCollectionWorker


# Dependencies are created once. ScopedProxy below resolves broker/client/engine
# per request or task through UserWorkspaces, never by switching a global user.
market = MarketSimulator()


broker = PaperBroker(market, initial_cash=settings.paper_initial_cash,
                     fee_rate=settings.paper_fee_rate, sell_tax_rate=settings.paper_sell_tax_rate,
                     slippage_rate=settings.paper_slippage_rate,
                     journal_path=Path(__file__).parent.parent / ".paper-history" / "orders.jsonl",
                     ignore_min_cash_ratio=settings.paper_ignore_min_cash_ratio,
                     ignore_daily_order_limit=settings.paper_ignore_daily_order_limit)


ml_decision_recorder = StrategyDecisionRecorder()


engine = MovingAverageEngine(
    market,
    broker,
    interval_seconds=settings.strategy_interval_seconds,
    short_period=settings.strategy_short_period,
    long_period=settings.strategy_long_period,
    order_quantity=settings.order_quantity,
    decision_recorder=ml_decision_recorder.record,
)


risk_manager = RiskManager(market)


toss_client = TossClient()


quote_stream = QuoteStream(toss_client, settings.toss_ws_url)


engine.price_feed = PaperPriceFeed(toss_client)


ml_collection_worker = CandleCollectionWorker(
    toss_client,
    settings.watch_symbols,
    market_indicators=settings.ml_market_indicators,
    count=settings.ml_data_collection_count,
    enabled=settings.ml_data_collection_enabled,
)


ml_quality_worker = QualityReportWorker(
    toss_client,
    settings.watch_symbols,
    settings.ml_market_indicators,
    hour=settings.ml_quality_report_hour,
    minute=settings.ml_quality_report_minute,
    enabled=settings.ml_data_collection_enabled,
)


ml_macro_worker = MacroCollectionWorker(
    toss_client,
    enabled=settings.ml_data_collection_enabled,
)


watchlist_source = "fallback"


broker.risk_manager = risk_manager


connections = UserConnections()


legacy_workspace = UserWorkspace(None, broker, engine, toss_client, quote_stream)


workspaces = UserWorkspaces(legacy_workspace, lambda user_id: _create_user_workspace(user_id))


broker = ScopedProxy(workspaces, 'broker')


engine = ScopedProxy(workspaces, 'engine')


risk_manager = ScopedProxy(workspaces, 'risk')


toss_client = ScopedProxy(workspaces, 'client')


quote_stream = ScopedProxy(workspaces, 'quote_stream')


market_hours = MarketHours(toss_client)


legacy_workspace.engine.market_hours_provider = lambda now: market_hours.status(
    'KR', legacy_workspace.engine.price_feed.client, now=now, trading=True)


static_dir = Path(__file__).parent / "static"


def paper_state():
    return workspaces.current()


def _create_paper_session(user_id, mode, client):
    owner = user_id == legacy_workspace.user_id
    name = ('paper-default' if mode == 'LIVE_COPY' else 'paper-experiment') if owner else f'user-{user_id}-paper-{mode.lower()}'
    new_broker = PaperBroker(market, initial_cash=settings.paper_initial_cash if mode == 'LIVE_COPY' else Decimal('10000000'),
        account_name=name, user_id=user_id,
        fee_rate=settings.paper_fee_rate, sell_tax_rate=settings.paper_sell_tax_rate,
        slippage_rate=settings.paper_slippage_rate,
        ignore_min_cash_ratio=settings.paper_ignore_min_cash_ratio,
        ignore_daily_order_limit=settings.paper_ignore_daily_order_limit,
        journal_path=Path(__file__).parent.parent / '.paper-history' / f'user-{user_id}' / f'{mode.lower()}.jsonl')
    new_broker.initialize()
    new_broker.set_risk_manager(RiskManager(market))
    new_engine = MovingAverageEngine(market, new_broker, price_feed=PaperPriceFeed(client),
                                    decision_recorder=ml_decision_recorder.record)
    new_engine.market_hours_provider = lambda now: market_hours.status(
        'KR', new_engine.price_feed.client, now=now, trading=True)
    metadata = new_broker.snapshot_metadata or {}
    stamp = datetime.fromisoformat(metadata['snapshot_at']) if metadata.get('snapshot_at') else None
    if mode == 'EXPERIMENT':
        stamp = stamp or datetime.now().astimezone()
    return new_broker, new_engine, stamp, metadata.get('source_label') or ('실험계좌' if mode == 'EXPERIMENT' else None), _restore_paper_strategy(new_broker, new_engine)


def _create_user_workspace(user_id):
    client = connections.client(user_id)
    session = _create_paper_session(user_id, 'LIVE_COPY', client)
    state = UserWorkspace(user_id, session[0], session[1], client, QuoteStream(client, settings.toss_ws_url),
                          snapshot_at=session[2], source_label=session[3], selected_strategy=session[4])
    with connect() as conn:
        preference = conn.execute('SELECT selected_mode FROM user_paper_preferences WHERE user_id=%s', (user_id,)).fetchone()
    if preference and preference['selected_mode'] == 'EXPERIMENT':
        state.sessions['LIVE_COPY'] = session
        state.broker, state.engine, state.snapshot_at, state.source_label, state.selected_strategy = _create_paper_session(user_id, 'EXPERIMENT', client)
        state.mode = 'EXPERIMENT'
    return state


def _serialize_paper_change(function):
    @wraps(function)
    async def serialized(*args, **kwargs):
        async with paper_state().lock:
            return await function(*args, **kwargs)
    return serialized


async def _stop_all_paper_engines():
    states = list(workspaces.items.values()) + [paper_state()]
    workers = {worker for state in states for worker in [state.engine, *(session[1] for session in state.sessions.values())]}
    await asyncio.gather(*(worker.stop() for worker in workers))


def _configure_paper_strategy(target_engine, strategy):
    target_symbols = [target.symbol for target in strategy.targets]
    target_engine.configure(
        interval_seconds=max(10, settings.strategy_interval_seconds),
        short_period=strategy.short_period, long_period=strategy.long_period,
        order_quantity=strategy.order_quantity, target_symbols=target_symbols,
        sizing_mode=strategy.sizing_mode, order_amount=strategy.order_amount,
        take_profit_rate=strategy.take_profit_rate,
        stop_loss_rate=strategy.stop_loss_rate,
        max_holding_days=strategy.max_holding_days,
        trading_start=strategy.trading_start,
        trading_end=strategy.trading_end,
        cooldown_minutes=strategy.cooldown_minutes,
        daily_order_limit=strategy.daily_order_limit,
        strategy_id=strategy.id, strategy_name=strategy.name,
    )


def _restore_paper_strategy(target_broker, target_engine):
    selection = target_broker.last_strategy_selection
    if not selection or (target_broker.user_id is not None and selection['user_id'] != target_broker.user_id):
        return None
    strategy = next((item for item in list_strategies(selection['user_id'])
                     if item.id == selection['strategy_id'] and item.execution_mode == 'DRY_RUN'), None)
    if strategy is None or any(not market.has_symbol(target.symbol) for target in strategy.targets):
        return None
    _configure_paper_strategy(target_engine, strategy)
    return strategy
