"""Append-only v1 pre-risk signal evidence; no order/execution dependencies."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import logging
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..database import connect
from .decision_pipeline import StrategyDecisionRecorder

VERSION = 'v1'
logger = logging.getLogger(__name__)
FIELDS = (
    'candidate_key contract_version engine_instance_id invocation_sequence run_id strategy_id strategy_name '
    'strategy_version execution_mode symbol candle_event_at quote_observed_at decision_at available_at '
    'quote_price quote_source short_period long_period history_count history_first_close history_last_close '
    'short_average long_average previous_trend trend entry_armed_before confirmation_count_before '
    'position_quantity_before signal_action signal_reason signal_decision entry_armed_after_signal '
    'confirmation_count_after_signal rising_condition baseline_buy_candidate entry_evaluation_performed metadata'
).split()


def validate_candidate(row):
    missing = set(FIELDS)-row.keys()
    if missing:
        raise ValueError(f'Missing candidate fields: {sorted(missing)}')
    if row['contract_version'] != VERSION or row['execution_mode'] not in ('PAPER', 'LIVE'):
        raise ValueError('Invalid contract version/execution mode')
    for field in ('quote_observed_at', 'decision_at', 'available_at'):
        value = row[field]
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError(f'{field} must be timezone-aware')
    if not row['quote_observed_at'] <= row['decision_at'] <= row['available_at']:
        raise ValueError('Invalid candidate timestamp order')
    at = row['candle_event_at']
    if at is not None and (at.utcoffset() is None or at > row['decision_at']):
        raise ValueError('Invalid candle_event_at')
    for field in ('quote_price',):
        value = Decimal(row[field])
        if not value.is_finite() or value <= 0:
            raise ValueError(f'Invalid {field}')
    for field in ('invocation_sequence', 'history_count', 'confirmation_count_before', 'position_quantity_before'):
        if not isinstance(row[field], int) or row[field] < 0:
            raise ValueError(f'Invalid {field}')
    if not 0 < row['short_period'] < row['long_period']:
        raise ValueError('Invalid MA periods')
    performed = row['entry_evaluation_performed']
    if not isinstance(performed, bool):
        raise ValueError('Invalid entry_evaluation_performed')
    if performed:
        if row['signal_action'] not in ('BUY', 'HOLD', 'SELL'):
            raise ValueError('Invalid signal_action')
        if row['baseline_buy_candidate'] is not (row['signal_action'] == 'BUY'):
            raise ValueError('Candidate must equal signal_action == BUY')
        if row['history_count'] < row['long_period']:
            raise ValueError('Insufficient evaluated history')
    elif any(row[k] is not None for k in ('signal_action', 'baseline_buy_candidate',
                                         'entry_armed_after_signal', 'confirmation_count_after_signal')):
        raise ValueError('Skipped evaluation is not a synthetic HOLD signal')
    closes = row['metadata'].get('history_closes', [])
    if len(closes) != row['history_count']:
        raise ValueError('Missing full input history')


def save_strategy_candidates(rows):
    if not rows:
        return 0
    for row in rows:
        validate_candidate(row)
    collected = datetime.now(timezone.utc)
    inserted = 0
    # Static identifiers only; values are always parameters. Retry keeps the same
    # invocation key. No UPDATE and no per-candle uniqueness that could hide resets.
    columns = FIELDS + ['collected_at']
    statement = ('INSERT INTO ml_strategy_candidates (' + ','.join(columns) + ') VALUES ('
                 + ','.join(['%s']*len(columns)) + ') ON CONFLICT (candidate_key) DO NOTHING RETURNING 1')
    with connect() as conn:
        for row in rows:
            values = [Jsonb(row[k]) if k == 'metadata' else row[k] for k in FIELDS]
            values.append(max(collected, row['available_at']))
            inserted += int(conn.execute(statement, values).fetchone() is not None)
    return inserted


class StrategyCandidateRecorder(StrategyDecisionRecorder):
    """Reuse existing queue/lock/batch/status/lifecycle pattern, with own sink."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._stopping = None

    def record(self, row):
        try:
            queued = deepcopy(row)  # Never retain mutable engine/signal state.
            validate_candidate(queued)
            with self._lock:
                if len(self._queue) >= self.max_queue:
                    raise ValueError('ML candidate queue full')
                self._queue.append(queued)
        except Exception as exc:
            self._dropped += 1
            self._last_error = str(exc)
            logger.warning('ML candidate dropped: %s', exc)

    async def flush(self):
        batch = self._take_batch()
        if not batch:
            return 0
        try:
            saved = await asyncio.to_thread(save_strategy_candidates, batch)
        except Exception as exc:
            # Keep old evidence, bound memory even while new events arrive.
            with self._lock:
                restored = list(batch)+list(self._queue)
                dropped = max(0, len(restored)-self.max_queue)
                self._queue.clear()
                self._queue.extend(restored[:self.max_queue])
            self._dropped += dropped
            self._failures += 1
            self._last_error = str(exc)
            logger.warning('ML candidate flush failed (overflow dropped=%s): %s', dropped, exc)
            return 0
        self._saved += saved
        self._last_saved_at = datetime.now(timezone.utc)
        self._last_error = None
        return saved

    async def start(self):
        if self._task is None or self._task.done():
            self._stopping = asyncio.Event()
            self._task = asyncio.create_task(self._run(), name='ml-strategy-candidates')

    async def _run(self):
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.flush_seconds)
            except TimeoutError:
                await self.flush()

    async def stop(self):
        # Finish any in-flight to_thread write before draining/closing the worker.
        # Cancelling that await would leave the DB thread running unobserved.
        if self._task is not None:
            self._stopping.set()
            await self._task
        self._task = None
        while self._queue:
            if not await self.flush():
                break


