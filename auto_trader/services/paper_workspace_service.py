"""Paper workspace service."""

import asyncio
from datetime import datetime
from fastapi import HTTPException
from ..auth import AuthenticatedUser
from ..models import PaperWorkspaceStatus, PaperManagementUpdate, Stock
from ..toss import TossApiError
from ..database import connect
from ..live_strategies import list_strategies
from .. import runtime


def paper_workspace(_: AuthenticatedUser) -> PaperWorkspaceStatus:
    sessions = {mode: session for mode, session in runtime.paper_state().sessions.items() if mode != runtime.paper_state().mode}
    sessions[runtime.paper_state().mode] = (runtime.paper_state().broker, runtime.paper_state().engine, runtime.paper_state().snapshot_at, runtime.paper_state().source_label, runtime.paper_state().selected_strategy)
    summaries = []
    for mode in ("LIVE_COPY", "EXPERIMENT"):
        session = sessions.get(mode)
        if session is None:
            summaries.append(dict(account_mode=mode, selected=False, loaded=False))
            continue
        account_broker, account_engine, _, _, selected = session
        account = account_broker.account()
        strategy = account_engine.status()
        summaries.append(dict(account_mode=mode, selected=mode == runtime.paper_state().mode, loaded=True,
                              running=strategy.running, emergency_stopped=strategy.emergency_stopped,
                              strategy_name=selected.name if selected else None, tick_count=strategy.tick_count,
                              data_message=strategy.data_message, position_count=len(account.positions),
                              total_asset=str(account.total_asset), total_profit=str(account.total_profit),
                              strategy_profit=str(strategy.strategy_profit)))
    return PaperWorkspaceStatus(
        account=runtime.broker.account(), account_mode=runtime.paper_state().mode, account_summaries=summaries,
        management_scope=runtime.broker.management_scope,
        background_runs=[dict(account_mode=mode, strategy_name=session[4].name if session[4] else None,
                              tick_count=session[1].tick_count, data_message=session[1].data_message)
                         for mode, session in runtime.paper_state().sessions.items()
                         if mode != runtime.paper_state().mode and session[1].running],
        managed_holdings=runtime.broker.managed_lots(),
        holding_management=runtime.broker.holding_management(),
        snapshot_ready=runtime.paper_state().snapshot_at is not None,
        snapshot_at=runtime.paper_state().snapshot_at, source_account_label=runtime.paper_state().source_label,
        selected_strategy_id=runtime.paper_state().selected_strategy.id if runtime.paper_state().selected_strategy else None,
        selected_strategy_name=runtime.paper_state().selected_strategy.name if runtime.paper_state().selected_strategy else None,
        selected_symbol=runtime.paper_state().selected_strategy.symbol if runtime.paper_state().selected_strategy else None,
        selected_symbols=[target.symbol for target in runtime.paper_state().selected_strategy.targets] if runtime.paper_state().selected_strategy else [],
    )


@runtime._serialize_paper_change
async def paper_management(payload: PaperManagementUpdate, user: AuthenticatedUser):
    try:
        runtime.engine.set_management_scope(payload.scope)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return paper_workspace(user)


@runtime._serialize_paper_change
async def select_paper_account(mode: str, user: AuthenticatedUser) -> PaperWorkspaceStatus:
    if mode not in {"EXPERIMENT", "LIVE_COPY"}:
        raise HTTPException(status_code=422, detail="지원하지 않는 모의계좌입니다.")
    if mode == runtime.paper_state().mode:
        return paper_workspace(user)
    # Switching the displayed account does not interrupt either account worker.
    # Preserve both portfolios and their separate strategy performance in memory.
    current = (runtime.paper_state().broker, runtime.paper_state().engine, runtime.paper_state().snapshot_at, runtime.paper_state().source_label, runtime.paper_state().selected_strategy)
    target = runtime.paper_state().sessions.get(mode)
    if target is None:
        if mode != "EXPERIMENT":
            raise HTTPException(status_code=409, detail="기존 모의계좌가 준비되지 않았습니다.")
        target = await asyncio.to_thread(runtime._create_paper_session, runtime.paper_state().user_id, mode, runtime.paper_state().client)
    if runtime.paper_state().user_id is not None:
        with connect() as conn:
            conn.execute('INSERT INTO user_paper_preferences(user_id,selected_mode) VALUES (%s,%s) ON CONFLICT(user_id) DO UPDATE SET selected_mode=EXCLUDED.selected_mode', (runtime.paper_state().user_id, mode))
    runtime.paper_state().sessions[runtime.paper_state().mode] = current
    runtime.paper_state().broker, runtime.paper_state().engine, runtime.paper_state().snapshot_at, runtime.paper_state().source_label, runtime.paper_state().selected_strategy = target
    runtime.paper_state().mode = mode
    return paper_workspace(user)


@runtime._serialize_paper_change
async def snapshot_live_account(_: AuthenticatedUser) -> PaperWorkspaceStatus:
    if runtime.paper_state().mode == "EXPERIMENT":
        raise HTTPException(status_code=409, detail="기존 모의계좌로 돌아간 뒤 실제 자산을 복사하세요.")
    await runtime.engine.stop()
    try:
        portfolio, buying_power = await asyncio.gather(
            asyncio.to_thread(runtime.toss_client.portfolio), asyncio.to_thread(runtime.toss_client.buying_power),
        )
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    positions = []
    for holding in portfolio.holdings:
        if holding.market_country != "KR" or holding.currency != "KRW":
            continue
        quantity = int(holding.quantity)
        if quantity <= 0:
            continue
        runtime.market.upsert_stock(
            Stock(symbol=holding.symbol, name=holding.name, market="KRX"), holding.last_price,
        )
        positions.append({
            "symbol": holding.symbol, "quantity": quantity,
            "average_price": holding.average_purchase_price, "current_price": holding.last_price,
        })
    snapshot_at = datetime.now().astimezone()
    runtime.broker.load_snapshot(cash=buying_power.krw_cash_buying_power, positions=positions,
                         metadata={'snapshot_at': snapshot_at.isoformat(), 'source_label': portfolio.account_label})
    runtime.engine.reset_performance_baseline()
    runtime.paper_state().snapshot_at = snapshot_at
    runtime.paper_state().source_label = portfolio.account_label
    return paper_workspace(_)


@runtime._serialize_paper_change
async def select_paper_strategy(
    strategy_id: int, user: AuthenticatedUser,
) -> PaperWorkspaceStatus:
    await runtime.engine.stop()
    strategy = next((item for item in list_strategies(user.id) if item.id == strategy_id), None)
    if strategy is None:
        raise HTTPException(status_code=404, detail="저장된 전략을 찾지 못했습니다.")
    if strategy.execution_mode != "DRY_RUN":
        raise HTTPException(status_code=409, detail="PAPER에서는 DRY RUN 전략만 선택할 수 있습니다.")
    target_symbols = [target.symbol for target in strategy.targets]
    try:
        prices = await asyncio.to_thread(runtime.toss_client.current_prices, target_symbols)
        for target in strategy.targets:
            price = prices.get(target.symbol.upper())
            if price is None:
                raise HTTPException(status_code=409, detail=f"{target.stock_name}의 현재가를 확인할 수 없습니다.")
            runtime.market.upsert_stock(Stock(symbol=target.symbol, name=target.stock_name, market="KRX"), price)
    except TossApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await asyncio.to_thread(runtime.broker.remember_strategy, user.id, strategy.id, target_symbols)
    runtime._configure_paper_strategy(runtime.engine, strategy)
    runtime.paper_state().selected_strategy = strategy
    return paper_workspace(user)
