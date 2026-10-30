import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from psycopg import sql

from auto_trader import database
from auto_trader.ml import candidate_pipeline as pipeline
from auto_trader.ml.candidate_pipeline import StrategyCandidateRecorder, candidate_for_sample
from auto_trader.models import LiveStockCandle
from auto_trader.paper import PaperBroker
from auto_trader.simulator import MarketSimulator
from auto_trader.trading.engine import TradingEngine


SYMBOL = '005930'


def fixture(recorder=None):
    now = datetime.now(timezone.utc)
    market = MarketSimulator(symbols=(SYMBOL,))
    market._prices[SYMBOL] = D(125)  # Deliberately differs from last completed close 120.
    broker = PaperBroker(market, initial_cash=D(100000))
    decisions = []
    engine = TradingEngine(market, broker, clock=lambda: now, short_period=2, long_period=3,
                           candidate_recorder=recorder, decision_recorder=decisions.append,
                           price_feed=SimpleNamespace())
    engine.configure(interval_seconds=2, short_period=2, long_period=3, order_quantity=1,
                     target_symbol=SYMBOL, trading_start='00:00', trading_end='23:59', cooldown_minutes=0,
                     strategy_id=17, strategy_name='candidate-contract-test')
    engine._previous_trend[SYMBOL] = 'ABOVE'
    engine._entry_armed.add(SYMBOL)
    engine._above_count[SYMBOL] = 1
    bars = [LiveStockCandle(timestamp=now-timedelta(minutes=3-i), open_price=p, high_price=p,
                           low_price=p, close_price=p, volume=1) for i, p in enumerate((100, 110, 120))]
    return engine, broker, bars, decisions


def candidate():
    records = []
    engine, broker, bars, decisions = fixture(records.append)
    with patch.object(engine, '_buy', return_value=False):
        engine.step(candles={SYMBOL: bars})
    return records[0]