def capture_before(engine, quote, history, *, new_bar, previous_bar, state_reset, skip_reason=None):
    """Read-only, independent observation clock; failures never escape to trading.

    Only new completed bars are recorded. Same-bar generate_signal always HOLDs
    and cannot create a candidate. SIMULATED ticks have no candle identity and
    are explicitly marked in metadata, never eligible for Dataset v2's join.
    """
    if engine.candidate_recorder is None or not new_bar:
        return None
    try:
        observed = datetime.now(timezone.utc)
        closes = tuple(history)
        quantity = engine.broker.strategy_quantity(quote.symbol)  # Pure account read, before signal.
        return dict(candidate_key=str(uuid4()), contract_version=VERSION,
                    engine_instance_id=engine._candidate_engine_id, invocation_sequence=engine._candidate_invocation_sequence,
                    run_id=engine.broker.run_id, strategy_id=engine.strategy_id, strategy_name=engine.strategy_name,
                    strategy_version=f'{type(engine.strategy).__module__}.{type(engine.strategy).__qualname__}',
                    execution_mode='PAPER', symbol=quote.symbol, candle_event_at=engine._last_bar_at.get(quote.symbol),
                    quote_observed_at=observed, decision_at=datetime.now(timezone.utc), available_at=observed,
                    quote_price=quote.price, quote_source='TOSS_CURRENT_PRICE' if engine.price_feed is not None else 'MARKET_SNAPSHOT',
                    short_period=engine.short_period, long_period=engine.long_period, history_count=len(closes),
                    history_first_close=closes[0] if closes else None, history_last_close=closes[-1] if closes else None,
                    short_average=None, long_average=None, previous_trend=engine._previous_trend.get(quote.symbol), trend=None,
                    entry_armed_before=quote.symbol in engine._entry_armed,
                    confirmation_count_before=engine._above_count.get(quote.symbol, 0), position_quantity_before=quantity,
                    signal_action=None, signal_reason=None, signal_decision=None, entry_armed_after_signal=None,
                    confirmation_count_after_signal=None, rising_condition=None, baseline_buy_candidate=None,
                    entry_evaluation_performed=False,
                    metadata=dict(history_closes=[str(p) for p in closes], new_bar=new_bar, tick_count=engine.tick_count,
                                  previous_candle_event_at=previous_bar.isoformat() if previous_bar else None,
                                  state_reset_for_gap=state_reset, skip_reason=skip_reason,
                                  exit_signal_present=skip_reason == 'EXIT_SUPPRESSION',
                                  data_source='TOSS' if engine.price_feed is not None else 'SIMULATED',
                                  strategy_config=dict(short_period=engine.short_period, long_period=engine.long_period),
                                  quote_time_policy='local read of engine quote; not provider event/receipt time'))
    except Exception:
        logger.exception('Could not capture pre-risk candidate state for %s', quote.symbol)
        return None


def capture_after(engine, row, signal=None):
    """Finalize and enqueue BEFORE engine state mutation and _buy/_sell."""
    if row is None:
        return None
    try:
        row = deepcopy(row)
        if signal is not None:
            row.update(short_average=signal.short_average, long_average=signal.long_average, trend=signal.trend,
                       signal_action=signal.action, signal_reason=signal.reason, signal_decision=signal.decision,
                       entry_armed_after_signal=signal.entry_armed, confirmation_count_after_signal=signal.confirmation_count,
                       rising_condition=signal.rising, baseline_buy_candidate=signal.action == 'BUY', entry_evaluation_performed=True)
        row['available_at'] = datetime.now(timezone.utc)
        validate_candidate(row)
        engine.candidate_recorder(row)
        return row['candidate_key']
    except Exception:
        logger.exception('Could not enqueue pre-risk candidate for %s', row.get('symbol'))
        return None


def candidate_for_sample(rows, *, strategy_id, symbol, candle_event_at, prediction_at,
                         execution_mode='PAPER', engine_instance_id=None):
    """PIT join interface; absence/ambiguity is UNKNOWN, never guessed false.

    Raises on distinct evaluations after reset/restart; Dataset v2 must resolve
    its scope explicitly, not keep an arbitrary first/last BUY.
    """
    eligible = {r['candidate_key']: r for r in rows if r['contract_version'] == VERSION
                and r['execution_mode'] == execution_mode and r['strategy_id'] == strategy_id
                and r['symbol'] == symbol and r['candle_event_at'] == candle_event_at
                and r['entry_evaluation_performed'] and r['available_at'] <= prediction_at
                and r['metadata']['data_source'] == 'TOSS'
                and (engine_instance_id is None or r['engine_instance_id'] == engine_instance_id)}
    if len(eligible) > 1:
        raise ValueError('Ambiguous candidate invocation; select an engine/session scope')
    return next(iter(eligible.values())) if eligible else None
