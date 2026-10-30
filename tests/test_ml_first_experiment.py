import copy
from datetime import datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auto_trader.ml.first_experiment import (
    KST, TARGET, chronological_folds, feature_list, load_dataset, make_models,
    replay, require_ready, summarize)


class FirstExperimentTests(unittest.TestCase):
    def fixture(self):
        start = datetime(2026, 10, 12, 9, tzinfo=KST)
        prices = [100]*40 + list(range(99, 79, -1)) + list(range(80, 130))
        return [dict(source='TOSS', interval='1m', symbol='A', event_at=start+timedelta(minutes=i),
                     available_at=start+timedelta(minutes=i+1), collected_at=start+timedelta(minutes=i+1),
                     open_price=D(p), high_price=D(p+1), low_price=D(p-1), close_price=D(p), volume=D(1))
                for i, p in enumerate(prices)]

    def test_candidate_pit_and_determinism(self):
        stocks = self.fixture()
        first = replay(stocks)
        self.assertTrue(any(r['baseline_buy_candidate'] for r in first))
        self.assertEqual(first, replay(list(reversed(stocks))))
        delayed = copy.deepcopy(stocks)
        delayed[75]['available_at'] += timedelta(days=1)
        delayed[75]['collected_at'] += timedelta(days=1)
        changed = replay(delayed)
        at = stocks[75]['event_at']
        self.assertFalse(any(at <= datetime.fromisoformat(r['candidate_event_at']) < at+timedelta(minutes=40) for r in changed))
        self.assertEqual([r for r in first if datetime.fromisoformat(r['candidate_event_at']) < at],
                         [r for r in changed if datetime.fromisoformat(r['candidate_event_at']) < at])
        for r in changed:
            self.assertLessEqual(datetime.fromisoformat(r['feature_available_at_max']), datetime.fromisoformat(r['prediction_at']))

    def test_position_and_fill_state_cannot_be_inferred_from_ma(self):
        from auto_trader.trading.strategies.base import SignalContext
        from auto_trader.trading.strategies.moving_average import MovingAverageStrategy
        from dataclasses import replace
        c = SignalContext([D(100)]*30+[D(110)]*10, D(110), 10, 40, 'ABOVE', True, 1, True, lambda: 0)
        strategy = MovingAverageStrategy()
        self.assertEqual(strategy.generate_signal(c).action, 'BUY')
        self.assertEqual(strategy.generate_signal(replace(c, position_quantity=lambda: 1)).action, 'HOLD')
        self.assertEqual(strategy.generate_signal(replace(c, entry_armed=False)).action, 'HOLD')

    def test_no_decision_action_shortcut(self):
        stocks = self.fixture()
        replay_rows = replay(stocks)
        r = replay_rows[-1]
        s = dict(symbol='A', prediction_event_at=r['candidate_event_at'], market_date='2026-10-12',
                 entry_event_at='2026-10-12T11:00:00+09:00', **{TARGET: '0.01'})
        decision = dict(symbol='A', candle_event_at=datetime.fromisoformat(r['candidate_event_at']),
                        history_count=0, action='WAIT')
        a = summarize({}, [s], replay_rows, [decision], stocks, {})
        decision['action'] = 'BUY_FILLED'
        decision['entry_armed'] = False
        b = summarize({}, [s], replay_rows, [decision], stocks, {})
        self.assertEqual(a['candidate_mask_hash'], b['candidate_mask_hash'])
        self.assertEqual(a['positive_target_count'], b['positive_target_count'])
        self.assertEqual(a['reconstructed_diagnostic_candidates'], b['reconstructed_diagnostic_candidates'])
        with self.assertRaisesRegex(RuntimeError, 'CANDIDATE_RECONSTRUCTION_NOT_READY'):
            require_ready(b)

    def test_no_feature_leakage(self):
        for bad in ('future_return_1m', 'executable_net_return_20m', 'MFE', 'MAE', 'entry_open',
                    'usable', 'exclusion_reasons', 'flags', 'symbol', 'max_adverse_excursion_20m'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                feature_list({'feature_list': ['return_1m', bad]})
        self.assertEqual(feature_list({'feature_list': ['return_1m', 'baseline_buy_candidate']}), ['return_1m'])

    def test_whole_date_split_and_horizon_purge(self):
        rows = [dict(market_date=f'2026-10-{i:02}', prediction_at=f'2026-10-{i:02}T09:00:00+09:00',
                     entry_event_at=f'2026-10-{i:02}T09:02:00+09:00') for i in range(1, 9)]
        folds = chronological_folds(rows)
        self.assertEqual(len(folds), 3)
        for train, valid in folds:
            self.assertLess(max(rows[i]['market_date'] for i in train), min(rows[i]['market_date'] for i in valid))
        rows[4]['entry_event_at'] = '2026-10-06T08:55:00+09:00'
        self.assertNotIn(4, chronological_folds(rows)[0][0])

    def test_train_only_preprocessing_and_prediction_determinism(self):
        import numpy as np
        x = np.array([[1, np.nan], [2, 3], [4, 5], [6, 7]]*10)
        y = np.array([0, 0, 1, 1]*10)
        validation = np.array([[10000, np.nan], [20000, -1000]])
        for name, model in make_models().items():
            with self.subTest(model=name):
                model.fit(x, y)
                imputer = model.named_steps['simpleimputer']
                self.assertEqual(imputer.statistics_.tolist(), [3, 5])
                before = imputer.statistics_.copy()
                prediction = model.predict_proba(validation)
                np.testing.assert_array_equal(imputer.statistics_, before)
                duplicate = make_models()[name].fit(x, y)
                np.testing.assert_array_equal(prediction, duplicate.predict_proba(validation))
                if name == 'logistic_regression':
                    np.testing.assert_array_equal(model.named_steps['standardscaler'].mean_, [3.25, 5])

    def test_hash_mismatch_aborts_before_evidence_or_training(self):
        import hashlib
        from auto_trader.ml import first_experiment as experiment
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root/'artifacts/ml/datasets/v1'
            output.mkdir(parents=True)
            sample = root/'samples.csv'
            sample.write_text('symbol\nA\n')
            manifest = dict(artifacts={'samples.csv': hashlib.sha256(sample.read_bytes()).hexdigest()},
                            artifact_locations={'samples.csv': str(sample)})
            (output/'manifest.json').write_text(json.dumps(manifest))
            load_dataset(root)
            sample.write_text('symbol\nB\n')
            with self.assertRaisesRegex(ValueError, 'Artifact hash mismatch'):
                load_dataset(root)
            with patch.object(experiment, 'load_dataset', side_effect=ValueError('Artifact hash mismatch')), \
                 patch.object(experiment, 'load_evidence') as evidence, patch.object(experiment, 'make_models') as models:
                with self.assertRaises(ValueError):
                    experiment.main()
                evidence.assert_not_called()
                models.assert_not_called()


if __name__ == '__main__':
    unittest.main()
