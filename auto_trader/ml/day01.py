"""DAY-01: pure, synthetic, fixed-universe opening-range replay. No I/O."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Iterable

from ..risk_policy import RiskPolicy

D = Decimal
KST = timezone(timedelta(hours=9), 'Asia/Seoul')
MINUTE = timedelta(minutes=1)


def _number(value, name, *, positive=False):
    if not isinstance(value, D) or not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError(f'{name} must be a finite {"positive" if positive else "nonnegative"} Decimal')


def _aware(at):
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('timezone-aware datetime required')
    return at.astimezone(KST)


@dataclass(frozen=True)
class Candle:
    symbol: str
    event_at: datetime  # inclusive minute start
    available_at: datetime  # completed observation first usable
    open_price: D
    high_price: D
    low_price: D
    close_price: D
    volume: D
    turnover: D | None = None

    def validate(self):
        if not isinstance(self.symbol, str) or not self.symbol or self.symbol != self.symbol.strip().upper():
            raise ValueError('symbol must be nonempty normalized text')
        event, available = _aware(self.event_at), _aware(self.available_at)
        if event.second or event.microsecond or available < event + MINUTE:
            raise ValueError('aligned completed one-minute candle required')
        if not time(9) <= event.time() < time(15, 30):
            raise ValueError('event outside KST regular session')
        for name in ('open_price', 'high_price', 'low_price', 'close_price'):
            _number(getattr(self, name), name, positive=True)
        _number(self.volume, 'volume')
        if self.high_price < max(self.open_price, self.close_price, self.low_price) or self.low_price > min(self.open_price, self.close_price):
            raise ValueError('inconsistent OHLC')
        if self.turnover is not None:
            _number(self.turnover, 'turnover')
            if not self.low_price * self.volume <= self.turnover <= self.high_price * self.volume:
                raise ValueError('turnover outside OHLC-volume bounds')


@dataclass(frozen=True)
class OpenQuote:
    """Synthetic minute-open snapshot; volume is capacity KNOWN at snapshot time."""
    symbol: str
    event_at: datetime
    available_at: datetime
    price: D
    executable_volume: D

    def validate(self):
        if not isinstance(self.symbol, str) or not self.symbol or self.symbol != self.symbol.strip().upper():
            raise ValueError('symbol must be nonempty normalized text')
        event, available = _aware(self.event_at), _aware(self.available_at)
        if event.second or event.microsecond or not time(9) <= event.time() < time(15, 30):
            raise ValueError('aligned KST session minute-open required')
        if available < event:
            raise ValueError('open quote cannot arrive before its event')
        _number(self.price, 'price', positive=True)
        _number(self.executable_volume, 'executable_volume')


@dataclass(frozen=True)
class Config:
    execution: str = 'next_open'
    initial_cash: D = D('1000000')
    order_quantity: int = 10
    volume_multiplier: D = D('1.2')
    stop_rate: D = D('0.01')
    target_rate: D = D('0.02')
    min_reward_risk: D = D('2')
    vwap_mode: str = 'exact'  # exact turnover or explicit typical approximation
    fee_rate: D = D('0.00015')
    sell_tax_rate: D = D('0.002')
    slippage_rate: D = D('0.0005')
    max_order_amount: D = D('100000')  # includes buy fee
    max_symbol_amount: D = D('200000')
    max_total_investment: D = D('500000')
    max_positions: int = 3
    min_cash_ratio: D = D('10')  # percent, same unit as RiskPolicy
    daily_loss_limit: D = D('20000')  # net liquidation equity loss, KRW
    daily_order_limit: int = 10  # accepted ENTRY orders; risk exits are always allowed
    volume_participation: D = D('1')  # per executable minute, integer capacity
    max_signal_to_fill_minutes: D = D('2')  # exclusive ENTRY age limit

    def validate(self):
        if self.execution not in {'next_open', 'legacy_close'}:
            raise ValueError('execution must be next_open or legacy_close')
        for name in ('initial_cash', 'volume_multiplier', 'stop_rate', 'target_rate',
                     'min_reward_risk', 'max_order_amount', 'max_symbol_amount',
                     'max_total_investment', 'daily_loss_limit', 'volume_participation',
                     'max_signal_to_fill_minutes'):
            _number(getattr(self, name), name, positive=True)
        for name in ('fee_rate', 'sell_tax_rate', 'slippage_rate', 'min_cash_ratio'):
            _number(getattr(self, name), name)
        for name in ('order_quantity', 'max_positions', 'daily_order_limit'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f'{name} must be a positive integer')
        if self.vwap_mode not in {'exact', 'typical_approximation'}:
            raise ValueError('explicit VWAP mode required')
        if (self.stop_rate >= 1 or self.slippage_rate >= 1 or self.target_rate / self.stop_rate < self.min_reward_risk
                or self.fee_rate + self.sell_tax_rate > 1 or self.min_cash_ratio > 100 or self.volume_participation > 1
                or self.max_signal_to_fill_minutes > 130):
            raise ValueError('invalid rate, reward/risk or participation')


def select_universe(symbols: Iterable[str]) -> tuple[str, ...]:
    """Caller supplies a fixed ex-ante universe; never rank using replay data."""
    if isinstance(symbols, str):
        raise ValueError('universe must be a sequence of symbols')
    values = list(symbols)
    if not values or any(not isinstance(s, str) or not s or s != s.strip().upper() for s in values):
        raise ValueError('nonempty normalized universe required')
    return tuple(sorted(set(values)))


def _validated(rows):
    result, keys = [], set()
    for row in rows:
        row.validate()
        row = replace(row, event_at=_aware(row.event_at), available_at=_aware(row.available_at))
        key = row.symbol, row.event_at
        if key in keys:
            raise ValueError('duplicate symbol/event (including conflicting sources)')
        keys.add(key)
        result.append(row)
    return result


@dataclass(frozen=True)
class FiveMinuteBar:
    symbol: str
    event_at: datetime
    end_at: datetime
    available_at: datetime
    open_price: D
    high_price: D
    low_price: D
    close_price: D
    volume: D
    turnover: D | None


def aggregate_five_minutes(rows: Iterable[Candle], as_of: datetime) -> list[FiveMinuteBar]:
    as_of = _aware(as_of)
    groups = defaultdict(dict)
    for row in _validated(rows):
        if row.available_at <= as_of:
            start = row.event_at.replace(minute=row.event_at.minute // 5 * 5)
            groups[row.symbol, start][row.event_at] = row
    result = []
    for (symbol, start), bucket in sorted(groups.items()):
        expected = [start + i * MINUTE for i in range(5)]
        if not all(t in bucket for t in expected):
            continue
        bars = [bucket[t] for t in expected]
        result.append(FiveMinuteBar(symbol, start, start + 5 * MINUTE,
            max(b.available_at for b in bars), bars[0].open_price,
            max(b.high_price for b in bars), min(b.low_price for b in bars), bars[-1].close_price,
            sum((b.volume for b in bars), D(0)),
            sum((b.turnover for b in bars), D(0)) if all(b.turnover is not None for b in bars) else None))
    return result


@dataclass(frozen=True)
class Decision:
    enter: bool
    reason: str
    at: datetime
    range_high: D | None = None
    vwap: D | None = None
    previous_vwap: D | None = None


def evaluate_entry(history: Iterable[Candle], at: datetime, config: Config) -> Decision:
    """Pure decision for ONE symbol and ONE session, using only known inputs."""
    config.validate()
    at = _aware(at)
    rows = [r for r in _validated(history) if r.available_at <= at and r.event_at.date() == at.date()]
    if len({r.symbol for r in rows}) > 1:
        raise ValueError('evaluate_entry expects one symbol')
    def no(reason):
        return Decision(False, reason, at)
    if not time(9, 20) <= at.time() < time(11, 30):
        return no('OUTSIDE_ENTRY_WINDOW')
    opening = at.replace(hour=9, minute=0, second=0, microsecond=0)
    known = {r.event_at: r for r in rows}
    if not all(opening + i * MINUTE in known for i in range(15)):
        return no('INCOMPLETE_OPENING_RANGE')
    five = aggregate_five_minutes(rows, at)
    if not five or five[-1].end_at != at or five[-1].available_at != at:
        return no('NO_FRESH_FIVE_MINUTE_BAR')
    current = five[-1]
    if len(five) < 2 or five[-2].end_at != current.event_at:
        return no('INCOMPLETE_PREVIOUS_FIVE_MINUTE_BAR')
    elapsed = int((at - opening) / MINUTE)
    if not all(opening + i * MINUTE in known for i in range(elapsed)):
        return no('INCOMPLETE_SESSION_VWAP')
    ordered = [known[opening + i * MINUTE] for i in range(elapsed)]
    if config.vwap_mode == 'exact' and any(r.turnover is None for r in ordered):
        return no('MISSING_EXACT_TURNOVER')
    def vwap(subset):
        volume = sum((r.volume for r in subset), D(0))
        total = sum(((r.turnover if config.vwap_mode == 'exact' else
            (r.high_price + r.low_price + r.close_price) / 3 * r.volume) for r in subset), D(0))
        return total / volume if volume else None
    now_vwap, previous_vwap = vwap(ordered), vwap(ordered[:-5])
    high = max(known[opening + i * MINUTE].high_price for i in range(15))
    reason = 'ENTRY'
    if current.close_price <= high:
        reason = 'NO_RANGE_BREAKOUT'
    elif current.volume <= 0 or five[-2].volume <= 0 or current.volume < five[-2].volume * config.volume_multiplier:
        reason = 'INSUFFICIENT_VOLUME_INCREASE'
    elif now_vwap is None or previous_vwap is None or current.close_price <= now_vwap or now_vwap <= previous_vwap:
        reason = 'NO_RISING_VWAP'
    return Decision(reason == 'ENTRY', reason, at, high, now_vwap, previous_vwap)


def execution_costs(reference: D, quantity: int, side: str, config: Config) -> dict:
    """PAPER arithmetic: adverse slippage, per-fill KRW fee/tax ROUND_DOWN."""
    price = reference * (1 + config.slippage_rate if side == 'BUY' else 1 - config.slippage_rate)
    amount = price * quantity
    fee = (amount * config.fee_rate).quantize(D(1), rounding=ROUND_DOWN)
    tax = (amount * config.sell_tax_rate).quantize(D(1), rounding=ROUND_DOWN) if side == 'SELL' else D(0)
    return dict(price=price, amount=amount, fee=fee, tax=tax,
                slippage=abs(price - reference) * quantity,
                cash_delta=-amount-fee if side == 'BUY' else amount-fee-tax)


def replay(rows: Iterable[Candle], config: Config = Config(), *, universe: Iterable[str],
           open_quotes: Iterable[OpenQuote] = ()) -> dict:
    """Deterministic availability-clock simulation; returns Decimal/datetime values."""
    config.validate()
    symbols = select_universe(universe)
    # Unselected observations cannot influence clocks, validation, equity or selection.
    data = _validated(r for r in rows if r.symbol in symbols)
    if not data:
        raise ValueError('no selected candles')
    quotes, quote_keys = [], set()
    for quote in open_quotes:
        if quote.symbol not in symbols:
            continue
        quote.validate()
        quote = replace(quote, event_at=_aware(quote.event_at), available_at=_aware(quote.available_at))
        key = quote.symbol, quote.event_at
        if key in quote_keys:
            raise ValueError('duplicate symbol/open event (including conflicts)')
        quote_keys.add(key)
        quotes.append(quote)
    if config.execution == 'legacy_close' and quotes:
        raise ValueError('legacy_close does not accept selected open_quotes')
    quotes_at, quote_history = defaultdict(list), {}
    batches = defaultdict(list)
    days = sorted({r.event_at.date() for r in data} | {q.event_at.date() for q in quotes})
    for row in data:
        batches[row.available_at].append(row)
    for quote in quotes:
        quotes_at[quote.available_at].append(quote)
        batches[quote.available_at]
    max_entry_age = timedelta(minutes=float(config.max_signal_to_fill_minutes))
    for day in days:
        for hour, minute in ((9, 0), (14, 50), (15, 10)):
            batches[datetime.combine(day, time(hour, minute), KST)]
        # Expiry advances even if no subsequent quote arrives; deadline is exclusive.
        for offset in range(20, 150, 5):
            signal_at = datetime.combine(day, time(9), KST) + offset * MINUTE
            deadline = min(signal_at + max_entry_age, datetime.combine(day, time(11, 30), KST))
            batches[deadline]
    cash = config.initial_cash
    positions, pending, history, marks = {}, {}, defaultdict(dict), {}
    mark_events, mark_received, late_risk_symbols = {}, {}, set()
    mark_types = {}
    signals, trades, reasons, failures, curve, daily = [], [], [], [], [], []
    entry_counts, order_counts = defaultdict(int), defaultdict(int)
    entered, finalized = set(), set()
    baselines = {}

    def equity():
        return cash + sum((execution_costs(marks[s], p['quantity'], 'SELL', config)['cash_delta']
                           for s, p in positions.items()), D(0))

    def reason(at, symbol, code, **details):
        reasons.append(dict(at=at, symbol=symbol, reason=code, **details))

    def queue_exit(symbol, at, code, trigger=None):
        if symbol in pending and pending[symbol]['side'] == 'SELL':
            existing = pending[symbol]
            # Newly learned adverse information supersedes a target/liquidation order.
            if code not in {'STOP', 'AMBIGUOUS_STOP_FIRST'} or (
                existing['reason'] in {'STOP', 'AMBIGUOUS_STOP_FIRST'} and trigger >= existing['trigger']):
                return
            reason(at, symbol, 'EXIT_REPLACED_BY_STOP')
        order_counts[at.date()] += 1
        order = dict(symbol=symbol, side='SELL', at=at, reason=code,
                     quantity=positions[symbol]['quantity'], trigger=trigger)
        pending[symbol] = order
        signals.append(dict(order))

    def risk(at, symbol, price, quantity):
        cost = execution_costs(price, quantity, 'BUY', config)
        asset = equity()
        if any(p['unresolved'] for p in positions.values()):
            return 'UNRESOLVED_EXPOSURE'
        if asset - baselines[at.date()] <= -config.daily_loss_limit:
            return 'DAILY_LOSS_LIMIT'
        if late_risk_symbols:
            return 'LATE_RISK_EXIT_PENDING'
        if symbol in positions or len(positions) >= config.max_positions:
            return 'POSITION_LIMIT'
        if not RiskPolicy.order_amount_allowed(config, cost['amount'], cost['fee']):
            return 'ORDER_AMOUNT_LIMIT'
        if not RiskPolicy.symbol_amount_allowed(config, D(0), cost['amount']):
            return 'SYMBOL_EXPOSURE_LIMIT'
        invested = sum((marks[s] * p['quantity'] for s, p in positions.items()), D(0))
        if not RiskPolicy.total_investment_allowed(config, invested, cost['amount']):
            return 'TOTAL_EXPOSURE_LIMIT'
        remaining = cash + cost['cash_delta']
        if remaining < 0:
            return 'INSUFFICIENT_CASH'
        # Costs of the proposed immediate net liquidation are included in denominator.
        post_equity = asset + cost['cash_delta'] + execution_costs(price, quantity, 'SELL', config)['cash_delta']
        if RiskPolicy.cash_ratio(remaining, post_equity) < config.min_cash_ratio:
            return 'MIN_CASH_RATIO'
        return None

    def monitor_exit(row, at, *, late=False):
        p = positions.get(row.symbol)
        if (p is None or p['unresolved'] or p['day'] != at.date()
                or row.event_at.date() != at.date() or row.event_at < p['entry_at'] or row.volume <= 0):
            return
        stop, target = row.low_price <= p['stop'], row.high_price >= p['target']
        if stop or target:
            if late:
                # Historical risk is known now, but it is never current fill evidence.
                late_risk_symbols.add(row.symbol)
                reason(at, row.symbol, 'LATE_RISK_OBSERVATION', event_at=row.event_at)
            if stop:
                queue_exit(row.symbol, at, 'AMBIGUOUS_STOP_FIRST' if target else 'STOP', min(p['stop'], row.open_price))
            else:
                queue_exit(row.symbol, at, 'TARGET', p['target'])

    def mark(symbol, event_at, at, price, kind):
        if at.date() in finalized or positions.get(symbol, {}).get('unresolved') or event_at.date() != at.date():
            return
        if (symbol not in mark_events or event_at > mark_events[symbol]
                or (event_at == mark_events[symbol] and kind == 'COMPLETED_CANDLE' and mark_types[symbol] == 'OPEN_QUOTE')):
            marks[symbol], mark_events[symbol], mark_received[symbol] = price, event_at, at
            mark_types[symbol] = kind
        else:
            reason(at, symbol, 'OLDER_OBSERVATION_MARK_PRESERVED', event_at=event_at,
                   mark_event_at=mark_events[symbol])

    def fill(symbol, at, event_at, reference, capacity, evidence_close, price_source, bar_open=None):
        nonlocal cash
        order = pending[symbol]
        if order['side'] == 'BUY' and not time(9, 20) <= at.time() < time(11, 30):
            reason(at, symbol, 'ENTRY_EXPIRED')
            pending.pop(symbol)
            return False
        quantity = min(order['quantity'], int(capacity * config.volume_participation))
        if quantity <= 0:
            reason(at, symbol, 'NO_EXECUTABLE_CAPACITY')
            return False
        if order['side'] == 'SELL':
            if positions[symbol]['unresolved']:
                return False
            if config.execution == 'legacy_close':
                if order['reason'] in {'STOP', 'AMBIGUOUS_STOP_FIRST'}:
                    reference = min(reference, bar_open, order['trigger'])
                elif order['reason'] == 'TARGET':
                    reference = min(reference, order['trigger'])
        else:
            breach = risk(at, symbol, reference, quantity)
            if breach:
                reason(at, symbol, breach, phase='FILL')
                pending.pop(symbol)
                return False
        cost = execution_costs(reference, quantity, order['side'], config)
        cash += cost['cash_delta']
        realized = None
        buying = order['side'] == 'BUY'
        if buying:
            positions[symbol] = dict(quantity=quantity, entry_price=cost['price'],
                cost_basis=-cost['cash_delta'], day=at.date(), entry_at=at,
                stop=cost['price'] * (1-config.stop_rate), target=cost['price'] * (1+config.target_rate), unresolved=False)
            pending.pop(symbol)
            if quantity < order['quantity']:
                reason(at, symbol, 'ENTRY_REMAINDER_CANCELLED', quantity=order['quantity']-quantity)
        else:
            p = positions[symbol]
            basis = p['cost_basis'] * quantity / p['quantity']
            realized = cost['cash_delta'] - basis
            p['quantity'] -= quantity
            p['cost_basis'] -= basis
            order['quantity'] -= quantity
            if not p['quantity']:
                positions.pop(symbol)
                pending.pop(symbol)
                late_risk_symbols.discard(symbol)
        trades.append(dict(symbol=symbol, side=order['side'], quantity=quantity,
            signal_at=order['at'], decision_at=order['at'], event_at=event_at,
            execution_at=at, observed_at=at, observation_at=at, price_source=price_source,
            evidence_close=evidence_close,
            reference_price=reference, reason=order['reason'], realized_profit=realized, **cost))
        return buying

    def observe_open_risk(quote, at, late=False):
        p = positions.get(quote.symbol)
        if (not p or p['unresolved'] or p['day'] != at.date() or quote.event_at.date() != at.date()
                or quote.event_at <= p['entry_at'] or quote.executable_volume <= 0):
            return
        stop, target = quote.price <= p['stop'], quote.price >= p['target']
        if stop or target:
            if late:
                late_risk_symbols.add(quote.symbol)
                reason(at, quote.symbol, 'LATE_RISK_OBSERVATION', event_at=quote.event_at)
            queue_exit(quote.symbol, at, 'STOP' if stop else 'TARGET',
                       min(p['stop'], quote.price) if stop else p['target'])

    for at, batch in sorted(batches.items()):
        day = at.date()
        baselines.setdefault(day, equity())
        for symbol, order in list(pending.items()):
            if order['side'] != 'BUY':
                continue
            if at - order['at'] >= max_entry_age:
                if config.execution == 'next_open':
                    reason(at, symbol, 'NO_EXECUTABLE_OPEN_QUOTE_BEFORE_EXPIRY', signal_at=order['at'])
                reason(at, symbol, 'ENTRY_SIGNAL_TOO_OLD', signal_at=order['at'], phase='EXPIRY')
                pending.pop(symbol)
            elif at.time() >= time(11, 30) or day != order['at'].date():
                reason(at, symbol, 'ENTRY_EXPIRED', phase='EXPIRY')
                pending.pop(symbol)
        # Timers are explicit events even when there is no market data.
        if day in days and at.time() == time(14, 50):
            for symbol, position in sorted(positions.items()):
                if position['day'] == day and not position['unresolved']:
                    queue_exit(symbol, at, 'SESSION_LIQUIDATION')
        if day in days and at.time() == time(15, 10):
            for symbol, position in sorted(positions.items()):
                if position['day'] == day:
                    position['unresolved'] = True
                    failure = dict(at=at, symbol=symbol, reason='UNLIQUIDATED_AT_1510', quantity=position['quantity'])
                    failures.append(failure)
                    reason(at, symbol, failure['reason'], quantity=position['quantity'])
            for symbol, order in list(pending.items()):
                if order['at'].date() == day:
                    if config.execution == 'next_open':
                        reason(at, symbol, 'NO_EXECUTABLE_OPEN_QUOTE_BEFORE_DEADLINE', side=order['side'])
                    reason(at, symbol, 'UNFILLED_ORDER_AT_1510', quantity=order['quantity'], side=order['side'])
                    pending.pop(symbol)
            finalized.add(day)

        # Phase 1: clocks above; phase 2: only information physically received at open.
        incoming_quotes = sorted(quotes_at[at], key=lambda q: (q.symbol, q.event_at))
        for quote in incoming_quotes:
            quote_history[quote.symbol, quote.event_at] = quote
            known_candle = history[quote.symbol].get(quote.event_at)
            if known_candle is not None and quote.price != known_candle.open_price:
                reason(at, quote.symbol, 'OPEN_CANDLE_PRICE_MISMATCH', event_at=quote.event_at,
                       quote_price=quote.price, candle_open=known_candle.open_price)
            if quote.available_at != quote.event_at:
                reason(at, quote.symbol, 'LATE_OPEN_QUOTE_NOT_EXECUTABLE', event_at=quote.event_at)
            if quote.executable_volume > 0:
                mark(quote.symbol, quote.event_at, at, quote.price, 'OPEN_QUOTE')
        for quote in incoming_quotes:
            if quote.available_at > quote.event_at:
                observe_open_risk(quote, at, late=True)
        fresh_quotes = [q for q in incoming_quotes if q.event_at == at and day not in finalized]
        fresh_quotes.sort(key=lambda q: (pending.get(q.symbol, {}).get('side') != 'SELL', q.symbol))
        for quote in fresh_quotes:
            order = pending.get(quote.symbol)
            if order is not None and order['at'].date() == day and order['at'] < quote.event_at:
                fill(quote.symbol, at, quote.event_at, quote.price, quote.executable_volume, None, 'OPEN_QUOTE')
        # An open can reveal a new gap trigger, but that new order cannot fill this open.
        for quote in fresh_quotes:
            observe_open_risk(quote, at)

        # Phase 3: completed candles arriving now cannot influence phase-2 fills.
        ordered = sorted(batch, key=lambda r: (r.symbol, r.event_at))
        # First consume all known observations/marks before account-wide risk checks.
        for row in ordered:
            history[row.symbol][row.event_at] = row
            opening_quote = quote_history.get((row.symbol, row.event_at))
            if opening_quote is not None and opening_quote.price != row.open_price:
                reason(at, row.symbol, 'OPEN_CANDLE_PRICE_MISMATCH', event_at=row.event_at,
                       quote_price=opening_quote.price, candle_open=row.open_price)
            if row.event_at.date() != day or at != row.event_at + MINUTE:
                reason(at, row.symbol, 'LATE_OBSERVATION_NOT_EXECUTABLE', event_at=row.event_at)
            if (row.event_at.date() == day and day not in finalized and row.volume > 0
                    and not positions.get(row.symbol, {}).get('unresolved')):
                mark(row.symbol, row.event_at, at, row.close_price, 'COMPLETED_CANDLE')

        # Delayed candle risk precedes legacy fills; next-open fills already used phase 2.
        if day not in finalized:
            for row in ordered:
                if at > row.event_at + MINUTE:
                    monitor_exit(row, at, late=True)

        bought_now = set()
        # Sells precede buys at the same information timestamp; symbols break ties.
        executable = [r for r in ordered if r.event_at.date() == day and at == r.event_at + MINUTE
                      and r.volume > 0 and day not in finalized]
        executable.sort(key=lambda r: (pending.get(r.symbol, {}).get('side') != 'SELL', r.symbol))
        for row in executable:
            if config.execution != 'legacy_close':
                continue
            symbol = row.symbol
            order = pending.get(symbol)
            if order is None or order['at'].date() != day or row.event_at < order['at']:
                continue
            if fill(symbol, at, row.event_at, row.close_price, row.volume,
                    row.close_price, 'LEGACY_CLOSE_PROXY', row.open_price):
                bought_now.add(symbol)

        # Stops/targets are decisions AFTER observation; never execute on their trigger bar.
        for row in executable:
            symbol = row.symbol
            if symbol in bought_now:
                continue
            monitor_exit(row, at)

        if day not in finalized:
            for symbol in symbols:
                own = [r for r in history[symbol].values() if r.event_at.date() == day]
                # Exactly one evaluation per aligned, fresh 5m boundary with fresh symbol data.
                if not any(r.symbol == symbol and r.event_at + MINUTE == at for r in ordered) or at.minute % 5 or at.second or at.microsecond:
                    continue
                decision = evaluate_entry(own, at, config)
                reason(at, symbol, decision.reason, vwap=decision.vwap, previous_vwap=decision.previous_vwap,
                       range_high=decision.range_high)
                if not decision.enter:
                    continue
                if any(p['unresolved'] for p in positions.values()):
                    reason(at, symbol, 'UNRESOLVED_EXPOSURE', phase='SIGNAL')
                    continue
                if (symbol, day) in entered or symbol in pending or symbol in positions:
                    reason(at, symbol, 'ALREADY_ENTERED_OR_PENDING')
                    continue
                if entry_counts[day] >= config.daily_order_limit:
                    reason(at, symbol, 'DAILY_ENTRY_ORDER_LIMIT')
                    continue
                price = max(own, key=lambda r: r.event_at).close_price
                breach = risk(at, symbol, price, config.order_quantity)
                if breach:
                    reason(at, symbol, breach, phase='SIGNAL')
                    continue
                entry_counts[day] += 1
                order_counts[day] += 1
                entered.add((symbol, day))
                order = dict(symbol=symbol, side='BUY', at=at, reason='ENTRY', quantity=config.order_quantity, trigger=None)
                pending[symbol] = order
                signals.append(dict(order))
        value = equity()
        curve.append(dict(at=at, cash=cash, net_liquidation_equity=value,
                          daily_net_change=value-baselines[day],
                          loss_limit_exceeded=value-baselines[day] <= -config.daily_loss_limit,
                          unresolved=any(p['unresolved'] for p in positions.values())))
        if at.time() == time(15, 10) and day in days:
            daily.append(dict(day=day, closing_equity=value, net_change=value-baselines[day],
                              entry_orders=entry_counts[day], total_orders=order_counts[day],
                              unresolved_quantities={s:p['quantity'] for s,p in positions.items() if p['unresolved']}))

    return dict(config=config, universe=symbols, vwap_mode=config.vwap_mode,
        execution_model=('causal_next_1m_open_quote' if config.execution == 'next_open'
                         else 'later_on_time_1m_close_with_adverse_trigger_proxy'),
        execution_evidence=dict(source=('explicit_open_quote' if config.execution == 'next_open' else 'completed_candle'),
            supplied_open_quotes=len(quotes), on_time_open_quotes=sum(q.available_at == q.event_at for q in quotes),
            late_open_quotes=sum(q.available_at > q.event_at for q in quotes),
            zero_or_subunit_capacity_quotes=sum(int(q.executable_volume * config.volume_participation) < 1 for q in quotes),
            missing_open_quotes=config.execution == 'next_open' and not quotes,
            capacity_source=('open_quote_executable_volume' if config.execution == 'next_open' else 'completed_minute_volume')),
        summary=dict(cash=cash, net_liquidation_equity=equity(),
            net_profit=equity()-config.initial_cash,
            realized_profit=sum((t['realized_profit'] for t in trades if t['realized_profit'] is not None), D(0)),
            fees=sum((t['fee'] for t in trades), D(0)), taxes=sum((t['tax'] for t in trades), D(0)),
            slippage=sum((t['slippage'] for t in trades), D(0)), filled_orders=len(trades),
            open_quote_fills=sum(t['price_source'] == 'OPEN_QUOTE' for t in trades),
            legacy_close_fills=sum(t['price_source'] == 'LEGACY_CLOSE_PROXY' for t in trades),
            open_positions={s:p['quantity'] for s,p in positions.items()},
            unresolved_quantities={s:p['quantity'] for s,p in positions.items() if p['unresolved']},
            overnight_gap_exposure_unmodeled=any(p['unresolved'] for p in positions.values()),
            frozen_valuation_symbols=sorted(s for s,p in positions.items() if p['unresolved']),
            entry_orders=sum(entry_counts.values()), total_orders=sum(order_counts.values()),
            status='UNLIQUIDATED' if positions else 'FLAT'),
        positions=positions,
        valuation_marks={s:dict(price=marks[s], event_at=mark_events[s], observed_at=mark_received[s],
                               frozen=p['unresolved'],
                               **({'price_source':mark_types[s]} if config.execution == 'next_open' else {}))
                         for s,p in positions.items()},
        signals=signals, trades=trades, reasons=reasons,
        failures=failures, equity_curve=curve, daily_results=daily)