class CandidateHookTests(unittest.TestCase):
    def test_buy_capture_precedes_risk_and_preserves_quote_and_pre_state(self):
        captured = []
        engine, broker, bars, decisions = fixture(captured.append)
        def risk(**kwargs):
            self.assertEqual(len(captured), 1)
            self.assertTrue(captured[0]['baseline_buy_candidate'])
            self.assertEqual(captured[0]['position_quantity_before'], 0)
            return 'risk rejected'
        broker.risk_manager = SimpleNamespace(check_buy=risk)
        engine.step(candles={SYMBOL: bars})  # Real engine -> signal -> _buy -> PaperBroker -> risk.
        row = captured[0]
        self.assertEqual(row['signal_action'], 'BUY')
        self.assertEqual(row['quote_price'], D(125))
        self.assertNotEqual(row['quote_price'], bars[-1].close_price)
        self.assertEqual((row['confirmation_count_before'], row['confirmation_count_after_signal']), (1, 2))
        self.assertEqual(row['metadata']['history_closes'], ['100', '110', '120'])
        self.assertEqual(decisions[-1]['action'], 'BUY_BLOCKED')
        self.assertEqual(decisions[-1]['metadata']['candidate_key'], row['candidate_key'])

    def test_filled_buy_does_not_mutate_recorded_after_signal_state(self):
        captured = []
        engine, broker, bars, decisions = fixture(captured.append)
        engine.step(candles={SYMBOL: bars})
        self.assertEqual(decisions[-1]['action'], 'BUY_FILLED')
        self.assertNotIn(SYMBOL, engine._entry_armed)
        self.assertTrue(captured[0]['entry_armed_after_signal'])
        self.assertEqual(captured[0]['position_quantity_before'], 0)
        self.assertEqual(broker.strategy_quantity(SYMBOL), 1)

    def test_before_after_arming_transition(self):
        rows = []
        engine, _, bars, _ = fixture(rows.append)
        engine._entry_armed.clear()
        engine._previous_trend[SYMBOL] = 'BELOW'
        engine._above_count[SYMBOL] = 0
        engine.step(candles={SYMBOL: bars})
        row = rows[0]
        self.assertFalse(row['entry_armed_before'])
        self.assertTrue(row['entry_armed_after_signal'])
        self.assertEqual(row['signal_action'], 'HOLD')
        self.assertFalse(row['baseline_buy_candidate'])

    def test_sell_capture_and_position_suppression(self):
        rows = []
        engine, broker, bars, _ = fixture(rows.append)
        with patch.object(broker, 'strategy_quantity', return_value=2), patch.object(engine, '_buy') as buy:
            engine.step(candles={SYMBOL: bars})
            buy.assert_not_called()
        self.assertEqual(rows[0]['position_quantity_before'], 2)
        self.assertEqual(rows[0]['signal_action'], 'HOLD')
        self.assertFalse(rows[0]['baseline_buy_candidate'])
        engine, _, bars, _ = fixture(rows.append)
        bars = [b.model_copy(update={'close_price': D(120-i*10)}) for i, b in enumerate(bars)]
        with patch.object(engine, '_sell') as sell:
            engine.step(candles={SYMBOL: bars})
            sell.assert_called_once()
        self.assertEqual(rows[-1]['signal_action'], 'SELL')
        self.assertFalse(rows[-1]['baseline_buy_candidate'])

    def test_same_candle_poll_is_not_a_duplicate_and_resets_remain_distinct(self):
        rows = []
        engine, _, bars, _ = fixture(rows.append)
        with patch.object(engine, '_buy', return_value=False):
            engine.step(candles={SYMBOL: bars})
            engine.step(candles={SYMBOL: bars})
            self.assertEqual(len(rows), 1)
            engine.configure(interval_seconds=2, short_period=2, long_period=3, order_quantity=1,
                             target_symbol=SYMBOL, trading_start='00:00', trading_end='23:59', strategy_id=17)
            engine.step(candles={SYMBOL: bars})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['candle_event_at'], rows[1]['candle_event_at'])
        self.assertNotEqual(rows[0]['invocation_sequence'], rows[1]['invocation_sequence'])
        self.assertNotEqual(rows[0]['candidate_key'], rows[1]['candidate_key'])
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            candidate_for_sample(rows, strategy_id=17, symbol=SYMBOL, candle_event_at=bars[-1].timestamp,
                                 prediction_at=datetime.now(timezone.utc))

    def test_skips_are_not_synthetic_signals_and_gap_reset_is_observed(self):
        for reason in ('EXIT_SUPPRESSION', 'INSUFFICIENT_HISTORY'):
            with self.subTest(reason=reason):
                rows = []
                engine, _, bars, _ = fixture(rows.append)
                with patch.object(engine, '_manage_exits', return_value={SYMBOL} if reason == 'EXIT_SUPPRESSION' else set()), \
                     patch.object(engine.strategy, 'generate_signal') as signal:
                    engine.step(candles={SYMBOL: bars if reason == 'EXIT_SUPPRESSION' else bars[:1]})
                    signal.assert_not_called()
                self.assertFalse(rows[0]['entry_evaluation_performed'])
                self.assertIsNone(rows[0]['baseline_buy_candidate'])
                self.assertIsNone(rows[0]['signal_action'])
                self.assertEqual(rows[0]['metadata']['skip_reason'], reason)
        rows = []
        engine, _, bars, _ = fixture(rows.append)
        engine._last_bar_at[SYMBOL] = bars[-1].timestamp-timedelta(minutes=4)
        engine.step(candles={SYMBOL: bars})
        self.assertTrue(rows[0]['metadata']['state_reset_for_gap'])
        self.assertFalse(rows[0]['entry_armed_before'])
        self.assertIsNone(rows[0]['previous_trend'])

    def test_logging_and_logging_failure_leave_real_order_results_unchanged(self):
        def failed(_):
            raise RuntimeError('recorder unavailable')
        results = []
        for recorder in (None, list().append, failed):
            engine, broker, bars, decisions = fixture(recorder)
            with self.assertLogs(pipeline.logger, level='ERROR') if recorder is failed else self.subTest(recorder=recorder):
                engine.step(candles={SYMBOL: bars})
            results.append(([(o.status, o.quantity, o.price, o.fee) for o in broker.orders()],
                            broker.account().cash, broker.strategy_quantity(SYMBOL),
                            engine._above_count.copy(), engine._entry_armed.copy(), decisions[-1]['action']))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])

    def test_capture_failure_is_isolated_and_no_db_in_engine_path(self):
        rows = []
        engine, _, bars, _ = fixture(rows.append)
        with patch.object(pipeline, 'connect', side_effect=AssertionError('DB in signal path')), \
             patch.object(pipeline, 'deepcopy', side_effect=RuntimeError('copy failed')), \
             patch.object(engine, '_buy', return_value=True) as buy, self.assertLogs(pipeline.logger, level='ERROR'):
            engine.step(candles={SYMBOL: bars})
        buy.assert_called_once()
        self.assertEqual(rows, [])

    def test_timestamp_contract_and_pit_join(self):
        row = candidate()
        self.assertLessEqual(row['candle_event_at'], row['quote_observed_at'])
        self.assertLessEqual(row['quote_observed_at'], row['decision_at'])
        self.assertLessEqual(row['decision_at'], row['available_at'])
        lookup = dict(strategy_id=17, symbol=SYMBOL, candle_event_at=row['candle_event_at'])
        self.assertIsNone(candidate_for_sample([row], prediction_at=row['decision_at'], **lookup))
        self.assertEqual(candidate_for_sample([row, row], prediction_at=row['available_at'], **lookup), row)
        self.assertIsNone(candidate_for_sample([row], prediction_at=row['available_at'], execution_mode='LIVE', **lookup))

    def test_active_logging_preserves_all_existing_replay_scenarios(self):
        from refactor_scenarios import replay, SCENARIOS
        for name, options in SCENARIOS.items():
            with self.subTest(scenario=name):
                captured = []
                def observed_engine(*args, **kwargs):
                    return TradingEngine(*args, **kwargs, candidate_recorder=captured.append)
                before = replay(TradingEngine, options)
                after = replay(observed_engine, options)
                for decision in after['ml_decisions']:
                    decision['metadata'].pop('candidate_key', None)
                self.assertEqual(before, after)
                self.assertTrue(captured)


class CandidateRecorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_waits_for_inflight_write(self):
        from threading import Event
        started, release = Event(), Event()
        recorder = StrategyCandidateRecorder(flush_seconds=0.001)
        recorder.record(candidate())
        def slow_save(batch):
            started.set()
            if not release.wait(3):
                raise RuntimeError('test release timeout')
            return 1
        with patch.object(pipeline, 'save_strategy_candidates', side_effect=slow_save):
            await recorder.start()
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                stopped = asyncio.create_task(recorder.stop())
                await asyncio.sleep(0.01)
                self.assertFalse(stopped.done())
            finally:
                release.set()
                if 'stopped' in locals():
                    await stopped
                else:
                    await recorder.stop()
        self.assertEqual(recorder.status()['saved'], 1)
        self.assertFalse(recorder.status()['running'])

    async def test_queue_snapshot_retry_and_lifecycle(self):
        row = candidate()
        recorder = StrategyCandidateRecorder(flush_seconds=0.01)
        recorder.record(row)
        row['metadata']['history_closes'][0] = '9999'
        with patch.object(pipeline, 'save_strategy_candidates', side_effect=RuntimeError('DB down')), \
             self.assertLogs(pipeline.logger, level='WARNING'):
            self.assertEqual(await recorder.flush(), 0)
        self.assertEqual(recorder.status()['queued'], 1)
        with patch.object(pipeline, 'save_strategy_candidates', return_value=1) as save:
            await recorder.start()
            await recorder.stop()
        saved = save.call_args.args[0][0]
        self.assertEqual(saved['metadata']['history_closes'][0], '100')
        self.assertEqual(recorder.status()['saved'], 1)
        self.assertEqual(recorder.status()['failures'], 1)

    async def test_invalid_and_overflow_are_explicit_and_queue_is_bounded(self):
        recorder = StrategyCandidateRecorder(max_queue=1)
        row = candidate()
        recorder.record(row)
        with self.assertLogs(pipeline.logger, level='WARNING'):
            recorder.record(row)
            invalid = deepcopy(row)
            invalid['baseline_buy_candidate'] = False
            recorder.record(invalid)
        self.assertEqual(recorder.status()['queued'], 1)
        self.assertEqual(recorder.status()['dropped'], 2)
        def fails_with_concurrent_append(batch):
            recorder.record(row)
            raise RuntimeError('DB down')
        with patch.object(pipeline, 'save_strategy_candidates', side_effect=fails_with_concurrent_append), \
             self.assertLogs(pipeline.logger, level='WARNING'):
            await recorder.flush()
        self.assertEqual(recorder.status()['queued'], 1)
        self.assertEqual(recorder.status()['dropped'], 3)


class CandidateRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'test_ml_candidates_'+uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            return conn
        for module in (database, pipeline):
            patcher = patch.object(module, 'connect', isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()

    def cleanup_schema(self):
        with self.original_connect() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_schema_idempotence_append_only_retry_and_roundtrip_replay(self):
        from auto_trader.trading.strategies.base import SignalContext
        from auto_trader.trading.strategies.moving_average import MovingAverageStrategy
        database.initialize()
        row = candidate()
        self.assertEqual(pipeline.save_strategy_candidates([row]), 1)
        self.assertEqual(pipeline.save_strategy_candidates([row]), 0)
        with pipeline.connect() as conn:
            saved = conn.execute('SELECT * FROM ml_strategy_candidates').fetchone()
        self.assertLessEqual(saved['available_at'], saved['collected_at'])
        self.assertEqual(saved['quote_price'], row['quote_price'])
        self.assertEqual(saved['baseline_buy_candidate'], saved['signal_action'] == 'BUY')
        signal = MovingAverageStrategy().generate_signal(SignalContext(
            [D(p) for p in saved['metadata']['history_closes']], saved['quote_price'],
            saved['short_period'], saved['long_period'], saved['previous_trend'], saved['entry_armed_before'],
            saved['confirmation_count_before'], saved['metadata']['new_bar'], lambda: saved['position_quantity_before']))
        self.assertEqual(signal.action, saved['signal_action'])
        self.assertEqual(signal.short_average, saved['short_average'])
        changed = deepcopy(row)
        changed['quote_price'] = D(999)
        self.assertEqual(pipeline.save_strategy_candidates([changed]), 0)
        with pipeline.connect() as conn:
            self.assertEqual(conn.execute('SELECT quote_price FROM ml_strategy_candidates').fetchone()['quote_price'], row['quote_price'])

    def test_skipped_signal_and_live_contract_and_timestamp_constraint(self):
        row = candidate()
        row['execution_mode'] = 'LIVE'  # Repository contract supports either mode; no LIVE order API is invoked.
        row.update(entry_evaluation_performed=False, signal_action=None, baseline_buy_candidate=None,
                   entry_armed_after_signal=None, confirmation_count_after_signal=None)
        self.assertEqual(pipeline.save_strategy_candidates([row]), 1)
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):
            with pipeline.connect() as conn:
                conn.execute('UPDATE ml_strategy_candidates SET collected_at=available_at-interval \'1 second\'')


if __name__ == '__main__':
    unittest.main()
