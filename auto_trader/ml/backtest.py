"""Read-only, availability-time replay of the PAPER moving-average engine."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

from ..database import connect
from ..models import LiveStockCandle, Order, OrderRequest, OrderSide, OrderStatus, RiskPreset, RiskSettings, Stock
from ..risk import PRESETS, RiskManager
from ..paper import PaperBroker
from ..settings import settings
from ..simulator import MarketSimulator
from ..strategy import MovingAverageEngine
from .data_pipeline import RawCandle

D = Decimal
KST = ZoneInfo('Asia/Seoul')


@dataclass(frozen=True)
class Config:
    initial_cash: D = D('10000000')
    short_period: int = 10
    long_period: int = 40
    order_amount: D = D('300000')
    sizing_mode: str = 'AMOUNT'
    order_quantity: int = 1
    take_profit_rate: D = D('4')
    stop_loss_rate: D = D('2')
    max_holding_days: int = 3
    trading_start: str = '08:00'
    trading_end: str = '20:00'
    cooldown_minutes: int = 10
    daily_order_limit: int = 0
    fee_rate: D = D('0.00015')
    sell_tax_rate: D = D('0.002')
    slippage_rate: D = D('0.0005')
    execution: str = 'next_open'
    risk_enabled: bool = True


class ReplayRisk(RiskManager):
    def __init__(self, market, broker, clock):
        super().__init__(market)
        self.broker, self.clock = broker, clock
        self._baseline_date = clock().date()
        self._opening_asset = broker.account().total_asset

    def settings(self):
        return RiskSettings(account_id=0, preset=RiskPreset.DEFAULT, **PRESETS[RiskPreset.DEFAULT])

    def _metrics(self):
        today = self.clock().astimezone(KST).date()
        if self._baseline_date != today:
            self._baseline_date = today
            self._opening_asset = self.broker.account().total_asset
        account = self.broker.account()
        orders = sum(o.status == OrderStatus.FILLED and o.created_at.astimezone(KST).date() == today
                     for o in self.broker.orders())
        return account.cash, sum((p.market_value for p in account.positions), D(0)), account.total_asset, account.total_asset - self._opening_asset, orders


class ReplayBroker(PaperBroker):
    """Never initialize persisted accounts or write audit history."""
    def initialize(self):
        raise RuntimeError('Backtest accounts must remain in memory')

    def _save_state(self):
        pass

    def _journal(self, payload):
        pass

    def submit(self, request, **kwargs):
        if self.defer:
            self.pending.setdefault(request.symbol, (self.clock(), request, kwargs))
            return Order(id=0, symbol=request.symbol, side=request.side, quantity=request.quantity,
                         price=self.market.quote(request.symbol).price, status=OrderStatus.REJECTED,
                         message='Awaiting a later observed bar open', created_at=self.clock())
        order = super().submit(request, **kwargs)
        order.created_at = self.clock()
        return order


def replay(rows: list[RawCandle], config: Config, mode: str = 'moving_average') -> dict:
    if mode not in {'moving_average', 'buy_and_hold', 'cash'}:
        raise ValueError('Unknown benchmark')
    if config.execution not in {'next_open', 'close'}:
        raise ValueError('Invalid execution model')
    if not config.initial_cash.is_finite() or config.initial_cash <= 0:
        raise ValueError('Initial cash must be positive')
    if not 1 <= config.short_period < config.long_period or config.cooldown_minutes < 0 or config.daily_order_limit < 0:
        raise ValueError('Invalid periods or order timing limits')
    if not rows:
        raise ValueError('No candles in selected range')
    groups = defaultdict(list)
    keys = set()
    for row in rows:
        row.validate()
        if row.interval != '1m' or row.available_at < row.event_at + timedelta(minutes=1):
            raise ValueError('Only completed 1-minute candles may enter the replay')
        key = (row.symbol, row.event_at)
        if key in keys:
            raise ValueError('Duplicate symbol/time (select one source)')
        keys.add(key)
        groups[row.available_at].append(row)
    symbols = sorted({row.symbol for row in rows})
    market = MarketSimulator()
    market.replace_stocks([Stock(symbol=s, name=s) for s in symbols], {s: D(1) for s in symbols})
    broker = ReplayBroker(market, initial_cash=config.initial_cash, fee_rate=config.fee_rate,
                          sell_tax_rate=config.sell_tax_rate, slippage_rate=config.slippage_rate)
    clock = [min(groups)]
    broker.clock = lambda: clock[0]
    broker.defer = config.execution == 'next_open'
    broker.pending = {}
    if config.risk_enabled:
        broker.risk_manager = ReplayRisk(market, broker, broker.clock)
    engine = MovingAverageEngine(market, broker, clock=lambda: clock[0])
    engine.configure(interval_seconds=60, target_symbols=symbols,
                     **{k: v for k, v in asdict(config).items()
                        if k not in {'initial_cash', 'fee_rate', 'sell_tax_rate', 'slippage_rate', 'execution', 'risk_enabled'}})
    broker.begin_strategy(symbols, context=engine._management_context())
    history = {s: {} for s in symbols}
    bought = set()
    trades, curve = [], []
    peak = config.initial_cash
    drawdown = D(0)
    stale_batches = 0
    for at, batch in sorted(groups.items()):
        clock[0] = at
        before = len(broker.orders())
        # Fill only an open strictly after the information time of the signal.
        # The bar is observed later; its close is not used to choose this fill.
        if config.execution == 'next_open':
            for row in sorted(batch, key=lambda r: (r.event_at, r.symbol)):
                pending = broker.pending.get(row.symbol)
                if pending is None:
                    continue
                signal_at, request, kw = pending
                if row.event_at.date() != signal_at.date():
                    broker.pending.pop(row.symbol)
                    continue
                if not signal_at < row.event_at <= signal_at + timedelta(minutes=2) or row.volume <= 0:
                    continue
                market.upsert_stock(Stock(symbol=row.symbol, name=row.symbol), row.open_price)
                clock[0], broker.defer = row.event_at, False
                if request.side == OrderSide.BUY:
                    # Freeze quantity at signal time; later candles cannot size it.
                    old_sizing, old_quantity = engine.sizing_mode, engine.order_quantity
                    engine.sizing_mode, engine.order_quantity = 'QUANTITY', request.quantity
                    try:
                        if engine._buy(row.symbol, row.open_price):
                            engine._entry_armed.discard(row.symbol)
                            bought.add(row.symbol)
                    finally:
                        engine.sizing_mode, engine.order_quantity = old_sizing, old_quantity
                else:
                    engine._sell(row.symbol, kw.get('reason', 'Pending exit'), lot_id=kw.get('lot_id'))
                broker.pending.pop(row.symbol)
                broker.defer = True
                clock[0] = at
            for symbol, (signal_at, _, _) in list(broker.pending.items()):
                if at - signal_at > timedelta(minutes=3):
                    broker.pending.pop(symbol)
        changed = set()
        for row in sorted(batch, key=lambda r: (r.event_at, r.symbol)):
            history[row.symbol][row.event_at] = row
            changed.add(row.symbol)
        candles = {}
        for symbol in sorted(changed):
            known = sorted(history[symbol].values(), key=lambda r: r.event_at)
            latest = known[-1]
            market.upsert_stock(Stock(symbol=symbol, name=symbol), latest.close_price)
            # Backfills warm the averages but must not cause trades on old prices.
            if latest.volume <= 0 or at - (latest.event_at + timedelta(minutes=1)) > timedelta(minutes=2):
                stale_batches += 1
                continue
            candles[symbol] = [LiveStockCandle(timestamp=r.event_at, open_price=r.open_price,
                high_price=r.high_price, low_price=r.low_price, close_price=r.close_price,
                volume=r.volume) for r in known[-config.long_period-1:]]
        if mode == 'moving_average' and candles:
            engine.step(candles=candles)
        elif mode == 'buy_and_hold':
            # Same order budget/capital/costs, first fresh observation per symbol.
            for symbol in sorted(candles):
                if symbol not in bought and engine._within_hours():
                    quantity = engine._buy_quantity(symbol)
                    if quantity:
                        order = broker.submit(OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=quantity))
                        if order.status == OrderStatus.FILLED:
                            bought.add(symbol)
        for order in list(reversed(broker.orders()))[before:]:
            entry = order.model_dump(mode='json')
            entry.update(run_id='backtest', observed_at=at.isoformat())
            trades.append(entry)
        # Liquidation value includes hypothetical exit costs for open positions.
        equity = broker.cash_balance() + sum((broker.exit_proceeds(s, broker.holding_quantity(s),
                   market.quote(s).price) for s in symbols), D(0))
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)
        curve.append({'at': at.isoformat(), 'equity': str(equity)})
    daily_closes = {}
    for point in curve:
        daily_closes[datetime.fromisoformat(point['at']).astimezone(KST).date().isoformat()] = D(point['equity'])
    daily = []
    previous = config.initial_cash
    for day, closing in sorted(daily_closes.items()):
        daily.append({'date': day, 'closing_equity': str(closing), 'net_change': str(closing - previous)})
        previous = closing
    sells = [t for t in trades if t['side'] == 'SELL' and t['status'] == 'FILLED']
    profits = [D(t['realized_profit'] or '0') for t in sells]
    streak = max_streak = 0
    for profit in profits:
        streak = streak + 1 if profit < 0 else 0
        max_streak = max(max_streak, streak)
    gains = sum((p for p in profits if p > 0), D(0))
    losses = -sum((p for p in profits if p < 0), D(0))
    filled = [t for t in trades if t['status'] == 'FILLED']
    return {'mode': mode, 'summary': {'final_liquidation_equity': str(equity),
        'net_profit': str(equity - config.initial_cash),
        'return_percent': str((equity / config.initial_cash - 1) * 100),
        'max_drawdown_percent': str(drawdown), 'filled_orders': len(filled),
        'closed_sell_orders': len(sells), 'win_rate_percent': str(sum(p > 0 for p in profits) / D(len(profits)) * 100) if profits else None,
        'profit_factor': str(gains / losses) if losses else None,
        'max_consecutive_losing_sell_orders': max_streak,
        'average_closed_profit': str(sum(profits) / len(profits)) if profits else None,
        'fees': str(sum(D(t['fee']) for t in filled)), 'taxes': str(sum(D(t['tax']) for t in filled)),
        'slippage': str(sum(D(t['slippage']) for t in filled)),
        'open_positions': {s: broker.holding_quantity(s) for s in symbols if broker.holding_quantity(s)},
        'unfilled_pending_signals': len(broker.pending),
        'stale_or_zero_volume_symbol_batches_skipped': stale_batches}, 'trades': trades, 'equity_curve': curve, 'daily_results': daily}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', help='Inclusive KST date YYYY-MM-DD')
    parser.add_argument('--end', help='Inclusive KST date YYYY-MM-DD')
    parser.add_argument('--symbols', help='Comma-separated stock codes')
    parser.add_argument('--strategy-id', type=int, help='Use saved strategy settings and targets')
    parser.add_argument('--execution', choices=['next_open', 'close'], default='next_open')
    parser.add_argument('--no-risk', action='store_true', help='Disable default account limits for comparison')
    parser.add_argument('--output', default='artifacts/ml/generated/backtest')
    args = parser.parse_args()
    config = Config(fee_rate=settings.paper_fee_rate, sell_tax_rate=settings.paper_sell_tax_rate,
                    slippage_rate=settings.paper_slippage_rate, execution=args.execution, risk_enabled=not args.no_risk)
    symbols = args.symbols.split(',') if args.symbols else None
    strategy = None
    with connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        if args.strategy_id:
            strategy = conn.execute('SELECT * FROM live_strategies WHERE id=%s', (args.strategy_id,)).fetchone()
            if strategy is None:
                parser.error('Saved strategy does not exist')
            fields = {k: strategy[k] for k in asdict(config) if k in strategy}
            for k in ('trading_start', 'trading_end'):
                fields[k] = fields[k].strftime('%H:%M')
            config = Config(**{**asdict(config), **fields})
            symbols = symbols or [r['symbol'] for r in conn.execute(
                'SELECT symbol FROM live_strategy_symbols WHERE strategy_id=%s ORDER BY symbol', (args.strategy_id,))]
            symbols = symbols or [strategy['symbol']]
        conditions, params = ["source='TOSS'", "interval='1m'"], []
        if symbols:
            conditions.append('symbol=ANY(%s)')
            params.append(symbols)
        if args.start:
            conditions.append('available_at >= %s')
            params.append(datetime.fromisoformat(args.start).replace(tzinfo=KST))
        if args.end:
            conditions.append('available_at < %s')
            params.append(datetime.fromisoformat(args.end).replace(tzinfo=KST) + timedelta(days=1))
        selected = conn.execute('SELECT ' + ','.join(RawCandle.__dataclass_fields__) +
            ' FROM ml_raw_candles WHERE ' + ' AND '.join(conditions) + ' ORDER BY available_at,symbol,event_at', params).fetchall()
        quality_reports = conn.execute('SELECT report_date,status FROM ml_data_quality_reports ORDER BY report_date').fetchall()
    rows = [RawCandle(**r) for r in selected]
    if not rows:
        parser.error('No stored candles match the selection')
    data = json.dumps([asdict(r) for r in rows], default=str, sort_keys=True)
    digest = hashlib.sha256(data.encode()).hexdigest()
    results = [replay(rows, config, mode) for mode in ('moving_average', 'buy_and_hold', 'cash')]
    zero = Config(**{**asdict(config), 'fee_rate': D(0), 'sell_tax_rate': D(0), 'slippage_rate': D(0)})
    results.append({**replay(rows, zero), 'mode': 'moving_average_zero_cost'})
    code_files = [Path(__file__), Path(__file__).parents[1] / 'strategy.py', Path(__file__).parents[1] / 'paper.py',
                  Path(__file__).parents[1] / 'simulator.py', Path(__file__).parents[1] / 'models.py',
                  Path(__file__).parents[1] / 'risk.py', Path(__file__).with_name('data_pipeline.py')]
    code_digest = hashlib.sha256(b''.join(p.read_bytes() for p in code_files)).hexdigest()
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    coverage = {}
    for symbol in sorted({r.symbol for r in rows}):
        times = sorted(r.event_at for r in rows if r.symbol == symbol)
        gaps = [(b - a).total_seconds() / 60 - 1 for a, b in zip(times, times[1:])
                if a.date() == b.date() and b - a > timedelta(minutes=1)]
        coverage[symbol] = {'candles': len(times), 'first_event_at': times[0], 'last_event_at': times[-1],
                            'missing_minutes_between_observations': sum(gaps)}
    run_id = hashlib.sha256((digest + code_digest + json.dumps(asdict(config), default=str, sort_keys=True)).encode()).hexdigest()[:16]
    report = {'run_id': run_id, 'created_at': datetime.now(KST), 'config': asdict(config), 'strategy_id': args.strategy_id,
        'strategy_name': strategy['name'] if strategy else 'Moving average baseline',
        'git_revision': revision, 'code_sha256': code_digest, 'data_sha256': digest,
        'candles': len(rows), 'symbols': sorted({r.symbol for r in rows}), 'coverage': coverage,
        'observed_days': sorted({r.event_at.astimezone(KST).date().isoformat() for r in rows}),
        'risk_preset': PRESETS[RiskPreset.DEFAULT] if config.risk_enabled else None,
        'quality_reports': quality_reports,
        'missing_target_symbols': sorted(set(symbols or []) - {r.symbol for r in rows}),
        'first_available_at': min(r.available_at for r in rows), 'last_available_at': max(r.available_at for r in rows),
        'assumptions': ['Replay uses available_at; historical backfills cannot trigger old trades.',
            f'Execution: {config.execution}; next_open uses the first observed positive-volume bar open strictly after signal availability within 2 minutes, same day.',
            f'Default account risk preset enabled: {config.risk_enabled}; does not load or modify running account limits.',
            'PAPER rules reused; historical bid/ask, partial fills and volume capacity are not simulated.',
            'Open positions remain open; final equity uses hypothetical net liquidation proceeds.',
            'Buy and hold uses same per-symbol order budget; symbols processed in code order.',
            'Partial single-day data is an engineering check, not evidence of profitability.'], 'results': results}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'report.json').write_text(json.dumps(report, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'input.json').write_text(data, encoding='utf-8')
    for result in results:
        with (output / (result['mode'] + '-equity.csv')).open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['at', 'equity'])
            writer.writeheader()
            writer.writerows(result['equity_curve'])
        with (output / (result['mode'] + '-daily.csv')).open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['date', 'closing_equity', 'net_change'])
            writer.writeheader()
            writer.writerows(result['daily_results'])
    lines = ['# First cost-inclusive backtest', '', f'Candles: {len(rows)}; symbols: {len(report["symbols"])}', '',
        '| Benchmark | Net profit (KRW) | Return % | Max drawdown % | Filled orders | Closed sell orders |',
        '|---|---:|---:|---:|---:|---:|']
    for result in results:
        s = result['summary']
        lines.append(f'| {result["mode"]} | {D(s["net_profit"]):,.2f} | {D(s["return_percent"]):.4f} | {D(s["max_drawdown_percent"]):.4f} | {s["filled_orders"]} | {s["closed_sell_orders"]} |')
    lines += ['', f'Execution: {config.execution}; default risk limits: {config.risk_enabled}',
              'Observed dates: ' + ', '.join(report['observed_days']),
              'Saved quality reports: ' + ', '.join(f'{q["report_date"]}: {q["status"]}' for q in quality_reports),
              '', *('- ' + a for a in report['assumptions']), '', 'Missing targets: ' + ', '.join(report['missing_target_symbols']), '',
              '| Symbol | Candles | First event | Last event | Internal missing minutes |', '|---|---:|---|---|---:|']
    for symbol, item in coverage.items():
        lines.append(f'| {symbol} | {item["candles"]} | {item["first_event_at"]} | {item["last_event_at"]} | {item["missing_minutes_between_observations"]} |')
    (output / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output.resolve()), 'candles': len(rows),
                      'results': [{'mode': r['mode'], **r['summary']} for r in results]}, ensure_ascii=True))


if __name__ == '__main__':
    main()
